from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from backend.knowledge import (
    analyze_doctrine_relationships,
    assign_document_to_library,
    chunk_blocks,
    create_doctrine_card,
    create_library,
    delete_doctrine_card,
    delete_library,
    extract_document,
    get_doctrine_card,
    get_import_status,
    generate_doctrine_candidates,
    import_documents,
    init_database,
    list_doctrine_cards,
    list_doctrine_card_versions,
    list_doctrine_generation_runs,
    list_documents,
    list_libraries,
    scan_documents,
    search_knowledge,
    update_doctrine_card,
    update_library,
)


class KnowledgeExtractionTests(unittest.TestCase):
    def test_scan_extract_markdown_and_preserve_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "格雷厄姆_2025-03-08.md"
            raw = (
                "---\n"
                "title: 格雷厄姆安全边际\n"
                "date: 2025-03-08\n"
                "---\n"
                "# 安全边际\n\n"
                "价格必须显著低于保守估值。\n\n"
                "## 偿债能力\n\n"
                "| 项目 | 要求 |\n"
                "| --- | --- |\n"
                "| 流动比率 | 保留余量 |\n"
            ).encode("utf-8")
            source.write_bytes(raw)
            (root / "ignore.csv").write_text("not supported", encoding="utf-8")
            ignored = root / "node_modules"
            ignored.mkdir()
            (ignored / "hidden.md").write_text("# hidden", encoding="utf-8")

            before = (source.read_bytes(), source.stat().st_mtime_ns)
            self.assertEqual(scan_documents(root), [source.resolve()])
            extraction = extract_document(source)
            chunks = chunk_blocks(extraction.blocks, max_chars=120, source_date=extraction.source_date)
            after = (source.read_bytes(), source.stat().st_mtime_ns)

            self.assertEqual(before, after)
            self.assertEqual(extraction.title, "格雷厄姆安全边际")
            self.assertEqual(extraction.source_date, "2025-03-08")
            self.assertEqual(extraction.blocks[0].locator, "md:l:000005")
            self.assertEqual(extraction.blocks[0].heading_path, ("安全边际",))
            table = next(block for block in extraction.blocks if block.block_type == "table")
            self.assertEqual(table.heading_path, ("安全边际", "偿债能力"))
            self.assertTrue(chunks)
            self.assertTrue(all(chunk.source_date == "2025-03-08" for chunk in chunks))

    def test_import_reports_legacy_word_documents_without_silently_omitting_them(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "资料"
            root.mkdir()
            (root / "现代资料.md").write_text("# 可导入", encoding="utf-8")
            legacy = root / "历史笔记.doc"
            legacy.write_bytes(b"legacy binary word placeholder")
            result = import_documents(Path(temp) / "knowledge.db", root)
            self.assertEqual(result["imported"], 1)
            self.assertEqual(result["legacy_word_documents"], 1)
            self.assertNotIn(legacy.resolve(), scan_documents(root))

    def test_extract_docx_headings_paragraphs_and_tables(self):
        try:
            from docx import Document
        except ImportError:
            self.skipTest("python-docx is not installed")
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "红利研究.docx"
            document = Document()
            document.core_properties.title = "红利投资研究"
            document.core_properties.created = datetime(2024, 6, 30)
            document.add_heading("现金流", level=1)
            document.add_paragraph("分红必须由自由现金流覆盖。")
            table = document.add_table(rows=2, cols=2)
            table.cell(0, 0).text = "指标"
            table.cell(0, 1).text = "要求"
            table.cell(1, 0).text = "派息率"
            table.cell(1, 1).text = "可持续"
            document.save(source)

            extraction = extract_document(source)
            self.assertEqual(extraction.title, "红利投资研究")
            self.assertEqual(extraction.source_date, "2024-06-30")
            self.assertEqual([item.block_type for item in extraction.blocks], ["heading", "paragraph", "table", "table"])
            self.assertEqual(extraction.blocks[1].locator, "docx:p:000002")
            self.assertEqual(extraction.blocks[2].locator, "docx:t:0001:r:0001")
            self.assertEqual(extraction.blocks[3].heading_path, ("现金流",))


class KnowledgeDatabaseTests(unittest.TestCase):
    def test_trusted_search_and_listing_exclude_unassigned_legacy_documents(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            unassigned = root / "unassigned"
            trusted = root / "trusted"
            unassigned.mkdir()
            trusted.mkdir()
            (unassigned / "system.txt").write_text("现金流系统帮助文件", encoding="utf-8")
            (trusted / "dividend.md").write_text("现金流覆盖分红才可持续", encoding="utf-8")
            database = root / "knowledge.db"
            import_documents(database, unassigned)
            library_id = create_library(database, "红利")
            import_documents(database, trusted, library_id)

            self.assertEqual(len(list_documents(database)), 2)
            trusted_docs = list_documents(database, trusted_only=True)
            self.assertEqual(len(trusted_docs), 1)
            self.assertEqual(trusted_docs[0]["file_name"], "dividend.md")
            hits = search_knowledge(database, "现金流", trusted_only=True)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["file_name"], "dividend.md")

    def test_analysis_search_excludes_documents_from_disabled_libraries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            enabled_source = root / "enabled"
            disabled_source = root / "disabled"
            enabled_source.mkdir()
            disabled_source.mkdir()
            (enabled_source / "enabled.md").write_text("现金流支持分红", encoding="utf-8")
            (disabled_source / "disabled.md").write_text("现金流允许激进分红", encoding="utf-8")
            database = root / "knowledge.db"
            enabled_library = create_library(database, "启用流派")
            disabled_library = create_library(database, "停用流派", enabled=False)
            import_documents(database, enabled_source, enabled_library)
            import_documents(database, disabled_source, disabled_library)

            ordinary_hits = search_knowledge(database, "现金流", trusted_only=True)
            self.assertEqual({item["file_name"] for item in ordinary_hits}, {"enabled.md", "disabled.md"})
            analysis_hits = search_knowledge(
                database,
                "现金流",
                trusted_only=True,
                enabled_libraries_only=True,
            )
            self.assertEqual([item["file_name"] for item in analysis_hits], ["enabled.md"])

            disabled_document = next(
                item for item in list_documents(database) if item["file_name"] == "disabled.md"
            )
            assign_document_to_library(database, disabled_document["id"], enabled_library)
            shared_hits = search_knowledge(
                database,
                "激进分红",
                trusted_only=True,
                enabled_libraries_only=True,
            )
            self.assertEqual(len(shared_hits), 1)
            self.assertEqual(shared_hits[0]["library_id"], enabled_library)
            self.assertEqual(shared_hits[0]["library_name"], "启用流派")

    def test_import_dedupe_search_status_and_versioning(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "sources"
            root.mkdir()
            first = root / "original.md"
            duplicate = root / "copy.txt"
            original_text = "# 盈利质量\n\n经营现金流必须能够覆盖净利润，关注应收账款异常增长。\n"
            first.write_text(original_text, encoding="utf-8")
            duplicate.write_text(original_text, encoding="utf-8")
            database = Path(temp) / "knowledge.db"
            init_database(database)
            library_id = create_library(database, "财务质量", "检查利润含金量")

            result = import_documents(database, root, library_id, chunk_size=300)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["discovered"], 2)
            self.assertEqual(result["imported"], 1)
            self.assertEqual(result["duplicates"], 1)
            self.assertEqual(len(list_documents(database)), 2)
            self.assertEqual(list_libraries(database)[0]["document_count"], 2)

            hits = search_knowledge(database, "应收账款", library_id=library_id)
            self.assertEqual(len(hits), 1)
            self.assertIn("经营现金流", hits[0]["content"])
            self.assertRegex(hits[0]["locator"], r"^(md|txt):l:")
            self.assertEqual(Path(hits[0]["source_path"]).read_text(encoding="utf-8"), original_text)
            self.assertEqual(hits[0]["source"]["locator"], hits[0]["locator"])

            second = import_documents(database, root, library_id)
            self.assertEqual(second["skipped"], 2)
            self.assertEqual(second["imported"], 0)
            status = get_import_status(database, second["run_id"])
            self.assertEqual(status["status"], "completed")
            self.assertEqual(len(status["items"]), 2)

            first.write_text(
                "# 盈利质量\n\n经营现金流必须覆盖净利润。存货增长不应长期超过收入增长。\n",
                encoding="utf-8",
            )
            third = import_documents(database, first, library_id)
            self.assertEqual(third["imported"], 1)
            versions = list_documents(database, include_history=True)
            first_versions = [row for row in versions if row["source_path"] == str(first.resolve())]
            self.assertEqual(sum(row["is_current"] for row in first_versions), 1)
            self.assertEqual(len(first_versions), 2)
            self.assertTrue(search_knowledge(database, "存货增长"))

    def test_like_fallback_when_fts_is_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            source_root = Path(temp) / "sources"
            source_root.mkdir()
            (source_root / "quality.txt").write_text("自由现金流覆盖分红，是红利可持续的基础。", encoding="utf-8")
            database = Path(temp) / "knowledge.db"
            import_documents(database, source_root)
            connection = sqlite3.connect(database)
            try:
                with connection:
                    connection.execute("UPDATE knowledge_meta SET value='0' WHERE key='fts5_enabled'")
            finally:
                connection.close()
            hits = search_knowledge(database, "自由现金流")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["match_mode"], "like")

    def test_library_and_doctrine_card_crud(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "knowledge.db"
            init_database(database)
            library_id = create_library(database, "红利", "原说明", "dividend")
            updated = update_library(database, library_id, description="现金流与分红", enabled=False)
            self.assertEqual(updated["description"], "现金流与分红")
            self.assertEqual(updated["enabled"], 0)

            card_id = create_doctrine_card(
                database,
                library_id,
                "分红覆盖",
                "分红应由正常化自由现金流覆盖",
                category="cash_flow",
                confidence="high",
                conditions=["剔除一次性处置收益"],
                exceptions=["金融企业需使用行业口径"],
                tags=["红利", "现金流"],
                source_locator="docx:p:000023",
            )
            card = get_doctrine_card(database, card_id)
            self.assertEqual(card["conditions"], ["剔除一次性处置收益"])
            self.assertEqual(card["tags"], ["红利", "现金流"])
            self.assertEqual(card["review_status"], "pending")
            self.assertEqual(card["enabled"], 0)
            with self.assertRaisesRegex(ValueError, "审核通过"):
                update_doctrine_card(database, card_id, {"enabled": True})
            approved_card = update_doctrine_card(
                database, card_id, {"review_status": "approved", "enabled": True}
            )
            self.assertEqual(approved_card["review_status"], "approved")
            self.assertEqual(list_doctrine_cards(database, library_id, enabled_only=True), [])
            update_library(database, library_id, enabled=True)
            self.assertEqual(len(list_doctrine_cards(database, library_id, enabled_only=True)), 1)
            updated_card = update_doctrine_card(database, card_id, {"confidence": "verified", "enabled": False})
            self.assertEqual(updated_card["confidence"], "verified")
            self.assertEqual(updated_card["enabled"], 0)
            self.assertEqual(len(list_doctrine_cards(database, library_id)), 1)
            self.assertEqual(list_doctrine_cards(database, library_id, enabled_only=True), [])
            self.assertTrue(delete_doctrine_card(database, card_id))
            self.assertTrue(delete_library(database, library_id))

    def test_assign_existing_document_to_multiple_libraries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "sources"
            root.mkdir()
            (root / "shared.md").write_text("# 安全边际\n\n保守估值与现金流分析。", encoding="utf-8")
            database = Path(temp) / "knowledge.db"
            import_documents(database, root)
            document_id = list_documents(database)[0]["id"]
            graham = create_library(database, "格雷厄姆")
            dividend = create_library(database, "红利")
            assign_document_to_library(database, document_id, graham)
            assign_document_to_library(database, document_id, dividend)
            self.assertTrue(search_knowledge(database, "保守估值", library_id=graham))
            self.assertTrue(search_knowledge(database, "保守估值", library_id=dividend))

    def test_generate_candidates_preserves_source_review_state_and_dedupes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "sources"
            root.mkdir()
            (root / "红利准则.md").write_text(
                "# 红利纪律\n\n股息率至少3%且自由现金流能够覆盖分红时，才应进入候选池。\n\n"
                "普通的背景介绍不会变成投资规则。\n",
                encoding="utf-8",
            )
            database = Path(temp) / "knowledge.db"
            library_id = create_library(database, "红利")
            import_documents(database, root, library_id)

            first = generate_doctrine_candidates(database, library_id)
            self.assertEqual(first["status"], "completed")
            self.assertGreaterEqual(first["candidates_created"], 1)
            card = list_doctrine_cards(database, library_id)[0]
            self.assertEqual(card["review_status"], "pending")
            self.assertEqual(card["enabled"], 0)
            self.assertEqual(card["extraction_method"], "deterministic-rule-v1")
            self.assertIsNotNone(card["source_document_id"])
            self.assertIsNotNone(card["source_chunk_id"])
            self.assertRegex(card["source_locator"], r"^md:l:")
            self.assertIn("dividend_yield", card["metrics"])
            self.assertIn("free_cash_flow", card["metrics"])
            self.assertIn("至少3%", card["thresholds"])

            second = generate_doctrine_candidates(database, library_id)
            self.assertEqual(second["candidates_created"], 0)
            self.assertGreaterEqual(second["duplicates"], 1)
            self.assertEqual(len(list_doctrine_cards(database, library_id)), first["candidates_created"])
            self.assertEqual(len(list_doctrine_generation_runs(database, library_id)), 2)

    def test_doctrine_versions_and_cross_library_relationships(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "knowledge.db"
            dividend = create_library(database, "红利")
            conservative = create_library(database, "保守派")
            pending_library = create_library(database, "未审核体系")
            first_id = create_doctrine_card(
                database,
                dividend,
                "最低股息率",
                "股息率至少4%才优先研究",
                metrics=["dividend_yield"],
                thresholds=["至少4%"],
            )
            second_id = create_doctrine_card(
                database,
                conservative,
                "警惕高股息陷阱",
                "周期顶部的股息率不超过3%才考虑",
                metrics=["dividend_yield"],
                thresholds=["不超过3%"],
            )
            create_doctrine_card(
                database,
                pending_library,
                "待审核规则",
                "股息率至少8%",
                metrics=["dividend_yield"],
                thresholds=["至少8%"],
            )
            update_doctrine_card(database, first_id, review_status="approved", enabled=True)
            update_doctrine_card(database, first_id, applicability="成熟期非金融企业")
            update_doctrine_card(database, second_id, review_status="approved", enabled=True)

            versions = list_doctrine_card_versions(database, first_id)
            self.assertEqual([item["version_no"] for item in versions], [3, 2, 1])
            self.assertEqual(versions[0]["snapshot"]["applicability"], "成熟期非金融企业")
            relationships = analyze_doctrine_relationships(database)
            self.assertEqual(relationships["summary"]["enabled_rules"], 2)
            self.assertEqual(relationships["summary"]["conflicts"], 1)
            conflict = relationships["relationships"][0]
            self.assertEqual(conflict["kind"], "conflict")
            self.assertEqual(conflict["review_status"], "heuristic_lead")
            self.assertEqual(set((conflict["left"]["library_name"], conflict["right"]["library_name"])), {"红利", "保守派"})


if __name__ == "__main__":
    unittest.main()
