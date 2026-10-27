"""C_01 and C_03 ramps, gaps and the SPCX customer-concentration rule."""

import unittest
from datetime import date

from scoring.filings import customer_concentration, parse_instance, segment_weights
from scoring.moat import (
    BUILD_PHASE,
    NO_CUSTOMER_NOTE,
    concentration_points,
    margin_trend,
    median_capped,
    rd_factor_points,
)
from scoring.model import linear_points, round1
from tests.test_scoring import CONFIG, _industry_moat, metric_map, point
from scoring.metrics import score_company


def _year(year, revenue, gross, operating=20, pretax=20):
    end = f"{year}-12-31"
    row = {
        "revenue": point(end, revenue),
        "gross_profit": point(end, gross),
        "operating_income": point(end, operating),
        "pretax": point(end, pretax),
        "tax": point(end, pretax * 0.21 if pretax else 0),
        "equity": point(end, 100),
        "debt": point(end, 0),
        "cash": point(end, 0),
    }
    return row


def _bundle(rows, moat=None):
    bundle = {}
    for row in rows:
        for key, value in row.items():
            bundle.setdefault(key, []).append(value)
    bundle["moat"] = moat if moat is not None else _industry_moat()
    return bundle


class RampTests(unittest.TestCase):
    def test_industry_growth_probe_rounds_to_the_brief(self):
        spec = CONFIG["moat"]["c03m1"]
        samples = ((0.196, 7.5), (0.184, 7.0), (0.117, 4.0), (0.0415, 0.5))
        for rate, expected in samples:
            self.assertEqual(round1(linear_points(rate, spec["zeroAt"], spec["tenAt"])), expected)

    def test_headroom_logs_are_not_rewritten(self):
        spec = CONFIG["moat"]["c03m4"]
        for value, expected in ((2.2, 6.8), (1.2, 2.8), (3.1, 10.0)):
            self.assertEqual(round1(linear_points(value, spec["zeroAt"], spec["tenAt"])), expected)

    def test_growth_is_capped_before_the_weight(self):
        self.assertEqual(median_capped([0.10, 0.515, 0.20], -0.05, 0.30), 0.20)
        self.assertEqual(median_capped([0.40, 0.50, 0.60], -0.05, 0.30), 0.30)
        self.assertEqual(median_capped([-0.20, -0.10, -0.40], -0.05, 0.30), -0.05)

    def test_customer_bands_and_the_spcx_quarter(self):
        bands = CONFIG["moat"]["m4a"]
        self.assertEqual(concentration_points(0.099, bands), 8)
        self.assertEqual(concentration_points(0.10, bands), 6)
        self.assertEqual(concentration_points(0.15, bands), 6)
        self.assertEqual(concentration_points(0.25, bands), 4)
        self.assertEqual(concentration_points(0.345, bands), 2)
        self.assertEqual(concentration_points(0.40, bands), 2)
        self.assertEqual(concentration_points(0.41, bands), 0)

    def test_research_factor_is_piecewise(self):
        rules = CONFIG["moat"]["c03m3"]
        self.assertEqual(rd_factor_points(0, rules), 0)
        self.assertEqual(rd_factor_points(1, rules), 6)
        self.assertEqual(rd_factor_points(2, rules), 10)
        self.assertEqual(rd_factor_points(3, rules), 10)
        self.assertAlmostEqual(rd_factor_points(0.5, rules), 3.0)

    def test_flat_margin_trend_weights_stability_and_slope(self):
        series = [{"end": f"{year}-12-31", "val": 0.50} for year in (2021, 2022, 2023, 2024, 2025)]
        fitted = margin_trend(series, 5, "population")
        self.assertAlmostEqual(fitted["stdev"], 0.0, places=6)
        self.assertAlmostEqual(fitted["slope"], 0.0, places=6)
        spec = CONFIG["moat"]["m2"]
        stability = linear_points(fitted["stdev"], spec["stabilityZero"], spec["stabilityTen"])
        trend = linear_points(fitted["slope"], spec["trendZero"], spec["trendTen"])
        score = spec["stabilityWeight"] * stability + spec["trendWeight"] * trend
        self.assertEqual(round1(score), 8.0)


class RuleTests(unittest.TestCase):
    def test_floor_lifts_a_high_margin_that_only_matches_the_industry(self):
        rows = [_year(year, 200_000_000, 144_000_000) for year in (2023, 2024, 2025)]
        moat = _industry_moat(gross_margin=0.72)
        scored = score_company(CONFIG, _bundle(rows, moat), {}, {}, "2026-10-27", {})
        metric = metric_map(scored, "C_01")[1]["gm_vs_industry"]
        self.assertEqual(metric["points"], 8.0)
        self.assertIsNone(metric["status"])

    def test_one_metric_is_wenig_daten_and_a_gap_is_not_five(self):
        rows = [_year(2025, 200_000_000, 160_000_000)]
        scored = score_company(CONFIG, _bundle(rows), {}, {}, "2026-10-27", {})
        model, metrics = metric_map(scored, "C_01")
        self.assertEqual(metrics["gm_vs_industry"]["points"], 10)
        self.assertIsNone(metrics["roic_spread"]["points"])
        self.assertNotEqual(metrics["roic_spread"]["points"], 5)
        self.assertEqual(model["status"], "wenig Daten")
        self.assertIsNone(model["score"])
        self.assertIn("1 von 4 Metriken", model["dataQuality"])

    def test_young_company_needs_an_industry_level_margin(self):
        strong = []
        weak = []
        for year, operating in ((2024, -5), (2025, -5)):
            strong.append(_year(year, 200_000_000, 160_000_000, operating, operating))
            weak.append(_year(year, 200_000_000, 40_000_000, operating, operating))
        strong_score = score_company(CONFIG, _bundle(strong), {}, {}, "2026-10-27", {})
        weak_score = score_company(CONFIG, _bundle(weak, _industry_moat(gross_margin=0.50)), {}, {}, "2026-10-27", {})
        strong_m3 = metric_map(strong_score, "C_01")[1]["roic_spread"]
        weak_m3 = metric_map(weak_score, "C_01")[1]["roic_spread"]
        self.assertEqual(strong_m3["status"], BUILD_PHASE)
        self.assertIsNone(strong_m3["points"])
        self.assertIsNone(weak_m3["status"])
        self.assertEqual(weak_m3["points"], 0.0)

    def test_biotech_loss_is_build_phase_and_a_profit_is_scored(self):
        moat = _industry_moat(industry="Drugs (Biotechnology)", gross_margin=0.60, wacc=0.08)
        loss_rows = [_year(year, 10_000_000, 9_000_000, -20, -20) for year in (2024, 2025)]
        loss = score_company(CONFIG, _bundle(loss_rows, moat), {}, {}, "2026-10-27", {})
        metrics = metric_map(loss, "C_01")[1]
        self.assertEqual(metrics["gm_vs_industry"]["status"], "n/a")
        self.assertEqual(metrics["roic_spread"]["status"], BUILD_PHASE)
        self.assertEqual(metric_map(loss, "C_01")[0]["status"], "wenig Daten")

        profit_rows = [_year(year, 10_000_000, 9_000_000, 20, 20) for year in (2024, 2025)]
        profit = score_company(CONFIG, _bundle(profit_rows, moat), {}, {}, "2026-10-27", {})
        spread = metric_map(profit, "C_01")[1]["roic_spread"]
        self.assertIsNone(spread["status"])
        self.assertIsNotNone(spread["points"])
        self.assertNotEqual(spread["points"], 0)

    def test_low_industry_research_and_missing_ebit_growth_are_na(self):
        moat = _industry_moat(rd=0.01, ebit=None)
        moat["benchmarks"]["Software (System & Application)"]["ebitGrowth"] = None
        rows = [_year(2025, 200_000_000, 100_000_000)]
        rows[0]["rd"] = point("2025-12-31", 40_000_000)
        scored = score_company(CONFIG, _bundle(rows, moat), {}, {}, "2026-10-27", {})
        market = metric_map(scored, "C_03")[1]
        self.assertEqual(market["rd_vs_industry"]["status"], "n/a")
        self.assertIsNone(market["rd_vs_industry"]["points"])
        self.assertEqual(market["ebit_pool"]["status"], "n/a")
        self.assertIsNone(market["ebit_pool"]["points"])

    def test_segment_weights_change_growth_and_headroom(self):
        moat = _industry_moat(growth=0.196)
        retail = {
            "grossMargin": 0.33,
            "rdSales": 0.05,
            "costOfCapital": 0.07,
            "revenueGrowth": [0.0415],
            "ebitGrowth": 0.1277,
        }
        moat["benchmarks"]["Retail (General)"] = retail
        moat["segments"] = [
            {"member": "Cloud", "industry": "Software (System & Application)", "weight": 0.5},
            {"member": "Shops", "industry": "Retail (General)", "weight": 0.5},
        ]
        moat["pools"]["Retail (General)"] = 500_000_000_000
        rows = [_year(2025, 200_000_000, 80_000_000)]
        scored = score_company(CONFIG, _bundle(rows, moat), {}, {}, "2026-10-27", {})
        market = metric_map(scored, "C_03")[1]
        blended = 0.5 * 0.196 + 0.5 * 0.0415
        self.assertAlmostEqual(market["industry_growth"]["raw"], blended, places=4)
        self.assertIsNone(market["industry_growth"]["status"])
        self.assertIsNotNone(market["headroom"]["raw"])
        self.assertNotEqual(market["headroom"]["points"], 5)

    def test_swedish_listing_skips_the_model_and_keeps_the_industry(self):
        moat = _industry_moat(industry="Semiconductor", growth=0.117, ebit=0.096)
        moat["sec"] = False
        moat["segmentNote"] = "nur Branchenebene, keine Segmentdaten"
        moat["ownRevenue"] = 30_000_000
        moat["revenueNote"] = "niedrige Datenqualität (yfinance). 0,1000 USD je SEK am 2025-12-31, Europäische Zentralbank, Frankfurter"
        scored = score_company(CONFIG, _bundle([], moat), {}, {}, "2026-10-27", {}, {"sec": False})
        model, metrics = metric_map(scored, "C_01")
        self.assertTrue(all(item["status"] == "nicht bewertbar – keine US-Meldepflicht" for item in metrics.values()))
        self.assertEqual(model["status"], "nicht bewertbar – keine US-Meldepflicht")
        self.assertIsNone(model["score"])
        market = metric_map(scored, "C_03")
        self.assertIn("nur Branchenebene, keine Segmentdaten", market[0]["dataQuality"])
        self.assertEqual(market[1]["rd_vs_industry"]["status"], "n/a")
        self.assertIsNone(market[1]["industry_growth"]["status"])
        self.assertIn("niedrige Datenqualität (yfinance)", market[1]["headroom"]["note"])

    def test_missing_10k_tag_scores_eight_and_ifrs_stays_blank(self):
        moat = _industry_moat()
        moat["concentration"] = {"mode": "none"}
        rows = [_year(year, 200_000_000, 120_000_000) for year in (2023, 2024, 2025)]
        scored = score_company(CONFIG, _bundle(rows, moat), {}, {}, "2026-10-27", {})
        quality = metric_map(scored, "C_01")[1]["revenue_quality"]
        self.assertIn(NO_CUSTOMER_NOTE, quality["value"])
        self.assertEqual(quality["points"], 8.0)
        self.assertIsNone(quality["status"])

        moat["concentration"] = {"mode": "na"}
        blank = score_company(CONFIG, _bundle(rows, moat), {}, {}, "2026-10-27", {})
        missing = metric_map(blank, "C_01")[1]["revenue_quality"]
        self.assertEqual(missing["status"], "n/a")
        self.assertIsNone(missing["points"])


class FilingTests(unittest.TestCase):
    def test_spcx_quarter_beats_the_nine_month_share_and_ignores_receivables(self):
        xml = """<?xml version="1.0"?>
        <xbrl>
          <context id="q">
            <period><startDate>2026-07-01</startDate><endDate>2026-09-30</endDate></period>
            <entity><segment>
              <explicitMember dimension="us-gaap:ConcentrationRiskByTypeAxis">us-gaap:CustomerConcentrationRiskMember</explicitMember>
              <explicitMember dimension="us-gaap:ConcentrationRiskByBenchmarkAxis">us-gaap:RevenueFromContractWithCustomerMember</explicitMember>
              <explicitMember dimension="us-gaap:MajorCustomersAxis">us-gaap:CustomerBMember</explicitMember>
            </segment></entity>
          </context>
          <context id="ytd">
            <period><startDate>2026-01-01</startDate><endDate>2026-09-30</endDate></period>
            <entity><segment>
              <explicitMember dimension="us-gaap:ConcentrationRiskByTypeAxis">us-gaap:CustomerConcentrationRiskMember</explicitMember>
              <explicitMember dimension="us-gaap:ConcentrationRiskByBenchmarkAxis">us-gaap:RevenueFromContractWithCustomerMember</explicitMember>
              <explicitMember dimension="us-gaap:MajorCustomersAxis">us-gaap:CustomerBMember</explicitMember>
            </segment></entity>
          </context>
          <context id="ar">
            <period><startDate>2026-07-01</startDate><endDate>2026-09-30</endDate></period>
            <entity><segment>
              <explicitMember dimension="us-gaap:ConcentrationRiskByTypeAxis">us-gaap:CustomerConcentrationRiskMember</explicitMember>
              <explicitMember dimension="us-gaap:ConcentrationRiskByBenchmarkAxis">us-gaap:AccountsReceivableMember</explicitMember>
              <explicitMember dimension="us-gaap:MajorCustomersAxis">us-gaap:CustomerBMember</explicitMember>
            </segment></entity>
          </context>
          <ConcentrationRiskPercentage1 contextRef="q">0.345</ConcentrationRiskPercentage1>
          <ConcentrationRiskPercentage1 contextRef="ytd">0.242</ConcentrationRiskPercentage1>
          <ConcentrationRiskPercentage1 contextRef="ar">0.277</ConcentrationRiskPercentage1>
        </xbrl>""".encode()
        instances = [{"form": "10-Q", "filed": "2026-10-22", "facts": parse_instance(xml)}]
        found = customer_concentration(instances, False)
        self.assertEqual(found["mode"], "value")
        self.assertAlmostEqual(found["pct"], 0.345)
        self.assertEqual(found["end"], "2026-09-30")

    def test_parsed_10k_without_the_tag_is_a_signal_and_20f_is_not(self):
        self.assertEqual(customer_concentration([{"form": "10-K", "filed": "2026-02-06", "facts": []}], False)["mode"], "none")
        self.assertEqual(customer_concentration([{"form": "20-F", "filed": "2026-04-01", "facts": []}], True)["mode"], "na")
        self.assertEqual(customer_concentration([{"form": "10-Q", "filed": "2026-08-04", "facts": []}], False)["mode"], "na")

    def test_segment_axis_ignores_a_second_dimension(self):
        xml = """<?xml version="1.0"?>
        <xbrl>
          <context id="space">
            <period><startDate>2025-01-01</startDate><endDate>2025-12-31</endDate></period>
            <entity><segment>
              <explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">spcx:SpaceMember</explicitMember>
            </segment></entity>
          </context>
          <context id="both">
            <period><startDate>2025-01-01</startDate><endDate>2025-12-31</endDate></period>
            <entity><segment>
              <explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">spcx:SpaceMember</explicitMember>
              <explicitMember dimension="us-gaap:ProductOrServiceAxis">spcx:LaunchMember</explicitMember>
            </segment></entity>
          </context>
          <context id="drop">
            <period><startDate>2025-01-01</startDate><endDate>2025-12-31</endDate></period>
            <entity><segment>
              <explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">spcx:EliminationMember</explicitMember>
            </segment></entity>
          </context>
          <RevenueFromContractWithCustomerExcludingAssessedTax contextRef="space">100</RevenueFromContractWithCustomerExcludingAssessedTax>
          <RevenueFromContractWithCustomerExcludingAssessedTax contextRef="both">999</RevenueFromContractWithCustomerExcludingAssessedTax>
          <RevenueFromContractWithCustomerExcludingAssessedTax contextRef="drop">5</RevenueFromContractWithCustomerExcludingAssessedTax>
        </xbrl>""".encode()
        instances = [{"form": "10-K", "filed": "2026-02-01", "facts": parse_instance(xml)}]
        weights, note = segment_weights(instances, {"SpaceMember": "Aerospace/Defense"})
        self.assertEqual(len(weights), 1)
        self.assertAlmostEqual(weights[0]["weight"], 1.0)
        self.assertEqual(weights[0]["industry"], "Aerospace/Defense")
        self.assertIn("2025-12-31", note)
        self.assertNotEqual(date.today().year, 0)
