"""SEC EDGAR companyfacts, submissions and a small Form 4 sample."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

from scoring.metrics import GAAP_RD_NAMES, has_recent_annual

USER_AGENT = "sidequest/1.0 (aktiencheck; maximilianhillert@mail.grokbot.com)"
TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
INSIDER_WINDOW_DAYS = 183
MAX_FORM4 = 250


class EdgarClient:
    def __init__(self, cache_dir, per_second=8):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.gap = 1.0 / per_second
        self._last = 0.0
        self._cik = None

    def cik_for(self, ticker):
        if self._cik is None:
            payload = self._get_json(TICKER_URL, self.cache_dir / "company_tickers.json")
            mapping = {}
            for row in payload.values():
                mapping[str(row.get("ticker", "")).upper()] = int(row["cik_str"])
            self._cik = mapping
        return self._cik.get(ticker.upper())

    def companyfacts(self, cik):
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
        return self._get_json(url, self.cache_dir / f"facts-{cik:010d}.json"), url

    def submissions(self, cik):
        url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
        return self._get_json(url, self.cache_dir / f"sub-{cik:010d}.json"), url

    def restatement_within(self, submissions, months, today):
        recent = (submissions.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        items = recent.get("items") or []
        dates = recent.get("filingDate") or []
        start = today - timedelta(days=int(months * 30.5))
        for index, form in enumerate(forms):
            if form != "8-K" or index >= len(items) or index >= len(dates):
                continue
            filed = dates[index]
            if filed < start.isoformat():
                continue
            if "4.02" in (items[index] or ""):
                return True
        return False

    def insider_flows(self, cik, submissions, today, window_days=INSIDER_WINDOW_DAYS):
        """Open-market buys and sells (codes P and S) over the last six months.

        Grants, exercises and gifts stay out. Sales on a filing whose
        `aff10b5One` checkbox is set are returned as `planSell` so the scorer
        can weight them. Filings without that element keep the full sale.
        """
        recent = (submissions.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        documents = recent.get("primaryDocument") or []
        start = (today - timedelta(days=window_days)).isoformat()
        chosen = []
        truncated = False
        for index, form in enumerate(forms):
            if form != "4" or index >= len(dates):
                continue
            if dates[index] < start:
                continue
            if len(chosen) >= MAX_FORM4:
                truncated = True
                break
            chosen.append(index)
        buy = 0.0
        sell = 0.0
        plan_sell = 0.0
        buys = 0
        sells = 0
        plan_sells = 0
        buyers = []
        parsed_any = False
        latest = None
        for index in chosen:
            accession = accessions[index].replace("-", "")
            document = documents[index].split("/")[-1]
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{document}"
            try:
                xml_bytes = self._get_bytes(url, self.cache_dir / f"form4-{accession}.xml")
            except (urllib.error.URLError, TimeoutError, OSError):
                continue
            parsed = parse_form4(xml_bytes)
            if parsed is None:
                continue
            parsed_any = True
            buy += parsed["buy"]
            sell += parsed["sell"]
            plan_sell += parsed.get("planSell") or 0.0
            buys += parsed["buys"]
            sells += parsed["sells"]
            plan_sells += parsed.get("planSells") or 0
            for owner in parsed.get("buyers") or []:
                if owner not in buyers:
                    buyers.append(owner)
            if parsed.get("observed") and (latest is None or parsed["observed"] > latest):
                latest = parsed["observed"]
        if chosen and not parsed_any:
            return None
        return {
            "buy": buy,
            "sell": sell,
            "planSell": plan_sell,
            "buys": buys,
            "sells": sells,
            "planSells": plan_sells,
            "buyers": buyers,
            "trades": buys + sells + plan_sells,
            "observed": latest,
            "filings": len(chosen),
            "windowDays": window_days,
            "truncated": truncated,
            "measured": True,
        }

    def merge_rd_extension(self, facts, cik, submissions):
        """Fill TechnologyAndInfrastructureExpense when GAAP research expense is absent.

        companyfacts drops custom namespaces. The line is read from the latest
        10-Q and 10-K instance documents and cached. A failed filing is skipped.
        """
        if not isinstance(facts, dict):
            return facts
        # A current annual GAAP tag is the research line. An older tag, or only
        # quarterly leftovers, does not block the extension concepts.
        if has_recent_annual(facts, GAAP_RD_NAMES, months=18):
            return facts
        payload = self._extension_payload(cik, submissions, RD_EXTENSION_NAMES)
        gaap = facts.setdefault("us-gaap", {})
        for name, concept in (payload.get("concepts") or {}).items():
            units = ((concept or {}).get("units") or {}).get("USD") or []
            if not units or _usd_concept(facts, name):
                continue
            gaap[name] = {"units": {"USD": units}}
        return facts

    def _extension_payload(self, cik, submissions, names):
        filings = _statement_filings(submissions)
        latest = filings[0]["filed"] if filings else ""
        path = self.cache_dir / f"ext-{int(cik):010d}.json"
        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            if (cached.get("filedThrough") or "") >= latest and latest:
                return cached
        grouped = {name: {} for name in names}
        for filing in filings:
            primary = (filing.get("primary") or "").split("/")[-1]
            stem = primary.rsplit(".", 1)[0]
            filename = primary if primary.endswith(".xml") else stem + "_htm.xml"
            accession = (filing.get("accession") or "").replace("-", "")
            if not accession or not filename:
                continue
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{filename}"
            try:
                xml_bytes = self._get_bytes(url, self.cache_dir / f"xbrl-{accession}.xml")
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                continue
            for name, row in parse_extension_instance(xml_bytes, names, filing["form"], filing["filed"]):
                slot = (row["start"], row["end"])
                current = grouped[name].get(slot)
                if current is None or (row.get("filed") or "") >= (current.get("filed") or ""):
                    grouped[name][slot] = row
        concepts = {}
        for name, slots in grouped.items():
            if slots:
                concepts[name] = {"units": {"USD": list(slots.values())}}
        payload = {"filedThrough": latest, "concepts": concepts}
        path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def _get_json(self, url, path):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        raw = self._get_bytes(url, path)
        return json.loads(raw.decode("utf-8"))

    def _get_bytes(self, url, path):
        if path.exists() and path.stat().st_size > 0:
            return path.read_bytes()
        self._wait()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
        if path.name.startswith("form4-") and b"ownershipDocument" not in body:
            return body
        path.write_bytes(body)
        return body

    def _wait(self):
        now = time.monotonic()
        delay = self._last + self.gap - now
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()


def aff10b5_one(root):
    """Rule 10b5-1 checkbox on the Form 4 document.

    The element is `aff10b5One` (SEC ownership XML, filings after the 2023
    amendment). It is document-level, not per transaction. Values seen in
    filings are 1/0 and true/false. None means the element is absent, so the
    sale is not treated as a plan sale.
    """
    found = None
    for node in root.iter():
        if _local(node.tag) != "aff10b5One":
            continue
        found = (node.text or "").strip().lower()
        break
    if found is None:
        return None
    if found in {"1", "true"}:
        return True
    if found in {"0", "false"}:
        return False
    return None


def _owners(root):
    owners = []
    for node in root.iter():
        if _local(node.tag) != "reportingOwner":
            continue
        cik = _nested(node, "reportingOwnerId", "rptOwnerCik")
        name = _nested(node, "reportingOwnerId", "rptOwnerName")
        token = cik or name
        if token and token not in owners:
            owners.append(token)
    return owners


def parse_form4(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    if _local(root.tag) != "ownershipDocument":
        return None
    # TODO: lock-up sales (SPCX) need a lock-up end date. Form 4 has no field for it.
    planned = aff10b5_one(root) is True
    buy = 0.0
    sell = 0.0
    plan_sell = 0.0
    buys = 0
    sells = 0
    plan_sells = 0
    had_purchase = False
    observed = _text(root, "periodOfReport") or None
    for node in root.iter():
        if _local(node.tag) != "nonDerivativeTransaction":
            continue
        code = _nested(node, "transactionCoding", "transactionCode")
        shares = _float(_nested(node, "transactionAmounts", "transactionShares", "value"))
        price = _float(_nested(node, "transactionAmounts", "transactionPricePerShare", "value"))
        when = _nested(node, "transactionDate", "value")
        if code not in {"P", "S"} or shares is None or price is None or price <= 0 or shares <= 0:
            continue
        dollars = shares * price
        if code == "P":
            buy += dollars
            buys += 1
            had_purchase = True
        elif planned:
            plan_sell += dollars
            plan_sells += 1
        else:
            sell += dollars
            sells += 1
        if when and (observed is None or when > observed):
            observed = when
    return {
        "buy": buy,
        "sell": sell,
        "planSell": plan_sell,
        "buys": buys,
        "sells": sells,
        "planSells": plan_sells,
        "buyers": _owners(root) if had_purchase else [],
        "planFlag": aff10b5_one(root),
        "trades": buys + sells + plan_sells,
        "observed": observed,
    }


RD_EXTENSION_NAMES = (
    "TechnologyAndInfrastructureExpense",
    "TechnologyAndDevelopmentExpense",
)


def parse_extension_instance(xml_bytes, names, form, filed):
    """Duration facts for extension concepts. Segment members and text blocks stay out."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    contexts = {}
    for element in root.iter():
        if _local(element.tag) != "context":
            continue
        start = end = None
        dimensional = False
        for child in element.iter():
            name = _local(child.tag)
            if name == "startDate" and child.text:
                start = child.text.strip()[:10]
            elif name == "endDate" and child.text:
                end = child.text.strip()[:10]
            elif name == "explicitMember":
                dimensional = True
        contexts[element.attrib.get("id")] = (start, end, dimensional)
    wanted = set(names)
    rows = []
    for element in root.iter():
        local = _local(element.tag)
        if local not in wanted:
            continue
        if (element.attrib.get("unitRef") or "").lower() not in {"usd", "iso4217:usd"}:
            continue
        value = _float((element.text or "").strip() or None)
        if value is None:
            continue
        start, end, dimensional = contexts.get(element.attrib.get("contextRef"), (None, None, True))
        if dimensional or not start or not end or start >= end:
            continue
        row = {
            "start": start,
            "end": end,
            "val": value,
            "form": form,
            "filed": filed or "",
        }
        try:
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days
        except ValueError:
            continue
        if form.startswith("10-K") and days > 300:
            row["fp"] = "FY"
        rows.append((local, row))
    return rows


def _statement_filings(submissions, limit=8):
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    filed = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    documents = recent.get("primaryDocument") or []
    chosen = []
    for index, form in enumerate(forms):
        if form not in {"10-Q", "10-K", "10-Q/A", "10-K/A"}:
            continue
        if index >= len(accessions) or index >= len(documents):
            continue
        chosen.append({
            "form": "10-K" if str(form).startswith("10-K") else "10-Q",
            "filed": filed[index] if index < len(filed) else "",
            "accession": accessions[index],
            "primary": documents[index],
        })
        if len(chosen) >= limit:
            break
    return chosen


def _usd_concept(facts, name):
    for namespace in ("us-gaap", "ifrs-full"):
        concept = ((facts or {}).get(namespace) or {}).get(name) or {}
        if ((concept.get("units") or {}).get("USD")):
            return True
    return False


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(root, name):
    for node in root.iter():
        if _local(node.tag) == name and node.text:
            return node.text.strip()
    return ""


def _nested(node, *names):
    current = node
    for name in names:
        found = None
        for child in list(current):
            if _local(child.tag) == name:
                found = child
                break
        if found is None:
            return None
        current = found
    return (current.text or "").strip() or None


def _float(value):
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
