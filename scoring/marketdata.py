"""Yahoo quoteSummary and Nasdaq fallbacks.

SEC stays first. These calls fill gaps only. A failed request is an empty
result, never a zero. Phase 1 may use these public endpoints; a paid product
needs licensed sources.
"""

from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from scoring.prices import USER_AGENT, _yahoo_open

YAHOO_MODULES = ",".join([
    "assetProfile",
    "price",
    "summaryDetail",
    "defaultKeyStatistics",
    "financialData",
    "incomeStatementHistoryQuarterly",
    "cashflowStatementHistoryQuarterly",
    "balanceSheetHistoryQuarterly",
    "recommendationTrend",
    "earningsTrend",
    "upgradeDowngradeHistory",
])


def _raw(node):
    if isinstance(node, dict) and "raw" in node:
        node = node.get("raw")
    if isinstance(node, bool) or not isinstance(node, (int, float)):
        return None
    return float(node)


def _text(node):
    if isinstance(node, str) and node.strip():
        return node.strip()
    return None


def _whole(node):
    value = _raw(node)
    if value is None and isinstance(node, (int, float)) and not isinstance(node, bool):
        value = float(node)
    if value is None:
        return None
    return int(round(value))


_COUNTRY_DE = {
    "United States": "USA",
    "United States of America": "USA",
    "USA": "USA",
    "Sweden": "Schweden",
    "Singapore": "Singapur",
    "Australia": "Australien",
    "Ireland": "Irland",
    "United Kingdom": "Vereinigtes Königreich",
    "Germany": "Deutschland",
    "Netherlands": "Niederlande",
    "The Netherlands": "Niederlande",
    "Cayman Islands": "Kaimaninseln",
    "China": "China",
    "Hong Kong": "Hongkong",
    "Israel": "Israel",
    "Canada": "Kanada",
    "France": "Frankreich",
    "Japan": "Japan",
    "South Korea": "Südkorea",
    "Taiwan": "Taiwan",
    "Switzerland": "Schweiz",
    "Luxembourg": "Luxemburg",
    "Bermuda": "Bermuda",
    "Brazil": "Brasilien",
    "India": "Indien",
}


def _hq(asset):
    city = _text(asset.get("city"))
    country_raw = _text(asset.get("country"))
    country = _COUNTRY_DE.get(country_raw, country_raw) if country_raw else None
    if city and country:
        if city.casefold() == country.casefold() or city.casefold() == (country_raw or "").casefold():
            return country
        return city + ", " + country
    return city or country


def profile_from_yahoo(summary, today):
    """Seat, employees and market cap from quoteSummary. Missing facts are omitted."""
    if not summary:
        return None
    asset = summary.get("assetProfile") or {}
    price = summary.get("price") or {}
    detail = summary.get("summaryDetail") or {}
    profile = {}
    hq = _hq(asset)
    if hq:
        profile["hq"] = hq
    employees = asset.get("fullTimeEmployees")
    staff = _whole(employees)
    if staff is not None:
        profile["employees"] = staff
    market_cap = _raw(price.get("marketCap"))
    currency = _text(price.get("currency"))
    if market_cap is None:
        market_cap = _raw(detail.get("marketCap"))
        currency = currency or _text(detail.get("currency"))
    else:
        currency = currency or _text(detail.get("currency"))
    if market_cap is not None:
        profile["marketCap"] = int(round(market_cap))
    if currency:
        profile["currency"] = currency
    if not any(key in profile for key in ("hq", "employees", "marketCap")):
        return None
    profile["source"] = "Yahoo Finance quoteSummary"
    profile["fetchedAt"] = today
    return profile


def _yahoo_summary(symbol, modules=None):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    page = "https://finance.yahoo.com/quote/" + urllib.parse.quote(symbol)
    try:
        _yahoo_open(opener, page, "text/html,application/xhtml+xml")
        crumb = _yahoo_open(
            opener,
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            "text/plain,*/*",
        ).decode("utf-8", "replace").strip()
        if not crumb or crumb[:1] in {"<", "{"}:
            return None
        url = (
            "https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
            + urllib.parse.quote(symbol)
            + "?modules=" + (modules or YAHOO_MODULES)
            + "&crumb=" + urllib.parse.quote(crumb)
        )
        payload = json.loads(_yahoo_open(opener, url, "application/json").decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None
    result = ((payload.get("quoteSummary") or {}).get("result") or [None])[0]
    if not isinstance(result, dict):
        return None
    result["_url"] = "https://finance.yahoo.com/quote/" + urllib.parse.quote(symbol)
    return result


def load_yahoo_summary(symbol, cache_dir):
    if not symbol:
        return None
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / ("summary-" + symbol.replace("/", "_") + ".json")
    if path.exists() and path.stat().st_size > 0:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cached = None
        if isinstance(cached, dict) and (
            cached.get("defaultKeyStatistics") or cached.get("financialData") or cached.get("recommendationTrend")
        ):
            return cached
    payload = _yahoo_summary(symbol)
    if not payload:
        return None
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _nasdaq_json(url):
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload.get("data")


def load_nasdaq(kind, ticker, cache_dir):
    if not ticker:
        return None
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / (kind + "-" + ticker + ".json")
    if path.exists() and path.stat().st_size > 0:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    urls = {
        "target": "https://api.nasdaq.com/api/analyst/" + urllib.parse.quote(ticker) + "/targetprice",
        "financials": "https://api.nasdaq.com/api/company/" + urllib.parse.quote(ticker) + "/financials?frequency=1",
        "quarterly": "https://api.nasdaq.com/api/company/" + urllib.parse.quote(ticker) + "/financials?frequency=2",
    }
    data = _nasdaq_json(urls[kind])
    if not data:
        return None
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _epoch_date(value):
    raw = _raw(value) if not isinstance(value, dict) else _raw(value)
    if isinstance(value, dict) and value.get("fmt"):
        text = str(value.get("fmt"))[:10]
        if len(text) == 10 and text[4] == "-":
            return text
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(raw, timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def analyst_from_yahoo(summary, price, today):
    if not summary:
        return None
    trend = ((summary.get("recommendationTrend") or {}).get("trend") or [])
    current = next((row for row in trend if row.get("period") == "0m"), None)
    previous = next((row for row in trend if row.get("period") == "-3m"), None)
    if not current:
        return None
    parts = [_raw(current.get(name)) for name in ("strongBuy", "buy", "hold", "sell", "strongSell")]
    if any(part is None for part in parts):
        return None
    strong, buy, hold, sell, strong_sell = parts
    total = strong + buy + hold + sell + strong_sell
    if total <= 0:
        return None
    financial = summary.get("financialData") or {}
    mean = _raw(financial.get("targetMeanPrice"))
    median = _raw(financial.get("targetMedianPrice"))
    low = _raw(financial.get("targetLowPrice"))
    high = _raw(financial.get("targetHighPrice"))
    upside = None
    if mean is not None and price and price > 0:
        upside = mean / price - 1.0
    revision = _eps_revision(summary)
    changes = []
    history = ((summary.get("upgradeDowngradeHistory") or {}).get("history") or [])
    for row in history:
        if not isinstance(row, dict):
            continue
        changes.append({
            "date": _epoch_date(row.get("epochGradeDate")),
            "firm": row.get("firm"),
            "grade": row.get("toGrade") or row.get("fromGrade"),
            "target": _raw(row.get("currentPriceTarget") or row.get("priceTarget")),
        })
        if len(changes) == 5:
            break
    url = summary.get("_url") or "https://finance.yahoo.com/"
    return {
        "buyShare": (strong + buy) / total,
        "targetUpside": upside,
        "epsRevision": revision,
        "revenueEstimateYoy": revenue_estimate_growth(summary),
        "count": int(total),
        "observed": None,
        "label": "Yahoo Finance quoteSummary",
        "url": url,
        "summary": {
            "current": _bucket(current),
            "threeMonthsAgo": _bucket(previous) if previous else None,
            "target": {"mean": mean, "median": median, "low": low, "high": high, "count": int(total)},
            "changes": changes,
            "sourceLabel": "Yahoo Finance quoteSummary",
            "sourceUrl": url,
            "fetchedAt": today,
        },
    }


def _bucket(row):
    if not row:
        return None
    return {
        "strongBuy": _raw(row.get("strongBuy")),
        "buy": _raw(row.get("buy")),
        "hold": _raw(row.get("hold")),
        "sell": _raw(row.get("sell")),
        "strongSell": _raw(row.get("strongSell")),
    }


def _eps_revision(summary):
    trend = ((summary.get("earningsTrend") or {}).get("trend") or [])
    row = next((item for item in trend if item.get("period") == "+1y"), None)
    if not row:
        return None
    eps_trend = row.get("epsTrend") or {}
    current = _raw(eps_trend.get("current"))
    ago = _raw(eps_trend.get("90daysAgo"))
    if current is None or ago is None or ago == 0:
        return None
    return (current - ago) / abs(ago)


def analyst_from_nasdaq(target, price, today, ticker):
    overview = (target or {}).get("consensusOverview") or {}
    buy = overview.get("buy")
    hold = overview.get("hold")
    sell = overview.get("sell")
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (buy, hold, sell)):
        return None
    total = buy + hold + sell
    if total <= 0:
        return None
    mean = overview.get("priceTarget")
    upside = None
    if isinstance(mean, (int, float)) and price and price > 0:
        upside = float(mean) / price - 1.0
    url = "https://api.nasdaq.com/api/analyst/" + urllib.parse.quote(ticker) + "/targetprice"
    return {
        "buyShare": buy / total,
        "targetUpside": upside,
        "epsRevision": None,
        "count": int(total),
        "observed": None,
        "label": "Nasdaq Analysten",
        "url": url,
        "summary": {
            "current": {"strongBuy": None, "buy": buy, "hold": hold, "sell": sell, "strongSell": None},
            "threeMonthsAgo": None,
            "target": {
                "mean": mean,
                "median": None,
                "low": overview.get("lowPriceTarget"),
                "high": overview.get("highPriceTarget"),
                "count": int(total),
            },
            "changes": [],
            "sourceLabel": "Nasdaq Analysten",
            "sourceUrl": url,
            "fetchedAt": today,
        },
    }


def _money(text):
    if text is None:
        return None
    cleaned = str(text).replace("$", "").replace(",", "").replace(" ", "").strip()
    if cleaned in {"", "--", "N/A", "n/a"}:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def _table_series(table):
    headers = (table or {}).get("headers") or {}
    periods = []
    for key in ("value2", "value3", "value4", "value5"):
        label = headers.get(key)
        if not label or "Ending" in str(label):
            continue
        periods.append((key, label))
    rows = {}
    for row in (table or {}).get("rows") or []:
        name = row.get("value1")
        if name:
            rows[name] = row
    return periods, rows


def _annual_points(table, row_name):
    periods, rows = _table_series(table)
    row = rows.get(row_name)
    if not row:
        return []
    points = []
    for key, label in periods:
        value = _money(row.get(key))
        end = _us_date(label)
        if value is None or not end:
            continue
        points.append({"end": end, "val": value, "filed": end, "form": "nasdaq"})
    points.sort(key=lambda point: point["end"])
    return points


def _us_date(label):
    text = str(label).strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _positive_share_series(series):
    for point in series or []:
        value = point.get("val")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return True
    return False


def revenue_estimate_growth(summary):
    """Analyst revenue growth for next fiscal year. None when the row is absent."""
    trend = ((summary or {}).get("earningsTrend") or {}).get("trend") or []
    row = next((item for item in trend if item.get("period") == "+1y"), None)
    if not row:
        return None
    estimate = row.get("revenueEstimate") or {}
    return _raw(estimate.get("growth"))


def apply_fundamental_fallback(bundle, yahoo, nasdaq_annual, source_url, today, nasdaq_quarterly=None):
    """Fill only values the SEC bundle does not already have.

    Quarterly Yahoo points stay in the snapshot. They are not written into the
    annual series, so a CAGR is never computed from four quarters.
    """
    bundle = bundle or {}
    for key in (
        "revenue", "gross_profit", "operating_income", "net_income", "eps", "fcf",
        "equity", "assets_current", "liabilities_current", "cash", "debt",
        "shares_out", "shares_diluted", "dividends", "cfo", "capex",
    ):
        bundle.setdefault(key, [])
    snapshot = dict(bundle.get("snapshot") or {})
    filled = False
    stats = (yahoo or {}).get("defaultKeyStatistics") or {}
    shares_missing = not _positive_share_series([{"val": snapshot.get("shares_out")}]) and not _positive_share_series(bundle["shares_out"])
    if shares_missing:
        shares = _raw(stats.get("sharesOutstanding"))
        if shares and shares > 0:
            snapshot["shares_out"] = shares
            snapshot["sharesSourceLabel"] = "Yahoo Finance quoteSummary"
            snapshot["sharesSourceUrl"] = source_url
            filled = True
    if not snapshot.get("revenue") and yahoo:
        revenue = _yahoo_quarter_series(yahoo, "incomeStatementHistoryQuarterly", ["incomeStatementHistory"], "totalRevenue")
        gross = _yahoo_quarter_series(yahoo, "incomeStatementHistoryQuarterly", ["incomeStatementHistory"], "grossProfit")
        operating = _yahoo_quarter_series(yahoo, "incomeStatementHistoryQuarterly", ["incomeStatementHistory"], "operatingIncome")
        net_income = _yahoo_quarter_series(yahoo, "incomeStatementHistoryQuarterly", ["incomeStatementHistory"], "netIncome")
        cfo = _yahoo_quarter_series(
            yahoo, "cashflowStatementHistoryQuarterly",
            ["cashflowStatements", "cashflowStatementHistory"],
            "totalCashFromOperatingActivities",
        )
        capex = _yahoo_quarter_series(
            yahoo, "cashflowStatementHistoryQuarterly",
            ["cashflowStatements", "cashflowStatementHistory"],
            "capitalExpenditures",
        )
        if revenue:
            fcf = _pair_fcf(cfo, capex) if cfo and capex else []
            if _placeholder_zeros(gross, revenue):
                gross = []
            if _placeholder_zeros(operating, revenue):
                operating = []
            _snapshot_from_quarters(snapshot, revenue, gross, operating, net_income, cfo, fcf)
            snapshot["sourceLabel"] = "Yahoo Finance quoteSummary"
            snapshot["sourceUrl"] = source_url
            filled = True
    # Nasdaq annuals only when the issuer has no SEC reporting window.
    # A short SEC history (one 10-Q) must not pick up a second source's years.
    if not snapshot.get("revenue") and not bundle["revenue"] and not bundle.get("_history_start") and nasdaq_annual:
        revenue = _annual_points((nasdaq_annual.get("incomeStatementTable") or {}), "Total Revenue")
        gross = _annual_points((nasdaq_annual.get("incomeStatementTable") or {}), "Gross Profit")
        if revenue:
            bundle["revenue"] = revenue
            bundle["gross_profit"] = gross
            if len(revenue) >= 2 and revenue[-2]["val"]:
                snapshot.update({
                    "end": revenue[-1]["end"],
                    "label": "Nasdaq Geschäftsjahr " + revenue[-1]["end"],
                    "months": 12.0,
                    "revenue": revenue[-1]["val"],
                    "revenue_prior": revenue[-2]["val"],
                    "sourceLabel": "Nasdaq Financials",
                    "sourceUrl": source_url,
                })
                if gross and gross[-1]["end"] == revenue[-1]["end"]:
                    snapshot["gross_profit"] = gross[-1]["val"]
            filled = True
    if snapshot:
        refresh_stale_fields(snapshot, yahoo, nasdaq_quarterly, source_url, today)
        if not snapshot.get("fetchedAt"):
            snapshot["fetchedAt"] = today
        bundle["snapshot"] = snapshot
    bundle["_fallback_used"] = filled or bool(snapshot.get("_refreshed"))
    return bundle


def _approx_zero_debt(debt, cash):
    if debt is None:
        return False
    if abs(debt) <= 1000:
        return True
    if cash is not None and cash > 0 and abs(debt) <= 0.01 * cash:
        return True
    return False


def refresh_stale_fields(snapshot, yahoo, nasdaq_quarterly, source_url, today):
    """Replace a SEC figure only when a newer Yahoo or Nasdaq figure exists.

    Order is SEC, then Yahoo, then Nasdaq. An explicit SEC zero for financial
    debt stays zero: Yahoo totalDebt includes leases and is not a substitute.
    Missing debt plus Yahoo debt near zero means net cash equals cash.
    """
    if not snapshot:
        return snapshot
    period = snapshot.get("end")
    provenance = dict(snapshot.get("provenance") or {})
    financial = (yahoo or {}).get("financialData") or {}
    yahoo_label = "Yahoo Finance quoteSummary"
    nasdaq_label = "Nasdaq Financials"
    refreshed = False

    def stamp(field, label, url, end, note=None):
        entry = {"label": label, "url": url, "end": end, "fetchedAt": today}
        if note:
            entry["note"] = note
        provenance[field] = entry

    def dated_balance(field_names):
        points = []
        for name in field_names:
            points.extend(_yahoo_quarter_series(
                yahoo or {}, "balanceSheetHistoryQuarterly",
                ["balanceSheetStatements", "balanceSheetHistory"], name,
            ))
        points = [point for point in points if point["val"] is not None]
        if not points:
            return None
        return points[-1]

    cash_end = snapshot.get("cash_end")
    if snapshot.get("cash") is None or (period and cash_end and cash_end < period):
        point = dated_balance(["cash", "cashAndCashEquivalents", "cashCashEquivalentsAndShortTermInvestments"])
        yahoo_cash = _raw(financial.get("totalCash"))
        if point and (cash_end is None or point["end"] > cash_end):
            snapshot["cash"] = point["val"]
            snapshot["cash_end"] = point["end"]
            snapshot["balance_end"] = point["end"]
            stamp("cash", yahoo_label, source_url, point["end"])
            refreshed = True
        elif yahoo_cash is not None and (snapshot.get("cash") is None or (period and cash_end and cash_end < period)):
            snapshot["cash"] = yahoo_cash
            snapshot["cash_end"] = None
            stamp("cash", yahoo_label + " financialData", source_url, None, "financialData totalCash, Stichtag nicht ausgewiesen")
            refreshed = True

    # Explicit SEC zero is the debt figure. Do not replace it with totalDebt.
    if snapshot.get("debt") is None:
        point = dated_balance(["totalDebt", "longTermDebt", "shortLongTermDebt"])
        yahoo_debt = _raw(financial.get("totalDebt"))
        chosen = None
        label = yahoo_label
        note = None
        end = None
        if point and (snapshot.get("debt_end") is None or point["end"] >= (snapshot.get("debt_end") or "")):
            chosen = point["val"]
            end = point["end"]
        elif yahoo_debt is not None:
            chosen = yahoo_debt
            end = period
            label = yahoo_label + " financialData"
            note = "totalDebt"
        if chosen is not None and _approx_zero_debt(chosen, snapshot.get("cash")):
            snapshot["debt"] = 0.0
            snapshot["debt_explicit_zero"] = True
            snapshot["debt_end"] = end
            stamp("debt", label, source_url, end, "Finanzschulden nahe 0, Netto-Cash = Cash")
            refreshed = True
        elif chosen is not None:
            snapshot["debt"] = chosen
            snapshot["debt_end"] = end
            stamp("debt", label, source_url, end, note or "totalDebt, kann Leasing enthalten")
            refreshed = True
    elif snapshot.get("debt_explicit_zero"):
        stamp(
            "debt",
            (provenance.get("debt") or {}).get("label") or "SEC EDGAR companyfacts",
            (provenance.get("debt") or {}).get("url"),
            snapshot.get("debt_end"),
            "Finanzschulden explizit 0",
        )

    def fill_flow(field, end_key, yahoo_names, financial_key, nasdaq_row):
        nonlocal refreshed
        current_end = snapshot.get(end_key)
        missing = snapshot.get(field) is None
        stale = bool(period and current_end and current_end < period)
        if not missing and not stale:
            return
        series = []
        for name in yahoo_names:
            series = _yahoo_quarter_series(
                yahoo or {}, "cashflowStatementHistoryQuarterly",
                ["cashflowStatements", "cashflowStatementHistory"], name,
            )
            if series:
                break
        if len(series) >= 4 and (missing or series[-1]["end"] > (current_end or "")):
            window = series[-4:]
            snapshot[field] = sum(point["val"] for point in window)
            snapshot[end_key] = window[-1]["end"]
            stamp(field, yahoo_label, source_url, window[-1]["end"], "TTM aus Quartals-Cashflow")
            refreshed = True
            return
        raw = _raw(financial.get(financial_key))
        if raw is not None and (missing or stale):
            snapshot[field] = raw
            snapshot[end_key] = None
            stamp(field, yahoo_label + " financialData", source_url, None, "financialData, Stichtag nicht ausgewiesen")
            refreshed = True
            return
        if nasdaq_quarterly and nasdaq_row:
            points = _annual_points((nasdaq_quarterly.get("cashFlowStatementTable") or nasdaq_quarterly.get("cashFlowTable") or {}), nasdaq_row)
            if not points:
                for table in nasdaq_quarterly.values():
                    if isinstance(table, dict) and table.get("rows"):
                        found = _annual_points(table, nasdaq_row)
                        if found:
                            points = found
                            break
            if points and (missing or points[-1]["end"] > (current_end or "")):
                snapshot[field] = points[-1]["val"]
                snapshot[end_key] = points[-1]["end"]
                stamp(field, nasdaq_label, source_url, points[-1]["end"])
                refreshed = True

    fill_flow(
        "cfo", "cfo_end",
        ["totalCashFromOperatingActivities", "operatingCashFlow"],
        "operatingCashflow", "Net Cash Flow-Operating",
    )
    fill_flow(
        "fcf", "fcf_end",
        ["freeCashFlow"],
        "freeCashflow", "Free Cash Flow",
    )
    # Free cash flow cannot exceed operating cash flow when both come from the
    # same undated Yahoo block. Those pairs are dropped instead of scored.
    cfo_label = (provenance.get("cfo") or {}).get("label") or ""
    fcf_label = (provenance.get("fcf") or {}).get("label") or ""
    if (
        snapshot.get("cfo") is not None
        and snapshot.get("fcf") is not None
        and snapshot["fcf"] > snapshot["cfo"]
        and cfo_label.startswith("Yahoo")
        and fcf_label.startswith("Yahoo")
    ):
        snapshot["fcf"] = None
        provenance.pop("fcf", None)

    ratio = _raw(financial.get("currentRatio"))
    assets_end = snapshot.get("assets_current_end")
    if ratio is not None and (snapshot.get("assets_current") is None or (period and assets_end and assets_end < period)):
        snapshot["current_ratio"] = ratio
        stamp("current_ratio", yahoo_label + " financialData", source_url, None, "financialData currentRatio")
        refreshed = True

    roe = _raw(financial.get("returnOnEquity"))
    if roe is not None and snapshot.get("equity") is None:
        snapshot["roe"] = roe
        stamp("roe", yahoo_label + " financialData", source_url, None, "financialData returnOnEquity")
        refreshed = True

    if snapshot.get("revenue_quarter_yoy") is None:
        growth = _raw(financial.get("revenueGrowth"))
        if growth is not None:
            snapshot["revenue_quarter_yoy"] = growth
            snapshot["revenue_quarter_end"] = period
            snapshot["revenue_quarter_label"] = "Yahoo revenueGrowth"
            stamp("revenue_quarter", yahoo_label + " financialData", source_url, period, "revenueGrowth, in der Regel das letzte Quartal zum Vorjahr")
            refreshed = True

    if snapshot.get("gross_profit") is None:
        margin = _raw(financial.get("grossMargins"))
        if margin is not None and snapshot.get("revenue"):
            snapshot["gross_profit"] = margin * snapshot["revenue"]
            stamp("gross_profit", yahoo_label + " financialData", source_url, period, "Bruttomarge aus financialData")
            refreshed = True
    if snapshot.get("operating_income") is None:
        margin = _raw(financial.get("operatingMargins"))
        if margin is not None and snapshot.get("revenue"):
            snapshot["operating_income"] = margin * snapshot["revenue"]
            stamp("operating_income", yahoo_label + " financialData", source_url, period, "operative Marge aus financialData")
            refreshed = True

    snapshot["provenance"] = provenance
    if refreshed:
        snapshot["_refreshed"] = True
    return snapshot


def _placeholder_zeros(series, revenue):
    """Yahoo sometimes returns 0 for a missing line while revenue is present."""
    if not series or not revenue:
        return False
    if not any(point["val"] for point in revenue):
        return False
    return all(point["val"] == 0 for point in series)


def _yahoo_quarter_series(summary, module, keys, field):
    block = summary.get(module) or {}
    rows = []
    for key in keys:
        found = block.get(key) or []
        if found:
            rows = found
            break
    points = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        end = None
        stamp = row.get("endDate")
        if isinstance(stamp, dict) and stamp.get("fmt"):
            end = str(stamp["fmt"])[:10]
        value = _raw(row.get(field))
        if end and value is not None:
            points.append({"end": end, "val": value, "filed": end, "form": "yahoo"})
    points.sort(key=lambda point: point["end"])
    # Yahoo quarters are single quarters. Sum is not a year. Keep them as points
    # so a later snapshot can use the latest four.
    return points


def _pair_fcf(cfo, capex):
    capex_by_end = {point["end"]: point["val"] for point in capex}
    points = []
    for point in cfo:
        if point["end"] not in capex_by_end:
            continue
        points.append({**point, "val": point["val"] - abs(capex_by_end[point["end"]])})
    return points


def _snapshot_from_quarters(snapshot, revenue, gross, operating, net_income, cfo, fcf):
    if len(revenue) < 4:
        if revenue:
            snapshot.setdefault("end", revenue[-1]["end"])
            snapshot.setdefault("revenue", revenue[-1]["val"])
            snapshot.setdefault("label", "Yahoo Quartal " + revenue[-1]["end"])
            snapshot.setdefault("months", 3.0)
        return
    window = revenue[-4:]
    prior = revenue[-8:-4] if len(revenue) >= 8 else []
    snapshot["end"] = window[-1]["end"]
    snapshot["revenue"] = sum(point["val"] for point in window)
    snapshot["months"] = 12.0
    snapshot["label"] = "TTM bis " + window[-1]["end"] + " (Yahoo)"
    if len(prior) == 4 and sum(point["val"] for point in prior):
        snapshot["revenue_prior"] = sum(point["val"] for point in prior)

    def _sum_match(series):
        by_end = {point["end"]: point["val"] for point in series}
        if not all(point["end"] in by_end for point in window):
            return None
        return sum(by_end[point["end"]] for point in window)

    gross_sum = _sum_match(gross)
    if gross_sum is not None:
        snapshot["gross_profit"] = gross_sum
    operating_sum = _sum_match(operating)
    if operating_sum is not None:
        snapshot["operating_income"] = operating_sum
    net_sum = _sum_match(net_income)
    if net_sum is not None:
        snapshot["net_income"] = net_sum
    cfo_sum = _sum_match(cfo)
    if cfo_sum is not None:
        snapshot["cfo"] = cfo_sum
    fcf_sum = _sum_match(fcf)
    if fcf_sum is not None:
        snapshot["fcf"] = fcf_sum
