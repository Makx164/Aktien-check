"""Turn annual series and daily prices into criterion metrics.

Formulas live here. The 0–10 ramps are read from config/scoring.json.
Missing inputs stay None and are marked „wenig Daten“. Nothing is filled
in with zero or a guess, and a missing metric never scores 0.
"""

from __future__ import annotations

import math
import re
import statistics
from datetime import date, datetime, timedelta

from scoring.model import apply_analyst_cap, band_points, format_value, linear_points, round1, weighted_mean

ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
POSITION_FORMS = ANNUAL_FORMS | {"10-Q", "10-Q/A", "6-K"}


def scale_text(spec):
    """Threshold sentence shown next to a metric. Config can override it."""
    if not spec:
        return None
    if spec.get("scale"):
        return spec["scale"]
    if spec.get("bands"):
        notes = [_compact_scale_note(band.get("note")) for band in spec["bands"] if band.get("note")]
        return ", ".join(notes) or None
    if spec.get("outcomes"):
        notes = []
        for item in spec["outcomes"].values():
            if isinstance(item, dict) and item.get("note"):
                notes.append(_compact_scale_note(item["note"]))
        return ", ".join(notes) or None
    if spec.get("id") == "above_sma_200":
        return "über dem 200-Tage-Schnitt = 10, darunter = 5"
    if spec.get("id") == "distance_52w_high":
        return "0–20 % unter dem Hoch = 10; darüber nach der Fondsmanager-Regel"
    if spec.get("id") == "excess_return_12_1":
        return _excess_scale(spec.get("momentum") or {})
    if "zeroAt" not in spec or "tenAt" not in spec:
        return None
    kind = spec.get("kind")
    higher = float(spec["tenAt"]) >= float(spec["zeroAt"])
    ten = _scale_amount(kind, spec["tenAt"])
    zero = _scale_amount(kind, spec["zeroAt"])
    if higher:
        return f"≥ {ten} = 10, {zero} = 0"
    return f"≤ {ten} = 10, {zero} = 0"


def _compact_scale_note(note):
    """Band notes use arrows. The table shows the same thresholds with '='."""
    return re.sub(r"\s*→\s*(\d+(?:[.,]\d+)?)\s*von\s*\d+", r" = \1", str(note))


def _note_parts(text):
    parts = []
    for chunk in re.split(r"\s*·\s*", str(text or "")):
        piece = " ".join(chunk.split())
        if piece and piece not in parts:
            parts.append(piece)
    return parts


def _join_notes(*texts):
    parts = []
    for text in texts:
        for piece in _note_parts(text):
            if piece not in parts:
                parts.append(piece)
    if not parts:
        return None
    return " · ".join(parts)


def _excess_scale(rules):
    """Piecewise excess-return thresholds: 10 at the top, 5 at zero, 0 at the floor."""
    top = _scale_amount("pp", rules.get("excessTenAt", 30))
    mid = _scale_amount("pp", rules.get("excessMidAt", 0))
    floor = _scale_amount("pp", rules.get("excessZeroAt", -50))
    mid_points = int(rules.get("excessMidPoints", 5))
    return f"≥ {top} = 10, {mid} = {mid_points}, ≤ {floor} = 0"


def _band_scale(band):
    return scale_text({"kind": "multiple", "zeroAt": band["zeroAt"], "tenAt": band["tenAt"]})


def _scale_amount(kind, raw):
    text = format_value(kind, raw) or ""
    for suffix in (" %", " Pp", " Monate"):
        text = text.replace(",0" + suffix, suffix)
    return text


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


def _concept_has_usd(facts, name):
    concept = _find_concept(facts, name)
    if not concept:
        return False
    return bool(_rows(concept, ["USD"]))


GAAP_RD_NAMES = (
    "ResearchAndDevelopmentExpense",
    "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
)
RD_EXTENSION_CONCEPTS = (
    "TechnologyAndInfrastructureExpense",
    "TechnologyAndDevelopmentExpense",
)
LOSS_BASE_REASON = "Verlust bzw. negativer Wert in der Basisperiode"
LOSS_CAPITAL_REASON = "Verlust bzw. negatives Kapital"
EBITDA_NEGATIVE_REASON = "EBITDA negativ"
NET_DEBT_FCF_REASON = "Nettoverschuldung bei positivem Free Cashflow"
STATUTORY_TAX_NOTE = "Steuersatz außerhalb −0,5 bis 1, daher 21 %"


def has_recent_annual(facts, names, months=18, today=None):
    """True when a GAAP annual fact ends within the last `months` months."""
    today = today or date.today()
    cutoff = (today - timedelta(days=int(round(float(months) * 30.4375)))).isoformat()
    for name in names:
        concept = _find_concept(facts, name)
        if not concept:
            continue
        for row in _rows(concept, ["USD"]):
            if row.get("form") not in ANNUAL_FORMS or row.get("fp") != "FY":
                continue
            if (row.get("end") or "") >= cutoff:
                return True
    return False


def chosen_concept_name(facts, concepts, units):
    """Concept with the newest fact. An earlier name wins a tie, matching the flow picker."""
    chosen = None
    chosen_key = None
    for rank, name in enumerate(concepts):
        concept = _find_concept(facts, name)
        if not concept:
            continue
        ends = []
        for row in _rows(concept, units):
            if row.get("form") not in POSITION_FORMS or not row.get("end"):
                continue
            if not isinstance(row.get("val"), (int, float)) or isinstance(row.get("val"), bool):
                continue
            ends.append(row["end"])
        if not ends:
            continue
        key = (max(ends), -rank)
        if chosen_key is None or key > chosen_key:
            chosen = name
            chosen_key = key
    return chosen


def research_concepts(facts):
    """GAAP research expense, plus extension lines such as technology-and-development.

    A fresh annual GAAP tag stays first. When that annual figure is older than
    18 months, the extension concepts are preferred on a date tie.
    """
    standard = list(GAAP_RD_NAMES)
    extensions = list(RD_EXTENSION_CONCEPTS)
    if has_recent_annual(facts, standard, months=18):
        return standard + extensions
    return extensions + standard


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


def cagr_result(latest, earlier):
    """Return (value, loss). Loss means both points exist and a value is not positive."""
    if latest is None or earlier is None:
        return None, False
    if latest["val"] <= 0 or earlier["val"] <= 0:
        return None, True
    return cagr(latest, earlier), False


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
    """Return value, loss flag and an optional statutory-tax note.

    A tax rate outside −0.5…1 is replaced by 21 %. Pretax or invested capital
    at or below zero is a loss, not a missing fetch.
    """
    operating = value_at(bundle["operating_income"], end)
    tax = value_at(bundle["tax"], end)
    pretax = value_at(bundle["pretax"], end)
    equity = value_at(bundle["equity"], end)
    debt = value_at(bundle["debt"], end)
    cash = value_at(bundle["cash"], end)
    if debt is None:
        series = bundle.get("debt") or []
        snap = bundle.get("snapshot") or {}
        last = series[-1]["end"] if series else None
        if snap.get("debt_explicit_zero") and last and last < end:
            debt = 0.0
    if None in (operating, tax, pretax, equity, debt, cash):
        return None, False, None
    if pretax <= 0:
        return None, True, None
    tax_rate = tax / pretax
    note = None
    if tax_rate < -0.5 or tax_rate > 1:
        tax_rate = 0.21
        note = STATUTORY_TAX_NOTE
    invested = equity + debt - cash
    if invested <= 0:
        return None, True, None
    return operating * (1.0 - tax_rate) / invested, False, note


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
        annual_series(facts, [
            "LongTermDebtNoncurrent",
            "LongtermBorrowings",
            "ConvertibleDebtNoncurrent",
        ], ["USD"]),
        annual_series(facts, [
            "LongTermDebtCurrent",
            "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
            "ConvertibleDebtCurrent",
            "ConvertibleNotesPayable",
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


def merged_annual(facts, concepts, units):
    """One point per year. Earlier concepts win; later concepts fill gaps."""
    by_end = {}
    for name in concepts:
        for point in annual_series(facts, [name], units):
            by_end.setdefault(point["end"], point)
    return [by_end[end] for end in sorted(by_end)]


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
    revenue = merged_annual(facts, revenue_concepts, ["USD"])
    history_start, history_end = reported_window(facts, revenue_concepts, ["USD"])
    cogs = annual_series(facts, [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        "CostOfSales",
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
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
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
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
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
        "Depreciation",
        "DepreciationAmortizationAndAccretionNet",
    ], ["USD"])
    sbc = annual_series(facts, [
        "ShareBasedCompensation",
        "AllocatedShareBasedCompensationExpense",
        "AdjustmentsForSharebasedPayments",
    ], ["USD"])
    rd = annual_series(facts, research_concepts(facts), ["USD"])
    tax = annual_series(facts, [
        "IncomeTaxExpenseBenefit",
        "IncomeTaxExpenseContinuingOperations",
    ], ["USD"])
    pretax = build_pretax_series(facts)
    shares_out = latest_position(facts, [
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
    ], ["shares"])
    shares_diluted = merged_annual(facts, [
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
    built = {
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
    from scoring.flows import build_snapshot
    built["snapshot"] = build_snapshot(facts, built)
    return built


def _margin(numerator_series, revenue, end):
    sales = value_at(revenue, end)
    if sales is None or sales <= 0:
        return None
    numerator = value_at(numerator_series, end)
    if numerator is None:
        return None
    return numerator / sales


def _stmt_end(bundle):
    """Annual period when filings have one, otherwise the snapshot period."""
    annual = _latest_end(bundle["revenue"], bundle["net_income"], bundle["operating_income"])
    if annual:
        return annual
    return (bundle.get("snapshot") or {}).get("end")


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


def _usable_shares(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def market_cap_of(price, shares):
    if price is None or not _usable_shares(shares) or price <= 0:
        return None
    return price * shares


def _status_text(config, key, default):
    return ((config.get("statuses") or {}).get(key) or default)


def _not_applicable(reasons, values, observed, metric_id, status, status_reasons=None, reason=None):
    values.pop(metric_id, None)
    observed.pop(metric_id, None)
    reasons[metric_id] = status
    if status_reasons is not None and reason:
        status_reasons[metric_id] = reason


def _history_short(series, end, years, tolerance):
    if not end or not series:
        return False
    return year_gap(end, series[0]["end"]) < (float(years) - float(tolerance))


EDGAR_METRIC_IDS = {
    "operating_margin", "fcf_margin",
    "ocf_margin", "fcf_margin_3y",
    "dividend_yield", "payout_to_fcf", "dividend_no_cut_5y",
    "revenue_cagr_3y", "revenue_cagr_5y", "eps_cagr_3y", "fcf_cagr_3y", "revenue_acceleration",
    "operating_margin_change_yoy",
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
    minimum = int(config.get("analystMinCount", 3))
    # Analyst coverage below the minimum is not scored. A missing fetch stays
    # "wenig Daten" and is applied by the caller when no count came back.


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
    labels["valuation_primary_scale"] = _band_scale(valuation)
    if note:
        labels["valuation_primary_note"] = note
    return observed


def _dividend_facts(bundle, market_cap, end):
    """Yield, payout and a five-year no-cut flag. Missing history stays absent."""
    result = {}
    dividends = value_at(bundle.get("dividends") or [], end)
    fcf_value = value_at(bundle.get("fcf") or [], end)
    if market_cap and dividends is not None and market_cap > 0:
        result["yield"] = abs(dividends) / market_cap
    if fcf_value is not None and fcf_value > 0 and dividends is not None:
        result["payout"] = abs(dividends) / fcf_value
    series = sorted(bundle.get("dividends") or [], key=lambda point: point["end"])
    comparisons = []
    if end:
        latest = at(series, end)
        cursor = end
        for _ in range(5):
            previous = point_years_before(series, cursor, 1, 0.4)
            current = at(series, cursor)
            if current is None or previous is None:
                comparisons = None
                break
            comparisons.append(abs(current["val"]) + 1e-9 >= abs(previous["val"]))
            cursor = previous["end"]
        if comparisons is not None and len(comparisons) == 5:
            result["no_cut"] = all(comparisons)
    result["qualifies_inputs"] = True
    return result


def _piecewise_excess(value, rules):
    zero_at = float(rules.get("excessZeroAt", -50))
    mid_at = float(rules.get("excessMidAt", 0))
    mid_points = float(rules.get("excessMidPoints", 5))
    ten_at = float(rules.get("excessTenAt", 30))
    if value <= zero_at:
        return 0.0
    if value <= mid_at:
        return mid_points * (value - zero_at) / (mid_at - zero_at)
    if value >= ten_at:
        return 10.0
    return mid_points + (10.0 - mid_points) * (value - mid_at) / (ten_at - mid_at)


def _distance_points(drawdown, growth_score, rules):
    """Return (points or None, warning status or None)."""
    near = float(rules.get("distanceNear", 0.2))
    mid = float(rules.get("distanceMid", 0.5))
    far = float(rules.get("distanceFar", 0.6))
    if drawdown <= near:
        return float(rules.get("nearPoints", 10)), None
    if drawdown <= mid:
        if growth_score is None or growth_score < float(rules.get("neutralBelow", 4)):
            return float(rules.get("neutralPoints", 5)), None
        if growth_score >= float(rules.get("opportunityAt", 6)):
            span = (drawdown - near) / (mid - near)
            low = float(rules.get("opportunityLow", 7))
            high = float(rules.get("opportunityHigh", 10))
            return low + span * (high - low), None
        span = (growth_score - float(rules.get("neutralBelow", 4))) / (
            float(rules.get("opportunityAt", 6)) - float(rules.get("neutralBelow", 4))
        )
        low = float(rules.get("betweenLow", 5))
        high = float(rules.get("betweenHigh", 7))
        return low + span * (high - low), None
    if drawdown <= far:
        if growth_score is not None and growth_score >= float(rules.get("intactAt", 6)):
            return float(rules.get("deepIntactPoints", 10)), None
        return float(rules.get("deepWeakPoints", 5)), None
    return None, rules.get("deepWarning")


def _momentum_points(values, points, reasons, labels, config):
    rules = config.get("momentum") or {}
    excess = values.get("excess_return_12_1")
    if excess is not None:
        points["excess_return_12_1"] = _piecewise_excess(excess, rules)
    flag = values.get("above_sma_200")
    if flag is not None:
        points["above_sma_200"] = float(rules.get("aboveSmaPoints", 10)) if flag == 1 else float(rules.get("belowSmaPoints", 5))
    # Distance depends on C_02, which is not known yet. A placeholder is replaced
    # once the growth criterion has a score.
    if values.get("distance_52w_high") is not None:
        labels["distance_52w_pending"] = True


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
    caption = (
        str(int(insider.get("buys") or 0))
        + " Käufe / "
        + str(open_sells + plan_sells)
        + " Verkäufe"
    )
    if plan_sells:
        caption += ", davon " + str(plan_sells) + " im 10b5-1-Plan"
    if neutralized:
        caption += " · unter 0,1 % der Marktkapitalisierung"
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
        "text": label,
        "caption": caption,
        "windowDays": insider.get("windowDays"),
        "filings": insider.get("filings"),
        "buys": int(insider.get("buys") or 0),
        "sells": open_sells + plan_sells,
        "planSells": plan_sells,
        "buyers": len(buyers),
        "buyDollars": buy,
        "sellDollars": sell + plan,
        "pointsOverride": points_override,
        "extraNote": _join_notes(*notes),
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
    snap = bundle.get("snapshot") or {}
    shares = snap.get("shares_out")
    shares_observed = snap.get("balance_end") or snap.get("end")
    if not _usable_shares(shares):
        shares = None
        for item in reversed(bundle["shares_out"]):
            if _usable_shares(item.get("val")):
                shares = item["val"]
                shares_observed = item["end"]
                break
    cap = market_cap_of(last_close, shares)
    end = _stmt_end(bundle)
    momentum = momentum_inputs(bars, benchmark)
    if momentum.get("benchmark_label") and momentum["benchmark_label"] != config["benchmark"]["label"]:
        pass
    raw = _raw_metrics(bundle, end, cap, last_close, momentum, analysts, config, profile)
    analyst_count = analysts.get("count") if analysts else None
    if analyst_count is not None and analyst_count < int(config.get("analystMinCount", 3)):
        few = _status_text(config, "tooFewAnalysts", "zu wenige Analysten")
        for metric_id in ANALYST_METRIC_IDS:
            raw["reasons"][metric_id] = few
            raw["values"].pop(metric_id, None)
            raw["points"].pop(metric_id, None)
    missing_label = _status_text(config, "missing", config.get("missingDataStatus") or "wenig Daten")
    na_label = _status_text(config, "notApplicable", "nicht anwendbar")
    criteria = []
    metric_available = 0
    metric_total = 0
    c02_score = None
    for criterion in config["criteria"]:
        if criterion["id"] == "C_07" and raw["labels"].get("distance_52w_pending"):
            rules = config.get("momentum") or {}
            drawdown = raw["values"].get("distance_52w_high")
            scored_distance, warning = _distance_points(drawdown, c02_score, rules)
            if warning:
                raw["reasons"]["distance_52w_high"] = warning
                raw.setdefault("withheld", set()).add("distance_52w_high")
            elif scored_distance is not None:
                raw["points"]["distance_52w_high"] = scored_distance
        metrics = []
        points = []
        available = 0
        applicable = 0
        for spec in criterion["metrics"]:
            metric_id = spec["id"]
            observed = raw["observed"].get(metric_id)
            value = raw["values"].get(metric_id)
            reason = raw["reasons"].get(metric_id)
            withheld = metric_id in (raw.get("withheld") or ())
            if reason and not withheld:
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
                value = None if not outcome else value["outcome"]
                if detail.get("pointsOverride") is not None and outcome:
                    scored = float(detail["pointsOverride"])
                else:
                    scored = None if not outcome else float(outcome["points"])
                note = detail.get("extraNote")
            else:
                if spec.get("id") == "valuation_primary":
                    kind = "multiple"
                if value is None:
                    scored = None
                elif spec.get("kind") == "binary" and spec["id"] == "above_sma_200":
                    scored = raw["points"].get(metric_id)
                    if scored is None and value is not None:
                        scored = 10.0 if value == 1 else 5.0
                elif spec.get("bands"):
                    scored, _band_note = band_points(value, spec["bands"])
                elif "zeroAt" in spec and "tenAt" in spec:
                    scored = linear_points(value, spec["zeroAt"], spec["tenAt"])
                else:
                    scored = raw["points"].get(metric_id)
            floor = (raw.get("floors") or {}).get(metric_id)
            if floor is not None and scored is not None:
                scored = max(float(scored), float(floor))
            if withheld:
                scored = None
            note = _join_notes(note, raw["labels"].get(metric_id + "_note"))
            row_status = reason if reason else (None if value is not None else missing_label)
            counts = row_status != na_label
            if counts:
                applicable += 1
                metric_total += 1
                if value is not None and not withheld:
                    available += 1
                    metric_available += 1
            source = dict((raw.get("sources") or {}).get(metric_id) or _source_for(metric_id, sources, prices) or {})
            if not source.get("url"):
                source["url"] = sources.get("fundamentalUrl")
            custom_display = raw["labels"].get(metric_id + "_display")
            if custom_display:
                display = custom_display
            if metric_id == "cash_runway_months" and isinstance(value, (int, float)) and value >= 36:
                display = "> 36 Monate"
            if display is not None:
                shown = display
                raw_value = None
            elif kind == "binary" and value is not None:
                shown = format_value("binary", value)
                raw_value = round(float(value), 6)
            else:
                shown = format_value(kind, value)
                raw_value = None if value is None else round(float(value), 6)
            metric_weight = float(spec.get("weight", 1))
            row = {
                "id": metric_id,
                "label": label,
                "value": shown,
                "raw": raw_value,
                "points": None if (not counts or withheld) else round1(scored),
                "status": row_status,
                "scale": raw["labels"].get(metric_id + "_scale") or scale_text(
                    {**spec, "momentum": config.get("momentum")} if metric_id == "excess_return_12_1" else spec
                ),
                "sourceLabel": source.get("label"),
                "sourceUrl": source.get("url") or sources.get("fundamentalUrl"),
                "fetchedAt": fetched_at,
                "observedAt": observed if value is not None else None,
            }
            if "weight" in spec:
                row["weight"] = metric_weight
            if criterion.get("aggregate") == "sum":
                points_max = _points_max(spec)
                if points_max is not None:
                    row["pointsMax"] = int(points_max) if points_max == int(points_max) else points_max
            reason_text = (raw.get("statusReasons") or {}).get(metric_id)
            if reason_text:
                row["statusReason"] = reason_text
            if detail and detail.get("caption"):
                row["caption"] = detail["caption"]
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
                points.append((round1(scored) if scored is not None else None, metric_weight))
        if criterion.get("aggregate") == "sum":
            present = [point for point, _weight in points if point is not None]
            criterion_score = sum(present) if present else None
        else:
            criterion_score = weighted_mean(points)
        slots = criterion.get("metricSlots")
        minimum_metrics = criterion.get("minMetrics")
        if slots:
            quality = f"{available} von {int(slots)} Metriken"
        else:
            quality = f"{available} von {applicable} Metriken verfügbar"
        floor_note = None
        if criterion["id"] == "C_07" and c02_score is not None and criterion_score is not None:
            rules = config.get("momentum") or {}
            if c02_score >= float(rules.get("floorWhenC02AtLeast", 5)) and criterion_score < float(rules.get("floorMin", 3)):
                criterion_score = float(rules.get("floorMin", 3))
                floor_note = "Untergrenze 3, weil C_02 mindestens 5 ist."
        capped = False
        if criterion.get("cap") == "analysts":
            criterion_score, capped = apply_analyst_cap(
                criterion_score,
                analysts.get("count"),
                config["analystCap"]["below"],
                config["analystCap"]["maxPoints"],
            )
        notes = [row["note"] for row in metrics if row.get("note")]
        quality_notes = _join_notes(*notes, floor_note)
        if quality_notes:
            quality = quality + ". " + quality_notes
        shared_statuses = set((config.get("moat") or {}).get("sharedCriterionStatuses") or [])
        if minimum_metrics is not None and available < int(minimum_metrics):
            criterion_score = None
            gap_labels = {row["status"] for row in metrics if row.get("status")}
            only = next(iter(gap_labels)) if len(gap_labels) == 1 else None
            if available == 0 and only in shared_statuses:
                criterion_status = only
            else:
                criterion_status = missing_label
        elif criterion_score is None:
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
        if criterion.get("aggregate"):
            entry["aggregate"] = criterion["aggregate"]
        if capped:
            entry["capApplied"] = True
        if criterion["id"] == "C_06" and analysts.get("summary"):
            entry["summary"] = analysts["summary"]
        criteria.append(entry)
        if criterion["id"] == "C_02":
            c02_score = entry["score"]
    flags = _ko_flags(config, bundle, end, cap, sources)
    warning = raw["labels"].get("excess_return_12_1_note")
    if warning and warning not in flags:
        flags.append(warning)
    return {
        "criteria": criteria,
        "available": metric_available,
        "total": metric_total,
        "koFlags": flags,
        "priceNote": prices.get("note"),
        "dividend": raw.get("dividend") or {},
    }


def _apply_provenance(sources_by_metric, labels, observed, snap):
    """Stamp each metric with the provider that actually supplied the figure."""
    fields = {
        "gross_margin": "gross_profit",
        "gross_margin_change_yoy": "gross_profit",
        "margin_progress": "operating_income",
        "operating_margin_fin": "operating_income",
        "ocf_margin": "cfo",
        "revenue_yoy": "revenue",
        "revenue_ttm_yoy": "revenue",
        "revenue_acceleration": "revenue",
        "revenue_quarter_yoy": "revenue_quarter",
        "fcf_yield": "cfo" if labels.get("fcf_yield_substitute") else "fcf",
        "fcf_margin_3y": "cfo" if labels.get("fcf_margin_3y_substitute") else None,
        "valuation_primary": "revenue",
        "net_debt_to_ebitda": "debt",
        "current_ratio": "current_ratio",
        "cash_runway_months": "cash",
        "net_cash_or_runway": "cash",
        "roe": "roe",
        "rd_intensity": "rd",
    }
    provenance = (snap or {}).get("provenance") or {}
    for metric_id, field in fields.items():
        if not field:
            continue
        src = provenance.get(field)
        if metric_id == "net_debt_to_ebitda":
            src = _join_sources(provenance.get("cash"), provenance.get("debt")) or src
        if metric_id == "current_ratio" and not src:
            src = provenance.get("assets_current")
        if metric_id == "roe" and not src:
            src = provenance.get("equity")
        if not src or not src.get("label"):
            continue
        if metric_id == "rd_intensity" and "Technology and infrastructure" in str(src.get("label")):
            labels["rd_intensity"] = "Forschungsquote, Technology and infrastructure"
        sources_by_metric[metric_id] = {
            "label": src.get("label"),
            "url": src.get("url") or (snap or {}).get("sourceUrl"),
        }
        if src.get("note"):
            labels[metric_id + "_note"] = _join_notes(labels.get(metric_id + "_note"), src["note"])
        if src.get("label") and not str(src["label"]).startswith("SEC"):
            observed[metric_id] = src.get("end")


def _join_sources(left, right):
    if not left and not right:
        return None
    if not left:
        return right
    if not right or left.get("label") == right.get("label"):
        note = " ".join(part for part in (left.get("note"), (right or {}).get("note")) if part)
        merged = dict(left)
        if note:
            merged["note"] = note
        return merged
    note = " ".join(part for part in (left.get("note"), right.get("note")) if part)
    return {
        "label": "Cash: " + str(left.get("label") or "") + "; Finanzschulden: " + str(right.get("label") or ""),
        "url": left.get("url") or right.get("url"),
        "end": left.get("end") or right.get("end"),
        "note": note or None,
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


def _investing_rules(config):
    rules = config.get("investing") or {}
    return {
        "min_op": float(rules.get("minOperatingMargin", 0.0)),
        "min_ocf": float(rules.get("minOcfMargin", 0.10)),
        "mark": rules.get("substituteMark") or "(operativer Cashflow, hohe Investitionen)",
    }


def _acceleration_rules(config):
    rules = config.get("acceleration") or {}
    return {
        "cap": float(rules.get("capPp", 0.30)),
        "floor_growth": float(rules.get("minCurrentGrowth", 0.20)),
        "floor_points": float(rules.get("floorPoints", 5)),
        "mini_revenue": float(rules.get("miniBaseRevenueUsd", 10000000)),
        "mini_growth": float(rules.get("miniBasePriorGrowth", 2.0)),
    }


def _pp_text(value):
    text = f"{round(float(value) * 100.0, 1):.1f}".replace(".", ",")
    if text.endswith(",0"):
        text = text[:-2]
    return text


def _record_acceleration(config, growth, prior_growth, prior_revenue, when, note, put, reasons, values, observed, labels, floors, na):
    """Cap the gap, floor fast growers, and drop a mini base entirely."""
    rules = _acceleration_rules(config)
    if prior_revenue is not None and prior_revenue < rules["mini_revenue"]:
        _not_applicable(reasons, values, observed, "revenue_acceleration", na)
        return
    if prior_growth is not None and prior_growth > rules["mini_growth"]:
        _not_applicable(reasons, values, observed, "revenue_acceleration", na)
        return
    if growth is None or prior_growth is None:
        return
    diff = growth - prior_growth
    cap = rules["cap"]
    capped = max(-cap, min(cap, diff))
    put("revenue_acceleration", capped, when)
    notes = [note] if note else []
    if abs(diff - capped) > 1e-12:
        notes.append("Differenz auf ±" + _pp_text(cap) + " Pp gekappt")
    if growth >= rules["floor_growth"]:
        spec = _metric_spec(config, "revenue_acceleration")
        scored = linear_points(capped, spec.get("zeroAt"), spec.get("tenAt")) if spec else None
        if scored is not None and scored < rules["floor_points"]:
            floors["revenue_acceleration"] = rules["floor_points"]
            notes.append(
                "aktuelles Wachstum mindestens "
                + _pp_text(rules["floor_growth"])
                + " %, deshalb mindestens "
                + _pp_text(rules["floor_points"] / 100.0)
                + " Punkte"
            )
    if notes:
        labels["revenue_acceleration_note"] = _join_notes(*notes)


def _points_max(spec):
    found = []
    for band in spec.get("bands") or []:
        if "points" in band:
            found.append(float(band["points"]))
    for item in (spec.get("outcomes") or {}).values():
        if isinstance(item, dict) and "points" in item:
            found.append(float(item["points"]))
    if not found:
        return None
    return max(found)


def _metric_spec(config, metric_id):
    for criterion in config.get("criteria") or []:
        for spec in criterion.get("metrics") or []:
            if spec.get("id") == metric_id:
                return spec
    return None


def _mark_cash_substitute(labels, metric_id, base_label, mark):
    labels[metric_id] = base_label + " " + mark
    labels[metric_id + "_substitute"] = True


def _raw_metrics(bundle, end, market_cap, price, momentum, analysts, config, profile=None):
    values = {}
    observed = {}
    labels = {}
    points = {}
    reasons = {}
    status_reasons = {}
    floors = {}
    sources_by_metric = {}
    dividend_points = {}
    na = _status_text(config, "notApplicable", "nicht anwendbar")
    revenue = bundle["revenue"]
    snap = bundle.get("snapshot") or {}
    if snap.get("sourceLabel"):
        for metric_id in (
            "gross_margin", "gross_margin_change_yoy", "margin_progress", "operating_margin_fin",
            "ocf_margin", "revenue_yoy", "fcf_yield", "valuation_primary", "net_debt_to_ebitda",
            "current_ratio", "cash_runway_months", "net_cash_or_runway", "roe",
        ):
            sources_by_metric[metric_id] = {
                "label": snap.get("sourceLabel"),
                "url": snap.get("sourceUrl"),
            }
    if snap.get("sharesSourceLabel"):
        for metric_id in ("valuation_primary", "fcf_yield", "ev_to_ebitda", "shareholder_yield"):
            current = sources_by_metric.get(metric_id) or {}
            label = current.get("label") or ""
            extra = "Aktienzahl " + snap["sharesSourceLabel"]
            if extra not in label:
                sources_by_metric[metric_id] = {
                    "label": (label + "; " if label else "") + extra,
                    "url": snap.get("sharesSourceUrl") or current.get("url"),
                }
    gross_margins = gross_margin_series(revenue, bundle["gross_profit"])

    def put(metric_id, value, when):
        values[metric_id] = value
        observed[metric_id] = when

    def mark_undefined(metric_id, reason):
        _not_applicable(reasons, values, observed, metric_id, na, status_reasons, reason)

    def _assign_growth(metric_id, latest, primary, secondary, alt_label, alt_note):
        value, loss = cagr_result(latest, primary)
        if value is None and latest and latest.get("val", 0) > 0 and secondary is not None:
            value2, loss2 = cagr_result(latest, secondary)
            if value2 is not None:
                value = value2
                loss = False
                labels[metric_id] = alt_label
                labels[metric_id + "_note"] = alt_note
            elif loss2:
                loss = True
        if value is None and loss:
            mark_undefined(metric_id, LOSS_BASE_REASON)
            return
        put(metric_id, value, end if value is not None else None)

    if end:
        income_years = sorted(point["end"] for point in bundle["operating_income"] if point["end"] <= end)
        successful = []
        for year in income_years:
            value, loss, note = roic_for_year(year, bundle)
            if value is not None:
                successful.append((year, value, note))
        if len(successful) >= 2 and year_gap(successful[-1][0], successful[-2][0]) <= 1.4:
            put("roic_2y", (successful[-1][1] + successful[-2][1]) / 2.0, successful[-1][0])
            tax_note = _join_notes(successful[-1][2], successful[-2][2])
            if tax_note:
                labels["roic_2y_note"] = tax_note
        elif len(income_years) >= 2 and year_gap(income_years[-1], income_years[-2]) <= 1.4:
            pair = [roic_for_year(year, bundle) for year in income_years[-2:]]
            if any(item[1] for item in pair):
                mark_undefined("roic_2y", LOSS_CAPITAL_REASON)
        if snap.get("revenue") and snap.get("gross_profit") is not None:
            put("gross_margin", snap["gross_profit"] / snap["revenue"], snap["end"])
            labels["gross_margin_note"] = snap.get("label")
        else:
            put("gross_margin", value_at(gross_margins, end), end if value_at(gross_margins, end) is not None else None)
        if len(gross_margins) >= 3:
            window = [point["val"] for point in gross_margins[-5:]]
            put("gross_margin_stability", statistics.pstdev(window), gross_margins[-1]["end"])
        if snap.get("revenue") and snap.get("operating_income") is not None:
            put("operating_margin", snap["operating_income"] / snap["revenue"], snap["end"])
        else:
            put("operating_margin", _margin(bundle["operating_income"], revenue, end), end if value_at(bundle["operating_income"], end) is not None else None)
        put("operating_margin_fin", values.get("operating_margin"), observed.get("operating_margin"))
        if snap.get("revenue") and snap.get("cfo") is not None:
            cfo_src = ((snap.get("provenance") or {}).get("cfo") or {})
            revenue_src = ((snap.get("provenance") or {}).get("revenue") or {})
            # Undated Yahoo cash flow is a trailing figure. It may fill a TTM
            # window. It must not be divided by a shorter SEC stub.
            mixed = (
                str(cfo_src.get("label") or "").startswith("Yahoo")
                and str(revenue_src.get("label") or snap.get("sourceLabel") or "").startswith("SEC")
                and not cfo_src.get("end")
                and (snap.get("months") or 12) < 11
            )
            if not mixed:
                put("ocf_margin", snap["cfo"] / snap["revenue"], snap.get("cfo_end") or snap["end"])
                labels["ocf_margin_note"] = snap.get("label")
        fcf_margins = []
        for point in bundle["fcf"]:
            margin = _margin(bundle["fcf"], revenue, point["end"])
            if margin is not None:
                fcf_margins.append(margin)
        if fcf_margins:
            put("fcf_margin_3y", sum(fcf_margins[-3:]) / len(fcf_margins[-3:]), end)
        if snap.get("rd") is not None and snap.get("revenue"):
            put("rd_intensity", snap["rd"] / snap["revenue"], snap["end"])
            labels["rd_intensity_note"] = snap.get("label")
        latest_revenue = at(revenue, end)
        three = point_years_before(revenue, end, 3)
        five = point_years_before(revenue, end, 5)
        _assign_growth("revenue_cagr_3y", latest_revenue, three, None, None, None)
        _assign_growth("revenue_cagr_5y", latest_revenue, five, None, None, None)
        eps_now = at(bundle["eps"], end)
        _assign_growth(
            "eps_cagr_3y", eps_now, point_years_before(bundle["eps"], end, 3),
            point_years_before(bundle["eps"], end, 2),
            "EPS-CAGR, 2 Jahre",
            "2-Jahres-CAGR, weil die 3-Jahres-Basis nicht positiv ist",
        )
        fcf_now = at(bundle["fcf"], end)
        _assign_growth(
            "fcf_cagr_3y", fcf_now, point_years_before(bundle["fcf"], end, 3),
            point_years_before(bundle["fcf"], end, 2),
            "Free-Cashflow-CAGR, 2 Jahre",
            "2-Jahres-CAGR, weil die 3-Jahres-Basis nicht positiv ist",
        )
        prior_end = point_years_before(revenue, end, 1, 0.4)
        if snap.get("revenue") and snap.get("revenue_prior"):
            growth = snap["revenue"] / snap["revenue_prior"] - 1.0
            put("revenue_yoy", growth, snap["end"])
            labels["revenue_yoy_note"] = snap.get("label")
            put("revenue_ttm_yoy", growth, snap["end"])
            months = snap.get("months") or 0
            if months >= 11:
                labels["revenue_ttm_yoy"] = "Umsatzwachstum, TTM"
            elif months >= 5:
                labels["revenue_ttm_yoy"] = "Umsatzwachstum, Halbjahr zum Vorjahr"
            else:
                labels["revenue_ttm_yoy"] = "Umsatzwachstum, Quartal zum Vorjahr"
            labels["revenue_ttm_yoy_note"] = snap.get("label")
            base = snap.get("revenue_prior_prior")
            if not base:
                earlier = point_years_before(revenue, snap.get("end") or end, 2, 0.6)
                if earlier and earlier.get("val"):
                    base = earlier["val"]
            if base:
                prior_growth = snap["revenue_prior"] / base - 1.0
                _record_acceleration(
                    config, growth, prior_growth, snap.get("revenue_prior"), snap["end"], snap.get("label"),
                    put, reasons, values, observed, labels, floors, na,
                )
        else:
            growth = yoy(revenue, end)
            put("revenue_yoy", growth, end if growth is not None else None)
            prior_growth = yoy(revenue, prior_end["end"]) if prior_end else None
            if growth is not None and prior_growth is not None:
                _record_acceleration(
                    config, growth, prior_growth, prior_end.get("val") if prior_end else None, end, None,
                    put, reasons, values, observed, labels, floors, na,
                )
        if snap.get("revenue_quarter_yoy") is not None:
            put("revenue_quarter_yoy", snap["revenue_quarter_yoy"], snap.get("revenue_quarter_end"))
            if snap.get("revenue_quarter_label"):
                labels["revenue_quarter_yoy_note"] = snap["revenue_quarter_label"]
        estimate = analysts.get("revenueEstimateYoy") if analysts else None
        if estimate is not None:
            put("revenue_estimate_yoy", estimate, None)
            labels["revenue_estimate_yoy_note"] = "Analystenschätzung, nächstes Jahr"
            sources_by_metric["revenue_estimate_yoy"] = {
                "label": (analysts.get("label") or "Yahoo Finance quoteSummary") + ", Analystenschätzung",
                "url": analysts.get("url"),
            }
        rd = value_at(bundle["rd"], end)
        if values.get("rd_intensity") is not None:
            rd = None
        sales = snap.get("revenue") if snap.get("revenue") else value_at(revenue, end)
        if rd is not None and sales:
            put("rd_intensity", rd / sales, end)
        if snap.get("revenue") and snap.get("gross_profit") is not None and snap.get("revenue_prior") and snap.get("gross_profit_prior") is not None:
            gm_now = snap["gross_profit"] / snap["revenue"]
            gm_then = snap["gross_profit_prior"] / snap["revenue_prior"]
            put("gross_margin_change_yoy", gm_now - gm_then, snap["end"])
            labels["gross_margin_change_yoy_note"] = snap.get("label")
        else:
            gm_now = value_at(gross_margins, end)
            gm_then = value_at(gross_margins, prior_end["end"]) if prior_end else None
            if gm_now is not None and gm_then is not None:
                put("gross_margin_change_yoy", gm_now - gm_then, end)
        equity = snap.get("equity") if snap.get("equity") is not None else value_at(bundle["equity"], end)
        net_income = snap.get("net_income") if snap.get("net_income") is not None else value_at(bundle["net_income"], end)
        loss = net_income is not None and net_income <= 0
        profit = net_income is not None and net_income > 0
        op_now = values.get("operating_margin")
        if snap.get("revenue_prior") and snap.get("operating_income_prior") is not None:
            op_then = snap["operating_income_prior"] / snap["revenue_prior"]
        else:
            op_then = _margin(bundle["operating_income"], revenue, prior_end["end"]) if prior_end else None
        if op_now is not None and op_then is not None:
            put("margin_progress", op_now - op_then, snap.get("end") or end)
            labels["margin_progress_note"] = snap.get("label")
        if profit:
            _not_applicable(reasons, values, observed, "operating_margin_change_yoy", na)
        elif loss and op_now is not None and op_then is not None:
            put("operating_margin_change_yoy", op_now - op_then, snap.get("end") or end)
        prior_equity_point = point_years_before(bundle["equity"], end, 1, 0.4)
        if values.get("roe") is None and snap.get("roe") is not None:
            put("roe", snap["roe"], snap.get("equity_end"))
        if net_income is not None and equity and prior_equity_point and prior_equity_point["val"]:
            average_equity = (equity + prior_equity_point["val"]) / 2.0
            if average_equity > 0:
                put("roe", net_income / average_equity, end)
        if snap.get("operating_income") is not None and snap.get("da") is not None:
            ebitda = snap["operating_income"] + abs(snap["da"])
            if snap.get("months") and snap["months"] > 0 and snap["months"] != 12:
                ebitda = ebitda * (12.0 / snap["months"])
        else:
            ebitda = ebitda_at(end, bundle)
        debt = snap.get("debt") if snap.get("debt") is not None else value_at(bundle["debt"], end)
        cash = snap.get("cash") if snap.get("cash") is not None else value_at(bundle["cash"], end)
        fcf_value = snap.get("fcf") if snap.get("fcf") is not None else value_at(bundle["fcf"], end)
        period_months = snap.get("months") or 12.0
        cfo_value = snap.get("cfo") if snap.get("cfo") is not None else value_at(bundle.get("cfo") or [], end)
        investing_rules = _investing_rules(config)
        investing = (
            profit
            and op_now is not None
            and op_now > investing_rules["min_op"]
            and values.get("ocf_margin") is not None
            and values["ocf_margin"] >= investing_rules["min_ocf"]
            and fcf_value is not None
            and fcf_value < 0
            and cfo_value is not None
            and cfo_value > 0
        )
        if investing:
            _not_applicable(reasons, values, observed, "fcf_cagr_3y", na)
            status_reasons.pop("fcf_cagr_3y", None)
            labels.pop("fcf_cagr_3y", None)
            labels.pop("fcf_cagr_3y_note", None)
            _not_applicable(reasons, values, observed, "cash_runway_months", na)
            put(
                "fcf_margin_3y",
                values["ocf_margin"],
                observed.get("ocf_margin") or snap.get("cfo_end") or snap.get("end") or end,
            )
            _mark_cash_substitute(
                labels, "fcf_margin_3y", "Free-Cashflow-Marge", investing_rules["mark"],
            )
        elif fcf_value is not None and fcf_value < 0:
            _not_applicable(reasons, values, observed, "roe", na)
            if cash is not None and cash >= 0 and fcf_value != 0:
                put("cash_runway_months", (cash / abs(fcf_value)) * period_months, snap.get("end") or end)
        elif fcf_value is not None:
            _not_applicable(reasons, values, observed, "cash_runway_months", na)
        net_cash = cash is not None and debt is not None and (cash - debt) > 0
        if net_cash and not (ebitda is not None and ebitda > 0):
            put("net_debt_to_ebitda", 0.0, snap.get("debt_end") or snap.get("cash_end") or snap.get("balance_end") or end)
            labels["net_debt_to_ebitda_display"] = "Netto-Cash"
            labels["net_debt_to_ebitda_note"] = "Netto-Cash, deshalb 10"
        elif ebitda is not None and ebitda > 0 and debt is not None and cash is not None:
            put("net_debt_to_ebitda", (debt - cash) / ebitda, snap.get("debt_end") or snap.get("balance_end") or end)
            labels["net_debt_to_ebitda"] = "Nettoverschuldung / EBITDA, Finanzschulden ohne Leasing"
            if net_cash:
                labels["net_debt_to_ebitda_note"] = "Netto-Cash, deshalb 10"
        elif (
            values.get("net_debt_to_ebitda") is None
            and "net_debt_to_ebitda" not in reasons
            and ebitda is not None
            and ebitda <= 0
            and debt is not None
            and cash is not None
        ):
            mark_undefined("net_debt_to_ebitda", EBITDA_NEGATIVE_REASON)
        if cash is not None and debt is not None and (cash - debt) > 0:
            points["net_cash_or_runway"] = 10.0
            values["net_cash_or_runway"] = 1
            observed["net_cash_or_runway"] = snap.get("balance_end") or end
            labels["net_cash_or_runway_display"] = "Netto-Cash"
        elif values.get("cash_runway_months") is not None:
            points["net_cash_or_runway"] = linear_points(values["cash_runway_months"], 12, 36)
            values["net_cash_or_runway"] = 1
            observed["net_cash_or_runway"] = observed.get("cash_runway_months")
            runway_text = format_value("months", values["cash_runway_months"])
            if values["cash_runway_months"] >= 36:
                runway_text = "> 36 Monate"
            labels["net_cash_or_runway_display"] = runway_text
        elif investing:
            _not_applicable(reasons, values, observed, "net_cash_or_runway", na)
        elif (
            values.get("net_cash_or_runway") is None
            and "net_cash_or_runway" not in reasons
            and cash is not None
            and debt is not None
            and (cash - debt) <= 0
            and fcf_value is not None
            and fcf_value > 0
        ):
            mark_undefined("net_cash_or_runway", NET_DEBT_FCF_REASON)
        current_assets = snap.get("assets_current") if snap.get("assets_current") is not None else value_at(bundle["assets_current"], end)
        current_liabilities = snap.get("liabilities_current") if snap.get("liabilities_current") is not None else value_at(bundle["liabilities_current"], end)
        if snap.get("current_ratio") is not None:
            put("current_ratio", snap["current_ratio"], snap.get("assets_current_end"))
        elif current_assets is not None and current_liabilities and current_liabilities > 0:
            put("current_ratio", current_assets / current_liabilities, snap.get("assets_current_end") or end)
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
        yield_fcf = fcf_value
        if snap.get("fcf") is not None and snap.get("months") and snap["months"] > 0:
            yield_fcf = fcf_value * (12.0 / snap["months"]) if fcf_value is not None else None
        yield_ocf = cfo_value
        if snap.get("cfo") is not None and snap.get("months") and snap["months"] > 0:
            yield_ocf = cfo_value * (12.0 / snap["months"]) if cfo_value is not None else None
        if loss:
            _not_applicable(reasons, values, observed, "fcf_yield", na)
        elif investing and market_cap and yield_ocf is not None:
            put("fcf_yield", yield_ocf / market_cap, momentum.get("observed_price") or snap.get("cfo_end") or snap.get("end") or end)
            _mark_cash_substitute(
                labels, "fcf_yield", "Free-Cashflow-Rendite", investing_rules["mark"],
            )
        elif market_cap and yield_fcf is not None:
            put("fcf_yield", yield_fcf / market_cap, momentum.get("observed_price") or snap.get("end") or end)
        dividend_points = _dividend_facts(bundle, market_cap, end)
        if dividend_points.get("yield") is not None:
            put("dividend_yield", dividend_points["yield"], end)
        if dividend_points.get("payout") is not None:
            put("payout_to_fcf", dividend_points["payout"], end)
        if dividend_points.get("no_cut") is not None:
            put("dividend_no_cut_5y", 1.0 if dividend_points["no_cut"] else 0.0, end)
            points["dividend_no_cut_5y"] = 10.0 if dividend_points["no_cut"] else 0.0
        if market_cap and debt is not None and cash is not None and sales:
            if ebitda is not None and ebitda <= 0:
                mark_undefined("ev_to_ebitda", EBITDA_NEGATIVE_REASON)
            elif ebitda is not None and ebitda > 0:
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
                    labels["valuation_primary_scale"] = _band_scale(valuation["profitable"])
                else:
                    growth, used_cagr = _positive_growth(values)
                    if values.get("eps_cagr_3y") is None:
                        note = "Ersatz für PEG, weil das EPS-Wachstum nicht berechenbar ist"
                    else:
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
    _momentum_points(values, points, reasons, labels, config)
    if momentum.get("excess_return_12_1") is not None and momentum["excess_return_12_1"] > float((config.get("momentum") or {}).get("plausibilityExcessPp", 300)):
        labels["excess_return_12_1_note"] = "Plausibilitätswarnung: 12-1 über +300 Pp"
    _mark_structural(
        reasons, values, observed, config, bundle, end,
        momentum.get("barCount") or 0, profile,
    )
    disclosed = _status_text(config, "notDisclosed", "nicht ausgewiesen")
    if (
        values.get("rd_intensity") is None
        and "rd_intensity" not in reasons
        and (profile or {}).get("sec", True)
        and (bundle.get("revenue") or snap.get("revenue"))
        and not bundle.get("rd")
        and snap.get("rd") is None
    ):
        reasons["rd_intensity"] = disclosed
    _apply_provenance(sources_by_metric, labels, observed, snap)
    raw = {
        "values": values,
        "observed": observed,
        "labels": labels,
        "points": points,
        "reasons": reasons,
        "statusReasons": status_reasons,
        "floors": floors,
        "sources": sources_by_metric,
        "dividend": dividend_points if end else {},
    }
    from scoring.moat import apply_moat
    apply_moat(raw, bundle, config, profile)
    return raw


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
