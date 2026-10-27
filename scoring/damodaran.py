"""Damodaran industry benchmarks, January file vintage, cached as xls.

The workbooks live under cache/damodaran and are fetched from
pages.stern.nyu.edu. Scoring reads the parsed rows, never a hardcoded ROIC.
"""

from __future__ import annotations

import json
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

USER_AGENT = "sidequest/1.0 (aktiencheck; maximilianhillert@mail.grokbot.com)"
HOST = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/"
FILES = {
    "indname": "indname.xls",
    "margin": "margin.xls",
    "eva": "EVA.xls",
    "histgr": "histgr.xls",
    "fundgr": "fundgrEB.xls",
}
US_EXCHANGES = {
    "NasdaqGS", "NasdaqGM", "NasdaqCM", "Nasdaq",
    "NYSE", "NYSEAM", "NYSEArca", "NYSEARCA", "AMEX", "BATS",
}

GROWTH_COLUMNS = (
    "CAGR in Revenues- Last 5 years",
    "Expected Growth in Revenues - Next 2 years",
    "Expected Growth in Revenues - Next 5 years",
)


def _excel_date(serial):
    try:
        return datetime(1899, 12, 30) + timedelta(days=int(float(serial)))
    except (TypeError, ValueError, OverflowError):
        return None


def _vintage_label(day):
    if day is None:
        return "Damodaran"
    months = (
        "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    )
    return f"Damodaran, Stand {months[day.month - 1]} {day.year}"


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.upper() in {"", "NA", "N/A", "NM"}:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _sheet_rows(path):
    import xlrd

    book = xlrd.open_workbook(path)
    sheet = book.sheet_by_name("Industry Averages")
    header = None
    updated = None
    for row in range(sheet.nrows):
        label = str(sheet.cell_value(row, 0)).strip()
        if label == "Date updated:" and updated is None:
            updated = _excel_date(sheet.cell_value(row, 1))
        if label == "Industry Name":
            header = row
            break
    if header is None:
        return updated, {}
    heads = [str(sheet.cell_value(header, col)).strip() for col in range(sheet.ncols)]
    rows = {}
    for row in range(header + 1, sheet.nrows):
        industry = str(sheet.cell_value(row, 0)).strip()
        if not industry or industry.lower().startswith("total"):
            continue
        record = {}
        for col, name in enumerate(heads):
            if name:
                record[name] = sheet.cell_value(row, col)
        rows[industry] = record
    return updated, rows


def _companies(path):
    import xlrd

    book = xlrd.open_workbook(path)
    sheet = book.sheet_by_name("By company name")
    by_ticker = defaultdict(list)
    by_sic = defaultdict(Counter)
    for row in range(1, sheet.nrows):
        exchange_ticker = str(sheet.cell_value(row, 1)).strip()
        industry = str(sheet.cell_value(row, 2)).strip()
        sic_raw = str(sheet.cell_value(row, 4)).strip()
        if sic_raw.endswith(".0"):
            sic_raw = sic_raw[:-2]
        sic = sic_raw.zfill(4) if sic_raw.isdigit() else sic_raw
        if not industry:
            continue
        if sic:
            by_sic[sic][industry] += 1
        if ":" not in exchange_ticker:
            continue
        exchange, ticker = exchange_ticker.split(":", 1)
        ticker = ticker.strip().upper()
        if not ticker:
            continue
        by_ticker[ticker].append({
            "exchange": exchange.strip(),
            "industry": industry,
            "sic": sic,
            "us": exchange.strip() in US_EXCHANGES,
        })
    sic_mode = {}
    for sic, counter in by_sic.items():
        ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
        sic_mode[sic] = ranked[0][0]
    return by_ticker, sic_mode


class IndustryBook:
    """Parsed Damodaran tables plus ticker and SIC maps."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.updated = None
        self.label = "Damodaran"
        self.benchmarks = {}
        self.by_ticker = {}
        self.sic_mode = {}
        self.urls = {key: HOST + name for key, name in FILES.items()}

    @classmethod
    def load(cls, directory):
        book = cls(directory)
        book.ensure()
        book.parse()
        return book

    def ensure(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        for name in FILES.values():
            path = self.directory / name
            if path.exists() and path.stat().st_size > 0:
                continue
            request = urllib.request.Request(
                HOST + name,
                headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                path.write_bytes(response.read())

    def parse(self):
        updated, margin = _sheet_rows(self.directory / FILES["margin"])
        _, eva = _sheet_rows(self.directory / FILES["eva"])
        _, hist = _sheet_rows(self.directory / FILES["histgr"])
        _, fund = _sheet_rows(self.directory / FILES["fundgr"])
        self.updated = updated
        self.label = _vintage_label(updated)
        industries = set(margin) | set(eva) | set(hist) | set(fund)
        benchmarks = {}
        for industry in industries:
            growth = []
            for column in GROWTH_COLUMNS:
                growth.append(_number((hist.get(industry) or {}).get(column)))
            present = [value for value in growth if value is not None]
            benchmarks[industry] = {
                "grossMargin": _number((margin.get(industry) or {}).get("Gross Margin")),
                "rdSales": _number((margin.get(industry) or {}).get("R&D/Sales")),
                "costOfCapital": _number((eva.get(industry) or {}).get("Cost of Capital")),
                "revenueGrowth": growth,
                "ebitGrowth": _number((fund.get(industry) or {}).get("Expected Growth in EBIT")),
                "growthCount": len(present),
            }
        self.benchmarks = benchmarks
        self.by_ticker, self.sic_mode = _companies(self.directory / FILES["indname"])

    def industry_for_ticker(self, ticker):
        rows = self.by_ticker.get((ticker or "").upper()) or []
        if not rows:
            return None, None
        us_rows = [row for row in rows if row["us"]]
        chosen = us_rows[0] if us_rows else rows[0]
        return chosen["industry"], chosen["exchange"] + ":" + ticker.upper()

    def industry_for_sic(self, sic):
        text = str(sic or "").strip()
        if text.endswith(".0"):
            text = text[:-2]
        if text.isdigit():
            text = text.zfill(4)
        return self.sic_mode.get(text)

    def dump_summary(self, path):
        payload = {
            "label": self.label,
            "updated": None if self.updated is None else self.updated.date().isoformat(),
            "benchmarks": self.benchmarks,
        }
        Path(path).write_text(json.dumps(payload), encoding="utf-8")
