import unittest

from backend.finance_engine import analyze_company
from backend.tests.test_sector_engine import _fact


def insurance_company():
    periods = []
    for year, service, premium, nbv, ev, core, comprehensive, investment_yield in (
        (2024, 90_000, 110_000, 9_000, 150_000, 80, 170, 4.2),
        (2025, 95_000, 112_000, 5_000, 158_000, 45, 95, -1.0),
    ):
        periods.append({
            "end_date": f"{year}-12-31", "fiscal_year": year, "period_type": "FY", "duration_months": 12, "cumulative": True, "audited": True,
            "facts": {
                "insurance_service_revenue": _fact(service, "保险服务收入"),
                "gross_written_premiums": _fact(premium, "原保险保费收入"),
                "new_business_value": _fact(nbv, "新业务价值"),
                "embedded_value": _fact(ev, "内含价值"),
                "net_investment_yield": _fact(3.5, "净投资收益率", "%"),
                "comprehensive_investment_yield": _fact(investment_yield, "综合投资收益率", "%"),
                "insurance_contract_liabilities": _fact(420_000, "保险合同负债"),
                "total_investment_assets": _fact(520_000, "投资资产"),
                "core_solvency_ratio": _fact(core, "核心偿付能力充足率", "%"),
                "comprehensive_solvency_ratio": _fact(comprehensive, "综合偿付能力充足率", "%"),
                "net_income": _fact(12_000, "归母净利润"),
                "total_equity": _fact(120_000, "股东权益"),
                "shares_outstanding": _fact(28_000, "总股本", "million shares"),
            },
        })
    return {
        "company": {"symbol": "601888.SH", "name": "示例保险", "industry": "人身保险", "unit": "CNY million"},
        "unit": "CNY million", "periods": periods,
        "valuation": {"market_price": 30, "price_as_of": "2026-01-15", "sector": {"scenarios": [{"name": "base", "target_pev": 0.8, "normalized_nbv": 6_000, "nbv_multiple": 5, "ev_haircut": 0.1}]}},
    }


def brokerage_company():
    periods = []
    for year, revenue, trading, income in ((2024, 30_000, 10_000, 8_000), (2025, 32_000, 18_000, 7_000)):
        periods.append({
            "end_date": f"{year}-12-31", "fiscal_year": year, "period_type": "FY", "duration_months": 12, "cumulative": True, "audited": True,
            "facts": {
                "revenue": _fact(revenue, "营业收入"),
                "brokerage_net_revenue": _fact(8_000, "经纪业务手续费净收入"),
                "investment_banking_net_revenue": _fact(3_000, "投行业务手续费净收入"),
                "asset_management_net_revenue": _fact(2_000, "资管业务手续费净收入"),
                "net_interest_income": _fact(4_000, "利息净收入"),
                "trading_investment_income": _fact(trading, "投资与公允价值收益"),
                "net_income": _fact(income, "归母净利润"),
                "net_capital": _fact(65_000, "净资本"),
                "risk_coverage_ratio": _fact(90, "风险覆盖率", "%"),
                "capital_leverage_ratio": _fact(7, "资本杠杆率", "%"),
                "liquidity_coverage_ratio": _fact(95, "流动性覆盖率", "%"),
                "net_stable_funding_ratio": _fact(110, "净稳定资金率", "%"),
                "total_equity": _fact(100_000, "股东权益"),
                "shares_outstanding": _fact(10_000, "总股本", "million shares"),
            },
        })
    return {
        "company": {"symbol": "600777.SH", "name": "示例证券", "industry": "证券", "unit": "CNY million"},
        "unit": "CNY million", "periods": periods,
        "valuation": {"market_price": 12, "price_as_of": "2026-01-15", "sector": {"scenarios": [{"name": "base", "target_pb": 1.2, "target_pe": 15, "normalized_net_income": 7_500}]}},
    }


class FinancialSectorEngineTests(unittest.TestCase):
    def test_insurance_uses_solvency_nbv_and_embedded_value(self):
        report = analyze_company(insurance_company())
        self.assertEqual(report["support"]["sector_template"], "insurance")
        metrics = report["sector"]["latest"]["metrics"]
        self.assertAlmostEqual(metrics["core_solvency_ratio"]["value"], 0.45)
        self.assertEqual(metrics["embedded_value"]["inputs"][0]["source"]["page"], 66)
        scenario = report["valuation"]["scenarios"][0]
        self.assertEqual(scenario["pev"]["status"], "ok")
        self.assertEqual(scenario["appraisal_value"]["status"], "ok")
        ids = {item["id"] for item in report["flags"]}
        self.assertIn("insurance_core_solvency_below_minimum", ids)
        self.assertIn("insurance_comprehensive_solvency_below_minimum", ids)
        self.assertIn("insurance_nbv_decline", ids)
        self.assertEqual(report["valuation"]["dcf"]["status"], "unsupported")

    def test_brokerage_uses_regulatory_capital_and_normalized_earnings(self):
        report = analyze_company(brokerage_company())
        self.assertEqual(report["support"]["sector_template"], "brokerage")
        metrics = report["sector"]["latest"]["metrics"]
        self.assertAlmostEqual(metrics["trading_revenue_share"]["value"], 18_000 / 32_000)
        scenario = report["valuation"]["scenarios"][0]
        self.assertEqual(scenario["pb"]["status"], "ok")
        self.assertEqual(scenario["normalized_pe"]["status"], "ok")
        ids = {item["id"] for item in report["flags"]}
        self.assertIn("broker_risk_coverage_ratio_below_minimum", ids)
        self.assertIn("broker_capital_leverage_ratio_below_minimum", ids)
        self.assertIn("broker_trading_income_concentration", ids)
        self.assertEqual(report["sector"]["regulatory_source"]["minimums"]["liquidity_coverage_ratio"], 1.0)


if __name__ == "__main__":
    unittest.main()
