import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the investment research workbench", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>散修宗投资大脑/);
  assert.match(html, /财务研究工作台/);
  assert.match(html, /盈利质量/);
  assert.match(html, /研究截止日/);
  assert.match(html, /Codex/);
  assert.doesNotMatch(html, /codex-preview/);
});

test("removes starter metadata and describes the real product", async () => {
  const [css, page, layout, packageJson] = await Promise.all([
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);
  assert.match(page, /财务/);
  assert.match(page, /连续 OCR 40 页/);
  assert.match(page, /之前完成的批次仍已保存/);
  assert.match(layout, /散修宗投资大脑/);
  assert.doesNotMatch(layout, /Starter Project|codex-preview|_sites-preview/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
  assert.match(css, /--ink/);
});

test("keeps live evidence company-scoped and financial chart gaps explicit", async () => {
  const [page, css, bankEngine] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../backend/bank_engine.py", import.meta.url), "utf8"),
  ]);
  assert.match(page, /缺失项保持缺失，不会借用演示公司数据/);
  assert.match(page, /缺失值不画柱、不补零/);
  assert.doesNotMatch(page, /annualMetric\(period, live\.primary\) \?\? 0/);
  assert.doesNotMatch(page, /annualMetric\(period, live\.secondary\) \?\? 0/);
  assert.match(page, /onClick=\{\(\) => setExpanded/);
  assert.match(page, /\/api\/portfolio\/evidence/);
  assert.match(page, /onClick=\{runPortfolioReport\}/);
  assert.match(page, /组合报告调用前确认/);
  assert.match(page, /portfolio_position_changes/);
  assert.match(page, /持仓与条件化决策变化/);
  assert.match(page, /打开公司研究与证据/);
  assert.match(page, /onOpenCompany=\{\(holding\)/);
  assert.match(page, /转入持仓/);
  assert.match(page, /进入对比/);
  assert.match(page, /请再添加 1–3 家可比公司/);
  assert.match(page, /FIRST RESEARCH LOOP/);
  assert.match(page, /从一家公司，开始建立自己的研究底片/);
  assert.match(page, /不把关注当成买入/);
  assert.match(page, /旧 \.doc/);
  assert.match(page, /legacy_word_documents/);
  assert.match(page, /\/api\/reports\?limit=50&include_content=0/);
  assert.match(page, /\/api\/reports\/content\?id=/);
  assert.match(page, /正在按需读取报告正文/);
  assert.match(page, /new Blob\(\["\\ufeff", result\.csv\]/);
  assert.match(page, /已带入观察资料；请填写真实数量、成本价并确认风险预算/);
  assert.match(page, /onPrepareHolding=\{\(holding\)/);
  assert.match(page, /onOpenCompare=\{\(symbol\)/);
  assert.match(page, /\/api\/libraries\/update/);
  assert.match(page, /setLibraryFilter/);
  assert.match(page, /规则审核进度/);
  assert.match(page, /修订会产生新版本/);
  assert.match(page, /保存新版本并重新审核/);
  assert.match(page, /retry_count: 2/);
  assert.match(page, /自动重试/);
  assert.match(page, /\/api\/reports\/snapshot\?id=/);
  assert.match(page, /\/api\/reports\/integrity\?id=/);
  assert.match(page, /已验证可复现/);
  assert.match(page, /快照内容与归档哈希不一致/);
  assert.match(page, /微秒级唯一文件名/);
  assert.match(page, /SHA-256/);
  assert.match(page, /完整性验证报告/);
  assert.match(page, /校验失败/);
  assert.doesNotMatch(page, /78 \+ index \* 5/);
  assert.match(page, /CompanyFilingPanel/);
  assert.match(page, /逾期贷款率/);
  assert.match(page, /关注类贷款率/);
  assert.match(bankEngine, /bank_overdue_loan_rising/);
  assert.match(page, /确认事实、待复核线索和不可检索缺口严格分层/);
  assert.match(page, /\/api\/filings\/artifact\?filing_id=/);
  assert.doesNotMatch(page, /将在公告适配器接入后逐项挂接原文/);
  assert.match(css, /\.bar\.missing/);
  assert.match(css, /\.company-filing-panel/);
  assert.match(css, /\.snapshot-integrity/);
  assert.match(css, /\.health-grid b\.corrupt/);
  assert.match(css, /\.decision-cards footer > button/);
  assert.match(css, /\.watch-card-actions/);
  assert.match(css, /\.getting-started/);
});
