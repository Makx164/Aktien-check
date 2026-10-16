import json
import unittest
from datetime import date, timedelta
from pathlib import Path

from scoring.edgar import parse_form4
from scoring.metrics import annual_series, score_company
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
        self.assertEqual(traffic_light(70, 70, 50), "green")
        self.assertEqual(traffic_light(69.9, 70, 50), "orange")
        self.assertEqual(traffic_light(50, 70, 50), "orange")
        self.assertEqual(traffic_light(49.9, 70, 50), "red")
        self.assertIsNone(traffic_light(None, 70, 50))

    def test_half_weight_and_missing_criteria(self):
        total = weighted_total([(10, 1), (0, 0.5)])
        self.assertAlmostEqual(total, (10 / 1.5) * 10)
        only = weighted_total([(8, 0.5), (None, 1)])
        self.assertAlmostEqual(only, 80)
        self.assertIsNone(weighted_total([(None, 1)]))

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
        self.assertEqual(parsed["buys"], 1)
        self.assertEqual(parsed["sells"], 1)
        planned = xml.replace("<aff10b5One>0</aff10b5One>", "<aff10b5One>1</aff10b5One>")
        kept = parse_form4(planned.encode())
        self.assertEqual(kept["buy"], 50)
        self.assertEqual(kept["sell"], 40)


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
        self.assertEqual(metrics["above_sma_200"]["value"], "ja")
        self.assertEqual(metrics["above_sma_200"]["points"], 10)
        self.assertEqual(metrics["distance_52w_high"]["points"], 1.1)
        self.assertEqual(metrics["excess_return_12_1"]["sourceUrl"], prices["sourceUrl"])
        self.assertEqual(metrics["excess_return_12_1"]["fetchedAt"], "2026-10-16")
        self.assertEqual(metrics["excess_return_12_1"]["observedAt"], bars[-1]["date"])
        self.assertEqual(by_id["C_07"]["weight"], 0.5)
        self.assertIsNone(by_id["C_06"]["score"])

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
        metrics = {m["id"]: m for c in scored["criteria"] for m in c["metrics"]}
        self.assertIsNone(metrics["rd_intensity"]["points"])
        self.assertIsNone(metrics["rd_intensity"]["value"])
        self.assertEqual(metrics["rd_intensity"]["value"], None)
        yoy = metrics["revenue_yoy"]
        self.assertEqual(yoy["raw"], 0.1)
        self.assertNotEqual(yoy["points"], 0)

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
            "revenue": [point("2024-12-31", 100), point("2025-12-31", 110)],
            "gross_profit": [point("2025-12-31", 66)],
        }
        scored = score_company(CONFIG, bundle, {}, {}, "2026-10-16", {})
        by_id = {item["id"]: item for item in scored["criteria"]}
        metrics = {metric["id"]: metric for metric in by_id["C_01"]["metrics"]}
        self.assertEqual(metrics["gross_margin"]["points"], 10)
        self.assertIsNone(metrics["gross_margin"]["status"])
        self.assertIsNone(metrics["roic_2y"]["points"])
        self.assertNotEqual(metrics["roic_2y"]["points"], 0)
        self.assertEqual(metrics["roic_2y"]["status"], "wenig Daten")
        self.assertEqual(by_id["C_01"]["score"], 10)
        self.assertIsNone(by_id["C_01"]["status"])
        self.assertEqual(by_id["C_06"]["status"], "wenig Daten")
        self.assertIsNone(by_id["C_06"]["score"])

        zero = {
            "revenue": [point("2025-12-31", 100)],
            "gross_profit": [point("2025-12-31", 20)],
        }
        scored_zero = score_company(CONFIG, zero, {}, {}, "2026-10-16", {})
        gross = {m["id"]: m for c in scored_zero["criteria"] if c["id"] == "C_01" for m in c["metrics"]}
        self.assertEqual(gross["gross_margin"]["points"], 0.0)
        self.assertIsNone(gross["gross_margin"]["status"])
        self.assertEqual(gross["fcf_margin"]["status"], "wenig Daten")
        self.assertIsNone(gross["fcf_margin"]["points"])

    def test_weights_are_renormalized_and_thin_coverage_hides_the_number(self):
        full = [
            {"id": "C_01", "score": 10, "weight": 1},
            {"id": "C_02", "score": 10, "weight": 1},
            {"id": "C_07", "score": 10, "weight": 0.5},
            {"id": "C_06", "score": None, "weight": 1},
        ]
        decision = coverage_decision(full, 3, "wenig Daten")
        self.assertEqual(decision["score"], 100)
        self.assertIsNone(decision["status"])
        self.assertEqual(decision["criteriaWithData"], 3)
        self.assertIn("C_06", decision["note"])
        self.assertIn("nur Kriterien mit Daten", decision["note"])
        diluted = weighted_total([(10, 1), (10, 1), (10, 0.5), (0, 1)])
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
            "revenue": [point("2024-12-31", 100), point("2025-12-31", 110)],
            "gross_profit": [point("2025-12-31", 66)],
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
        self.assertTrue(all(item["status"] == "wenig Daten" for item in document["criteria"] if item["score"] is None))


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
        self.assertEqual(metrics["insider_ownership"]["note"], "3–10 % → 3 von 5")
        self.assertEqual(metrics["institutional_ownership"]["points"], 2)
        self.assertEqual(metrics["insider_market_balance"]["points"], 0)
        self.assertIn("Netto-Verkäufe", metrics["insider_market_balance"]["value"])
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
        self.assertIn("Ich halte diese Aktien selbst.", html)
        self.assertIn("Keine Anlageberatung.", html)
        self.assertIn('href = "#/" + score.ticker', html)
        self.assertIn("hashchange", html)
        self.assertIn("Alle Positionen", html)


if __name__ == "__main__":
    unittest.main()
