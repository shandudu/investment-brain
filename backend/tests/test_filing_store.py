import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from backend.filing_store import (
    _extraction_quality,
    archive_filing,
    extract_bank_regulatory_facts,
    extract_evidence_signals,
    filing_stats,
    filing_evidence_coverage,
    finish_filing_sync_run,
    get_filing,
    get_filing_sync_state,
    init_filing_store,
    list_filing_evidence,
    list_filings,
    ocr_filing_batch,
    reindex_filing,
    start_filing_sync_run,
    update_filing_evidence_review,
    validate_official_filing_url,
)


class FilingStoreTests(unittest.TestCase):
    def test_late_report_evidence_is_not_lost_after_eight_early_matches(self):
        pages = [(page, "历史报告：标准无保留意见。") for page in range(1, 10)]
        pages.append((210, "本期审计报告：无法表示意见。审计证据不足。"))
        pages.extend((page, f"应收账款附注第{page}页：账龄构成与坏账准备。") for page in range(220, 231))
        signals = extract_evidence_signals(pages)
        self.assertTrue(any(item["page_number"] == 210 and item["severity"] == "red" for item in signals))
        self.assertTrue(any(item["page_number"] == 230 and item["category"] == "receivables_aging" for item in signals))

    def test_going_concern_section_is_extracted_without_opinion_keyword(self):
        signals = extract_evidence_signals([(17, "与持续经营相关的重大不确定性。公司流动负债超过流动资产，融资安排尚待落实。")])
        audit = next(item for item in signals if item["category"] == "audit_opinion")
        self.assertEqual(audit["severity"], "review")
        self.assertEqual(audit["locator"], "pdf:p:000017")
        self.assertIn("融资安排尚待落实", audit["excerpt"])

    def test_unqualified_audit_with_material_context_requires_review(self):
        from backend.filing_store import _signal_severity
        for excerpt in (
            "无保留意见，包含强调事项段",
            "无保留意见，持续经营存在重大不确定性",
            "上年无保留意见，本年保留意见",
            "审计机构出具非标准无保留意见",
            "本报告属于非标准审计意见",
        ):
            with self.subTest(excerpt=excerpt):
                self.assertEqual(_signal_severity("audit_opinion", excerpt), "review")
        self.assertEqual(_signal_severity("audit_opinion", "标准无保留意见"), "info")
        self.assertEqual(_signal_severity("audit_opinion", "保留意见"), "red")
        self.assertEqual(_signal_severity("audit_opinion", "无法表示意见"), "red")

    def test_common_annual_report_wording_is_extracted_as_review_leads(self):
        pages = [
            (10, "应收款项按账龄组合计提预期信用损失，逾期应收余额详见附注。"),
            (11, "存货及存货跌价准备按类别列示，并披露相关库龄。"),
            (12, "货币资金中保证金存款属于使用受限的货币资金。"),
            (13, "开发阶段支出满足资本化条件，研究阶段支出全部费用化。"),
            (14, "公司本期首次执行企业会计准则解释第十八号。"),
        ]
        signals = extract_evidence_signals(pages)
        by_category = {item["category"]: item for item in signals}
        expected = {
            "receivables_aging": 10,
            "inventory_impairment": 11,
            "restricted_cash": 12,
            "capitalization": 13,
            "accounting_change": 14,
        }
        for category, page_number in expected.items():
            with self.subTest(category=category):
                self.assertEqual(by_category[category]["page_number"], page_number)
                self.assertEqual(by_category[category]["locator"], f"pdf:p:{page_number:06d}")
                self.assertEqual(by_category[category]["severity"], "review")

    def test_extraction_quality_rejects_garbled_custom_font_text(self):
        readable = [(1, "招商银行股份有限公司 2025 年年度报告 audit 600036")]
        garbled = [(1, "ඳ 2025čAĎ ଢ੣ གิൕ ᇗေิൕ ൙ӉᇁՊ 7 ྛӉᇁՊ")]
        self.assertGreater(_extraction_quality(readable), 0.95)
        self.assertLess(_extraction_quality(garbled), 0.82)

    def test_local_pdf_is_immutable_hash_deduplicated_and_traceable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "sources"
            source_root.mkdir()
            pdf_path = source_root / "annual-report.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            with pdf_path.open("wb") as stream:
                writer.write(stream)
            database = root / "brain.db"
            archive_root = root / "archive"
            init_filing_store(database)

            created = archive_filing(
                database,
                archive_root,
                symbol="600519",
                title="贵州茅台2025年年度报告",
                source_provider="manual_official_copy",
                local_path=pdf_path,
                allowed_local_root=source_root,
                filing_type="annual_report",
                report_period="2025-12-31",
                disclosed_at="2026-03-30T18:00:00+08:00",
            )
            self.assertFalse(created["duplicate"])
            self.assertEqual(created["symbol"], "600519")
            self.assertEqual(len(created["sha256"]), 64)
            self.assertTrue((archive_root / created["relative_path"]).is_file())

            duplicate = archive_filing(
                database,
                archive_root,
                symbol="600519",
                title="贵州茅台2025年年度报告",
                source_provider="manual_official_copy",
                local_path=pdf_path,
                allowed_local_root=source_root,
            )
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(len(list_filings(database, symbol="600519")), 1)
            self.assertEqual(filing_stats(database)["filings"], 1)

    def test_correction_lineage_is_auto_linked_only_for_one_exact_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "sources"
            source_root.mkdir()
            pdf_path = source_root / "filing.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with pdf_path.open("wb") as stream:
                writer.write(stream)
            database = root / "brain.db"
            archive_root = root / "archive"
            init_filing_store(database)
            correction = archive_filing(
                database, archive_root, symbol="600519", title="贵州茅台2025年年度报告更正公告",
                source_provider="manual", local_path=pdf_path, allowed_local_root=source_root,
                filing_type="correction", report_period="2025-12-31",
                disclosed_at="2026-04-02T18:00:00+08:00", is_correction=True,
            )
            self.assertIsNone(correction["supersedes_filing_id"])
            original = archive_filing(
                database, archive_root, symbol="600519", title="贵州茅台2025年年度报告",
                source_provider="manual", local_path=pdf_path, allowed_local_root=source_root,
                filing_type="annual_report", report_period="2025-12-31",
                disclosed_at="2026-03-30T18:00:00+08:00",
            )
            correction = get_filing(database, correction["id"])
            self.assertEqual(correction["supersedes_filing_id"], original["id"])
            self.assertEqual(correction["supersedes_title"], original["title"])
            self.assertEqual(correction["metadata"]["supersession_link_method"], "exact_symbol_period_type")
            refreshed_original = get_filing(database, original["id"])
            self.assertEqual(refreshed_original["superseded_by_id"], correction["id"])
            self.assertEqual(refreshed_original["superseded_by_title"], correction["title"])

    def test_explicit_correction_lineage_rejects_another_company(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "sources"
            source_root.mkdir()
            pdf_path = source_root / "filing.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with pdf_path.open("wb") as stream:
                writer.write(stream)
            database = root / "brain.db"
            archive_root = root / "archive"
            init_filing_store(database)
            original = archive_filing(
                database, archive_root, symbol="600519", title="贵州茅台2025年年度报告",
                source_provider="manual", local_path=pdf_path, allowed_local_root=source_root,
                filing_type="annual_report", report_period="2025-12-31",
            )
            with self.assertRaisesRegex(ValueError, "同一公司"):
                archive_filing(
                    database, archive_root, symbol="000001", title="平安银行更正公告",
                    source_provider="manual", local_path=pdf_path, allowed_local_root=source_root,
                    filing_type="correction", report_period="2025-12-31", is_correction=True,
                    supersedes_filing_id=original["id"],
                )

    def test_local_path_must_stay_inside_configured_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            allowed = root / "allowed"
            allowed.mkdir()
            outside = root / "outside.pdf"
            outside.write_bytes(b"%PDF-not-a-real-file")
            database = root / "brain.db"
            init_filing_store(database)
            with self.assertRaisesRegex(ValueError, "知识根目录"):
                archive_filing(
                    database,
                    root / "archive",
                    symbol="000001",
                    title="测试公告",
                    source_provider="manual",
                    local_path=outside,
                    allowed_local_root=allowed,
                )

    def test_reindex_preserves_pdf_hash_and_rebuilds_derived_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "sources"
            source_root.mkdir()
            pdf_path = source_root / "report.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with pdf_path.open("wb") as stream:
                writer.write(stream)
            database = root / "brain.db"
            archive_root = root / "archive"
            init_filing_store(database)
            filing = archive_filing(
                database,
                archive_root,
                symbol="600519",
                title="测试审计报告",
                source_provider="manual",
                local_path=pdf_path,
                allowed_local_root=source_root,
            )
            original_hash = filing["sha256"]
            with patch(
                "backend.filing_store._extract_pdf_pages",
                return_value=([(7, "审计机构出具保留意见，涉及应收账款12亿元。")], "ready", 1.0, "测试可读"),
            ):
                reindexed = reindex_filing(database, archive_root, filing["id"])
            self.assertEqual(reindexed["sha256"], original_hash)
            self.assertEqual(reindexed["extraction_status"], "ready")
            evidence = list_filing_evidence(database, filing["id"])
            self.assertTrue(any(item["category"] == "audit_opinion" for item in evidence))
            self.assertTrue(any(item["locator"] == "pdf:p:000007" for item in evidence))
            reviewed = next(item for item in evidence if item["category"] == "audit_opinion")
            update_filing_evidence_review(database, reviewed["id"], review_status="confirmed", reviewer_note="已核对原件")
            with patch("backend.filing_store._extract_pdf_pages", return_value=([(7, "审计机构出具保留意见，涉及应收账款12亿元。")], "ready", 1.0, "测试可读")):
                reindex_filing(database, archive_root, filing["id"])
            retained = next(item for item in list_filing_evidence(database, filing["id"]) if item["category"] == "audit_opinion")
            self.assertEqual(retained["id"], reviewed["id"])
            self.assertEqual(retained["review_status"], "confirmed")
            self.assertEqual(retained["reviewer_note"], "已核对原件")

    def test_ocr_runs_in_persistent_batches_and_never_claims_full_coverage_early(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "sources"
            source_root.mkdir()
            pdf_path = source_root / "two-pages.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            writer.add_blank_page(width=100, height=100)
            with pdf_path.open("wb") as stream:
                writer.write(stream)
            database = root / "brain.db"
            archive_root = root / "archive"
            init_filing_store(database)
            filing = archive_filing(
                database,
                archive_root,
                symbol="600519",
                title="乱码年报",
                source_provider="manual",
                local_path=pdf_path,
                allowed_local_root=source_root,
            )
            with patch(
                "backend.filing_store._run_ocr_pages",
                side_effect=[[(1, "关键审计事项包括收入确认。")], [(2, "对外担保余额12亿元。")]],
            ):
                first = ocr_filing_batch(database, archive_root, filing["id"], batch_size=1)
                first_coverage = filing_evidence_coverage(database, filing["id"])
                second = ocr_filing_batch(database, archive_root, filing["id"], batch_size=1)
            self.assertEqual(first["extraction_status"], "ocr_partial")
            self.assertFalse(first_coverage["searchable"])
            self.assertTrue(first_coverage["partially_searchable"])
            self.assertEqual(first_coverage["ocr_pages_done"], 1)
            self.assertEqual(second["extraction_status"], "ready_ocr")
            self.assertEqual(second["ocr_pages_done"], 2)
            categories = {item["category"] for item in list_filing_evidence(database, filing["id"])}
            self.assertIn("key_audit_matter", categories)
            self.assertIn("guarantee", categories)
            reviewed = next(item for item in list_filing_evidence(database, filing["id"]) if item["category"] == "key_audit_matter")
            update_filing_evidence_review(database, reviewed["id"], review_status="dismissed", reviewer_note="已核对无需跟进")
            # Simulate a retried worker returning an already processed page.
            import sqlite3
            with sqlite3.connect(database) as connection:
                connection.execute("DELETE FROM filing_ocr_pages WHERE page_number=1")
            connection.close()
            with patch("backend.filing_store._run_ocr_pages", return_value=[(1, "关键审计事项包括收入确认。")]):
                ocr_filing_batch(database, archive_root, filing["id"], batch_size=1)
            retained = next(item for item in list_filing_evidence(database, filing["id"]) if item["id"] == reviewed["id"])
            self.assertEqual(retained["review_status"], "dismissed")
            self.assertEqual(retained["reviewer_note"], "已核对无需跟进")

    def test_official_url_allowlist_blocks_ssrf_and_unstable_links(self):
        valid = validate_official_filing_url(
            "https://static.cninfo.com.cn/finalpage/2026-02-27/1224987042.PDF"
        )
        self.assertTrue(valid.endswith("1224987042.PDF"))
        for url in (
            "http://static.cninfo.com.cn/a.pdf",
            "https://evil.example/a.pdf",
            "https://static.cninfo.com.cn/a.pdf?token=secret",
            "https://static.cninfo.com.cn/a.html",
        ):
            with self.assertRaises(ValueError):
                validate_official_filing_url(url)

    def test_keyword_signals_are_page_located_review_leads(self):
        pages = [
            (8, "审计机构出具标准无保留意见。关键审计事项包括收入确认。"),
            (42, "本期发生前期差错更正并进行了追溯调整。"),
            (77, "公司存在对外担保余额，详见或有事项。"),
        ]
        signals = extract_evidence_signals(pages)
        audit = next(item for item in signals if item["category"] == "audit_opinion")
        correction = next(item for item in signals if item["category"] == "accounting_change")
        self.assertEqual(audit["severity"], "info")
        self.assertEqual(audit["locator"], "pdf:p:000008")
        self.assertEqual(correction["severity"], "red")
        self.assertEqual(correction["detected_values"], [])
        self.assertTrue(all(item["excerpt"] for item in signals))

        numeric = extract_evidence_signals([(9, "对外担保余额为12.50亿元，占净资产6.2%。")])
        values = next(item for item in numeric if item["category"] == "guarantee")["detected_values"]
        self.assertEqual([item["raw"] for item in values], ["12.50亿元", "6.2%"])

    def test_bank_regulatory_facts_are_deterministic_page_located_candidates(self):
        pages = [
            (9, "本集团不良贷款率0.94%，拨备覆盖率387.76%，贷款拨备率3.63%。本集团净利息收益率1.83%。"),
            (10, "本公司房地产业不良贷款率4.44%。关注贷款率1.48%，逾期贷款率1.29%。"),
            (11, "截至报告期末，本集团高级法下核心一级资本充足率14.13%，一级资本充足率16.05%，资本充足率17.76%。"),
        ]
        facts = extract_bank_regulatory_facts(pages, period_end="2026-03-31")
        by_metric = {item["metric"]: item for item in facts}
        self.assertAlmostEqual(by_metric["nonperforming_loan_ratio"]["value"], 0.0094)
        self.assertAlmostEqual(by_metric["provision_coverage_ratio"]["value"], 3.8776)
        self.assertAlmostEqual(by_metric["core_tier1_capital_ratio"]["value"], 0.1413)
        self.assertAlmostEqual(by_metric["tier1_capital_ratio"]["value"], 0.1605)
        self.assertAlmostEqual(by_metric["capital_adequacy_ratio"]["value"], 0.1776)
        self.assertEqual(by_metric["capital_adequacy_ratio"]["locator"], "pdf:p:000011")
        self.assertEqual(by_metric["capital_adequacy_ratio"]["period_end"], "2026-03-31")

    def test_evidence_review_status_is_explicit_and_reversible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            pdf = source / "report.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with pdf.open("wb") as stream:
                writer.write(stream)
            database = root / "brain.db"
            init_filing_store(database)
            filing = archive_filing(
                database,
                root / "archive",
                symbol="600519",
                title="测试年报",
                source_provider="manual",
                local_path=pdf,
                allowed_local_root=source,
            )
            import sqlite3
            connection = sqlite3.connect(database)
            connection.execute(
                """INSERT INTO filing_evidence(
                       filing_id, category, severity, title, excerpt, page_number, locator)
                   VALUES (?, 'guarantee', 'review', '担保与承诺', '担保余额12亿元', 7, 'pdf:p:000007')""",
                (filing["id"],),
            )
            evidence_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.commit()
            connection.close()
            confirmed = update_filing_evidence_review(
                database, evidence_id, review_status="confirmed", reviewer_note="已核对附注"
            )
            self.assertEqual(confirmed["review_status"], "confirmed")
            self.assertEqual(confirmed["reviewer_note"], "已核对附注")
            reset = update_filing_evidence_review(database, evidence_id, review_status="pending")
            self.assertEqual(reset["review_status"], "pending")
            self.assertIsNone(reset["reviewed_at"])

    def test_filing_stats_include_regulatory_fact_review_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "brain.db"
            init_filing_store(database)
            import sqlite3
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """INSERT INTO filing_artifacts(
                           sha256, relative_path, byte_size, extraction_status)
                       VALUES ('hash-1', 'report.pdf', 1, 'ready')"""
                )
                artifact_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
                connection.execute(
                    """INSERT INTO filings(
                           source_key, symbol, title, source_provider, source_notice_id,
                           artifact_id, disclosed_at, retrieved_at)
                       VALUES ('manual:notice-1', '600036', '测试季报', 'manual', 'notice-1',
                               ?, '2026-04-29', '2026-04-29')""",
                    (artifact_id,),
                )
                filing_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
                connection.executemany(
                    """INSERT INTO filing_facts(
                           filing_id, metric, label, scope, value, unit, raw_value, page_number,
                           locator, excerpt, extraction_method, confidence, review_status)
                       VALUES (?, ?, ?, 'group', ?, 'ratio', ?, 5, 'pdf:p:000005',
                               '原文', 'pdf_text_v1', 'exact_label_group_context', ?)""",
                    [
                        (filing_id, "net_interest_margin", "净利息收益率", 0.0183, "1.83%", "pending"),
                        (filing_id, "nonperforming_loan_ratio", "不良贷款率", 0.0094, "0.94%", "confirmed"),
                    ],
                )
                connection.commit()
            finally:
                connection.close()
            stats = filing_stats(database)
            self.assertEqual(stats["pending_facts"], 1)
            self.assertEqual(stats["confirmed_facts"], 1)

    def test_incremental_sync_state_advances_only_on_success(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "brain.db"
            init_filing_store(database)
            first = start_filing_sync_run(
                database, "600519", since_date="2025-01-01", until_date="2026-01-01"
            )
            state = finish_filing_sync_run(
                database,
                first,
                status="success",
                discovered=3,
                archived=2,
                duplicates=1,
                cursor_date="2026-01-01",
                last_disclosed_at="2025-12-20T18:00:00+08:00",
            )
            self.assertEqual(state["cursor_date"], "2026-01-01")
            second = start_filing_sync_run(
                database, "600519", since_date="2025-12-30", until_date="2026-02-01"
            )
            failed = finish_filing_sync_run(
                database,
                second,
                status="partial",
                discovered=1,
                archived=0,
                duplicates=0,
                errors=[{"error": "download failed"}],
                cursor_date=None,
            )
            self.assertEqual(failed["cursor_date"], "2026-01-01")
            self.assertEqual(get_filing_sync_state(database, "600519")["status"], "partial")

    def test_coverage_distinguishes_absence_review_and_unsearchable_pdf(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            pdf = source / "report.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with pdf.open("wb") as stream:
                writer.write(stream)
            database = root / "brain.db"
            init_filing_store(database)
            filing = archive_filing(
                database,
                root / "archive",
                symbol="600519",
                title="测试年报",
                source_provider="manual",
                local_path=pdf,
                allowed_local_root=source,
            )
            coverage = filing_evidence_coverage(database, filing["id"])
            self.assertFalse(coverage["searchable"])
            self.assertTrue(all(item["status"] == "not_searchable" for item in coverage["categories"]))

            import sqlite3
            connection = sqlite3.connect(database)
            connection.execute(
                "UPDATE filing_artifacts SET extraction_status='ready' WHERE id=(SELECT artifact_id FROM filings WHERE id=?)",
                (filing["id"],),
            )
            connection.execute(
                """INSERT INTO filing_evidence(
                       filing_id, category, severity, title, excerpt, page_number, locator, review_status)
                   VALUES (?, 'guarantee', 'review', '担保与承诺', '担保余额12亿元', 7, 'pdf:p:000007', 'confirmed'),
                          (?, 'goodwill', 'red', '商誉与减值', '商誉', 8, 'pdf:p:000008', 'dismissed')""",
                (filing["id"], filing["id"]),
            )
            connection.commit()
            connection.close()
            categories = {
                item["category"]: item
                for item in filing_evidence_coverage(database, filing["id"])["categories"]
            }
            self.assertEqual(categories["guarantee"]["status"], "found_confirmed")
            self.assertEqual(categories["goodwill"]["status"], "reviewed_no_issue")
            self.assertEqual(categories["audit_opinion"]["status"], "not_found_in_extracted_text")
            summary = get_filing(database, filing["id"])
            self.assertEqual(summary["evidence_count"], 2)
            self.assertEqual(summary["active_evidence_count"], 1)
            self.assertEqual(summary["dismissed_count"], 1)
            self.assertEqual(summary["red_count"], 0)
            self.assertEqual(summary["review_count"], 1)


if __name__ == "__main__":
    unittest.main()
