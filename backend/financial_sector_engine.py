"""Deterministic insurance and securities-company analysis plug-ins.

The module intentionally keeps insurance and brokerage economics separate from
industrial-company cash flow models.  Regulatory ratios remain missing until a
source-labelled filing or manual disclosure supplies them; the model never
infers solvency or capital compliance from ordinary statement totals.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Optional

from .finance_engine import CompanyData, ZERO, _annual_periods, _build_ttm, _fact_metric, _json_safe, _number, _snapshot
from .sector_engine import _fact, _flag, _growth, _metric_value, _ratio, _reported_ratio


FINANCIAL_SECTOR_SCHEMA_VERSION = "financial-sector-analysis/1.0"
INSURANCE_KEYWORDS = ("保险", "寿险", "财险", "再保险", "insurance", "insurer", "reinsurance")
BROKERAGE_KEYWORDS = ("证券", "券商", "brokerage", "securities", "investment bank")

FINANCIAL_TEMPLATES = {
    "insurance": {
        "label": "保险",
        "required": (
            "insurance_service_revenue", "gross_written_premiums", "new_business_value", "embedded_value",
            "net_investment_yield", "comprehensive_investment_yield", "insurance_contract_liabilities",
            "core_solvency_ratio", "comprehensive_solvency_ratio", "total_equity", "shares_outstanding",
        ),
    },
    "brokerage": {
        "label": "证券公司",
        "required": (
            "revenue", "brokerage_net_revenue", "investment_banking_net_revenue", "asset_management_net_revenue",
            "trading_investment_income", "net_capital", "risk_coverage_ratio", "capital_leverage_ratio",
            "liquidity_coverage_ratio", "net_stable_funding_ratio", "total_equity", "shares_outstanding",
        ),
    },
}

REGULATORY_SOURCES = {
    "insurance": {
        "rule": "保险公司偿付能力管理规定（2021年第1号）",
        "url": "https://www.nfra.gov.cn/cn/view/pages/rulesDetail.html?docId=962016",
        "minimums": {"core_solvency_ratio": 0.50, "comprehensive_solvency_ratio": 1.00},
        "note": "达标还要求风险综合评级在 B 类及以上；评级缺失时不得声称偿付能力达标。",
    },
    "brokerage": {
        "rule": "证券公司风险控制指标管理办法（2020年修订）",
        "url": "https://www.csrc.gov.cn/csrc/c106256/c1653957/content.shtml",
        "minimums": {"risk_coverage_ratio": 1.00, "capital_leverage_ratio": 0.08, "liquidity_coverage_ratio": 1.00, "net_stable_funding_ratio": 1.00},
        "note": "证监会可以动态调整指标标准；报告必须同时显示披露期和规则版本。",
    },
}


def classify_financial_sector_template(company: CompanyData) -> Optional[str]:
    explicit = str(company.valuation.get("financial_sector_template") or company.valuation.get("sector_template") or "").strip().lower()
    if explicit in FINANCIAL_TEMPLATES:
        return explicit
    industry = company.industry.strip().lower()
    if any(keyword in industry for keyword in INSURANCE_KEYWORDS):
        return "insurance"
    if any(keyword in industry for keyword in BROKERAGE_KEYWORDS):
        return "brokerage"
    return None


def _insurance_metrics(snapshot: Any, prior: Optional[Any], unit: str) -> dict[str, Any]:
    service_revenue = _fact(snapshot, "insurance_service_revenue", unit)
    premiums = _fact(snapshot, "gross_written_premiums", unit)
    nbv = _fact(snapshot, "new_business_value", unit)
    ev = _fact(snapshot, "embedded_value", unit)
    liabilities = _fact(snapshot, "insurance_contract_liabilities", unit)
    investment_assets = _fact(snapshot, "total_investment_assets", unit)
    net_income = _fact(snapshot, "net_income", unit)
    equity = _fact(snapshot, "total_equity", unit)
    shares = _fact(snapshot, "shares_outstanding", "million shares")
    return {
        "insurance_service_revenue": _fact_metric(service_revenue),
        "insurance_service_revenue_growth": _growth(service_revenue, _fact(prior, "insurance_service_revenue", unit) if prior else None, "insurance_service_revenue"),
        "gross_written_premiums": _fact_metric(premiums),
        "premium_growth": _growth(premiums, _fact(prior, "gross_written_premiums", unit) if prior else None, "gross_written_premiums"),
        "new_business_value": _fact_metric(nbv),
        "new_business_value_growth": _growth(nbv, _fact(prior, "new_business_value", unit) if prior else None, "new_business_value"),
        "new_business_value_margin": _ratio(nbv, premiums, "new_business_value / gross_written_premiums"),
        "embedded_value": _fact_metric(ev),
        "embedded_value_growth": _growth(ev, _fact(prior, "embedded_value", unit) if prior else None, "embedded_value"),
        "embedded_value_per_share": _ratio(ev, shares, "embedded_value / shares_outstanding", "CNY/share"),
        "net_investment_yield": _reported_ratio(snapshot, "net_investment_yield"),
        "comprehensive_investment_yield": _reported_ratio(snapshot, "comprehensive_investment_yield"),
        "insurance_contract_liabilities": _fact_metric(liabilities),
        "insurance_contract_liability_growth": _growth(liabilities, _fact(prior, "insurance_contract_liabilities", unit) if prior else None, "insurance_contract_liabilities"),
        "insurance_liabilities_to_equity": _ratio(liabilities, equity, "insurance_contract_liabilities / total_equity"),
        "total_investment_assets": _fact_metric(investment_assets),
        "net_income": _fact_metric(net_income),
        "net_income_growth": _growth(net_income, _fact(prior, "net_income", unit) if prior else None, "net_income"),
        "return_on_equity": _ratio(net_income, equity, "net_income / ending_total_equity"),
        "core_solvency_ratio": _reported_ratio(snapshot, "core_solvency_ratio"),
        "comprehensive_solvency_ratio": _reported_ratio(snapshot, "comprehensive_solvency_ratio"),
    }


def _brokerage_metrics(snapshot: Any, prior: Optional[Any], unit: str) -> dict[str, Any]:
    revenue = _fact(snapshot, "revenue", unit)
    brokerage = _fact(snapshot, "brokerage_net_revenue", unit)
    investment_bank = _fact(snapshot, "investment_banking_net_revenue", unit)
    asset_management = _fact(snapshot, "asset_management_net_revenue", unit)
    net_interest = _fact(snapshot, "net_interest_income", unit)
    trading = _fact(snapshot, "trading_investment_income", unit)
    net_income = _fact(snapshot, "net_income", unit)
    net_capital = _fact(snapshot, "net_capital", unit)
    equity = _fact(snapshot, "total_equity", unit)
    return {
        "revenue": _fact_metric(revenue),
        "revenue_growth": _growth(revenue, _fact(prior, "revenue", unit) if prior else None, "revenue"),
        "brokerage_net_revenue": _fact_metric(brokerage),
        "brokerage_revenue_share": _ratio(brokerage, revenue, "brokerage_net_revenue / revenue"),
        "investment_banking_net_revenue": _fact_metric(investment_bank),
        "investment_banking_revenue_share": _ratio(investment_bank, revenue, "investment_banking_net_revenue / revenue"),
        "asset_management_net_revenue": _fact_metric(asset_management),
        "asset_management_revenue_share": _ratio(asset_management, revenue, "asset_management_net_revenue / revenue"),
        "net_interest_income": _fact_metric(net_interest),
        "trading_investment_income": _fact_metric(trading),
        "trading_revenue_share": _ratio(trading, revenue, "trading_investment_income / revenue"),
        "net_income": _fact_metric(net_income),
        "net_income_growth": _growth(net_income, _fact(prior, "net_income", unit) if prior else None, "net_income"),
        "return_on_equity": _ratio(net_income, equity, "net_income / ending_total_equity"),
        "net_capital": _fact_metric(net_capital),
        "net_capital_to_equity": _ratio(net_capital, equity, "net_capital / total_equity"),
        "risk_coverage_ratio": _reported_ratio(snapshot, "risk_coverage_ratio"),
        "capital_leverage_ratio": _reported_ratio(snapshot, "capital_leverage_ratio"),
        "liquidity_coverage_ratio": _reported_ratio(snapshot, "liquidity_coverage_ratio"),
        "net_stable_funding_ratio": _reported_ratio(snapshot, "net_stable_funding_ratio"),
    }


def _insurance_flags(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    core = _metric_value(metrics, "core_solvency_ratio")
    comprehensive = _metric_value(metrics, "comprehensive_solvency_ratio")
    nbv_growth = _metric_value(metrics, "new_business_value_growth")
    investment_yield = _metric_value(metrics, "comprehensive_investment_yield")
    if core is not None and core < Decimal("0.50"):
        flags.append(_flag("insurance_core_solvency_below_minimum", "red", "核心偿付能力低于监管最低线", f"核心偿付能力充足率 {_number(core * Decimal('100'))}%。", metrics, ("core_solvency_ratio",), "核对偿付能力报告、风险综合评级、资本补充与监管措施。"))
    elif core is not None and core < Decimal("0.75"):
        flags.append(_flag("insurance_core_solvency_buffer_thin", "yellow", "核心偿付能力缓冲偏薄", f"核心偿付能力充足率 {_number(core * Decimal('100'))}%。", metrics, ("core_solvency_ratio",), "低于 75% 作为内部复核线，不等同于不达标；核对资本计划和业务增长消耗。"))
    if comprehensive is not None and comprehensive < Decimal("1.00"):
        flags.append(_flag("insurance_comprehensive_solvency_below_minimum", "red", "综合偿付能力低于监管最低线", f"综合偿付能力充足率 {_number(comprehensive * Decimal('100'))}%。", metrics, ("comprehensive_solvency_ratio",), "核对偿付能力报告、风险综合评级和监管措施。"))
    elif comprehensive is not None and comprehensive < Decimal("1.50"):
        flags.append(_flag("insurance_comprehensive_solvency_buffer_thin", "yellow", "综合偿付能力缓冲偏薄", f"综合偿付能力充足率 {_number(comprehensive * Decimal('100'))}%。", metrics, ("comprehensive_solvency_ratio",), "150% 是内部压力复核线，不等同于监管最低要求。"))
    if nbv_growth is not None and nbv_growth <= Decimal("-0.20"):
        flags.append(_flag("insurance_nbv_decline", "red" if nbv_growth <= Decimal("-0.40") else "yellow", "新业务价值明显下降", f"新业务价值同比下降 {_number(-nbv_growth * Decimal('100'))}%。", metrics, ("new_business_value", "new_business_value_growth", "new_business_value_margin"), "核对产品结构、渠道费用、利率假设、退保率和经济假设变更。"))
    if investment_yield is not None and investment_yield < ZERO:
        flags.append(_flag("insurance_negative_comprehensive_yield", "red", "综合投资收益率为负", f"综合投资收益率 {_number(investment_yield * Decimal('100'))}%。", metrics, ("net_investment_yield", "comprehensive_investment_yield"), "核对权益波动、债券公允价值、信用减值和资产负债久期错配。"))
    return flags


def _brokerage_flags(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    source = REGULATORY_SOURCES["brokerage"]
    for name, title in (("risk_coverage_ratio", "风险覆盖率"), ("capital_leverage_ratio", "资本杠杆率"), ("liquidity_coverage_ratio", "流动性覆盖率"), ("net_stable_funding_ratio", "净稳定资金率")):
        value = _metric_value(metrics, name)
        minimum = Decimal(str(source["minimums"][name]))
        if value is not None and value < minimum:
            flags.append(_flag(f"broker_{name}_below_minimum", "red", f"{title}低于监管最低线", f"{title} {_number(value * Decimal('100'))}%，最低线 {_number(minimum * Decimal('100'))}%。", metrics, (name,), "核对母公司口径监管报表、计算标准版本、分类监管调整和整改安排。"))
    trading_share = _metric_value(metrics, "trading_revenue_share")
    if trading_share is not None and trading_share > Decimal("0.50"):
        flags.append(_flag("broker_trading_income_concentration", "yellow", "交易投资收入占比较高", f"交易投资收入占营业收入 {_number(trading_share * Decimal('100'))}%。", metrics, ("trading_investment_income", "trading_revenue_share"), "核对方向性敞口、衍生品、公允价值层级和压力测试；该信号不是监管违规判断。"))
    return flags


def _insurance_valuation(snapshot: Any, valuation: Mapping[str, Any]) -> dict[str, Any]:
    config = valuation.get("sector") if isinstance(valuation.get("sector"), Mapping) else {}
    scenarios = config.get("scenarios") if isinstance(config, Mapping) else []
    ev = _fact(snapshot, "embedded_value", snapshot.facts.get("total_equity", _fact(snapshot, "total_equity", "CNY million")).unit)
    nbv = _fact(snapshot, "new_business_value", ev.unit)
    shares = _fact(snapshot, "shares_outstanding", "million shares")
    outputs = []
    for raw in scenarios if isinstance(scenarios, list) else []:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or f"scenario_{len(outputs) + 1}")
        target_pev = Decimal(str(raw["target_pev"])) if raw.get("target_pev") is not None else None
        nbv_multiple = Decimal(str(raw["nbv_multiple"])) if raw.get("nbv_multiple") is not None else None
        normalized_nbv = Decimal(str(raw["normalized_nbv"])) if raw.get("normalized_nbv") is not None else nbv.value
        ev_haircut = Decimal(str(raw.get("ev_haircut", 0)))
        missing = [key for key, value in (("embedded_value", ev.value), ("shares", shares.value), ("target_pev", target_pev), ("nbv_multiple", nbv_multiple), ("normalized_nbv", normalized_nbv)) if value is None]
        pev_value = appraisal = None
        reason = ""
        if missing:
            reason = "missing " + ", ".join(missing)
        elif shares.value <= ZERO or target_pev <= ZERO or nbv_multiple < ZERO or not ZERO <= ev_haircut <= Decimal("0.9"):
            reason = "shares and target P/EV must be positive; NBV multiple cannot be negative; EV haircut must be 0–90%"
        else:
            adjusted_ev = ev.value * (Decimal("1") - ev_haircut)
            pev_value = adjusted_ev * target_pev / shares.value
            appraisal = (adjusted_ev + normalized_nbv * nbv_multiple) / shares.value
        outputs.append({"name": name, "pev": {"status": "ok" if pev_value is not None else "not_calculable", "value_per_share": _number(pev_value), "formula": "embedded_value × (1 − EV_haircut) × target_P/EV / shares", "reason": reason}, "appraisal_value": {"status": "ok" if appraisal is not None else "not_calculable", "value_per_share": _number(appraisal), "formula": "[embedded_value × (1 − EV_haircut) + normalized_NBV × NBV_multiple] / shares", "reason": reason}, "assumptions": dict(raw), "inputs": _fact_metric(ev).get("inputs", []) + _fact_metric(nbv).get("inputs", []) + _fact_metric(shares).get("inputs", [])})
    return {"method_policy": "P/EV and appraisal value are separate lenses; EV/NBV assumptions must come from the insurer's disclosed methodology.", "scenarios": outputs, "dcf": {"status": "unsupported", "reason": "Industrial free-cash-flow DCF is not used for insurers."}}


def _brokerage_valuation(snapshot: Any, valuation: Mapping[str, Any]) -> dict[str, Any]:
    config = valuation.get("sector") if isinstance(valuation.get("sector"), Mapping) else {}
    scenarios = config.get("scenarios") if isinstance(config, Mapping) else []
    equity = _fact(snapshot, "total_equity", "CNY million")
    net_income = _fact(snapshot, "net_income", equity.unit)
    shares = _fact(snapshot, "shares_outstanding", "million shares")
    outputs = []
    for raw in scenarios if isinstance(scenarios, list) else []:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or f"scenario_{len(outputs) + 1}")
        target_pb = Decimal(str(raw["target_pb"])) if raw.get("target_pb") is not None else None
        target_pe = Decimal(str(raw["target_pe"])) if raw.get("target_pe") is not None else None
        normalized_income = Decimal(str(raw["normalized_net_income"])) if raw.get("normalized_net_income") is not None else net_income.value
        missing = [key for key, value in (("equity", equity.value), ("shares", shares.value), ("target_pb", target_pb), ("target_pe", target_pe), ("normalized_net_income", normalized_income)) if value is None]
        pb_value = pe_value = None
        reason = ""
        if missing:
            reason = "missing " + ", ".join(missing)
        elif shares.value <= ZERO or target_pb <= ZERO or target_pe <= ZERO:
            reason = "shares, target PB and target PE must be positive"
        else:
            pb_value = equity.value / shares.value * target_pb
            pe_value = normalized_income / shares.value * target_pe
        outputs.append({"name": name, "pb": {"status": "ok" if pb_value is not None else "not_calculable", "value_per_share": _number(pb_value), "formula": "total_equity / shares × target_PB", "reason": reason}, "normalized_pe": {"status": "ok" if pe_value is not None else "not_calculable", "value_per_share": _number(pe_value), "formula": "normalized_net_income / shares × target_PE", "reason": reason}, "assumptions": dict(raw), "inputs": _fact_metric(equity).get("inputs", []) + _fact_metric(net_income).get("inputs", []) + _fact_metric(shares).get("inputs", [])})
    return {"method_policy": "Book-value and normalized-earnings methods remain separate because trading income is cyclical.", "scenarios": outputs, "dcf": {"status": "unsupported", "reason": "Industrial free-cash-flow DCF is not used for securities companies."}}


def analyze_financial_sector_company(company: CompanyData, template: str) -> dict[str, Any]:
    if template not in FINANCIAL_TEMPLATES:
        raise ValueError(f"unsupported financial sector template: {template}")
    annual = [_snapshot(period, company.unit) for period in _annual_periods(company)]
    _, ttm_notes = _build_ttm(company)
    builder = _insurance_metrics if template == "insurance" else _brokerage_metrics
    annual_payload = []
    for index, snapshot in enumerate(annual):
        prior = annual[index - 1] if index else None
        annual_payload.append({"label": snapshot.label, "end_date": snapshot.end_date, "fiscal_year": snapshot.fiscal_year, "construction": snapshot.construction, "metrics": builder(snapshot, prior, company.unit)})
    latest = annual[-1] if annual else (_snapshot(company.periods[-1], company.unit) if company.periods else None)
    prior = annual[-2] if len(annual) >= 2 else None
    metrics = builder(latest, prior, company.unit) if latest else {}
    flags = (_insurance_flags(metrics) if template == "insurance" else _brokerage_flags(metrics)) if latest else []
    required = FINANCIAL_TEMPLATES[template]["required"]
    covered = [name for name in required if latest and _fact(latest, name, company.unit).value is not None]
    missing = [name for name in required if name not in covered]
    score = round(len(covered) / len(required) * 100) if required else 0
    market = {key: company.valuation.get(key) for key in ("market_price", "price_as_of", "market_cap", "shares_outstanding", "pe", "pe_ttm", "pb", "dividend_yield", "source")}
    valuation = (_insurance_valuation(latest, company.valuation) if template == "insurance" else _brokerage_valuation(latest, company.valuation)) if latest else {"status": "missing", "reason": "No financial period supplied."}
    changes = {"schema_version": f"{template}-change-detection/1.0", "summary": {"total": len(flags), "red": sum(item["severity"] == "red" for item in flags), "yellow": sum(item["severity"] == "yellow" for item in flags)}, "signals": flags, "missing_metrics": missing, "unsupported_checks": ["Industrial cash conversion, inventory turns, net debt and generic FCF checks are disabled."]}
    result = {
        "schema_version": FINANCIAL_SECTOR_SCHEMA_VERSION,
        "engine": "deterministic_financial_sector_engine",
        "company": {"symbol": company.symbol, "name": company.name, "industry": company.industry, "currency": company.currency, "unit": company.unit},
        "market": market,
        "as_of": latest.end_date if latest else None,
        "support": {"status": "supported_sector", "supported": True, "industry": company.industry, "model": f"{template}/1.0", "sector_template": template, "label": FINANCIAL_TEMPLATES[template]["label"], "reason": "Financial-sector capital, operating-quality and valuation model enabled; industrial-company conclusions are disabled."},
        "data_quality": {"score": score, "covered_required_facts": covered, "missing_required_facts": missing, "ttm_notes": ttm_notes, "warning": "Coverage measures disclosure availability, not safety. Missing regulatory ratios remain unknown."},
        "periods": {"annual": annual_payload, "ttm": {"label": "LATEST_FINANCIAL_SECTOR_PERIOD", "end_date": latest.end_date, "fiscal_year": latest.fiscal_year, "construction": "regulatory and value metrics use latest comparable reported period", "metrics": metrics} if latest else None},
        "sector": {"template": template, "label": FINANCIAL_TEMPLATES[template]["label"], "latest": {"end_date": latest.end_date, "metrics": metrics} if latest else None, "coverage": {"score": score, "required": list(required), "covered": covered, "missing": missing}, "flags": flags, "regulatory_source": REGULATORY_SOURCES[template]},
        "flags": flags,
        "changes": changes,
        "valuation": valuation,
        "piotroski": {"status": "unsupported", "reason": "Generic industrial Piotroski score is not applied."},
        "dupont": {"status": "unsupported", "reason": "Financial-sector capital and business drivers replace industrial DuPont."},
        "methodology": {"missing_vs_zero": "Missing remains unknown and is never treated as compliant.", "period_policy": "Regulatory ratios and EV/net-capital facts use the disclosed point-in-time period; flows use comparable annual periods.", "decision_policy": "Signals identify review priorities and are not automatic trade or fraud conclusions.", "regulatory_source": REGULATORY_SOURCES[template]},
    }
    return _json_safe(result)


__all__ = ["FINANCIAL_SECTOR_SCHEMA_VERSION", "FINANCIAL_TEMPLATES", "REGULATORY_SOURCES", "classify_financial_sector_template", "analyze_financial_sector_company"]
