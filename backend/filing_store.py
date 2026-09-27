"""Local, immutable archive for A-share filing evidence.

The archive separates three layers deliberately:

* filing metadata (what was disclosed, when, and by whom),
* immutable PDF artifacts identified by SHA-256,
* deterministic evidence signals with an exact PDF page locator.

This module does not decide whether a company is investable.  Keyword signals
are review leads, never proof of misconduct, and missing signals never mean the
underlying risk is absent.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .knowledge import get_connection


MAX_FILING_BYTES = 80 * 1024 * 1024
OFFICIAL_FILING_HOSTS = frozenset(
    {
        "static.cninfo.com.cn",
        "www.cninfo.com.cn",
        "dataclouds.cninfo.com.cn",
        "disc.static.szse.cn",
        "www.szse.cn",
        "static.sse.com.cn",
        "www.sse.com.cn",
        "www.bse.cn",
    }
)


@dataclass(frozen=True)
class EvidencePattern:
    category: str
    label: str
    pattern: re.Pattern[str]
    default_severity: str = "review"


EVIDENCE_PATTERNS = (
    EvidencePattern("audit_opinion", "审计意见", re.compile(r"非标准(?:无保留)?(?:审计)?意见|标准无保留意见|无保留意见|保留意见|否定意见|无法表示意见|强调事项段|持续经营[^。；]{0,80}重大不确定性")),
    EvidencePattern("key_audit_matter", "关键审计事项", re.compile(r"关键审计事项")),
    EvidencePattern("receivables_aging", "应收账款与账龄", re.compile(r"应收账款|应收票据|应收款项|账龄|坏账准备|预期信用损失|逾期应收")),
    EvidencePattern("inventory_impairment", "存货与减值", re.compile(r"存货(?:及|和|与)(?:存货)?跌价准备|存货跌价准备|存货减值|可变现净值|存货(?:构成|分类|账面价值|余额)|库龄")),
    EvidencePattern("goodwill", "商誉与减值", re.compile(r"商誉减值|商誉")),
    EvidencePattern("related_party", "关联方与关联交易", re.compile(r"关联交易|关联方|资金占用")),
    EvidencePattern("guarantee", "担保与承诺", re.compile(r"对外担保|担保余额|或有事项|承诺事项")),
    EvidencePattern("restricted_cash", "受限资金", re.compile(r"受限资金|受限制的货币资金|使用受限的货币资金|货币资金[^。；]{0,40}(?:受限|冻结)|冻结资金|保证金存款|所有权或使用权受到限制")),
    EvidencePattern("non_recurring", "非经常性损益", re.compile(r"非经常性损益|政府补助|资产处置收益")),
    EvidencePattern("capitalization", "资本化政策", re.compile(r"研发支出资本化|开发支出|开发阶段(?:的)?支出|资本化条件|资本化率|利息资本化|研究阶段[^。；]{0,40}费用化")),
    EvidencePattern("accounting_change", "会计政策、估计或差错更正", re.compile(r"会计政策变更|会计估计变更|前期差错更正|追溯调整|首次执行[^。；]{0,40}(?:会计准则|准则解释)")),
    EvidencePattern("regulatory_inquiry", "监管问询与回复", re.compile(r"问询函|监管工作函|纪律处分|监管措施")),
)


def init_filing_store(db: str | os.PathLike[str] | sqlite3.Connection) -> None:
    with _using_connection(db) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS filing_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sha256 TEXT NOT NULL UNIQUE,
                relative_path TEXT NOT NULL,
                mime_type TEXT NOT NULL DEFAULT 'application/pdf',
                byte_size INTEGER NOT NULL,
                page_count INTEGER,
                extracted_text_path TEXT NOT NULL DEFAULT '',
                extraction_status TEXT NOT NULL DEFAULT 'pending',
                extraction_quality REAL NOT NULL DEFAULT 0,
                extraction_note TEXT NOT NULL DEFAULT '',
                ocr_pages_done INTEGER NOT NULL DEFAULT 0,
                ocr_total_pages INTEGER NOT NULL DEFAULT 0,
                ocr_status TEXT NOT NULL DEFAULT 'not_started',
                text_chars INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS filings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL UNIQUE,
                symbol TEXT NOT NULL,
                company_name TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                filing_type TEXT NOT NULL DEFAULT 'other',
                report_period TEXT NOT NULL DEFAULT '',
                disclosed_at TEXT NOT NULL DEFAULT '',
                source_provider TEXT NOT NULL,
                source_notice_id TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                artifact_id INTEGER NOT NULL REFERENCES filing_artifacts(id),
                is_correction INTEGER NOT NULL DEFAULT 0 CHECK(is_correction IN (0, 1)),
                supersedes_filing_id INTEGER REFERENCES filings(id),
                metadata_json TEXT NOT NULL DEFAULT '{}',
                retrieved_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS filing_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filing_id INTEGER NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
                category TEXT NOT NULL,
                severity TEXT NOT NULL CHECK(severity IN ('info', 'review', 'red')),
                title TEXT NOT NULL,
                excerpt TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                locator TEXT NOT NULL,
                extraction_method TEXT NOT NULL DEFAULT 'deterministic_keyword_v1',
                confidence TEXT NOT NULL DEFAULT 'keyword_match',
                detected_values_json TEXT NOT NULL DEFAULT '[]',
                review_status TEXT NOT NULL DEFAULT 'pending' CHECK(review_status IN ('pending', 'confirmed', 'dismissed')),
                reviewer_note TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(filing_id, category, page_number, excerpt)
            );

            CREATE TABLE IF NOT EXISTS filing_ocr_pages (
                artifact_id INTEGER NOT NULL REFERENCES filing_artifacts(id) ON DELETE CASCADE,
                page_number INTEGER NOT NULL,
                text TEXT NOT NULL,
                text_chars INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(artifact_id, page_number)
            );

            CREATE TABLE IF NOT EXISTS filing_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filing_id INTEGER NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
                metric TEXT NOT NULL,
                label TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'group',
                value REAL NOT NULL,
                unit TEXT NOT NULL DEFAULT 'ratio',
                raw_value TEXT NOT NULL,
                period_end TEXT NOT NULL DEFAULT '',
                page_number INTEGER NOT NULL,
                locator TEXT NOT NULL,
                excerpt TEXT NOT NULL,
                extraction_method TEXT NOT NULL,
                confidence TEXT NOT NULL DEFAULT 'exact_label',
                review_status TEXT NOT NULL DEFAULT 'pending' CHECK(review_status IN ('pending', 'confirmed', 'dismissed')),
                reviewer_note TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(filing_id, metric, scope, period_end, page_number, raw_value)
            );

            CREATE TABLE IF NOT EXISTS filing_sync_state (
                symbol TEXT NOT NULL,
                source_provider TEXT NOT NULL DEFAULT 'cninfo_public',
                cursor_date TEXT NOT NULL DEFAULT '',
                last_disclosed_at TEXT NOT NULL DEFAULT '',
                last_run_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'never' CHECK(status IN ('never', 'running', 'success', 'partial', 'error')),
                last_error TEXT NOT NULL DEFAULT '',
                last_discovered INTEGER NOT NULL DEFAULT 0,
                last_archived INTEGER NOT NULL DEFAULT 0,
                last_duplicates INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(symbol, source_provider)
            );

            CREATE TABLE IF NOT EXISTS filing_sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                source_provider TEXT NOT NULL DEFAULT 'cninfo_public',
                since_date TEXT NOT NULL,
                until_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'success', 'partial', 'error')),
                discovered INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                duplicates INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                error_json TEXT NOT NULL DEFAULT '[]',
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_filings_symbol_disclosed
                ON filings(symbol, disclosed_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_filings_type_period
                ON filings(filing_type, report_period);
            CREATE INDEX IF NOT EXISTS idx_filing_evidence_filing_category
                ON filing_evidence(filing_id, category, severity);
            CREATE INDEX IF NOT EXISTS idx_filing_sync_runs_symbol_started
                ON filing_sync_runs(symbol, started_at DESC);
            """
        )
        evidence_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(filing_evidence)")
        }
        artifact_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(filing_artifacts)")
        }
        if "extraction_quality" not in artifact_columns:
            connection.execute(
                "ALTER TABLE filing_artifacts ADD COLUMN extraction_quality REAL NOT NULL DEFAULT 0"
            )
        if "extraction_note" not in artifact_columns:
            connection.execute(
                "ALTER TABLE filing_artifacts ADD COLUMN extraction_note TEXT NOT NULL DEFAULT ''"
            )
        if "ocr_pages_done" not in artifact_columns:
            connection.execute(
                "ALTER TABLE filing_artifacts ADD COLUMN ocr_pages_done INTEGER NOT NULL DEFAULT 0"
            )
        if "ocr_total_pages" not in artifact_columns:
            connection.execute(
                "ALTER TABLE filing_artifacts ADD COLUMN ocr_total_pages INTEGER NOT NULL DEFAULT 0"
            )
        if "ocr_status" not in artifact_columns:
            connection.execute(
                "ALTER TABLE filing_artifacts ADD COLUMN ocr_status TEXT NOT NULL DEFAULT 'not_started'"
            )
        if "detected_values_json" not in evidence_columns:
            connection.execute(
                "ALTER TABLE filing_evidence ADD COLUMN detected_values_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "review_status" not in evidence_columns:
            connection.execute(
                "ALTER TABLE filing_evidence ADD COLUMN review_status TEXT NOT NULL DEFAULT 'pending' "
                "CHECK(review_status IN ('pending', 'confirmed', 'dismissed'))"
            )
        if "reviewer_note" not in evidence_columns:
            connection.execute(
                "ALTER TABLE filing_evidence ADD COLUMN reviewer_note TEXT NOT NULL DEFAULT ''"
            )
        if "reviewed_at" not in evidence_columns:
            connection.execute("ALTER TABLE filing_evidence ADD COLUMN reviewed_at TEXT")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_filing_evidence_review "
            "ON filing_evidence(review_status, severity, filing_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_filing_facts_review "
            "ON filing_facts(filing_id, review_status, metric)"
        )
        unresolved_symbols = connection.execute(
            "SELECT DISTINCT symbol FROM filings WHERE is_correction=1 AND supersedes_filing_id IS NULL"
        ).fetchall()
        for row in unresolved_symbols:
            _backfill_filing_supersessions(connection, row["symbol"])
        connection.execute("PRAGMA optimize")


def archive_filing(
    db: str | os.PathLike[str] | sqlite3.Connection,
    artifact_root: str | os.PathLike[str],
    *,
    symbol: str,
    title: str,
    source_provider: str,
    source_url: str = "",
    local_path: str | os.PathLike[str] | None = None,
    allowed_local_root: str | os.PathLike[str] | None = None,
    source_notice_id: str = "",
    company_name: str = "",
    filing_type: str = "other",
    report_period: str = "",
    disclosed_at: str = "",
    is_correction: bool = False,
    supersedes_filing_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    timeout: float = 25.0,
) -> dict[str, Any]:
    """Archive a PDF from an official URL or an explicitly allowed local path."""

    normalized_symbol = _normalize_symbol(symbol)
    clean_title = _clean_required(title, "公告标题")
    provider = _clean_required(source_provider, "来源")
    resolved_supersedes_id, supersession_link_method = _resolve_supersedes_filing_id(
        db,
        symbol=normalized_symbol,
        title=clean_title,
        filing_type=filing_type.strip() or "other",
        report_period=report_period.strip(),
        disclosed_at=disclosed_at.strip(),
        is_correction=bool(is_correction),
        requested_id=supersedes_filing_id,
    )
    if bool(source_url) == bool(local_path):
        raise ValueError("官方链接和本地文件必须且只能提供一个")
    if source_url:
        normalized_url = validate_official_filing_url(source_url)
        raw, final_url, content_type = _download_pdf(normalized_url, timeout=timeout)
        normalized_url = final_url
        origin = normalized_url
    else:
        path = Path(local_path or "").expanduser().resolve()
        if allowed_local_root is None:
            raise ValueError("导入本地财报时必须指定允许的资料目录")
        root = Path(allowed_local_root).expanduser().resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"本地财报必须位于已配置的知识根目录内：{root}")
        if path.suffix.lower() != ".pdf" or not path.is_file():
            raise ValueError("请选择知识根目录内存在的 PDF 文件")
        if path.stat().st_size > MAX_FILING_BYTES:
            raise ValueError(f"单份财报不能超过 {MAX_FILING_BYTES // 1024 // 1024} MB")
        raw = path.read_bytes()
        normalized_url = ""
        content_type = "application/pdf"
        origin = str(path)
    if not raw.startswith(b"%PDF"):
        raise ValueError("文件不是有效的 PDF 公告原件")

    sha256 = hashlib.sha256(raw).hexdigest()
    artifact_base = Path(artifact_root).resolve()
    artifact_dir = artifact_base / normalized_symbol
    artifact_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = artifact_dir / f"{sha256}.pdf"
    if not pdf_path.exists():
        _atomic_write(pdf_path, raw)
    pages, extraction_status, extraction_quality, extraction_note = _extract_pdf_pages(raw)
    text_path = artifact_dir / f"{sha256}.txt"
    if pages and not text_path.exists():
        page_text = "\n\n".join(f"--- PAGE {number} ---\n{text}" for number, text in pages)
        _atomic_write(text_path, page_text.encode("utf-8"))
    retrieved_at = datetime.now(timezone.utc).isoformat()
    notice_component = source_notice_id.strip() or normalized_url or f"{sha256}|{normalized_symbol}|{clean_title}"
    source_key = hashlib.sha256(f"{provider}|{notice_component}".encode("utf-8")).hexdigest()
    artifact_relative = pdf_path.relative_to(artifact_base).as_posix()
    text_relative = text_path.relative_to(artifact_base).as_posix() if pages else ""
    with _using_connection(db) as connection:
        existing = connection.execute(
            "SELECT id FROM filings WHERE source_key=?", (source_key,)
        ).fetchone()
        if existing:
            result = get_filing(connection, int(existing["id"]))
            result["duplicate"] = True
            return result
        connection.execute(
            """INSERT INTO filing_artifacts(
                   sha256, relative_path, mime_type, byte_size, page_count,
                   extracted_text_path, extraction_status, extraction_quality,
                   extraction_note, text_chars)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(sha256) DO UPDATE SET
                   extraction_status=excluded.extraction_status,
                   extraction_quality=excluded.extraction_quality,
                   extraction_note=excluded.extraction_note,
                   page_count=COALESCE(filing_artifacts.page_count, excluded.page_count),
                   extracted_text_path=CASE WHEN filing_artifacts.extracted_text_path=''
                                            THEN excluded.extracted_text_path ELSE filing_artifacts.extracted_text_path END,
                   text_chars=MAX(filing_artifacts.text_chars, excluded.text_chars)""",
            (
                sha256,
                artifact_relative,
                content_type or "application/pdf",
                len(raw),
                len(pages) if pages else None,
                text_relative,
                extraction_status,
                extraction_quality,
                extraction_note,
                sum(len(text) for _, text in pages),
            ),
        )
        artifact_id = int(
            connection.execute("SELECT id FROM filing_artifacts WHERE sha256=?", (sha256,)).fetchone()[0]
        )
        cursor = connection.execute(
            """INSERT INTO filings(
                   source_key, symbol, company_name, title, filing_type, report_period,
                   disclosed_at, source_provider, source_notice_id, source_url,
                   artifact_id, is_correction, supersedes_filing_id, metadata_json, retrieved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source_key,
                normalized_symbol,
                company_name.strip(),
                clean_title,
                filing_type.strip() or "other",
                report_period.strip(),
                disclosed_at.strip(),
                provider,
                source_notice_id.strip(),
                normalized_url,
                artifact_id,
                int(bool(is_correction)),
                resolved_supersedes_id,
                json.dumps(
                    {
                        "origin": origin,
                        **(metadata or {}),
                        "supersession_link_method": supersession_link_method,
                    },
                    ensure_ascii=False,
                ),
                retrieved_at,
            ),
        )
        filing_id = int(cursor.lastrowid)
        for evidence in extract_evidence_signals(pages) if extraction_status == "ready" else []:
            connection.execute(
                """INSERT OR IGNORE INTO filing_evidence(
                       filing_id, category, severity, title, excerpt, page_number, locator,
                       detected_values_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    filing_id,
                    evidence["category"],
                    evidence["severity"],
                    evidence["title"],
                    evidence["excerpt"],
                    evidence["page_number"],
                    evidence["locator"],
                    json.dumps(evidence.get("detected_values") or [], ensure_ascii=False),
                ),
            )
        _replace_filing_facts(
            connection,
            filing_id,
            pages if extraction_status == "ready" else [],
            period_end=report_period.strip() or _infer_period_from_title(clean_title),
            extraction_method="deterministic_bank_fact_v1",
        )
        _backfill_filing_supersessions(connection, normalized_symbol)
        result = get_filing(connection, filing_id)
        result["duplicate"] = False
        return result


def _resolve_supersedes_filing_id(
    db: str | os.PathLike[str] | sqlite3.Connection,
    *,
    symbol: str,
    title: str,
    filing_type: str,
    report_period: str,
    disclosed_at: str,
    is_correction: bool,
    requested_id: int | None,
) -> tuple[int | None, str]:
    """Resolve correction lineage conservatively; ambiguity is never guessed away."""

    with _using_connection(db) as connection:
        if requested_id is not None:
            row = connection.execute(
                "SELECT id, symbol FROM filings WHERE id=?", (int(requested_id),)
            ).fetchone()
            if row is None:
                raise ValueError("被更正的原公告不存在")
            if row["symbol"] != symbol:
                raise ValueError("更正公告只能关联同一公司的原公告")
            return int(row["id"]), "explicit"
        if not is_correction or not report_period:
            return None, "not_applicable" if not is_correction else "unresolved_missing_period"

        if filing_type in {"annual_report", "interim_report", "quarterly_report", "audit_report"}:
            target_types = (filing_type,)
        elif "半年度" in title:
            target_types = ("interim_report",)
        elif "季度" in title:
            target_types = ("quarterly_report",)
        elif "审计" in title:
            target_types = ("audit_report",)
        elif "年度" in title:
            target_types = ("annual_report",)
        else:
            return None, "unresolved_ambiguous_type"

        placeholders = ",".join("?" for _ in target_types)
        params: list[Any] = [symbol, report_period, *target_types]
        disclosed_filter = ""
        if disclosed_at:
            disclosed_filter = " AND (disclosed_at='' OR disclosed_at<?)"
            params.append(disclosed_at)
        rows = connection.execute(
            f"""SELECT id FROM filings
                 WHERE symbol=? AND report_period=? AND filing_type IN ({placeholders})
                   AND is_correction=0{disclosed_filter}
                 ORDER BY disclosed_at DESC, id DESC LIMIT 2""",
            params,
        ).fetchall()
        if len(rows) == 1:
            return int(rows[0]["id"]), "exact_symbol_period_type"
        return None, "unresolved_no_candidate" if not rows else "unresolved_multiple_candidates"


def _backfill_filing_supersessions(
    db: str | os.PathLike[str] | sqlite3.Connection, symbol: str
) -> int:
    """Link older unresolved corrections after their original arrives in a later batch."""

    linked = 0
    with _using_connection(db) as connection:
        rows = connection.execute(
            """SELECT id, symbol, title, filing_type, report_period, disclosed_at, metadata_json
                 FROM filings
                 WHERE symbol=? AND is_correction=1 AND supersedes_filing_id IS NULL""",
            (symbol,),
        ).fetchall()
        for row in rows:
            target_id, method = _resolve_supersedes_filing_id(
                connection,
                symbol=row["symbol"],
                title=row["title"],
                filing_type=row["filing_type"],
                report_period=row["report_period"],
                disclosed_at=row["disclosed_at"],
                is_correction=True,
                requested_id=None,
            )
            if target_id is None:
                continue
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                metadata = {}
            metadata["supersession_link_method"] = method
            connection.execute(
                "UPDATE filings SET supersedes_filing_id=?, metadata_json=? WHERE id=?",
                (target_id, json.dumps(metadata, ensure_ascii=False), int(row["id"])),
            )
            linked += 1
    return linked


def reindex_filing(
    db: str | os.PathLike[str] | sqlite3.Connection,
    artifact_root: str | os.PathLike[str],
    filing_id: int,
) -> dict[str, Any]:
    """Re-extract one immutable artifact and rebuild only its derived evidence."""

    filing = get_filing(db, filing_id)
    root = Path(artifact_root).resolve()
    pdf_path = (root / str(filing["relative_path"])).resolve()
    if root not in pdf_path.parents or not pdf_path.is_file():
        raise ValueError("公告归档文件不存在或路径越界")
    raw = pdf_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != str(filing["sha256"]):
        raise RuntimeError("公告原件哈希校验失败，已停止重新抽取")
    pages, status, quality, note = _extract_pdf_pages(raw)
    text_path = pdf_path.with_suffix(".txt")
    if pages:
        page_text = "\n\n".join(f"--- PAGE {number} ---\n{text}" for number, text in pages)
        _atomic_write(text_path, page_text.encode("utf-8"))
    signals = extract_evidence_signals(pages) if status == "ready" else []
    with _using_connection(db) as connection:
        connection.execute(
            """UPDATE filing_artifacts
               SET page_count=?, extracted_text_path=?, extraction_status=?,
                   extraction_quality=?, extraction_note=?, text_chars=?
               WHERE id=?""",
            (
                len(pages) if pages else None,
                text_path.relative_to(root).as_posix() if pages else "",
                status,
                quality,
                note,
                sum(len(text) for _, text in pages),
                int(filing["artifact_id"]),
            ),
        )
        connection.execute("DELETE FROM filing_evidence WHERE filing_id=? AND review_status='pending'", (int(filing_id),))
        for evidence in signals:
            connection.execute(
                """INSERT INTO filing_evidence(
                       filing_id, category, severity, title, excerpt, page_number, locator,
                       detected_values_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(filing_id, category, page_number, excerpt) DO UPDATE SET
                       severity=excluded.severity, title=excluded.title,
                       detected_values_json=excluded.detected_values_json""",
                (
                    int(filing_id), evidence["category"], evidence["severity"], evidence["title"],
                    evidence["excerpt"], evidence["page_number"], evidence["locator"],
                    json.dumps(evidence.get("detected_values") or [], ensure_ascii=False),
                ),
            )
        _replace_filing_facts(
            connection,
            int(filing_id),
            pages if status == "ready" else [],
            period_end=str(filing.get("report_period") or "") or _infer_period_from_title(str(filing.get("title") or "")),
            extraction_method="deterministic_bank_fact_v1",
        )
    result = get_filing(db, filing_id)
    result["reindexed"] = True
    return result


def ocr_filing_batch(
    db: str | os.PathLike[str] | sqlite3.Connection,
    artifact_root: str | os.PathLike[str],
    filing_id: int,
    *,
    batch_size: int = 8,
) -> dict[str, Any]:
    """OCR the next bounded page batch and persist page-level progress."""

    size = max(1, min(int(batch_size), 12))
    filing = get_filing(db, filing_id)
    root = Path(artifact_root).resolve()
    pdf_path = (root / str(filing["relative_path"])).resolve()
    if root not in pdf_path.parents or not pdf_path.is_file():
        raise ValueError("公告归档文件不存在或路径越界")
    raw = pdf_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != str(filing["sha256"]):
        raise RuntimeError("公告原件哈希校验失败，已停止 OCR")
    with _using_connection(db) as connection:
        done_rows = connection.execute(
            "SELECT page_number FROM filing_ocr_pages WHERE artifact_id=? ORDER BY page_number",
            (int(filing["artifact_id"]),),
        ).fetchall()
    done = {int(row["page_number"]) for row in done_rows}
    total = int(filing.get("page_count") or 0)
    if total <= 0:
        try:
            from pypdf import PdfReader  # type: ignore

            total = len(PdfReader(io.BytesIO(raw)).pages)
        except Exception as exc:
            raise RuntimeError("无法确定 PDF 页数，不能开始 OCR") from exc
    pending = [page for page in range(1, total + 1) if page not in done][:size]
    extracted = _run_ocr_pages(pdf_path, pending)

    with _using_connection(db) as connection:
        for page_number, text in extracted:
            connection.execute(
                """INSERT INTO filing_ocr_pages(artifact_id, page_number, text, text_chars)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(artifact_id, page_number) DO UPDATE SET
                       text=excluded.text, text_chars=excluded.text_chars,
                       created_at=CURRENT_TIMESTAMP""",
                (int(filing["artifact_id"]), page_number, text, len(text)),
            )
            connection.execute(
                "DELETE FROM filing_evidence WHERE filing_id=? AND page_number=? AND extraction_method='local_ocr_v1' AND review_status='pending'",
                (int(filing_id), page_number),
            )
            for evidence in extract_evidence_signals([(page_number, text)]):
                connection.execute(
                    """INSERT INTO filing_evidence(
                           filing_id, category, severity, title, excerpt, page_number, locator,
                           extraction_method, confidence, detected_values_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'local_ocr_v1', 'ocr_keyword_match', ?)
                       ON CONFLICT(filing_id, category, page_number, excerpt) DO UPDATE SET
                           severity=excluded.severity, title=excluded.title,
                           detected_values_json=excluded.detected_values_json""",
                    (
                        int(filing_id), evidence["category"], evidence["severity"], evidence["title"],
                        evidence["excerpt"], page_number, evidence["locator"],
                        json.dumps(evidence.get("detected_values") or [], ensure_ascii=False),
                    ),
                )
        _replace_filing_facts(
            connection,
            int(filing_id),
            extracted,
            period_end=str(filing.get("report_period") or "") or _infer_period_from_title(str(filing.get("title") or "")),
            extraction_method="local_ocr_v1",
            page_numbers={page_number for page_number, _ in extracted},
        )
        rows = connection.execute(
            "SELECT page_number, text FROM filing_ocr_pages WHERE artifact_id=? ORDER BY page_number",
            (int(filing["artifact_id"]),),
        ).fetchall()
        ocr_pages = [(int(row["page_number"]), str(row["text"] or "")) for row in rows]
        completed = len(ocr_pages)
        finished = completed >= total
        status = "ready_ocr" if finished else "ocr_partial"
        note = f"本地 OCR 已完成 {completed}/{total} 页" if not finished else "全部页面已完成本地 OCR"
        text_path = pdf_path.with_suffix(".ocr.txt")
        if ocr_pages:
            page_text = "\n\n".join(f"--- PAGE {number} ---\n{text}" for number, text in ocr_pages)
            _atomic_write(text_path, page_text.encode("utf-8"))
        quality = _extraction_quality(ocr_pages)
        connection.execute(
            """UPDATE filing_artifacts SET extracted_text_path=?, extraction_status=?,
                       extraction_quality=?, extraction_note=?, ocr_pages_done=?,
                       ocr_total_pages=?, ocr_status=?, text_chars=? WHERE id=?""",
            (
                text_path.relative_to(root).as_posix() if ocr_pages else "",
                status, quality, note, completed, total,
                "complete" if finished else "partial",
                sum(len(text) for _, text in ocr_pages), int(filing["artifact_id"]),
            ),
        )
    result = get_filing(db, filing_id)
    result["processed_pages"] = pending
    return result


def _run_ocr_pages(pdf_path: Path, page_numbers: list[int]) -> list[tuple[int, str]]:
    try:
        import numpy as np  # type: ignore
        import pypdfium2 as pdfium  # type: ignore
        from rapidocr import RapidOCR  # type: ignore
    except ImportError as exc:
        raise RuntimeError("本地 OCR 组件未安装，请先运行 setup-ocr.bat") from exc
    pdf = pdfium.PdfDocument(str(pdf_path))
    engine = RapidOCR()
    extracted: list[tuple[int, str]] = []
    try:
        for page_number in page_numbers:
            image = np.asarray(pdf[page_number - 1].render(scale=1.5).to_pil())
            result = engine(image)
            raw_texts = getattr(result, "txts", None) if result is not None else None
            texts = list(raw_texts) if raw_texts is not None else []
            extracted.append((page_number, "\n".join(str(text) for text in texts)))
    finally:
        pdf.close()
    return extracted


def validate_official_filing_url(value: str) -> str:
    url = value.strip()
    parsed = urllib.parse.urlsplit(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or hostname not in OFFICIAL_FILING_HOSTS:
        raise ValueError("公告链接必须使用巨潮、深交所、上交所或北交所的官方 HTTPS 域名")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("公告链接不能包含账户信息或非标准端口")
    if parsed.query or parsed.fragment:
        # Query strings can contain short-lived signatures or tracking data;
        # official PDF artifacts should have a stable canonical URL.
        raise ValueError("公告原件链接不能包含查询参数或片段")
    if not parsed.path.lower().endswith(".pdf"):
        raise ValueError("公告原件链接必须直接指向 PDF")
    return urllib.parse.urlunsplit(("https", hostname, parsed.path, "", ""))


def list_filings(
    db: str | os.PathLike[str] | sqlite3.Connection,
    *,
    symbol: str | None = None,
    filing_type: str | None = None,
    disclosed_on_or_before: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if symbol:
        filters.append("f.symbol=?")
        params.append(_normalize_symbol(symbol))
    if filing_type:
        filters.append("f.filing_type=?")
        params.append(filing_type.strip())
    if disclosed_on_or_before:
        try:
            disclosure_cutoff = datetime.strptime(disclosed_on_or_before.strip(), "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise ValueError("公告披露截止日必须是 YYYY-MM-DD") from exc
        # CNINFO/SSE/SZSE disclosure timestamps carry China-local calendar dates.
        # Compare that calendar prefix directly instead of converting it through
        # the host timezone. Unknown disclosure dates are not provably available.
        filters.append("f.disclosed_at!='' AND substr(f.disclosed_at, 1, 10)<=?")
        params.append(disclosure_cutoff)
    clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(max(1, min(int(limit), 500)))
    with _using_connection(db) as connection:
        rows = connection.execute(
            f"""SELECT f.*, a.sha256, a.relative_path, a.mime_type, a.byte_size,
                       a.page_count, a.extraction_status, a.extraction_quality,
                       a.extraction_note, a.ocr_pages_done, a.ocr_total_pages,
                       a.ocr_status, a.text_chars,
                       (SELECT p.title FROM filings p WHERE p.id=f.supersedes_filing_id) AS supersedes_title,
                       (SELECT p.disclosed_at FROM filings p WHERE p.id=f.supersedes_filing_id) AS supersedes_disclosed_at,
                       (SELECT c.id FROM filings c WHERE c.supersedes_filing_id=f.id
                            ORDER BY c.disclosed_at DESC, c.id DESC LIMIT 1) AS superseded_by_id,
                       (SELECT c.title FROM filings c WHERE c.supersedes_filing_id=f.id
                            ORDER BY c.disclosed_at DESC, c.id DESC LIMIT 1) AS superseded_by_title,
                       (SELECT c.disclosed_at FROM filings c WHERE c.supersedes_filing_id=f.id
                            ORDER BY c.disclosed_at DESC, c.id DESC LIMIT 1) AS superseded_by_disclosed_at,
                       (SELECT COUNT(*) FROM filing_evidence e WHERE e.filing_id=f.id) AS evidence_count,
                       (SELECT COUNT(*) FROM filing_evidence e WHERE e.filing_id=f.id AND e.review_status!='dismissed') AS active_evidence_count,
                       (SELECT COUNT(*) FROM filing_evidence e WHERE e.filing_id=f.id AND e.review_status='dismissed') AS dismissed_count,
                       (SELECT COUNT(*) FROM filing_evidence e WHERE e.filing_id=f.id AND e.severity='red' AND e.review_status!='dismissed') AS red_count,
                       (SELECT COUNT(*) FROM filing_evidence e WHERE e.filing_id=f.id AND e.severity='review' AND e.review_status!='dismissed') AS review_count
                FROM filings f JOIN filing_artifacts a ON a.id=f.artifact_id
                {clause}
                ORDER BY CASE WHEN f.disclosed_at='' THEN 1 ELSE 0 END,
                         f.disclosed_at DESC, f.id DESC LIMIT ?""",
            params,
        ).fetchall()
        results = [_filing_row(row) for row in rows]
        if disclosed_on_or_before:
            for item in results:
                superseding_date = str(item.get("superseded_by_disclosed_at") or "")[:10]
                if not superseding_date or superseding_date > disclosure_cutoff:
                    item["superseded_by_id"] = None
                    item["superseded_by_title"] = None
                    item["superseded_by_disclosed_at"] = None
        return results


def get_filing(
    db: str | os.PathLike[str] | sqlite3.Connection, filing_id: int
) -> dict[str, Any]:
    with _using_connection(db) as connection:
        row = connection.execute(
            """SELECT f.*, a.sha256, a.relative_path, a.mime_type, a.byte_size,
                      a.page_count, a.extraction_status, a.extraction_quality,
                      a.extraction_note, a.ocr_pages_done, a.ocr_total_pages,
                      a.ocr_status, a.text_chars,
                      (SELECT p.title FROM filings p WHERE p.id=f.supersedes_filing_id) AS supersedes_title,
                      (SELECT p.disclosed_at FROM filings p WHERE p.id=f.supersedes_filing_id) AS supersedes_disclosed_at,
                      (SELECT c.id FROM filings c WHERE c.supersedes_filing_id=f.id
                           ORDER BY c.disclosed_at DESC, c.id DESC LIMIT 1) AS superseded_by_id,
                      (SELECT c.title FROM filings c WHERE c.supersedes_filing_id=f.id
                           ORDER BY c.disclosed_at DESC, c.id DESC LIMIT 1) AS superseded_by_title,
                      (SELECT c.disclosed_at FROM filings c WHERE c.supersedes_filing_id=f.id
                           ORDER BY c.disclosed_at DESC, c.id DESC LIMIT 1) AS superseded_by_disclosed_at,
                      (SELECT COUNT(*) FROM filing_evidence e WHERE e.filing_id=f.id) AS evidence_count,
                      (SELECT COUNT(*) FROM filing_evidence e WHERE e.filing_id=f.id AND e.review_status!='dismissed') AS active_evidence_count,
                      (SELECT COUNT(*) FROM filing_evidence e WHERE e.filing_id=f.id AND e.review_status='dismissed') AS dismissed_count,
                      (SELECT COUNT(*) FROM filing_evidence e WHERE e.filing_id=f.id AND e.severity='red' AND e.review_status!='dismissed') AS red_count,
                      (SELECT COUNT(*) FROM filing_evidence e WHERE e.filing_id=f.id AND e.severity='review' AND e.review_status!='dismissed') AS review_count
               FROM filings f JOIN filing_artifacts a ON a.id=f.artifact_id WHERE f.id=?""",
            (int(filing_id),),
        ).fetchone()
        if row is None:
            raise KeyError(f"公告不存在：{filing_id}")
        return _filing_row(row)


def list_filing_evidence(
    db: str | os.PathLike[str] | sqlite3.Connection,
    filing_id: int,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    with _using_connection(db) as connection:
        rows = connection.execute(
                """SELECT * FROM filing_evidence WHERE filing_id=?
                   ORDER BY CASE review_status WHEN 'confirmed' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                            CASE severity WHEN 'red' THEN 0 WHEN 'review' THEN 1 ELSE 2 END,
                            page_number, id LIMIT ?""",
                (int(filing_id), max(1, min(int(limit), 1000))),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            try:
                value["detected_values"] = json.loads(value.pop("detected_values_json") or "[]")
            except (json.JSONDecodeError, TypeError):
                value["detected_values"] = []
            results.append(value)
        return results


BANK_FACT_LABELS = {
    "net_interest_margin": "净利息收益率",
    "nonperforming_loan_ratio": "不良贷款率",
    "special_mention_ratio": "关注贷款率",
    "overdue_loan_ratio": "逾期贷款率",
    "provision_coverage_ratio": "拨备覆盖率",
    "provision_loan_ratio": "贷款拨备率",
    "cost_income_ratio": "成本收入比",
    "core_tier1_capital_ratio": "核心一级资本充足率",
    "tier1_capital_ratio": "一级资本充足率",
    "capital_adequacy_ratio": "资本充足率",
}


def extract_bank_regulatory_facts(
    pages: Iterable[tuple[int, str]],
    *,
    period_end: str = "",
    extraction_method: str = "deterministic_bank_fact_v1",
) -> list[dict[str, Any]]:
    """Extract exact-label bank ratios as review candidates, never conclusions."""

    candidates: dict[str, list[tuple[int, int, re.Match[str], str]]] = {
        metric: [] for metric in BANK_FACT_LABELS
    }
    for page_number, raw_text in pages:
        text = re.sub(r"\s+", " ", raw_text).strip()
        if not text:
            continue
        for metric, label in BANK_FACT_LABELS.items():
            label_pattern = re.escape(label)
            if metric == "tier1_capital_ratio":
                label_pattern = r"(?<!核心)一级资本充足率"
            elif metric == "capital_adequacy_ratio":
                label_pattern = r"(?<!一级)资本充足率"
            for match in re.finditer(rf"{label_pattern}\s*(\d{{1,3}}(?:\.\d+)?)\s*[%％]", text):
                prefix = text[max(0, match.start() - 260):match.start()]
                suffix = text[match.end():min(len(text), match.end() + 100)]
                score = 0
                if "本集团" in prefix:
                    score += 5
                if metric.endswith("capital_ratio") and "高级法" in prefix:
                    score += 4
                if any(term in prefix[-100:] for term in ("房地产", "零售", "信用卡", "小微", "公司贷款")):
                    score -= 6
                if any(term in suffix[:60] for term in ("应分别不低于", "监管要求", "触发")):
                    score -= 5
                candidates[metric].append((score, page_number, match, text))
    facts: list[dict[str, Any]] = []
    for metric, rows in candidates.items():
        if not rows:
            continue
        score, page_number, match, text = sorted(rows, key=lambda row: (-row[0], row[1], row[2].start()))[0]
        raw_value = match.group(1) + "%"
        start = max(0, match.start() - 180)
        end = min(len(text), match.end() + 220)
        facts.append(
            {
                "metric": metric,
                "label": BANK_FACT_LABELS[metric],
                "scope": "group",
                "value": float(match.group(1)) / 100.0,
                "unit": "ratio",
                "raw_value": raw_value,
                "period_end": period_end,
                "page_number": page_number,
                "locator": f"pdf:p:{page_number:06d}",
                "excerpt": text[start:end].strip(),
                "extraction_method": extraction_method,
                "confidence": "exact_label_group_context" if score >= 5 else "exact_label_needs_scope_review",
            }
        )
    return facts


def list_filing_facts(
    db: str | os.PathLike[str] | sqlite3.Connection,
    filing_id: int,
) -> list[dict[str, Any]]:
    with _using_connection(db) as connection:
        rows = connection.execute(
            """SELECT * FROM filing_facts WHERE filing_id=?
               ORDER BY CASE review_status WHEN 'confirmed' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                        page_number, metric""",
            (int(filing_id),),
        ).fetchall()
        return [dict(row) for row in rows]


def update_filing_fact_review(
    db: str | os.PathLike[str] | sqlite3.Connection,
    fact_id: int,
    *,
    review_status: str,
    reviewer_note: str = "",
) -> dict[str, Any]:
    status = review_status.strip().lower()
    if status not in {"pending", "confirmed", "dismissed"}:
        raise ValueError("财务事实审核状态只能是 pending、confirmed 或 dismissed")
    reviewed_at = None if status == "pending" else datetime.now(timezone.utc).isoformat()
    with _using_connection(db) as connection:
        connection.execute(
            """UPDATE filing_facts SET review_status=?, reviewer_note=?, reviewed_at=? WHERE id=?""",
            (status, reviewer_note.strip(), reviewed_at, int(fact_id)),
        )
        row = connection.execute("SELECT * FROM filing_facts WHERE id=?", (int(fact_id),)).fetchone()
        if row is None:
            raise KeyError(f"财务事实不存在：{fact_id}")
        return dict(row)


def _replace_filing_facts(
    connection: sqlite3.Connection,
    filing_id: int,
    pages: Iterable[tuple[int, str]],
    *,
    period_end: str,
    extraction_method: str,
    page_numbers: set[int] | None = None,
) -> None:
    if page_numbers is None:
        connection.execute("DELETE FROM filing_facts WHERE filing_id=?", (int(filing_id),))
    elif page_numbers:
        placeholders = ",".join("?" for _ in page_numbers)
        connection.execute(
            f"DELETE FROM filing_facts WHERE filing_id=? AND extraction_method=? AND page_number IN ({placeholders})",
            (int(filing_id), extraction_method, *sorted(page_numbers)),
        )
    for fact in extract_bank_regulatory_facts(
        pages, period_end=period_end, extraction_method=extraction_method
    ):
        connection.execute(
            """INSERT OR IGNORE INTO filing_facts(
                   filing_id, metric, label, scope, value, unit, raw_value, period_end,
                   page_number, locator, excerpt, extraction_method, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(filing_id), fact["metric"], fact["label"], fact["scope"], fact["value"],
                fact["unit"], fact["raw_value"], fact["period_end"], fact["page_number"],
                fact["locator"], fact["excerpt"], fact["extraction_method"], fact["confidence"],
            ),
        )


def filing_evidence_coverage(
    db: str | os.PathLike[str] | sqlite3.Connection,
    filing_id: int,
) -> dict[str, Any]:
    """Return an explicit review matrix for every deterministic evidence category.

    A zero keyword count is deliberately labelled as ``not_found_in_extracted_text``;
    it is never treated as evidence that the underlying accounting risk is absent.
    """

    filing = get_filing(db, filing_id)
    extraction_status = str(filing.get("extraction_status") or "unknown")
    searchable = extraction_status in {"ready", "ready_ocr"}
    partially_searchable = extraction_status == "ocr_partial"
    evidence = list_filing_evidence(db, filing_id, limit=1000)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        grouped.setdefault(str(item.get("category") or ""), []).append(item)

    categories: list[dict[str, Any]] = []
    for spec in EVIDENCE_PATTERNS:
        items = grouped.get(spec.category, [])
        counts = {
            status: sum(1 for item in items if item.get("review_status") == status)
            for status in ("pending", "confirmed", "dismissed")
        }
        if not searchable and not partially_searchable:
            status = "not_searchable"
        elif counts["confirmed"]:
            status = "found_confirmed"
        elif counts["pending"]:
            status = "found_pending"
        elif items and counts["dismissed"] == len(items):
            status = "reviewed_no_issue"
        elif searchable:
            status = "not_found_in_extracted_text"
        else:
            status = "not_searchable"
        categories.append(
            {
                "category": spec.category,
                "label": spec.label,
                "status": status,
                "signal_count": len(items),
                **counts,
            }
        )
    return {
        "filing_id": int(filing_id),
        "extraction_status": extraction_status,
        "extraction_quality": filing.get("extraction_quality") or 0,
        "extraction_note": filing.get("extraction_note") or "",
        "searchable": searchable,
        "partially_searchable": partially_searchable,
        "ocr_pages_done": int(filing.get("ocr_pages_done") or 0),
        "ocr_total_pages": int(filing.get("ocr_total_pages") or filing.get("page_count") or 0),
        "categories": categories,
        "disclaimer": (
            "未在已提取文本中命中关键词，不等于该事项不存在；扫描版、复杂表格、措辞差异和未披露都可能造成漏检。"
        ),
    }


def update_filing_evidence_review(
    db: str | os.PathLike[str] | sqlite3.Connection,
    evidence_id: int,
    *,
    review_status: str,
    reviewer_note: str = "",
) -> dict[str, Any]:
    status = review_status.strip().lower()
    if status not in {"pending", "confirmed", "dismissed"}:
        raise ValueError("证据审核状态只能是 pending、confirmed 或 dismissed")
    reviewed_at = None if status == "pending" else datetime.now(timezone.utc).isoformat()
    with _using_connection(db) as connection:
        cursor = connection.execute(
            """UPDATE filing_evidence SET review_status=?, reviewer_note=?, reviewed_at=? WHERE id=?""",
            (status, reviewer_note.strip()[:1000], reviewed_at, int(evidence_id)),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"证据线索不存在：{evidence_id}")
        row = connection.execute("SELECT * FROM filing_evidence WHERE id=?", (int(evidence_id),)).fetchone()
        value = dict(row)
        try:
            value["detected_values"] = json.loads(value.pop("detected_values_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            value["detected_values"] = []
        return value


def filing_stats(db: str | os.PathLike[str] | sqlite3.Connection) -> dict[str, int]:
    with _using_connection(db) as connection:
        row = connection.execute(
            """SELECT COUNT(*) AS filings,
                      COUNT(DISTINCT symbol) AS companies,
                      COALESCE(SUM(CASE WHEN is_correction=1 THEN 1 ELSE 0 END), 0) AS corrections,
                      COALESCE(SUM(CASE WHEN fa.extraction_status IN ('ready', 'ready_ocr') THEN 1 ELSE 0 END), 0) AS searchable_filings,
                      COALESCE(SUM(CASE WHEN fa.extraction_status='ocr_partial' THEN 1 ELSE 0 END), 0) AS partially_searchable_filings,
                      COALESCE(SUM(CASE WHEN fa.extraction_status NOT IN ('ready', 'ready_ocr', 'ocr_partial') THEN 1 ELSE 0 END), 0) AS unsearchable_filings,
                      (SELECT COUNT(*) FROM filing_evidence) AS evidence,
                      (SELECT COUNT(*) FROM filing_evidence WHERE severity='red' AND review_status!='dismissed') AS red_signals,
                      (SELECT COUNT(*) FROM filing_evidence WHERE review_status='pending') AS pending_reviews,
                      (SELECT COUNT(*) FROM filing_evidence WHERE review_status='confirmed') AS confirmed_evidence,
                      (SELECT COUNT(*) FROM filing_facts WHERE review_status='pending') AS pending_facts,
                      (SELECT COUNT(*) FROM filing_facts WHERE review_status='confirmed') AS confirmed_facts
               FROM filings f JOIN filing_artifacts fa ON fa.id=f.artifact_id"""
        ).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}


def filing_review_summary(
    db: str | os.PathLike[str] | sqlite3.Connection,
    symbol: str,
) -> dict[str, Any]:
    """Summarize current statutory-document coverage for one company.

    Originals replaced by a linked correction are excluded. Pending red
    keyword hits stay pending; only a human-confirmed red item is counted as a
    confirmed risk signal.
    """

    normalized = _normalize_symbol(symbol)
    with _using_connection(db) as connection:
        row = connection.execute(
            """SELECT COUNT(*) AS filings,
                      COALESCE(SUM(CASE WHEN fa.extraction_status IN ('ready', 'ready_ocr') THEN 1 ELSE 0 END), 0) AS searchable,
                      COALESCE(SUM(CASE WHEN fa.extraction_status='ocr_partial' THEN 1 ELSE 0 END), 0) AS partially_searchable,
                      COALESCE(SUM(CASE WHEN fa.extraction_status NOT IN ('ready', 'ready_ocr', 'ocr_partial') THEN 1 ELSE 0 END), 0) AS unsearchable,
                      COALESCE(SUM((SELECT COUNT(*) FROM filing_evidence e
                                    WHERE e.filing_id=f.id AND e.review_status='pending')), 0) AS pending_evidence,
                      COALESCE(SUM((SELECT COUNT(*) FROM filing_evidence e
                                    WHERE e.filing_id=f.id AND e.review_status='confirmed')), 0) AS confirmed_evidence,
                      COALESCE(SUM((SELECT COUNT(*) FROM filing_evidence e
                                    WHERE e.filing_id=f.id AND e.review_status='pending' AND e.severity='red')), 0) AS pending_red,
                      COALESCE(SUM((SELECT COUNT(*) FROM filing_evidence e
                                    WHERE e.filing_id=f.id AND e.review_status='confirmed' AND e.severity='red')), 0) AS confirmed_red,
                      MAX(f.disclosed_at) AS latest_disclosed_at
               FROM filings f
               JOIN filing_artifacts fa ON fa.id=f.artifact_id
               WHERE f.symbol=?
                 AND NOT EXISTS (SELECT 1 FROM filings correction WHERE correction.supersedes_filing_id=f.id)""",
            (normalized,),
        ).fetchone()
    result = {
        key: int(row[key] or 0)
        for key in (
            "filings", "searchable", "partially_searchable", "unsearchable",
            "pending_evidence", "confirmed_evidence", "pending_red", "confirmed_red",
        )
    }
    result["latest_disclosed_at"] = row["latest_disclosed_at"] or None
    result["policy"] = "待复核关键词不等于事实；不可检索或部分 OCR 的公告不能据此认定无风险。"
    return result


def filing_exists(
    db: str | os.PathLike[str] | sqlite3.Connection,
    *,
    source_provider: str,
    source_notice_id: str,
) -> bool:
    if not source_notice_id.strip():
        return False
    with _using_connection(db) as connection:
        row = connection.execute(
            "SELECT 1 FROM filings WHERE source_provider=? AND source_notice_id=? LIMIT 1",
            (source_provider.strip(), source_notice_id.strip()),
        ).fetchone()
        return row is not None


def get_filing_sync_state(
    db: str | os.PathLike[str] | sqlite3.Connection,
    symbol: str,
    *,
    source_provider: str = "cninfo_public",
) -> dict[str, Any] | None:
    with _using_connection(db) as connection:
        row = connection.execute(
            "SELECT * FROM filing_sync_state WHERE symbol=? AND source_provider=?",
            (_normalize_symbol(symbol), source_provider),
        ).fetchone()
        return dict(row) if row else None


def list_filing_sync_states(
    db: str | os.PathLike[str] | sqlite3.Connection,
) -> list[dict[str, Any]]:
    with _using_connection(db) as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM filing_sync_state ORDER BY last_run_at DESC, symbol"
            ).fetchall()
        ]


def start_filing_sync_run(
    db: str | os.PathLike[str] | sqlite3.Connection,
    symbol: str,
    *,
    since_date: str,
    until_date: str,
    source_provider: str = "cninfo_public",
) -> int:
    normalized = _normalize_symbol(symbol)
    now = datetime.now(timezone.utc).isoformat()
    with _using_connection(db) as connection:
        connection.execute(
            """INSERT INTO filing_sync_state(symbol, source_provider, last_run_at, status, last_error, updated_at)
               VALUES (?, ?, ?, 'running', '', CURRENT_TIMESTAMP)
               ON CONFLICT(symbol, source_provider) DO UPDATE SET
                   last_run_at=excluded.last_run_at, status='running', last_error='', updated_at=CURRENT_TIMESTAMP""",
            (normalized, source_provider, now),
        )
        cursor = connection.execute(
            """INSERT INTO filing_sync_runs(symbol, source_provider, since_date, until_date)
               VALUES (?, ?, ?, ?)""",
            (normalized, source_provider, since_date, until_date),
        )
        return int(cursor.lastrowid)


def finish_filing_sync_run(
    db: str | os.PathLike[str] | sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    discovered: int,
    archived: int,
    duplicates: int,
    errors: list[dict[str, Any]] | None = None,
    cursor_date: str | None = None,
    last_disclosed_at: str = "",
) -> dict[str, Any]:
    if status not in {"success", "partial", "error"}:
        raise ValueError("同步状态无效")
    error_items = errors or []
    with _using_connection(db) as connection:
        run = connection.execute(
            "SELECT symbol, source_provider FROM filing_sync_runs WHERE id=?", (int(run_id),)
        ).fetchone()
        if run is None:
            raise KeyError(f"公告同步任务不存在：{run_id}")
        connection.execute(
            """UPDATE filing_sync_runs SET status=?, discovered=?, archived=?, duplicates=?,
                      failed=?, error_json=?, completed_at=CURRENT_TIMESTAMP WHERE id=?""",
            (
                status,
                int(discovered),
                int(archived),
                int(duplicates),
                len(error_items),
                json.dumps(error_items, ensure_ascii=False),
                int(run_id),
            ),
        )
        connection.execute(
            """UPDATE filing_sync_state SET
                   cursor_date=CASE WHEN ? IS NULL THEN cursor_date ELSE ? END,
                   last_disclosed_at=CASE WHEN ?='' THEN last_disclosed_at ELSE ? END,
                   status=?, last_error=?, last_discovered=?, last_archived=?, last_duplicates=?,
                   updated_at=CURRENT_TIMESTAMP
               WHERE symbol=? AND source_provider=?""",
            (
                cursor_date,
                cursor_date,
                last_disclosed_at,
                last_disclosed_at,
                status,
                str(error_items[0].get("error") or "") if error_items else "",
                int(discovered),
                int(archived),
                int(duplicates),
                run["symbol"],
                run["source_provider"],
            ),
        )
        state = connection.execute(
            "SELECT * FROM filing_sync_state WHERE symbol=? AND source_provider=?",
            (run["symbol"], run["source_provider"]),
        ).fetchone()
        return dict(state)


def extract_evidence_signals(pages: Iterable[tuple[int, str]]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for page_number, raw_text in pages:
        text = re.sub(r"\s+", " ", raw_text).strip()
        if not text:
            continue
        for spec in EVIDENCE_PATTERNS:
            for match in spec.pattern.finditer(text):
                start = max(0, match.start() - 150)
                end = min(len(text), match.end() + 260)
                excerpt = text[start:end].strip()
                key = (spec.category, page_number, excerpt)
                if key in seen:
                    continue
                seen.add(key)
                severity = _signal_severity(spec.category, excerpt)
                signals.append(
                    {
                        "category": spec.category,
                        "severity": severity,
                        "title": spec.label,
                        "excerpt": excerpt,
                        "page_number": page_number,
                        "locator": f"pdf:p:{page_number:06d}",
                        "detected_values": _detected_values(excerpt),
                    }
                )
    return signals


def _detected_values(excerpt: str) -> list[dict[str, str]]:
    """Capture exact textual values without interpreting their accounting meaning."""

    values: list[dict[str, str]] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"(?P<raw>(?:人民币)?\s*-?[\d,]+(?:\.\d+)?\s*(?:万亿元|亿元|万元|元|%|％|个百分点|倍|天))"
    )
    for match in pattern.finditer(excerpt):
        raw = re.sub(r"\s+", "", match.group("raw"))
        if raw in seen:
            continue
        seen.add(raw)
        unit_match = re.search(r"(万亿元|亿元|万元|元|%|％|个百分点|倍|天)$", raw)
        values.append({"raw": raw, "unit": unit_match.group(1) if unit_match else ""})
        if len(values) >= 12:
            break
    return values


def _signal_severity(category: str, excerpt: str) -> str:
    if category == "audit_opinion":
        has_unqualified = bool(re.search(r"标准无保留意见|无保留意见", excerpt))
        has_adverse = bool(re.search(r"否定意见|无法表示意见|(?<!无)保留意见", excerpt))
        if has_unqualified and has_adverse:
            return "review"
        if has_adverse:
            return "red"
        if re.search(r"非标准(?:无保留)?(?:审计)?意见", excerpt):
            return "review"
        if re.search(r"强调事项段|持续经营.*重大不确定性", excerpt):
            return "review"
        if has_unqualified:
            return "info"
    if category == "accounting_change" and re.search(r"前期差错更正|追溯调整", excerpt):
        return "red"
    if category == "regulatory_inquiry" and re.search(r"纪律处分|监管措施", excerpt):
        return "red"
    return "review"


def _extraction_quality(pages: Iterable[tuple[int, str]]) -> float:
    visible = [character for _, text in pages for character in text if not character.isspace()]
    if not visible:
        return 0.0
    expected = sum(
        character.isascii() or "\u3400" <= character <= "\u9fff" for character in visible
    )
    return expected / len(visible)


def _extract_pdf_pages(raw: bytes) -> tuple[list[tuple[int, str]], str, float, str]:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return [], "missing_pypdf", 0.0, "缺少 pypdf，尚未提取文本"
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            reader.decrypt("")
        pages: list[tuple[int, str]] = []
        for number, page in enumerate(reader.pages, start=1):
            pages.append((number, page.extract_text() or ""))
        quality = _extraction_quality(pages)
        if not any(text.strip() for _, text in pages):
            return pages, "needs_ocr", quality, "PDF 没有可用文本层，需要 OCR"
        if quality < 0.82:
            return pages, "needs_ocr", quality, "文本层字符映射异常，需要 OCR 后才能检索"
        return pages, "ready", quality, "文本层通过可读性检查"
    except Exception as exc:
        return [], "parse_failed", 0.0, f"PDF 文本解析失败：{type(exc).__name__}"


def _download_pdf(url: str, *, timeout: float) -> tuple[bytes, str, str]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "InvestmentBrain/0.1 (personal local research)"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - URL allowlist above
            final_url = validate_official_filing_url(response.geturl())
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > MAX_FILING_BYTES:
                raise ValueError(f"单份财报不能超过 {MAX_FILING_BYTES // 1024 // 1024} MB")
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILING_BYTES:
                    raise ValueError(f"单份财报不能超过 {MAX_FILING_BYTES // 1024 // 1024} MB")
                chunks.append(chunk)
            return b"".join(chunks), final_url, response.headers.get_content_type()
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(f"下载官方公告失败：{exc}") from exc


def _filing_row(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    try:
        value["metadata"] = json.loads(value.pop("metadata_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        value["metadata"] = {}
    return value


def _normalize_symbol(value: str) -> str:
    symbol = value.strip().upper().split(".", 1)[0]
    if not re.fullmatch(r"\d{6}", symbol):
        raise ValueError("股票代码必须是六位数字")
    return symbol


def _infer_period_from_title(title: str) -> str:
    match = re.search(r"(20\d{2})\s*年", title)
    if not match:
        return ""
    year = match.group(1)
    if "半年度" in title:
        return f"{year}-06-30"
    if "第一季度" in title:
        return f"{year}-03-31"
    if "第三季度" in title:
        return f"{year}-09-30"
    return f"{year}-12-31"


def _clean_required(value: str, label: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    if not cleaned:
        raise ValueError(f"{label}不能为空")
    return cleaned


def _atomic_write(path: Path, raw: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, path)


class _ConnectionContext:
    def __init__(self, db: str | os.PathLike[str] | sqlite3.Connection):
        self.db = db
        self.connection: sqlite3.Connection | None = None
        self.owned = False

    def __enter__(self) -> sqlite3.Connection:
        if isinstance(self.db, sqlite3.Connection):
            self.connection = self.db
        else:
            self.connection = get_connection(self.db)
            self.owned = True
        return self.connection

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        assert self.connection is not None
        if exc is None:
            self.connection.commit()
        elif self.owned:
            self.connection.rollback()
        if self.owned:
            self.connection.close()


def _using_connection(
    db: str | os.PathLike[str] | sqlite3.Connection,
) -> _ConnectionContext:
    return _ConnectionContext(db)
