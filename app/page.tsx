"use client";

import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";

type View = "research" | "filings" | "compare" | "knowledge" | "holdings" | "reports" | "watchlist";
type EvidenceKey = "profit" | "cash" | "balance" | "capital" | "governance" | "flag" | "valuation";
type TrendKey = "profit" | "cash" | "return";
type EvidenceSelection = { key: EvidenceKey; company: ResearchCompany; analysis?: UnknownRecord; source?: UnknownRecord };

type ApiHolding = {
  symbol: string;
  name?: string;
  quantity?: number;
  cost?: number;
  current_price?: number | null;
  current_price_as_of?: string;
  industry?: string;
  thesis?: string;
  horizon?: string;
  max_loss_pct?: number | null;
};

type PortfolioPosition = { symbol: string; name?: string; industry?: string; value?: number | null; weight?: number | null; price?: number | null; price_source?: string | null; price_as_of?: string | null; price_age_days?: number | null; financial_data?: boolean; data_as_of?: string | null; financial_period_age_days?: number | null; financial_period_status?: "ready" | "stale" | "future" | "unknown" | "missing"; snapshot_status?: string; cash_conversion?: number | null; return_on_invested_capital?: number | null; debt_to_equity?: number | null; red_flags?: number; yellow_flags?: number; red_changes?: number; yellow_changes?: number; pending_filing_reviews?: number; confirmed_filing_red_flags?: number; unsearchable_filings?: number; flag_titles?: string[]; risk_status?: string; decision?: { priority: "red" | "yellow" | "green"; status: string; loss_limit_triggered?: boolean; reasons?: string[]; next_actions?: string[]; review_conditions?: string[]; execution_policy?: string } };
type PortfolioDiagnostics = { positions?: PortfolioPosition[]; coverage?: { positions?: number; priced_positions?: number; financial_positions?: number; industry_classified_positions?: number; unclassified_value?: number }; total_value_for_diagnostics?: number; concentration?: { top_position?: string | null; top_position_weight?: number | null; hhi?: number | null; interpretation?: string }; industries?: Array<{ industry: string; value: number; weight: number }>; alerts?: Array<{ severity: string; title: string; detail: string }>; decision_summary?: { immediate_review?: number; conditional_review?: number; observe?: number } };

type ApiLibrary = { id?: number; name?: string; title?: string; description?: string; kind?: string; enabled?: number; document_count?: number; card_count?: number; approved_card_count?: number; pending_card_count?: number; updated_at?: string };
type DoctrineCard = { id: number; library_id: number; library_name?: string; title: string; content: string; category?: string; confidence?: string; applicability?: string; conditions?: string[]; exceptions?: string[]; tags?: string[]; industries?: string[]; cycles?: string[]; metrics?: string[]; thresholds?: string[]; counter_evidence?: string[]; source_document_id?: number | null; source_chunk_id?: number | null; source_locator?: string; extraction_method?: string; review_status?: "pending" | "approved" | "rejected"; enabled?: number; current_version?: number; version_count?: number; reviewed_at?: string | null; updated_at?: string };
type DoctrineRelationshipSide = { card_id: number; library_id: number; library_name?: string; title: string; content?: string; direction?: string; metrics?: string[]; thresholds?: string[]; source_locator?: string; version?: number };
type DoctrineRelationship = { kind: "consensus" | "conflict"; anchors: string[]; confidence?: string; left: DoctrineRelationshipSide; right: DoctrineRelationshipSide; notice?: string };
type DoctrineRelationshipResult = { summary?: { enabled_rules?: number; consensus?: number; conflicts?: number }; relationships?: DoctrineRelationship[]; method?: string; review_required?: boolean };
type DoctrineVersion = { id: number; card_id: number; version_no: number; change_kind: string; created_at?: string; snapshot?: DoctrineCard };
type DoctrineCoverage = { schema_version?: string; status?: "compliant" | "needs_review" | "not_applicable"; expected_libraries?: string[]; covered_libraries?: string[]; missing_libraries?: string[]; requires_cross_library_sections?: boolean; has_consensus_section?: boolean; has_conflict_section?: boolean; notice?: string };
type ReviewReminder = { status?: "none" | "scheduled" | "due_soon" | "due_today" | "overdue" | "completed" | "invalid"; days_until_due?: number | null; label?: string };
type ApiReport = { id: number; symbol: string; title: string; provider: string; model?: string; content?: string; doctrine_coverage?: DoctrineCoverage; evidence_hash?: string; snapshot_schema?: string; review_status?: string; review_due_at?: string | null; expected_conditions?: string; actual_outcome?: string; reviewer_note?: string; actual_as_of?: string; reminder?: ReviewReminder; created_at?: string; request?: { knowledge_query?: string; use_tushare?: boolean } };
type ReportNarrative = { id: number; content?: string; doctrine_coverage?: DoctrineCoverage };
type ReportReminderItem = { report_id: number; symbol: string; title: string; due_at?: string; expected_conditions?: string; reminder?: ReviewReminder };
type ReportSnapshotAudit = { id?: number; replayable?: boolean; integrity?: { status?: "verified" | "incomplete" | "corrupt"; schema_verified?: boolean; evidence_verified?: boolean; evidence_hash_fields_match?: boolean; prompt_verified?: boolean; formula_versioned?: boolean; formula_matches_current?: boolean; notice?: string } };
type ReportDiff = { schema_version?: string; report_kind?: "company" | "portfolio"; before?: { id?: number; as_of?: string }; after?: { id?: number; as_of?: string }; metric_changes?: Array<{ metric: string; before?: unknown; after?: unknown }>; rule_changes?: Array<{ card_id: string; before?: number; after?: number }>; risk_signal_changes?: Array<{ signal_id: string; change: "added" | "removed" | "changed"; before?: { severity?: string; title?: string; detail?: string; review_condition?: string } | null; after?: { severity?: string; title?: string; detail?: string; review_condition?: string } | null }>; portfolio_position_changes?: Array<{ symbol: string; change: "added" | "removed" | "changed"; before?: { name?: string; weight?: number | null; current_price?: number | null; price_as_of?: string | null; return_pct?: number | null; financial_as_of?: string | null; financial_period_status?: string; decision?: { priority?: string; status?: string } } | null; after?: { name?: string; weight?: number | null; current_price?: number | null; price_as_of?: string | null; return_pct?: number | null; financial_as_of?: string | null; financial_period_status?: string; decision?: { priority?: string; status?: string } } | null }>; data_context?: { before?: { financial_as_of?: string; price_as_of?: string; source_provider?: string; source_mode?: string; source_cache?: string; model?: string; ai_provider?: string; position_count?: number; priced_positions?: number; financial_positions?: number }; after?: { financial_as_of?: string; price_as_of?: string; source_provider?: string; source_mode?: string; source_cache?: string; model?: string; ai_provider?: string; position_count?: number; priced_positions?: number; financial_positions?: number } }; data_context_changed?: boolean; judgment_follow_up?: { before_report?: { status?: string; expected_conditions?: string; due_at?: string; actual_outcome?: string; actual_as_of?: string } | null; after_report?: { status?: string; expected_conditions?: string; due_at?: string; actual_outcome?: string; actual_as_of?: string } | null }; narrative_changed?: boolean; doctrine_output_changed?: boolean; evidence_changed?: boolean; model_changed?: boolean; replayable?: boolean; warning?: string };
type BackupInfo = { file_name: string; path?: string; size_bytes?: number; modified_at?: string; created_at?: string; sha256?: string; integrity?: string; recoverable?: boolean };
type DataHealth = { providers?: { tushare?: { configured?: boolean }; free?: { available?: boolean } }; financial_snapshots?: { count?: number; companies?: number; fresh_companies?: number; stale_companies?: number; unknown_companies?: number; historical_count?: number; historical_companies?: number; policy?: string; latest?: string | null; status?: string; age_hours?: number | null }; filings?: { count?: number; searchable?: number; partially_searchable?: number; unsearchable?: number; coverage_status?: string; latest?: string | null; status?: string; age_hours?: number | null }; knowledge?: { documents?: number; chunks?: number; eligible_documents?: number; eligible_chunks?: number; disabled_documents?: number; disabled_chunks?: number; quarantined_documents?: number; quarantined_chunks?: number; policy?: string }; reports?: { count?: number; replayable?: number; partial?: number; corrupt?: number; policy?: string }; policy?: string };
type AiCostEstimate = { schema_version: string; provider: "codex" | "api"; model: string; evidence_bytes: number; estimated_input_tokens: number; estimated_output_tokens: number; estimated_total_tokens: number; monthly_tokens_remaining: number; monthly_tokens_after: number; within_budget: boolean; configuration_ready: boolean; estimated_cost?: number | null; currency?: string; pricing_configured?: boolean; cost_notice?: string; fingerprint: string };
type ApiWatchQuote = { market_price?: string | number | null; market_cap?: string | number | null; pe?: string | number | null; pb?: string | number | null; as_of?: string; retrieved_at?: string; provider?: string };
type ApiWatchResearch = { status?: "继续观察" | "等待条件"; priority?: "red" | "yellow" | "green"; financial_as_of?: string | null; reasons?: string[]; review_conditions?: string[]; execution_policy?: string; filing_review?: { pending_evidence?: number; confirmed_red?: number; unsearchable?: number; latest_disclosed_at?: string | null } };
type ApiWatch = { id: number; symbol: string; name?: string; note?: string; created_at?: string; updated_at?: string; quote?: ApiWatchQuote | null; quote_status?: "ready" | "stale" | "not_loaded" | "invalid_snapshot"; research?: ApiWatchResearch };
type ApiFiling = { id: number; symbol: string; company_name?: string; title: string; filing_type?: string; report_period?: string; disclosed_at?: string; source_provider?: string; source_url?: string; sha256?: string; byte_size?: number; page_count?: number | null; extraction_status?: string; extraction_quality?: number; extraction_note?: string; ocr_pages_done?: number; ocr_total_pages?: number; ocr_status?: string; evidence_count?: number; active_evidence_count?: number; dismissed_count?: number; red_count?: number; review_count?: number; duplicate?: boolean; is_correction?: number | boolean; supersedes_filing_id?: number | null; supersedes_title?: string | null; supersedes_disclosed_at?: string | null; superseded_by_id?: number | null; superseded_by_title?: string | null };
type FilingEvidence = { id: number; filing_id: number; category: string; severity: "info" | "review" | "red"; title: string; excerpt: string; page_number: number; locator: string; confidence?: string; detected_values?: Array<{ raw: string; unit?: string }>; review_status?: "pending" | "confirmed" | "dismissed"; reviewer_note?: string; reviewed_at?: string | null };
type FilingFact = { id: number; filing_id: number; metric: string; label: string; scope: string; value: number; unit: string; raw_value: string; period_end?: string; page_number: number; locator: string; excerpt: string; confidence?: string; review_status?: "pending" | "confirmed" | "dismissed"; application_status?: "awaiting_review" | "excluded" | "waiting_financial_snapshot" | "waiting_financial_period" | "conflict_isolated" | "applied" | "not_applied" | "superseded"; reviewer_note?: string; reviewed_at?: string | null };
type FilingCoverage = { filing_id: number; extraction_status: string; extraction_quality?: number; extraction_note?: string; searchable: boolean; partially_searchable?: boolean; ocr_pages_done?: number; ocr_total_pages?: number; disclaimer: string; categories: Array<{ category: string; label: string; status: "not_searchable" | "found_confirmed" | "found_pending" | "reviewed_no_issue" | "not_found_in_extracted_text"; signal_count: number; pending: number; confirmed: number; dismissed: number }> };
type CninfoAnnouncement = { symbol: string; company_name?: string; title: string; filing_type: string; report_period?: string; disclosed_at?: string; source_provider: string; source_notice_id: string; source_url: string; is_correction?: boolean };
type FilingSyncState = { symbol: string; source_provider: string; cursor_date?: string; last_disclosed_at?: string; last_run_at?: string; status: "never" | "running" | "success" | "partial" | "error"; last_error?: string; last_discovered?: number; last_archived?: number; last_duplicates?: number };
type ComparisonCompany = { symbol: string; name: string; industry?: string; status: "ready" | "missing" | "error" | "unsupported"; error?: string; as_of?: string; financial_period?: { status?: "ready" | "stale" | "future" | "unknown" | "missing"; age_days?: number | null; reference_date?: string }; source?: { provider?: string; mode?: string; retrieved_at?: string; cache?: string }; data_quality?: { score?: number; status?: string }; metrics?: Record<string, number | null>; market?: Record<string, number | string | null>; filing_review?: { pending_evidence?: number; confirmed_red?: number; unsearchable?: number }; risk?: { red_flags?: number; yellow_flags?: number; red_changes?: number; yellow_changes?: number; confirmed_filing_red?: number; pending_filing_reviews?: number; unsearchable_filings?: number; titles?: string[] }; decision?: { status?: "继续观察" | "等待条件"; priority?: "red" | "yellow" | "green"; reasons?: string[]; review_conditions?: string[]; execution_policy?: string } };
type ComparisonResult = { schema_version: string; as_of_request?: string | null; refreshed?: boolean; companies: ComparisonCompany[]; coverage: { requested: number; ready: number }; methodology?: { basis?: string; warning?: string } };
type DashboardData = {
  dashboard_scope?: "all" | "core" | "knowledge" | "filings";
  holdings?: ApiHolding[];
  portfolio?: { cost?: number; value?: number; pnl?: number; positions?: number; priced_positions?: number; valuation_policy?: string };
  portfolio_diagnostics?: PortfolioDiagnostics;
  libraries?: ApiLibrary[];
  doctrine_cards?: DoctrineCard[];
  doctrine_relationships?: DoctrineRelationshipResult;
  filings?: ApiFiling[];
  filing_stats?: { filings?: number; companies?: number; corrections?: number; evidence?: number; red_signals?: number; pending_reviews?: number; confirmed_evidence?: number; pending_facts?: number; confirmed_facts?: number };
  filing_sync_states?: FilingSyncState[];
  documents?: unknown[];
  document_stats?: { document_instances?: number; unique_documents?: number; duplicates?: number; chunks?: number; quarantined_documents?: number; quarantined_chunks?: number };
  reports?: ApiReport[];
  review_reminders?: ReportReminderItem[];
  watchlist?: ApiWatch[];
  settings?: { ai_provider?: string; api_base_url?: string; api_model?: string; api_key_configured?: boolean; api_input_price_per_million?: number; api_output_price_per_million?: number; api_price_currency?: "CNY" | "USD"; tushare_configured?: boolean; free_provider_available?: boolean; financial_data_mode?: string; knowledge_root?: string; monthly_token_limit?: number; monthly_tokens_used?: number; monthly_tokens_remaining?: number; settings_warnings?: string[] };
  data_health?: DataHealth;
};

type UnknownRecord = Record<string, unknown>;

function asRecord(value: unknown): UnknownRecord | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : undefined;
}

function doctrineVersionLabel(kind?: string) {
  return ({ created: "初始版本", review: "审核状态变更", edit: "内容修订", edit_requires_review: "内容修订 · 待重新审核", migration_snapshot: "历史迁移快照" } as Record<string, string>)[kind || ""] || kind || "版本快照";
}

function renderInlineMarkdown(text: string): ReactNode[] {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g);
  return parts.filter(Boolean).map((part, index) => {
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    const link = part.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
    if (link) return <a key={index} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a>;
    return <Fragment key={index}>{part}</Fragment>;
  });
}

function MarkdownReport({ content }: { content?: string }) {
  const lines = (content || "该报告没有返回可显示的文字内容，但分析请求已保存。").replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index].trimEnd();
    if (!line.trim()) { index += 1; continue; }
    if (line.trim().startsWith("```")) {
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) code.push(lines[index++]);
      if (index < lines.length) index += 1;
      blocks.push(<pre key={`code-${index}`}><code>{code.join("\n")}</code></pre>);
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      const children = renderInlineMarkdown(heading[2]);
      blocks.push(level === 1 ? <h2 key={index}>{children}</h2> : level === 2 ? <h3 key={index}>{children}</h3> : <h4 key={index}>{children}</h4>);
      index += 1;
      continue;
    }
    if (/^[-*]\s+/.test(line.trim())) {
      const items: string[] = [];
      while (index < lines.length && /^[-*]\s+/.test(lines[index].trim())) items.push(lines[index++].trim().replace(/^[-*]\s+/, ""));
      blocks.push(<ul key={`ul-${index}`}>{items.map((item, itemIndex) => <li key={itemIndex}>{renderInlineMarkdown(item)}</li>)}</ul>);
      continue;
    }
    if (/^\d+\.\s+/.test(line.trim())) {
      const items: string[] = [];
      while (index < lines.length && /^\d+\.\s+/.test(lines[index].trim())) items.push(lines[index++].trim().replace(/^\d+\.\s+/, ""));
      blocks.push(<ol key={`ol-${index}`}>{items.map((item, itemIndex) => <li key={itemIndex}>{renderInlineMarkdown(item)}</li>)}</ol>);
      continue;
    }
    if (line.trim().startsWith(">")) {
      const quote: string[] = [];
      while (index < lines.length && lines[index].trim().startsWith(">")) quote.push(lines[index++].trim().replace(/^>\s?/, ""));
      blocks.push(<blockquote key={`quote-${index}`}>{renderInlineMarkdown(quote.join(" "))}</blockquote>);
      continue;
    }
    const paragraph = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim() && !/^(#{1,4})\s+|^[-*]\s+|^\d+\.\s+|^>|^```/.test(lines[index].trim())) paragraph.push(lines[index++].trim());
    blocks.push(<p key={`p-${index}`}>{renderInlineMarkdown(paragraph.join(" "))}</p>);
  }
  return <div className="markdown-report">{blocks}</div>;
}

function metricValue(analysis: UnknownRecord | undefined, name: string): number | undefined {
  const periods = asRecord(analysis?.periods);
  const ttm = asRecord(periods?.ttm);
  const metrics = asRecord(ttm?.metrics);
  const metric = asRecord(metrics?.[name]);
  return typeof metric?.value === "number" && Number.isFinite(metric.value) ? metric.value : undefined;
}

function annualPeriods(analysis: UnknownRecord | undefined): UnknownRecord[] {
  const periods = asRecord(analysis?.periods)?.annual;
  return Array.isArray(periods) ? periods.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
}

function annualMetric(period: UnknownRecord, name: string): number | undefined {
  const metric = asRecord(asRecord(period.metrics)?.[name]);
  return typeof metric?.value === "number" && Number.isFinite(metric.value) ? metric.value : undefined;
}

function formatPercent(value: number | undefined): string {
  return value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;
}

function formatRatio(value: number | undefined): string {
  return value === undefined ? "—" : `${value.toFixed(2)}×`;
}

function formatAmount(value: number | undefined): string {
  if (value === undefined) return "—";
  const absolute = Math.abs(value);
  const suffix = absolute >= 10000 ? "亿" : "百万元";
  const divisor = absolute >= 10000 ? 10000 : 1;
  return `${(value / divisor).toLocaleString("zh-CN", { maximumFractionDigits: 1 })}${suffix}`;
}

function metricStatus(value: number | undefined): { status: string; tone: "good" | "great" | "watch" } {
  return value === undefined ? { status: "缺数据", tone: "watch" } : { status: "已计算", tone: "good" };
}

async function apiRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data?.error || "本地服务暂时不可用");
  return data as T;
}

type ResearchCompany = { code: string; name: string; market: string; price: string; change: string; industry: string };

const companies: ResearchCompany[] = [
  { code: "601088", name: "中国神华", market: "SH", price: "42.36", change: "+0.86%", industry: "煤炭开采" },
  { code: "600519", name: "贵州茅台", market: "SH", price: "1,428.00", change: "−0.42%", industry: "白酒" },
  { code: "000333", name: "美的集团", market: "SZ", price: "76.18", change: "+1.21%", industry: "家用电器" },
  { code: "600036", name: "招商银行", market: "SH", price: "46.92", change: "+0.34%", industry: "股份制银行" },
];

function marketForSymbol(symbol: string): string {
  if (/^(4|8|9)/.test(symbol)) return "BJ";
  return /^(6|5)/.test(symbol) ? "SH" : "SZ";
}

function researchCompany(symbol: string, name = ""): ResearchCompany {
  const code = String(symbol || "").trim().toUpperCase().split(".", 1)[0];
  const known = companies.find((company) => company.code === code);
  return known || { code, name: name || `股票 ${code}`, market: marketForSymbol(code), price: "—", change: "—", industry: "待识别行业" };
}

const diagnosticCards = [
  { key: "profit" as const, index: "01", title: "盈利质量", score: 86, status: "稳健", tone: "good", metric: "ROIC 16.8%", detail: "高于 5 年中位数 2.1pct", note: "煤价下行阶段仍保持较强成本控制" },
  { key: "cash" as const, index: "02", title: "现金质量", score: 92, status: "优秀", tone: "great", metric: "现金含量 1.18×", detail: "经营现金流 / 净利润", note: "连续 5 年覆盖净利润，回款质量稳定" },
  { key: "balance" as const, index: "03", title: "资产负债", score: 88, status: "稳健", tone: "good", metric: "净现金 1,168亿", detail: "有息负债率 6.3%", note: "短债覆盖充分，压力情景安全垫较厚" },
  { key: "capital" as const, index: "04", title: "资本配置", score: 78, status: "关注", tone: "watch", metric: "分红率 75.2%", detail: "3 年平均股息率 6.8%", note: "资本开支回升可能压缩未来自由现金流" },
  { key: "governance" as const, index: "05", title: "治理审计", score: 82, status: "清晰", tone: "good", metric: "标准无保留", detail: "关联交易占营收 3.2%", note: "需持续跟踪集团资产注入与同业竞争" },
];

const trendData: Record<TrendKey, { label: string; unit: string; values: number[]; comparison: number[]; valueLabels: string[]; comparisonLabels: string[] }> = {
  profit: { label: "营收 / 归母净利", unit: "亿元", values: [3352, 3445, 3431, 3231, 3286], comparison: [516, 696, 596, 587, 622], valueLabels: ["3,352", "3,445", "3,431", "3,231", "3,286"], comparisonLabels: ["516", "696", "596", "587", "622"] },
  cash: { label: "经营现金流 / 自由现金流", unit: "亿元", values: [945, 1097, 884, 932, 957], comparison: [506, 704, 503, 548, 601], valueLabels: ["945", "1,097", "884", "932", "957"], comparisonLabels: ["506", "704", "503", "548", "601"] },
  return: { label: "ROE / ROIC", unit: "%", values: [12.7, 18.1, 16.4, 15.2, 16.1], comparison: [11.8, 17.3, 15.6, 14.7, 16.8], valueLabels: ["12.7%", "18.1%", "16.4%", "15.2%", "16.1%"], comparisonLabels: ["11.8%", "17.3%", "15.6%", "14.7%", "16.8%"] },
};

const evidenceContent: Record<EvidenceKey, { eyebrow: string; title: string; summary: string; formula?: string; facts: { label: string; value: string; source: string }[]; judgment: string }> = {
  profit: {
    eyebrow: "诊断 01 · 盈利能力",
    title: "利润有韧性，但仍需按中周期看待",
    summary: "核心营业利润的稳定性好于行业平均，成本曲线是主要护城河。当前利润仍受煤价影响，不应直接把峰值利润永久化。",
    formula: "ROIC = 税后经营利润 ÷ 平均投入资本",
    facts: [
      { label: "2025 ROIC", value: "16.8%", source: "2025 年报 · 合并利润表 / 资产负债表" },
      { label: "毛利率", value: "34.6%", source: "2025 年报 · 分行业经营情况" },
      { label: "5 年毛利率区间", value: "30.8% — 39.0%", source: "2021—2025 年报标准化序列" },
    ],
    judgment: "事实支持“高质量周期股”，但不足以支持“盈利永久稳定”。估值应使用正常化利润，而非单年利润。",
  },
  cash: {
    eyebrow: "诊断 02 · 现金质量",
    title: "利润兑现为现金，回款压力较低",
    summary: "经营活动现金流持续覆盖净利润，应收账款增速没有显著超过收入增速，利润含金量处于健康区间。",
    formula: "现金含量 = 经营活动现金流净额 ÷ 归母净利润",
    facts: [
      { label: "现金含量", value: "1.18×", source: "2025 年报 · 现金流量表" },
      { label: "应收周转天数", value: "11.4 天", source: "2025 年报 · 应收账款附注" },
      { label: "自由现金流", value: "601 亿元", source: "经营现金流 − 购建长期资产现金支出" },
    ],
    judgment: "未发现“利润增、现金流降”的典型背离。自由现金流能够覆盖当前分红，但资本开支回升是主要变量。",
  },
  balance: {
    eyebrow: "诊断 03 · 资产负债",
    title: "低杠杆与净现金构成下行保护",
    summary: "货币资金在扣除受限资金后仍显著高于有息负债，短期偿债风险较低。矿权及长期资产的减值敏感性值得保留。",
    formula: "净现金 = 可自由支配现金 − 有息负债",
    facts: [
      { label: "净现金", value: "1,168 亿元", source: "2025 年报 · 货币资金与借款附注" },
      { label: "流动比率", value: "2.31×", source: "2025 年报 · 合并资产负债表" },
      { label: "利息保障倍数", value: "38.7×", source: "EBIT ÷ 利息费用" },
    ],
    judgment: "压力情景下仍有较强分红与运营缓冲。若发生大型并购，应重新计算净现金与资本回报。",
  },
  capital: {
    eyebrow: "诊断 04 · 资本配置",
    title: "高分红可信，新增资本开支需观察回报",
    summary: "公司分红纪录稳定，当前派息由自由现金流覆盖。但铁路、电力与矿区扩建项目进入投入期，不能只用静态股息率判断。",
    formula: "分红覆盖倍数 = 自由现金流 ÷ 现金分红",
    facts: [
      { label: "现金分红", value: "467 亿元", source: "2025 年度利润分配方案" },
      { label: "覆盖倍数", value: "1.29×", source: "自由现金流 ÷ 现金分红" },
      { label: "资本开支同比", value: "+18.6%", source: "2025 年报 · 资本开支计划" },
    ],
    judgment: "分红可持续性目前良好，但若煤价回落与资本开支上行同时发生，分红增长空间会收窄。",
  },
  governance: {
    eyebrow: "诊断 05 · 治理审计",
    title: "披露整体清晰，关联交易需持续审视",
    summary: "审计意见为标准无保留，未发现重大会计政策异常变化。集团内部采购及销售具备商业背景，但定价公允性需逐年复核。",
    facts: [
      { label: "审计意见", value: "标准无保留", source: "2025 年审计报告" },
      { label: "关键审计事项", value: "3 项", source: "收入确认、固定资产减值、矿山复垦义务" },
      { label: "关联交易占比", value: "3.2%", source: "2025 年报 · 关联方及关联交易" },
    ],
    judgment: "暂无一票否决项。仍需关注大股东资产安排、关联采购定价与同业竞争承诺的履行情况。",
  },
  flag: {
    eyebrow: "财务雷达 · 中度关注",
    title: "资本开支增速超过收入增速",
    summary: "2025 年购建长期资产现金支出同比增长 18.6%，高于收入增速。扩建项目可能提升未来产能，也可能降低短期自由现金流。",
    facts: [
      { label: "资本开支", value: "356 亿元", source: "2025 年报 · 现金流量表" },
      { label: "收入增速", value: "+1.7%", source: "2025 年报 · 合并利润表" },
      { label: "主要投向", value: "煤矿 / 电力 / 运输", source: "2025 年报 · 资本开支计划" },
    ],
    judgment: "这不是财务造假信号，而是估值与分红预测中的重要敏感变量。下一期重点核对项目进度和新增回报。",
  },
  valuation: {
    eyebrow: "估值依据 · 三情景",
    title: "价格已反映部分红利确定性",
    summary: "采用中周期自由现金流与股息折现交叉验证。当前价格接近基准情景中枢，安全边际主要来自低杠杆与现金分红。",
    formula: "股权价值 = 正常化现金流折现值 + 净现金",
    facts: [
      { label: "当前价", value: "42.36 元", source: "演示行情 · 2026-08-11 收盘" },
      { label: "基准价值区间", value: "40.8 — 48.6 元", source: "内部估值模型 v0.3" },
      { label: "隐含中周期煤价", value: "约 790 元/吨", source: "反向 DCF 推导" },
    ],
    judgment: "当前不属于显著低估区间。若要求 20% 安全边际，需等待价格回落或盈利预测上修。",
  },
};

function AppMark() {
  return <span className="app-mark" aria-hidden="true"><i /><i /><i /></span>;
}

function Sidebar({ activeView, onChange, open, onClose, watchlistCount, reminderCount }: { activeView: View; onChange: (view: View) => void; open: boolean; onClose: () => void; watchlistCount: number; reminderCount: number }) {
  const items: { id: View; label: string; glyph: string }[] = [
    { id: "research", label: "财务研究", glyph: "研" },
    { id: "filings", label: "财报证据", glyph: "证" },
    { id: "compare", label: "公司对比", glyph: "比" },
    { id: "knowledge", label: "知识大脑", glyph: "知" },
    { id: "holdings", label: "我的持仓", glyph: "仓" },
    { id: "watchlist", label: "观察清单", glyph: "观" },
    { id: "reports", label: "研究档案", glyph: "档" },
  ];

  return (
    <>
      <button className={`sidebar-scrim ${open ? "visible" : ""}`} aria-label="关闭导航" onClick={onClose} />
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="brand"><AppMark /><div><strong>散修宗</strong><span>投资大脑 · 衡镜</span></div></div>
        <nav aria-label="主导航">
          <p className="nav-label">研究工作台</p>
          {items.map((item) => (
            <button
              key={item.id}
              className={activeView === item.id ? "active" : ""}
              onClick={() => { onChange(item.id); onClose(); }}
            >
              <span className="nav-glyph">{item.glyph}</span>{item.label}
              {item.id === "watchlist" && watchlistCount > 0 && <b className="nav-count">{watchlistCount}</b>}
              {item.id === "reports" && reminderCount > 0 && <b className="nav-count alert">{reminderCount}</b>}
            </button>
          ))}
        </nav>
        <div className="sidebar-spacer" />
        <div className="research-mode">
          <span className="status-orb" />
          <div><strong>研究模式</strong><span>演示数据 · 不构成建议</span></div>
        </div>
        <button className="profile-button"><span className="avatar">林</span><span><strong>林先生</strong><small>本地工作区</small></span><b>···</b></button>
      </aside>
    </>
  );
}

function Header({ engine, setEngine, onMenu, onSettings, backendOnline, dataHealth, onSelectCompany }: { engine: string; setEngine: (value: string) => void; onMenu: () => void; onSettings: () => void; backendOnline: boolean; dataHealth?: DataHealth; onSelectCompany: (company: ResearchCompany) => void }) {
  const [engineOpen, setEngineOpen] = useState(false);
  const [freshOpen, setFreshOpen] = useState(false);

  return (
    <header className="topbar">
      <button className="menu-button" onClick={onMenu} aria-label="打开导航">☰</button>
      <div className="topbar-title"><span>财务研究工作台</span><small>让每个结论都有出处</small></div>
      <div className="topbar-search"><SearchBox onSelectCompany={onSelectCompany} /></div>
      <div className="topbar-actions">
        <div className="popover-wrap">
          <button className="freshness" onClick={() => setFreshOpen(!freshOpen)} aria-expanded={freshOpen}>
            <span className={`pulse-dot ${backendOnline ? "" : "offline"}`} /> {backendOnline ? "本地数据已连接" : "演示数据模式"} <b>⌄</b>
          </button>
          {freshOpen && (
            <div className="popover freshness-popover">
              <strong>数据新鲜度</strong>
              <div><span>当前财务缓存</span><b>{dataHealth?.financial_snapshots?.count ? `${dataHealth.financial_snapshots.status === "fresh" ? "全部新鲜" : dataHealth.financial_snapshots.status === "mixed" ? "部分过期" : dataHealth.financial_snapshots.status === "stale" ? "全部过期" : "时间未知"} · 公司 ${dataHealth.financial_snapshots.fresh_companies ?? 0}/${dataHealth.financial_snapshots.companies ?? 0}` : "暂无真实快照"}</b></div>
              <div><span>历史截止日快照</span><b>{dataHealth?.financial_snapshots?.historical_count ? `${dataHealth.financial_snapshots.historical_count} 份 · 已与当前诊断隔离` : "暂无"}</b></div>
              <div><span>公司公告</span><b>{dataHealth?.filings?.count ? `可检索 ${dataHealth.filings.searchable ?? 0}/${dataHealth.filings.count} · ${String(dataHealth.filings.latest || "").slice(0, 10)}` : "尚未归档"}</b></div>
              <div><span>免费数据源</span><b>{dataHealth?.providers?.free?.available ? "AKShare / BaoStock 可用" : "未安装"}</b></div>
              <small>{backendOnline ? "以上来自本地数据库，不用模拟时间冒充实时数据。" : "本地服务未启动，当前界面自动使用演示数据。"}</small>
            </div>
          )}
        </div>
        <div className="popover-wrap">
          <button className="engine-button" onClick={() => setEngineOpen(!engineOpen)} aria-expanded={engineOpen}>
            <span className="engine-symbol">✦</span><span><small>分析引擎</small><strong>{engine}</strong></span><b>⌄</b>
          </button>
          {engineOpen && (
            <div className="popover engine-popover" role="menu">
              <button className={engine === "Codex" ? "selected" : ""} onClick={() => { setEngine("Codex"); setEngineOpen(false); }}>
                <span className="engine-icon codex">✦</span><span><strong>Codex</strong><small>深度财报与多文档研究</small></span><i>{engine === "Codex" ? "✓" : ""}</i>
              </button>
              <button className={engine === "OpenAI 兼容 API" ? "selected" : ""} onClick={() => { setEngine("OpenAI 兼容 API"); setEngineOpen(false); }}>
                <span className="engine-icon api">API</span><span><strong>OpenAI 兼容 API</strong><small>批量分析与自动报告</small></span><i>{engine !== "Codex" ? "✓" : ""}</i>
              </button>
              <p>调用前会展示模型、预计 Token 与费用。</p>
            </div>
          )}
        </div>
        <button className="icon-button" aria-label="打开设置" onClick={onSettings}>⚙<span /></button>
      </div>
    </header>
  );
}

function SearchBox({ onSelectCompany }: { onSelectCompany: (company: ResearchCompany) => void }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const matches = useMemo(() => companies.filter((company) => `${company.name}${company.code}`.toLowerCase().includes(query.toLowerCase())), [query]);

  return (
    <div className="company-search">
      <span aria-hidden="true">⌕</span>
      <input
        aria-label="搜索公司、代码或行业"
        value={query}
        onFocus={() => setOpen(true)}
        onChange={(event) => { setQuery(event.target.value); setOpen(true); }}
        placeholder="搜索公司、代码或行业"
      />
      <kbd>⌘ K</kbd>
      {open && query && (
        <div className="search-results">
          {matches.length ? matches.map((company) => {
            return (
              <button key={company.code} onClick={() => { onSelectCompany(company); setQuery(""); setOpen(false); }}>
                <span className="stock-monogram">{company.name.slice(0, 1)}</span>
                <span><strong>{company.name}</strong><small>{company.code}.{company.market} · {company.industry}</small></span>
                <b>{company.price}</b>
              </button>
            );
          }) : /^\d{6}$/.test(query.trim()) ? <button onClick={() => { onSelectCompany(researchCompany(query)); setQuery(""); setOpen(false); }}><span className="stock-monogram">研</span><span><strong>研究 {query.trim()}</strong><small>任意 A 股代码 · 将拉取真实财务数据</small></span><b>进入</b></button> : <p>输入 6 位 A 股代码即可研究任意公司</p>}
        </div>
      )}
      {open && query && <button className="search-dismiss" aria-label="关闭搜索结果" onClick={() => setOpen(false)} />}
    </div>
  );
}

function EvidenceDrawer({ evidence, onClose }: { evidence: EvidenceSelection | null; onClose: () => void }) {
  if (!evidence) return null;
  const { key, company, analysis, source } = evidence;
  const demoContent = evidenceContent[key];
  const ttmMetrics = asRecord(asRecord(asRecord(analysis?.periods)?.ttm)?.metrics);
  const metricFact = (name: string, label: string, formatter: (value: number | undefined) => string) => {
    const metric = asRecord(ttmMetrics?.[name]);
    const value = typeof metric?.value === "number" && Number.isFinite(metric.value) ? metric.value : undefined;
    const inputs = Array.isArray(metric?.inputs) ? metric.inputs.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
    const reported = inputs.find((item) => item.status === "reported") || inputs[0];
    const inputSource = asRecord(reported?.source);
    const provider = String(inputSource?.provider || source?.provider || "本地财务快照");
    const period = String(reported?.period_end || inputSource?.report_end || analysis?.as_of || "日期待核");
    const inputLabel = String(reported?.as_reported_label || "标准化财务事实");
    return { label, value: value === undefined ? "缺失" : formatter(value), source: `${provider} · ${period} · ${inputLabel}`, missing: value === undefined };
  };
  const metricGroups: Record<Exclude<EvidenceKey, "flag" | "valuation">, Array<[string, string, (value: number | undefined) => string]>> = {
    profit: [["return_on_invested_capital", "ROIC", formatPercent], ["return_on_equity", "ROE", formatPercent], ["gross_margin", "毛利率", formatPercent]],
    cash: [["cash_conversion", "现金含量", formatRatio], ["free_cash_flow", "自由现金流", formatAmount], ["free_cash_flow_dividend_cover", "分红覆盖", formatRatio]],
    balance: [["net_debt", "净债务", formatAmount], ["current_ratio", "流动比率", formatRatio], ["debt_to_equity", "债务 / 权益", formatRatio]],
    capital: [["free_cash_flow_dividend_cover", "分红覆盖", formatRatio], ["dividend_payout", "派息率", formatPercent], ["capital_expenditure", "资本开支", formatAmount]],
    governance: [["return_on_equity", "ROE（治理结果观察）", formatPercent], ["cash_conversion", "现金含量（利润核验）", formatRatio], ["debt_to_equity", "债务 / 权益", formatRatio]],
  };
  const liveFlags = Array.isArray(analysis?.flags) ? analysis.flags.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
  const valuation = asRecord(analysis?.valuation);
  const dcf = asRecord(valuation?.dcf);
  const market = asRecord(analysis?.market);
  const liveFacts = key === "flag"
    ? liveFlags.slice(0, 6).map((flag) => ({ label: String(flag.title || flag.id || "风险信号"), value: flag.severity === "red" ? "红色" : "黄色", source: String(flag.message || flag.reason || "系统规则触发，需回到指标来源复核"), missing: false }))
    : key === "valuation"
      ? [
          { label: "市场价格", value: typeof market?.market_price === "number" ? `¥ ${market.market_price.toFixed(2)}` : typeof dcf?.market_price === "number" ? `¥ ${dcf.market_price.toFixed(2)}` : "缺失", source: `${String(source?.provider || "本地财务快照")} · ${String(market?.price_as_of || analysis?.as_of || "日期待核")}`, missing: typeof market?.market_price !== "number" && typeof dcf?.market_price !== "number" },
          { label: "DCF 状态", value: String(dcf?.status || "不可计算"), source: String(dcf?.reason || "查看估值工作台中的统一日期与假设"), missing: !dcf || dcf.status !== "ok" },
          { label: "财务截止日", value: String(analysis?.as_of || "缺失"), source: "标准化财务快照", missing: !analysis?.as_of },
        ]
      : metricGroups[key].map(([name, label, formatter]) => metricFact(name, label, formatter));
  const missingCount = liveFacts.filter((fact) => fact.missing).length;
  const sourceUrl = String(source?.url || source?.source_url || source?.documentation || "");
  const content = analysis ? {
    eyebrow: `${company.code} · 系统计算证据`,
    title: `${company.name} · ${demoContent.title.split("，")[0]}`,
    summary: `以下内容只来自 ${company.name} 的当前财务快照与确定性公式，数据截至 ${String(analysis.as_of || "日期待核")}。缺失项保持缺失，不会借用演示公司数据。`,
    formula: key === "flag" ? "风险信号 = 阈值规则 + 可追溯输入；不等于舞弊或买卖结论" : demoContent.formula,
    facts: liveFacts,
    judgment: missingCount ? `${missingCount} 项关键输入缺失，因此只能形成待复核条件，不能声称证据完整。` : "当前展示项具备结构化输入；仍需结合公告原文、审计意见和附注独立复核。",
  } : { ...demoContent, facts: demoContent.facts.map((fact) => ({ ...fact, missing: false })) };
  return (
    <div className="drawer-layer" role="dialog" aria-modal="true" aria-labelledby="evidence-title">
      <button className="drawer-backdrop" aria-label="关闭证据详情" onClick={onClose} />
      <aside className="evidence-drawer">
        <div className="drawer-head">
          <div><span>{content.eyebrow}</span><h2 id="evidence-title">{content.title}</h2></div>
          <button onClick={onClose} aria-label="关闭">×</button>
        </div>
        <div className="drawer-body">
          <p className="drawer-summary">{content.summary}</p>
          {content.formula && <div className="formula-box"><small>计算口径</small><code>{content.formula}</code></div>}
          <div className="source-list">
            <div className="section-heading"><h3>关键事实</h3><span>{content.facts.length} 条证据</span></div>
            {content.facts.map((fact, index) => (
              <article key={fact.label}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div><small>{fact.label}</small><strong>{fact.value}</strong><p><i>↗</i>{fact.source}</p></div>
              </article>
            ))}
          </div>
          <div className="judgment-box"><span>{analysis ? "系统判断" : "演示推断"}</span><p>{content.judgment}</p><small>{analysis ? "由本地确定性规则生成 · 不消耗 Token" : "演示内容 · 请勿用于真实投资判断"}</small></div>
          <div className="trace-note"><span>{analysis && missingCount ? "!" : "✓"}</span><p><strong>{analysis && missingCount ? "证据链存在缺口" : analysis ? "当前展示项可追溯" : "演示证据"}</strong><small>{analysis ? (missingCount ? "缺失事实已明确标注，不能推断为正常。" : "仍需打开法定披露核对原文口径。") : "硬编码演示内容，不代表任何真实公司。"}</small></p></div>
        </div>
        <div className="drawer-footer"><button onClick={onClose}>返回研究报告</button>{sourceUrl ? <a className="primary" href={sourceUrl} target="_blank" rel="noreferrer">打开来源 ↗</a> : <button className="primary" disabled>暂无原始链接</button>}</div>
      </aside>
    </div>
  );
}

function Diagnostics({ onEvidence, analysis }: { onEvidence: (key: EvidenceKey) => void; analysis?: UnknownRecord }) {
  const liveCards = analysis ? [
    { key: "profit" as const, index: "01", title: "盈利质量", score: "—" as const, metric: `ROIC ${formatPercent(metricValue(analysis, "return_on_invested_capital"))}`, detail: `ROE ${formatPercent(metricValue(analysis, "return_on_equity"))} · 毛利率 ${formatPercent(metricValue(analysis, "gross_margin"))}`, note: "系统计算；不使用跨行业综合评分。", ...metricStatus(metricValue(analysis, "return_on_invested_capital")) },
    { key: "cash" as const, index: "02", title: "现金质量", score: "—" as const, metric: `现金含量 ${formatRatio(metricValue(analysis, "cash_conversion"))}`, detail: `自由现金流 ${formatAmount(metricValue(analysis, "free_cash_flow"))}`, note: "经营现金流、资本开支与净利润均来自标准化事实。", ...metricStatus(metricValue(analysis, "cash_conversion")) },
    { key: "balance" as const, index: "03", title: "资产负债", score: "—" as const, metric: `净债务 ${formatAmount(metricValue(analysis, "net_debt"))}`, detail: `流动比率 ${formatRatio(metricValue(analysis, "current_ratio"))}`, note: "净债务为有息负债减现金；受限现金需结合附注复核。", ...metricStatus(metricValue(analysis, "net_debt")) },
    { key: "capital" as const, index: "04", title: "资本配置", score: "—" as const, metric: `分红覆盖 ${formatRatio(metricValue(analysis, "free_cash_flow_dividend_cover"))}`, detail: `派息率 ${formatPercent(metricValue(analysis, "dividend_payout"))}`, note: "分红覆盖仅在已披露现金分红与自由现金流可比时计算。", ...metricStatus(metricValue(analysis, "free_cash_flow_dividend_cover")) },
    { key: "governance" as const, index: "05", title: "治理审计", score: "—" as const, metric: `数据质量 ${String(asRecord(analysis.data_quality)?.score ?? "—")}/100`, detail: `截至 ${String(analysis.as_of || "—")}`, note: "质量分数反映口径、勾稽与覆盖情况，不代表投资评级。", ...metricStatus(typeof asRecord(analysis.data_quality)?.score === "number" ? asRecord(analysis.data_quality)?.score as number : undefined) },
  ] : diagnosticCards;
  const cards = liveCards;
  return (
    <section className="section-block">
      <div className="section-heading main-heading"><div><span className="eyebrow">FINANCIAL DIAGNOSTICS</span><h2>五项财务诊断</h2></div><p>{analysis ? `真实数据 · 截至 ${String(analysis.as_of || "—")}` : "基于 5 年年报与近 4 个季度数据"}</p></div>
      <div className="diagnostic-grid">
        {cards.map((card) => (
          <button className={`diagnostic-card ${card.tone}`} key={card.key} onClick={() => onEvidence(card.key)}>
            <div className="diag-top"><span>{card.index}</span><em>{card.status}</em></div>
            <h3>{card.title}</h3>
            <div className="score-row"><strong>{card.score}</strong><small>{card.score === "—" ? "系统计算" : "/ 100"}</small><div className="score-track"><i style={{ width: `${typeof card.score === "number" ? card.score : 0}%` }} /></div></div>
            <div className="diag-metric"><b>{card.metric}</b><span>{card.detail}</span></div>
            <p>{card.note}</p>
            <span className="view-evidence">查看证据 <b>↗</b></span>
          </button>
        ))}
      </div>
    </section>
  );
}

function BankDiagnostics({ analysis, symbol, onUpdated }: { analysis: UnknownRecord; symbol: string; onUpdated: (analysis: UnknownRecord) => void }) {
  const [importOpen, setImportOpen] = useState(false);
  const [importStatus, setImportStatus] = useState("");
  const [periodEnd, setPeriodEnd] = useState(String(analysis.as_of || ""));
  const [sourceDocument, setSourceDocument] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourcePage, setSourcePage] = useState("");
  const [disclosures, setDisclosures] = useState({ net_interest_margin: "", nonperforming_loan_ratio: "", special_mention_ratio: "", overdue_loan_ratio: "", provision_coverage_ratio: "", core_tier1_capital_ratio: "", capital_adequacy_ratio: "" });
  const bank = asRecord(analysis.bank);
  const latest = asRecord(bank?.latest);
  const metrics = asRecord(latest?.metrics) || {};
  const coverage = asRecord(bank?.coverage);
  const flags = Array.isArray(bank?.flags) ? bank.flags.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
  const periods = annualPeriods(analysis);
  const metric = (name: string) => asRecord(metrics[name]);
  const value = (name: string) => typeof metric(name)?.value === "number" ? metric(name)?.value as number : undefined;
  const cards = [
    { key: "net_interest_margin", label: "净息差", value: formatPercent(value("net_interest_margin")), note: "利率定价与存款成本的核心结果" },
    { key: "nonperforming_loan_ratio", label: "不良贷款率", value: formatPercent(value("nonperforming_loan_ratio")), note: "需与关注、逾期和迁徙率一起看" },
    { key: "special_mention_ratio", label: "关注类贷款率", value: formatPercent(value("special_mention_ratio")), note: "观察迁徙风险，不能因尚未转为不良而忽略" },
    { key: "overdue_loan_ratio", label: "逾期贷款率", value: formatPercent(value("overdue_loan_ratio")), note: "先于不良迁徙的资产质量线索，缺失不能视为正常" },
    { key: "provision_coverage_ratio", label: "拨备覆盖", value: formatPercent(value("provision_coverage_ratio")), note: "拨备缓冲，不单独等同资产质量" },
    { key: "core_tier1_capital_ratio", label: "核心一级资本", value: formatPercent(value("core_tier1_capital_ratio")), note: "监管缓冲因银行类别而异" },
    { key: "loan_deposit_ratio", label: "贷存比", value: formatPercent(value("loan_deposit_ratio")), note: "资产投放与存款基础的结构线索" },
    { key: "return_on_equity", label: "ROE", value: formatPercent(value("return_on_equity")), note: "优先使用披露值，否则由平均权益计算" },
  ];
  async function importDisclosures() {
    const supplied = Object.fromEntries(Object.entries(disclosures).filter(([, item]) => item !== "").map(([key, item]) => [key, Number(item)]));
    if (!Object.keys(supplied).length) return setImportStatus("至少填写一项披露指标");
    setImportStatus("正在生成新的可追溯快照…");
    try {
      const result = await apiRequest<{ analysis: UnknownRecord; snapshot_id?: number }>("/api/bank/disclosures", { method: "POST", body: JSON.stringify({ symbol, period_end: periodEnd, source_document: sourceDocument, source_url: sourceUrl, page: sourcePage, metrics: supplied }) });
      onUpdated(result.analysis);
      setImportStatus(`已补录并生成快照 #${result.snapshot_id || "—"}`);
      setImportOpen(false);
    } catch (error) { setImportStatus(error instanceof Error ? error.message : "补录失败"); }
  }
  return <section className="bank-workbench">
    <div className="bank-head"><div><span className="eyebrow">BANK-SPECIFIC FINANCIAL MODEL</span><h2>银行财务驾驶舱</h2><p>银行不使用工业企业的净债务、存货、EBITDA 或通用 FCF 结论。</p></div><div className="bank-head-actions"><button onClick={() => setImportOpen((value) => !value)}>{importOpen ? "收起补录" : "＋ 补录监管指标"}</button><div className="bank-coverage"><strong>{String(coverage?.score ?? 0)}%</strong><span>监管指标覆盖</span></div></div></div>
    {importOpen && <div className="bank-disclosure-form"><div className="bank-disclosure-intro"><strong>从法定财报补录</strong><span>按财报披露的百分数填写，例如净息差 1.70、拨备覆盖率 180。必须填写来源，原快照不会被覆盖。</span></div><div className="bank-disclosure-source"><label>报告期<input type="date" value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)} /></label><label>报告名称<input value={sourceDocument} onChange={(event) => setSourceDocument(event.target.value)} placeholder="2025 年年度报告" /></label><label>原文链接<input value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://…pdf" /></label><label>页码 / 定位<input value={sourcePage} onChange={(event) => setSourcePage(event.target.value)} placeholder="第 76 页" /></label></div><div className="bank-disclosure-metrics">{([{ key: "net_interest_margin", label: "净息差 %" }, { key: "nonperforming_loan_ratio", label: "不良率 %" }, { key: "special_mention_ratio", label: "关注率 %" }, { key: "overdue_loan_ratio", label: "逾期率 %" }, { key: "provision_coverage_ratio", label: "拨备覆盖 %" }, { key: "core_tier1_capital_ratio", label: "核心一级资本 %" }, { key: "capital_adequacy_ratio", label: "资本充足率 %" }] as const).map((item) => <label key={item.key}>{item.label}<input type="number" step="0.01" value={disclosures[item.key]} onChange={(event) => setDisclosures((current) => ({ ...current, [item.key]: event.target.value }))} /></label>)}</div><div className="bank-disclosure-actions"><p>{importStatus || "补录值将标记为“原始事实”，并保留报告、链接、页码及上一版本。"}</p><button onClick={importDisclosures}>保存为新快照</button></div></div>}
    <div className="bank-metric-grid">{cards.map((card) => { const item = metric(card.key); return <article key={card.key} className={item?.status === "ok" ? "ready" : "missing"}><span>{card.label}</span><strong>{card.value}</strong><p>{item?.status === "ok" ? card.note : "数据源未披露，等待财报附注或人工补录"}</p>{item?.status === "ok" && <details><summary>公式与来源</summary><pre>{JSON.stringify({ formula: item.formula, inputs: item.inputs }, null, 2)}</pre></details>}</article>; })}</div>
    <div className="bank-lower-grid">
      <section className="bank-trend"><div><h3>近年核心指标</h3><small>数值为系统计算或原报告值</small></div><div className="bank-trend-table"><div className="header"><span>年度</span><span>净息差</span><span>不良率</span><span>关注率</span><span>逾期率</span><span>拨备覆盖</span><span>核心一级资本</span><span>ROE</span></div>{periods.map((period) => <div key={String(period.end_date)}><b>{String(period.end_date || "—").slice(0, 4)}</b>{["net_interest_margin", "nonperforming_loan_ratio", "special_mention_ratio", "overdue_loan_ratio", "provision_coverage_ratio", "core_tier1_capital_ratio", "return_on_equity"].map((name) => <span key={name}>{formatPercent(annualMetric(period, name))}</span>)}</div>)}</div></section>
      <aside className="bank-flags"><div><h3>银行专项复核</h3><small>{flags.length ? `${flags.length} 条变化线索` : "当前未触发阈值"}</small></div>{flags.length ? flags.map((flag) => <article key={String(flag.id)} className={String(flag.severity)}><span>{flag.severity === "red" ? "高" : "察"}</span><div><strong>{String(flag.title)}</strong><p>{String(flag.detail)}</p><small>{String(flag.review_condition)}</small></div></article>) : <p className="bank-no-flags">没有触发阈值不代表没有风险；仍需核对监管指标缺口与财报附注。</p>}</aside>
    </div>
    {Boolean((coverage?.missing as unknown[])?.length) && <div className="bank-missing"><strong>尚缺关键披露</strong><p>{(coverage?.missing as unknown[]).map(String).join("、")}</p><span>缺失 ≠ 正常，AI 不得自行补值。</span></div>}
  </section>;
}

function BankValuation({ analysis, symbol, industry }: { analysis: UnknownRecord; symbol: string; industry: string }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState("");
  const [margin, setMargin] = useState("20");
  const [valuationAsOf, setValuationAsOf] = useState("");
  const [workbench, setWorkbench] = useState<UnknownRecord>();
  const [inputs, setInputs] = useState([
    { name: "stress", label: "压力", pb: "0.60", roe: "7", cost: "12", growth: "1", terminal: "1", years: "5" },
    { name: "base", label: "基准", pb: "0.90", roe: "11", cost: "10", growth: "3", terminal: "2", years: "5" },
    { name: "optimistic", label: "乐观", pb: "1.20", roe: "14", cost: "9", growth: "5", terminal: "2.5", years: "5" },
  ]);
  const effective = workbench || analysis;
  const valuation = asRecord(effective.valuation);
  const pb = asRecord(valuation?.pb);
  const residual = asRecord(valuation?.residual_income);
  const bvps = asRecord(valuation?.book_value_per_share);
  const currentPb = asRecord(valuation?.current_pb);
  const summary = asRecord(valuation?.workbench_summary);
  const dateContext = asRecord(summary?.date_context);
  const market = asRecord(effective.market);
  const effectiveValuationAsOf = valuationAsOf || String(market?.price_as_of || effective.as_of || "");
  const ranges = Array.isArray(summary?.method_ranges) ? summary.method_ranges.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
  const scenarios = Array.isArray(pb?.scenarios) ? pb.scenarios.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
  const residualScenarios = Array.isArray(residual?.scenarios) ? residual.scenarios.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
  async function run() {
    if (!effectiveValuationAsOf) return setStatus("请先填写统一估值截止日");
    setStatus("正在用本地公式计算…");
    try {
      const result = await apiRequest<{ analysis: UnknownRecord; run_id?: number }>("/api/bank/valuation/run", { method: "POST", body: JSON.stringify({ symbol, industry, as_of: effectiveValuationAsOf, save: true, assumptions: { assumptions_as_of: effectiveValuationAsOf, margin_of_safety: Number(margin) / 100, scenarios: inputs.map((item) => ({ name: item.name, target_pb: Number(item.pb), roe: Number(item.roe) / 100, cost_of_equity: Number(item.cost) / 100, book_value_growth: Number(item.growth) / 100, terminal_growth: Number(item.terminal) / 100, forecast_years: Number(item.years) })) } }) });
      setWorkbench(result.analysis);
      setStatus(`已保存银行估值快照 #${result.run_id || "—"}`);
    } catch (error) { setStatus(error instanceof Error ? error.message : "银行估值失败"); }
  }
  return <section className="panel bank-valuation">
    <SectorValuationHistory key={symbol} symbol={symbol} revision={workbench} />
    <div className="panel-heading"><div><span className="eyebrow">BANK VALUATION</span><h2>PB × 剩余收益估值</h2></div><button className="evidence-link" onClick={() => setOpen((value) => !value)}>{open ? "收起假设" : "调整假设"}</button></div>
    <div className="bank-valuation-context"><article><span>每股净资产</span><strong>{typeof bvps?.value === "number" ? `¥${bvps.value.toFixed(2)}` : "缺失"}</strong></article><article><span>当前 PB</span><strong>{typeof currentPb?.value === "number" ? `${currentPb.value.toFixed(2)}×` : "缺失"}</strong></article><p>银行估值不使用工业企业 DCF。PB 与剩余收益分别展示，不平均成一个目标价。</p></div>
    {open && <label className="valuation-cutoff-control"><span>统一估值截止日 *</span><input type="date" value={effectiveValuationAsOf} onChange={(event) => setValuationAsOf(event.target.value)} /><small>价格、股本、财务与本次假设都必须不晚于该日期。</small></label>}
    {open && <div className="bank-valuation-editor"><div className="bank-editor-note"><strong>以下均为用户假设</strong><span>预填值只是可编辑的情景模板，不是系统预测。运行前请逐项确认。</span><label>安全边际 %<input type="number" min="0" max="80" value={margin} onChange={(event) => setMargin(event.target.value)} /></label></div><div className="bank-input-table"><div className="header"><span>情景</span><span>目标 PB</span><span>ROE %</span><span>权益成本 %</span><span>净资产增速 %</span><span>永续增长 %</span><span>预测年</span></div>{inputs.map((item, index) => <div key={item.name}><b>{item.label}</b>{(["pb", "roe", "cost", "growth", "terminal", "years"] as const).map((key) => <input key={key} type="number" step={key === "years" ? "1" : "0.1"} value={item[key]} onChange={(event) => setInputs((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, [key]: event.target.value } : row))} aria-label={`${item.label}${key}`} />)}</div>)}</div><div className="bank-editor-actions"><p>{status || "本地计算，零 Token；每次运行保存假设和数据指纹。"}</p><button onClick={run}>计算并保存</button></div></div>}
    <div className="bank-scenario-grid">{inputs.map((input) => { const p = scenarios.find((item) => item.name === input.name); const r = residualScenarios.find((item) => item.name === input.name); return <article key={input.name}><span>{input.label}</span><div><small>PB 锚点</small><strong>{typeof p?.value_per_share === "number" ? `¥${p.value_per_share.toFixed(2)}` : "待计算"}</strong></div><div><small>剩余收益</small><strong>{typeof r?.value_per_share === "number" ? `¥${r.value_per_share.toFixed(2)}` : "待计算"}</strong></div></article>; })}</div>
    {ranges.length > 0 && <div className="bank-range-grid">{ranges.map((item) => <article key={String(item.method)}><span>{String(item.label)}价值区间</span><strong>¥{Number(item.value_range_low).toFixed(2)}—{Number(item.value_range_high).toFixed(2)}</strong><p>{Number(item.margin_of_safety) * 100}% 安全边际后：¥{Number(item.margin_price_low).toFixed(2)}—{Number(item.margin_price_high).toFixed(2)}</p></article>)}</div>}
    {dateContext && <div className={`valuation-date-context ${dateContext.status}`}><span>{dateContext.status === "complete" ? "✓" : "!"}</span><p><strong>银行估值日期口径</strong>财务 {String(dateContext.financial_period_end || "缺失")} · 价格 {String(dateContext.price_as_of || "缺失")} · 股本 {String(dateContext.shares_as_of || "缺失")} · 假设 {String(dateContext.assumptions_as_of || "缺失")} · 截止 {String(dateContext.as_of_request || "缺失")}<small>{String(dateContext.warning || "")}</small></p></div>}
  </section>;
}

function SectorDiagnostics({ analysis, symbol, industry, onUpdated }: { analysis: UnknownRecord; symbol: string; industry: string; onUpdated: (analysis: UnknownRecord) => void }) {
  const support = asRecord(analysis.support);
  const template = String(support?.sector_template || "");
  const sector = asRecord(analysis.sector);
  const latest = asRecord(sector?.latest);
  const metrics = asRecord(latest?.metrics) || {};
  const coverage = asRecord(sector?.coverage);
  const flags = Array.isArray(sector?.flags) ? sector.flags.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState("");
  const [periodEnd, setPeriodEnd] = useState(String(analysis.as_of || ""));
  const [sourceDocument, setSourceDocument] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [page, setPage] = useState("");
  const [productionUnit, setProductionUnit] = useState("million tonnes");
  const [priceUnit, setPriceUnit] = useState("CNY/tonne");
  const [values, setValues] = useState<Record<string, string>>({});
  const resource = template === "cyclical_resource";
  const property = template === "real_estate";
  const insurance = template === "insurance";
  const brokerage = template === "brokerage";
  const configs = resource ? [
    ["production_volume", "产量"], ["realized_price", "实现价格"], ["unit_cash_cost", "单位现金成本"], ["proven_probable_reserves", "经济可采储量"], ["sustaining_capex", "维持性资本开支"], ["expansion_capex", "扩张性资本开支"],
  ] : property ? [
    ["contract_liabilities", "合同负债 百万元"], ["sales_collections", "销售回款 百万元"], ["presales", "签约销售额 百万元"], ["inventory", "项目存货 百万元"], ["project_inventory_impairment", "项目存货减值 百万元"], ["cash", "货币资金 百万元"], ["restricted_cash", "受限资金 百万元"], ["interest_bearing_debt", "有息负债 百万元"], ["debt_due_within_one_year", "一年内到期债务 百万元"], ["external_guarantees", "对外担保 百万元"],
  ] : insurance ? [
    ["insurance_service_revenue", "保险服务收入 百万元"], ["gross_written_premiums", "原保险保费 百万元"], ["new_business_value", "新业务价值 百万元"], ["embedded_value", "内含价值 百万元"], ["net_investment_yield", "净投资收益率 %"], ["comprehensive_investment_yield", "综合投资收益率 %"], ["insurance_contract_liabilities", "保险合同负债 百万元"], ["total_investment_assets", "投资资产 百万元"], ["core_solvency_ratio", "核心偿付能力 %"], ["comprehensive_solvency_ratio", "综合偿付能力 %"],
  ] : brokerage ? [
    ["brokerage_net_revenue", "经纪净收入 百万元"], ["investment_banking_net_revenue", "投行净收入 百万元"], ["asset_management_net_revenue", "资管净收入 百万元"], ["net_interest_income", "利息净收入 百万元"], ["trading_investment_income", "投资与交易收益 百万元"], ["net_capital", "净资本 百万元"], ["risk_coverage_ratio", "风险覆盖率 %"], ["capital_leverage_ratio", "资本杠杆率 %"], ["liquidity_coverage_ratio", "流动性覆盖率 %"], ["net_stable_funding_ratio", "净稳定资金率 %"],
  ] : [
    ["installed_capacity", "装机容量 MW"], ["utilization_hours", "利用小时"], ["average_tariff", "平均电价 元/kWh"], ["fuel_cost_per_kwh", "燃料成本 元/kWh"], ["regulated_asset_base", "受监管资产 百万元"], ["allowed_return", "准许收益率 %"], ["sustaining_capex", "维持性资本开支"], ["expansion_capex", "扩张性资本开支"],
  ];
  const cardConfigs = resource ? [
    ["production_volume", "产量", "number"], ["realized_price", "实现价格", "number"], ["unit_cash_cost", "单位现金成本", "number"], ["cash_margin_ratio", "单位现金毛利率", "percent"], ["reserve_life_years", "储量寿命", "years"], ["sustaining_capex_coverage", "维持开支覆盖", "ratio"],
  ] : property ? [
    ["contract_liabilities", "合同负债", "number"], ["sales_collections", "销售回款", "number"], ["presales_growth", "预售增速", "percent"], ["restricted_cash_ratio", "受限资金占比", "percent"], ["short_debt_cash_coverage", "可用现金/短债", "ratio"], ["inventory_impairment_ratio", "存货减值率", "percent"],
  ] : insurance ? [
    ["insurance_service_revenue", "保险服务收入", "number"], ["new_business_value_growth", "新业务价值增速", "percent"], ["new_business_value_margin", "新业务价值率", "percent"], ["embedded_value_per_share", "每股内含价值", "number"], ["core_solvency_ratio", "核心偿付能力", "percent"], ["comprehensive_solvency_ratio", "综合偿付能力", "percent"],
  ] : brokerage ? [
    ["revenue", "营业收入", "number"], ["brokerage_revenue_share", "经纪收入占比", "percent"], ["trading_revenue_share", "交易收入占比", "percent"], ["return_on_equity", "ROE", "percent"], ["risk_coverage_ratio", "风险覆盖率", "percent"], ["capital_leverage_ratio", "资本杠杆率", "percent"],
  ] : [
    ["installed_capacity", "装机容量", "number"], ["utilization_hours", "利用小时", "number"], ["average_tariff", "平均电价", "number"], ["tariff_fuel_spread", "电价燃料价差", "number"], ["sustaining_capex_coverage", "维持开支覆盖", "ratio"], ["debt_to_equity", "有息负债/权益", "ratio"],
  ];
  function display(name: string, kind: string) {
    const item = asRecord(metrics[name]); const number = typeof item?.value === "number" ? item.value : undefined;
    if (kind === "percent") return formatPercent(number);
    if (kind === "ratio") return formatRatio(number);
    if (kind === "years") return number === undefined ? "缺失" : `${number.toFixed(1)} 年`;
    return number === undefined ? "缺失" : `${number.toLocaleString("zh-CN", { maximumFractionDigits: 2 })} ${String(item?.unit || "")}`;
  }
  const title = resource ? "周期资源经营驾驶舱" : property ? "房地产资金与项目驾驶舱" : insurance ? "保险价值与偿付能力驾驶舱" : brokerage ? "券商业务与净资本驾驶舱" : "公用事业经营驾驶舱";
  const description = resource ? "用产量、实现价格、单位现金成本、储量和维持性资本开支解释周期利润。" : property ? "用合同负债、销售回款、受限资金、项目存货减值和债务到期解释流动性与资产质量。" : insurance ? "用保险服务收入、新业务价值、内含价值、投资收益与偿付能力解释经营质量。" : brokerage ? "用经纪、投行、资管、自营收入与净资本和流动性监管指标解释周期与风险。" : "用装机、利用小时、电价、燃料成本、受监管资产和资本开支解释回报。";
  async function submit() {
    const supplied = Object.fromEntries(Object.entries(values).filter(([, value]) => value !== "").map(([key, value]) => [key, Number(value)]));
    if (!Object.keys(supplied).length) return setStatus("至少填写一项经营披露");
    const units: Record<string, string> = resource ? { production_volume: productionUnit, proven_probable_reserves: productionUnit, realized_price: priceUnit, unit_cash_cost: priceUnit } : {};
    setStatus("正在生成新的行业快照…");
    try {
      const result = await apiRequest<{ analysis: UnknownRecord; snapshot_id?: number }>("/api/sector/disclosures", { method: "POST", body: JSON.stringify({ symbol, industry, sector_template: template, period_end: periodEnd, source_document: sourceDocument, source_url: sourceUrl, page, metrics: supplied, units }) });
      onUpdated(result.analysis); setOpen(false); setStatus(`已生成快照 #${result.snapshot_id || "—"}`);
    } catch (error) { setStatus(error instanceof Error ? error.message : "补录失败"); }
  }
  return <section className={`sector-workbench ${template}`}>
    <div className="sector-head"><div><span className="eyebrow">SECTOR-SPECIFIC MODEL</span><h2>{title}</h2><p>{description}</p></div><div className="sector-head-actions"><button onClick={() => setOpen((value) => !value)}>{open ? "收起补录" : "＋ 补录经营披露"}</button><div><strong>{String(coverage?.score ?? 0)}%</strong><span>专用指标覆盖</span></div></div></div>
    {open && <div className="sector-disclosure"><div className="sector-source-row"><label>报告期<input type="date" value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)} /></label><label>报告名称<input value={sourceDocument} onChange={(event) => setSourceDocument(event.target.value)} placeholder="2025 年年度报告" /></label><label>原文链接<input value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://…" /></label><label>页码 / 定位<input value={page} onChange={(event) => setPage(event.target.value)} /></label>{resource && <><label>产量/储量单位<input value={productionUnit} onChange={(event) => setProductionUnit(event.target.value)} /></label><label>价格/成本单位<input value={priceUnit} onChange={(event) => setPriceUnit(event.target.value)} /></label></>}</div><div className="sector-metric-inputs">{configs.map(([key, label]) => <label key={key}>{label}<input type="number" step="0.01" value={values[key] || ""} onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value }))} /></label>)}</div><div className="sector-form-actions"><p>{status || "补录必须带来源；旧快照保持不变。"}</p><button onClick={submit}>保存为新快照</button></div></div>}
    <div className="sector-metric-grid">{cardConfigs.map(([key, label, kind]) => { const item = asRecord(metrics[key]); return <article key={key} className={item?.status === "ok" ? "ready" : "missing"}><span>{label}</span><strong>{display(key, kind)}</strong><p>{item?.status === "ok" ? String(item.formula || "原报告值") : "缺失：等待财报经营披露"}</p>{item?.status === "ok" && <details><summary>查看来源</summary><pre>{JSON.stringify(item.inputs, null, 2)}</pre></details>}</article>; })}</div>
    <div className="sector-flags"><div><h3>专项复核信号</h3><small>{flags.length ? `${flags.length} 条` : "未触发阈值"}</small></div>{flags.length ? flags.map((flag) => <article key={String(flag.id)} className={String(flag.severity)}><span>{flag.severity === "red" ? "高" : "察"}</span><div><strong>{String(flag.title)}</strong><p>{String(flag.detail)}</p><small>{String(flag.review_condition)}</small></div></article>) : <p>没有触发阈值不代表风险为零；先补齐缺失经营指标，再核对财报附注。</p>}</div>
    {Boolean((coverage?.missing as unknown[])?.length) && <div className="sector-missing"><strong>尚缺专用披露</strong><p>{(coverage?.missing as unknown[]).map(String).join("、")}</p><span>缺失不会被 AI 补成“正常”。</span></div>}
  </section>;
}

function SectorValuation({ analysis, symbol, industry }: { analysis: UnknownRecord; symbol: string; industry: string }) {
  const support = asRecord(analysis.support); const template = String(support?.sector_template || ""); const resource = template === "cyclical_resource"; const property = template === "real_estate"; const insurance = template === "insurance"; const brokerage = template === "brokerage";
  const sector = asRecord(analysis.sector); const latest = asRecord(sector?.latest); const metrics = asRecord(latest?.metrics) || {};
  const number = (name: string) => { const item = asRecord(metrics[name]); return typeof item?.value === "number" ? item.value : undefined; };
  const baseVolume = number("production_volume"); const basePrice = number("realized_price"); const baseCost = number("unit_cash_cost"); const baseReserves = number("reserve_life_years") && baseVolume ? number("reserve_life_years")! * baseVolume : undefined; const baseRab = number("regulated_asset_base"); const baseReturn = number("allowed_return"); const baseImpairment = number("inventory_impairment_ratio"); const baseGrossMargin = number("gross_margin"); const baseNbv = number("new_business_value"); const baseNetIncome = number("net_income");
  const [open, setOpen] = useState(false); const [status, setStatus] = useState(""); const [margin, setMargin] = useState("20"); const [valuationAsOf, setValuationAsOf] = useState(""); const [workbench, setWorkbench] = useState<UnknownRecord>();
  const [rows, setRows] = useState(() => [
    { name: "stress", label: "压力", volume: baseVolume ? String(baseVolume * .9) : "", price: basePrice ? String(basePrice * .85) : "", cost: baseCost ? String(baseCost * 1.1) : "", tax: "25", pe: resource ? "6" : brokerage ? "10" : "10", reserves: baseReserves ? String(baseReserves) : "", recovery: "70", discount: "12", life: "12", rab: baseRab ? String(baseRab * .95) : "", allowed: baseReturn ? String(baseReturn * 100 * .9) : "", dividendGrowth: "0", equityCost: "11", terminal: "1", years: "5", haircut: String(Math.max(20, (baseImpairment || 0) * 100 * 2)), recognition: "50", grossMargin: String((baseGrossMargin || .15) * 100 * .8), targetPb: "0.45", additionalLoss: "0", targetPev: "0.55", normalizedNbv: baseNbv ? String(baseNbv * .8) : "", nbvMultiple: "3", evHaircut: "20", normalizedIncome: baseNetIncome ? String(baseNetIncome * .75) : "" },
    { name: "base", label: "基准", volume: baseVolume ? String(baseVolume) : "", price: basePrice ? String(basePrice) : "", cost: baseCost ? String(baseCost) : "", tax: "25", pe: resource ? "8" : brokerage ? "15" : "12", reserves: baseReserves ? String(baseReserves) : "", recovery: "80", discount: "10", life: "12", rab: baseRab ? String(baseRab) : "", allowed: baseReturn ? String(baseReturn * 100) : "", dividendGrowth: "3", equityCost: "9", terminal: "2", years: "5", haircut: String(Math.max(10, (baseImpairment || 0) * 100)), recognition: "70", grossMargin: String((baseGrossMargin || .18) * 100), targetPb: "0.9", additionalLoss: "0", targetPev: "0.8", normalizedNbv: baseNbv ? String(baseNbv) : "", nbvMultiple: "5", evHaircut: "10", normalizedIncome: baseNetIncome ? String(baseNetIncome) : "" },
    { name: "optimistic", label: "乐观", volume: baseVolume ? String(baseVolume * 1.05) : "", price: basePrice ? String(basePrice * 1.15) : "", cost: baseCost ? String(baseCost * .95) : "", tax: "25", pe: resource ? "10" : brokerage ? "20" : "14", reserves: baseReserves ? String(baseReserves) : "", recovery: "90", discount: "9", life: "12", rab: baseRab ? String(baseRab * 1.05) : "", allowed: baseReturn ? String(baseReturn * 100 * 1.05) : "", dividendGrowth: "5", equityCost: "8", terminal: "2.5", years: "5", haircut: String(Math.max(5, (baseImpairment || 0) * 100 * .5)), recognition: "85", grossMargin: String((baseGrossMargin || .2) * 100 * 1.1), targetPb: "1.25", additionalLoss: "0", targetPev: "1.05", normalizedNbv: baseNbv ? String(baseNbv * 1.2) : "", nbvMultiple: "7", evHaircut: "0", normalizedIncome: baseNetIncome ? String(baseNetIncome * 1.2) : "" },
  ]);
  const effective = workbench || analysis; const valuation = asRecord(effective.valuation); const summary = asRecord(valuation?.workbench_summary); const dateContext = asRecord(summary?.date_context); const market = asRecord(effective.market); const effectiveValuationAsOf = valuationAsOf || String(market?.price_as_of || effective.as_of || ""); const ranges = Array.isArray(summary?.method_ranges) ? summary.method_ranges.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
  const resourceScenarios = Array.isArray(valuation?.scenarios) ? valuation.scenarios.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
  const regulated = asRecord(valuation?.regulated_return); const regulatedScenarios = Array.isArray(regulated?.scenarios) ? regulated.scenarios.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : []; const ddm = asRecord(valuation?.ddm); const ddmScenarios = Array.isArray(ddm?.scenarios) ? ddm.scenarios.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
  const fields = resource ? [["volume", "正常化产量"], ["price", "正常化价格"], ["cost", "单位成本"], ["tax", "税率 %"], ["pe", "目标 PE"], ["reserves", "可采储量"], ["recovery", "经济可采 %"], ["discount", "折现率 %"], ["life", "矿山寿命"]] as const
    : property ? [["haircut", "存货折价 %"], ["recognition", "合同结转 %"], ["grossMargin", "正常毛利率 %"], ["tax", "税率 %"], ["targetPb", "目标 PB"], ["additionalLoss", "额外损失准备"]] as const
    : insurance ? [["targetPev", "目标 P/EV"], ["normalizedNbv", "正常化 NBV"], ["nbvMultiple", "NBV 倍数"], ["evHaircut", "EV 折价 %"]] as const
    : brokerage ? [["targetPb", "目标 PB"], ["pe", "目标 PE"], ["normalizedIncome", "正常化净利润"]] as const
    : [["rab", "正常化 RAB"], ["allowed", "准许收益 %"], ["tax", "税率 %"], ["pe", "目标 PE"], ["dividendGrowth", "股息增长 %"], ["equityCost", "权益成本 %"], ["terminal", "永续增长 %"], ["years", "预测年"]] as const;
  const complete = rows.every((row) => fields.every(([key]) => row[key] !== ""));
  async function run() {
    if (!complete) return setStatus("请先补齐三组情景假设");
    if (!effectiveValuationAsOf) return setStatus("请先填写统一估值截止日");
    setStatus("正在用本地公式计算…");
    try {
      const scenarios = rows.map((row) => resource ? { name: row.name, normalized_volume: Number(row.volume), normalized_price: Number(row.price), unit_cash_cost: Number(row.cost), tax_rate: Number(row.tax) / 100, target_pe: Number(row.pe), recoverable_reserves: Number(row.reserves), recovery_factor: Number(row.recovery) / 100, discount_rate: Number(row.discount) / 100, mine_life_years: Number(row.life) }
        : property ? { name: row.name, inventory_haircut: Number(row.haircut) / 100, contract_recognition_rate: Number(row.recognition) / 100, normalized_gross_margin: Number(row.grossMargin) / 100, tax_rate: Number(row.tax) / 100, target_pb: Number(row.targetPb), additional_loss_provision: Number(row.additionalLoss) }
        : insurance ? { name: row.name, target_pev: Number(row.targetPev), normalized_nbv: Number(row.normalizedNbv), nbv_multiple: Number(row.nbvMultiple), ev_haircut: Number(row.evHaircut) / 100 }
        : brokerage ? { name: row.name, target_pb: Number(row.targetPb), target_pe: Number(row.pe), normalized_net_income: Number(row.normalizedIncome) }
        : { name: row.name, normalized_rab: Number(row.rab), allowed_return: Number(row.allowed) / 100, tax_rate: Number(row.tax) / 100, target_pe: Number(row.pe), dividend_growth: Number(row.dividendGrowth) / 100, cost_of_equity: Number(row.equityCost) / 100, terminal_growth: Number(row.terminal) / 100, forecast_years: Number(row.years) });
      const result = await apiRequest<{ analysis: UnknownRecord; run_id?: number }>("/api/sector/valuation/run", { method: "POST", body: JSON.stringify({ symbol, industry, sector_template: template, as_of: effectiveValuationAsOf, save: true, assumptions: { assumptions_as_of: effectiveValuationAsOf, margin_of_safety: Number(margin) / 100, scenarios } }) });
      setWorkbench(result.analysis); setStatus(`已保存行业估值快照 #${result.run_id || "—"}`);
    } catch (error) { setStatus(error instanceof Error ? error.message : "行业估值失败"); }
  }
  const valuationTitle = resource ? "中周期盈利 × 储量 NAV" : property ? "合同结转调整 NAV × 资产折价 PB" : insurance ? "内含价值 P/EV × EV+NBV 评估价值" : brokerage ? "净资产 PB × 正常化盈利 PE" : "受监管资产收益 × DDM";
  const policyText = resource ? "不拿周期峰值利润直接套 PE；正常化经营与储量 NAV 分开看。" : property ? "不把账面存货和受限资金当作可随时变现；存货折价、合同结转和额外损失均为显式假设。" : insurance ? "内含价值与新业务价值必须采用公司披露口径；P/EV 与 EV+NBV 评估价值分开显示。" : brokerage ? "自营收益具有周期性；净资产锚点与正常化盈利锚点分开显示，不把当期交易收益永续化。" : "受监管资产盈利能力与股息价值分开看；维持性和扩张性资本开支不得混为一谈。";
  function scenarioResult(name: string) {
    const item = resourceScenarios.find((candidate) => candidate.name === name);
    if (resource) return { first: asRecord(item?.midcycle), second: asRecord(item?.nav), firstLabel: "中周期价值", secondLabel: "储量 NAV" };
    if (property) return { first: asRecord(item?.adjusted_nav), second: asRecord(item?.adjusted_pb), firstLabel: "调整 NAV", secondLabel: "资产折价 PB" };
    if (insurance) return { first: asRecord(item?.pev), second: asRecord(item?.appraisal_value), firstLabel: "P/EV", secondLabel: "EV+NBV" };
    if (brokerage) return { first: asRecord(item?.pb), second: asRecord(item?.normalized_pe), firstLabel: "净资产 PB", secondLabel: "正常化 PE" };
    return { first: regulatedScenarios.find((candidate) => candidate.name === name), second: ddmScenarios.find((candidate) => candidate.name === name), firstLabel: "受监管收益价值", secondLabel: "DDM" };
  }
  return <section className="panel sector-valuation">
    <SectorValuationHistory key={symbol} symbol={symbol} revision={workbench} />
    <div className="panel-heading"><div><span className="eyebrow">SECTOR VALUATION</span><h2>{valuationTitle}</h2></div><button className="evidence-link" onClick={() => setOpen((value) => !value)}>{open ? "收起假设" : "调整假设"}</button></div>
    <p className="sector-valuation-policy">{policyText}</p>
    {open && <label className="valuation-cutoff-control"><span>统一估值截止日 *</span><input type="date" value={effectiveValuationAsOf} onChange={(event) => setValuationAsOf(event.target.value)} /><small>价格、股本、财务与本次假设都必须不晚于该日期。</small></label>}
    {open && <div className="sector-valuation-editor"><div className="sector-editor-top"><strong>用户情景假设</strong><span>预填值由当前披露机械生成，只是起点，不是系统预测。</span><label>安全边际 %<input type="number" value={margin} onChange={(event) => setMargin(event.target.value)} /></label></div>{rows.map((row, index) => <div className="sector-scenario-editor" key={row.name}><b>{row.label}</b><div>{fields.map(([key, label]) => <label key={key}>{label}<input type="number" step="0.01" value={row[key]} onChange={(event) => setRows((current) => current.map((item, rowIndex) => rowIndex === index ? { ...item, [key]: event.target.value } : item))} /></label>)}</div></div>)}<div className="sector-editor-actions"><p>{status || "本地计算，零 Token；保存假设与数据指纹。"}</p><button onClick={run}>计算并保存</button></div></div>}
    <div className="sector-valuation-results">{rows.map((row) => { const result = scenarioResult(row.name); return <article key={row.name}><span>{row.label}</span><div><small>{result.firstLabel}</small><strong>{typeof result.first?.value_per_share === "number" ? `¥${result.first.value_per_share.toFixed(2)}` : "待计算"}</strong></div><div><small>{result.secondLabel}</small><strong>{typeof result.second?.value_per_share === "number" ? `¥${result.second.value_per_share.toFixed(2)}` : "待计算"}</strong></div></article>; })}</div>
    {ranges.length > 0 && <div className="sector-range-grid">{ranges.map((item) => <article key={String(item.method)}><span>{String(item.label)}区间</span><strong>¥{Number(item.value_range_low).toFixed(2)}—{Number(item.value_range_high).toFixed(2)}</strong><p>安全边际后 ¥{Number(item.margin_price_low).toFixed(2)}—{Number(item.margin_price_high).toFixed(2)}</p></article>)}</div>}
    {dateContext && <div className={`valuation-date-context ${dateContext.status}`}><span>{dateContext.status === "complete" ? "✓" : "!"}</span><p><strong>行业估值日期口径</strong>财务 {String(dateContext.financial_period_end || "缺失")} · 价格 {String(dateContext.price_as_of || "缺失")} · 股本 {String(dateContext.shares_as_of || "缺失")} · 假设 {String(dateContext.assumptions_as_of || "缺失")} · 截止 {String(dateContext.as_of_request || "缺失")}<small>{String(dateContext.warning || "")}</small></p></div>}
  </section>;
}

function FinancialTrend({ analysis }: { analysis?: UnknownRecord }) {
  const [trend, setTrend] = useState<TrendKey>("profit");
  const livePeriods = annualPeriods(analysis);
  const liveConfig: Record<TrendKey, { label: string; unit: string; primary: string; secondary: string }> = {
    profit: { label: "营收 / 归母净利", unit: "百万元", primary: "revenue", secondary: "net_income" },
    cash: { label: "经营现金流 / 自由现金流", unit: "百万元", primary: "operating_cash_flow", secondary: "free_cash_flow" },
    return: { label: "ROE / ROIC", unit: "%", primary: "return_on_equity", secondary: "return_on_invested_capital" },
  };
  const live = analysis ? liveConfig[trend] : undefined;
  const data = live ? {
    label: live.label,
    unit: live.unit,
    values: livePeriods.map((period) => annualMetric(period, live.primary) ?? null),
    comparison: livePeriods.map((period) => annualMetric(period, live.secondary) ?? null),
    valueLabels: livePeriods.map((period) => live.unit === "%" ? formatPercent(annualMetric(period, live.primary)) : formatAmount(annualMetric(period, live.primary))),
    comparisonLabels: livePeriods.map((period) => live.unit === "%" ? formatPercent(annualMetric(period, live.secondary)) : formatAmount(annualMetric(period, live.secondary))),
  } : trendData[trend];
  const finiteValues = [...data.values, ...data.comparison].filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const max = Math.max(1e-9, ...finiteValues.map((value) => Math.abs(value)));
  const years = live ? livePeriods.map((period) => String(period.end_date || period.label || "—").slice(0, 4)) : ["2021", "2022", "2023", "2024", "2025"];
  return (
    <section className="panel trend-panel">
      <div className="panel-heading">
        <div><span className="eyebrow">5-YEAR TREND</span><h2>财务趋势</h2></div>
        <div className="segmented" role="group" aria-label="趋势指标">
          <button className={trend === "profit" ? "active" : ""} onClick={() => setTrend("profit")}>利润</button>
          <button className={trend === "cash" ? "active" : ""} onClick={() => setTrend("cash")}>现金流</button>
          <button className={trend === "return" ? "active" : ""} onClick={() => setTrend("return")}>回报率</button>
        </div>
      </div>
      <div className="chart-legend"><span><i className="legend-primary" />{data.label.split(" / ")[0]}</span><span><i className="legend-secondary" />{data.label.split(" / ")[1]}</span><small>单位：{data.unit}</small></div>
      <div className="bar-chart" aria-label={`${data.label}五年趋势图`}>
        <div className="chart-grid"><i /><i /><i /><i /></div>
        {years.map((year, index) => {
          const primary = data.values[index];
          const secondary = data.comparison[index];
          return <div className="year-group" key={`${year}-${index}`}>
            <div className="bars">
              <div className={`bar primary ${primary === null ? "missing" : primary < 0 ? "negative" : ""}`} style={{ height: primary === null ? "0%" : `${Math.max(4, (Math.abs(primary) / max) * 100)}%` }}><span>{data.valueLabels[index]}</span></div>
              <div className={`bar secondary ${secondary === null ? "missing" : secondary < 0 ? "negative" : ""}`} style={{ height: secondary === null ? "0%" : `${Math.max(4, (Math.abs(secondary) / max) * 100)}%` }}><span>{data.comparisonLabels[index]}</span></div>
            </div>
            <b>{year}</b>
          </div>;
        })}
        {live && !years.length && <div className="chart-empty">当前没有可展示的年度序列；不会使用演示数据补位。</div>}
      </div>
      <div className="trend-footer"><span className="trend-badge">{live ? "✓ 系统序列" : "演示数据"}</span><p>{live ? (years.length ? `展示 ${years[0]}—${years[years.length - 1]} 年度标准化序列；缺失值不画柱、不补零。` : "年度序列缺失，等待数据补齐。") : <>2025 年利润增速 <b>+6.0%</b>，自由现金流增速 <b>+9.7%</b>，现金增长快于利润。</>}</p></div>
    </section>
  );
}

function RiskRadar({ onEvidence, analysis }: { onEvidence: (key: EvidenceKey) => void; analysis?: UnknownRecord }) {
  const [expanded, setExpanded] = useState(false);
  const liveFlags = Array.isArray(analysis?.flags) ? analysis.flags.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
  const visibleFlags = expanded ? liveFlags : liveFlags.slice(0, 4);
  const redCount = liveFlags.filter((flag) => flag.severity === "red").length;
  const yellowCount = liveFlags.filter((flag) => flag.severity === "yellow").length;
  return (
    <section className="panel risk-panel">
      <div className="panel-heading"><div><span className="eyebrow">RISK RADAR</span><h2>财务雷达</h2></div>{analysis ? <button className="more-button" disabled={liveFlags.length <= 4} onClick={() => setExpanded((value) => !value)}>{expanded ? "收起" : `全部 ${liveFlags.length} 项`}</button> : <span className="more-button">演示 4 项</span>}</div>
      <div className="risk-summary"><div className="risk-ring"><strong>{analysis ? redCount + yellowCount : 1}</strong><span>项关注</span></div><p>{analysis ? (redCount ? `${redCount} 项高危信号` : "未发现高危财务信号") : "未发现高危财务信号"}<br/><small>{analysis ? `${yellowCount} 项中度关注 · 数据质量需结合原文复核` : "1 项中度关注 · 11 项正常"}</small></p></div>
      {analysis ? (liveFlags.length ? visibleFlags.map((flag, index) => {
        const severe = flag.severity === "red";
        return <button className={`risk-item ${severe ? "critical" : "warning"}`} key={String(flag.id || index)} onClick={() => onEvidence("flag")}><span className="risk-icon">{severe ? "×" : "!"}</span><div><strong>{String(flag.title || flag.message || flag.id || "待核查信号")}</strong><p>{String(flag.message || flag.reason || "需要结合原始披露进一步核查")}</p></div><b>查看 ›</b></button>;
      }) : <div className="risk-item"><span className="risk-icon ok">✓</span><div><strong>未发现已计算红旗</strong><p>仍需查看附注、审计意见与非经常性损益。</p></div></div>) : <>
        <button className="risk-item warning" onClick={() => onEvidence("flag")}><span className="risk-icon">!</span><div><strong>资本开支增速偏快</strong><p>同比 +18.6%，显著高于收入增速</p></div><b>查看 ›</b></button>
        <div className="risk-item"><span className="risk-icon ok">✓</span><div><strong>应收与收入匹配</strong><p>应收增速 +0.8%，低于收入增速</p></div></div>
        <div className="risk-item"><span className="risk-icon ok">✓</span><div><strong>无商誉减值压力</strong><p>商誉仅占净资产 0.02%</p></div></div>
        <div className="risk-item"><span className="risk-icon ok">✓</span><div><strong>审计意见正常</strong><p>连续 8 年标准无保留意见</p></div></div>
      </>}
      <p className="risk-disclaimer">财务信号用于提示进一步核查，不等同于舞弊判断。</p>
    </section>
  );
}

function SectorValuationHistory({ symbol, revision }: { symbol: string; revision?: UnknownRecord }) {
  const [runs, setRuns] = useState<UnknownRecord[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    apiRequest<UnknownRecord[]>(`/api/valuation/runs?symbol=${encodeURIComponent(symbol)}&limit=6`)
      .then((items) => { if (active) { setRuns(items); setError(""); } })
      .catch(() => { if (active) setError("历史估值读取失败，请稍后重新打开页面"); });
    return () => { active = false; };
  }, [symbol, revision]);
  return <>{error && <p role="status">{error}</p>}<ValuationReplay runs={runs} /></>;
}

function ValuationReplay({ runs }: { runs: UnknownRecord[] }) {
  const [pending, setPending] = useState<number | null>(null);
  const [status, setStatus] = useState("");
  const [differences, setDifferences] = useState<UnknownRecord[]>([]);
  async function replay(id: number) {
    setPending(id);
    setStatus("");
    setDifferences([]);
    try {
      const result = await apiRequest<UnknownRecord>("/api/valuation/replay", { method: "POST", body: JSON.stringify({ run_id: id }) });
      setDifferences(Array.isArray(result.differences) ? result.differences.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : []);
      const labels: Record<string, string> = { verified: "归档输入重算一致，公式版本一致", version_changed: "公式版本已变化，请复核差异", mismatch: "同版本重算结果不一致，需要检查", unavailable: String(result.reason || "缺少归档输入") };
      setStatus(`#${id}：${labels[String(result.status)] || "无法确认复核结果"}`);
    } catch (error) { setStatus(error instanceof Error ? error.message : "复核失败"); }
    finally { setPending(null); }
  }
  if (!runs.length) return null;
  return <details className="valuation-editor"><summary>历史估值复核</summary><p>使用当时保存的数据和假设重算，历史记录保持原样。</p><div className="valuation-head-actions">{runs.map((run) => <button className="evidence-link" key={String(run.id)} disabled={pending !== null} onClick={() => replay(Number(run.id))}>{pending === Number(run.id) ? "复核中…" : `复核 #${String(run.id)} · ${String(run.as_of || "日期待补")}`}</button>)}</div><p role="status">{status}</p>{differences.length > 0 && <div className="compare-table-scroll"><p>差异明细（最多显示 100 项）</p><table className="compare-table"><thead><tr><th>字段</th><th>归档值</th><th>重算值</th></tr></thead><tbody>{differences.map((item) => <tr key={String(item.path)}><td>{String(item.path)}</td><td>{JSON.stringify(item.archived) ?? "缺失"}</td><td>{JSON.stringify(item.recalculated) ?? "缺失"}</td></tr>)}</tbody></table></div>}</details>;
}

function Valuation({ onEvidence, analysis, symbol }: { onEvidence: (key: EvidenceKey) => void; analysis?: UnknownRecord; symbol?: string }) {
  const [scenario, setScenario] = useState("基准");
  const [editorOpen, setEditorOpen] = useState(false);
  const [workbenchAnalysis, setWorkbenchAnalysis] = useState<UnknownRecord | undefined>();
  const [workbenchStatus, setWorkbenchStatus] = useState("");
  const [valuationRuns, setValuationRuns] = useState<UnknownRecord[]>([]);
  const [baseCashFlow, setBaseCashFlow] = useState("");
  const [dividendPerShare, setDividendPerShare] = useState("");
  const [ddmEnabled, setDdmEnabled] = useState(false);
  const [marginOfSafety, setMarginOfSafety] = useState("20");
  const [valuationAsOf, setValuationAsOf] = useState("");
  const [valuationInputs, setValuationInputs] = useState([
    { name: "stress", label: "压力", growth: "-2", discount: "12", terminal: "1", years: "5", pe: "", pb: "" },
    { name: "base", label: "基准", growth: "4", discount: "10", terminal: "2", years: "5", pe: "", pb: "" },
    { name: "optimistic", label: "乐观", growth: "8", discount: "9", terminal: "2.5", years: "5", pe: "", pb: "" },
  ]);
  const effectiveAnalysis = workbenchAnalysis || analysis;
  const liveValuationRoot = asRecord(effectiveAnalysis?.valuation);
  const liveValuation = asRecord(liveValuationRoot?.dcf);
  const liveMarket = asRecord(effectiveAnalysis?.market);
  const sensitivity = asRecord(liveValuationRoot?.sensitivity);
  const sensitivityGrowths = Array.isArray(sensitivity?.growth_rates) ? sensitivity.growth_rates.map(Number) : [];
  const sensitivityRows = Array.isArray(sensitivity?.rows) ? sensitivity.rows.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
  const reverseDcf = asRecord(liveValuationRoot?.reverse_dcf);
  const ddm = asRecord(liveValuationRoot?.ddm);
  const relative = asRecord(liveValuationRoot?.relative);
  const workbenchSummary = asRecord(liveValuationRoot?.workbench_summary);
  const methodRanges = Array.isArray(workbenchSummary?.method_ranges) ? workbenchSummary.method_ranges.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
  const dateContext = asRecord(workbenchSummary?.date_context);
  const liveScenarios = Array.isArray(liveValuation?.scenarios) ? liveValuation.scenarios.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
  const effectiveValuationAsOf = valuationAsOf || String(liveMarket?.price_as_of || effectiveAnalysis?.as_of || "");
  const scenarios = effectiveAnalysis ? (liveScenarios.length ? liveScenarios.map((item, index) => ({
    name: ({ stress: "压力", base: "基准", optimistic: "乐观" } as Record<string, string>)[String(item.name)] || String(item.name || ["压力", "基准", "乐观"][index] || `情景${index + 1}`),
    value: typeof item.value_per_share === "number" ? item.value_per_share.toFixed(2) : "—",
    returnText: typeof item.upside_downside === "number" ? formatPercent(item.upside_downside) : "—",
    tone: ["bear", "base", "bull"][index] || "base",
    desc: String(item.status || "系统 DCF 情景"),
  })) : [{ name: "不可计算", value: "—", returnText: "—", tone: "base", desc: String(liveValuation?.reason || "缺少经过校验的估值假设") }]) : [
    { name: "压力", value: "31.2 — 36.8", returnText: "−17.3%", tone: "bear", desc: "煤价 −20% · 资本开支上行" },
    { name: "基准", value: "40.8 — 48.6", returnText: "+5.5%", tone: "base", desc: "中周期煤价 · 分红率 70%" },
    { name: "乐观", value: "51.4 — 58.2", returnText: "+29.4%", tone: "bull", desc: "量价稳定 · 新项目达产" },
  ];
  useEffect(() => {
    let active = true;
    if (!editorOpen || !symbol) return () => { active = false; };
    apiRequest<UnknownRecord[]>(`/api/valuation/runs?symbol=${encodeURIComponent(symbol)}&limit=6`)
      .then((runs) => { if (active) setValuationRuns(runs); })
      .catch(() => { if (active) setValuationRuns([]); });
    return () => { active = false; };
  }, [editorOpen, symbol]);

  function reuseValuationRun(run: UnknownRecord) {
    const assumptions = asRecord(run.assumptions);
    const archivedScenarios = Array.isArray(assumptions?.scenarios) ? assumptions.scenarios.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
    if (!archivedScenarios.length) return;
    setBaseCashFlow(assumptions?.base_cash_flow === null || assumptions?.base_cash_flow === undefined ? "" : String(assumptions.base_cash_flow));
    setDdmEnabled(Boolean(assumptions?.ddm_enabled));
    setDividendPerShare(assumptions?.dividend_per_share === null || assumptions?.dividend_per_share === undefined ? "" : String(assumptions.dividend_per_share));
    setMarginOfSafety(String(Number(assumptions?.margin_of_safety ?? 0.2) * 100));
    setValuationAsOf(String(assumptions?.assumptions_as_of || run.as_of || ""));
    const archivedRelative = Array.isArray(assumptions?.relative_scenarios) ? assumptions.relative_scenarios.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
    setValuationInputs(archivedScenarios.map((item, index) => ({
      name: String(item.name || `scenario_${index + 1}`),
      label: ({ stress: "压力", base: "基准", optimistic: "乐观" } as Record<string, string>)[String(item.name)] || String(item.name || `情景${index + 1}`),
      growth: String(Number(item.growth_rate || 0) * 100),
      discount: String(Number(item.discount_rate || 0) * 100),
      terminal: String(Number(item.terminal_growth_rate || 0) * 100),
      years: String(item.forecast_years || 5),
      pe: archivedRelative.find((candidate) => candidate.name === item.name)?.target_pe === null || archivedRelative.find((candidate) => candidate.name === item.name)?.target_pe === undefined ? "" : String(archivedRelative.find((candidate) => candidate.name === item.name)?.target_pe),
      pb: archivedRelative.find((candidate) => candidate.name === item.name)?.target_pb === null || archivedRelative.find((candidate) => candidate.name === item.name)?.target_pb === undefined ? "" : String(archivedRelative.find((candidate) => candidate.name === item.name)?.target_pb),
    })));
    setWorkbenchStatus(`已载入历史运行 #${String(run.id || "—")} 的假设，尚未重算`);
  }
  async function runWorkbench() {
    if (!analysis || !symbol) return setWorkbenchStatus("请先接入该公司的真实财务快照");
    if (!effectiveValuationAsOf) return setWorkbenchStatus("请先填写统一估值截止日");
    setWorkbenchStatus("正在用本地公式重算并保存假设…");
    try {
      const result = await apiRequest<{ analysis: UnknownRecord; run_id?: number }>("/api/valuation/run", {
        method: "POST",
        body: JSON.stringify({
          symbol,
          as_of: effectiveValuationAsOf,
          save: true,
          assumptions: {
            assumptions_as_of: effectiveValuationAsOf,
            cash_flow_basis: "fcfe",
            base_cash_flow: baseCashFlow ? Number(baseCashFlow) : undefined,
            ddm_enabled: ddmEnabled,
            dividend_per_share: dividendPerShare ? Number(dividendPerShare) : undefined,
            margin_of_safety: Number(marginOfSafety) / 100,
            scenarios: valuationInputs.map((item) => ({ name: item.name, growth_rate: Number(item.growth) / 100, discount_rate: Number(item.discount) / 100, terminal_growth_rate: Number(item.terminal) / 100, forecast_years: Number(item.years) })),
            relative_scenarios: valuationInputs.map((item) => ({ name: item.name, target_pe: item.pe ? Number(item.pe) : undefined, target_pb: item.pb ? Number(item.pb) : undefined })),
          },
        }),
      });
      setWorkbenchAnalysis(result.analysis);
      setWorkbenchStatus(`已完成并保存估值运行 #${result.run_id || "—"}`);
      setScenario("基准");
      if (symbol) setValuationRuns(await apiRequest<UnknownRecord[]>(`/api/valuation/runs?symbol=${encodeURIComponent(symbol)}&limit=6`));
    } catch (error) {
      setWorkbenchStatus(error instanceof Error ? error.message : "估值重算失败");
    }
  }
  return (
    <section className="panel valuation-panel">
      <ValuationReplay key={symbol} runs={valuationRuns} />
      <div className="panel-heading"><div><span className="eyebrow">VALUATION</span><h2>三情景估值</h2></div><div className="valuation-head-actions"><button className="evidence-link" disabled={!analysis} onClick={() => setEditorOpen((value) => !value)}>{editorOpen ? "收起假设" : "调整假设"}</button><button className="evidence-link" onClick={() => onEvidence("valuation")}>查看模型依据 ↗</button></div></div>
      {editorOpen && <div className="valuation-editor"><div className="valuation-editor-intro"><div><strong>本地多方法估值工作台</strong><small>百分比按年输入；DCF 留空基准现金流时使用 TTM CFO − 资本开支。PE/PB 留空则不计算该锚点。</small></div><div className="valuation-editor-options"><label><span>统一估值截止日 *</span><input type="date" value={effectiveValuationAsOf} onChange={(event) => setValuationAsOf(event.target.value)} /></label><label><span>自定义基准现金流（百万元）</span><input type="number" value={baseCashFlow} onChange={(event) => setBaseCashFlow(event.target.value)} placeholder="留空自动取数" /></label><label><span>每股股息（可留空自动取）</span><input type="number" value={dividendPerShare} onChange={(event) => setDividendPerShare(event.target.value)} placeholder="例如 2.50" /></label><label><span>要求安全边际 %</span><input type="number" min="0" max="80" value={marginOfSafety} onChange={(event) => setMarginOfSafety(event.target.value)} /></label><label className="valuation-check"><input type="checkbox" checked={ddmEnabled} onChange={(event) => setDdmEnabled(event.target.checked)} /><span>同时计算 DDM</span></label></div></div><div className="valuation-input-table"><div className="valuation-input-row header"><span>情景</span><span>增长率 %</span><span>折现率 %</span><span>永续增长 %</span><span>预测年</span><span>目标 PE</span><span>目标 PB</span></div>{valuationInputs.map((item, index) => <div className="valuation-input-row" key={item.name}><b>{item.label}</b>{(["growth", "discount", "terminal", "years", "pe", "pb"] as const).map((key) => <input key={key} type="number" step={key === "years" ? "1" : "0.1"} value={item[key]} placeholder={key === "pe" || key === "pb" ? "可选" : undefined} onChange={(event) => setValuationInputs((current) => current.map((candidate, candidateIndex) => candidateIndex === index ? { ...candidate, [key]: event.target.value } : candidate))} aria-label={`${item.label}${key}`} />)}</div>)}</div><div className="valuation-editor-actions"><p>{workbenchStatus || "只重算估值，不调用 AI，不消耗 Token。"}</p><button disabled={!analysis || !effectiveValuationAsOf} onClick={runWorkbench}>重算并保存快照</button></div>{valuationRuns.length > 0 && <div className="valuation-run-history"><div><strong>历史运行</strong><small>复用只会载入假设，不会伪装成当前估值</small></div>{valuationRuns.map((run) => { const dcf = asRecord(asRecord(run.result)?.dcf); const runScenarios = dcf?.scenarios; const scenarioList = Array.isArray(runScenarios) ? runScenarios.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : []; const base = scenarioList.find((item) => item.name === "base") || scenarioList[Math.floor(scenarioList.length / 2)]; return <button key={String(run.id)} onClick={() => reuseValuationRun(run)}><span>#{String(run.id)} · {String(run.as_of || "日期待补")}</span><b>{typeof base?.value_per_share === "number" ? `基准 ¥${base.value_per_share.toFixed(2)}` : "结果不可计算"}</b><small>{String(run.created_at || "")}</small></button>; })}</div>}</div>}
      <div className="price-context"><div><small>当前价格</small><strong>¥ {effectiveAnalysis ? String(liveMarket?.market_price ?? liveValuation?.market_price ?? "—") : "42.36"}</strong><span>{effectiveAnalysis ? `行情截至 ${String(liveMarket?.price_as_of || effectiveAnalysis.as_of || "—")}` : "2026-08-11 收盘"}</span></div><div className="valuation-meter"><span className="meter-range"><i /><b style={{ left: "49%" }} /></span><div><span>低估</span><span>合理区间</span><span>高估</span></div></div></div>
      <div className="scenario-grid">
        {scenarios.map((item) => (
          <button key={item.name} className={`scenario ${item.tone} ${scenario === item.name ? "selected" : ""}`} onClick={() => setScenario(item.name)}>
            <div><span>{item.name}情景</span>{scenario === item.name && <b>当前采用</b>}</div>
            <strong>¥ {item.value}</strong>
            <p>区间中值潜在空间 <em>{item.returnText}</em></p>
            <small>{item.desc}</small>
          </button>
        ))}
      </div>
      {sensitivity?.status === "ok" && <div className="valuation-sensitivity"><div><strong>DCF 敏感性矩阵</strong><small>行：折现率 · 列：现金流增长率 · 单位：每股价值</small></div><div className="sensitivity-table"><span>折现 \ 增长</span>{sensitivityGrowths.map((growth) => <b key={growth}>{(growth * 100).toFixed(1)}%</b>)}{sensitivityRows.map((row, rowIndex) => <Fragment key={`sensitivity-${rowIndex}`}><b>{(Number(row.discount_rate) * 100).toFixed(1)}%</b>{(Array.isArray(row.values) ? row.values : []).map((value, valueIndex) => <span key={`${rowIndex}-${valueIndex}`}>{typeof value === "number" ? `¥${value.toFixed(2)}` : "—"}</span>)}</Fragment>)}</div></div>}
      {effectiveAnalysis && <div className="valuation-method-grid"><article><span>市场隐含预期</span><strong>{reverseDcf?.status === "ok" && typeof reverseDcf.implied_constant_growth_rate === "number" ? `${(reverseDcf.implied_constant_growth_rate * 100).toFixed(1)}%` : "不可计算"}</strong><p>反向 DCF 求解的恒定增长率，不是系统预测。</p></article><article><span>DDM 红利价值</span><strong>{(() => { const items = Array.isArray(ddm?.scenarios) ? ddm.scenarios.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : []; const base = items.find((item) => item.name === "base") || items[0]; return typeof base?.value_per_share === "number" ? `¥${base.value_per_share.toFixed(2)}` : "未启用 / 不可算"; })()}</strong><p>必须由可持续股息、覆盖能力和权益成本共同支撑。</p></article><article><span>相对估值区间</span><strong>{(() => { const items = Array.isArray(relative?.scenarios) ? relative.scenarios.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : []; const base = items.find((item) => item.name === "base") || items[0]; return typeof base?.value_range_low === "number" && typeof base?.value_range_high === "number" ? `¥${base.value_range_low.toFixed(2)}—${base.value_range_high.toFixed(2)}` : "未输入倍数"; })()}</strong><p>PE 与 PB 分别作为锚点展示，不强行平均成单点。</p></article>{methodRanges.map((item) => <article key={String(item.method)} className="margin-card"><span>{String(item.label)} · 安全边际后区间</span><strong>{typeof item.margin_price_low === "number" && typeof item.margin_price_high === "number" ? `¥${item.margin_price_low.toFixed(2)}—${item.margin_price_high.toFixed(2)}` : "不可计算"}</strong><p>按 {(Number(item.margin_of_safety || 0) * 100).toFixed(0)}% 安全边际折算；方法之间不合并平均。</p></article>)}</div>}
      {dateContext && <div className={`valuation-date-context ${dateContext.status}`}><span>{dateContext.status === "complete" ? "✓" : "!"}</span><p><strong>估值日期口径</strong>财务/净债务 {String(dateContext.financial_period_end || "缺失")} · 价格 {String(dateContext.price_as_of || "缺失")} · 股本 {String(dateContext.shares_as_of || "缺失")} · 假设 {String(dateContext.assumptions_as_of || "缺失")} · 截止 {String(dateContext.as_of_request || "未记录")}<small>{String(dateContext.warning || "")}</small></p></div>}
      <div className="valuation-insight"><span>观点</span><p>{effectiveAnalysis ? "估值仅在现金流、股本、净债务与价格同一截至日期且数据质量通过时成立；请打开模型依据查看假设与不可计算项。" : <>当前价格处于基准价值区间下沿，但距离 <b>20% 安全边际</b>仍有约 13% 空间。红利视角可观察，格雷厄姆视角暂不满足严格买点。</>}</p></div>
    </section>
  );
}

function FinancialChanges({ analysis, onEvidence }: { analysis?: UnknownRecord; onEvidence: (key: EvidenceKey) => void }) {
  const changes = asRecord(analysis?.changes);
  const summary = asRecord(changes?.summary);
  const currentContext = asRecord(changes?.current_context);
  const comparisonContext = asRecord(changes?.comparison_context);
  const signals = Array.isArray(changes?.signals) ? changes.signals.map(asRecord).filter((item): item is UnknownRecord => Boolean(item)) : [];
  const missing = Array.isArray(changes?.missing_metrics) ? changes.missing_metrics.map(String) : [];
  const unsupported = Array.isArray(changes?.unsupported_checks) ? changes.unsupported_checks.map(String) : [];
  const contextLabel = (context?: UnknownRecord) => context ? [String(context.end_date || "日期缺失"), context.basis === "reported_cumulative_ytd" ? "累计口径" : context.basis === "reported_discrete_quarter" ? "单季口径" : context.basis === "reported_fy" ? "完整年度" : context.basis === "constructed_ttm" ? "滚动12月" : "口径待核", context.audited ? "已审计" : "未审计", context.restated ? "含重述" : "未标记重述", context.report_scope === "consolidated" ? "合并报表" : context.report_scope === "parent" ? "母公司" : String(context.report_scope || "报表范围待核")].join(" · ") : "无可比期间";
  if (!analysis) return <section className="change-empty"><span>变</span><h2>等待真实财务快照</h2><p>变化检测不会使用演示数字。接入公司财报后，本地引擎会比较可比 FY / TTM，抓利润现金背离、应收存货压力、杠杆、分红覆盖和资本开支变化。</p></section>;
  return <section className="change-workbench">
    <div className="change-head"><div><span className="eyebrow">DETERMINISTIC CHANGE DETECTION</span><h2>财务变化检测</h2><p>累计季报只与上年同期限累计数比较，单季只与单季比较，年报只与年报比较；阈值用于安排复核优先级。</p></div><div className="change-summary"><span><b>{String(summary?.red ?? 0)}</b> 红色</span><span><b>{String(summary?.yellow ?? 0)}</b> 黄色</span><span><b>{String(summary?.total ?? 0)}</b> 合计</span></div></div>
    <div className="change-period-context"><div><span>本期</span><strong>{contextLabel(currentContext)}</strong></div><i>对比</i><div><span>可比期</span><strong>{contextLabel(comparisonContext)}</strong></div><small>{String(changes?.comparison_basis || "未建立可比口径")}</small></div>
    {signals.length ? <div className="change-signal-list">{signals.map((signal) => {
      const values = asRecord(signal.values);
      const metrics = asRecord(signal.metrics);
      return <article key={String(signal.id)} className={String(signal.severity || "yellow")}><div className="change-signal-index"><span>{signal.severity === "red" ? "高" : "察"}</span><small>{String(signal.period || "—")}</small></div><div className="change-signal-body"><div><h3>{String(signal.title || signal.id)}</h3><b>{signal.severity === "red" ? "高优先复核" : "持续观察"}</b></div><p>{String(signal.detail || "")}</p>{values && Object.keys(values).length > 0 && <code>{JSON.stringify(values)}</code>}<footer><span>对比期 {String(signal.comparison_period || "—")} · {contextLabel(asRecord(signal.current_context))}</span><button onClick={() => onEvidence("flag")}>查看计算规则 ↗</button></footer>{metrics && <details><summary>展开本期/对比期公式与原始来源</summary><pre>{JSON.stringify(metrics, null, 2)}</pre></details>}<small>{String(signal.review_condition || "")}</small></div></article>;
    })}</div> : <div className="change-no-signal"><span>○</span><div><h3>当前阈值未触发变化线索</h3><p>这不是“财务没问题”的证明；请继续核对缺失指标、附注和公告证据。</p></div></div>}
    <div className="change-coverage"><div><h3>尚不能自动判断</h3><p>{unsupported.length ? unsupported.join("；") : "暂无"}</p></div><div><h3>本期缺失指标</h3><p>{missing.length ? missing.join("、") : "核心变化指标已覆盖"}</p></div></div>
  </section>;
}

function ResearchView({ company, onEvidence, engine, onDashboardRefresh, onOpenFilings, watchlisted, onToggleWatch }: { company: ResearchCompany; onEvidence: (selection: EvidenceSelection) => void; engine: string; onDashboardRefresh: () => Promise<void>; onOpenFilings: () => void; watchlisted: boolean; onToggleWatch: () => Promise<void> }) {
  const [reportTab, setReportTab] = useState("财务透视");
  const [analysisState, setAnalysisState] = useState<"idle" | "running" | "done" | "error">("idle");
  const [analysisMessage, setAnalysisMessage] = useState("");
  const [analysisMessageCompany, setAnalysisMessageCompany] = useState("");
  const [liveAnalysis, setLiveAnalysis] = useState<UnknownRecord | undefined>();
  const [liveSource, setLiveSource] = useState<UnknownRecord | undefined>();
  const [liveCompany, setLiveCompany] = useState("");
  const [analysisContent, setAnalysisContent] = useState("");
  const [analysisDoctrineCoverage, setAnalysisDoctrineCoverage] = useState<DoctrineCoverage | undefined>();
  const [analysisCompany, setAnalysisCompany] = useState("");
  const [analysisAsOf, setAnalysisAsOf] = useState("");
  const [analysisMaxDate] = useState(() => new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10));
  const [preparedAnalysis, setPreparedAnalysis] = useState<{ companyCode: string; provider: "codex" | "api"; request: UnknownRecord; evidence: UnknownRecord; estimate: AiCostEstimate } | null>(null);
  const isPrimaryDemo = company.code === companies[0].code;

  useEffect(() => {
    let cancelled = false;
    apiRequest<{ analysis?: UnknownRecord; source?: UnknownRecord }>("/api/finance/analyze", { method: "POST", body: JSON.stringify({ symbol: company.code, industry: company.industry }) })
      .then((result) => {
        if (cancelled) return;
        setLiveAnalysis(result.analysis);
        setLiveSource(result.source);
        setLiveCompany(company.code);
        setAnalysisState("idle");
        setAnalysisMessage("");
        setAnalysisMessageCompany("");
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setAnalysisState("error");
        setAnalysisMessage(error instanceof Error ? error.message : "真实财务数据加载失败");
        setAnalysisMessageCompany(company.code);
      });
    return () => { cancelled = true; };
  }, [company.code, company.industry]);

  async function runAnalysis() {
    const provider = engine === "Codex" ? "codex" : "api";
    const request = {
      provider,
      symbol: company.code,
      industry: company.industry,
      as_of: analysisAsOf || undefined,
      title: `${company.name}财务研究`,
      knowledge_query: `${company.name} 盈利质量 现金流 资产负债 分红 安全边际`,
      prompt: `请分析${company.name}${analysisAsOf ? `，所有事实严格截止 ${analysisAsOf}` : ""}。先判断证据包里的财务数据是否确实属于该公司；如果仍是虚构演示财务数据，严禁把演示数字套到该公司，只分析持仓条件与知识库观点并明确列出缺失数据。若事实充分，再从盈利质量、现金流、资产负债、资本配置、治理审计和估值六方面展开，给出反方证据和逻辑失效条件。`,
    };
    const prepared = preparedAnalysis?.companyCode === company.code && preparedAnalysis.provider === provider ? preparedAnalysis : null;
    setAnalysisState("running");
    setAnalysisMessage("");
    setAnalysisMessageCompany("");
    try {
      if (!prepared) {
        const quote = await apiRequest<{ estimate: AiCostEstimate; evidence: UnknownRecord }>("/api/ai/estimate", {
          method: "POST",
          body: JSON.stringify(request),
        });
        setPreparedAnalysis({ companyCode: company.code, provider, request, evidence: quote.evidence, estimate: quote.estimate });
        setAnalysisState("idle");
        return;
      }
      const result = await apiRequest<{ content?: string; provider?: string; doctrine_coverage?: DoctrineCoverage }>("/api/ai/analyze", {
        method: "POST",
        body: JSON.stringify({
          ...prepared.request,
          evidence: prepared.evidence,
          estimate_fingerprint: prepared.estimate.fingerprint,
        }),
      });
      setPreparedAnalysis(null);
      setAnalysisContent(result.content || "");
      setAnalysisDoctrineCoverage(result.doctrine_coverage);
      setAnalysisCompany(company.code);
      setAnalysisMessage(result.doctrine_coverage?.status === "needs_review" ? "分析已保存，但流派分离格式未通过程序检查，请在“知识库观点”中复核。" : "分析已完成，报告已保存到研究档案。");
      setAnalysisMessageCompany(company.code);
      setAnalysisState("done");
      await onDashboardRefresh();
    } catch (error) {
      setAnalysisMessage(error instanceof Error ? error.message : "分析失败，请检查本地服务配置。");
      setAnalysisMessageCompany(company.code);
      setAnalysisState("error");
    }
  }
  const currentLiveAnalysis = liveCompany === company.code ? liveAnalysis : undefined;
  const currentLiveSource = liveCompany === company.code ? liveSource : undefined;
  const showReport = isPrimaryDemo || Boolean(currentLiveAnalysis);
  const isLive = Boolean(currentLiveAnalysis);
  const isBank = asRecord(currentLiveAnalysis?.support)?.status === "supported_bank";
  const isSector = asRecord(currentLiveAnalysis?.support)?.status === "supported_sector";
  const sectorTemplate = String(asRecord(currentLiveAnalysis?.support)?.sector_template || "");
  const liveLoading = !currentLiveAnalysis && !(analysisState === "error" && analysisMessageCompany === company.code);
  const liveMarketPrice = asRecord(currentLiveAnalysis?.market)?.market_price ?? asRecord(asRecord(currentLiveAnalysis?.valuation)?.dcf)?.market_price;
  const currentAnalysisContent = analysisCompany === company.code ? analysisContent : "";
  const currentDoctrineCoverage = analysisCompany === company.code ? analysisDoctrineCoverage : undefined;
  const activePreparedAnalysis = preparedAnalysis?.companyCode === company.code && preparedAnalysis.provider === (engine === "Codex" ? "codex" : "api") ? preparedAnalysis : null;
  const openEvidence = (key: EvidenceKey) => onEvidence({ key, company, analysis: currentLiveAnalysis, source: currentLiveSource });
  const sectorLead = ({ cyclical_resource: "先把量、价、成本和储量还原，再谈中周期价值。", real_estate: "先把受限资金、短债和项目存货折价还原，再谈账面价值。", insurance: "先看偿付能力、内含价值和新业务质量，再谈低估。", brokerage: "先看净资本、流动性和收入周期性，再谈 PB 与 PE。", utility: "先看利用效率、成本传导和资本回收，再谈股息与估值。" } as Record<string, string>)[sectorTemplate] || "先补齐行业经营事实，再谈估值。";
  const sectorValuationExplanation = ({ cyclical_resource: "周期资源使用正常化量价成本与储量 NAV，不把峰值利润当作永续盈利。", real_estate: "地产使用项目存货折价、合同负债结转和额外损失准备计算调整 NAV/PB，不把账面存货原值当作可随时变现价值。", insurance: "保险使用内含价值 P/EV 与 EV+正常化新业务价值估值；偿付能力和 EV/NBV 口径缺失时停止精确计算。", brokerage: "券商使用净资产 PB 与正常化盈利 PE；不把单年自营交易收益直接永续化。", utility: "公用事业使用受监管资产盈利能力与 DDM，并显式拆分维持性、扩张性资本开支。" } as Record<string, string>)[sectorTemplate] || "行业估值需要独立方法与可核验假设。";
  return (
    <>
      <section className="company-hero">
        <div className="company-identity"><span className="company-mark">{company.name.slice(0, 1)}</span><div><div className="company-title"><h1>{company.name}</h1><span>{company.code}.{company.market}</span><em>{company.industry}</em></div><p>{isLive ? `真实财务数据已接入 · 截至 ${String(currentLiveAnalysis?.as_of || "—")}` : isPrimaryDemo ? "能源一体化龙头 · 演示研究档案" : "尚未接入该公司的真实财报 · 下方研究面板已锁定"}</p></div></div>
        <div className="market-quote"><span>¥ {typeof liveMarketPrice === "number" ? liveMarketPrice.toFixed(2) : company.price}</span><b className={company.change.startsWith("+") ? "positive" : "negative"}>{isLive ? "已接入财务数据" : company.change}</b><small>{isLive ? "行情价格来自估值配置/数据快照" : "总市值 8,416 亿元"}</small></div>
        <div className="hero-actions"><button onClick={onToggleWatch}>{watchlisted ? "× 移出观察" : "＋ 加入观察"}</button><button className="run-analysis" disabled={analysisState === "running" || Boolean(activePreparedAnalysis && (!activePreparedAnalysis.estimate.within_budget || !activePreparedAnalysis.estimate.configuration_ready))} onClick={runAnalysis}>{analysisState === "running" ? (activePreparedAnalysis ? "分析中…" : "正在预估…") : activePreparedAnalysis ? "✦ 确认调用" : "✦ 预估并分析"}</button></div>
      </section>
      <section className="analysis-cutoff-bar"><div><strong>研究截止日</strong><span>{analysisAsOf ? `历史模式：财务、行情与公告不得晚于 ${analysisAsOf}` : "留空使用当前可获得数据；历史复盘请明确选择日期"}</span></div><label><span>截至</span><input type="date" max={analysisMaxDate} value={analysisAsOf} onChange={(event) => { setAnalysisAsOf(event.target.value); setPreparedAnalysis(null); }} /></label>{analysisAsOf && <button onClick={() => { setAnalysisAsOf(""); setPreparedAnalysis(null); }}>恢复当前</button>}</section>
      {activePreparedAnalysis && <section className={`ai-cost-preview ${activePreparedAnalysis.estimate.within_budget && activePreparedAnalysis.estimate.configuration_ready ? "ready" : "blocked"}`}><div><span className="eyebrow">BEFORE AI CALL</span><h2>调用前确认</h2><p>{activePreparedAnalysis.estimate.model} · 证据包 {(activePreparedAnalysis.estimate.evidence_bytes / 1024).toFixed(1)} KB{activePreparedAnalysis.request.as_of ? ` · 截止 ${String(activePreparedAnalysis.request.as_of)}` : " · 当前模式"}</p></div><div className="ai-cost-metrics"><span>输入约 <b>{activePreparedAnalysis.estimate.estimated_input_tokens.toLocaleString("zh-CN")}</b></span><span>输出预留 <b>{activePreparedAnalysis.estimate.estimated_output_tokens.toLocaleString("zh-CN")}</b></span><span>本次合计 <b>{activePreparedAnalysis.estimate.estimated_total_tokens.toLocaleString("zh-CN")}</b></span><span>调用后月余额 <b>{activePreparedAnalysis.estimate.monthly_tokens_after.toLocaleString("zh-CN")}</b></span>{activePreparedAnalysis.estimate.estimated_cost != null && <span>估算费用 <b>{activePreparedAnalysis.estimate.currency} {activePreparedAnalysis.estimate.estimated_cost.toFixed(4)}</b></span>}</div><p className="ai-cost-notice">{!activePreparedAnalysis.estimate.within_budget ? "本次调用超过月度 Token 预算，已禁止执行。" : !activePreparedAnalysis.estimate.configuration_ready ? "兼容 API 配置不完整，请先在设置中填写地址、模型并配置密钥。" : activePreparedAnalysis.estimate.cost_notice}</p><div><button onClick={() => setPreparedAnalysis(null)}>取消</button><button className="primary-action" disabled={!activePreparedAnalysis.estimate.within_budget || !activePreparedAnalysis.estimate.configuration_ready} onClick={runAnalysis}>确认并开始分析</button></div></section>}
      {analysisMessage && analysisMessageCompany === company.code && <div className={`action-message ${analysisState}`}><span>{analysisState === "done" ? "✓" : "!"}</span><p><strong>{analysisState === "done" ? `${engine} 分析完成` : "暂时无法完成分析"}</strong>{analysisMessage}</p><button onClick={() => { setAnalysisMessage(""); setAnalysisMessageCompany(""); }}>×</button></div>}
      {!showReport && !isPrimaryDemo && <div className={`action-message ${liveLoading ? "running" : "error"}`}><span>{liveLoading ? "…" : "!"}</span><p><strong>{liveLoading ? "正在加载该公司的真实财务事实" : "没有可用于该公司的财务事实"}</strong>{liveLoading ? "系统会优先使用本地快照；需要更新时再调用已配置的数据源。" : `当前不会把中国神华的演示数字套到 ${company.name}；请安装免费数据源、配置 TUSHARE_TOKEN，或先导入该公司的财务快照。`}</p></div>}
      <div aria-hidden={!showReport} style={showReport ? undefined : { display: "none" }}>
      <div className="report-tabs" role="tablist">
        {["财务透视", "变化检测", "估值与情景", "知识库观点", "公告与治理"].map((tab) => <button role="tab" aria-selected={reportTab === tab} className={reportTab === tab ? "active" : ""} key={tab} onClick={() => setReportTab(tab)}>{tab}</button>)}
        <span className="report-id">报告编号 {isLive ? `LIVE-${company.code}-${String(currentLiveAnalysis?.as_of || "").replaceAll("-", "")}` : "HG-601088-260812"}</span>
      </div>
      <div className="thesis-strip"><span className="thesis-label">一句话结论</span><p>{isBank ? <><strong>先看资产质量与资本缓冲，再看 PB。</strong>净息差、不良、关注、拨备和资本指标缺一项都不能用“正常”代替；估值需结合你确认的 ROE 与权益成本情景。</> : isSector ? <><strong>{sectorLead}</strong>行业经营披露缺失时不会用通用利润率代替。</> : isLive ? <><strong>先看证据质量，再看估值空间。</strong>当前页面展示的是标准化财务事实与系统计算结果；是否适合持仓，仍需结合你的成本、仓位、流派规则和反方证据。</> : <><strong>现金质量优秀、资产负债表稳健，</strong>但当前估值已反映部分红利确定性；等待更厚安全边际，或盈利预期获得新支撑。</>}</p><div><span>{isLive ? "数据质量" : "置信度"}</span><b>{isLive ? `${String(asRecord(currentLiveAnalysis?.data_quality)?.score ?? "—")}` : "82%"}</b></div></div>
      {currentAnalysisContent && <section className="analysis-output"><div className="analysis-output-head"><div><span className="eyebrow">AI RESEARCH MEMO</span><h2>{engine} 研究结论</h2></div><span>已保存 · 可在研究档案中回看</span></div><MarkdownReport content={currentAnalysisContent} /></section>}
      {reportTab === "财务透视" && <>
        {isBank ? <><BankDiagnostics analysis={currentLiveAnalysis as UnknownRecord} symbol={company.code} onUpdated={(updated) => { setLiveAnalysis(updated); setLiveCompany(company.code); }} /><BankValuation analysis={currentLiveAnalysis as UnknownRecord} symbol={company.code} industry={company.industry} /></> : isSector ? <><SectorDiagnostics analysis={currentLiveAnalysis as UnknownRecord} symbol={company.code} industry={company.industry} onUpdated={(updated) => { setLiveAnalysis(updated); setLiveCompany(company.code); }} /><SectorValuation analysis={currentLiveAnalysis as UnknownRecord} symbol={company.code} industry={company.industry} /></> : <><Diagnostics onEvidence={openEvidence} analysis={currentLiveAnalysis} /><div className="analytics-grid"><FinancialTrend analysis={currentLiveAnalysis} /><RiskRadar onEvidence={openEvidence} analysis={currentLiveAnalysis} /></div><Valuation onEvidence={openEvidence} analysis={currentLiveAnalysis} symbol={company.code} /></>}
      </>}
      {reportTab === "变化检测" && <FinancialChanges analysis={currentLiveAnalysis} onEvidence={openEvidence} />}
      {reportTab === "估值与情景" && <>
        {isBank ? <BankValuation analysis={currentLiveAnalysis as UnknownRecord} symbol={company.code} industry={company.industry} /> : isSector ? <SectorValuation analysis={currentLiveAnalysis as UnknownRecord} symbol={company.code} industry={company.industry} /> : <Valuation onEvidence={openEvidence} analysis={currentLiveAnalysis} symbol={company.code} />}
        <section className="tab-insight"><span className="eyebrow">VALUATION DISCIPLINE</span><h2>估值先看假设，再看区间</h2><p>{isBank ? "银行使用每股净资产、可持续 ROE、权益成本与资本约束做 PB/剩余收益估值；不套用工业企业自由现金流 DCF。" : isSector ? sectorValuationExplanation : "系统不会把单点目标价当作结论。现金流、折现率、终值增长、净债务、股本和价格必须在同一截至日期；任一关键输入缺失，就显示不可计算。"}</p><div><b>{isBank ? "银行专用模型" : isSector ? "行业专用模型" : "反向 DCF"}</b><span>{isLive ? "已随财务快照计算" : "演示模式等待真实数据"}</span></div></section>
      </>}
      {reportTab === "知识库观点" && <section className="knowledge-consensus">
        <div className="consensus-title"><span>知</span><div><small>{isLive ? "知识库观点需在 AI 分析时结合" : "3 个知识库已参与分析"}</small><h2>不同体系如何看这家公司</h2></div></div>
        <div className="consensus-grid">
          {isLive ? currentAnalysisContent ? <>{currentDoctrineCoverage?.status === "not_applicable" ? <article><span className="library-dot blue" /><div><small>本次流派规则</small><strong>没有可用的已审核规则</strong><p>{currentDoctrineCoverage.notice}</p></div><b>未生成</b></article> : <>{(currentDoctrineCoverage?.expected_libraries || []).map((name) => { const covered = currentDoctrineCoverage?.covered_libraries?.includes(name); return <article key={name} className={covered ? "doctrine-covered" : "doctrine-missing"}><span className={`library-dot ${covered ? "jade" : "gold"}`} /><div><small>独立流派结论</small><strong>{name}</strong><p>{covered ? "报告中已找到独立标题；仍需人工核对规则引用、适用性与反证。" : "AI 没有按输出契约单列该流派，不能把综合文字当作该体系结论。"}</p></div><b>{covered ? "已分流" : "待复核"}</b></article>; })}<article className={currentDoctrineCoverage?.status === "compliant" ? "doctrine-covered" : "doctrine-missing"}><span className={`library-dot ${currentDoctrineCoverage?.status === "compliant" ? "jade" : "gold"}`} /><div><small>程序格式审计</small><strong>{currentDoctrineCoverage?.status === "compliant" ? "流派结构完整" : "流派结构不完整"}</strong><p>{currentDoctrineCoverage?.notice || "旧结果没有流派结构审计，请重新运行分析。"}</p></div><b>{currentDoctrineCoverage?.status === "compliant" ? "通过" : "未通过"}</b></article></>}</> : <article><span className="library-dot blue" /><div><small>当前研究状态</small><strong>等待流派分析</strong><p>运行“预估并分析”后，系统会把已审核的红利、格雷厄姆或自定义规则分别应用，并保留冲突。</p></div><b>可追溯</b></article> : <>
            <article><span className="library-dot jade" /><div><small>红利质量体系</small><strong>谨慎积极</strong><p>自由现金流覆盖分红，低负债提供缓冲；需观察资本开支。</p></div><b>4 条引用</b></article>
            <article><span className="library-dot gold" /><div><small>格雷厄姆安全边际</small><strong>等待价格</strong><p>资产与盈利质量合格，但当前折价尚未达到严格安全边际。</p></div><b>6 条引用</b></article>
            <article><span className="library-dot blue" /><div><small>散修宗投资框架</small><strong>观察持有</strong><p>基本面没有走坏，趋势未出现加速信号，仓位不宜追高。</p></div><b>5 条引用</b></article>
          </>}
        </div>
      </section>}
      {reportTab === "公告与治理" && <CompanyFilingPanel company={company} onOpenVault={onOpenFilings} />}
      <div className="analysis-note"><span>i</span><p><strong>关于这份报告</strong>{isLive ? `财务数据来源：${String(currentLiveSource?.provider || "本地数据服务")}；系统计算、文档观点与 AI 推断分别标注，数据截至 ${String(currentLiveAnalysis?.as_of || "—")}。` : "当前为界面演示数据，并非真实投资建议。配置 TUSHARE_TOKEN 后，事实、系统计算与 AI 推断将分别标注并可回溯来源。"}</p></div>
      </div>
    </>
  );
}

const comparisonMetrics = [
  { key: "revenue_growth", label: "营收增长", group: "成长", format: "percent", better: "high" },
  { key: "net_income_growth", label: "净利润增长", group: "成长", format: "percent", better: "high" },
  { key: "gross_margin", label: "毛利率", group: "盈利", format: "percent", better: "high" },
  { key: "net_margin", label: "净利率", group: "盈利", format: "percent", better: "high" },
  { key: "return_on_equity", label: "ROE", group: "回报", format: "percent", better: "high" },
  { key: "return_on_invested_capital", label: "ROIC", group: "回报", format: "percent", better: "high" },
  { key: "cash_conversion", label: "现金含量", group: "现金", format: "ratio", better: "high" },
  { key: "free_cash_flow", label: "自由现金流", group: "现金", format: "amount", better: "high" },
  { key: "free_cash_flow_dividend_cover", label: "分红覆盖", group: "现金", format: "ratio", better: "high" },
  { key: "debt_to_equity", label: "债务 / 权益", group: "偿债", format: "ratio", better: "low" },
  { key: "current_ratio", label: "流动比率", group: "偿债", format: "ratio", better: "high" },
  { key: "interest_coverage", label: "利息保障", group: "偿债", format: "ratio", better: "high" },
] as const;

const filingTypeLabels: Record<string, string> = {
  annual_report: "年度报告",
  interim_report: "半年度报告",
  quarterly_report: "季度报告",
  audit_report: "审计报告",
  correction: "更正公告",
  inquiry: "问询函 / 回复",
  internal_control: "内部控制报告",
  other: "其他公告",
};

const evidenceCategoryLabels: Record<string, string> = {
  audit_opinion: "审计意见",
  key_audit_matter: "关键审计事项",
  receivables_aging: "应收与账龄",
  inventory_impairment: "存货减值",
  goodwill: "商誉",
  related_party: "关联交易",
  guarantee: "担保与承诺",
  restricted_cash: "受限资金",
  non_recurring: "非经常损益",
  capitalization: "资本化",
  accounting_change: "会计变更 / 差错",
  regulatory_inquiry: "监管问询",
};

function CompanyFilingPanel({ company, onOpenVault }: { company: ResearchCompany; onOpenVault: () => void }) {
  const [filings, setFilings] = useState<ApiFiling[]>([]);
  const [signals, setSignals] = useState<Array<{ filing: ApiFiling; evidence: FilingEvidence }>>([]);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState("");

  useEffect(() => {
    let active = true;
    apiRequest<ApiFiling[]>(`/api/filings?symbol=${encodeURIComponent(company.code)}&limit=20`)
      .then(async (items) => {
        const evidenceGroups = await Promise.all(items.slice(0, 12).map(async (filing) => {
          try {
            const evidence = await apiRequest<FilingEvidence[]>(`/api/filings/evidence?filing_id=${filing.id}`);
            return evidence.map((item) => ({ filing, evidence: item }));
          } catch {
            return [];
          }
        }));
        if (!active) return;
        setFilings(items);
        setSignals(evidenceGroups.flat());
        setLoading(false);
        setStatus(items.length > 12 ? `展示最近 12/${items.length} 份公告的线索；完整记录请进入财报证据仓。` : "");
      })
      .catch((error: unknown) => {
        if (!active) return;
        setLoading(false);
        setStatus(error instanceof Error ? error.message : "公告证据读取失败");
      });
    return () => { active = false; };
  }, [company.code]);

  const activeSignals = signals.filter((item) => item.evidence.review_status !== "dismissed" && !item.filing.superseded_by_id);
  const confirmed = activeSignals.filter((item) => item.evidence.review_status === "confirmed");
  const confirmedRed = confirmed.filter((item) => item.evidence.severity === "red");
  const pending = activeSignals.filter((item) => item.evidence.review_status === "pending" || !item.evidence.review_status);
  const searchable = filings.filter((item) => ["ready", "ready_ocr"].includes(item.extraction_status || "")).length;
  const partial = filings.filter((item) => item.extraction_status === "ocr_partial").length;
  const unsearchable = filings.length - searchable - partial;
  if (loading) return <section className="company-filing-panel loading"><span>证</span><div><h2>正在读取 {company.name} 的公告证据</h2><p>只读取本地归档，不会在后台隐式下载或调用 AI。</p></div></section>;
  if (!filings.length) return <section className="company-filing-panel empty"><span>档</span><div><h2>尚未归档 {company.name} 的公告原件</h2><p>没有公告不等于没有风险。请在财报证据仓查询并归档年报、审计报告、更正公告、内控报告与监管问询。</p><button className="primary-action" onClick={onOpenVault}>前往财报证据仓</button></div></section>;
  return <section className="company-filing-panel">
    <div className="company-filing-head"><div><span className="eyebrow">DISCLOSURE & GOVERNANCE</span><h2>{company.name} · 公告与审计证据</h2><p>确认事实、待复核线索和不可检索缺口严格分层；“未命中”不能写成“正常”。</p></div><button onClick={onOpenVault}>进入完整证据仓 →</button></div>
    <div className="company-filing-stats"><article><span>归档公告</span><strong>{filings.length}</strong><small>最近披露 {filings[0]?.disclosed_at?.slice(0, 10) || "日期待补"}</small></article><article><span>已确认证据</span><strong>{confirmed.length}</strong><small>其中确认红旗 {confirmedRed.length}</small></article><article><span>待复核线索</span><strong>{pending.length}</strong><small>不能作为公司事实</small></article><article className={unsearchable ? "danger" : ""}><span>检索覆盖</span><strong>{searchable}/{filings.length}</strong><small>{partial ? `OCR 部分 ${partial} · ` : ""}不可检索 {unsearchable}</small></article></div>
    {unsearchable > 0 && <div className="company-filing-warning"><span>!</span><p><strong>{unsearchable} 份公告目前不可完整检索</strong><small>在完成 OCR 或人工原件复核前，不能据此认定审计、减值、关联交易或担保不存在异常。</small></p></div>}
    <div className="company-filing-grid"><div><h3>最近公告原件</h3>{filings.slice(0, 6).map((filing) => <article className="company-filing-item" key={filing.id}><div><b>{filingTypeLabels[filing.filing_type || "other"] || filing.filing_type || "公告"}</b><time>{filing.disclosed_at?.slice(0, 10) || "日期待补"}</time></div><strong>{filing.title}</strong><footer><span>{filing.extraction_status === "ready" || filing.extraction_status === "ready_ocr" ? "可检索" : filing.extraction_status === "ocr_partial" ? `OCR ${filing.ocr_pages_done || 0}/${filing.ocr_total_pages || filing.page_count || 0}` : "需要 OCR / 人工核对"}</span><a href={`/api/filings/artifact?filing_id=${filing.id}`} target="_blank" rel="noreferrer">打开原件 ↗</a></footer></article>)}</div><div><h3>确认事实与待复核线索</h3>{[...confirmedRed, ...confirmed.filter((item) => item.evidence.severity !== "red"), ...pending].slice(0, 8).map(({ filing, evidence }) => <article className={`company-signal-item ${evidence.review_status || "pending"} ${evidence.severity}`} key={evidence.id}><div><b>{evidenceCategoryLabels[evidence.category] || evidence.title}</b><span>{evidence.review_status === "confirmed" ? evidence.severity === "red" ? "确认红旗" : "已确认" : "待复核线索"}</span></div><p>{evidence.excerpt}</p><footer><small>{filing.title}</small><a href={`/api/filings/artifact?filing_id=${filing.id}#page=${evidence.page_number}`} target="_blank" rel="noreferrer">PDF 第 {evidence.page_number} 页 ↗</a></footer></article>)}{!activeSignals.length && <div className="company-signal-empty"><strong>当前没有有效关键词线索</strong><p>这只表示已提取文本未命中或线索已排除，不代表公告不存在风险。</p></div>}</div></div>
    {status && <p className="company-filing-status">{status}</p>}
  </section>;
}

function FilingsView({ dashboard, onRefresh }: { dashboard: DashboardData | null; onRefresh: () => Promise<void> }) {
  const [modalOpen, setModalOpen] = useState(false);
  const [sourceMode, setSourceMode] = useState<"search" | "url" | "local">("search");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<ApiFiling | null>(null);
  const [evidence, setEvidence] = useState<FilingEvidence[]>([]);
  const [facts, setFacts] = useState<FilingFact[]>([]);
  const [coverage, setCoverage] = useState<FilingCoverage | null>(null);
  const [loadingEvidence, setLoadingEvidence] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [ocring, setOcring] = useState(false);
  const [searching, setSearching] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [officialSearch, setOfficialSearch] = useState({ symbol: "", since: `${new Date().getFullYear() - 1}-01-01`, until: new Date().toISOString().slice(0, 10) });
  const [officialResults, setOfficialResults] = useState<CninfoAnnouncement[]>([]);
  const [form, setForm] = useState({ symbol: "", company_name: "", title: "", filing_type: "annual_report", report_period: "", disclosed_at: "", source_url: "", local_path: "", is_correction: false });
  const filings = dashboard?.filings || [];
  const stats = dashboard?.filing_stats || {};
  const syncStates = dashboard?.filing_sync_states || [];
  const lastSync = syncStates[0];

  async function importFiling() {
    setStatus(sourceMode === "url" ? "正在下载并校验官方公告原件…" : "正在归档本地公告原件…");
    try {
      const response = await apiRequest<{ filing: ApiFiling; evidence: FilingEvidence[] }>("/api/filings/import", {
        method: "POST",
        body: JSON.stringify({ ...form, source_url: sourceMode === "url" ? form.source_url : "", local_path: sourceMode === "local" ? form.local_path : "" }),
      });
      setSelected(response.filing);
      setEvidence(response.evidence);
      setModalOpen(false);
      setStatus(response.filing.duplicate ? "该公告原件已经归档，未重复保存" : `归档完成，并提取 ${response.evidence.length} 条待复核证据`);
      await onRefresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "公告归档失败");
    }
  }

  async function searchOfficialFilings() {
    setSearching(true);
    setStatus("正在查询巨潮公开公告目录…");
    try {
      const response = await apiRequest<{ announcements: CninfoAnnouncement[] }>("/api/filings/search", { method: "POST", body: JSON.stringify({ ...officialSearch, limit: 30 }) });
      setOfficialResults(response.announcements);
      setStatus(response.announcements.length ? `找到 ${response.announcements.length} 份关键财报与监管文件，请选择需要归档的原件` : "该时间段未找到年报正文、审计、更正或问询文件");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "巨潮公告查询失败");
    } finally {
      setSearching(false);
    }
  }

  async function archiveOfficialResult(announcement: CninfoAnnouncement) {
    setStatus(`正在归档：${announcement.title}`);
    try {
      const response = await apiRequest<{ filing: ApiFiling; evidence: FilingEvidence[] }>("/api/filings/import", { method: "POST", body: JSON.stringify(announcement) });
      setSelected(response.filing);
      setEvidence(response.evidence);
      setStatus(response.filing.duplicate ? "该公告已经归档" : `归档完成，并提取 ${response.evidence.length} 条待复核证据`);
      await onRefresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "公告归档失败");
    }
  }

  async function syncPortfolioFilings() {
    setSyncing(true);
    setStatus("正在增量检查持仓与观察清单的关键公告；首次同步可能需要几分钟…");
    try {
      const response = await apiRequest<{ symbols: number; archived: number; duplicates: number; failed: number; retry_attempts: number; results: Array<{ symbol: string; status: string; remaining: number; retry_attempts: number }> }>("/api/filings/sync", { method: "POST", body: JSON.stringify({ max_documents_per_symbol: 6, retry_count: 2 }) });
      await onRefresh();
      const remaining = response.results.reduce((sum, item) => sum + Number(item.remaining || 0), 0);
      const retryNote = response.retry_attempts ? `，期间自动重试 ${response.retry_attempts} 次` : "";
      setStatus(response.failed ? `已归档 ${response.archived} 份，${response.failed} 项重试后仍失败并保留游标${retryNote}` : remaining ? `已归档 ${response.archived} 份，仍有 ${remaining} 份待下次继续；游标尚未前移${retryNote}` : `同步完成：${response.symbols} 只股票，新增 ${response.archived} 份，已存在 ${response.duplicates} 份${retryNote}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "公告增量同步失败");
    } finally {
      setSyncing(false);
    }
  }

  async function openEvidence(filing: ApiFiling) {
    setSelected(filing);
    setLoadingEvidence(true);
    try {
      const [items, factItems, matrix] = await Promise.all([
        apiRequest<FilingEvidence[]>(`/api/filings/evidence?filing_id=${filing.id}`),
        apiRequest<FilingFact[]>(`/api/filings/facts?filing_id=${filing.id}`),
        apiRequest<FilingCoverage>(`/api/filings/coverage?filing_id=${filing.id}`),
      ]);
      setEvidence(items);
      setFacts(factItems);
      setCoverage(matrix);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "证据读取失败");
    } finally {
      setLoadingEvidence(false);
    }
  }

  async function reviewEvidence(item: FilingEvidence, reviewStatus: "pending" | "confirmed" | "dismissed") {
    try {
      const response = await apiRequest<{ evidence: FilingEvidence }>("/api/filings/evidence/review", { method: "POST", body: JSON.stringify({ id: item.id, review_status: reviewStatus }) });
      setEvidence((current) => current.map((candidate) => candidate.id === item.id ? response.evidence : candidate));
      if (selected) setCoverage(await apiRequest<FilingCoverage>(`/api/filings/coverage?filing_id=${selected.id}`));
      setStatus(reviewStatus === "confirmed" ? "已确认：该线索可以作为分析证据" : reviewStatus === "dismissed" ? "已排除误报：该线索不会进入 AI 证据包" : "已恢复为待复核线索");
      await onRefresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "证据审核失败");
    }
  }

  async function reviewFact(item: FilingFact, reviewStatus: "pending" | "confirmed" | "dismissed") {
    try {
      const response = await apiRequest<{ fact: FilingFact }>("/api/filings/facts/review", { method: "POST", body: JSON.stringify({ id: item.id, review_status: reviewStatus }) });
      setFacts((current) => current.map((candidate) => candidate.id === item.id ? response.fact : candidate));
      setStatus(reviewStatus === "confirmed" ? "已确认：该监管指标将进入确定性财务计算与 AI 证据包" : reviewStatus === "dismissed" ? "已排除：该数字不会进入分析" : "已恢复为待复核指标");
      await onRefresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "监管指标审核失败");
    }
  }

  async function reindexSelected() {
    if (!selected) return;
    setReindexing(true);
    setStatus("正在校验原件哈希并重新抽取文本…");
    try {
      const response = await apiRequest<{ filing: ApiFiling; evidence: FilingEvidence[]; facts: FilingFact[]; coverage: FilingCoverage }>("/api/filings/reindex", { method: "POST", body: JSON.stringify({ filing_id: selected.id }) });
      setSelected(response.filing);
      setEvidence(response.evidence);
      setFacts(response.facts);
      setCoverage(response.coverage);
      setStatus(response.filing.extraction_status === "ready" ? `重新抽取完成：${response.evidence.length} 条待复核线索` : `文本层不可可靠检索：${response.filing.extraction_note || "需要 OCR"}`);
      await onRefresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "重新抽取失败");
    } finally {
      setReindexing(false);
    }
  }

  async function ocrBatches(batchCount: number) {
    if (!selected) return;
    const filingId = selected.id;
    setOcring(true);
    setStatus(batchCount > 1 ? "正在本机连续 OCR，最多处理 40 页；每 8 页保存一次进度…" : "正在本机 OCR 下一批 8 页；文档不会上传…");
    try {
      let latest: { filing: ApiFiling; evidence: FilingEvidence[]; facts: FilingFact[]; coverage: FilingCoverage } | null = null;
      for (let batch = 1; batch <= batchCount; batch += 1) {
        latest = await apiRequest<{ filing: ApiFiling; evidence: FilingEvidence[]; facts: FilingFact[]; coverage: FilingCoverage }>("/api/filings/ocr", { method: "POST", body: JSON.stringify({ filing_id: filingId, batch_size: 8 }) });
        setSelected(latest.filing);
        setEvidence(latest.evidence);
        setFacts(latest.facts);
        setCoverage(latest.coverage);
        const done = Number(latest.filing.ocr_pages_done || 0);
        const total = Number(latest.filing.ocr_total_pages || latest.filing.page_count || 0);
        setStatus(latest.filing.extraction_status === "ready_ocr" ? `整份 OCR 完成：${total} 页，提取 ${latest.evidence.length} 条待复核线索` : `连续 OCR ${batch}/${batchCount} 批：已保存 ${done}/${total} 页；未处理页面仍不视为无风险`);
        if (latest.filing.extraction_status === "ready_ocr") break;
      }
      await onRefresh();
    } catch (error) {
      setStatus(error instanceof Error ? `OCR 已停止，之前完成的批次仍已保存：${error.message}` : "OCR 已停止，之前完成的批次仍已保存");
    } finally {
      setOcring(false);
    }
  }

  return <section className="workspace-page filings-page">
    <div className="page-intro"><div><span className="eyebrow">FILING EVIDENCE VAULT</span><h1>财报证据仓</h1><p>保存公告原件、披露时间与文件指纹；风险线索必须能回到具体 PDF 页，没找到不代表不存在。</p></div><div className="page-actions"><button className="secondary-action" disabled={syncing} onClick={syncPortfolioFilings}>{syncing ? "同步中…" : "↻ 增量同步自选与持仓"}</button><button className="primary-action" onClick={() => setModalOpen(true)}>＋ 归档公告原件</button></div></div>
    <div className="filing-stats">
      <article><span>归档公告</span><strong>{stats.filings || 0}</strong><small>原件按 SHA-256 去重</small></article>
      <article><span>覆盖公司</span><strong>{stats.companies || 0}</strong><small>本地独立保存</small></article>
      <article><span>待复核项目</span><strong>{(stats.pending_reviews || 0) + (stats.pending_facts || 0)}</strong><small>线索 {stats.pending_reviews || 0} · 指标 {stats.pending_facts || 0}</small></article>
      <article><span>已确认项目</span><strong>{(stats.confirmed_evidence || 0) + (stats.confirmed_facts || 0)}</strong><small>线索 {stats.confirmed_evidence || 0} · 指标 {stats.confirmed_facts || 0}</small></article>
    </div>
    <div className="evidence-principle"><span>证</span><p><strong>证据分层已经生效</strong><small>公告原文是事实来源；关键词只产生“复核线索”，AI 无权把缺失内容补成正常，也无权改写原始文件。</small></p><b>{lastSync ? `${lastSync.symbol} · ${lastSync.status === "success" ? "同步成功" : lastSync.status === "partial" ? "等待续传" : lastSync.status === "error" ? "同步失败" : "同步中"}` : "尚未增量同步"}</b></div>
    {status && <p className="inline-status">{status}</p>}
    <div className="filing-workspace">
      <section className="filing-list-panel"><div className="filing-panel-head"><div><span className="eyebrow">ARCHIVED FILINGS</span><h2>最近归档</h2></div><span>{filings.length} 份</span></div>
        {filings.length ? <div className="filing-list">{filings.map((filing) => <button key={filing.id} className={selected?.id === filing.id ? "selected" : ""} onClick={() => openEvidence(filing)}>
          <div className="filing-list-top"><span>{filing.symbol}</span><b>{filingTypeLabels[filing.filing_type || "other"] || filing.filing_type}</b></div><strong>{filing.title}</strong><p>{filing.disclosed_at || "披露时间待补充"} · {filing.extraction_status === "ocr_partial" ? `OCR ${filing.ocr_pages_done || 0}/${filing.ocr_total_pages || filing.page_count || 0} 页` : filing.extraction_status === "needs_ocr" ? `${filing.page_count || "—"} 页 · 需要 OCR` : filing.page_count ? `${filing.page_count} 页` : "页数待解析"}</p>{filing.supersedes_filing_id ? <small className="filing-lineage correction">更正：{filing.supersedes_title || `原公告 #${filing.supersedes_filing_id}`}</small> : filing.superseded_by_id ? <small className="filing-lineage superseded">已有后续更正：{filing.superseded_by_title || `公告 #${filing.superseded_by_id}`}</small> : filing.is_correction ? <small className="filing-lineage unresolved">更正关系待人工核对</small> : null}<footer><span>{filing.active_evidence_count ?? filing.evidence_count ?? 0} 条有效线索{filing.dismissed_count ? ` · 已排除 ${filing.dismissed_count}` : ""}</span>{Boolean(filing.red_count) && <em>{filing.red_count} 条高优先</em>}{Boolean(filing.review_count) && <small>{filing.review_count} 条待复核</small>}</footer>
        </button>)}</div> : <div className="filing-empty"><span>档</span><h3>还没有公告原件</h3><p>可粘贴交易所或巨潮的 PDF 直链，也可导入知识目录中的本地 PDF。</p><button onClick={() => setModalOpen(true)}>归档第一份财报</button></div>}
      </section>
      <section className="filing-evidence-panel"><div className="filing-panel-head"><div><span className="eyebrow">SOURCE-LOCATED SIGNALS</span><h2>{selected ? "原文排雷线索" : "选择一份公告"}</h2></div>{selected && <div className="filing-panel-actions">{["needs_ocr", "ocr_partial"].includes(selected.extraction_status || "") && <><button disabled={ocring} onClick={() => ocrBatches(1)}>{ocring ? "OCR 中…" : "OCR 下一批 8 页"}</button><button disabled={ocring} onClick={() => ocrBatches(5)} title="连续执行 5 个小批次；每批完成即保存，失败时不会丢失之前进度">连续 OCR 40 页</button></>}<button disabled={reindexing || ocring} onClick={reindexSelected}>{reindexing ? "抽取中…" : "重读文本层"}</button><a href={`/api/filings/artifact?filing_id=${selected.id}`} target="_blank" rel="noreferrer">打开 PDF 原件 ↗</a></div>}</div>
        {!selected ? <div className="filing-evidence-empty"><p>点击左侧公告后，审计意见、减值、关联交易、担保、受限资金等线索会按页展示。</p></div> : loadingEvidence ? <div className="filing-evidence-empty"><p>正在读取证据…</p></div> : <>
          {(selected.supersedes_filing_id || selected.superseded_by_id || selected.is_correction) && <div className={`filing-lineage-banner ${selected.supersedes_filing_id ? "correction" : selected.superseded_by_id ? "superseded" : "unresolved"}`}><strong>{selected.supersedes_filing_id ? "本公告是更正版本" : selected.superseded_by_id ? "本公告已有后续更正" : "更正关系尚未确定"}</strong><span>{selected.supersedes_filing_id ? `关联原件：${selected.supersedes_title || `公告 #${selected.supersedes_filing_id}`}` : selected.superseded_by_id ? `后续版本：${selected.superseded_by_title || `公告 #${selected.superseded_by_id}`}` : "系统未找到唯一的同公司、同报告期、同类型原件，请人工核对，当前不会自动替代。"}</span></div>}
          {facts.length > 0 && <section className="bank-fact-candidates"><div><strong>银行监管指标候选</strong><small>代码按原文标签提取；确认后进入财务计算与 AI 分析，冲突值自动隔离</small></div><div className="bank-fact-grid">{facts.map((fact) => { const factState = fact.application_status === "superseded" ? "已被更正公告替代" : fact.review_status === "dismissed" ? "已排除" : fact.review_status !== "confirmed" ? "待人工复核" : fact.application_status === "applied" ? "已进入财务计算" : fact.application_status === "conflict_isolated" ? "数值冲突 · 已隔离" : fact.application_status === "waiting_financial_period" ? "已确认 · 等待同报告期快照" : fact.application_status === "waiting_financial_snapshot" ? "已确认 · 等待财务快照" : "已确认 · 进入 AI 证据"; return <article key={fact.id} className={`${fact.review_status || "pending"} ${fact.application_status || ""}`}><div className="bank-fact-title"><span>{fact.label}</span><b>{factState}</b></div><strong>{(fact.value * 100).toFixed(2)}%</strong><small>{fact.period_end || "报告期待核对"} · {fact.scope === "group" ? "集团口径" : fact.scope}</small><p>{fact.excerpt}</p><footer><a href={`/api/filings/artifact?filing_id=${fact.filing_id}#page=${fact.page_number}`} target="_blank" rel="noreferrer">PDF 第 {fact.page_number} 页 ↗</a><div>{fact.review_status !== "confirmed" && <button onClick={() => reviewFact(fact, "confirmed")}>确认</button>}{fact.review_status !== "dismissed" && <button onClick={() => reviewFact(fact, "dismissed")}>排除</button>}{fact.review_status && fact.review_status !== "pending" && <button onClick={() => reviewFact(fact, "pending")}>撤销</button>}</div></footer></article>; })}</div></section>}
          {coverage && <div className="filing-coverage"><div className="filing-coverage-head"><div><strong>排雷覆盖矩阵</strong><small>{coverage.searchable ? `已对提取文本执行 12 类确定性检索 · 可读度 ${Math.round(Number(coverage.extraction_quality || 0) * 100)}%` : coverage.partially_searchable ? `OCR 进行中 ${coverage.ocr_pages_done || 0}/${coverage.ocr_total_pages || 0} 页 · 未处理页面不计入覆盖` : `当前不可完整检索 · ${coverage.extraction_note || coverage.extraction_status}`}</small></div><span>{coverage.categories.filter((item) => item.status === "found_confirmed").length} 项已确认</span></div><div className="filing-coverage-grid">{coverage.categories.map((item) => <div key={item.category} className={item.status}><i /><span>{item.label}</span><b>{item.status === "found_confirmed" ? `已确认 ${item.confirmed}` : item.status === "found_pending" ? `待复核 ${item.pending}` : item.status === "reviewed_no_issue" ? "命中已排除" : item.status === "not_searchable" ? coverage.partially_searchable ? "未完成全页 OCR" : "文本不可检索" : "文本未命中"}</b></div>)}</div><p>{coverage.disclaimer}</p></div>}
          {evidence.length ? <div className="filing-evidence-list">{evidence.map((item) => <article key={item.id} className={`${item.severity} ${item.review_status || "pending"}`}><div><span>{evidenceCategoryLabels[item.category] || item.title}</span><b>{item.review_status === "confirmed" ? "已确认" : item.review_status === "dismissed" ? "已排除" : item.severity === "red" ? "高优先复核" : item.severity === "review" ? "需要复核" : "信息项"}</b></div><p>{item.excerpt}</p>{Boolean(item.detected_values?.length) && <div className="detected-values">{item.detected_values?.map((value, index) => <span key={`${value.raw}-${index}`}>{value.raw}</span>)}</div>}<footer><a href={`/api/filings/artifact?filing_id=${item.filing_id}#page=${item.page_number}`} target="_blank" rel="noreferrer">PDF 第 {item.page_number} 页 ↗</a><span>{item.locator}</span></footer><div className="evidence-review-actions">{item.review_status !== "confirmed" && <button className="confirm" onClick={() => reviewEvidence(item, "confirmed")}>确认是有效证据</button>}{item.review_status !== "dismissed" && <button onClick={() => reviewEvidence(item, "dismissed")}>排除误报</button>}{item.review_status && item.review_status !== "pending" && <button onClick={() => reviewEvidence(item, "pending")}>恢复待复核</button>}</div></article>)}</div> : <div className="filing-evidence-empty safe"><span>○</span><h3>未提取到关键词线索</h3><p>上方矩阵只说明检索覆盖，不代表风险不存在。请结合原文、财务变化和人工复核。</p></div>}
        </>}
      </section>
    </div>
    {modalOpen && <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="filing-import-title"><button className="modal-backdrop" aria-label="关闭" onClick={() => setModalOpen(false)} /><div className="upload-modal filing-modal"><button className="modal-close" onClick={() => setModalOpen(false)} aria-label="关闭">×</button><span className="upload-symbol">证</span><h2 id="filing-import-title">归档公告原件</h2><p>系统只接受官方 HTTPS PDF 链接，或知识根目录内的本地 PDF；原件按哈希保存，不会静默覆盖。</p>
      <div className="filing-source-tabs"><button className={sourceMode === "search" ? "selected" : ""} onClick={() => setSourceMode("search")}>巨潮自动查询</button><button className={sourceMode === "url" ? "selected" : ""} onClick={() => setSourceMode("url")}>官方 PDF 链接</button><button className={sourceMode === "local" ? "selected" : ""} onClick={() => setSourceMode("local")}>本地 PDF 文件</button></div>
      {sourceMode === "search" ? <div className="official-filing-search">
        <div className="rule-form-grid"><label><span>股票代码</span><input value={officialSearch.symbol} onChange={(event) => setOfficialSearch({ ...officialSearch, symbol: event.target.value })} placeholder="600519" /></label><label><span>查询范围</span><div className="date-range"><input value={officialSearch.since} onChange={(event) => setOfficialSearch({ ...officialSearch, since: event.target.value })} aria-label="开始日期"/><i>至</i><input value={officialSearch.until} onChange={(event) => setOfficialSearch({ ...officialSearch, until: event.target.value })} aria-label="结束日期"/></div></label></div>
        <button className="official-search-button" disabled={searching} onClick={searchOfficialFilings}>{searching ? "查询中…" : "查询关键财报与监管文件"}</button>
        {officialResults.length > 0 && <div className="official-results">{officialResults.map((item) => <article key={item.source_notice_id || item.source_url}><div><span>{filingTypeLabels[item.filing_type] || item.filing_type}</span><b>{item.disclosed_at?.slice(0, 10) || "日期待确认"}</b></div><strong>{item.title}</strong><small>{item.symbol} · {item.company_name || ""}</small><button onClick={() => archiveOfficialResult(item)}>下载原件并归档</button></article>)}</div>}
        <p className="official-search-note">仅筛选年报/中报/季报正文、审计与内控报告、更正公告、监管问询；摘要和普通临时公告不会自动进入证据仓。</p>
      </div> : <>
        <div className="rule-form-grid"><label><span>股票代码</span><input value={form.symbol} onChange={(event) => setForm({ ...form, symbol: event.target.value })} placeholder="600519" /></label><label><span>公司简称</span><input value={form.company_name} onChange={(event) => setForm({ ...form, company_name: event.target.value })} placeholder="贵州茅台" /></label></div>
        <label><span>公告标题</span><input value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} placeholder="贵州茅台2025年年度报告" /></label>
        <div className="rule-form-grid"><label><span>公告类型</span><select value={form.filing_type} onChange={(event) => setForm({ ...form, filing_type: event.target.value })}>{Object.entries(filingTypeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label><span>报告期</span><input value={form.report_period} onChange={(event) => setForm({ ...form, report_period: event.target.value })} placeholder="2025-12-31" /></label></div>
        <label><span>披露时间</span><input value={form.disclosed_at} onChange={(event) => setForm({ ...form, disclosed_at: event.target.value })} placeholder="2026-03-30 18:00" /></label>
        {sourceMode === "url" ? <label><span>官方 PDF 直链</span><input value={form.source_url} onChange={(event) => setForm({ ...form, source_url: event.target.value })} placeholder="https://static.cninfo.com.cn/finalpage/...PDF" /></label> : <label><span>本地 PDF 完整路径</span><input value={form.local_path} onChange={(event) => setForm({ ...form, local_path: event.target.value })} placeholder="D:\\投资资料\\公司年报.pdf" /></label>}
        <label className="filing-check"><input type="checkbox" checked={form.is_correction} onChange={(event) => setForm({ ...form, is_correction: event.target.checked })} /><span>这是更正或补充公告</span></label>
      </>}
      <div className="modal-actions"><button onClick={() => setModalOpen(false)}>关闭</button>{sourceMode !== "search" && <button className="primary" onClick={importFiling}>校验并归档</button>}</div></div></div>}
  </section>;
}

function comparisonValue(company: ComparisonCompany, key: string): number | null {
  const value = company.metrics?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatComparisonValue(value: number | null, format: string): string {
  if (value === null) return "—";
  if (format === "percent") return `${(value * 100).toFixed(1)}%`;
  if (format === "amount") return formatAmount(value);
  return `${value.toFixed(2)}×`;
}

function CompareView({ dashboard, onRefresh, onOpenCompany, initialSymbol, onInitialSymbolConsumed }: { dashboard: DashboardData | null; onRefresh: () => Promise<void>; onOpenCompany: (company: ComparisonCompany) => void; initialSymbol?: string | null; onInitialSymbolConsumed: () => void }) {
  const seedSymbols = useMemo(() => {
    const available = [...(dashboard?.holdings || []), ...(dashboard?.watchlist || [])].map((item) => item.symbol).filter(Boolean);
    return Array.from(new Set(available)).slice(0, 3).join("，") || "601088，600519，000333";
  }, [dashboard]);
  const [symbolsText, setSymbolsText] = useState(initialSymbol || seedSymbols);
  const [result, setResult] = useState<ComparisonResult | null>(null);
  const [status, setStatus] = useState(initialSymbol ? `已带入 ${initialSymbol}；请再添加 1–3 家可比公司` : "");
  const [loading, setLoading] = useState(false);
  const [holdingTarget, setHoldingTarget] = useState<ComparisonCompany | null>(null);
  const [holdingDraft, setHoldingDraft] = useState({ quantity: "", cost: "", current_price_as_of: "", max_loss_pct: "10", thesis: "", horizon: "1—3 年" });
  const candidates = [...(dashboard?.holdings || []), ...(dashboard?.watchlist || [])].filter((item, index, all) => all.findIndex((candidate) => candidate.symbol === item.symbol) === index).slice(0, 8);
  const readyCompanies = result?.companies.filter((company) => company.status === "ready") || [];
  const industries = Array.from(new Set(readyCompanies.map((company) => company.industry).filter(Boolean)));

  useEffect(() => {
    if (!initialSymbol) return;
    const timer = window.setTimeout(onInitialSymbolConsumed, 0);
    return () => window.clearTimeout(timer);
  }, [initialSymbol, onInitialSymbolConsumed]);

  function parsedSymbols(): string[] {
    return Array.from(new Set(symbolsText.split(/[\s,，、;；]+/).map((item) => item.trim().split(".", 1)[0]).filter((item) => /^\d{6}$/.test(item))));
  }

  async function compare(refresh: boolean) {
    const symbols = parsedSymbols();
    if (symbols.length < 2 || symbols.length > 4) return setStatus("请输入 2 到 4 个不同的六位股票代码");
    setLoading(true);
    setStatus(refresh ? "正在更新财务数据并统一口径计算…" : "正在读取本地财务快照…");
    try {
      const response = await apiRequest<ComparisonResult>("/api/compare", { method: "POST", body: JSON.stringify({ symbols, refresh }) });
      setResult(response);
      setStatus(response.coverage.ready === response.coverage.requested ? "" : `已完成 ${response.coverage.ready}/${response.coverage.requested} 家；缺失项已明确标记`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "公司对比失败");
    } finally {
      setLoading(false);
    }
  }

  function addCandidate(symbol: string) {
    const current = parsedSymbols();
    if (!current.includes(symbol) && current.length < 4) current.push(symbol);
    setSymbolsText(current.join("，"));
  }

  function isBest(company: ComparisonCompany, metric: typeof comparisonMetrics[number]): boolean {
    const value = comparisonValue(company, metric.key);
    const values = readyCompanies.map((item) => comparisonValue(item, metric.key)).filter((item): item is number => item !== null);
    if (value === null || values.length < 2) return false;
    return metric.better === "low" ? value === Math.min(...values) : value === Math.max(...values);
  }

  async function addWatch(company: ComparisonCompany) {
    try {
      await apiRequest<ApiWatch>("/api/watchlist", { method: "POST", body: JSON.stringify({ symbol: company.symbol, name: company.name, note: `来自公司对比 · ${company.decision?.status || "继续观察"} · 财务截至 ${company.as_of || "待补"}` }) });
      await onRefresh();
      setStatus(`${company.name} 已加入观察清单`);
    } catch (error) { setStatus(error instanceof Error ? error.message : "加入观察失败"); }
  }

  function prepareHolding(company: ComparisonCompany) {
    const price = company.market?.market_price;
    setHoldingTarget(company);
    setHoldingDraft({ quantity: "", cost: typeof price === "number" ? String(price) : "", current_price_as_of: typeof company.market?.price_as_of === "string" ? company.market.price_as_of : "", max_loss_pct: "10", thesis: "", horizon: "1—3 年" });
  }

  async function saveComparedHolding() {
    if (!holdingTarget) return;
    const quantity = Number(holdingDraft.quantity);
    const cost = Number(holdingDraft.cost);
    if (!Number.isFinite(quantity) || quantity <= 0 || !Number.isFinite(cost) || cost <= 0) return setStatus("加入持仓前必须填写大于 0 的数量和实际成本价");
    try {
      await apiRequest<ApiHolding>("/api/holdings", { method: "POST", body: JSON.stringify({ symbol: holdingTarget.symbol, name: holdingTarget.name, industry: holdingTarget.industry || "", quantity, cost, current_price: typeof holdingTarget.market?.market_price === "number" ? holdingTarget.market.market_price : undefined, current_price_as_of: holdingDraft.current_price_as_of, max_loss_pct: Number(holdingDraft.max_loss_pct), thesis: holdingDraft.thesis, horizon: holdingDraft.horizon }) });
      await onRefresh();
      setHoldingTarget(null);
      setStatus(`${holdingTarget.name} 已加入持仓，成本纪律和财务变化将进入组合诊断`);
    } catch (error) { setStatus(error instanceof Error ? error.message : "加入持仓失败"); }
  }

  return <section className="workspace-page compare-page">
    <div className="page-intro"><div><span className="eyebrow">PEER COMPARISON</span><h1>公司横向对比</h1><p>同一口径比较成长、盈利、现金质量与偿债能力。先看财务事实，再谈哪家公司更适合你的价格和仓位。</p></div></div>
    <section className="compare-builder">
      <div className="compare-input-row"><label><span>股票代码（2–4 家）</span><input value={symbolsText} onChange={(event) => setSymbolsText(event.target.value)} placeholder="例如 601088，600519，000333" /></label><button disabled={loading} onClick={() => compare(false)}>用本地快照对比</button><button className="primary" disabled={loading} onClick={() => compare(true)}>{loading ? "计算中…" : "更新数据并对比"}</button></div>
      {candidates.length > 0 && <div className="compare-candidates"><span>从持仓 / 观察清单添加</span>{candidates.map((item) => <button key={item.symbol} onClick={() => addCandidate(item.symbol)}>{item.name || item.symbol} <small>{item.symbol}</small></button>)}</div>}
      <p className="compare-privacy">更新数据只调用已配置的 Tushare 或免费数据源，不会调用 AI，也不消耗模型 Token。</p>
    </section>
    {status && <p className="inline-status">{status}</p>}
    {!result && <section className="compare-empty"><span>比</span><h2>把候选公司放到一张财务底片上</h2><p>系统会统一报告期、TTM、金额单位和公式版本。没有数据时显示“缺失”，不会拿演示数据补位。</p><div><b>成长</b><b>盈利</b><b>现金</b><b>杠杆</b><b>风险旗帜</b></div></section>}
    {result && <>
      <div className="compare-summary">
        <article><span>完成覆盖</span><strong>{result.coverage.ready}/{result.coverage.requested}</strong><small>具备可比较财务数据</small></article>
        <article><span>统一口径</span><strong>TTM</strong><small>最近十二个月</small></article>
        <article><span>行业数量</span><strong>{industries.length || "—"}</strong><small>{industries.length > 1 ? "跨行业对比需谨慎" : "同业可比性较高"}</small></article>
        <article><span>AI 消耗</span><strong>0</strong><small>全部由本地公式计算</small></article>
      </div>
      {industries.length > 1 && <div className="compare-warning"><span>!</span><p><strong>这是一组跨行业公司</strong><small>ROIC、现金质量可以参考，但估值倍数和资产负债结构不应直接机械排名。</small></p></div>}
      <section className="compare-table-panel">
        <div className="compare-table-scroll"><table className="compare-table"><thead><tr><th><span>财务维度</span><small>最近可用 TTM</small></th>{result.companies.map((company) => <th key={company.symbol}><span>{company.name}</span><small>{company.symbol} · {company.industry || "行业待识别"}</small></th>)}</tr></thead><tbody>
          {comparisonMetrics.map((metric, index) => <tr key={metric.key} className={index > 0 && comparisonMetrics[index - 1].group !== metric.group ? "group-start" : ""}><td><span>{metric.label}</span><small>{metric.group}</small></td>{result.companies.map((company) => <td key={company.symbol} className={isBest(company, metric) ? "best" : ""}>{company.status === "ready" ? <><strong>{formatComparisonValue(comparisonValue(company, metric.key), metric.format)}</strong>{isBest(company, metric) && <small>本组领先</small>}</> : <span className="missing-value">不可比较</span>}</td>)}</tr>)}
          <tr className="group-start risk-row"><td><span>财务红 / 黄旗</span><small>含变化检测</small></td>{result.companies.map((company) => <td key={company.symbol}>{company.status === "ready" ? <><strong>{(company.risk?.red_flags || 0) + (company.risk?.red_changes || 0)} / {(company.risk?.yellow_flags || 0) + (company.risk?.yellow_changes || 0)}</strong><small>{company.risk?.titles?.slice(0, 1).join("") || "暂无红黄旗"}</small></> : <><strong>—</strong><small>{company.error || "无数据"}</small></>}</td>)}</tr>
        </tbody></table></div>
      </section>
      <div className="compare-company-notes">{result.companies.map((company) => { const watched = dashboard?.watchlist?.some((item) => item.symbol === company.symbol); const held = dashboard?.holdings?.some((item) => item.symbol === company.symbol); const filing = company.filing_review; return <article key={company.symbol} className={company.status}><div><span>{company.symbol}</span><b>{company.status === "ready" ? company.decision?.status || "可比较" : company.status === "missing" ? "缺少快照" : company.status === "unsupported" ? "行业模板未支持" : "数据错误"}</b></div><h3>{company.name}</h3><p>{company.status === "ready" ? `数据截至 ${company.as_of || "—"}，来源 ${company.source?.provider || "本地快照"}。数据质量 ${company.data_quality?.score ?? "—"}。${company.decision?.reasons?.[0] || ""}` : company.error}</p>{Boolean(filing?.pending_evidence || filing?.confirmed_red || filing?.unsearchable) && <small className="compare-filing-status">公告：{filing?.confirmed_red ? `确认红旗 ${filing.confirmed_red}` : ""}{filing?.pending_evidence ? `${filing.confirmed_red ? " · " : ""}待复核 ${filing.pending_evidence}` : ""}{filing?.unsearchable ? `${filing.confirmed_red || filing.pending_evidence ? " · " : ""}不可检索 ${filing.unsearchable}` : ""}</small>}{Boolean(company.decision?.review_conditions?.length) && <small className="compare-review-condition">下一条件：{company.decision?.review_conditions?.[0]}</small>}{company.status === "ready" && <div className="compare-company-actions"><button onClick={() => onOpenCompany(company)}>打开研究</button><button disabled={watched} onClick={() => addWatch(company)}>{watched ? "已在观察" : "加入观察"}</button><button disabled={held} onClick={() => prepareHolding(company)}>{held ? "已在持仓" : "加入持仓"}</button></div>}</article>; })}</div>
      <p className="compare-methodology">{result.methodology?.warning}</p>
    </>}
    {holdingTarget && <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="compare-holding-title"><button className="modal-backdrop" onClick={() => setHoldingTarget(null)} aria-label="关闭"/><div className="upload-modal holding-modal"><button className="modal-close" onClick={() => setHoldingTarget(null)} aria-label="关闭">×</button><span className="upload-symbol">仓</span><h2 id="compare-holding-title">将 {holdingTarget.name} 加入持仓</h2><p>对比排名不是买入理由。请填写真实数量、成本、风险预算和可验证逻辑；软件不会自动下单。</p><div className="form-grid"><label><span>持仓数量 *</span><input type="number" min="0" value={holdingDraft.quantity} onChange={(event) => setHoldingDraft({ ...holdingDraft, quantity: event.target.value })}/></label><label><span>实际成本价 *</span><input type="number" min="0" value={holdingDraft.cost} onChange={(event) => setHoldingDraft({ ...holdingDraft, cost: event.target.value })}/></label><label><span>当前价日期</span><input type="date" value={holdingDraft.current_price_as_of} onChange={(event) => setHoldingDraft({ ...holdingDraft, current_price_as_of: event.target.value })}/></label><label><span>最大承受亏损 %</span><input type="number" min="0" max="100" value={holdingDraft.max_loss_pct} onChange={(event) => setHoldingDraft({ ...holdingDraft, max_loss_pct: event.target.value })}/></label><label><span>计划持有周期</span><input value={holdingDraft.horizon} onChange={(event) => setHoldingDraft({ ...holdingDraft, horizon: event.target.value })}/></label><label className="wide"><span>买入逻辑 / 验证条件</span><textarea rows={3} value={holdingDraft.thesis} onChange={(event) => setHoldingDraft({ ...holdingDraft, thesis: event.target.value })} placeholder="例如：现金流覆盖分红，下一期净息差停止下滑"/></label></div><div className="modal-actions"><button onClick={() => setHoldingTarget(null)}>取消</button><button className="primary" disabled={Number(holdingDraft.quantity) <= 0 || Number(holdingDraft.cost) <= 0} onClick={saveComparedHolding}>确认加入持仓</button></div></div></div>}
  </section>;
}

function KnowledgeView({ dashboard, onRefresh }: { dashboard: DashboardData | null; onRefresh: () => Promise<void> }) {
  const [modalOpen, setModalOpen] = useState(false);
  const [libraryModalOpen, setLibraryModalOpen] = useState(false);
  const [ruleModalOpen, setRuleModalOpen] = useState(false);
  const [knowledgeRoot, setKnowledgeRoot] = useState("");
  const [status, setStatus] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<Array<Record<string, unknown>>>([]);
  const [libraryFilter, setLibraryFilter] = useState<"all" | "enabled" | "organizing">("all");
  const [ruleFilter, setRuleFilter] = useState<"all" | "pending" | "approved" | "rejected">("all");
  const [selectedLibrary, setSelectedLibrary] = useState<number>(0);
  const [importLibrary, setImportLibrary] = useState<number>(0);
  const [libraryDraft, setLibraryDraft] = useState({ name: "", description: "", kind: "custom" });
  const [ruleDraft, setRuleDraft] = useState({ library_id: 0, title: "", content: "", category: "principle", applicability: "", conditions: "", exceptions: "", tags: "", industries: "", cycles: "", metrics: "", thresholds: "", counter_evidence: "", source_document_id: 0, source_chunk_id: 0, source_locator: "" });
  const [editingRuleId, setEditingRuleId] = useState(0);
  const [versionCard, setVersionCard] = useState<DoctrineCard | null>(null);
  const [versions, setVersions] = useState<DoctrineVersion[]>([]);
  const apiLibraries = dashboard?.libraries || [];
  const doctrineCards = dashboard?.doctrine_cards || [];
  const doctrineRelationships = dashboard?.doctrine_relationships?.relationships || [];
  const approvedRules = doctrineCards.filter((card) => card.review_status === "approved" && Boolean(card.enabled));
  const pendingRules = doctrineCards.filter((card) => card.review_status === "pending");
  const filteredRules = doctrineCards.filter((card) => (ruleFilter === "all" || card.review_status === ruleFilter) && (!selectedLibrary || card.library_id === selectedLibrary));
  const knowledgeLoaded = dashboard?.dashboard_scope === "knowledge" || dashboard?.dashboard_scope === "all";
  const displayLibraries = apiLibraries.filter((lib) => {
    const enabled = lib.enabled !== 0;
    const organizing = Number(lib.pending_card_count || 0) > 0 || (Number(lib.document_count || 0) > 0 && Number(lib.card_count || 0) === 0);
    return libraryFilter === "all" || (libraryFilter === "enabled" && enabled) || (libraryFilter === "organizing" && organizing);
  }).map((lib, index) => {
    const enabled = lib.enabled !== 0;
    const cardCount = Number(lib.card_count || 0);
    const pendingCount = Number(lib.pending_card_count || 0);
    const reviewedCount = Math.max(0, cardCount - pendingCount);
    const progress = cardCount > 0 ? Math.round((reviewedCount / cardCount) * 100) : 0;
    return { id: lib.id || 0, title: lib.name || lib.title || `知识库 ${index + 1}`, meta: `${lib.document_count ?? 0} 份资料 · ${lib.approved_card_count ?? 0} 条已启用规则`, color: ["jade", "gold", "blue", "violet"][index % 4], tag: !enabled ? "已停用" : pendingCount ? `${pendingCount} 条待审` : cardCount ? "已整理" : "待提炼", note: lib.description || "已连接本地知识库", updated: lib.updated_at || "本地数据", enabled, progress, reviewedCount, cardCount };
  });
  const effectiveImportLibrary = importLibrary || apiLibraries[0]?.id || 0;

  function openRuleDraft(item?: Record<string, unknown>) {
    const source = item ? asRecord(item.source) : undefined;
    const defaultLibrary = apiLibraries[0]?.id || 0;
    const rawTitle = String(item?.title || source?.title || "新投资规则");
    setRuleDraft({
      library_id: defaultLibrary,
      title: rawTitle.slice(0, 80),
      content: String(item?.content || "").slice(0, 3000),
      category: "principle",
      applicability: "",
      conditions: "",
      exceptions: "",
      tags: "",
      industries: "",
      cycles: "",
      metrics: "",
      thresholds: "",
      counter_evidence: "",
      source_document_id: Number(item?.document_id || 0),
      source_chunk_id: Number(item?.chunk_id || 0),
      source_locator: String(item?.locator || source?.locator || ""),
    });
    setEditingRuleId(0);
    setRuleModalOpen(true);
  }

  function editRule(card: DoctrineCard) {
    setRuleDraft({
      library_id: card.library_id,
      title: card.title,
      content: card.content,
      category: card.category || "principle",
      applicability: card.applicability || "",
      conditions: (card.conditions || []).join("\n"),
      exceptions: (card.exceptions || []).join("\n"),
      tags: (card.tags || []).join("，"),
      industries: (card.industries || []).join("，"),
      cycles: (card.cycles || []).join("，"),
      metrics: (card.metrics || []).join("，"),
      thresholds: (card.thresholds || []).join("\n"),
      counter_evidence: (card.counter_evidence || []).join("\n"),
      source_document_id: Number(card.source_document_id || 0),
      source_chunk_id: Number(card.source_chunk_id || 0),
      source_locator: card.source_locator || "",
    });
    setEditingRuleId(card.id);
    setRuleModalOpen(true);
  }

  async function saveRuleCandidate() {
    if (!ruleDraft.library_id) return setStatus("请先选择所属投资体系");
    setStatus(editingRuleId ? "正在保存修订并转回待审核…" : "正在保存候选规则…");
    try {
      const parsedDraft = {
        ...ruleDraft,
        source_document_id: ruleDraft.source_document_id || null,
        source_chunk_id: ruleDraft.source_chunk_id || null,
        conditions: ruleDraft.conditions.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
        exceptions: ruleDraft.exceptions.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
        tags: ruleDraft.tags.split(/[，,、]/).map((item) => item.trim()).filter(Boolean),
        industries: ruleDraft.industries.split(/[，,、]/).map((item) => item.trim()).filter(Boolean),
        cycles: ruleDraft.cycles.split(/[，,、]/).map((item) => item.trim()).filter(Boolean),
        metrics: ruleDraft.metrics.split(/[，,、]/).map((item) => item.trim()).filter(Boolean),
        thresholds: ruleDraft.thresholds.split(/\r?\n|[，,、]/).map((item) => item.trim()).filter(Boolean),
        counter_evidence: ruleDraft.counter_evidence.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
      };
      await apiRequest<{ card: DoctrineCard }>(editingRuleId ? "/api/doctrine-cards/update" : "/api/doctrine-cards", {
        method: "POST",
        body: JSON.stringify({
          ...(editingRuleId ? { id: editingRuleId, action: "edit" } : {}),
          ...parsedDraft,
        }),
      });
      setRuleModalOpen(false);
      setEditingRuleId(0);
      setRuleFilter("pending");
      setStatus(editingRuleId ? "修订已保存为新版本；原审批已失效，请重新审核后启用" : "候选规则已保存，请审核后再启用");
      await onRefresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "规则保存失败");
    }
  }

  async function generateCandidates() {
    if (!selectedLibrary) return setStatus("请先在审核台选择一个投资体系，再从该体系文档生成候选规则");
    setStatus("正在从该体系的文档中提取候选规则…");
    try {
      const result = await apiRequest<{ candidates_created?: number; duplicates?: number; scanned_chunks?: number }>("/api/doctrine-cards/generate", {
        method: "POST",
        body: JSON.stringify({ library_id: selectedLibrary, max_candidates: 120 }),
      });
      setRuleFilter("pending");
      setStatus(`已扫描 ${result.scanned_chunks || 0} 个知识片段，新增 ${result.candidates_created || 0} 条待审核规则，跳过 ${result.duplicates || 0} 条重复内容`);
      await onRefresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "候选规则生成失败");
    }
  }

  async function openVersions(card: DoctrineCard) {
    setStatus("正在读取规则版本…");
    try {
      const result = await apiRequest<DoctrineVersion[]>(`/api/doctrine-cards/versions?id=${card.id}`);
      setVersionCard(card);
      setVersions(result);
      setStatus("");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "版本历史读取失败");
    }
  }

  async function reviewRule(id: number, action: "approve" | "reject" | "reset" | "enable" | "disable") {
    setStatus("正在更新规则状态…");
    try {
      await apiRequest<{ card: DoctrineCard }>("/api/doctrine-cards/update", { method: "POST", body: JSON.stringify({ id, action }) });
      setStatus(action === "approve" ? "规则已审核通过并参与后续分析" : action === "reject" ? "规则已驳回，不会参与分析" : "规则状态已更新");
      await onRefresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "规则更新失败");
    }
  }

  async function toggleLibrary(id: number, enabled: boolean) {
    setStatus(enabled ? "正在停用该投资体系…" : "正在启用该投资体系…");
    try {
      const result = await apiRequest<{ analysis_policy?: string }>("/api/libraries/update", { method: "POST", body: JSON.stringify({ id, action: enabled ? "disable" : "enable" }) });
      setStatus(result.analysis_policy || "投资体系状态已更新");
      await onRefresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "投资体系状态更新失败");
    }
  }

  async function importKnowledge() {
    if (!effectiveImportLibrary) return setStatus("请先选择资料要归入的投资体系；不同作者或流派应分别导入");
    setStatus("正在扫描并建立索引…");
    try {
      const result = await apiRequest<Record<string, unknown>>("/api/knowledge/import", { method: "POST", body: JSON.stringify({ root: knowledgeRoot || undefined, library_id: effectiveImportLibrary }) });
      const legacyWordCount = Number(result.legacy_word_documents || 0);
      setStatus(`导入完成：${Number(result.imported ?? result.documents ?? 0)} 份资料已处理${legacyWordCount ? `；发现 ${legacyWordCount} 份旧 .doc，需在 Word 中另存为 .docx 或导出 PDF 后再导入` : ""}`);
      setModalOpen(false);
      await onRefresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "导入失败");
    }
  }

  async function createLibrary() {
    if (!libraryDraft.name.trim()) return setStatus("请填写投资体系名称");
    setStatus("正在创建投资体系…");
    try {
      const result = await apiRequest<{ id: number }>("/api/libraries", { method: "POST", body: JSON.stringify(libraryDraft) });
      setSelectedLibrary(result.id);
      setImportLibrary(result.id);
      setLibraryModalOpen(false);
      setLibraryDraft({ name: "", description: "", kind: "custom" });
      setStatus("投资体系已创建，可以把对应作者或流派的资料导入其中");
      await onRefresh();
      setModalOpen(true);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "投资体系创建失败");
    }
  }

  async function searchKnowledge() {
    if (!searchQuery.trim()) return;
    setStatus("正在检索知识库…");
    try {
      const result = await apiRequest<Array<Record<string, unknown>> | { results?: Array<Record<string, unknown>> }>("/api/knowledge/search", { method: "POST", body: JSON.stringify({ query: searchQuery, limit: 8 }) });
      setSearchResults(Array.isArray(result) ? result : result.results || []);
      setStatus("");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "检索失败");
    }
  }
  return (
    <section className="workspace-page">
      <div className="page-intro"><div><span className="eyebrow">KNOWLEDGE BRAIN</span><h1>知识大脑</h1><p>把投资大师、行业专家和你的经验，转化为可追溯、可复用的分析规则。</p></div><button className="primary-action" onClick={() => setModalOpen(true)}>＋ 导入新资料</button></div>
      {!knowledgeLoaded && <p className="inline-status">正在载入知识体系与规则关系…</p>}
      <div className="knowledge-stats">
        <article><span>知识库</span><strong>{knowledgeLoaded ? apiLibraries.length : "—"}</strong><small>多体系独立分析</small></article>
          <article><span>有效资料</span><strong>{dashboard?.document_stats?.unique_documents ?? dashboard?.documents?.length ?? 0}</strong><small>{dashboard?.document_stats?.quarantined_documents ? `已隔离 ${dashboard.document_stats.quarantined_documents} 份未归类旧资料` : dashboard?.document_stats?.duplicates ? `另识别 ${dashboard.document_stats.duplicates} 份重复副本` : ".docx / PDF / Markdown"}</small></article>
        <article><span>已启用规则</span><strong>{approvedRules.length}</strong><small>只有审核通过的规则参与分析</small></article>
        <article><span>待审核规则</span><strong>{pendingRules.length}</strong><small>原文观点不会自动生效</small></article>
      </div>
      <div className="knowledge-search"><span>⌕</span><input value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") searchKnowledge(); }} placeholder="搜索观点、规则、作者或原文…" aria-label="搜索知识库"/><button onClick={searchKnowledge}>搜索知识库</button></div>
      {status && <p className="inline-status">{status}</p>}
      {searchResults.length > 0 && <div className="knowledge-results"><div className="section-heading"><h3>检索结果</h3><button onClick={() => setSearchResults([])}>收起 ×</button></div>{searchResults.map((item, index) => <article key={index}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{String(item.title || item.document_title || asRecord(item.source)?.title || "知识片段")}</strong><p>{String(item.content || item.text || item.snippet || JSON.stringify(item)).slice(0, 260)}</p><button className="extract-rule" onClick={() => openRuleDraft(item)}>提炼为候选规则 →</button></div></article>)}</div>}
      <div className="library-toolbar"><h2>我的投资体系</h2><div>{(["all", "enabled", "organizing"] as const).map((filter) => <button key={filter} className={libraryFilter === filter ? "selected" : ""} onClick={() => setLibraryFilter(filter)}>{filter === "all" ? `全部 ${apiLibraries.length}` : filter === "enabled" ? `已启用 ${apiLibraries.filter((lib) => lib.enabled !== 0).length}` : `待整理 ${apiLibraries.filter((lib) => Number(lib.pending_card_count || 0) > 0 || (Number(lib.document_count || 0) > 0 && Number(lib.card_count || 0) === 0)).length}`}</button>)}</div></div>
      <div className="library-grid">
        {displayLibraries.map((lib) => (
          <article className={`library-card ${lib.enabled ? "" : "disabled"}`} key={lib.id}>
            <div className="library-card-top"><span className={`library-emblem ${lib.color}`}>{lib.title.slice(0, 1)}</span><button className="library-state-action" aria-label={`${lib.enabled ? "停用" : "启用"}${lib.title}`} onClick={() => toggleLibrary(lib.id, lib.enabled)}>{lib.enabled ? "停用" : "启用"}</button></div>
            <h3>{lib.title}</h3><p>{lib.note}</p>
            <div className="library-meta"><span>{lib.meta}</span><b>{lib.tag}</b></div>
            <div className="library-progress" role="progressbar" aria-label={`${lib.title}规则审核进度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={lib.progress}><i style={{ width: `${lib.progress}%` }} /></div>
            <footer><span>{lib.cardCount ? `已复核 ${lib.reviewedCount}/${lib.cardCount} · ` : "尚未生成规则 · "}更新于 {lib.updated}</span><button onClick={() => { setSelectedLibrary(lib.id); setRuleFilter("all"); }}>查看规则 →</button></footer>
          </article>
        ))}
        {!displayLibraries.length && apiLibraries.length > 0 && <div className="library-filter-empty"><span>筛</span><strong>当前筛选下没有投资体系</strong><button onClick={() => setLibraryFilter("all")}>查看全部</button></div>}
        <button className="new-library-card" onClick={() => setLibraryModalOpen(true)}><span>＋</span><strong>创建新的投资体系</strong><small>红利、格雷厄姆或某位作者应分别建立</small></button>
      </div>
      <section className="rule-workbench">
        <div className="rule-workbench-head">
          <div><span className="eyebrow">DOCTRINE REVIEW</span><h2>投资规则审核台</h2><p>文档片段先成为候选规则，经过你确认后才会进入 Codex / API 的分析证据包。</p></div>
          <div className="rule-head-actions"><button onClick={generateCandidates}>从该体系文档生成候选</button><button className="primary-action" onClick={() => openRuleDraft()}>＋ 手动创建规则</button></div>
        </div>
        <div className="rule-filters">
          <select value={selectedLibrary} onChange={(event) => setSelectedLibrary(Number(event.target.value))} aria-label="按投资体系筛选">
            <option value={0}>全部投资体系</option>
            {apiLibraries.map((library) => <option key={library.id} value={library.id}>{library.name || library.title}</option>)}
          </select>
          {(["all", "pending", "approved", "rejected"] as const).map((filter) => <button key={filter} className={ruleFilter === filter ? "selected" : ""} onClick={() => setRuleFilter(filter)}>{filter === "all" ? "全部" : filter === "pending" ? `待审核 ${pendingRules.length}` : filter === "approved" ? `已通过 ${approvedRules.length}` : "已驳回"}</button>)}
        </div>
        <div className="rule-list">
          {filteredRules.length ? filteredRules.map((card) => <article className={`rule-card ${card.review_status || "pending"}`} key={card.id}>
            <div className="rule-card-main">
              <div className="rule-card-title"><span>{card.library_name || "投资体系"} · v{card.current_version || 1}</span><b>{card.review_status === "approved" ? card.enabled ? "已通过 · 启用中" : "已通过 · 已停用" : card.review_status === "rejected" ? "已驳回" : "待审核"}</b></div>
              <h3>{card.title}</h3><p>{card.content}</p>
              {(card.applicability || card.conditions?.length || card.exceptions?.length) ? <dl>
                {card.applicability && <div><dt>适用范围</dt><dd>{card.applicability}</dd></div>}
                {!!card.conditions?.length && <div><dt>成立条件</dt><dd>{card.conditions.join("；")}</dd></div>}
                {!!card.exceptions?.length && <div><dt>例外 / 反证</dt><dd>{card.exceptions.join("；")}</dd></div>}
                {!!card.industries?.length && <div><dt>适用行业</dt><dd>{card.industries.join("、")}</dd></div>}
                {!!card.cycles?.length && <div><dt>适用周期</dt><dd>{card.cycles.join("、")}</dd></div>}
                {!!card.metrics?.length && <div><dt>财务指标</dt><dd>{card.metrics.join("、")}</dd></div>}
                {!!card.thresholds?.length && <div><dt>判断阈值</dt><dd>{card.thresholds.join("；")}</dd></div>}
                {!!card.counter_evidence?.length && <div><dt>反方证据</dt><dd>{card.counter_evidence.join("；")}</dd></div>}
              </dl> : null}
              <footer><span>{card.source_locator ? `原文定位 ${card.source_locator}` : "手动创建"}</span>{card.tags?.length ? <span>{card.tags.map((tag) => `#${tag}`).join(" ")}</span> : null}</footer>
            </div>
            <div className="rule-actions">
              <button onClick={() => openVersions(card)}>版本 {card.version_count || 1}</button>
              <button onClick={() => editRule(card)}>修订</button>
              {card.review_status === "pending" && <><button className="approve" onClick={() => reviewRule(card.id, "approve")}>通过并启用</button><button onClick={() => reviewRule(card.id, "reject")}>驳回</button></>}
              {card.review_status === "approved" && <button onClick={() => reviewRule(card.id, card.enabled ? "disable" : "enable")}>{card.enabled ? "暂停使用" : "重新启用"}</button>}
              {card.review_status === "rejected" && <button onClick={() => reviewRule(card.id, "reset")}>重新审核</button>}
            </div>
          </article>) : <div className="rule-empty"><span>尺</span><h3>这里还没有规则卡</h3><p>先搜索文档并点击“提炼为候选规则”，或手动建立一条。系统不会自己批准观点。</p><button onClick={() => openRuleDraft()}>创建第一条规则</button></div>}
        </div>
      </section>
      <section className="doctrine-relations">
        <div className="section-heading"><div><span className="eyebrow">CONSENSUS &amp; CONFLICT</span><h2>流派共识与冲突</h2></div><p>{dashboard?.doctrine_relationships?.summary?.conflicts || 0} 项冲突 · {dashboard?.doctrine_relationships?.summary?.consensus || 0} 项共识</p></div>
        {doctrineRelationships.length ? <div className="relationship-list">{doctrineRelationships.slice(0, 12).map((item, index) => <article className={item.kind} key={`${item.left.card_id}-${item.right.card_id}-${index}`}>
          <div className="relationship-kind"><span>{item.kind === "conflict" ? "≠" : "≈"}</span><b>{item.kind === "conflict" ? "观点冲突" : "观点共识"}</b><small>{item.anchors.map((anchor) => anchor.replace(/^metric:|^tag:|^category:/, "")).join(" · ")}</small></div>
          <div className="relationship-side"><span>{item.left.library_name}</span><strong>{item.left.title}</strong><p>{item.left.content}</p><small>v{item.left.version || 1} · {item.left.source_locator || "手动规则"}</small></div>
          <div className="relationship-divider">{item.kind === "conflict" ? "VS" : "AND"}</div>
          <div className="relationship-side"><span>{item.right.library_name}</span><strong>{item.right.title}</strong><p>{item.right.content}</p><small>v{item.right.version || 1} · {item.right.source_locator || "手动规则"}</small></div>
        </article>)}</div> : <div className="relationship-empty"><span>≠</span><div><strong>启用两套以上投资体系后，这里会显示真实关系</strong><p>系统只提供复核线索；你需要回看双方原文确认，不会让 AI 把观点混成平均数。</p></div></div>}
      </section>
      {modalOpen && (
        <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="upload-title">
          <button className="modal-backdrop" aria-label="关闭" onClick={() => setModalOpen(false)} />
          <div className="upload-modal"><button className="modal-close" onClick={() => setModalOpen(false)} aria-label="关闭">×</button><span className="upload-symbol">文</span><h2 id="upload-title">导入投资资料</h2><p>支持 .docx、PDF、Markdown 与 TXT。旧 .doc 会被明确报告但不会自动转换；请先在 Word 中另存为 .docx 或导出 PDF。原文件始终保持只读。</p><label><span>归入投资体系</span><select value={effectiveImportLibrary} onChange={(event) => setImportLibrary(Number(event.target.value))}><option value={0}>请选择投资体系</option>{apiLibraries.map((library) => <option key={library.id} value={library.id}>{library.name || library.title}</option>)}</select></label><label><span>资料所在文件夹</span><input value={knowledgeRoot} onChange={(event) => setKnowledgeRoot(event.target.value)} placeholder="例如 D:\\投资资料\\格雷厄姆" /><small>必须明确填写具体资料目录；允许范围：{dashboard?.settings?.knowledge_root || "本地知识根目录"}</small></label><button className="drop-zone" onClick={importKnowledge}><span>⇧</span><strong>扫描并归入所选体系</strong><small>已索引的同一文件会直接建立归属，不重复解析</small></button><div className="modal-actions"><button onClick={() => { setModalOpen(false); setLibraryModalOpen(true); }}>新建体系</button><button onClick={() => setModalOpen(false)}>取消</button><button className="primary" disabled={!knowledgeRoot.trim()} onClick={importKnowledge}>开始导入</button></div></div>
        </div>
      )}
      {libraryModalOpen && (
        <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="library-title">
          <button className="modal-backdrop" aria-label="关闭" onClick={() => setLibraryModalOpen(false)} />
          <div className="upload-modal holding-modal"><button className="modal-close" onClick={() => setLibraryModalOpen(false)} aria-label="关闭">×</button><span className="upload-symbol">派</span><h2 id="library-title">创建投资体系</h2><p>一个体系对应一套独立分析尺子。不同作者、流派或你的个人框架不要混在同一个体系里。</p><div className="form-grid"><label className="wide"><span>体系名称</span><input value={libraryDraft.name} onChange={(event) => setLibraryDraft({ ...libraryDraft, name: event.target.value })} placeholder="例如：格雷厄姆安全边际" /></label><label><span>类型</span><select value={libraryDraft.kind} onChange={(event) => setLibraryDraft({ ...libraryDraft, kind: event.target.value })}><option value="custom">自定义体系</option><option value="dividend">红利投资</option><option value="value">价值投资</option><option value="personal">个人框架</option></select></label><label className="wide"><span>说明</span><textarea rows={3} value={libraryDraft.description} onChange={(event) => setLibraryDraft({ ...libraryDraft, description: event.target.value })} placeholder="这套体系关注什么、适用于哪些公司？" /></label></div><div className="modal-actions"><button onClick={() => setLibraryModalOpen(false)}>取消</button><button className="primary" onClick={createLibrary}>创建并导入资料</button></div></div>
        </div>
      )}
      {ruleModalOpen && (
        <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="rule-title">
          <button className="modal-backdrop" aria-label="关闭" onClick={() => { setRuleModalOpen(false); setEditingRuleId(0); }} />
          <div className="upload-modal rule-modal">
            <button className="modal-close" onClick={() => { setRuleModalOpen(false); setEditingRuleId(0); }} aria-label="关闭">×</button>
            <span className="upload-symbol">尺</span><h2 id="rule-title">{editingRuleId ? "修订投资规则" : "建立候选投资规则"}</h2><p>{editingRuleId ? "修订会产生新版本，并自动撤销旧版本的启用状态；重新审核通过后才能再次参与分析。" : "先忠实记录观点、条件和例外。保存后仍是“待审核”，不会立即影响股票分析。"}</p>
            <div className="rule-form-grid">
              <label><span>所属投资体系</span><select value={ruleDraft.library_id} onChange={(event) => setRuleDraft({ ...ruleDraft, library_id: Number(event.target.value) })}><option value={0}>请选择</option>{apiLibraries.map((library) => <option value={library.id} key={library.id}>{library.name || library.title}</option>)}</select></label>
              <label><span>规则分类</span><select value={ruleDraft.category} onChange={(event) => setRuleDraft({ ...ruleDraft, category: event.target.value })}><option value="principle">投资原则</option><option value="cash_flow">现金流</option><option value="valuation">估值</option><option value="balance_sheet">资产负债</option><option value="governance">治理审计</option><option value="portfolio">仓位纪律</option></select></label>
            </div>
            <label><span>规则标题</span><input value={ruleDraft.title} onChange={(event) => setRuleDraft({ ...ruleDraft, title: event.target.value })} placeholder="例如：分红必须由正常化自由现金流覆盖" /></label>
            <label><span>核心主张</span><textarea rows={4} value={ruleDraft.content} onChange={(event) => setRuleDraft({ ...ruleDraft, content: event.target.value })} placeholder="这条观点具体要求什么？不要只写结论。" /></label>
            <label><span>适用范围</span><input value={ruleDraft.applicability} onChange={(event) => setRuleDraft({ ...ruleDraft, applicability: event.target.value })} placeholder="例如：成熟期非金融企业；不适用于银行" /></label>
            <div className="rule-form-grid">
              <label><span>适用行业</span><input value={ruleDraft.industries} onChange={(event) => setRuleDraft({ ...ruleDraft, industries: event.target.value })} placeholder="消费，公用事业，非金融" /></label>
              <label><span>适用周期</span><input value={ruleDraft.cycles} onChange={(event) => setRuleDraft({ ...ruleDraft, cycles: event.target.value })} placeholder="成熟期，全周期，衰退期" /></label>
            </div>
            <div className="rule-form-grid">
              <label><span>财务指标</span><input value={ruleDraft.metrics} onChange={(event) => setRuleDraft({ ...ruleDraft, metrics: event.target.value })} placeholder="free_cash_flow，dividend_yield" /></label>
              <label><span>判断阈值</span><textarea rows={2} value={ruleDraft.thresholds} onChange={(event) => setRuleDraft({ ...ruleDraft, thresholds: event.target.value })} placeholder={"股息率至少3%\n派息率不超过70%"} /></label>
            </div>
            <div className="rule-form-grid">
              <label><span>成立条件（每行一条）</span><textarea rows={3} value={ruleDraft.conditions} onChange={(event) => setRuleDraft({ ...ruleDraft, conditions: event.target.value })} placeholder={"经营现金流稳定\n剔除一次性收益"} /></label>
              <label><span>例外 / 反证（每行一条）</span><textarea rows={3} value={ruleDraft.exceptions} onChange={(event) => setRuleDraft({ ...ruleDraft, exceptions: event.target.value })} placeholder={"周期高点利润不可外推\n重大并购后重新评估"} /></label>
            </div>
            <label><span>标签</span><input value={ruleDraft.tags} onChange={(event) => setRuleDraft({ ...ruleDraft, tags: event.target.value })} placeholder="红利，现金流，安全边际" /></label>
            <label><span>需要主动寻找的反方证据（每行一条）</span><textarea rows={3} value={ruleDraft.counter_evidence} onChange={(event) => setRuleDraft({ ...ruleDraft, counter_evidence: event.target.value })} placeholder={"资本开支突然上升\n股息由举债维持"} /></label>
            {ruleDraft.source_locator && <p className="rule-source-note">原文定位：{ruleDraft.source_locator}</p>}
            <div className="modal-actions"><button onClick={() => { setRuleModalOpen(false); setEditingRuleId(0); }}>取消</button><button className="primary" onClick={saveRuleCandidate}>{editingRuleId ? "保存新版本并重新审核" : "保存为待审核"}</button></div>
          </div>
        </div>
      )}
      {versionCard && (
        <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="version-title">
          <button className="modal-backdrop" aria-label="关闭" onClick={() => setVersionCard(null)} />
          <div className="upload-modal version-modal"><button className="modal-close" onClick={() => setVersionCard(null)} aria-label="关闭">×</button><span className="upload-symbol">史</span><h2 id="version-title">{versionCard.title} · 版本历史</h2><p>每次编辑、审核、启停都会留下快照，历史报告可固定使用当时版本。</p><div className="version-list">{versions.map((version) => <article key={version.id}><div><strong>v{version.version_no}</strong><span>{doctrineVersionLabel(version.change_kind)}</span><time>{version.created_at || "本地时间"}</time></div><p>{version.snapshot?.content || "—"}</p><small>{version.snapshot?.review_status || "pending"} · {version.snapshot?.enabled ? "已启用" : "未启用"} · {version.snapshot?.source_locator || "手动规则"}</small></article>)}</div><div className="modal-actions"><button className="primary" onClick={() => setVersionCard(null)}>关闭</button></div></div>
        </div>
      )}
    </section>
  );
}

function WatchlistView({ dashboard, onRefresh, onOpenCompany, onPrepareHolding, onOpenCompare }: { dashboard: DashboardData | null; onRefresh: () => Promise<void>; onOpenCompany: (symbol: string) => void; onPrepareHolding: (holding: ApiHolding) => void; onOpenCompare: (symbol: string) => void }) {
  const [modalOpen, setModalOpen] = useState(false);
  const [status, setStatus] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [form, setForm] = useState({ symbol: "", name: "", note: "" });
  const watchlist = dashboard?.watchlist || [];

  async function save() {
    setStatus("正在保存…");
    try {
      await apiRequest<ApiWatch>("/api/watchlist", { method: "POST", body: JSON.stringify(form) });
      setForm({ symbol: "", name: "", note: "" });
      setModalOpen(false);
      setStatus("已加入观察清单");
      await onRefresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "保存失败");
    }
  }

  async function remove(symbol: string) {
    try {
      await apiRequest<{ ok: boolean }>(`/api/watchlist?symbol=${encodeURIComponent(symbol)}`, { method: "DELETE" });
      setStatus("已移出观察清单");
      await onRefresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "移除失败");
    }
  }

  async function refreshQuotes() {
    setRefreshing(true);
    setStatus("正在从可用数据源更新观察清单数据…");
    try {
      const result = await apiRequest<{ refreshed?: string[]; errors?: Array<{ symbol?: string; error?: string }> }>("/api/watchlist/refresh", { method: "POST", body: JSON.stringify({}) });
      await onRefresh();
      const errors = result.errors || [];
      setStatus(errors.length ? `已更新 ${result.refreshed?.length || 0} 只；${errors[0]?.error || "部分股票暂时无法更新"}` : `已更新 ${result.refreshed?.length || 0} 只股票`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "行情更新失败");
    } finally {
      setRefreshing(false);
    }
  }

  return <section className="workspace-page watchlist-page">
    <div className="page-intro"><div><span className="eyebrow">WATCHLIST</span><h1>观察清单</h1><p>把还没准备好买入、但值得持续跟踪的公司单独放在这里。</p></div><div className="page-actions"><button className="secondary-action" disabled={refreshing || !watchlist.length} onClick={refreshQuotes}>{refreshing ? "更新中…" : "↻ 刷新数据"}</button><button className="primary-action" onClick={() => setModalOpen(true)}>＋ 添加股票</button></div></div>
    {status && <p className="inline-status">{status}</p>}
    {!watchlist.length ? <div className="empty-state"><span>观</span><h2>观察清单还是空的</h2><p>你可以从研究页点击“加入观察”，也可以在这里手动添加股票代码和跟踪理由。</p><button className="primary-action empty-action" onClick={() => setModalOpen(true)}>添加第一只股票</button></div> : <div className="watch-grid">{watchlist.map((item) => { const company = companies.find((candidate) => candidate.code === item.symbol); const quote = item.quote; const research = item.research; const filing = research?.filing_review; const price = quote?.market_price == null ? null : Number(quote.market_price); const held = dashboard?.holdings?.some((holding) => holding.symbol === item.symbol); const statusText = item.quote_status === "ready" ? "已更新" : item.quote_status === "stale" ? "本地旧快照" : "尚未刷新"; return <article className={`watch-card ${research?.priority || "yellow"}`} key={item.symbol}><div className="watch-card-head"><span className="company-mark small">{(item.name || company?.name || item.symbol).slice(0, 1)}</span><div><h2>{item.name || company?.name || item.symbol}</h2><small>{item.symbol} · {company?.industry || "待补充行业"}</small></div><button className="watch-remove" aria-label={`移除${item.symbol}`} onClick={() => remove(item.symbol)}>×</button></div><div className="watch-quote"><strong>{price !== null && Number.isFinite(price) ? `¥ ${price.toFixed(2)}` : "暂无真实行情"}</strong><span className={item.quote_status === "ready" ? "positive" : "muted-text"}>{statusText}</span></div><div className="watch-facts"><span>PE <b>{quote?.pe ?? "—"}</b></span><span>PB <b>{quote?.pb ?? "—"}</b></span><span>财务截至 <b>{research?.financial_as_of || quote?.as_of || "—"}</b></span></div><div className="watch-research-state"><div><strong>{research?.status || "等待条件"}</strong><span>{research?.reasons?.[0] || "等待真实财务事实与公告复核"}</span></div>{Boolean(filing?.pending_evidence || filing?.confirmed_red || filing?.unsearchable) && <small>公告：{filing?.confirmed_red ? `确认红旗 ${filing.confirmed_red}` : ""}{filing?.pending_evidence ? `${filing?.confirmed_red ? " · " : ""}待复核 ${filing.pending_evidence}` : ""}{filing?.unsearchable ? `${filing?.confirmed_red || filing?.pending_evidence ? " · " : ""}不可检索 ${filing.unsearchable}` : ""}</small>}{Boolean(research?.review_conditions?.length) && <p>下一条件：{research?.review_conditions?.[0]}</p>}</div><p>{item.note || "还没有记录跟踪理由。"}</p><footer><div className="watch-card-actions"><button onClick={() => onOpenCompany(item.symbol)}>打开研究</button><button onClick={() => onOpenCompare(item.symbol)}>进入对比</button><button disabled={held} onClick={() => onPrepareHolding({ symbol: item.symbol, name: item.name || company?.name || item.symbol, industry: company?.industry || "", current_price: price !== null && Number.isFinite(price) ? price : null, current_price_as_of: quote?.as_of || "", thesis: item.note || "", horizon: "1—3 年", max_loss_pct: 10 })}>{held ? "已在持仓" : "转入持仓"}</button></div><span>加入于 {String(item.created_at || "").slice(0, 10) || "本地"}</span></footer></article>; })}</div>}
    {modalOpen && <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="watch-title"><button className="modal-backdrop" onClick={() => setModalOpen(false)} aria-label="关闭"/><div className="upload-modal holding-modal"><button className="modal-close" onClick={() => setModalOpen(false)} aria-label="关闭">×</button><span className="upload-symbol">观</span><h2 id="watch-title">添加到观察清单</h2><p>只记录你想跟踪的股票，不会自动买入或生成交易指令。</p><div className="form-grid"><label><span>股票代码 *</span><input value={form.symbol} onChange={(event) => setForm({ ...form, symbol: event.target.value })} placeholder="601088"/></label><label><span>公司名称</span><input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="中国神华"/></label><label className="wide"><span>跟踪理由</span><textarea value={form.note} onChange={(event) => setForm({ ...form, note: event.target.value })} placeholder="例如：等待估值进入安全边际，或等待下一次财报验证现金流…"/></label></div><div className="modal-actions"><button onClick={() => setModalOpen(false)}>取消</button><button className="primary" disabled={!form.symbol.trim()} onClick={save}>保存观察</button></div></div></div>}
  </section>;
}

function GettingStarted({ onNavigate, onOpenSettings, onDismiss }: { onNavigate: (view: View) => void; onOpenSettings: () => void; onDismiss: () => void }) {
  return <section className="getting-started" aria-labelledby="getting-started-title">
    <div className="getting-started-head"><div><span className="eyebrow">FIRST RESEARCH LOOP</span><h2 id="getting-started-title">从一家公司，开始建立自己的研究底片</h2><p>先让数据、证据和你的投资纪律进入系统；不会自动下单，也不会在未确认前调用模型。</p></div><button onClick={onDismiss} aria-label="本次关闭新手引导">×</button></div>
    <div className="getting-started-steps">
      <article><span>01</span><h3>确认数据源</h3><p>免费数据源可先用；有 Tushare 再补更完整的财务口径。</p><button onClick={onOpenSettings}>打开设置</button></article>
      <article><span>02</span><h3>建立观察</h3><p>先记录值得研究的公司和等待条件，不把关注当成买入。</p><button onClick={() => onNavigate("watchlist")}>添加观察股</button></article>
      <article><span>03</span><h3>录入真实持仓</h3><p>填写成本、数量、风险预算和买入逻辑，才会获得组合诊断。</p><button onClick={() => onNavigate("holdings")}>录入持仓</button></article>
      <article><span>04</span><h3>导入你的体系</h3><p>Word 等资料先生成待审核规则；审核通过后才会进入分析。</p><button onClick={() => onNavigate("knowledge")}>打开知识库</button></article>
    </div>
  </section>;
}

type SavedReview = { status: string; due_at?: string | null; expected_conditions?: string; actual_outcome?: string; actual_as_of?: string; reminder?: ReviewReminder };

function ReportReviewEditor({ report, onSaved, onStatus }: { report: ApiReport; onSaved: (review: SavedReview, message: string) => Promise<void>; onStatus: (message: string) => void }) {
  const [actualOutcome, setActualOutcome] = useState(report.actual_outcome || report.reviewer_note || "");
  const [actualAsOf, setActualAsOf] = useState(report.actual_as_of || "");
  const [expectedConditions, setExpectedConditions] = useState(report.expected_conditions || "");
  const [dueAt, setDueAt] = useState(report.review_due_at || "");

  async function save(reviewStatus: "open" | "reviewed" | "invalidated") {
    if (reviewStatus === "open" && (!expectedConditions.trim() || !dueAt)) {
      return onStatus("请先填写待验证条件和下次复核日期");
    }
    if (reviewStatus !== "open" && !actualOutcome.trim()) {
      return onStatus("请先填写后来实际结果或复盘笔记");
    }
    onStatus(reviewStatus === "open" ? "正在保存观察条件…" : "正在保存复盘结果…");
    try {
      const response = await apiRequest<{ review: SavedReview }>("/api/reports/review", {
        method: "POST",
        body: JSON.stringify({ report_id: report.id, status: reviewStatus, expected_conditions: expectedConditions, actual_outcome: actualOutcome, actual_as_of: actualAsOf, due_at: dueAt }),
      });
      const message = reviewStatus === "open" ? "观察条件已保存，到期后会在研究档案旁提醒" : reviewStatus === "invalidated" ? "已记录：原判断逻辑失效" : "复盘结果已保存";
      await onSaved(response.review, message);
    } catch (error) {
      onStatus(error instanceof Error ? error.message : "复盘保存失败");
    }
  }

  return <section className="report-review">
    <div className="section-heading"><h3>观察条件与复盘</h3><span>{report.review_status === "reviewed" ? "已完成" : report.review_status === "invalidated" ? "逻辑失效" : report.review_status === "open" ? report.reminder?.label : "尚未设置"}</span></div>
    <label><span>当时预期验证条件</span><input value={expectedConditions} onChange={(event) => setExpectedConditions(event.target.value)} placeholder="例如：下一季经营现金流恢复并覆盖分红" /></label>
    <label><span>下次复核日期</span><input type="date" value={dueAt} onChange={(event) => setDueAt(event.target.value)} /></label>
    <label><span>后来实际结果 / 复盘笔记</span><textarea rows={3} value={actualOutcome} onChange={(event) => setActualOutcome(event.target.value)} placeholder="到期后记录实际发生了什么，以及原判断哪里需要修正" /></label>
    <label><span>实际结果截至日期</span><input type="date" value={actualAsOf} onChange={(event) => setActualAsOf(event.target.value)} /></label>
    <div className="review-actions"><button onClick={() => save("open")}>保存待验证条件</button><button onClick={() => save("invalidated")}>记录逻辑失效</button><button className="primary-action" onClick={() => save("reviewed")}>完成复盘</button></div>
  </section>;
}

function ReportsView({ dashboard, onRefresh }: { dashboard: DashboardData | null; onRefresh: () => Promise<void> }) {
  const [reports, setReports] = useState<ApiReport[]>(dashboard?.reports || []);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [status, setStatus] = useState("");
  const [diff, setDiff] = useState<ReportDiff | null>(null);
  const [snapshotAudit, setSnapshotAudit] = useState<ReportSnapshotAudit | null>(null);
  const [narrative, setNarrative] = useState<ReportNarrative | null>(null);
  const latestReportId = dashboard?.reports?.[0]?.id;

  useEffect(() => {
    let active = true;
    apiRequest<ApiReport[]>("/api/reports?limit=50&include_content=0")
      .then((result) => { if (active) setReports(result); })
      .catch((error: unknown) => { if (active) setStatus(error instanceof Error ? error.message : "研究档案暂时不可用"); });
    return () => { active = false; };
  }, [latestReportId]);

  const selected = reports.find((report) => report.id === selectedId) || reports[0];
  const selectedReportId = selected?.id;
  const selectedNarrative = narrative?.id === selectedReportId ? narrative : null;
  const snapshotIntegrity = snapshotAudit?.id === selectedReportId ? snapshotAudit.integrity : undefined;
  const snapshotAuditLoading = Boolean(selectedReportId && snapshotAudit?.id !== selectedReportId);
  const previous = selected ? reports.find((report) => report.symbol === selected.symbol && report.id < selected.id) : undefined;
  const actionableReminders = reports.filter((report) => ["overdue", "due_today", "due_soon"].includes(report.reminder?.status || ""));

  useEffect(() => {
    let active = true;
    if (!selectedReportId) {
      return () => { active = false; };
    }
    apiRequest<ReportSnapshotAudit>(`/api/reports/integrity?id=${selectedReportId}`)
      .then((result) => { if (active) setSnapshotAudit(result); })
      .catch(() => { if (active) setSnapshotAudit({ id: selectedReportId, replayable: false, integrity: { status: "incomplete", notice: "无法读取快照完整性结果，当前不能宣称报告可复现。" } }); });
    return () => { active = false; };
  }, [selectedReportId]);

  useEffect(() => {
    let active = true;
    if (!selectedReportId) return () => { active = false; };
    apiRequest<ReportNarrative>(`/api/reports/content?id=${selectedReportId}`)
      .then((result) => { if (active) setNarrative(result); })
      .catch((error: unknown) => { if (active) setStatus(error instanceof Error ? error.message : "报告正文读取失败"); });
    return () => { active = false; };
  }, [selectedReportId]);

  async function compareWithPrevious() {
    if (!selected || !previous) return setStatus("当前报告没有同一股票的更早版本");
    setStatus("正在计算报告差异…");
    try {
      setDiff(await apiRequest<ReportDiff>(`/api/reports/diff?from=${previous.id}&to=${selected.id}`));
      setStatus("");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "报告差异读取失败");
    }
  }

  return (
    <section className="workspace-page reports-page">
      <div className="page-intro">
        <div><span className="eyebrow">RESEARCH ARCHIVE</span><h1>研究档案</h1><p>保存每次分析时的模型、证据范围和结论，方便复盘而不是事后改口。</p></div>
        <span className={`archive-count ${actionableReminders.length ? "has-reminders" : ""}`}>{actionableReminders.length ? `${actionableReminders.length} 项待复核` : `${reports.length} 份已保存`}</span>
      </div>
      {status && <p className="inline-status">{status}</p>}
      {!reports.length ? <div className="empty-state"><span>档</span><h2>还没有研究档案</h2><p>在财务研究页运行一次 Codex 或兼容 API 分析，报告会自动保存在这里。</p></div> : <div className="reports-layout">
        <div className="report-list">
          {reports.map((report) => <button key={report.id} className={`report-list-item ${selected?.id === report.id ? "selected" : ""} ${report.reminder?.status || ""}`} onClick={() => { setSelectedId(report.id); setDiff(null); }}><div><strong>{report.title || `${report.symbol} 财务研究`}</strong><small>{report.symbol} · {report.provider === "codex" ? "Codex" : "兼容 API"}{report.reminder?.status && !["none", "completed"].includes(report.reminder.status) ? ` · ${report.reminder.label}` : ""}</small></div><time>{String(report.created_at || "").slice(0, 16)}</time></button>)}
        </div>
        {selected && <article className="report-reader">
          <div className="report-reader-head"><div><span className="eyebrow">{selected.provider === "codex" ? "CODEX MEMO" : "API MEMO"}</span><h2>{selected.title}</h2><p>{selected.symbol} · {selected.created_at || "本地保存"} · 证据指纹 {String(selected.evidence_hash || "").slice(0, 12)}</p></div><span className={`archive-badge ${snapshotIntegrity?.status || "loading"}`}>{selected.review_status === "reviewed" ? "已复盘 · " : ""}{snapshotAuditLoading ? "正在校验" : snapshotIntegrity?.status === "verified" ? "已验证可复现" : snapshotIntegrity?.status === "corrupt" ? "快照校验失败" : "归档不完整"}</span></div>
          {selected.review_status === "open" && selected.reminder && <div className={`review-reminder ${selected.reminder.status}`}><strong>{selected.reminder.label}</strong><span>{selected.review_due_at} · {selected.expected_conditions}</span></div>}
          {selected.doctrine_coverage && selected.doctrine_coverage.status !== "not_applicable" && <div className={`doctrine-audit ${selected.doctrine_coverage.status}`}><strong>{selected.doctrine_coverage.status === "compliant" ? "流派分离格式已通过" : "流派分离格式待复核"}</strong><span>{selected.doctrine_coverage.notice}{selected.doctrine_coverage.missing_libraries?.length ? ` 缺少：${selected.doctrine_coverage.missing_libraries.join("、")}。` : ""}</span></div>}
          {snapshotIntegrity && <div className={`snapshot-integrity ${snapshotIntegrity.status || "incomplete"}`}><div><strong>{snapshotIntegrity.status === "verified" ? "快照完整性校验通过" : snapshotIntegrity.status === "corrupt" ? "快照内容与归档哈希不一致" : "快照归档信息不完整"}</strong><p>{snapshotIntegrity.notice}</p></div><div className="snapshot-checks"><span className={snapshotIntegrity.evidence_verified ? "pass" : "fail"}>证据哈希 {snapshotIntegrity.evidence_verified ? "✓" : "×"}</span><span className={snapshotIntegrity.prompt_verified ? "pass" : "fail"}>模型输入 {snapshotIntegrity.prompt_verified ? "✓" : "×"}</span><span className={snapshotIntegrity.formula_versioned ? "pass" : "fail"}>公式版本 {snapshotIntegrity.formula_versioned ? "✓" : "×"}</span><span className={snapshotIntegrity.formula_matches_current ? "pass" : "changed"}>当前公式 {snapshotIntegrity.formula_matches_current ? "一致" : "已变化"}</span></div></div>}
          <div className="report-actions"><button onClick={compareWithPrevious} disabled={!previous}>与上一版比较</button><a href={`/api/reports/snapshot?id=${selected.id}`} target="_blank" rel="noreferrer">打开证据快照</a></div>
          <div className="report-reader-body">{selectedNarrative ? <MarkdownReport content={selectedNarrative.content} /> : <p className="report-narrative-loading">正在按需读取报告正文…</p>}</div>
          {diff && <section className="report-diff">
            <div className="section-heading"><h3>跨日期变化与复盘</h3><small>{diff.before?.as_of || "—"} → {diff.after?.as_of || "—"}</small></div>
            {diff.data_context && <div className="report-context-diff"><div><span>前次数据口径</span>{diff.report_kind === "portfolio" ? <><b>持仓 {diff.data_context.before?.position_count ?? "—"} · 已计价 {diff.data_context.before?.priced_positions ?? "—"}</b><small>有财务快照 {diff.data_context.before?.financial_positions ?? "—"} 项</small></> : <><b>财务 {diff.data_context.before?.financial_as_of || "缺失"} · 价格 {diff.data_context.before?.price_as_of || "缺失"}</b><small>{diff.data_context.before?.source_provider || "来源未记录"} · {diff.data_context.before?.source_cache || "缓存状态未记录"}</small></>}</div><i>→</i><div><span>本次数据口径</span>{diff.report_kind === "portfolio" ? <><b>持仓 {diff.data_context.after?.position_count ?? "—"} · 已计价 {diff.data_context.after?.priced_positions ?? "—"}</b><small>有财务快照 {diff.data_context.after?.financial_positions ?? "—"} 项</small></> : <><b>财务 {diff.data_context.after?.financial_as_of || "缺失"} · 价格 {diff.data_context.after?.price_as_of || "缺失"}</b><small>{diff.data_context.after?.source_provider || "来源未记录"} · {diff.data_context.after?.source_cache || "缓存状态未记录"}</small></>}</div></div>}
            {Boolean(diff.portfolio_position_changes?.length) && <><h4>持仓与条件化决策变化</h4><div className="portfolio-position-diff">{diff.portfolio_position_changes?.map((item) => { const before = item.before; const after = item.after; const name = after?.name || before?.name || item.symbol; const percent = (value?: number | null) => value == null ? "—" : `${(value * 100).toFixed(1)}%`; return <article key={item.symbol} className={item.change}><div><strong>{name} · {item.symbol}</strong><b>{item.change === "added" ? "新增持仓" : item.change === "removed" ? "已移出" : "状态变化"}</b></div><p>仓位 {percent(before?.weight)} → {percent(after?.weight)} · 收益 {percent(before?.return_pct)} → {percent(after?.return_pct)}</p><small>决策 {before?.decision?.status || "无"} → {after?.decision?.status || "无"} · 财务截至 {before?.financial_as_of || "缺失"} → {after?.financial_as_of || "缺失"}</small></article>; })}</div></>}
            {diff.metric_changes?.length ? <><h4>确定性指标变化</h4><div className="diff-grid">{diff.metric_changes.map((item) => <div key={item.metric}><span>{item.metric}</span><b>{String(item.before ?? "—")} → {String(item.after ?? "—")}</b></div>)}</div></> : <p>确定性指标没有变化，或旧报告未保存结构化证据。</p>}
            {Boolean(diff.risk_signal_changes?.length) && <><h4>风险信号变化</h4><div className="report-signal-diff">{diff.risk_signal_changes?.map((item) => <article key={item.signal_id} className={item.change}><div><strong>{item.after?.title || item.before?.title || item.signal_id}</strong><b>{item.change === "added" ? "新增" : item.change === "removed" ? "解除" : "程度变化"}</b></div><p>{item.before?.severity || "无"} → {item.after?.severity || "无"} · {item.after?.detail || item.before?.detail || "详情未记录"}</p><small>{item.after?.review_condition || item.before?.review_condition || "复核条件未记录"}</small></article>)}</div></>}
            {Boolean(diff.rule_changes?.length) && <p>知识规则版本变化：{diff.rule_changes?.map((item) => `#${item.card_id} v${item.before || "—"}→v${item.after || "—"}`).join("、")}</p>}
            {(diff.judgment_follow_up?.before_report || diff.judgment_follow_up?.after_report) && <><h4>当时条件与后来结果</h4><div className="report-followup-diff">{diff.judgment_follow_up.before_report && <div><span>前次报告</span><b>{diff.judgment_follow_up.before_report.expected_conditions || "未设置验证条件"}</b><p>{diff.judgment_follow_up.before_report.actual_outcome || "尚未记录实际结果"}</p><small>{diff.judgment_follow_up.before_report.actual_as_of || diff.judgment_follow_up.before_report.due_at || "日期未记录"}</small></div>}{diff.judgment_follow_up.after_report && <div><span>本次报告</span><b>{diff.judgment_follow_up.after_report.expected_conditions || "未设置验证条件"}</b><p>{diff.judgment_follow_up.after_report.actual_outcome || "尚未记录实际结果"}</p><small>{diff.judgment_follow_up.after_report.actual_as_of || diff.judgment_follow_up.after_report.due_at || "日期未记录"}</small></div>}</div></>}
            <p className="report-change-flags">叙述结论 {diff.narrative_changed ? "已变化" : "未变化"} · 证据 {diff.evidence_changed ? "已变化" : "未变化"} · 流派输出 {diff.doctrine_output_changed ? "已变化" : "未变化"}</p>
            <small>{diff.warning}</small>
          </section>}
          <ReportReviewEditor key={selected.id} report={selected} onStatus={setStatus} onSaved={async (review, message) => {
            setReports((items) => items.map((item) => item.id === selected.id ? {
              ...item,
              review_status: review.status,
              review_due_at: review.due_at || null,
              expected_conditions: review.expected_conditions || "",
              actual_outcome: review.actual_outcome || "",
              actual_as_of: review.actual_as_of || "",
              reminder: review.reminder,
            } : item));
            await onRefresh();
            setStatus(message);
          }} />
        </article>}
      </div>}
    </section>
  );
}

function HoldingsView({ dashboard, onRefresh, engine, onOpenCompany, initialHolding, onInitialHoldingConsumed }: { dashboard: DashboardData | null; onRefresh: () => Promise<void>; engine: string; onOpenCompany: (holding: ApiHolding | PortfolioPosition) => void; initialHolding?: ApiHolding | null; onInitialHoldingConsumed: () => void }) {
  const blankForm = { symbol: "", name: "", industry: "", quantity: "", cost: "", current_price: "", current_price_as_of: "", thesis: "", horizon: "1—3 年", max_loss_pct: "10" };
  const seededForm = initialHolding ? {
    symbol: initialHolding.symbol,
    name: initialHolding.name || "",
    industry: initialHolding.industry || "",
    quantity: "",
    cost: "",
    current_price: initialHolding.current_price === null || initialHolding.current_price === undefined ? "" : String(initialHolding.current_price),
    current_price_as_of: initialHolding.current_price_as_of || "",
    thesis: initialHolding.thesis || "",
    horizon: initialHolding.horizon || "1—3 年",
    max_loss_pct: initialHolding.max_loss_pct === null || initialHolding.max_loss_pct === undefined ? "10" : String(initialHolding.max_loss_pct),
  } : blankForm;
  const [selected, setSelected] = useState("全部持仓");
  const [adding, setAdding] = useState(Boolean(initialHolding));
  const [importing, setImporting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [status, setStatus] = useState(initialHolding ? "已带入观察资料；请填写真实数量、成本价并确认风险预算" : "");
  const [reportState, setReportState] = useState<"idle" | "running" | "done" | "error">("idle");
  const [reportContent, setReportContent] = useState("");
  const [reportDoctrineCoverage, setReportDoctrineCoverage] = useState<DoctrineCoverage | undefined>();
  const [preparedReport, setPreparedReport] = useState<{ provider: "codex" | "api"; request: UnknownRecord; evidence: UnknownRecord; estimate: AiCostEstimate } | null>(null);
  const [csvText, setCsvText] = useState("代码,名称,行业,数量,成本价,当前价,当前价日期,买入逻辑\n");
  const [form, setForm] = useState(seededForm);
  const apiHoldings = dashboard?.holdings || [];
  const portfolio = dashboard?.portfolio;
  const diagnostics = dashboard?.portfolio_diagnostics;
  const diagnosticsBySymbol = new Map((diagnostics?.positions || []).map((position) => [position.symbol, position]));
  const displayHoldings = apiHoldings.length ? apiHoldings.map((item) => {
    const cost = Number(item.cost || 0);
    const diagnosis = diagnosticsBySymbol.get(item.symbol);
    const current = diagnosis?.price ?? item.current_price ?? null;
    const pnl = cost > 0 && current !== null ? ((current / cost - 1) * 100) : null;
    const weight = diagnosis?.weight == null ? null : diagnosis.weight * 100;
    return { code: item.symbol, name: item.name || item.symbol, industry: item.industry || diagnosis?.industry || "", weight, cost: cost.toFixed(2), price: current === null ? "缺价" : current.toFixed(2), priceAsOf: diagnosis?.price_as_of || "日期待补", pnl: pnl === null ? "—" : `${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}%`, health: diagnosis?.decision?.status || diagnosis?.risk_status || "待刷新", decision: diagnosis?.decision, tone: pnl === null ? "" : pnl >= 0 ? "up" : "down", flags: diagnosis?.flag_titles || [], asOf: diagnosis?.data_as_of || "", pendingFilingReviews: diagnosis?.pending_filing_reviews || 0, confirmedFilingRedFlags: diagnosis?.confirmed_filing_red_flags || 0, unsearchableFilings: diagnosis?.unsearchable_filings || 0 };
  }) : [];

  function closeHoldingEditor() {
    setAdding(false);
    setForm(blankForm);
    if (initialHolding) onInitialHoldingConsumed();
  }

  async function saveHolding() {
    setStatus("正在保存持仓…");
    try {
      await apiRequest<ApiHolding>("/api/holdings", { method: "POST", body: JSON.stringify({ ...form, quantity: Number(form.quantity), cost: Number(form.cost), current_price: form.current_price ? Number(form.current_price) : undefined, max_loss_pct: form.max_loss_pct ? Number(form.max_loss_pct) : undefined }) });
      setPreparedReport(null);
      closeHoldingEditor();
      setForm({ symbol: "", name: "", industry: "", quantity: "", cost: "", current_price: "", current_price_as_of: "", thesis: "", horizon: "1—3 年", max_loss_pct: "10" });
      setStatus("持仓已保存");
      await onRefresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "保存失败");
    }
  }

  function editHolding(symbol: string) {
    const item = apiHoldings.find((holding) => holding.symbol === symbol);
    if (!item) return;
    setForm({
      symbol: item.symbol,
      name: item.name || "",
      industry: item.industry || "",
      quantity: item.quantity === undefined ? "" : String(item.quantity),
      cost: item.cost === undefined ? "" : String(item.cost),
      current_price: item.current_price === null || item.current_price === undefined ? "" : String(item.current_price),
      current_price_as_of: item.current_price_as_of || "",
      thesis: item.thesis || "",
      horizon: item.horizon || "1—3 年",
      max_loss_pct: item.max_loss_pct === null || item.max_loss_pct === undefined ? "10" : String(item.max_loss_pct),
    });
    setAdding(true);
  }

  async function deleteHolding(symbol: string, name: string) {
    if (!window.confirm(`确定移除持仓 ${name || symbol}（${symbol}）吗？`)) return;
    setStatus("正在移除持仓…");
    try {
      await apiRequest<{ ok: boolean }>(`/api/holdings?symbol=${encodeURIComponent(symbol)}`, { method: "DELETE" });
      setPreparedReport(null);
      setStatus("持仓已移除");
      await onRefresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "移除失败");
    }
  }

  async function importCsv() {
    setStatus("正在导入持仓…");
    try {
      const result = await apiRequest<{ imported?: number; errors?: Array<{ line?: number; error?: string }> }>("/api/holdings/import", { method: "POST", body: JSON.stringify({ csv: csvText }) });
      setPreparedReport(null);
      setImporting(false);
      setStatus(result.errors?.length ? `已导入 ${result.imported || 0} 条；第 ${result.errors[0]?.line || "—"} 行未导入：${result.errors[0]?.error || "数据无效"}` : `已导入 ${result.imported || 0} 条持仓`);
      await onRefresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "导入失败");
    }
  }

  async function exportCsv() {
    setStatus("正在生成持仓 CSV…");
    try {
      const result = await apiRequest<{ file_name: string; csv: string; count?: number }>("/api/holdings/export");
      const blob = new Blob(["\ufeff", result.csv], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = result.file_name;
      link.click();
      URL.revokeObjectURL(url);
      setStatus(`已导出 ${result.count || 0} 条持仓`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "导出失败");
    }
  }

  async function refreshPortfolio() {
    setRefreshing(true);
    setStatus("正在更新持仓的真实财务快照…");
    try {
      const result = await apiRequest<{ refreshed?: string[]; errors?: Array<{ symbol?: string; error?: string }> }>("/api/portfolio/refresh", { method: "POST", body: JSON.stringify({}) });
      setPreparedReport(null);
      await onRefresh();
      setStatus(result.errors?.length ? `已更新 ${result.refreshed?.length || 0} 只；${result.errors[0]?.symbol || "部分持仓"}：${result.errors[0]?.error || "暂时无法更新"}` : `已更新 ${result.refreshed?.length || 0} 只持仓，并重新计算组合风险`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "组合数据更新失败");
    } finally {
      setRefreshing(false);
    }
  }

  async function runPortfolioReport() {
    const provider = engine === "Codex" ? "codex" : "api";
    const prepared = preparedReport?.provider === provider ? preparedReport : null;
    const request: UnknownRecord = {
      provider,
      symbol: "PORTFOLIO",
      title: "组合深度报告",
      prompt: "请基于组合证据包生成可执行但不自动交易的深度复核报告。先审计数据覆盖、价格日期和公告可检索性，再分析集中度、行业暴露、成本风险与各持仓财务变化。逐项输出继续观察、持有、等待条件或减仓复核，并列出原因、反方证据、下一验证条件和逻辑失效条件。confirmed 公告证据可作为事实，pending 只能称待复核线索。每个已启用投资体系必须单独形成结论，最后再列共识与冲突，禁止把流派平均化。",
    };
    setReportState("running");
    setStatus(prepared ? "正在生成组合深度报告…" : "正在整理组合证据并预估 Token…");
    try {
      if (!prepared) {
        const evidence = await apiRequest<UnknownRecord>("/api/portfolio/evidence", { method: "POST", body: JSON.stringify({ knowledge_query: "组合风险 仓位 成本 安全边际 财务质量 失效条件" }) });
        const quote = await apiRequest<{ estimate: AiCostEstimate; evidence: UnknownRecord }>("/api/ai/estimate", { method: "POST", body: JSON.stringify({ ...request, evidence }) });
        setPreparedReport({ provider, request, evidence: quote.evidence, estimate: quote.estimate });
        setReportState("idle");
        setStatus("组合证据已冻结，请确认 Token 与费用后再调用");
        return;
      }
      const result = await apiRequest<{ content?: string; doctrine_coverage?: DoctrineCoverage }>("/api/ai/analyze", {
        method: "POST",
        body: JSON.stringify({ ...prepared.request, evidence: prepared.evidence, estimate_fingerprint: prepared.estimate.fingerprint }),
      });
      setPreparedReport(null);
      setReportContent(result.content || "");
      setReportDoctrineCoverage(result.doctrine_coverage);
      setReportState("done");
      setStatus(result.doctrine_coverage?.status === "needs_review" ? "组合报告已保存，但流派分离格式需要人工复核" : "组合深度报告已完成并保存到研究档案");
      await onRefresh();
    } catch (error) {
      setReportState("error");
      setStatus(error instanceof Error ? error.message : "组合深度报告生成失败");
    }
  }
  return (
    <section className="workspace-page">
      <div className="page-intro"><div><span className="eyebrow">PORTFOLIO LAB</span><h1>我的持仓</h1><p>从财务质量、估值安全边际与组合暴露三个层面审视你的真实仓位。</p></div><div className="page-actions"><button className="secondary-action" disabled={refreshing || !apiHoldings.length} onClick={refreshPortfolio}>{refreshing ? "更新中…" : "↻ 刷新财务"}</button><button className="secondary-action" disabled={!apiHoldings.length} onClick={exportCsv}>⇧ 导出 CSV</button><button className="secondary-action" onClick={() => setImporting(true)}>⇩ 导入 CSV</button><button className="primary-action" onClick={() => setAdding(true)}>＋ 添加持仓</button></div></div>
      <div className="portfolio-summary">
        <article className="portfolio-value"><span>已计价持仓市值 · {portfolio?.priced_positions ?? "—"}/{portfolio?.positions ?? 0} 项</span><strong>{portfolio?.priced_positions ? `¥ ${Number(portfolio.value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}` : "暂无可用价格"}</strong><p><b>{portfolio?.priced_positions ? `${Number(portfolio.pnl || 0) >= 0 ? "+" : ""}${Number(portfolio.pnl || 0).toLocaleString("zh-CN")}` : "—"}</b> 已计价部分浮动盈亏</p><p>{portfolio?.valuation_policy || "缺少价格的持仓暂不计入市值与盈亏"}</p></article>
        <article><span>已录入持仓</span><strong>{Number(portfolio?.positions || 0)}</strong><p>{portfolio?.positions ? "市值覆盖取决于可用价格，请检查缺价项" : "添加持仓后计算组合暴露"}</p></article>
        <article><span>减仓复核</span><strong>{diagnostics?.decision_summary?.immediate_review ?? 0}</strong><p>只要求人工复核，不是卖出指令</p></article>
        <article><span>最大行业暴露</span><strong>{diagnostics?.industries?.[0] ? `${(diagnostics.industries[0].weight * 100).toFixed(1)}%` : "—"}</strong><p>{diagnostics?.industries?.[0]?.industry || "需要补充持仓行业"}</p></article>
      </div>
      <div className="portfolio-layout">
        <section className="panel holdings-panel">
          <div className="panel-heading"><div><span className="eyebrow">POSITIONS</span><h2>持仓明细</h2></div><div className="mini-filter">{["全部持仓", "需关注"].map((item) => <button key={item} className={selected === item ? "active" : ""} onClick={() => setSelected(item)}>{item}</button>)}</div></div>
          <div className="holdings-table" role="table" aria-label="持仓明细">
            <div className="holding-row header" role="row"><span>公司</span><span>仓位</span><span>成本 / 现价</span><span>收益</span><span>财务状态</span></div>
            {displayHoldings.filter((item) => selected === "全部持仓" || item.decision?.priority !== "green").map((item) => (
              <div className="holding-row" role="row" key={item.code}>
                <span><i className="holding-mark">{item.name.slice(0, 1)}</i><span><strong>{item.name}</strong><small>{item.code}</small>{Boolean(item.pendingFilingReviews || item.confirmedFilingRedFlags || item.unsearchableFilings) && <small>公告：{item.confirmedFilingRedFlags ? `已确认红旗 ${item.confirmedFilingRedFlags}` : ""}{item.pendingFilingReviews ? `${item.confirmedFilingRedFlags ? " · " : ""}待复核 ${item.pendingFilingReviews}` : ""}{item.unsearchableFilings ? `${item.confirmedFilingRedFlags || item.pendingFilingReviews ? " · " : ""}不可检索 ${item.unsearchableFilings}` : ""}</small>}</span></span>
                <span><b>{item.weight === null ? "—" : `${item.weight.toFixed(1)}%`}</b><i className="weight-bar"><em style={{ width: `${Math.min(100, item.weight ?? 0)}%` }} /></i></span>
                <span><small>成本 ¥ {item.cost}</small><b>现价 ¥ {item.price}</b><small>{item.priceAsOf}</small></span>
                <span className={item.tone}>{item.pnl}</span>
                <span><em className={`health ${item.decision?.priority === "green" ? "healthy" : item.decision?.priority === "red" ? "danger" : "attention"}`}>{item.health}</em><span className="holding-actions"><button onClick={() => onOpenCompany({ symbol: item.code, name: item.name, industry: item.industry })}>研究</button><button onClick={() => editHolding(item.code)}>编辑</button><button className="danger" onClick={() => deleteHolding(item.code, item.name)}>删除</button></span></span>
              </div>
            ))}
            {!displayHoldings.length && <div className="holding-empty"><strong>还没有录入持仓</strong><span>点击右上角“添加持仓”，输入代码、数量和成本价后，这里会开始计算组合层面的风险。</span></div>}
          </div>
        </section>
        <aside className="panel portfolio-risks">
          <div className="panel-heading"><div><span className="eyebrow">PORTFOLIO CHECK</span><h2>组合诊断</h2></div><span className={`risk-level ${diagnostics?.alerts?.some((alert) => alert.severity === "red") ? "danger" : ""}`}>{diagnostics?.alerts?.some((alert) => alert.severity === "red") ? "存在红旗" : diagnostics?.alerts?.length ? "需关注" : "待计算"}</span></div>
          <div className="allocation-ring"><div><strong>{apiHoldings.length ? `${apiHoldings.length}` : "—"}</strong><span>{apiHoldings.length ? "个持仓" : "暂无组合"}</span></div></div>
          <ul><li><span className="swatch teal" />有财务快照<b>{diagnostics?.coverage?.financial_positions ?? 0}/{diagnostics?.coverage?.positions ?? apiHoldings.length}</b></li><li><span className="swatch amber" />已分类行业<b>{diagnostics?.coverage?.industry_classified_positions ?? 0}/{diagnostics?.coverage?.positions ?? apiHoldings.length}</b></li><li><span className="swatch blue" />最大单股<b>{diagnostics?.concentration?.top_position_weight !== undefined && diagnostics?.concentration?.top_position_weight !== null ? `${(diagnostics.concentration.top_position_weight * 100).toFixed(1)}%` : "—"}</b></li><li><span className="swatch grey" />集中度 HHI<b>{diagnostics?.concentration?.hhi ?? "—"}</b></li></ul>
          {diagnostics?.industries?.length ? <div className="industry-exposure">{diagnostics.industries.slice(0, 3).map((industry) => <div key={industry.industry}><span>{industry.industry}</span><i><b style={{ width: `${Math.min(100, industry.weight * 100)}%` }} /></i><strong>{(industry.weight * 100).toFixed(1)}%</strong></div>)}</div> : null}
          <div className="portfolio-alert"><span>i</span><p><strong>{diagnostics?.alerts?.[0]?.title || (apiHoldings.length ? "先刷新财务数据，生成真实风险信号" : "先录入持仓，再生成组合诊断")}</strong><small>{diagnostics?.alerts?.[0]?.detail || (apiHoldings.length ? "只使用已缓存的真实财务快照；缺失数据会显示待刷新。" : "软件不会用演示持仓冒充你的真实仓位。")}</small></p></div>
          <button className="full-report" disabled={!apiHoldings.length || reportState === "running" || Boolean(preparedReport && (!preparedReport.estimate.within_budget || !preparedReport.estimate.configuration_ready))} onClick={runPortfolioReport}>{reportState === "running" ? (preparedReport ? "分析中…" : "正在预估…") : preparedReport ? "确认调用并生成 ✦" : "生成组合深度报告 ✦"}</button>
        </aside>
      </div>
      {preparedReport && <section className={`ai-cost-preview ${preparedReport.estimate.within_budget && preparedReport.estimate.configuration_ready ? "ready" : "blocked"}`}><div><span className="eyebrow">PORTFOLIO AI PREFLIGHT</span><h2>组合报告调用前确认</h2><p>{preparedReport.estimate.model} · 已冻结 {preparedReport.evidence.positions && Array.isArray(preparedReport.evidence.positions) ? preparedReport.evidence.positions.length : apiHoldings.length} 个持仓证据 · {(preparedReport.estimate.evidence_bytes / 1024).toFixed(1)} KB</p></div><div className="ai-cost-metrics"><span>输入约 <b>{preparedReport.estimate.estimated_input_tokens.toLocaleString("zh-CN")}</b></span><span>输出预留 <b>{preparedReport.estimate.estimated_output_tokens.toLocaleString("zh-CN")}</b></span><span>本次合计 <b>{preparedReport.estimate.estimated_total_tokens.toLocaleString("zh-CN")}</b></span><span>调用后月余额 <b>{preparedReport.estimate.monthly_tokens_after.toLocaleString("zh-CN")}</b></span>{preparedReport.estimate.estimated_cost != null && <span>估算费用 <b>{preparedReport.estimate.currency} {preparedReport.estimate.estimated_cost.toFixed(4)}</b></span>}</div><p className="ai-cost-notice">{!preparedReport.estimate.within_budget ? "本次调用超过月度 Token 预算，已禁止执行。" : !preparedReport.estimate.configuration_ready ? "兼容 API 配置不完整，请先在设置中补齐。" : preparedReport.estimate.cost_notice}</p><div><button onClick={() => { setPreparedReport(null); setStatus("已取消本次组合分析"); }}>取消</button><button className="primary-action" disabled={!preparedReport.estimate.within_budget || !preparedReport.estimate.configuration_ready} onClick={runPortfolioReport}>确认并开始分析</button></div></section>}
      {reportContent && <section className="analysis-output portfolio-report-output"><div className="analysis-output-head"><div><span className="eyebrow">PORTFOLIO RESEARCH MEMO</span><h2>{engine} 组合深度报告</h2></div><span>{reportDoctrineCoverage?.status === "needs_review" ? "流派格式待复核" : "已保存 · 可在研究档案回看"}</span></div><MarkdownReport content={reportContent} /></section>}
      {Boolean(diagnostics?.positions?.length) && <section className="decision-loop"><div className="decision-loop-head"><div><span className="eyebrow">POSITION DECISION LOOP</span><h2>持仓行动清单</h2></div><p>只给条件化动作，不自动交易；“减仓复核”表示需要重新评估暴露，不是卖出指令。</p></div><div className="decision-cards">{diagnostics?.positions?.map((position) => <article key={position.symbol} className={position.decision?.priority || "yellow"}><div><span>{position.symbol}</span><b>{position.decision?.status || "待计算"}</b></div><h3>{position.name || position.symbol}</h3><ul>{position.decision?.reasons?.slice(0, 3).map((reason) => <li key={reason}>{reason}</li>)}</ul><footer><strong>下一步</strong><p>{position.decision?.next_actions?.slice(0, 2).join("；") || "刷新数据并补充买入逻辑"}</p>{Boolean(position.decision?.review_conditions?.length) && <small>解除/升级条件：{position.decision?.review_conditions?.slice(0, 2).join("；")}</small>}<button onClick={() => onOpenCompany(position)}>打开公司研究与证据 →</button></footer></article>)}</div></section>}
      {status && <p className="inline-status">{status}</p>}
      {importing && <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="holding-import-title"><button className="modal-backdrop" onClick={() => setImporting(false)} aria-label="关闭"/><div className="upload-modal holding-modal"><button className="modal-close" onClick={() => setImporting(false)} aria-label="关闭">×</button><span className="upload-symbol">⇩</span><h2 id="holding-import-title">导入持仓 CSV</h2><p>可粘贴 CSV 内容；支持中文列名。相同股票代码会更新原持仓。</p><textarea className="csv-editor" value={csvText} onChange={(event) => setCsvText(event.target.value)} aria-label="CSV 内容"/><small className="csv-help">必需列：代码（或 symbol）、数量、成本价；可选列：名称、行业、当前价、当前价日期、买入逻辑、持有周期、最大承受亏损。手工当前价建议同时填写 YYYY-MM-DD 日期。</small><div className="modal-actions"><button onClick={() => setImporting(false)}>取消</button><button className="primary" onClick={importCsv}>开始导入</button></div></div></div>}
      {adding && <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="holding-title"><button className="modal-backdrop" onClick={closeHoldingEditor} aria-label="关闭"/><div className="upload-modal holding-modal"><button className="modal-close" onClick={closeHoldingEditor} aria-label="关闭">×</button><span className="upload-symbol">仓</span><h2 id="holding-title">添加或更新持仓</h2><p>相同代码会更新原持仓。手工当前价必须同时记录日期，否则只能作为待核值。</p><div className="form-grid"><label><span>股票代码 *</span><input value={form.symbol} onChange={(event) => setForm({ ...form, symbol: event.target.value })} placeholder="601088"/></label><label><span>公司名称</span><input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="中国神华"/></label><label><span>行业</span><input value={form.industry} onChange={(event) => setForm({ ...form, industry: event.target.value })} placeholder="例如：煤炭"/></label><label><span>数量</span><input type="number" value={form.quantity} onChange={(event) => setForm({ ...form, quantity: event.target.value })}/></label><label><span>成本价</span><input type="number" value={form.cost} onChange={(event) => setForm({ ...form, cost: event.target.value })}/></label><label><span>当前价</span><input type="number" value={form.current_price} onChange={(event) => setForm({ ...form, current_price: event.target.value })}/></label><label><span>当前价日期</span><input type="date" value={form.current_price_as_of} onChange={(event) => setForm({ ...form, current_price_as_of: event.target.value })}/></label><label><span>最大承受亏损 %</span><input type="number" value={form.max_loss_pct} onChange={(event) => setForm({ ...form, max_loss_pct: event.target.value })}/></label><label className="wide"><span>持有周期</span><input value={form.horizon} onChange={(event) => setForm({ ...form, horizon: event.target.value })}/></label><label className="wide"><span>买入逻辑</span><textarea value={form.thesis} onChange={(event) => setForm({ ...form, thesis: event.target.value })} placeholder="记录当初为什么买入，方便日后验证逻辑…"/></label></div><div className="modal-actions"><button onClick={closeHoldingEditor}>取消</button><button className="primary" disabled={!form.symbol.trim()} onClick={saveHolding}>保存持仓</button></div></div></div>}
    </section>
  );
}

function SettingsModal({ onClose, settings, engine, onSaved }: { onClose: () => void; settings?: DashboardData["settings"]; engine: string; onSaved: (settings: DashboardData["settings"]) => void }) {
  const [form, setForm] = useState({ ai_provider: engine === "Codex" ? "codex" : "api", api_base_url: settings?.api_base_url || "", api_model: settings?.api_model || "", monthly_token_limit: String(settings?.monthly_token_limit || 1000000), api_input_price_per_million: String(settings?.api_input_price_per_million || 0), api_output_price_per_million: String(settings?.api_output_price_per_million || 0), api_price_currency: settings?.api_price_currency || "CNY" });
  const [status, setStatus] = useState("");
  const [backups, setBackups] = useState<BackupInfo[]>([]);
  const [health, setHealth] = useState<DataHealth | null>(null);

  useEffect(() => {
    apiRequest<BackupInfo[]>("/api/data/backups").then(setBackups).catch(() => undefined);
    apiRequest<DataHealth>("/api/data/health").then(setHealth).catch(() => undefined);
  }, []);

  async function save() {
    setStatus("正在保存…");
    try {
      const result = await apiRequest<NonNullable<DashboardData["settings"]>>("/api/settings", { method: "POST", body: JSON.stringify({ ...form, monthly_token_limit: Number(form.monthly_token_limit), api_input_price_per_million: Number(form.api_input_price_per_million), api_output_price_per_million: Number(form.api_output_price_per_million) }) });
      onSaved(result);
      setStatus("设置已保存");
      setTimeout(onClose, 500);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "保存失败");
    }
  }
  async function backupNow() {
    setStatus("正在创建数据库备份…");
    try {
      const result = await apiRequest<BackupInfo & { notice?: string }>("/api/data/backup", { method: "POST", body: "{}" });
      setBackups((items) => [{ file_name: result.file_name, path: result.path, size_bytes: result.size_bytes, modified_at: result.created_at, sha256: result.sha256, integrity: result.integrity, recoverable: result.recoverable }, ...items]);
      setStatus(`备份已完成并通过 SQLite 校验：${result.file_name}${result.sha256 ? ` · SHA-256 ${result.sha256.slice(0, 12)}…` : ""}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "备份失败");
    }
  }
  async function restoreBackup(backup: BackupInfo) {
    if (!backup.path || !window.confirm(`确定恢复 ${backup.file_name} 吗？恢复前系统会自动再备份当前数据库。`)) return;
    setStatus("正在校验并恢复数据库…");
    try {
      await apiRequest("/api/data/restore", { method: "POST", body: JSON.stringify({ source: backup.path, confirm: true }) });
      setStatus("恢复完成，页面即将刷新");
      setTimeout(() => window.location.reload(), 900);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "恢复失败");
    }
  }
  return <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="settings-title">
    <button className="modal-backdrop" onClick={onClose} aria-label="关闭"/>
    <div className="upload-modal settings-modal">
      <button className="modal-close" onClick={onClose} aria-label="关闭">×</button><span className="upload-symbol">⚙</span>
      <h2 id="settings-title">分析引擎设置</h2><p>Codex 使用本机登录状态；兼容 API 的密钥仅从环境变量读取，不写入数据库。</p>
      <div className="form-grid">
        <label className="wide"><span>默认引擎</span><select value={form.ai_provider} onChange={(event) => setForm({ ...form, ai_provider: event.target.value })}><option value="codex">Codex</option><option value="api">OpenAI 兼容 API</option></select></label>
        <label className="wide"><span>API Base URL</span><input value={form.api_base_url} onChange={(event) => setForm({ ...form, api_base_url: event.target.value })} placeholder="https://api.example.com/v1"/></label>
        <label><span>模型名称</span><input value={form.api_model} onChange={(event) => setForm({ ...form, api_model: event.target.value })} placeholder="模型 ID"/></label>
        <label><span>月度 Token 上限</span><input type="number" value={form.monthly_token_limit} onChange={(event) => setForm({ ...form, monthly_token_limit: event.target.value })}/><small>本月已记账 {Number(settings?.monthly_tokens_used || 0).toLocaleString("zh-CN")} · 剩余 {Number(settings?.monthly_tokens_remaining || settings?.monthly_token_limit || 0).toLocaleString("zh-CN")}</small></label>
        <label><span>API 输入单价 / 百万 Token</span><input type="number" min="0" step="0.01" value={form.api_input_price_per_million} onChange={(event) => setForm({ ...form, api_input_price_per_million: event.target.value })}/></label>
        <label><span>API 输出单价 / 百万 Token</span><input type="number" min="0" step="0.01" value={form.api_output_price_per_million} onChange={(event) => setForm({ ...form, api_output_price_per_million: event.target.value })}/></label>
        <label><span>API 价格币种</span><select value={form.api_price_currency} onChange={(event) => setForm({ ...form, api_price_currency: event.target.value as "CNY" | "USD" })}><option value="CNY">人民币 CNY</option><option value="USD">美元 USD</option></select><small>单价填 0 时只预估 Token，不臆测服务商费用。</small></label>
      </div>
      <div className="credential-note"><span>{settings?.api_key_configured ? "✓" : "i"}</span><p><strong>{settings?.api_key_configured ? "API Key 已配置" : "尚未检测到 API Key"}</strong><small>环境变量名：INVESTMENT_BRAIN_API_KEY</small></p></div>
      <section className="health-box"><div><strong>数据健康</strong><small>这里显示真正可参与分析的数据，不把停用资料、历史截止日快照或未提取公告算作当前可用。</small></div><div className="health-grid"><span>当前财务公司 <b className={health?.financial_snapshots?.status}>{health?.financial_snapshots?.fresh_companies ?? "—"}/{health?.financial_snapshots?.companies ?? "—"}{Number(health?.financial_snapshots?.stale_companies || 0) > 0 ? ` · 过期 ${health?.financial_snapshots?.stale_companies}` : ""}{Number(health?.financial_snapshots?.unknown_companies || 0) > 0 ? ` · 未知 ${health?.financial_snapshots?.unknown_companies}` : ""}</b></span><span>历史快照（已隔离） <b>{health?.financial_snapshots?.historical_count ?? "—"}</b></span><span>可检索财报 <b className={health?.filings?.coverage_status}>{health?.filings?.searchable ?? "—"}/{health?.filings?.count ?? "—"}{Number(health?.filings?.partially_searchable || 0) > 0 ? ` · OCR 部分 ${health?.filings?.partially_searchable}` : ""}</b></span><span>启用知识片段 <b>{health?.knowledge?.eligible_chunks ?? "—"}/{health?.knowledge?.chunks ?? "—"}{Number(health?.knowledge?.disabled_chunks || 0) > 0 ? ` · 停用 ${health?.knowledge?.disabled_chunks}` : ""}</b></span><span>完整性验证报告 <b className={Number(health?.reports?.corrupt || 0) > 0 ? "corrupt" : ""}>{health?.reports?.replayable ?? "—"}/{health?.reports?.count ?? "—"}{Number(health?.reports?.partial || 0) > 0 ? ` · 不完整 ${health?.reports?.partial}` : ""}{Number(health?.reports?.corrupt || 0) > 0 ? ` · 校验失败 ${health?.reports?.corrupt}` : ""}</b></span></div></section>
      <section className="backup-box"><div><strong>本地数据库备份</strong><small>使用微秒级唯一文件名，禁止覆盖旧备份；创建后执行 SQLite 完整性检查并计算 SHA-256。只复制数据库，不修改你的 Word/PDF 原文件。</small></div><button onClick={backupNow}>立即备份</button>{backups.length > 0 && <ul>{backups.slice(0, 3).map((backup) => <li key={backup.file_name}><span>{backup.file_name}</span><small>{backup.sha256 ? `校验 ${backup.sha256.slice(0, 12)}…` : backup.modified_at ? String(backup.modified_at).slice(0, 16) : "历史备份"}</small><button onClick={() => restoreBackup(backup)}>恢复</button></li>)}</ul>}</section>
      {status && <p className="inline-status">{status}</p>}<div className="modal-actions"><button onClick={onClose}>取消</button><button className="primary" onClick={save}>保存设置</button></div>
    </div>
  </div>;
}

export default function Home() {
  const [activeView, setActiveView] = useState<View>("research");
  const [selectedCompany, setSelectedCompany] = useState<ResearchCompany>(companies[0]);
  const [engine, setEngine] = useState("Codex");
  const [evidence, setEvidence] = useState<EvidenceSelection | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [backendOnline, setBackendOnline] = useState(false);
  const [holdingSeed, setHoldingSeed] = useState<ApiHolding | null>(null);
  const [compareSeed, setCompareSeed] = useState<string | null>(null);
  const [onboardingDismissed, setOnboardingDismissed] = useState(false);
  const showGettingStarted = Boolean(dashboard) && !onboardingDismissed && !dashboard?.holdings?.length && !dashboard?.watchlist?.length && !dashboard?.data_health?.financial_snapshots?.count;

  async function refreshDashboard() {
    try {
      const scope = activeView === "knowledge" ? "knowledge" : activeView === "filings" ? "filings" : "core";
      const result = await apiRequest<DashboardData>(`/api/dashboard?scope=${scope}`);
      setDashboard((current) => ({ ...(current || {}), ...result }));
      setBackendOnline(true);
      if (result.settings?.ai_provider) setEngine(result.settings.ai_provider === "codex" ? "Codex" : "OpenAI 兼容 API");
    } catch {
      setBackendOnline(false);
    }
  }

  async function toggleWatch(company: ResearchCompany, watchlisted: boolean) {
    if (watchlisted) {
      await apiRequest<{ ok: boolean }>(`/api/watchlist?symbol=${encodeURIComponent(company.code)}`, { method: "DELETE" });
    } else {
      await apiRequest<ApiWatch>("/api/watchlist", { method: "POST", body: JSON.stringify({ symbol: company.code, name: company.name, note: `待跟踪：${company.industry} 的财务质量、估值与下一次财报。` }) });
    }
    await refreshDashboard();
  }

  useEffect(() => {
    let active = true;
    apiRequest<DashboardData>("/api/dashboard?scope=core")
      .then((result) => {
        if (!active) return;
        setDashboard(result);
        setBackendOnline(true);
        if (result.settings?.ai_provider) setEngine(result.settings.ai_provider === "codex" ? "Codex" : "OpenAI 兼容 API");
      })
      .catch(() => { if (active) setBackendOnline(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (activeView !== "knowledge" && activeView !== "filings") return;
    let active = true;
    apiRequest<DashboardData>(`/api/dashboard?scope=${activeView}`)
      .then((result) => {
        if (!active) return;
        setDashboard((current) => ({ ...(current || {}), ...result }));
        setBackendOnline(true);
      })
      .catch(() => { if (active) setBackendOnline(false); });
    return () => { active = false; };
  }, [activeView]);

  return (
    <div className="app-shell">
      <Sidebar activeView={activeView} onChange={setActiveView} open={sidebarOpen} onClose={() => setSidebarOpen(false)} watchlistCount={dashboard?.watchlist?.length || 0} reminderCount={dashboard?.review_reminders?.length || 0} />
      <main className="main-shell">
        <Header engine={engine} setEngine={setEngine} onMenu={() => setSidebarOpen(true)} onSettings={() => setSettingsOpen(true)} backendOnline={backendOnline} dataHealth={dashboard?.data_health} onSelectCompany={(company) => { setSelectedCompany(company); setActiveView("research"); }} />
        <div className="content-wrap">
          {showGettingStarted && <GettingStarted onDismiss={() => setOnboardingDismissed(true)} onOpenSettings={() => { setOnboardingDismissed(true); setSettingsOpen(true); }} onNavigate={(view) => { setOnboardingDismissed(true); setActiveView(view); }} />}
          {activeView === "research" && <ResearchView company={selectedCompany} onEvidence={setEvidence} engine={engine} onDashboardRefresh={refreshDashboard} onOpenFilings={() => setActiveView("filings")} watchlisted={Boolean(dashboard?.watchlist?.some((item) => item.symbol === selectedCompany.code))} onToggleWatch={() => toggleWatch(selectedCompany, Boolean(dashboard?.watchlist?.some((item) => item.symbol === selectedCompany.code)))} />}
          {activeView === "filings" && <FilingsView dashboard={dashboard} onRefresh={refreshDashboard} />}
          {activeView === "compare" && <CompareView dashboard={dashboard} onRefresh={refreshDashboard} initialSymbol={compareSeed} onInitialSymbolConsumed={() => setCompareSeed(null)} onOpenCompany={(company) => { const base = researchCompany(company.symbol, company.name); setSelectedCompany({ ...base, industry: company.industry || base.industry }); setActiveView("research"); }} />}
          {activeView === "knowledge" && <KnowledgeView dashboard={dashboard} onRefresh={refreshDashboard} />}
          {activeView === "holdings" && <HoldingsView dashboard={dashboard} onRefresh={refreshDashboard} engine={engine} initialHolding={holdingSeed} onInitialHoldingConsumed={() => setHoldingSeed(null)} onOpenCompany={(holding) => { const base = researchCompany(holding.symbol, holding.name); setSelectedCompany({ ...base, industry: holding.industry || base.industry }); setActiveView("research"); }} />}
          {activeView === "reports" && <ReportsView dashboard={dashboard} onRefresh={refreshDashboard} />}
          {activeView === "watchlist" && <WatchlistView dashboard={dashboard} onRefresh={refreshDashboard} onOpenCompany={(symbol) => { const item = dashboard?.watchlist?.find((watch) => watch.symbol === symbol); setSelectedCompany(researchCompany(symbol, item?.name)); setActiveView("research"); }} onOpenCompare={(symbol) => { setCompareSeed(symbol); setActiveView("compare"); }} onPrepareHolding={(holding) => { setHoldingSeed(holding); setActiveView("holdings"); }} />}
        </div>
      </main>
      <EvidenceDrawer evidence={evidence} onClose={() => setEvidence(null)} />
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} settings={dashboard?.settings} engine={engine} onSaved={(settings) => { setDashboard((current) => ({ ...(current || {}), settings })); setEngine(settings?.ai_provider === "codex" ? "Codex" : "OpenAI 兼容 API"); }} />}
    </div>
  );
}
