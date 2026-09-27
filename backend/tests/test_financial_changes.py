import unittest

from backend.finance_engine import analyze_company, demo_company
from backend.financial_changes import detect_financial_changes


class FinancialChangeTests(unittest.TestCase):
    def test_analysis_contains_source_linked_change_output(self):
        report = analyze_company(demo_company())
        changes = report["changes"]
        self.assertEqual(changes["status"], "ready")
        self.assertIn("短期债务", changes["unsupported_checks"][0])
        self.assertTrue(all("metrics" in signal for signal in changes["signals"]))

    def test_profit_cash_divergence_is_deterministic_and_linked(self):
        report = analyze_company(demo_company())
        annual = report["periods"]["annual"]
        current = annual[-1]
        current["metrics"]["net_income"]["value"] = annual[-2]["metrics"]["net_income"]["value"] + 100
        current["metrics"]["operating_cash_flow"]["value"] = annual[-2]["metrics"]["operating_cash_flow"]["value"] - 100
        changes = detect_financial_changes({"periods": {"annual": annual, "ttm": None}})
        signal = next(item for item in changes["signals"] if item["id"] == "PROFIT_UP_CASH_DOWN")
        self.assertIn(signal["severity"], {"yellow", "red"})
        self.assertTrue(signal["metrics"]["current"]["net_income"]["inputs"])

    def test_three_period_margin_decline_and_missing_are_explicit(self):
        def period(date, margin):
            return {
                "end_date": date,
                "metrics": {
                    "gross_margin": {"status": "ok", "value": margin, "inputs": [{"source": date}]},
                },
            }

        changes = detect_financial_changes(
            {"periods": {"annual": [period("2023-12-31", 0.32), period("2024-12-31", 0.29), period("2025-12-31", 0.26)], "ttm": None}}
        )
        self.assertIn("GROSS_MARGIN_THREE_PERIOD_DECLINE", {item["id"] for item in changes["signals"]})
        self.assertIn("net_income", changes["missing_metrics"])

    def test_latest_cumulative_interim_compares_with_same_prior_interim_not_fy(self):
        raw = demo_company()
        raw["periods"][-1]["facts"]["net_income"]["restated"] = True
        report = analyze_company(raw)
        changes = report["changes"]
        self.assertEqual(changes["current_context"]["end_date"], "2025-09-30")
        self.assertEqual(changes["comparison_context"]["end_date"], "2024-09-30")
        self.assertEqual(changes["current_context"]["basis"], "reported_cumulative_ytd")
        self.assertTrue(changes["current_context"]["cumulative"])
        self.assertFalse(changes["current_context"]["audited"])
        self.assertTrue(changes["current_context"]["restated"])
        self.assertIn("same-duration prior-year interim", changes["comparison_basis"])

    def test_short_debt_cash_gap_and_working_capital_growth_are_source_linked(self):
        raw = demo_company()
        latest = raw["periods"][-1]["facts"]
        latest["cash"]["value"] = "100"
        latest["restricted_cash"] = {"value": "20", "unit": "CNY million", "source": {"document": "2025Q3.pdf", "page": 50}}
        latest["debt_due_within_one_year"] = {"value": "200", "unit": "CNY million", "source": {"document": "2025Q3.pdf", "page": 91}}
        latest["accounts_receivable"]["value"] = "3000"
        report = analyze_company(raw)
        changes = report["changes"]
        by_id = {item["id"]: item for item in changes["signals"]}
        self.assertIn("USABLE_CASH_SHORT_DEBT_GAP", by_id)
        self.assertAlmostEqual(by_id["USABLE_CASH_SHORT_DEBT_GAP"]["values"]["usable_cash_short_debt_coverage"], 0.4)
        self.assertEqual(
            by_id["USABLE_CASH_SHORT_DEBT_GAP"]["metrics"]["current"]["debt_due_within_one_year"]["inputs"][0]["source"]["page"],
            91,
        )
        self.assertIn("RECEIVABLES_OUTRUN_REVENUE_CHANGE", by_id)

    def test_latest_interim_without_comparator_still_runs_absolute_risk_checks(self):
        latest = {
            "end_date": "2026-03-31",
            "fiscal_year": 2026,
            "duration_months": 3,
            "cumulative": True,
            "basis": "reported_cumulative_ytd",
            "metrics": {
                "interest_coverage": {"status": "ok", "value": 0.8, "inputs": [{"source": "2026Q1.pdf"}]},
                "operating_profit": {"status": "ok", "value": 80, "inputs": [{"source": "2026Q1.pdf"}]},
                "usable_cash_short_debt_coverage": {"status": "ok", "value": 0.4, "inputs": [{"source": "2026Q1.pdf"}]},
                "usable_cash": {"status": "ok", "value": 40, "inputs": [{"source": "2026Q1.pdf"}]},
                "restricted_cash": {"status": "ok", "value": 10, "inputs": [{"source": "2026Q1.pdf"}]},
                "debt_due_within_one_year": {"status": "ok", "value": 100, "inputs": [{"source": "2026Q1.pdf"}]},
            },
        }
        changes = detect_financial_changes({"periods": {"annual": [], "interim": [latest], "ttm": None}})
        signal_ids = {item["id"] for item in changes["signals"]}
        self.assertEqual(changes["status"], "ready")
        self.assertIn("INTEREST_COVERAGE_WEAK", signal_ids)
        self.assertIn("USABLE_CASH_SHORT_DEBT_GAP", signal_ids)
        self.assertEqual(changes["current_context"]["end_date"], "2026-03-31")
        self.assertIsNone(changes["comparison_context"])
        self.assertIn("without a same-duration", changes["comparison_basis"])

    def test_ttm_without_comparable_is_not_compared_with_an_old_fiscal_year(self):
        def period(date, net_income, operating_cash_flow):
            return {
                "end_date": date,
                "metrics": {
                    "net_income": {"status": "ok", "value": net_income, "inputs": [{"source": date}]},
                    "operating_cash_flow": {"status": "ok", "value": operating_cash_flow, "inputs": [{"source": date}]},
                },
            }

        annual = [period("2024-12-31", 100, 300), period("2025-12-31", 200, 250)]
        ttm = period("2026-03-31", 300, 100)
        changes = detect_financial_changes(
            {"periods": {"annual": annual, "interim": [], "ttm": ttm, "ttm_comparable": None}}
        )
        self.assertNotIn("PROFIT_UP_CASH_DOWN", {item["id"] for item in changes["signals"]})
        self.assertIsNone(changes["comparison_context"])
        self.assertIn("without a comparable prior TTM", changes["comparison_basis"])

    def test_consolidated_and_parent_periods_are_never_compared(self):
        def period(end_date, scope, net_income, operating_cash_flow, margin):
            return {
                "end_date": end_date,
                "report_scope": scope,
                "metrics": {
                    "net_income": {"status": "ok", "value": net_income, "inputs": [{"source": end_date}]},
                    "operating_cash_flow": {"status": "ok", "value": operating_cash_flow, "inputs": [{"source": end_date}]},
                    "gross_margin": {"status": "ok", "value": margin, "inputs": [{"source": end_date}]},
                },
            }

        changes = detect_financial_changes({"periods": {"annual": [
            period("2023-12-31", "consolidated", 80, 100, 0.35),
            period("2024-12-31", "consolidated", 90, 90, 0.30),
            period("2025-12-31", "parent", 120, 50, 0.25),
        ], "ttm": None}})
        signal_ids = {item["id"] for item in changes["signals"]}
        self.assertNotIn("PROFIT_UP_CASH_DOWN", signal_ids)
        self.assertNotIn("GROSS_MARGIN_THREE_PERIOD_DECLINE", signal_ids)
        self.assertIsNone(changes["comparison_context"])
        self.assertIn("same-scope", changes["comparison_basis"])
        self.assertTrue(any("报表范围不同" in item for item in changes["unsupported_checks"]))
        self.assertTrue(any("连续趋势口径" in item for item in changes["unsupported_checks"]))


if __name__ == "__main__":
    unittest.main()
