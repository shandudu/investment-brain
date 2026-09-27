import tempfile
import unittest
import gc
import sqlite3
import os
from unittest.mock import MagicMock, patch
from pathlib import Path
from datetime import date, timedelta

from backend.app import Application, SYSTEM_PROMPT, _filing_source_provider
from backend.finance_engine import analyze_company, demo_company
import json
from backend.free_provider import FreeProviderError
from backend.knowledge import create_doctrine_card, create_library, list_doctrine_card_versions, list_libraries, update_doctrine_card
from backend.tests.test_bank_engine import bank_company
from backend.tests.test_financial_sector_engine import brokerage_company, insurance_company
from backend.tests.test_sector_engine import property_company, resource_company, utility_company


class ApplicationTests(unittest.TestCase):
    def test_manual_filing_provider_uses_canonical_cninfo_identity(self):
        self.assertEqual(
            _filing_source_provider("https://static.cninfo.com.cn/finalpage/2026-03-28/a.pdf"),
            "cninfo_public",
        )
        self.assertEqual(
            _filing_source_provider("https://www.sse.com.cn/disclosure/a.pdf"),
            "official_manual",
        )
        # A lookalike host must not be trusted or merged into CNINFO records.
        self.assertEqual(_filing_source_provider("https://cninfo.com.cn.evil.test/a.pdf"), "official_manual")

    def test_dashboard_scopes_avoid_unused_demo_and_heavy_page_payloads(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            core = app.dashboard(scope="core")
            self.assertEqual(core["dashboard_scope"], "core")
            self.assertNotIn("demo_analysis", core)
            self.assertNotIn("doctrine_cards", core)
            self.assertNotIn("filings", core)
            self.assertLess(len(json.dumps(core, ensure_ascii=False).encode("utf-8")), 50_000)
            knowledge = app.dashboard(scope="knowledge")
            self.assertIn("doctrine_cards", knowledge)
            self.assertIn("document_stats", knowledge)
            self.assertNotIn("filings", knowledge)
            filings = app.dashboard(scope="filings")
            self.assertIn("filings", filings)
            self.assertIn("filing_stats", filings)
            self.assertNotIn("doctrine_cards", filings)
            with self.assertRaisesRegex(ValueError, "scope"):
                app.dashboard(scope="unknown")
            del app
            gc.collect()

    def test_dashboard_missing_price_does_not_substitute_cost(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.save_holding({"symbol": "600519", "quantity": 10, "cost": 100})
            app.save_holding({"symbol": "000333", "quantity": 5, "cost": 20})
            dashboard = app.dashboard()
            self.assertEqual(dashboard["portfolio"]["value"], 0)
            self.assertEqual(dashboard["portfolio"]["pnl"], 0)
            self.assertEqual(dashboard["portfolio"]["priced_positions"], 0)
            self.assertEqual(dashboard["portfolio"]["value"], dashboard["portfolio_diagnostics"]["total_value_for_diagnostics"])
            missing = next(item for item in dashboard["portfolio_diagnostics"]["positions"] if item["symbol"] == "600519")
            self.assertIsNone(missing["return_pct"])
            self.assertEqual(missing["decision"]["status"], "等待条件")
            self.assertTrue(any("缺少可用价格" in reason for reason in missing["decision"]["reasons"]))
            self.assertTrue(any("最大承受亏损" in reason for reason in missing["decision"]["reasons"]))
            with self.assertRaisesRegex(ValueError, "必须大于 0"):
                app.save_holding({"symbol": "000333", "quantity": 5, "cost": 20, "current_price": 0})
            del app
            gc.collect()

    def test_legacy_zero_prices_are_missing_not_total_loss_signals(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.save_holding({"symbol": "600519", "quantity": 1, "cost": 100, "max_loss_pct": 10, "thesis": "现金流"})
            app.save_watch({"symbol": "600519", "name": "贵州茅台"})
            payload = demo_company()
            payload["valuation"]["market_price"] = "0"
            with app.connect() as conn:
                conn.execute("UPDATE holdings SET current_price=0, current_price_as_of=? WHERE symbol='600519'", (date.today().isoformat(),))
                conn.execute(
                    "INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json) VALUES ('600519.SH', '', 'bad-fixture', ?)",
                    (json.dumps(payload),),
                )
            conn.close()
            position = app.portfolio_diagnostics()["positions"][0]
            watched = app.list_watchlist()[0]
            self.assertIsNone(position["price"])
            self.assertIsNone(position["return_pct"])
            self.assertFalse(position["decision"]["loss_limit_triggered"])
            self.assertEqual(position["decision"]["status"], "等待条件")
            self.assertIsNone(watched["quote"]["market_price"])
            del app
            del conn
            gc.collect()

    def test_holding_boolean_numbers_are_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            for field in ("quantity", "cost", "current_price", "max_loss_pct"):
                for value in (True, False):
                    with self.subTest(field=field, value=value):
                        payload = {"symbol": "600519", "quantity": 10, "cost": 1200, field: value}
                        with self.assertRaisesRegex(ValueError, "布尔值"):
                            app.save_holding(payload)
            self.assertEqual(app.dashboard()["holdings"], [])
            del app
            gc.collect()

    def test_snapshot_age_uses_utc_independent_of_machine_timezone(self):
        from backend.app import _snapshot_age_hours
        from datetime import datetime, timezone
        now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc).timestamp()
        with patch("backend.app.time.time", return_value=now):
            with patch("backend.app.time.mktime", side_effect=AssertionError("Local timezone must not be used")):
                self.assertEqual(_snapshot_age_hours("2026-09-08 11:00:00"), 1)
                self.assertEqual(_snapshot_age_hours("2026-09-08 12:00:00"), 0)
                self.assertEqual(_snapshot_age_hours("2026-09-08 13:00:00"), 0)
                self.assertEqual(_snapshot_age_hours("2026-09-08T19:00:00+08:00"), 1)
                self.assertEqual(_snapshot_age_hours("2026-09-08T12:00:00Z"), 0)
                self.assertEqual(_snapshot_age_hours("invalid"), float("inf"))

    def test_dashboard_and_holding_persistence(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            dashboard = app.dashboard()
            self.assertNotIn("demo_analysis", dashboard)
            self.assertEqual(dashboard["dashboard_scope"], "all")
            holding = app.save_holding({"symbol": "000001", "name": "平安银行", "quantity": 10, "cost": 11.5})
            self.assertEqual(holding["symbol"], "000001")
            self.assertEqual(len(app.dashboard()["holdings"]), 1)
            del app
            gc.collect()

    def test_settings_never_store_api_key(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            result = app.update_settings({"api_base_url": "https://example.test/v1", "secret": "no"})
            self.assertEqual(result["api_base_url"], "https://example.test/v1")
            self.assertNotIn("secret", result)
            del app
            gc.collect()

    def test_legacy_unsafe_api_url_is_quarantined_without_blocking_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            conn = app.connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO settings(key, value) VALUES ('api_base_url', ?)",
                    (json.dumps("file:///C:/Windows"),),
                )
                conn.commit()
            finally:
                conn.close()
            settings = app.settings()
            self.assertEqual(settings["api_base_url"], "")
            self.assertTrue(settings["settings_warnings"])
            saved = app.update_settings({"api_base_url": "", "monthly_token_limit": 2000})
            self.assertEqual(saved["monthly_token_limit"], 2000)
            del app
            gc.collect()

    def test_evidence_bundle_combines_financials_holdings_and_knowledge(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            supplied = demo_company()
            supplied["company"]["symbol"] = "600519"
            supplied["company"]["name"] = "测试公司"
            bundle = app.build_evidence_bundle({"symbol": "600519", "company": supplied, "knowledge_query": "现金流"})
            self.assertEqual(bundle["schema_version"], "evidence-bundle/0.4")
            self.assertEqual(bundle["holdings"], [])
            self.assertEqual(bundle["financial_analysis"]["engine"], "deterministic_decimal_finance_engine")
            self.assertEqual(bundle["financial_analysis"]["ai_compaction"]["method"], "deduplicated_input_refs")
            revenue = bundle["financial_analysis"]["periods"]["ttm"]["metrics"]["revenue"]
            self.assertTrue(revenue["input_refs"])
            self.assertNotIn("inputs", revenue)
            source_refs = {item["ref"] for item in bundle["financial_analysis"]["source_facts"]}
            self.assertTrue(set(revenue["input_refs"]).issubset(source_refs))
            evidence_bytes = len(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            self.assertLess(evidence_bytes, 120_000)
            self.assertIn("knowledge_evidence", bundle)
            self.assertIn("approved_doctrine_rules", bundle)
            self.assertIn("doctrine_by_library", bundle)
            self.assertIn("doctrine_relationships", bundle)
            del app
            gc.collect()

    def test_symbol_evidence_uses_cached_company_without_legacy_provider_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            cached = demo_company()
            cached["company"]["symbol"] = "600036.SH"
            cached["company"]["name"] = "招商银行测试快照"
            cached["company"]["industry"] = "bank"
            with app.connect() as conn:
                conn.execute(
                    """INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json)
                       VALUES ('600036.SH', '', 'cached-test', ?)""",
                    (json.dumps(cached, ensure_ascii=False),),
                )
            conn.close()

            with patch("backend.app.tushare_is_configured", return_value=False), patch(
                "backend.app.fetch_free_company_financials",
                side_effect=AssertionError("fresh cache must be used before any provider call"),
            ):
                bundle = app.build_evidence_bundle({"symbol": "600036", "industry": "bank"})

            self.assertEqual(bundle["requested_symbol"], "600036")
            self.assertEqual(bundle["financial_analysis"]["company"]["symbol"], "600036.SH")
            self.assertEqual(bundle["financial_analysis"]["company"]["name"], "招商银行测试快照")
            self.assertNotEqual(bundle["financial_analysis"]["company"]["symbol"], "600888")
            self.assertEqual(bundle["financial_source"]["cache"], "fresh")
            self.assertEqual(bundle["financial_source"]["mode"], "cached")
            del app
            del conn
            gc.collect()

    def test_cached_source_mode_comes_from_snapshot_provider_not_current_token(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            snapshot = demo_company()
            with app.connect() as conn:
                conn.executemany(
                    """INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json)
                       VALUES (?, '', ?, ?)""",
                    [
                        ("600036.SH", "akshare", json.dumps(snapshot)),
                        ("000001.SZ", "tushare", json.dumps(snapshot)),
                    ],
                )
            conn.close()

            with patch("backend.app.tushare_is_configured", return_value=True):
                _, akshare_source = app.company_financial_data("600036")
            with patch("backend.app.tushare_is_configured", return_value=False):
                _, tushare_source = app.company_financial_data("000001")

            self.assertEqual(akshare_source["provider"], "akshare")
            self.assertEqual(akshare_source["mode"], "free")
            self.assertEqual(tushare_source["provider"], "tushare")
            self.assertEqual(tushare_source["mode"], "tushare")
            del app
            del conn
            gc.collect()

    def test_only_confirmed_filing_facts_enter_ai_evidence_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            with app.connect() as conn:
                artifact_id = conn.execute(
                    """INSERT INTO filing_artifacts(sha256, relative_path, byte_size, extraction_status)
                       VALUES (?, '600036/test.pdf', 10, 'ready')""",
                    ("a" * 64,),
                ).lastrowid
                filing_id = conn.execute(
                    """INSERT INTO filings(source_key, symbol, title, source_provider, artifact_id, retrieved_at)
                       VALUES ('source-1', '600036', '招商银行2025年度报告', 'cninfo_public', ?, '2026-03-28')""",
                    (artifact_id,),
                ).lastrowid
                conn.executemany(
                    """INSERT INTO filing_facts(
                           filing_id, metric, label, value, raw_value, period_end, page_number,
                           locator, excerpt, extraction_method, review_status)
                       VALUES (?, ?, ?, ?, ?, '2025-12-31', ?, ?, ?, 'deterministic_bank_fact_v1', ?)""",
                    [
                        (filing_id, "nonperforming_loan_ratio", "不良贷款率", 0.0094, "0.94%", 20, "pdf:p:000020", "本集团不良贷款率0.94%", "confirmed"),
                        (filing_id, "capital_adequacy_ratio", "资本充足率", 0.1824, "18.24%", 30, "pdf:p:000030", "本集团资本充足率18.24%", "pending"),
                    ],
                )
            conn.close()
            bundle = app.build_evidence_bundle({"symbol": "600036", "company": demo_company()})
            self.assertEqual(len(bundle["confirmed_filing_facts"]), 1)
            fact = bundle["confirmed_filing_facts"][0]
            self.assertEqual(fact["metric"], "nonperforming_loan_ratio")
            self.assertEqual(fact["locator"], "pdf:p:000020")
            self.assertEqual(fact["artifact_sha256"], "a" * 64)
            del app
            gc.collect()

    def test_ai_filing_evidence_is_balanced_across_risk_categories(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            with app.connect() as conn:
                artifact_id = conn.execute(
                    "INSERT INTO filing_artifacts(sha256, relative_path, byte_size, extraction_status) VALUES (?, '600036/report.pdf', 10, 'ready')",
                    ("c" * 64,),
                ).lastrowid
                filing_id = conn.execute(
                    """INSERT INTO filings(source_key, symbol, title, source_provider, artifact_id, disclosed_at, retrieved_at)
                       VALUES ('balanced-report', '600036', '招商银行2025年度报告', 'test', ?, '2026-03-30', '2026-03-30')""",
                    (artifact_id,),
                ).lastrowid
                conn.executemany(
                    """INSERT INTO filing_evidence(
                           filing_id, category, severity, title, excerpt, page_number, locator, review_status)
                       VALUES (?, 'audit_opinion', 'red', '审计意见', ?, ?, ?, 'pending')""",
                    [(filing_id, f"审计意见重复线索 {page}", page, f"pdf:p:{page:06d}") for page in range(1, 26)],
                )
                conn.execute(
                    """INSERT INTO filing_evidence(
                           filing_id, category, severity, title, excerpt, page_number, locator, review_status)
                       VALUES (?, 'restricted_cash', 'review', '受限资金', '保证金存款使用受限', 300, 'pdf:p:000300', 'pending')""",
                    (filing_id,),
                )
            conn.close()
            bundle = app.build_evidence_bundle({"symbol": "600036", "company": demo_company()})
            categories = {item["category"] for item in bundle["pending_filing_leads"]}
            self.assertEqual(bundle["schema_version"], "evidence-bundle/0.4")
            self.assertEqual(categories, {"audit_opinion", "restricted_cash"})
            self.assertEqual(bundle["confirmed_filing_evidence"], [])
            self.assertNotIn("filing_evidence", bundle)
            self.assertNotIn("superseded_filing_evidence", bundle)
            self.assertIn("严禁改写为该公司已经发生的事实", SYSTEM_PROMPT)
            self.assertEqual(bundle["filing_evidence_selection"]["current_active_total"], 26)
            self.assertEqual(bundle["filing_evidence_selection"]["current_included"], 20)
            self.assertEqual(bundle["filing_evidence_selection"]["current_pending_included"], 20)
            self.assertEqual(bundle["filing_evidence_selection"]["current_omitted"], 6)
            self.assertIn("按风险类别轮询", bundle["filing_evidence_selection"]["policy"])
            del app
            del conn
            gc.collect()

    def test_ai_filing_selection_discloses_omissions_and_keeps_critical_types(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            with app.connect() as conn:
                artifact_id = conn.execute(
                    "INSERT INTO filing_artifacts(sha256, relative_path, byte_size, extraction_status) VALUES (?, '600036/shared.pdf', 10, 'ready')",
                    ("f" * 64,),
                ).lastrowid
                rows = []
                for index in range(9):
                    rows.append((f"quarter-{index}", "季度报告", "quarterly_report", f"2026-0{min(index + 1, 9)}-20", artifact_id))
                rows.extend([
                    ("old-inquiry", "监管问询函回复", "inquiry", "2025-03-01", artifact_id),
                    ("old-annual", "2024年度报告", "annual_report", "2025-02-20", artifact_id),
                    ("old-audit", "2024年度审计报告", "audit_report", "2025-02-20", artifact_id),
                ])
                conn.executemany(
                    """INSERT INTO filings(source_key, symbol, title, filing_type, source_provider, artifact_id, disclosed_at, retrieved_at)
                       VALUES (?, '600036', ?, ?, 'test', ?, ?, ?)""",
                    [(key, title, filing_type, artifact, disclosed, disclosed) for key, title, filing_type, disclosed, artifact in rows],
                )
            conn.close()
            bundle = app.build_evidence_bundle({"symbol": "600036", "company": demo_company()})
            selection = bundle["filing_evidence_selection"]
            selected_types = set(selection["selected_filing_types"])
            self.assertEqual(selection["filings_available"], 12)
            self.assertEqual(selection["filings_scanned"], 8)
            self.assertEqual(selection["filings_omitted"], 4)
            self.assertTrue({"inquiry", "annual_report", "audit_report"}.issubset(selected_types))
            self.assertIn("未扫描公告", selection["policy"])
            del app
            del conn
            gc.collect()

    def test_superseded_filing_facts_are_historical_not_current_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            raw = bank_company()
            del raw["periods"][-1]["facts"]["net_interest_margin"]
            with app.connect() as conn:
                conn.execute(
                    "INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json) VALUES ('600000.SH', '', 'fixture', ?)",
                    (json.dumps(raw, ensure_ascii=False),),
                )
                original_artifact = conn.execute(
                    """INSERT INTO filing_artifacts(sha256, relative_path, byte_size, extraction_status)
                       VALUES (?, '600000/original.pdf', 10, 'ready')""",
                    ("d" * 64,),
                ).lastrowid
                correction_artifact = conn.execute(
                    """INSERT INTO filing_artifacts(sha256, relative_path, byte_size, extraction_status)
                       VALUES (?, '600000/correction.pdf', 10, 'ready')""",
                    ("e" * 64,),
                ).lastrowid
                original_id = conn.execute(
                    """INSERT INTO filings(
                           source_key, symbol, title, filing_type, report_period, source_provider,
                           source_url, artifact_id, disclosed_at, retrieved_at)
                       VALUES ('original-2025', '600000', '示例银行2025年度报告', 'annual_report',
                               '2025-12-31', 'cninfo_public', 'https://example.test/original.pdf', ?,
                               '2026-03-20', '2026-03-20')""",
                    (original_artifact,),
                ).lastrowid
                correction_id = conn.execute(
                    """INSERT INTO filings(
                           source_key, symbol, title, filing_type, report_period, source_provider,
                           source_url, artifact_id, disclosed_at, retrieved_at, is_correction,
                           supersedes_filing_id)
                       VALUES ('correction-2025', '600000', '示例银行2025年度报告（修订版）',
                               'annual_report', '2025-12-31', 'cninfo_public',
                               'https://example.test/correction.pdf', ?, '2026-04-02', '2026-04-02', 1, ?)""",
                    (correction_artifact, original_id),
                ).lastrowid
                for filing_id, value, raw_value, page in (
                    (original_id, 0.0180, "1.80%", 70),
                    (correction_id, 0.0190, "1.90%", 72),
                ):
                    conn.execute(
                        """INSERT INTO filing_facts(
                               filing_id, metric, label, value, raw_value, period_end, page_number,
                               locator, excerpt, extraction_method, review_status)
                           VALUES (?, 'net_interest_margin', '净利息收益率', ?, ?, '2025-12-31', ?,
                                   ?, ?, 'deterministic_bank_fact_v1', 'confirmed')""",
                        (filing_id, value, raw_value, page, f"pdf:p:{page:06d}", f"净利息收益率{raw_value}"),
                    )
                    conn.execute(
                        """INSERT INTO filing_evidence(
                               filing_id, category, severity, title, excerpt, page_number, locator, review_status)
                           VALUES (?, 'accounting_change', 'review', '会计政策、估计或差错更正', ?, ?, ?, 'confirmed')""",
                        (filing_id, f"报告披露口径 {raw_value}", page, f"pdf:p:{page:06d}"),
                    )
            conn.close()

            bundle = app.build_evidence_bundle({"symbol": "600000"})
            self.assertEqual([item["filing_id"] for item in bundle["confirmed_filing_facts"]], [correction_id])
            self.assertEqual([item["filing_id"] for item in bundle["superseded_filing_facts"]], [original_id])
            self.assertEqual([item["filing_id"] for item in bundle["confirmed_filing_evidence"]], [correction_id])
            self.assertEqual([item["filing_id"] for item in bundle["superseded_confirmed_filing_evidence"]], [original_id])
            self.assertEqual(bundle["superseded_filing_facts"][0]["filing_status"], "superseded")

            historical = app.build_evidence_bundle({"symbol": "600000", "as_of": "2026-03-31", "company": raw})
            self.assertEqual(historical["filing_disclosure_cutoff"], "2026-03-31")
            self.assertEqual([item["filing_id"] for item in historical["confirmed_filing_facts"]], [original_id])
            self.assertEqual(historical["superseded_filing_facts"], [])
            self.assertIsNone(historical["filing_records"][0]["superseded_by_id"])
            historical_company = app._enrich_with_confirmed_filing_facts(
                raw,
                "600000",
                disclosed_on_or_before="2026-03-31",
            )
            historical_metric = analyze_company(historical_company)["bank"]["latest"]["metrics"]["net_interest_margin"]
            self.assertAlmostEqual(historical_metric["value"], 0.0180)
            self.assertEqual(
                historical_company["provider"]["confirmed_filing_facts"]["disclosure_cutoff"],
                "2026-03-31",
            )
            before_original = app.build_evidence_bundle({"symbol": "600000", "as_of": "2026-03-01", "company": raw})
            self.assertEqual(before_original["filing_records"], [])
            pre_disclosure_company = app._enrich_with_confirmed_filing_facts(
                raw,
                "600000",
                disclosed_on_or_before="2026-03-01",
            )
            self.assertNotIn("confirmed_filing_facts", pre_disclosure_company.get("provider", {}))
            with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
                app.build_evidence_bundle({"symbol": "600000", "as_of": "2026/03/31"})
            with self.assertRaisesRegex(ValueError, "不能晚于今天"):
                app.build_evidence_bundle({"symbol": "600000", "as_of": (date.today() + timedelta(days=1)).isoformat()})

            company, source = app.company_financial_data("600000")
            metric = analyze_company(company)["bank"]["latest"]["metrics"]["net_interest_margin"]
            self.assertAlmostEqual(metric["value"], 0.0190)
            self.assertEqual(source["confirmed_filing_facts"]["applied"][0]["filing_id"], correction_id)
            self.assertEqual(app.filing_facts_for_review(original_id)[0]["application_status"], "superseded")
            self.assertEqual(app.filing_facts_for_review(correction_id)[0]["application_status"], "applied")
            del app
            gc.collect()

    def test_library_can_be_disabled_without_deleting_knowledge_history(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            library_id = create_library(app.db_path, "格雷厄姆体系")
            card_id = create_doctrine_card(app.db_path, library_id, "净流动资产折价", "价格应显著低于保守资产价值")
            update_doctrine_card(app.db_path, card_id, review_status="approved", enabled=True)

            result = app.set_library_enabled({"id": library_id, "action": "disable"})
            self.assertEqual(result["library"]["enabled"], 0)
            self.assertIn("规则立即退出", result["analysis_policy"])
            library = next(item for item in list_libraries(app.db_path) if item["id"] == library_id)
            self.assertEqual(library["card_count"], 1)
            self.assertEqual(library["enabled"], 0)

            enabled = app.set_library_enabled({"id": library_id, "action": "enable"})
            self.assertEqual(enabled["library"]["enabled"], 1)
            revised = app.update_doctrine_card_from_review({
                "id": card_id,
                "action": "edit",
                "title": "净流动资产折价（修订）",
                "content": "价格应低于经过负债与流动性折扣后的保守资产价值",
            })["card"]
            self.assertEqual(revised["review_status"], "pending")
            self.assertEqual(revised["enabled"], 0)
            self.assertEqual(revised["current_version"], 3)
            self.assertEqual(list_doctrine_card_versions(app.db_path, card_id)[0]["change_kind"], "edit_requires_review")
            with self.assertRaisesRegex(ValueError, "enable 或 disable"):
                app.set_library_enabled({"id": library_id, "action": "delete"})
            with self.assertRaisesRegex(ValueError, "未知的规则卡操作"):
                app.update_doctrine_card_from_review({"id": card_id, "action": "delete"})
            del app
            gc.collect()

    def test_evidence_bundle_keeps_doctrines_separate_and_exposes_relationships(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            dividend = create_library(app.db_path, "红利体系")
            conservative = create_library(app.db_path, "保守体系")
            first = create_doctrine_card(
                app.db_path,
                dividend,
                "股息率底线",
                "股息率至少4%才优先研究",
                metrics=["dividend_yield"],
                thresholds=["至少4%"],
                tags=["股息率"],
            )
            second = create_doctrine_card(
                app.db_path,
                conservative,
                "避免高股息陷阱",
                "周期顶部股息率不超过3%才考虑",
                metrics=["dividend_yield"],
                thresholds=["不超过3%"],
                tags=["股息率"],
            )
            update_doctrine_card(app.db_path, first, review_status="approved", enabled=True)
            update_doctrine_card(app.db_path, second, review_status="approved", enabled=True)
            bundle = app.build_evidence_bundle({"knowledge_query": "股息率"})
            self.assertEqual(
                {item["library_name"] for item in bundle["doctrine_by_library"]},
                {"红利体系", "保守体系"},
            )
            self.assertEqual(bundle["doctrine_relationships"]["summary"]["conflicts"], 1)
            self.assertIn("禁止把不同体系混成平均观点", bundle["evidence_policy"]["doctrine_separation"])
            del app
            gc.collect()

    def test_only_approved_enabled_doctrine_cards_guide_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            library_id = app.dashboard()["libraries"][0]["id"]
            card_id = create_doctrine_card(
                app.db_path,
                library_id,
                "自由现金流覆盖分红",
                "只有自由现金流长期覆盖现金分红，股息才具备持续性。",
                tags=["现金流", "红利"],
            )
            pending = app.build_evidence_bundle({"knowledge_query": "现金流"})
            self.assertEqual(pending["approved_doctrine_rules"], [])
            update_doctrine_card(
                app.db_path, card_id, {"review_status": "approved", "enabled": True}
            )
            approved = app.build_evidence_bundle({"knowledge_query": "现金流"})
            self.assertEqual(approved["approved_doctrine_rules"][0]["id"], card_id)
            update_doctrine_card(app.db_path, card_id, {"enabled": False})
            disabled = app.build_evidence_bundle({"knowledge_query": "现金流"})
            self.assertEqual(disabled["approved_doctrine_rules"], [])
            del app
            gc.collect()

    def test_real_company_path_requires_tushare_token_without_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            with patch("backend.app.tushare_is_configured", return_value=False), patch("backend.app.fetch_free_company_financials", side_effect=FreeProviderError("没有免费数据")):
                with self.assertRaisesRegex(RuntimeError, "免费数据源"):
                    app.company_financial_data("600519")
            del app
            gc.collect()

    def test_holding_rejects_non_finite_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            with self.assertRaisesRegex(ValueError, "有限数字"):
                app.save_holding({"symbol": "000001", "quantity": "NaN", "cost": 1})
            del app
            gc.collect()

    def test_holding_normalizes_market_suffix_and_can_be_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            saved = app.save_holding({"symbol": "600036.SH", "quantity": 10, "cost": 30})
            self.assertEqual(saved["symbol"], "600036")
            app.delete_holding("600036.SH")
            self.assertEqual(app.dashboard()["holdings"], [])
            del app
            gc.collect()

    def test_holdings_csv_import_accepts_chinese_headers_and_reports_bad_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            result = app.import_holdings_csv("代码,名称,数量,成本价,当前价,买入逻辑\n600519,贵州茅台,10,1200,1230,等待估值\n000001,平安银行,NaN,10,11,坏数据\n")
            self.assertEqual(result["imported"], 1)
            self.assertEqual(result["errors"][0]["symbol"], "000001")
            self.assertEqual(app.dashboard()["portfolio"]["positions"], 1)
            del app
            gc.collect()

    def test_portfolio_diagnostics_uses_cached_facts_and_discloses_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.save_holding({"symbol": "600519", "name": "贵州茅台", "industry": "白酒", "quantity": 10, "cost": 1000, "current_price": 800, "max_loss_pct": 10, "thesis": "现金流长期增长"})
            with app.connect() as conn:
                conn.execute(
                    "INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json) VALUES (?, ?, ?, ?)",
                    ("600519.SH", "", "fixture", json.dumps(demo_company())),
                )
            conn.close()
            result = app.portfolio_diagnostics()
            self.assertEqual(result["coverage"]["financial_positions"], 1)
            self.assertEqual(result["coverage"]["industry_classified_positions"], 1)
            self.assertEqual(result["positions"][0]["weight"], 1.0)
            self.assertEqual(result["industries"][0]["industry"], "白酒")
            self.assertEqual(result["positions"][0]["decision"]["status"], "减仓复核")
            self.assertTrue(result["positions"][0]["decision"]["loss_limit_triggered"])
            self.assertIn("不自动交易", result["positions"][0]["decision"]["execution_policy"])
            self.assertEqual(result["decision_summary"]["immediate_review"], 1)
            del app
            gc.collect()

    def test_portfolio_evidence_uses_real_holdings_and_never_demo_substitutes_missing_company(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            with self.assertRaisesRegex(ValueError, "真实持仓"):
                app.build_portfolio_evidence_bundle()
            app.save_holding({"symbol": "600519", "name": "贵州茅台", "industry": "白酒", "quantity": 2, "cost": 1000, "current_price": 1100, "current_price_as_of": date.today().isoformat(), "max_loss_pct": 12, "thesis": "现金流与品牌力", "horizon": "3 年"})
            app.save_holding({"symbol": "000333", "name": "美的集团", "industry": "家电", "quantity": 3, "cost": 70, "max_loss_pct": 10, "thesis": "等待财务快照"})
            with app.connect() as conn:
                conn.execute(
                    "INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json) VALUES (?, '', 'fixture', ?)",
                    ("600519.SH", json.dumps(demo_company())),
                )
            conn.close()
            library_id = create_library(app.db_path, "组合纪律")
            card_id = create_doctrine_card(app.db_path, library_id, "限制单股仓位", "单一持仓超过25%时必须复核风险预算")
            update_doctrine_card(app.db_path, card_id, review_status="approved", enabled=True)

            bundle = app.build_portfolio_evidence_bundle()
            self.assertEqual(bundle["schema_version"], "portfolio-evidence/1")
            self.assertEqual(len(bundle["positions"]), 2)
            by_symbol = {item["symbol"]: item for item in bundle["positions"]}
            self.assertTrue(by_symbol["600519"]["financial_data"])
            self.assertFalse(by_symbol["000333"]["financial_data"])
            self.assertEqual(by_symbol["600519"]["horizon"], "3 年")
            self.assertNotIn("financial_analysis", bundle)
            self.assertEqual(bundle["approved_doctrine_rules"][0]["id"], card_id)
            self.assertIn("禁止补猜", bundle["evidence_policy"]["missing_data"])
            self.assertLess(len(json.dumps(bundle, ensure_ascii=False).encode("utf-8")), 600 * 1024)
            app._save_report(
                {"symbol": "PORTFOLIO", "title": "组合深度报告", "provider": "codex", "prompt": "组合复核"},
                {"provider": "codex", "model": "test", "content": "组合报告"},
                bundle,
            )
            with app.connect() as conn:
                row = conn.execute("SELECT id, symbol, snapshot_json FROM reports ORDER BY id DESC LIMIT 1").fetchone()
            conn.close()
            snapshot = json.loads(row["snapshot_json"])
            self.assertEqual(row["symbol"], "PORTFOLIO")
            self.assertEqual(snapshot["formula_engine"], "portfolio-diagnostics/1")
            self.assertEqual(len(snapshot["portfolio_context"]["positions"]), 2)
            updated_bundle = json.loads(json.dumps(bundle))
            updated_bundle["positions"][0]["weight"] = 0.75
            updated_bundle["positions"][0]["decision"] = {
                "priority": "yellow", "status": "等待条件", "reasons": ["价格数据已变化"],
                "review_conditions": ["更新价格后复核"],
            }
            updated_bundle["portfolio"]["concentration"]["top_position_weight"] = 0.75
            app._save_report(
                {"symbol": "PORTFOLIO", "title": "组合深度报告", "provider": "codex", "prompt": "组合复核"},
                {"provider": "codex", "model": "test", "content": "更新后的组合报告"},
                updated_bundle,
            )
            with app.connect() as conn:
                second_id = conn.execute("SELECT id FROM reports ORDER BY id DESC LIMIT 1").fetchone()["id"]
            conn.close()
            diff = app.compare_reports(row["id"], second_id)
            self.assertEqual(diff["report_kind"], "portfolio")
            self.assertEqual(diff["schema_version"], "report-diff/3")
            self.assertEqual(diff["portfolio_position_changes"][0]["symbol"], "600519")
            self.assertTrue(any(item["metric"] == "最大单股权重" for item in diff["metric_changes"]))
            self.assertTrue(any(item["signal_id"] == "portfolio:600519" for item in diff["risk_signal_changes"]))
            del app
            gc.collect()

    def test_portfolio_decision_loop_includes_filing_review_and_coverage_gaps(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.save_holding({
                "symbol": "600036", "name": "招商银行", "industry": "银行",
                "quantity": 10, "cost": 40, "current_price": 41,
                "current_price_as_of": date.today().isoformat(), "max_loss_pct": 10,
                "thesis": "资产质量保持稳定",
            })
            with app.connect() as conn:
                ready_artifact = conn.execute(
                    """INSERT INTO filing_artifacts(sha256, relative_path, byte_size, extraction_status)
                       VALUES (?, '600036/current.pdf', 10, 'ready')""",
                    ("7" * 64,),
                ).lastrowid
                unreadable_artifact = conn.execute(
                    """INSERT INTO filing_artifacts(sha256, relative_path, byte_size, extraction_status)
                       VALUES (?, '600036/scan.pdf', 10, 'needs_ocr')""",
                    ("8" * 64,),
                ).lastrowid
                current_filing = conn.execute(
                    """INSERT INTO filings(source_key, symbol, title, source_provider, artifact_id, disclosed_at, retrieved_at)
                       VALUES ('holding-current', '600036', '招商银行年度报告', 'test', ?, '2026-03-20', '2026-03-20')""",
                    (ready_artifact,),
                ).lastrowid
                conn.execute(
                    """INSERT INTO filings(source_key, symbol, title, source_provider, artifact_id, disclosed_at, retrieved_at)
                       VALUES ('holding-scan', '600036', '招商银行审计报告扫描件', 'test', ?, '2026-03-21', '2026-03-21')""",
                    (unreadable_artifact,),
                )
                evidence_id = conn.execute(
                    """INSERT INTO filing_evidence(
                           filing_id, category, severity, title, excerpt, page_number, locator, review_status)
                       VALUES (?, 'audit_opinion', 'red', '审计意见异常措辞', '关键词命中，尚待核对', 8, 'pdf:p:000008', 'pending')""",
                    (current_filing,),
                ).lastrowid
            conn.close()

            pending = app.portfolio_diagnostics()["positions"][0]
            self.assertEqual(pending["pending_filing_reviews"], 1)
            self.assertEqual(pending["confirmed_filing_red_flags"], 0)
            self.assertEqual(pending["unsearchable_filings"], 1)
            self.assertTrue(any("不按事实处理" in reason for reason in pending["decision"]["reasons"]))
            self.assertTrue(any("不能据此认定没有风险" in reason for reason in pending["decision"]["reasons"]))

            with app.connect() as conn:
                conn.execute(
                    "UPDATE filing_evidence SET review_status='confirmed' WHERE id=?",
                    (evidence_id,),
                )
            conn.close()
            confirmed = app.portfolio_diagnostics()["positions"][0]
            self.assertEqual(confirmed["decision"]["priority"], "red")
            self.assertEqual(confirmed["confirmed_filing_red_flags"], 1)
            self.assertTrue(any("人工确认" in reason for reason in confirmed["decision"]["reasons"]))
            del app
            del conn
            gc.collect()

    def test_holding_price_date_is_validated_and_disclosed_in_decision(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.save_holding({
                "symbol": "600519", "quantity": 1, "cost": 100, "current_price": 95,
                "max_loss_pct": 10, "thesis": "测试逻辑",
            })
            undated = app.portfolio_diagnostics()["positions"][0]
            self.assertIsNone(undated["price_as_of"])
            self.assertTrue(any("缺少有效交易日期" in reason for reason in undated["decision"]["reasons"]))
            self.assertTrue(any("交易日期" in condition for condition in undated["decision"]["review_conditions"]))

            stale_date = (date.today() - timedelta(days=10)).isoformat()
            saved = app.save_holding({
                "symbol": "600519", "quantity": 1, "cost": 100, "current_price": 95,
                "current_price_as_of": stale_date, "max_loss_pct": 10, "thesis": "测试逻辑",
            })
            self.assertEqual(saved["current_price_as_of"], stale_date)
            stale = app.portfolio_diagnostics()["positions"][0]
            self.assertEqual(stale["price_age_days"], 10)
            self.assertTrue(any("已过去 10 天" in reason for reason in stale["decision"]["reasons"]))

            with self.assertRaisesRegex(ValueError, "不能晚于今天"):
                app.save_holding({
                    "symbol": "600519", "quantity": 1, "cost": 100, "current_price": 95,
                    "current_price_as_of": (date.today() + timedelta(days=1)).isoformat(),
                })
            del app
            gc.collect()

    def test_recently_cached_old_financial_period_still_requires_refresh(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.save_holding({
                "symbol": "600519", "quantity": 1, "cost": 100, "current_price": 100,
                "current_price_as_of": date.today().isoformat(), "max_loss_pct": 10, "thesis": "现金流",
            })
            with app.connect() as conn:
                conn.execute(
                    """INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json, retrieved_at)
                       VALUES ('600519.SH', '', 'fresh-cache-old-period', ?, CURRENT_TIMESTAMP)""",
                    (json.dumps(demo_company()),),
                )
            conn.close()
            position = app.portfolio_diagnostics()["positions"][0]
            self.assertEqual(position["snapshot_status"], "ready")
            self.assertEqual(position["financial_period_status"], "stale")
            self.assertGreater(position["financial_period_age_days"], 180)
            self.assertEqual(position["decision"]["status"], "等待条件")
            self.assertTrue(any("超过 180 天" in reason for reason in position["decision"]["reasons"]))
            del app
            del conn
            gc.collect()

    def test_future_financial_period_is_excluded_from_portfolio_signals(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.save_holding({
                "symbol": "600519", "quantity": 1, "cost": 100, "current_price": 100,
                "current_price_as_of": date.today().isoformat(), "max_loss_pct": 10, "thesis": "现金流",
            })
            payload = demo_company()
            payload["periods"][-1]["end_date"] = (date.today() + timedelta(days=1)).isoformat()
            payload["periods"][-1]["fiscal_year"] = date.today().year
            with app.connect() as conn:
                conn.execute(
                    "INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json) VALUES ('600519.SH', '', 'future-fixture', ?)",
                    (json.dumps(payload),),
                )
            conn.close()
            position = app.portfolio_diagnostics()["positions"][0]
            self.assertEqual(position["financial_period_status"], "future")
            self.assertFalse(position["financial_data"])
            self.assertEqual(position["red_flags"] + position["yellow_flags"] + position["red_changes"] + position["yellow_changes"], 0)
            self.assertEqual(position["decision"]["status"], "等待条件")
            self.assertTrue(any("晚于今天" in reason for reason in position["decision"]["reasons"]))
            del app
            del conn
            gc.collect()

    def test_portfolio_refresh_only_updates_explicitly_and_returns_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.save_holding({"symbol": "600519", "quantity": 1, "cost": 100})
            with patch.object(app, "company_financial_data", return_value=({}, {"cache": "refreshed"})) as refresh:
                result = app.refresh_portfolio_financials()
            refresh.assert_called_once_with("600519", refresh=True)
            self.assertEqual(result["refreshed"], ["600519"])
            self.assertEqual(result["errors"], [])
            del app
            gc.collect()

    def test_company_comparison_uses_local_snapshots_without_hidden_refresh(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            first = demo_company()
            second = json.loads(json.dumps(first))
            first["company"]["symbol"] = "600001"
            first["company"]["name"] = "甲公司"
            second["company"]["symbol"] = "000002"
            second["company"]["name"] = "乙公司"
            with app.connect() as conn:
                conn.executemany(
                    "INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json) VALUES (?, '', 'fixture', ?)",
                    [
                        ("600001.SH", json.dumps(first)),
                        ("000002.SZ", json.dumps(second)),
                    ],
                )
                ready_artifact = conn.execute(
                    """INSERT INTO filing_artifacts(sha256, relative_path, byte_size, extraction_status)
                       VALUES (?, '600001/compare-ready.pdf', 10, 'ready')""",
                    ("1" * 64,),
                ).lastrowid
                scan_artifact = conn.execute(
                    """INSERT INTO filing_artifacts(sha256, relative_path, byte_size, extraction_status)
                       VALUES (?, '600001/compare-scan.pdf', 10, 'needs_ocr')""",
                    ("2" * 64,),
                ).lastrowid
                filing_id = conn.execute(
                    """INSERT INTO filings(source_key, symbol, title, source_provider, artifact_id, disclosed_at, retrieved_at)
                       VALUES ('compare-ready', '600001', '甲公司年度报告', 'test', ?, '2026-03-20', '2026-03-20')""",
                    (ready_artifact,),
                ).lastrowid
                conn.execute(
                    """INSERT INTO filings(source_key, symbol, title, source_provider, artifact_id, disclosed_at, retrieved_at)
                       VALUES ('compare-scan', '600001', '甲公司审计报告扫描件', 'test', ?, '2026-03-21', '2026-03-21')""",
                    (scan_artifact,),
                )
                evidence_id = conn.execute(
                    """INSERT INTO filing_evidence(
                           filing_id, category, severity, title, excerpt, page_number, locator, review_status)
                       VALUES (?, 'audit_opinion', 'red', '审计意见异常措辞', '关键词命中，尚待核对', 8, 'pdf:p:000008', 'pending')""",
                    (filing_id,),
                ).lastrowid
            conn.close()
            with patch.object(app, "company_financial_data") as refresh_call:
                result = app.compare_companies({"symbols": ["600001", "000002"]})
            refresh_call.assert_not_called()
            self.assertEqual(result["coverage"], {"requested": 2, "ready": 2})
            self.assertEqual(result["companies"][0]["name"], "甲公司")
            self.assertIn("return_on_invested_capital", result["companies"][0]["metrics"])
            self.assertIn(result["companies"][0]["decision"]["status"], {"继续观察", "等待条件"})
            self.assertIn("red_changes", result["companies"][0]["risk"])
            self.assertEqual(result["companies"][0]["source"]["mode"], "cached")
            self.assertEqual(result["companies"][0]["risk"]["pending_filing_reviews"], 1)
            self.assertEqual(result["companies"][0]["risk"]["confirmed_filing_red"], 0)
            self.assertEqual(result["companies"][0]["risk"]["unsearchable_filings"], 1)
            self.assertEqual(result["companies"][0]["decision"]["status"], "等待条件")
            self.assertTrue(any("不按事实处理" in reason for reason in result["companies"][0]["decision"]["reasons"]))
            with app.connect() as conn:
                conn.execute("UPDATE filing_evidence SET review_status='confirmed' WHERE id=?", (evidence_id,))
            conn.close()
            confirmed = app.compare_companies({"symbols": ["600001", "000002"]})["companies"][0]
            self.assertEqual(confirmed["risk"]["confirmed_filing_red"], 1)
            self.assertEqual(confirmed["decision"]["priority"], "red")
            self.assertTrue(any("人工确认" in reason for reason in confirmed["decision"]["reasons"]))
            del app
            del conn
            gc.collect()

    def test_company_comparison_uses_cutoff_for_period_freshness_and_blocks_future_period(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            first = demo_company()
            second = json.loads(json.dumps(first))
            first["company"].update({"symbol": "600001", "name": "甲公司"})
            second["company"].update({"symbol": "000002", "name": "乙公司"})
            second["periods"][-1]["end_date"] = "2026-01-01"
            with app.connect() as conn:
                conn.executemany(
                    "INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json) VALUES (?, '2025-12-31', 'fixture', ?)",
                    [("600001.SH", json.dumps(first)), ("000002.SZ", json.dumps(second))],
                )
            conn.close()
            result = app.compare_companies({"symbols": ["600001", "000002"], "as_of": "2025-12-31"})
            by_symbol = {item["symbol"]: item for item in result["companies"]}
            self.assertEqual(by_symbol["600001"]["financial_period"]["status"], "ready")
            self.assertEqual(by_symbol["600001"]["financial_period"]["reference_date"], "2025-12-31")
            self.assertEqual(by_symbol["000002"]["status"], "error")
            self.assertEqual(by_symbol["000002"]["financial_period"]["status"], "future")
            self.assertIn("晚于对比截止日", by_symbol["000002"]["error"])
            self.assertEqual(result["coverage"], {"requested": 2, "ready": 1})
            del app
            del conn
            gc.collect()

    def test_valuation_workbench_calculates_and_archives_assumptions(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            result = app.run_valuation_workbench(
                {
                    "company": demo_company(),
                    "assumptions": {
                        "cash_flow_basis": "fcfe",
                        "margin_of_safety": 0.2,
                        "scenarios": [
                            {"name": "stress", "growth_rate": -0.02, "discount_rate": 0.12, "terminal_growth_rate": 0.01, "forecast_years": 5},
                            {"name": "base", "growth_rate": 0.04, "discount_rate": 0.10, "terminal_growth_rate": 0.02, "forecast_years": 5},
                            {"name": "optimistic", "growth_rate": 0.08, "discount_rate": 0.09, "terminal_growth_rate": 0.025, "forecast_years": 5},
                        ],
                    },
                }
            )
            self.assertIsNotNone(result["run_id"])
            self.assertEqual(result["analysis"]["valuation"]["dcf"]["status"], "ok")
            self.assertEqual(len(result["analysis"]["valuation"]["dcf"]["scenarios"]), 3)
            self.assertEqual(result["analysis"]["valuation"]["sensitivity"]["status"], "ok")
            summary = result["analysis"]["valuation"]["workbench_summary"]
            self.assertEqual(summary["method_ranges"][0]["margin_of_safety"], 0.2)
            self.assertLess(summary["method_ranges"][0]["margin_price_high"], summary["method_ranges"][0]["value_range_high"])
            self.assertEqual(summary["policy"], "Methods remain separate ranges; they are not averaged into a single target price.")
            runs = app.list_valuation_runs("600888")
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["assumptions"]["schema_version"], "valuation-workbench/1.0")
            reproduction = runs[0]["assumptions"]["reproduction"]
            replayed = analyze_company(reproduction["configured_company"])
            self.assertEqual(replayed["valuation"]["dcf"], runs[0]["result"]["dcf"])
            self.assertEqual(reproduction["original_company"], demo_company())
            self.assertEqual(len(reproduction["engine_hashes"]["finance_engine.py"]), 64)
            verification = app.replay_valuation_run(result["run_id"])
            self.assertEqual(verification["status"], "verified")
            self.assertTrue(verification["identical_result"])
            self.assertEqual(verification["differences"], [])
            self.assertEqual(len(app.list_valuation_runs("600888")), 1)
            compact = app.list_valuation_runs("600888", include_inputs=False)[0]
            self.assertNotIn("reproduction", compact["assumptions"])
            self.assertTrue(compact["replay_available"])
            self.assertEqual(compact["result"], runs[0]["result"])
            self.assertIn("reproduction", app.list_valuation_runs("600888")[0]["assumptions"])
            with self.assertRaisesRegex(ValueError, "折现率"):
                app.run_valuation_workbench(
                    {
                        "company": demo_company(),
                        "assumptions": {"scenarios": [{"name": "bad", "growth_rate": 0.1, "discount_rate": 0.02, "terminal_growth_rate": 0.03}]},
                    }
                )
            del app
            gc.collect()

    def test_valuation_replay_handles_legacy_version_change_and_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            result = app.run_valuation_workbench({
                "company": demo_company(),
                "assumptions": {"scenarios": [{"name": "base", "growth_rate": 0.04, "discount_rate": 0.1, "terminal_growth_rate": 0.02}]},
            })
            run_id = result["run_id"]
            assumptions = app.list_valuation_runs()[0]["assumptions"]
            assumptions["reproduction"]["engine_hashes"]["finance_engine.py"] = "old-version"
            with app.connect() as conn:
                conn.execute("UPDATE valuation_runs SET assumptions_json=? WHERE id=?", (json.dumps(assumptions), run_id))
            conn.close()
            replay = app.replay_valuation_run(run_id)
            self.assertEqual(replay["status"], "version_changed")
            self.assertTrue(replay["identical_result"])
            self.assertEqual(app.list_valuation_runs()[0]["assumptions"], assumptions)
            archived_result = app.list_valuation_runs()[0]["result"]
            changed_result = json.loads(json.dumps(archived_result))
            changed_result["dcf"]["scenarios"][0]["value_per_share"] = -123
            with app.connect() as conn:
                conn.execute("UPDATE valuation_runs SET result_json=? WHERE id=?", (json.dumps(changed_result), run_id))
            conn.close()
            changed_replay = app.replay_valuation_run(run_id)
            self.assertFalse(changed_replay["identical_result"])
            self.assertTrue(any(item["path"].endswith("value_per_share") and item["archived"] == -123 for item in changed_replay["differences"]))
            with app.connect() as conn:
                conn.execute("UPDATE valuation_runs SET result_json=? WHERE id=?", (json.dumps(archived_result), run_id))
            conn.close()
            pristine = json.loads(json.dumps(assumptions))
            assumptions["reproduction"]["configured_company"]["valuation"]["dcf"]["scenarios"][0]["growth_rate"] = 0.5
            with app.connect() as conn:
                conn.execute("UPDATE valuation_runs SET assumptions_json=? WHERE id=?", (json.dumps(assumptions), run_id))
            conn.close()
            with self.assertRaisesRegex(ValueError, "计算输入哈希不匹配"):
                app.replay_valuation_run(run_id)
            assumptions = pristine
            assumptions["reproduction"]["original_company"]["valuation"]["market_price"] = 999999
            with app.connect() as conn:
                conn.execute("UPDATE valuation_runs SET assumptions_json=? WHERE id=?", (json.dumps(assumptions), run_id))
            conn.close()
            with self.assertRaisesRegex(ValueError, "哈希不匹配"):
                app.replay_valuation_run(run_id)
            with app.connect() as conn:
                conn.execute("UPDATE valuation_runs SET assumptions_json='{}' WHERE id=?", (run_id,))
            conn.close()
            self.assertEqual(app.replay_valuation_run(run_id)["status"], "unavailable")
            with self.assertRaisesRegex(ValueError, "不存在"):
                app.replay_valuation_run(run_id + 100)
            del app
            gc.collect()

    def test_valuation_disabled_methods_drop_stale_snapshot_assumptions(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            raw = demo_company()
            raw["valuation"]["ddm"] = {"dividend_per_share": 2, "scenarios": [{"name": "old", "growth_rate": 0.03, "cost_of_equity": 0.1, "terminal_growth_rate": 0.02, "forecast_years": 5}]}
            raw["valuation"]["relative"] = {"scenarios": [{"name": "old", "target_pe": 20}]}
            result = app.run_valuation_workbench({"company": raw, "save": False, "assumptions": {
                "ddm_enabled": False, "relative_scenarios": [],
                "scenarios": [{"name": "base", "growth_rate": 0.04, "discount_rate": 0.1, "terminal_growth_rate": 0.02}],
            }})
            configured = result["assumptions"]["reproduction"]["configured_company"]["valuation"]
            self.assertNotIn("ddm", configured)
            self.assertNotIn("relative", configured)
            self.assertIn("ddm", raw["valuation"])
            self.assertFalse(any(item["method"] in {"ddm", "relative"} for item in result["analysis"]["valuation"]["workbench_summary"]["method_ranges"]))
            del app
            gc.collect()

    def test_valuation_rejects_fractional_forecast_years(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            with self.assertRaisesRegex(ValueError, "整数"):
                app.run_valuation_workbench({"company": demo_company(), "assumptions": {"scenarios": [{"name": "base", "growth_rate": 0.04, "discount_rate": 0.1, "terminal_growth_rate": 0.02, "forecast_years": 5.9}]}})
            self.assertEqual(app.list_valuation_runs(), [])
            del app
            gc.collect()

    def test_valuation_rejects_future_price_before_archiving(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            raw = demo_company()
            raw["valuation"]["price_as_of"] = "2026-09-08"
            with self.assertRaisesRegex(ValueError, "晚于截止日"):
                app.run_valuation_workbench({
                    "company": raw, "as_of": "2026-09-07",
                    "assumptions": {"scenarios": [{"name": "base", "growth_rate": 0.04, "discount_rate": 0.1, "terminal_growth_rate": 0.02}]},
                })
            self.assertEqual(app.list_valuation_runs(), [])
            del app
            gc.collect()

    def test_valuation_missing_cutoff_does_not_claim_complete(self):
        from backend.app import _valuation_date_audit
        raw = demo_company()
        raw["valuation"]["price_as_of"] = "2026-09-07"
        result = _valuation_date_audit(raw, {}, {})
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["cutoff_check"], "missing_cutoff")
        raw["valuation"]["shares_as_of"] = "2026-09-08"
        with self.assertRaisesRegex(ValueError, "晚于截止日"):
            _valuation_date_audit(raw, {"as_of": "2026-09-07"}, {})

    def test_valuation_date_audit_completes_only_with_all_core_dates(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            raw = demo_company()
            raw["valuation"]["price_as_of"] = "2026-09-07"
            raw["valuation"]["shares_as_of"] = "2026-09-07"
            result = app.run_valuation_workbench({
                "company": raw,
                "as_of": "2026-09-07",
                "save": False,
                "assumptions": {
                    "assumptions_as_of": "2026-09-07",
                    "scenarios": [{"name": "base", "growth_rate": 0.04, "discount_rate": 0.1, "terminal_growth_rate": 0.02}],
                },
            })
            context = result["analysis"]["valuation"]["workbench_summary"]["date_context"]
            self.assertEqual(context["status"], "complete")
            self.assertEqual(context["missing_required_dates"], [])
            self.assertEqual(context["shares_as_of"], "2026-09-07")
            self.assertEqual(context["net_debt_as_of"], result["analysis"]["as_of"])
            self.assertTrue(any(item["path"] == "date_context.assumptions_as_of" for item in context["checked_dates"]))
            del app
            gc.collect()

    def test_valuation_rejects_late_restatement_disclosure(self):
        from backend.app import _valuation_date_audit
        raw = demo_company()
        # The accounting period and original publication predate the cutoff,
        # but the corrected numbers were only available afterwards.
        raw["periods"][0]["source"] = {
            "report_end": "2025-12-31",
            "announcement_date": "2026-03-20",
            "final_announcement_date": "2026-09-08",
        }
        with self.assertRaisesRegex(ValueError, "final_announcement_date"):
            _valuation_date_audit(raw, {"as_of": "2026-09-07"}, {})
        result = _valuation_date_audit(raw, {"as_of": "2026-09-08"}, {})
        self.assertEqual(result["cutoff_check"], "passed_known_dates")
        self.assertTrue(any(item["path"].endswith("final_announcement_date") for item in result["checked_dates"]))

    def test_bank_valuation_workbench_keeps_pb_and_residual_income_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            result = app.run_bank_valuation_workbench({
                "company": bank_company(),
                "assumptions": {
                    "margin_of_safety": 0.25,
                    "scenarios": [
                        {"name": "stress", "target_pb": 0.6, "roe": 0.09, "cost_of_equity": 0.12, "book_value_growth": 0.01, "terminal_growth": 0.01, "forecast_years": 5},
                        {"name": "base", "target_pb": 0.9, "roe": 0.13, "cost_of_equity": 0.10, "book_value_growth": 0.04, "terminal_growth": 0.02, "forecast_years": 5},
                    ],
                },
            })
            self.assertEqual(result["analysis"]["support"]["status"], "supported_bank")
            summary = result["analysis"]["valuation"]["workbench_summary"]
            self.assertEqual({item["method"] for item in summary["method_ranges"]}, {"pb", "residual_income"})
            self.assertIn("不平均", summary["policy"])
            self.assertEqual(result["assumptions"]["schema_version"], "bank-valuation-workbench/1.0")
            self.assertIsNotNone(result["run_id"])
            self.assertEqual(app.replay_valuation_run(result["run_id"])["status"], "verified")
            del app
            gc.collect()

    def test_bank_and_sector_valuation_require_complete_unified_date_context(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            bank = bank_company()
            bank["valuation"]["shares_as_of"] = "2026-01-15"
            bank_result = app.run_bank_valuation_workbench({
                "company": bank, "as_of": "2026-01-15", "save": False,
                "assumptions": {
                    "assumptions_as_of": "2026-01-15",
                    "scenarios": [{"name": "base", "target_pb": 0.9, "roe": 0.13, "cost_of_equity": 0.10, "book_value_growth": 0.04, "terminal_growth": 0.02, "forecast_years": 5}],
                },
            })
            bank_dates = bank_result["analysis"]["valuation"]["workbench_summary"]["date_context"]
            self.assertEqual(bank_dates["status"], "complete")
            self.assertEqual(bank_dates["missing_required_dates"], [])

            resource = resource_company()
            resource["valuation"]["shares_as_of"] = "2026-01-15"
            sector_result = app.run_sector_valuation_workbench({
                "company": resource, "sector_template": "cyclical_resource", "as_of": "2026-01-15", "save": False,
                "assumptions": {
                    "assumptions_as_of": "2026-01-15",
                    "scenarios": [{"name": "base", "normalized_volume": 90, "normalized_price": 450, "unit_cash_cost": 280, "tax_rate": 0.25, "target_pe": 8, "recoverable_reserves": 500, "recovery_factor": 0.8, "discount_rate": 0.1, "mine_life_years": 12}],
                },
            })
            sector_dates = sector_result["analysis"]["valuation"]["workbench_summary"]["date_context"]
            self.assertEqual(sector_dates["status"], "complete")
            self.assertEqual(sector_dates["net_debt_as_of"], sector_result["analysis"]["as_of"])
            del app
            gc.collect()

    def test_bank_disclosure_import_creates_traceable_snapshot_version(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            raw = bank_company()
            with app.connect() as conn:
                conn.execute("INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json) VALUES (?, '', 'fixture', ?)", ("600000.SH", json.dumps(raw, ensure_ascii=False)))
            conn.close()
            result = app.import_bank_disclosures({
                "symbol": "600000",
                "period_end": "2025-12-31",
                "source_document": "2025 年年度报告",
                "source_url": "https://example.test/annual-report.pdf",
                "page": "76",
                "metrics": {"provision_coverage_ratio": 180, "capital_adequacy_ratio": 13.2},
            })
            metric = result["analysis"]["bank"]["latest"]["metrics"]["provision_coverage_ratio"]
            self.assertAlmostEqual(metric["value"], 1.8)
            self.assertEqual(metric["inputs"][0]["source"]["page"], "76")
            with app.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM financial_snapshots WHERE symbol='600000.SH'").fetchone()[0], 2)
            conn.close()
            with self.assertRaisesRegex(ValueError, "必须填写"):
                app.import_bank_disclosures({"symbol": "600000", "period_end": "2025-12-31", "metrics": {"net_interest_margin": 1.5}})
            del app
            gc.collect()

    def test_confirmed_filing_fact_enters_deterministic_bank_analysis_and_is_reversible(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            raw = bank_company()
            del raw["periods"][-1]["facts"]["net_interest_margin"]
            with app.connect() as conn:
                conn.execute(
                    "INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json) VALUES (?, '', 'fixture', ?)",
                    ("600000.SH", json.dumps(raw, ensure_ascii=False)),
                )
                artifact_id = conn.execute(
                    """INSERT INTO filing_artifacts(sha256, relative_path, byte_size, extraction_status)
                       VALUES (?, '600000/annual.pdf', 10, 'ready')""",
                    ("b" * 64,),
                ).lastrowid
                filing_id = conn.execute(
                    """INSERT INTO filings(
                           source_key, symbol, title, source_provider, source_url, artifact_id,
                           disclosed_at, retrieved_at)
                       VALUES ('source-bank-1', '600000', '示例银行2025年度报告', 'cninfo_public',
                               'https://example.test/annual.pdf', ?, '2026-03-28', '2026-03-28')""",
                    (artifact_id,),
                ).lastrowid
                fact_id = conn.execute(
                    """INSERT INTO filing_facts(
                           filing_id, metric, label, value, raw_value, period_end, page_number,
                           locator, excerpt, extraction_method, review_status, reviewed_at)
                       VALUES (?, 'net_interest_margin', '净利息收益率', 0.0183, '1.83%',
                               '2025-12-31', 76, 'pdf:p:000076', '本集团净利息收益率1.83%',
                               'deterministic_bank_fact_v1', 'confirmed', '2026-04-01')""",
                    (filing_id,),
                ).lastrowid
            conn.close()
            company, source = app.company_financial_data("600000")
            analysis = analyze_company(company)
            metric = analysis["bank"]["latest"]["metrics"]["net_interest_margin"]
            self.assertAlmostEqual(metric["value"], 0.0183)
            self.assertEqual(metric["inputs"][0]["source"]["locator"], "pdf:p:000076")
            self.assertEqual(source["confirmed_filing_facts"]["applied_count"], 1)
            self.assertEqual(app.filing_facts_for_review(filing_id)[0]["application_status"], "applied")
            with app.connect() as conn:
                conn.execute("UPDATE filing_facts SET review_status='pending', reviewed_at=NULL WHERE id=?", (fact_id,))
            conn.close()
            reverted, reverted_source = app.company_financial_data("600000")
            reverted_metric = analyze_company(reverted)["bank"]["latest"]["metrics"]["net_interest_margin"]
            self.assertEqual(reverted_metric["status"], "missing")
            self.assertNotIn("confirmed_filing_facts", reverted_source)
            self.assertEqual(app.filing_facts_for_review(filing_id)[0]["application_status"], "awaiting_review")
            del app
            gc.collect()

    def test_confirmed_filing_fact_conflict_is_isolated_not_silently_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            raw = bank_company()
            with app.connect() as conn:
                conn.execute(
                    "INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json) VALUES (?, '', 'fixture', ?)",
                    ("600000.SH", json.dumps(raw, ensure_ascii=False)),
                )
                artifact_id = conn.execute(
                    """INSERT INTO filing_artifacts(sha256, relative_path, byte_size, extraction_status)
                       VALUES (?, '600000/annual.pdf', 10, 'ready')""",
                    ("c" * 64,),
                ).lastrowid
                filing_id = conn.execute(
                    """INSERT INTO filings(source_key, symbol, title, source_provider, artifact_id, disclosed_at, retrieved_at)
                       VALUES ('source-bank-2', '600000', '示例银行2025年度报告', 'cninfo_public', ?, '2026-03-28', '2026-03-28')""",
                    (artifact_id,),
                ).lastrowid
                conn.execute(
                    """INSERT INTO filing_facts(
                           filing_id, metric, label, value, raw_value, period_end, page_number,
                           locator, excerpt, extraction_method, review_status)
                       VALUES (?, 'net_interest_margin', '净利息收益率', 0.0183, '1.83%',
                               '2025-12-31', 76, 'pdf:p:000076', '本集团净利息收益率1.83%',
                               'deterministic_bank_fact_v1', 'confirmed')""",
                    (filing_id,),
                )
            conn.close()
            company, source = app.company_financial_data("600000")
            metric = analyze_company(company)["bank"]["latest"]["metrics"]["net_interest_margin"]
            self.assertEqual(metric["status"], "missing")
            self.assertEqual(source["confirmed_filing_facts"]["conflict_count"], 1)
            self.assertEqual(
                source["confirmed_filing_facts"]["conflicts"][0]["reason"],
                "provider_and_confirmed_filing_disagree",
            )
            self.assertEqual(app.filing_facts_for_review(filing_id)[0]["application_status"], "conflict_isolated")
            del app
            gc.collect()

    def test_sector_valuation_workbench_uses_industry_methods(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            resource = app.run_sector_valuation_workbench({
                "company": resource_company(), "sector_template": "cyclical_resource", "save": True,
                "assumptions": {"margin_of_safety": 0.2, "scenarios": [{"name": "base", "normalized_volume": 90, "normalized_price": 450, "unit_cash_cost": 280, "tax_rate": 0.25, "target_pe": 8, "recoverable_reserves": 500, "recovery_factor": 0.8, "discount_rate": 0.1, "mine_life_years": 12}]},
            })
            self.assertEqual(resource["analysis"]["support"]["sector_template"], "cyclical_resource")
            self.assertEqual({item["method"] for item in resource["analysis"]["valuation"]["workbench_summary"]["method_ranges"]}, {"midcycle", "nav"})
            utility = app.run_sector_valuation_workbench({
                "company": utility_company(), "sector_template": "utility", "save": True,
                "assumptions": {"margin_of_safety": 0.2, "scenarios": [{"name": "base", "normalized_rab": 80_000, "allowed_return": 0.08, "tax_rate": 0.25, "target_pe": 12, "dividend_growth": 0.03, "cost_of_equity": 0.09, "terminal_growth": 0.02, "forecast_years": 5}]},
            })
            self.assertEqual({item["method"] for item in utility["analysis"]["valuation"]["workbench_summary"]["method_ranges"]}, {"regulated_return", "ddm"})
            property_result = app.run_sector_valuation_workbench({
                "company": property_company(), "sector_template": "real_estate", "save": True,
                "assumptions": {"margin_of_safety": 0.25, "scenarios": [{"name": "base", "inventory_haircut": 0.12, "contract_recognition_rate": 0.7, "normalized_gross_margin": 0.18, "tax_rate": 0.25, "target_pb": 0.7, "additional_loss_provision": 3_000}]},
            })
            self.assertEqual({item["method"] for item in property_result["analysis"]["valuation"]["workbench_summary"]["method_ranges"]}, {"adjusted_nav", "adjusted_pb"})
            insurance_result = app.run_sector_valuation_workbench({
                "company": insurance_company(), "sector_template": "insurance", "save": True,
                "assumptions": {"margin_of_safety": 0.2, "scenarios": [{"name": "base", "target_pev": 0.8, "normalized_nbv": 6_000, "nbv_multiple": 5, "ev_haircut": 0.1}]},
            })
            self.assertEqual({item["method"] for item in insurance_result["analysis"]["valuation"]["workbench_summary"]["method_ranges"]}, {"pev", "appraisal_value"})
            broker_result = app.run_sector_valuation_workbench({
                "company": brokerage_company(), "sector_template": "brokerage", "save": True,
                "assumptions": {"margin_of_safety": 0.2, "scenarios": [{"name": "base", "target_pb": 1.2, "target_pe": 15, "normalized_net_income": 7_500}]},
            })
            self.assertEqual({item["method"] for item in broker_result["analysis"]["valuation"]["workbench_summary"]["method_ranges"]}, {"pb", "normalized_pe"})
            for saved in (resource, utility, property_result, insurance_result, broker_result):
                with self.subTest(template=saved["assumptions"]["sector_template"]):
                    self.assertEqual(app.replay_valuation_run(saved["run_id"])["status"], "verified")
            self.assertEqual(len(app.list_valuation_runs()), 5)
            del app
            gc.collect()

    def test_sector_disclosure_import_preserves_source_and_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            raw = resource_company()
            del raw["periods"][-1]["facts"]["production_volume"]
            with app.connect() as conn:
                conn.execute("INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json) VALUES (?, '', 'fixture', ?)", ("601999.SH", json.dumps(raw, ensure_ascii=False)))
            conn.close()
            result = app.import_sector_disclosures({
                "symbol": "601999", "industry": "煤炭开采", "sector_template": "cyclical_resource", "period_end": "2025-12-31",
                "source_document": "2025 年年度报告", "source_url": "https://example.test/resource-report.pdf", "page": "42",
                "metrics": {"production_volume": 86, "realized_price": 405, "unit_cash_cost": 298, "proven_probable_reserves": 510},
                "units": {"production_volume": "million tonnes", "realized_price": "CNY/tonne", "unit_cash_cost": "CNY/tonne", "proven_probable_reserves": "million tonnes"},
            })
            metric = result["analysis"]["sector"]["latest"]["metrics"]["production_volume"]
            self.assertEqual(metric["value"], 86)
            self.assertEqual(metric["inputs"][0]["source"]["page"], "42")
            with app.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM financial_snapshots WHERE symbol='601999.SH'").fetchone()[0], 2)
            conn.close()
            del app
            gc.collect()

    def test_property_disclosure_import_updates_liquidity_metrics_with_lineage(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            raw = property_company()
            del raw["periods"][-1]["facts"]["restricted_cash"]
            with app.connect() as conn:
                conn.execute("INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json) VALUES (?, '', 'fixture', ?)", ("000999.SZ", json.dumps(raw, ensure_ascii=False)))
            conn.close()
            result = app.import_sector_disclosures({
                "symbol": "000999", "industry": "房地产开发", "sector_template": "real_estate", "period_end": "2025-12-31",
                "source_document": "2025 年年度报告", "source_url": "https://example.test/property-report.pdf", "page": "188",
                "metrics": {"restricted_cash": 15_000, "debt_due_within_one_year": 24_000},
            })
            metrics = result["analysis"]["sector"]["latest"]["metrics"]
            self.assertEqual(metrics["usable_cash"]["value"], 15_000)
            self.assertEqual(metrics["restricted_cash"]["inputs"][0]["source"]["page"], "188")
            del app
            gc.collect()

    def test_insurance_disclosure_import_preserves_regulatory_source(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            raw = insurance_company()
            del raw["periods"][-1]["facts"]["core_solvency_ratio"]
            with app.connect() as conn:
                conn.execute("INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json) VALUES (?, '', 'fixture', ?)", ("601888.SH", json.dumps(raw, ensure_ascii=False)))
            conn.close()
            result = app.import_sector_disclosures({
                "symbol": "601888", "industry": "人身保险", "sector_template": "insurance", "period_end": "2025-12-31",
                "source_document": "2025 年偿付能力报告", "source_url": "https://example.test/solvency.pdf", "page": "12",
                "metrics": {"core_solvency_ratio": 45, "comprehensive_investment_yield": -1.0},
            })
            metrics = result["analysis"]["sector"]["latest"]["metrics"]
            self.assertEqual(metrics["core_solvency_ratio"]["value"], 0.45)
            self.assertEqual(metrics["core_solvency_ratio"]["inputs"][0]["source"]["page"], "12")
            del app
            gc.collect()

    def test_filing_sync_uses_cursor_and_records_success_without_ai(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            announcements = [
                {
                    "symbol": "600519",
                    "company_name": "贵州茅台",
                    "title": "贵州茅台2025年年度报告",
                    "filing_type": "annual_report",
                    "report_period": "2025-12-31",
                    "disclosed_at": "2026-03-30T18:00:00+08:00",
                    "source_provider": "cninfo_public",
                    "source_notice_id": "ann-1",
                    "source_url": "https://static.cninfo.com.cn/finalpage/2026-03-30/ann-1.PDF",
                    "is_correction": False,
                }
            ]
            with patch("backend.app.search_critical_filings", return_value=announcements) as search, patch(
                "backend.app.archive_filing", return_value={"duplicate": False}
            ) as archive:
                result = app.sync_critical_filings(
                    {"symbol": "600519", "since": "2026-01-01", "until": "2026-04-01"}
                )
            self.assertEqual(result["archived"], 1)
            self.assertEqual(result["results"][0]["status"], "success")
            self.assertEqual(result["results"][0]["state"]["cursor_date"], "2026-04-01")
            search.assert_called_once_with("600519", since="2026-01-01", until="2026-04-01", limit=180)
            archive.assert_called_once()
            del app
            gc.collect()

    def test_filing_sync_retries_transient_discovery_and_download_failures(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            announcements = [{
                "symbol": "600519", "company_name": "贵州茅台", "title": "2025年年度报告",
                "filing_type": "annual_report", "report_period": "2025-12-31",
                "disclosed_at": "2026-03-30T18:00:00+08:00", "source_provider": "cninfo_public",
                "source_notice_id": "retry-ann-1",
                "source_url": "https://static.cninfo.com.cn/finalpage/2026-03-30/retry-ann-1.PDF",
            }]
            with patch(
                "backend.app.search_critical_filings",
                side_effect=[RuntimeError("临时搜索失败"), announcements],
            ) as search, patch(
                "backend.app.archive_filing",
                side_effect=[RuntimeError("临时下载失败"), {"duplicate": False}],
            ) as archive, patch("backend.app.time.sleep") as sleep:
                result = app.sync_critical_filings({
                    "symbol": "600519", "since": "2026-01-01", "until": "2026-04-01",
                    "retry_count": 1,
                })
            self.assertEqual(result["archived"], 1)
            self.assertEqual(result["retry_attempts"], 2)
            self.assertEqual(result["results"][0]["retry_attempts"], 2)
            self.assertEqual(result["results"][0]["status"], "success")
            self.assertEqual(search.call_count, 2)
            self.assertEqual(archive.call_count, 2)
            self.assertEqual(sleep.call_count, 2)
            del app
            gc.collect()

    def test_settings_require_safe_api_url_and_positive_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                app.update_settings({"api_base_url": "http://example.test/v1"})
            with self.assertRaisesRegex(ValueError, "上限"):
                app.update_settings({"monthly_token_limit": -1})
            saved = app.update_settings({"api_base_url": "http://localhost:11434/v1"})
            self.assertEqual(saved["api_base_url"], "http://localhost:11434/v1")
            with app.connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO settings(key, value) VALUES ('monthly_token_limit', '-5')"
                )
            del conn
            gc.collect()
            self.assertEqual(app.settings()["monthly_token_limit"], 1_000)
            del app
            gc.collect()

    def test_ai_budget_is_enforced_before_provider_call(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.update_settings({"monthly_token_limit": 1_000})
            with patch.object(app, "_run_codex") as provider:
                with self.assertRaisesRegex(ValueError, "超过"):
                    app.run_ai({"provider": "codex", "prompt": "分析", "evidence": {"large": "证据" * 8_000}})
                provider.assert_not_called()
            del app
            gc.collect()

    def test_ai_cost_estimate_uses_configured_api_prices_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.update_settings({
                "monthly_token_limit": 100_000,
                "api_model": "priced-model",
                "api_input_price_per_million": 2.5,
                "api_output_price_per_million": 10,
                "api_price_currency": "USD",
            })
            payload = {"provider": "api", "prompt": "分析现金流", "evidence": {"fact": "经营现金流"}}
            estimate = app.estimate_ai(payload)
            expected_cost = round(
                (estimate["estimated_input_tokens"] * 2.5 + estimate["estimated_output_tokens"] * 10) / 1_000_000,
                6,
            )
            self.assertEqual(estimate["schema_version"], "ai-cost-estimate/1")
            self.assertEqual(estimate["estimated_cost"], expected_cost)
            self.assertEqual(estimate["currency"], "USD")
            self.assertTrue(estimate["within_budget"])
            self.assertFalse(estimate["configuration_ready"])
            changed = app.estimate_ai({**payload, "evidence": {"fact": "已变化"}})
            self.assertNotEqual(estimate["fingerprint"], changed["fingerprint"])
            with patch.object(app, "_run_compatible_api") as provider:
                with self.assertRaisesRegex(ValueError, "重新预估"):
                    app.run_ai({**payload, "estimate_fingerprint": changed["fingerprint"]})
                provider.assert_not_called()
            with self.assertRaisesRegex(ValueError, "非负数字"):
                app.update_settings({"api_input_price_per_million": True})
            with self.assertRaisesRegex(ValueError, "CNY 或 USD"):
                app.update_settings({"api_price_currency": "EUR"})
            del app
            gc.collect()

    def test_ai_estimate_fingerprint_survives_browser_number_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            payload = {
                "provider": "codex",
                "prompt": "分析现金流",
                "evidence": {"zero": 0.0, "whole": 12.0, "fraction": 1.25, "nested": [0.0, 2.0]},
            }
            estimate = app.estimate_ai(payload)
            browser_round_trip = {
                "zero": 0,
                "whole": 12,
                "fraction": 1.25,
                "nested": [0, 2],
            }
            with patch.object(
                app,
                "_run_codex",
                return_value={"provider": "codex", "content": "完成", "billable": False},
            ) as provider:
                result = app.run_ai({
                    **payload,
                    "evidence": browser_round_trip,
                    "estimate_fingerprint": estimate["fingerprint"],
                })
            self.assertEqual(result["content"], "完成")
            provider.assert_called_once()
            del app
            gc.collect()

    def test_successful_ai_call_is_recorded_in_monthly_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            before = app.settings()["monthly_tokens_used"]
            with patch.object(app, "_run_codex", return_value={"provider": "codex", "content": "完成", "billable": False}):
                app.run_ai({"provider": "codex", "prompt": "分析", "evidence": {"fact": "现金流"}})
            after = app.settings()["monthly_tokens_used"]
            self.assertGreater(after, before)
            self.assertEqual(app.dashboard()["reports"][0]["provider"], "codex")
            del app
            gc.collect()

    def test_ai_doctrine_output_is_contracted_audited_and_archived(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            evidence = {
                "doctrine_by_library": [
                    {"library_id": 1, "library_name": "红利体系", "rules": [{"id": 11}]},
                    {"library_id": 2, "library_name": "格雷厄姆体系", "rules": [{"id": 22}]},
                ],
                "approved_doctrine_rules": [
                    {"id": 11, "library_id": 1, "library_name": "红利体系", "current_version": 2},
                    {"id": 22, "library_id": 2, "library_name": "格雷厄姆体系", "current_version": 3},
                ],
            }
            compliant_content = """## 财务事实与系统计算
事实。
## 流派结论
### 流派：红利体系
适用性、结论、规则依据与反证。
### 流派：格雷厄姆体系
适用性、结论、规则依据与反证。
## 共识
现金质量重要。
## 冲突
估值阈值不同。
## 情景、反方证据与失效条件
压力情景。
"""
            with patch.object(app, "_run_codex", side_effect=[
                {"provider": "codex", "content": compliant_content, "billable": False},
                {"provider": "codex", "content": "两个流派综合评分为七分。", "billable": False},
            ]):
                compliant = app.run_ai({"provider": "codex", "symbol": "601088", "prompt": "研究", "evidence": evidence})
                noncompliant = app.run_ai({"provider": "codex", "symbol": "601088", "prompt": "研究", "evidence": evidence})

            self.assertEqual(compliant["doctrine_coverage"]["status"], "compliant")
            self.assertEqual(noncompliant["doctrine_coverage"]["status"], "needs_review")
            self.assertEqual(
                noncompliant["doctrine_coverage"]["missing_libraries"],
                ["红利体系", "格雷厄姆体系"],
            )
            reports = app.list_reports(symbol="601088")
            self.assertEqual(reports[0]["doctrine_coverage"]["status"], "needs_review")
            archived = app.get_report_snapshot(reports[1]["id"])
            self.assertEqual(archived["result"]["doctrine_coverage"]["status"], "compliant")
            prompt_input = archived["snapshot"]["prompt"]["exact_model_input"]["input"]
            self.assertIn("### 流派：红利体系", prompt_input)
            self.assertIn("### 流派：格雷厄姆体系", prompt_input)
            self.assertIn("## 共识", prompt_input)
            self.assertIn("## 冲突", prompt_input)
            del app
            gc.collect()

    def test_compatible_api_sends_system_prompt_once_and_archives_exact_input(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.update_settings({
                "api_base_url": "http://localhost:11434/v1",
                "api_model": "test-model",
            })
            response = MagicMock()
            response.read.return_value = json.dumps({
                "choices": [{"message": {"content": "完成"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            }).encode("utf-8")
            response.__enter__.return_value = response
            with patch.dict(os.environ, {"INVESTMENT_BRAIN_API_KEY": "secret"}), patch(
                "backend.app.urllib.request.urlopen", return_value=response
            ) as urlopen:
                app.run_ai({
                    "provider": "api",
                    "symbol": "601088",
                    "prompt": "分析现金流",
                    "evidence": {"fact": "经营现金流"},
                })
            request = urlopen.call_args.args[0]
            sent = json.loads(request.data.decode("utf-8"))
            self.assertEqual(sent["messages"][0]["content"], SYSTEM_PROMPT)
            self.assertNotIn(SYSTEM_PROMPT, sent["messages"][1]["content"])
            report_id = app.list_reports(symbol="601088")[0]["id"]
            archived = app.get_report_snapshot(report_id)["snapshot"]["prompt"]
            self.assertEqual(archived["delivery"], "openai-compatible-chat-completions")
            self.assertEqual(archived["exact_model_input"]["messages"], sent["messages"])
            self.assertEqual(archived["exact_model_input"]["temperature"], sent["temperature"])
            self.assertNotIn("secret", json.dumps(app.get_report_snapshot(report_id), ensure_ascii=False))
            del app
            gc.collect()

    def test_saved_report_archive_returns_narrative_without_exposing_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            with patch.object(app, "_run_codex", return_value={"provider": "codex", "content": "现金流持续覆盖分红。", "billable": False}):
                app.run_ai({
                    "provider": "codex",
                    "symbol": "601088",
                    "title": "中国神华财务研究",
                    "prompt": "分析现金流",
                    "knowledge_query": "红利质量",
                    "evidence": {"secret": "不要把证据全文返回到列表页"},
                })
            reports = app.list_reports(symbol="601088")
            self.assertEqual(len(reports), 1)
            self.assertIn("现金流持续覆盖分红", reports[0]["content"])
            self.assertNotIn("secret", reports[0])
            metadata_only = app.list_reports(symbol="601088", include_content=False)
            self.assertEqual(metadata_only[0]["content"], "")
            narrative = app.get_report_narrative(reports[0]["id"])
            self.assertEqual(narrative["content"], reports[0]["content"])
            self.assertNotIn("secret", narrative["content"])
            del app
            gc.collect()

    def test_report_snapshot_diff_and_review_are_reproducible(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            first_evidence = {
                "financial_analysis": {
                    "as_of": "2025-03-31",
                    "engine": "deterministic_decimal_finance_engine",
                    "market": {"price_as_of": "2025-04-01"},
                    "periods": {"ttm": {"metrics": {"free_cash_flow": {"value": 100}}}},
                    "changes": {"signals": [{"id": "profit_cash_divergence", "severity": "yellow", "title": "利润现金背离", "detail": "开始背离", "review_condition": "下季复核"}]},
                },
                "approved_doctrine_rules": [{"id": 7, "library_id": 1, "library_name": "红利", "title": "现金覆盖", "current_version": 2}],
            }
            second_evidence = {
                "financial_analysis": {
                    "as_of": "2025-06-30",
                    "engine": "deterministic_decimal_finance_engine",
                    "market": {"price_as_of": "2025-07-01"},
                    "periods": {"ttm": {"metrics": {"free_cash_flow": {"value": 72}}}},
                    "changes": {"signals": [
                        {"id": "profit_cash_divergence", "severity": "red", "title": "利润现金背离", "detail": "背离扩大", "review_condition": "立即复核"},
                        {"id": "short_debt_cash_gap", "severity": "yellow", "title": "短债现金缺口", "detail": "覆盖下降", "review_condition": "核对授信"},
                    ]},
                },
                "approved_doctrine_rules": [{"id": 7, "library_id": 1, "library_name": "红利", "title": "现金覆盖", "current_version": 3}],
            }
            with patch.object(app, "_run_codex", side_effect=[
                {"provider": "codex", "model": "gpt-test", "content": "第一版", "billable": False},
                {"provider": "codex", "model": "gpt-test", "content": "第二版", "billable": False},
            ]):
                app.run_ai({"provider": "codex", "symbol": "601088", "as_of": "2025-03-31", "prompt": "研究", "evidence": first_evidence})
                app.run_ai({"provider": "codex", "symbol": "601088", "as_of": "2025-06-30", "prompt": "研究", "evidence": second_evidence})
            reports = app.list_reports(symbol="601088")
            self.assertEqual(len(reports), 2)
            newest, oldest = reports[0]["id"], reports[1]["id"]
            snapshot = app.get_report_snapshot(oldest)
            self.assertTrue(snapshot["replayable"])
            self.assertEqual(snapshot["snapshot"]["schema_version"], "report-snapshot/2")
            self.assertEqual(snapshot["snapshot"]["rule_versions"][0]["version"], 2)
            self.assertEqual(snapshot["evidence"]["financial_analysis"]["as_of"], "2025-03-31")
            self.assertEqual(len(snapshot["snapshot"]["formula_engine_hashes"]["finance_engine.py"]), 64)
            self.assertEqual(snapshot["integrity"]["status"], "verified")
            self.assertTrue(snapshot["integrity"]["formula_matches_current"])
            integrity_summary = app.get_report_integrity(oldest)
            self.assertEqual(integrity_summary["integrity"]["status"], "verified")
            self.assertTrue(integrity_summary["replayable"])
            self.assertNotIn("evidence", integrity_summary)
            self.assertNotIn("snapshot", integrity_summary)
            self.assertNotIn("prompt", integrity_summary)
            self.assertLess(len(json.dumps(integrity_summary, ensure_ascii=False)), 2_000)
            prompt_snapshot = snapshot["snapshot"]["prompt"]
            self.assertEqual(prompt_snapshot["schema_version"], "financial-research-prompt/2")
            self.assertEqual(prompt_snapshot["delivery"], "codex-cli-stdin")
            self.assertIn(SYSTEM_PROMPT, prompt_snapshot["exact_model_input"]["input"])
            self.assertIn("用户问题：研究", prompt_snapshot["exact_model_input"]["input"])
            diff = app.compare_reports(oldest, newest)
            self.assertEqual(diff["schema_version"], "report-diff/3")
            self.assertEqual(diff["report_kind"], "company")
            self.assertEqual(diff["metric_changes"][0], {"metric": "free_cash_flow", "before": 100, "after": 72})
            self.assertEqual(diff["rule_changes"][0]["after"], 3)
            signal_changes = {item["signal_id"]: item for item in diff["risk_signal_changes"]}
            self.assertEqual(signal_changes["profit_cash_divergence"]["change"], "changed")
            self.assertEqual(signal_changes["short_debt_cash_gap"]["change"], "added")
            self.assertTrue(diff["data_context_changed"])
            self.assertTrue(diff["narrative_changed"])
            review = app.update_report_review(
                newest,
                {"status": "reviewed", "expected_conditions": "现金流恢复", "actual_outcome": "仍在观察", "due_at": "2025-09-30"},
            )
            self.assertEqual(review["status"], "reviewed")
            self.assertEqual(app.get_report_snapshot(newest)["review"]["actual_outcome"], "仍在观察")
            reviewed_diff = app.compare_reports(oldest, newest)
            self.assertEqual(
                reviewed_diff["judgment_follow_up"]["after_report"]["actual_outcome"],
                "仍在观察",
            )
            with app.connect() as conn:
                conn.execute("UPDATE reports SET evidence_json=? WHERE id=?", ('{"tampered":true}', oldest))
            conn.close()
            damaged = app.get_report_snapshot(oldest)
            self.assertFalse(damaged["replayable"])
            self.assertEqual(damaged["integrity"]["status"], "corrupt")
            self.assertEqual(app.get_report_integrity(oldest)["integrity"]["status"], "corrupt")
            report_health = app.data_health()["reports"]
            self.assertEqual(report_health["count"], 2)
            self.assertEqual(report_health["replayable"], 1)
            self.assertEqual(report_health["corrupt"], 1)
            self.assertEqual(report_health["partial"], 0)
            del app
            gc.collect()

    def test_report_review_conditions_create_and_clear_due_reminders(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            with patch.object(app, "_run_codex", return_value={"provider": "codex", "content": "结论", "billable": False}):
                app.run_ai({"provider": "codex", "symbol": "601088", "prompt": "研究", "evidence": {"fact": "现金流"}})
            report_id = app.list_reports(symbol="601088")[0]["id"]
            with self.assertRaisesRegex(ValueError, "验证条件和复核日期"):
                app.update_report_review(report_id, {"status": "open", "due_at": date.today().isoformat()})
            with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
                app.update_report_review(report_id, {"status": "open", "expected_conditions": "现金流恢复", "due_at": "明天"})
            overdue = (date.today() - timedelta(days=2)).isoformat()
            opened = app.update_report_review(report_id, {
                "status": "open",
                "expected_conditions": "下一季经营现金流恢复",
                "due_at": overdue,
            })
            self.assertEqual(opened["reminder"]["status"], "overdue")
            self.assertEqual(opened["reminder"]["days_until_due"], -2)
            reminders = app.list_report_reminders()
            self.assertEqual(reminders[0]["report_id"], report_id)
            listed = app.list_reports(symbol="601088")[0]
            self.assertEqual(listed["expected_conditions"], "下一季经营现金流恢复")
            self.assertEqual(listed["reminder"]["status"], "overdue")
            with self.assertRaisesRegex(ValueError, "实际结果或复盘笔记"):
                app.update_report_review(report_id, {"status": "reviewed"})
            completed = app.update_report_review(report_id, {
                "status": "reviewed",
                "expected_conditions": "下一季经营现金流恢复",
                "actual_outcome": "经营现金流尚未恢复",
                "actual_as_of": date.today().isoformat(),
            })
            self.assertEqual(completed["reminder"]["status"], "completed")
            self.assertEqual(app.list_report_reminders(), [])
            del app
            gc.collect()

    def test_database_backup_is_recoverable_and_path_is_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.save_holding({"symbol": "601088", "quantity": 10, "cost": 20})
            backup = app.create_backup()
            self.assertTrue(Path(backup["path"]).exists())
            self.assertGreater(backup["size_bytes"], 0)
            self.assertEqual(backup["integrity"], "ok")
            self.assertEqual(len(backup["sha256"]), 64)
            second_backup = app.create_backup()
            self.assertNotEqual(second_backup["file_name"], backup["file_name"])
            self.assertEqual(len(app.list_backups()), 2)
            with self.assertRaisesRegex(ValueError, "禁止覆盖"):
                app.create_backup(backup["path"])
            with self.assertRaisesRegex(ValueError, "只能保存到"):
                app.create_backup(str(Path(temp).parent / "outside.db"))
            restored = sqlite3.connect(backup["path"])
            try:
                self.assertEqual(restored.execute("SELECT COUNT(*) FROM holdings WHERE symbol='601088'").fetchone()[0], 1)
            finally:
                restored.close()
            del app
            gc.collect()

    def test_restore_requires_confirmation_and_preserves_safety_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.save_holding({"symbol": "601088", "quantity": 10, "cost": 20})
            backup = app.create_backup()
            app.save_holding({"symbol": "600519", "quantity": 2, "cost": 100})
            with self.assertRaisesRegex(ValueError, "明确确认"):
                app.restore_backup(backup["path"])
            result = app.restore_backup(backup["path"], confirm=True)
            self.assertEqual(result["integrity"], "ok")
            self.assertEqual(result["safety_backup"]["file_name"].endswith(".db"), True)
            with app.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM holdings WHERE symbol='601088'").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM holdings WHERE symbol='600519'").fetchone()[0], 0)
            conn.close()
            del app
            gc.collect()

    def test_holdings_export_and_data_health_disclose_freshness(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.save_holding({"symbol": "601088", "name": "中国神华", "industry": "煤炭", "quantity": 3, "cost": 20, "thesis": "现金流"})
            exported = app.export_holdings_csv()
            self.assertEqual(exported["count"], 1)
            self.assertIn("代码,名称,行业", exported["csv"])
            self.assertIn("601088,中国神华,煤炭", exported["csv"])
            health = app.data_health()
            self.assertIn(health["financial_snapshots"]["status"], {"unknown", "fresh", "mixed", "stale"})
            self.assertEqual(health["knowledge"]["chunks"], 0)
            self.assertEqual(health["knowledge"]["eligible_chunks"], 0)
            self.assertEqual(health["filings"]["coverage_status"], "none")
            self.assertIn("缺失数据", health["policy"])
            del app
            gc.collect()

    def test_data_health_reports_snapshot_freshness_per_company(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            with app.connect() as conn:
                conn.execute(
                    """INSERT INTO financial_snapshots(symbol, provider, payload_json, retrieved_at)
                       VALUES ('600036.SH', 'fixture', '{}', datetime('now'))"""
                )
                conn.execute(
                    """INSERT INTO financial_snapshots(symbol, provider, payload_json, retrieved_at)
                       VALUES ('601088.SH', 'fixture', '{}', datetime('now', '-10 days'))"""
                )
            conn.close()
            health = app.data_health()["financial_snapshots"]
            self.assertEqual(health["companies"], 2)
            self.assertEqual(health["fresh_companies"], 1)
            self.assertEqual(health["stale_companies"], 1)
            self.assertEqual(health["unknown_companies"], 0)
            self.assertEqual(health["status"], "mixed")
            del app
            del conn
            gc.collect()

    def test_data_health_only_counts_enabled_knowledge_and_searchable_filings_as_usable(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            with app.connect() as conn:
                enabled_id = conn.execute("INSERT INTO libraries(name, enabled) VALUES ('红利', 1)").lastrowid
                disabled_id = conn.execute("INSERT INTO libraries(name, enabled) VALUES ('停用体系', 0)").lastrowid
                for index, library_id in enumerate((enabled_id, disabled_id), start=1):
                    document_id = conn.execute(
                        """INSERT INTO documents(library_id, source_path, file_name, file_type, content_hash, status)
                           VALUES (?, ?, ?, 'docx', ?, 'ready')""",
                        (library_id, f"book-{index}.docx", f"book-{index}.docx", str(index) * 64),
                    ).lastrowid
                    conn.execute(
                        """INSERT INTO chunks(document_id, ordinal, content, start_locator, end_locator, locator)
                           VALUES (?, 0, '测试知识片段', 'p:1', 'p:1', 'p:1')""",
                        (document_id,),
                    )
                ready_artifact = conn.execute(
                    "INSERT INTO filing_artifacts(sha256, relative_path, byte_size, extraction_status) VALUES (?, 'ready.pdf', 10, 'ready')",
                    ("a" * 64,),
                ).lastrowid
                pending_artifact = conn.execute(
                    "INSERT INTO filing_artifacts(sha256, relative_path, byte_size, extraction_status) VALUES (?, 'pending.pdf', 10, 'pending')",
                    ("b" * 64,),
                ).lastrowid
                conn.execute(
                    "INSERT INTO filings(source_key, symbol, title, source_provider, artifact_id, retrieved_at) VALUES ('ready', '600036', '已提取年报', 'test', ?, CURRENT_TIMESTAMP)",
                    (ready_artifact,),
                )
                conn.execute(
                    "INSERT INTO filings(source_key, symbol, title, source_provider, artifact_id, retrieved_at) VALUES ('pending', '600036', '待提取公告', 'test', ?, CURRENT_TIMESTAMP)",
                    (pending_artifact,),
                )
            conn.close()
            health = app.data_health()
            self.assertEqual(health["knowledge"]["chunks"], 2)
            self.assertEqual(health["knowledge"]["eligible_chunks"], 1)
            self.assertEqual(health["knowledge"]["disabled_chunks"], 1)
            self.assertEqual(health["filings"]["searchable"], 1)
            self.assertEqual(health["filings"]["unsearchable"], 1)
            self.assertEqual(health["filings"]["coverage_status"], "partial")
            del app
            del conn
            gc.collect()

    def test_watchlist_persists_updates_and_removes(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            saved = app.save_watch({"symbol": "600519.SH", "name": "贵州茅台", "note": "等待估值"})
            self.assertEqual(saved["symbol"], "600519")
            updated = app.save_watch({"symbol": "600519", "name": "贵州茅台", "note": "等待财报"})
            self.assertEqual(updated["note"], "等待财报")
            listed = app.list_watchlist()
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["research"]["status"], "等待条件")
            self.assertIn("尚无真实财务快照", listed[0]["research"]["reasons"])
            self.assertIn("不构成建仓建议", listed[0]["research"]["execution_policy"])
            with self.assertRaisesRegex(ValueError, "6 位"):
                app.save_watch({"symbol": "ABC"})
            app.delete_watch("600519")
            self.assertEqual(app.list_watchlist(), [])
            del app
            gc.collect()

    def test_watchlist_research_state_separates_pending_and_confirmed_filing_risk(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.save_watch({"symbol": "600036", "name": "招商银行", "note": "等待资产质量验证"})
            with app.connect() as conn:
                ready_artifact = conn.execute(
                    """INSERT INTO filing_artifacts(sha256, relative_path, byte_size, extraction_status)
                       VALUES (?, '600036/watch-ready.pdf', 10, 'ready')""",
                    ("9" * 64,),
                ).lastrowid
                scan_artifact = conn.execute(
                    """INSERT INTO filing_artifacts(sha256, relative_path, byte_size, extraction_status)
                       VALUES (?, '600036/watch-scan.pdf', 10, 'needs_ocr')""",
                    ("0" * 64,),
                ).lastrowid
                filing_id = conn.execute(
                    """INSERT INTO filings(source_key, symbol, title, source_provider, artifact_id, disclosed_at, retrieved_at)
                       VALUES ('watch-ready', '600036', '招商银行年度报告', 'test', ?, '2026-03-20', '2026-03-20')""",
                    (ready_artifact,),
                ).lastrowid
                conn.execute(
                    """INSERT INTO filings(source_key, symbol, title, source_provider, artifact_id, disclosed_at, retrieved_at)
                       VALUES ('watch-scan', '600036', '招商银行审计报告扫描件', 'test', ?, '2026-03-21', '2026-03-21')""",
                    (scan_artifact,),
                )
                evidence_id = conn.execute(
                    """INSERT INTO filing_evidence(
                           filing_id, category, severity, title, excerpt, page_number, locator, review_status)
                       VALUES (?, 'audit_opinion', 'red', '审计意见异常措辞', '关键词命中，尚待核对', 8, 'pdf:p:000008', 'pending')""",
                    (filing_id,),
                ).lastrowid
            conn.close()

            pending = app.list_watchlist()[0]["research"]
            self.assertEqual(pending["priority"], "yellow")
            self.assertEqual(pending["filing_review"]["pending_evidence"], 1)
            self.assertEqual(pending["filing_review"]["confirmed_red"], 0)
            self.assertEqual(pending["filing_review"]["unsearchable"], 1)
            self.assertTrue(any("不按事实处理" in reason for reason in pending["reasons"]))

            with app.connect() as conn:
                conn.execute("UPDATE filing_evidence SET review_status='confirmed' WHERE id=?", (evidence_id,))
            conn.close()
            confirmed = app.list_watchlist()[0]["research"]
            self.assertEqual(confirmed["priority"], "red")
            self.assertEqual(confirmed["filing_review"]["confirmed_red"], 1)
            self.assertTrue(any("人工确认" in reason for reason in confirmed["reasons"]))
            del app
            del conn
            gc.collect()

    def test_watchlist_exposes_cached_quote_without_claiming_freshness(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.save_watch({"symbol": "600519", "name": "贵州茅台"})
            with app.connect() as conn:
                conn.execute(
                    "INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json, retrieved_at) VALUES (?, ?, ?, ?, datetime('now', '-2 days'))",
                    ("600519.SH", "", "tushare", '{"as_of":"2026-08-17","valuation":{"market_price":"123.45","pe":"18.2"}}'),
                )
            conn.close()
            item = app.list_watchlist()[0]
            self.assertEqual(item["quote"]["market_price"], 123.45)
            self.assertEqual(item["quote_status"], "stale")
            with patch.object(
                app,
                "company_financial_data",
                return_value=({}, {"cache": "stale", "warning": "测试旧快照"}),
            ) as refresh:
                result = app.refresh_watchlist("600519")
            self.assertEqual(result["errors"][0]["symbol"], "600519")
            refresh.assert_called_once_with("600519", refresh=True)
            del app
            gc.collect()

    def test_current_portfolio_and_watchlist_ignore_historical_cutoff_snapshots(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            app.save_holding({"symbol": "600519", "quantity": 1, "cost": 100})
            app.save_watch({"symbol": "600519", "name": "贵州茅台"})
            current = demo_company()
            current["valuation"]["market_price"] = "100"
            historical = json.loads(json.dumps(current))
            historical["valuation"]["market_price"] = "50"
            with app.connect() as conn:
                conn.execute(
                    "INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json) VALUES ('600519.SH', '', 'current-provider', ?)",
                    (json.dumps(current),),
                )
                conn.execute(
                    "INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json) VALUES ('600519.SH', '2020-12-31', 'historical-provider', ?)",
                    (json.dumps(historical),),
                )
            conn.close()
            position = app.portfolio_diagnostics()["positions"][0]
            watched = app.list_watchlist()[0]
            self.assertEqual(position["snapshot_provider"], "current-provider")
            self.assertEqual(position["price"], 100.0)
            self.assertEqual(watched["quote"]["provider"], "current-provider")
            self.assertEqual(float(watched["quote"]["market_price"]), 100.0)
            health = app.data_health()["financial_snapshots"]
            self.assertEqual(health["count"], 1)
            self.assertEqual(health["historical_count"], 1)
            del app
            del conn
            gc.collect()

    def test_codex_json_event_parser_returns_final_agent_message(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "test.db")
            stdout = "\n".join([
                '{"type":"thread.started","thread_id":"x"}',
                '{"type":"item.completed","item":{"type":"agent_message","text":"连接成功"}}',
                '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}',
            ])
            fake = type("Proc", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()
            with patch("backend.app.subprocess.run", return_value=fake) as called:
                result = app._run_codex("测试", {"fact": 1})
            self.assertEqual(result["content"], "连接成功")
            args = called.call_args.args[0]
            self.assertIn("--skip-git-repo-check", args)
            self.assertIn("--", args)
            del app
            gc.collect()


if __name__ == "__main__":
    unittest.main()
