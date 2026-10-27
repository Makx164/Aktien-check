"""Segment mix, customer concentration and the US industry revenue pool.

Segment and customer facts carry an XBRL dimension, so they are read from the
filing instance rather than from companyfacts. The revenue pool is the sum of
the SEC frames for the prior calendar year.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime

from scoring.damodaran import US_EXCHANGES, USER_AGENT
from scoring.metrics import parse_date

REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
)
FRAME_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
)
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
STATEMENT_FORMS = ANNUAL_FORMS | {"10-Q", "10-Q/A"}
ELIMINATION = ("elimination", "intersegment", "reconcil")


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _days(start, end):
    if not start or not end:
        return None
    try:
        return (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return None


def parse_instance(xml_bytes):
    """Return dimensional and plain facts as dicts. A broken file yields []."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    contexts = {}
    for element in root.iter():
        if _local(element.tag) != "context":
            continue
        members = {}
        start = end = instant = None
        for child in element.iter():
            name = _local(child.tag)
            if name == "explicitMember":
                axis = (child.attrib.get("dimension") or "").rsplit(":", 1)[-1]
                member = (child.text or "").strip().rsplit(":", 1)[-1]
                if axis and member:
                    members[axis] = member
            elif name == "startDate" and child.text:
                start = child.text.strip()[:10]
            elif name == "endDate" and child.text:
                end = child.text.strip()[:10]
            elif name == "instant" and child.text:
                instant = child.text.strip()[:10]
        contexts[element.attrib.get("id")] = (start, end, instant, members)
    facts = []
    for element in root.iter():
        name = _local(element.tag)
        if name in {"context", "unit", "xbrl", "schemaRef"}:
            continue
        text = (element.text or "").strip()
        if not text:
            continue
        try:
            value = float(text.replace(",", ""))
        except ValueError:
            continue
        start, end, instant, members = contexts.get(element.attrib.get("contextRef"), (None, None, None, {}))
        facts.append({
            "name": name,
            "value": value,
            "start": start,
            "end": end or instant,
            "instant": instant,
            "members": members,
        })
    return facts


def _statement_filings(submissions, limit=6):
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    filed = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    documents = recent.get("primaryDocument") or []
    chosen = []
    for index, form in enumerate(forms):
        if form not in STATEMENT_FORMS:
            continue
        if index >= len(accessions) or index >= len(documents):
            continue
        chosen.append({
            "form": form,
            "filed": filed[index] if index < len(filed) else "",
            "accession": accessions[index],
            "primary": documents[index],
        })
        if len(chosen) >= limit:
            break
    return chosen


def _instance_url(cik, filing):
    primary = (filing.get("primary") or "").split("/")[-1]
    stem = primary.rsplit(".", 1)[0]
    filename = primary if primary.endswith(".xml") else stem + "_htm.xml"
    accession = (filing.get("accession") or "").replace("-", "")
    if not accession or not filename:
        return None, None
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{filename}"
    return url, f"xbrl-{accession}.xml"


def load_instances(edgar, cik, submissions, as_of=None):
    """Download the latest statement instances. Failures are skipped.

    A filing dated after as_of is left out so a rebuild cannot look ahead.
    """
    loaded = []
    if edgar is None or cik is None:
        return loaded
    for filing in _statement_filings(submissions):
        filed = (filing.get("filed") or "")[:10]
        if as_of and filed and filed > as_of:
            continue
        url, name = _instance_url(cik, filing)
        if not url:
            continue
        try:
            xml_bytes = edgar._get_bytes(url, edgar.cache_dir / name)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
        facts = parse_instance(xml_bytes)
        if not facts:
            continue
        loaded.append({
            "form": filing["form"],
            "filed": filing["filed"],
            "url": url,
            "facts": facts,
        })
    return loaded


def _is_elimination(member):
    text = (member or "").lower()
    return any(token in text for token in ELIMINATION)


def _segment_rows(instances, mapping):
    """Configured segment revenues. Only a single business-segment axis counts."""
    wanted = set(mapping or {})
    rows = []
    for filing in instances:
        for fact in filing["facts"]:
            if fact["name"] not in REVENUE_TAGS:
                continue
            members = fact["members"] or {}
            if list(members) != ["StatementBusinessSegmentsAxis"] and set(members) != {"StatementBusinessSegmentsAxis"}:
                continue
            member = members.get("StatementBusinessSegmentsAxis")
            if not member or member not in wanted or _is_elimination(member):
                continue
            if fact["value"] is None or fact["value"] <= 0 or not fact["start"] or not fact["end"]:
                continue
            rows.append({
                "member": member,
                "value": float(fact["value"]),
                "start": fact["start"],
                "end": fact["end"],
                "days": _days(fact["start"], fact["end"]),
                "filed": filing["filed"],
                "tag": fact["name"],
            })
    return rows


def _pick_tag(rows):
    """Prefer the revenue tag that covers the most configured members."""
    if not rows:
        return None
    coverage = defaultdict(set)
    for row in rows:
        coverage[row["tag"]].add(row["member"])
    return max(coverage, key=lambda tag: (len(coverage[tag]), tag == REVENUE_TAGS[0]))


def segment_weights(instances, mapping):
    """Return (weights, note). weights is a list of {member, industry, weight}.

    Preference: latest fiscal year, else a trailing four quarters, else the
    longest year-to-date that ends on the latest date.
    """
    rows = _segment_rows(instances, mapping)
    tag = _pick_tag(rows)
    rows = [row for row in rows if row["tag"] == tag] if tag else []
    if not rows:
        return [], None
    annual = [row for row in rows if row["days"] and 300 <= row["days"] <= 400]
    if annual:
        end = max(row["end"] for row in annual)
        chosen = [row for row in annual if row["end"] == end]
        note = "Geschäftsjahr bis " + end
        return _weights(chosen, mapping), note
    quarters = [row for row in rows if row["days"] and 70 <= row["days"] <= 120]
    window = _trailing_quarters(quarters)
    if window:
        end = max(row["end"] for row in window)
        return _weights(window, mapping), "TTM bis " + end
    latest_end = max(row["end"] for row in rows)
    ending = [row for row in rows if row["end"] == latest_end]
    longest = max(row["days"] or 0 for row in ending)
    chosen = [row for row in ending if (row["days"] or 0) == longest]
    if longest >= 160:
        note = "kein volles TTM, längster Zeitraum bis " + latest_end
    else:
        note = "Quartal bis " + latest_end
    return _weights(chosen, mapping), note


def _trailing_quarters(quarters):
    if not quarters:
        return None
    by_member = defaultdict(list)
    for row in quarters:
        by_member[row["member"]].append(row)
    ends = sorted({row["end"] for row in quarters})
    if len(ends) < 4:
        return None
    window_ends = ends[-4:]
    span = _days(window_ends[0], window_ends[-1])
    # Four quarter-ends about a year apart, not four copies of one quarter.
    if span is None or span < 240 or span > 380:
        return None
    chosen = []
    for member, rows in by_member.items():
        got = []
        for end in window_ends:
            match = [row for row in rows if row["end"] == end]
            if not match:
                return None
            match.sort(key=lambda row: row["filed"])
            got.append(match[-1])
        chosen.extend(got)
    return chosen


def _weights(rows, mapping):
    totals = defaultdict(float)
    for row in rows:
        totals[row["member"]] += row["value"]
    grand = sum(totals.values())
    if grand <= 0:
        return []
    weights = []
    for member, value in sorted(totals.items()):
        industry = (mapping or {}).get(member)
        if not industry:
            continue
        weights.append({
            "member": member,
            "industry": industry,
            "weight": value / grand,
            "revenue": value,
        })
    return weights


def customer_concentration(instances, ifrs):
    """Largest revenue-based customer share.

    A parsed US-GAAP 10-K without the tag scores as no customer at or above
    10 percent. IFRS and 20-F filings stay n/a. Receivables are ignored.
    """
    parsed_10k = any(str(item["form"]).startswith("10-K") for item in instances)
    parsed_20f = any(str(item["form"]).startswith("20-F") or str(item["form"]).startswith("40-F") for item in instances)
    rows = []
    for filing in instances:
        for fact in filing["facts"]:
            if fact["name"] != "ConcentrationRiskPercentage1":
                continue
            members = fact["members"] or {}
            if members.get("ConcentrationRiskByTypeAxis") != "CustomerConcentrationRiskMember":
                continue
            benchmark = members.get("ConcentrationRiskByBenchmarkAxis") or ""
            if "Receivable" in benchmark:
                continue
            if "Revenue" not in benchmark and "Sales" not in benchmark:
                continue
            value = float(fact["value"])
            if value > 1:
                value = value / 100.0
            rows.append({
                "pct": value,
                "start": fact["start"],
                "end": fact["end"],
                "days": _days(fact["start"], fact["end"]),
                "member": members.get("MajorCustomersAxis") or "",
                "filed": filing["filed"],
            })
    if not rows:
        if ifrs or parsed_20f:
            return {"mode": "na"}
        if parsed_10k:
            return {"mode": "none"}
        return {"mode": "na"}
    annual = [row for row in rows if row["days"] and 300 <= row["days"] <= 400]
    pool = annual or rows
    end = max(row["end"] for row in pool if row["end"])
    ending = [row for row in pool if row["end"] == end]
    quarters = [row for row in ending if row["days"] and 70 <= row["days"] <= 120]
    chosen = quarters or ending
    best = max(chosen, key=lambda row: row["pct"])
    return {
        "mode": "value",
        "pct": best["pct"],
        "end": best["end"],
        "member": best["member"],
        "days": best["days"],
    }


def _frame_url(tag, year):
    return f"https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/USD/CY{year}.json"


def _load_frame(cache_dir, tag, year):
    path = cache_dir / f"frame-{tag}-CY{year}.json"
    if path.exists() and path.stat().st_size > 0:
        return json.loads(path.read_text(encoding="utf-8"))
    request = urllib.request.Request(
        _frame_url(tag, year),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        body = response.read()
    path.write_bytes(body)
    return json.loads(body.decode("utf-8"))


def _annual_value(rows, as_of):
    best = None
    best_key = None
    for row in rows:
        filed = (row.get("filed") or "")[:10]
        if as_of and filed and filed > as_of:
            continue
        try:
            value = float(row.get("val"))
        except (TypeError, ValueError):
            continue
        form = row.get("form") or ""
        annual = form in ANNUAL_FORMS or row.get("fp") == "FY"
        key = (1 if annual else 0, filed, row.get("end") or "")
        if best_key is None or key > best_key:
            best = value
            best_key = key
    return best


def _pools_for_year(book, cache_dir, tickers_to_cik, year, as_of):
    per_cik = defaultdict(dict)
    for tag in FRAME_TAGS:
        try:
            payload = _load_frame(cache_dir, tag, year)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            continue
        grouped = defaultdict(list)
        for row in payload.get("data") or []:
            cik = row.get("cik")
            if cik is None:
                continue
            grouped[int(cik)].append(row)
        for cik, rows in grouped.items():
            value = _annual_value(rows, as_of)
            if value is not None and value > 0:
                per_cik[cik][tag] = value
    higher = {cik: max(values.values()) for cik, values in per_cik.items() if values}
    ticker_cik = {}
    for ticker, cik in (tickers_to_cik or {}).items():
        ticker_cik[str(ticker).upper()] = int(cik)
    pools = defaultdict(float)
    for ticker, rows in (book.by_ticker or {}).items():
        cik = ticker_cik.get(ticker)
        if cik is None or cik not in higher:
            continue
        us_rows = [row for row in rows if row["us"] or row["exchange"] in US_EXCHANGES]
        chosen = (us_rows or rows)[0]
        pools[chosen["industry"]] += higher[cik]
    return {
        "year": year,
        "pools": dict(pools),
        "ciks": len(higher),
        "url": _frame_url(FRAME_TAGS[0], year),
    }


def revenue_pools(book, cache_dir, tickers_to_cik, as_of):
    """Sum of US filer revenue by Damodaran industry, prior calendar year.

    Per CIK the higher of the two revenue tags is kept. Facts filed after
    as_of are left out. If that calendar year has no rows yet, the year
    before is used.
    """
    year = int(as_of[:4]) - 1 if as_of else date.today().year - 1
    as_of_text = as_of or date.today().isoformat()
    current = _pools_for_year(book, cache_dir, tickers_to_cik, year, as_of_text)
    if current["pools"] or year <= 1990:
        return current
    previous = _pools_for_year(book, cache_dir, tickers_to_cik, year - 1, as_of_text)
    return previous if previous["pools"] else current


def fx_rate(cache_dir, as_of):
    """SEK per 1 USD inverted to USD per SEK, from the ECB via Frankfurter.

    The rate is the one published for the statement date, or the previous
    business day when that date is not a fixing day.
    """
    if not as_of:
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"fx-sek-usd-{as_of}.json"
    url = f"https://api.frankfurter.app/{as_of}?from=SEK&to=USD"
    if not path.exists():
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                path.write_bytes(response.read())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    rate = ((payload.get("rates") or {}).get("USD"))
    if rate is None:
        return None
    return {
        "rate": float(rate),
        "date": payload.get("date") or as_of,
        "source": "Europäische Zentralbank, Frankfurter",
        "url": url,
    }


def yahoo_annual_revenue(summary):
    """Latest annual totalRevenue from a Yahoo quoteSummary, currency aside."""
    history = (summary or {}).get("incomeStatementHistory") or {}
    rows = history.get("incomeStatementHistory") or []
    best = None
    for row in rows:
        revenue = ((row.get("totalRevenue") or {}).get("raw"))
        end_raw = (row.get("endDate") or {}).get("raw")
        end_fmt = (row.get("endDate") or {}).get("fmt")
        if revenue is None:
            continue
        if end_raw:
            try:
                end = datetime.utcfromtimestamp(int(end_raw)).date().isoformat()
            except (OverflowError, OSError, ValueError):
                end = None
        else:
            end = str(end_fmt)[:10] if end_fmt else None
        if not end:
            continue
        if best is None or end > best["end"]:
            best = {"revenue": float(revenue), "end": end}
    return best


def parse_date_or_none(value):
    try:
        return parse_date(value)
    except (TypeError, ValueError):
        return None
