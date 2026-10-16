"""Daily prices.

Stooq CSV is tried first. Nasdaq historical quotes are the next fallback.
A configured Yahoo Finance chart symbol is used when both are empty.
"""

from __future__ import annotations

import csv
import hashlib
import http.cookiejar
import io
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

USER_AGENT = "sidequest/1.0 (aktiencheck; maximilianhillert@mail.grokbot.com)"
BROWSER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def parse_stooq_csv(text):
    if not text or not text.lstrip().startswith("Date"):
        return []
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        close = _number(row.get("Close"))
        if not row.get("Date") or close is None:
            continue
        high = _number(row.get("High"))
        rows.append({"date": row["Date"][:10], "close": close, "high": high})
    rows.sort(key=lambda item: item["date"])
    return rows


def parse_nasdaq_payload(payload):
    data = payload.get("data") or {}
    table = data.get("tradesTable") or {}
    rows = []
    for row in table.get("rows") or []:
        close = _number(row.get("close"))
        if close is None or not row.get("date"):
            continue
        rows.append({
            "date": _nasdaq_date(row["date"]),
            "close": close,
            "high": _number(row.get("high")),
        })
    rows.sort(key=lambda item: item["date"])
    return rows


def _number(value):
    if value is None:
        return None
    text = str(value).replace("$", "").replace(",", "").strip()
    if text in {"", "N/A", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _nasdaq_date(value):
    month, day, year = value.split("/")
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def parse_yahoo_chart(payload):
    """Daily bars from a Yahoo v8 chart payload. Null closes are skipped."""
    chart = payload.get("chart") or {}
    if chart.get("error"):
        return []
    result = chart.get("result") or []
    if not result:
        return []
    block = result[0] or {}
    timestamps = block.get("timestamp") or []
    quotes = (block.get("indicators") or {}).get("quote") or []
    quote = quotes[0] if quotes else {}
    closes = quote.get("close") or []
    highs = quote.get("high") or []
    rows = {}
    for index, ts in enumerate(timestamps):
        if ts is None or index >= len(closes) or closes[index] is None:
            continue
        try:
            close = float(closes[index])
        except (TypeError, ValueError):
            continue
        high = None
        if index < len(highs) and highs[index] is not None:
            try:
                high = float(highs[index])
            except (TypeError, ValueError):
                high = None
        day = datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat()
        rows[day] = {"date": day, "close": close, "high": high}
    return [rows[key] for key in sorted(rows)]


def yahoo_chart_url(symbol):
    query = urllib.parse.urlencode({"interval": "1d", "range": "5y"})
    return (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(symbol)
        + "?"
        + query
    )


YAHOO_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _yahoo_open(opener, url, accept):
    request = urllib.request.Request(url, headers={
        "User-Agent": YAHOO_AGENT,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
    })
    with opener.open(request, timeout=40) as response:
        return response.read()


def _yahoo_download(symbol):
    """Chart payload. The quote page sets the crumb cookies only for an HTML accept."""
    url = yahoo_chart_url(symbol)
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
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
        )
        crumb = crumb.decode("utf-8", "replace").strip()
        if not crumb or crumb[:1] in {"<", "{"}:
            return None, url
        chart_url = url + "&crumb=" + urllib.parse.quote(crumb)
        payload = json.loads(_yahoo_open(opener, chart_url, "application/json").decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None, url
    if (payload.get("chart") or {}).get("error"):
        return None, url
    return payload, url


def fetch_series(symbol_stooq, nasdaq_symbol, asset_class, stooq_fallbacks=None, yahoo_symbol=None):
    """Return bars plus the source that actually answered."""
    symbols = [symbol_stooq] + list(stooq_fallbacks or [])
    denied = False
    for symbol in symbols:
        if not symbol:
            continue
        try:
            text = _stooq_download(symbol)
        except Exception:
            continue
        if text.strip() == "Access denied":
            denied = True
            continue
        bars = parse_stooq_csv(text)
        if bars:
            return {
                "bars": bars,
                "sourceLabel": "Stooq",
                "sourceUrl": stooq_url(symbol),
                "note": None,
                "provider": "stooq",
            }
    if nasdaq_symbol:
        payload, url = _nasdaq_download(nasdaq_symbol, asset_class or "stocks")
        bars = parse_nasdaq_payload(payload) if payload else []
        if bars:
            note = "Kurse von Nasdaq, Stooq hat den Abruf abgelehnt." if denied else None
            return {
                "bars": bars,
                "sourceLabel": "Nasdaq Historical",
                "sourceUrl": url,
                "note": note,
                "provider": "nasdaq",
            }
    if yahoo_symbol:
        payload, url = _yahoo_download(yahoo_symbol)
        bars = parse_yahoo_chart(payload) if payload else []
        if bars:
            note = f"Kurse von Yahoo Finance Chart-API ({yahoo_symbol})."
            if denied:
                note += " Stooq hat den Abruf abgelehnt."
            return {
                "bars": bars,
                "sourceLabel": f"Yahoo Finance Chart-API ({yahoo_symbol})",
                "sourceUrl": url,
                "note": note,
                "provider": "yahoo",
            }
    note = "Stooq hat den Abruf abgelehnt." if denied else "Keine Kursdaten."
    return {"bars": [], "sourceLabel": None, "sourceUrl": None, "note": note, "provider": None}


def stooq_url(symbol):
    return "https://stooq.com/q/d/l/?s=" + urllib.parse.quote(symbol, safe="") + "&i=d"


def _stooq_download(symbol):
    path = "/q/d/l/?s=" + urllib.parse.quote(symbol, safe="") + "&i=d"
    ips = [item[4][0] for item in socket.getaddrinfo("stooq.com", 443, socket.AF_INET, socket.SOCK_STREAM)]
    seen = []
    for ip in ips:
        if ip not in seen:
            seen.append(ip)
    last = ""
    for ip in seen[:2]:
        try:
            last = _stooq_on_connection(ip, path)
        except Exception as exc:
            last = str(exc)
            continue
        if last.lstrip().startswith("Date") or last.strip() == "Access denied":
            return last
    return last


def _stooq_on_connection(ip, path):
    context = ssl.create_default_context()
    raw = socket.create_connection((ip, 443), timeout=25)
    sock = context.wrap_socket(raw, server_hostname="stooq.com")
    try:
        headers, body = _request(sock, "GET", path, None, None)
        text = body.decode("utf-8", "replace")
        if text.lstrip().startswith("Date"):
            return text
        match = re.search(r'const c="([^"]+)",d=(\d+)', text)
        if not match:
            return text
        token, difficulty = match.group(1), int(match.group(2))
        nonce = solve_pow(token, difficulty)
        form = urllib.parse.urlencode({"c": token, "n": str(nonce)})
        headers, body = _request(
            sock,
            "POST",
            "/__verify",
            form.encode(),
            {"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://stooq.com"},
        )
        cookie = ""
        for line in headers.split("\r\n"):
            if line.lower().startswith("set-cookie:"):
                cookie = line.split(":", 1)[1].strip().split(";", 1)[0]
                break
        extra = {"Cookie": cookie} if cookie else None
        headers, body = _request(sock, "GET", path, None, extra)
        return body.decode("utf-8", "replace")
    finally:
        sock.close()


def solve_pow(token, difficulty):
    target = "0" * difficulty
    nonce = 0
    while True:
        digest = hashlib.sha256((token + str(nonce)).encode()).hexdigest()
        if digest.startswith(target):
            return nonce
        nonce += 1


def _request(sock, method, path, body, extra):
    headers = {
        "Host": "stooq.com",
        "User-Agent": USER_AGENT,
        "Accept": "text/csv,*/*",
        "Connection": "keep-alive",
    }
    if extra:
        headers.update(extra)
    payload = body or b""
    if body is not None:
        headers["Content-Length"] = str(len(payload))
    request = (
        f"{method} {path} HTTP/1.1\r\n"
        + "".join(f"{key}: {value}\r\n" for key, value in headers.items())
        + "\r\n"
    ).encode() + payload
    sock.sendall(request)
    return _read_response(sock)


def _read_response(sock):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    headers = head.decode("iso-8859-1", "replace")
    match = re.search(r"(?i)content-length:\s*(\d+)", headers)
    if match:
        need = int(match.group(1))
        body = rest
        while len(body) < need:
            chunk = sock.recv(65536)
            if not chunk:
                break
            body += chunk
        return headers, body[:need]

    def parse_chunks(buf):
        out = b""
        index = 0
        while True:
            newline = buf.find(b"\r\n", index)
            if newline < 0:
                return None
            try:
                size = int(buf[index:newline].split(b";", 1)[0], 16)
            except ValueError:
                return None
            index = newline + 2
            if size == 0:
                return out
            if len(buf) < index + size + 2:
                return None
            out += buf[index:index + size]
            index += size + 2

    body = rest
    parsed = parse_chunks(body)
    while parsed is None:
        chunk = sock.recv(65536)
        if not chunk:
            break
        body += chunk
        parsed = parse_chunks(body)
    return headers, parsed or b""


def _nasdaq_download(symbol, asset_class):
    today = date.today()
    start = today - timedelta(days=900)
    query = urllib.parse.urlencode({
        "assetclass": asset_class,
        "fromdate": start.isoformat(),
        "todate": today.isoformat(),
        "limit": "2000",
    })
    url = f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(symbol)}/historical?{query}"
    request = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_AGENT,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None, url
    time.sleep(0.25)
    return payload, url
