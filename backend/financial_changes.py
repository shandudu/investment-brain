"""Deterministic, source-linked financial change detection.

The detector consumes the finance engine's calculated output.  It never fills
missing values and never asks an LLM to perform arithmetic.  Every emitted
signal carries the exact metric payloads (including upstream filing inputs).
"""

from __future__ import annotations

import math
from typing import Any, Mapping


def _record(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(metric: Any) -> float | None:
    item = _record(metric)
    value = item.get("value")
    if item.get("status") != "ok" or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _metric(period: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    return _record(_record(period.get("metrics")).get(name))


def _change(current: float, prior: float) -> dict[str, float | None]:
    return {
        "absolute": current - prior,
        "relative": None if prior == 0 else (current / abs(prior)) - 1,
    }


def _period_context(period: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "end_date": period.get("end_date"),
        "period_type": period.get("period_type"),
        "duration_months": period.get("duration_months"),
        "basis": period.get("basis"),
        "cumulative": period.get("cumulative"),
        "audited": period.get("audited"),
        "report_scope": period.get("report_scope"),
        "restated": period.get("restated"),
        "construction": period.get("construction"),
    }


def _report_scope(period: Mapping[str, Any]) -> str:
    return str(period.get("report_scope") or "").strip().lower()


def _same_report_scope(current: Mapping[str, Any], prior: Mapping[str, Any]) -> bool:
    """Reject known consolidated/parent or mixed-scope comparisons.

    Legacy inputs without scope remain comparable for backwards compatibility,
    but the missing scope is disclosed in ``unsupported_checks``.
    """

    current_scope, prior_scope = _report_scope(current), _report_scope(prior)
    if "mixed" in {current_scope, prior_scope}:
        return False
    return not current_scope or not prior_scope or current_scope == prior_scope


def detect_financial_changes(analysis: Mapping[str, Any]) -> dict[str, Any]:
    periods = _record(analysis.get("periods"))
    annual = [item for item in periods.get("annual", []) if isinstance(item, Mapping)] if isinstance(periods.get("annual"), list) else []
    interim = [item for item in periods.get("interim", []) if isinstance(item, Mapping)] if isinstance(periods.get("interim"), list) else []
    ttm = _record(periods.get("ttm"))
    ttm_comparable = _record(periods.get("ttm_comparable"))
    timeline = list(annual)
    has_distinct_ttm = bool(ttm and (not timeline or ttm.get("end_date") != timeline[-1].get("end_date")))
    if has_distinct_ttm:
        timeline.append(ttm)

    coverage_names = {
        "profit_cash": ("net_income", "operating_cash_flow", "cash_conversion"),
        "working_capital": ("revenue", "accounts_receivable_growth", "inventory_growth", "receivable_days", "inventory_days"),
        "leverage": ("net_debt", "debt_to_equity", "interest_coverage", "debt_due_within_one_year", "gross_cash_short_debt_coverage", "usable_cash_short_debt_coverage"),
        "dividend": ("free_cash_flow_dividend_cover",),
        "capex": ("capital_expenditure", "free_cash_flow"),
        "profitability": ("gross_margin", "operating_margin", "net_margin", "return_on_invested_capital"),
    }
    latest = interim[-1] if interim and (not timeline or str(interim[-1].get("end_date") or "") >= str(timeline[-1].get("end_date") or "")) else (timeline[-1] if timeline else {})
    coverage: list[dict[str, Any]] = []
    missing: list[str] = []
    for group, names in coverage_names.items():
        available = [name for name in names if _number(_metric(latest, name)) is not None]
        unavailable = [name for name in names if name not in available]
        missing.extend(unavailable)
        coverage.append({"group": group, "available": available, "missing": unavailable})

    signals: list[dict[str, Any]] = []

    def add(
        signal_id: str,
        severity: str,
        title: str,
        detail: str,
        metric_names: tuple[str, ...],
        current_period: Mapping[str, Any],
        prior_period: Mapping[str, Any] | None = None,
        *,
        values: Mapping[str, Any] | None = None,
        review_condition: str,
    ) -> None:
        current_metrics = {name: dict(_metric(current_period, name)) for name in metric_names}
        prior_metrics = {name: dict(_metric(prior_period or {}, name)) for name in metric_names} if prior_period else {}
        signals.append(
            {
                "id": signal_id,
                "severity": severity,
                "title": title,
                "detail": detail,
                "period": current_period.get("end_date"),
                "comparison_period": prior_period.get("end_date") if prior_period else None,
                "values": dict(values or {}),
                "metrics": {"current": current_metrics, "prior": prior_metrics},
                "current_context": _period_context(current_period),
                "comparison_context": _period_context(prior_period or {}) if prior_period else None,
                "review_condition": review_condition,
            }
        )

    current = timeline[-1] if timeline else {}
    prior = (
        ttm_comparable if ttm_comparable else {}
    ) if has_distinct_ttm else (annual[-2] if len(annual) >= 2 else {})
    comparison_basis = "FY vs prior FY"
    scope_comparison_issue = False
    if interim:
        candidate = interim[-1]
        comparable_candidates = [
            item for item in reversed(interim[:-1])
            if isinstance(candidate.get("fiscal_year"), int)
            and item.get("fiscal_year") == candidate.get("fiscal_year") - 1
            and item.get("duration_months") == candidate.get("duration_months")
            and item.get("cumulative") == candidate.get("cumulative")
        ]
        comparable = next((item for item in comparable_candidates if _same_report_scope(candidate, item)), None)
        current = candidate
        if comparable:
            prior = comparable
            comparison_basis = "latest cumulative/discrete interim vs same-duration prior-year interim"
        else:
            prior = {}
            scope_comparison_issue = bool(comparable_candidates)
            comparison_basis = "latest interim without a same-scope prior-year comparator" if scope_comparison_issue else "latest interim without a same-duration prior-year comparator"
    elif has_distinct_ttm:
        if ttm_comparable and not _same_report_scope(current, ttm_comparable):
            prior = {}
            scope_comparison_issue = True
            comparison_basis = "constructed TTM without a same-scope prior TTM"
        else:
            comparison_basis = "constructed TTM vs prior-year comparable TTM" if ttm_comparable else "constructed TTM without a comparable prior TTM"
    elif current and prior and not _same_report_scope(current, prior):
        prior = {}
        scope_comparison_issue = True
        comparison_basis = "FY without a same-scope prior FY comparator"
    if current:
        ni, p_ni = _number(_metric(current, "net_income")), _number(_metric(prior, "net_income"))
        ocf, p_ocf = _number(_metric(current, "operating_cash_flow")), _number(_metric(prior, "operating_cash_flow"))
        cash_conversion = _number(_metric(current, "cash_conversion"))
        prior_conversion = _number(_metric(prior, "cash_conversion"))
        if ni is not None and p_ni is not None and ocf is not None and p_ocf is not None and ni > p_ni and ocf < p_ocf:
            add("PROFIT_UP_CASH_DOWN", "red" if ocf < 0 else "yellow", "利润增长但经营现金流下降", "利润和现金流方向背离，需核对营运资本、非经常项目及收入确认。", ("net_income", "operating_cash_flow"), current, prior, values={"net_income_change": _change(ni, p_ni), "operating_cash_flow_change": _change(ocf, p_ocf)}, review_condition="若后续一期经营现金流恢复且应收、存货未恶化，可解除；否则升级复核。")
        if cash_conversion is not None and cash_conversion < 0.8:
            add("LOW_CASH_CONVERSION_CHANGE", "red" if cash_conversion < 0 else "yellow", "盈利现金含量偏低", "经营现金流不足净利润的 80%，盈利质量需要解释。", ("cash_conversion", "net_income", "operating_cash_flow"), current, prior, values={"cash_conversion": cash_conversion, "prior_cash_conversion": prior_conversion}, review_condition="核对应收、存货、合同资产、预付款和一次性现金收支；连续两期低于 0.8 应重点跟踪。")
        if cash_conversion is not None and prior_conversion is not None and cash_conversion < prior_conversion - 0.25:
            add("CASH_CONVERSION_DETERIORATION", "yellow", "现金转化率明显下降", "经营现金流/净利润较上个可比 12 个月下降超过 0.25。", ("cash_conversion",), current, prior, values={"change": _change(cash_conversion, prior_conversion)}, review_condition="若下降由税费、季节性或可验证的扩产备货造成，应记录解释并观察下一期回转。")

        revenue_growth = _number(_metric(current, "revenue_growth"))
        receivables_growth = _number(_metric(current, "accounts_receivable_growth"))
        inventory_growth = _number(_metric(current, "inventory_growth"))
        if revenue_growth is not None and receivables_growth is not None and receivables_growth > revenue_growth + 0.15:
            add("RECEIVABLES_OUTRUN_REVENUE_CHANGE", "yellow", "应收增速明显快于收入", "应收账款增速超过收入增速 15 个百分点以上，需核对回款与收入确认。", ("accounts_receivable_growth", "revenue_growth", "accounts_receivable"), current, prior, values={"accounts_receivable_growth": receivables_growth, "revenue_growth": revenue_growth, "gap": receivables_growth - revenue_growth}, review_condition="核对应收账龄、坏账准备、期后回款、前五大客户与票据/合同资产变化；后续增速差收敛且现金回款改善后再解除。")
        if revenue_growth is not None and inventory_growth is not None and inventory_growth > revenue_growth + 0.15:
            add("INVENTORY_OUTRUN_REVENUE_CHANGE", "yellow", "存货增速明显快于收入", "存货增速超过收入增速 15 个百分点以上，需核对备货、滞销和减值。", ("inventory_growth", "revenue_growth", "inventory"), current, prior, values={"inventory_growth": inventory_growth, "revenue_growth": revenue_growth, "gap": inventory_growth - revenue_growth}, review_condition="拆分原材料、在产品和产成品，核对库龄、订单覆盖、跌价准备与期后销售；不能只按管理层的扩产解释解除。")
        for metric_name, signal_id, title in (
            ("receivable_days", "RECEIVABLE_DAYS_PRESSURE", "应收周转天数上升"),
            ("inventory_days", "INVENTORY_DAYS_PRESSURE", "存货周转天数上升"),
        ):
            value, prior_value = _number(_metric(current, metric_name)), _number(_metric(prior, metric_name))
            if value is not None and prior_value is not None and value - prior_value >= max(15.0, abs(prior_value) * 0.2):
                add(signal_id, "yellow", title, f"{metric_name} 较上期增加至少 15 天且增幅超过 20%，需核对回款、渠道库存与减值。", (metric_name, "revenue_growth"), current, prior, values={"days_change": _change(value, prior_value), "revenue_growth": revenue_growth}, review_condition="结合账龄、坏账/跌价政策、前五大客户及同行周转变化复核，不能仅凭天数判断造假。")

        net_debt, p_net_debt = _number(_metric(current, "net_debt")), _number(_metric(prior, "net_debt"))
        interest_cover = _number(_metric(current, "interest_coverage"))
        current_ratio = _number(_metric(current, "current_ratio"))
        debt_equity = _number(_metric(current, "debt_to_equity"))
        gross_short_cover = _number(_metric(current, "gross_cash_short_debt_coverage"))
        usable_short_cover = _number(_metric(current, "usable_cash_short_debt_coverage"))
        if net_debt is not None and p_net_debt is not None and net_debt > p_net_debt and (p_net_debt <= 0 or net_debt - p_net_debt > max(abs(p_net_debt) * 0.2, 1)):
            add("NET_DEBT_RISING", "yellow", "净负债明显上升", "净负债较上期增加超过 20%（或从净现金转为净负债）。", ("net_debt", "debt_to_equity", "interest_coverage"), current, prior, values={"net_debt_change": _change(net_debt, p_net_debt), "debt_to_equity": debt_equity}, review_condition="核对新增债务用途、受限资金、债务期限和在建工程；若扩产回报兑现可下调风险。")
        if interest_cover is not None and interest_cover < 3:
            add("INTEREST_COVERAGE_WEAK", "red" if interest_cover < 1 else "yellow", "利息保障倍数偏弱", "经营利润覆盖利息的缓冲有限。", ("interest_coverage", "operating_profit"), current, prior, values={"interest_coverage": interest_cover}, review_condition="核对利息资本化、财务费用净额及未来一年到期债务；恢复到 3 倍以上后再降级。")
        if current_ratio is not None and current_ratio < 1:
            add("CURRENT_RATIO_BELOW_ONE", "yellow", "流动资产不足流动负债", "流动比率低于 1，短期偿付依赖经营回款或再融资。", ("current_ratio", "cash"), current, prior, values={"current_ratio": current_ratio}, review_condition="结合受限现金、短期借款、票据、授信和现金流季节性判断，不单独作为违约结论。")
        if usable_short_cover is not None and usable_short_cover < 1:
            add("USABLE_CASH_SHORT_DEBT_GAP", "red" if usable_short_cover < 0.5 else "yellow", "可用现金不足以覆盖一年内债务", "剔除已披露受限资金后，现金无法完全覆盖一年内到期有息债务。", ("usable_cash_short_debt_coverage", "usable_cash", "restricted_cash", "debt_due_within_one_year"), current, prior, values={"usable_cash_short_debt_coverage": usable_short_cover}, review_condition="逐项核对未来十二个月到期表、受限资金解限条件、未使用授信、经营回款与再融资安排；不得把授信额度直接当现金。")
        elif usable_short_cover is None and gross_short_cover is not None and gross_short_cover < 1:
            add("GROSS_CASH_SHORT_DEBT_GAP", "yellow", "账面现金不足以覆盖一年内债务", "即使未扣除受限资金，账面现金仍低于一年内到期有息债务；实际可用覆盖只会更低。", ("gross_cash_short_debt_coverage", "cash", "debt_due_within_one_year", "restricted_cash"), current, prior, values={"gross_cash_short_debt_coverage": gross_short_cover}, review_condition="补齐受限资金并核对债务到期表、授信与经营现金流；在可用资金证据补齐前维持复核。")

        dividend_cover = _number(_metric(current, "free_cash_flow_dividend_cover"))
        if dividend_cover is not None and dividend_cover < 1:
            add("DIVIDEND_COVER_WEAK", "red" if dividend_cover < 0 else "yellow", "自由现金流未覆盖现金分红", "当期自由现金流不足以覆盖现金分红，红利持续性需要核验。", ("free_cash_flow_dividend_cover", "free_cash_flow", "dividend_payout"), current, prior, values={"free_cash_flow_dividend_cover": dividend_cover}, review_condition="区分一次性资本开支与持续缺口；观察净现金、借款和后续分红方案。")

        capex, p_capex = _number(_metric(current, "capital_expenditure")), _number(_metric(prior, "capital_expenditure"))
        fcf, p_fcf = _number(_metric(current, "free_cash_flow")), _number(_metric(prior, "free_cash_flow"))
        if capex is not None and p_capex not in (None, 0) and fcf is not None and p_fcf is not None and capex > p_capex * 1.25 and fcf < p_fcf:
            add("CAPEX_UP_FCF_DOWN", "yellow", "资本开支上升并挤压自由现金流", "资本开支增长超过 25%，同时自由现金流下降。", ("capital_expenditure", "free_cash_flow", "operating_cash_flow"), current, prior, values={"capex_change": _change(capex, p_capex), "free_cash_flow_change": _change(fcf, p_fcf)}, review_condition="拆分维持性/扩张性资本开支并跟踪投产进度、增量 ROIC 与未来现金回收。")

    deterioration_specs = (
        ("gross_margin", "down", 0.015, "GROSS_MARGIN_THREE_PERIOD_DECLINE", "毛利率连续下降"),
        ("operating_margin", "down", 0.01, "OPERATING_MARGIN_THREE_PERIOD_DECLINE", "营业利润率连续下降"),
        ("net_margin", "down", 0.01, "NET_MARGIN_THREE_PERIOD_DECLINE", "净利率连续下降"),
        ("return_on_invested_capital", "down", 0.01, "ROIC_THREE_PERIOD_DECLINE", "ROIC 连续下降"),
        ("cash_conversion", "down", 0.1, "CASH_CONVERSION_THREE_PERIOD_DECLINE", "现金转化率连续下降"),
        ("receivable_days", "up", 5.0, "RECEIVABLE_DAYS_THREE_PERIOD_RISE", "应收天数连续上升"),
        ("inventory_days", "up", 5.0, "INVENTORY_DAYS_THREE_PERIOD_RISE", "存货天数连续上升"),
        ("debt_to_equity", "up", 0.05, "LEVERAGE_THREE_PERIOD_RISE", "杠杆率连续上升"),
        ("gross_cash_short_debt_coverage", "down", 0.25, "SHORT_DEBT_COVER_THREE_PERIOD_DECLINE", "短债现金覆盖连续下降"),
    )
    trend_scope_issue = False
    if len(annual) >= 3:
        last_three = annual[-3:]
        trend_scope_issue = any(not _same_report_scope(last_three[index], last_three[index - 1]) for index in range(1, len(last_three)))
        for name, direction, minimum_total, signal_id, title in deterioration_specs:
            if trend_scope_issue:
                continue
            values = [_number(_metric(period, name)) for period in last_three]
            if any(value is None for value in values):
                continue
            first, middle, last = values  # type: ignore[misc]
            deteriorating = (first > middle > last and first - last >= minimum_total) if direction == "down" else (first < middle < last and last - first >= minimum_total)
            if deteriorating:
                add(signal_id, "yellow", title, "最近三个可比 12 个月同方向恶化，单期波动解释不足以消除趋势。", (name,), last_three[-1], last_three[-2], values={"three_period_values": values, "periods": [item.get("end_date") for item in last_three]}, review_condition="核对口径、重述、行业周期和同行分位；趋势反转至少需一个新的可比报告期确认。")

    severity_order = {"red": 0, "yellow": 1, "info": 2}
    signals.sort(key=lambda item: (severity_order.get(str(item.get("severity")), 9), str(item.get("id"))))
    unsupported_checks: list[str] = []
    if _number(_metric(latest, "debt_due_within_one_year")) is None:
        unsupported_checks.append("短期债务/现金覆盖：未取得一年内到期有息债务，不能推断偿债安全。")
    elif _number(_metric(latest, "restricted_cash")) is None:
        unsupported_checks.append("可用现金覆盖：受限资金缺失，只能把账面现金覆盖视为乐观上限。")
    unsupported_checks.append("合同资产、账龄和减值政策：需结合已归档财报附注人工确认。")
    if has_distinct_ttm and not ttm_comparable:
        unsupported_checks.append("最新 TTM 缺少上年同期可比 TTM，未拿最近年报代替同比基准。")
    if current and not _report_scope(current):
        unsupported_checks.append("合并/母公司口径：本期报表范围缺失；同比结果保留但必须回到原始报表确认口径。")
    if scope_comparison_issue:
        unsupported_checks.append("合并/母公司口径：存在同期间候选，但报表范围不同或混合，系统已停止同比比较。")
    if trend_scope_issue:
        unsupported_checks.append("连续趋势口径：最近三个年度包含不同或混合报表范围，系统未生成连续恶化信号。")
    return {
        "schema_version": "1.1",
        "status": "ready" if current else "insufficient_data",
        "as_of": latest.get("end_date") if latest else None,
        "comparison_basis": comparison_basis + "; no cumulative-to-single-quarter or interim-to-FY comparison.",
        "current_context": _period_context(current) if current else None,
        "comparison_context": _period_context(prior) if prior else None,
        "signals": signals,
        "summary": {
            "red": sum(1 for item in signals if item["severity"] == "red"),
            "yellow": sum(1 for item in signals if item["severity"] == "yellow"),
            "total": len(signals),
        },
        "coverage": coverage,
        "missing_metrics": sorted(set(missing)),
        "unsupported_checks": unsupported_checks,
        "methodology": "阈值用于发现变化和安排复核优先级，不构成舞弊认定或买卖建议。",
    }
