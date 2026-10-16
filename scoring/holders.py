"""Insider and institutional ownership from Yahoo Finance key statistics."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
from pathlib import Path

from scoring.prices import _yahoo_open


def holders_url(symbol):
    return "https://finance.yahoo.com/quote/" + urllib.parse.quote(symbol) + "/holders"


def fetch_holders(symbol, cache_dir):
    """Return ownership fractions. A failed lookup is an empty dict, not a zero."""
    if not symbol:
        return {}
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / ("holders-" + symbol.replace("/", "_") + ".json")
    if path.exists() and path.stat().st_size > 0:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("url"):
            return cached
    payload = _download(symbol)
    if not payload:
        return {}
    stats = _statistics(payload)
    if stats is None:
        return {}
    result = {
        "insider": _raw(stats, "heldPercentInsiders"),
        "institutional": _raw(stats, "heldPercentInstitutions"),
        "label": "Yahoo Finance, Anteil Insider und Institutionen",
        "url": holders_url(symbol),
    }
    path.write_text(json.dumps(result), encoding="utf-8")
    return result


def _download(symbol):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    url = (
        "https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
        + urllib.parse.quote(symbol)
        + "?modules=defaultKeyStatistics"
    )
    try:
        _yahoo_open(
            opener,
            "https://finance.yahoo.com/quote/" + urllib.parse.quote(symbol),
            "text/html,application/xhtml+xml",
        )
        crumb = _yahoo_open(
            opener,
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            "text/plain,*/*",
        ).decode("utf-8", "replace").strip()
        if not crumb or crumb[:1] in {"<", "{"}:
            return None
        chart_url = url + "&crumb=" + urllib.parse.quote(crumb)
        return json.loads(_yahoo_open(opener, chart_url, "application/json").decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None


def _statistics(payload):
    result = ((payload.get("quoteSummary") or {}).get("result") or [None])[0] or {}
    return result.get("defaultKeyStatistics")


def _raw(stats, name):
    node = stats.get(name)
    if not isinstance(node, dict):
        return None
    value = node.get("raw")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
