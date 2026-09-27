import json
import unittest

from backend.finance_engine import CompanyData, analyze_company, demo_company


class CanonicalInputTests(unittest.TestCase):
    def test_demo_is_dict_friendly_and_parses_to_decimal_model(self):
        raw = demo_company()
        company = CompanyData.from_input(raw)
        self.assertEqual(company.symbol, "600888")
        self.assertEqual(company.periods[-1].end_date, "2025-09-30")
        self.assertEqual(str(company.periods[-1].facts["revenue"].value), "7350")

    def test_invalid_number_is_rejected_instead_of_coerced(self):
        raw = demo_company()
        raw["periods"][0]["facts"]["revenue"]["value"] = "not-a-number"
        with self.assertRaisesRegex(ValueError, "not a valid decimal"):
            analyze_company(raw)

    def test_output_is_json_serializable_and_contains_methodology(self):
        report = analyze_company(demo_company())
        encoded = json.dumps(report, ensure_ascii=False, allow_nan=False)
        self.assertIn("华星装备", encoded)
        self.assertEqual(report["schema_version"], "1.0")
        self.assertIn("missing_vs_zero", report["methodology"])


class PeriodAndMetricTests(unittest.TestCase):
    def setUp(self):
        self.report = analyze_company(demo_company())

    def test_annual_periods_and_ttm_are_computed(self):
        annual = self.report["periods"]["annual"]
        self.assertEqual([item["fiscal_year"] for item in annual], [2023, 2024])
        ttm = self.report["periods"]["ttm"]
        self.assertEqual(ttm["metrics"]["revenue"]["value"], 9750)
        self.assertEqual(ttm["metrics"]["net_income"]["value"], 1105)
        self.assertIn("FY2024", ttm["construction"])
        self.assertEqual(len(ttm["metrics"]["revenue"]["inputs"]), 3)
        self.assertIn("current YTD", ttm["metrics"]["revenue"]["formula"])

    def test_financial_ratios_and_dupont_have_formula_lineage(self):
        metrics = self.report["periods"]["annual"][-1]["metrics"]
        self.assertAlmostEqual(metrics["gross_margin"]["value"], 0.3)
        self.assertEqual(metrics["free_cash_flow"]["value"], 610)
        self.assertGreater(metrics["return_on_equity"]["value"], 0)
        dupont = self.report["dupont"]
        self.assertEqual(dupont["status"], "ok")
        self.assertEqual(dupont["roe"]["formula"], "net_margin × asset_turnover × equity_multiplier")
        self.assertTrue(dupont["net_margin"]["inputs"])

    def test_missing_and_zero_are_distinct(self):
        raw = demo_company()
        latest = raw["periods"][-1]["facts"]
        latest["current_liabilities"]["value"] = "0"
        del latest["inventory"]
        report = analyze_company(raw)
        metrics = report["periods"]["ttm"]["metrics"]
        self.assertEqual(metrics["current_ratio"]["status"], "not_calculable")
        self.assertIn("division by zero", metrics["current_ratio"]["reason"])
        denominator = [line for line in metrics["current_ratio"]["inputs"] if line["fact"] == "current_liabilities"]
        self.assertEqual(denominator[-1]["value"], 0)
        self.assertEqual(metrics["quick_ratio"]["status"], "missing")
        self.assertIn("inventory", metrics["quick_ratio"]["inputs"][-2]["fact"] if metrics["quick_ratio"]["inputs"] else "")

    def test_zero_prior_revenue_does_not_crash_growth(self):
        raw = demo_company()
        raw["periods"][0]["facts"]["revenue"]["value"] = "0"
        report = analyze_company(raw)
        growth = report["periods"]["annual"][1]["metrics"]["revenue_growth"]
        self.assertEqual(growth["status"], "not_calculable")
        self.assertIn("zero", growth["reason"])

    def test_mixed_monetary_units_are_not_silently_combined(self):
        raw = demo_company()
        raw["periods"][-1]["facts"]["capital_expenditure"]["unit"] = "CNY"
        report = analyze_company(raw)
        fcf = report["periods"]["ttm"]["metrics"]["free_cash_flow"]
        self.assertEqual(fcf["status"], "missing")
        self.assertIn("unit mismatch", fcf["reason"])
        checks = [check for check in report["data_quality"]["checks"] if check["id"] == "UNIT_CONSISTENCY"]
        self.assertTrue(checks)
        self.assertEqual(checks[-1]["status"], "fail")


class QualityAndScreenTests(unittest.TestCase):
    def test_balance_sheet_failure_is_detected(self):
        raw = demo_company()
        raw["periods"][-1]["facts"]["total_assets"]["value"] = "15000"
        report = analyze_company(raw)
        failed = [
            check
            for check in report["data_quality"]["checks"]
            if check["id"] == "BALANCE_SHEET_EQUATION"
            and check["period_end"] == "2025-09-30"
        ]
        self.assertEqual(failed[0]["status"], "fail")
        self.assertTrue(failed[0]["inputs"])

    def test_red_and_yellow_flags_are_deterministic(self):
        raw = demo_company()
        latest = raw["periods"][-1]["facts"]
        # Q3 inputs are cumulative; make FY2024 + Q3'25 - Q3'24 negative.
        latest["operating_cash_flow"]["value"] = "-2000"
        latest["interest_expense"]["value"] = "1500"
        latest["goodwill"]["value"] = "4000"
        report = analyze_company(raw)
        flags = {flag["id"]: flag for flag in report["flags"]}
        self.assertEqual(flags["EARNINGS_CASH_DIVERGENCE"]["severity"], "red")
        self.assertEqual(flags["INTEREST_COVERAGE_CRITICAL"]["severity"], "red")
        self.assertEqual(flags["HIGH_GOODWILL"]["severity"], "yellow")
        self.assertTrue(flags["EARNINGS_CASH_DIVERGENCE"]["inputs"])
        self.assertIn("operating_cash_flow", flags["EARNINGS_CASH_DIVERGENCE"]["formula"])

    def test_piotroski_is_auxiliary_and_not_rescaled_when_missing(self):
        report = analyze_company(demo_company())
        score = report["piotroski"]
        self.assertEqual(score["status"], "complete")
        self.assertEqual(score["score"], 9)
        self.assertEqual(len(score["criteria"]), 9)
        self.assertIn("Auxiliary", score["note"])

        raw = demo_company()
        del raw["periods"][2]["facts"]["shares_outstanding"]
        incomplete = analyze_company(raw)["piotroski"]
        self.assertEqual(incomplete["status"], "incomplete")
        self.assertEqual(incomplete["points_available"], 8)
        self.assertIsNone(next(item for item in incomplete["criteria"] if item["id"] == "F_EQ_OFFER")["point"])

    def test_insurance_routes_to_dedicated_model_and_disables_industrial_outputs(self):
        raw = demo_company()
        raw["company"]["industry"] = "保险"
        report = analyze_company(raw)
        self.assertEqual(report["support"]["status"], "supported_sector")
        self.assertEqual(report["support"]["sector_template"], "insurance")
        self.assertIn("core_solvency_ratio", report["sector"]["coverage"]["missing"])
        self.assertEqual(report["dupont"]["status"], "unsupported")
        self.assertEqual(report["piotroski"]["status"], "unsupported")
        self.assertEqual(report["valuation"]["dcf"]["status"], "unsupported")


class ValuationTests(unittest.TestCase):
    def test_three_dcf_scenarios_have_traceable_results(self):
        valuation = analyze_company(demo_company())["valuation"]["dcf"]
        self.assertEqual(valuation["status"], "ok")
        self.assertEqual([item["name"] for item in valuation["scenarios"]], ["stress", "base", "optimistic"])
        for scenario in valuation["scenarios"]:
            self.assertEqual(scenario["status"], "ok")
            self.assertGreater(scenario["equity_value"], 0)
            self.assertGreater(scenario["value_per_share"], 0)
            self.assertTrue(scenario["inputs"])
            self.assertIn("terminal", scenario["formula"])

    def test_discount_not_above_terminal_growth_is_rejected(self):
        raw = demo_company()
        raw["valuation"]["dcf"]["scenarios"] = [
            {"name": "invalid", "growth_rate": "0.05", "discount_rate": "0.02", "terminal_growth_rate": "0.03"}
        ]
        scenario = analyze_company(raw)["valuation"]["dcf"]["scenarios"][0]
        self.assertEqual(scenario["status"], "not_calculable")
        self.assertIn("greater", scenario["reason"])

    def test_reverse_dcf_solves_market_implied_growth(self):
        reverse = analyze_company(demo_company())["valuation"]["reverse_dcf"]
        self.assertEqual(reverse["status"], "ok")
        self.assertLess(abs(reverse["residual"]), 0.000001)
        self.assertTrue(-0.30 <= reverse["implied_constant_growth_rate"] <= 0.60)
        self.assertIn("not a growth forecast", reverse["interpretation"])
        self.assertTrue(reverse["inputs"])

    def test_fcff_requires_net_debt_lineage(self):
        raw = demo_company()
        raw["valuation"]["dcf"] = {
            "cash_flow_basis": "fcff",
            "base_cash_flow": "900",
            "scenarios": [
                {"name": "base", "growth_rate": "0.05", "forecast_years": 5, "discount_rate": "0.10", "terminal_growth_rate": "0.02"}
            ],
        }
        result = analyze_company(raw)["valuation"]["dcf"]["scenarios"][0]
        self.assertEqual(result["status"], "ok")
        self.assertIsNotNone(result["enterprise_value"])
        self.assertLess(result["equity_value"], result["enterprise_value"])
        input_names = {item["fact"] for item in result["inputs"]}
        self.assertIn("interest_bearing_debt", input_names)
        self.assertIn("cash", input_names)

    def test_dcf_sensitivity_ddm_and_relative_valuation_are_traceable(self):
        raw = demo_company()
        raw["valuation"]["sensitivity"] = {
            "cash_flow_basis": "fcfe",
            "growth_rates": [0.02, 0.04, 0.06],
            "discount_rates": [0.09, 0.10, 0.11],
            "terminal_growth_rate": 0.02,
            "forecast_years": 5,
        }
        raw["valuation"]["ddm"] = {
            "scenarios": [
                {"name": "base", "growth_rate": 0.04, "cost_of_equity": 0.10, "terminal_growth_rate": 0.02, "forecast_years": 5}
            ]
        }
        raw["valuation"]["relative"] = {
            "scenarios": [{"name": "base", "target_pe": 14, "target_pb": 2.2}]
        }
        valuation = analyze_company(raw)["valuation"]
        self.assertEqual(valuation["sensitivity"]["status"], "ok")
        self.assertEqual(len(valuation["sensitivity"]["rows"]), 3)
        self.assertEqual(len(valuation["sensitivity"]["rows"][0]["values"]), 3)
        self.assertTrue(valuation["sensitivity"]["inputs"])
        self.assertEqual(valuation["ddm"]["status"], "ok")
        self.assertGreater(valuation["ddm"]["scenarios"][0]["value_per_share"], 0)
        self.assertTrue(valuation["ddm"]["scenarios"][0]["inputs"])
        self.assertEqual(valuation["relative"]["status"], "ok")
        relative = valuation["relative"]["scenarios"][0]
        self.assertLessEqual(relative["value_range_low"], relative["value_range_high"])
        self.assertIn("not averaged", relative["formula"])


if __name__ == "__main__":
    unittest.main()
