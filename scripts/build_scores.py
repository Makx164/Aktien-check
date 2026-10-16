#!/usr/bin/env python3
"""Build data/<TICKER>/score.json from EDGAR, prices and optional analyst keys."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scoring.analysts import fetch_analysts, load_keys
from scoring.edgar import EdgarClient
from scoring.holders import fetch_holders
from scoring.metrics import extract_fundamentals, latest_observed, score_company
from scoring.model import coverage_decision, quality_phrase, round1, traffic_light, weighted_total
from scoring.prices import fetch_series

QUALITY = {"C_01", "C_02", "C_03", "C_04", "C_05"}
MARKET = {"C_06", "C_07", "C_08"}


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def group_score(criteria, ids):
    pairs = [(item["score"], item["weight"]) for item in criteria if item["id"] in ids]
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


def assemble(ticker, name, note, scored, config, price=None):
    criteria = scored["criteria"]
    decision = coverage_decision(
        criteria,
        config["minCriteriaWithData"],
        config.get("missingDataStatus") or "wenig Daten",
    )
    total = decision["score"]
    parts = []
    if note:
        parts.append(note)
    if scored.get("priceNote"):
        parts.append(scored["priceNote"])
    parts.append(quality_phrase(scored["available"], scored["total"]))
    lights = config["trafficLight"]
    return {
        "ticker": ticker,
        "name": name,
        "asOf": latest_observed(criteria),
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
        "criteria": criteria,
    }


def main():
    config = load_json(ROOT / "config" / "scoring.json")
    tickers = load_json(ROOT / "config" / "tickers.json")
    today = date.today().isoformat()
    keys = load_keys()
    if not keys["finnhub"] and not keys["fmp"]:
        print("FMP_API_KEY and FINNHUB_API_KEY are unset. Analyst metrics stay unknown.")
    edgar = EdgarClient(ROOT / "cache" / "edgar")
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
                bundle = extract_fundamentals(facts.get("facts") or {})
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
        if holders:
            bundle["holders"] = dict(holders)
            bundle["holders"]["observed"] = None
        sources["holderLabel"] = holders.get("label")
        sources["holderUrl"] = holders.get("url")
        if spec.get("analysts", True):
            analysts = fetch_analysts(ticker, last_close, today, keys)
        else:
            analysts = {}
            print("  analysts skipped")
        sources["analystLabel"] = analysts.get("label")
        sources["analystUrl"] = analysts.get("url")
        price_input = {
            "bars": prices["bars"],
            "sourceLabel": prices.get("sourceLabel"),
            "sourceUrl": prices.get("sourceUrl"),
            "note": prices.get("note"),
            "benchmark": bench_payload,
        }
        scored = score_company(config, bundle, price_input, analysts, today, sources)
        document = assemble(
            ticker,
            spec["name"],
            spec.get("note"),
            scored,
            config,
            price_quote(prices, spec.get("currency") or "USD"),
        )
        target = ROOT / "data" / ticker / "score.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        points = " ".join(
            f"{item['id']}={item['score'] if item['score'] is not None else 'null'}"
            for item in document["criteria"]
        )
        shown = document["status"] or document["score"]
        print(f"  score={shown} status={document['status']} {document['trafficLight']} {document['dataQuality']}")
        print(f"  {points}")


if __name__ == "__main__":
    main()
