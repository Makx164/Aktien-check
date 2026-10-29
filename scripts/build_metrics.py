#!/usr/bin/env python3
"""Schreibt data/<TICKER>/metrics.json aus Yahoo Finance.

Kein Score, kein Finnhub, kein neuer API-Schlüssel. Eine vorhandene These
bleibt stehen. Fehlt sie, steht „These folgt“. Fehlende Kennzahlen bleiben
leer und werden nicht geschätzt.

    python3 scripts/build_metrics.py
    python3 scripts/build_metrics.py --ticker PLTR
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
import urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from metricsbuild.compute import (  # noqa: E402
    BASIC_SHARE_ROWS,
    CAPEX_ROWS,
    CASH_ONLY_ROWS,
    CASH_ROWS,
    DEBT_ROWS,
    DILUTED_SHARE_ROWS,
    FCF_ROWS,
    GROSS_ROWS,
    METRIC_ORDER,
    OCF_ROWS,
    REVENUE_ROWS,
    STI_ROWS,
    THESIS_PLACEHOLDER,
    BERLIN,
    cash_vs_debt,
    close_point,
    dilution,
    finite,
    format_percent,
    free_cash_flow,
    gross_margin,
    headquarters,
    latest_point,
    next_earnings,
    prefer_newer_series,
    quarterly_points_from_rows,
    quarter_label,
    quote_url,
    revenue_growth,
    row_points,
)

USER_AGENT = "sidequest/1.0 (aktiencheck; maximilianhillert@mail.grokbot.com)"
TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_REVENUE = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
)
def berlin_today():
    return datetime.now(BERLIN).date()


def load_tickers(only=None):
    rows = json.loads((ROOT / "config" / "tickers.json").read_text(encoding="utf-8"))
    if only:
        wanted = {item.upper() for item in only}
        rows = [row for row in rows if row["ticker"].upper() in wanted]
    return rows


def previous_document(ticker):
    path = ROOT / "data" / ticker / "metrics.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def previous_profile(ticker):
    metrics = previous_document(ticker)
    if metrics.get("profile"):
        return metrics["profile"]
    score_path = ROOT / "data" / ticker / "score.json"
    if not score_path.exists():
        return None
    score = json.loads(score_path.read_text(encoding="utf-8"))
    profile = score.get("profile") or None
    return profile


def fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.loads(response.read().decode("utf-8"))


def sec_cik_map(cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "company_tickers.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = fetch_json(TICKER_URL)
        path.write_text(json.dumps(payload), encoding="utf-8")
    mapping = {}
    for row in payload.values():
        mapping[str(row.get("ticker", "")).upper()] = int(row["cik_str"])
    return mapping


def sec_facts(cik, cache_dir):
    path = cache_dir / f"facts-{cik:010d}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    payload = fetch_json(url)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def sec_quarter_rows(facts, concepts):
    gaap = ((facts or {}).get("facts") or {}).get("us-gaap") or {}
    rows = []
    for concept in concepts:
        units = (gaap.get(concept) or {}).get("units") or {}
        for unit_name in ("USD", "shares"):
            for row in units.get(unit_name) or []:
                copied = dict(row)
                copied["concept"] = concept
                rows.append(copied)
            if units.get(unit_name):
                break
    return rows


def fiscal_month(info):
    raw = (info or {}).get("lastFiscalYearEnd")
    if raw in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(raw), BERLIN).date().month
    except (TypeError, ValueError, OSError):
        return None


def confirm_or_replace_growth(yahoo_points, facts, currency, fetched_at, yahoo_url, sec_url, fiscal):
    sec_points, concept = quarterly_points_from_rows(sec_quarter_rows(facts, SEC_REVENUE))
    series, origin = prefer_newer_series(yahoo_points, sec_points)
    if origin == "alternate":
        metric = revenue_growth(
            series,
            currency,
            fetched_at,
            sec_url,
            source="SEC companyfacts " + (concept or ""),
            fiscal_month=fiscal if fiscal is not None else 0,
        )
        return metric
    metric = revenue_growth(
        series,
        currency,
        fetched_at,
        yahoo_url,
        fiscal_month=fiscal if fiscal is not None else 0,
    )
    inputs = metric.get("inputs") or {}
    if metric.get("value") is None or not metric.get("observedAt") or not inputs.get("priorEnd"):
        return metric
    current_end = date.fromisoformat(metric["observedAt"])
    prior_end = date.fromisoformat(inputs["priorEnd"])
    sec_current = close_point(sec_points, current_end)
    sec_prior = close_point(sec_points, prior_end)
    if sec_current is None or sec_prior is None or sec_prior[1] <= 0:
        return metric
    sec_ratio = sec_current[1] / sec_prior[1] - 1
    if abs(sec_ratio - metric["value"]) <= 0.01 and abs(sec_current[1] - inputs["current"]) <= max(1_000_000, abs(inputs["current"]) * 0.01):
        metric["source"] = metric["source"] + "; SEC companyfacts bestätigt"
        metric["secConcept"] = concept
    elif abs(sec_ratio - metric["value"]) > 0.01:
        metric["note"] = (
            "SEC companyfacts weicht ab ("
            + format_percent(sec_ratio, signed=True)
            + ") und wird nicht übernommen"
        )
    return metric


def series_map(income, cashflow, balance):
    return {
        "revenue": row_points(income, REVENUE_ROWS),
        "gross": row_points(income, GROSS_ROWS),
        "basic": row_points(income, BASIC_SHARE_ROWS),
        "diluted": row_points(income, DILUTED_SHARE_ROWS),
        "ocf": row_points(cashflow, OCF_ROWS),
        "capex": row_points(cashflow, CAPEX_ROWS),
        "fcf": row_points(cashflow, FCF_ROWS),
        "cash": row_points(balance, CASH_ROWS),
        "cash_only": row_points(balance, CASH_ONLY_ROWS),
        "sti": row_points(balance, STI_ROWS),
        "debt": row_points(balance, DEBT_ROWS),
    }


def pick_earnings(ticker, today):
    frame = None
    source_name = "Yahoo Finance earnings_dates"
    try:
        frame = ticker.earnings_dates
    except Exception:
        frame = None
    if frame is not None:
        try:
            empty = frame.empty
        except AttributeError:
            empty = True
        if not empty and "Reported EPS" in frame.columns:
            for moment, row in frame.sort_index().iterrows():
                reported = row.get("Reported EPS")
                when = moment.to_pydatetime() if hasattr(moment, "to_pydatetime") else moment
                day = when.date() if isinstance(when, datetime) else when
                if day >= today and finite(reported) is None:
                    return when, source_name
    try:
        calendar = ticker.calendar or {}
    except Exception:
        calendar = {}
    dates = calendar.get("Earnings Date") or []
    for item in dates:
        day = item.date() if isinstance(item, datetime) else item
        if isinstance(day, date) and day >= today:
            return day, "Yahoo Finance calendar"
    return None, source_name


def pick_price(ticker, symbol, fetched_at):
    history = ticker.history(period="10d", auto_adjust=False)
    if history is None or history.empty or "Close" not in history.columns:
        return None
    row = history.dropna(subset=["Close"]).tail(1)
    if row.empty:
        return None
    moment = row.index[-1]
    day = moment.date() if hasattr(moment, "date") else moment
    close = finite(row["Close"].iloc[-1])
    if close is None:
        return None
    currency = None
    try:
        currency = getattr(ticker.fast_info, "currency", None)
    except Exception:
        currency = None
    return {
        "close": round(close, 2),
        "currency": currency or "USD",
        "date": day.isoformat(),
        "source": "Yahoo Finance",
        "sourceUrl": quote_url(symbol, "history"),
        "fetchedAt": fetched_at,
    }


def pick_profile(info, symbol, fetched_at, fallback):
    info = info or {}
    hq = headquarters(info.get("city"), info.get("country"))
    employees = info.get("fullTimeEmployees")
    market_cap = finite(info.get("marketCap"))
    currency = info.get("currency") or info.get("financialCurrency")
    if hq or employees or market_cap:
        from metricsbuild.compute import de_grouped, format_money
        profile = {
            "hq": hq,
            "employees": int(employees) if employees is not None else None,
            "employeesDisplay": de_grouped(int(employees), 0) if employees is not None else None,
            "marketCap": int(round(market_cap)) if market_cap else None,
            "marketCapDisplay": format_money(market_cap, currency or "USD") if market_cap else None,
            "currency": currency,
            "source": "Yahoo Finance",
            "sourceUrl": quote_url(symbol, "profile"),
            "fetchedAt": fetched_at,
        }
        return {key: value for key, value in profile.items()}
    return fallback


def build_document(row, bundle, info, earnings, price, profile, fetched_at, facts, thesis):
    symbol = row.get("yahoo") or row["ticker"]
    currency = (info or {}).get("financialCurrency") or (info or {}).get("currency") or (price or {}).get("currency") or "USD"
    financials = quote_url(symbol, "financials")
    cashflow_url = quote_url(symbol, "cash-flow")
    balance_url = quote_url(symbol, "balance-sheet")
    label_month = fiscal_month(info) or 0
    if row.get("sec", True) and facts:
        cik = facts.get("cik")
        sec_url = (
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
            if cik else financials
        )
        growth = confirm_or_replace_growth(
            bundle["revenue"], facts, currency, fetched_at, financials, sec_url, label_month
        )
    else:
        growth = revenue_growth(
            bundle["revenue"], currency, fetched_at, financials, fiscal_month=label_month
        )
    margin = gross_margin(
        bundle["revenue"], bundle["gross"], (info or {}).get("grossMargins"),
        currency, fetched_at, financials, fiscal_month=label_month,
    )
    cash = free_cash_flow(bundle["ocf"], bundle["capex"], bundle["fcf"], currency, fetched_at, cashflow_url)
    balance = cash_vs_debt(bundle["cash"], bundle["cash_only"], bundle["sti"], bundle["debt"], currency, fetched_at, balance_url)
    last_end = date.fromisoformat(growth["observedAt"]) if growth.get("observedAt") else None
    period = growth.get("period") or ""
    last_label = period.split(" vs. ")[0] if " vs. " in period else (quarter_label(last_end, label_month) if last_end else None)
    shares = dilution(
        bundle["basic"], bundle["diluted"], fetched_at, financials,
        fiscal_month=label_month, anchor=last_end,
    )
    moment, source_name = earnings
    upcoming = next_earnings(moment, fetched_at, quote_url(symbol, ""), last_label, source_name, last_end)
    metrics = [growth, margin, cash, balance, shares, upcoming]
    for metric in metrics:
        metric.pop("inputs", None)
    by_id = [metric["id"] for metric in metrics]
    if by_id != list(METRIC_ORDER):
        raise RuntimeError("metric order drifted: " + ",".join(by_id))
    return {
        "ticker": row["ticker"],
        "name": row["name"],
        "yahoo": symbol,
        "currency": currency,
        "asOf": fetched_at,
        "thesis": thesis or THESIS_PLACEHOLDER,
        "price": price,
        "profile": profile,
        "metrics": metrics,
    }


def write_document(document):
    directory = ROOT / "data" / document["ticker"]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "metrics.json"
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def refresh(rows, today=None):
    import yfinance as yf

    today = today or berlin_today()
    fetched_at = today.isoformat()
    cache_dir = ROOT / "cache" / "sec"
    try:
        cik_map = sec_cik_map(cache_dir)
    except Exception as error:
        print("SEC-Tickerliste nicht geladen:", error)
        cik_map = {}
    gaps = []
    for row in rows:
        ticker = row["ticker"]
        symbol = row.get("yahoo") or ticker
        print(f"== {ticker} ({symbol})")
        try:
            previous = previous_document(ticker)
            thesis = previous.get("thesis") or THESIS_PLACEHOLDER
            instrument = yf.Ticker(symbol)
            income = instrument.quarterly_income_stmt
            cashflow = instrument.quarterly_cashflow
            balance = instrument.quarterly_balance_sheet
            try:
                info = instrument.info or {}
            except Exception as error:
                print("  info nicht geladen:", error)
                info = {}
            facts = None
            if row.get("sec", True):
                cik = cik_map.get(symbol.split(".")[0].upper())
                if cik:
                    try:
                        facts = sec_facts(cik, cache_dir)
                        time.sleep(0.2)
                    except Exception as error:
                        print("  SEC nicht geladen:", error)
            document = build_document(
                row,
                series_map(income, cashflow, balance),
                info,
                pick_earnings(instrument, today),
                pick_price(instrument, symbol, fetched_at),
                pick_profile(info, symbol, fetched_at, previous_profile(ticker)),
                fetched_at,
                facts,
                thesis,
            )
            path = write_document(document)
            for metric in document["metrics"]:
                shown = metric["display"] or "—"
                if metric["value"] is None:
                    gaps.append(f"{ticker} {metric['id']}: — ({metric.get('note') or 'keine Zahl'})")
                print(f"  {metric['label']}: {shown} | {metric.get('period') or '—'} | {metric['source']}")
            print("  geschrieben", path.relative_to(ROOT))
        except Exception:
            traceback.print_exc()
            gaps.append(f"{ticker} ABRUF FEHLGESCHLAGEN")
        time.sleep(0.4)
    print("\nLücken:")
    if not gaps:
        print("  keine")
    else:
        for line in gaps:
            print(" ", line)
    failed = [line for line in gaps if line.endswith("ABRUF FEHLGESCHLAGEN")]
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Kennzahlen aus Yahoo Finance schreiben.")
    parser.add_argument("--ticker", action="append", help="Nur diesen Ticker (mehrfach erlaubt).")
    args = parser.parse_args(argv)
    return refresh(load_tickers(args.ticker))


if __name__ == "__main__":
    raise SystemExit(main())
