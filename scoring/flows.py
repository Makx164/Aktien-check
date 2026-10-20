"""Trailing twelve months and the latest balance sheet from companyfacts.

Annual series stay in extract_fundamentals for CAGRs. Margins, year-on-year
growth, cash flow and the balance sheet use the latest comparable period:
four quarters when they exist, otherwise the longest year-ago stub (H1 or a
quarter). Missing pieces stay absent. Nothing is filled with zero.
"""

from __future__ import annotations

from scoring.metrics import (
    POSITION_FORMS,
    _find_concept,
    _rows,
    latest_position,
    parse_date,
    research_concepts,
    year_gap,
)


QUARTER_MIN = 70
QUARTER_MAX = 120


def _days(start, end):
    if not start or not end:
        return None
    return (parse_date(end) - parse_date(start)).days


def _concept_rows(facts, concepts, units):
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
            if not row.get("end") or not row.get("start") or row["start"] >= row["end"]:
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
    best = {}
    for row in chosen:
        slot = (row.get("start"), row.get("end"))
        current = best.get(slot)
        if current is None or (row.get("filed") or "") >= (current.get("filed") or ""):
            best[slot] = row
    return list(best.values())


def _quarters(rows, allow_derived=True, allow_negative_derived=True):
    explicit = {}
    longer = []
    for row in rows:
        days = _days(row.get("start"), row.get("end"))
        if days is None:
            continue
        if QUARTER_MIN <= days <= QUARTER_MAX:
            current = explicit.get(row["end"])
            if current is None or (row.get("filed") or "") >= (current.get("filed") or ""):
                explicit[row["end"]] = {
                    "end": row["end"],
                    "start": row["start"],
                    "val": float(row["val"]),
                    "filed": row.get("filed") or "",
                }
        elif days > QUARTER_MAX:
            longer.append(row)
    if not allow_derived:
        return [explicit[end] for end in sorted(explicit)]
    by_start = {}
    for row in longer:
        by_start.setdefault(row["start"], []).append(row)
    for start, group in by_start.items():
        group.sort(key=lambda item: item["end"])
        previous = None
        for row in group:
            if previous is not None and row["end"] not in explicit:
                gap = _days(previous["end"], row["end"])
                derived = float(row["val"]) - float(previous["val"])
                # A negative slice of a cumulative revenue or cost line is a
                # restatement (the later year sits below the older year-to-date),
                # not a real quarter. Income and cash flow may be negative.
                if gap is not None and QUARTER_MIN <= gap <= QUARTER_MAX and (allow_negative_derived or derived >= 0):
                    explicit[row["end"]] = {
                        "end": row["end"],
                        "start": previous["end"],
                        "val": derived,
                        "filed": row.get("filed") or "",
                    }
            previous = row
    return [explicit[end] for end in sorted(explicit)]


def _continuous(quarters, end_index, count):
    if end_index - count + 1 < 0:
        return None
    window = quarters[end_index - count + 1:end_index + 1]
    for older, newer in zip(window, window[1:]):
        gap = _days(older["end"], newer["end"])
        if gap is None or gap < 70 or gap > 140:
            return None
    span = _days(window[0]["end"], window[-1]["end"])
    if span is None or span < 240 or span > 380:
        return None
    return window


def _sum_window(window):
    return {
        "end": window[-1]["end"],
        "start": window[0].get("start") or window[0]["end"],
        "val": sum(point["val"] for point in window),
        "filed": window[-1].get("filed") or "",
        "label": "TTM bis " + window[-1]["end"],
        "months": 12.0,
    }


def _trailing_pair(quarters):
    if len(quarters) < 4:
        return None
    latest = _continuous(quarters, len(quarters) - 1, 4)
    if not latest:
        return None
    current = _sum_window(latest)
    prior = None
    for index in range(len(quarters) - 1, 2, -1):
        if abs(year_gap(current["end"], quarters[index]["end"]) - 1.0) > 0.12:
            continue
        window = _continuous(quarters, index, 4)
        if window:
            prior = _sum_window(window)
            break
    return current, prior


def _stub_pair(rows):
    """Longest year-ago comparison when four quarters are not available."""
    candidates = []
    for row in rows:
        days = _days(row.get("start"), row.get("end"))
        if days is None or days < QUARTER_MIN:
            continue
        for other in rows:
            if other is row or not other.get("end") or other["end"] >= row["end"]:
                continue
            other_days = _days(other.get("start"), other.get("end"))
            if other_days is None or abs(other_days - days) > 25:
                continue
            if abs(year_gap(row["end"], other["end"]) - 1.0) > 0.12:
                continue
            if row.get("start") and other.get("start") and abs(year_gap(row["start"], other["start"]) - 1.0) > 0.12:
                continue
            candidates.append((row["end"], days, row, other))
    if not candidates:
        return None
    candidates.sort()
    end, days, row, other = candidates[-1]
    # Prefer the longest stub that ends on the latest date.
    same_end = [item for item in candidates if item[0] == end]
    same_end.sort(key=lambda item: item[1])
    end, days, row, other = same_end[-1]
    months = days / 30.4375
    label = "Vergleich bis " + row["end"]
    if 160 <= days <= 220:
        label = "H1 bis " + row["end"]
    elif QUARTER_MIN <= days <= QUARTER_MAX:
        label = "Quartal bis " + row["end"]
    current = {
        "end": row["end"],
        "start": row.get("start"),
        "val": float(row["val"]),
        "filed": row.get("filed") or "",
        "label": label,
        "months": months,
    }
    prior = {
        "end": other["end"],
        "start": other.get("start"),
        "val": float(other["val"]),
        "filed": other.get("filed") or "",
        "label": label,
        "months": months,
    }
    return current, prior


def _index_years_before(quarters, end, years=1.0, tolerance=0.12):
    best = None
    best_dist = None
    for index, quarter in enumerate(quarters):
        if quarter["end"] >= end:
            continue
        dist = abs(year_gap(end, quarter["end"]) - years)
        if dist > tolerance:
            continue
        if best_dist is None or dist < best_dist:
            best = index
            best_dist = dist
    return best


def _row_years_before(rows, anchor, years=1.0, tolerance=0.12):
    """Same-length reported flow ending one year before anchor. No annual stand-in."""
    if not anchor or not anchor.get("end"):
        return None
    days = _days(anchor.get("start"), anchor.get("end"))
    best = None
    best_dist = None
    for row in rows:
        if not row.get("end") or row["end"] >= anchor["end"]:
            continue
        dist = abs(year_gap(anchor["end"], row["end"]) - years)
        if dist > tolerance:
            continue
        other_days = _days(row.get("start"), row.get("end"))
        if days is not None and other_days is not None and abs(other_days - days) > 25:
            continue
        if anchor.get("start") and row.get("start") and abs(year_gap(anchor["start"], row["start"]) - years) > tolerance:
            continue
        if best_dist is None or dist < best_dist or (
            dist == best_dist and (row.get("filed") or "") >= (best.get("filed") or "")
        ):
            best = row
            best_dist = dist
    return best


def _earlier_value(facts, concepts, units, current, prior):
    """Revenue of the window one year before `prior`, same length.

    A TTM comparison uses the earlier TTM. A half-year or quarter uses the
    matching stub. An annual figure is not mixed into a shorter window.
    """
    if not current or not prior or not prior.get("end") or not prior.get("val"):
        return None
    rows = _concept_rows(facts, concepts, units)
    if (current.get("months") or 0) >= 11:
        quarters = _quarters(rows, allow_negative_derived=False)
        index = _index_years_before(quarters, prior["end"])
        if index is None:
            return None
        window = _continuous(quarters, index, 4)
        if not window:
            return None
        summed = _sum_window(window)
        if abs(year_gap(prior["end"], summed["end"]) - 1.0) > 0.12 or summed["val"] <= 0:
            return None
        return summed["val"]
    match = _row_years_before(rows, prior)
    if match is None or match.get("val") is None or float(match["val"]) <= 0:
        return None
    return float(match["val"])


def _aligned(flow, anchor):
    if not flow or not anchor or flow.get("end") != anchor.get("end"):
        return False
    return abs((flow.get("months") or 0) - (anchor.get("months") or 0)) <= 1.5


def flow_matching(facts, concepts, units, anchor, allow_negative_derived=True):
    """Flow for the same period as the revenue window, not a longer TTM beside an H1."""
    if anchor is None:
        return None, None
    current, prior = flow_pair(
        facts, concepts, units, allow_negative_derived=allow_negative_derived,
    )
    if _aligned(current, anchor):
        return current, prior
    rows = _concept_rows(facts, concepts, units)
    pair = _stub_pair(rows)
    if pair and _aligned(pair[0], anchor):
        return pair
    return None, None


def flow_pair(facts, concepts, units, allow_derived=True, allow_negative_derived=True):
    rows = _concept_rows(facts, concepts, units)
    if not rows:
        return None, None
    quarters = _quarters(rows, allow_derived=allow_derived, allow_negative_derived=allow_negative_derived)
    pair = _trailing_pair(quarters)
    if pair and pair[0] is not None:
        return pair
    return _stub_pair(rows) or (None, None)


def quarter_yoy(facts, concepts, units):
    """Last reported quarter against the quarter one year earlier.

    Uses explicit quarters and only those derived slices that stay non-negative.
    Returns None when either quarter is missing. A missing quarter is not zero.
    """
    rows = _concept_rows(facts, concepts, units)
    quarters = _quarters(rows, allow_negative_derived=False)
    if len(quarters) < 2:
        return None
    last = quarters[-1]
    prior = None
    for older in quarters:
        if abs(year_gap(last["end"], older["end"]) - 1.0) <= 0.12 and older["val"]:
            prior = older
    if prior is None:
        return None
    return {
        "end": last["end"],
        "prior_end": prior["end"],
        "growth": last["val"] / prior["val"] - 1.0,
        "label": "Quartal bis " + last["end"],
    }


def _point(series):
    if not series:
        return None
    return series[-1]


def build_snapshot(facts, annual):
    """Current flow and balance. annual is the extract_fundamentals dict."""
    revenue_concepts = [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenue",
        "RevenueFromContractsWithCustomers",
        "SalesRevenueNet",
        "Revenues",
    ]
    revenue, revenue_prior = flow_pair(facts, revenue_concepts, ["USD"], allow_negative_derived=False)
    if revenue is None:
        return None
    cogs, cogs_prior = flow_matching(facts, [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        "CostOfSales",
    ], ["USD"], revenue, allow_negative_derived=False)
    operating, operating_prior = flow_matching(facts, [
        "OperatingIncomeLoss",
        "ProfitLossFromOperatingActivities",
    ], ["USD"], revenue)
    net_income, _net_prior = flow_matching(facts, [
        "NetIncomeLoss",
        "ProfitLoss",
        "ProfitLossAttributableToOwnersOfParent",
    ], ["USD"], revenue)
    cfo, _cfo_prior = flow_matching(facts, [
        "NetCashProvidedByUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ], ["USD"], revenue)
    capex, _capex_prior = flow_matching(facts, [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    ], ["USD"], revenue, allow_negative_derived=False)
    depreciation, _da_prior = flow_matching(facts, [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "DepreciationAndAmortisationExpense",
    ], ["USD"], revenue)
    rd_concepts = research_concepts(facts)
    research, _rd_prior = flow_matching(
        facts, rd_concepts, ["USD"], revenue, allow_negative_derived=False,
    )
    revenue_prior_prior = _earlier_value(facts, revenue_concepts, ["USD"], revenue, revenue_prior)
    cash = _point(latest_position(facts, [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashAndCashEquivalents",
        "Cash",
    ], ["USD"]))
    equity = _point(latest_position(facts, [
        "StockholdersEquity",
        "EquityAttributableToOwnersOfParent",
        "Equity",
    ], ["USD"]))
    assets_current = _point(latest_position(facts, ["AssetsCurrent", "CurrentAssets"], ["USD"]))
    liabilities_current = _point(latest_position(facts, ["LiabilitiesCurrent", "CurrentLiabilities"], ["USD"]))
    shares = _point(latest_position(facts, [
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
    ], ["shares"]))
    debt = _debt_latest(facts)
    quarter = quarter_yoy(facts, revenue_concepts, ["USD"])
    gross = None
    gross_prior = None
    if cogs and cogs["end"] == revenue["end"]:
        gross = revenue["val"] - cogs["val"]
    if cogs_prior and revenue_prior and cogs_prior["end"] == revenue_prior["end"]:
        gross_prior = revenue_prior["val"] - cogs_prior["val"]
    fcf = None
    if cfo and capex and cfo["end"] == capex["end"]:
        fcf = cfo["val"] - abs(capex["val"])
    snapshot = {
        "end": revenue["end"],
        "label": revenue["label"],
        "months": revenue["months"],
        "revenue": revenue["val"],
        "revenue_prior": None if revenue_prior is None else revenue_prior["val"],
        "revenue_prior_prior": revenue_prior_prior,
        "operating_income": None if operating is None or operating["end"] != revenue["end"] else operating["val"],
        "operating_income_prior": None if operating_prior is None or revenue_prior is None or operating_prior["end"] != revenue_prior["end"] else operating_prior["val"],
        "net_income": None if net_income is None or net_income["end"] != revenue["end"] else net_income["val"],
        "cfo": None if cfo is None or cfo["end"] != revenue["end"] else cfo["val"],
        "fcf": fcf,
        "da": None if depreciation is None or depreciation["end"] != revenue["end"] else depreciation["val"],
        "rd": None if research is None or research["end"] != revenue["end"] else research["val"],
        "gross_profit": gross,
        "gross_profit_prior": gross_prior,
        "cash": None if cash is None else cash["val"],
        "debt": None if debt is None else debt["val"],
        "equity": None if equity is None else equity["val"],
        "assets_current": None if assets_current is None else assets_current["val"],
        "liabilities_current": None if liabilities_current is None else liabilities_current["val"],
        "shares_out": None if shares is None else shares["val"],
        "balance_end": None if cash is None else cash["end"],
        "cash_end": None if cash is None else cash["end"],
        "debt_end": None if debt is None else debt["end"],
        "debt_explicit_zero": bool(debt is not None and debt["val"] == 0),
        "equity_end": None if equity is None else equity["end"],
        "assets_current_end": None if assets_current is None else assets_current["end"],
        "liabilities_current_end": None if liabilities_current is None else liabilities_current["end"],
        "cfo_end": None if cfo is None or cfo["end"] != revenue["end"] else cfo["end"],
        "fcf_end": revenue["end"] if fcf is not None else None,
        "revenue_quarter_yoy": None if quarter is None else quarter["growth"],
        "revenue_quarter_end": None if quarter is None else quarter["end"],
        "revenue_quarter_label": None if quarter is None else quarter["label"],
        "sourceLabel": "SEC EDGAR companyfacts",
    }
    provenance = {}
    sec = "SEC EDGAR companyfacts"
    for field, point in (
        ("revenue", revenue),
        ("gross_profit", {"end": revenue["end"]} if gross is not None else None),
        ("operating_income", operating if operating and operating["end"] == revenue["end"] else None),
        ("net_income", net_income if net_income and net_income["end"] == revenue["end"] else None),
        ("cfo", cfo if cfo and cfo["end"] == revenue["end"] else None),
        ("fcf", {"end": revenue["end"]} if fcf is not None else None),
        ("da", depreciation if depreciation and depreciation["end"] == revenue["end"] else None),
        ("rd", research if research and research["end"] == revenue["end"] else None),
        ("cash", cash),
        ("debt", debt),
        ("equity", equity),
        ("assets_current", assets_current),
        ("liabilities_current", liabilities_current),
    ):
        if point is None:
            continue
        label = sec
        if field == "rd" and rd_concepts[-1] == "TechnologyAndInfrastructureExpense":
            label = "SEC EDGAR, Technology and infrastructure"
        provenance[field] = {"label": label, "url": None, "end": point.get("end")}
    if quarter is not None:
        provenance["revenue_quarter"] = {"label": sec, "url": None, "end": quarter["end"]}
    snapshot["provenance"] = provenance
    # Keep annual shares if the quarterly share count is missing.
    if snapshot["shares_out"] is None and annual.get("shares_out"):
        snapshot["shares_out"] = annual["shares_out"][-1]["val"]
    return snapshot


def _debt_components(facts):
    """Previous component sum. Lease liabilities are not included."""
    long_term = latest_position(facts, ["LongTermDebt", "Borrowings"], ["USD"])
    short_term = latest_position(facts, [
        "ShortTermBorrowings",
        "ShorttermBorrowings",
        "CommercialPaper",
    ], ["USD"])
    if not long_term and not short_term:
        noncurrent = latest_position(facts, ["LongTermDebtNoncurrent", "LongtermBorrowings"], ["USD"])
        current = latest_position(facts, [
            "LongTermDebtCurrent",
            "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
            "NotesPayableCurrent",
        ], ["USD"])
        parts = [series[-1] for series in (noncurrent, current, short_term) if series]
        if not parts:
            return None
        end = max(point["end"] for point in parts)
        present = [point for point in parts if point["end"] == end]
        if not present:
            return None
        return {"end": end, "val": sum(point["val"] for point in present)}
    anchor = (long_term or short_term)[-1]
    extra = 0.0
    if short_term:
        matched = [point for point in short_term if point["end"] == anchor["end"]]
        if matched and long_term:
            extra = matched[-1]["val"]
        elif not long_term:
            return {"end": anchor["end"], "val": anchor["val"]}
    if not long_term:
        return {"end": anchor["end"], "val": anchor["val"]}
    return {"end": anchor["end"], "val": long_term[-1]["val"] + extra}


def _debt_latest(facts):
    """Interest-bearing debt excluding lease liabilities, at the latest filing.

    An explicit zero is a reported figure, not a missing tag. The newest
    period wins, including a later filing that reports zero.
    """
    candidates = []
    for name in (
        "DebtInstrumentCarryingAmount",
        "LongTermDebt",
        "Borrowings",
        "NotesPayable",
        "LongTermNotesPayable",
    ):
        series = latest_position(facts, [name], ["USD"])
        if series:
            point = series[-1]
            candidates.append(point)
    component = _debt_components(facts)
    if component:
        candidates.append(component)
    if not candidates:
        return None
    end = max(point["end"] for point in candidates)
    newest = [point for point in candidates if point["end"] == end]
    # A reported total at this date beats a sum of older component concepts
    # that happen to share the date. Explicit zero stays zero.
    return {"end": end, "val": newest[-1]["val"]}
