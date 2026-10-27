import json
import unittest
from datetime import date, timedelta
from pathlib import Path

from scoring.edgar import parse_form4
from scoring.marketdata import apply_fundamental_fallback
from scoring.metrics import _distance_points, annual_series, extract_fundamentals, score_company
from scoring.model import (
    apply_analyst_cap,
    band_points,
    coverage_decision,
    linear_points,
    traffic_light,
    weighted_total,
)
from scoring.prices import parse_nasdaq_payload, parse_stooq_csv, parse_yahoo_chart, solve_pow, yahoo_chart_url
from scripts.build_scores import assemble

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config" / "scoring.json").read_text(encoding="utf-8"))


def point(end, val, form="10-K"):
    return {"end": end, "val": val, "filed": end, "form": form}


def _industry_moat(gross_margin=0.40, wacc=0.08, rd=0.10, growth=0.20, ebit=0.10, industry="Software (System & Application)"):
    bench = {
        "grossMargin": gross_margin,
        "rdSales": rd,
        "costOfCapital": wacc,
        "revenueGrowth": [growth],
        "ebitGrowth": ebit,
    }
    return {
        "sec": True,
        "industry": industry,
        "label": "Damodaran, Stand Januar 2026",
        "benchmarks": {industry: bench},
        "pools": {industry: 1_000_000_000_000},
        "concentration": {"mode": "na"},
    }


class RampTests(unittest.TestCase):
    def test_higher_and_lower_are_linear(self):
        self.assertEqual(band_points(0.03457, [
            {"above": 0.10, "points": 5, "note": "über 10 %"},
            {"atLeast": 0.03, "points": 3, "note": "3–10 %"},
            {"else": True, "points": 1, "note": "unter 3 %"},
        ]), (3, "3–10 %"))
        self.assertEqual(band_points(0.6466, [
            {"above": 0.40, "points": 2},
            {"else": True, "points": 0, "note": "bis 40 %"},
        ]), (2, None))
        self.assertEqual(linear_points(0.5, 0, 1), 5)
        self.assertEqual(linear_points(-1, 0, 1), 0)
        self.assertEqual(linear_points(2, 0, 1), 10)
        self.assertEqual(linear_points(0, 4, 0), 10)
        self.assertEqual(linear_points(4, 4, 0), 0)
        self.assertAlmostEqual(linear_points(2, 4, 0), 5)
        self.assertIsNone(linear_points(None, 0, 1))

    def test_traffic_light_boundaries(self):
        self.assertEqual(CONFIG["trafficLight"]["greenAt"], 7)
        self.assertEqual(CONFIG["trafficLight"]["orangeAt"], 5)
        green = CONFIG["trafficLight"]["greenAt"]
        orange = CONFIG["trafficLight"]["orangeAt"]
        self.assertEqual(traffic_light(7, green, orange), "green")
        self.assertEqual(traffic_light(6.9, green, orange), "orange")
        self.assertEqual(traffic_light(5, green, orange), "orange")
        self.assertEqual(traffic_light(4.9, green, orange), "red")
        self.assertIsNone(traffic_light(None, green, orange))

    def test_unweighted_mean_ignores_weights_and_missing_criteria(self):
        total = weighted_total([(10, 1), (0, 0.5)])
        self.assertAlmostEqual(total, 5.0)
        self.assertAlmostEqual(total, weighted_total([(10, 2), (0, 0.25)]))
        only = weighted_total([(8, 0.5), (None, 1)])
        self.assertAlmostEqual(only, 8)
        self.assertNotAlmostEqual(only, 80)
        self.assertIsNone(weighted_total([(None, 1)]))
        source = (ROOT / "scoring" / "model.py").read_text(encoding="utf-8")
        self.assertNotIn("* 10.0", source)
        self.assertNotIn("(num / den) * 10", source)

    def test_analyst_cap(self):
        capped, applied = apply_analyst_cap(9, 4, 5, 7)
        self.assertEqual(capped, 7)
        self.assertTrue(applied)
        stayed, applied = apply_analyst_cap(9, 5, 5, 7)
        self.assertEqual(stayed, 9)
        self.assertFalse(applied)
        unknown, applied = apply_analyst_cap(9, None, 5, 7)
        self.assertEqual(unknown, 9)
        self.assertFalse(applied)


class SeriesTests(unittest.TestCase):
    def test_newer_concept_wins(self):
        facts = {
            "us-gaap": {
                "Old": {"units": {"USD": [
                    {"end": "2020-12-31", "val": 1, "fp": "FY", "form": "10-K", "filed": "2021-02-01"},
                ]}},
                "New": {"units": {"USD": [
                    {"end": "2025-12-31", "val": 5, "fp": "FY", "form": "10-K", "filed": "2026-02-01"},
                ]}},
            }
        }
        series = annual_series(facts, ["Old", "New"], ["USD"])
        self.assertEqual(series[-1]["end"], "2025-12-31")
        self.assertEqual(series[-1]["val"], 5)

    def test_quarterly_rows_are_ignored_for_annual(self):
        facts = {
            "us-gaap": {
                "Revenue": {"units": {"USD": [
                    {"end": "2025-12-31", "val": 10, "fp": "FY", "form": "10-K", "filed": "2026-02-01"},
                    {"end": "2025-12-31", "val": 99, "fp": "Q2", "form": "10-Q", "filed": "2026-07-01"},
                ]}}
            }
        }
        series = annual_series(facts, ["Revenue"], ["USD"])
        self.assertEqual(series[-1]["val"], 10)


class PriceParseTests(unittest.TestCase):
    def test_stooq_and_nasdaq(self):
        csv_text = "Date,Open,High,Low,Close,Volume\n2026-10-14,1,3,1,2,10\n2026-10-15,2,4,2,3,11\n"
        bars = parse_stooq_csv(csv_text)
        self.assertEqual(bars[-1]["close"], 3)
        self.assertEqual(bars[-1]["high"], 4)
        self.assertEqual(parse_stooq_csv("<html></html>"), [])
        payload = {"data": {"tradesTable": {"rows": [
            {"date": "10/15/2026", "close": "$254.76", "high": "$255.60"},
            {"date": "10/14/2026", "close": "1,234.50", "high": "1,300"},
        ]}}}
        parsed = parse_nasdaq_payload(payload)
        self.assertEqual(parsed[0]["date"], "2026-10-14")
        self.assertEqual(parsed[1]["close"], 254.76)

    def test_yahoo_chart_skips_nulls_and_sorts(self):
        payload = {"chart": {"result": [{
            "timestamp": [1634540400, 1634626800, None],
            "indicators": {"quote": [{
                "close": [None, 11.5, 9],
                "high": [10.2, 12.0, None],
            }]},
        }]}}
        bars = parse_yahoo_chart(payload)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["date"], "2021-10-19")
        self.assertEqual(bars[0]["close"], 11.5)
        self.assertEqual(bars[0]["high"], 12.0)
        self.assertEqual(parse_yahoo_chart({"chart": {"error": {"code": "Not Found"}}}), [])
        self.assertEqual(parse_yahoo_chart({}), [])
        url = yahoo_chart_url("SIVE.ST")
        self.assertIn("/chart/SIVE.ST?", url)
        self.assertNotIn("crumb", url)

    def test_pow_finds_a_prefix(self):
        nonce = solve_pow("sidequest", 1)
        import hashlib
        digest = hashlib.sha256(("sidequest" + str(nonce)).encode()).hexdigest()
        self.assertTrue(digest.startswith("0"))


class Form4Tests(unittest.TestCase):
    def test_open_market_and_plan_filter(self):
        xml = """<?xml version="1.0"?>
        <ownershipDocument>
          <aff10b5One>0</aff10b5One>
          <nonDerivativeTransaction>
            <transactionDate><value>2026-10-01</value></transactionDate>
            <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
            <transactionAmounts>
              <transactionShares><value>10</value></transactionShares>
              <transactionPricePerShare><value>5</value></transactionPricePerShare>
            </transactionAmounts>
          </nonDerivativeTransaction>
          <nonDerivativeTransaction>
            <transactionDate><value>2026-10-02</value></transactionDate>
            <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
            <transactionAmounts>
              <transactionShares><value>4</value></transactionShares>
              <transactionPricePerShare><value>10</value></transactionPricePerShare>
            </transactionAmounts>
          </nonDerivativeTransaction>
          <nonDerivativeTransaction>
            <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
            <transactionAmounts>
              <transactionShares><value>100</value></transactionShares>
              <transactionPricePerShare><value>1</value></transactionPricePerShare>
            </transactionAmounts>
          </nonDerivativeTransaction>
        </ownershipDocument>"""
        parsed = parse_form4(xml.encode())
        self.assertEqual(parsed["buy"], 50)
        self.assertEqual(parsed["sell"], 40)
        self.assertEqual(parsed["planSell"], 0)
        self.assertIs(parsed["planFlag"], False)
        self.assertEqual(parsed["buys"], 1)
        self.assertEqual(parsed["sells"], 1)
        planned = xml.replace("<aff10b5One>0</aff10b5One>", "<aff10b5One>1</aff10b5One>")
        kept = parse_form4(planned.encode())
        self.assertEqual(kept["buy"], 50)
        self.assertEqual(kept["sell"], 0)
        self.assertEqual(kept["planSell"], 40)
        self.assertEqual(kept["planSells"], 1)
        self.assertIs(kept["planFlag"], True)
        spelled = xml.replace("<aff10b5One>0</aff10b5One>", "<aff10b5One>true</aff10b5One>")
        self.assertEqual(parse_form4(spelled.encode())["planSell"], 40)
        absent = xml.replace("<aff10b5One>0</aff10b5One>", "")
        plain = parse_form4(absent.encode())
        self.assertIsNone(plain["planFlag"])
        self.assertEqual(plain["sell"], 40)
        self.assertEqual(plain["planSell"], 0)


def dated_bars(count):
    day = date(2024, 1, 2)
    rows = []
    for _ in range(count):
        rows.append({"date": day.isoformat(), "close": 100.0, "high": 100.0})
        day += timedelta(days=1)
    return rows


class CompanyScoreTests(unittest.TestCase):
    def test_empty_company_is_unknown(self):
        scored = score_company(CONFIG, {}, {}, {}, "2026-10-16", {})
        self.assertEqual(scored["available"], 0)
        self.assertEqual(scored["total"], sum(len(item["metrics"]) for item in CONFIG["criteria"]))
        self.assertEqual(scored["koFlags"], [])
        self.assertTrue(all(metric["points"] is None for item in scored["criteria"] for metric in item["metrics"]))

    def test_momentum_thresholds_and_half_weight_inputs(self):
        bars = dated_bars(300)
        bars[47]["close"] = 100
        bars[278]["close"] = 140
        bars[100]["high"] = 200
        bars[299]["close"] = 110
        bench = dated_bars(300)
        prices = {
            "bars": bars,
            "sourceLabel": "Stooq",
            "sourceUrl": "https://stooq.com/q/d/l/?s=amzn.us&i=d",
            "benchmark": {"bars": bench, "label": "S&P 500"},
        }
        scored = score_company(CONFIG, {}, prices, {}, "2026-10-16", {})
        by_id = {item["id"]: item for item in scored["criteria"]}
        metrics = {metric["id"]: metric for metric in by_id["C_07"]["metrics"]}
        self.assertEqual(metrics["excess_return_12_1"]["points"], 10)
        self.assertEqual(metrics["excess_return_12_1"]["value"], "40,0 Pp")
        self.assertEqual(metrics["excess_return_12_1"]["scale"], "≥ 30 Pp = 10, 0 Pp = 5, ≤ -50 Pp = 0")
        self.assertEqual(metrics["above_sma_200"]["value"], "ja")
        self.assertEqual(metrics["above_sma_200"]["points"], 10)
        self.assertEqual(metrics["distance_52w_high"]["points"], 5.0)
        self.assertEqual(metrics["excess_return_12_1"]["sourceUrl"], prices["sourceUrl"])
        self.assertEqual(metrics["excess_return_12_1"]["fetchedAt"], "2026-10-16")
        self.assertEqual(metrics["excess_return_12_1"]["observedAt"], bars[-1]["date"])
        self.assertEqual(by_id["C_02"]["weight"], 2.0)
        self.assertEqual(by_id["C_07"]["weight"], 0.25)
        self.assertEqual(by_id["C_08"]["weight"], 0.5)
        self.assertIsNone(by_id["C_06"]["score"])
        self.assertEqual(by_id["C_06"]["status"], "wenig Daten")

    def test_peg_unprofitable_switch_cap_and_ko(self):
        ends = ["2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]
        revenue = [100, 120, 150, 200]
        bundle = {
            "revenue": [point(end, val) for end, val in zip(ends, revenue)],
            "gross_profit": [point(end, val * 0.4) for end, val in zip(ends, revenue)],
            "operating_income": [point(end, -5) for end in ends],
            "net_income": [point(end, -5) for end in ends],
            "eps": [point(end, -0.2) for end in ends],
            "fcf": [point(end, -10) for end in ends],
            "equity": [point(end, 40) for end in ends],
            "assets": [point(end, 100) for end in ends],
            "assets_current": [point(end, 50) for end in ends],
            "liabilities": [point(end, 60) for end in ends],
            "liabilities_current": [point(end, 20) for end in ends],
            "cash": [point(end, 0) for end in ends],
            "debt": [point(end, 50) for end in ends],
            "da": [point(end, 15) for end in ends],
            "sbc": [],
            "rd": [],
            "tax": [],
            "pretax": [],
            "shares_out": [point("2025-12-31", 100)],
            "shares_diluted": [point(end, 100) for end in ends],
            "dividends": [],
            "buybacks": [],
            "retained_earnings": [point(end, 10) for end in ends],
        }
        bars = dated_bars(260)
        for bar in bars:
            bar["close"] = 10
            bar["high"] = 10
        prices = {"bars": bars, "sourceLabel": "Nasdaq Historical", "sourceUrl": "https://api.nasdaq.com/x", "benchmark": None}
        analysts = {"buyShare": 1, "targetUpside": None, "epsRevision": None, "count": 3, "observed": "2026-10-01"}
        scored = score_company(CONFIG, bundle, prices, analysts, "2026-10-16", {"restatement": False})
        by_id = {item["id"]: item for item in scored["criteria"]}
        valuation = {metric["id"]: metric for metric in by_id["C_08"]["metrics"]}["valuation_primary"]
        self.assertEqual(valuation["label"], "(EV/Umsatz) / Umsatzwachstum")
        self.assertEqual(valuation["scale"], "≤ 0,20 = 10, 1,00 = 0")
        # EV = 10*100 + 50 - 0 = 1050; sales 200; growth 200/150-1 = 1/3;
        # ratio = (1050/200) / (100/3) = 5.25 / 33.333 = 0.1575 → 10 points
        self.assertEqual(valuation["points"], 10)
        self.assertTrue(by_id["C_06"]["capApplied"])
        self.assertEqual(by_id["C_06"]["score"], 7)
        self.assertIn("Negativer Free Cashflow bei hoher Nettoverschuldung", scored["koFlags"])

        healthy = dict(bundle)
        healthy["fcf"] = [point(end, 20) for end in ends]
        healthy["debt"] = [point(end, 1) for end in ends]
        healthy["net_income"] = [point(end, 20) for end in ends]
        healthy["eps"] = [point("2022-12-31", 1), point("2025-12-31", 2)]
        healthy["operating_income"] = [point(end, 20) for end in ends]
        no_flags = score_company(CONFIG, healthy, prices, {}, "2026-10-16", {})
        self.assertEqual(no_flags["koFlags"], [])
        peg = {m["id"]: m for c in no_flags["criteria"] if c["id"] == "C_08" for m in c["metrics"]}["valuation_primary"]
        self.assertEqual(peg["label"], "PEG")
        self.assertIsNotNone(peg["points"])

    def test_missing_inputs_do_not_become_zero(self):
        bundle = {"revenue": [point("2024-12-31", 100), point("2025-12-31", 110)]}
        scored = score_company(CONFIG, bundle, {}, {}, "2026-10-16", {})
        research = metric_map(scored, "C_03")[1]["rd_vs_industry"]
        self.assertIsNone(research["points"])
        self.assertIsNone(research["value"])
        self.assertNotEqual(research["points"], 0)
        self.assertEqual(research["status"], "nicht ausgewiesen")
        growth = metric_map(scored, "C_03")[1]["industry_growth"]
        self.assertIsNone(growth["points"])
        self.assertNotEqual(growth["points"], 0)

    def test_altman_only_when_every_input_exists(self):
        end = "2025-12-31"
        bundle = {
            "revenue": [point(end, 40)],
            "operating_income": [point(end, -5)],
            "assets": [point(end, 100)],
            "assets_current": [point(end, 20)],
            "liabilities_current": [point(end, 40)],
            "liabilities": [point(end, 80)],
            "retained_earnings": [point(end, 5)],
            "shares_out": [point(end, 1)],
            "fcf": [point(end, 5)],
            "debt": [point(end, 1)],
            "cash": [point(end, 1)],
            "da": [point(end, 1)],
        }
        bars = [{"date": "2026-10-15", "close": 10.0, "high": 10.0}]
        scored = score_company(
            CONFIG, bundle,
            {"bars": bars, "sourceLabel": "Stooq", "sourceUrl": "https://stooq.com/x"},
            {}, "2026-10-16", {},
        )
        self.assertTrue(any(flag.startswith("Altman Z") for flag in scored["koFlags"]))
        missing = score_company(CONFIG, bundle, {}, {}, "2026-10-16", {})
        self.assertFalse(any(flag.startswith("Altman Z") for flag in missing["koFlags"]))


class MissingDataTests(unittest.TestCase):
    def test_missing_metrics_are_not_scored_as_zero(self):
        bundle = {
            "revenue": [point("2024-12-31", 100_000_000), point("2025-12-31", 110_000_000)],
            "gross_profit": [point("2025-12-31", 66_000_000)],
            "moat": _industry_moat(gross_margin=0.40),
        }
        scored = score_company(CONFIG, bundle, {}, {}, "2026-10-16", {})
        by_id = {item["id"]: item for item in scored["criteria"]}
        metrics = {metric["id"]: metric for metric in by_id["C_01"]["metrics"]}
        self.assertEqual(metrics["gm_vs_industry"]["points"], 10)
        self.assertIsNone(metrics["gm_vs_industry"]["status"])
        self.assertIsNone(metrics["roic_spread"]["points"])
        self.assertNotEqual(metrics["roic_spread"]["points"], 0)
        self.assertEqual(metrics["roic_spread"]["status"], "n/a")
        self.assertIsNone(by_id["C_01"]["score"])
        self.assertEqual(by_id["C_01"]["status"], "wenig Daten")
        self.assertIn("1 von 4 Metriken", by_id["C_01"]["dataQuality"])
        self.assertEqual(by_id["C_06"]["status"], "wenig Daten")
        self.assertIsNone(by_id["C_06"]["score"])

        zero = {
            "revenue": [point("2025-12-31", 100_000_000)],
            "gross_profit": [point("2025-12-31", 20_000_000)],
            "moat": _industry_moat(gross_margin=0.40),
        }
        scored_zero = score_company(CONFIG, zero, {}, {}, "2026-10-16", {})
        gross = {m["id"]: m for c in scored_zero["criteria"] if c["id"] == "C_01" for m in c["metrics"]}
        self.assertEqual(gross["gm_vs_industry"]["points"], 0.0)
        self.assertIsNone(gross["gm_vs_industry"]["status"])
        self.assertEqual(gross["gm_stability"]["status"], "zu kurze Historie")
        self.assertIsNone(gross["gm_stability"]["points"])
        self.assertTrue(all(
            metric["points"] is None
            for item in scored_zero["criteria"]
            for metric in item["metrics"]
            if metric.get("status")
        ))

    def test_weights_are_renormalized_and_thin_coverage_hides_the_number(self):
        full = [
            {"id": "C_01", "score": 10, "weight": 1},
            {"id": "C_02", "score": 10, "weight": 1},
            {"id": "C_07", "score": 10, "weight": 0.5},
            {"id": "C_06", "score": None, "weight": 1},
        ]
        decision = coverage_decision(full, 3, "wenig Daten")
        self.assertEqual(decision["score"], 10)
        self.assertIsNone(decision["status"])
        self.assertEqual(decision["criteriaWithData"], 3)
        self.assertIn("C_06", decision["note"])
        self.assertIn("nur Kriterien mit Daten", decision["note"])
        diluted = weighted_total([(10, 1), (10, 1), (10, 0.5), (0, 1)])
        self.assertAlmostEqual(diluted, 7.5)
        self.assertNotEqual(round(diluted, 1), decision["score"])

        thin = [
            {"id": "C_05", "score": 0, "weight": 1},
            {"id": "C_01", "score": None, "weight": 1},
            {"id": "C_07", "score": None, "weight": 0.5},
        ]
        hidden = coverage_decision(thin, CONFIG["minCriteriaWithData"], CONFIG["missingDataStatus"])
        self.assertIsNone(hidden["score"])
        self.assertNotEqual(hidden["score"], 0)
        self.assertEqual(hidden["status"], "wenig Daten")
        self.assertEqual(hidden["criteriaWithData"], 1)
        self.assertIn("Weniger als 3", hidden["note"])

    def test_assemble_marks_a_thin_stock(self):
        bundle = {
            "revenue": [point("2024-12-31", 100_000_000), point("2025-12-31", 110_000_000)],
            "gross_profit": [point("2025-12-31", 66_000_000)],
            "moat": {
                "sec": True,
                "industry": "Software (System & Application)",
                "label": "Damodaran, Stand Januar 2026",
                "benchmarks": {"Software (System & Application)": {"grossMargin": 0.40}},
                "concentration": {"mode": "none"},
            },
        }
        scored = score_company(CONFIG, bundle, {}, {}, "2026-10-16", {})
        document = assemble("SPCX", "Space Exploration Technologies", None, scored, CONFIG)
        self.assertIsNone(document["score"])
        self.assertEqual(document["status"], "wenig Daten")
        self.assertLess(document["criteriaWithData"], 3)
        self.assertGreaterEqual(document["criteriaWithData"], 1)
        self.assertIn("wenig daten", document["missingDataNote"].lower())
        self.assertIsNone(document["trafficLight"])
        present = [item for item in document["criteria"] if item["score"] is not None]
        self.assertTrue(present)
        self.assertTrue(all(
            item["status"] in {
                "wenig Daten", "ausgeblendet, zählt nicht", "zu kurze Historie",
                "nicht anwendbar", "keine US-Meldepflicht",
            }
            for item in document["criteria"] if item["score"] is None
        ))
        hidden_c06 = {item["id"]: item for item in document["criteria"]}["C_06"]
        self.assertEqual(hidden_c06["status"], "wenig Daten")
        self.assertEqual(document["role"]["id"], "satellit")
        self.assertNotIn("C_09", {item["id"] for item in document["criteria"]})
        self.assertEqual(document["role"]["weights"]["C_02"], 2.0)
        self.assertEqual(document["role"]["weights"]["C_03"], 1.0)


class ManagementTests(unittest.TestCase):
    def test_insider_and_institutions_sum_to_the_concept_points(self):
        bundle = {
            "holders": {
                "insider": 0.03457,
                "institutional": 0.6466,
                "observed": "2026-10-16",
            },
            "insider": {
                "measured": True,
                "buy": 0,
                "sell": 250000,
                "buys": 0,
                "sells": 4,
                "observed": "2026-09-15",
                "windowDays": 183,
                "filings": 12,
            },
        }
        sources = {
            "holderLabel": "Yahoo Finance, Anteil Insider und Institutionen",
            "holderUrl": "https://finance.yahoo.com/quote/PLTR/holders",
            "insiderLabel": "SEC EDGAR Form 4",
            "insiderUrl": "https://www.sec.gov/submissions/CIK0001321655.json",
        }
        scored = score_company(CONFIG, bundle, {}, {}, "2026-10-16", sources)
        criterion = {item["id"]: item for item in scored["criteria"]}["C_05"]
        metrics = {metric["id"]: metric for metric in criterion["metrics"]}
        self.assertEqual(metrics["insider_ownership"]["points"], 3)
        self.assertNotIn("note", metrics["insider_ownership"])
        self.assertEqual(
            metrics["insider_ownership"]["scale"],
            "über 10 % = 5, 3–10 % = 3, unter 3 % = 1",
        )
        self.assertEqual(metrics["insider_ownership"]["pointsMax"], 5)
        self.assertEqual(metrics["insider_market_balance"]["pointsMax"], 3)
        self.assertEqual(metrics["institutional_ownership"]["pointsMax"], 2)
        self.assertNotIn("→", criterion["dataQuality"])
        self.assertIn("3 von 3 Metriken verfügbar", criterion["dataQuality"])
        self.assertEqual(metrics["institutional_ownership"]["points"], 2)
        self.assertEqual(metrics["insider_market_balance"]["points"], 0)
        self.assertEqual(metrics["insider_market_balance"]["value"], "Netto-Verkäufe")
        self.assertIn("4 Verkäufe", metrics["insider_market_balance"]["caption"])
        self.assertEqual(metrics["insider_market_balance"]["detail"]["sells"], 4)
        self.assertEqual(criterion["score"], 5)
        self.assertEqual(metrics["insider_ownership"]["sourceUrl"], sources["holderUrl"])

    def test_quote_uses_the_last_close(self):
        from scripts.build_scores import price_quote
        quote = price_quote(
            {"bars": [{"date": "2026-10-15", "close": 178.32}], "sourceLabel": "Nasdaq", "sourceUrl": "https://api.nasdaq.com/api/quote/PLTR/historical"},
            "USD",
        )
        self.assertEqual(quote["close"], 178.32)
        self.assertEqual(quote["date"], "2026-10-15")
        self.assertEqual(quote["currency"], "USD")
        document = assemble("PLTR", "Palantir Technologies", None, {
            "criteria": [],
            "available": 0,
            "total": 0,
            "koFlags": [],
            "priceNote": None,
        }, CONFIG, quote)
        self.assertEqual(document["price"]["close"], 178.32)

    def test_stand_uses_the_close_and_ignores_a_trade_date_alone(self):
        from scoring.metrics import data_stand
        insider_only = [{
            "metrics": [{
                "id": "insider_market_balance",
                "observedAt": "2026-04-02",
                "raw": None,
            }]
        }]
        self.assertIsNone(data_stand(insider_only, None))
        self.assertEqual(data_stand(insider_only, {"date": "2026-10-15"}), "2026-10-15")
        self.assertEqual(
            data_stand(
                [{"metrics": [{"observedAt": "2025-12-31", "raw": 0.1}]}],
                {"date": "2026-10-15"},
            ),
            "2026-10-15",
        )


class KillSwitchTests(unittest.TestCase):
    def test_service_worker_and_page_unregister(self):
        root_worker = (ROOT / "sw.js").read_text(encoding="utf-8")
        alias = (ROOT / "service-worker.js").read_text(encoding="utf-8")
        self.assertEqual(root_worker, alias)
        self.assertLess(root_worker.index("skipWaiting"), root_worker.index("caches.delete"))
        activate = root_worker.split("activate", 1)[1]
        self.assertLess(activate.index("caches.delete"), activate.index("unregister"))
        self.assertLess(activate.index("unregister"), activate.index("matchAll"))
        self.assertLess(activate.index("matchAll"), activate.index("navigate"))
        self.assertIn("client.url", root_worker)
        vercel = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        headers = {item["source"]: item["headers"] for item in vercel["headers"]}
        for path in ("/sw.js", "/service-worker.js"):
            values = [header["value"] for header in headers[path] if header["key"] == "Cache-Control"]
            self.assertIn("no-cache", values)
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("serviceWorker", html)
        self.assertIn("unregister", html)
        self.assertIn("missingDataNote", html)
        self.assertIn("gap-note", html)
        self.assertIn('className = "tiles"', html)
        self.assertIn('className = "tile-stand"', html)
        self.assertNotIn("Ich halte diese Aktien selbst.", html)
        self.assertIn(
            "Keine Anlageberatung und keine Kauf- oder Verkaufsempfehlung.",
            html,
        )
        self.assertNotIn("spiegeln keine tatsächlichen Werte wider", html)
        self.assertNotIn("<small>AKTIEN-CHECK</small>", html)
        self.assertNotIn("ZAHLEN BIS", html)
        self.assertIn('href = "#/" + score.ticker', html)
        self.assertIn("hashchange", html)
        self.assertIn("Alle Positionen", html)
        self.assertIn("Übersicht Aktienportfolio", html)
        self.assertIn("ausgeblendet, zählt nicht", html)


def metric_map(scored, criterion_id):
    criterion = {item["id"]: item for item in scored["criteria"]}[criterion_id]
    return criterion, {metric["id"]: metric for metric in criterion["metrics"]}


class ReviewRuleTests(unittest.TestCase):
    def test_loss_maker_margin_progress_and_cash_runway(self):
        ends = ["2024-12-31", "2025-12-31"]
        bundle = {
            "revenue": [point(end, 100) for end in ends],
            "operating_income": [point("2024-12-31", -20), point("2025-12-31", -5)],
            "net_income": [point(end, -8) for end in ends],
            "equity": [point(end, 40) for end in ends],
            "fcf": [point("2025-12-31", -10)],
            "cash": [point("2025-12-31", 30)],
            "debt": [point("2025-12-31", 0)],
            "eps": [point("2025-12-31", -0.2)],
        }
        scored = score_company(CONFIG, bundle, {}, {}, "2026-10-16", {})
        growth, growth_metrics = metric_map(scored, "C_02")
        change = growth_metrics["operating_margin_change_yoy"]
        self.assertEqual(change["raw"], 0.15)
        self.assertEqual(change["points"], 10)
        self.assertIsNone(change["status"])
        finance, finance_metrics = metric_map(scored, "C_04")
        self.assertEqual(finance_metrics["roe"]["status"], "nicht anwendbar")
        self.assertIsNone(finance_metrics["roe"]["points"])
        self.assertNotEqual(finance_metrics["roe"]["points"], 0)
        runway = finance_metrics["cash_runway_months"]
        self.assertEqual(runway["points"], 10)
        self.assertEqual(runway["value"], "> 36 Monate")
        valuation = metric_map(scored, "C_08")[1]["fcf_yield"]
        self.assertEqual(valuation["status"], "nicht anwendbar")
        self.assertIsNone(valuation["points"])
        self.assertIsNotNone(finance["score"])

    def test_profitable_company_keeps_roe_and_skips_the_loss_maker_signals(self):
        ends = ["2024-12-31", "2025-12-31"]
        bundle = {
            "revenue": [point("2024-12-31", 100), point("2025-12-31", 110)],
            "operating_income": [point("2024-12-31", 10), point("2025-12-31", 20)],
            "net_income": [point("2024-12-31", 8), point("2025-12-31", 12)],
            "equity": [point("2024-12-31", 40), point("2025-12-31", 50)],
            "fcf": [point("2025-12-31", 15)],
            "cash": [point("2025-12-31", 30)],
            "debt": [point("2025-12-31", 0)],
            "eps": [point("2022-12-31", 1), point("2025-12-31", 2)],
            "shares_out": [point("2025-12-31", 10)],
        }
        bars = [{"date": "2026-10-15", "close": 20.0, "high": 20.0}]
        scored = score_company(CONFIG, bundle, {"bars": bars}, {}, "2026-10-16", {})
        growth = metric_map(scored, "C_02")[1]["operating_margin_change_yoy"]
        self.assertEqual(growth["status"], "nicht anwendbar")
        self.assertIsNone(growth["points"])
        finance = metric_map(scored, "C_04")[1]
        self.assertIsNone(finance["roe"]["status"])
        self.assertIsNotNone(finance["roe"]["points"])
        self.assertEqual(finance["cash_runway_months"]["status"], "nicht anwendbar")
        valuation = metric_map(scored, "C_08")[1]
        self.assertEqual(valuation["valuation_primary"]["label"], "PEG")
        self.assertIsNone(valuation["fcf_yield"]["status"])
        self.assertIsNotNone(valuation["fcf_yield"]["points"])

    def test_peg_fallback_uses_ev_sales_over_revenue_growth(self):
        bundle = {
            "revenue": [point("2024-12-31", 100), point("2025-12-31", 150)],
            "operating_income": [point("2025-12-31", 20)],
            "net_income": [point("2025-12-31", 10)],
            "eps": [point("2022-12-31", 2), point("2025-12-31", 1)],
            "fcf": [point("2025-12-31", 5)],
            "equity": [point("2024-12-31", 40), point("2025-12-31", 50)],
            "cash": [point("2025-12-31", 0)],
            "debt": [point("2025-12-31", 0)],
            "shares_out": [point("2025-12-31", 10)],
            "da": [point("2025-12-31", 1)],
        }
        bars = [{"date": "2026-10-15", "close": 20.0, "high": 20.0}]
        scored = score_company(CONFIG, bundle, {"bars": bars}, {}, "2026-10-16", {})
        valuation = metric_map(scored, "C_08")[1]["valuation_primary"]
        self.assertEqual(valuation["label"], "(EV/Umsatz) / Umsatzwachstum")
        self.assertEqual(valuation["points"], 10)
        self.assertIn("Ersatz für PEG", valuation["note"])

    def test_insurer_ratios_are_not_applicable(self):
        bundle = {
            "revenue": [point("2025-12-31", 100)],
            "operating_income": [point("2025-12-31", 10)],
            "net_income": [point("2025-12-31", 5)],
            "assets_current": [point("2025-12-31", 10)],
            "liabilities_current": [point("2025-12-31", 100)],
            "debt": [point("2025-12-31", 80)],
            "cash": [point("2025-12-31", 0)],
            "da": [point("2025-12-31", 5)],
            "fcf": [point("2025-12-31", 4)],
            "equity": [point("2024-12-31", 40), point("2025-12-31", 50)],
        }
        scored = score_company(CONFIG, bundle, {}, {}, "2026-10-16", {}, {"sector": "insurer"})
        finance = metric_map(scored, "C_04")[1]
        for metric_id in ("current_ratio", "net_debt_to_ebitda"):
            self.assertEqual(finance[metric_id]["status"], "nicht anwendbar")
            self.assertIsNone(finance[metric_id]["points"])
            self.assertNotEqual(finance[metric_id]["points"], 0)
        model = metric_map(scored, "C_01")[1]
        growth = metric_map(scored, "C_02")[1]
        for metric_id in ("gm_vs_industry", "gm_stability"):
            self.assertEqual(model[metric_id]["status"], "nicht anwendbar")
            self.assertEqual(model[metric_id]["statusReason"], "Finanzwert")
            self.assertIsNone(model[metric_id]["points"])
        self.assertEqual(growth["operating_margin_change_yoy"]["status"], "nicht anwendbar")
        self.assertIsNone(growth["operating_margin_change_yoy"]["points"])

    def test_plan_sales_purchases_and_small_net_selling(self):
        bars = [{"date": "2026-10-15", "close": 100.0, "high": 100.0}]
        shares = {"shares_out": [point("2025-12-31", 1_000_000)]}
        neutral = score_company(CONFIG, {
            **shares,
            "insider": {
                "measured": True, "buy": 0, "sell": 50_000, "planSell": 0,
                "buys": 0, "sells": 2, "planSells": 0, "buyers": [],
                "observed": "2026-09-01", "windowDays": 183, "filings": 2,
            },
        }, {"bars": bars}, {}, "2026-10-16", {})
        balance = metric_map(neutral, "C_05")[1]["insider_market_balance"]
        self.assertEqual(balance["points"], 2)
        self.assertEqual(balance["value"], "neutral")
        self.assertIn("0,1 %", balance["caption"])

        planned = score_company(CONFIG, {
            **shares,
            "insider": {
                "measured": True, "buy": 0, "sell": 0, "planSell": 200_000,
                "buys": 0, "sells": 0, "planSells": 3, "buyers": [],
                "observed": "2026-09-01", "windowDays": 183, "filings": 3,
            },
        }, {"bars": bars}, {}, "2026-10-16", {})
        plan_balance = metric_map(planned, "C_05")[1]["insider_market_balance"]
        self.assertEqual(plan_balance["points"], 2)
        self.assertEqual(plan_balance["value"], "neutral")
        self.assertIn("10b5-1-Plan", plan_balance["caption"])

        heavy = score_company(CONFIG, {
            **shares,
            "insider": {
                "measured": True, "buy": 0, "sell": 200_000, "planSell": 0,
                "buys": 0, "sells": 1, "planSells": 0, "buyers": [],
                "observed": "2026-09-01", "windowDays": 183, "filings": 1,
            },
        }, {"bars": bars}, {}, "2026-10-16", {})
        self.assertEqual(metric_map(heavy, "C_05")[1]["insider_market_balance"]["points"], 0)

        doubled = score_company(CONFIG, {
            **shares,
            "insider": {
                "measured": True, "buy": 100_000, "sell": 150_000, "planSell": 0,
                "buys": 2, "sells": 1, "planSells": 0, "buyers": ["0001", "0002"],
                "observed": "2026-09-01", "windowDays": 183, "filings": 3,
            },
        }, {"bars": bars}, {}, "2026-10-16", {})
        bought = metric_map(doubled, "C_05")[1]["insider_market_balance"]
        self.assertEqual(bought["points"], 3)
        self.assertIn("Netto-Käufe", bought["value"])
        self.assertIn("mehrere", bought["note"])

    def test_short_history_and_missing_us_filing_are_not_zero(self):
        bars = dated_bars(80)
        short = score_company(CONFIG, {}, {"bars": bars}, {}, "2026-10-16", {})
        prices = metric_map(short, "C_07")[1]
        for metric_id in ("excess_return_12_1", "above_sma_200", "distance_52w_high"):
            self.assertEqual(prices[metric_id]["status"], "zu kurze Historie")
            self.assertIsNone(prices[metric_id]["points"])
            self.assertNotEqual(prices[metric_id]["points"], 0)
        swedish = score_company(CONFIG, {}, {}, {}, "2026-10-16", {}, {"sec": False})
        fundamentals = metric_map(swedish, "C_01")[1]
        self.assertTrue(all(
            metric["status"] == "nicht bewertbar – keine US-Meldepflicht" for metric in fundamentals.values()
        ))
        self.assertEqual(metric_map(swedish, "C_01")[0]["status"], "nicht bewertbar – keine US-Meldepflicht")
        self.assertIsNone(metric_map(swedish, "C_01")[0]["score"])
        self.assertEqual(metric_map(swedish, "C_06")[0]["status"], "wenig Daten")

    def test_quarterly_only_history_is_too_short(self):
        facts = {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {"start": "2025-01-01", "end": "2025-06-30", "val": 8, "form": "10-Q", "fp": "Q2", "filed": "2026-08-04"},
                            {"start": "2026-01-01", "end": "2026-06-30", "val": 12, "form": "10-Q", "fp": "Q2", "filed": "2026-08-04"},
                        ]
                    }
                }
            }
        }
        bundle = extract_fundamentals(facts)
        self.assertEqual(bundle["revenue"], [])
        self.assertEqual(bundle["_history_start"], "2025-01-01")
        self.assertEqual(bundle["_history_end"], "2026-06-30")
        scored = score_company(CONFIG, bundle, {}, {}, "2026-10-16", {})
        growth = metric_map(scored, "C_02")[1]
        for metric_id in (
            "revenue_cagr_3y", "revenue_cagr_5y", "eps_cagr_3y", "fcf_cagr_3y",
            "revenue_acceleration", "operating_margin_change_yoy",
        ):
            self.assertEqual(growth[metric_id]["status"], "zu kurze Historie", metric_id)
            self.assertIsNone(growth[metric_id]["points"])
            self.assertNotEqual(growth[metric_id]["points"], 0)

    def test_long_reporting_window_without_annuals_stays_missing(self):
        scored = score_company(CONFIG, {
            "_history_start": "2016-01-01",
            "_history_end": "2025-12-31",
        }, {}, {}, "2026-10-16", {})
        growth = metric_map(scored, "C_02")[1]["revenue_cagr_3y"]
        self.assertEqual(growth["status"], "wenig Daten")
        self.assertIsNone(growth["points"])

    def test_peg_fallback_uses_three_year_cagr_when_the_latest_year_shrinks(self):
        bundle = {
            "revenue": [
                point("2022-12-31", 100),
                point("2024-12-31", 120),
                point("2025-12-31", 116),
            ],
            "operating_income": [point("2025-12-31", 20)],
            "net_income": [point("2025-12-31", 10)],
            "eps": [point("2022-12-31", 2), point("2025-12-31", 1)],
            "fcf": [point("2025-12-31", 5)],
            "equity": [point("2025-12-31", 50)],
            "cash": [point("2025-12-31", 0)],
            "debt": [point("2025-12-31", 0)],
            "shares_out": [point("2025-12-31", 10)],
        }
        bars = [{"date": "2026-10-15", "close": 20.0, "high": 20.0}]
        scored = score_company(CONFIG, bundle, {"bars": bars}, {}, "2026-10-16", {})
        valuation = metric_map(scored, "C_08")[1]["valuation_primary"]
        self.assertEqual(valuation["label"], "(EV/Umsatz) / Umsatzwachstum")
        self.assertIn("3-Jahres-CAGR", valuation["note"])
        self.assertIsNotNone(valuation["points"])
        self.assertNotEqual(valuation["points"], 0)


def _roic_year(end, operating, pretax):
    return {
        "operating_income": point(end, operating),
        "tax": point(end, pretax * 0.2 if pretax > 0 else 0),
        "pretax": point(end, pretax),
        "equity": point(end, 100),
        "debt": point(end, 0),
        "cash": point(end, 0),
    }


class RoundTwoTests(unittest.TestCase):
    def test_roic_uses_only_consecutive_years(self):
        pieces = [
            _roic_year("2021-12-31", 10, 10),
            _roic_year("2022-12-31", -5, -5),
            _roic_year("2025-12-31", 20, 20),
        ]
        bundle = {"revenue": [point("2025-12-31", 100)]}
        for key in ("operating_income", "tax", "pretax", "equity", "debt", "cash"):
            bundle[key] = [row[key] for row in pieces]
        scored = score_company(CONFIG, bundle, {}, {}, "2026-10-20", {})
        roic = metric_map(scored, "C_01")[1]["roic_spread"]
        self.assertEqual(roic["status"], "n/a")
        self.assertIsNone(roic["points"])
        self.assertNotEqual(roic["points"], 0)

        consecutive = {"revenue": [point("2025-12-31", 100)], "moat": _industry_moat(wacc=0.0)}
        years = [_roic_year("2024-12-31", 10, 10), _roic_year("2025-12-31", 20, 20)]
        for key in ("operating_income", "tax", "pretax", "equity", "debt", "cash"):
            consecutive[key] = [row[key] for row in years]
        scored_ok = score_company(CONFIG, consecutive, {}, {}, "2026-10-20", {})
        roic_ok = metric_map(scored_ok, "C_01")[1]["roic_spread"]
        self.assertIsNone(roic_ok["status"])
        self.assertAlmostEqual(roic_ok["raw"], 0.12, places=4)

    def test_grab_cost_of_sales_gross_margin(self):
        def row(start, end, val):
            return {"start": start, "end": end, "val": val, "form": "20-F", "fp": "FY", "filed": end}

        facts = {"ifrs-full": {
            "Revenue": {"units": {"USD": [
                row("2024-01-01", "2024-12-31", 2830 * 1_000_000),
                row("2025-01-01", "2025-12-31", 3370 * 1_000_000),
            ]}},
            "CostOfSales": {"units": {"USD": [
                row("2024-01-01", "2024-12-31", 1641.4 * 1_000_000),
                row("2025-01-01", "2025-12-31", 1914 * 1_000_000),
            ]}},
        }}
        bundle = extract_fundamentals(facts)
        bundle["moat"] = _industry_moat(gross_margin=0.0, industry="Transportation")
        bundle["moat"]["ifrs"] = True
        bundle["moat"]["concentration"] = {"mode": "na"}
        scored = score_company(CONFIG, bundle, {}, {}, "2026-10-20", {})
        gross = metric_map(scored, "C_01")[1]["gm_vs_industry"]
        self.assertAlmostEqual(gross["raw"], (3370 - 1914) / 3370, places=4)
        self.assertEqual(gross["value"], "43,2 Pp")
        self.assertIsNone(gross["status"])
        quality = metric_map(scored, "C_01")[1]["revenue_quality"]
        self.assertEqual(quality["status"], "n/a")
        self.assertIsNone(quality["points"])

    def test_eps_cagr_falls_back_to_two_positive_years(self):
        bundle = {
            "revenue": [point("2025-12-31", 100)],
            "net_income": [point("2025-12-31", 10)],
            "eps": [
                point("2022-12-31", -0.27),
                point("2023-12-31", 2.90),
                point("2025-12-31", 7.17),
            ],
        }
        scored = score_company(CONFIG, bundle, {}, {}, "2026-10-20", {})
        eps = metric_map(scored, "C_02")[1]["eps_cagr_3y"]
        self.assertEqual(eps["label"], "EPS-CAGR, 2 Jahre")
        self.assertIsNotNone(eps["points"])
        self.assertNotEqual(eps["points"], 0)
        self.assertIn("nicht positiv", eps["note"])
        self.assertNotIn("nicht berechenbar", eps["note"])
        valuation = metric_map(scored, "C_08")[1]["valuation_primary"]
        self.assertIsNone(valuation["points"])

    def test_peg_note_when_eps_growth_cannot_be_computed(self):
        bundle = {
            "revenue": [point("2024-12-31", 100), point("2025-12-31", 130)],
            "operating_income": [point("2025-12-31", 20)],
            "net_income": [point("2025-12-31", 10)],
            "eps": [point("2022-12-31", -1), point("2025-12-31", 2)],
            "fcf": [point("2025-12-31", 5)],
            "equity": [point("2025-12-31", 50)],
            "cash": [point("2025-12-31", 0)],
            "debt": [point("2025-12-31", 0)],
            "shares_out": [point("2025-12-31", 10)],
        }
        bars = [{"date": "2026-10-15", "close": 20.0, "high": 20.0}]
        scored = score_company(CONFIG, bundle, {"bars": bars}, {}, "2026-10-20", {})
        valuation = metric_map(scored, "C_08")[1]["valuation_primary"]
        self.assertIn("nicht berechenbar", valuation["note"])

    def test_momentum_floor_and_distance_bands(self):
        rules = CONFIG["momentum"]
        self.assertEqual(_distance_points(0.1, None, rules)[0], 10)
        self.assertEqual(_distance_points(0.35, 7, rules)[0], 8.5)
        self.assertEqual(_distance_points(0.35, 5, rules)[0], 6)
        self.assertEqual(_distance_points(0.35, 3, rules)[0], 5)
        self.assertEqual(_distance_points(0.55, 7, rules)[0], 10)
        self.assertEqual(_distance_points(0.55, 5, rules)[0], 5)
        points, warning = _distance_points(0.7, 8, rules)
        self.assertIsNone(points)
        self.assertIn("60", warning)

        bars = dated_bars(300)
        bars[47]["close"] = 100
        bars[278]["close"] = 40
        bars[299]["close"] = 20
        bench = dated_bars(300)
        prices = {"bars": bars, "benchmark": {"bars": bench, "label": "S&P 500"}}
        bundle = {"revenue": [point("2022-12-31", 100), point("2025-12-31", 200)]}
        scored = score_company(CONFIG, bundle, prices, {}, "2026-10-20", {})
        momentum = metric_map(scored, "C_07")
        self.assertEqual(momentum[0]["score"], 3)
        self.assertIn("Untergrenze", momentum[0]["dataQuality"])
        distance = momentum[1]["distance_52w_high"]
        self.assertIsNone(distance["points"])
        self.assertIsNotNone(distance["value"])
        self.assertIsNotNone(distance["raw"])
        self.assertGreater(distance["raw"], 0.6)
        self.assertIn("60", distance["status"])
        self.assertGreaterEqual(metric_map(scored, "C_02")[0]["score"], 5)

    def test_yahoo_quarters_do_not_become_the_cagr_series(self):
        yahoo = {
            "_url": "https://finance.yahoo.com/quote/SIVE.ST",
            "defaultKeyStatistics": {"sharesOutstanding": {"raw": 1000}},
            "incomeStatementHistoryQuarterly": {"incomeStatementHistory": [
                {"endDate": {"fmt": "2025-09-30"}, "totalRevenue": {"raw": 10}, "grossProfit": {"raw": 0}},
                {"endDate": {"fmt": "2025-12-31"}, "totalRevenue": {"raw": 11}, "grossProfit": {"raw": 0}},
                {"endDate": {"fmt": "2026-03-31"}, "totalRevenue": {"raw": 12}, "grossProfit": {"raw": 0}},
                {"endDate": {"fmt": "2026-06-30"}, "totalRevenue": {"raw": 13}, "grossProfit": {"raw": 0}},
            ]},
        }
        bundle = apply_fundamental_fallback(
            {"shares_out": [{"end": "2025-12-31", "val": 0}], "revenue": []},
            yahoo, None, yahoo["_url"], "2026-10-20",
        )
        self.assertEqual(bundle["revenue"], [])
        self.assertEqual(bundle["snapshot"]["shares_out"], 1000)
        self.assertEqual(bundle["snapshot"]["revenue"], 46)
        self.assertIsNone(bundle["snapshot"].get("gross_profit"))
        self.assertIsNone(bundle["snapshot"].get("revenue_prior"))
        self.assertEqual(bundle["snapshot"]["sourceLabel"], "Yahoo Finance quoteSummary")

    def test_dividend_role_keeps_c09(self):
        scored = score_company(CONFIG, {
            "revenue": [point("2025-12-31", 100)],
            "gross_profit": [point("2025-12-31", 40)],
        }, {}, {}, "2026-10-20", {})
        scored["dividend"] = {"yield": 0.03, "no_cut": True}
        enabled = json.loads(json.dumps(CONFIG))
        enabled["roles"]["dividendEnabled"] = True
        document = assemble("PYPL", "PayPal", None, scored, enabled)
        self.assertEqual(document["role"]["id"], "dividende")
        self.assertEqual(document["role"]["weights"]["C_09"], 1.5)
        self.assertIn("C_09", {item["id"] for item in document["criteria"]})
        self.assertEqual({item["id"]: item["weight"] for item in document["criteria"]}["C_04"], 1.5)
        off = assemble("PYPL", "PayPal", None, scored, CONFIG)
        self.assertEqual(off["role"]["id"], "satellit")
        self.assertNotIn("C_09", {item["id"] for item in off["criteria"]})


class RoundThreeTests(unittest.TestCase):
    def test_negative_derived_revenue_quarter_is_dropped(self):
        from scoring.flows import _quarters
        rows = [
            {"start": "2025-01-01", "end": "2025-03-31", "val": 5, "filed": "2026-05-06", "form": "10-Q"},
            {"start": "2025-01-01", "end": "2025-09-30", "val": 45, "filed": "2025-11-05", "form": "10-Q"},
            {"start": "2025-01-01", "end": "2025-12-31", "val": 32, "filed": "2026-03-18", "form": "10-K"},
        ]
        quarters = _quarters(rows, allow_negative_derived=False)
        self.assertNotIn("2025-12-31", {item["end"] for item in quarters})
        self.assertTrue(all(item["val"] >= 0 for item in quarters))
        derived = _quarters([
            {"start": "2025-01-01", "end": "2025-09-30", "val": 90, "filed": "2025-11-01", "form": "10-Q"},
            {"start": "2025-01-01", "end": "2025-12-31", "val": 120, "filed": "2026-02-01", "form": "10-K"},
        ], allow_negative_derived=False)
        self.assertEqual(derived[-1]["val"], 30)

    def test_growth_metrics_and_net_cash_do_not_treat_gaps_as_zero(self):
        bundle = {
            "revenue": [point("2023-12-31", 80), point("2024-12-31", 70), point("2025-12-31", 60)],
            "snapshot": {
                "end": "2026-06-30",
                "label": "H1 bis 2026-06-30",
                "months": 6,
                "revenue": 17.4,
                "revenue_prior": 11.0,
                "revenue_quarter_yoy": 0.623,
                "revenue_quarter_end": "2026-06-30",
                "revenue_quarter_label": "Quartal bis 2026-06-30",
                "cash": 31.4,
                "debt": 0.0,
                "debt_explicit_zero": True,
                "debt_end": "2025-12-31",
                "net_income": -7.8,
                "operating_income": -8.3,
                "operating_income_prior": -4.9,
                "cfo": -0.63,
                "fcf": -0.75,
                "sourceLabel": "SEC EDGAR companyfacts",
                "provenance": {
                    "revenue": {"label": "SEC EDGAR companyfacts", "url": "https://data.sec.gov/oss", "end": "2026-06-30"},
                    "revenue_quarter": {"label": "SEC EDGAR companyfacts", "url": "https://data.sec.gov/oss", "end": "2026-06-30"},
                    "cash": {"label": "Yahoo Finance quoteSummary financialData", "url": "https://finance.yahoo.com/quote/OSS", "note": "financialData totalCash"},
                    "debt": {"label": "SEC EDGAR companyfacts", "url": "https://data.sec.gov/oss", "end": "2025-12-31", "note": "Finanzschulden explizit 0"},
                    "cfo": {"label": "SEC EDGAR companyfacts", "end": "2026-06-30"},
                },
            },
        }
        scored = score_company(CONFIG, bundle, {}, {
            "revenueEstimateYoy": 0.232,
            "label": "Yahoo Finance quoteSummary",
            "url": "https://finance.yahoo.com/quote/OSS",
            "count": 3,
        }, "2026-10-20", {"fundamentalUrl": "https://data.sec.gov/oss"})
        metrics = {row["id"]: row for row in next(item for item in scored["criteria"] if item["id"] == "C_02")["metrics"]}
        self.assertAlmostEqual(metrics["revenue_quarter_yoy"]["raw"], 0.623, places=3)
        self.assertEqual(metrics["revenue_quarter_yoy"]["points"], 10)
        self.assertIn("Halbjahr", metrics["revenue_ttm_yoy"]["label"])
        self.assertGreater(metrics["revenue_ttm_yoy"]["points"], 0)
        self.assertAlmostEqual(metrics["revenue_estimate_yoy"]["raw"], 0.232, places=3)
        self.assertIn("Analystenschätzung", metrics["revenue_estimate_yoy"]["sourceLabel"])
        self.assertIsNone(metrics["eps_cagr_3y"]["points"])
        self.assertEqual(metrics["eps_cagr_3y"]["status"], "wenig Daten")
        growth = next(item for item in scored["criteria"] if item["id"] == "C_02")
        for metric in growth["metrics"]:
            self.assertEqual(metric.get("weight", 1), 1)
        present = [
            metric["points"] for metric in growth["metrics"]
            if metric.get("points") is not None and not metric.get("status")
        ]
        self.assertNotIn("eps_cagr_3y", [metric["id"] for metric in growth["metrics"] if metric.get("points") is not None])
        self.assertEqual(growth["score"], round(sum(present) / len(present), 1))
        leverage = {row["id"]: row for row in next(item for item in scored["criteria"] if item["id"] == "C_04")["metrics"]}
        self.assertEqual(leverage["net_debt_to_ebitda"]["value"], "Netto-Cash")
        self.assertEqual(leverage["net_debt_to_ebitda"]["points"], 10)
        self.assertIn("Yahoo", leverage["net_debt_to_ebitda"]["sourceLabel"])
        self.assertIn("SEC", leverage["net_debt_to_ebitda"]["sourceLabel"])

    def test_explicit_zero_debt_is_not_replaced_by_yahoo_total_debt(self):
        yahoo = {
            "_url": "https://finance.yahoo.com/quote/OSS",
            "financialData": {
                "totalCash": {"raw": 31407756},
                "totalDebt": {"raw": 1367134},
            },
        }
        bundle = apply_fundamental_fallback({
            "snapshot": {
                "end": "2026-06-30",
                "revenue": 17418162,
                "cash": 31174880,
                "cash_end": "2025-12-31",
                "debt": 0,
                "debt_explicit_zero": True,
                "debt_end": "2025-12-31",
                "provenance": {"debt": {"label": "SEC EDGAR companyfacts", "end": "2025-12-31"}},
            },
        }, yahoo, None, yahoo["_url"], "2026-10-20")
        snap = bundle["snapshot"]
        self.assertEqual(snap["debt"], 0)
        self.assertEqual(snap["cash"], 31407756)
        self.assertIn("Yahoo", snap["provenance"]["cash"]["label"])

    def test_yahoo_free_cash_flow_above_operating_cash_flow_is_dropped(self):
        yahoo = {
            "_url": "https://finance.yahoo.com/quote/HIMS",
            "financialData": {
                "operatingCashflow": {"raw": 263450000},
                "freeCashflow": {"raw": 849437248},
                "totalRevenue": {"raw": 2578112000},
            },
        }
        bundle = apply_fundamental_fallback({
            "snapshot": {
                "end": "2026-06-30",
                "label": "TTM bis 2026-06-30",
                "months": 12,
                "revenue": 2578112000,
                "sourceLabel": "SEC EDGAR companyfacts",
                "provenance": {"revenue": {"label": "SEC EDGAR companyfacts", "end": "2026-06-30"}},
            },
        }, yahoo, None, yahoo["_url"], "2026-10-20")
        snap = bundle["snapshot"]
        self.assertEqual(snap["cfo"], 263450000)
        self.assertIsNone(snap.get("fcf"))

    def test_page_hides_role_and_shows_the_scale(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("GEWICHTE", html)
        self.assertNotIn('id="rolle"', html)
        self.assertIn("Kriterium = ", html)
        self.assertIn("Mittel aus ", html)
        self.assertIn("Summe aus ", html)
        self.assertIn("Ohne Daten:", html)
        self.assertIn("Nicht anwendbar:", html)
        self.assertIn("Nicht gewertet:", html)
        self.assertIn("(max. 10)", html)
        self.assertIn("pointsMax", html)
        self.assertIn("table-layout:fixed", html)
        self.assertIn(">Skala<", html)
        self.assertNotIn("Eine Metrik ohne Daten zählt nicht als 0.", html)
        self.assertNotIn("Der Aktien-Score ist der gewichtete Mittelwert", html)
        self.assertIn("function companyBlock", html)
        self.assertIn('fetch("config/profiles.json")', html)
        self.assertNotIn("data-x2", html)
        self.assertNotIn("isCriterionX2", html)
        self.assertNotIn("angepasster Score", html)
        self.assertNotIn("x2-reset", html)
        self.assertNotIn("ac-x2:", html)
        self.assertNotIn("withVisitorWeights", html)
        self.assertIn("function sourceBlock", html)
        self.assertNotIn("gewichtetes Mittel", html)

    def test_published_score_stays_standard_until_the_visitor_doubles(self):
        for criterion in CONFIG["criteria"]:
            if criterion["id"] != "C_02":
                continue
            for metric in criterion["metrics"]:
                self.assertNotIn("weight", metric)
        oss = json.loads((ROOT / "data" / "OSS" / "score.json").read_text(encoding="utf-8"))
        self.assertEqual(oss["score"], 6.2)
        growth = next(item for item in oss["criteria"] if item["id"] == "C_02")
        self.assertEqual(growth["score"], 5.6)
        present = [item["score"] for item in oss["criteria"] if item.get("score") is not None]
        self.assertEqual(round(sum(present) / len(present) + 1e-9, 1), 6.2)
        weighted = 0.0
        weight_sum = 0.0
        for item in oss["criteria"]:
            if item.get("score") is None:
                continue
            weighted += item["score"] * item["weight"]
            weight_sum += item["weight"]
        self.assertNotEqual(round((weighted / weight_sum) * 10 + 1e-9, 1), oss["score"])


class RoundFiveTests(unittest.TestCase):
    def test_ttm_acceleration_uses_the_prior_window_and_a_gap_stays_blank(self):
        rows = []
        values = [v * 20000 for v in (100, 110, 120, 130, 140, 150, 160, 170, 180, 190, 200, 210, 220, 230)]
        starts = [
            "2023-01-01", "2023-04-01", "2023-07-01", "2023-10-01",
            "2024-01-01", "2024-04-01", "2024-07-01", "2024-10-01",
            "2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01",
            "2026-01-01", "2026-04-01",
        ]
        ends = [
            "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
            "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
            "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
            "2026-03-31", "2026-06-30",
        ]
        for start, end, val in zip(starts, ends, values):
            rows.append({
                "start": start, "end": end, "val": val, "form": "10-Q", "filed": "2026-07-31", "fp": "Q2",
            })
        facts = {"us-gaap": {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rows}}}}
        bundle = extract_fundamentals(facts)
        snap = bundle["snapshot"]
        self.assertEqual(snap["revenue"], 860 * 20000)
        self.assertEqual(snap["revenue_prior"], 700 * 20000)
        self.assertEqual(snap["revenue_prior_prior"], 540 * 20000)
        scored = score_company(CONFIG, bundle, {}, {}, "2026-10-20", {})
        acceleration = metric_map(scored, "C_02")[1]["revenue_acceleration"]
        self.assertAlmostEqual(acceleration["raw"], 860 / 700 - 700 / 540, places=6)
        self.assertEqual(acceleration["points"], 5.0)
        self.assertIn("mindestens 5 Punkte", acceleration["note"])
        self.assertIsNone(acceleration["status"])

        gap = score_company(CONFIG, {"snapshot": {
            "end": "2026-06-30",
            "label": "TTM bis 2026-06-30",
            "months": 12,
            "revenue": 100,
            "revenue_prior": 80,
            "sourceLabel": "SEC EDGAR companyfacts",
        }}, {}, {}, "2026-10-20", {})
        missing = metric_map(gap, "C_02")[1]["revenue_acceleration"]
        self.assertIsNone(missing["points"])
        self.assertNotEqual(missing["points"], 0)
        self.assertIsNotNone(missing["status"])

    def test_technology_and_infrastructure_fills_research_when_gaap_rd_is_absent(self):
        from scoring.edgar import parse_extension_instance

        xml = """<?xml version="1.0"?>
        <xbrl xmlns:amzn="http://example.com/amzn">
          <context id="q"><period><startDate>2026-04-01</startDate><endDate>2026-06-30</endDate></period></context>
          <context id="seg"><entity><segment><explicitMember dimension="x">amzn:AWS</explicitMember></segment></entity>
            <period><startDate>2026-04-01</startDate><endDate>2026-06-30</endDate></period></context>
          <amzn:TechnologyAndInfrastructureExpense contextRef="q" unitRef="usd">33158000000</amzn:TechnologyAndInfrastructureExpense>
          <amzn:TechnologyAndInfrastructureExpense contextRef="seg" unitRef="usd">1</amzn:TechnologyAndInfrastructureExpense>
        </xbrl>""".encode()
        parsed = parse_extension_instance(xml, ("TechnologyAndInfrastructureExpense",), "10-Q", "2026-07-31")
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0][1]["val"], 33158000000)

        def row(start, end, val, form="10-Q", filed="2026-07-31", fp=None):
            item = {"start": start, "end": end, "val": val, "form": form, "filed": filed}
            if fp:
                item["fp"] = fp
            return item

        tech = [
            row("2025-07-01", "2025-09-30", 28_962_000_000, filed="2025-10-31"),
            row("2025-01-01", "2025-09-30", 79_122_000_000, filed="2025-10-31"),
            row("2025-01-01", "2025-12-31", 108_521_000_000, form="10-K", filed="2026-02-06", fp="FY"),
            row("2026-01-01", "2026-03-31", 29_567_000_000, filed="2026-04-30"),
            row("2026-04-01", "2026-06-30", 33_158_000_000),
        ]
        revenue = [
            row("2025-07-01", "2025-09-30", 180_000_000_000),
            row("2025-10-01", "2025-12-31", 190_000_000_000),
            row("2026-01-01", "2026-03-31", 200_000_000_000),
            row("2026-04-01", "2026-06-30", 205_680_000_000),
        ]
        facts = {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": revenue}},
            "TechnologyAndInfrastructureExpense": {"units": {"USD": tech}},
        }}
        bundle = extract_fundamentals(facts)
        self.assertEqual(bundle["snapshot"]["rd"], 121_086_000_000)
        bundle["moat"] = _industry_moat(rd=0.05)
        scored = score_company(CONFIG, bundle, {}, {}, "2026-10-20", {
            "fundamentalLabel": "SEC EDGAR companyfacts",
            "fundamentalUrl": "https://data.sec.gov/api/xbrl/companyfacts/CIK0001018724.json",
        })
        research = metric_map(scored, "C_03")[1]["rd_vs_industry"]
        self.assertEqual(research["label"], "Forschungsquote, Technology and infrastructure")
        self.assertIn("Technology and infrastructure", research["sourceLabel"])
        self.assertEqual(research["points"], 10)
        self.assertAlmostEqual(research["raw"], 121_086_000_000 / 775_680_000_000, places=6)

        gaap = [
            row("2025-07-01", "2025-09-30", 1_000_000_000),
            row("2025-10-01", "2025-12-31", 1_000_000_000),
            row("2026-01-01", "2026-03-31", 1_000_000_000),
            row("2026-04-01", "2026-06-30", 1_000_000_000),
        ]
        facts["us-gaap"]["ResearchAndDevelopmentExpense"] = {"units": {"USD": gaap}}
        # Quarterly GAAP without a fresh annual filing does not replace the extension.
        preferred = extract_fundamentals(facts)
        self.assertEqual(preferred["snapshot"]["rd"], 121_086_000_000)


class RoundSixTests(unittest.TestCase):
    def _investor(self, net_income=80, fcf=-50, ocf=200, revenue_prior=800, revenue_base=640):
        bundle = {
            "revenue": [point("2024-12-31", 800), point("2025-12-31", 1000)],
            "operating_income": [point("2025-12-31", 120)],
            "net_income": [point("2024-12-31", 70), point("2025-12-31", net_income)],
            "equity": [point("2024-12-31", 360), point("2025-12-31", 400)],
            "fcf": [point("2023-12-31", 40), point("2025-12-31", 20)],
            "cash": [point("2025-12-31", 100)],
            "debt": [point("2025-12-31", 300)],
            "eps": [point("2022-12-31", 1), point("2025-12-31", 2)],
            "shares_out": [point("2025-12-31", 10)],
            "snapshot": {
                "end": "2026-06-30",
                "label": "TTM bis 2026-06-30",
                "months": 12,
                "revenue": 1000,
                "revenue_prior": revenue_prior,
                "revenue_prior_prior": revenue_base,
                "operating_income": 120,
                "net_income": net_income,
                "cfo": ocf,
                "fcf": fcf,
                "equity": 400,
                "cash": 100,
                "debt": 300,
                "sourceLabel": "SEC EDGAR companyfacts",
            },
        }
        bars = [{"date": "2026-10-15", "close": 50.0, "high": 50.0}]
        return score_company(CONFIG, bundle, {"bars": bars}, {}, "2026-10-20", {})

    def test_profitable_investor_substitutes_operating_cash_flow(self):
        scored = self._investor()
        growth = metric_map(scored, "C_02")[1]
        self.assertEqual(growth["fcf_cagr_3y"]["status"], "nicht anwendbar")
        self.assertIsNone(growth["fcf_cagr_3y"]["points"])
        finance = metric_map(scored, "C_04")[1]
        self.assertIsNone(finance["roe"]["status"])
        self.assertAlmostEqual(finance["roe"]["raw"], 80 / 380, places=6)
        self.assertEqual(finance["cash_runway_months"]["status"], "nicht anwendbar")
        self.assertIn("(operativer Cashflow, hohe Investitionen)", finance["fcf_margin_3y"]["label"])
        self.assertAlmostEqual(finance["fcf_margin_3y"]["raw"], 0.2, places=6)
        self.assertEqual(finance["fcf_margin_3y"]["points"], 10)
        valuation = metric_map(scored, "C_08")[1]["fcf_yield"]
        self.assertIn("(operativer Cashflow, hohe Investitionen)", valuation["label"])
        self.assertAlmostEqual(valuation["raw"], 200 / 500, places=6)
        self.assertEqual(valuation["points"], 10)
        self.assertIsNone(valuation["status"])

    def test_loss_maker_keeps_the_runway_even_with_operating_cash_flow(self):
        scored = self._investor(net_income=-5)
        finance = metric_map(scored, "C_04")[1]
        self.assertEqual(finance["roe"]["status"], "nicht anwendbar")
        self.assertEqual(finance["cash_runway_months"]["points"], 5.0)
        self.assertEqual(finance["cash_runway_months"]["value"], "24,0 Monate")
        self.assertNotIn("operativer Cashflow", finance["fcf_margin_3y"]["label"])
        valuation = metric_map(scored, "C_08")[1]["fcf_yield"]
        self.assertEqual(valuation["status"], "nicht anwendbar")

    def test_thresholds_and_acceleration_rules_come_from_config(self):
        strict = json.loads(json.dumps(CONFIG))
        strict["investing"]["minOcfMargin"] = 0.5
        scored = score_company(strict, {
            "revenue": [point("2024-12-31", 800), point("2025-12-31", 1000)],
            "net_income": [point("2025-12-31", 80)],
            "equity": [point("2024-12-31", 360), point("2025-12-31", 400)],
            "cash": [point("2025-12-31", 100)],
            "debt": [point("2025-12-31", 300)],
            "snapshot": {
                "end": "2026-06-30", "months": 12, "revenue": 1000,
                "operating_income": 120, "net_income": 80, "cfo": 200, "fcf": -50,
                "equity": 400, "cash": 100, "debt": 300,
            },
        }, {}, {}, "2026-10-20", {})
        finance = metric_map(scored, "C_04")[1]
        self.assertEqual(finance["roe"]["status"], "nicht anwendbar")
        self.assertEqual(finance["cash_runway_months"]["points"], 5.0)

        def acceleration(prior_revenue, prior_base, current=100):
            gap = score_company(CONFIG, {"snapshot": {
                "end": "2026-06-30",
                "label": "TTM bis 2026-06-30",
                "months": 12,
                "revenue": current,
                "revenue_prior": prior_revenue,
                "revenue_prior_prior": prior_base,
            }}, {}, {}, "2026-10-20", {})
            return metric_map(gap, "C_02")[1]["revenue_acceleration"]

        capped = acceleration(20_000_000, 10_000_000, 25_000_000)
        self.assertEqual(capped["raw"], -0.3)
        self.assertEqual(capped["points"], 5.0)
        self.assertIn("±30 Pp", capped["note"])
        self.assertIn("mindestens 5 Punkte", capped["note"])

        tiny = acceleration(5_000_000, 4_000_000, 20_000_000)
        self.assertEqual(tiny["status"], "nicht anwendbar")
        self.assertIsNone(tiny["points"])

        spike = acceleration(100_000_000, 20_000_000, 130_000_000)
        self.assertEqual(spike["status"], "nicht anwendbar")
        self.assertIsNone(spike["points"])

        loose = json.loads(json.dumps(CONFIG))
        loose["acceleration"]["capPp"] = 0.05
        loose["acceleration"]["minCurrentGrowth"] = 9
        narrowed = score_company(loose, {"snapshot": {
            "end": "2026-06-30", "months": 12, "revenue": 25_000_000,
            "revenue_prior": 20_000_000, "revenue_prior_prior": 10_000_000,
        }}, {}, {}, "2026-10-20", {})
        metric = metric_map(narrowed, "C_02")[1]["revenue_acceleration"]
        self.assertEqual(metric["raw"], -0.05)
        self.assertEqual(metric["points"], 2.5)


class ScaleTenTests(unittest.TestCase):
    def test_ring_uses_a_scale_of_ten_and_keeps_forty_eight_ticks(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("var n = 48, pct = score == null ? 0 : Math.max(0, Math.min(10, Number(score)));", html)
        self.assertIn("Math.round(pct / 10 * n)", html)
        self.assertIn("von 10", html)
        self.assertIn("<small>/ 10</small>", html)
        self.assertIn("<small>/10</small>", html)
        self.assertIn("SCORE 0–10", html)
        self.assertIn("MAX 10", html)
        self.assertIn("≥ 7", html)
        self.assertIn("5–6,9", html)
        self.assertIn("&lt; 5", html)
        self.assertNotIn("von 100", html)
        self.assertNotIn("/ 100", html)
        self.assertNotIn("SCORE 0–100", html)
        self.assertNotIn("MAX 100", html)
        self.assertNotIn("/100", html)
        self.assertNotIn("≥ 70", html)
        self.assertNotIn("50–69", html)
        self.assertNotIn("&lt; 50", html)
        self.assertNotIn("ab: 70", html)
        self.assertNotIn("ab: 50", html)
        self.assertIn("ab: 7", html)
        self.assertIn("ab: 5", html)
        self.assertNotIn("score >= 70", html)
        self.assertNotIn("score >= 50", html)
        self.assertIn("doc.score >= 7", html)
        self.assertIn("doc.score >= 5", html)
        self.assertNotIn("Gewicht", html)

    def test_company_description_renders_and_omits_missing_facts(self):
        import subprocess
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("function companyBlock", html)
        self.assertIn('fetch("config/profiles.json")', html)
        start = html.index("  function ring(")
        end = html.index("  function showDetail(")
        profiles = json.loads((ROOT / "config" / "profiles.json").read_text(encoding="utf-8"))
        tickers = json.loads((ROOT / "config" / "tickers.json").read_text(encoding="utf-8"))
        self.assertEqual([item["ticker"] for item in tickers], list(profiles))
        for ticker, entry in profiles.items():
            sentences = [part for part in entry["text"].split(".") if part.strip()]
            self.assertGreaterEqual(len(sentences), 2, ticker)
            self.assertLessEqual(len(sentences), 3, ticker)
            self.assertIsNone(__import__("re").search(r"\d", entry["text"]), ticker)
            self.assertTrue(entry["segments"], ticker)
            self.assertTrue(all(isinstance(part, str) and part for part in entry["segments"]))
        self.assertEqual(
            profiles["AMZN"]["text"],
            "Amazon betreibt den weltweit größten Online-Marktplatz und mit AWS den führenden Cloud-Anbieter. Daneben verdient Amazon an Werbung, Prime-Abos und eigenen Geräten wie Echo und Kindle.",
        )
        self.assertEqual(profiles["AMZN"]["segments"], ["Nordamerika", "International", "AWS"])
        iren = profiles["IREN"]["text"]
        self.assertIn("Rechenzentren", iren)
        self.assertIn("KI-Cloud", iren)
        script = """
        function esc(s) {
          return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
          });
        }
        function n1(x) {
          if (x == null || Number.isNaN(Number(x))) return "–";
          return (Math.round(Number(x) * 10) / 10).toFixed(1).replace(".", ",");
        }
        var CFG = {scoreName: "Aktien-Score"};
        var profiles = {AMZN: {text: "TEXT & Co.", segments: ["Nordamerika", "International", "AWS"]}};
        """ + html[start:end] + """
        var line = factLine(
          {hq: "Seattle, USA", employees: 1551000, marketCap: 2830000000000, currency: "USD"},
          ["Nordamerika", "International", "AWS"]
        );
        if (line !== "Sitz Seattle, USA · Mitarbeiter 1.551.000 · Börsenwert 2,83 Bio. $ · Segmente Nordamerika, International, AWS") {
          throw new Error("line " + line);
        }
        var partial = factLine({hq: "Kista, Schweden"}, ["Drahtlos", "Photonik"]);
        if (partial !== "Sitz Kista, Schweden · Segmente Drahtlos, Photonik") throw new Error("partial " + partial);
        var block = companyBlock({
          ticker: "AMZN",
          profile: {hq: "Seattle, USA", employees: 1551000, marketCap: 2830000000000, currency: "USD"}
        });
        if (block.indexOf("TEXT &amp; Co.") < 0) throw new Error("text " + block);
        if (block.indexOf("2,83 Bio. $") < 0) throw new Error("cap " + block);
        function ons(markup, tone) {
          var needle = 'stroke="var(--' + tone + ')"';
          var count = 0;
          var from = 0;
          while (markup.indexOf(needle, from) >= 0) {
            count += 1;
            from = markup.indexOf(needle, from) + needle.length;
          }
          return count;
        }
        var gauge = ring(7.6, "gruen");
        var lines = (gauge.match(/<line /g) || []).length;
        if (lines !== 48) throw new Error("lines " + lines);
        if (ons(gauge, "ok") !== Math.round(7.6 / 10 * 48)) throw new Error("on " + ons(gauge, "ok"));
        if (gauge.indexOf("7,6") < 0 || gauge.indexOf("von 10") < 0 || gauge.indexOf("/ 10") < 0) throw new Error(gauge);
        if (gauge.indexOf("von 100") >= 0 || gauge.indexOf("/ 100") >= 0) throw new Error("hundred");
        var full = ring(10, "gruen");
        if (ons(full, "ok") !== 48) throw new Error("full " + ons(full, "ok"));
        """
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        if result.returncode != 0:
            self.fail(result.stderr or result.stdout)

    def test_profile_facts_come_from_yahoo_without_filling_gaps(self):
        from scoring.marketdata import YAHOO_MODULES, profile_from_yahoo
        self.assertIn("assetProfile", YAHOO_MODULES)
        self.assertIn("summaryDetail", YAHOO_MODULES)
        self.assertIn("price", YAHOO_MODULES.split(","))
        summary = {
            "assetProfile": {
                "city": "Seattle",
                "country": "United States",
                "fullTimeEmployees": 1551000,
            },
            "price": {"currency": "USD", "marketCap": {"raw": 2_830_000_000_000}},
        }
        profile = profile_from_yahoo(summary, "2026-10-21")
        self.assertEqual(profile["hq"], "Seattle, USA")
        self.assertEqual(profile["employees"], 1551000)
        self.assertEqual(profile["marketCap"], 2_830_000_000_000)
        self.assertEqual(profile["currency"], "USD")
        self.assertEqual(profile["source"], "Yahoo Finance quoteSummary")
        self.assertEqual(profile["fetchedAt"], "2026-10-21")
        partial = profile_from_yahoo({"assetProfile": {"country": "Sweden"}}, "2026-10-21")
        self.assertEqual(partial["hq"], "Schweden")
        self.assertNotIn("employees", partial)
        self.assertNotIn("marketCap", partial)
        self.assertNotIn("currency", partial)
        same = profile_from_yahoo({"assetProfile": {"city": "Singapore", "country": "Singapore"}}, "2026-10-21")
        self.assertEqual(same["hq"], "Singapur")
        self.assertIsNone(profile_from_yahoo({"assetProfile": {}}, "2026-10-21"))
        self.assertIsNone(profile_from_yahoo(None, "2026-10-21"))
        scored = score_company(CONFIG, {}, {}, {}, "2026-10-21", {})
        document = assemble(
            "AMZN", "Amazon", None, scored, CONFIG, None,
            {"hq": "Seattle, USA", "employees": 10, "source": "Yahoo Finance quoteSummary", "fetchedAt": "2026-10-21"},
        )
        self.assertEqual(document["profile"]["hq"], "Seattle, USA")
        self.assertLess(list(document).index("price"), list(document).index("profile"))
        bare = assemble("AMZN", "Amazon", None, scored, CONFIG)
        self.assertNotIn("profile", bare)


class LegalPageTests(unittest.TestCase):
    def test_footer_links_sit_beside_the_disclaimer(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        footer = html.split("<footer", 1)[1].split("</footer>", 1)[0]
        self.assertIn("KEINE ANLAGEBERATUNG", footer)
        self.assertIn('href="impressum.html"', footer)
        self.assertIn('href="datenschutz.html"', footer)
        self.assertIn('class="foot-legal"', footer)
        self.assertNotIn("function disclaimer(", html)
        self.assertNotIn("DISCLAIMER", html)

    def test_disclaimer_renders_on_overview_and_analysis(self):
        import subprocess
        disclaimer = (
            "Keine Anlageberatung und keine Kauf- oder Verkaufsempfehlung. "
            "Die Scores sind eigene, regelbasierte Einschätzungen: öffentliche Unternehmens- "
            "und Marktdaten (u. a. SEC, Yahoo Finance, Nasdaq) werden nach festen Kriterien "
            "in Punkte von 0 bis 10 umgerechnet und gemittelt. Sie können unvollständig oder "
            "veraltet sein und ersetzen keine eigene Prüfung."
        )
        short = "Eigene, regelbasierte Aktien-Scores aus öffentlichen Unternehmensdaten. Keine Anlageberatung."
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        footer = html.split("<footer", 1)[1].split("</footer>", 1)[0]
        self.assertIn(disclaimer, footer)
        self.assertIn("discl-big compact", footer)
        self.assertIn(f'content="{short}"', html)
        self.assertEqual(html.count(f'content="{short}"'), 2)
        self.assertNotIn("spiegeln keine tatsächlichen Werte wider", html)
        for name in ("impressum.html", "datenschutz.html"):
            legal = (ROOT / name).read_text(encoding="utf-8")
            legal_footer = legal.split("<footer", 1)[1].split("</footer>", 1)[0]
            self.assertIn(disclaimer, legal_footer, name)
            self.assertIn("discl-big compact", legal_footer, name)
            self.assertNotIn("spiegeln keine tatsächlichen Werte wider", legal, name)
        script = r"""
        const fs = require("fs");
        const path = require("path");
        const root = process.cwd();
        const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
        const disclaimer = process.env.DISCLAIMER;
        const footerSource = html.split("<footer")[1].split("</footer>")[0];
        if (!footerSource.includes(disclaimer)) throw new Error("footer source");
        function make(tag) {
          return {
            tagName: String(tag || "").toUpperCase(),
            className: "",
            id: "",
            href: "",
            textContent: "",
            innerHTML: "",
            style: {},
            children: [],
            append() { this.children.push(...arguments); },
            replaceChildren() { this.children = Array.from(arguments); },
            addEventListener() {},
            setAttribute(key, value) { this[key] = value; },
            getAttribute(key) { return this[key] || null; },
            querySelector() { return null; },
            closest() { return null; },
            scrollIntoView() {},
          };
        }
        const app = make("main");
        const footer = make("footer");
        footer.className = "footer";
        footer.textContent = footerSource.replace(/<[^>]+>/g, "");
        const listeners = {};
        global.document = {
          title: "",
          documentElement: {
            getAttribute() { return "light"; },
            setAttribute() {},
          },
          getElementById(id) {
            if (id === "app") return app;
            if (id === "theme-tg") return make("button");
            return null;
          },
          createElement: make,
          createTextNode(value) { return { textContent: String(value) }; },
          querySelector(selector) {
            if (selector === "footer" || selector === ".discl-big") return footer;
            if (selector === 'meta[name="theme-color"]') return { content: "" };
            return null;
          },
          addEventListener() {},
        };
        global.localStorage = { getItem() { return null; }, setItem() {} };
        global.location = { search: "", hash: "", href: "http://local/" };
        global.window = {
          addEventListener(type, fn) { listeners[type] = fn; },
          scrollTo() {},
        };
        global.fetch = async (url) => {
          const file = path.join(root, url);
          return { ok: true, json: async () => JSON.parse(fs.readFileSync(file, "utf8")) };
        };
        const marker = "(function () {\n  \"use strict\";";
        const source = html.slice(html.indexOf(marker), html.lastIndexOf("</script>"));
        if (source.indexOf(marker) !== 0) throw new Error("script marker missing");
        eval(source);
        function pageText() {
          return footer.textContent + "\n" + app.innerHTML + app.children.map((child) => child.className).join(" ");
        }
        setTimeout(() => {
          try {
            if (!pageText().includes(disclaimer)) throw new Error("overview missing disclaimer");
            if (document.title !== "sidequest") throw new Error("overview title " + document.title);
            const tiles = app.children.filter((child) => child.className === "tiles")[0];
            if (!tiles || tiles.children.length < 13) throw new Error("overview tiles " + (tiles && tiles.children.length));
            location.hash = "#/AMZN";
            listeners.hashchange();
            if (app.innerHTML.indexOf("AMZN") < 0 || app.innerHTML.indexOf("Amazon") < 0) {
              throw new Error("analysis not rendered");
            }
            if (!pageText().includes(disclaimer)) throw new Error("analysis missing disclaimer");
            if (app.innerHTML.includes("spiegeln keine tatsächlichen Werte wider")) {
              throw new Error("old sentence in analysis");
            }
            console.log("ok");
          } catch (err) {
            console.error(err && err.stack || err);
            process.exit(1);
          }
        }, 300);
        """
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "DISCLAIMER": disclaimer},
        )
        if result.returncode != 0:
            self.fail(result.stderr or result.stdout)

    def test_impressum_names_the_provider(self):
        html = (ROOT / "impressum.html").read_text(encoding="utf-8")
        self.assertIn("Angaben gemäß § 5 DDG", html)
        self.assertIn("Maximilian Hillert", html)
        self.assertIn("Illgnerstraße 35", html)
        self.assertIn("04442 Zwenkau", html)
        self.assertIn("maximilianhillert@mail.grokbot.com", html)
        self.assertIn("Verantwortlich für den Inhalt nach § 18 Abs. 2 MStV", html)
        self.assertIn("keine Anlageberatung", html)
        self.assertIn("keine Gewähr", html)
        self.assertIn("Soweit auf fremde Websites verlinkt wird", html)
        self.assertIn('href="datenschutz.html"', html)
        self.assertIn("KEINE ANLAGEBERATUNG", html)

    def test_privacy_matches_the_runtime(self):
        html = (ROOT / "datenschutz.html").read_text(encoding="utf-8")
        page = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("Stand: 21. Oktober 2026", html)
        self.assertIn("Art. 6 Abs. 1 lit. f DSGVO", html)
        self.assertIn("Vercel Inc.", html)
        self.assertIn("EU-US Data Privacy Framework", html)
        self.assertIn("keine Cookies", html)
        self.assertIn("ac-theme", html)
        self.assertIn("sessionStorage wird nicht verwendet", html)
        self.assertIn("Google Fonts", html)
        self.assertIn("nicht eingebunden", html)
        self.assertIn("Speed Insights", html)
        self.assertIn("Der Browser ruft diese Dienste nicht auf", html)
        self.assertIn("scripts/build_scores.py", html)
        self.assertIn("Art. 77 DSGVO", html)
        self.assertIn("Sächsische Datenschutz- und Transparenzbeauftragte", html)
        self.assertIn('href="impressum.html"', html)
        for banned in ("googleapis", "cdn.jsdelivr", "va.vercel-scripts", "googletagmanager", "plausible"):
            self.assertNotIn(banned, html)
            self.assertNotIn(banned, page)
        self.assertIn('url("fonts/space-grotesk.woff2")', page)
        self.assertIn('localStorage.getItem("ac-theme")', page)
        self.assertIn('localStorage.setItem("ac-theme", t)', page)
        self.assertNotIn("sessionStorage", page)
        self.assertNotIn("document.cookie", page)


class DataGapTests(unittest.TestCase):
    def test_negative_base_is_not_applicable_and_notes_are_not_repeated(self):
        bundle = {
            "revenue": [point("2022-12-31", -5), point("2025-12-31", 20)],
            "eps": [point("2022-12-31", -1), point("2025-12-31", -2)],
            "fcf": [point("2022-12-31", 4), point("2025-12-31", -3)],
            "snapshot": {
                "end": "2026-06-30",
                "label": "TTM bis 2026-06-30",
                "months": 12,
                "revenue": 20,
                "revenue_prior": 10,
                "gross_profit": 8,
                "operating_income": 4,
                "cfo": 3,
                "sourceLabel": "SEC EDGAR companyfacts",
            },
        }
        scored = score_company(CONFIG, bundle, {}, {}, "2026-10-21", {})
        growth = metric_map(scored, "C_02")[1]
        for metric_id in ("revenue_cagr_3y", "eps_cagr_3y", "fcf_cagr_3y"):
            self.assertEqual(growth[metric_id]["status"], "nicht anwendbar")
            self.assertEqual(growth[metric_id]["statusReason"], "Verlust bzw. negativer Wert in der Basisperiode")
            self.assertIsNone(growth[metric_id]["points"])
        quality = metric_map(scored, "C_01")[0]["dataQuality"]
        self.assertEqual(quality.count("TTM bis 2026-06-30"), 1)
        self.assertNotIn("TTM bis 2026-06-30 TTM", quality)

    def test_roic_loss_statutory_tax_and_explicit_zero_debt(self):
        loss = {
            "revenue": [point("2025-12-31", 100)],
            "operating_income": [point("2024-12-31", 10), point("2025-12-31", 12)],
            "pretax": [point("2024-12-31", 8), point("2025-12-31", -1)],
            "tax": [point("2024-12-31", 2), point("2025-12-31", 0)],
            "equity": [point("2024-12-31", 100), point("2025-12-31", 100)],
            "debt": [point("2024-12-31", 0), point("2025-12-31", 0)],
            "cash": [point("2024-12-31", 0), point("2025-12-31", 0)],
        }
        loss["moat"] = _industry_moat(wacc=0.0)
        scored = score_company(CONFIG, loss, {}, {}, "2026-10-21", {})
        roic = metric_map(scored, "C_01")[1]["roic_spread"]
        self.assertIsNone(roic["status"])
        self.assertEqual(roic["points"], 0.0)
        self.assertIn("Bruttomarge unter Branchenniveau", roic["note"])

        statutory = {
            "revenue": [point("2025-12-31", 100)],
            "operating_income": [point("2024-12-31", 20), point("2025-12-31", 20)],
            "pretax": [point("2024-12-31", 10), point("2025-12-31", 10)],
            "tax": [point("2024-12-31", 20), point("2025-12-31", 20)],
            "equity": [point("2024-12-31", 100), point("2025-12-31", 100)],
            "debt": [point("2024-12-31", 0), point("2025-12-31", 0)],
            "cash": [point("2024-12-31", 0), point("2025-12-31", 0)],
        }
        statutory["moat"] = _industry_moat(wacc=0.0)
        taxed = score_company(CONFIG, statutory, {}, {}, "2026-10-21", {})
        roic_tax = metric_map(taxed, "C_01")[1]["roic_spread"]
        self.assertAlmostEqual(roic_tax["raw"], 0.158, places=4)
        self.assertIn("21 %", roic_tax["note"])
        self.assertIsNone(roic_tax["status"])

        stale_debt = {
            "revenue": [point("2025-12-31", 100)],
            "operating_income": [point("2024-12-31", 20), point("2025-12-31", 20)],
            "pretax": [point("2024-12-31", 10), point("2025-12-31", 10)],
            "tax": [point("2024-12-31", 2), point("2025-12-31", 2)],
            "equity": [point("2024-12-31", 80), point("2025-12-31", 80)],
            "debt": [point("2023-12-31", 40)],
            "cash": [point("2024-12-31", 0), point("2025-12-31", 0)],
            "snapshot": {"debt_explicit_zero": True, "debt": 0, "debt_end": "2025-12-31"},
        }
        stale_debt["moat"] = _industry_moat(wacc=0.0)
        filled = score_company(CONFIG, stale_debt, {}, {}, "2026-10-21", {})
        roic_zero = metric_map(filled, "C_01")[1]["roic_spread"]
        self.assertIsNone(roic_zero["status"])
        self.assertAlmostEqual(roic_zero["raw"], 0.2, places=4)

    def test_negative_ebitda_and_net_debt_with_positive_cash_flow_are_labeled(self):
        bundle = {
            "revenue": [point("2024-12-31", 80), point("2025-12-31", 100)],
            "operating_income": [point("2025-12-31", -5)],
            "net_income": [point("2025-12-31", -4)],
            "da": [point("2025-12-31", 1)],
            "debt": [point("2025-12-31", 30)],
            "cash": [point("2025-12-31", 10)],
            "fcf": [point("2025-12-31", 6)],
            "equity": [point("2024-12-31", 40), point("2025-12-31", 36)],
            "shares_out": [point("2025-12-31", 10)],
        }
        bars = [{"date": "2026-10-20", "close": 20.0, "high": 20.0}]
        scored = score_company(CONFIG, bundle, {"bars": bars}, {}, "2026-10-21", {})
        valuation = metric_map(scored, "C_08")[1]["ev_to_ebitda"]
        self.assertEqual(valuation["status"], "nicht anwendbar")
        self.assertEqual(valuation["statusReason"], "EBITDA negativ")
        finance = metric_map(scored, "C_04")[1]["net_debt_to_ebitda"]
        self.assertEqual(finance["status"], "nicht anwendbar")
        self.assertEqual(finance["statusReason"], "EBITDA negativ")

        net_cash = score_company(CONFIG, {
            "revenue": [point("2025-12-31", 100)],
            "operating_income": [point("2025-12-31", -5)],
            "da": [point("2025-12-31", 1)],
            "debt": [point("2025-12-31", 10)],
            "cash": [point("2025-12-31", 40)],
            "fcf": [point("2025-12-31", 6)],
        }, {}, {}, "2026-10-21", {})
        kept = metric_map(net_cash, "C_04")[1]["net_debt_to_ebitda"]
        self.assertEqual(kept["value"], "Netto-Cash")
        self.assertEqual(kept["points"], 10)
        self.assertIsNone(kept["status"])

    def test_short_ipo_history_and_missing_research_disclosure(self):
        bundle = {
            "revenue": [point("2025-12-31", 10)],
            "operating_income": [point("2025-12-31", 1)],
            "shares_diluted": [point("2025-12-31", 5)],
            "fcf": [],
            "_history_start": "2025-06-01",
            "_history_end": "2025-12-31",
            "snapshot": {"revenue": 10, "end": "2025-12-31", "sourceLabel": "SEC EDGAR companyfacts"},
        }
        scored = score_company(CONFIG, bundle, {}, {}, "2026-10-21", {}, {"sec": True})
        self.assertEqual(metric_map(scored, "C_01")[1]["roic_spread"]["status"], "zu kurze Historie")
        self.assertEqual(metric_map(scored, "C_04")[1]["dilution_3y"]["status"], "zu kurze Historie")
        self.assertEqual(metric_map(scored, "C_04")[1]["fcf_margin_3y"]["status"], "zu kurze Historie")
        research = metric_map(scored, "C_03")[1]["rd_vs_industry"]
        self.assertEqual(research["status"], "nicht ausgewiesen")
        self.assertIsNone(research["points"])
        swedish = score_company(CONFIG, {"revenue": [point("2025-12-31", 10)]}, {}, {}, "2026-10-21", {}, {"sec": False})
        self.assertEqual(metric_map(swedish, "C_03")[1]["rd_vs_industry"]["status"], "n/a")

    def test_merged_revenue_convertible_debt_depreciation_and_equity(self):
        def row(start, end, val, form="10-K"):
            return {"start": start, "end": end, "val": val, "form": form, "fp": "FY", "filed": end}

        facts = {"us-gaap": {
            "SalesRevenueNet": {"units": {"USD": [
                row("2019-01-01", "2019-12-31", 100),
                row("2020-01-01", "2020-12-31", 110),
            ]}},
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                row("2023-01-01", "2023-12-31", 140),
                row("2024-01-01", "2024-12-31", 160),
                row("2025-01-01", "2025-12-31", 200),
            ]}},
            "ConvertibleDebtNoncurrent": {"units": {"USD": [row("2025-01-01", "2025-12-31", 30)]}},
            "ConvertibleDebtCurrent": {"units": {"USD": [row("2025-01-01", "2025-12-31", 5)]}},
            "Depreciation": {"units": {"USD": [row("2025-01-01", "2025-12-31", 7)]}},
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {
                "units": {"USD": [row("2025-01-01", "2025-12-31", 90)]},
            },
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations": {
                "units": {"USD": [row("2025-01-01", "2025-12-31", 15)]},
            },
        }}
        bundle = extract_fundamentals(facts)
        self.assertEqual([point["end"] for point in bundle["revenue"]], [
            "2019-12-31", "2020-12-31", "2023-12-31", "2024-12-31", "2025-12-31",
        ])
        self.assertEqual(bundle["debt"][-1]["val"], 35)
        self.assertEqual(bundle["da"][-1]["val"], 7)
        self.assertEqual(bundle["equity"][-1]["val"], 90)
        self.assertEqual(bundle["cfo"][-1]["val"], 15)

    def test_prior_prior_revenue_comes_from_the_annual_series(self):
        scored = score_company(CONFIG, {
            "revenue": [
                point("2023-12-31", 50_000_000),
                point("2024-12-31", 80_000_000),
                point("2025-12-31", 100_000_000),
            ],
            "snapshot": {
                "end": "2025-12-31",
                "label": "Geschäftsjahr 2025",
                "months": 12,
                "revenue": 100_000_000,
                "revenue_prior": 80_000_000,
                "sourceLabel": "SEC EDGAR companyfacts",
            },
        }, {}, {}, "2026-10-21", {})
        acceleration = metric_map(scored, "C_02")[1]["revenue_acceleration"]
        self.assertIsNone(acceleration["status"])
        self.assertEqual(acceleration["raw"], -0.3)
        self.assertEqual(acceleration["points"], 5.0)

    def test_nasdaq_ttm_fills_depreciation_and_research(self):
        from scoring.marketdata import refresh_stale_fields

        nasdaq = {"incomeStatementTable": {
            "headers": {
                "value2": "6/30/2026",
                "value3": "3/31/2026",
                "value4": "12/31/2025",
                "value5": "9/30/2025",
            },
            "rows": [
                {"value1": "Depreciation", "value2": "$10", "value3": "$10", "value4": "$10", "value5": "$10"},
                {"value1": "Research and Development", "value2": "$4", "value3": "$4", "value4": "$4", "value5": "$4"},
            ],
        }}
        snapshot = refresh_stale_fields(
            {"end": "2026-06-30", "revenue": 100},
            None, nasdaq, "https://api.nasdaq.com/api/company/IBRX/financials?frequency=2", "2026-10-21",
        )
        # Nasdaq prints statement lines in thousands, so $10 is $10,000.
        self.assertEqual(snapshot["da"], 40_000)
        self.assertEqual(snapshot["rd"], 16_000)
        self.assertIn("Depreciation", snapshot["provenance"]["da"]["note"])
        self.assertIn("Research and Development", snapshot["provenance"]["rd"]["note"])

    def test_extension_research_is_preferred_only_when_gaap_annual_is_stale(self):
        from scoring.edgar import RD_EXTENSION_NAMES
        from scoring.metrics import has_recent_annual, research_concepts

        self.assertIn("TechnologyAndDevelopmentExpense", RD_EXTENSION_NAMES)
        fresh_end = (date.today() - timedelta(days=40)).isoformat()
        stale_end = (date.today() - timedelta(days=800)).isoformat()
        fresh = {"us-gaap": {"ResearchAndDevelopmentExpense": {"units": {"USD": [
            {"end": fresh_end, "val": 5, "form": "10-K", "fp": "FY", "filed": fresh_end},
        ]}}}}
        stale = {"us-gaap": {"ResearchAndDevelopmentExpense": {"units": {"USD": [
            {"end": stale_end, "val": 5, "form": "10-K", "fp": "FY", "filed": stale_end},
        ]}}}}
        self.assertTrue(has_recent_annual(fresh, ("ResearchAndDevelopmentExpense",)))
        self.assertFalse(has_recent_annual(stale, ("ResearchAndDevelopmentExpense",)))
        fresh_names = research_concepts(fresh)
        stale_names = research_concepts(stale)
        self.assertLess(
            fresh_names.index("ResearchAndDevelopmentExpense"),
            fresh_names.index("TechnologyAndDevelopmentExpense"),
        )
        self.assertLess(
            stale_names.index("TechnologyAndDevelopmentExpense"),
            stale_names.index("ResearchAndDevelopmentExpense"),
        )
        source = (ROOT / "scoring" / "marketdata.py").read_text(encoding="utf-8")
        body = source.split("def _nasdaq_json", 1)[1].split("def load_nasdaq", 1)[0]
        self.assertIn("BROWSER_AGENT", body)
        self.assertNotIn("USER_AGENT", body)


if __name__ == "__main__":
    unittest.main()
