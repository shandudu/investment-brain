import json
import os
import socket
import unittest
from unittest.mock import patch

from backend.finance_engine import analyze_company
from backend.tushare_provider import (
    TushareAPIError,
    TushareConfigurationError,
    TushareTransportError,
    canonicalize_tushare_data,
    fetch_company_financials,
    is_configured,
    normalize_ts_code,
)


def _statement_row(end_date, *, report_type="1", update_flag="1", comp_type="1", ann_date=None, **facts):
    return {
        "ts_code": "600888.SH",
        "ann_date": ann_date or (str(int(end_date[:4]) + 1) + "0328" if end_date.endswith("1231") else end_date),
        "f_ann_date": ann_date or (str(int(end_date[:4]) + 1) + "0328" if end_date.endswith("1231") else end_date),
        "end_date": end_date,
        "report_type": report_type,
        "comp_type": comp_type,
        "end_type": "4" if end_date.endswith("1231") else "3",
        "update_flag": update_flag,
        **facts,
    }


def _income(end_date, revenue, net_income, *, report_type="1", update_flag="1", comp_type="1", ann_date=None):
    revenue = int(revenue)
    net_income = int(net_income)
    return _statement_row(
        end_date,
        report_type=report_type,
        update_flag=update_flag,
        comp_type=comp_type,
        ann_date=ann_date,
        revenue=revenue,
        total_revenue=revenue,
        oper_cost=int(revenue * 0.70),
        operate_profit=int(net_income * 1.35),
        total_profit=int(net_income * 1.20),
        income_tax=int(net_income * 0.20),
        n_income=int(net_income * 1.02),
        n_income_attr_p=net_income,
        ebit=int(net_income * 1.40),
        fin_exp_int_exp=int(net_income * 0.08),
    )


def _balance(end_date, assets, *, report_type="1", update_flag="1", comp_type="1", ann_date=None):
    assets = int(assets)
    liabilities = int(assets * 0.45)
    equity = assets - liabilities
    return _statement_row(
        end_date,
        report_type=report_type,
        update_flag=update_flag,
        comp_type=comp_type,
        ann_date=ann_date,
        total_share=1_000_000_000,
        money_cap=int(assets * 0.12),
        accounts_receiv=int(assets * 0.09),
        inventories=int(assets * 0.10),
        total_cur_assets=int(assets * 0.45),
        goodwill=int(assets * 0.02),
        total_assets=assets,
        st_borr=int(assets * 0.08),
        non_cur_liab_due_1y=int(assets * 0.01),
        total_cur_liab=int(assets * 0.28),
        lt_borr=int(assets * 0.07),
        bond_payable=0,
        lease_liab=int(assets * 0.01),
        total_liab=liabilities,
        total_hldr_eqy_exc_min_int=int(equity * 0.98),
        total_hldr_eqy_inc_min_int=equity,
    )


def _cashflow(end_date, ocf, capex, *, report_type="1", update_flag="1", comp_type="1", ann_date=None):
    return _statement_row(
        end_date,
        report_type=report_type,
        update_flag=update_flag,
        comp_type=comp_type,
        ann_date=ann_date,
        n_cashflow_act=int(ocf),
        im_net_cashflow_oper_act=int(ocf),
        c_pay_acq_const_fiolta=int(capex),
        free_cashflow=int(ocf) - int(capex),
        depr_fa_coga_dpba=int(capex * 0.35),
        amort_intang_assets=int(capex * 0.05),
        lt_amort_deferred_exp=0,
    )


def _audit(end_date, result="标准无保留意见"):
    return {
        "ts_code": "600888.SH",
        "ann_date": str(int(end_date[:4]) + 1) + "0328",
        "end_date": end_date,
        "audit_result": result,
        "audit_agency": "示例会计师事务所",
        "audit_sign": "甲,乙",
    }


def _fixtures():
    # Six FY rows verify that the provider keeps exactly the latest five, while
    # latest/prior comparable Q3 rows let the finance engine construct TTM.
    annual_dates = [f"{year}1231" for year in range(2019, 2025)]
    income = [_income(item, 5_000_000_000 + index * 500_000_000, 500_000_000 + index * 50_000_000) for index, item in enumerate(annual_dates)]
    balance = [_balance(item, 9_000_000_000 + index * 500_000_000) for index, item in enumerate(annual_dates)]
    cashflow = [_cashflow(item, 650_000_000 + index * 60_000_000, 280_000_000 + index * 20_000_000) for index, item in enumerate(annual_dates)]
    income += [_income("20240930", 5_200_000_000, 520_000_000), _income("20250930", 5_900_000_000, 630_000_000)]
    balance += [_balance("20240930", 11_300_000_000), _balance("20250930", 12_100_000_000)]
    cashflow += [_cashflow("20240930", 680_000_000, 260_000_000), _cashflow("20250930", 770_000_000, 290_000_000)]
    return {
        "stock_rows": [{"ts_code": "600888.SH", "symbol": "600888", "name": "华星设备", "industry": "专用机械", "curr_type": "CNY"}],
        "income_rows": income,
        "balance_rows": balance,
        "cashflow_rows": cashflow,
        "audit_rows": [_audit(item) for item in annual_dates],
        "daily_basic_rows": [{"ts_code": "600888.SH", "trade_date": "20251231", "close": 12.5, "pe": 14.2, "pe_ttm": 13.5, "pb": 1.8, "total_share": 100_000, "total_mv": 1_250_000}],
    }


def _bank_fixtures():
    annual_dates = ["20231231", "20241231"]
    income = []
    balance = []
    cashflow = []
    for index, end_date in enumerate(annual_dates):
        income.append(_statement_row(
            end_date, comp_type="2", total_revenue=80_000_000_000 + index * 3_000_000_000,
            int_income=100_000_000_000 + index * 2_000_000_000,
            int_exp=55_000_000_000 + index * 3_000_000_000,
            comm_income=18_000_000_000, comm_exp=7_000_000_000,
            n_commis_income=11_000_000_000, operate_profit=30_000_000_000,
            total_profit=29_000_000_000, income_tax=5_000_000_000,
            n_income=24_000_000_000, n_income_attr_p=23_000_000_000,
            credit_impa_loss=12_000_000_000,
        ))
        balance.append(_statement_row(
            end_date, comp_type="2", total_share=10_000_000_000,
            cash_reser_cb=120_000_000_000, decr_in_disbur=700_000_000_000 + index * 30_000_000_000,
            depos_ib_deposits=820_000_000_000 + index * 35_000_000_000,
            total_assets=1_100_000_000_000 + index * 50_000_000_000,
            total_liab=1_000_000_000_000 + index * 45_000_000_000,
            total_hldr_eqy_exc_min_int=100_000_000_000 + index * 5_000_000_000,
            total_hldr_eqy_inc_min_int=100_000_000_000 + index * 5_000_000_000,
        ))
        cashflow.append(_statement_row(end_date, comp_type="2"))
    return {
        "stock_rows": [{"ts_code": "600888.SH", "symbol": "600888", "name": "华星银行", "industry": "银行", "curr_type": "CNY"}],
        "income_rows": income,
        "balance_rows": balance,
        "cashflow_rows": cashflow,
        "audit_rows": [_audit(item) for item in annual_dates],
        "daily_basic_rows": [{"ts_code": "600888.SH", "trade_date": "20251231", "close": 9.5, "pb": 0.9, "total_share": 1_000_000, "total_mv": 9_500_000}],
    }


def _api_response(rows, fields):
    return {"code": 0, "msg": None, "data": {"fields": list(fields), "items": [[row.get(field) for field in fields] for row in rows]}}


class CanonicalizationTests(unittest.TestCase):
    def test_symbol_normalization(self):
        self.assertEqual(normalize_ts_code("600888"), "600888.SH")
        self.assertEqual(normalize_ts_code("000001"), "000001.SZ")
        self.assertEqual(normalize_ts_code("830799"), "830799.BJ")
        self.assertEqual(normalize_ts_code("600888.sh"), "600888.SH")
        with self.assertRaisesRegex(ValueError, "六位数字"):
            normalize_ts_code("BAD")

    def test_latest_five_fy_and_comparable_interim_are_canonical(self):
        raw = canonicalize_tushare_data("600888", as_of="2025-12-31", years=5, **_fixtures())
        self.assertEqual([period["end_date"] for period in raw["periods"]], [
            "2020-12-31",
            "2021-12-31",
            "2022-12-31",
            "2023-12-31",
            "2024-09-30",
            "2024-12-31",
            "2025-09-30",
        ])
        latest = raw["periods"][-1]
        self.assertEqual(latest["period_type"], "Q3")
        self.assertEqual(latest["duration_months"], 9)
        self.assertTrue(latest["cumulative"])
        self.assertFalse(latest["audited"])
        self.assertEqual(latest["facts"]["revenue"]["value"], "5900")
        self.assertEqual(latest["facts"]["total_assets"]["value"], "12100")
        self.assertEqual(latest["facts"]["debt_due_within_one_year"]["value"], "1089")
        self.assertEqual(latest["facts"]["debt_due_within_one_year"]["source"]["completeness"], "complete")
        self.assertEqual(latest["facts"]["shares_outstanding"]["value"], "1000")
        self.assertEqual(latest["facts"]["shares_outstanding"]["unit"], "million shares")
        source = latest["facts"]["revenue"]["source"]
        self.assertEqual(source["api_name"], "income")
        self.assertEqual(source["report_scope"], "consolidated")
        self.assertEqual(source["basis"], "cumulative")
        self.assertNotIn("token", json.dumps(raw, ensure_ascii=False).lower())

        # The provider output is not merely schema-shaped: it is directly
        # accepted by the deterministic finance engine and yields a TTM.
        report = analyze_company(raw)
        self.assertEqual(report["support"]["status"], "supported_non_financial")
        self.assertEqual(report["periods"]["ttm"]["end_date"], "2025-09-30")
        self.assertEqual(report["periods"]["ttm"]["metrics"]["revenue"]["value"], 8200)

    def test_update_flag_and_restatement_choose_latest_consolidated_version(self):
        fixtures = _fixtures()
        fixtures["income_rows"] += [
            _income("20241231", 7_000_000_000, 700_000_000, report_type="5", update_flag="0", ann_date="20250320"),
            _income("20241231", 7_400_000_000, 740_000_000, report_type="4", update_flag="1", ann_date="20260320"),
            _income("20241231", 9_999_000_000, 999_000_000, report_type="6", update_flag="1", ann_date="20260321"),
            _income("20241231", 8_888_000_000, 888_000_000, report_type="2", update_flag="1", ann_date="20260322"),
        ]
        raw = canonicalize_tushare_data("600888.SH", as_of="2026-12-31", **fixtures)
        fy2024 = next(period for period in raw["periods"] if period["end_date"] == "2024-12-31")
        fact = fy2024["facts"]["revenue"]
        self.assertEqual(fact["value"], "7400")
        self.assertTrue(fact["restated"])
        self.assertEqual(fact["source"]["report_type"], "4")
        self.assertGreaterEqual(fact["source"]["candidate_versions"], 2)

    def test_as_of_does_not_leak_later_restatement(self):
        fixtures = _fixtures()
        fixtures["income_rows"] += [
            _income("20241231", 7_400_000_000, 740_000_000, report_type="4", update_flag="1", ann_date="20260320"),
        ]
        raw = canonicalize_tushare_data("600888.SH", as_of="2025-12-31", **fixtures)
        fy2024 = next(period for period in raw["periods"] if period["end_date"] == "2024-12-31")
        self.assertEqual(fy2024["facts"]["revenue"]["value"], "7500")
        self.assertEqual(fy2024["facts"]["revenue"]["source"]["report_type"], "1")

    def test_incomplete_debt_components_remain_missing_not_zero(self):
        fixtures = _fixtures()
        latest_balance = next(row for row in fixtures["balance_rows"] if row["end_date"] == "20250930")
        latest_balance["lease_liab"] = None
        raw = canonicalize_tushare_data("600888.SH", as_of="2025-12-31", **fixtures)
        latest = raw["periods"][-1]
        self.assertNotIn("interest_bearing_debt", latest["facts"])

    def test_financial_company_types_route_to_dedicated_canonical_schemas(self):
        raw = canonicalize_tushare_data("600888.SH", as_of="2025-12-31", **_bank_fixtures())
        self.assertEqual(raw["valuation"]["company_type"], "2")
        self.assertEqual(raw["provider"]["company_type"], "2")
        self.assertEqual(raw["periods"][-1]["facts"]["net_interest_income"]["value"], "44000")
        self.assertEqual(raw["periods"][-1]["facts"]["loans_and_advances"]["value"], "730000")
        report = analyze_company(raw)
        self.assertEqual(report["support"]["status"], "supported_bank")
        self.assertIn("net_interest_margin", report["bank"]["coverage"]["missing"])

        fixtures = _fixtures()
        fixtures["stock_rows"][0]["industry"] = "保险"
        for api_name in ("income_rows", "balance_rows", "cashflow_rows"):
            for row in fixtures[api_name]:
                row["comp_type"] = "3"
        insurance = canonicalize_tushare_data("600888.SH", **fixtures)
        self.assertEqual(insurance["provider"]["company_type"], "3")
        self.assertEqual(insurance["valuation"]["financial_sector_template"], "insurance")
        insurance_report = analyze_company(insurance)
        self.assertEqual(insurance_report["support"]["sector_template"], "insurance")
        self.assertIn("core_solvency_ratio", insurance_report["sector"]["coverage"]["missing"])


class FetchTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("TUSHARE_TOKEN", None)

    def test_missing_token_fails_before_any_http_call(self):
        os.environ.pop("TUSHARE_TOKEN", None)
        called = False

        def should_not_call(*args):
            nonlocal called
            called = True
            raise AssertionError("HTTP should not be called")

        self.assertFalse(is_configured())
        with self.assertRaisesRegex(TushareConfigurationError, "TUSHARE_TOKEN"):
            fetch_company_financials("600888", http_post=should_not_call)
        self.assertFalse(called)

    def test_fetch_uses_official_http_shape_without_returning_token(self):
        token = "super-secret-token-value"
        os.environ["TUSHARE_TOKEN"] = token
        fixtures = _fixtures()
        rows_by_api = {
            "stock_basic": fixtures["stock_rows"],
            "income": fixtures["income_rows"],
            "balancesheet": fixtures["balance_rows"],
            "cashflow": fixtures["cashflow_rows"],
            "fina_audit": fixtures["audit_rows"],
            "daily_basic": fixtures["daily_basic_rows"],
        }
        calls = []

        def fake_http(endpoint, body, headers, timeout):
            payload = json.loads(body.decode("utf-8"))
            calls.append(payload)
            fields = payload["fields"].split(",") if payload["fields"] else []
            return json.dumps(_api_response(rows_by_api[payload["api_name"]], fields), ensure_ascii=False).encode("utf-8")

        raw = fetch_company_financials("600888", as_of="2025-12-31", http_post=fake_http)
        self.assertEqual([call["api_name"] for call in calls], [
            "stock_basic",
            "income",
            "balancesheet",
            "cashflow",
            "fina_audit",
            "daily_basic",
        ])
        self.assertTrue(all(call["token"] == token for call in calls))
        self.assertEqual(calls[-1]["params"]["end_date"], "20251231")
        output = json.dumps(raw, ensure_ascii=False)
        self.assertNotIn(token, output)
        self.assertEqual(raw["valuation"]["market_price"], "12.5")
        self.assertEqual(raw["valuation"]["shares_outstanding"], "1000")
        self.assertEqual(raw["valuation"]["shares_as_of"], "2025-12-31")
        self.assertEqual(raw["valuation"]["shares_as_of"], raw["valuation"]["price_as_of"])
        self.assertEqual(raw["valuation"]["market_cap"], "12500")

    def test_optional_market_api_failure_is_a_warning_and_token_is_redacted(self):
        token = "dont-print-this-token"
        os.environ["TUSHARE_TOKEN"] = token
        fixtures = _fixtures()
        rows_by_api = {
            "stock_basic": fixtures["stock_rows"],
            "income": fixtures["income_rows"],
            "balancesheet": fixtures["balance_rows"],
            "cashflow": fixtures["cashflow_rows"],
            "fina_audit": fixtures["audit_rows"],
        }

        def fake_http(endpoint, body, headers, timeout):
            payload = json.loads(body.decode("utf-8"))
            if payload["api_name"] == "daily_basic":
                return {"code": 2002, "msg": f"没有权限 token={token}", "data": None}
            fields = payload["fields"].split(",") if payload["fields"] else []
            return _api_response(rows_by_api[payload["api_name"]], fields)

        raw = fetch_company_financials("600888.SH", as_of="2025-12-31", http_post=fake_http)
        warning = raw["provider"]["warnings"][0]
        self.assertIn("市场估值数据未获取", warning)
        self.assertIn("[REDACTED]", warning)
        self.assertNotIn(token, warning)

    def test_required_api_error_is_clear_and_redacted(self):
        token = "sensitive-token"
        os.environ["TUSHARE_TOKEN"] = token

        def fake_http(endpoint, body, headers, timeout):
            payload = json.loads(body.decode("utf-8"))
            if payload["api_name"] == "stock_basic":
                return {"code": 2002, "msg": f"token={token} 没有权限", "data": None}
            raise AssertionError("unexpected second call")

        with self.assertRaises(TushareAPIError) as raised:
            fetch_company_financials("600888", http_post=fake_http)
        self.assertIn("code=2002", str(raised.exception))
        self.assertIn("[REDACTED]", str(raised.exception))
        self.assertNotIn(token, str(raised.exception))

    def test_timeout_is_clear(self):
        os.environ["TUSHARE_TOKEN"] = "token"

        def timeout(*args):
            raise socket.timeout("timed out")

        with self.assertRaisesRegex(TushareTransportError, "超时"):
            fetch_company_financials("600888", timeout=1.5, http_post=timeout)


if __name__ == "__main__":
    unittest.main()
