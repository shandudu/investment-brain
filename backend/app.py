from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .finance_engine import analyze_company, demo_company
from .cninfo_provider import search_critical_filings
from .filing_store import (
    archive_filing,
    filing_exists,
    filing_evidence_coverage,
    filing_review_summary,
    filing_stats,
    finish_filing_sync_run,
    get_filing_sync_state,
    get_filing,
    init_filing_store,
    list_filing_evidence,
    list_filing_facts,
    list_filing_sync_states,
    list_filings,
    ocr_filing_batch,
    reindex_filing,
    start_filing_sync_run,
    update_filing_evidence_review,
    update_filing_fact_review,
)
from .free_provider import FreeProviderError, fetch_free_company_financials, free_provider_available
from .knowledge import (
    analyze_doctrine_relationships,
    create_doctrine_card,
    create_library,
    generate_doctrine_candidates,
    get_connection,
    import_documents,
    init_database,
    list_doctrine_cards,
    list_doctrine_card_versions,
    list_doctrine_generation_runs,
    list_documents,
    list_libraries,
    search_knowledge,
    update_doctrine_card,
    update_library,
)
from .tushare_provider import (
    fetch_company_financials,
    is_configured as tushare_is_configured,
    normalize_ts_code,
)


APP_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = APP_ROOT / "data"
DB_PATH = DATA_DIR / "investment_brain.db"
FILINGS_DIR = DATA_DIR / "filings"
DIST_DIR = APP_ROOT / "dist" / "client"
DEFAULT_KNOWLEDGE_ROOT = APP_ROOT.parent
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_BYTES = 600 * 1024
REPORT_FORMULA_FILES = (
    "finance_engine.py",
    "financial_changes.py",
    "bank_engine.py",
    "sector_engine.py",
    "financial_sector_engine.py",
)
FINANCIAL_PERIOD_STALE_DAYS = 180


@dataclass
class Settings:
    knowledge_root: str = str(DEFAULT_KNOWLEDGE_ROOT)
    ai_provider: str = "codex"
    api_base_url: str = ""
    api_model: str = ""
    api_key_env: str = "INVESTMENT_BRAIN_API_KEY"
    api_input_price_per_million: float = 0.0
    api_output_price_per_million: float = 0.0
    api_price_currency: str = "CNY"
    monthly_token_limit: int = 1_000_000


class Application:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.filings_dir = self.db_path.parent / "filings"
        init_database(self.db_path)
        init_filing_store(self.db_path)
        self._init_app_tables()
        self._seed_demo()

    def connect(self) -> sqlite3.Connection:
        return get_connection(self.db_path)

    def _init_app_tables(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS holdings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    quantity REAL NOT NULL DEFAULT 0,
                    cost REAL NOT NULL DEFAULT 0,
                    current_price REAL,
                    current_price_as_of TEXT NOT NULL DEFAULT '',
                    industry TEXT NOT NULL DEFAULT '',
                    thesis TEXT NOT NULL DEFAULT '',
                    horizon TEXT NOT NULL DEFAULT '',
                    max_loss_pct REAL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol)
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    title TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    review_status TEXT NOT NULL DEFAULT 'unreviewed',
                    review_due_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS report_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id INTEGER NOT NULL UNIQUE REFERENCES reports(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'open',
                    expected_conditions TEXT NOT NULL DEFAULT '',
                    actual_outcome TEXT NOT NULL DEFAULT '',
                    reviewer_note TEXT NOT NULL DEFAULT '',
                    actual_as_of TEXT NOT NULL DEFAULT '',
                    due_at TEXT,
                    reviewed_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_report_reviews_due ON report_reviews(status, due_at);
                CREATE TABLE IF NOT EXISTS ai_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    period_month TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    estimated INTEGER NOT NULL DEFAULT 1 CHECK(estimated IN (0, 1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_ai_usage_month ON ai_usage(period_month);
                CREATE TABLE IF NOT EXISTS financial_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    as_of_request TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_financial_snapshot_lookup
                    ON financial_snapshots(symbol, as_of_request, retrieved_at DESC);
                CREATE TABLE IF NOT EXISTS valuation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    as_of TEXT NOT NULL DEFAULT '',
                    assumptions_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_valuation_runs_symbol_created
                    ON valuation_runs(symbol, created_at DESC, id DESC);
                CREATE TABLE IF NOT EXISTS watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            holding_columns = {row["name"] for row in conn.execute("PRAGMA table_info(holdings)")}
            if "industry" not in holding_columns:
                conn.execute("ALTER TABLE holdings ADD COLUMN industry TEXT NOT NULL DEFAULT ''")
            if "current_price_as_of" not in holding_columns:
                conn.execute("ALTER TABLE holdings ADD COLUMN current_price_as_of TEXT NOT NULL DEFAULT ''")
            report_columns = {row["name"] for row in conn.execute("PRAGMA table_info(reports)")}
            report_defaults = {
                "evidence_json": "TEXT NOT NULL DEFAULT '{}'",
                "snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
                "review_status": "TEXT NOT NULL DEFAULT 'unreviewed'",
                "review_due_at": "TEXT",
            }
            for column, definition in report_defaults.items():
                if column not in report_columns:
                    conn.execute(f"ALTER TABLE reports ADD COLUMN {column} {definition}")

    def _seed_demo(self) -> None:
        with self.connect() as conn:
            # Remove only the exact synthetic v0.1 seed row; user-entered rows
            # are never touched.
            conn.execute(
                "DELETE FROM holdings WHERE symbol=? AND name=? AND thesis=?",
                ("600519", "贵州茅台（示例）", "品牌与现金流示例记录"),
            )
            lib_count = conn.execute("SELECT COUNT(*) FROM libraries").fetchone()[0]
            if lib_count == 0:
                create_library(conn, "散修宗", "交易纪律、现金流与风险框架", "custom")
                create_library(conn, "红利投资", "关注可分配现金流与分红持续性", "dividend")
                create_library(conn, "格雷厄姆", "资产质量、正常化盈利与安全边际", "graham")

    def settings(self) -> dict[str, Any]:
        defaults = asdict(Settings())
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        for row in rows:
            try:
                defaults[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                defaults[row["key"]] = row["value"]
        try:
            stored_limit = int(defaults["monthly_token_limit"])
        except (TypeError, ValueError):
            stored_limit = Settings.monthly_token_limit
        defaults["monthly_token_limit"] = min(max(stored_limit, 1_000), 100_000_000)
        settings_warnings: list[str] = []
        for key in ("api_input_price_per_million", "api_output_price_per_million"):
            try:
                price = float(defaults.get(key) or 0)
            except (TypeError, ValueError):
                price = 0.0
            if not math.isfinite(price) or price < 0 or price > 1_000_000:
                price = 0.0
                settings_warnings.append("已隔离无效的 API Token 单价，请在设置中重新填写。")
            defaults[key] = price
        currency = str(defaults.get("api_price_currency") or "CNY").strip().upper()
        if currency not in {"CNY", "USD"}:
            currency = "CNY"
            settings_warnings.append("已将无效的 API 价格币种恢复为 CNY。")
        defaults["api_price_currency"] = currency
        stored_base_url = str(defaults.get("api_base_url") or "").strip()
        if stored_base_url:
            try:
                defaults["api_base_url"] = _validated_api_base_url(stored_base_url)
            except ValueError:
                defaults["api_base_url"] = ""
                settings_warnings.append("已隔离旧版本保存的非法 API Base URL，请重新填写 HTTPS 地址。")
        defaults["settings_warnings"] = settings_warnings
        defaults["api_key_configured"] = bool(os.environ.get(defaults["api_key_env"], ""))
        defaults["tushare_configured"] = tushare_is_configured()
        defaults["free_provider_available"] = free_provider_available()
        defaults["financial_data_mode"] = "tushare" if defaults["tushare_configured"] else ("free" if defaults["free_provider_available"] else "demo")
        period_month = time.strftime("%Y-%m")
        with self.connect() as conn:
            usage = conn.execute(
                "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM ai_usage WHERE period_month=?",
                (period_month,),
            ).fetchone()[0]
        defaults["monthly_tokens_used"] = int(usage)
        defaults["monthly_tokens_remaining"] = max(0, int(defaults["monthly_token_limit"]) - int(usage))
        return defaults

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = set(asdict(Settings()))
        if "monthly_token_limit" in values:
            try:
                token_limit = int(values["monthly_token_limit"])
            except (TypeError, ValueError) as exc:
                raise ValueError("月度 Token 上限必须是整数") from exc
            if not 1_000 <= token_limit <= 100_000_000:
                raise ValueError("月度 Token 上限必须在 1,000 到 100,000,000 之间")
            values["monthly_token_limit"] = token_limit
        for key in ("api_input_price_per_million", "api_output_price_per_million"):
            if key not in values:
                continue
            raw = values[key]
            if isinstance(raw, bool):
                raise ValueError("API Token 单价必须是非负数字")
            try:
                price = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("API Token 单价必须是非负数字") from exc
            if not math.isfinite(price) or price < 0 or price > 1_000_000:
                raise ValueError("API Token 单价必须在 0 到 1,000,000 之间")
            values[key] = price
        if "api_price_currency" in values:
            currency = str(values["api_price_currency"] or "CNY").strip().upper()
            if currency not in {"CNY", "USD"}:
                raise ValueError("API 价格币种只能是 CNY 或 USD")
            values["api_price_currency"] = currency
        if values.get("api_base_url"):
            values["api_base_url"] = _validated_api_base_url(str(values["api_base_url"]))
        with self.connect() as conn:
            for key, value in values.items():
                if key not in allowed:
                    continue
                conn.execute(
                    """INSERT INTO settings(key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
                    (key, json.dumps(value, ensure_ascii=False)),
                )
        return self.settings()

    def set_library_enabled(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            library_id = int(payload.get("id") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("缺少有效的投资体系编号") from exc
        if library_id <= 0:
            raise ValueError("缺少有效的投资体系编号")
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"enable", "disable"}:
            raise ValueError("投资体系操作只能是 enable 或 disable")
        library = update_library(self.db_path, library_id, enabled=action == "enable")
        return {
            "library": library,
            "analysis_policy": (
                "该体系已启用；其中审核通过且单独启用的规则可以参与后续分析。"
                if action == "enable"
                else "该体系已停用；其中所有规则立即退出后续分析，但文档、规则和版本历史仍保留。"
            ),
        }

    def update_doctrine_card_from_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            card_id = int(payload.get("id") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("缺少有效的规则卡编号") from exc
        if card_id <= 0:
            raise ValueError("缺少有效的规则卡编号")
        action = str(payload.get("action") or "").strip().lower()
        action_changes = {
            "approve": {"review_status": "approved", "enabled": True},
            "reject": {"review_status": "rejected", "enabled": False},
            "reset": {"review_status": "pending", "enabled": False},
            "enable": {"enabled": True},
            "disable": {"enabled": False},
        }
        if action == "edit":
            editable = {
                key: payload[key]
                for key in (
                    "library_id", "title", "content", "category", "confidence", "applicability",
                    "conditions", "exceptions", "tags", "industries", "cycles",
                    "metrics", "thresholds", "counter_evidence", "source_document_id",
                    "source_chunk_id", "source_locator",
                )
                if key in payload
            }
            if not editable:
                raise ValueError("没有可保存的规则字段")
            # Any substantive revision is a new, unreviewed rule version.  It must
            # never inherit approval from the previous wording.
            changes = {**editable, "review_status": "pending", "enabled": False, "_change_kind": "edit_requires_review"}
        elif action in action_changes:
            changes = action_changes[action]
        else:
            raise ValueError("未知的规则卡操作")
        return {"card": update_doctrine_card(self.db_path, card_id, changes)}

    def dashboard(self, *, scope: str = "all") -> dict[str, Any]:
        scope = str(scope or "all").strip().lower()
        if scope not in {"all", "core", "knowledge", "filings"}:
            raise ValueError("仪表盘 scope 只能是 all、core、knowledge 或 filings")
        with self.connect() as conn:
            holdings = [dict(row) for row in conn.execute("SELECT * FROM holdings ORDER BY id")]
            reports = [dict(row) for row in conn.execute(
                "SELECT id, symbol, title, provider, review_status, review_due_at, created_at FROM reports ORDER BY id DESC LIMIT 5"
            )]
        total_cost = sum(float(h["quantity"]) * float(h["cost"]) for h in holdings)
        diagnostics = self.portfolio_diagnostics(holdings)
        total_value = diagnostics["total_value_for_diagnostics"]
        priced_symbols = {position["symbol"] for position in diagnostics["positions"] if position["value"] is not None}
        priced_cost = sum(float(h["quantity"]) * float(h["cost"]) for h in holdings if h["symbol"] in priced_symbols)
        result = {
            "dashboard_scope": scope,
            "holdings": holdings,
            "portfolio": {
                "cost": round(total_cost, 2),
                "value": round(total_value, 2),
                "pnl": round(total_value - priced_cost, 2),
                "priced_positions": diagnostics["coverage"]["priced_positions"],
                "valuation_policy": "市值与盈亏仅统计有价格的持仓，缺价不以成本代替",
                "positions": len(holdings),
            },
            "portfolio_diagnostics": diagnostics,
            "reports": reports,
            "review_reminders": self.list_report_reminders(limit=10),
            "watchlist": self.list_watchlist(),
            "settings": self.settings(),
            "data_health": self.data_health(),
        }
        if scope in {"all", "knowledge"}:
            assigned = "(d.library_id IS NOT NULL OR EXISTS(SELECT 1 FROM library_documents ld WHERE ld.document_id=d.id))"
            with self.connect() as conn:
                document_stats = dict(conn.execute(
                    f"""SELECT
                        SUM(CASE WHEN {assigned} THEN 1 ELSE 0 END) AS document_instances,
                        SUM(CASE WHEN {assigned} AND d.is_current=1 AND d.status='ready' THEN 1 ELSE 0 END) AS unique_documents,
                        SUM(CASE WHEN {assigned} AND d.is_current=1 AND d.status='duplicate' THEN 1 ELSE 0 END) AS duplicates,
                        SUM(CASE WHEN NOT {assigned} THEN 1 ELSE 0 END) AS quarantined_documents
                       FROM documents d"""
                ).fetchone())
                document_stats["chunks"] = conn.execute(
                    f"SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id=c.document_id WHERE {assigned}"
                ).fetchone()[0]
                document_stats["quarantined_chunks"] = conn.execute(
                    f"SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id=c.document_id WHERE NOT {assigned}"
                ).fetchone()[0]
            result.update({
                "libraries": list_libraries(self.db_path),
                "doctrine_cards": list_doctrine_cards(self.db_path),
                "doctrine_relationships": analyze_doctrine_relationships(self.db_path),
                "documents": list_documents(self.db_path, limit=8, trusted_only=True),
                "document_stats": document_stats,
            })
        if scope in {"all", "filings"}:
            result.update({
                "filings": list_filings(self.db_path, limit=8),
                "filing_stats": filing_stats(self.db_path),
                "filing_sync_states": list_filing_sync_states(self.db_path),
            })
        return result

    def portfolio_diagnostics(self, holdings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Diagnose a portfolio from local prices and already-cached facts only.

        This intentionally never triggers a market request from the dashboard:
        a missing fact is more useful than a hidden slow network call. Refresh a
        company/observation first, then the portfolio view picks it up.
        """
        if holdings is None:
            with self.connect() as conn:
                holdings = [dict(row) for row in conn.execute("SELECT * FROM holdings ORDER BY id")]
        positions: list[dict[str, Any]] = []
        total_value = 0.0
        for holding in holdings:
            symbol = str(holding.get("symbol") or "").strip()
            try:
                ts_code = normalize_ts_code(symbol)
            except ValueError:
                ts_code = ""
            snapshot = None
            if ts_code:
                with self.connect() as conn:
                    snapshot = conn.execute(
                        """SELECT payload_json, provider, retrieved_at FROM financial_snapshots
                           WHERE symbol=? AND as_of_request='' ORDER BY id DESC LIMIT 1""",
                        (ts_code,),
                    ).fetchone()
            snapshot_payload: dict[str, Any] | None = None
            analysis: dict[str, Any] | None = None
            filing_review = filing_review_summary(self.db_path, symbol) if ts_code else {
                "filings": 0, "searchable": 0, "partially_searchable": 0,
                "unsearchable": 0, "pending_evidence": 0, "confirmed_evidence": 0,
                "pending_red": 0, "confirmed_red": 0, "latest_disclosed_at": None,
            }
            if snapshot:
                try:
                    snapshot_payload = json.loads(snapshot["payload_json"])
                    holding_industry = str(holding.get("industry") or "").strip()
                    if holding_industry and isinstance(snapshot_payload, dict):
                        # Free public sources do not always supply a reliable
                        # industry. A user-provided banking/insurance label must
                        # stop the generic industrial model instead of creating
                        # spurious ratios.
                        snapshot_payload = dict(snapshot_payload)
                        company_identity = dict(snapshot_payload.get("company") or {})
                        company_identity["industry"] = holding_industry
                        snapshot_payload["company"] = company_identity
                    snapshot_payload = self._enrich_with_confirmed_filing_facts(snapshot_payload, ts_code)
                    analysis = analyze_company(snapshot_payload)
                except (TypeError, ValueError, json.JSONDecodeError):
                    snapshot_payload = None
                    analysis = None
            data_as_of = str(analysis.get("as_of") or "").strip() if isinstance(analysis, dict) else ""
            period_freshness = _financial_period_freshness(data_as_of, available=bool(analysis))
            financial_period_age_days = period_freshness["age_days"]
            financial_period_status = period_freshness["status"]
            analysis_usable = bool(analysis) and financial_period_status != "future"
            market = analysis.get("market") if isinstance(analysis, dict) else {}
            market = market if isinstance(market, dict) else {}
            price = _optional_positive_number(holding.get("current_price"), "当前价")
            price_source = "手工录入" if price is not None else ""
            price_as_of = str(holding.get("current_price_as_of") or "").strip() if price is not None else ""
            if price is None:
                market_price = _optional_positive_number(market.get("market_price"), "行情价格")
                if market_price is not None:
                    price = market_price
                    price_source = str(snapshot["provider"] if snapshot else "本地快照")
                    price_as_of = str(market.get("price_as_of") or "").strip()
            price_age_days: int | None = None
            price_date_valid = False
            if price_as_of:
                try:
                    price_age_days = (date.today() - date.fromisoformat(price_as_of)).days
                    price_date_valid = price_age_days >= 0
                except ValueError:
                    price_date_valid = False
            quantity = float(holding.get("quantity") or 0)
            value = quantity * price if price is not None else None
            if value is not None:
                total_value += value
            ttm = ((analysis.get("periods") or {}).get("ttm") if analysis_usable and isinstance(analysis, dict) else None) or {}
            metrics = ttm.get("metrics") if isinstance(ttm, dict) else {}
            metrics = metrics if isinstance(metrics, dict) else {}
            def metric_value(name: str) -> float | None:
                item = metrics.get(name)
                value = item.get("value") if isinstance(item, dict) else None
                return _optional_finite_number(value, name) if value is not None else None
            flags = analysis.get("flags", []) if analysis_usable and isinstance(analysis, dict) else []
            flags = flags if isinstance(flags, list) else []
            red_flags = [flag for flag in flags if isinstance(flag, dict) and flag.get("severity") == "red"]
            yellow_flags = [flag for flag in flags if isinstance(flag, dict) and flag.get("severity") == "yellow"]
            change_payload = analysis.get("changes", {}) if analysis_usable and isinstance(analysis, dict) else {}
            change_signals = change_payload.get("signals", []) if isinstance(change_payload, dict) else []
            change_signals = change_signals if isinstance(change_signals, list) else []
            red_changes = [item for item in change_signals if isinstance(item, dict) and item.get("severity") == "red"]
            yellow_changes = [item for item in change_signals if isinstance(item, dict) and item.get("severity") == "yellow"]
            data_quality = analysis.get("data_quality", {}) if isinstance(analysis, dict) else {}
            quality_score = data_quality.get("score") if isinstance(data_quality, dict) else None
            position = {
                "symbol": symbol,
                "name": holding.get("name") or symbol,
                "industry": str(holding.get("industry") or "").strip(),
                "value": round(value, 2) if value is not None else None,
                "price": price,
                "price_source": price_source or None,
                "price_as_of": price_as_of or None,
                "price_age_days": price_age_days,
                "price_date_valid": price_date_valid,
                "financial_data": analysis_usable,
                "data_as_of": data_as_of or None,
                "financial_period_age_days": financial_period_age_days,
                "financial_period_status": financial_period_status,
                "snapshot_provider": snapshot["provider"] if snapshot else None,
                "snapshot_retrieved_at": snapshot["retrieved_at"] if snapshot else None,
                "snapshot_status": "stale" if snapshot and _snapshot_age_hours(snapshot["retrieved_at"]) > 24 else ("ready" if snapshot else "missing"),
                "data_quality_score": quality_score,
                "cash_conversion": metric_value("cash_conversion"),
                "return_on_invested_capital": metric_value("return_on_invested_capital"),
                "debt_to_equity": metric_value("debt_to_equity"),
                "cost": float(holding.get("cost") or 0),
                "max_loss_pct": _optional_finite_number(holding.get("max_loss_pct"), "最大承受亏损", minimum=0, maximum=100),
                "thesis": str(holding.get("thesis") or "").strip(),
                "red_flags": len(red_flags),
                "yellow_flags": len(yellow_flags),
                "red_changes": len(red_changes),
                "yellow_changes": len(yellow_changes),
                "filing_review": filing_review,
                "pending_filing_reviews": int(filing_review.get("pending_evidence") or 0),
                "confirmed_filing_red_flags": int(filing_review.get("confirmed_red") or 0),
                "unsearchable_filings": int(filing_review.get("unsearchable") or 0),
                "flag_titles": list(dict.fromkeys(
                    str(flag.get("title") or flag.get("id") or "财务信号")
                    for flag in (red_changes + red_flags + yellow_changes + yellow_flags)
                ))[:3],
                "review_conditions": list(dict.fromkeys(
                    str(item.get("review_condition") or "").strip()
                    for item in (red_changes + yellow_changes)
                    if str(item.get("review_condition") or "").strip()
                ))[:3],
            }
            if not analysis_usable:
                position["risk_status"] = "待刷新"
            elif red_flags or red_changes:
                position["risk_status"] = "高风险"
            elif yellow_flags or yellow_changes:
                position["risk_status"] = "需复核"
            else:
                position["risk_status"] = "暂无红黄旗"
            positions.append(position)
        known_positions = [position for position in positions if position["value"] is not None and total_value > 0]
        for position in positions:
            position["weight"] = round(float(position["value"]) / total_value, 6) if position["value"] is not None and total_value else None
            reasons: list[str] = []
            actions: list[str] = []
            review_conditions = list(position.get("review_conditions", []))
            priority = "green"
            price = position.get("price")
            cost = float(position.get("cost") or 0)
            max_loss_pct = position.get("max_loss_pct")
            if price is None:
                priority = "yellow"
                reasons.append("缺少可用价格，无法核验成本亏损与当前仓位")
                actions.append("补充有日期的行情价格后重新计算组合风险")
                review_conditions.append("取得带交易日期的有效价格后，重新计算成本亏损和仓位")
            elif position.get("price_as_of") is None or not position.get("price_date_valid"):
                priority = "yellow"
                reasons.append("当前价缺少有效交易日期，成本亏损与仓位只能视为待核值")
                actions.append("补录当前价对应的交易日期，或清空手工价格后使用有日期的数据快照")
                review_conditions.append("价格及交易日期同时完整后，才能解除价格数据缺口")
            elif int(position.get("price_age_days") or 0) > 7:
                priority = "yellow"
                reasons.append(f"当前价截至 {position['price_as_of']}，已过去 {position['price_age_days']} 天")
                actions.append("更新近期交易日价格后再核验亏损触发和组合仓位")
                review_conditions.append("价格更新至最近 7 天内的交易日后重新计算")
            if cost <= 0:
                priority = "yellow"
                reasons.append("缺少有效成本，无法核验相对成本亏损")
                actions.append("补充真实持仓成本后复核风险预算")
            if max_loss_pct is None:
                priority = "yellow"
                reasons.append("尚未设置最大承受亏损，成本纪律未完整校验")
                actions.append("记录可承受损失及触发后的复核条件")
                review_conditions.append("设置最大承受亏损比例及触发后的人工处置规则")
            return_pct = (float(price) / cost - 1) if price is not None and cost > 0 else None
            loss_limit_triggered = (
                return_pct is not None
                and max_loss_pct is not None
                and return_pct <= -(float(max_loss_pct) / 100)
            )
            if loss_limit_triggered:
                priority = "red"
                reasons.append(f"当前相对成本 {return_pct * 100:.1f}%，已触及记录的最大承受亏损 {float(max_loss_pct):.1f}%")
                actions.append("立即复核原买入逻辑和风险预算，决定是否降低暴露；系统不会自动卖出")
                review_conditions.append("核对最新价格、财务事实与原买入逻辑；只有风险预算恢复或逻辑重新成立后才解除")
            if position["red_flags"] or position["red_changes"]:
                priority = "red"
                reasons.append("财务红旗或高优先变化已经触发")
                actions.extend(("暂停新增仓位，先核对公式上游事实与最新公告", "逐条验证买入逻辑是否已经失效"))
            elif position["yellow_flags"] or position["yellow_changes"]:
                if priority != "red":
                    priority = "yellow"
                reasons.append("存在需要解释的财务变化或黄色线索")
                actions.append("记录管理层解释与下一报告期验证条件")
            if position["confirmed_filing_red_flags"]:
                priority = "red"
                reasons.append(f"有 {position['confirmed_filing_red_flags']} 条人工确认的高优先公告证据")
                actions.append("打开公告原文定位，复核其对持仓逻辑、估值假设和风险预算的影响")
                review_conditions.append("确认相关公告风险已消除、已充分计价或不影响原持仓逻辑后再解除")
            if position["pending_filing_reviews"]:
                if priority == "green":
                    priority = "yellow"
                reasons.append(f"有 {position['pending_filing_reviews']} 条公告关键词线索尚待人工复核，当前不按事实处理")
                actions.append("在财报证据页逐条核对原文，将线索确认为证据或排除误报")
                review_conditions.append("公告待复核线索全部完成确认或排除后，重新计算持仓诊断")
            if position["unsearchable_filings"]:
                if priority == "green":
                    priority = "yellow"
                reasons.append(f"有 {position['unsearchable_filings']} 份当前公告不可完整检索，不能据此认定没有风险")
                actions.append("完成公告 OCR 或直接核对 PDF 原件后再判断治理与会计风险")
                review_conditions.append("不可检索公告完成全文 OCR 或人工原件复核后，重新计算持仓诊断")
            if position["financial_period_status"] == "future":
                if priority == "green":
                    priority = "yellow"
                reasons.append(f"财务报告期 {position['data_as_of']} 晚于今天，已停止使用该快照的财务信号")
                actions.append("核对快照来源与报告日期，重新获取不晚于今天的真实披露")
                review_conditions.append("取得报告期不晚于今天、来源可追溯的财务快照后再恢复诊断")
            elif not position["financial_data"]:
                if priority == "green":
                    priority = "yellow"
                reasons.append("没有可用的真实财务快照")
                actions.append("先刷新财务数据和关键公告，避免依据旧印象决策")
            elif position["financial_period_status"] == "unknown":
                if priority == "green":
                    priority = "yellow"
                reasons.append("财务快照缺少有效报告期，无法判断基本面数据是否仍具时效性")
                actions.append("补齐财务报告期并核对原始披露时间")
                review_conditions.append("报告期明确且不晚于今天后，重新计算持仓诊断")
            elif position["financial_period_status"] == "stale":
                if priority == "green":
                    priority = "yellow"
                reasons.append(f"财务数据截至 {position['data_as_of']}，距今已超过 {FINANCIAL_PERIOD_STALE_DAYS} 天")
                actions.append("取得更新报告期的财务数据和公告后再判断基本面状态")
                review_conditions.append(f"财务报告期更新至最近 {FINANCIAL_PERIOD_STALE_DAYS} 天内后重新计算")
            elif position["snapshot_status"] == "stale":
                if priority == "green":
                    priority = "yellow"
                reasons.append("财务快照已超过 24 小时未更新")
                actions.append("更新数据后再作仓位调整")
            if position["weight"] is not None and float(position["weight"]) >= 0.25:
                if priority == "green":
                    priority = "yellow"
                reasons.append(f"单一持仓占已计价组合 {float(position['weight']) * 100:.1f}%")
                actions.append("核对该集中度是否仍符合组合风险预算")
            if not position["thesis"]:
                if priority == "green":
                    priority = "yellow"
                reasons.append("尚未记录可验证的买入逻辑")
                actions.append("补充买入逻辑、失效条件和计划持有周期")
            if not reasons:
                reasons.append("成本纪律、财务变化和组合集中度均未触发当前阈值")
                actions.append("继续观察，并在下一份财报后重新计算")
            position["return_pct"] = round(return_pct, 6) if return_pct is not None else None
            position["decision"] = {
                "priority": priority,
                "status": "减仓复核" if priority == "red" else ("等待条件" if priority == "yellow" else "持有"),
                "loss_limit_triggered": loss_limit_triggered,
                "reasons": list(dict.fromkeys(reasons)),
                "next_actions": list(dict.fromkeys(actions)),
                "review_conditions": list(dict.fromkeys(review_conditions)),
                "execution_policy": "仅供人工复核，不自动交易；减仓复核不等于卖出指令。",
            }
        positions.sort(key=lambda position: float(position["value"] or -1), reverse=True)
        industry_values: dict[str, float] = {}
        unclassified_value = 0.0
        for position in known_positions:
            industry = str(position["industry"] or "").strip()
            if industry:
                industry_values[industry] = industry_values.get(industry, 0.0) + float(position["value"] or 0)
            else:
                unclassified_value += float(position["value"] or 0)
        industries = sorted(
            [{"industry": name, "value": round(value, 2), "weight": round(value / total_value, 6)} for name, value in industry_values.items()],
            key=lambda item: item["value"], reverse=True,
        )
        top_position = positions[0] if positions and positions[0]["value"] is not None else None
        hhi = sum((float(position["weight"] or 0) * 100) ** 2 for position in known_positions)
        alerts: list[dict[str, Any]] = []
        if top_position and float(top_position["weight"] or 0) >= 0.25:
            alerts.append({"severity": "yellow", "title": "单一持仓集中", "detail": f"{top_position['name']} 占已计价组合 {float(top_position['weight']) * 100:.1f}%"})
        if industries and float(industries[0]["weight"]) >= 0.4:
            alerts.append({"severity": "yellow", "title": "行业集中", "detail": f"{industries[0]['industry']} 占已分类组合 {float(industries[0]['weight']) * 100:.1f}%"})
        for position in positions:
            if position["red_flags"] or position["red_changes"]:
                alerts.append({"severity": "red", "title": f"{position['name']} 存在红旗", "detail": "；".join(position["flag_titles"])})
            elif position["yellow_flags"] or position["yellow_changes"]:
                alerts.append({"severity": "yellow", "title": f"{position['name']} 需要复核", "detail": "；".join(position["flag_titles"])})
        return {
            "positions": positions,
            "coverage": {
                "positions": len(positions),
                "priced_positions": sum(1 for position in positions if position["value"] is not None),
                "financial_positions": sum(1 for position in positions if position["financial_data"]),
                "industry_classified_positions": sum(1 for position in positions if position["industry"]),
                "unclassified_value": round(unclassified_value, 2),
            },
            "total_value_for_diagnostics": round(total_value, 2),
            "concentration": {
                "top_position": top_position["symbol"] if top_position else None,
                "top_position_weight": top_position["weight"] if top_position else None,
                "hhi": round(hhi, 1) if known_positions else None,
                "interpretation": "HHI 以已计价持仓的权重计算；行业暴露仅统计已录入行业的持仓。",
            },
            "industries": industries,
            "alerts": alerts[:12],
            "decision_summary": {
                "immediate_review": sum(1 for position in positions if position["decision"]["priority"] == "red"),
                "conditional_review": sum(1 for position in positions if position["decision"]["priority"] == "yellow"),
                "observe": sum(1 for position in positions if position["decision"]["priority"] == "green"),
            },
        }

    def compare_companies(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Compare 2-4 companies on one deterministic financial basis.

        The default path only reads local snapshots. Network refresh happens
        solely when the caller explicitly requests it.
        """

        raw_symbols = payload.get("symbols")
        if not isinstance(raw_symbols, list):
            raise ValueError("请选择 2 到 4 家公司进行对比")
        symbols: list[str] = []
        for raw_symbol in raw_symbols:
            ts_code = normalize_ts_code(str(raw_symbol or ""))
            symbol = ts_code.split(".", 1)[0]
            if symbol not in symbols:
                symbols.append(symbol)
        if not 2 <= len(symbols) <= 4:
            raise ValueError("公司对比需要 2 到 4 个不同的股票代码")
        refresh = bool(payload.get("refresh", False))
        as_of = str(payload.get("as_of") or "").strip() or None
        results: list[dict[str, Any]] = []
        with self.connect() as conn:
            known_names = {
                str(row["symbol"]): str(row["name"] or "")
                for row in conn.execute(
                    "SELECT symbol, name FROM holdings UNION SELECT symbol, name FROM watchlist"
                )
            }
        metric_names = (
            "revenue_growth",
            "net_income_growth",
            "gross_margin",
            "net_margin",
            "return_on_equity",
            "return_on_invested_capital",
            "cash_conversion",
            "free_cash_flow",
            "free_cash_flow_dividend_cover",
            "debt_to_equity",
            "current_ratio",
            "interest_coverage",
        )
        for symbol in symbols:
            ts_code = normalize_ts_code(symbol)
            filing_review = filing_review_summary(self.db_path, symbol)
            source: dict[str, Any] = {}
            company_payload: dict[str, Any] | None = None
            if refresh:
                try:
                    company_payload, source = self.company_financial_data(symbol, as_of=as_of, refresh=True)
                except (RuntimeError, ValueError) as exc:
                    results.append({"symbol": symbol, "name": known_names.get(symbol) or symbol, "status": "error", "error": str(exc), "filing_review": filing_review})
                    continue
            else:
                with self.connect() as conn:
                    snapshot = conn.execute(
                        """SELECT payload_json, provider, retrieved_at FROM financial_snapshots
                           WHERE symbol=? AND as_of_request=? ORDER BY id DESC LIMIT 1""",
                        (ts_code, as_of or ""),
                    ).fetchone()
                    if snapshot is None and as_of is None:
                        snapshot = conn.execute(
                            """SELECT payload_json, provider, retrieved_at FROM financial_snapshots
                               WHERE symbol=? ORDER BY id DESC LIMIT 1""",
                            (ts_code,),
                        ).fetchone()
                if snapshot:
                    company_payload = json.loads(snapshot["payload_json"])
                    company_payload = self._enrich_with_confirmed_filing_facts(
                        company_payload,
                        ts_code,
                        disclosed_on_or_before=as_of,
                    )
                    source = {
                        "provider": snapshot["provider"],
                        "mode": _financial_provider_mode(snapshot["provider"]),
                        "retrieved_at": snapshot["retrieved_at"],
                        "cache": "stale" if _snapshot_age_hours(snapshot["retrieved_at"]) > 24 else "fresh",
                    }
            if company_payload is None:
                results.append({"symbol": symbol, "name": known_names.get(symbol) or symbol, "status": "missing", "error": "本地没有财务快照，请点击“更新数据并对比”。", "filing_review": filing_review})
                continue
            try:
                analysis = analyze_company(company_payload)
            except (TypeError, ValueError) as exc:
                results.append({"symbol": symbol, "name": known_names.get(symbol) or symbol, "status": "error", "error": str(exc)})
                continue
            company = analysis.get("company") if isinstance(analysis.get("company"), dict) else {}
            period_freshness = _financial_period_freshness(
                analysis.get("as_of"),
                reference_date=as_of,
                available=True,
            )
            if period_freshness["status"] == "future":
                results.append({
                    "symbol": symbol,
                    "name": company.get("name") or known_names.get(symbol) or symbol,
                    "industry": company.get("industry") or "",
                    "status": "error",
                    "error": f"财务报告期 {analysis.get('as_of') or '缺失'} 晚于对比截止日 {period_freshness['reference_date']}，已停止比较。",
                    "source": source,
                    "financial_period": period_freshness,
                    "filing_review": filing_review,
                })
                continue
            support = analysis.get("support") if isinstance(analysis.get("support"), dict) else {}
            if support.get("supported") is False:
                results.append({
                    "symbol": symbol,
                    "name": company.get("name") or known_names.get(symbol) or symbol,
                    "industry": company.get("industry") or "",
                    "status": "unsupported",
                    "error": support.get("reason") or "该行业尚未接入专用财务模板。",
                    "source": source,
                    "filing_review": filing_review,
                })
                continue
            ttm = ((analysis.get("periods") or {}).get("ttm") or {}) if isinstance(analysis.get("periods"), dict) else {}
            metrics_payload = ttm.get("metrics") if isinstance(ttm, dict) else {}
            metrics_payload = metrics_payload if isinstance(metrics_payload, dict) else {}
            metrics: dict[str, float | None] = {}
            for name in metric_names:
                item = metrics_payload.get(name)
                raw_value = item.get("value") if isinstance(item, dict) else None
                metrics[name] = _optional_finite_number(raw_value, name) if raw_value is not None else None
            flags = analysis.get("flags") if isinstance(analysis.get("flags"), list) else []
            red_flags = [item for item in flags if isinstance(item, dict) and item.get("severity") == "red"]
            yellow_flags = [item for item in flags if isinstance(item, dict) and item.get("severity") == "yellow"]
            changes_payload = analysis.get("changes") if isinstance(analysis.get("changes"), dict) else {}
            change_signals = changes_payload.get("signals") if isinstance(changes_payload.get("signals"), list) else []
            red_changes = [item for item in change_signals if isinstance(item, dict) and item.get("severity") == "red"]
            yellow_changes = [item for item in change_signals if isinstance(item, dict) and item.get("severity") == "yellow"]
            market = analysis.get("market") if isinstance(analysis.get("market"), dict) else {}
            market_metrics: dict[str, float | None] = {}
            for name in ("market_price", "market_cap", "pe_ttm", "pb", "dividend_yield_ttm"):
                raw_value = market.get(name)
                market_metrics[name] = _optional_finite_number(raw_value, name) if raw_value is not None else None
            confirmed_filing_red = int(filing_review.get("confirmed_red") or 0)
            pending_filing = int(filing_review.get("pending_evidence") or 0)
            unsearchable_filings = int(filing_review.get("unsearchable") or 0)
            decision_reasons = (
                ([f"财务数据截至 {analysis.get('as_of') or '缺失'}，相对对比基准已超过 {FINANCIAL_PERIOD_STALE_DAYS} 天"] if period_freshness["status"] == "stale" else ["财务报告期缺失或无效，无法判断数据时效性"] if period_freshness["status"] == "unknown" else [])
                + [str(item.get("title") or item.get("id") or "财务信号") for item in (red_changes + red_flags + yellow_changes + yellow_flags)[:3]]
                + ([f"有 {confirmed_filing_red} 条人工确认的高优先公告证据"] if confirmed_filing_red else [])
                + ([f"有 {pending_filing} 条公告关键词线索待人工复核，当前不按事实处理"] if pending_filing else [])
                + ([f"有 {unsearchable_filings} 份公告不可完整检索，不能认定没有风险"] if unsearchable_filings else [])
            )
            review_conditions = (
                ([f"取得相对对比基准最近 {FINANCIAL_PERIOD_STALE_DAYS} 天内的同口径财务报告后重新比较"] if period_freshness["status"] == "stale" else ["补齐有效财务报告期后重新比较"] if period_freshness["status"] == "unknown" else [])
                + [str(item.get("review_condition") or "").strip() for item in (red_changes + yellow_changes) if str(item.get("review_condition") or "").strip()]
                + (["逐条核对人工确认公告证据对财务质量、估值和风险预算的影响"] if confirmed_filing_red else [])
                + (["公告待复核线索全部完成确认或排除"] if pending_filing else [])
                + (["不可检索公告完成全文 OCR 或人工原件复核"] if unsearchable_filings else [])
            )
            decision_red = bool(red_flags or red_changes or confirmed_filing_red)
            decision_yellow = bool(yellow_flags or yellow_changes or pending_filing or unsearchable_filings or period_freshness["status"] != "ready")
            decision_blocked = bool(decision_red or pending_filing or unsearchable_filings or period_freshness["status"] != "ready")
            results.append({
                "symbol": symbol,
                "name": company.get("name") or known_names.get(symbol) or symbol,
                "industry": company.get("industry") or "",
                "status": "ready",
                "as_of": analysis.get("as_of"),
                "financial_period": period_freshness,
                "source": source,
                "data_quality": analysis.get("data_quality"),
                "metrics": metrics,
                "market": market_metrics,
                "risk": {
                    "red_flags": len(red_flags),
                    "yellow_flags": len(yellow_flags),
                    "red_changes": len(red_changes),
                    "yellow_changes": len(yellow_changes),
                    "confirmed_filing_red": confirmed_filing_red,
                    "pending_filing_reviews": pending_filing,
                    "unsearchable_filings": unsearchable_filings,
                    "titles": [str(item.get("title") or item.get("id") or "财务信号") for item in (red_changes + red_flags + yellow_changes + yellow_flags)[:4]],
                },
                "filing_review": filing_review,
                "decision": {
                    "status": "等待条件" if decision_blocked else "继续观察",
                    "priority": "red" if decision_red else ("yellow" if decision_yellow else "green"),
                    "reasons": list(dict.fromkeys(decision_reasons))[:5] or ["当前确定性阈值未触发红黄旗，但不代表风险不存在"],
                    "review_conditions": list(dict.fromkeys(review_conditions))[:5],
                    "execution_policy": "对比结果只用于观察或等待条件，不构成建仓建议。",
                },
            })
        ready_count = sum(1 for item in results if item.get("status") == "ready")
        return {
            "schema_version": "company-comparison/0.1",
            "as_of_request": as_of,
            "refreshed": refresh,
            "companies": results,
            "coverage": {"requested": len(symbols), "ready": ready_count},
            "methodology": {
                "basis": "相同确定性财务公式与最近可用 TTM 口径",
                "warning": "横向比较用于发现差异，不构成买卖排序；跨行业时应优先使用行业专用模板。",
            },
        }

    def run_valuation_workbench(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run and optionally archive a user-authored, deterministic DCF scenario set."""

        symbol = str(payload.get("symbol") or "").strip()
        if symbol:
            company, source = self.company_financial_data(
                symbol,
                as_of=str(payload.get("as_of") or "").strip() or None,
                refresh=bool(payload.get("refresh", False)),
            )
        else:
            raw_company = payload.get("company")
            if not isinstance(raw_company, dict):
                raise ValueError("估值工作台需要股票代码或标准化公司数据")
            company = raw_company
            source = {"provider": "request_payload"}
            symbol = str((company.get("company") or {}).get("symbol") or "custom")
        assumptions = payload.get("assumptions")
        if not isinstance(assumptions, dict):
            raise ValueError("缺少估值假设")
        raw_scenarios = assumptions.get("scenarios")
        if not isinstance(raw_scenarios, list) or not 1 <= len(raw_scenarios) <= 5:
            raise ValueError("估值情景必须为 1 到 5 组")
        scenarios: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_scenarios):
            if not isinstance(raw, dict):
                raise ValueError("每个估值情景必须是对象")
            name = str(raw.get("name") or f"scenario_{index + 1}").strip()[:40]
            growth = _finite_number(raw.get("growth_rate"), f"{name}增长率", minimum=-0.9, maximum=1.0)
            discount = _finite_number(raw.get("discount_rate"), f"{name}折现率", minimum=0.001, maximum=1.0)
            terminal = _finite_number(raw.get("terminal_growth_rate"), f"{name}永续增长率", minimum=-0.5, maximum=0.2)
            years = _integer_number(raw.get("forecast_years", 5), f"{name}预测年数", minimum=1, maximum=15)
            if discount <= terminal:
                raise ValueError(f"{name}：折现率必须高于永续增长率")
            scenarios.append(
                {
                    "name": name,
                    "growth_rate": growth,
                    "discount_rate": discount,
                    "terminal_growth_rate": terminal,
                    "forecast_years": years,
                }
            )
        base_cash_flow = _optional_finite_number(assumptions.get("base_cash_flow"), "基准现金流", minimum=0.000001)
        cash_flow_basis = str(assumptions.get("cash_flow_basis") or "fcfe").strip().lower()
        if cash_flow_basis not in {"fcfe", "fcff"}:
            raise ValueError("现金流口径只能是 fcfe 或 fcff")
        dividend_per_share = _optional_finite_number(assumptions.get("dividend_per_share"), "每股股息", minimum=0.000001)
        assumptions_as_of = str(assumptions.get("assumptions_as_of") or "").strip()
        margin_of_safety = _finite_number(assumptions.get("margin_of_safety", 0.2), "安全边际", minimum=0, maximum=0.8)
        ddm_enabled = bool(assumptions.get("ddm_enabled", False))
        relative_scenarios: list[dict[str, Any]] = []
        raw_relative = assumptions.get("relative_scenarios") or []
        if raw_relative and not isinstance(raw_relative, list):
            raise ValueError("相对估值情景必须为数组")
        for index, raw in enumerate(raw_relative):
            if not isinstance(raw, dict):
                raise ValueError("相对估值情景必须是对象")
            target_pe = _optional_finite_number(raw.get("target_pe"), "目标 PE", minimum=0.01, maximum=500)
            target_pb = _optional_finite_number(raw.get("target_pb"), "目标 PB", minimum=0.01, maximum=100)
            if target_pe is None and target_pb is None:
                continue
            relative_scenarios.append({"name": str(raw.get("name") or f"scenario_{index + 1}")[:40], "target_pe": target_pe, "target_pb": target_pb})
        manifest = {
            "schema_version": "valuation-workbench/1.0",
            "cash_flow_basis": cash_flow_basis,
            "base_cash_flow": base_cash_flow,
            "scenarios": scenarios,
            "ddm_enabled": ddm_enabled,
            "dividend_per_share": dividend_per_share,
            "relative_scenarios": relative_scenarios,
            "margin_of_safety": margin_of_safety,
            "assumptions_as_of": assumptions_as_of or None,
        }
        configured = json.loads(json.dumps(company, ensure_ascii=False))
        valuation = dict(configured.get("valuation") or {})
        dcf_config: dict[str, Any] = {"cash_flow_basis": cash_flow_basis, "scenarios": scenarios}
        if base_cash_flow is not None:
            dcf_config["base_cash_flow"] = base_cash_flow
        valuation["dcf"] = dcf_config
        base_scenario = next((item for item in scenarios if item["name"] in {"base", "基准"}), scenarios[len(scenarios) // 2])
        valuation["reverse_dcf"] = {
            "cash_flow_basis": cash_flow_basis,
            "discount_rate": base_scenario["discount_rate"],
            "terminal_growth_rate": base_scenario["terminal_growth_rate"],
            "forecast_years": base_scenario["forecast_years"],
            "growth_lower_bound": -0.5,
            "growth_upper_bound": 1.0,
            **({"base_cash_flow": base_cash_flow} if base_cash_flow is not None else {}),
        }
        growth_center = float(base_scenario["growth_rate"])
        discount_center = float(base_scenario["discount_rate"])
        terminal_center = float(base_scenario["terminal_growth_rate"])
        valuation["sensitivity"] = {
            "cash_flow_basis": cash_flow_basis,
            "growth_rates": [max(-0.9, growth_center - 0.02), growth_center, min(1.0, growth_center + 0.02)],
            "discount_rates": [(discount_center + terminal_center) / 2, discount_center, min(1.0, discount_center + 0.01)],
            "terminal_growth_rate": base_scenario["terminal_growth_rate"],
            "forecast_years": base_scenario["forecast_years"],
            **({"base_cash_flow": base_cash_flow} if base_cash_flow is not None else {}),
        }
        valuation.pop("ddm", None)
        valuation.pop("relative", None)
        if ddm_enabled:
            valuation["ddm"] = {
                **({"dividend_per_share": dividend_per_share} if dividend_per_share is not None else {}),
                "scenarios": [
                    {
                        "name": item["name"],
                        "growth_rate": item["growth_rate"],
                        "cost_of_equity": item["discount_rate"],
                        "terminal_growth_rate": item["terminal_growth_rate"],
                        "forecast_years": item["forecast_years"],
                    }
                    for item in scenarios
                ],
            }
        if relative_scenarios:
            valuation["relative"] = {"scenarios": relative_scenarios}
        configured["valuation"] = valuation
        analysis = analyze_company(configured)
        valuation_result = analysis.get("valuation") if isinstance(analysis.get("valuation"), dict) else {}

        def scenario_values(method: str) -> list[float]:
            payload_value = valuation_result.get(method) if isinstance(valuation_result, dict) else {}
            items = payload_value.get("scenarios", []) if isinstance(payload_value, dict) else []
            values: list[float] = []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict) or item.get("status") != "ok":
                    continue
                if method == "relative":
                    for key in ("value_range_low", "value_range_high"):
                        value = item.get(key)
                        if isinstance(value, (int, float)) and math.isfinite(float(value)):
                            values.append(float(value))
                else:
                    value = item.get("value_per_share")
                    if isinstance(value, (int, float)) and math.isfinite(float(value)):
                        values.append(float(value))
            return values

        method_ranges = []
        for method, label in (("dcf", "DCF"), ("ddm", "DDM"), ("relative", "PE/PB 相对估值")):
            values = scenario_values(method)
            if not values:
                continue
            low, high = min(values), max(values)
            method_ranges.append(
                {
                    "method": method,
                    "label": label,
                    "value_range_low": low,
                    "value_range_high": high,
                    "margin_of_safety": margin_of_safety,
                    "margin_price_low": low * (1 - margin_of_safety),
                    "margin_price_high": high * (1 - margin_of_safety),
                }
            )
        provider_payload = company.get("provider") if isinstance(company.get("provider"), dict) else {}
        market_payload = analysis.get("market") if isinstance(analysis.get("market"), dict) else {}
        as_of_request = str(payload.get("as_of") or provider_payload.get("as_of") or source.get("as_of_request") or "")
        date_context = {
            "as_of_request": as_of_request or None,
            "financial_period_end": analysis.get("as_of"),
            "price_as_of": market_payload.get("price_as_of"),
            "shares_as_of": valuation.get("shares_as_of"),
            "net_debt_as_of": analysis.get("as_of"),
            "assumptions_as_of": assumptions_as_of or None,
        }
        date_context.update(_valuation_date_audit(
            company,
            payload,
            source,
            required_dates=date_context,
            required_keys=("financial_period_end", "price_as_of", "shares_as_of", "net_debt_as_of", "assumptions_as_of"),
        ))
        if isinstance(valuation_result, dict):
            valuation_result["workbench_summary"] = {
                "method_ranges": method_ranges,
                "date_context": date_context,
                "policy": "Methods remain separate ranges; they are not averaged into a single target price.",
            }
        manifest["reproduction"] = _valuation_reproduction(company, configured, source, date_context)
        source_hash = hashlib.sha256(
            json.dumps(company, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        run_id = None
        if payload.get("save", True):
            with self.connect() as conn:
                cursor = conn.execute(
                    """INSERT INTO valuation_runs(symbol, as_of, assumptions_json, result_json, source_hash)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        normalize_ts_code(symbol) if symbol != "custom" else symbol,
                        str(analysis.get("as_of") or ""),
                        json.dumps(manifest, ensure_ascii=False, allow_nan=False),
                        json.dumps(analysis.get("valuation") or {}, ensure_ascii=False, allow_nan=False),
                        source_hash,
                    ),
                )
                run_id = int(cursor.lastrowid)
        return {"run_id": run_id, "analysis": analysis, "assumptions": manifest, "source": source, "source_hash": source_hash}

    def run_sector_valuation_workbench(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run explicit resource, utility or property-sector scenarios."""

        template = str(payload.get("sector_template") or "").strip().lower()
        if template not in {"cyclical_resource", "utility", "real_estate", "insurance", "brokerage"}:
            raise ValueError("行业估值模板只能是 cyclical_resource、utility、real_estate、insurance 或 brokerage")
        symbol = str(payload.get("symbol") or "").strip()
        if symbol:
            default_industry = {"cyclical_resource": "周期资源", "utility": "公用事业", "real_estate": "房地产开发", "insurance": "保险", "brokerage": "证券"}[template]
            company, source = self.company_financial_data(symbol, as_of=str(payload.get("as_of") or "").strip() or None, refresh=bool(payload.get("refresh", False)), industry_hint=str(payload.get("industry") or default_industry))
        else:
            company = payload.get("company")
            if not isinstance(company, dict):
                raise ValueError("行业估值工作台需要股票代码或标准化公司数据")
            source = {"provider": "request_payload"}
            symbol = str((company.get("company") or {}).get("symbol") or "custom")
        assumptions = payload.get("assumptions")
        if not isinstance(assumptions, dict):
            raise ValueError("缺少行业估值假设")
        assumptions_as_of = str(assumptions.get("assumptions_as_of") or "").strip()
        raw_scenarios = assumptions.get("scenarios")
        if not isinstance(raw_scenarios, list) or not 1 <= len(raw_scenarios) <= 5:
            raise ValueError("行业估值情景必须为 1 到 5 组")
        margin = _finite_number(assumptions.get("margin_of_safety", 0.2), "安全边际", minimum=0, maximum=0.8)
        scenarios = []
        for index, raw in enumerate(raw_scenarios):
            if not isinstance(raw, dict):
                raise ValueError("每个行业估值情景必须是对象")
            name = str(raw.get("name") or f"scenario_{index + 1}").strip()[:40]
            if template == "cyclical_resource":
                scenario = {
                    "name": name,
                    "normalized_volume": _finite_number(raw.get("normalized_volume"), f"{name}正常化产量", minimum=0.000001, maximum=1e15),
                    "normalized_price": _finite_number(raw.get("normalized_price"), f"{name}正常化价格", minimum=0.000001, maximum=1e15),
                    "unit_cash_cost": _finite_number(raw.get("unit_cash_cost"), f"{name}单位现金成本", minimum=0, maximum=1e15),
                    "tax_rate": _finite_number(raw.get("tax_rate"), f"{name}税率", minimum=0, maximum=0.99),
                    "target_pe": _finite_number(raw.get("target_pe"), f"{name}目标 PE", minimum=0.01, maximum=100),
                    "recoverable_reserves": _finite_number(raw.get("recoverable_reserves"), f"{name}可采储量", minimum=0.000001, maximum=1e18),
                    "recovery_factor": _finite_number(raw.get("recovery_factor"), f"{name}经济可采比例", minimum=0.000001, maximum=1),
                    "discount_rate": _finite_number(raw.get("discount_rate"), f"{name}折现率", minimum=0.000001, maximum=1),
                    "mine_life_years": _integer_number(raw.get("mine_life_years"), f"{name}矿山寿命", minimum=1, maximum=60),
                }
            elif template == "utility":
                scenario = {
                    "name": name,
                    "normalized_rab": _finite_number(raw.get("normalized_rab"), f"{name}正常化受监管资产", minimum=0, maximum=1e18),
                    "allowed_return": _finite_number(raw.get("allowed_return"), f"{name}准许收益率", minimum=0.000001, maximum=1),
                    "tax_rate": _finite_number(raw.get("tax_rate"), f"{name}税率", minimum=0, maximum=0.99),
                    "target_pe": _finite_number(raw.get("target_pe"), f"{name}目标 PE", minimum=0.01, maximum=100),
                    "dividend_growth": _finite_number(raw.get("dividend_growth"), f"{name}股息增长", minimum=-0.9, maximum=1),
                    "cost_of_equity": _finite_number(raw.get("cost_of_equity"), f"{name}权益成本", minimum=0.000001, maximum=1),
                    "terminal_growth": _finite_number(raw.get("terminal_growth"), f"{name}永续增长", minimum=-0.5, maximum=0.2),
                    "forecast_years": _integer_number(raw.get("forecast_years", 5), f"{name}预测年数", minimum=1, maximum=15),
                }
                if scenario["cost_of_equity"] <= scenario["terminal_growth"]:
                    raise ValueError(f"{name}：权益成本必须高于永续增长率")
            elif template == "real_estate":
                scenario = {
                    "name": name,
                    "inventory_haircut": _finite_number(raw.get("inventory_haircut"), f"{name}存货折价率", minimum=0, maximum=1),
                    "contract_recognition_rate": _finite_number(raw.get("contract_recognition_rate"), f"{name}合同负债结转比例", minimum=0, maximum=1),
                    "normalized_gross_margin": _finite_number(raw.get("normalized_gross_margin"), f"{name}正常化毛利率", minimum=0, maximum=1),
                    "tax_rate": _finite_number(raw.get("tax_rate"), f"{name}税率", minimum=0, maximum=0.99),
                    "target_pb": _finite_number(raw.get("target_pb"), f"{name}目标 PB", minimum=0.01, maximum=20),
                    "additional_loss_provision": _finite_number(raw.get("additional_loss_provision", 0), f"{name}额外损失准备", minimum=0, maximum=1e18),
                }
            elif template == "insurance":
                scenario = {
                    "name": name,
                    "target_pev": _finite_number(raw.get("target_pev"), f"{name}目标 P/EV", minimum=0.01, maximum=5),
                    "normalized_nbv": _finite_number(raw.get("normalized_nbv"), f"{name}正常化新业务价值", minimum=0, maximum=1e18),
                    "nbv_multiple": _finite_number(raw.get("nbv_multiple"), f"{name}新业务价值倍数", minimum=0, maximum=50),
                    "ev_haircut": _finite_number(raw.get("ev_haircut", 0), f"{name}内含价值折价率", minimum=0, maximum=0.9),
                }
            else:
                scenario = {
                    "name": name,
                    "target_pb": _finite_number(raw.get("target_pb"), f"{name}目标 PB", minimum=0.01, maximum=20),
                    "target_pe": _finite_number(raw.get("target_pe"), f"{name}目标 PE", minimum=0.01, maximum=100),
                    "normalized_net_income": _finite_number(raw.get("normalized_net_income"), f"{name}正常化净利润", minimum=0.000001, maximum=1e18),
                }
            if raw.get("net_debt") not in (None, ""):
                scenario["net_debt"] = _finite_number(raw.get("net_debt"), f"{name}净债务", minimum=-1e18, maximum=1e18)
            scenarios.append(scenario)
        manifest = {"schema_version": "sector-valuation-workbench/1.0", "sector_template": template, "margin_of_safety": margin, "scenarios": scenarios, "dividend_per_share": _optional_finite_number(assumptions.get("dividend_per_share"), "每股股息", minimum=0.000001), "assumptions_as_of": assumptions_as_of or None}
        configured = json.loads(json.dumps(company, ensure_ascii=False))
        valuation = dict(configured.get("valuation") or {})
        valuation["sector_template"] = template
        valuation["sector"] = {"scenarios": scenarios}
        if template == "utility":
            valuation["ddm"] = {
                **({"dividend_per_share": manifest["dividend_per_share"]} if manifest["dividend_per_share"] is not None else {}),
                "scenarios": [{"name": item["name"], "growth_rate": item["dividend_growth"], "cost_of_equity": item["cost_of_equity"], "terminal_growth_rate": item["terminal_growth"], "forecast_years": item["forecast_years"]} for item in scenarios],
            }
        configured["valuation"] = valuation
        analysis = analyze_company(configured)
        support = analysis.get("support") if isinstance(analysis.get("support"), dict) else {}
        if support.get("status") != "supported_sector" or support.get("sector_template") != template:
            raise ValueError("当前公司与所选行业模板不匹配，拒绝套用估值")
        valuation_result = analysis.get("valuation") if isinstance(analysis.get("valuation"), dict) else {}
        method_values: dict[str, list[float]] = {}
        if template == "cyclical_resource":
            for item in valuation_result.get("scenarios", []) if isinstance(valuation_result.get("scenarios"), list) else []:
                if not isinstance(item, dict):
                    continue
                for method in ("midcycle", "nav"):
                    block = item.get(method) if isinstance(item.get(method), dict) else {}
                    value = block.get("value_per_share")
                    if block.get("status") == "ok" and isinstance(value, (int, float)) and math.isfinite(float(value)):
                        method_values.setdefault(method, []).append(float(value))
        elif template == "utility":
            for method in ("regulated_return", "ddm"):
                block = valuation_result.get(method) if isinstance(valuation_result.get(method), dict) else {}
                for item in block.get("scenarios", []) if isinstance(block.get("scenarios"), list) else []:
                    value = item.get("value_per_share") if isinstance(item, dict) else None
                    if isinstance(item, dict) and item.get("status") == "ok" and isinstance(value, (int, float)) and math.isfinite(float(value)):
                        method_values.setdefault(method, []).append(float(value))
        elif template == "real_estate":
            for item in valuation_result.get("scenarios", []) if isinstance(valuation_result.get("scenarios"), list) else []:
                if not isinstance(item, dict):
                    continue
                for method in ("adjusted_nav", "adjusted_pb"):
                    block = item.get(method) if isinstance(item.get(method), dict) else {}
                    value = block.get("value_per_share")
                    if block.get("status") == "ok" and isinstance(value, (int, float)) and math.isfinite(float(value)):
                        method_values.setdefault(method, []).append(float(value))
        else:
            methods = ("pev", "appraisal_value") if template == "insurance" else ("pb", "normalized_pe")
            for item in valuation_result.get("scenarios", []) if isinstance(valuation_result.get("scenarios"), list) else []:
                if not isinstance(item, dict):
                    continue
                for method in methods:
                    block = item.get(method) if isinstance(item.get(method), dict) else {}
                    value = block.get("value_per_share")
                    if block.get("status") == "ok" and isinstance(value, (int, float)) and math.isfinite(float(value)):
                        method_values.setdefault(method, []).append(float(value))
        labels = {"midcycle": "中周期盈利", "nav": "储量 NAV", "regulated_return": "受监管资产收益", "ddm": "DDM", "adjusted_nav": "合同结转调整 NAV", "adjusted_pb": "资产折价后 PB", "pev": "内含价值 P/EV", "appraisal_value": "评估价值 EV+NBV", "pb": "净资产 PB", "normalized_pe": "正常化盈利 PE"}
        ranges = []
        for method, values in method_values.items():
            low, high = min(values), max(values)
            ranges.append({"method": method, "label": labels[method], "value_range_low": low, "value_range_high": high, "margin_of_safety": margin, "margin_price_low": low * (1 - margin), "margin_price_high": high * (1 - margin)})
        market = analysis.get("market") if isinstance(analysis.get("market"), dict) else {}
        company_valuation = company.get("valuation") if isinstance(company.get("valuation"), dict) else {}
        date_context = {
            "as_of_request": str(payload.get("as_of") or (company.get("provider") or {}).get("as_of") or source.get("as_of_request") or "") or None,
            "financial_period_end": analysis.get("as_of"),
            "price_as_of": market.get("price_as_of"),
            "shares_as_of": company_valuation.get("shares_as_of"),
            "assumptions_as_of": assumptions_as_of or None,
        }
        required_keys = ["financial_period_end", "price_as_of", "shares_as_of", "assumptions_as_of"]
        if template == "cyclical_resource":
            date_context["net_debt_as_of"] = analysis.get("as_of")
            required_keys.append("net_debt_as_of")
        date_context.update(_valuation_date_audit(company, payload, source, required_dates=date_context, required_keys=tuple(required_keys)))
        valuation_result["workbench_summary"] = {"method_ranges": ranges, "date_context": date_context, "policy": "行业方法保持独立区间，不平均成单点目标价。"}
        manifest["reproduction"] = _valuation_reproduction(company, configured, source, date_context)
        source_hash = hashlib.sha256(json.dumps(company, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        run_id = None
        if payload.get("save", True):
            with self.connect() as conn:
                cursor = conn.execute("""INSERT INTO valuation_runs(symbol, as_of, assumptions_json, result_json, source_hash) VALUES (?, ?, ?, ?, ?)""", (normalize_ts_code(symbol) if symbol != "custom" else symbol, str(analysis.get("as_of") or ""), json.dumps(manifest, ensure_ascii=False, allow_nan=False), json.dumps(valuation_result, ensure_ascii=False, allow_nan=False), source_hash))
                run_id = int(cursor.lastrowid)
        return {"run_id": run_id, "analysis": analysis, "assumptions": manifest, "source": source, "source_hash": source_hash}

    def run_bank_valuation_workbench(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run PB and residual-income scenarios without invoking an AI model."""

        symbol = str(payload.get("symbol") or "").strip()
        if symbol:
            company, source = self.company_financial_data(
                symbol,
                as_of=str(payload.get("as_of") or "").strip() or None,
                refresh=bool(payload.get("refresh", False)),
                industry_hint=str(payload.get("industry") or "银行") or "银行",
            )
        else:
            company = payload.get("company")
            if not isinstance(company, dict):
                raise ValueError("银行估值工作台需要股票代码或标准化银行数据")
            source = {"provider": "request_payload"}
            symbol = str((company.get("company") or {}).get("symbol") or "custom")
        assumptions = payload.get("assumptions")
        if not isinstance(assumptions, dict):
            raise ValueError("缺少银行估值假设")
        assumptions_as_of = str(assumptions.get("assumptions_as_of") or "").strip()
        raw_scenarios = assumptions.get("scenarios")
        if not isinstance(raw_scenarios, list) or not 1 <= len(raw_scenarios) <= 5:
            raise ValueError("银行估值情景必须为 1 到 5 组")
        margin = _finite_number(assumptions.get("margin_of_safety", 0.2), "安全边际", minimum=0, maximum=0.8)
        scenarios: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_scenarios):
            if not isinstance(raw, dict):
                raise ValueError("每个银行估值情景必须是对象")
            name = str(raw.get("name") or f"scenario_{index + 1}").strip()[:40]
            target_pb = _finite_number(raw.get("target_pb"), f"{name}目标 PB", minimum=0.01, maximum=20)
            roe = _finite_number(raw.get("roe"), f"{name}ROE", minimum=-0.5, maximum=1.0)
            cost = _finite_number(raw.get("cost_of_equity"), f"{name}权益成本", minimum=0.001, maximum=1.0)
            growth = _finite_number(raw.get("book_value_growth"), f"{name}净资产增速", minimum=-0.5, maximum=1.0)
            terminal = _finite_number(raw.get("terminal_growth"), f"{name}永续增长", minimum=-0.2, maximum=0.2)
            years = _integer_number(raw.get("forecast_years", 5), f"{name}预测年数", minimum=1, maximum=15)
            if cost <= terminal:
                raise ValueError(f"{name}：权益成本必须高于永续增长率")
            scenarios.append({"name": name, "target_pb": target_pb, "roe": roe, "cost_of_equity": cost, "book_value_growth": growth, "terminal_growth": terminal, "forecast_years": years})
        manifest = {"schema_version": "bank-valuation-workbench/1.0", "margin_of_safety": margin, "scenarios": scenarios, "assumptions_as_of": assumptions_as_of or None}
        configured = json.loads(json.dumps(company, ensure_ascii=False))
        valuation = dict(configured.get("valuation") or {})
        valuation["company_type"] = "2"
        valuation["bank"] = {
            "pb_scenarios": [{"name": item["name"], "target_pb": item["target_pb"]} for item in scenarios],
            "residual_income_scenarios": [{key: value for key, value in item.items() if key != "target_pb"} for item in scenarios],
        }
        configured["valuation"] = valuation
        analysis = analyze_company(configured)
        support = analysis.get("support") if isinstance(analysis.get("support"), dict) else {}
        if support.get("status") != "supported_bank":
            raise ValueError("当前公司不是已支持的银行模型，拒绝套用银行估值")
        valuation_result = analysis.get("valuation") if isinstance(analysis.get("valuation"), dict) else {}

        def values(method: str) -> list[float]:
            block = valuation_result.get(method) if isinstance(valuation_result.get(method), dict) else {}
            rows = block.get("scenarios") if isinstance(block, dict) else []
            return [float(row["value_per_share"]) for row in rows if isinstance(row, dict) and row.get("status") == "ok" and isinstance(row.get("value_per_share"), (int, float)) and math.isfinite(float(row["value_per_share"]))]

        method_ranges = []
        for method, label in (("pb", "PB 情景"), ("residual_income", "剩余收益")):
            method_values = values(method)
            if method_values:
                low, high = min(method_values), max(method_values)
                method_ranges.append({"method": method, "label": label, "value_range_low": low, "value_range_high": high, "margin_of_safety": margin, "margin_price_low": low * (1 - margin), "margin_price_high": high * (1 - margin)})
        market = analysis.get("market") if isinstance(analysis.get("market"), dict) else {}
        latest_bank = ((analysis.get("bank") or {}).get("latest") or {}) if isinstance(analysis.get("bank"), dict) else {}
        latest_metrics = latest_bank.get("metrics") if isinstance(latest_bank, dict) else {}
        bvps = latest_metrics.get("book_value_per_share") if isinstance(latest_metrics, dict) else {}
        date_context = {
            "as_of_request": str(payload.get("as_of") or (company.get("provider") or {}).get("as_of") or source.get("as_of_request") or "") or None,
            "financial_period_end": analysis.get("as_of"),
            "price_as_of": market.get("price_as_of"),
            "shares_as_of": (company.get("valuation") or {}).get("shares_as_of") if isinstance(company.get("valuation"), dict) else None,
            "assumptions_as_of": assumptions_as_of or None,
        }
        date_context.update(_valuation_date_audit(
            company,
            payload,
            source,
            required_dates=date_context,
            required_keys=("financial_period_end", "price_as_of", "shares_as_of", "assumptions_as_of"),
        ))
        valuation_result["workbench_summary"] = {"method_ranges": method_ranges, "date_context": date_context, "policy": "PB 与剩余收益保持独立区间，不平均成单点目标价。"}
        manifest["reproduction"] = _valuation_reproduction(company, configured, source, date_context)
        source_hash = hashlib.sha256(json.dumps(company, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        run_id = None
        if payload.get("save", True):
            with self.connect() as conn:
                cursor = conn.execute(
                    """INSERT INTO valuation_runs(symbol, as_of, assumptions_json, result_json, source_hash)
                       VALUES (?, ?, ?, ?, ?)""",
                    (normalize_ts_code(symbol) if symbol != "custom" else symbol, str(analysis.get("as_of") or ""), json.dumps(manifest, ensure_ascii=False, allow_nan=False), json.dumps(valuation_result, ensure_ascii=False, allow_nan=False), source_hash),
                )
                run_id = int(cursor.lastrowid)
        return {"run_id": run_id, "analysis": analysis, "assumptions": manifest, "source": source, "source_hash": source_hash}

    def replay_valuation_run(self, run_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM valuation_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("估值记录不存在")
        assumptions = json.loads(row["assumptions_json"])
        reproduction = assumptions.get("reproduction") or {}
        configured = reproduction.get("configured_company")
        if not isinstance(configured, dict):
            return {"run_id": run_id, "status": "unavailable", "reason": "旧记录未保存计算输入，无法重算"}
        original = reproduction.get("original_company")
        digest = hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if digest != row["source_hash"]:
            raise ValueError("归档原始数据哈希不匹配，停止复核")
        configured_digest = hashlib.sha256(json.dumps(configured, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if not reproduction.get("configured_hash"):
            return {"run_id": run_id, "status": "unavailable", "reason": "旧记录缺少计算输入校验值，无法验证输入完整性"}
        if configured_digest != reproduction["configured_hash"]:
            raise ValueError("归档计算输入哈希不匹配，停止复核")
        current_hashes = _valuation_reproduction(original, configured, {}, {})["engine_hashes"]
        same_engine = current_hashes == reproduction.get("engine_hashes")
        recalculated = analyze_company(configured).get("valuation") or {}
        archived = json.loads(row["result_json"])
        comparable = {key: value for key, value in archived.items() if key != "workbench_summary"}
        identical = recalculated == comparable
        differences: list[dict[str, Any]] = []
        def compare_values(before: Any, after: Any, path: str) -> None:
            if before == after or len(differences) >= 100:
                return
            if isinstance(before, dict) and isinstance(after, dict):
                for key in sorted(set(before) | set(after)):
                    compare_values(before.get(key), after.get(key), f"{path}.{key}")
            elif isinstance(before, list) and isinstance(after, list):
                for index in range(max(len(before), len(after))):
                    compare_values(before[index] if index < len(before) else None, after[index] if index < len(after) else None, f"{path}[{index}]")
            else:
                differences.append({"path": path, "archived": before, "recalculated": after})
        compare_values(comparable, recalculated, "valuation")
        return {
            "run_id": run_id,
            "status": "verified" if identical and same_engine else "version_changed" if not same_engine else "mismatch",
            "same_engine": same_engine, "identical_result": identical,
            "differences": differences, "difference_limit": 100,
            "archived_result": archived, "recalculated_result": recalculated,
            "policy": "使用归档输入与当前公式复核，保留原历史记录。",
        }

    def list_valuation_runs(self, symbol: str | None = None, limit: int = 30, *, include_inputs: bool = True) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with self.connect() as conn:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM valuation_runs WHERE symbol=? ORDER BY id DESC LIMIT ?",
                    (normalize_ts_code(symbol), safe_limit),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM valuation_runs ORDER BY id DESC LIMIT ?", (safe_limit,)).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["assumptions"] = json.loads(item.pop("assumptions_json"))
            reproduction = item["assumptions"].get("reproduction") or {}
            item["replay_available"] = bool(reproduction.get("configured_company") and reproduction.get("configured_hash"))
            if not include_inputs:
                item["assumptions"].pop("reproduction", None)
            item["result"] = json.loads(item.pop("result_json"))
            results.append(item)
        return results

    def refresh_portfolio_financials(self) -> dict[str, Any]:
        """Refresh each saved holding explicitly, then return its local diagnosis."""
        with self.connect() as conn:
            holdings = [dict(row) for row in conn.execute("SELECT * FROM holdings ORDER BY id")]
        refreshed: list[str] = []
        errors: list[dict[str, str]] = []
        for holding in holdings:
            symbol = str(holding.get("symbol") or "").strip()
            try:
                _, source = self.company_financial_data(symbol, refresh=True)
                if source.get("cache") == "stale":
                    errors.append({"symbol": symbol, "error": str(source.get("warning") or "只能使用旧快照")})
                else:
                    refreshed.append(symbol)
            except (ValueError, RuntimeError) as exc:
                errors.append({"symbol": symbol, "error": str(exc)})
        return {"refreshed": refreshed, "errors": errors, "diagnostics": self.portfolio_diagnostics(holdings)}

    def list_reports(self, *, symbol: str | None = None, limit: int = 30, include_content: bool = True) -> list[dict[str, Any]]:
        """Return saved AI reports with their generated narrative attached.

        The dashboard intentionally keeps only report metadata so its initial
        payload stays small. The archive endpoint is the explicit place where
        a user asks for the stored narrative and its request metadata.
        """

        safe_limit = max(1, min(int(limit), 100))
        clauses: list[str] = []
        params: list[Any] = []
        if symbol:
            clauses.append("r.symbol=?")
            params.append(str(symbol).strip())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT r.id, r.symbol, r.title, r.provider, r.evidence_hash, r.snapshot_json,
                    r.review_status, r.review_due_at, r.payload_json, r.created_at,
                    rr.expected_conditions, rr.actual_outcome, rr.reviewer_note, rr.actual_as_of
                    FROM reports r LEFT JOIN report_reviews rr ON rr.report_id=r.id
                    {where} ORDER BY r.id DESC LIMIT ?""",
                (*params, safe_limit),
            ).fetchall()
        reports: list[dict[str, Any]] = []
        for row in rows:
            try:
                stored = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                stored = {}
            result = stored.get("result") if isinstance(stored, dict) else {}
            request = stored.get("request") if isinstance(stored, dict) else {}
            result = result if isinstance(result, dict) else {}
            request = request if isinstance(request, dict) else {}
            try:
                snapshot_schema = (json.loads(row["snapshot_json"]) if row["snapshot_json"] else {}).get("schema_version", "legacy-report")
            except (TypeError, json.JSONDecodeError):
                snapshot_schema = "invalid-report"
            reminder = _review_reminder(str(row["review_status"] or "unreviewed"), row["review_due_at"])
            reports.append(
                {
                    "id": row["id"],
                    "symbol": row["symbol"],
                    "title": row["title"],
                    "provider": row["provider"],
                    "model": result.get("model") or request.get("model") or "",
                    "content": str(result.get("content") or "") if include_content else "",
                    "doctrine_coverage": result.get("doctrine_coverage"),
                    "evidence_hash": row["evidence_hash"],
                    "review_status": row["review_status"],
                    "review_due_at": row["review_due_at"],
                    "expected_conditions": row["expected_conditions"] or "",
                    "actual_outcome": row["actual_outcome"] or "",
                    "reviewer_note": row["reviewer_note"] or "",
                    "actual_as_of": row["actual_as_of"] or "",
                    "reminder": reminder,
                    "snapshot_schema": snapshot_schema,
                    "created_at": row["created_at"],
                    "request": {
                        "knowledge_query": request.get("knowledge_query") or "",
                        "use_tushare": bool(request.get("use_tushare")),
                    },
                }
            )
        return reports

    def list_report_reminders(self, *, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        horizon = (date.today() + timedelta(days=7)).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT r.id, r.symbol, r.title, r.review_status, r.review_due_at,
                   rr.expected_conditions
                   FROM reports r LEFT JOIN report_reviews rr ON rr.report_id=r.id
                   WHERE r.review_status='open' AND r.review_due_at IS NOT NULL
                     AND date(r.review_due_at) <= date(?)
                   ORDER BY date(r.review_due_at), r.id
                   LIMIT ?""",
                (horizon, safe_limit),
            ).fetchall()
        return [
            {
                "report_id": row["id"],
                "symbol": row["symbol"],
                "title": row["title"],
                "due_at": row["review_due_at"],
                "expected_conditions": row["expected_conditions"] or "",
                "reminder": _review_reminder(row["review_status"], row["review_due_at"]),
            }
            for row in rows
        ]

    def get_report_narrative(self, report_id: int) -> dict[str, Any] | None:
        """Return only the user-visible narrative for a selected archive row."""
        with self.connect() as conn:
            row = conn.execute("SELECT id, payload_json FROM reports WHERE id=?", (int(report_id),)).fetchone()
        if row is None:
            return None
        try:
            stored = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            stored = {}
        result = stored.get("result") if isinstance(stored, dict) else {}
        result = result if isinstance(result, dict) else {}
        return {
            "id": row["id"],
            "content": str(result.get("content") or ""),
            "doctrine_coverage": result.get("doctrine_coverage"),
        }

    def get_report_snapshot(self, report_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM reports WHERE id=?", (int(report_id),)).fetchone()
            if row is None:
                return None
            review = conn.execute("SELECT * FROM report_reviews WHERE report_id=?", (int(report_id),)).fetchone()
        try:
            stored = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            stored = {}
        try:
            evidence = json.loads(row["evidence_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            evidence = {}
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            snapshot = {}
        result = stored.get("result") if isinstance(stored, dict) else {}
        request = stored.get("request") if isinstance(stored, dict) else {}
        integrity = _report_snapshot_integrity(row, evidence, snapshot)
        return {
            "id": row["id"],
            "symbol": row["symbol"],
            "title": row["title"],
            "provider": row["provider"],
            "evidence_hash": row["evidence_hash"],
            "created_at": row["created_at"],
            "request": request if isinstance(request, dict) else {},
            "result": result if isinstance(result, dict) else {},
            "evidence": evidence if isinstance(evidence, dict) else {},
            "snapshot": snapshot if isinstance(snapshot, dict) else {},
            "integrity": integrity,
            "review": dict(review) if review else None,
            "replayable": integrity["status"] == "verified",
        }

    def get_report_integrity(self, report_id: int) -> dict[str, Any] | None:
        """Return a small verification summary without exposing the evidence bundle."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, symbol, created_at, evidence_hash, evidence_json, snapshot_json FROM reports WHERE id=?",
                (int(report_id),),
            ).fetchone()
        if row is None:
            return None
        try:
            evidence = json.loads(row["evidence_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            evidence = {}
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            snapshot = {}
        integrity = _report_snapshot_integrity(row, evidence, snapshot)
        return {
            "id": row["id"],
            "symbol": row["symbol"],
            "created_at": row["created_at"],
            "snapshot_schema": snapshot.get("schema_version") or "legacy-report",
            "integrity": integrity,
            "replayable": integrity["status"] == "verified",
            "payload_policy": "仅返回完整性摘要；证据、提示词和模型输入不会随此接口发送到浏览器。",
        }

    def compare_reports(self, left_id: int, right_id: int) -> dict[str, Any]:
        left = self.get_report_snapshot(left_id)
        right = self.get_report_snapshot(right_id)
        if left is None or right is None:
            raise KeyError("需要两个有效的报告编号")
        if left["symbol"] != right["symbol"]:
            raise ValueError("只能比较同一股票的报告")

        def metric_map(report: dict[str, Any]) -> dict[str, Any]:
            financial = report.get("evidence", {}).get("financial_analysis", {})
            periods = financial.get("periods", {}) if isinstance(financial, dict) else {}
            ttm = periods.get("ttm", {}) if isinstance(periods, dict) else {}
            metrics = ttm.get("metrics", {}) if isinstance(ttm, dict) else {}
            result: dict[str, Any] = {}
            if isinstance(metrics, dict):
                for name, item in metrics.items():
                    if isinstance(item, dict) and "value" in item:
                        result[str(name)] = item.get("value")
            return result

        left_metrics = metric_map(left)
        right_metrics = metric_map(right)
        portfolio_report = str(left.get("evidence", {}).get("schema_version") or "").startswith("portfolio-evidence/")
        if portfolio_report:
            def portfolio_metric_map(report: dict[str, Any]) -> dict[str, Any]:
                portfolio = report.get("evidence", {}).get("portfolio", {})
                coverage = portfolio.get("coverage", {}) if isinstance(portfolio, dict) else {}
                concentration = portfolio.get("concentration", {}) if isinstance(portfolio, dict) else {}
                decisions = portfolio.get("decision_summary", {}) if isinstance(portfolio, dict) else {}
                return {
                    "组合市值（已计价）": portfolio.get("total_value_for_diagnostics"),
                    "持仓数量": coverage.get("positions"),
                    "已计价持仓": coverage.get("priced_positions"),
                    "有财务快照持仓": coverage.get("financial_positions"),
                    "已分类行业持仓": coverage.get("industry_classified_positions"),
                    "最大单股权重": concentration.get("top_position_weight"),
                    "集中度 HHI": concentration.get("hhi"),
                    "减仓复核数量": decisions.get("immediate_review"),
                    "等待条件数量": decisions.get("conditional_review"),
                    "持有观察数量": decisions.get("observe"),
                }
            left_metrics = portfolio_metric_map(left)
            right_metrics = portfolio_metric_map(right)
        metric_changes = []
        for name in sorted(set(left_metrics) | set(right_metrics)):
            before, after = left_metrics.get(name), right_metrics.get(name)
            if before != after:
                metric_changes.append({"metric": name, "before": before, "after": after})

        def rule_versions(report: dict[str, Any]) -> dict[str, int]:
            rules = report.get("evidence", {}).get("approved_doctrine_rules", [])
            return {
                str(item.get("id")): int(item.get("current_version") or 1)
                for item in rules if isinstance(item, dict) and item.get("id") is not None
            }

        left_rules, right_rules = rule_versions(left), rule_versions(right)
        rule_changes = []
        for card_id in sorted(set(left_rules) | set(right_rules), key=str):
            if left_rules.get(card_id) != right_rules.get(card_id):
                rule_changes.append({"card_id": card_id, "before": left_rules.get(card_id), "after": right_rules.get(card_id)})

        def signal_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
            financial = report.get("evidence", {}).get("financial_analysis", {})
            changes = financial.get("changes", {}) if isinstance(financial, dict) else {}
            signals = changes.get("signals", []) if isinstance(changes, dict) else []
            return {
                str(item.get("id")): {
                    "severity": item.get("severity"),
                    "title": item.get("title") or item.get("id"),
                    "detail": item.get("detail"),
                    "review_condition": item.get("review_condition"),
                }
                for item in signals
                if isinstance(item, dict) and item.get("id")
            }

        left_signals, right_signals = signal_map(left), signal_map(right)
        portfolio_position_changes: list[dict[str, Any]] = []
        if portfolio_report:
            def portfolio_positions(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
                items = report.get("evidence", {}).get("positions", [])
                return {
                    str(item.get("symbol")): {
                        "name": item.get("name"),
                        "weight": item.get("weight"),
                        "current_price": item.get("current_price"),
                        "price_as_of": item.get("price_as_of"),
                        "return_pct": item.get("return_pct"),
                        "financial_as_of": item.get("financial_as_of"),
                        "financial_period_status": item.get("financial_period_status"),
                        "risk_counts": item.get("risk_counts"),
                        "decision": item.get("decision"),
                    }
                    for item in items if isinstance(item, dict) and item.get("symbol")
                }
            left_positions, right_positions = portfolio_positions(left), portfolio_positions(right)
            for symbol in sorted(set(left_positions) | set(right_positions)):
                before, after = left_positions.get(symbol), right_positions.get(symbol)
                if before == after:
                    continue
                change = "added" if before is None else "removed" if after is None else "changed"
                portfolio_position_changes.append({"symbol": symbol, "change": change, "before": before, "after": after})
                before_decision = before.get("decision", {}) if isinstance(before, dict) else {}
                after_decision = after.get("decision", {}) if isinstance(after, dict) else {}
                if change != "changed" or before_decision != after_decision or (before or {}).get("risk_counts") != (after or {}).get("risk_counts"):
                    left_signals[f"portfolio:{symbol}"] = {
                        "severity": before_decision.get("priority"),
                        "title": f"{(before or {}).get('name') or symbol} · 组合决策",
                        "detail": "；".join(before_decision.get("reasons") or [])[:800],
                        "review_condition": "；".join(before_decision.get("review_conditions") or [])[:800],
                    } if before else None
                    right_signals[f"portfolio:{symbol}"] = {
                        "severity": after_decision.get("priority"),
                        "title": f"{(after or {}).get('name') or symbol} · 组合决策",
                        "detail": "；".join(after_decision.get("reasons") or [])[:800],
                        "review_condition": "；".join(after_decision.get("review_conditions") or [])[:800],
                    } if after else None
        risk_signal_changes: list[dict[str, Any]] = []
        for signal_id in sorted(set(left_signals) | set(right_signals)):
            before, after = left_signals.get(signal_id), right_signals.get(signal_id)
            if before == after:
                continue
            change = "added" if before is None else "removed" if after is None else "changed"
            risk_signal_changes.append({
                "signal_id": signal_id,
                "change": change,
                "before": before,
                "after": after,
            })

        def report_context(report: dict[str, Any]) -> dict[str, Any]:
            snapshot = report.get("snapshot", {})
            source = report.get("evidence", {}).get("financial_source", {})
            portfolio = report.get("evidence", {}).get("portfolio", {})
            coverage = portfolio.get("coverage", {}) if isinstance(portfolio, dict) else {}
            return {
                "financial_as_of": snapshot.get("financial_as_of"),
                "price_as_of": snapshot.get("price_as_of"),
                "source_provider": source.get("provider") if isinstance(source, dict) else None,
                "source_mode": source.get("mode") if isinstance(source, dict) else None,
                "source_cache": source.get("cache") if isinstance(source, dict) else None,
                "model": snapshot.get("model"),
                "ai_provider": snapshot.get("provider"),
                "position_count": coverage.get("positions"),
                "priced_positions": coverage.get("priced_positions"),
                "financial_positions": coverage.get("financial_positions"),
            }

        def review_context(report: dict[str, Any]) -> dict[str, Any] | None:
            review = report.get("review")
            if not isinstance(review, dict):
                return None
            return {
                "status": review.get("status"),
                "expected_conditions": review.get("expected_conditions"),
                "due_at": review.get("due_at"),
                "actual_outcome": review.get("actual_outcome"),
                "actual_as_of": review.get("actual_as_of"),
                "reviewer_note": review.get("reviewer_note"),
            }

        left_snapshot, right_snapshot = left.get("snapshot", {}), right.get("snapshot", {})
        left_context, right_context = report_context(left), report_context(right)
        left_content = str(left.get("result", {}).get("content") or "")
        right_content = str(right.get("result", {}).get("content") or "")
        return {
            "schema_version": "report-diff/3",
            "report_kind": "portfolio" if portfolio_report else "company",
            "symbol": left["symbol"],
            "before": {"id": left["id"], "created_at": left["created_at"], "as_of": left_snapshot.get("as_of_request")},
            "after": {"id": right["id"], "created_at": right["created_at"], "as_of": right_snapshot.get("as_of_request")},
            "metric_changes": metric_changes,
            "rule_changes": rule_changes,
            "risk_signal_changes": risk_signal_changes,
            "portfolio_position_changes": portfolio_position_changes,
            "data_context": {"before": left_context, "after": right_context},
            "data_context_changed": left_context != right_context,
            "judgment_follow_up": {
                "before_report": review_context(left),
                "after_report": review_context(right),
            },
            "narrative_changed": hashlib.sha256(left_content.encode("utf-8")).hexdigest()
                != hashlib.sha256(right_content.encode("utf-8")).hexdigest(),
            "doctrine_output_changed": left.get("result", {}).get("doctrine_coverage")
                != right.get("result", {}).get("doctrine_coverage"),
            "evidence_changed": left["evidence_hash"] != right["evidence_hash"],
            "model_changed": left_snapshot.get("model") != right_snapshot.get("model") or left_snapshot.get("provider") != right_snapshot.get("provider"),
            "replayable": bool(left["replayable"] and right["replayable"]),
            "warning": ("组合差异只比较两次保存时冻结的持仓、仓位、价格日期、财务覆盖、风险计数与条件化决策；不代表实时账户或交易指令。" if portfolio_report else "差异只比较两次保存时冻结的证据、确定性指标、风险信号与人工复盘；不能把不同截至日的报告当成实时行情，也不能由后续结果反向改写当时判断。"),
        }

    def update_report_review(self, report_id: int, values: dict[str, Any]) -> dict[str, Any]:
        report_id = int(report_id)
        if self.get_report_snapshot(report_id) is None:
            raise KeyError("报告不存在")
        status = str(values.get("status") or "open").strip().lower()
        if status not in {"open", "reviewed", "invalidated"}:
            raise ValueError("复核状态只能是 open、reviewed 或 invalidated")
        due_at = str(values.get("due_at") or "").strip() or None
        actual_as_of = str(values.get("actual_as_of") or "").strip()
        if due_at:
            try:
                date.fromisoformat(due_at)
            except ValueError as exc:
                raise ValueError("下次复核日期必须是 YYYY-MM-DD") from exc
        if actual_as_of:
            try:
                date.fromisoformat(actual_as_of)
            except ValueError as exc:
                raise ValueError("实际结果日期必须是 YYYY-MM-DD") from exc
        expected_conditions = str(values.get("expected_conditions") or "").strip()
        actual_outcome = str(values.get("actual_outcome") or "").strip()
        reviewer_note = str(values.get("reviewer_note") or "").strip()
        if status == "open" and (not expected_conditions or not due_at):
            raise ValueError("保存待验证条件时必须填写验证条件和复核日期")
        if status in {"reviewed", "invalidated"} and not (actual_outcome or reviewer_note):
            raise ValueError("完成复盘时必须填写实际结果或复盘笔记")
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO report_reviews(report_id, status, expected_conditions, actual_outcome, reviewer_note, actual_as_of, due_at, reviewed_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, CASE WHEN ?='reviewed' THEN CURRENT_TIMESTAMP ELSE NULL END, CURRENT_TIMESTAMP)
                   ON CONFLICT(report_id) DO UPDATE SET status=excluded.status,
                     expected_conditions=excluded.expected_conditions, actual_outcome=excluded.actual_outcome,
                     reviewer_note=excluded.reviewer_note, actual_as_of=excluded.actual_as_of,
                     due_at=excluded.due_at, reviewed_at=excluded.reviewed_at, updated_at=CURRENT_TIMESTAMP""",
                (
                    report_id,
                    status,
                    expected_conditions,
                    actual_outcome,
                    reviewer_note,
                    actual_as_of,
                    due_at,
                    status,
                ),
            )
            conn.execute("UPDATE reports SET review_status=?, review_due_at=? WHERE id=?", (status, due_at, report_id))
            row = conn.execute("SELECT * FROM report_reviews WHERE report_id=?", (report_id,)).fetchone()
        result = dict(row)
        result["reminder"] = _review_reminder(result["status"], result["due_at"])
        return result

    def create_backup(self, destination: str | None = None) -> dict[str, Any]:
        backup_dir = self.db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        if destination:
            target = Path(destination).expanduser().resolve()
            if target.parent != backup_dir.resolve() and backup_dir.resolve() not in target.parents:
                raise ValueError("备份文件只能保存到应用 data/backups 目录内")
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            target = backup_dir / f"investment_brain_{stamp}.db"
        if target.suffix.lower() != ".db":
            raise ValueError("备份文件必须使用 .db 扩展名")
        if target.exists():
            raise ValueError("备份文件已存在，禁止覆盖；请使用新的文件名")
        source = sqlite3.connect(str(self.db_path))
        target_conn = sqlite3.connect(str(target))
        try:
            source.backup(target_conn)
            target_conn.commit()
            integrity = str(target_conn.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            target_conn.close()
            source.close()
        digest = hashlib.sha256()
        with target.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return {
            "path": str(target),
            "file_name": target.name,
            "size_bytes": target.stat().st_size,
            "sha256": digest.hexdigest(),
            "integrity": integrity,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "recoverable": integrity == "ok",
            "notice": "这是只读备份副本；恢复前请关闭应用并保留当前数据库。",
        }

    def list_backups(self) -> list[dict[str, Any]]:
        backup_dir = self.db_path.parent / "backups"
        if not backup_dir.exists():
            return []
        items = []
        for path in sorted(backup_dir.glob("*.db"), key=lambda item: item.stat().st_mtime, reverse=True)[:50]:
            items.append({
                "file_name": path.name,
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            })
        return items

    def restore_backup(self, source: str, *, confirm: bool = False) -> dict[str, Any]:
        """Restore a validated backup after creating a safety copy.

        This is intentionally an explicit, user-confirmed operation.  The
        source must be inside ``data/backups``; the candidate database is
        integrity-checked and copied to a temporary file before replacing the
        active database.  A current-database safety backup is returned so a
        mistaken restore remains recoverable.
        """

        if not confirm:
            raise ValueError("恢复数据库前必须明确确认；当前数据库会先自动备份")
        backup_dir = (self.db_path.parent / "backups").resolve()
        source_path = Path(source).expanduser().resolve()
        if source_path.parent != backup_dir and backup_dir not in source_path.parents:
            raise ValueError("只能从应用 data/backups 目录恢复数据库")
        if source_path.suffix.lower() != ".db" or not source_path.is_file():
            raise ValueError("恢复源必须是存在的 .db 备份文件")
        candidate = self.db_path.parent / f".restore_candidate_{os.getpid()}_{int(time.time() * 1000)}.db"
        source_conn = sqlite3.connect(str(source_path))
        candidate_conn = sqlite3.connect(str(candidate))
        try:
            integrity = source_conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError(f"备份完整性检查失败：{integrity}")
            source_conn.backup(candidate_conn)
            candidate_conn.commit()
        finally:
            candidate_conn.close()
            source_conn.close()
        safety = self.create_backup()
        restore_source = sqlite3.connect(str(candidate))
        target_conn = sqlite3.connect(str(self.db_path))
        try:
            # SQLite's backup API works on Windows even when the active file
            # has recently been opened by the application; os.replace does
            # not, and would leave a misleading half-restored state.
            restore_source.backup(target_conn)
            target_conn.commit()
        except Exception as exc:
            raise RuntimeError(f"恢复写入失败；当前数据库安全备份仍然保留：{exc}") from exc
        finally:
            target_conn.close()
            restore_source.close()
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass
        init_database(self.db_path)
        return {
            "restored_from": str(source_path),
            "safety_backup": safety,
            "integrity": integrity,
            "notice": "恢复已完成；请刷新页面，后续数据将以恢复时点为准。",
        }

    def list_watchlist(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute("SELECT id, symbol, name, note, created_at, updated_at FROM watchlist ORDER BY updated_at DESC, id DESC")]
            for row in rows:
                ts_code = normalize_ts_code(row["symbol"])
                filing_review = filing_review_summary(self.db_path, row["symbol"])
                snapshot = conn.execute(
                    """SELECT payload_json, provider, retrieved_at FROM financial_snapshots
                       WHERE symbol=? AND as_of_request='' ORDER BY id DESC LIMIT 1""",
                    (ts_code,),
                ).fetchone()
                if not snapshot:
                    row["quote"] = None
                    row["quote_status"] = "not_loaded"
                    row["research"] = self._watchlist_research_state(row["symbol"], None, filing_review)
                    continue
                try:
                    payload = json.loads(snapshot["payload_json"])
                except (TypeError, json.JSONDecodeError):
                    row["quote"] = None
                    row["quote_status"] = "invalid_snapshot"
                    row["research"] = self._watchlist_research_state(row["symbol"], None, filing_review, invalid_snapshot=True)
                    continue
                valuation = payload.get("valuation") if isinstance(payload, dict) else {}
                valuation = valuation if isinstance(valuation, dict) else {}
                row["quote"] = {
                    "market_price": _optional_positive_number(valuation.get("market_price"), "观察清单行情价格"),
                    "market_cap": valuation.get("market_cap"),
                    "pe": valuation.get("pe"),
                    "pb": valuation.get("pb"),
                    "as_of": payload.get("as_of") if isinstance(payload, dict) else None,
                    "retrieved_at": snapshot["retrieved_at"],
                    "provider": snapshot["provider"],
                }
                row["quote_status"] = "stale" if _snapshot_age_hours(snapshot["retrieved_at"]) > 24 else "ready"
                row["research"] = self._watchlist_research_state(row["symbol"], payload, filing_review)
            return rows

    def _watchlist_research_state(
        self,
        symbol: str,
        company_payload: dict[str, Any] | None,
        filing_review: dict[str, Any],
        *,
        invalid_snapshot: bool = False,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        conditions: list[str] = []
        priority = "green"
        analysis: dict[str, Any] | None = None
        period_freshness = _financial_period_freshness(None, available=False)
        red_flags: list[dict[str, Any]] = []
        yellow_flags: list[dict[str, Any]] = []
        red_changes: list[dict[str, Any]] = []
        yellow_changes: list[dict[str, Any]] = []
        if company_payload is None:
            priority = "yellow"
            reasons.append("财务快照损坏，无法执行确定性分析" if invalid_snapshot else "尚无真实财务快照")
            conditions.append("取得来源可追溯的真实财务快照后重新计算")
        else:
            try:
                enriched = self._enrich_with_confirmed_filing_facts(company_payload, normalize_ts_code(symbol))
                analysis = analyze_company(enriched)
                period_freshness = _financial_period_freshness(analysis.get("as_of"), available=True)
                if period_freshness["status"] == "future":
                    priority = "yellow"
                    reasons.append(f"财务报告期 {analysis.get('as_of') or '缺失'} 晚于今天，已停止使用其风险信号")
                    conditions.append("取得报告期不晚于今天的真实财务快照后重新计算")
                else:
                    flags = analysis.get("flags") if isinstance(analysis.get("flags"), list) else []
                    changes = analysis.get("changes") if isinstance(analysis.get("changes"), dict) else {}
                    signals = changes.get("signals") if isinstance(changes.get("signals"), list) else []
                    red_flags = [item for item in flags if isinstance(item, dict) and item.get("severity") == "red"]
                    yellow_flags = [item for item in flags if isinstance(item, dict) and item.get("severity") == "yellow"]
                    red_changes = [item for item in signals if isinstance(item, dict) and item.get("severity") == "red"]
                    yellow_changes = [item for item in signals if isinstance(item, dict) and item.get("severity") == "yellow"]
                    if red_flags or red_changes:
                        priority = "red"
                    elif yellow_flags or yellow_changes:
                        priority = "yellow"
                    reasons.extend(str(item.get("title") or item.get("id") or "财务信号") for item in (red_changes + red_flags + yellow_changes + yellow_flags)[:3])
                    conditions.extend(
                        str(item.get("review_condition") or "").strip()
                        for item in (red_changes + yellow_changes)
                        if str(item.get("review_condition") or "").strip()
                    )
                    if period_freshness["status"] == "stale":
                        if priority == "green":
                            priority = "yellow"
                        reasons.append(f"财务数据截至 {analysis.get('as_of') or '缺失'}，已超过 {FINANCIAL_PERIOD_STALE_DAYS} 天")
                        conditions.append(f"取得最近 {FINANCIAL_PERIOD_STALE_DAYS} 天内的同口径财务报告后重新判断")
                    elif period_freshness["status"] == "unknown":
                        if priority == "green":
                            priority = "yellow"
                        reasons.append("财务快照缺少有效报告期")
                        conditions.append("补齐有效财务报告期后重新计算")
            except (TypeError, ValueError, json.JSONDecodeError):
                priority = "yellow"
                reasons.append("财务快照无法通过确定性模型校验")
                conditions.append("修复或重新获取财务快照后再判断")
                analysis = None

        confirmed_red = int(filing_review.get("confirmed_red") or 0)
        pending = int(filing_review.get("pending_evidence") or 0)
        unsearchable = int(filing_review.get("unsearchable") or 0)
        if confirmed_red:
            priority = "red"
            reasons.append(f"有 {confirmed_red} 条人工确认的高优先公告证据")
            conditions.append("逐条核对公告影响及风险是否已经消除或充分计价")
        if pending:
            if priority == "green":
                priority = "yellow"
            reasons.append(f"有 {pending} 条公告关键词线索待人工复核，当前不按事实处理")
            conditions.append("待复核公告线索全部完成确认或排除")
        if unsearchable:
            if priority == "green":
                priority = "yellow"
            reasons.append(f"有 {unsearchable} 份公告不可完整检索，不能认定没有风险")
            conditions.append("完成相关公告全文 OCR 或人工原件复核")
        blocking = confirmed_red or pending or unsearchable or period_freshness["status"] in {"missing", "unknown", "stale", "future"}
        return {
            "status": "等待条件" if blocking or priority == "red" else "继续观察",
            "priority": priority,
            "financial_as_of": analysis.get("as_of") if isinstance(analysis, dict) else None,
            "financial_period": period_freshness,
            "risk": {
                "red_flags": len(red_flags), "yellow_flags": len(yellow_flags),
                "red_changes": len(red_changes), "yellow_changes": len(yellow_changes),
            },
            "filing_review": filing_review,
            "reasons": list(dict.fromkeys(reasons))[:5] or ["当前确定性阈值未触发红黄旗，但不代表风险不存在"],
            "review_conditions": list(dict.fromkeys(conditions))[:5],
            "execution_policy": "观察清单只记录等待与复核条件，不构成建仓建议。",
        }

    def refresh_watchlist(self, symbol: str | None = None) -> dict[str, Any]:
        targets = self.list_watchlist()
        if symbol:
            normalized = str(symbol).strip().upper().split(".", 1)[0]
            targets = [item for item in targets if item["symbol"] == normalized]
        if not targets:
            return {"items": self.list_watchlist(), "errors": []}
        refreshed = []
        errors = []
        for item in targets:
            try:
                _, source = self.company_financial_data(item["symbol"], refresh=True)
                if source.get("cache") == "stale":
                    errors.append({"symbol": item["symbol"], "error": source.get("warning") or "只能使用本地旧快照"})
                else:
                    refreshed.append(item["symbol"])
            except (ValueError, RuntimeError) as exc:
                errors.append({"symbol": item["symbol"], "error": str(exc)})
        return {"items": self.list_watchlist(), "refreshed": refreshed, "errors": errors}

    def sync_critical_filings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Incrementally discover and archive critical CNINFO filings.

        Cursor advancement is conservative: a failed or truncated run retains
        the previous cursor so the next explicit sync retries the same window.
        """

        raw_symbols = payload.get("symbols")
        symbols: list[str] = []
        if isinstance(raw_symbols, list):
            candidates = raw_symbols
        elif payload.get("symbol"):
            candidates = [payload.get("symbol")]
        else:
            with self.connect() as conn:
                candidates = [
                    row[0]
                    for row in conn.execute(
                        "SELECT symbol FROM holdings UNION SELECT symbol FROM watchlist ORDER BY symbol"
                    ).fetchall()
                ]
        for candidate in candidates:
            code = normalize_ts_code(str(candidate or "")).split(".", 1)[0]
            if code not in symbols:
                symbols.append(code)
        if not symbols:
            raise ValueError("持仓和观察清单里还没有可同步的股票")
        if len(symbols) > 8:
            raise ValueError("单次最多同步 8 只股票，请分批执行")
        try:
            max_docs = int(payload.get("max_documents_per_symbol") or 6)
        except (TypeError, ValueError) as exc:
            raise ValueError("单只股票归档上限必须是整数") from exc
        if not 1 <= max_docs <= 15:
            raise ValueError("单只股票单次归档上限必须在 1 到 15 之间")
        try:
            retry_count = int(payload.get("retry_count", 2))
        except (TypeError, ValueError) as exc:
            raise ValueError("公告同步重试次数必须是整数") from exc
        if not 0 <= retry_count <= 2:
            raise ValueError("公告同步重试次数必须在 0 到 2 之间")

        def retry_runtime(operation: Any) -> tuple[Any, int]:
            for attempt in range(retry_count + 1):
                try:
                    return operation(), attempt
                except RuntimeError as exc:
                    if attempt >= retry_count:
                        setattr(exc, "retry_attempts", attempt)
                        raise
                    time.sleep(0.15 * (2 ** attempt))
            raise RuntimeError("公告同步重试状态异常")  # pragma: no cover

        until_date = str(payload.get("until") or date.today().isoformat())
        explicit_since = str(payload.get("since") or "").strip()
        results: list[dict[str, Any]] = []
        total_archived = 0
        total_duplicates = 0
        total_failed = 0
        total_retry_attempts = 0
        for symbol in symbols:
            state = get_filing_sync_state(self.db_path, symbol)
            if explicit_since:
                since_date = explicit_since
            elif state and state.get("cursor_date"):
                try:
                    cursor = date.fromisoformat(str(state["cursor_date"]))
                    since_date = (cursor - timedelta(days=2)).isoformat()
                except ValueError:
                    since_date = (date.fromisoformat(until_date) - timedelta(days=400)).isoformat()
            else:
                since_date = (date.fromisoformat(until_date) - timedelta(days=400)).isoformat()
            run_id = start_filing_sync_run(
                self.db_path, symbol, since_date=since_date, until_date=until_date
            )
            archived = 0
            duplicates = 0
            errors: list[dict[str, Any]] = []
            discovered = 0
            last_disclosed_at = ""
            pending_more = False
            retry_attempts = 0
            try:
                announcements, search_retries = retry_runtime(
                    lambda: search_critical_filings(
                        symbol, since=since_date, until=until_date, limit=180
                    )
                )
                retry_attempts += search_retries
                discovered = len(announcements)
                if announcements:
                    last_disclosed_at = max(
                        str(item.get("disclosed_at") or "") for item in announcements
                    )
                new_items: list[dict[str, Any]] = []
                for item in announcements:
                    if filing_exists(
                        self.db_path,
                        source_provider=str(item.get("source_provider") or "cninfo_public"),
                        source_notice_id=str(item.get("source_notice_id") or ""),
                    ):
                        duplicates += 1
                    else:
                        new_items.append(item)
                pending_more = len(new_items) > max_docs
                for item in new_items[:max_docs]:
                    try:
                        result, archive_retries = retry_runtime(
                            lambda item=item: archive_filing(
                                self.db_path,
                                self.filings_dir,
                                symbol=str(item["symbol"]),
                                company_name=str(item.get("company_name") or ""),
                                title=str(item["title"]),
                                filing_type=str(item.get("filing_type") or "other"),
                                report_period=str(item.get("report_period") or ""),
                                disclosed_at=str(item.get("disclosed_at") or ""),
                                source_provider=str(item.get("source_provider") or "cninfo_public"),
                                source_notice_id=str(item.get("source_notice_id") or ""),
                                source_url=str(item.get("source_url") or ""),
                                is_correction=bool(item.get("is_correction", False)),
                                metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else None,
                            )
                        )
                        retry_attempts += archive_retries
                        if result.get("duplicate"):
                            duplicates += 1
                        else:
                            archived += 1
                    except (ValueError, RuntimeError) as exc:
                        retry_attempts += int(getattr(exc, "retry_attempts", 0))
                        errors.append(
                            {
                                "notice_id": item.get("source_notice_id"),
                                "title": item.get("title"),
                                "error": str(exc),
                            }
                        )
            except (ValueError, RuntimeError) as exc:
                retry_attempts += int(getattr(exc, "retry_attempts", 0))
                errors.append({"error": str(exc)})
            if errors and discovered == 0:
                status = "error"
            elif errors or pending_more:
                status = "partial"
            else:
                status = "success"
            updated_state = finish_filing_sync_run(
                self.db_path,
                run_id,
                status=status,
                discovered=discovered,
                archived=archived,
                duplicates=duplicates,
                errors=errors,
                cursor_date=until_date if status == "success" else None,
                last_disclosed_at=last_disclosed_at,
            )
            total_archived += archived
            total_duplicates += duplicates
            total_failed += len(errors)
            total_retry_attempts += retry_attempts
            results.append(
                {
                    "symbol": symbol,
                    "status": status,
                    "since": since_date,
                    "until": until_date,
                    "discovered": discovered,
                    "archived": archived,
                    "duplicates": duplicates,
                    "remaining": max(0, discovered - duplicates - archived),
                    "retry_attempts": retry_attempts,
                    "errors": errors,
                    "state": updated_state,
                }
            )
        return {
            "symbols": len(symbols),
            "archived": total_archived,
            "duplicates": total_duplicates,
            "failed": total_failed,
            "retry_attempts": total_retry_attempts,
            "results": results,
            "sync_states": list_filing_sync_states(self.db_path),
        }

    def save_watch(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = str(payload.get("symbol", "")).strip().upper()
        if "." in symbol:
            symbol = symbol.split(".", 1)[0]
        if len(symbol) != 6 or not symbol.isdigit():
            raise ValueError("观察清单中的股票代码必须是 6 位数字")
        name = str(payload.get("name", "")).strip()
        note = str(payload.get("note", "")).strip()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO watchlist(symbol, name, note) VALUES (?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, note=excluded.note, updated_at=CURRENT_TIMESTAMP""",
                (symbol, name, note),
            )
            row = conn.execute("SELECT id, symbol, name, note, created_at, updated_at FROM watchlist WHERE symbol=?", (symbol,)).fetchone()
        return dict(row)

    def delete_watch(self, symbol: str) -> None:
        normalized = str(symbol or "").strip().upper().split(".", 1)[0]
        with self.connect() as conn:
            conn.execute("DELETE FROM watchlist WHERE symbol=?", (normalized,))

    def delete_holding(self, symbol: str) -> None:
        normalized = str(symbol or "").strip().upper().split(".", 1)[0]
        if len(normalized) != 6 or not normalized.isdigit():
            raise ValueError("持仓股票代码必须是 6 位数字")
        with self.connect() as conn:
            conn.execute("DELETE FROM holdings WHERE symbol=?", (normalized,))

    def save_holding(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = str(payload.get("symbol", "")).strip().upper().split(".", 1)[0]
        if len(symbol) != 6 or not symbol.isdigit():
            raise ValueError("持仓股票代码必须是 6 位数字")
        quantity = _finite_number(0 if payload.get("quantity") in (None, "") else payload["quantity"], "持仓数量", minimum=0)
        cost = _finite_number(0 if payload.get("cost") in (None, "") else payload["cost"], "成本价", minimum=0)
        current_price = _optional_finite_number(payload.get("current_price"), "当前价", minimum=0)
        if current_price is not None and current_price <= 0:
            raise ValueError("当前价必须大于 0；未知价格请留空")
        current_price_as_of = str(payload.get("current_price_as_of") or "").strip()
        if current_price_as_of:
            try:
                parsed_price_date = date.fromisoformat(current_price_as_of)
            except ValueError as exc:
                raise ValueError("当前价日期必须是 YYYY-MM-DD") from exc
            if parsed_price_date > date.today():
                raise ValueError("当前价日期不能晚于今天")
        if current_price is None:
            current_price_as_of = ""
        max_loss_pct = _optional_finite_number(payload.get("max_loss_pct"), "最大承受亏损", minimum=0, maximum=100)
        values = (
            symbol,
            str(payload.get("name", "")).strip(),
            quantity,
            cost,
            current_price,
            current_price_as_of,
            str(payload.get("industry", "")).strip(),
            str(payload.get("thesis", "")).strip(),
            str(payload.get("horizon", "")).strip(),
            max_loss_pct,
        )
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO holdings
                (symbol, name, quantity, cost, current_price, current_price_as_of, industry, thesis, horizon, max_loss_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                  name=excluded.name, quantity=excluded.quantity, cost=excluded.cost,
                  current_price=excluded.current_price, current_price_as_of=excluded.current_price_as_of,
                  industry=excluded.industry, thesis=excluded.thesis,
                  horizon=excluded.horizon, max_loss_pct=excluded.max_loss_pct,
                  updated_at=CURRENT_TIMESTAMP""",
                values,
            )
            row = conn.execute("SELECT * FROM holdings WHERE symbol=?", (symbol,)).fetchone()
        return dict(row)

    def import_holdings_csv(self, text: str) -> dict[str, Any]:
        raw = str(text or "").lstrip("\ufeff")
        if not raw.strip():
            raise ValueError("CSV 内容不能为空")
        reader = csv.DictReader(io.StringIO(raw))
        if not reader.fieldnames:
            raise ValueError("CSV 第一行必须是字段名")
        aliases = {
            "代码": "symbol", "股票代码": "symbol", "证券代码": "symbol",
            "名称": "name", "股票名称": "name",
            "行业": "industry", "所属行业": "industry",
            "数量": "quantity", "持仓数量": "quantity",
            "成本": "cost", "成本价": "cost", "买入价": "cost",
            "当前价": "current_price", "现价": "current_price",
            "当前价日期": "current_price_as_of", "价格日期": "current_price_as_of",
            "最大承受亏损": "max_loss_pct", "最大亏损%": "max_loss_pct",
            "持有周期": "horizon", "买入逻辑": "thesis", "投资逻辑": "thesis",
        }
        fields = [aliases.get(str(field).strip(), str(field).strip().lower()) for field in reader.fieldnames]
        if "symbol" not in fields:
            raise ValueError("CSV 必须包含 symbol 或 代码 列")
        imported = []
        errors = []
        for line_no, values in enumerate(reader, start=2):
            payload = {fields[index]: value for index, value in enumerate(values.values()) if index < len(fields)}
            if not str(payload.get("symbol") or "").strip():
                continue
            try:
                imported.append(self.save_holding(payload))
            except (TypeError, ValueError) as exc:
                errors.append({"line": line_no, "symbol": str(payload.get("symbol") or ""), "error": str(exc)})
        return {"imported": len(imported), "errors": errors, "holdings": imported}

    def export_holdings_csv(self) -> dict[str, Any]:
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute("SELECT symbol, name, industry, quantity, cost, current_price, current_price_as_of, max_loss_pct, horizon, thesis FROM holdings ORDER BY symbol")]
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["代码", "名称", "行业", "数量", "成本价", "当前价", "当前价日期", "最大承受亏损", "持有周期", "买入逻辑"])
        for row in rows:
            writer.writerow([row.get("symbol", ""), row.get("name", ""), row.get("industry", ""), row.get("quantity", ""), row.get("cost", ""), row.get("current_price", ""), row.get("current_price_as_of", ""), row.get("max_loss_pct", ""), row.get("horizon", ""), row.get("thesis", "")])
        return {"file_name": f"holdings_{time.strftime('%Y%m%d_%H%M%S')}.csv", "csv": output.getvalue(), "count": len(rows)}

    def data_health(self) -> dict[str, Any]:
        with self.connect() as conn:
            snapshot = conn.execute(
                """SELECT COUNT(*) AS count, MAX(retrieved_at) AS latest
                   FROM financial_snapshots WHERE as_of_request=''"""
            ).fetchone()
            snapshot_companies = conn.execute(
                """SELECT symbol, MAX(retrieved_at) AS latest
                   FROM financial_snapshots WHERE as_of_request='' GROUP BY symbol"""
            ).fetchall()
            historical_snapshot = conn.execute(
                """SELECT COUNT(*) AS count, COUNT(DISTINCT symbol) AS companies
                   FROM financial_snapshots WHERE as_of_request<>''"""
            ).fetchone()
            filing = conn.execute("SELECT COUNT(*) AS count, MAX(retrieved_at) AS latest FROM filings").fetchone()
            filing_coverage = filing_stats(conn)
            report_rows = conn.execute(
                "SELECT id, symbol, created_at, evidence_hash, evidence_json, snapshot_json FROM reports"
            ).fetchall()
            assigned = "(d.library_id IS NOT NULL OR EXISTS(SELECT 1 FROM library_documents ld WHERE ld.document_id=d.id))"
            enabled = """(EXISTS(SELECT 1 FROM libraries primary_library
                         WHERE primary_library.id=d.library_id AND primary_library.enabled=1)
                         OR EXISTS(SELECT 1 FROM library_documents enabled_ld
                         JOIN libraries enabled_library ON enabled_library.id=enabled_ld.library_id
                         WHERE enabled_ld.document_id=d.id AND enabled_library.enabled=1))"""
            active_ready = """(d.status='ready' AND
                            (d.is_current=1 OR EXISTS(SELECT 1 FROM documents active_dup
                             WHERE active_dup.duplicate_of_id=d.id AND active_dup.is_current=1)))"""
            docs = conn.execute(
                f"""SELECT
                    SUM(CASE WHEN {assigned} THEN 1 ELSE 0 END) AS trusted_documents,
                    SUM(CASE WHEN NOT {assigned} THEN 1 ELSE 0 END) AS quarantined_documents,
                    SUM(CASE WHEN {active_ready} AND {enabled} THEN 1 ELSE 0 END) AS eligible_documents,
                    SUM(CASE WHEN {active_ready} AND {assigned} AND NOT {enabled} THEN 1 ELSE 0 END) AS disabled_documents
                    FROM documents d"""
            ).fetchone()
            trusted_chunks = conn.execute(
                f"SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id=c.document_id WHERE {assigned}"
            ).fetchone()[0]
            quarantined_chunks = conn.execute(
                f"SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id=c.document_id WHERE NOT {assigned}"
            ).fetchone()[0]
            eligible_chunks = conn.execute(
                f"SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id=c.document_id WHERE {active_ready} AND {enabled}"
            ).fetchone()[0]
            disabled_chunks = conn.execute(
                f"SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id=c.document_id WHERE {active_ready} AND {assigned} AND NOT {enabled}"
            ).fetchone()[0]
        report_integrity_counts = {"verified": 0, "incomplete": 0, "corrupt": 0}
        for report_row in report_rows:
            try:
                report_evidence = json.loads(report_row["evidence_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                report_evidence = {}
            try:
                report_snapshot = json.loads(report_row["snapshot_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                report_snapshot = {}
            integrity_status = _report_snapshot_integrity(report_row, report_evidence, report_snapshot)["status"]
            report_integrity_counts[integrity_status] += 1
        def freshness(latest: Any, threshold_hours: float) -> dict[str, Any]:
            age = _snapshot_age_hours(latest)
            return {"latest": latest, "age_hours": None if age == float("inf") else round(age, 1), "status": "unknown" if age == float("inf") else ("fresh" if age <= threshold_hours else "stale"), "threshold_hours": threshold_hours}
        snapshot_ages = [_snapshot_age_hours(row["latest"]) for row in snapshot_companies]
        fresh_snapshot_companies = sum(age <= 24 * 7 for age in snapshot_ages)
        stale_snapshot_companies = sum(age != float("inf") and age > 24 * 7 for age in snapshot_ages)
        unknown_snapshot_companies = sum(age == float("inf") for age in snapshot_ages)
        if not snapshot_ages or unknown_snapshot_companies == len(snapshot_ages):
            snapshot_status = "unknown"
        elif fresh_snapshot_companies == len(snapshot_ages):
            snapshot_status = "fresh"
        elif fresh_snapshot_companies:
            snapshot_status = "mixed"
        else:
            snapshot_status = "stale"
        snapshot_freshness = freshness(snapshot["latest"], 24 * 7)
        snapshot_freshness["status"] = snapshot_status
        return {
            "providers": {
                "tushare": {"configured": tushare_is_configured()},
                "free": {"available": free_provider_available()},
            },
            "financial_snapshots": {
                "count": int(snapshot["count"] or 0),
                "companies": len(snapshot_ages),
                "fresh_companies": fresh_snapshot_companies,
                "stale_companies": stale_snapshot_companies,
                "unknown_companies": unknown_snapshot_companies,
                "historical_count": int(historical_snapshot["count"] or 0),
                "historical_companies": int(historical_snapshot["companies"] or 0),
                "policy": "当前诊断只使用无历史截止日的快照；带截止日快照仅供历史报告与复盘。",
                **snapshot_freshness,
            },
            "filings": {
                "count": int(filing["count"] or 0),
                "searchable": filing_coverage["searchable_filings"],
                "partially_searchable": filing_coverage["partially_searchable_filings"],
                "unsearchable": filing_coverage["unsearchable_filings"],
                "coverage_status": (
                    "none" if not filing_coverage["filings"] or not filing_coverage["searchable_filings"]
                    else "complete" if filing_coverage["searchable_filings"] == filing_coverage["filings"]
                    else "partial"
                ),
                **freshness(filing["latest"], 24 * 30),
            },
            "knowledge": {
                "documents": int(docs["trusted_documents"] or 0),
                "chunks": int(trusted_chunks or 0),
                "eligible_documents": int(docs["eligible_documents"] or 0),
                "eligible_chunks": int(eligible_chunks or 0),
                "disabled_documents": int(docs["disabled_documents"] or 0),
                "disabled_chunks": int(disabled_chunks or 0),
                "quarantined_documents": int(docs["quarantined_documents"] or 0),
                "quarantined_chunks": int(quarantined_chunks or 0),
                "policy": "只有已启用投资体系中的可用资料参与 AI 检索；停用流派和未归类资料均不进入分析。",
            },
            "reports": {
                "count": len(report_rows),
                "replayable": report_integrity_counts["verified"],
                "partial": report_integrity_counts["incomplete"],
                "corrupt": report_integrity_counts["corrupt"],
                "policy": "逐份校验证据哈希、精确模型输入和公式版本；格式正确但哈希失败的报告不会计入可复现。",
            },
            "policy": "陈旧或未知数据不会被描述为当前；缺失数据保持缺失，不补猜。",
        }

    def build_portfolio_evidence_bundle(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build a bounded portfolio-only evidence pack without demo substitution."""

        payload = payload or {}
        with self.connect() as conn:
            holdings = [dict(row) for row in conn.execute("SELECT * FROM holdings ORDER BY id")]
        if not holdings:
            raise ValueError("请先录入至少一项真实持仓，再生成组合深度报告")
        diagnostics = self.portfolio_diagnostics(holdings)
        holding_by_symbol = {str(item.get("symbol") or ""): item for item in holdings}
        positions: list[dict[str, Any]] = []
        for item in diagnostics.get("positions", [])[:20]:
            if not isinstance(item, dict):
                continue
            holding = holding_by_symbol.get(str(item.get("symbol") or ""), {})
            positions.append({
                "symbol": item.get("symbol"), "name": item.get("name"), "industry": item.get("industry"),
                "quantity": holding.get("quantity"), "cost": item.get("cost"), "current_price": item.get("price"),
                "price_as_of": item.get("price_as_of"), "weight": item.get("weight"), "return_pct": item.get("return_pct"),
                "max_loss_pct": item.get("max_loss_pct"), "thesis": item.get("thesis"), "horizon": holding.get("horizon"),
                "financial_data": item.get("financial_data"), "financial_as_of": item.get("data_as_of"),
                "financial_period_status": item.get("financial_period_status"), "snapshot_provider": item.get("snapshot_provider"),
                "snapshot_retrieved_at": item.get("snapshot_retrieved_at"), "snapshot_status": item.get("snapshot_status"),
                "data_quality_score": item.get("data_quality_score"),
                "key_metrics": {"cash_conversion": item.get("cash_conversion"), "return_on_invested_capital": item.get("return_on_invested_capital"), "debt_to_equity": item.get("debt_to_equity")},
                "risk_counts": {"financial_red": item.get("red_flags"), "financial_yellow": item.get("yellow_flags"), "change_red": item.get("red_changes"), "change_yellow": item.get("yellow_changes"), "confirmed_filing_red": item.get("confirmed_filing_red_flags"), "pending_filing_reviews": item.get("pending_filing_reviews"), "unsearchable_filings": item.get("unsearchable_filings")},
                "risk_titles": list(item.get("flag_titles") or [])[:5], "decision": item.get("decision"), "filing_review": item.get("filing_review"),
            })
        doctrine_rules = list_doctrine_cards(self.db_path, enabled_only=True)[:24]
        compact_rules = [{
            "id": card.get("id"), "library_id": card.get("library_id"), "library_name": card.get("library_name"),
            "title": card.get("title"), "content": str(card.get("content") or "")[:1200],
            "applicability": card.get("applicability"), "conditions": list(card.get("conditions") or [])[:6],
            "exceptions": list(card.get("exceptions") or [])[:6], "counter_evidence": list(card.get("counter_evidence") or [])[:6],
            "current_version": card.get("current_version") or 1, "source_locator": card.get("source_locator"),
        } for card in doctrine_rules]
        doctrine_by_library: list[dict[str, Any]] = []
        for card in compact_rules:
            library = next((item for item in doctrine_by_library if item["library_id"] == card["library_id"]), None)
            if library is None:
                library = {"library_id": card["library_id"], "library_name": card["library_name"], "rules": []}
                doctrine_by_library.append(library)
            library["rules"].append({"id": card["id"], "title": card["title"], "current_version": card["current_version"]})
        selected_ids = {int(card["id"]) for card in compact_rules if card.get("id") is not None}
        relationships = analyze_doctrine_relationships(self.db_path)
        relationships["relationships"] = [item for item in relationships.get("relationships", []) if int(item["left"]["card_id"]) in selected_ids and int(item["right"]["card_id"]) in selected_ids][:20]
        relationships["summary"] = {"enabled_rules": len(compact_rules), "consensus": sum(item.get("kind") == "consensus" for item in relationships["relationships"]), "conflicts": sum(item.get("kind") == "conflict" for item in relationships["relationships"])}
        return {
            "schema_version": "portfolio-evidence/1", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "portfolio": {"coverage": diagnostics.get("coverage"), "total_value_for_diagnostics": diagnostics.get("total_value_for_diagnostics"), "concentration": diagnostics.get("concentration"), "industries": diagnostics.get("industries"), "alerts": diagnostics.get("alerts"), "decision_summary": diagnostics.get("decision_summary")},
            "positions": positions, "omitted_positions": max(0, len(holdings) - len(positions)),
            "knowledge_query": str(payload.get("knowledge_query") or "组合风险 仓位 成本 安全边际 财务质量 失效条件"),
            "approved_doctrine_rules": compact_rules, "doctrine_by_library": doctrine_by_library, "doctrine_relationships": relationships,
            "evidence_policy": {
                "fact_priority": "持仓记录与法定披露事实 > 系统确定性计算 > 已审核知识库观点 > AI推断",
                "missing_data": "没有财务快照、价格日期、公告 OCR 或指标时必须明确缺失，禁止补猜或视为正常。",
                "filing_layers": "confirmed_filing_red 是人工确认事实；pending_filing_reviews 只是待复核线索；unsearchable_filings 表示无法证明没有风险。",
                "doctrine_separation": "每个启用投资体系必须独立形成结论，再列共识与冲突，禁止平均化。",
                "execution": "只输出继续观察、持有、等待条件或减仓复核及其触发条件；不自动交易，不生成下单指令。",
                "snapshot": "证据只读取当前本地持仓与缓存快照，不会在 AI 调用前隐式联网刷新。",
            },
        }

    def estimate_ai(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider = str(payload.get("provider", "codex"))
        prompt = str(payload.get("prompt", "")).strip()
        evidence = payload.get("evidence") or {}
        if not prompt:
            raise ValueError("分析问题不能为空")
        if provider not in {"codex", "api"}:
            raise ValueError("分析引擎只能是 codex 或 api")
        evidence_bytes = len(json.dumps(evidence, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if evidence_bytes > MAX_EVIDENCE_BYTES:
            raise ValueError(f"证据包过大（{evidence_bytes // 1024} KB）；请缩小检索范围后重试")
        settings = self.settings()
        model = "Codex 登录额度" if provider == "codex" else str(payload.get("model") or settings["api_model"] or "未配置模型")
        if provider == "api":
            model_input = {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _analysis_user_message(prompt, evidence)},
                ],
                "temperature": 0.2,
            }
        else:
            model_input = {"input": _analysis_prompt(prompt, evidence)}
        model_input_bytes = len(json.dumps(model_input, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        estimated_input = max(1, model_input_bytes // 3)
        estimated_output = 4_000
        total_estimate = estimated_input + estimated_output
        remaining = int(settings["monthly_tokens_remaining"])
        input_price = float(settings.get("api_input_price_per_million") or 0)
        output_price = float(settings.get("api_output_price_per_million") or 0)
        pricing_configured = provider == "api" and (input_price > 0 or output_price > 0)
        estimated_cost = (
            round((estimated_input * input_price + estimated_output * output_price) / 1_000_000, 6)
            if pricing_configured else None
        )
        fingerprint_source = {
            "provider": provider,
            "model": model,
            "prompt": prompt,
            "evidence": evidence,
            "monthly_tokens_remaining": remaining,
            "pricing": {
                "input_per_million": input_price,
                "output_per_million": output_price,
                "currency": str(settings.get("api_price_currency") or "CNY"),
            },
        }
        fingerprint = hashlib.sha256(json.dumps(
            _canonical_fingerprint_value(fingerprint_source),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        configuration_ready = provider == "codex" or bool(
            settings.get("api_base_url") and settings.get("api_model") and settings.get("api_key_configured")
        )
        return {
            "schema_version": "ai-cost-estimate/1",
            "provider": provider,
            "model": model,
            "evidence_bytes": evidence_bytes,
            "model_input_bytes": model_input_bytes,
            "estimated_input_tokens": estimated_input,
            "estimated_output_tokens": estimated_output,
            "estimated_total_tokens": total_estimate,
            "monthly_tokens_remaining": remaining,
            "monthly_tokens_after": max(0, remaining - total_estimate),
            "within_budget": total_estimate <= remaining,
            "configuration_ready": configuration_ready,
            "estimated_cost": estimated_cost,
            "currency": str(settings.get("api_price_currency") or "CNY"),
            "pricing_configured": pricing_configured,
            "cost_notice": (
                "Codex 使用本机登录额度，不产生兼容 API 账单；Token 数仍计入软件月度预算。"
                if provider == "codex" else (
                    "费用按你配置的每百万 Token 单价估算，实际账单以 API 服务商为准。"
                    if pricing_configured else "尚未配置 API 单价，只能预估 Token，不能可靠估算金额。"
                )
            ),
            "fingerprint": fingerprint,
        }

    def run_ai(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider = str(payload.get("provider", "codex"))
        prompt = str(payload.get("prompt", "")).strip()
        evidence = payload.get("evidence") or {}
        estimate = self.estimate_ai(payload)
        quoted_fingerprint = str(payload.get("estimate_fingerprint") or "")
        if quoted_fingerprint and quoted_fingerprint != estimate["fingerprint"]:
            raise ValueError("分析输入、额度或计费设置已变化，请重新预估 Token 与费用后再调用")
        if not estimate["within_budget"]:
            raise ValueError(f"本次预计用量约 {estimate['estimated_total_tokens']:,} Token，超过本月剩余额度 {estimate['monthly_tokens_remaining']:,}")
        if provider == "codex":
            result = self._run_codex(prompt, evidence)
        elif provider == "api":
            result = self._run_compatible_api(prompt, evidence, payload)
        else:
            raise ValueError("分析引擎只能是 codex 或 api")
        result["doctrine_coverage"] = _audit_doctrine_output(
            str(result.get("content") or ""), evidence
        )
        self._record_usage(provider, result, estimate["estimated_input_tokens"], estimate["estimated_output_tokens"])
        self._save_report(payload, result, evidence)
        return result

    def _record_usage(self, provider: str, result: dict[str, Any], estimated_input: int, estimated_output: int) -> None:
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        input_tokens = _usage_int(usage, "prompt_tokens", "input_tokens")
        output_tokens = _usage_int(usage, "completion_tokens", "output_tokens")
        exact = input_tokens is not None or output_tokens is not None
        if input_tokens is None:
            input_tokens = estimated_input
        if output_tokens is None:
            output_tokens = estimated_output
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO ai_usage(provider, model, period_month, input_tokens, output_tokens, estimated)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    provider,
                    str(result.get("model") or ""),
                    time.strftime("%Y-%m"),
                    input_tokens,
                    output_tokens,
                    0 if exact else 1,
                ),
            )

    def build_evidence_bundle(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Build the bounded, source-labelled payload consumed by either AI engine."""

        symbol = str(payload.get("symbol", "")).strip()
        as_of_raw = str(payload.get("as_of") or "").strip()
        filing_cutoff: str | None = None
        if as_of_raw:
            try:
                filing_cutoff = date.fromisoformat(as_of_raw).isoformat()
            except ValueError as exc:
                raise ValueError("分析截止日必须是 YYYY-MM-DD") from exc
            if filing_cutoff > date.today().isoformat():
                raise ValueError("分析截止日不能晚于今天")
        financial_source: dict[str, Any]
        supplied_company = payload.get("company")
        if isinstance(supplied_company, dict):
            company = supplied_company
            financial_source = {
                "provider": "request_payload",
                "mode": "user_supplied",
                "warning": "财务数据由本次请求直接提供，必须按用户输入事实标记并独立核验。",
            }
        elif symbol:
            company, financial_source = self.company_financial_data(
                symbol,
                as_of=str(payload.get("as_of") or "") or None,
                refresh=bool(payload.get("refresh_financials", False)),
                industry_hint=str(payload.get("industry") or "") or None,
            )
        else:
            company = demo_company()
            financial_source = {"provider": "fictional_demo", "demo": True}
        financial = _compact_financial_analysis_for_ai(analyze_company(company))
        query = str(payload.get("knowledge_query") or payload.get("question") or "盈利质量 现金流 资产负债 分红 安全边际").strip()
        try:
            knowledge_limit = max(1, min(int(payload.get("knowledge_limit", 12)), 30))
        except (TypeError, ValueError) as exc:
            raise ValueError("知识证据数量必须是整数") from exc
        knowledge = search_knowledge(
            self.db_path,
            query,
            knowledge_limit,
            trusted_only=True,
            enabled_libraries_only=True,
        ) if query else []
        available_filing_records = list_filings(
            self.db_path,
            symbol=symbol,
            disclosed_on_or_before=filing_cutoff,
            limit=100,
        ) if symbol else []
        filing_records = _select_filing_records_for_evidence(available_filing_records, limit=8)
        filing_evidence: list[dict[str, Any]] = []
        superseded_filing_evidence: list[dict[str, Any]] = []
        active_filing_evidence_total = 0
        active_superseded_evidence_total = 0
        current_categories_available: set[str] = set()
        superseded_categories_available: set[str] = set()
        filing_facts: list[dict[str, Any]] = []
        superseded_filing_facts: list[dict[str, Any]] = []
        for filing in filing_records:
            is_superseded = filing.get("superseded_by_id") is not None
            lineage = {
                "filing_status": "superseded" if is_superseded else "current",
                "superseded_by_id": filing.get("superseded_by_id"),
                "superseded_by_title": filing.get("superseded_by_title"),
                "supersedes_filing_id": filing.get("supersedes_filing_id"),
            }
            active_items = [
                item for item in list_filing_evidence(self.db_path, int(filing["id"]), limit=1000)
                if item.get("review_status") != "dismissed"
            ]
            if is_superseded:
                active_superseded_evidence_total += len(active_items)
                superseded_categories_available.update(str(item.get("category") or "") for item in active_items if item.get("category"))
            else:
                active_filing_evidence_total += len(active_items)
                current_categories_available.update(str(item.get("category") or "") for item in active_items if item.get("category"))
            for item in _balanced_filing_evidence(active_items, limit=20):
                evidence_item = {
                    **item,
                    **lineage,
                    "filing_title": filing["title"],
                    "disclosed_at": filing["disclosed_at"],
                    "source_url": filing["source_url"],
                    "artifact_sha256": filing["sha256"],
                }
                (superseded_filing_evidence if is_superseded else filing_evidence).append(evidence_item)
            for fact in list_filing_facts(self.db_path, int(filing["id"])):
                if fact.get("review_status") != "confirmed":
                    continue
                fact_item = {
                    **fact,
                    **lineage,
                    "filing_title": filing["title"],
                    "disclosed_at": filing["disclosed_at"],
                    "source_url": filing["source_url"],
                    "artifact_sha256": filing["sha256"],
                }
                (superseded_filing_facts if is_superseded else filing_facts).append(fact_item)
        doctrine_limit = max(1, min(int(payload.get("doctrine_limit", 16)), 50))
        approved_cards = list_doctrine_cards(self.db_path, enabled_only=True)
        query_terms = [term.lower() for term in query.split() if term]

        def doctrine_score(card: dict[str, Any]) -> int:
            searchable = " ".join(
                str(value)
                for value in (
                    card.get("title"),
                    card.get("content"),
                    card.get("category"),
                    card.get("applicability"),
                    card.get("tags"),
                    card.get("conditions"),
                    card.get("exceptions"),
                    card.get("industries"),
                    card.get("cycles"),
                    card.get("metrics"),
                    card.get("thresholds"),
                    card.get("counter_evidence"),
                )
            ).lower()
            return sum(1 for term in query_terms if term in searchable)

        ranked_cards = sorted(
            approved_cards,
            key=lambda card: (doctrine_score(card), int(card.get("id") or 0)),
            reverse=True,
        )
        doctrine_rules = [
            card for card in ranked_cards if doctrine_score(card) > 0
        ][:doctrine_limit]
        doctrine_by_library: list[dict[str, Any]] = []
        for card in doctrine_rules:
            library = next(
                (item for item in doctrine_by_library if item["library_id"] == card["library_id"]),
                None,
            )
            if library is None:
                library = {
                    "library_id": card["library_id"],
                    "library_name": card.get("library_name", ""),
                    "rules": [],
                }
                doctrine_by_library.append(library)
            library["rules"].append({
                "id": card.get("id"),
                "title": card.get("title"),
                "current_version": card.get("current_version") or 1,
            })
        selected_card_ids = {int(card["id"]) for card in doctrine_rules}
        doctrine_relationships = analyze_doctrine_relationships(self.db_path)
        doctrine_relationships["relationships"] = [
            item for item in doctrine_relationships["relationships"]
            if int(item["left"]["card_id"]) in selected_card_ids
            and int(item["right"]["card_id"]) in selected_card_ids
        ]
        doctrine_relationships["summary"] = {
            "enabled_rules": len(doctrine_rules),
            "consensus": sum(item["kind"] == "consensus" for item in doctrine_relationships["relationships"]),
            "conflicts": sum(item["kind"] == "conflict" for item in doctrine_relationships["relationships"]),
        }
        with self.connect() as conn:
            if symbol:
                holding_row = conn.execute("SELECT * FROM holdings WHERE symbol=?", (symbol,)).fetchone()
                holdings = [dict(holding_row)] if holding_row else []
            else:
                holdings = [dict(row) for row in conn.execute("SELECT * FROM holdings ORDER BY id")]
        source_mode = str(financial_source.get("mode") or "").lower()
        if source_mode == "free":
            source_warning = (
                "免费公开数据源是 provisional（可能延迟或缺少监管附注），但在公司代码与名称匹配时，"
                "允许用于方向性财务筛选；不得把它升级为法定披露事实，所有缺失和不确定性必须逐项列出。"
            )
        elif financial_source.get("demo"):
            source_warning = "当前未接入真实财报数据源时，financial_analysis 中的虚构演示公司不得被当成所请求股票的事实。"
        else:
            source_warning = "财务事实仍需结合法定披露原文核验；结构化数据不应替代公告和年报。"
        selected_current_evidence = _balanced_filing_evidence(filing_evidence, limit=60, group_by_filing=True)
        selected_superseded_evidence = _balanced_filing_evidence(
            superseded_filing_evidence, limit=60, group_by_filing=True
        )
        confirmed_filing_evidence = [
            item for item in selected_current_evidence if item.get("review_status") == "confirmed"
        ]
        pending_filing_leads = [
            item for item in selected_current_evidence if item.get("review_status") == "pending"
        ]
        superseded_confirmed_filing_evidence = [
            item for item in selected_superseded_evidence if item.get("review_status") == "confirmed"
        ]
        superseded_pending_filing_leads = [
            item for item in selected_superseded_evidence if item.get("review_status") == "pending"
        ]
        filing_selection = {
            "filings_available": len(available_filing_records),
            "filings_scanned": len(filing_records),
            "filings_omitted": max(0, len(available_filing_records) - len(filing_records)),
            "selected_filing_ids": [int(item["id"]) for item in filing_records],
            "selected_filing_types": [str(item.get("filing_type") or "other") for item in filing_records],
            "current_active_total": active_filing_evidence_total,
            "current_included": len(selected_current_evidence),
            "current_confirmed_included": len(confirmed_filing_evidence),
            "current_pending_included": len(pending_filing_leads),
            "current_omitted": max(0, active_filing_evidence_total - len(selected_current_evidence)),
            "superseded_active_total": active_superseded_evidence_total,
            "superseded_included": len(selected_superseded_evidence),
            "superseded_confirmed_included": len(superseded_confirmed_filing_evidence),
            "superseded_pending_included": len(superseded_pending_filing_leads),
            "superseded_omitted": max(0, active_superseded_evidence_total - len(selected_superseded_evidence)),
            "current_categories_available": sorted(current_categories_available),
            "current_categories_included": sorted({str(item.get("category") or "") for item in selected_current_evidence if item.get("category")}),
            "superseded_categories_available": sorted(superseded_categories_available),
            "superseded_categories_included": sorted({str(item.get("category") or "") for item in selected_superseded_evidence if item.get("category")}),
            "policy": "优先覆盖更正/问询、年报、审计、内控及最近定期报告；每份公告按风险类别轮询取证，再按公告+类别平衡总量。入选后将 confirmed 证据与 pending 待复核线索物理分层；未扫描公告和截断线索均显式披露。",
        }
        return {
            "schema_version": "evidence-bundle/0.4",
            "requested_symbol": symbol or None,
            "financial_analysis": financial,
            "financial_source": financial_source,
            "holdings": holdings,
            "knowledge_query": query,
            "knowledge_evidence": knowledge,
            "approved_doctrine_rules": doctrine_rules,
            "doctrine_by_library": doctrine_by_library,
            "doctrine_relationships": doctrine_relationships,
            "filing_records": filing_records,
            "filing_disclosure_cutoff": filing_cutoff,
            "filing_evidence_selection": filing_selection,
            "confirmed_filing_evidence": confirmed_filing_evidence,
            "pending_filing_leads": pending_filing_leads,
            "superseded_confirmed_filing_evidence": superseded_confirmed_filing_evidence,
            "superseded_pending_filing_leads": superseded_pending_filing_leads,
            "confirmed_filing_facts": filing_facts[:60],
            "superseded_filing_facts": superseded_filing_facts[:60],
            "evidence_policy": {
                "fact_priority": "法定披露事实 > 系统确定性计算 > 用户知识库观点 > AI推断",
                "doctrine_gate": "只有用户审核通过且当前启用的规则卡，才能作为分析尺子；原始检索片段仅作引用材料。",
                "doctrine_separation": "每个投资体系必须独立形成结论，再列共识与冲突；禁止把不同体系混成平均观点。",
                "filing_gate": "confirmed_filing_evidence 与 confirmed_filing_facts 才能作为人工确认事实；pending_filing_leads 只是检索命中，必须表述为待复核线索，严禁写成公司事实；dismissed 不进入分析证据包。已被更正公告替代的内容只进入 superseded_* 历史区，禁止作为当前事实。设置分析截止日时，只允许披露日不晚于截止日的公告；披露日未知的公告因无法证明当时可用而排除。",
                "warning": source_warning,
            },
        }

    def company_financial_data(
        self,
        symbol: str,
        *,
        as_of: str | None = None,
        refresh: bool = False,
        industry_hint: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        def apply_industry_hint(payload: dict[str, Any]) -> dict[str, Any]:
            hint = str(industry_hint or "").strip()
            if not hint:
                return payload
            adjusted = dict(payload)
            identity = dict(adjusted.get("company") or {})
            identity["industry"] = hint
            adjusted["company"] = identity
            if "银行" in hint or "bank" in hint.lower():
                valuation = dict(adjusted.get("valuation") or {})
                valuation["company_type"] = "2"
                adjusted["valuation"] = valuation
            return adjusted

        def finalize(payload: dict[str, Any], source: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
            enriched = self._enrich_with_confirmed_filing_facts(
                apply_industry_hint(payload),
                normalize_ts_code(symbol),
                disclosed_on_or_before=as_of,
            )
            overlay = (enriched.get("provider") or {}).get("confirmed_filing_facts")
            if isinstance(overlay, dict):
                source = dict(source)
                source["confirmed_filing_facts"] = overlay
            return enriched, source

        ts_code = normalize_ts_code(symbol)
        as_of_key = as_of or ""
        with self.connect() as conn:
            row = conn.execute(
                """SELECT payload_json, provider, retrieved_at,
                          (julianday('now') - julianday(retrieved_at)) * 24 AS age_hours
                   FROM financial_snapshots
                   WHERE symbol=? AND as_of_request=?
                   ORDER BY id DESC LIMIT 1""",
                (ts_code, as_of_key),
            ).fetchone()
        if row and not refresh and float(row["age_hours"] or 0) <= 12:
            cached_provider = str(row["provider"] or "unknown")
            return finalize(json.loads(row["payload_json"]), {
                "provider": cached_provider,
                "mode": _financial_provider_mode(cached_provider),
                "cache": "fresh",
                "retrieved_at": row["retrieved_at"],
                "as_of_request": as_of,
            })
        if not tushare_is_configured():
            if row and not refresh:
                cached_provider = str(row["provider"] or "unknown")
                return finalize(json.loads(row["payload_json"]), {
                    "provider": cached_provider,
                    "mode": _financial_provider_mode(cached_provider),
                    "cache": "stale",
                    "retrieved_at": row["retrieved_at"],
                    "as_of_request": as_of,
                    "warning": "未配置 TUSHARE_TOKEN，正在使用本地旧快照。",
                })
            try:
                canonical = apply_industry_hint(fetch_free_company_financials(ts_code, as_of=as_of))
            except FreeProviderError as exc:
                if row:
                    cached_provider = str(row["provider"] or "unknown")
                    return finalize(json.loads(row["payload_json"]), {
                        "provider": cached_provider,
                        "mode": _financial_provider_mode(cached_provider),
                        "cache": "stale",
                        "retrieved_at": row["retrieved_at"],
                        "as_of_request": as_of,
                        "warning": f"免费数据源更新失败，继续使用本地旧快照：{exc}",
                    })
                raise RuntimeError(f"免费数据源暂不可用：{exc}") from exc
            provider_name = str((canonical.get("provider") or {}).get("name") or "free").lower()
            with self.connect() as conn:
                conn.execute(
                    """INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json)
                       VALUES (?, ?, ?, ?)""",
                    (ts_code, as_of_key, provider_name, json.dumps(canonical, ensure_ascii=False)),
                )
            return finalize(canonical, {
                "provider": provider_name,
                "mode": "free",
                "cache": "refreshed",
                "retrieved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "as_of_request": as_of,
                "warnings": (canonical.get("provider") or {}).get("warnings", []),
            })
        canonical = apply_industry_hint(fetch_company_financials(ts_code, years=5, as_of=as_of))
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json)
                   VALUES (?, ?, 'tushare', ?)""",
                (ts_code, as_of_key, json.dumps(canonical, ensure_ascii=False)),
            )
        return finalize(canonical, {
            "provider": "tushare",
            "cache": "refreshed",
            "retrieved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "as_of_request": as_of,
        })

    def _enrich_with_confirmed_filing_facts(
        self,
        company: dict[str, Any],
        symbol: str,
        *,
        disclosed_on_or_before: str | None = None,
    ) -> dict[str, Any]:
        """Overlay reviewed statutory bank ratios without mutating provider snapshots.

        Only exact report-period matches are eligible. Conflicting confirmed filings,
        or a conflict with an existing explicit ratio, are isolated as missing rather
        than silently selecting either value.
        """

        enriched = json.loads(json.dumps(company, ensure_ascii=False))
        periods = enriched.get("periods")
        if not isinstance(periods, list):
            return enriched
        period_map = {
            str(item.get("end_date") or ""): item
            for item in periods
            if isinstance(item, dict) and item.get("end_date")
        }
        if not period_map:
            return enriched
        normalized = normalize_ts_code(symbol)
        plain = normalized.split(".", 1)[0]
        cutoff = str(disclosed_on_or_before or "").strip()
        if cutoff:
            try:
                cutoff = date.fromisoformat(cutoff).isoformat()
            except ValueError as exc:
                raise ValueError("公告披露截止日必须是 YYYY-MM-DD") from exc
            disclosure_filter = """
                     AND f.disclosed_at!='' AND substr(f.disclosed_at, 1, 10)<=?
                     AND NOT EXISTS (
                         SELECT 1 FROM filings correction
                         WHERE correction.supersedes_filing_id=f.id
                           AND correction.disclosed_at!=''
                           AND substr(correction.disclosed_at, 1, 10)<=?
                     )"""
            parameters: tuple[Any, ...] = (plain, normalized, cutoff, cutoff)
        else:
            disclosure_filter = """
                     AND NOT EXISTS (
                         SELECT 1 FROM filings correction
                         WHERE correction.supersedes_filing_id=f.id
                     )"""
            parameters = (plain, normalized)
        conn = self.connect()
        try:
            rows = conn.execute(
                f"""SELECT ff.*, f.symbol, f.title AS filing_title, f.source_url,
                          f.source_provider, f.disclosed_at, fa.sha256 AS artifact_sha256
                   FROM filing_facts ff
                   JOIN filings f ON f.id=ff.filing_id
                   JOIN filing_artifacts fa ON fa.id=f.artifact_id
                   WHERE ff.review_status='confirmed' AND f.symbol IN (?, ?)
                     {disclosure_filter}
                   ORDER BY f.disclosed_at DESC, ff.id DESC""",
                parameters,
            ).fetchall()
        finally:
            conn.close()
        grouped: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for row in rows:
            period_end = str(row["period_end"] or "")
            if period_end in period_map:
                grouped.setdefault((period_end, str(row["metric"])), []).append(row)

        applied: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        tolerance = 0.00005  # half a basis point in decimal-ratio form
        for (period_end, metric), candidates in grouped.items():
            period = period_map[period_end]
            facts = period.setdefault("facts", {})
            if not isinstance(facts, dict):
                continue
            values = [float(row["value"]) for row in candidates]
            if max(values) - min(values) > tolerance:
                facts.pop(metric, None)
                conflicts.append({
                    "period_end": period_end,
                    "metric": metric,
                    "reason": "confirmed_filings_disagree",
                    "candidates": [
                        {"fact_id": int(row["id"]), "filing_id": int(row["filing_id"]), "value": float(row["value"]), "locator": row["locator"]}
                        for row in candidates
                    ],
                })
                continue
            row = candidates[0]
            candidate_value = float(row["value"])
            existing = facts.get(metric)
            existing_value: float | None = None
            if isinstance(existing, dict) and existing.get("value") is not None:
                try:
                    existing_value = float(existing["value"])
                    if str(existing.get("unit") or "").strip().lower() in {"%", "percent", "percentage"}:
                        existing_value /= 100.0
                except (TypeError, ValueError):
                    existing_value = None
            if existing_value is not None and abs(existing_value - candidate_value) > tolerance:
                facts.pop(metric, None)
                conflicts.append({
                    "period_end": period_end,
                    "metric": metric,
                    "reason": "provider_and_confirmed_filing_disagree",
                    "provider_value": existing_value,
                    "confirmed_value": candidate_value,
                    "fact_id": int(row["id"]),
                    "filing_id": int(row["filing_id"]),
                    "locator": row["locator"],
                })
                continue
            source = {
                "provider": "confirmed_filing_fact",
                "filing_id": int(row["filing_id"]),
                "fact_id": int(row["id"]),
                "document": row["filing_title"],
                "url": row["source_url"] or None,
                "page": int(row["page_number"]),
                "locator": row["locator"],
                "artifact_sha256": row["artifact_sha256"],
                "extraction_method": row["extraction_method"],
                "reviewed_at": row["reviewed_at"],
            }
            facts[metric] = {
                "value": candidate_value,
                "unit": "ratio",
                "statement": "bank_regulatory_disclosure",
                "as_reported_label": row["label"],
                "source": source,
            }
            applied.append({"period_end": period_end, "metric": metric, **source})

        if applied or conflicts:
            provider = dict(enriched.get("provider") or {})
            provider["confirmed_filing_facts"] = {
                "policy": "only user-confirmed facts; exact period and disclosure-cutoff match; conflicts are not calculated",
                "disclosure_cutoff": cutoff or None,
                "applied_count": len(applied),
                "conflict_count": len(conflicts),
                "applied": applied,
                "conflicts": conflicts,
            }
            enriched["provider"] = provider
        return enriched

    def filing_facts_for_review(self, filing_id: int) -> list[dict[str, Any]]:
        """Return candidates with an honest calculation-link status for the UI."""

        facts = list_filing_facts(self.db_path, filing_id)
        conn = self.connect()
        try:
            filing = conn.execute(
                """SELECT f.symbol,
                          EXISTS(SELECT 1 FROM filings correction WHERE correction.supersedes_filing_id=f.id) AS superseded
                   FROM filings f WHERE f.id=?""",
                (int(filing_id),),
            ).fetchone()
            if filing is None:
                raise KeyError(f"公告不存在：{filing_id}")
            symbol = normalize_ts_code(str(filing["symbol"]))
            snapshot = conn.execute(
                """SELECT payload_json FROM financial_snapshots
                   WHERE symbol=? ORDER BY id DESC LIMIT 1""",
                (symbol,),
            ).fetchone()
        finally:
            conn.close()
        period_dates: set[str] = set()
        applied_keys: set[tuple[str, str]] = set()
        conflict_keys: set[tuple[str, str]] = set()
        if snapshot is not None:
            raw = json.loads(snapshot["payload_json"])
            period_dates = {
                str(item.get("end_date") or "")
                for item in raw.get("periods", [])
                if isinstance(item, dict)
            }
            enriched = self._enrich_with_confirmed_filing_facts(raw, symbol)
            overlay = (enriched.get("provider") or {}).get("confirmed_filing_facts") or {}
            applied_keys = {
                (str(item.get("period_end") or ""), str(item.get("metric") or ""))
                for item in overlay.get("applied", [])
                if isinstance(item, dict)
            }
            conflict_keys = {
                (str(item.get("period_end") or ""), str(item.get("metric") or ""))
                for item in overlay.get("conflicts", [])
                if isinstance(item, dict)
            }
        for fact in facts:
            status = str(fact.get("review_status") or "pending")
            key = (str(fact.get("period_end") or ""), str(fact.get("metric") or ""))
            if bool(filing["superseded"]):
                application_status = "superseded"
            elif status == "pending":
                application_status = "awaiting_review"
            elif status == "dismissed":
                application_status = "excluded"
            elif snapshot is None:
                application_status = "waiting_financial_snapshot"
            elif key[0] not in period_dates:
                application_status = "waiting_financial_period"
            elif key in conflict_keys:
                application_status = "conflict_isolated"
            elif key in applied_keys:
                application_status = "applied"
            else:
                application_status = "not_applied"
            fact["application_status"] = application_status
        return facts

    def import_bank_disclosures(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Append source-labelled bank regulatory facts as a new snapshot version."""

        symbol = normalize_ts_code(str(payload.get("symbol") or ""))
        period_end_raw = str(payload.get("period_end") or "").strip()
        try:
            period_end = date.fromisoformat(period_end_raw).isoformat()
        except ValueError as exc:
            raise ValueError("报告期必须为 YYYY-MM-DD") from exc
        values = payload.get("metrics")
        if not isinstance(values, dict) or not values:
            raise ValueError("至少补录一项银行披露指标")
        if len(values) > 20:
            raise ValueError("单次最多补录 20 项银行披露指标")
        source_document = str(payload.get("source_document") or "").strip()
        source_url = str(payload.get("source_url") or "").strip()
        page = str(payload.get("page") or "").strip()
        filing_id = payload.get("filing_id")
        if not source_document and not source_url and not filing_id:
            raise ValueError("必须填写报告名称、原文链接或已归档公告 ID")
        if source_url:
            parsed = urllib.parse.urlparse(source_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("原文链接必须是有效的 HTTP(S) 地址")

        ratio_labels = {
            "net_interest_margin": "净息差",
            "nonperforming_loan_ratio": "不良贷款率",
            "special_mention_ratio": "关注类贷款占比",
            "overdue_loan_ratio": "逾期贷款占比",
            "provision_coverage_ratio": "拨备覆盖率",
            "provision_loan_ratio": "拨贷比",
            "core_tier1_capital_ratio": "核心一级资本充足率",
            "tier1_capital_ratio": "一级资本充足率",
            "capital_adequacy_ratio": "资本充足率",
            "cost_income_ratio": "成本收入比",
            "return_on_equity": "加权平均净资产收益率",
        }
        amount_labels = {
            "nonperforming_loans": "不良贷款余额",
            "special_mention_loans": "关注类贷款余额",
            "overdue_loans": "逾期贷款余额",
            "loan_loss_provision": "贷款损失准备",
            "risk_weighted_assets": "风险加权资产",
            "core_tier1_capital": "核心一级资本净额",
        }
        allowed = set(ratio_labels) | set(amount_labels)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError("不支持的银行指标：" + "、".join(unknown))

        company, original_source = self.company_financial_data(
            symbol,
            as_of=str(payload.get("as_of") or "").strip() or None,
            refresh=False,
            industry_hint=str(payload.get("industry") or "银行"),
        )
        enriched = json.loads(json.dumps(company, ensure_ascii=False))
        periods = enriched.get("periods")
        if not isinstance(periods, list):
            raise ValueError("当前快照没有可补录的报告期")
        period = next((item for item in periods if isinstance(item, dict) and item.get("end_date") == period_end), None)
        if period is None:
            raise ValueError(f"当前快照不包含报告期 {period_end}")
        facts = period.setdefault("facts", {})
        if not isinstance(facts, dict):
            raise ValueError("当前报告期事实结构异常")
        imported: list[str] = []
        for name, raw_value in values.items():
            maximum = 10000 if name == "provision_coverage_ratio" else 1000
            numeric = _finite_number(raw_value, ratio_labels.get(name) or amount_labels.get(name) or name, minimum=0, maximum=maximum if name in ratio_labels else 1e18)
            previous = facts.get(name) if isinstance(facts.get(name), dict) else None
            source = {
                "provider": "manual_bank_disclosure",
                "document": source_document or None,
                "url": source_url or None,
                "page": page or None,
                "filing_id": filing_id,
                "report_end": period_end,
                "imported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "previous_fact": previous,
            }
            facts[name] = {
                "value": numeric,
                "unit": "%" if name in ratio_labels else "CNY million",
                "statement": "bank_regulatory_disclosure",
                "as_reported_label": ratio_labels.get(name) or amount_labels[name],
                "source": source,
            }
            imported.append(name)
        valuation = dict(enriched.get("valuation") or {})
        valuation["company_type"] = "2"
        enriched["valuation"] = valuation
        identity = dict(enriched.get("company") or {})
        identity["industry"] = str(payload.get("industry") or identity.get("industry") or "银行")
        enriched["company"] = identity
        provider = dict(enriched.get("provider") or {})
        provider["manual_bank_enrichment"] = {"period_end": period_end, "fields": imported, "source_document": source_document or None, "source_url": source_url or None, "page": page or None, "filing_id": filing_id}
        enriched["provider"] = provider
        as_of_key = str(payload.get("as_of") or "")
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json)
                   VALUES (?, ?, 'manual_bank_enrichment', ?)""",
                (symbol, as_of_key, json.dumps(enriched, ensure_ascii=False, allow_nan=False)),
            )
            snapshot_id = int(cursor.lastrowid)
        return {"snapshot_id": snapshot_id, "imported_fields": imported, "analysis": analyze_company(enriched), "source": {"provider": "manual_bank_enrichment", "base_snapshot": original_source}}

    def import_sector_disclosures(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Append source-linked industry disclosures as a new immutable snapshot."""

        symbol = normalize_ts_code(str(payload.get("symbol") or ""))
        template = str(payload.get("sector_template") or "").strip().lower()
        if template not in {"cyclical_resource", "utility", "real_estate", "insurance", "brokerage"}:
            raise ValueError("行业模板只能是 cyclical_resource、utility、real_estate、insurance 或 brokerage")
        period_end_raw = str(payload.get("period_end") or "").strip()
        try:
            period_end = date.fromisoformat(period_end_raw).isoformat()
        except ValueError as exc:
            raise ValueError("报告期必须为 YYYY-MM-DD") from exc
        values = payload.get("metrics")
        units = payload.get("units") or {}
        if not isinstance(values, dict) or not values:
            raise ValueError("至少补录一项行业经营指标")
        if not isinstance(units, dict):
            raise ValueError("指标单位必须是对象")
        source_document = str(payload.get("source_document") or "").strip()
        source_url = str(payload.get("source_url") or "").strip()
        page = str(payload.get("page") or "").strip()
        filing_id = payload.get("filing_id")
        if not source_document and not source_url and not filing_id:
            raise ValueError("必须填写报告名称、原文链接或已归档公告 ID")
        if source_url:
            parsed = urllib.parse.urlparse(source_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("原文链接必须是有效的 HTTP(S) 地址")
        resource_fields = {
            "production_volume": ("产量", None),
            "realized_price": ("平均实现价格", None),
            "unit_cash_cost": ("单位现金成本", None),
            "proven_probable_reserves": ("经济可采储量", None),
            "sustaining_capex": ("维持性资本开支", "CNY million"),
            "expansion_capex": ("扩张性资本开支", "CNY million"),
        }
        utility_fields = {
            "installed_capacity": ("控股装机容量", "MW"),
            "utilization_hours": ("平均利用小时", "hours"),
            "average_tariff": ("平均上网电价", "CNY/kWh"),
            "fuel_cost_per_kwh": ("单位燃料成本", "CNY/kWh"),
            "regulated_asset_base": ("受监管资产基数", "CNY million"),
            "allowed_return": ("准许收益率", "%"),
            "sustaining_capex": ("维持性资本开支", "CNY million"),
            "expansion_capex": ("扩张性资本开支", "CNY million"),
        }
        property_fields = {
            "contract_liabilities": ("合同负债", "CNY million"),
            "sales_collections": ("销售回款", "CNY million"),
            "presales": ("签约销售额", "CNY million"),
            "inventory": ("项目存货", "CNY million"),
            "project_inventory_impairment": ("项目存货减值", "CNY million"),
            "cash": ("货币资金", "CNY million"),
            "restricted_cash": ("受限资金", "CNY million"),
            "interest_bearing_debt": ("有息负债", "CNY million"),
            "debt_due_within_one_year": ("一年内到期有息负债", "CNY million"),
            "external_guarantees": ("对外担保余额", "CNY million"),
        }
        insurance_fields = {
            "insurance_service_revenue": ("保险服务收入", "CNY million"),
            "gross_written_premiums": ("原保险保费收入", "CNY million"),
            "new_business_value": ("新业务价值", "CNY million"),
            "embedded_value": ("内含价值", "CNY million"),
            "net_investment_yield": ("净投资收益率", "%"),
            "comprehensive_investment_yield": ("综合投资收益率", "%"),
            "insurance_contract_liabilities": ("保险合同负债", "CNY million"),
            "total_investment_assets": ("投资资产", "CNY million"),
            "core_solvency_ratio": ("核心偿付能力充足率", "%"),
            "comprehensive_solvency_ratio": ("综合偿付能力充足率", "%"),
        }
        brokerage_fields = {
            "brokerage_net_revenue": ("经纪业务手续费净收入", "CNY million"),
            "investment_banking_net_revenue": ("投行业务手续费净收入", "CNY million"),
            "asset_management_net_revenue": ("资管业务手续费净收入", "CNY million"),
            "net_interest_income": ("利息净收入", "CNY million"),
            "trading_investment_income": ("投资及公允价值收益", "CNY million"),
            "net_capital": ("净资本", "CNY million"),
            "risk_coverage_ratio": ("风险覆盖率", "%"),
            "capital_leverage_ratio": ("资本杠杆率", "%"),
            "liquidity_coverage_ratio": ("流动性覆盖率", "%"),
            "net_stable_funding_ratio": ("净稳定资金率", "%"),
        }
        field_groups = {"cyclical_resource": resource_fields, "utility": utility_fields, "real_estate": property_fields, "insurance": insurance_fields, "brokerage": brokerage_fields}
        fields = field_groups[template]
        unknown = sorted(set(values) - set(fields))
        if unknown:
            raise ValueError("不支持的行业指标：" + "、".join(unknown))
        default_industry = {"cyclical_resource": "周期资源", "utility": "公用事业", "real_estate": "房地产开发", "insurance": "保险", "brokerage": "证券"}[template]
        industry = str(payload.get("industry") or default_industry)
        company, original_source = self.company_financial_data(symbol, as_of=str(payload.get("as_of") or "").strip() or None, refresh=False, industry_hint=industry)
        enriched = json.loads(json.dumps(company, ensure_ascii=False))
        periods = enriched.get("periods")
        if not isinstance(periods, list):
            raise ValueError("当前快照没有可补录的报告期")
        period = next((item for item in periods if isinstance(item, dict) and item.get("end_date") == period_end), None)
        if period is None:
            raise ValueError(f"当前快照不包含报告期 {period_end}")
        facts = period.setdefault("facts", {})
        if not isinstance(facts, dict):
            raise ValueError("当前报告期事实结构异常")
        imported = []
        for name, raw_value in values.items():
            label, default_unit = fields[name]
            fact_unit = str(units.get(name) or default_unit or "").strip()
            if not fact_unit:
                raise ValueError(f"{label}必须填写单位")
            allow_negative = name in {"net_investment_yield", "comprehensive_investment_yield", "net_interest_income", "trading_investment_income"}
            numeric = _finite_number(raw_value, label, minimum=-1e18 if allow_negative else 0, maximum=1e18)
            previous = facts.get(name) if isinstance(facts.get(name), dict) else None
            facts[name] = {
                "value": numeric,
                "unit": fact_unit,
                "statement": "sector_operating_disclosure",
                "as_reported_label": label,
                "source": {"provider": "manual_sector_disclosure", "document": source_document or None, "url": source_url or None, "page": page or None, "filing_id": filing_id, "report_end": period_end, "imported_at": time.strftime("%Y-%m-%d %H:%M:%S"), "previous_fact": previous},
            }
            imported.append(name)
        valuation = dict(enriched.get("valuation") or {})
        valuation["sector_template"] = template
        enriched["valuation"] = valuation
        identity = dict(enriched.get("company") or {})
        identity["industry"] = industry
        enriched["company"] = identity
        provider = dict(enriched.get("provider") or {})
        provider["manual_sector_enrichment"] = {"template": template, "period_end": period_end, "fields": imported, "source_document": source_document or None, "source_url": source_url or None, "page": page or None, "filing_id": filing_id}
        enriched["provider"] = provider
        with self.connect() as conn:
            cursor = conn.execute("""INSERT INTO financial_snapshots(symbol, as_of_request, provider, payload_json) VALUES (?, ?, 'manual_sector_enrichment', ?)""", (symbol, str(payload.get("as_of") or ""), json.dumps(enriched, ensure_ascii=False, allow_nan=False)))
            snapshot_id = int(cursor.lastrowid)
        return {"snapshot_id": snapshot_id, "imported_fields": imported, "analysis": analyze_company(enriched), "source": {"provider": "manual_sector_enrichment", "base_snapshot": original_source}}

    def _save_report(self, request_payload: dict[str, Any], result: dict[str, Any], evidence: Any) -> None:
        encoded = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        symbol = str(request_payload.get("symbol") or "DEMO").strip()
        provider = str(result.get("provider") or request_payload.get("provider") or "unknown")
        title = str(request_payload.get("title") or f"{symbol} 财务研究")
        question = str(request_payload.get("prompt") or "").strip()
        evidence_hash = hashlib.sha256(encoded).hexdigest()
        approved_rules = evidence.get("approved_doctrine_rules", []) if isinstance(evidence, dict) else []
        rule_versions = [
            {
                "id": item.get("id"),
                "library_id": item.get("library_id"),
                "library_name": item.get("library_name"),
                "title": item.get("title"),
                "version": item.get("current_version") or 1,
            }
            for item in approved_rules if isinstance(item, dict)
        ]
        financial = evidence.get("financial_analysis", {}) if isinstance(evidence, dict) else {}
        portfolio_input = {
            "portfolio": evidence.get("portfolio"),
            "positions": evidence.get("positions"),
        } if isinstance(evidence, dict) and str(evidence.get("schema_version") or "").startswith("portfolio-evidence/") else None
        fingerprint_input = financial if financial else (portfolio_input or {})
        if provider == "api":
            exact_model_input: Any = {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _analysis_user_message(question, evidence)},
                ],
                "temperature": 0.2,
            }
            delivery = "openai-compatible-chat-completions"
        else:
            exact_model_input = {"input": _analysis_prompt(question, evidence)}
            delivery = "codex-cli-stdin"
        model_input_json = json.dumps(
            exact_model_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        snapshot = {
            "schema_version": "report-snapshot/2",
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "as_of_request": request_payload.get("as_of") or financial.get("as_of"),
            "financial_as_of": financial.get("as_of") if isinstance(financial, dict) else None,
            "price_as_of": (financial.get("market") or {}).get("price_as_of") if isinstance(financial, dict) else None,
            "provider": provider,
            "model": result.get("model") or request_payload.get("model") or "",
            "formula_engine": financial.get("engine") if isinstance(financial, dict) and financial else ("portfolio-diagnostics/1" if portfolio_input else None),
            "formula_engine_hashes": _report_formula_hashes(),
            "financial_source": evidence.get("financial_source") if isinstance(evidence, dict) else None,
            "rule_versions": rule_versions,
            "evidence_hash": evidence_hash,
            "prompt": {
                "schema_version": "financial-research-prompt/2",
                "delivery": delivery,
                "exact_model_input": exact_model_input,
                "sha256": hashlib.sha256(model_input_json.encode("utf-8")).hexdigest(),
            },
            "portfolio_context": portfolio_input,
            "input_fingerprint": hashlib.sha256(
                json.dumps(fingerprint_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "future_data_policy": "evidence is frozen at analysis time; later restatements do not mutate this report",
        }
        stored = {
            "request": {key: value for key, value in request_payload.items() if key not in {"evidence", "api_key"}},
            "result": result,
        }
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO reports(symbol, title, provider, evidence_hash, payload_json, evidence_json, snapshot_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol,
                    title,
                    provider,
                    evidence_hash,
                    json.dumps(stored, ensure_ascii=False, allow_nan=False),
                    json.dumps(evidence, ensure_ascii=False, allow_nan=False),
                    json.dumps(snapshot, ensure_ascii=False, allow_nan=False),
                ),
            )

    def _run_codex(self, prompt: str, evidence: Any) -> dict[str, Any]:
        bundled_codex = Path.home() / ".codex" / "plugins" / ".plugin-appserver" / "codex.exe"
        executable = os.environ.get("CODEX_EXECUTABLE") or (str(bundled_codex) if bundled_codex.exists() else "codex")
        composed = _analysis_prompt(prompt, evidence)
        try:
            process_env = os.environ.copy()
            process_env["CODEX_HOME"] = str(Path.home() / ".codex")
            proc = subprocess.run(
                [
                    executable,
                    "exec",
                    "--json",
                    "--sandbox",
                    "read-only",
                    "--skip-git-repo-check",
                    "-C",
                    str(APP_ROOT),
                    "--",
                    "-",
                ],
                cwd=APP_ROOT,
                capture_output=True,
                text=True,
                input=composed,
                encoding="utf-8",
                timeout=600,
                check=False,
                env=process_env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("未找到 Codex CLI。请先安装并使用 ChatGPT 登录。") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Codex 分析超时，请缩小证据范围后重试。") from exc
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "Codex 分析失败")
        final_text = ""
        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") in {"item.completed", "message"}:
                item = event.get("item") or event
                if item.get("type") == "agent_message":
                    final_text = item.get("text", final_text)
        if not final_text:
            final_text = proc.stdout.strip()
        return {"provider": "codex", "content": final_text, "billable": False}

    def _run_compatible_api(self, prompt: str, evidence: Any, payload: dict[str, Any]) -> dict[str, Any]:
        settings = self.settings()
        base_url = _validated_api_base_url(str(payload.get("base_url") or settings["api_base_url"]))
        model = str(payload.get("model") or settings["api_model"])
        key_env = str(settings["api_key_env"])
        api_key = os.environ.get(key_env, "")
        if not base_url or not model:
            raise RuntimeError("请先在设置中填写 API Base URL 和模型名称。")
        if not api_key:
            raise RuntimeError(f"未配置环境变量 {key_env}。API Key 不保存在软件数据库中。")
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _analysis_user_message(prompt, evidence)},
            ],
            "temperature": 0.2,
        }
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"API 返回 {exc.code}: {detail[:500]}") from exc
        content = result["choices"][0]["message"]["content"]
        return {"provider": "api", "model": model, "content": content, "billable": True, "usage": result.get("usage")}


SYSTEM_PROMPT = """你是严谨的财务研究员。所有财务数字只能来自提供的证据包；不得补猜。
明确区分：法定披露事实、系统计算、知识库观点、AI推断。优先分析盈利质量、现金流、
资产负债、资本配置、治理审计与估值假设。发现资料缺失或校验失败必须明确说明。
superseded_filing_facts、superseded_confirmed_filing_evidence 与 superseded_pending_filing_leads
仅用于解释更正前后变化，绝不能作为当前事实。当前法定披露事实只能取 confirmed_filing_facts
与 confirmed_filing_evidence，并保留其原文定位。pending_filing_leads 只是关键词检索命中，
必须明确写成“待复核线索/关键词命中”，严禁改写为该公司已经发生的事实；没有命中也不代表正常。
当 evidence 中 financial_source.mode 为 free 且 requested_symbol 与 company.symbol、company.name
能够匹配时，不要仅因没有 filing_records 就停止分析：可以用免费源的 provisional 数据做方向性筛选，
但必须把它标为临时数据，不能称为法定披露，并把缺失的监管指标、公告和审计证据列为待补项。
只有 financial_source.demo=true 或主体代码/名称明显不匹配时，才禁止把财务数字套用到请求公司。
若证据包包含多个知识流派，必须逐个流派独立给出适用性、结论、依据与反证，然后单列
共识和冲突；不得将不同流派规则加总、投票或混成平均结论。规则关系只是启发式复核线索，
必须回引规则原文。不要使用待审核、已拒绝或未启用规则。
不承诺收益，不给无条件买卖指令；输出基准/乐观/压力情景、反方证据和逻辑失效条件。"""


def _filing_source_provider(source_url: str) -> str:
    """Give manual imports the same canonical provider identity as syncs.

    The archive uses provider + notice id for incremental de-duplication, so a
    manually selected CNINFO PDF must not become a second, differently named
    source merely because it did not arrive through the discovery endpoint.
    """
    host = (urllib.parse.urlsplit(source_url).hostname or "").lower()
    if host == "cninfo.com.cn" or host.endswith(".cninfo.com.cn"):
        return "cninfo_public"
    return "official_manual"


def _validated_api_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        raise ValueError("请先在设置中填写 API Base URL")
    parsed = urllib.parse.urlsplit(value)
    is_local = (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_local):
        raise ValueError("API Base URL 必须使用 HTTPS；仅 localhost 可使用 HTTP")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API Base URL 格式无效")
    return value


def _report_formula_hashes() -> dict[str, str]:
    backend_dir = Path(__file__).parent
    return {
        name: hashlib.sha256((backend_dir / name).read_bytes()).hexdigest()
        for name in REPORT_FORMULA_FILES
    }


def _compact_financial_analysis_for_ai(financial: dict[str, Any]) -> dict[str, Any]:
    """Deduplicate metric provenance without dropping values, formulas or source locators."""
    source_facts: list[dict[str, Any]] = []
    source_fact_ids: dict[str, str] = {}

    def intern_fact(item: dict[str, Any]) -> str:
        fact = {
            key: item.get(key)
            for key in (
                "period_end", "fiscal_year", "period_type", "fact", "value", "unit",
                "source", "statement", "as_reported_label", "restated", "status",
            )
            if item.get(key) not in (None, "")
        }
        canonical = json.dumps(fact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        existing = source_fact_ids.get(canonical)
        if existing:
            return existing
        ref = f"F{len(source_facts) + 1:04d}"
        source_fact_ids[canonical] = ref
        source_facts.append({"ref": ref, **fact})
        return ref

    def compact(value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, child in value.items():
                if key == "inputs" and isinstance(child, list) and child and all(
                    isinstance(item, dict) and any(field in item for field in ("fact", "source", "period_end"))
                    for item in child
                ):
                    result["input_refs"] = [intern_fact(item) for item in child]
                else:
                    result[key] = compact(child)
            return result
        if isinstance(value, list):
            return [compact(item) for item in value]
        return value

    compacted = compact(financial)
    if not isinstance(compacted, dict):
        return financial
    compacted["source_facts"] = source_facts
    compacted["ai_compaction"] = {
        "schema_version": "financial-ai-evidence/1",
        "method": "deduplicated_input_refs",
        "notice": "指标数值、公式和状态保持原样；input_refs 指向 source_facts，可回溯报告期、原始值与来源定位。",
    }
    return compacted


def _review_reminder(status: str, due_at: Any, *, today: date | None = None) -> dict[str, Any]:
    normalized_status = str(status or "unreviewed").strip().lower()
    if normalized_status in {"reviewed", "invalidated"}:
        return {"status": "completed", "days_until_due": None, "label": "已完成复盘"}
    if normalized_status != "open" or not due_at:
        return {"status": "none", "days_until_due": None, "label": "未设置提醒"}
    try:
        due_date = date.fromisoformat(str(due_at))
    except ValueError:
        return {"status": "invalid", "days_until_due": None, "label": "复核日期无效"}
    days = (due_date - (today or date.today())).days
    if days < 0:
        return {"status": "overdue", "days_until_due": days, "label": f"已逾期 {abs(days)} 天"}
    if days == 0:
        return {"status": "due_today", "days_until_due": 0, "label": "今天复核"}
    if days <= 7:
        return {"status": "due_soon", "days_until_due": days, "label": f"{days} 天后复核"}
    return {"status": "scheduled", "days_until_due": days, "label": f"{days} 天后复核"}


def _report_snapshot_integrity(row: Any, evidence: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Verify the immutable inputs needed to audit a saved AI report."""
    evidence_bytes = json.dumps(
        evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    actual_evidence_hash = hashlib.sha256(evidence_bytes).hexdigest()
    row_evidence_hash = str(row["evidence_hash"] or "")
    snapshot_evidence_hash = str(snapshot.get("evidence_hash") or "")
    expected_evidence_hash = snapshot_evidence_hash or row_evidence_hash
    evidence_hash_fields_match = (
        bool(row_evidence_hash)
        and bool(snapshot_evidence_hash)
        and row_evidence_hash == snapshot_evidence_hash
    )
    evidence_verified = bool(evidence) and bool(expected_evidence_hash) and actual_evidence_hash == expected_evidence_hash

    prompt = snapshot.get("prompt") if isinstance(snapshot.get("prompt"), dict) else {}
    exact_model_input = prompt.get("exact_model_input") if isinstance(prompt, dict) else None
    expected_prompt_hash = str(prompt.get("sha256") or "") if isinstance(prompt, dict) else ""
    if exact_model_input is None:
        actual_prompt_hash = ""
        prompt_verified = False
    else:
        prompt_bytes = json.dumps(
            exact_model_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        actual_prompt_hash = hashlib.sha256(prompt_bytes).hexdigest()
        prompt_verified = bool(expected_prompt_hash) and actual_prompt_hash == expected_prompt_hash

    archived_formula_hashes = snapshot.get("formula_engine_hashes")
    formula_versioned = (
        isinstance(archived_formula_hashes, dict)
        and set(REPORT_FORMULA_FILES).issubset(archived_formula_hashes)
        and all(len(str(archived_formula_hashes.get(name) or "")) == 64 for name in REPORT_FORMULA_FILES)
    )
    schema_verified = snapshot.get("schema_version") == "report-snapshot/2"
    corrupt = (
        (bool(expected_evidence_hash) and not evidence_verified)
        or (bool(row_evidence_hash or snapshot_evidence_hash) and not evidence_hash_fields_match)
        or (bool(expected_prompt_hash) and not prompt_verified)
    )
    status = "corrupt" if corrupt else (
        "verified" if schema_verified and evidence_verified and prompt_verified and formula_versioned else "incomplete"
    )
    return {
        "status": status,
        "schema_verified": schema_verified,
        "evidence_verified": evidence_verified,
        "evidence_hash_fields_match": evidence_hash_fields_match,
        "prompt_verified": prompt_verified,
        "formula_versioned": formula_versioned,
        "formula_matches_current": formula_versioned and archived_formula_hashes == _report_formula_hashes(),
        "evidence_sha256": actual_evidence_hash,
        "prompt_sha256": actual_prompt_hash,
        "notice": (
            "归档证据、模型输入与公式版本校验通过。"
            if status == "verified"
            else "归档不完整或校验失败，不应据此宣称报告可精确复现。"
        ),
    }


def _valuation_reproduction(company: dict[str, Any], configured: dict[str, Any], source: dict[str, Any], date_context: dict[str, Any]) -> dict[str, Any]:
    """Freeze inputs, including formula configuration, independently of later refreshes."""
    engine_files = ("finance_engine.py", "bank_engine.py", "sector_engine.py", "financial_sector_engine.py")
    return json.loads(json.dumps({
        "schema_version": "valuation-reproduction/1.0",
        "original_company": company,
        "configured_company": configured,
        "configured_hash": hashlib.sha256(json.dumps(configured, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "source": source,
        "date_context": date_context,
        "engine_hashes": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() for name in engine_files},
        "policy": "可用归档输入重算；公式文件哈希变化时必须标记版本差异，历史展示仍使用归档结果。",
    }, ensure_ascii=False, allow_nan=False))


def _valuation_date_audit(
    company: dict[str, Any],
    payload: dict[str, Any],
    source: dict[str, Any],
    *,
    required_dates: dict[str, Any] | None = None,
    required_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Reject known future inputs; absent provenance must never certify a historical valuation."""
    provider = company.get("provider") or {}
    cutoff_text = str(payload.get("as_of") or provider.get("as_of") or source.get("as_of_request") or "")
    def parse(value: Any, label: str) -> date:
        try:
            return date.fromisoformat(str(value))
        except (ValueError, TypeError):
            raise ValueError(f"估值日期无效：{label}={value}") from None
    cutoff = parse(cutoff_text, "截止日") if cutoff_text else None
    date_keys = {"end_date", "period_end", "report_end", "financial_period_end", "net_debt_as_of", "price_as_of", "shares_as_of", "ann_date", "f_ann_date", "announcement_date", "final_announcement_date", "disclosure_date", "available_date", "assumptions_as_of"}
    checked: list[dict[str, str]] = []
    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                location = f"{path}.{key}"
                if key in date_keys and child not in (None, ""):
                    observed = parse(child, location)
                    if cutoff and observed > cutoff:
                        raise ValueError(f"估值输入晚于截止日 {cutoff.isoformat()}：{location}={observed.isoformat()}")
                    checked.append({"path": location, "date": observed.isoformat()})
                elif isinstance(child, (dict, list)):
                    walk(child, location)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
    walk(company, "company")
    walk(payload.get("assumptions") or {}, "assumptions")
    if required_dates:
        walk(required_dates, "date_context")
    missing_required = [key for key in required_keys if not (required_dates or {}).get(key)]
    complete = bool(cutoff) and not missing_required
    return {
        "as_of_request": cutoff.isoformat() if cutoff else None,
        "status": "complete" if complete else "incomplete",
        "checked_dates": checked,
        "cutoff_check": "passed_known_dates" if cutoff else "missing_cutoff",
        "missing_required_dates": missing_required,
        "warning": (
            "关键估值日期齐全，且均未晚于统一截止日；这只证明时点口径一致，不代表估值假设正确。"
            if complete
            else "估值日期口径不完整：" + ("、".join(missing_required) if missing_required else "缺少统一截止日") + "。结果仅供情景研究。"
        ),
    }


def _integer_number(value: Any, label: str, *, minimum: int, maximum: int) -> int:
    number = _finite_number(value, label, minimum=minimum, maximum=maximum)
    if isinstance(value, bool) or not number.is_integer():
        raise ValueError(f"{label}必须是整数")
    return int(number)


def _finite_number(value: Any, label: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是数字，不能使用布尔值")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是数字") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label}必须是有限数字")
    if minimum is not None and number < minimum:
        raise ValueError(f"{label}不能小于 {minimum:g}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{label}不能大于 {maximum:g}")
    return number


def _optional_finite_number(value: Any, label: str, **limits: float) -> float | None:
    if value in (None, ""):
        return None
    return _finite_number(value, label, **limits)


def _optional_positive_number(value: Any, label: str) -> float | None:
    """Read a price-like value conservatively without crashing on legacy bad data."""

    if value in (None, ""):
        return None
    try:
        return _finite_number(value, label, minimum=0.000001)
    except ValueError:
        return None


def _canonical_fingerprint_value(value: Any) -> Any:
    """Normalize JSON values that browsers serialize equivalently."""

    if isinstance(value, dict):
        return {str(key): _canonical_fingerprint_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_fingerprint_value(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("分析输入包含非有限数字")
        # JSON.parse + JSON.stringify turns 0.0/12.0 into 0/12.  They are the
        # same JSON number and must keep the same confirmation fingerprint.
        if value == 0 or value.is_integer():
            return int(value)
    return value


def _financial_provider_mode(provider: Any) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized in {"tushare", "tushare_pro"}:
        return "tushare"
    if normalized in {"akshare", "baostock", "free"}:
        return "free"
    return "cached"


def _balanced_filing_evidence(
    items: list[dict[str, Any]],
    *,
    limit: int,
    group_by_filing: bool = False,
) -> list[dict[str, Any]]:
    """Round-robin evidence categories so frequent boilerplate cannot crowd out others."""

    bounded = max(1, int(limit))
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for item in items:
        key = (
            (item.get("filing_id"), item.get("category"))
            if group_by_filing else (item.get("category"),)
        )
        groups.setdefault(key, []).append(item)
    selected: list[dict[str, Any]] = []
    offset = 0
    while len(selected) < bounded:
        added = False
        for group in groups.values():
            if offset < len(group):
                selected.append(group[offset])
                added = True
                if len(selected) >= bounded:
                    break
        if not added:
            break
        offset += 1
    return selected


def _select_filing_records_for_evidence(
    records: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Select a bounded but filing-type-diverse evidence set from newest-first records."""

    bounded = max(1, int(limit))
    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    by_id = {int(item["id"]): item for item in records}

    def add(item: dict[str, Any] | None) -> None:
        if item is None or len(selected) >= bounded:
            return
        filing_id = int(item["id"])
        if filing_id not in selected_ids:
            selected.append(item)
            selected_ids.add(filing_id)

    for filing_type in ("correction", "inquiry", "annual_report", "audit_report", "internal_control", "interim_report", "quarterly_report"):
        candidate = next(
            (
                item for item in records
                if str(item.get("filing_type") or "") == filing_type
                or (filing_type == "correction" and bool(item.get("is_correction")))
            ),
            None,
        )
        add(candidate)
        if candidate and filing_type == "correction":
            original_id = candidate.get("supersedes_filing_id")
            if original_id:
                add(by_id.get(int(original_id)))
    for item in records:
        add(item)
    return selected


def _financial_period_freshness(
    period_end: Any,
    *,
    reference_date: str | None = None,
    available: bool = True,
) -> dict[str, Any]:
    """Classify report-period recency against today or an explicit research cutoff."""

    reference_raw = str(reference_date or date.today().isoformat()).strip()
    period_raw = str(period_end or "").strip()
    if not available:
        return {"status": "missing", "age_days": None, "reference_date": reference_raw}
    try:
        reference = date.fromisoformat(reference_raw)
        period = date.fromisoformat(period_raw)
    except ValueError:
        return {"status": "unknown", "age_days": None, "reference_date": reference_raw}
    age = (reference - period).days
    status = "future" if age < 0 else "stale" if age > FINANCIAL_PERIOD_STALE_DAYS else "ready"
    return {"status": status, "age_days": age, "reference_date": reference.isoformat()}


def _snapshot_age_hours(retrieved_at: Any) -> float:
    """Return a conservative age for SQLite or timezone-aware ISO timestamps.

    Malformed timestamps are treated as stale instead of being presented as
    current market data.
    """
    try:
        raw = str(retrieved_at).strip()
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, time.time() - parsed.timestamp()) / 3600
    except (TypeError, ValueError, OverflowError):
        return float("inf")


def _usage_int(usage: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key not in usage:
            continue
        try:
            value = int(usage[key])
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return None


def _analysis_prompt(question: str, evidence: Any) -> str:
    return f"{SYSTEM_PROMPT}\n\n{_analysis_user_message(question, evidence)}"


def _analysis_user_message(question: str, evidence: Any) -> str:
    return (
        f"用户问题：{question}\n\n"
        f"{_doctrine_output_contract(evidence)}\n\n"
        f"证据包：\n{json.dumps(evidence, ensure_ascii=False, indent=2)}"
    )


def _doctrine_library_names(evidence: Any) -> list[str]:
    if not isinstance(evidence, dict):
        return []
    groups = evidence.get("doctrine_by_library")
    if not isinstance(groups, list):
        return []
    names: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        name = str(group.get("library_name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _doctrine_output_contract(evidence: Any) -> str:
    names = _doctrine_library_names(evidence)
    if not names:
        return (
            "输出契约：先写“## 财务事实与系统计算”，再写“## 情景、反方证据与失效条件”。"
            "证据包没有已审核且启用的流派规则时，不得虚构任何流派结论。"
        )
    sections = "\n".join(
        f"### 流派：{name}\n必须分别填写：适用性、结论、规则依据（规则卡 ID/版本/原文定位）、反证。"
        for name in names
    )
    cross_sections = "\n## 共识\n## 冲突" if len(names) > 1 else ""
    return (
        "输出契约：必须使用以下 Markdown 标题且保留流派原名；不得省略、合并或改成综合评分。\n"
        "## 财务事实与系统计算\n"
        "只写证据包事实、代码计算结果、数据缺口与截至日期。\n"
        "## 流派结论\n"
        f"{sections}{cross_sections}\n"
        "## 情景、反方证据与失效条件\n"
        "若某流派证据不足，也必须保留对应标题并明确写“证据不足”，不得借用另一流派补齐。"
    )


def _audit_doctrine_output(content: str, evidence: Any) -> dict[str, Any]:
    expected = _doctrine_library_names(evidence)
    if not expected:
        return {
            "schema_version": "doctrine-output-audit/1",
            "status": "not_applicable",
            "expected_libraries": [],
            "covered_libraries": [],
            "missing_libraries": [],
            "requires_cross_library_sections": False,
            "has_consensus_section": False,
            "has_conflict_section": False,
            "notice": "本次证据包没有已审核且启用的流派规则，不应生成流派结论。",
        }

    covered = [
        name
        for name in expected
        if re.search(rf"(?m)^#{{2,4}}\s*流派[：:]\s*{re.escape(name)}\s*$", content)
    ]
    requires_cross = len(expected) > 1
    has_consensus = bool(re.search(r"(?m)^#{2,4}\s*共识\s*$", content))
    has_conflict = bool(re.search(r"(?m)^#{2,4}\s*冲突\s*$", content))
    missing = [name for name in expected if name not in covered]
    compliant = not missing and (not requires_cross or (has_consensus and has_conflict))
    return {
        "schema_version": "doctrine-output-audit/1",
        "status": "compliant" if compliant else "needs_review",
        "expected_libraries": expected,
        "covered_libraries": covered,
        "missing_libraries": missing,
        "requires_cross_library_sections": requires_cross,
        "has_consensus_section": has_consensus,
        "has_conflict_section": has_conflict,
        "notice": (
            "流派输出结构已通过程序检查；仍需人工核对规则引用与推理。"
            if compliant
            else "AI 未完整遵守流派分离格式；该报告可留档，但不能视为已完成流派综合。"
        ),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "InvestmentBrain/0.1"

    @property
    def app(self) -> Application:
        return self.server.app  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            try:
                return self._get_api()
            except (ValueError, RuntimeError, KeyError) as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # pragma: no cover - safety boundary
                return self._json({"error": f"服务器内部错误：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return self._serve_static()

    def do_POST(self) -> None:
        if not self.path.startswith("/api/"):
            return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        try:
            body = self._body()
            if self.path == "/api/holdings":
                return self._json(self.app.save_holding(body))
            if self.path == "/api/holdings/import":
                return self._json(self.app.import_holdings_csv(str(body.get("csv") or "")))
            if self.path == "/api/portfolio/refresh":
                return self._json(self.app.refresh_portfolio_financials())
            if self.path == "/api/portfolio/evidence":
                return self._json(self.app.build_portfolio_evidence_bundle(body))
            if self.path == "/api/compare":
                return self._json(self.app.compare_companies(body))
            if self.path == "/api/valuation/run":
                return self._json(self.app.run_valuation_workbench(body))
            if self.path == "/api/valuation/replay":
                return self._json(self.app.replay_valuation_run(int(body.get("run_id", 0))))
            if self.path == "/api/bank/valuation/run":
                return self._json(self.app.run_bank_valuation_workbench(body))
            if self.path == "/api/bank/disclosures":
                return self._json(self.app.import_bank_disclosures(body))
            if self.path == "/api/sector/valuation/run":
                return self._json(self.app.run_sector_valuation_workbench(body))
            if self.path == "/api/sector/disclosures":
                return self._json(self.app.import_sector_disclosures(body))
            if self.path == "/api/filings/import":
                settings = self.app.settings()
                source_url = str(body.get("source_url") or "").strip()
                local_path = str(body.get("local_path") or "").strip() or None
                provider = str(body.get("source_provider") or _filing_source_provider(source_url))
                filing = archive_filing(
                    self.app.db_path,
                    self.app.filings_dir,
                    symbol=str(body.get("symbol") or ""),
                    company_name=str(body.get("company_name") or ""),
                    title=str(body.get("title") or ""),
                    filing_type=str(body.get("filing_type") or "other"),
                    report_period=str(body.get("report_period") or ""),
                    disclosed_at=str(body.get("disclosed_at") or ""),
                    source_provider=provider,
                    source_notice_id=str(body.get("source_notice_id") or ""),
                    source_url=source_url,
                    local_path=local_path,
                    allowed_local_root=str(settings["knowledge_root"]),
                    is_correction=bool(body.get("is_correction", False)),
                    supersedes_filing_id=int(body["supersedes_filing_id"]) if body.get("supersedes_filing_id") else None,
                    metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else None,
                )
                return self._json({"filing": filing, "evidence": list_filing_evidence(self.app.db_path, int(filing["id"])), "facts": list_filing_facts(self.app.db_path, int(filing["id"]))})
            if self.path == "/api/filings/search":
                return self._json(
                    {
                        "announcements": search_critical_filings(
                            str(body.get("symbol") or ""),
                            since=str(body.get("since") or ""),
                            until=str(body.get("until") or ""),
                            limit=int(body.get("limit") or 30),
                        )
                    }
                )
            if self.path == "/api/filings/sync":
                return self._json(self.app.sync_critical_filings(body))
            if self.path == "/api/filings/reindex":
                filing_id = int(body.get("filing_id") or 0)
                if filing_id <= 0:
                    raise ValueError("请选择需要重新抽取的公告")
                filing = reindex_filing(self.app.db_path, self.app.filings_dir, filing_id)
                return self._json(
                    {
                        "filing": filing,
                        "evidence": list_filing_evidence(self.app.db_path, filing_id),
                        "facts": list_filing_facts(self.app.db_path, filing_id),
                        "coverage": filing_evidence_coverage(self.app.db_path, filing_id),
                    }
                )
            if self.path == "/api/filings/ocr":
                filing_id = int(body.get("filing_id") or 0)
                if filing_id <= 0:
                    raise ValueError("请选择需要 OCR 的公告")
                filing = ocr_filing_batch(
                    self.app.db_path,
                    self.app.filings_dir,
                    filing_id,
                    batch_size=int(body.get("batch_size") or 8),
                )
                return self._json(
                    {
                        "filing": filing,
                        "evidence": list_filing_evidence(self.app.db_path, filing_id),
                        "facts": list_filing_facts(self.app.db_path, filing_id),
                        "coverage": filing_evidence_coverage(self.app.db_path, filing_id),
                    }
                )
            if self.path == "/api/filings/evidence/review":
                return self._json(
                    {
                        "evidence": update_filing_evidence_review(
                            self.app.db_path,
                            int(body.get("id") or 0),
                            review_status=str(body.get("review_status") or ""),
                            reviewer_note=str(body.get("reviewer_note") or ""),
                        )
                    }
                )
            if self.path == "/api/filings/facts/review":
                updated = update_filing_fact_review(
                    self.app.db_path,
                    int(body.get("id") or 0),
                    review_status=str(body.get("review_status") or "pending"),
                    reviewer_note=str(body.get("reviewer_note") or ""),
                )
                reviewed = self.app.filing_facts_for_review(int(updated["filing_id"]))
                return self._json({"fact": next(item for item in reviewed if int(item["id"]) == int(updated["id"]))})
            if self.path == "/api/watchlist/refresh":
                return self._json(self.app.refresh_watchlist(body.get("symbol")))
            if self.path == "/api/watchlist":
                return self._json(self.app.save_watch(body))
            if self.path == "/api/settings":
                return self._json(self.app.update_settings(body))
            if self.path == "/api/knowledge/import":
                settings = self.app.settings()
                configured_root = Path(settings["knowledge_root"]).expanduser().resolve()
                requested_root = str(body.get("root") or "").strip()
                if not requested_root:
                    raise ValueError("请明确填写要导入的投资资料文件夹，软件不会默认扫描整个工作区")
                root = Path(requested_root).expanduser().resolve()
                if root != configured_root and configured_root not in root.parents:
                    raise ValueError(f"导入目录必须位于已配置的知识根目录内：{configured_root}")
                library_raw = body.get("library_id")
                try:
                    library_id = int(library_raw) if library_raw not in (None, "", 0, "0") else None
                except (TypeError, ValueError) as exc:
                    raise ValueError("投资体系编号无效") from exc
                result = import_documents(self.app.db_path, root, library_id)
                result["library_id"] = library_id
                return self._json(result)
            if self.path == "/api/knowledge/search":
                return self._json(search_knowledge(self.app.db_path, str(body.get("query", "")), int(body.get("limit", 20)), trusted_only=True))
            if self.path == "/api/doctrine-cards":
                try:
                    library_id = int(body.get("library_id"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("请选择规则卡所属的投资体系") from exc
                card_id = create_doctrine_card(
                    self.app.db_path,
                    library_id,
                    str(body.get("title") or ""),
                    str(body.get("content") or ""),
                    category=str(body.get("category") or "principle"),
                    confidence=str(body.get("confidence") or "medium"),
                    applicability=str(body.get("applicability") or ""),
                    conditions=body.get("conditions") or [],
                    exceptions=body.get("exceptions") or [],
                    tags=body.get("tags") or [],
                    industries=body.get("industries") or [],
                    cycles=body.get("cycles") or [],
                    metrics=body.get("metrics") or [],
                    thresholds=body.get("thresholds") or [],
                    counter_evidence=body.get("counter_evidence") or [],
                    source_document_id=body.get("source_document_id"),
                    source_chunk_id=body.get("source_chunk_id"),
                    source_locator=str(body.get("source_locator") or ""),
                    review_status="pending",
                    enabled=False,
                )
                return self._json({"card": next(card for card in list_doctrine_cards(self.app.db_path) if card["id"] == card_id)})
            if self.path == "/api/doctrine-cards/generate":
                try:
                    library_id = int(body.get("library_id"))
                    max_candidates = int(body.get("max_candidates") or 120)
                except (TypeError, ValueError) as exc:
                    raise ValueError("请选择投资体系并填写有效的候选数量") from exc
                return self._json(generate_doctrine_candidates(
                    self.app.db_path, library_id, max_candidates=max_candidates
                ))
            if self.path == "/api/doctrine-cards/update":
                return self._json(self.app.update_doctrine_card_from_review(body))
            if self.path == "/api/libraries":
                with get_connection(self.app.db_path) as conn:
                    library_id = create_library(conn, str(body.get("name", "未命名流派")), str(body.get("description", "")), str(body.get("kind", "custom")))
                return self._json({"id": library_id})
            if self.path == "/api/libraries/update":
                return self._json(self.app.set_library_enabled(body))
            if self.path == "/api/finance/analyze":
                if body.get("symbol"):
                    company, source = self.app.company_financial_data(
                        str(body["symbol"]),
                        as_of=str(body.get("as_of") or "") or None,
                        refresh=bool(body.get("refresh", False)),
                        industry_hint=str(body.get("industry") or "") or None,
                    )
                    return self._json({"analysis": analyze_company(company), "source": source})
                return self._json({
                    "analysis": analyze_company(body.get("company") or demo_company()),
                    "source": {"provider": "fictional_demo", "demo": True},
                })
            if self.path == "/api/analysis/evidence":
                return self._json(self.app.build_evidence_bundle(body))
            if self.path == "/api/ai/estimate":
                if not body.get("evidence"):
                    body["evidence"] = self.app.build_evidence_bundle(body)
                return self._json({"estimate": self.app.estimate_ai(body), "evidence": body["evidence"]})
            if self.path == "/api/ai/analyze":
                if not body.get("evidence"):
                    body["evidence"] = self.app.build_evidence_bundle(body)
                return self._json(self.app.run_ai(body))
            if self.path == "/api/reports/review":
                try:
                    report_id = int(body.get("report_id"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("缺少有效的报告编号") from exc
                return self._json({"review": self.app.update_report_review(report_id, body)})
            if self.path == "/api/data/backup":
                return self._json(self.app.create_backup(str(body.get("destination") or "") or None))
            if self.path == "/api/data/restore":
                return self._json(self.app.restore_backup(str(body.get("source") or ""), confirm=bool(body.get("confirm"))))
            return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, RuntimeError, KeyError) as exc:
            return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # pragma: no cover - safety boundary
            return self._json({"error": f"服务器内部错误：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path not in {"/api/watchlist", "/api/holdings"}:
            return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        try:
            symbol = (urllib.parse.parse_qs(parsed.query).get("symbol") or [""])[0]
            if not symbol:
                raise ValueError("缺少股票代码")
            if parsed.path == "/api/holdings":
                self.app.delete_holding(symbol)
            else:
                self.app.delete_watch(symbol)
            return self._json({"ok": True})
        except (ValueError, RuntimeError, KeyError) as exc:
            return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _get_api(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == "/api/health":
            return self._json({"ok": True, "version": "0.1.0"})
        if path == "/api/dashboard":
            query = urllib.parse.parse_qs(parsed.query)
            scope = str((query.get("scope") or ["all"])[0])
            return self._json(self.app.dashboard(scope=scope))
        if path == "/api/watchlist":
            return self._json(self.app.list_watchlist())
        if path == "/api/settings":
            return self._json(self.app.settings())
        if path == "/api/data/status":
            return self._json({
                "tushare_configured": tushare_is_configured(),
                "free_provider_available": free_provider_available(),
                "token_env": "TUSHARE_TOKEN",
                "mode": "tushare" if tushare_is_configured() else ("free" if free_provider_available() else "demo"),
            })
        if path == "/api/data/health":
            return self._json(self.app.data_health())
        if path == "/api/holdings/export":
            return self._json(self.app.export_holdings_csv())
        if path == "/api/data/backups":
            return self._json(self.app.list_backups())
        if path == "/api/reports":
            query = urllib.parse.parse_qs(parsed.query)
            symbol = (query.get("symbol") or [None])[0]
            limit_raw = (query.get("limit") or ["30"])[0]
            include_content = str((query.get("include_content") or ["1"])[0]).lower() not in {"0", "false", "no"}
            try:
                limit = int(limit_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("报告数量必须是整数") from exc
            return self._json(self.app.list_reports(symbol=symbol, limit=limit, include_content=include_content))
        if path == "/api/reports/reminders":
            return self._json(self.app.list_report_reminders())
        if path == "/api/reports/content":
            query = urllib.parse.parse_qs(parsed.query)
            report_id = int((query.get("id") or ["0"])[0])
            if report_id <= 0:
                raise ValueError("缺少有效的报告编号")
            narrative = self.app.get_report_narrative(report_id)
            if narrative is None:
                raise KeyError("报告不存在")
            return self._json(narrative)
        if path in {"/api/reports/snapshot", "/api/reports/integrity", "/api/reports/review"}:
            query = urllib.parse.parse_qs(parsed.query)
            report_id = int((query.get("id") or query.get("report_id") or ["0"])[0])
            if report_id <= 0:
                raise ValueError("缺少有效的报告编号")
            if path.endswith("/integrity"):
                summary = self.app.get_report_integrity(report_id)
                if summary is None:
                    raise KeyError("报告不存在")
                return self._json(summary)
            snapshot = self.app.get_report_snapshot(report_id)
            if snapshot is None:
                raise KeyError("报告不存在")
            if path.endswith("/review"):
                return self._json(snapshot.get("review") or {"report_id": report_id, "status": "open"})
            return self._json(snapshot)
        if path == "/api/reports/diff":
            query = urllib.parse.parse_qs(parsed.query)
            left_id = int((query.get("from") or query.get("left") or ["0"])[0])
            right_id = int((query.get("to") or query.get("right") or ["0"])[0])
            if left_id <= 0 or right_id <= 0:
                raise ValueError("需要提供 from 和 to 两个报告编号")
            return self._json(self.app.compare_reports(left_id, right_id))
        if path == "/api/valuation/runs":
            query = urllib.parse.parse_qs(parsed.query)
            symbol = (query.get("symbol") or [None])[0]
            limit = int((query.get("limit") or ["30"])[0])
            return self._json(self.app.list_valuation_runs(symbol=symbol, limit=limit, include_inputs=False))
        if path == "/api/filings":
            query = urllib.parse.parse_qs(parsed.query)
            symbol = (query.get("symbol") or [None])[0]
            filing_type = (query.get("filing_type") or [None])[0]
            limit_raw = (query.get("limit") or ["100"])[0]
            return self._json(list_filings(self.app.db_path, symbol=symbol, filing_type=filing_type, limit=int(limit_raw)))
        if path == "/api/filings/evidence":
            query = urllib.parse.parse_qs(parsed.query)
            filing_id = int((query.get("filing_id") or ["0"])[0])
            if filing_id <= 0:
                raise ValueError("缺少有效的公告编号")
            return self._json(list_filing_evidence(self.app.db_path, filing_id))
        if path == "/api/filings/facts":
            query = urllib.parse.parse_qs(parsed.query)
            filing_id = int((query.get("filing_id") or ["0"])[0])
            if filing_id <= 0:
                raise ValueError("filing_id 无效")
            return self._json(self.app.filing_facts_for_review(filing_id))
        if path == "/api/filings/coverage":
            query = urllib.parse.parse_qs(parsed.query)
            filing_id = int((query.get("filing_id") or ["0"])[0])
            if filing_id <= 0:
                raise ValueError("缺少有效的公告编号")
            return self._json(filing_evidence_coverage(self.app.db_path, filing_id))
        if path == "/api/filings/artifact":
            query = urllib.parse.parse_qs(parsed.query)
            filing_id = int((query.get("filing_id") or ["0"])[0])
            return self._serve_filing_artifact(filing_id)
        if path == "/api/libraries":
            return self._json(list_libraries(self.app.db_path))
        if path == "/api/doctrine-cards":
            query = urllib.parse.parse_qs(parsed.query)
            library_raw = (query.get("library_id") or [None])[0]
            library_id = int(library_raw) if library_raw not in (None, "") else None
            return self._json(list_doctrine_cards(self.app.db_path, library_id))
        if path == "/api/doctrine-cards/versions":
            query = urllib.parse.parse_qs(parsed.query)
            card_id = int((query.get("id") or ["0"])[0])
            if card_id <= 0:
                raise ValueError("缺少有效的规则卡编号")
            return self._json(list_doctrine_card_versions(self.app.db_path, card_id))
        if path == "/api/doctrine-generation-runs":
            query = urllib.parse.parse_qs(parsed.query)
            library_raw = (query.get("library_id") or [None])[0]
            library_id = int(library_raw) if library_raw not in (None, "") else None
            return self._json(list_doctrine_generation_runs(self.app.db_path, library_id))
        if path == "/api/doctrine-relationships":
            return self._json(analyze_doctrine_relationships(self.app.db_path))
        if path == "/api/documents":
            return self._json(list_documents(self.app.db_path, limit=100, trusted_only=True))
        return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Length 无效") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError(f"请求体不能超过 {MAX_REQUEST_BYTES // 1024 // 1024} MB")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return value

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _serve_filing_artifact(self, filing_id: int) -> None:
        if filing_id <= 0:
            raise ValueError("缺少有效的公告编号")
        filing = get_filing(self.app.db_path, filing_id)
        root = self.app.filings_dir.resolve()
        target = (root / str(filing["relative_path"])).resolve()
        if root not in target.parents or not target.is_file():
            return self._json({"error": "公告原件不存在或已移出归档目录"}, HTTPStatus.NOT_FOUND)
        raw = target.read_bytes()
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Content-Disposition", f'inline; filename="filing-{filing_id}.pdf"')
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        self.wfile.write(raw)

    def _serve_static(self) -> None:
        requested = self.path.split("?", 1)[0].lstrip("/") or "index.html"
        target = (DIST_DIR / requested).resolve()
        if DIST_DIR.resolve() not in target.parents and target != DIST_DIR.resolve():
            return self.send_error(HTTPStatus.FORBIDDEN.value)
        if not target.is_file():
            target = DIST_DIR / "index.html"
        if not target.exists():
            return self._json({"error": "前端静态文件不可用；请使用 npm run dev，或运行项目构建后由内置预览启动。"}, HTTPStatus.SERVICE_UNAVAILABLE)
        mime = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }.get(target.suffix.lower(), "application/octet-stream")
        raw = target.read_bytes()
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    app = Application()
    server = ThreadingHTTPServer((host, port), Handler)
    server.app = app  # type: ignore[attr-defined]
    url = f"http://{host}:{port}"
    print(f"散修宗投资大脑已启动：{url}")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="散修宗投资大脑本地服务器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    serve(args.host, args.port, not args.no_browser)


if __name__ == "__main__":
    main()
