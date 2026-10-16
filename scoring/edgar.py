"""SEC EDGAR companyfacts, submissions and a small Form 4 sample."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

USER_AGENT = "sidequest/1.0 (aktiencheck; maximilianhillert@mail.grokbot.com)"
TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
MAX_FORM4 = 12


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

    def insider_flows(self, cik, submissions, today):
        """Open-market buys and sells in dollars. 10b5-1 plan filings are skipped."""
        recent = (submissions.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        documents = recent.get("primaryDocument") or []
        start = (today - timedelta(days=365)).isoformat()
        chosen = []
        for index, form in enumerate(forms):
            if form != "4" or index >= len(dates):
                continue
            if dates[index] < start:
                continue
            chosen.append(index)
            if len(chosen) >= MAX_FORM4:
                break
        buy = 0.0
        sell = 0.0
        trades = 0
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
            buy += parsed["buy"]
            sell += parsed["sell"]
            trades += parsed["trades"]
            if parsed.get("observed") and (latest is None or parsed["observed"] > latest):
                latest = parsed["observed"]
        if trades == 0:
            return None
        return {"buy": buy, "sell": sell, "trades": trades, "observed": latest}

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


def parse_form4(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    if _local(root.tag) != "ownershipDocument":
        return None
    plan = _text(root, "aff10b5One")
    if plan in {"1", "true", "True"}:
        return {"buy": 0.0, "sell": 0.0, "trades": 0, "observed": None}
    buy = 0.0
    sell = 0.0
    trades = 0
    observed = None
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
        else:
            sell += dollars
        trades += 1
        if when and (observed is None or when > observed):
            observed = when
    return {"buy": buy, "sell": sell, "trades": trades, "observed": observed}


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
