import unittest

from backend.finance_engine import analyze_company, demo_company


def _fact(value, label, unit="CNY million"):
    return {"value": value, "unit": unit, "as_reported_label": label, "source": {"document": "annual-report.pdf", "page": 66, "table": "operating review"}}


def resource_company():
    periods = []
    for year, production, price, cost, reserves, ocf in ((2024, 100, 500, 250, 600, 30_000), (2025, 85, 400, 300, 500, 8_000)):
        periods.append({
            "end_date": f"{year}-12-31", "fiscal_year": year, "period_type": "FY", "duration_months": 12, "cumulative": True, "audited": True,
            "facts": {
                "production_volume": _fact(production, "商品煤产量", "million tonnes"),
                "realized_price": _fact(price, "平均销售价格", "CNY/tonne"),
                "unit_cash_cost": _fact(cost, "单位现金成本", "CNY/tonne"),
                "proven_probable_reserves": _fact(reserves, "经济可采储量", "million tonnes"),
                "sustaining_capex": _fact(9_000, "维持性资本开支"),
                "expansion_capex": _fact(3_000, "扩张性资本开支"),
                "capital_expenditure": _fact(12_000, "资本开支"),
                "operating_cash_flow": _fact(ocf, "经营现金流"),
                "revenue": _fact(60_000, "营业收入"),
                "net_income": _fact(15_000 if year == 2024 else 6_000, "归母净利润"),
                "interest_bearing_debt": _fact(20_000, "有息负债"),
                "cash": _fact(8_000, "现金"),
                "total_assets": _fact(120_000, "总资产"),
                "total_equity": _fact(70_000, "权益"),
                "shares_outstanding": _fact(10_000, "总股本", "million shares"),
            },
        })
    return {
        "company": {"symbol": "601999.SH", "name": "示例资源", "industry": "煤炭开采", "unit": "CNY million"},
        "unit": "CNY million", "periods": periods,
        "valuation": {
            "market_price": 8, "price_as_of": "2026-01-15", "shares_outstanding": 10_000,
            "sector": {"scenarios": [
                {"name": "base", "normalized_volume": 90, "normalized_price": 450, "unit_cash_cost": 280, "tax_rate": 0.25, "target_pe": 8, "recoverable_reserves": 500, "recovery_factor": 0.8, "discount_rate": 0.1, "mine_life_years": 12},
            ]},
        },
    }


def utility_company():
    periods = []
    for year, capacity, hours, tariff, fuel, ocf in ((2024, 10_000, 4_500, 0.42, 0.30, 18_000), (2025, 11_000, 3_900, 0.41, 0.43, 7_000)):
        periods.append({
            "end_date": f"{year}-12-31", "fiscal_year": year, "period_type": "FY", "duration_months": 12, "cumulative": True, "audited": True,
            "facts": {
                "installed_capacity": _fact(capacity, "控股装机容量", "MW"),
                "utilization_hours": _fact(hours, "平均利用小时", "hours"),
                "average_tariff": _fact(tariff, "平均上网电价", "CNY/kWh"),
                "fuel_cost_per_kwh": _fact(fuel, "单位燃料成本", "CNY/kWh"),
                "regulated_asset_base": _fact(80_000, "受监管资产基数"),
                "allowed_return": _fact(0.08, "准许收益率", "ratio"),
                "sustaining_capex": _fact(9_000, "维持性资本开支"),
                "capital_expenditure": _fact(15_000, "资本开支"),
                "operating_cash_flow": _fact(ocf, "经营现金流"),
                "revenue": _fact(50_000, "营业收入"),
                "net_income": _fact(5_000, "归母净利润"),
                "dividends_paid": _fact(2_000, "现金分红"),
                "interest_bearing_debt": _fact(90_000, "有息负债"),
                "cash": _fact(10_000, "现金"),
                "total_assets": _fact(180_000, "总资产"),
                "total_equity": _fact(40_000, "权益"),
                "shares_outstanding": _fact(10_000, "总股本", "million shares"),
            },
        })
    return {
        "company": {"symbol": "600999.SH", "name": "示例电力", "industry": "火力发电", "unit": "CNY million"},
        "unit": "CNY million", "periods": periods,
        "valuation": {
            "market_price": 6, "price_as_of": "2026-01-15", "shares_outstanding": 10_000,
            "sector": {"scenarios": [{"name": "base", "normalized_rab": 80_000, "allowed_return": 0.08, "tax_rate": 0.25, "target_pe": 12}]},
            "ddm": {"scenarios": [{"name": "base", "growth_rate": 0.03, "cost_of_equity": 0.09, "terminal_growth_rate": 0.02, "forecast_years": 5}]},
        },
    }


def property_company():
    periods = []
    for year, contract, collections, presales, restricted, impairment, ocf in (
        (2024, 80_000, 70_000, 76_000, 8_000, 2_000, 10_000),
        (2025, 58_000, 48_000, 55_000, 15_000, 8_000, -6_000),
    ):
        periods.append({
            "end_date": f"{year}-12-31", "fiscal_year": year, "period_type": "FY", "duration_months": 12, "cumulative": True, "audited": True,
            "facts": {
                "contract_liabilities": _fact(contract, "合同负债"),
                "sales_collections": _fact(collections, "销售回款"),
                "presales": _fact(presales, "全口径签约销售额"),
                "inventory": _fact(150_000, "开发成本及开发产品"),
                "project_inventory_impairment": _fact(impairment, "存货跌价损失"),
                "revenue": _fact(65_000, "营业收入"),
                "net_income": _fact(3_000, "归母净利润"),
                "operating_cash_flow": _fact(ocf, "经营活动现金流净额"),
                "cash": _fact(30_000, "货币资金"),
                "restricted_cash": _fact(restricted, "受限资金"),
                "interest_bearing_debt": _fact(60_000, "有息负债"),
                "debt_due_within_one_year": _fact(24_000, "一年内到期有息负债"),
                "total_assets": _fact(260_000, "总资产"),
                "total_equity": _fact(40_000, "股东权益"),
                "external_guarantees": _fact(12_000, "对外担保余额"),
                "shares_outstanding": _fact(10_000, "总股本", "million shares"),
            },
        })
    return {
        "company": {"symbol": "000999.SZ", "name": "示例地产", "industry": "房地产开发", "unit": "CNY million"},
        "unit": "CNY million", "periods": periods,
        "valuation": {
            "market_price": 2.8, "price_as_of": "2026-01-15", "shares_outstanding": 10_000,
            "sector": {"scenarios": [{"name": "base", "inventory_haircut": 0.12, "contract_recognition_rate": 0.7, "normalized_gross_margin": 0.18, "tax_rate": 0.25, "target_pb": 0.7, "additional_loss_provision": 3_000}]},
        },
    }


class SectorEngineTests(unittest.TestCase):
    def test_resource_template_uses_physical_economics_and_nav(self):
        report = analyze_company(resource_company())
        self.assertEqual(report["support"]["status"], "supported_sector")
        self.assertEqual(report["support"]["sector_template"], "cyclical_resource")
        metrics = report["sector"]["latest"]["metrics"]
        self.assertEqual(metrics["cash_margin_per_unit"]["value"], 100)
        self.assertAlmostEqual(metrics["reserve_life_years"]["value"], 500 / 85)
        self.assertEqual(metrics["production_volume"]["inputs"][0]["source"]["page"], 66)
        scenario = report["valuation"]["scenarios"][0]
        self.assertEqual(scenario["midcycle"]["status"], "ok")
        self.assertEqual(scenario["nav"]["status"], "ok")
        ids = {item["id"] for item in report["flags"]}
        self.assertIn("resource_price_cost_squeeze", ids)
        self.assertIn("resource_production_decline", ids)
        self.assertEqual(report["valuation"]["dcf"]["status"], "unsupported")

    def test_utility_template_separates_operating_drivers_and_regulated_value(self):
        report = analyze_company(utility_company())
        self.assertEqual(report["support"]["sector_template"], "utility")
        metrics = report["sector"]["latest"]["metrics"]
        self.assertAlmostEqual(metrics["tariff_fuel_spread"]["value"], -0.02)
        self.assertEqual(metrics["estimated_generation"]["value"], 42_900_000)
        self.assertEqual(report["valuation"]["regulated_return"]["scenarios"][0]["status"], "ok")
        self.assertEqual(report["valuation"]["ddm"]["status"], "ok")
        ids = {item["id"] for item in report["flags"]}
        self.assertIn("utility_negative_fuel_spread", ids)
        self.assertIn("utility_high_financial_leverage", ids)

    def test_property_template_uses_restricted_cash_and_adjusted_nav(self):
        report = analyze_company(property_company())
        self.assertEqual(report["support"]["sector_template"], "real_estate")
        metrics = report["sector"]["latest"]["metrics"]
        self.assertEqual(metrics["usable_cash"]["value"], 15_000)
        self.assertAlmostEqual(metrics["short_debt_cash_coverage"]["value"], 0.625)
        self.assertEqual(metrics["contract_liabilities"]["inputs"][0]["source"]["page"], 66)
        scenario = report["valuation"]["scenarios"][0]
        self.assertEqual(scenario["adjusted_nav"]["status"], "ok")
        self.assertEqual(scenario["adjusted_pb"]["status"], "ok")
        ids = {item["id"] for item in report["flags"]}
        self.assertIn("property_contract_pipeline_shrink", ids)
        self.assertIn("property_short_debt_cash_gap", ids)
        self.assertIn("property_inventory_impairment", ids)
        self.assertEqual(report["valuation"]["dcf"]["status"], "unsupported")

    def test_other_companies_keep_existing_routes(self):
        report = analyze_company(demo_company())
        self.assertEqual(report["support"]["status"], "supported_non_financial")
        raw = demo_company()
        raw["company"]["industry"] = "保险"
        insurance = analyze_company(raw)
        self.assertEqual(insurance["support"]["status"], "supported_sector")
        self.assertEqual(insurance["support"]["sector_template"], "insurance")


if __name__ == "__main__":
    unittest.main()
