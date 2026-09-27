"""Deterministic bank analysis plug-in.

Banks are not industrial companies: deposits are operating funding, loans are
earning assets, and generic net-debt/FCF/EBITDA screens are misleading.  This
module therefore consumes the same canonical, source-labelled facts as the
main engine but calculates a bank-only dashboard and bank-only valuations.

Reported ratios use decimal form (1.5% = 0.015).  A fact explicitly labelled
with unit ``%``/``percent`` is converted to decimal at the calculation boundary.
Missing regulatory disclosures remain missing and are never treated as zero.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Any, Iterable, Mapping, Optional

from .finance_engine import (
    CompanyData,
    DerivedFact,
    FinancialPeriod,
    ONE,
    ZERO,
    _annual_periods,
    _balance_only_snapshot,
    _build_ttm,
    _decimal,
    _fact_metric,
    _find_comparable,
    _inputs,
    _json_safe,
    _metric,
    _number,
    _reported,
    _snapshot,
)


BANK_SCHEMA_VERSION = "bank-analysis/1.0"

BANK_REQUIRED_FACTS = (
    "net_income",
    "net_interest_income",
    "total_assets",
    "total_equity",
    "loans_and_advances",
    "customer_deposits",
    "shares_outstanding",
    "net_interest_margin",
    "nonperforming_loan_ratio",
    "special_mention_ratio",
    "overdue_loan_ratio",
    "provision_coverage_ratio",
    "core_tier1_capital_ratio",
    "capital_adequacy_ratio",
)


def _fact(snapshot: Any, name: str, unit: str) -> DerivedFact:
    return snapshot.facts.get(name) or DerivedFact(None, f"reported({name})", (), unit, f"missing {name}")


def _reported_ratio(snapshot: Any, name: str) -> dict[str, Any]:
    fact = _fact(snapshot, name, "ratio")
    value = fact.value
    formula = fact.formula
    if value is not None and str(fact.unit).strip().lower() in {"%", "percent", "percentage"}:
        value = value / Decimal("100")
        formula = f"{formula} / 100 because source unit is percent"
    return _metric(value, "ratio", formula, [fact], reason=fact.missing_reason)


def _ratio_metric(numerator: DerivedFact, denominator: DerivedFact, formula: str) -> dict[str, Any]:
    if numerator.value is None or denominator.value is None:
        return _metric(None, "ratio", formula, [numerator, denominator], reason="required source fact is missing")
    if numerator.unit != denominator.unit:
        return _metric(None, "ratio", formula, [numerator, denominator], status="not_calculable", reason="source units do not match")
    if denominator.value == ZERO:
        return _metric(None, "ratio", formula, [numerator, denominator], status="not_calculable", reason="reported denominator is zero")
    return _metric(numerator.value / denominator.value, "ratio", formula, [numerator, denominator])


def _per_share_metric(amount: DerivedFact, shares: DerivedFact, formula: str) -> dict[str, Any]:
    """Divide CNY millions by million shares without treating units as equal."""

    if amount.value is None or shares.value is None:
        return _metric(None, "CNY/share", formula, [amount, shares], reason="amount or shares is missing")
    if shares.value == ZERO:
        return _metric(None, "CNY/share", formula, [amount, shares], status="not_calculable", reason="reported shares are zero")
    if "million" not in amount.unit.lower() or "share" not in shares.unit.lower():
        return _metric(None, "CNY/share", formula, [amount, shares], status="not_calculable", reason=f"unsupported units: {amount.unit!r} and {shares.unit!r}")
    return _metric(amount.value / shares.value, "CNY/share", formula, [amount, shares])


def _average_ratio_metric(
    numerator: DerivedFact,
    current_denominator: DerivedFact,
    prior_denominator: Optional[DerivedFact],
    formula: str,
) -> dict[str, Any]:
    if prior_denominator is None or prior_denominator.value is None:
        return _metric(None, "ratio", formula, [numerator, current_denominator, prior_denominator], reason="prior-period denominator is missing")
    if numerator.value is None or current_denominator.value is None:
        return _metric(None, "ratio", formula, [numerator, current_denominator, prior_denominator], reason="required source fact is missing")
    if len({numerator.unit, current_denominator.unit, prior_denominator.unit}) != 1:
        return _metric(None, "ratio", formula, [numerator, current_denominator, prior_denominator], status="not_calculable", reason="source units do not match")
    average = (current_denominator.value + prior_denominator.value) / Decimal("2")
    if average == ZERO:
        return _metric(None, "ratio", formula, [numerator, current_denominator, prior_denominator], status="not_calculable", reason="average denominator is zero")
    return _metric(numerator.value / average, "ratio", formula, [numerator, current_denominator, prior_denominator])


def _growth_metric(current: DerivedFact, prior: Optional[DerivedFact], name: str) -> dict[str, Any]:
    formula = f"{name}(current) / {name}(prior) - 1"
    if prior is None or current.value is None or prior.value is None:
        return _metric(None, "ratio", formula, [current, prior], reason="current or comparable prior fact is missing")
    if current.unit != prior.unit:
        return _metric(None, "ratio", formula, [current, prior], status="not_calculable", reason="source units do not match")
    if prior.value == ZERO:
        return _metric(None, "ratio", formula, [current, prior], status="not_calculable", reason="prior value is zero")
    return _metric(current.value / prior.value - ONE, "ratio", formula, [current, prior])


def _prefer_reported_or_derived(snapshot: Any, reported_name: str, derived: dict[str, Any]) -> dict[str, Any]:
    reported = _reported_ratio(snapshot, reported_name)
    return reported if reported.get("status") == "ok" else derived


def _bank_metrics(snapshot: Any, prior: Optional[Any], unit: str) -> dict[str, Any]:
    net_income = _fact(snapshot, "net_income", unit)
    net_interest = _fact(snapshot, "net_interest_income", unit)
    net_fees = _fact(snapshot, "net_fee_commission_income", unit)
    equity = _fact(snapshot, "total_equity", unit)
    prior_equity = _fact(prior, "total_equity", unit) if prior else None
    loans = _fact(snapshot, "loans_and_advances", unit)
    deposits = _fact(snapshot, "customer_deposits", unit)
    npl = _fact(snapshot, "nonperforming_loans", unit)
    special = _fact(snapshot, "special_mention_loans", unit)
    overdue = _fact(snapshot, "overdue_loans", unit)
    provision = _fact(snapshot, "loan_loss_provision", unit)
    shares = _fact(snapshot, "shares_outstanding", "million shares")

    roe_derived = _average_ratio_metric(net_income, equity, prior_equity, "net_income / average(total_equity)")
    return {
        "net_interest_income": _fact_metric(net_interest),
        "net_interest_income_growth": _growth_metric(net_interest, _fact(prior, "net_interest_income", unit) if prior else None, "net_interest_income"),
        "net_fee_commission_income": _fact_metric(net_fees),
        "net_income": _fact_metric(net_income),
        "net_income_growth": _growth_metric(net_income, _fact(prior, "net_income", unit) if prior else None, "net_income"),
        "return_on_equity": _prefer_reported_or_derived(snapshot, "return_on_equity", roe_derived),
        "net_interest_margin": _reported_ratio(snapshot, "net_interest_margin"),
        "nonperforming_loan_ratio": _prefer_reported_or_derived(snapshot, "nonperforming_loan_ratio", _ratio_metric(npl, loans, "nonperforming_loans / loans_and_advances")),
        "special_mention_ratio": _prefer_reported_or_derived(snapshot, "special_mention_ratio", _ratio_metric(special, loans, "special_mention_loans / loans_and_advances")),
        "overdue_loan_ratio": _prefer_reported_or_derived(snapshot, "overdue_loan_ratio", _ratio_metric(overdue, loans, "overdue_loans / loans_and_advances")),
        "provision_coverage_ratio": _prefer_reported_or_derived(snapshot, "provision_coverage_ratio", _ratio_metric(provision, npl, "loan_loss_provision / nonperforming_loans")),
        "provision_loan_ratio": _prefer_reported_or_derived(snapshot, "provision_loan_ratio", _ratio_metric(provision, loans, "loan_loss_provision / loans_and_advances")),
        "loan_deposit_ratio": _ratio_metric(loans, deposits, "loans_and_advances / customer_deposits"),
        "core_tier1_capital_ratio": _reported_ratio(snapshot, "core_tier1_capital_ratio"),
        "tier1_capital_ratio": _reported_ratio(snapshot, "tier1_capital_ratio"),
        "capital_adequacy_ratio": _reported_ratio(snapshot, "capital_adequacy_ratio"),
        "cost_income_ratio": _reported_ratio(snapshot, "cost_income_ratio"),
        "loans_and_advances": _fact_metric(loans),
        "customer_deposits": _fact_metric(deposits),
        "loan_loss_provision": _fact_metric(provision),
        "total_assets": _fact_metric(_fact(snapshot, "total_assets", unit)),
        "total_equity": _fact_metric(equity),
        "book_value_per_share": _per_share_metric(equity, shares, "total_equity / shares_outstanding"),
    }


def _metric_value(metrics: Mapping[str, Any], name: str) -> Optional[Decimal]:
    raw = metrics.get(name)
    if not isinstance(raw, Mapping) or raw.get("status") != "ok":
        return None
    return _decimal(raw.get("value"), name)


def _flag(
    flag_id: str,
    severity: str,
    title: str,
    detail: str,
    metrics: Mapping[str, Any],
    names: Iterable[str],
    review_condition: str,
) -> dict[str, Any]:
    selected = {name: metrics.get(name) for name in names}
    inputs = [line for item in selected.values() if isinstance(item, Mapping) for line in item.get("inputs", [])]
    return {
        "id": flag_id,
        "severity": severity,
        "title": title,
        "detail": detail,
        "metrics": selected,
        "inputs": inputs,
        "review_condition": review_condition,
        "classification": "deterministic_review_signal_not_fraud_or_trade_call",
    }


def _bank_flags(current: Mapping[str, Any], prior: Optional[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not prior:
        return []
    flags: list[dict[str, Any]] = []

    def delta(name: str) -> Optional[Decimal]:
        now = _metric_value(current, name)
        before = _metric_value(prior, name)
        return now - before if now is not None and before is not None else None

    npl_change = delta("nonperforming_loan_ratio")
    if npl_change is not None and npl_change >= Decimal("0.002"):
        severity = "red" if npl_change >= Decimal("0.005") else "yellow"
        flags.append(_flag("bank_npl_rising", severity, "不良贷款率上升", f"同比上升 {_number(npl_change * Decimal('10000'))} 个基点。", current, ("nonperforming_loan_ratio",), "核对不良生成、核销、转让、重组贷款和迁徙率。"))
    special_change = delta("special_mention_ratio")
    if special_change is not None and special_change >= Decimal("0.003"):
        flags.append(_flag("bank_special_mention_rising", "yellow", "关注类贷款占比上升", f"同比上升 {_number(special_change * Decimal('10000'))} 个基点。", current, ("special_mention_ratio",), "核对关注类向不良迁徙、行业与区域集中度。"))
    overdue_change = delta("overdue_loan_ratio")
    if overdue_change is not None and overdue_change >= Decimal("0.002"):
        severity = "red" if overdue_change >= Decimal("0.005") else "yellow"
        flags.append(_flag("bank_overdue_loan_rising", severity, "逾期贷款率上升", f"同比上升 {_number(overdue_change * Decimal('10000'))} 个基点。", current, ("overdue_loan_ratio", "nonperforming_loan_ratio", "special_mention_ratio"), "核对逾期结构、逾期向不良迁徙、展期重组及核销处置节奏。"))
    nim_change = delta("net_interest_margin")
    if nim_change is not None and nim_change <= Decimal("-0.002"):
        severity = "red" if nim_change <= Decimal("-0.004") else "yellow"
        flags.append(_flag("bank_nim_compression", severity, "净息差收窄", f"同比下降 {_number(-nim_change * Decimal('10000'))} 个基点。", current, ("net_interest_margin",), "拆分资产收益率、存款成本、负债结构和重定价节奏。"))
    coverage_change = delta("provision_coverage_ratio")
    if coverage_change is not None and coverage_change <= Decimal("-0.20"):
        flags.append(_flag("bank_provision_coverage_falling", "yellow", "拨备覆盖率下降", f"同比下降 {_number(-coverage_change * Decimal('100'))} 个百分点。", current, ("provision_coverage_ratio", "provision_loan_ratio"), "结合不良认定、拨备计提和核销节奏判断缓冲是否变薄。"))
    capital_change = delta("core_tier1_capital_ratio")
    if capital_change is not None and capital_change <= Decimal("-0.01"):
        flags.append(_flag("bank_core_tier1_falling", "yellow", "核心一级资本充足率下降", f"同比下降 {_number(-capital_change * Decimal('100'))} 个百分点。", current, ("core_tier1_capital_ratio", "capital_adequacy_ratio"), "核对风险加权资产增速、分红、内生资本和资本补充计划；监管要求因银行类别而异。"))
    cost_change = delta("cost_income_ratio")
    if cost_change is not None and cost_change >= Decimal("0.03"):
        flags.append(_flag("bank_cost_income_rising", "yellow", "成本收入比上升", f"同比上升 {_number(cost_change * Decimal('100'))} 个百分点。", current, ("cost_income_ratio",), "区分战略投入、一次性费用与持续效率恶化。"))
    return flags


def _valuation_inputs(*metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [line for metric in metrics for line in metric.get("inputs", [])]


def _bank_valuation(metrics: Mapping[str, Any], valuation: Mapping[str, Any]) -> dict[str, Any]:
    bvps_metric = metrics.get("book_value_per_share") if isinstance(metrics.get("book_value_per_share"), Mapping) else {}
    roe_metric = metrics.get("return_on_equity") if isinstance(metrics.get("return_on_equity"), Mapping) else {}
    bvps = _metric_value(metrics, "book_value_per_share")
    observed_roe = _metric_value(metrics, "return_on_equity")
    market_price = _decimal(valuation.get("market_price"), "market_price")
    current_pb = None if bvps in (None, ZERO) or market_price is None else market_price / bvps
    bank_config = valuation.get("bank") if isinstance(valuation.get("bank"), Mapping) else valuation
    pb_scenarios = bank_config.get("pb_scenarios") if isinstance(bank_config, Mapping) else []
    pb_outputs: list[dict[str, Any]] = []
    for raw in pb_scenarios if isinstance(pb_scenarios, list) else []:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or f"scenario_{len(pb_outputs) + 1}")
        target_pb = _decimal(raw.get("target_pb"), f"{name}.target_pb")
        value = bvps * target_pb if bvps is not None and target_pb is not None else None
        pb_outputs.append({
            "name": name,
            "status": "ok" if value is not None else "missing",
            "target_pb": _number(target_pb),
            "value_per_share": _number(value),
            "formula": "book_value_per_share × target_pb",
            "inputs": _valuation_inputs(bvps_metric),
            "reason": "" if value is not None else "book value per share or target PB is missing",
        })

    residual_inputs = bank_config.get("residual_income_scenarios") if isinstance(bank_config, Mapping) else []
    residual_outputs: list[dict[str, Any]] = []
    for raw in residual_inputs if isinstance(residual_inputs, list) else []:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or f"scenario_{len(residual_outputs) + 1}")
        roe = _decimal(raw.get("roe", observed_roe), f"{name}.roe")
        cost = _decimal(raw.get("cost_of_equity"), f"{name}.cost_of_equity")
        growth = _decimal(raw.get("book_value_growth"), f"{name}.book_value_growth")
        terminal = _decimal(raw.get("terminal_growth"), f"{name}.terminal_growth")
        years_raw = raw.get("forecast_years")
        years = int(years_raw) if isinstance(years_raw, int) and not isinstance(years_raw, bool) else 0
        missing = [key for key, value in (("book_value_per_share", bvps), ("roe", roe), ("cost_of_equity", cost), ("book_value_growth", growth), ("terminal_growth", terminal)) if value is None]
        reason = ""
        value: Optional[Decimal] = None
        forecast: list[dict[str, Any]] = []
        if missing:
            reason = "missing " + ", ".join(missing)
        elif not 1 <= years <= 15:
            reason = "forecast_years must be an integer from 1 to 15"
        elif cost <= terminal:
            reason = "cost_of_equity must exceed terminal_growth"
        elif min(roe, cost) <= Decimal("-0.99") or growth <= Decimal("-0.99"):
            reason = "assumptions must be greater than -99%"
        else:
            with localcontext() as ctx:
                ctx.prec = 32
                opening_book = bvps
                present_value = ZERO
                for year in range(1, years + 1):
                    residual = opening_book * (roe - cost)
                    discounted = residual / ((ONE + cost) ** year)
                    present_value += discounted
                    forecast.append({"year": year, "opening_book_per_share": _number(opening_book), "residual_income_per_share": _number(residual), "present_value": _number(discounted)})
                    opening_book *= ONE + growth
                next_residual = opening_book * (roe - cost) * (ONE + terminal)
                terminal_value = next_residual / (cost - terminal)
                discounted_terminal = terminal_value / ((ONE + cost) ** years)
                value = bvps + present_value + discounted_terminal
        residual_outputs.append({
            "name": name,
            "status": "ok" if value is not None else "not_calculable",
            "value_per_share": _number(value),
            "observed_roe": _number(observed_roe),
            "assumptions": {"roe": _number(roe), "cost_of_equity": _number(cost), "book_value_growth": _number(growth), "terminal_growth": _number(terminal), "forecast_years": years or years_raw},
            "forecast": forecast,
            "formula": "BVPS + PV[(ROE − cost_of_equity) × opening_BVPS] + PV(terminal residual income)",
            "inputs": _valuation_inputs(bvps_metric, roe_metric),
            "reason": reason,
        })
    return {
        "method_policy": "Banks use PB and residual-income valuation; industrial FCFF/FCFE DCF is disabled.",
        "book_value_per_share": bvps_metric,
        "current_pb": _metric(current_pb, "multiple", "market_price / book_value_per_share", [], reason="market price or book value per share is missing"),
        "pb": {"status": "ok" if pb_outputs else "needs_assumptions", "scenarios": pb_outputs},
        "residual_income": {"status": "ok" if any(item["status"] == "ok" for item in residual_outputs) else "needs_assumptions", "scenarios": residual_outputs},
        "dcf": {"status": "unsupported", "reason": "Generic industrial DCF is not used for deposit-taking banks."},
    }


def analyze_bank(company: CompanyData) -> dict[str, Any]:
    """Return a source-traceable bank report for one canonical company."""

    annual_periods = _annual_periods(company)
    annual_snapshots = [_snapshot(period, company.unit) for period in annual_periods]
    ttm, ttm_notes = _build_ttm(company)
    annual_payload: list[dict[str, Any]] = []
    for index, snapshot in enumerate(annual_snapshots):
        prior = annual_snapshots[index - 1] if index else None
        annual_payload.append({
            "label": snapshot.label,
            "end_date": snapshot.end_date,
            "fiscal_year": snapshot.fiscal_year,
            "construction": snapshot.construction,
            "metrics": _bank_metrics(snapshot, prior, company.unit),
        })
    latest_annual = annual_snapshots[-1] if annual_snapshots else None
    prior_annual = annual_snapshots[-2] if len(annual_snapshots) >= 2 else None
    latest_snapshot = ttm or latest_annual
    prior_prior_snapshot = annual_snapshots[-3] if len(annual_snapshots) >= 3 else None
    if ttm and latest_annual and ttm.end_date == latest_annual.end_date:
        prior_snapshot = prior_annual
    elif ttm and company.periods:
        latest_period = company.periods[-1]
        comparable = _find_comparable(company.periods, latest_period)
        if comparable:
            prior_company = CompanyData(
                symbol=company.symbol,
                name=company.name,
                industry=company.industry,
                currency=company.currency,
                unit=company.unit,
                periods=tuple(period for period in company.periods if period.end_date <= comparable.end_date),
                valuation={},
                audit={},
            )
            constructed_prior_ttm, _ = _build_ttm(prior_company)
            prior_snapshot = constructed_prior_ttm or _balance_only_snapshot(comparable, company.unit)
            prior_prior_snapshot = None
        else:
            prior_snapshot = None
            prior_prior_snapshot = None
    else:
        prior_snapshot = prior_annual
    latest_metrics = _bank_metrics(latest_snapshot, prior_snapshot, company.unit) if latest_snapshot else {}
    prior_metrics = _bank_metrics(prior_snapshot, prior_prior_snapshot, company.unit) if prior_snapshot else None
    flags = _bank_flags(latest_metrics, prior_metrics)
    covered = []
    if latest_snapshot:
        for name in BANK_REQUIRED_FACTS:
            if name == "shares_outstanding":
                available = _fact(latest_snapshot, name, "million shares").value is not None
            else:
                metric = latest_metrics.get(name)
                available = isinstance(metric, Mapping) and metric.get("status") == "ok"
            if available:
                covered.append(name)
    missing = [name for name in BANK_REQUIRED_FACTS if name not in covered]
    coverage_score = round(len(covered) / len(BANK_REQUIRED_FACTS) * 100) if BANK_REQUIRED_FACTS else 0
    market = {
        "market_price": company.valuation.get("market_price"),
        "price_as_of": company.valuation.get("price_as_of"),
        "market_cap": company.valuation.get("market_cap"),
        "shares_outstanding": company.valuation.get("shares_outstanding"),
        "pb": company.valuation.get("pb"),
        "pe": company.valuation.get("pe"),
        "dividend_yield": company.valuation.get("dividend_yield"),
        "dividend_yield_ttm": company.valuation.get("dividend_yield_ttm"),
        "source": company.valuation.get("source"),
    }
    valuation = _bank_valuation(latest_metrics, company.valuation) if latest_snapshot else {
        "pb": {"status": "missing", "scenarios": []},
        "residual_income": {"status": "missing", "scenarios": []},
        "dcf": {"status": "unsupported", "reason": "Bank model requires bank facts."},
    }
    changes = {
        "schema_version": "bank-change-detection/1.0",
        "summary": {
            "total": len(flags),
            "red": sum(item["severity"] == "red" for item in flags),
            "yellow": sum(item["severity"] == "yellow" for item in flags),
        },
        "signals": flags,
        "missing_metrics": missing,
        "unsupported_checks": ["Industrial cash conversion, net debt, inventory and generic FCF checks do not apply to banks."],
    }
    result = {
        "schema_version": BANK_SCHEMA_VERSION,
        "engine": "deterministic_decimal_bank_engine",
        "company": {"symbol": company.symbol, "name": company.name, "industry": company.industry, "currency": company.currency, "unit": company.unit},
        "market": market,
        "as_of": latest_snapshot.end_date if latest_snapshot else None,
        "support": {
            "status": "supported_bank",
            "supported": True,
            "industry": company.industry,
            "model": "bank/v1",
            "reason": "Bank-specific asset-quality, profitability, capital and PB/residual-income model enabled; industrial ratios remain disabled.",
        },
        "data_quality": {
            "score": coverage_score,
            "covered_required_facts": covered,
            "missing_required_facts": missing,
            "ttm_notes": ttm_notes,
            "warning": "Coverage is not a credit rating. Missing regulatory metrics must be filled from statutory reports or another traceable source.",
        },
        "periods": {
            "annual": annual_payload,
            "ttm": {"label": latest_snapshot.label, "end_date": latest_snapshot.end_date, "fiscal_year": latest_snapshot.fiscal_year, "construction": latest_snapshot.construction, "metrics": latest_metrics} if latest_snapshot else None,
        },
        "bank": {
            "latest": {"label": latest_snapshot.label, "end_date": latest_snapshot.end_date, "metrics": latest_metrics} if latest_snapshot else None,
            "coverage": {"score": coverage_score, "covered": covered, "missing": missing, "required": list(BANK_REQUIRED_FACTS)},
            "flags": flags,
            "methodology": {
                "ratio_convention": "decimal ratios; source facts labelled percent are divided by 100",
                "priority": "net interest margin, asset quality, provision buffer, capital adequacy, earnings and efficiency",
                "regulatory_warning": "No single hard capital threshold is used because buffers differ by bank category and designation; deterioration is a review signal, not a compliance finding.",
            },
        },
        "flags": flags,
        "changes": changes,
        "valuation": valuation,
        "piotroski": {"status": "unsupported", "reason": "Piotroski industrial-company score is not applied to banks."},
        "dupont": {"status": "unsupported", "reason": "Industrial DuPont/asset-turnover decomposition is not applied to banks."},
        "methodology": {
            "missing_vs_zero": "Missing remains null/status=missing; a reported zero is retained.",
            "period_policy": "Annual facts use reported FY; TTM flows use the shared cumulative/discrete reconstruction rules while regulatory point-in-time ratios use the latest reported period.",
            "decision_policy": "Signals prioritise review and never constitute fraud findings, credit ratings or automatic trade instructions.",
        },
    }
    return _json_safe(result)


__all__ = ["BANK_SCHEMA_VERSION", "BANK_REQUIRED_FACTS", "analyze_bank"]
