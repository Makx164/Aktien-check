"""Optional analyst metrics. Missing keys skip the call instead of failing the run."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "sidequest/1.0 (aktiencheck; maximilianhillert@mail.grokbot.com)"


def load_keys():
    return {
        "finnhub": os.environ.get("FINNHUB_API_KEY", "").strip(),
        "fmp": os.environ.get("FMP_API_KEY", "").strip(),
    }


def fetch_analysts(ticker, price, today, keys=None, yahoo=None, nasdaq_target=None):
    keys = keys if keys is not None else load_keys()
    empty = {
        "buyShare": None,
        "targetUpside": None,
        "epsRevision": None,
        "count": None,
        "observed": None,
        "label": None,
        "url": None,
    }
    try:
        from scoring.marketdata import analyst_from_nasdaq, analyst_from_yahoo
        yahoo_result = analyst_from_yahoo(yahoo, price, today)
        if yahoo_result:
            return yahoo_result
        nasdaq_result = analyst_from_nasdaq(nasdaq_target, price, today, ticker)
        if nasdaq_result:
            return nasdaq_result
    except (OSError, ValueError, TypeError):
        pass
    if keys.get("finnhub"):
        result = _finnhub(ticker, price, today, keys["finnhub"])
        if result:
            return result
    if keys.get("fmp"):
        result = _fmp(ticker, price, today, keys["fmp"])
        if result:
            return result
    return empty


def _get(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _finnhub(ticker, price, today, token):
    base = "https://finnhub.io/api/v1/stock/recommendation"
    public = f"{base}?symbol={urllib.parse.quote(ticker)}"
    try:
        rows = _get(public + "&token=" + urllib.parse.quote(token))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None
    if not isinstance(rows, list) or not rows:
        return None
    rows = sorted(rows, key=lambda row: row.get("period") or "")
    latest = rows[-1]
    strong = _num(latest.get("strongBuy"))
    buy = _num(latest.get("buy"))
    hold = _num(latest.get("hold"))
    sell = _num(latest.get("sell"))
    strong_sell = _num(latest.get("strongSell"))
    parts = [strong, buy, hold, sell, strong_sell]
    if any(part is None for part in parts):
        return None
    total = sum(parts)
    result = {
        "buyShare": None if total <= 0 else (strong + buy) / total,
        "targetUpside": None,
        "epsRevision": None,
        "count": int(total),
        "observed": (latest.get("period") or "")[:10] or today,
        "label": "Finnhub",
        "url": public,
    }
    target_url = "https://finnhub.io/api/v1/stock/price-target?symbol=" + urllib.parse.quote(ticker)
    try:
        target = _get(target_url + "&token=" + urllib.parse.quote(token))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        target = None
    mean = _num((target or {}).get("targetMean"))
    if mean is not None and price and price > 0:
        result["targetUpside"] = mean / price - 1.0
    # A current estimate is not a 90-day revision. Leave epsRevision unset
    # unless the payload itself carries a revision field.
    revision = _revision_field(target)
    if revision is not None:
        result["epsRevision"] = revision
    return result


def _fmp(ticker, price, today, token):
    public = "https://financialmodelingprep.com/stable/grades-consensus?symbol=" + urllib.parse.quote(ticker)
    try:
        payload = _get(public + "&apikey=" + urllib.parse.quote(token))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None
    row = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(row, dict):
        return None
    strong = _num(row.get("strongBuy"))
    buy = _num(row.get("buy"))
    hold = _num(row.get("hold"))
    sell = _num(row.get("sell"))
    strong_sell = _num(row.get("strongSell"))
    parts = [strong, buy, hold, sell, strong_sell]
    if any(part is None for part in parts):
        return None
    total = sum(parts)
    result = {
        "buyShare": None if total <= 0 else (strong + buy) / total,
        "targetUpside": None,
        "epsRevision": None,
        "count": int(total),
        "observed": today,
        "label": "Financial Modeling Prep",
        "url": public,
    }
    target_url = "https://financialmodelingprep.com/stable/price-target-consensus?symbol=" + urllib.parse.quote(ticker)
    try:
        target_payload = _get(target_url + "&apikey=" + urllib.parse.quote(token))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        target_payload = None
    target_row = target_payload[0] if isinstance(target_payload, list) and target_payload else target_payload
    mean = _num((target_row or {}).get("targetConsensus") if isinstance(target_row, dict) else None)
    if mean is None and isinstance(target_row, dict):
        mean = _num(target_row.get("targetMedian"))
    if mean is not None and price and price > 0:
        result["targetUpside"] = mean / price - 1.0
    return result


def _revision_field(payload):
    if not isinstance(payload, dict):
        return None
    for key in ("epsRevision90d", "epsRevision", "revision"):
        value = _num(payload.get(key))
        if value is not None:
            return value
    return None


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
