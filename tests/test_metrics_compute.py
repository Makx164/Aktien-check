"""Rechenregeln der sechs Kennzahlen, ohne Netz."""

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from metricsbuild.compute import (
    THESIS_PLACEHOLDER,
    cash_vs_debt,
    dilution,
    format_money,
    format_percent,
    free_cash_flow,
    gross_margin,
    next_earnings,
    prefer_newer_series,
    quarterly_points_from_rows,
    revenue_growth,
    yoy,
)


class FormatTests(unittest.TestCase):
    def test_money_and_percent_match_the_pltr_example_scale(self):
        self.assertEqual(format_money(1_935_464_000, "USD"), "1,935 Mrd. USD")
        self.assertEqual(format_money(1_003_697_000, "USD"), "1,004 Mrd. USD")
        self.assertEqual(format_money(211_400_000, "USD"), "0,211 Mrd. USD")
        self.assertEqual(format_money(3_358_272_000, "USD"), "3,358 Mrd. USD")
        self.assertEqual(format_percent(1_935_464_000 / 1_003_697_000 - 1, signed=True), "+92,8 %")
        self.assertEqual(format_percent(0.84796), "84,8 %")

    def test_yoy_does_not_invent_a_rate_from_a_non_positive_base(self):
        self.assertIsNone(yoy(10, 0))
        self.assertIsNone(yoy(10, None))
        self.assertAlmostEqual(yoy(0, 10), -1)


class PltrFixtureTests(unittest.TestCase):
    def setUp(self):
        self.revenue = [
            (date(2026, 6, 30), 1_935_464_000),
            (date(2026, 3, 31), 1_632_583_000),
            (date(2025, 12, 31), 1_406_802_000),
            (date(2025, 9, 30), 1_181_092_000),
            (date(2025, 6, 30), 1_003_697_000),
        ]
        self.gross = [
            (date(2026, 6, 30), 1_638_594_000),
            (date(2026, 3, 31), 1_416_785_000),
            (date(2025, 12, 31), 1_190_836_000),
            (date(2025, 9, 30), 973_785_000),
            (date(2025, 6, 30), 810_763_000),
        ]
        self.ocf = [
            (date(2026, 6, 30), 1_216_167_000),
            (date(2026, 3, 31), 899_165_000),
            (date(2025, 12, 31), 777_295_000),
            (date(2025, 9, 30), 507_664_000),
        ]
        self.capex = [
            (date(2026, 6, 30), -14_554_000),
            (date(2026, 3, 31), -7_401_000),
            (date(2025, 12, 31), -13_272_000),
            (date(2025, 9, 30), -6_792_000),
        ]
        self.shares = [
            (date(2026, 6, 30), 2_399_820_000),
            (date(2025, 6, 30), 2_361_748_820),
        ]

    def test_six_fields_from_the_published_pltr_figures(self):
        growth = revenue_growth(self.revenue, "USD", "2026-10-29", "https://example.test")
        self.assertEqual(growth["display"], "+92,8 %")
        self.assertEqual(growth["period"], "Q2 2026 vs. Q2 2025")
        self.assertEqual(growth["detail"], "1,935 Mrd. USD vs. 1,004 Mrd. USD")

        margin = gross_margin(self.revenue, self.gross, 0.84796, "USD", "2026-10-29", "https://example.test")
        self.assertEqual(margin["display"], "84,8 %")
        self.assertTrue(margin["period"].startswith("TTM"))

        cash = free_cash_flow(self.ocf, self.capex, None, "USD", "2026-10-29", "https://example.test")
        self.assertEqual(cash["value"], 3_358_272_000)
        self.assertEqual(cash["display"], "+3,358 Mrd. USD")
        self.assertIn("GAAP", cash["note"])

        balance = cash_vs_debt(
            [(date(2026, 6, 30), 9_409_099_000)],
            [],
            [],
            [(date(2026, 6, 30), 211_400_000)],
            "USD",
            "2026-10-29",
            "https://example.test",
        )
        self.assertEqual(balance["display"], "Cash 9,409 Mrd. · Schulden 0,211 Mrd. USD")
        self.assertEqual(balance["value"]["cash"], 9_409_099_000)
        self.assertEqual(balance["value"]["debt"], 211_400_000)
        self.assertEqual(balance["period"], "letzte Bilanz 30.06.2026")

        watered = dilution(self.shares, [], "2026-10-29", "https://example.test")
        self.assertEqual(watered["display"], "+1,6 %")
        self.assertIn("Basic Average Shares", watered["detail"])

        moment = datetime(2026, 11, 2, 15, 0, tzinfo=ZoneInfo("America/New_York"))
        upcoming = next_earnings(
            moment, "2026-10-29", "https://example.test", "Q2 2026", "Yahoo Finance earnings_dates", date(2026, 6, 30)
        )
        self.assertEqual(upcoming["display"], "02.11.2026, 21:00 MEZ")
        self.assertEqual(upcoming["period"], "Q3 2026")

    def test_missing_series_stay_empty(self):
        growth = revenue_growth([], "USD", "2026-10-29", "https://example.test")
        self.assertIsNone(growth["value"])
        self.assertIsNone(growth["display"])
        self.assertEqual(growth["note"], "keine Quartalsreihe")
        margin = gross_margin([(date(2026, 6, 30), 0)], [(date(2026, 6, 30), 0)], None, "USD", "2026-10-29", "https://example.test")
        self.assertIsNone(margin["value"])
        cash = free_cash_flow(self.ocf[:2], self.capex[:2], None, "USD", "2026-10-29", "https://example.test")
        self.assertIsNone(cash["value"])
        self.assertNotEqual(cash["display"], "0")

    def test_placeholder_constant(self):
        self.assertEqual(THESIS_PLACEHOLDER, "These folgt")

    def test_non_calendar_fiscal_year_is_not_labeled_as_calendar_quarter(self):
        growth = revenue_growth(self.revenue, "USD", "2026-10-29", "https://example.test", fiscal_month=6)
        self.assertEqual(growth["period"], "Quartal bis 30.06.2026 vs. Quartal bis 30.06.2025")

    def test_sec_restatement_uses_the_later_filing_and_can_replace_a_stale_yahoo_quarter(self):
        rows = [
            {"end": "2025-09-30", "start": "2025-07-01", "val": 18_756_199, "form": "10-Q", "filed": "2025-11-05", "concept": "Revenue"},
            {"end": "2025-09-30", "start": "2025-07-01", "val": 9_262_517, "form": "10-Q", "filed": "2026-10-28", "concept": "Revenue"},
            {"end": "2026-09-30", "start": "2026-07-01", "val": 12_583_518, "form": "10-Q", "filed": "2026-10-28", "concept": "Revenue"},
        ]
        points, concept = quarterly_points_from_rows(rows)
        self.assertEqual(concept, "Revenue")
        self.assertEqual(points, [(date(2025, 9, 30), 9_262_517), (date(2026, 9, 30), 12_583_518)])
        stale = [(date(2026, 6, 30), 9_348_551), (date(2025, 6, 30), 5_760_711)]
        chosen, origin = prefer_newer_series(stale, points)
        self.assertEqual(origin, "alternate")
        growth = revenue_growth(chosen, "USD", "2026-10-29", "https://example.test", source="SEC companyfacts")
        self.assertEqual(growth["display"], "+35,9 %")
        self.assertEqual(growth["source"], "SEC companyfacts")

    def test_fcf_line_fills_a_capex_hole_and_gross_margins_fills_a_missing_statement(self):
        cash = free_cash_flow(
            [(date(2026, 6, 30), 10), (date(2026, 3, 31), 10), (date(2025, 12, 31), 10)],
            [(date(2026, 6, 30), -1), (date(2026, 3, 31), -1)],
            [(date(2026, 6, 30), 9), (date(2026, 3, 31), 9), (date(2025, 12, 31), 9), (date(2025, 9, 30), 9)],
            "USD",
            "2026-10-29",
            "https://example.test",
        )
        self.assertEqual(cash["value"], 36)
        self.assertIn("Free Cash Flow", cash["note"])
        margin = gross_margin([], [], 0.51651, "USD", "2026-10-29", "https://example.test")
        self.assertEqual(margin["display"], "51,7 %")
        self.assertEqual(margin["source"], "Yahoo Finance grossMargins")


if __name__ == "__main__":
    unittest.main()
