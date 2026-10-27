#!/usr/bin/env python3
"""Build data/<TICKER>/score.json from EDGAR, prices and optional analyst keys."""

from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scoring.analysts import fetch_analysts, load_keys
from scoring.damodaran import IndustryBook
from scoring.edgar import EdgarClient
from scoring.filings import fx_rate, load_instances, revenue_pools, yahoo_annual_revenue
from scoring.holders import fetch_holders
from scoring.marketdata import apply_fundamental_fallback, load_nasdaq, load_yahoo_summary, profile_from_yahoo
from scoring.metrics import data_stand, extract_fundamentals, score_company
from scoring.moat import assemble_context
from scoring.model import coverage_decision, quality_phrase, round1, traffic_light, weighted_total
from scoring.prices import fetch_series

QUALITY = {"C_01", "C_02", "C_03", "C_04", "C_05"}
MARKET = {"C_06", "C_07", "C_08"}


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def group_score(criteria, ids):
    pairs = [(item["score"], 1) for item in criteria if item["id"] in ids]
    return round1(weighted_total(pairs))


def price_quote(prices, currency):
    bars = (prices or {}).get("bars") or []
    if not bars or bars[-1].get("close") is None:
        return None
    last = bars[-1]
    return {
        "close": round(float(last["close"]), 4),
        "currency": currency or "USD",
        "date": last.get("date"),
        "sourceLabel": prices.get("sourceLabel"),
        "sourceUrl": prices.get("sourceUrl"),
    }


def assigned_role(scored, config):
    roles = config.get("roles") or {}
    if not roles.get("dividendEnabled"):
        return roles["satellite"], "Dieselbe Kriterienauswahl für alle Titel."
    dividend = scored.get("dividend") or {}
    threshold = float(roles.get("dividendYieldAtLeast", 0.025))
    qualifies = (
        dividend.get("yield") is not None
        and float(dividend["yield"]) >= threshold
        and dividend.get("no_cut") is True
    )
    chosen = roles["dividend"] if qualifies else roles["satellite"]
    if qualifies:
        reason = "Dividendenrendite mindestens 2,5 % und fünf Jahre ohne Kürzung."
    else:
        reason = "Satellit, solange die Dividendenregel nicht erfüllt ist."
    return chosen, reason


def apply_role(criteria, role):
    weights = role.get("weights") or {}
    kept = []
    for item in criteria:
        weight = float(weights.get(item["id"], 0) or 0)
        if weight <= 0:
            continue
        copied = dict(item)
        copied["weight"] = weight
        kept.append(copied)
    return kept


def metric_counts(criteria):
    available = 0
    total = 0
    for item in criteria:
        for metric in item.get("metrics") or []:
            if metric.get("status") == "nicht anwendbar":
                continue
            total += 1
            if metric.get("status") is None and metric.get("points") is not None:
                available += 1
    return available, total


def assemble(ticker, name, note, scored, config, price=None, profile=None):
    role, reason = assigned_role(scored, config)
    criteria = apply_role(scored["criteria"], role)
    decision = coverage_decision(
        criteria,
        config["minCriteriaWithData"],
        config.get("missingDataStatus") or "wenig Daten",
    )
    total = decision["score"]
    available, metric_total = metric_counts(criteria)
    parts = []
    if note:
        parts.append(note)
    if scored.get("priceNote"):
        parts.append(scored["priceNote"])
    parts.append(quality_phrase(available, metric_total))
    lights = config["trafficLight"]
    visible_weights = {
        key: value for key, value in (role.get("weights") or {}).items() if value and float(value) > 0
    }
    document = {
        "ticker": ticker,
        "name": name,
        "asOf": data_stand(criteria, price),
        "score": total,
        "status": decision["status"],
        "criteriaWithData": decision["criteriaWithData"],
        "trafficLight": traffic_light(total, lights["greenAt"], lights["orangeAt"]),
        "qualityScore": group_score(criteria, QUALITY),
        "marketScore": group_score(criteria, MARKET),
        "dataQuality": " ".join(parts),
        "missingDataNote": decision["note"],
        "koFlags": scored["koFlags"],
        "price": price,
        "role": {
            "id": role.get("id"),
            "label": role.get("label"),
            "weights": visible_weights,
            "reason": reason,
        },
        "criteria": criteria,
    }
    if profile:
        ordered = {}
        for key, value in document.items():
            ordered[key] = value
            if key == "price":
                ordered["profile"] = profile
        document = ordered
    return document


def scored_metric_count(document):
    count = 0
    for item in (document or {}).get("criteria") or []:
        for metric in item.get("metrics") or []:
            if metric.get("points") is not None and not metric.get("status"):
                count += 1
    return count


RETIRED_METRIC_IDS = {
    "roic_2y", "gross_margin", "gross_margin_change_yoy", "margin_progress",
    "net_cash_or_runway", "revenue_yoy", "rd_intensity",
}


def _retired_methodology(previous):
    for item in (previous or {}).get("criteria") or []:
        for metric in item.get("metrics") or []:
            if metric.get("id") in RETIRED_METRIC_IDS:
                return True
    return False


def fetch_failed(previous, document, prices):
    """A failed fetch must not replace a fuller previous file."""
    if not previous:
        return None
    old_price = (previous.get("price") or {}).get("close")
    if old_price and not (prices or {}).get("bars"):
        return "Kursabruf lieferte keine Kurse"
    if _retired_methodology(previous):
        return None
    old_count = scored_metric_count(previous)
    new_count = scored_metric_count(document)
    if old_count >= 8 and new_count + 8 <= old_count:
        return f"bewertete Metriken {old_count} -> {new_count}"
    return None


def main():
    config = load_json(ROOT / "config" / "scoring.json")
    tickers = load_json(ROOT / "config" / "tickers.json")
    today = date.today().isoformat()
    keys = load_keys()
    if not keys["finnhub"] and not keys["fmp"]:
        print("FMP_API_KEY and FINNHUB_API_KEY are unset. Analysts use Yahoo, then Nasdaq.")
    edgar = EdgarClient(ROOT / "cache" / "edgar")
    print("Loading Damodaran industry tables")
    industry_book = IndustryBook.load(ROOT / "cache" / "damodaran")
    print(" ", industry_book.label)
    overrides = load_json(ROOT / "config" / "industry_overrides.json")
    segment_map = load_json(ROOT / "config" / "segments.json")
    print("Loading US revenue pools")
    pools = revenue_pools(industry_book, edgar.cache_dir, edgar.ticker_map(), today)
    print(f"  CY{pools['year']} {pools['ciks']} filers, {len(pools['pools'])} industries")
    bench_cfg = config["benchmark"]
    print("Fetching benchmark", bench_cfg["stooq"])
    benchmark = fetch_series(bench_cfg["stooq"], bench_cfg["nasdaq"], bench_cfg["assetClass"])
    bench_label = bench_cfg["label"] if benchmark["provider"] == "stooq" else bench_cfg["proxyLabel"]
    bench_payload = None
    if benchmark["bars"]:
        bench_payload = {"bars": benchmark["bars"], "label": bench_label}
        print(f"  {len(benchmark['bars'])} bars via {benchmark['provider']} ({bench_label})")
    else:
        print("  no benchmark:", benchmark.get("note"))
    custom_benchmarks = {}
    for spec in tickers:
        key = spec.get("benchmark")
        if not key or key in custom_benchmarks:
            continue
        custom = (config.get("benchmarks") or {}).get(key)
        if not custom:
            continue
        print("Fetching benchmark", custom.get("stooq") or custom.get("yahoo"), "for", key)
        series = fetch_series(
            custom.get("stooq"),
            custom.get("nasdaq"),
            custom.get("assetClass"),
            custom.get("stooqFallbacks"),
            custom.get("yahoo"),
        )
        if series["bars"]:
            label = custom["label"] if series["provider"] == "stooq" else (custom.get("proxyLabel") or custom["label"])
            custom_benchmarks[key] = {"bars": series["bars"], "label": label}
            print(f"  {len(series['bars'])} bars via {series['provider']} ({label})")
        else:
            # TODO: SIVE momentum versus OMXS30. This run got no index bars, so the S&P stays.
            custom_benchmarks[key] = None
            print("  TODO: benchmark", key, "unavailable; momentum stays versus the S&P")

    for spec in tickers:
        ticker = spec["ticker"]
        print("Building", ticker)
        prices = fetch_series(
            spec.get("stooq"),
            spec.get("nasdaq"),
            spec.get("assetClass"),
            spec.get("stooqFallbacks"),
            spec.get("yahoo"),
        )
        print(f"  prices {len(prices['bars'])} via {prices['provider']} {prices.get('note') or ''}")
        bundle = {}
        cik = None
        submissions = None
        instances = []
        ifrs = False
        sources = {
            "fundamentalLabel": None,
            "fundamentalUrl": None,
            "insiderLabel": None,
            "insiderUrl": None,
            "analystLabel": None,
            "analystUrl": None,
            "restatement": False,
        }
        last_close = prices["bars"][-1]["close"] if prices["bars"] else None
        holder_symbol = spec.get("yahoo") or ticker
        holders = fetch_holders(holder_symbol, ROOT / "cache" / "yahoo")
        if holders:
            print(
                "  holders insider="
                + str(holders.get("insider"))
                + " institutional="
                + str(holders.get("institutional"))
            )
        else:
            print("  holders unavailable")
        if spec.get("sec"):
            cik = edgar.cik_for(ticker)
            if cik is None:
                print("  no CIK")
            else:
                facts, facts_url = edgar.companyfacts(cik)
                submissions, sub_url = edgar.submissions(cik)
                body = facts.get("facts") or {}
                try:
                    instances = load_instances(edgar, cik, submissions, today)
                    print(f"  xbrl instances {len(instances)}")
                except (OSError, ValueError, TypeError):
                    instances = []
                    print("  xbrl instances unavailable")
                forms = {item.get("form") for item in instances}
                ifrs = any(str(form).startswith("20-F") or str(form).startswith("40-F") for form in forms)
                if not ifrs:
                    ifrs = "ifrs-full" in body and "us-gaap" not in body
                try:
                    edgar.merge_rd_extension(body, cik, submissions)
                except (OSError, ValueError, TypeError):
                    print("  extension facts unavailable")
                bundle = extract_fundamentals(body)
                insider = edgar.insider_flows(cik, submissions, date.today())
                if insider:
                    bundle["insider"] = insider
                sources["restatement"] = edgar.restatement_within(
                    submissions, config["ko"]["restatementMonths"], date.today()
                )
                sources["fundamentalLabel"] = "SEC EDGAR companyfacts"
                sources["fundamentalUrl"] = facts_url
                sources["insiderLabel"] = "SEC EDGAR Form 4, offene Marktgeschäfte, 6 Monate"
                sources["insiderUrl"] = sub_url
        yahoo_symbol = spec.get("yahoo") or ticker
        summary = load_yahoo_summary(yahoo_symbol, ROOT / "cache" / "yahoo")
        nasdaq_symbol = spec.get("nasdaq")
        nasdaq_target = load_nasdaq("target", nasdaq_symbol, ROOT / "cache" / "nasdaq") if nasdaq_symbol else None
        nasdaq_annual = load_nasdaq("financials", nasdaq_symbol, ROOT / "cache" / "nasdaq") if nasdaq_symbol else None
        nasdaq_quarterly = load_nasdaq("quarterly", nasdaq_symbol, ROOT / "cache" / "nasdaq") if nasdaq_symbol else None
        yahoo_url = (summary or {}).get("_url")
        nasdaq_url = (
            "https://api.nasdaq.com/api/company/" + nasdaq_symbol + "/financials?frequency=1"
            if nasdaq_symbol else None
        )
        bundle = apply_fundamental_fallback(
            bundle, summary, nasdaq_annual, yahoo_url or nasdaq_url, today, nasdaq_quarterly,
        )
        snapshot = bundle.get("snapshot") or {}
        if snapshot.get("sourceLabel") == "SEC EDGAR companyfacts" and not snapshot.get("sourceUrl"):
            snapshot["sourceUrl"] = sources.get("fundamentalUrl")
            snapshot["fetchedAt"] = snapshot.get("fetchedAt") or today
            bundle["snapshot"] = snapshot
        if summary:
            print("  yahoo summary ok")
        else:
            print("  yahoo summary unavailable")
        if holders:
            bundle["holders"] = dict(holders)
            bundle["holders"]["observed"] = None
        sources["holderLabel"] = holders.get("label")
        sources["holderUrl"] = holders.get("url")
        analysts = fetch_analysts(
            ticker, last_close, today, keys, yahoo=summary, nasdaq_target=nasdaq_target,
        )
        if not analysts.get("label"):
            print("  analysts unavailable")
        else:
            print("  analysts via", analysts.get("label"), "count", analysts.get("count"))
        sources["analystLabel"] = analysts.get("label")
        sources["analystUrl"] = analysts.get("url")
        ticker_bench = bench_payload
        custom_key = spec.get("benchmark")
        if custom_key and custom_benchmarks.get(custom_key):
            ticker_bench = custom_benchmarks[custom_key]
        price_input = {
            "bars": prices["bars"],
            "sourceLabel": prices.get("sourceLabel"),
            "sourceUrl": prices.get("sourceUrl"),
            "note": prices.get("note"),
            "benchmark": ticker_bench,
        }
        sic = str((submissions or {}).get("sic") or "")
        own_revenue = None
        if not spec.get("sec"):
            yahoo_revenue = yahoo_annual_revenue(summary)
            if yahoo_revenue and yahoo_revenue.get("revenue") and yahoo_revenue.get("end"):
                fx = fx_rate(ROOT / "cache" / "fx", yahoo_revenue["end"])
                if fx and fx.get("rate"):
                    own_revenue = {
                        "usd": float(yahoo_revenue["revenue"]) * float(fx["rate"]),
                        "end": yahoo_revenue["end"],
                        "note": (
                            "niedrige Datenqualität (yfinance). "
                            + f"{float(fx['rate']):.5f}".replace(".", ",")
                            + f" USD je SEK am {fx['date']}, {fx['source']}"
                        ),
                    }
                    print("  yfinance revenue", round(own_revenue["usd"]), "USD at", fx["date"])
                else:
                    print("  yfinance revenue without an FX rate")
        bundle["moat"] = assemble_context(
            industry_book, overrides, segment_map, pools,
            ticker, sic, bool(spec.get("sec")), instances, ifrs, own_revenue,
        )
        print("  industry", bundle["moat"].get("industry"), bundle["moat"].get("industrySource"))
        scored = score_company(
            config, bundle, price_input, analysts, today, sources,
            {"sec": bool(spec.get("sec")), "sector": spec.get("sector")},
        )
        document = assemble(
            ticker,
            spec["name"],
            spec.get("note"),
            scored,
            config,
            price_quote(prices, spec.get("currency") or "USD"),
            profile_from_yahoo(summary, today),
        )
        target = ROOT / "data" / ticker / "score.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        previous = None
        if target.exists():
            try:
                previous = json.loads(target.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                previous = None
        failed = fetch_failed(previous, document, prices)
        if failed:
            print(f"  KEEP previous score.json ({failed})")
        else:
            target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        time.sleep(0.4)
        points = " ".join(
            f"{item['id']}={item['score'] if item['score'] is not None else 'null'}"
            for item in document["criteria"]
        )
        shown = document["status"] or document["score"]
        role_label = (document.get("role") or {}).get("label")
        print(f"  score={shown} status={document['status']} role={role_label} {document['trafficLight']} {document['dataQuality']}")
        print(f"  {points}")


if __name__ == "__main__":
    main()
