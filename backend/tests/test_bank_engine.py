import unittest
from copy import deepcopy

from backend.finance_engine import analyze_company


def _fact(value, label, *, unit="CNY million"):
    return {
        "value": value,
        "unit": unit,
        "as_reported_label": label,
        "source": {"document": "annual-report.pdf", "page": 88, "table": "bank disclosure"},
    }


def bank_company():
    return {
        "company": {"symbol": "600000.SH", "name": "示例银行", "industry": "股份制银行", "currency": "CNY", "unit": "CNY million"},
        "unit": "CNY million",
        "periods": [
            {
                "end_date": "2024-12-31", "fiscal_year": 2024, "period_type": "FY", "duration_months": 12, "cumulative": True, "audited": True,
                "facts": {
                    "net_interest_income": _fact(44, "利息净收入"),
                    "net_fee_commission_income": _fact(9, "手续费及佣金净收入"),
                    "net_income": _fact(18, "归母净利润"),
                    "total_assets": _fact(1000, "资产总额"),
                    "total_liabilities": _fact(910, "负债总额"),
                    "total_equity": _fact(90, "归母权益"),
                    "loans_and_advances": _fact(600, "客户贷款及垫款"),
                    "customer_deposits": _fact(720, "吸收存款及同业存放"),
                    "nonperforming_loans": _fact(6, "不良贷款余额"),
                    "special_mention_loans": _fact(15, "关注类贷款余额"),
                    "overdue_loans": _fact(9, "逾期贷款余额"),
                    "loan_loss_provision": _fact(12, "贷款损失准备"),
                    "shares_outstanding": _fact(10, "期末总股本", unit="million shares"),
                    "net_interest_margin": _fact(2.0, "净息差", unit="%"),
                    "core_tier1_capital_ratio": _fact(10.5, "核心一级资本充足率", unit="%"),
                    "tier1_capital_ratio": _fact(11.2, "一级资本充足率", unit="%"),
                    "capital_adequacy_ratio": _fact(13.4, "资本充足率", unit="%"),
                    "cost_income_ratio": _fact(28.0, "成本收入比", unit="%"),
                },
            },
            {
                "end_date": "2025-12-31", "fiscal_year": 2025, "period_type": "FY", "duration_months": 12, "cumulative": True, "audited": True,
                "facts": {
                    "net_interest_income": _fact(42, "利息净收入"),
                    "net_fee_commission_income": _fact(8, "手续费及佣金净收入"),
                    "net_income": _fact(17, "归母净利润"),
                    "total_assets": _fact(1100, "资产总额"),
                    "total_liabilities": _fact(1000, "负债总额"),
                    "total_equity": _fact(100, "归母权益"),
                    "loans_and_advances": _fact(625, "客户贷款及垫款"),
                    "customer_deposits": _fact(760, "吸收存款及同业存放"),
                    "nonperforming_loans": _fact(10, "不良贷款余额"),
                    "special_mention_loans": _fact(21, "关注类贷款余额"),
                    "overdue_loans": _fact(12, "逾期贷款余额"),
                    "loan_loss_provision": _fact(15, "贷款损失准备"),
                    "shares_outstanding": _fact(10, "期末总股本", unit="million shares"),
                    "net_interest_margin": _fact(1.7, "净息差", unit="%"),
                    "core_tier1_capital_ratio": _fact(9.2, "核心一级资本充足率", unit="%"),
                    "tier1_capital_ratio": _fact(10.1, "一级资本充足率", unit="%"),
                    "capital_adequacy_ratio": _fact(12.5, "资本充足率", unit="%"),
                    "cost_income_ratio": _fact(32.0, "成本收入比", unit="%"),
                },
            },
        ],
        "valuation": {
            "company_type": "2",
            "market_price": 8,
            "price_as_of": "2026-01-15",
            "shares_outstanding": 10,
            "bank": {
                "pb_scenarios": [{"name": "stress", "target_pb": 0.6}, {"name": "base", "target_pb": 0.9}],
                "residual_income_scenarios": [{"name": "base", "roe": 0.15, "cost_of_equity": 0.10, "book_value_growth": 0.04, "terminal_growth": 0.02, "forecast_years": 5}],
            },
        },
    }


class BankEngineTests(unittest.TestCase):
    def test_bank_uses_dedicated_metrics_and_valuations_with_lineage(self):
        report = analyze_company(bank_company())
        self.assertEqual(report["support"]["status"], "supported_bank")
        self.assertEqual(report["engine"], "deterministic_decimal_bank_engine")
        latest = report["bank"]["latest"]["metrics"]
        self.assertAlmostEqual(latest["net_interest_margin"]["value"], 0.017)
        self.assertAlmostEqual(latest["nonperforming_loan_ratio"]["value"], 0.016)
        self.assertAlmostEqual(latest["overdue_loan_ratio"]["value"], 0.0192)
        self.assertAlmostEqual(latest["provision_coverage_ratio"]["value"], 1.5)
        self.assertEqual(latest["book_value_per_share"]["value"], 10)
        self.assertEqual(latest["nonperforming_loan_ratio"]["inputs"][0]["source"]["page"], 88)
        self.assertEqual(report["valuation"]["pb"]["scenarios"][1]["value_per_share"], 9)
        self.assertEqual(report["valuation"]["residual_income"]["scenarios"][0]["status"], "ok")
        self.assertEqual(report["valuation"]["dcf"]["status"], "unsupported")
        ids = {item["id"] for item in report["flags"]}
        self.assertIn("bank_npl_rising", ids)
        self.assertIn("bank_overdue_loan_rising", ids)
        self.assertIn("bank_nim_compression", ids)
        self.assertIn("bank_core_tier1_falling", ids)

    def test_missing_regulatory_fact_is_not_treated_as_zero(self):
        raw = bank_company()
        del raw["periods"][-1]["facts"]["capital_adequacy_ratio"]
        report = analyze_company(raw)
        metric = report["bank"]["latest"]["metrics"]["capital_adequacy_ratio"]
        self.assertEqual(metric["status"], "missing")
        self.assertIsNone(metric["value"])
        self.assertIn("capital_adequacy_ratio", report["bank"]["coverage"]["missing"])

    def test_missing_overdue_ratio_is_explicit_and_not_normalized_away(self):
        raw = bank_company()
        del raw["periods"][-1]["facts"]["overdue_loans"]
        report = analyze_company(raw)
        metric = report["bank"]["latest"]["metrics"]["overdue_loan_ratio"]
        self.assertEqual(metric["status"], "missing")
        self.assertIsNone(metric["value"])
        self.assertIn("overdue_loan_ratio", report["bank"]["coverage"]["missing"])

    def test_interim_ttm_growth_stays_missing_without_comparable_prior_ttm(self):
        raw = bank_company()
        q1_2025 = deepcopy(raw["periods"][0])
        q1_2025.update({"end_date": "2025-03-31", "fiscal_year": 2025, "period_type": "Q1", "duration_months": 3, "audited": False})
        q1_2025["facts"]["net_income"] = _fact(4, "一季度归母净利润")
        q1_2026 = deepcopy(raw["periods"][1])
        q1_2026.update({"end_date": "2026-03-31", "fiscal_year": 2026, "period_type": "Q1", "duration_months": 3, "audited": False})
        q1_2026["facts"]["net_income"] = _fact(5, "一季度归母净利润")
        raw["periods"].extend([q1_2025, q1_2026])

        report = analyze_company(raw)
        metric = report["periods"]["ttm"]["metrics"]["net_income_growth"]
        self.assertEqual(report["periods"]["ttm"]["metrics"]["net_income"]["value"], 18)
        self.assertEqual(metric["status"], "missing")
        self.assertIsNone(metric["value"])
        self.assertIn("comparable prior", metric["reason"])


if __name__ == "__main__":
    unittest.main()
