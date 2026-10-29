"""Rechenregeln für die sechs Kennzahlen.

Eingaben sind bereits abgerufene Reihen (Datum, Wert). Hier wird nichts
geschätzt: fehlt eine Reihe, bleibt der Wert leer.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
THESIS_PLACEHOLDER = "These folgt"

REVENUE_ROWS = ("Total Revenue", "Operating Revenue")
GROSS_ROWS = ("Gross Profit",)
BASIC_SHARE_ROWS = ("Basic Average Shares",)
DILUTED_SHARE_ROWS = ("Diluted Average Shares",)
OCF_ROWS = ("Operating Cash Flow",)
CAPEX_ROWS = ("Capital Expenditure", "Purchase Of PPE")
FCF_ROWS = ("Free Cash Flow",)
CASH_ROWS = ("Cash Cash Equivalents And Short Term Investments",)
CASH_ONLY_ROWS = ("Cash And Cash Equivalents",)
STI_ROWS = ("Other Short Term Investments",)
DEBT_ROWS = ("Total Debt",)

METRIC_ORDER = (
    "revenueGrowthYoy",
    "grossMarginTtm",
    "freeCashFlowTtm",
    "cashVsDebt",
    "dilutionYoy",
    "nextEarnings",
)


def finite(value):
    if value is None:
        return None
    try:
        if value != value:  # NaN
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number in (float("inf"), float("-inf")):
        return None
    return number


def clean_number(value):
    number = finite(value)
    if number is None:
        return None
    if abs(number - round(number)) < 1e-6:
        return int(round(number))
    return round(number, 8)


def de_grouped(value, digits):
    number = float(value)
    sign = "−" if number < 0 else ""
    text = f"{abs(number):.{digits}f}"
    whole, _, frac = text.partition(".")
    groups = []
    while whole:
        groups.append(whole[-3:])
        whole = whole[:-3]
    whole = ".".join(reversed(groups))
    if digits == 0:
        return sign + whole
    return sign + whole + "," + frac


def format_money(amount, currency):
    number = finite(amount)
    if number is None:
        return None
    code = currency or ""
    sign = "−" if number < 0 else ""
    absolute = abs(number)
    if absolute >= 1e8:
        scaled = absolute / 1e9
        digits = 2 if scaled >= 10 else 3
        body = de_grouped(scaled, digits) + " Mrd."
    elif absolute >= 1e6:
        scaled = absolute / 1e6
        digits = 1 if scaled >= 10 else 2
        body = de_grouped(scaled, digits) + " Mio."
    else:
        body = de_grouped(absolute, 0)
    return (sign + body + (" " + code if code else "")).strip()


def format_percent(ratio, signed=False):
    number = finite(ratio)
    if number is None:
        return None
    percent = number * 100
    text = de_grouped(abs(percent), 1) + " %"
    if percent < 0:
        return "−" + text
    if signed and percent > 0:
        return "+" + text
    return text


def format_shares(amount):
    return format_money(amount, "").strip()


def quarter_label(day, fiscal_month=12):
    """Qn nur bei Kalender-Geschäftsjahr. Sonst das Periodenende, ohne geratenes Quartal."""
    if fiscal_month == 12 and day.month in (3, 6, 9, 12) and day.day >= 28:
        return f"Q{day.month // 3} {day.year}"
    return "Quartal bis " + day.strftime("%d.%m.%Y")


def next_quarter_label(label):
    if not label or not label.startswith("Q") or " " not in label:
        return None
    quarter_text, year_text = label.split(" ", 1)
    try:
        quarter = int(quarter_text[1:])
        year = int(year_text)
    except ValueError:
        return None
    if quarter < 1 or quarter > 4:
        return None
    if quarter == 4:
        return f"Q1 {year + 1}"
    return f"Q{quarter + 1} {year}"


def latest_point(points):
    for day, value in sorted(points, key=lambda item: item[0], reverse=True):
        number = finite(value)
        if number is not None:
            return day, number
    return None


def year_ago(points, end, window=20):
    best = None
    best_distance = window + 1
    for day, value in points:
        number = finite(value)
        if number is None:
            continue
        distance = abs((end - day).days - 365)
        if distance <= window and distance < best_distance:
            best = (day, number)
            best_distance = distance
    return best


def ttm_points(points):
    """Die vier jüngsten Quartale ohne Loch, über etwa ein Jahr."""
    ordered = sorted(points, key=lambda item: item[0], reverse=True)
    chosen = []
    started = False
    for day, value in ordered:
        number = finite(value)
        if number is None:
            if started:
                break
            continue
        started = True
        chosen.append((day, number))
        if len(chosen) == 4:
            break
    if len(chosen) < 4:
        return None
    span = (chosen[0][0] - chosen[-1][0]).days
    if span < 240 or span > 400:
        return None
    return chosen


def aligned_ttm(left, right):
    right_by_day = {day: finite(value) for day, value in right}
    ordered = sorted(left, key=lambda item: item[0], reverse=True)
    chosen = []
    started = False
    for day, value in ordered:
        left_value = finite(value)
        right_value = right_by_day.get(day)
        if left_value is None or right_value is None:
            if started:
                break
            continue
        started = True
        chosen.append((day, left_value, right_value))
        if len(chosen) == 4:
            break
    if len(chosen) < 4:
        return None
    span = (chosen[0][0] - chosen[-1][0]).days
    if span < 240 or span > 400:
        return None
    return chosen


def fcf_quarter(operating, capex):
    operating = finite(operating)
    capex = finite(capex)
    if operating is None or capex is None:
        return None
    if capex <= 0:
        return operating + capex
    return operating - capex


def yoy(current, prior):
    current = finite(current)
    prior = finite(prior)
    if current is None or prior is None or prior <= 0:
        return None
    return current / prior - 1


def _blank(metric_id, label, fetched_at, source, source_url, note):
    return {
        "id": metric_id,
        "label": label,
        "value": None,
        "display": None,
        "detail": None,
        "period": None,
        "source": source,
        "sourceUrl": source_url,
        "observedAt": None,
        "fetchedAt": fetched_at,
        "note": note,
    }


def _filled(**fields):
    return fields


def revenue_growth(revenue, currency, fetched_at, source_url, source=None, fiscal_month=12):
    label = "Umsatzwachstum y/y"
    source = source or "Yahoo Finance quarterly_income_stmt"
    current = latest_point(revenue or [])
    prior = year_ago(revenue or [], current[0]) if current else None
    if current is None or prior is None:
        return _blank(METRIC_ORDER[0], label, fetched_at, source, source_url, "keine Quartalsreihe")
    ratio = yoy(current[1], prior[1])
    if ratio is None:
        return _blank(
            METRIC_ORDER[0],
            label,
            fetched_at,
            source,
            source_url,
            "Vorjahresumsatz nicht positiv",
        )
    current_text = format_money(current[1], currency)
    prior_text = format_money(prior[1], currency)
    return _filled(
        id=METRIC_ORDER[0],
        label=label,
        value=clean_number(ratio),
        display=format_percent(ratio, signed=True),
        detail=f"{current_text} vs. {prior_text}",
        period=f"{quarter_label(current[0], fiscal_month)} vs. {quarter_label(prior[0], fiscal_month)}",
        source=source,
        sourceUrl=source_url,
        observedAt=current[0].isoformat(),
        fetchedAt=fetched_at,
        note=None,
        inputs={"current": clean_number(current[1]), "prior": clean_number(prior[1]), "priorEnd": prior[0].isoformat()},
    )


def gross_margin(revenue, gross, info_margin, currency, fetched_at, source_url, fiscal_month=12):
    label = "Bruttomarge"
    source = "Yahoo Finance quarterly_income_stmt"
    window = aligned_ttm(revenue or [], gross or [])
    if window and any(item[1] <= 0 for item in window):
        window = None
    if window:
        revenue_sum = sum(item[1] for item in window)
        gross_sum = sum(item[2] for item in window)
        if revenue_sum <= 0:
            return _blank(METRIC_ORDER[1], label, fetched_at, source, source_url, "kein Umsatz für die TTM-Marge")
        ratio = gross_sum / revenue_sum
        period = "TTM (4 Quartale bis " + window[0][0].strftime("%d.%m.%Y") + ")"
        observed = window[0][0]
        note = None
    else:
        current_revenue = latest_point(revenue or [])
        gross_by_day = {day: finite(value) for day, value in (gross or [])}
        gross_value = gross_by_day.get(current_revenue[0]) if current_revenue else None
        if current_revenue is not None and gross_value is not None and current_revenue[1] > 0:
            ratio = gross_value / current_revenue[1]
            period = quarter_label(current_revenue[0], fiscal_month) + " (TTM unvollständig)"
            observed = current_revenue[0]
            note = "letztes Quartal, keine vier Quartale"
        else:
            info_value = finite(info_margin)
            if info_value is None:
                return _blank(METRIC_ORDER[1], label, fetched_at, source, source_url, "kein Bruttogewinn in der Quartalsreihe")
            return _filled(
                id=METRIC_ORDER[1],
                label=label,
                value=clean_number(info_value),
                display=format_percent(info_value),
                detail=None,
                period="TTM",
                source="Yahoo Finance grossMargins",
                sourceUrl=source_url,
                observedAt=None,
                fetchedAt=fetched_at,
                note="kein Gross Profit in den Quartalen; Yahoo-Feld grossMargins",
            )
    info_value = finite(info_margin)
    if info_value is not None and abs(info_value - ratio) > 0.005:
        extra = "Yahoo grossMargins " + format_percent(info_value) + " weicht ab, gezeigt ist die Quartalsrechnung"
        note = extra if not note else note + ". " + extra
    return _filled(
        id=METRIC_ORDER[1],
        label=label,
        value=clean_number(ratio),
        display=format_percent(ratio),
        detail=None,
        period=period,
        source=source,
        sourceUrl=source_url,
        observedAt=observed.isoformat(),
        fetchedAt=fetched_at,
        note=note,
    )


def free_cash_flow(operating, capex, yahoo_fcf, currency, fetched_at, source_url):
    label = "Free Cashflow"
    source = "Yahoo Finance quarterly_cashflow"
    capex_by_day = {day: value for day, value in (capex or [])}
    paired = []
    for day, value in (operating or []):
        quarter = fcf_quarter(value, capex_by_day.get(day))
        paired.append((day, quarter))
    window = ttm_points(paired)
    used_line = False
    note = "GAAP: Operating Cash Flow − Capital Expenditure"
    if not window:
        window = ttm_points(yahoo_fcf or [])
        used_line = bool(window)
        note = "Yahoo-Zeile Free Cash Flow; Capital Expenditure nicht in jedem Quartal gefüllt"
    if not window:
        return _blank(METRIC_ORDER[2], label, fetched_at, source, source_url, "keine vier Quartale")
    total = sum(value for _, value in window)
    yahoo_window = None if used_line else ttm_points(yahoo_fcf or [])
    if yahoo_window:
        yahoo_total = sum(value for _, value in yahoo_window)
        if abs(yahoo_total - total) > max(1_000_000, abs(total) * 0.01):
            note += ". Yahoo-Zeile Free Cash Flow weicht ab und wird nicht gezeigt"
        else:
            note += ". Yahoo-Zeile Free Cash Flow stimmt überein"
    return _filled(
        id=METRIC_ORDER[2],
        label=label,
        value=clean_number(total),
        display=("+" if total > 0 else "") + format_money(total, currency),
        detail=None,
        period="TTM (4 Quartale bis " + window[0][0].strftime("%d.%m.%Y") + ")",
        source=source,
        sourceUrl=source_url,
        observedAt=window[0][0].isoformat(),
        fetchedAt=fetched_at,
        note=note,
    )


def _latest_named(rows):
    return latest_point(rows or [])


def cash_vs_debt(cash_combined, cash_only, short_term, debt, currency, fetched_at, source_url):
    label = "Cash vs. Schulden"
    source = "Yahoo Finance quarterly_balance_sheet"
    combined = _latest_named(cash_combined)
    components_note = None
    if combined:
        cash_point = combined
    else:
        cash_point = _latest_named(cash_only)
        sti_point = _latest_named(short_term)
        if cash_point and sti_point and cash_point[0] == sti_point[0]:
            cash_point = (cash_point[0], cash_point[1] + sti_point[1])
            components_note = "Summe aus Cash und kurzfristigen Anlagen"
        elif cash_point and (sti_point is None or sti_point[0] != cash_point[0]):
            components_note = "kurzfristige Anlagen nicht separat ausgewiesen"
    debt_point = _latest_named(debt)
    if cash_point is None and debt_point is None:
        return _blank(METRIC_ORDER[3], label, fetched_at, source, source_url, None)
    observed = cash_point[0] if cash_point else debt_point[0]
    if cash_point and debt_point and cash_point[0] != debt_point[0]:
        return _blank(METRIC_ORDER[3], label, fetched_at, source, source_url, "Cash und Schulden mit unterschiedlichem Stichtag")
    cash_value = cash_point[1] if cash_point else None
    debt_value = debt_point[1] if debt_point else None
    bits = []
    if cash_value is None:
        bits.append("Cash —")
    else:
        bits.append("Cash " + format_money(cash_value, ""))
    if debt_value is None:
        bits.append("Schulden —")
    else:
        bits.append("Schulden " + format_money(debt_value, ""))
    display = " · ".join(bit.strip() for bit in bits)
    if currency:
        display = display + " " + currency
    detail = None
    if cash_value is not None and debt_value is not None:
        net = cash_value - debt_value
        detail = ("Netto-Cash " if net >= 0 else "Netto-Schulden ") + format_money(abs(net), currency)
    return _filled(
        id=METRIC_ORDER[3],
        label=label,
        value={"cash": clean_number(cash_value), "debt": clean_number(debt_value)},
        display=display,
        detail=detail,
        period="letzte Bilanz " + observed.strftime("%d.%m.%Y"),
        source=source,
        sourceUrl=source_url,
        observedAt=observed.isoformat(),
        fetchedAt=fetched_at,
        note=components_note,
    )


def _share_pair(series):
    current = latest_point(series or [])
    prior = year_ago(series or [], current[0]) if current else None
    if current is None or prior is None:
        return None
    return current, prior


def dilution(basic, diluted, fetched_at, source_url, fiscal_month=12, anchor=None):
    label = "Verwässerung"
    basic_pair = _share_pair(basic)
    diluted_pair = _share_pair(diluted)
    kind = "Basic Average Shares"
    pair = basic_pair
    if pair is None:
        pair = diluted_pair
        kind = "Diluted Average Shares"
    elif diluted_pair and anchor is not None:
        basic_gap = abs((anchor - basic_pair[0][0]).days)
        diluted_gap = abs((anchor - diluted_pair[0][0]).days)
        if basic_gap > 20 and diluted_gap + 20 < basic_gap:
            pair = diluted_pair
            kind = "Diluted Average Shares"
    source = "Yahoo Finance quarterly_income_stmt"
    if pair is None:
        return _blank(METRIC_ORDER[4], label, fetched_at, source, source_url, "keine Quartalsreihe")
    current, prior = pair
    ratio = yoy(current[1], prior[1])
    if ratio is None:
        return _blank(METRIC_ORDER[4], label, fetched_at, source, source_url, "Vorjahres-Aktienzahl nicht positiv")
    note = None
    if anchor is not None and (anchor - current[0]).days > 20:
        note = "jüngeres Quartal ohne Aktienzahl"
    return _filled(
        id=METRIC_ORDER[4],
        label=label,
        value=clean_number(ratio),
        display=format_percent(ratio, signed=True),
        detail=f"{format_shares(current[1])} vs. {format_shares(prior[1])} {kind}",
        period=f"{quarter_label(current[0], fiscal_month)} vs. {quarter_label(prior[0], fiscal_month)}",
        source=source + " " + kind,
        sourceUrl=source_url,
        observedAt=current[0].isoformat(),
        fetchedAt=fetched_at,
        note=note,
    )


def earnings_display(moment):
    if moment is None:
        return None, None
    if isinstance(moment, datetime):
        local = moment.astimezone(BERLIN) if moment.tzinfo else moment.replace(tzinfo=BERLIN)
        if local.hour == 0 and local.minute == 0:
            return local.strftime("%d.%m.%Y"), local.date().isoformat()
        offset = local.utcoffset() or timedelta(0)
        zone = "MESZ" if offset == timedelta(hours=2) else "MEZ" if offset == timedelta(hours=1) else "Europe/Berlin"
        text = local.strftime("%d.%m.%Y, %H:%M ") + zone
        return text, local.isoformat()
    if isinstance(moment, date):
        return moment.strftime("%d.%m.%Y"), moment.isoformat()
    return None, None


def earnings_period(last_period_label, last_end, moment):
    label = next_quarter_label(last_period_label)
    if not label or last_end is None or moment is None:
        return None
    day = moment.date() if isinstance(moment, datetime) else moment
    delta = (day - last_end).days
    if 20 <= delta <= 200:
        return label
    return None


def next_earnings(moment, fetched_at, source_url, last_period_label, source_name, last_end=None):
    label = "Nächster Zahlentermin"
    source = source_name or "Yahoo Finance earnings_dates"
    text, observed = earnings_display(moment)
    if not text:
        return _blank(METRIC_ORDER[5], label, fetched_at, source, source_url, None)
    return _filled(
        id=METRIC_ORDER[5],
        label=label,
        value=observed,
        display=text,
        detail=None,
        period=earnings_period(last_period_label, last_end, moment),
        source=source,
        sourceUrl=source_url,
        observedAt=observed[:10],
        fetchedAt=fetched_at,
        note=None,
    )


def row_points(frame, names):
    if frame is None:
        return []
    try:
        empty = frame.empty
    except AttributeError:
        empty = False
    if empty:
        return []
    index = list(frame.index)
    row_name = next((name for name in names if name in index), None)
    if row_name is None:
        return []
    row = frame.loc[row_name]
    points = []
    for column, value in row.items():
        day = column.date() if hasattr(column, "date") else column
        if isinstance(day, datetime):
            day = day.date()
        points.append((day, finite(value)))
    return points


def country_short(country):
    if not country:
        return None
    if country in ("United States", "USA", "US"):
        return "USA"
    return country


def headquarters(city, country):
    short = country_short(country)
    if city and short and city.lower() == short.lower():
        return city
    bits = [bit for bit in (city, short) if bit]
    return ", ".join(bits) if bits else None


def quote_url(symbol, path):
    suffix = "/" + path if path else ""
    return f"https://finance.yahoo.com/quote/{symbol}{suffix}"


def quarterly_points_from_rows(rows):
    """Jüngster Filing-Wert je Quartalsende. Ältere, später geänderte Zahlen fallen weg."""
    best = {}
    for row in rows or []:
        end = row.get("end")
        start = row.get("start")
        if not end or not start:
            continue
        try:
            end_day = date.fromisoformat(end)
            span = (end_day - date.fromisoformat(start)).days
        except ValueError:
            continue
        if span < 80 or span > 100:
            continue
        if (row.get("form") or "") not in ("10-Q", "10-K", "10-Q/A", "10-K/A"):
            continue
        value = finite(row.get("val"))
        if value is None:
            continue
        filed = row.get("filed") or ""
        current = best.get(end_day)
        if current is None or filed >= current[0]:
            best[end_day] = (filed, value, row.get("concept"))
    points = [(day, item[1]) for day, item in sorted(best.items())]
    concept = None
    if points:
        concept = best[points[-1][0]][2]
    return points, concept


def close_point(points, day, window=4):
    best = None
    for point_day, value in points or []:
        number = finite(value)
        if number is None:
            continue
        distance = abs((point_day - day).days)
        if distance <= window and (best is None or distance < best[0]):
            best = (distance, point_day, number)
    if best is None:
        return None
    return best[1], best[2]


def prefer_newer_series(primary, alternate):
    """alternate gewinnt nur, wenn sein jüngstes Quartal deutlich neuer ist."""
    primary_latest = latest_point(primary or [])
    alternate_latest = latest_point(alternate or [])
    if alternate_latest and (primary_latest is None or (alternate_latest[0] - primary_latest[0]).days > 10):
        return alternate, "alternate"
    return primary, "primary"
