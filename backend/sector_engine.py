"""Industry-specific deterministic analysis for non-financial A-share sectors.

The first plug-ins cover commodity/resource producers, regulated/capital-
intensive utilities and property developers.  They share canonical source-labelled facts with the main
engine but deliberately replace generic conclusions where cycle position,
physical output, sustaining capital or regulated returns dominate economics.

Operational facts are never fabricated from accounting totals.  Missing
production, realised-price, cost, reserve, capacity, tariff or regulated-asset
disclosures remain explicit gaps until a statutory-report extraction or manual
source-labelled import supplies them.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Any, Iterable, Mapping, Optional

from .finance_engine import (
    CompanyData,
    DerivedFact,
    ONE,
    ZERO,
    _annual_periods,
    _build_ttm,
    _ddm,
    _decimal,
    _fact_metric,
    _json_safe,
    _metric,
    _number,
    _snapshot,
)


SECTOR_SCHEMA_VERSION = "sector-analysis/1.0"

RESOURCE_KEYWORDS = (
    "煤炭", "煤矿", "石油", "天然气", "油气", "有色", "金属", "矿业", "采掘",
    "钢铁", "黄金", "铜业", "铝业", "锂", "稀土", "资源", "mining", "coal",
    "oil & gas", "oil and gas", "metals", "steel", "gold", "copper", "aluminium",
)
UTILITY_KEYWORDS = (
    "公用事业", "电力", "发电", "水务", "燃气", "热力", "核电", "水电", "火电",
    "utility", "utilities", "electric power", "power generation", "water utility", "gas utility",
)
REAL_ESTATE_KEYWORDS = (
    "房地产", "房地产开发", "地产开发", "房产开发", "物业开发", "住宅开发",
    "property developer", "real estate developer", "property development",
)

TEMPLATES = {
    "cyclical_resource": {
        "label": "周期资源",
        "required": (
            "production_volume", "realized_price", "unit_cash_cost", "proven_probable_reserves",
            "sustaining_capex", "operating_cash_flow", "interest_bearing_debt", "cash", "shares_outstanding",
        ),
    },
    "utility": {
        "label": "公用事业",
        "required": (
            "installed_capacity", "utilization_hours", "average_tariff", "fuel_cost_per_kwh",
            "sustaining_capex", "operating_cash_flow", "interest_bearing_debt", "cash", "shares_outstanding",
        ),
    },
    "real_estate": {
        "label": "房地产开发",
        "required": (
            "contract_liabilities", "sales_collections", "presales", "inventory",
            "project_inventory_impairment", "cash", "restricted_cash", "interest_bearing_debt",
            "debt_due_within_one_year", "total_equity", "shares_outstanding",
        ),
    },
}


def classify_sector_template(company: CompanyData) -> Optional[str]:
    explicit = str(company.valuation.get("sector_template") or "").strip().lower()
    if explicit in TEMPLATES:
        return explicit
    industry = company.industry.strip().lower()
    if any(keyword in industry for keyword in RESOURCE_KEYWORDS):
        return "cyclical_resource"
    if any(keyword in industry for keyword in UTILITY_KEYWORDS):
        return "utility"
    if any(keyword in industry for keyword in REAL_ESTATE_KEYWORDS):
        return "real_estate"
    return None


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


def _growth(current: DerivedFact, prior: Optional[DerivedFact], name: str) -> dict[str, Any]:
    formula = f"{name}(current) / {name}(prior) - 1"
    if prior is None or current.value is None or prior.value is None:
        return _metric(None, "ratio", formula, [current, prior], reason="current or comparable prior fact is missing")
    if current.unit != prior.unit:
        return _metric(None, "ratio", formula, [current, prior], status="not_calculable", reason="source units do not match")
    if prior.value == ZERO:
        return _metric(None, "ratio", formula, [current, prior], status="not_calculable", reason="prior value is zero")
    return _metric(current.value / prior.value - ONE, "ratio", formula, [current, prior])


def _ratio(numerator: DerivedFact, denominator: DerivedFact, formula: str, unit: str = "ratio") -> dict[str, Any]:
    if numerator.value is None or denominator.value is None:
        return _metric(None, unit, formula, [numerator, denominator], reason="required source fact is missing")
    if numerator.unit != denominator.unit:
        return _metric(None, unit, formula, [numerator, denominator], status="not_calculable", reason=f"source unit mismatch: {numerator.unit} vs {denominator.unit}")
    if denominator.value == ZERO:
        return _metric(None, unit, formula, [numerator, denominator], status="not_calculable", reason="reported denominator is zero")
    return _metric(numerator.value / denominator.value, unit, formula, [numerator, denominator])


def _difference(left: DerivedFact, right: DerivedFact, formula: str, unit: Optional[str] = None) -> dict[str, Any]:
    if left.value is None or right.value is None:
        return _metric(None, unit or left.unit, formula, [left, right], reason="required source fact is missing")
    if left.unit != right.unit:
        return _metric(None, unit or left.unit, formula, [left, right], status="not_calculable", reason=f"source unit mismatch: {left.unit} vs {right.unit}")
    return _metric(left.value - right.value, unit or left.unit, formula, [left, right])


def _product(left: DerivedFact, right: DerivedFact, formula: str, unit: str) -> dict[str, Any]:
    if left.value is None or right.value is None:
        return _metric(None, unit, formula, [left, right], reason="required source fact is missing")
    return _metric(left.value * right.value, unit, formula, [left, right])


def _metric_value(metrics: Mapping[str, Any], name: str) -> Optional[Decimal]:
    item = metrics.get(name)
    if not isinstance(item, Mapping) or item.get("status") != "ok":
        return None
    return _decimal(item.get("value"), name)


def _metric_fact(metric: Mapping[str, Any], name: str, unit: str) -> DerivedFact:
    value = _decimal(metric.get("value"), name) if metric.get("status") == "ok" else None
    return DerivedFact(
        value,
        str(metric.get("formula") or name),
        tuple(metric.get("inputs") or ()),
        str(metric.get("unit") or unit),
        str(metric.get("reason") or ("metric is unavailable" if value is None else "")),
    )


def _resource_metrics(snapshot: Any, prior: Optional[Any], unit: str) -> dict[str, Any]:
    production = _fact(snapshot, "production_volume", "physical unit")
    price = _fact(snapshot, "realized_price", "CNY/physical unit")
    cost = _fact(snapshot, "unit_cash_cost", "CNY/physical unit")
    reserves = _fact(snapshot, "proven_probable_reserves", production.unit)
    ocf = _fact(snapshot, "operating_cash_flow", unit)
    sustaining = _fact(snapshot, "sustaining_capex", unit)
    total_capex = _fact(snapshot, "capital_expenditure", unit)
    debt = _fact(snapshot, "interest_bearing_debt", unit)
    cash = _fact(snapshot, "cash", unit)
    margin = _difference(price, cost, "realized_price - unit_cash_cost")
    margin_fact = DerivedFact(_decimal(margin.get("value"), "cash_margin_per_unit") if margin.get("status") == "ok" else None, str(margin.get("formula")), tuple(margin.get("inputs") or ()), str(margin.get("unit") or price.unit), str(margin.get("reason") or ""))
    return {
        "production_volume": _fact_metric(production),
        "production_growth": _growth(production, _fact(prior, "production_volume", production.unit) if prior else None, "production_volume"),
        "realized_price": _fact_metric(price),
        "realized_price_growth": _growth(price, _fact(prior, "realized_price", price.unit) if prior else None, "realized_price"),
        "unit_cash_cost": _fact_metric(cost),
        "unit_cash_cost_growth": _growth(cost, _fact(prior, "unit_cash_cost", cost.unit) if prior else None, "unit_cash_cost"),
        "cash_margin_per_unit": margin,
        "cash_margin_ratio": _ratio(margin_fact, price, "(realized_price - unit_cash_cost) / realized_price"),
        "reserve_life_years": _ratio(reserves, production, "proven_probable_reserves / annual_production", "years"),
        "sustaining_capex": _fact_metric(sustaining),
        "total_capex": _fact_metric(total_capex),
        "sustaining_capex_coverage": _ratio(ocf, sustaining, "operating_cash_flow / sustaining_capex"),
        "cash_after_sustaining_capex": _difference(ocf, sustaining, "operating_cash_flow - sustaining_capex", unit),
        "net_debt": _difference(debt, cash, "interest_bearing_debt - cash", unit),
        "revenue": _fact_metric(_fact(snapshot, "revenue", unit)),
        "net_income": _fact_metric(_fact(snapshot, "net_income", unit)),
        "operating_cash_flow": _fact_metric(ocf),
    }


def _utility_metrics(snapshot: Any, prior: Optional[Any], unit: str) -> dict[str, Any]:
    capacity = _fact(snapshot, "installed_capacity", "MW")
    hours = _fact(snapshot, "utilization_hours", "hours")
    tariff = _fact(snapshot, "average_tariff", "CNY/kWh")
    fuel = _fact(snapshot, "fuel_cost_per_kwh", "CNY/kWh")
    rab = _fact(snapshot, "regulated_asset_base", unit)
    allowed_return = _fact(snapshot, "allowed_return", "ratio")
    ocf = _fact(snapshot, "operating_cash_flow", unit)
    sustaining = _fact(snapshot, "sustaining_capex", unit)
    debt = _fact(snapshot, "interest_bearing_debt", unit)
    cash = _fact(snapshot, "cash", unit)
    equity = _fact(snapshot, "total_equity", unit)
    generation = _product(capacity, hours, "installed_capacity × utilization_hours", "MWh")
    return {
        "installed_capacity": _fact_metric(capacity),
        "capacity_growth": _growth(capacity, _fact(prior, "installed_capacity", capacity.unit) if prior else None, "installed_capacity"),
        "utilization_hours": _fact_metric(hours),
        "utilization_hours_growth": _growth(hours, _fact(prior, "utilization_hours", hours.unit) if prior else None, "utilization_hours"),
        "estimated_generation": generation,
        "average_tariff": _fact_metric(tariff),
        "fuel_cost_per_kwh": _fact_metric(fuel),
        "tariff_fuel_spread": _difference(tariff, fuel, "average_tariff - fuel_cost_per_kwh"),
        "regulated_asset_base": _fact_metric(rab),
        "allowed_return": _reported_ratio(snapshot, "allowed_return"),
        "sustaining_capex": _fact_metric(sustaining),
        "sustaining_capex_coverage": _ratio(ocf, sustaining, "operating_cash_flow / sustaining_capex"),
        "cash_after_sustaining_capex": _difference(ocf, sustaining, "operating_cash_flow - sustaining_capex", unit),
        "net_debt": _difference(debt, cash, "interest_bearing_debt - cash", unit),
        "debt_to_equity": _ratio(debt, equity, "interest_bearing_debt / total_equity"),
        "revenue": _fact_metric(_fact(snapshot, "revenue", unit)),
        "net_income": _fact_metric(_fact(snapshot, "net_income", unit)),
        "operating_cash_flow": _fact_metric(ocf),
    }


def _real_estate_metrics(snapshot: Any, prior: Optional[Any], unit: str) -> dict[str, Any]:
    contract = _fact(snapshot, "contract_liabilities", unit)
    collections = _fact(snapshot, "sales_collections", unit)
    presales = _fact(snapshot, "presales", unit)
    inventory = _fact(snapshot, "inventory", unit)
    impairment = _fact(snapshot, "project_inventory_impairment", unit)
    assets = _fact(snapshot, "total_assets", unit)
    revenue = _fact(snapshot, "revenue", unit)
    gross_profit = _fact(snapshot, "gross_profit", unit)
    ocf = _fact(snapshot, "operating_cash_flow", unit)
    debt = _fact(snapshot, "interest_bearing_debt", unit)
    short_debt = _fact(snapshot, "debt_due_within_one_year", unit)
    cash = _fact(snapshot, "cash", unit)
    restricted = _fact(snapshot, "restricted_cash", unit)
    equity = _fact(snapshot, "total_equity", unit)
    usable_cash_metric = _difference(cash, restricted, "cash - restricted_cash", unit)
    usable_cash = _metric_fact(usable_cash_metric, "usable_cash", unit)
    adjusted_net_debt = _difference(debt, usable_cash, "interest_bearing_debt - (cash - restricted_cash)", unit)
    return {
        "contract_liabilities": _fact_metric(contract),
        "contract_liability_growth": _growth(contract, _fact(prior, "contract_liabilities", unit) if prior else None, "contract_liabilities"),
        "contract_liabilities_to_revenue": _ratio(contract, revenue, "contract_liabilities / annual_revenue"),
        "sales_collections": _fact_metric(collections),
        "sales_collection_growth": _growth(collections, _fact(prior, "sales_collections", unit) if prior else None, "sales_collections"),
        "sales_collections_to_revenue": _ratio(collections, revenue, "sales_collections / annual_revenue"),
        "presales": _fact_metric(presales),
        "presales_growth": _growth(presales, _fact(prior, "presales", unit) if prior else None, "presales"),
        "inventory": _fact_metric(inventory),
        "inventory_to_assets": _ratio(inventory, assets, "project_inventory / total_assets"),
        "project_inventory_impairment": _fact_metric(impairment),
        "inventory_impairment_ratio": _ratio(impairment, inventory, "project_inventory_impairment / project_inventory"),
        "cash": _fact_metric(cash),
        "restricted_cash": _fact_metric(restricted),
        "restricted_cash_ratio": _ratio(restricted, cash, "restricted_cash / cash"),
        "usable_cash": usable_cash_metric,
        "debt_due_within_one_year": _fact_metric(short_debt),
        "short_debt_cash_coverage": _ratio(usable_cash, short_debt, "(cash - restricted_cash) / debt_due_within_one_year"),
        "interest_bearing_debt": _fact_metric(debt),
        "adjusted_net_debt": adjusted_net_debt,
        "debt_to_equity": _ratio(debt, equity, "interest_bearing_debt / total_equity"),
        "operating_cash_flow": _fact_metric(ocf),
        "operating_cash_flow_growth": _growth(ocf, _fact(prior, "operating_cash_flow", unit) if prior else None, "operating_cash_flow"),
        "revenue": _fact_metric(revenue),
        "gross_margin": _ratio(gross_profit, revenue, "gross_profit / revenue"),
        "net_income": _fact_metric(_fact(snapshot, "net_income", unit)),
        "external_guarantees": _fact_metric(_fact(snapshot, "external_guarantees", unit)),
    }


def _flag(flag_id: str, severity: str, title: str, detail: str, metrics: Mapping[str, Any], names: Iterable[str], review: str) -> dict[str, Any]:
    selected = {name: metrics.get(name) for name in names}
    inputs = [line for item in selected.values() if isinstance(item, Mapping) for line in item.get("inputs", [])]
    return {"id": flag_id, "severity": severity, "title": title, "detail": detail, "metrics": selected, "inputs": inputs, "review_condition": review, "classification": "deterministic_review_signal_not_trade_call"}


def _resource_flags(current: Mapping[str, Any], prior: Optional[Mapping[str, Any]]) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    price_growth = _metric_value(current, "realized_price_growth")
    cost_growth = _metric_value(current, "unit_cash_cost_growth")
    production_growth = _metric_value(current, "production_growth")
    reserve_life = _metric_value(current, "reserve_life_years")
    coverage = _metric_value(current, "sustaining_capex_coverage")
    if price_growth is not None and cost_growth is not None and price_growth < ZERO < cost_growth:
        flags.append(_flag("resource_price_cost_squeeze", "red" if cost_growth - price_growth >= Decimal("0.15") else "yellow", "价格下行、成本上升", f"实现价格增速 {_number(price_growth)}，单位现金成本增速 {_number(cost_growth)}。", current, ("realized_price_growth", "unit_cash_cost_growth", "cash_margin_ratio"), "核对品位、剥采比、能源人工成本、长协比例与套保影响。"))
    if production_growth is not None and production_growth <= Decimal("-0.10"):
        flags.append(_flag("resource_production_decline", "yellow", "产量明显下降", f"可比口径产量下降 {_number(-production_growth * Decimal('100'))}%。", current, ("production_volume", "production_growth"), "核对检修、安全环保、矿权、品位和产能利用率。"))
    if reserve_life is not None and reserve_life < Decimal("8"):
        flags.append(_flag("resource_short_reserve_life", "yellow", "储量寿命偏短", f"按当前年产量测算储量寿命约 {_number(reserve_life)} 年。", current, ("reserve_life_years",), "核对储量口径、经济可采比例、扩储投入与矿权到期时间。"))
    if coverage is not None and coverage < ONE:
        flags.append(_flag("resource_sustaining_capex_not_covered", "red", "经营现金流不足以覆盖维持性资本开支", f"覆盖倍数 {_number(coverage)}。", current, ("sustaining_capex_coverage", "cash_after_sustaining_capex"), "区分周期低点、营运资本波动与持续性现金缺口。"))
    return flags


def _utility_flags(current: Mapping[str, Any], prior: Optional[Mapping[str, Any]]) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    hours_growth = _metric_value(current, "utilization_hours_growth")
    spread = _metric_value(current, "tariff_fuel_spread")
    coverage = _metric_value(current, "sustaining_capex_coverage")
    debt_to_equity = _metric_value(current, "debt_to_equity")
    if hours_growth is not None and hours_growth <= Decimal("-0.10"):
        flags.append(_flag("utility_utilization_decline", "yellow", "利用小时下降", f"可比口径利用小时下降 {_number(-hours_growth * Decimal('100'))}%。", current, ("utilization_hours", "utilization_hours_growth"), "核对来水、风光资源、机组检修、消纳和市场化交易电量。"))
    if spread is not None and spread <= ZERO:
        flags.append(_flag("utility_negative_fuel_spread", "red", "电价未覆盖单位燃料成本", f"电价燃料价差为 {_number(spread)}。", current, ("average_tariff", "fuel_cost_per_kwh", "tariff_fuel_spread"), "核对煤价、气价、容量电价、辅助服务和成本传导机制。"))
    if coverage is not None and coverage < ONE:
        flags.append(_flag("utility_sustaining_capex_not_covered", "yellow", "维持性资本开支覆盖不足", f"覆盖倍数 {_number(coverage)}。", current, ("sustaining_capex_coverage", "cash_after_sustaining_capex"), "拆分维持性与扩张性资本开支，并核对电价回收周期。"))
    if debt_to_equity is not None and debt_to_equity > Decimal("2"):
        flags.append(_flag("utility_high_financial_leverage", "yellow", "有息负债相对权益偏高", f"有息负债/权益 {_number(debt_to_equity)} 倍。", current, ("debt_to_equity", "net_debt"), "核对利率、债务期限、项目现金流锁定和资本金补充安排。"))
    return flags


def _real_estate_flags(current: Mapping[str, Any], prior: Optional[Mapping[str, Any]]) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    contract_growth = _metric_value(current, "contract_liability_growth")
    collection_growth = _metric_value(current, "sales_collection_growth")
    presales_growth = _metric_value(current, "presales_growth")
    restricted_ratio = _metric_value(current, "restricted_cash_ratio")
    short_cover = _metric_value(current, "short_debt_cash_coverage")
    impairment_ratio = _metric_value(current, "inventory_impairment_ratio")
    debt_to_equity = _metric_value(current, "debt_to_equity")
    ocf = _metric_value(current, "operating_cash_flow")
    if contract_growth is not None and contract_growth <= Decimal("-0.20"):
        severity = "red" if collection_growth is not None and collection_growth < ZERO else "yellow"
        flags.append(_flag("property_contract_pipeline_shrink", severity, "合同负债明显收缩", f"合同负债同比下降 {_number(-contract_growth * Decimal('100'))}%。", current, ("contract_liabilities", "contract_liability_growth", "sales_collection_growth"), "核对预售结转、退房、项目去化、并表范围和新增土储是否同步收缩。"))
    if presales_growth is not None and presales_growth <= Decimal("-0.20"):
        flags.append(_flag("property_presales_decline", "yellow", "预售金额明显下降", f"可比口径预售下降 {_number(-presales_growth * Decimal('100'))}%。", current, ("presales", "presales_growth", "sales_collections"), "核对推盘节奏、权益销售口径、去化率和回款率。"))
    if short_cover is not None and short_cover < ONE:
        flags.append(_flag("property_short_debt_cash_gap", "red", "可用现金不足以覆盖一年内债务", f"剔除受限资金后的短债覆盖倍数为 {_number(short_cover)}。", current, ("usable_cash", "debt_due_within_one_year", "short_debt_cash_coverage"), "核对预售监管资金可动用条件、债务到期表、再融资安排与项目层资金归集限制。"))
    if restricted_ratio is not None and restricted_ratio >= Decimal("0.30"):
        flags.append(_flag("property_restricted_cash_high", "yellow", "受限资金占比较高", f"受限资金占账面现金 {_number(restricted_ratio * Decimal('100'))}%。", current, ("cash", "restricted_cash", "restricted_cash_ratio"), "核对预售监管、按揭保证金、质押与冻结资金的用途和解限时点。"))
    if impairment_ratio is not None and impairment_ratio >= Decimal("0.02"):
        severity = "red" if impairment_ratio >= Decimal("0.05") else "yellow"
        flags.append(_flag("property_inventory_impairment", severity, "项目存货减值压力", f"当期项目存货减值/期末存货为 {_number(impairment_ratio * Decimal('100'))}%。", current, ("project_inventory_impairment", "inventory", "inventory_impairment_ratio"), "核对城市能级、项目售价与成本、完工进度、可变现净值假设和减值转回。"))
    if debt_to_equity is not None and debt_to_equity > Decimal("1.5"):
        flags.append(_flag("property_high_leverage", "yellow", "有息负债相对权益偏高", f"有息负债/权益 {_number(debt_to_equity)} 倍。", current, ("interest_bearing_debt", "debt_to_equity", "adjusted_net_debt"), "核对少数股东权益、明股实债、永续债、合联营项目负债和表外担保。"))
    if ocf is not None and ocf < ZERO and short_cover is not None and short_cover < ONE:
        flags.append(_flag("property_cashflow_liquidity_stress", "red", "经营现金流与短债覆盖同时承压", f"经营现金流 {_number(ocf)}，短债覆盖 {_number(short_cover)} 倍。", current, ("operating_cash_flow", "short_debt_cash_coverage", "adjusted_net_debt"), "区分拿地支出与销售回款恶化，并复核未来十二个月偿债来源。"))
    return flags


def _scenario_decimal(raw: Mapping[str, Any], name: str) -> Optional[Decimal]:
    return _decimal(raw.get(name), name)


def _resource_valuation(snapshot: Any, metrics: Mapping[str, Any], valuation: Mapping[str, Any], unit: str) -> dict[str, Any]:
    config = valuation.get("sector") if isinstance(valuation.get("sector"), Mapping) else {}
    raw_scenarios = config.get("scenarios") if isinstance(config, Mapping) else []
    shares = _fact(snapshot, "shares_outstanding", "million shares")
    net_debt_metric = metrics.get("net_debt") if isinstance(metrics.get("net_debt"), Mapping) else {}
    derived_net_debt = _metric_value(metrics, "net_debt")
    outputs = []
    for raw in raw_scenarios if isinstance(raw_scenarios, list) else []:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or f"scenario_{len(outputs) + 1}")
        volume = _scenario_decimal(raw, "normalized_volume")
        price = _scenario_decimal(raw, "normalized_price")
        cost = _scenario_decimal(raw, "unit_cash_cost")
        tax = _scenario_decimal(raw, "tax_rate")
        target_pe = _scenario_decimal(raw, "target_pe")
        reserves = _scenario_decimal(raw, "recoverable_reserves")
        recovery = _scenario_decimal(raw, "recovery_factor")
        discount = _scenario_decimal(raw, "discount_rate")
        years_raw = raw.get("mine_life_years")
        years = int(years_raw) if isinstance(years_raw, int) and not isinstance(years_raw, bool) else 0
        net_debt = _decimal(raw.get("net_debt"), "net_debt") if raw.get("net_debt") not in (None, "") else derived_net_debt
        missing_mid = [key for key, value in (("normalized_volume", volume), ("normalized_price", price), ("unit_cash_cost", cost), ("tax_rate", tax), ("target_pe", target_pe), ("shares", shares.value), ("net_debt", net_debt)) if value is None]
        mid_value = None
        mid_reason = ""
        if missing_mid:
            mid_reason = "missing " + ", ".join(missing_mid)
        elif price <= cost or not ZERO <= tax < ONE or target_pe <= ZERO or shares.value == ZERO:
            mid_reason = "price must exceed cash cost; tax must be 0–1; PE and shares must be positive"
        else:
            normalized_earnings = volume * (price - cost) * (ONE - tax)
            mid_value = (normalized_earnings * target_pe - net_debt) / shares.value
        missing_nav = [key for key, value in (("recoverable_reserves", reserves), ("normalized_price", price), ("unit_cash_cost", cost), ("tax_rate", tax), ("recovery_factor", recovery), ("discount_rate", discount), ("shares", shares.value), ("net_debt", net_debt)) if value is None]
        nav_value = None
        nav_reason = ""
        if missing_nav:
            nav_reason = "missing " + ", ".join(missing_nav)
        elif not 1 <= years <= 60:
            nav_reason = "mine_life_years must be an integer from 1 to 60"
        elif price <= cost or not ZERO <= tax < ONE or not ZERO < recovery <= ONE or discount <= ZERO or shares.value == ZERO:
            nav_reason = "NAV assumptions are outside allowed economic ranges"
        else:
            with localcontext() as ctx:
                ctx.prec = 32
                annual_volume = reserves * recovery / years
                annual_cash = annual_volume * (price - cost) * (ONE - tax)
                annuity = sum((annual_cash / ((ONE + discount) ** year) for year in range(1, years + 1)), ZERO)
                nav_value = (annuity - net_debt) / shares.value
        outputs.append({
            "name": name,
            "midcycle": {"status": "ok" if mid_value is not None else "not_calculable", "value_per_share": _number(mid_value), "formula": "[normalized_volume × (normalized_price − unit_cash_cost) × (1 − tax_rate) × target_PE − net_debt] / shares", "reason": mid_reason},
            "nav": {"status": "ok" if nav_value is not None else "not_calculable", "value_per_share": _number(nav_value), "formula": "PV[recoverable_reserves × recovery_factor / mine_life × unit_cash_margin × (1 − tax)] − net_debt, divided by shares", "reason": nav_reason},
            "assumptions": {key: raw.get(key) for key in raw},
            "inputs": [line for item in (net_debt_metric, _fact_metric(shares)) if isinstance(item, Mapping) for line in item.get("inputs", [])],
        })
    return {"method_policy": "Use normalized mid-cycle economics and reserve NAV; peak/trough spot PE is not treated as fair value.", "scenarios": outputs, "dcf": {"status": "unsupported", "reason": "Generic industrial DCF is replaced by cycle-normalized earnings and reserve NAV."}}


def _utility_valuation(snapshot: Any, metrics: Mapping[str, Any], valuation: Mapping[str, Any], unit: str, ttm: Optional[Any]) -> dict[str, Any]:
    config = valuation.get("sector") if isinstance(valuation.get("sector"), Mapping) else {}
    raw_scenarios = config.get("scenarios") if isinstance(config, Mapping) else []
    shares = _fact(snapshot, "shares_outstanding", "million shares")
    derived_net_debt = _metric_value(metrics, "net_debt")
    outputs = []
    for raw in raw_scenarios if isinstance(raw_scenarios, list) else []:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or f"scenario_{len(outputs) + 1}")
        rab = _scenario_decimal(raw, "normalized_rab")
        allowed = _scenario_decimal(raw, "allowed_return")
        tax = _scenario_decimal(raw, "tax_rate")
        target_pe = _scenario_decimal(raw, "target_pe")
        net_debt = _decimal(raw.get("net_debt"), "net_debt") if raw.get("net_debt") not in (None, "") else derived_net_debt
        missing = [key for key, value in (("normalized_rab", rab), ("allowed_return", allowed), ("tax_rate", tax), ("target_pe", target_pe), ("shares", shares.value), ("net_debt", net_debt)) if value is None]
        value = None
        reason = ""
        if missing:
            reason = "missing " + ", ".join(missing)
        elif rab < ZERO or allowed <= ZERO or not ZERO <= tax < ONE or target_pe <= ZERO or shares.value == ZERO:
            reason = "regulated-return assumptions are outside allowed ranges"
        else:
            normalized_earnings = rab * allowed * (ONE - tax)
            value = (normalized_earnings * target_pe - net_debt) / shares.value
        outputs.append({"name": name, "status": "ok" if value is not None else "not_calculable", "value_per_share": _number(value), "formula": "[normalized_RAB × allowed_return × (1 − tax_rate) × target_PE − net_debt] / shares", "assumptions": {key: raw.get(key) for key in raw}, "reason": reason, "inputs": _fact_metric(shares).get("inputs", [])})
    return {"method_policy": "Regulated-asset earnings power and DDM are shown separately; expansion capex is not silently treated as maintenance.", "regulated_return": {"status": "ok" if any(item["status"] == "ok" for item in outputs) else "needs_assumptions", "scenarios": outputs}, "ddm": _ddm(ttm, valuation, unit), "dcf": {"status": "unsupported", "reason": "Generic DCF is disabled until maintenance/growth capex and regulatory recovery are separated."}}


def _real_estate_valuation(snapshot: Any, metrics: Mapping[str, Any], valuation: Mapping[str, Any], unit: str) -> dict[str, Any]:
    config = valuation.get("sector") if isinstance(valuation.get("sector"), Mapping) else {}
    raw_scenarios = config.get("scenarios") if isinstance(config, Mapping) else []
    equity = _fact(snapshot, "total_equity", unit)
    inventory = _fact(snapshot, "inventory", unit)
    contract = _fact(snapshot, "contract_liabilities", unit)
    shares = _fact(snapshot, "shares_outstanding", "million shares")
    source_inputs = [line for item in (_fact_metric(equity), _fact_metric(inventory), _fact_metric(contract), _fact_metric(shares)) for line in item.get("inputs", [])]
    outputs = []
    for raw in raw_scenarios if isinstance(raw_scenarios, list) else []:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or f"scenario_{len(outputs) + 1}")
        inventory_haircut = _scenario_decimal(raw, "inventory_haircut")
        recognition = _scenario_decimal(raw, "contract_recognition_rate")
        gross_margin = _scenario_decimal(raw, "normalized_gross_margin")
        tax = _scenario_decimal(raw, "tax_rate")
        target_pb = _scenario_decimal(raw, "target_pb")
        additional_loss = _scenario_decimal(raw, "additional_loss_provision")
        if additional_loss is None:
            additional_loss = ZERO
        missing = [key for key, value in (("total_equity", equity.value), ("inventory", inventory.value), ("contract_liabilities", contract.value), ("shares", shares.value), ("inventory_haircut", inventory_haircut), ("contract_recognition_rate", recognition), ("normalized_gross_margin", gross_margin), ("tax_rate", tax), ("target_pb", target_pb)) if value is None]
        adjusted_nav = None
        adjusted_pb = None
        reason = ""
        if missing:
            reason = "missing " + ", ".join(missing)
        elif shares.value == ZERO or any(value < ZERO or value > ONE for value in (inventory_haircut, recognition, gross_margin, tax)) or target_pb <= ZERO or additional_loss < ZERO:
            reason = "haircuts, recognition, margin and tax must be 0–1; target PB and shares must be positive; additional loss cannot be negative"
        else:
            inventory_loss = inventory.value * inventory_haircut
            contracted_profit = contract.value * recognition * gross_margin * (ONE - tax)
            adjusted_equity = equity.value - inventory_loss - additional_loss
            adjusted_nav = (adjusted_equity + contracted_profit) / shares.value
            adjusted_pb = adjusted_equity / shares.value * target_pb
        outputs.append({
            "name": name,
            "adjusted_nav": {"status": "ok" if adjusted_nav is not None else "not_calculable", "value_per_share": _number(adjusted_nav), "formula": "[equity − inventory × haircut − additional_loss + contract_liabilities × recognition × normalized_gross_margin × (1 − tax)] / shares", "reason": reason},
            "adjusted_pb": {"status": "ok" if adjusted_pb is not None else "not_calculable", "value_per_share": _number(adjusted_pb), "formula": "[(equity − inventory × haircut − additional_loss) / shares] × target_PB", "reason": reason},
            "assumptions": {key: raw.get(key) for key in raw},
            "inputs": source_inputs,
        })
    return {
        "method_policy": "Property value is shown as source-linked adjusted NAV and adjusted-book PB. Inventory haircuts and contracted-profit recognition are explicit assumptions, not hidden forecasts.",
        "scenarios": outputs,
        "dcf": {"status": "unsupported", "reason": "Generic industrial DCF is disabled because presales, project completion, restricted cash and land/project inventory require a project-level schedule."},
    }


def analyze_sector_company(company: CompanyData, template: str) -> dict[str, Any]:
    if template not in TEMPLATES:
        raise ValueError(f"unsupported sector template: {template}")
    annual_snapshots = [_snapshot(period, company.unit) for period in _annual_periods(company)]
    ttm, ttm_notes = _build_ttm(company)
    metric_builders = {
        "cyclical_resource": _resource_metrics,
        "utility": _utility_metrics,
        "real_estate": _real_estate_metrics,
    }
    metric_builder = metric_builders[template]
    annual_payload = []
    for index, snapshot in enumerate(annual_snapshots):
        prior = annual_snapshots[index - 1] if index else None
        annual_payload.append({"label": snapshot.label, "end_date": snapshot.end_date, "fiscal_year": snapshot.fiscal_year, "construction": snapshot.construction, "metrics": metric_builder(snapshot, prior, company.unit)})
    latest = annual_snapshots[-1] if annual_snapshots else (_snapshot(company.periods[-1], company.unit) if company.periods else None)
    prior = annual_snapshots[-2] if len(annual_snapshots) >= 2 else None
    latest_metrics = metric_builder(latest, prior, company.unit) if latest else {}
    prior_metrics = metric_builder(prior, annual_snapshots[-3] if len(annual_snapshots) >= 3 else None, company.unit) if prior else None
    flag_builders = {
        "cyclical_resource": _resource_flags,
        "utility": _utility_flags,
        "real_estate": _real_estate_flags,
    }
    flags = flag_builders[template](latest_metrics, prior_metrics) if latest else []
    required = TEMPLATES[template]["required"]
    covered = [name for name in required if latest and _fact(latest, name, company.unit).value is not None]
    missing = [name for name in required if name not in covered]
    score = round(len(covered) / len(required) * 100) if required else 0
    market = {key: company.valuation.get(key) for key in ("market_price", "price_as_of", "market_cap", "shares_outstanding", "pe", "pe_ttm", "pb", "dividend_yield", "dividend_yield_ttm", "source")}
    if latest:
        if template == "cyclical_resource":
            valuation = _resource_valuation(latest, latest_metrics, company.valuation, company.unit)
        elif template == "utility":
            valuation = _utility_valuation(latest, latest_metrics, company.valuation, company.unit, ttm)
        else:
            valuation = _real_estate_valuation(latest, latest_metrics, company.valuation, company.unit)
    else:
        valuation = {"status": "missing", "reason": "No financial period supplied."}
    changes = {"schema_version": f"{template}-change-detection/1.0", "summary": {"total": len(flags), "red": sum(item["severity"] == "red" for item in flags), "yellow": sum(item["severity"] == "yellow" for item in flags)}, "signals": flags, "missing_metrics": missing, "unsupported_checks": ["Generic all-industry conclusions are disabled where sector economics require operational disclosures."]}
    result = {
        "schema_version": SECTOR_SCHEMA_VERSION,
        "engine": "deterministic_sector_engine",
        "company": {"symbol": company.symbol, "name": company.name, "industry": company.industry, "currency": company.currency, "unit": company.unit},
        "market": market,
        "as_of": latest.end_date if latest else None,
        "support": {"status": "supported_sector", "supported": True, "industry": company.industry, "model": f"{template}/1.0", "sector_template": template, "label": TEMPLATES[template]["label"], "reason": "Sector-specific operating, risk and valuation model enabled; unsupported generic conclusions are disabled."},
        "data_quality": {"score": score, "covered_required_facts": covered, "missing_required_facts": missing, "ttm_notes": ttm_notes, "warning": "Coverage measures source availability, not investment quality. Missing operational disclosures remain unknown."},
        "periods": {"annual": annual_payload, "ttm": {"label": "LATEST_SECTOR_PERIOD", "end_date": latest.end_date, "fiscal_year": latest.fiscal_year, "construction": "sector operating metrics use latest comparable reported period", "metrics": latest_metrics} if latest else None},
        "sector": {"template": template, "label": TEMPLATES[template]["label"], "latest": {"end_date": latest.end_date, "metrics": latest_metrics} if latest else None, "coverage": {"score": score, "required": list(required), "covered": covered, "missing": missing}, "flags": flags},
        "flags": flags,
        "changes": changes,
        "valuation": valuation,
        "piotroski": {"status": "unsupported", "reason": "A single generic score is not used for sector templates."},
        "dupont": {"status": "unsupported", "reason": "Sector operating drivers are shown instead of generic asset-turnover decomposition."},
        "methodology": {"missing_vs_zero": "Missing remains null/status=missing; reported zero remains numeric zero.", "period_policy": "Operational KPIs use comparable reported periods; financial TTM reconstruction is not used to invent annualised physical KPIs.", "decision_policy": "Flags prioritise review and are not buy/sell instructions."},
    }
    return _json_safe(result)


__all__ = ["SECTOR_SCHEMA_VERSION", "TEMPLATES", "classify_sector_template", "analyze_sector_company"]
