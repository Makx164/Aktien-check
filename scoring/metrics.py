"""Turn annual series and daily prices into criterion metrics.

Formulas live here. The 0–10 ramps are read from config/scoring.json.
Missing inputs stay None and are marked „wenig Daten“. Nothing is filled
in with zero or a guess, and a missing metric never scores 0.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta

from scoring.model import apply_analyst_cap, band_points, format_value, linear_points, mean, round1

ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
POSITION_FORMS = ANNUAL_FORMS | {"10-Q", "10-Q/A", "6-K"}


def parse_date(value):
    if not value:
        return None
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def year_gap(later, earlier):
    return (parse_date(later) - parse_date(earlier)).days / 365.25


def _dedupe(rows):
    best = {}
    for row in rows:
        end = row.get("end")
        if not end:
            continue
        current = best.get(end)
        if current is None or (row.get("filed") or "") >= (current.get("filed") or ""):
            best[end] = row
    points = []
    for end in sorted(best):
        row = best[end]
        points.append({
            "end": end,
            "val": float(row["val"]),
            "filed": row.get("filed") or "",
            "form": row.get("form"),
        })
    return points


def annual_series(facts, concepts, units):
    """Latest annual filing per period end. Prefer the concept with the newest year."""
    chosen = []
    chosen_key = None
    for rank, name in enumerate(concepts):
        concept = _find_concept(facts, name)
        if not concept:
            continue
        rows = _rows(concept, units)
        annual = [
            row for row in rows
            if row.get("form") in ANNUAL_FORMS
            and row.get("fp") == "FY"
            and isinstance(row.get("val"), (int, float))
            and not isinstance(row.get("val"), bool)
        ]
        series = _dedupe(annual)
        if not series:
            continue
        key = (series[-1]["end"], -rank)
        if chosen_key is None or key > chosen_key:
            chosen = series
            chosen_key = key
    return chosen


def reported_window(facts, concepts, units):
    """Earliest period start and latest period end, including quarterly filings.

    Used when the annual series is empty, so a newly listed issuer is not
    treated as a failed fetch. Instant facts (no duration) are ignored.
    """
    chosen = []
    chosen_key = None
    for rank, name in enumerate(concepts):
        concept = _find_concept(facts, name)
        if not concept:
            continue
        rows = []
        for row in _rows(concept, units):
            if row.get("form") not in POSITION_FORMS:
                continue
            start = row.get("start")
            end = row.get("end")
            if not start or not end or start >= end:
                continue
            if not isinstance(row.get("val"), (int, float)) or isinstance(row.get("val"), bool):
                continue
            rows.append(row)
        if not rows:
            continue
        key = (max(row["end"] for row in rows), -rank)
        if chosen_key is None or key > chosen_key:
            chosen = rows
            chosen_key = key
    if not chosen:
        return None, None
    return min(row["start"] for row in chosen), max(row["end"] for row in chosen)


def latest_position(facts, concepts, units):
    """Most recent balance-sheet or share-count point, including 10-Q."""
    chosen = []
    chosen_key = None
    for rank, name in enumerate(concepts):
        concept = _find_concept(facts, name)
        if not concept:
            continue
        rows = []
        for row in _rows(concept, units):
            if row.get("form") not in POSITION_FORMS:
                continue
            if not isinstance(row.get("val"), (int, float)) or isinstance(row.get("val"), bool):
                continue
            start = row.get("start")
            end = row.get("end")
            if not end or (start and start != end):
                continue
            rows.append(row)
        series = _dedupe(rows)
        if not series:
            continue
        key = (series[-1]["end"], -rank)
        if chosen_key is None or key > chosen_key:
            chosen = series
            chosen_key = key
    return chosen


def _find_concept(facts, name):
    for namespace in ("us-gaap", "ifrs-full", "dei"):
        bucket = facts.get(namespace) or {}
        if name in bucket:
            return bucket[name]
    return None


def _rows(concept, units):
    available = concept.get("units") or {}
    for unit in units:
        if unit in available:
            return available[unit]
    if len(available) == 1 and "shares" not in units:
        return next(iter(available.values()))
    return []


def at(series, end):
    for point in series:
        if point["end"] == end:
            return point
    return None


def value_at(series, end):
    point = at(series, end)
    return None if point is None else point["val"]


def point_years_before(series, end, years, tolerance=0.6):
    target = years
    best = None
    best_gap = None
    for point in series:
        if point["end"] >= end:
            continue
        gap = year_gap(end, point["end"])
        distance = abs(gap - target)
        if distance > tolerance:
            continue
        if best_gap is None or distance < best_gap:
            best = point
            best_gap = distance
    return best


def cagr(latest, earlier):
    if latest is None or earlier is None:
        return None
    if latest["val"] <= 0 or earlier["val"] <= 0:
        return None
    years = year_gap(latest["end"], earlier["end"])
    if years <= 0:
        return None
    return (latest["val"] / earlier["val"]) ** (1.0 / years) - 1.0


def yoy(series, end):
    current = at(series, end)
    previous = point_years_before(series, end, 1, tolerance=0.4)
    if current is None or previous is None or previous["val"] <= 0:
        return None
    return current["val"] / previous["val"] - 1.0


def derived_gross_profit(revenue, cogs, reported):
    if reported and revenue and reported[-1]["end"] >= revenue[-1]["end"]:
        return reported
    points = []
    for point in revenue:
        cost = value_at(cogs, point["end"])
        if cost is None:
            continue
        points.append({
            "end": point["end"],
            "val": point["val"] - cost,
            "filed": point["filed"],
            "form": point["form"],
        })
    return points


def fcf_series(cfo, capex):
    points = []
    for point in cfo:
        spend = value_at(capex, point["end"])
        if spend is None:
            continue
        points.append({
            "end": point["end"],
            "val": point["val"] - abs(spend),
            "filed": point["filed"],
            "form": point["form"],
        })
    return points


def gross_margin_series(revenue, gross):
    points = []
    for point in gross:
        sales = value_at(revenue, point["end"])
        if sales is None or sales <= 0:
            continue
        points.append({**point, "val": point["val"] / sales})
    return points


def ratio(numerator, denominator):
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _latest_end(*series):
    ends = [item[-1]["end"] for item in series if item]
    if not ends:
        return None
    return max(ends)


def roic_for_year(end, bundle):
    operating = value_at(bundle["operating_income"], end)
    tax = value_at(bundle["tax"], end)
    pretax = value_at(bundle["pretax"], end)
    equity = value_at(bundle["equity"], end)
    debt = value_at(bundle["debt"], end)
    cash = value_at(bundle["cash"], end)
    if None in (operating, tax, pretax, equity, debt, cash):
        return None
    if pretax <= 0:
        return None
    tax_rate = tax / pretax
    if tax_rate < -0.5 or tax_rate > 1:
        return None
    invested = equity + debt - cash
    if invested <= 0:
        return None
    return operating * (1.0 - tax_rate) / invested


def ebitda_at(end, bundle):
    operating = value_at(bundle["operating_income"], end)
    depreciation = value_at(bundle["da"], end)
    if operating is None or depreciation is None:
        return None
    return operating + abs(depreciation)


def build_debt_series(facts):
    """Interest-bearing debt. Long-term debt is not added twice."""
    long_term = annual_series(facts, [
        "LongTermDebt",
        "Borrowings",
    ], ["USD"])
    short_term = annual_series(facts, [
        "ShortTermBorrowings",
        "ShorttermBorrowings",
        "CommercialPaper",
    ], ["USD"])
    if long_term:
        points = []
        for point in long_term:
            extra = value_at(short_term, point["end"])
            points.append({**point, "val": point["val"] + (extra or 0.0 if short_term else 0.0)})
        # If the short-term concept is missing entirely, do not invent zero.
        if not short_term:
            return long_term
        return points
    parts = [
        annual_series(facts, ["LongTermDebtNoncurrent", "LongtermBorrowings"], ["USD"]),
        annual_series(facts, [
            "LongTermDebtCurrent",
            "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
        ], ["USD"]),
        short_term,
    ]
    ends = sorted({point["end"] for series in parts for point in series})
    points = []
    for end in ends:
        present = [value_at(series, end) for series in parts if series]
        present = [value for value in present if value is not None]
        if not present:
            continue
        filed = ""
        for series in parts:
            point = at(series, end)
            if point and point["filed"] >= filed:
                filed = point["filed"]
        points.append({"end": end, "val": sum(present), "filed": filed, "form": "10-K"})
    return points


def build_pretax_series(facts):
    direct = annual_series(facts, [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "ProfitLossBeforeTax",
    ], ["USD"])
    if direct:
        return direct
    domestic = annual_series(facts, [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",
    ], ["USD"])
    foreign = annual_series(facts, [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesForeign",
    ], ["USD"])
    if not domestic or not foreign:
        return []
    points = []
    for point in domestic:
        other = value_at(foreign, point["end"])
        if other is None:
            continue
        points.append({**point, "val": point["val"] + other})
    return points


def build_liabilities_series(facts, assets, equity):
    reported = annual_series(facts, ["Liabilities"], ["USD"])
    if reported:
        return reported
    points = []
    for point in assets:
        book = value_at(equity, point["end"])
        if book is None:
            continue
        liabilities = point["val"] - book
        if liabilities <= 0:
            continue
        points.append({**point, "val": liabilities})
    return points


def extract_fundamentals(facts):
    """Annual series used by the scorers. facts is a companyfacts `facts` object."""
    revenue_concepts = [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenue",
        "RevenueFromContractsWithCustomers",
        "SalesRevenueNet",
        "Revenues",
    ]
    revenue = annual_series(facts, revenue_concepts, ["USD"])
    history_start, history_end = reported_window(facts, revenue_concepts, ["USD"])
    cogs = annual_series(facts, [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
    ], ["USD"])
    reported_gross = annual_series(facts, ["GrossProfit"], ["USD"])
    gross = derived_gross_profit(revenue, cogs, reported_gross)
    operating = annual_series(facts, ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"], ["USD"])
    net_income = annual_series(facts, [
        "NetIncomeLoss",
        "ProfitLoss",
        "ProfitLossAttributableToOwnersOfParent",
    ], ["USD"])
    eps = annual_series(facts, [
        "EarningsPerShareDiluted",
        "DilutedEarningsLossPerShare",
    ], ["USD/shares"])
    cfo = annual_series(facts, [
        "NetCashProvidedByUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
    ], ["USD"])
    capex = annual_series(facts, [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    ], ["USD"])
    equity = annual_series(facts, [
        "StockholdersEquity",
        "EquityAttributableToOwnersOfParent",
        "Equity",
    ], ["USD"])
    assets = annual_series(facts, ["Assets"], ["USD"])
    assets_current = annual_series(facts, ["AssetsCurrent", "CurrentAssets"], ["USD"])
    liabilities_current = annual_series(facts, ["LiabilitiesCurrent", "CurrentLiabilities"], ["USD"])
    cash = annual_series(facts, [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashAndCashEquivalents",
        "Cash",
    ], ["USD"])
    debt = build_debt_series(facts)
    depreciation = annual_series(facts, [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "DepreciationAndAmortisationExpense",
    ], ["USD"])
    sbc = annual_series(facts, [
        "ShareBasedCompensation",
        "AllocatedShareBasedCompensationExpense",
        "AdjustmentsForSharebasedPayments",
    ], ["USD"])
    rd = annual_series(facts, [
        "ResearchAndDevelopmentExpense",
    ], ["USD"])
    tax = annual_series(facts, [
        "IncomeTaxExpenseBenefit",
        "IncomeTaxExpenseContinuingOperations",
    ], ["USD"])
    pretax = build_pretax_series(facts)
    shares_out = latest_position(facts, [
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
    ], ["shares"])
    shares_diluted = annual_series(facts, [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "AdjustedWeightedAverageShares",
        "WeightedAverageShares",
    ], ["shares"])
    dividends = annual_series(facts, [
        "PaymentsOfDividends",
        "PaymentsOfDividendsCommonStock",
        "DividendsPaidClassifiedAsFinancingActivities",
    ], ["USD"])
    buybacks = annual_series(facts, [
        "PaymentsForRepurchaseOfCommonStock",
        "PaymentsForRepurchaseOfEquity",
    ], ["USD"])
    retained = annual_series(facts, [
        "RetainedEarningsAccumulatedDeficit",
        "RetainedEarnings",
    ], ["USD"])
    liabilities = build_liabilities_series(facts, assets, equity)
    return {
        "revenue": revenue,
        "gross_profit": gross,
        "operating_income": operating,
        "net_income": net_income,
        "eps": eps,
        "cfo": cfo,
        "capex": capex,
        "fcf": fcf_series(cfo, capex),
        "equity": equity,
        "assets": assets,
        "assets_current": assets_current,
        "liabilities": liabilities,
        "liabilities_current": liabilities_current,
        "cash": cash,
        "debt": debt,
        "da": depreciation,
        "sbc": sbc,
        "rd": rd,
        "tax": tax,
        "pretax": pretax,
        "shares_out": shares_out,
        "shares_diluted": shares_diluted,
        "dividends": dividends,
        "buybacks": buybacks,
        "retained_earnings": retained,
        "_history_start": history_start,
        "_history_end": history_end,
    }


def _margin(numerator_series, revenue, end):
    sales = value_at(revenue, end)
    if sales is None or sales <= 0:
        return None
    numerator = value_at(numerator_series, end)
    if numerator is None:
        return None
    return numerator / sales


def _stmt_end(bundle):
    return _latest_end(bundle["revenue"], bundle["net_income"], bundle["operating_income"])


def price_return(bars, newer_index, older_index):
    if older_index < 0 or newer_index >= len(bars):
        return None
    older = bars[older_index]["close"]
    newer = bars[newer_index]["close"]
    if older is None or newer is None or older <= 0:
        return None
    return newer / older - 1.0


def close_on_or_before(bars, day):
    found = None
    for bar in bars:
        bar_day = parse_date(bar["date"])
        if bar_day <= day:
            found = bar
        else:
            break
    if found is None:
        return None
    if (day - parse_date(found["date"])).days > 7:
        return None
    return found["close"]


def momentum_inputs(bars, benchmark):
    """12-1 excess return in percentage points, SMA flag, distance to 52-week high."""
    result = {"excess_return_12_1": None, "above_sma_200": None, "distance_52w_high": None,
              "observed_price": None, "benchmark_label": None, "barCount": len(bars or [])}
    if not bars:
        return result
    result["observed_price"] = bars[-1]["date"]
    last = len(bars) - 1
    if last >= 252 and benchmark:
        stock = price_return(bars, last - 21, last - 252)
        start_day = parse_date(bars[last - 252]["date"])
        end_day = parse_date(bars[last - 21]["date"])
        bench_start = close_on_or_before(benchmark["bars"], start_day)
        bench_end = close_on_or_before(benchmark["bars"], end_day)
        if stock is not None and bench_start and bench_end and bench_start > 0:
            bench_ret = bench_end / bench_start - 1.0
            result["excess_return_12_1"] = (stock - bench_ret) * 100.0
            result["benchmark_label"] = benchmark.get("label")
    if len(bars) >= 200:
        window = [bar["close"] for bar in bars[-200:]]
        average = sum(window) / 200.0
        result["above_sma_200"] = 1.0 if bars[-1]["close"] > average else 0.0
    if len(bars) >= 252:
        highs = []
        for bar in bars[-252:]:
            high = bar.get("high")
            highs.append(bar["close"] if high is None else high)
        peak = max(highs)
        if peak > 0:
            result["distance_52w_high"] = (peak - bars[-1]["close"]) / peak
    return result


def altman_z(bundle, market_cap, end):
    assets = value_at(bundle["assets"], end)
    current_assets = value_at(bundle["assets_current"], end)
    current_liabilities = value_at(bundle["liabilities_current"], end)
    retained = value_at(bundle["retained_earnings"], end)
    operating = value_at(bundle["operating_income"], end)
    liabilities = value_at(bundle["liabilities"], end)
    revenue = value_at(bundle["revenue"], end)
    if None in (assets, current_assets, current_liabilities, retained, operating, liabilities, revenue, market_cap):
        return None
    if assets <= 0 or liabilities <= 0:
        return None
    working_capital = current_assets - current_liabilities
    return (
        1.2 * (working_capital / assets)
        + 1.4 * (retained / assets)
        + 3.3 * (operating / assets)
        + 0.6 * (market_cap / liabilities)
        + 1.0 * (revenue / assets)
    )


def market_cap_of(price, shares):
    if price is None or shares is None or price <= 0 or shares <= 0:
        return None
    return price * shares


def _status_text(config, key, default):
    return ((config.get("statuses") or {}).get(key) or default)


def _not_applicable(reasons, values, observed, metric_id, status):
    values.pop(metric_id, None)
    observed.pop(metric_id, None)
    reasons[metric_id] = status


def _history_short(series, end, years, tolerance):
    if not end or not series:
        return False
    return year_gap(end, series[0]["end"]) < (float(years) - float(tolerance))


EDGAR_METRIC_IDS = {
    "roic_2y", "gross_margin", "gross_margin_stability", "operating_margin", "fcf_margin",
    "revenue_cagr_3y", "revenue_cagr_5y", "eps_cagr_3y", "fcf_cagr_3y", "revenue_acceleration",
    "operating_margin_change_yoy",
    "revenue_yoy", "rd_intensity", "gross_margin_change_yoy",
    "operating_margin_fin", "fcf_margin_fin", "roe", "cash_runway_months",
    "net_debt_to_ebitda", "current_ratio", "dilution_3y",
    "insider_market_balance",
    "valuation_primary", "fcf_yield", "ev_to_ebitda", "shareholder_yield",
}
ANALYST_METRIC_IDS = {"buy_share", "target_upside", "eps_revision_90d"}


def _mark_structural(reasons, values, observed, config, bundle, end, bar_count, profile):
    """Labels for gaps that are not a missing fetch: sector, filing duty, short history."""
    na = _status_text(config, "notApplicable", "nicht anwendbar")
    sector = (profile or {}).get("sector")
    for metric_id in ((config.get("notApplicableBySector") or {}).get(sector) or []):
        _not_applicable(reasons, values, observed, metric_id, na)
    if not (profile or {}).get("sec", True):
        filing = _status_text(config, "noUsFiling", "keine US-Meldepflicht")
        for metric_id in EDGAR_METRIC_IDS:
            if values.get(metric_id) is None and metric_id not in reasons:
                reasons[metric_id] = filing
    short = _status_text(config, "shortHistory", "zu kurze Historie")
    history = config.get("shortHistory") or {}
    for metric_id, need in (history.get("priceBars") or {}).items():
        if values.get(metric_id) is not None or metric_id in reasons:
            continue
        if bar_count and bar_count < int(need):
            reasons[metric_id] = short
    for metric_id, rule in (history.get("seriesYears") or {}).items():
        if values.get(metric_id) is not None or metric_id in reasons:
            continue
        series = bundle.get(rule.get("series")) or []
        years = rule.get("years") or 0
        if _history_short(series, end, years, rule.get("tolerance", 0.6)):
            reasons[metric_id] = short
            continue
        # No annual points, but the issuer has only a short reporting window (IPO).
        if series:
            continue
        window_start = bundle.get("_history_start")
        window_end = bundle.get("_history_end")
        limit = rule.get("emptyBelow", years)
        if window_start and window_end and year_gap(window_end, window_start) < float(limit):
            reasons[metric_id] = short
    if not any(values.get(metric_id) is not None for metric_id in ANALYST_METRIC_IDS):
        excluded = _status_text(config, "excluded", "ausgeblendet, zählt nicht")
        for metric_id in ANALYST_METRIC_IDS:
            if metric_id not in reasons:
                reasons[metric_id] = excluded


def _positive_growth(values):
    """YoY revenue growth, or the 3-year CAGR when the latest year is not growing."""
    yoy_growth = values.get("revenue_yoy")
    if yoy_growth and yoy_growth > 0:
        return yoy_growth, False
    cagr_growth = values.get("revenue_cagr_3y")
    if cagr_growth and cagr_growth > 0:
        return cagr_growth, True
    return None, False


def _ev_sales_growth(values, labels, points, market_cap, debt, cash, sales, observed, valuation, growth, note=None):
    if not (market_cap and sales and growth and growth > 0 and debt is not None and cash is not None):
        return False
    enterprise = market_cap + debt - cash
    growth_percent = growth * 100.0
    if valuation.get("growthUnit") != "percent" or growth_percent <= 0:
        return False
    multiple = (enterprise / sales) / growth_percent
    values["valuation_primary"] = multiple
    labels["valuation_primary"] = valuation["label"]
    points["valuation_primary"] = linear_points(multiple, valuation["zeroAt"], valuation["tenAt"])
    if note:
        labels["valuation_primary_note"] = note
    return observed


def _insider_balance(insider, market_cap, config):
    rules = config.get("insider") or {}
    purchase_weight = float(rules.get("purchaseWeight", 2))
    plan_weight = float(rules.get("planSaleWeight", 0.25))
    neutral_ratio = float(rules.get("neutralNetSellToMarketCap", 0.001))
    bonus = rules.get("distinctBuyerBonus") or {}
    buy = float(insider.get("buy") or 0)
    sell = float(insider.get("sell") or 0)
    plan = float(insider.get("planSell") or 0)
    weighted_buy = buy * purchase_weight
    weighted_sell = sell + plan * plan_weight
    net_sell = weighted_sell - weighted_buy
    neutralized = False
    if net_sell < 0:
        outcome = "buy"
        label = "Netto-Käufe"
    elif net_sell == 0:
        outcome = "flat"
        label = "neutral"
    elif market_cap and market_cap > 0 and (net_sell / market_cap) < neutral_ratio:
        outcome = "flat"
        label = "neutral"
        neutralized = True
    else:
        outcome = "sell"
        label = "Netto-Verkäufe"
    buyers = []
    for owner in insider.get("buyers") or []:
        if owner not in buyers:
            buyers.append(owner)
    plan_sells = int(insider.get("planSells") or 0)
    open_sells = int(insider.get("sells") or 0)
    text = (
        label
        + " · "
        + str(int(insider.get("buys") or 0))
        + " Käufe / "
        + str(open_sells + plan_sells)
        + " Verkäufe"
    )
    if plan_sells:
        text += ", davon " + str(plan_sells) + " im 10b5-1-Plan"
    if neutralized:
        text += " · unter 0,1 % der Marktkapitalisierung"
    notes = []
    if plan > 0:
        notes.append("10b5-1-Planverkäufe zählen mit einem Viertel")
    if buy > 0:
        notes.append("offene Käufe zählen doppelt")
    if neutralized:
        notes.append("Netto-Verkauf unter 0,1 % der Marktkapitalisierung ist neutral")
    points_override = None
    if len(buyers) >= int(bonus.get("atLeast", 2)):
        notes.append("Bonus für mehrere verschiedene Käufer")
        base = {"buy": 3.0, "flat": 2.0, "sell": 0.0}[outcome]
        points_override = min(base + float(bonus.get("points", 1)), float(bonus.get("cap", 3)))
    return {
        "outcome": outcome,
        "text": text,
        "windowDays": insider.get("windowDays"),
        "filings": insider.get("filings"),
        "buys": int(insider.get("buys") or 0),
        "sells": open_sells + plan_sells,
        "planSells": plan_sells,
        "buyers": len(buyers),
        "buyDollars": buy,
        "sellDollars": sell + plan,
        "pointsOverride": points_override,
        "extraNote": " ".join(notes) or None,
    }


def score_company(config, bundle, prices, analysts, fetched_at, sources, profile=None):
    """Return criteria, score, flags and quality counts. bundle may be empty."""
    bundle = bundle or {}
    for key in (
        "revenue", "gross_profit", "operating_income", "net_income", "eps", "fcf",
        "equity", "assets", "assets_current", "liabilities", "liabilities_current",
        "cash", "debt", "da", "sbc", "rd", "tax", "pretax", "shares_out",
        "shares_diluted", "dividends", "buybacks", "retained_earnings",
    ):
        bundle.setdefault(key, [])
    analysts = analysts or {}
    prices = prices or {}
    bars = prices.get("bars") or []
    benchmark = prices.get("benchmark")
    last_close = bars[-1]["close"] if bars else None
    price_observed = bars[-1]["date"] if bars else None
    shares = bundle["shares_out"][-1]["val"] if bundle["shares_out"] else None
    shares_observed = bundle["shares_out"][-1]["end"] if bundle["shares_out"] else None
    cap = market_cap_of(last_close, shares)
    end = _stmt_end(bundle)
    momentum = momentum_inputs(bars, benchmark)
    if momentum.get("benchmark_label") and momentum["benchmark_label"] != config["benchmark"]["label"]:
        pass
    raw = _raw_metrics(bundle, end, cap, last_close, momentum, analysts, config, profile)
    missing_label = _status_text(config, "missing", config.get("missingDataStatus") or "wenig Daten")
    na_label = _status_text(config, "notApplicable", "nicht anwendbar")
    criteria = []
    metric_available = 0
    metric_total = 0
    for criterion in config["criteria"]:
        metrics = []
        points = []
        available = 0
        applicable = 0
        for spec in criterion["metrics"]:
            metric_id = spec["id"]
            observed = raw["observed"].get(metric_id)
            value = raw["values"].get(metric_id)
            reason = raw["reasons"].get(metric_id)
            if reason:
                value = None
            label = raw["labels"].get(metric_id) or spec["label"]
            kind = spec.get("kind")
            note = None
            detail = None
            display = None
            if isinstance(value, dict) and "outcome" in value:
                detail = value
                display = value.get("text")
                outcome = (spec.get("outcomes") or {}).get(value["outcome"]) or {}
                detail = value
                value = None if not outcome else value["outcome"]
                if detail.get("pointsOverride") is not None and outcome:
                    scored = float(detail["pointsOverride"])
                else:
                    scored = None if not outcome else float(outcome["points"])
                note = outcome.get("note")
                if detail.get("extraNote"):
                    note = ((note + " ") if note else "") + detail["extraNote"]
            else:
                if spec.get("id") == "valuation_primary":
                    kind = "multiple"
                if value is None:
                    scored = None
                elif spec.get("kind") == "binary" and spec["id"] == "above_sma_200":
                    scored = 10.0 if value == 1 else (0.0 if value == 0 else None)
                elif spec.get("bands"):
                    scored, note = band_points(value, spec["bands"])
                elif "zeroAt" in spec and "tenAt" in spec:
                    scored = linear_points(value, spec["zeroAt"], spec["tenAt"])
                else:
                    scored = raw["points"].get(metric_id)
            extra_note = raw["labels"].get(metric_id + "_note")
            if extra_note:
                note = ((note + " ") if note else "") + extra_note
            row_status = reason if reason else (None if value is not None else missing_label)
            counts = row_status != na_label
            if counts:
                applicable += 1
                metric_total += 1
                if value is not None:
                    available += 1
                    metric_available += 1
            source = _source_for(metric_id, sources, prices)
            if display is not None:
                shown = display
                raw_value = None
            elif kind == "binary" and value is not None:
                shown = format_value("binary", value)
                raw_value = round(float(value), 6)
            else:
                shown = format_value(kind, value)
                raw_value = None if value is None else round(float(value), 6)
            row = {
                "id": metric_id,
                "label": label,
                "value": shown,
                "raw": raw_value,
                "points": None if not counts else round1(scored),
                "status": row_status,
                "sourceLabel": source["label"],
                "sourceUrl": source["url"],
                "fetchedAt": fetched_at,
                "observedAt": observed if value is not None else None,
            }
            if note:
                row["note"] = note
            if detail and detail.get("outcome"):
                row["detail"] = {
                    "windowDays": detail.get("windowDays"),
                    "filings": detail.get("filings"),
                    "buys": detail.get("buys"),
                    "sells": detail.get("sells"),
                    "buyDollars": None if detail.get("buyDollars") is None else round(float(detail["buyDollars"]), 2),
                    "sellDollars": None if detail.get("sellDollars") is None else round(float(detail["sellDollars"]), 2),
                }
            metrics.append(row)
            if counts:
                points.append(round1(scored) if scored is not None else None)
        if criterion.get("aggregate") == "sum":
            present = [point for point in points if point is not None]
            criterion_score = sum(present) if present else None
        else:
            criterion_score = mean(points)
        capped = False
        if criterion.get("cap") == "analysts":
            criterion_score, capped = apply_analyst_cap(
                criterion_score,
                analysts.get("count"),
                config["analystCap"]["below"],
                config["analystCap"]["maxPoints"],
            )
        quality = f"{available} von {applicable} Metriken verfügbar"
        notes = [row["note"] for row in metrics if row.get("note")]
        if notes:
            quality = quality + ". " + " ".join(notes)
        if criterion_score is None:
            gap_labels = {row["status"] for row in metrics if row.get("status")}
            criterion_status = gap_labels.pop() if len(gap_labels) == 1 else missing_label
        else:
            criterion_status = None
        entry = {
            "id": criterion["id"],
            "label": criterion["label"],
            "score": round1(criterion_score),
            "status": criterion_status,
            "weight": criterion["weight"],
            "dataQuality": quality,
            "metrics": metrics,
        }
        if capped:
            entry["capApplied"] = True
        criteria.append(entry)
    flags = _ko_flags(config, bundle, end, cap, sources)
    return {
        "criteria": criteria,
        "available": metric_available,
        "total": metric_total,
        "koFlags": flags,
        "priceNote": prices.get("note"),
    }


def _source_for(metric_id, sources, prices):
    price_ids = {"excess_return_12_1", "above_sma_200", "distance_52w_high"}
    analyst_ids = {"buy_share", "target_upside", "eps_revision_90d"}
    mixed = {"valuation_primary", "fcf_yield", "ev_to_ebitda", "shareholder_yield"}
    if metric_id in price_ids:
        return {
            "label": prices.get("sourceLabel"),
            "url": prices.get("sourceUrl"),
        }
    if metric_id in analyst_ids:
        return {
            "label": sources.get("analystLabel"),
            "url": sources.get("analystUrl"),
        }
    if metric_id in mixed:
        label = sources.get("fundamentalLabel") or ""
        if prices.get("sourceLabel"):
            label = (label + "; " if label else "") + prices["sourceLabel"]
        return {"label": label or None, "url": sources.get("fundamentalUrl")}
    if metric_id in {"insider_ownership", "institutional_ownership"}:
        return {"label": sources.get("holderLabel"), "url": sources.get("holderUrl")}
    if metric_id == "insider_market_balance":
        return {"label": sources.get("insiderLabel"), "url": sources.get("insiderUrl")}
    return {"label": sources.get("fundamentalLabel"), "url": sources.get("fundamentalUrl")}


def _raw_metrics(bundle, end, market_cap, price, momentum, analysts, config, profile=None):
    values = {}
    observed = {}
    labels = {}
    points = {}
    reasons = {}
    na = _status_text(config, "notApplicable", "nicht anwendbar")
    revenue = bundle["revenue"]
    gross_margins = gross_margin_series(revenue, bundle["gross_profit"])

    def put(metric_id, value, when):
        values[metric_id] = value
        observed[metric_id] = when

    if end:
        roics = []
        for point in bundle["operating_income"]:
            if point["end"] > end:
                continue
            figure = roic_for_year(point["end"], bundle)
            if figure is not None:
                roics.append((point["end"], figure))
        if len(roics) >= 2:
            put("roic_2y", (roics[-1][1] + roics[-2][1]) / 2.0, roics[-1][0])
        put("gross_margin", value_at(gross_margins, end), end if value_at(gross_margins, end) is not None else None)
        if len(gross_margins) >= 3:
            window = [point["val"] for point in gross_margins[-5:]]
            put("gross_margin_stability", statistics.pstdev(window), gross_margins[-1]["end"])
        put("operating_margin", _margin(bundle["operating_income"], revenue, end), end if value_at(bundle["operating_income"], end) is not None else None)
        put("operating_margin_fin", values.get("operating_margin"), observed.get("operating_margin"))
        fcf_margin = _margin(bundle["fcf"], revenue, end)
        put("fcf_margin", fcf_margin, end if fcf_margin is not None else None)
        put("fcf_margin_fin", fcf_margin, observed.get("fcf_margin"))
        latest_revenue = at(revenue, end)
        three = point_years_before(revenue, end, 3)
        five = point_years_before(revenue, end, 5)
        put("revenue_cagr_3y", cagr(latest_revenue, three), end if three else None)
        put("revenue_cagr_5y", cagr(latest_revenue, five), end if five else None)
        eps_now = at(bundle["eps"], end)
        eps_then = point_years_before(bundle["eps"], end, 3)
        put("eps_cagr_3y", cagr(eps_now, eps_then), end if eps_then and eps_now else None)
        fcf_now = at(bundle["fcf"], end)
        fcf_then = point_years_before(bundle["fcf"], end, 3)
        put("fcf_cagr_3y", cagr(fcf_now, fcf_then), end if fcf_then and fcf_now else None)
        growth = yoy(revenue, end)
        put("revenue_yoy", growth, end if growth is not None else None)
        prior_end = point_years_before(revenue, end, 1, 0.4)
        prior_growth = yoy(revenue, prior_end["end"]) if prior_end else None
        if growth is not None and prior_growth is not None:
            put("revenue_acceleration", growth - prior_growth, end)
        rd = value_at(bundle["rd"], end)
        sales = value_at(revenue, end)
        if rd is not None and sales:
            put("rd_intensity", rd / sales, end)
        gm_now = value_at(gross_margins, end)
        gm_then = value_at(gross_margins, prior_end["end"]) if prior_end else None
        if gm_now is not None and gm_then is not None:
            put("gross_margin_change_yoy", gm_now - gm_then, end)
        equity = value_at(bundle["equity"], end)
        net_income = value_at(bundle["net_income"], end)
        loss = net_income is not None and net_income <= 0
        profit = net_income is not None and net_income > 0
        op_now = values.get("operating_margin")
        op_then = _margin(bundle["operating_income"], revenue, prior_end["end"]) if prior_end else None
        if profit:
            _not_applicable(reasons, values, observed, "operating_margin_change_yoy", na)
        elif loss and op_now is not None and op_then is not None:
            put("operating_margin_change_yoy", op_now - op_then, end)
        prior_equity_point = point_years_before(bundle["equity"], end, 1, 0.4)
        if net_income is not None and equity and prior_equity_point and prior_equity_point["val"]:
            average_equity = (equity + prior_equity_point["val"]) / 2.0
            if average_equity > 0:
                put("roe", net_income / average_equity, end)
        ebitda = ebitda_at(end, bundle)
        debt = value_at(bundle["debt"], end)
        cash = value_at(bundle["cash"], end)
        fcf_value = value_at(bundle["fcf"], end)
        if fcf_value is not None and fcf_value < 0:
            _not_applicable(reasons, values, observed, "roe", na)
            if cash is not None and cash >= 0 and fcf_value != 0:
                put("cash_runway_months", (cash / abs(fcf_value)) * 12.0, end)
        elif fcf_value is not None:
            _not_applicable(reasons, values, observed, "cash_runway_months", na)
        if ebitda is not None and ebitda > 0 and debt is not None and cash is not None:
            put("net_debt_to_ebitda", (debt - cash) / ebitda, end)
        current_assets = value_at(bundle["assets_current"], end)
        current_liabilities = value_at(bundle["liabilities_current"], end)
        if current_assets is not None and current_liabilities and current_liabilities > 0:
            put("current_ratio", current_assets / current_liabilities, end)
        diluted_now = at(bundle["shares_diluted"], end)
        diluted_3 = point_years_before(bundle["shares_diluted"], end, 3)
        diluted_1 = point_years_before(bundle["shares_diluted"], end, 1, 0.4)
        if diluted_now and diluted_3 and diluted_3["val"] > 0:
            put("dilution_3y", diluted_now["val"] / diluted_3["val"] - 1.0, end)
        if diluted_now and diluted_1 and diluted_1["val"] > 0:
            put("share_change_1y", diluted_now["val"] / diluted_1["val"] - 1.0, end)
        sbc = value_at(bundle["sbc"], end)
        if sbc is not None and sales:
            put("sbc_to_revenue", sbc / sales, end)
        dividends = value_at(bundle["dividends"], end)
        buybacks = value_at(bundle["buybacks"], end)
        if market_cap and (dividends is not None or buybacks is not None):
            payout = (abs(dividends) if dividends is not None else 0.0) + (abs(buybacks) if buybacks is not None else 0.0)
            put("shareholder_yield", payout / market_cap, momentum.get("observed_price") or end)
        if loss:
            _not_applicable(reasons, values, observed, "fcf_yield", na)
        elif market_cap and fcf_value is not None:
            put("fcf_yield", fcf_value / market_cap, momentum.get("observed_price") or end)
        if market_cap and debt is not None and cash is not None and sales:
            if ebitda is not None and ebitda > 0:
                enterprise = market_cap + debt - cash
                put("ev_to_ebitda", enterprise / ebitda, momentum.get("observed_price") or end)
            valuation = config["valuation"]
            observed_value = momentum.get("observed_price") or end
            if profit:
                growth_eps = values.get("eps_cagr_3y")
                if price and eps_now is not None and eps_now["val"] > 0 and growth_eps and growth_eps > 0:
                    peg = (price / eps_now["val"]) / (growth_eps * 100.0)
                    put("valuation_primary", peg, observed_value)
                    labels["valuation_primary"] = valuation["profitable"]["label"]
                    points["valuation_primary"] = linear_points(
                        peg, valuation["profitable"]["zeroAt"], valuation["profitable"]["tenAt"]
                    )
                else:
                    growth, used_cagr = _positive_growth(values)
                    note = "Ersatz für PEG, weil das EPS-Wachstum nicht positiv ist"
                    if used_cagr:
                        note += ". Umsatzwachstum ist der 3-Jahres-CAGR"
                    _ev_sales_growth(
                        values, labels, points, market_cap, debt, cash, sales, observed_value,
                        valuation["unprofitable"], growth, note,
                    )
                    if values.get("valuation_primary") is not None:
                        observed["valuation_primary"] = observed_value
            elif loss:
                _ev_sales_growth(
                    values, labels, points, market_cap, debt, cash, sales, observed_value,
                    valuation["unprofitable"], values.get("revenue_yoy"),
                )
                if values.get("valuation_primary") is not None:
                    observed["valuation_primary"] = observed_value
    insider = bundle.get("insider")
    if insider and insider.get("measured"):
        put("insider_market_balance", _insider_balance(insider, market_cap, config), insider.get("observed"))
        labels["insider_market_balance"] = "Insider-Saldo, 6 Monate"
    holders = bundle.get("holders") or {}
    if holders.get("insider") is not None:
        put("insider_ownership", holders["insider"], holders.get("observed"))
    if holders.get("institutional") is not None:
        put("institutional_ownership", holders["institutional"], holders.get("observed"))
    if analysts.get("buyShare") is not None:
        put("buy_share", analysts["buyShare"], analysts.get("observed"))
    if analysts.get("targetUpside") is not None:
        put("target_upside", analysts["targetUpside"], analysts.get("observed"))
    if analysts.get("epsRevision") is not None:
        put("eps_revision_90d", analysts["epsRevision"], analysts.get("observed"))
    if momentum.get("excess_return_12_1") is not None:
        put("excess_return_12_1", momentum["excess_return_12_1"], momentum.get("observed_price"))
        if momentum.get("benchmark_label"):
            labels["excess_return_12_1"] = "12-1-Monats-Rendite gegen " + momentum["benchmark_label"]
    if momentum.get("above_sma_200") is not None:
        put("above_sma_200", momentum["above_sma_200"], momentum.get("observed_price"))
    if momentum.get("distance_52w_high") is not None:
        put("distance_52w_high", momentum["distance_52w_high"], momentum.get("observed_price"))
    _mark_structural(
        reasons, values, observed, config, bundle, end,
        momentum.get("barCount") or 0, profile,
    )
    return {"values": values, "observed": observed, "labels": labels, "points": points, "reasons": reasons}


def _ko_flags(config, bundle, end, market_cap, sources):
    flags = []
    ko = config["ko"]
    if end:
        z = altman_z(bundle, market_cap, end)
        if z is not None and z < ko["altmanZBelow"]:
            flags.append(f"Altman Z {str(round(z, 2)).replace('.', ',')} unter {str(ko['altmanZBelow']).replace('.', ',')}")
        fcf_value = value_at(bundle["fcf"], end)
        leverage = None
        ebitda = ebitda_at(end, bundle)
        debt = value_at(bundle["debt"], end)
        cash = value_at(bundle["cash"], end)
        if ebitda is not None and ebitda > 0 and debt is not None and cash is not None:
            leverage = (debt - cash) / ebitda
        if fcf_value is not None and fcf_value < 0 and leverage is not None and leverage > ko["netDebtToEbitdaAbove"]:
            flags.append("Negativer Free Cashflow bei hoher Nettoverschuldung")
    if sources.get("restatement"):
        flags.append("8-K Item 4.02 (Restatement) in den letzten 24 Monaten")
    return flags


def latest_observed(criteria):
    """Latest reporting-period or price-linked metric date. Fetch days do not count."""
    found = []
    for criterion in criteria:
        for metric in criterion["metrics"]:
            if metric.get("observedAt") and metric.get("raw") is not None:
                found.append(metric["observedAt"])
    if not found:
        return None
    return max(found)


def data_stand(criteria, price):
    """Stand der Daten: newest close or reporting date. A trade date alone is not one."""
    found = []
    metric = latest_observed(criteria)
    if metric:
        found.append(metric)
    if price and price.get("date"):
        found.append(price["date"])
    if not found:
        return None
    return max(found)
