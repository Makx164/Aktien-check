"""C_01 and C_03 from industry benchmarks, segments and filing facts.

Thresholds live in config.scoring.json under "moat". A missing input is
"n/a", never a neutral 5. Custom points are returned without zeroAt/tenAt
so the linear scorer does not overwrite them.
"""

from __future__ import annotations

import math

from scoring.metrics import _join_notes, gross_margin_series, parse_date, roic_for_year, value_at, year_gap
from scoring.model import format_de, format_value, linear_points

C01_IDS = ("gm_vs_industry", "gm_stability", "roic_spread", "revenue_quality")
C03_IDS = ("industry_growth", "ebit_pool", "rd_vs_industry", "headroom")
NO_CUSTOMER_NOTE = "kein Großkunde ≥ 10 % gemeldet"
BRANCH_NOTE = "nur Branchenebene, keine Segmentdaten"
BUILD_PHASE = "n/a (Aufbauphase)"
NO_US = "nicht bewertbar – keine US-Meldepflicht"
NA = "n/a"


def _rules(config):
    return (config or {}).get("moat") or {}


def _status_text(config, key, default):
    return ((config.get("statuses") or {}).get(key) or default)


def median_capped(values, low, high):
    """Median of the present rates, then clipped to the industry cap."""
    present = sorted(float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool))
    if not present:
        return None
    count = len(present)
    if count % 2:
        middle = present[count // 2]
    else:
        middle = (present[count // 2 - 1] + present[count // 2]) / 2.0
    return min(float(high), max(float(low), middle))


def concentration_points(pct, bands):
    """Customer-share bands. `below` is exclusive, `atMost` is inclusive."""
    if pct is None:
        return None
    pct = float(pct)
    for band in bands or []:
        if band.get("else"):
            return float(band["points"])
        if "below" in band and pct < float(band["below"]):
            return float(band["points"])
        if "atMost" in band and pct <= float(band["atMost"]):
            return float(band["points"])
    return None


def liability_points(ratio, at_zero, ten_at):
    """0 % scores at_zero, ten_at and above score 10, linear in between."""
    if ratio is None or ten_at is None or float(ten_at) <= 0:
        return None
    ratio = float(ratio)
    at_zero = float(at_zero)
    ten_at = float(ten_at)
    if ratio <= 0:
        return at_zero
    if ratio >= ten_at:
        return 10.0
    return at_zero + (10.0 - at_zero) * (ratio / ten_at)


def rd_factor_points(factor, rules):
    """0 → 0, midFactor → midPoints, tenFactor and above → tenPoints."""
    if factor is None:
        return None
    factor = float(factor)
    zero = float(rules.get("zeroFactor", 0))
    mid_factor = float(rules.get("midFactor", 1))
    mid_points = float(rules.get("midPoints", 6))
    ten_factor = float(rules.get("tenFactor", 2))
    ten_points = float(rules.get("tenPoints", 10))
    if factor <= zero:
        return 0.0
    if factor <= mid_factor:
        if mid_factor == zero:
            return mid_points
        return mid_points * (factor - zero) / (mid_factor - zero)
    if factor >= ten_factor:
        return ten_points
    if ten_factor == mid_factor:
        return ten_points
    return mid_points + (ten_points - mid_points) * (factor - mid_factor) / (ten_factor - mid_factor)


def weighted_value(pairs):
    """Renormalize over pairs whose value is present."""
    numerator = 0.0
    denominator = 0.0
    for weight, value in pairs:
        if value is None or weight is None:
            continue
        numerator += float(weight) * float(value)
        denominator += float(weight)
    if denominator <= 0:
        return None
    return numerator / denominator


def margin_trend(series, max_years, residual="population"):
    """Residual stdev and slope per year for the last fiscal years.

    `population` divides by n, `sample` by n−1, `dof` by n−2.
    """
    window = list(series or [])[-int(max_years):]
    if len(window) < 3:
        return None
    origin = parse_date(window[0]["end"]).toordinal()
    points = []
    for item in window:
        years = (parse_date(item["end"]).toordinal() - origin) / 365.25
        points.append((years, float(item["val"])))
    count = len(points)
    x_mean = sum(point[0] for point in points) / count
    y_mean = sum(point[1] for point in points) / count
    variance = sum((point[0] - x_mean) ** 2 for point in points)
    if variance <= 0:
        slope = 0.0
    else:
        slope = sum((point[0] - x_mean) * (point[1] - y_mean) for point in points) / variance
    intercept = y_mean - slope * x_mean
    residuals = [point[1] - (intercept + slope * point[0]) for point in points]
    if residual == "sample" and count > 1:
        divisor = count - 1
    elif residual == "dof" and count > 2:
        divisor = count - 2
    else:
        divisor = count
    stdev = math.sqrt(sum(value * value for value in residuals) / divisor)
    return {
        "stdev": stdev,
        "slope": slope,
        "end": window[-1]["end"],
        "years": count,
    }


def resolve_industry(ticker, sic, book, overrides):
    """Business-model configuration only.

    The Damodaran ticker table and the exchange are not a source. A listing
    that says Software does not score a miner, and a missing business entry
    stays empty rather than falling back to the ticker or the SIC mode.
    `sic` and `book` stay in the signature so a caller cannot silently switch
    back to that lookup.
    """
    del sic, book
    chosen = (overrides or {}).get((ticker or "").upper()) or (overrides or {}).get(ticker)
    if isinstance(chosen, str) and chosen:
        return chosen, "business", None
    if isinstance(chosen, dict) and chosen.get("industry"):
        return chosen["industry"], "business", chosen.get("note")
    return None, None, None


def assemble_context(book, overrides, segment_map, pools, ticker, sic, sec, instances, ifrs, own_revenue):
    """Bundle the inputs score_company reads from bundle['moat']."""
    from scoring.filings import customer_concentration, segment_weights

    industry, source, business_note = resolve_industry(ticker, sic, book, overrides)
    mapping = (segment_map or {}).get(ticker) or (segment_map or {}).get((ticker or "").upper()) or {}
    weights = []
    segment_note = None
    if mapping and instances:
        weights, segment_note = segment_weights(instances, mapping)
    concentration = None
    if instances:
        concentration = customer_concentration(instances, bool(ifrs))
    elif sec:
        concentration = {"mode": "na"}
    names = set()
    if industry:
        names.add(industry)
    for item in weights:
        if item.get("industry"):
            names.add(item["industry"])
        if item.get("poolFallback"):
            names.add(item["poolFallback"])
    benchmarks = {}
    for name in names:
        row = (book.benchmarks or {}).get(name) if book is not None else None
        if row:
            benchmarks[name] = row
    if not sec:
        segment_note = _join_notes(segment_note, BRANCH_NOTE)
    pool_payload = pools or {}
    return {
        "industry": industry,
        "industrySource": source,
        "businessNote": business_note,
        "benchmarks": benchmarks,
        "segments": weights,
        "segmentNote": segment_note,
        "concentration": concentration,
        "pools": pool_payload.get("pools") or {},
        "poolYear": pool_payload.get("year"),
        "poolUrl": pool_payload.get("url"),
        "label": book.label if book is not None else "Damodaran",
        "ticker": (ticker or "").upper(),
        "sec": bool(sec),
        "ifrs": bool(ifrs),
        "ownRevenue": None if not own_revenue else own_revenue.get("usd"),
        "ownRevenueEnd": None if not own_revenue else own_revenue.get("end"),
        "revenueNote": None if not own_revenue else own_revenue.get("note"),
    }


def _mark(raw, metric_id, status, reason=None, note=None):
    raw["values"].pop(metric_id, None)
    raw["observed"].pop(metric_id, None)
    raw["points"].pop(metric_id, None)
    raw["reasons"][metric_id] = status
    if reason:
        raw["statusReasons"][metric_id] = reason
    else:
        raw["statusReasons"].pop(metric_id, None)
    if note:
        raw["labels"][metric_id + "_note"] = _join_notes(raw["labels"].get(metric_id + "_note"), note)


def _score(raw, metric_id, value, points, when, note=None, display=None, source=None, label=None):
    raw["reasons"].pop(metric_id, None)
    raw["statusReasons"].pop(metric_id, None)
    raw["values"][metric_id] = value
    raw["points"][metric_id] = points
    raw["observed"][metric_id] = when
    if label:
        raw["labels"][metric_id] = label
    if note:
        raw["labels"][metric_id + "_note"] = _join_notes(raw["labels"].get(metric_id + "_note"), note)
    if display:
        raw["labels"][metric_id + "_display"] = display
    if source:
        raw["sources"][metric_id] = source


def _bench(moat, industry):
    return ((moat or {}).get("benchmarks") or {}).get(industry) or {}


def _mix(moat):
    rows = []
    for item in (moat or {}).get("segments") or []:
        if item.get("industry") and item.get("weight"):
            rows.append((float(item["weight"]), item["industry"]))
    if rows:
        return rows
    industry = (moat or {}).get("industry")
    if industry:
        return [(1.0, industry)]
    return []


def _window_years(bundle):
    start = bundle.get("_history_start")
    end = bundle.get("_history_end") or (bundle.get("snapshot") or {}).get("end")
    if not start or not end:
        return None
    return year_gap(end, start)


def _is_short(bundle, series, years):
    window = _window_years(bundle)
    if window is not None:
        return window < float(years)
    series = series or []
    if len(series) < 2:
        return bool(series) and float(years) > 1
    span = year_gap(series[-1]["end"], series[0]["end"])
    if len(series) >= int(years) and span >= float(years) - 0.6:
        return False
    if span >= float(years) - 0.6:
        return False
    return True


def _own_revenue(bundle, moat):
    if moat.get("ownRevenue"):
        return float(moat["ownRevenue"]), moat.get("ownRevenueEnd"), moat.get("revenueNote")
    snap = bundle.get("snapshot") or {}
    annual = bundle.get("revenue") or []
    if snap.get("revenue") and (snap.get("months") or 0) >= 11:
        return float(snap["revenue"]), snap.get("end"), None
    if annual and annual[-1].get("val"):
        return float(annual[-1]["val"]), annual[-1].get("end"), None
    if snap.get("revenue"):
        return float(snap["revenue"]), snap.get("end"), snap.get("label")
    return None, None, None


def _ttm_gross(bundle):
    snap = bundle.get("snapshot") or {}
    if snap.get("revenue") and snap.get("gross_profit") is not None and snap["revenue"] > 0:
        return snap["gross_profit"] / float(snap["revenue"]), snap.get("end"), snap.get("label")
    series = gross_margin_series(bundle.get("revenue") or [], bundle.get("gross_profit") or [])
    if not series:
        return None, None, None
    return series[-1]["val"], series[-1]["end"], None


def _company_rd_ratio(bundle):
    snap = bundle.get("snapshot") or {}
    if snap.get("rd") is not None and snap.get("revenue"):
        return snap["rd"] / float(snap["revenue"]), snap.get("end")
    end = None
    revenue = bundle.get("revenue") or []
    research = bundle.get("rd") or []
    if revenue:
        end = revenue[-1]["end"]
    ratio_revenue = value_at(revenue, end) if end else None
    ratio_rd = value_at(research, end) if end else None
    if ratio_rd is None and research:
        ratio_rd = research[-1]["val"]
        end = research[-1]["end"]
        ratio_revenue = value_at(revenue, end)
    if ratio_rd is None or not ratio_revenue:
        return None, end
    return ratio_rd / float(ratio_revenue), end


def _last_result(bundle):
    """Last fiscal operating income and pretax, else the snapshot."""
    operating = bundle.get("operating_income") or []
    snap = bundle.get("snapshot") or {}
    if operating:
        end = operating[-1]["end"]
        pretax = value_at(bundle.get("pretax") or [], end)
        return operating[-1]["val"], pretax, end
    if snap.get("operating_income") is not None:
        return snap.get("operating_income"), None, snap.get("end")
    return None, None, None


def _source(moat, industry, extra=None):
    label = moat.get("label") or "Damodaran"
    if industry:
        label = label + " · " + industry
    if extra:
        label = extra + "; " + label
    return {"label": label, "url": "https://pages.stern.nyu.edu/~adamodar/pc/datasets/"}


def _usd(amount):
    if amount is None:
        return "n/a"
    value = float(amount)
    sign = "−" if value < 0 else ""
    value = abs(value)
    if value >= 1_000_000_000_000:
        text = format_de(value / 1_000_000_000_000, 1) + " Bio. USD"
    elif value >= 1_000_000_000:
        text = format_de(value / 1_000_000_000, 1) + " Mrd. USD"
    elif value >= 1_000_000:
        text = format_de(value / 1_000_000, 1) + " Mio. USD"
    else:
        text = format_de(value, 0) + " USD"
    return sign + text


def _sec_cite(filed, form):
    if not filed:
        return "SEC, Einreichungsdatum n/a"
    return "SEC " + (form or "Filing") + " vom " + str(filed)


def _damo(moat):
    return (moat or {}).get("label") or "Damodaran, Stand n/a"


def _cite_end(bundle, end):
    snap = bundle.get("snapshot") or {}
    if end and snap.get("end") == end and snap.get("filed"):
        return snap.get("filed"), snap.get("form")
    for key in ("revenue", "operating_income", "gross_profit"):
        for point in bundle.get(key) or []:
            if point.get("end") == end and point.get("filed"):
                return point.get("filed"), point.get("form")
    if snap.get("filed"):
        return snap.get("filed"), snap.get("form")
    return None, None


def _anchor(bundle, moat):
    """Company, its own revenue, and the filing or FX source."""
    ticker = moat.get("ticker") or "n/a"
    revenue, end, revenue_note = _own_revenue(bundle, moat)
    when = end or "n/a"
    filed, form = _cite_end(bundle, end)
    if revenue_note:
        text = f"{ticker}: Umsatz {_usd(revenue)} bis {when}. {revenue_note}"
        if filed and "SEC " not in text and "yfinance" not in text:
            text += " (" + _sec_cite(filed, form) + ")"
    else:
        text = f"{ticker}: Umsatz {_usd(revenue)} bis {when} ({_sec_cite(filed, form)})"
    label = (bundle.get("snapshot") or {}).get("label")
    if label and str(label) not in text:
        text += ". " + str(label)
    business = moat.get("businessNote")
    if business and str(business) not in text:
        text += ". " + str(business)
    return text


def _member_label(member):
    if not member:
        return None
    text = str(member)
    for suffix in ("SegmentMember", "Member"):
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    chars = []
    for index, char in enumerate(text):
        previous = text[index - 1] if index else ""
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if index and char.isupper() and (previous.islower() or (previous.isupper() and nxt.islower())):
            chars.append(" ")
        chars.append(char)
    return "".join(chars)


def _mix_rows(moat, mix):
    rows = [
        item for item in (moat.get("segments") or [])
        if item.get("industry") and item.get("weight")
    ]
    if rows:
        return rows
    return [{"industry": name, "weight": weight} for weight, name in mix]


def _mix_sentence(moat, mix, kind, low, high):
    bits = []
    rows = _mix_rows(moat, mix)
    for item in rows:
        name = item.get("industry")
        weight = item.get("weight")
        bench = _bench(moat, name)
        if kind == "growth":
            series = bench.get("revenueGrowth") or []
            rate = median_capped(series, low, high)
            what = "Umsatzwachstum"
            present = sorted(
                float(value) for value in series
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            )
            uncapped = None
            if present:
                count = len(present)
                if count % 2:
                    uncapped = present[count // 2]
                else:
                    uncapped = (present[count // 2 - 1] + present[count // 2]) / 2.0
            cap = ""
            if rate is not None and uncapped is not None and abs(float(rate) - float(uncapped)) > 1e-9:
                cap = " (Deckel " + format_value("percent", high) + ", Median " + format_value("percent", uncapped) + ")"
            rate_text = (format_value("percent", rate) if rate is not None else "n/a") + cap
        else:
            rate = bench.get("ebitGrowth")
            what = "EBIT-Wachstum"
            rate_text = format_value("percent", rate) if rate is not None else "n/a"
        share = format_value("percent", weight) if weight is not None else "n/a"
        member = _member_label(item.get("member"))
        label = name if not member else member + " (" + (name or "n/a") + ")"
        revenue = item.get("revenue")
        revenue_text = "" if revenue is None else ", Segmentumsatz " + _usd(revenue)
        bits.append(f"{share} {label}{revenue_text}, {what} {rate_text}")
    return _join_notes(moat.get("segmentNote"), "; ".join(bits) if bits else None, _damo(moat))


def apply_moat(raw, bundle, config, profile=None):
    """Fill the eight C_01 and C_03 metrics. Existing raw keys stay in place."""
    rules = _rules(config)
    moat = dict(bundle.get("moat") or {})
    if "sec" not in moat:
        moat["sec"] = (profile or {}).get("sec", True)
    sec = bool(moat.get("sec"))
    short = _status_text(config, "shortHistory", "zu kurze Historie")
    na_label = _status_text(config, "notApplicable", "nicht anwendbar")
    disclosed = _status_text(config, "notDisclosed", "nicht ausgewiesen")
    no_us = rules.get("noUsFilingCriterion") or NO_US
    financial_reason = rules.get("financialReason") or "Finanzwert"
    if not sec:
        note = _join_notes(_anchor(bundle, moat), no_us)
        for metric_id in C01_IDS:
            _mark(raw, metric_id, no_us, note=note)
    else:
        _apply_c01(raw, bundle, moat, rules, config, profile, short, na_label, financial_reason)
    _apply_c03(raw, bundle, moat, rules, config, profile, short, na_label, disclosed, sec)


def _apply_c01(raw, bundle, moat, rules, config, profile, short, na_label, financial_reason):
    industry = moat.get("industry")
    bench = _bench(moat, industry)
    financials = set(rules.get("financialIndustries") or [])
    biotech_names = set(rules.get("biotechIndustries") or [])
    is_financial = industry in financials or (profile or {}).get("sector") == "insurer"
    is_biotech = industry in biotech_names
    revenue, _revenue_end, revenue_note = _own_revenue(bundle, moat)
    low_revenue = revenue is not None and revenue < float(rules.get("biotechRevenueUsd", 50_000_000))
    gross, gross_end, gross_label = _ttm_gross(bundle)
    source = _source(moat, industry, "SEC EDGAR companyfacts")

    if is_financial:
        blocked = _join_notes(
            _anchor(bundle, moat),
            f"Branche {industry or 'n/a'}, Bruttomarge nicht anwendbar ({_damo(moat)})",
        )
        _mark(raw, "gm_vs_industry", na_label, financial_reason, blocked)
        _mark(raw, "gm_stability", na_label, financial_reason, blocked)
    elif is_biotech or low_revenue:
        reason_note = _join_notes(
            _anchor(bundle, moat),
            f"Branche {industry}" if is_biotech else None,
            "Umsatz unter 50 Mio. USD" if low_revenue else None,
            _damo(moat) if is_biotech else None,
        )
        _mark(raw, "gm_vs_industry", NA, note=reason_note)
        _mark(raw, "gm_stability", NA, note=reason_note)
    else:
        industry_margin = bench.get("grossMargin") if industry else None
        if gross is None or industry_margin is None:
            _mark(raw, "gm_vs_industry", NA, note=_join_notes(_anchor(bundle, moat), "Bruttomarge n/a"))
        else:
            spread = gross - float(industry_margin)
            m1 = rules.get("m1") or {}
            points = linear_points(spread, m1.get("zeroAt", -0.20), m1.get("tenAt", 0.20))
            if gross >= float(rules.get("grossMarginFloor", 0.70)):
                points = max(float(points), float(rules.get("grossMarginFloorPoints", 8)))
            default = ""
            if moat.get("industrySource") == "business" and "Firmen-Default" in str(moat.get("businessNote") or ""):
                default = ", Firmen-Default statt SIC"
            note = _join_notes(
                _anchor(bundle, moat),
                "Bruttomarge " + format_value("percent", gross)
                + " gegen " + format_value("percent", industry_margin)
                + " " + (industry or "n/a")
                + default
                + " (" + _damo(moat) + "), Abstand " + format_value("ratioPp", spread),
            )
            _score(raw, "gm_vs_industry", spread, points, gross_end, note=note, source=source)
        _apply_m2(raw, bundle, moat, rules, short)

    _apply_m3(raw, bundle, moat, rules, bench, source, short)
    _apply_m4(raw, bundle, moat, rules, source)


def _apply_m2(raw, bundle, moat, rules, short):
    spec = rules.get("m2") or {}
    minimum = int(spec.get("minYears", 3))
    maximum = int(spec.get("maxYears", 5))
    series = gross_margin_series(bundle.get("revenue") or [], bundle.get("gross_profit") or [])
    fitted = margin_trend(series, maximum, spec.get("residualStdev") or rules.get("residualStdev") or "population")
    if fitted is None or fitted["years"] < minimum:
        if _margin_history_short(bundle, series, minimum):
            _mark(raw, "gm_stability", short, note=_join_notes(
                _anchor(bundle, moat),
                "weniger als " + str(minimum) + " Geschäftsjahre mit Bruttomarge",
            ))
        else:
            _mark(raw, "gm_stability", NA, note=_join_notes(_anchor(bundle, moat), "Bruttomargen-Historie n/a"))
        return
    stability = linear_points(
        fitted["stdev"], spec.get("stabilityZero", 0.08), spec.get("stabilityTen", 0.01),
    )
    trend = linear_points(
        fitted["slope"], spec.get("trendZero", -0.03), spec.get("trendTen", 0.03),
    )
    weight_stability = float(spec.get("stabilityWeight", 0.6))
    weight_trend = float(spec.get("trendWeight", 0.4))
    points = weight_stability * stability + weight_trend * trend
    filed, form = _cite_end(bundle, fitted["end"])
    note = _join_notes(
        _anchor(bundle, moat),
        "Streuung " + format_value("ratioPp", fitted["stdev"])
        + ", Trend " + format_value("ratioPp", fitted["slope"]) + " pro Jahr"
        + " (" + str(fitted["years"]) + " Geschäftsjahre bis " + str(fitted["end"]) + ", "
        + _sec_cite(filed, form) + ")",
    )
    display = (
        "Streuung " + format_value("ratioPp", fitted["stdev"])
        + " · Trend " + format_value("ratioPp", fitted["slope"]) + "/Jahr"
    )
    _score(
        raw, "gm_stability", fitted["stdev"], points, fitted["end"],
        note=note, display=display,
    )


def _margin_history_short(bundle, series, minimum):
    if len(series or []) >= minimum:
        return False
    if _window_years(bundle) is not None and _window_years(bundle) < float(minimum):
        return True
    annual = bundle.get("revenue") or []
    snap = bundle.get("snapshot") or {}
    if len(annual) < minimum and (annual or snap.get("revenue") or bundle.get("_history_start")):
        if _window_years(bundle) is not None and _window_years(bundle) >= float(minimum):
            return False
        return True
    return False


def _loss_roic(bundle):
    """Two-year ROIC when the latest pretax year is a loss.

    The stored average skips a negative pretax year. M3 still needs that
    year once the gross margin has been scored: NOPAT over invested capital,
    then the same two-year mean.
    """
    years = sorted(point["end"] for point in (bundle.get("operating_income") or []) if point.get("end"))
    if len(years) < 2 or year_gap(years[-2], years[-1]) > 1.4:
        return None, None, None
    got = []
    notes = []
    for year in years[-2:]:
        value, _loss, note = roic_for_year(year, bundle, allow_negative_pretax=True)
        if value is None:
            return None, None, None
        got.append(value)
        if note:
            notes.append(note)
    return (got[0] + got[1]) / 2.0, _join_notes(*notes), years[-1]


def _apply_m3(raw, bundle, moat, rules, bench, source, short):
    spec = rules.get("m3") or {}
    operating, _pretax, end = _last_result(bundle)
    roic = raw["values"].get("roic_2y")
    wacc = bench.get("costOfCapital") if moat.get("industry") else None
    tax_note = raw["labels"].get("roic_2y_note")
    operating_loss = operating is not None and operating <= 0
    # "n/a (Aufbauphase)" only when M1 is n/a. A scored margin, including
    # M1 at or above 5, keeps the normal ROIC spread. A negative spread
    # is 0 through the ramp.
    op_filed, op_form = _cite_end(bundle, end)
    op_fact = "Operatives Ergebnis " + _usd(operating) + " bis " + (end or "n/a") + " (" + _sec_cite(op_filed, op_form) + ")"
    if raw["reasons"].get("gm_vs_industry") == NA and operating_loss:
        _mark(raw, "roic_spread", BUILD_PHASE, note=_join_notes(op_fact, "M1 ist n/a, deshalb Aufbauphase"))
        return
    if roic is None and operating_loss and "gm_vs_industry" not in raw["reasons"]:
        roic, loss_note, loss_end = _loss_roic(bundle)
        tax_note = _join_notes(tax_note, loss_note)
        if loss_end:
            end = loss_end
    if roic is not None and wacc is not None:
        spread = float(roic) - float(wacc)
        points = linear_points(spread, spec.get("zeroAt", -0.05), spec.get("tenAt", 0.15))
        industry = moat.get("industry") or "n/a"
        spread_text = (
            "ROIC " + format_value("percent", roic)
            + " minus Kapitalkosten " + format_value("percent", wacc)
            + " " + industry + " (" + _damo(moat) + "), Abstand " + format_value("ratioPp", spread)
        )
        if tax_note:
            spread_text += ". " + tax_note
        note = _join_notes(op_fact, spread_text)
        _score(raw, "roic_spread", spread, points, raw["observed"].get("roic_2y") or end, note=note, source=source)
        return
    if _is_short(bundle, bundle.get("operating_income") or [], spec.get("minYears", 2)):
        _mark(raw, "roic_spread", short, note=_join_notes(op_fact, "weniger als zwei ROIC-Jahre"))
        return
    _mark(raw, "roic_spread", NA, note=_join_notes(op_fact, "ROIC n/a"))


def _apply_m4(raw, bundle, moat, rules, source):
    concentration = moat.get("concentration") or {}
    mode = concentration.get("mode")
    bands = rules.get("m4a") or []
    points_a = None
    pct = None
    bits = []
    cite = _sec_cite(concentration.get("filed"), concentration.get("form"))
    if mode == "none":
        points_a = 8.0
        bits.append(NO_CUSTOMER_NOTE)
    elif mode == "value" and concentration.get("pct") is not None:
        pct = float(concentration["pct"])
        points_a = concentration_points(pct, bands)
        bits.append("Kunden " + format_value("percent", pct) + " → " + _points_text(points_a))
    liability = (bundle.get("snapshot") or {}).get("contract_liability")
    if liability is None:
        liability = moat.get("contractLiability")
    revenue, revenue_end, revenue_note = _own_revenue(bundle, moat)
    ratio = None
    points_b = None
    if liability is not None and revenue:
        ratio = float(liability) / float(revenue)
        band = rules.get("m4b") or {}
        points_b = liability_points(ratio, band.get("atZero", 3), band.get("tenAt", 0.20))
        bits.append("Vertragsbindung " + format_value("percent", ratio) + " → " + _points_text(points_b))
    present = [value for value in (points_a, points_b) if value is not None]
    if not present:
        _mark(raw, "revenue_quality", NA, note=_join_notes(
            _anchor(bundle, moat),
            "Kundenkonzentration im " + (concentration.get("form") or "Filing") + " vom " + str(concentration.get("filed") or "n/a") + " ist n/a",
        ))
        return
    points = sum(present) / len(present)
    when = concentration.get("end") or (bundle.get("snapshot") or {}).get("contract_liability_end") or revenue_end
    value = pct if pct is not None else (ratio if ratio is not None else 0.0)
    display = " · ".join(bits) if bits else None
    facts = []
    if mode == "none":
        facts.append(
            "Im " + (concentration.get("form") or "10-K") + " vom " + str(concentration.get("filed") or "n/a")
            + " fehlt ConcentrationRiskPercentage1 (" + NO_CUSTOMER_NOTE + ")"
        )
    elif mode == "value" and pct is not None:
        facts.append("Größter Kunde " + format_value("percent", pct) + " (" + cite + ")")
    elif mode == "na":
        facts.append(
            "Kundenkonzentration im " + (concentration.get("form") or "Filing") + " vom "
            + str(concentration.get("filed") or "n/a") + " ist n/a"
        )
    if ratio is not None:
        facts.append("Vertragsbindung " + format_value("percent", ratio) + " des Umsatzes " + _usd(revenue))
    _score(
        raw, "revenue_quality", value, points, when,
        note=_join_notes(_anchor(bundle, moat), *facts),
        display=display,
        source=source,
    )


def _points_text(points):
    if points is None:
        return "n/a"
    rounded = round(float(points) + 0.0, 1)
    if abs(rounded - int(rounded)) < 1e-9:
        return str(int(rounded))
    return str(rounded).replace(".", ",")


def apply_pool_rule(moat):
    """A segment larger than its US pool uses the configured next industry.

    The pool is then not representative. The replacement is the segment's
    ``poolFallback``. The page note names both industries and the two amounts.
    """
    pools = moat.get("pools") or {}
    year = moat.get("poolYear")
    notes = []
    for item in moat.get("segments") or []:
        industry = item.get("industry")
        revenue = item.get("revenue")
        fallback = item.get("poolFallback")
        pool = pools.get(industry) if industry else None
        if revenue is None or pool is None or float(pool) <= 0 or float(revenue) <= float(pool):
            continue
        if not fallback:
            continue
        item["replacedIndustry"] = industry
        item["industry"] = fallback
        notes.append(
            "Segmentumsatz " + _usd(revenue)
            + " ist größer als der US-Pool " + industry + " " + _usd(pool)
            + " (CY" + (str(year) if year is not None else "n/a") + "). "
            + "Der Pool ist nicht repräsentativ, deshalb " + fallback
            + " (" + _damo(moat) + ")."
        )
    if notes:
        moat["poolNote"] = " ".join(notes)
        moat["segmentNote"] = _join_notes(moat.get("segmentNote"), moat["poolNote"])
    return moat


def _apply_c03(raw, bundle, moat, rules, config, profile, short, na_label, disclosed, sec):
    apply_pool_rule(moat)
    mix = _mix(moat)
    low = float(rules.get("growthCapLow", -0.05))
    high = float(rules.get("growthCapHigh", 0.30))
    industry = moat.get("industry")
    source = _source(moat, industry)
    segment_note = moat.get("segmentNote")
    growth_pairs = []
    ebit_pairs = []
    for weight, name in mix:
        bench = _bench(moat, name)
        growth_pairs.append((weight, median_capped(bench.get("revenueGrowth") or [], low, high)))
        ebit_pairs.append((weight, bench.get("ebitGrowth")))
    growth = weighted_value(growth_pairs)
    spec = rules.get("c03m1") or {}
    growth_note = _join_notes(_anchor(bundle, moat), _mix_sentence(moat, mix, "growth", low, high))
    ebit_note = _join_notes(_anchor(bundle, moat), _mix_sentence(moat, mix, "ebit", low, high))
    if growth is None:
        extra = None if growth_note and "Umsatzwachstum n/a" in growth_note else "Umsatzwachstum n/a"
        _mark(raw, "industry_growth", NA, note=_join_notes(growth_note, extra))
    else:
        points = linear_points(growth, spec.get("zeroAt", 0.03), spec.get("tenAt", 0.25))
        _score(
            raw, "industry_growth", growth, points, None,
            note=growth_note, source=source,
        )
    ebit = weighted_value(ebit_pairs)
    ebit_spec = rules.get("c03m2") or {}
    if ebit is None:
        extra = None if ebit_note and "EBIT-Wachstum n/a" in ebit_note else "EBIT-Wachstum n/a"
        _mark(raw, "ebit_pool", NA, note=_join_notes(ebit_note, extra))
    else:
        points = linear_points(ebit, ebit_spec.get("zeroAt", 0.0), ebit_spec.get("tenAt", 0.20))
        _score(raw, "ebit_pool", ebit, points, None, note=ebit_note, source=source)
    _apply_rd(raw, bundle, moat, rules, disclosed, sec, source)
    _apply_headroom(raw, bundle, moat, rules, mix, source, segment_note)


def _apply_rd(raw, bundle, moat, rules, disclosed, sec, source):
    spec = rules.get("c03m3") or {}
    industry = moat.get("industry")
    bench = _bench(moat, industry)
    industry_rd = bench.get("rdSales") if industry else None
    if not sec:
        _mark(raw, "rd_vs_industry", NA, note=_join_notes(
            _anchor(bundle, moat),
            "aktivierte Forschungsquote nicht vergleichbar",
        ))
        return
    if industry_rd is not None and float(industry_rd) < float(rules.get("industryRdMin", 0.02)):
        _mark(raw, "rd_vs_industry", NA, note=_join_notes(
            _anchor(bundle, moat),
            "R&D/Sales " + format_value("percent", industry_rd) + " " + (industry or "n/a")
            + " (" + _damo(moat) + ") liegt unter 2 %",
        ))
        return
    ratio, end = _company_rd_ratio(bundle)
    snap = bundle.get("snapshot") or {}
    provenance = ((snap.get("provenance") or {}).get("rd") or {})
    label = None
    if "Technology and infrastructure" in str(provenance.get("label") or ""):
        label = "Forschungsquote, Technology and infrastructure"
    elif "Technology and development" in str(provenance.get("label") or ""):
        label = "Forschungsquote, Technology and development"
    if ratio is None or industry_rd is None or float(industry_rd) <= 0:
        revenue_known = bool(snap.get("revenue") or bundle.get("revenue"))
        research_missing = not bundle.get("rd") and snap.get("rd") is None
        filed, form = _cite_end(bundle, snap.get("end"))
        missing = _join_notes(
            _anchor(bundle, moat),
            "ResearchAndDevelopmentExpense n/a (" + _sec_cite(filed, form) + ")",
        )
        if sec and revenue_known and research_missing:
            _mark(raw, "rd_vs_industry", disclosed, note=missing)
        else:
            _mark(raw, "rd_vs_industry", NA, note=missing)
        return
    factor = ratio / float(industry_rd)
    points = rd_factor_points(factor, spec)
    note = _join_notes(
        _anchor(bundle, moat),
        "Forschungsquote " + format_value("percent", ratio)
        + " gegen R&D/Sales " + format_value("percent", industry_rd)
        + " " + (industry or "n/a") + " (" + _damo(moat) + "), Faktor " + format_value("multiple", factor),
    )
    rd_source = source
    if provenance.get("label"):
        rd_source = {"label": provenance.get("label"), "url": provenance.get("url") or source.get("url")}
    _score(raw, "rd_vs_industry", ratio, points, end, note=note, source=rd_source, label=label)


def _apply_headroom(raw, bundle, moat, rules, mix, source, segment_note):
    spec = rules.get("c03m4") or {}
    revenue, end, revenue_note = _own_revenue(bundle, moat)
    pools = moat.get("pools") or {}
    if not revenue or revenue <= 0 or not mix:
        _mark(raw, "headroom", NA, note=_join_notes(_anchor(bundle, moat), segment_note))
        return
    pairs = []
    for weight, industry in mix:
        pool = pools.get(industry)
        if pool is None or float(pool) <= 0:
            continue
        pairs.append((weight, math.log10(float(pool) / float(revenue))))
    log_ratio = weighted_value(pairs)
    if log_ratio is None:
        _mark(raw, "headroom", NA, note=_join_notes(_anchor(bundle, moat), segment_note))
        return
    points = linear_points(log_ratio, spec.get("zeroAt", 0.5), spec.get("tenAt", 3.0))
    year = moat.get("poolYear")
    pool_bits = []
    for weight, name in mix:
        pool_bits.append(
            (format_value("percent", weight) or "n/a") + " " + name + " " + _usd((moat.get("pools") or {}).get(name))
        )
    pool_note = "US-Pool CY" + (str(year) if year is not None else "n/a") + ": " + "; ".join(pool_bits)
    ticker_note = (rules.get("headroomNotes") or {}).get(moat.get("ticker"))
    if moat.get("ticker") == "MU":
        low = float(rules.get("growthCapLow", -0.05))
        high = float(rules.get("growthCapHigh", 0.30))
        semi = None
        for weight, name in mix:
            if name == "Semiconductor":
                semi = median_capped(_bench(moat, name).get("revenueGrowth") or [], low, high)
        growth_text = format_value("percent", semi) if semi is not None else "n/a"
        ticker_note = "Halbleiterwachstum " + growth_text + " (" + _damo(moat) + "); " + (ticker_note or "")
    note = _join_notes(
        _anchor(bundle, moat),
        moat.get("poolNote"),
        "log10 " + format_value("multiple", log_ratio),
        pool_note,
        ticker_note,
    )
    pool_source = dict(source)
    if moat.get("poolUrl"):
        pool_source["url"] = moat["poolUrl"]
    _score(raw, "headroom", log_ratio, points, end, note=note, source=pool_source)
