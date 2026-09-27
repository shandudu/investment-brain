"""Deterministic financial statement analysis for the Investment Brain.

The module deliberately does not fetch data and does not ask an LLM to calculate
anything.  It accepts canonical, dict-friendly facts, performs all arithmetic with
``Decimal``, and returns JSON-serialisable evidence objects.  AI providers can then
explain the result without being trusted with the accounting arithmetic.

Canonical input (abridged)::

    {
      "company": {"symbol": "600000", "name": "...", "industry": "Machinery"},
      "periods": [{
        "end_date": "2025-12-31", "fiscal_year": 2025,
        "period_type": "FY", "duration_months": 12, "cumulative": True,
        "audited": True,
        "facts": {
          "revenue": {"value": "100", "unit": "CNY million",
                      "source": {"document": "2025 annual report", "page": 80}},
          "net_income": "10"
        }
      }],
      "valuation": { ... }
    }

Zero is a valid value.  Missing values must be omitted or supplied as ``None``;
they never silently become zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Iterable, Mapping, Optional

from .financial_changes import detect_financial_changes


SCHEMA_VERSION = "1.0"
ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
DAYS_PER_YEAR = Decimal("365")


FLOW_FACTS = frozenset(
    {
        "revenue",
        "cost_of_revenue",
        "gross_profit",
        "operating_profit",
        "ebit",
        "pretax_income",
        "income_tax_expense",
        "net_income",
        "operating_cash_flow",
        "capital_expenditure",
        "depreciation_amortization",
        "change_in_working_capital",
        "dividends_paid",
        "interest_expense",
        "nonrecurring_income",
        "free_cash_flow_to_firm",
        "free_cash_flow_to_equity",
        "interest_income",
        "net_interest_income",
        "fee_commission_income",
        "fee_commission_expense",
        "net_fee_commission_income",
        "credit_impairment_loss",
        "asset_impairment_loss",
        "loan_impairment_loss",
        "operating_expense",
        "sustaining_capex",
        "expansion_capex",
        "sales_collections",
        "presales",
        "project_inventory_impairment",
    }
)

STOCK_FACTS = frozenset(
    {
        "total_assets",
        "total_liabilities",
        "total_equity",
        "cash",
        "restricted_cash",
        "interest_bearing_debt",
        "debt_due_within_one_year",
        "current_assets",
        "current_liabilities",
        "accounts_receivable",
        "inventory",
        "goodwill",
        "shares_outstanding",
        "loans_and_advances",
        "customer_deposits",
        "loan_loss_provision",
        "nonperforming_loans",
        "special_mention_loans",
        "overdue_loans",
        "risk_weighted_assets",
        "core_tier1_capital",
    }
)

FACT_ALIASES = {
    "sales": "revenue",
    "sales_revenue": "revenue",
    "cogs": "cost_of_revenue",
    "cost_of_goods_sold": "cost_of_revenue",
    "operating_income": "operating_profit",
    "profit_before_tax": "pretax_income",
    "tax_expense": "income_tax_expense",
    "profit": "net_income",
    "net_profit": "net_income",
    "cash_from_operations": "operating_cash_flow",
    "cash_flow_from_operations": "operating_cash_flow",
    "capex": "capital_expenditure",
    "total_stockholders_equity": "total_equity",
    "equity": "total_equity",
    "cash_and_equivalents": "cash",
    "debt": "interest_bearing_debt",
    "receivables": "accounts_receivable",
    "d_and_a": "depreciation_amortization",
}

FINANCIAL_INDUSTRY_KEYWORDS = (
    "bank",
    "banking",
    "insurance",
    "insurer",
    "broker",
    "brokerage",
    "securities",
    "diversified financial",
    "commercial finance",
    "financial services",
    "financials",
    "asset management",
    "credit services",
    "银行",
    "保险",
    "证券",
    "券商",
    "多元金融",
    "金融服务",
)


def _decimal(value: Any, field_name: str = "value") -> Optional[Decimal]:
    """Convert user input without collapsing a legitimate zero into missing."""

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric, not boolean")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} is not a valid decimal: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return result


def _number(value: Optional[Decimal]) -> Optional[float | int | str]:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    # JSON has no decimal type.  Keep ordinary financial values numeric, but use
    # a fixed-point string if converting an extreme exponent would create inf or
    # silently underflow to zero.
    converted = float(value)
    if converted == float("inf") or converted == float("-inf") or (converted == 0.0 and value != ZERO):
        return format(value, "f")
    return converted


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _number(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _canonical_fact_name(name: str) -> str:
    normalised = str(name).strip().lower().replace(" ", "_").replace("-", "_")
    return FACT_ALIASES.get(normalised, normalised)


@dataclass(frozen=True)
class FinancialFact:
    """One reported fact.  ``None`` means missing; ``Decimal(0)`` is real data."""

    value: Optional[Decimal]
    unit: str = "CNY"
    source: dict[str, Any] = field(default_factory=dict)
    statement: str = ""
    as_reported_label: str = ""
    restated: bool = False

    @classmethod
    def from_input(
        cls,
        raw: Any,
        *,
        default_unit: str,
        default_source: Mapping[str, Any] | str | None,
        field_name: str,
    ) -> "FinancialFact":
        if isinstance(raw, Mapping):
            source = raw.get("source", default_source)
            if isinstance(source, str):
                source = {"document": source}
            elif not isinstance(source, Mapping):
                source = {}
            return cls(
                value=_decimal(raw.get("value"), field_name),
                unit=str(raw.get("unit") or default_unit),
                source=dict(source),
                statement=str(raw.get("statement") or ""),
                as_reported_label=str(raw.get("as_reported_label") or raw.get("label") or ""),
                restated=bool(raw.get("restated", False)),
            )
        source_dict: dict[str, Any]
        if isinstance(default_source, str):
            source_dict = {"document": default_source}
        elif isinstance(default_source, Mapping):
            source_dict = dict(default_source)
        else:
            source_dict = {}
        return cls(_decimal(raw, field_name), default_unit, source_dict)


@dataclass(frozen=True)
class FinancialPeriod:
    end_date: str
    fiscal_year: int
    period_type: str
    duration_months: int
    cumulative: bool
    audited: bool
    facts: dict[str, FinancialFact]
    source: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_input(cls, raw: Mapping[str, Any], default_unit: str) -> "FinancialPeriod":
        if not isinstance(raw, Mapping):
            raise ValueError("each period must be an object")
        end_date = str(raw.get("end_date") or "")
        try:
            parsed_date = date.fromisoformat(end_date)
        except ValueError as exc:
            raise ValueError(f"invalid period end_date: {end_date!r}") from exc
        period_type = str(raw.get("period_type") or raw.get("kind") or "").upper()
        duration_default = {"FY": 12, "Q1": 3, "H1": 6, "Q2": 6, "Q3": 9, "Q4": 3}.get(period_type, 0)
        duration_months = int(raw.get("duration_months", duration_default))
        if duration_months < 0 or duration_months > 12:
            raise ValueError("duration_months must be between 0 and 12")
        cumulative_default = period_type in {"FY", "Q1", "H1", "Q2", "Q3"}
        cumulative = bool(raw.get("cumulative", cumulative_default))
        source = raw.get("source") or {}
        if isinstance(source, str):
            source = {"document": source}
        if not isinstance(source, Mapping):
            raise ValueError("period source must be an object or string")
        raw_facts = raw.get("facts") or {}
        if not isinstance(raw_facts, Mapping):
            raise ValueError("period facts must be an object")
        facts: dict[str, FinancialFact] = {}
        for supplied_name, supplied_fact in raw_facts.items():
            name = _canonical_fact_name(str(supplied_name))
            if name in facts:
                raise ValueError(f"duplicate canonical fact {name!r} in {end_date}")
            facts[name] = FinancialFact.from_input(
                supplied_fact,
                default_unit=default_unit,
                default_source=source,
                field_name=f"{end_date}.{name}",
            )
        return cls(
            end_date=end_date,
            fiscal_year=int(raw.get("fiscal_year", parsed_date.year)),
            period_type=period_type or ("FY" if duration_months == 12 else "OTHER"),
            duration_months=duration_months,
            cumulative=cumulative,
            audited=bool(raw.get("audited", False)),
            facts=facts,
            source=dict(source),
        )


@dataclass(frozen=True)
class CompanyData:
    symbol: str
    name: str
    industry: str
    currency: str
    unit: str
    periods: tuple[FinancialPeriod, ...]
    valuation: dict[str, Any]
    audit: dict[str, Any]

    @classmethod
    def from_input(cls, raw: Mapping[str, Any]) -> "CompanyData":
        if not isinstance(raw, Mapping):
            raise ValueError("company input must be an object")
        identity = raw.get("company") if isinstance(raw.get("company"), Mapping) else raw
        currency = str(raw.get("currency") or identity.get("currency") or "CNY")
        unit = str(raw.get("unit") or identity.get("unit") or currency)
        raw_periods = raw.get("periods") or []
        if not isinstance(raw_periods, list):
            raise ValueError("periods must be an array")
        periods = tuple(
            sorted(
                (FinancialPeriod.from_input(period, unit) for period in raw_periods),
                key=lambda period: (period.end_date, period.duration_months, period.period_type),
            )
        )
        valuation = raw.get("valuation") or {}
        audit = raw.get("audit") or {}
        if not isinstance(valuation, Mapping) or not isinstance(audit, Mapping):
            raise ValueError("valuation and audit must be objects")
        return cls(
            symbol=str(identity.get("symbol") or identity.get("ticker") or ""),
            name=str(identity.get("name") or ""),
            industry=str(identity.get("industry") or identity.get("sector") or ""),
            currency=currency,
            unit=unit,
            periods=periods,
            valuation=dict(valuation),
            audit=dict(audit),
        )


@dataclass(frozen=True)
class DerivedFact:
    value: Optional[Decimal]
    formula: str
    inputs: tuple[dict[str, Any], ...]
    unit: str
    missing_reason: str = ""


@dataclass(frozen=True)
class Snapshot:
    label: str
    end_date: str
    fiscal_year: Optional[int]
    facts: dict[str, DerivedFact]
    construction: str
    period_type: str = "OTHER"
    duration_months: int = 0
    cumulative: bool = False
    audited: bool = False
    report_scope: str = ""
    restated: bool = False


def _period_report_scope(period: FinancialPeriod) -> str:
    direct = str(period.source.get("report_scope") or "").strip()
    if direct:
        return direct
    scopes = {
        str(fact.source.get("report_scope") or "").strip()
        for fact in period.facts.values()
        if str(fact.source.get("report_scope") or "").strip()
    }
    return next(iter(scopes)) if len(scopes) == 1 else ("mixed" if scopes else "")


def _lineage(period: FinancialPeriod, name: str, fact: Optional[FinancialFact]) -> dict[str, Any]:
    return {
        "period_end": period.end_date,
        "fiscal_year": period.fiscal_year,
        "period_type": period.period_type,
        "fact": name,
        "value": _number(fact.value) if fact else None,
        "unit": fact.unit if fact else None,
        "source": _json_safe(fact.source if fact else period.source),
        "statement": fact.statement if fact else "",
        "as_reported_label": fact.as_reported_label if fact else "",
        "restated": fact.restated if fact else False,
        "status": "reported" if fact and fact.value is not None else "missing",
    }


def _reported(period: FinancialPeriod, name: str, unit: str) -> DerivedFact:
    fact = period.facts.get(name)
    return DerivedFact(
        fact.value if fact else None,
        f"reported({name})",
        (_lineage(period, name, fact),),
        fact.unit if fact else unit,
        "" if fact and fact.value is not None else f"missing {name} for {period.end_date}",
    )


def _snapshot(period: FinancialPeriod, unit: str) -> Snapshot:
    names = FLOW_FACTS | STOCK_FACTS | frozenset(period.facts)
    return Snapshot(
        label=f"FY{period.fiscal_year}" if period.period_type == "FY" else period.period_type,
        end_date=period.end_date,
        fiscal_year=period.fiscal_year,
        facts={name: _reported(period, name, unit) for name in names},
        construction="reported period",
        period_type=period.period_type,
        duration_months=period.duration_months,
        cumulative=period.cumulative,
        audited=period.audited,
        report_scope=_period_report_scope(period),
        restated=any(fact.restated for fact in period.facts.values()),
    )


def _balance_only_snapshot(period: FinancialPeriod, unit: str) -> Snapshot:
    """Supply comparable balance-sheet dates without mislabelling YTD flows as TTM."""

    facts: dict[str, DerivedFact] = {}
    for name in FLOW_FACTS | STOCK_FACTS | frozenset(period.facts):
        if name in FLOW_FACTS:
            raw = period.facts.get(name)
            facts[name] = DerivedFact(
                None,
                f"prior TTM {name} not constructed",
                (_lineage(period, name, raw),),
                raw.unit if raw else unit,
                "matching prior TTM flow is unavailable; a cumulative YTD value is not substituted",
            )
        else:
            facts[name] = _reported(period, name, unit)
    return Snapshot(
        label="PRIOR_TTM_BALANCES_ONLY",
        end_date=period.end_date,
        fiscal_year=period.fiscal_year,
        facts=facts,
        construction="balance-sheet facts from comparable prior date; flow facts intentionally unavailable",
        period_type=period.period_type,
        duration_months=period.duration_months,
        cumulative=period.cumulative,
        audited=period.audited,
        report_scope=_period_report_scope(period),
        restated=any(fact.restated for fact in period.facts.values()),
    )


def _combine(
    terms: Iterable[tuple[Decimal, DerivedFact]], formula: str, unit: str, missing_reason: str
) -> DerivedFact:
    items = list(terms)
    inputs = tuple(line for _, item in items for line in item.inputs)
    if any(item.value is None for _, item in items):
        upstream = sorted({item.missing_reason for _, item in items if item.value is None and item.missing_reason})
        reason = missing_reason + (f"; upstream: {'; '.join(upstream)}" if upstream else "")
        return DerivedFact(None, formula, inputs, unit, reason)
    incompatible = sorted({item.unit for _, item in items if item.unit != unit})
    if incompatible:
        return DerivedFact(
            None,
            formula,
            inputs,
            unit,
            f"unit mismatch: expected {unit!r}, received {', '.join(repr(value) for value in incompatible)}",
        )
    return DerivedFact(sum((coefficient * item.value for coefficient, item in items), ZERO), formula, inputs, unit)


def _annual_periods(company: CompanyData) -> list[FinancialPeriod]:
    return [period for period in company.periods if period.duration_months == 12 and period.cumulative]


def _find_comparable(periods: Iterable[FinancialPeriod], target: FinancialPeriod) -> Optional[FinancialPeriod]:
    candidates = [
        period
        for period in periods
        if period.fiscal_year == target.fiscal_year - 1
        and period.duration_months == target.duration_months
        and period.cumulative == target.cumulative
    ]
    return candidates[-1] if candidates else None


def _build_ttm(company: CompanyData) -> tuple[Optional[Snapshot], list[dict[str, Any]]]:
    notes: list[dict[str, Any]] = []
    if not company.periods:
        return None, [{"severity": "warning", "code": "NO_PERIODS", "message": "No financial periods supplied."}]
    latest = company.periods[-1]
    if latest.duration_months == 12 and latest.cumulative:
        snap = _snapshot(latest, company.unit)
        return Snapshot("TTM", latest.end_date, latest.fiscal_year, snap.facts, "latest audited/reported FY used as TTM", "TTM", 12, False, latest.audited, _period_report_scope(latest), snap.restated), notes

    discrete = [
        period
        for period in company.periods
        if not period.cumulative and 1 <= period.duration_months <= 3 and period.end_date <= latest.end_date
    ][-4:]
    discrete_dates = [date.fromisoformat(period.end_date) for period in discrete]
    quarterly_spacing = len(discrete_dates) == 4 and all(
        60 <= (right - left).days <= 120 for left, right in zip(discrete_dates, discrete_dates[1:])
    )
    if (
        len(discrete) == 4
        and len({period.end_date for period in discrete}) == 4
        and sum(period.duration_months for period in discrete) == 12
        and quarterly_spacing
    ):
        facts: dict[str, DerivedFact] = {}
        for name in FLOW_FACTS:
            pieces = [_reported(period, name, company.unit) for period in discrete]
            facts[name] = _combine(
                [(ONE, piece) for piece in pieces],
                " + ".join(f"reported({name}, {period.end_date})" for period in discrete),
                company.unit,
                f"one or more of the latest four discrete quarters are missing {name}",
            )
        for name in STOCK_FACTS | frozenset(latest.facts):
            if name not in FLOW_FACTS:
                facts[name] = _reported(latest, name, company.unit)
        return Snapshot("TTM", latest.end_date, latest.fiscal_year, facts, "sum of latest four discrete quarters; balance-sheet facts from latest quarter", "TTM", 12, False, all(period.audited for period in discrete), _period_report_scope(latest), any(fact.restated for period in discrete for fact in period.facts.values())), notes

    prior_comparable = _find_comparable(company.periods, latest)
    prior_fy_candidates = [
        period for period in _annual_periods(company) if period.fiscal_year == latest.fiscal_year - 1
    ]
    prior_fy = prior_fy_candidates[-1] if prior_fy_candidates else None
    if latest.cumulative and latest.duration_months < 12 and prior_comparable and prior_fy:
        facts = {}
        for name in FLOW_FACTS:
            fy_fact = _reported(prior_fy, name, company.unit)
            current_fact = _reported(latest, name, company.unit)
            prior_fact = _reported(prior_comparable, name, company.unit)
            facts[name] = _combine(
                [(ONE, fy_fact), (ONE, current_fact), (-ONE, prior_fact)],
                f"FY{prior_fy.fiscal_year} {name} + current YTD {name} - prior YTD {name}",
                company.unit,
                f"cannot construct TTM {name}: FY/current YTD/prior YTD fact missing",
            )
        for name in STOCK_FACTS | frozenset(latest.facts):
            if name not in FLOW_FACTS:
                facts[name] = _reported(latest, name, company.unit)
        return Snapshot(
            "TTM",
            latest.end_date,
            latest.fiscal_year,
            facts,
            f"FY{prior_fy.fiscal_year} + {latest.end_date} YTD - {prior_comparable.end_date} YTD; balance-sheet facts from latest period",
            "TTM",
            12,
            False,
            False,
            _period_report_scope(latest),
            any(fact.restated for period in (prior_fy, latest, prior_comparable) for fact in period.facts.values()),
        ), notes

    notes.append(
        {
            "severity": "warning",
            "code": "TTM_NOT_CONSTRUCTIBLE",
            "message": "TTM needs an FY plus matching current/prior cumulative periods, or four discrete quarters.",
            "latest_period": latest.end_date,
        }
    )
    return None, notes


def _inputs(*facts: Optional[DerivedFact]) -> list[dict[str, Any]]:
    return [line for fact in facts if fact is not None for line in fact.inputs]


def _metric(
    value: Optional[Decimal],
    unit: str,
    formula: str,
    facts: Iterable[Optional[DerivedFact]] = (),
    *,
    status: Optional[str] = None,
    reason: str = "",
) -> dict[str, Any]:
    facts_list = list(facts)
    if status is None:
        status = "ok" if value is not None else "missing"
    return {
        "value": _number(value),
        "status": status,
        "unit": unit,
        "formula": formula,
        "inputs": _inputs(*facts_list),
        "reason": reason,
    }


def _fact_metric(fact: DerivedFact) -> dict[str, Any]:
    return _metric(
        fact.value,
        fact.unit,
        fact.formula,
        [fact],
        reason=fact.missing_reason,
    )


def _ratio(
    numerator: DerivedFact,
    denominator: DerivedFact,
    formula: str,
    *,
    multiplier: Decimal = ONE,
    unit: str = "ratio",
) -> dict[str, Any]:
    if numerator.value is None or denominator.value is None:
        missing = [name for name, fact in (("numerator", numerator), ("denominator", denominator)) if fact.value is None]
        return _metric(None, unit, formula, [numerator, denominator], reason="missing " + " and ".join(missing))
    if numerator.unit != denominator.unit:
        return _metric(
            None,
            unit,
            formula,
            [numerator, denominator],
            status="not_calculable",
            reason=f"unit mismatch: numerator is {numerator.unit!r}, denominator is {denominator.unit!r}",
        )
    if denominator.value == ZERO:
        return _metric(
            None,
            unit,
            formula,
            [numerator, denominator],
            status="not_calculable",
            reason="division by zero; zero is retained as reported data",
        )
    return _metric(numerator.value / denominator.value * multiplier, unit, formula, [numerator, denominator])


def _average(current: DerivedFact, prior: Optional[DerivedFact], name: str, unit: str) -> DerivedFact:
    if prior is None:
        return DerivedFact(None, f"({name}_current + {name}_prior) / 2", current.inputs, unit, f"prior {name} missing")
    return _combine(
        [(Decimal("0.5"), current), (Decimal("0.5"), prior)],
        f"({name}_current + {name}_prior) / 2",
        unit,
        f"current or prior {name} missing",
    )


def _subtract(left: DerivedFact, right: DerivedFact, formula: str, unit: str) -> DerivedFact:
    return _combine([(ONE, left), (-ONE, right)], formula, unit, "one or more inputs missing")


def _growth(current: DerivedFact, prior: Optional[DerivedFact], name: str) -> dict[str, Any]:
    if prior is None:
        return _metric(None, "ratio", f"({name}_current / {name}_prior) - 1", [current], reason=f"prior {name} missing")
    if current.value is None or prior.value is None:
        return _metric(None, "ratio", f"({name}_current / {name}_prior) - 1", [current, prior], reason="current or prior value missing")
    if current.unit != prior.unit:
        return _metric(
            None,
            "ratio",
            f"({name}_current / {name}_prior) - 1",
            [current, prior],
            status="not_calculable",
            reason=f"unit mismatch: current is {current.unit!r}, prior is {prior.unit!r}",
        )
    if prior.value == ZERO:
        return _metric(
            None,
            "ratio",
            f"({name}_current / {name}_prior) - 1",
            [current, prior],
            status="not_calculable",
            reason="prior value is zero; percentage growth is undefined",
        )
    return _metric(current.value / prior.value - ONE, "ratio", f"({name}_current / {name}_prior) - 1", [current, prior])


def _snapshot_metrics(snapshot: Snapshot, prior: Optional[Snapshot], unit: str) -> dict[str, Any]:
    fact = lambda name: snapshot.facts.get(name, DerivedFact(None, f"reported({name})", (), unit, f"missing {name}"))
    prior_fact = lambda name: prior.facts.get(name) if prior else None

    revenue = fact("revenue")
    gross_profit = fact("gross_profit")
    if gross_profit.value is None:
        cost = fact("cost_of_revenue")
        gross_profit = _subtract(revenue, cost, "revenue - cost_of_revenue", unit)
    operating_profit = fact("operating_profit")
    ebit = fact("ebit") if fact("ebit").value is not None else operating_profit
    net_income = fact("net_income")
    ocf = fact("operating_cash_flow")
    capex = fact("capital_expenditure")
    fcf = fact("free_cash_flow_to_equity")
    if fcf.value is None:
        fcf = _subtract(ocf, capex, "operating_cash_flow - capital_expenditure", unit)
    assets = fact("total_assets")
    equity = fact("total_equity")
    debt = fact("interest_bearing_debt")
    short_debt = fact("debt_due_within_one_year")
    cash = fact("cash")
    restricted_cash = fact("restricted_cash")
    net_debt = _subtract(debt, cash, "interest_bearing_debt - cash", unit)
    usable_cash = _subtract(cash, restricted_cash, "cash - restricted_cash", unit)
    avg_assets = _average(assets, prior_fact("total_assets"), "total_assets", unit)
    avg_equity = _average(equity, prior_fact("total_equity"), "total_equity", unit)
    invested_capital = _combine(
        [(ONE, equity), (ONE, debt), (-ONE, cash)],
        "total_equity + interest_bearing_debt - cash",
        unit,
        "equity, debt, or cash missing",
    )
    prior_invested: Optional[DerivedFact] = None
    if prior:
        p_equity, p_debt, p_cash = prior_fact("total_equity"), prior_fact("interest_bearing_debt"), prior_fact("cash")
        if p_equity and p_debt and p_cash:
            prior_invested = _combine(
                [(ONE, p_equity), (ONE, p_debt), (-ONE, p_cash)],
                "prior equity + prior debt - prior cash",
                unit,
                "prior equity, debt, or cash missing",
            )
    avg_invested = _average(invested_capital, prior_invested, "invested_capital", unit)

    pretax = fact("pretax_income")
    tax = fact("income_tax_expense")
    tax_rate: Optional[Decimal] = None
    tax_inputs = [pretax, tax]
    if pretax.value is not None and tax.value is not None and pretax.value > ZERO:
        candidate = tax.value / pretax.value
        if ZERO <= candidate <= ONE:
            tax_rate = candidate
    nopat = DerivedFact(None, "EBIT × (1 - effective_tax_rate)", tuple(_inputs(ebit, pretax, tax)), unit, "EBIT or a valid effective tax rate missing")
    if ebit.value is not None and tax_rate is not None:
        nopat = DerivedFact(ebit.value * (ONE - tax_rate), "EBIT × (1 - income_tax_expense / pretax_income)", tuple(_inputs(ebit, pretax, tax)), unit)

    current_assets = fact("current_assets")
    current_liabilities = fact("current_liabilities")
    inventory = fact("inventory")
    quick_assets = _subtract(current_assets, inventory, "current_assets - inventory", unit)
    receivables = fact("accounts_receivable")
    avg_receivables = _average(receivables, prior_fact("accounts_receivable"), "accounts_receivable", unit)
    avg_inventory = _average(inventory, prior_fact("inventory"), "inventory", unit)
    cost = fact("cost_of_revenue")
    interest = fact("interest_expense")
    dividends = fact("dividends_paid")

    metrics: dict[str, Any] = {
        "revenue": _fact_metric(revenue),
        "gross_profit": _fact_metric(gross_profit),
        "operating_profit": _fact_metric(operating_profit),
        "net_income": _fact_metric(net_income),
        "operating_cash_flow": _fact_metric(ocf),
        "capital_expenditure": _fact_metric(capex),
        "free_cash_flow": _fact_metric(fcf),
        "total_assets": _fact_metric(assets),
        "total_equity": _fact_metric(equity),
        "interest_bearing_debt": _fact_metric(debt),
        "debt_due_within_one_year": _fact_metric(short_debt),
        "cash": _fact_metric(cash),
        "restricted_cash": _fact_metric(restricted_cash),
        "usable_cash": _fact_metric(usable_cash),
        "net_debt": _fact_metric(net_debt),
        "revenue_growth": _growth(revenue, prior_fact("revenue"), "revenue"),
        "net_income_growth": _growth(net_income, prior_fact("net_income"), "net_income"),
        "accounts_receivable": _fact_metric(receivables),
        "inventory": _fact_metric(inventory),
        "accounts_receivable_growth": _growth(receivables, prior_fact("accounts_receivable"), "accounts_receivable"),
        "inventory_growth": _growth(inventory, prior_fact("inventory"), "inventory"),
        "gross_margin": _ratio(gross_profit, revenue, "gross_profit / revenue"),
        "operating_margin": _ratio(operating_profit, revenue, "operating_profit / revenue"),
        "net_margin": _ratio(net_income, revenue, "net_income / revenue"),
        "return_on_assets": _ratio(net_income, avg_assets, "net_income / average_total_assets"),
        "return_on_equity": _ratio(net_income, avg_equity, "net_income / average_total_equity"),
        "asset_turnover": _ratio(revenue, avg_assets, "revenue / average_total_assets"),
        "cash_conversion": _ratio(ocf, net_income, "operating_cash_flow / net_income"),
        "debt_to_equity": _ratio(debt, equity, "interest_bearing_debt / total_equity"),
        "current_ratio": _ratio(current_assets, current_liabilities, "current_assets / current_liabilities"),
        "quick_ratio": _ratio(quick_assets, current_liabilities, "(current_assets - inventory) / current_liabilities"),
        "interest_coverage": _ratio(ebit, interest, "EBIT / interest_expense"),
        "gross_cash_short_debt_coverage": _ratio(cash, short_debt, "cash / debt_due_within_one_year"),
        "usable_cash_short_debt_coverage": _ratio(usable_cash, short_debt, "(cash - restricted_cash) / debt_due_within_one_year"),
        "restricted_cash_ratio": _ratio(restricted_cash, cash, "restricted_cash / cash"),
        "receivable_days": _ratio(avg_receivables, revenue, "average_accounts_receivable / revenue × 365", multiplier=DAYS_PER_YEAR, unit="days"),
        "inventory_days": _ratio(avg_inventory, cost, "average_inventory / cost_of_revenue × 365", multiplier=DAYS_PER_YEAR, unit="days"),
        "dividend_payout": _ratio(dividends, net_income, "dividends_paid / net_income"),
        "free_cash_flow_dividend_cover": _ratio(fcf, dividends, "free_cash_flow / dividends_paid"),
        "effective_tax_rate": _metric(
            tax_rate,
            "ratio",
            "income_tax_expense / pretax_income (accepted only when 0%–100%)",
            tax_inputs,
            reason="pretax income/tax missing, pretax income non-positive, or rate outside 0%–100%" if tax_rate is None else "",
        ),
        "nopat": _fact_metric(nopat),
        "invested_capital": _fact_metric(invested_capital),
        "return_on_invested_capital": _ratio(nopat, avg_invested, "NOPAT / average_invested_capital"),
    }
    return metrics


def _dupont(snapshot: Snapshot, prior: Optional[Snapshot], unit: str) -> dict[str, Any]:
    metrics = _snapshot_metrics(snapshot, prior, unit)
    margin = metrics["net_margin"]
    turnover = metrics["asset_turnover"]
    current_assets = snapshot.facts.get("total_assets")
    current_equity = snapshot.facts.get("total_equity")
    prior_assets = prior.facts.get("total_assets") if prior else None
    prior_equity = prior.facts.get("total_equity") if prior else None
    if current_assets is None or current_equity is None:
        multiplier = _metric(None, "multiple", "average_total_assets / average_total_equity", reason="assets or equity missing")
    else:
        avg_assets = _average(current_assets, prior_assets, "total_assets", unit)
        avg_equity = _average(current_equity, prior_equity, "total_equity", unit)
        multiplier = _ratio(avg_assets, avg_equity, "average_total_assets / average_total_equity", unit="multiple")
    components = [margin, turnover, multiplier]
    if all(component["status"] == "ok" for component in components):
        roe = Decimal(str(margin["value"])) * Decimal(str(turnover["value"])) * Decimal(str(multiplier["value"]))
        result = _metric(roe, "ratio", "net_margin × asset_turnover × equity_multiplier")
    else:
        result = _metric(None, "ratio", "net_margin × asset_turnover × equity_multiplier", status="missing", reason="one or more DuPont components unavailable")
    result["inputs"] = margin["inputs"] + turnover["inputs"] + multiplier["inputs"]
    return {
        "status": "ok" if result["status"] == "ok" else "incomplete",
        "period_end": snapshot.end_date,
        "net_margin": margin,
        "asset_turnover": turnover,
        "equity_multiplier": multiplier,
        "roe": result,
        "note": "Three-step DuPont uses average assets and average equity; it is not applied to unsupported financial industries.",
    }


def _criterion(
    criterion_id: str,
    label: str,
    passed: Optional[bool],
    formula: str,
    facts: Iterable[Optional[DerivedFact]],
    values: Mapping[str, Optional[Decimal]],
) -> dict[str, Any]:
    return {
        "id": criterion_id,
        "label": label,
        "status": "pass" if passed is True else "fail" if passed is False else "missing",
        "point": 1 if passed is True else 0 if passed is False else None,
        "formula": formula,
        "values": {key: _number(value) for key, value in values.items()},
        "inputs": _inputs(*list(facts)),
    }


def _safe_ratio_value(numerator: Optional[Decimal], denominator: Optional[Decimal]) -> Optional[Decimal]:
    if numerator is None or denominator is None or denominator == ZERO:
        return None
    return numerator / denominator


def _fact_ratio_value(numerator: DerivedFact, denominator: DerivedFact) -> Optional[Decimal]:
    if numerator.unit != denominator.unit:
        return None
    return _safe_ratio_value(numerator.value, denominator.value)


def _piotroski(current: Snapshot, prior: Optional[Snapshot], unit: str) -> dict[str, Any]:
    if prior is None:
        return {
            "status": "incomplete",
            "score": None,
            "points_available": 0,
            "points_total": 9,
            "criteria": [],
            "reason": "Two consecutive annual periods are required.",
            "note": "Auxiliary screen only; not a valuation or investment recommendation.",
        }
    c = current.facts
    p = prior.facts
    get_c = lambda name: c.get(name, DerivedFact(None, f"reported({name})", (), unit, f"missing {name}"))
    get_p = lambda name: p.get(name, DerivedFact(None, f"reported({name})", (), unit, f"missing prior {name}"))
    c_ni, p_ni = get_c("net_income"), get_p("net_income")
    c_ocf = get_c("operating_cash_flow")
    c_assets, p_assets = get_c("total_assets"), get_p("total_assets")
    c_roa = _fact_ratio_value(c_ni, c_assets)
    p_roa = _fact_ratio_value(p_ni, p_assets)
    c_debt, p_debt = get_c("interest_bearing_debt"), get_p("interest_bearing_debt")
    c_lev = _fact_ratio_value(c_debt, c_assets)
    p_lev = _fact_ratio_value(p_debt, p_assets)
    c_ca, p_ca = get_c("current_assets"), get_p("current_assets")
    c_cl, p_cl = get_c("current_liabilities"), get_p("current_liabilities")
    c_cr = _fact_ratio_value(c_ca, c_cl)
    p_cr = _fact_ratio_value(p_ca, p_cl)
    c_shares, p_shares = get_c("shares_outstanding"), get_p("shares_outstanding")
    c_revenue, p_revenue = get_c("revenue"), get_p("revenue")
    c_gp, p_gp = get_c("gross_profit"), get_p("gross_profit")
    if c_gp.value is None:
        c_gp = _subtract(c_revenue, get_c("cost_of_revenue"), "revenue - cost_of_revenue", unit)
    if p_gp.value is None:
        p_gp = _subtract(p_revenue, get_p("cost_of_revenue"), "revenue - cost_of_revenue", unit)
    c_gm = _fact_ratio_value(c_gp, c_revenue)
    p_gm = _fact_ratio_value(p_gp, p_revenue)
    c_turnover = _fact_ratio_value(c_revenue, c_assets)
    p_turnover = _fact_ratio_value(p_revenue, p_assets)

    criteria = [
        _criterion("F_ROA", "Positive return on assets", c_roa > ZERO if c_roa is not None else None, "net_income / total_assets > 0", [c_ni, c_assets], {"roa": c_roa}),
        _criterion("F_CFO", "Positive operating cash flow", c_ocf.value > ZERO if c_ocf.value is not None else None, "operating_cash_flow > 0", [c_ocf], {"operating_cash_flow": c_ocf.value}),
        _criterion("F_DROA", "Return on assets improved", c_roa > p_roa if c_roa is not None and p_roa is not None else None, "current_roa > prior_roa", [c_ni, p_ni, c_assets, p_assets], {"current_roa": c_roa, "prior_roa": p_roa}),
        _criterion("F_ACCRUAL", "Cash flow exceeds earnings", c_ocf.value > c_ni.value if c_ocf.value is not None and c_ni.value is not None and c_ocf.unit == c_ni.unit else None, "operating_cash_flow > net_income", [c_ocf, c_ni], {"operating_cash_flow": c_ocf.value, "net_income": c_ni.value}),
        _criterion("F_LEVER", "Leverage decreased", c_lev < p_lev if c_lev is not None and p_lev is not None else None, "current_debt/assets < prior_debt/assets", [c_debt, p_debt, c_assets, p_assets], {"current_leverage": c_lev, "prior_leverage": p_lev}),
        _criterion("F_LIQUID", "Liquidity improved", c_cr > p_cr if c_cr is not None and p_cr is not None else None, "current_current_ratio > prior_current_ratio", [c_ca, c_cl, p_ca, p_cl], {"current_ratio": c_cr, "prior_ratio": p_cr}),
        _criterion("F_EQ_OFFER", "No net share issuance", c_shares.value <= p_shares.value if c_shares.value is not None and p_shares.value is not None and c_shares.unit == p_shares.unit else None, "current_shares <= prior_shares", [c_shares, p_shares], {"current_shares": c_shares.value, "prior_shares": p_shares.value}),
        _criterion("F_MARGIN", "Gross margin improved", c_gm > p_gm if c_gm is not None and p_gm is not None else None, "current_gross_margin > prior_gross_margin", [c_gp, p_gp, c_revenue, p_revenue], {"current_margin": c_gm, "prior_margin": p_gm}),
        _criterion("F_TURN", "Asset turnover improved", c_turnover > p_turnover if c_turnover is not None and p_turnover is not None else None, "current_asset_turnover > prior_asset_turnover", [c_revenue, p_revenue, c_assets, p_assets], {"current_turnover": c_turnover, "prior_turnover": p_turnover}),
    ]
    available = sum(item["point"] is not None for item in criteria)
    score = sum(item["point"] or 0 for item in criteria)
    return {
        "status": "complete" if available == 9 else "incomplete",
        "score": score,
        "points_available": available,
        "points_total": 9,
        "criteria": criteria,
        "reason": "" if available == 9 else "Missing inputs reduce comparability; the score is not rescaled.",
        "note": "Auxiliary Piotroski-style screen using canonical facts. It is not a valuation or investment recommendation.",
    }


def _quality_check(
    check_id: str,
    status: str,
    message: str,
    period: Optional[FinancialPeriod] = None,
    inputs: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "severity": "error" if status == "fail" else "warning" if status == "warn" else "info",
        "message": message,
        "period_end": period.end_date if period else None,
        "inputs": list(inputs),
    }


def _data_quality(company: CompanyData, ttm_notes: list[dict[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, bool]] = set()
    for period in company.periods:
        key = (period.end_date, period.period_type, period.duration_months, period.cumulative)
        checks.append(_quality_check("UNIQUE_PERIOD", "fail" if key in seen else "pass", "Duplicate canonical period." if key in seen else "Period key is unique.", period))
        seen.add(key)
        assets = period.facts.get("total_assets")
        liabilities = period.facts.get("total_liabilities")
        equity = period.facts.get("total_equity")
        if all(item is not None and item.value is not None for item in (assets, liabilities, equity)):
            difference = assets.value - liabilities.value - equity.value
            tolerance = max(abs(assets.value) * Decimal("0.005"), Decimal("1"))
            passed = abs(difference) <= tolerance
            checks.append(
                _quality_check(
                    "BALANCE_SHEET_EQUATION",
                    "pass" if passed else "fail",
                    f"Assets - liabilities - equity = {_number(difference)}; tolerance = {_number(tolerance)}.",
                    period,
                    [_lineage(period, name, period.facts.get(name)) for name in ("total_assets", "total_liabilities", "total_equity")],
                )
            )
        else:
            checks.append(_quality_check("BALANCE_SHEET_EQUATION", "warn", "Cannot test assets = liabilities + equity because one or more facts are missing.", period))
        for name in (
            "total_assets",
            "total_liabilities",
            "cash",
            "restricted_cash",
            "interest_bearing_debt",
            "current_assets",
            "current_liabilities",
            "accounts_receivable",
            "inventory",
            "goodwill",
            "shares_outstanding",
        ):
            fact = period.facts.get(name)
            if fact is not None and fact.value is not None and fact.value < ZERO:
                checks.append(_quality_check(f"NONNEGATIVE_{name.upper()}", "fail", f"{name} is negative although the canonical convention expects a non-negative value.", period, [_lineage(period, name, fact)]))
        capex = period.facts.get("capital_expenditure")
        if capex is not None and capex.value is not None and capex.value < ZERO:
            checks.append(_quality_check("CAPEX_SIGN", "warn", "capital_expenditure is negative; canonical input expects a positive cash-outflow magnitude.", period, [_lineage(period, "capital_expenditure", capex)]))
        if not period.source and not any(fact.source for fact in period.facts.values()):
            checks.append(_quality_check("SOURCE_LINEAGE", "warn", "No document/page source metadata supplied for this period.", period))
        monetary_units = {
            fact.unit
            for name, fact in period.facts.items()
            if name != "shares_outstanding" and fact.value is not None
        }
        if monetary_units and monetary_units != {company.unit}:
            checks.append(
                _quality_check(
                    "UNIT_CONSISTENCY",
                    "fail",
                    f"Monetary facts must be normalised to {company.unit!r}; observed {sorted(monetary_units)!r}.",
                    period,
                )
            )

    annual = _annual_periods(company)
    for previous, current in zip(annual, annual[1:]):
        if current.fiscal_year - previous.fiscal_year != 1:
            checks.append(_quality_check("ANNUAL_CONTINUITY", "warn", f"Annual history jumps from FY{previous.fiscal_year} to FY{current.fiscal_year}.", current))
    if company.periods:
        latest = company.periods[-1]
        for name in ("revenue", "net_income", "operating_cash_flow", "total_assets", "total_liabilities", "total_equity"):
            fact = latest.facts.get(name)
            checks.append(
                _quality_check(
                    f"LATEST_{name.upper()}",
                    "pass" if fact is not None and fact.value is not None else "warn",
                    f"Latest period has {name}." if fact is not None and fact.value is not None else f"Latest period is missing critical fact {name}.",
                    latest,
                    [_lineage(latest, name, fact)],
                )
            )
    else:
        checks.append(_quality_check("PERIODS_PRESENT", "fail", "No periods supplied."))
    for note in ttm_notes:
        checks.append(_quality_check(note["code"], "warn", note["message"]))
    failures = sum(check["status"] == "fail" for check in checks)
    warnings = sum(check["status"] == "warn" for check in checks)
    score = max(0, 100 - failures * 15 - warnings * 5)
    return {
        "score": score,
        "status": "poor" if failures else "needs_review" if warnings else "good",
        "failures": failures,
        "warnings": warnings,
        "checks": checks,
        "note": "Quality score measures input completeness/consistency, not company quality.",
    }


def _flag(
    flag_id: str,
    severity: str,
    title: str,
    rationale: str,
    formula: str,
    facts: Iterable[Optional[DerivedFact]],
    values: Mapping[str, Optional[Decimal]],
    period_end: str,
) -> dict[str, Any]:
    return {
        "id": flag_id,
        "severity": severity,
        "title": title,
        "rationale": rationale,
        "formula": formula,
        "values": {key: _number(value) for key, value in values.items()},
        "period_end": period_end,
        "inputs": _inputs(*list(facts)),
        "interpretation_limit": "Screening signal only; verify business context and the original filing.",
    }


def _financial_flags(snapshot: Snapshot, prior: Optional[Snapshot], audit: Mapping[str, Any], unit: str) -> list[dict[str, Any]]:
    facts = snapshot.facts
    get = lambda name: facts.get(name, DerivedFact(None, f"reported({name})", (), unit, f"missing {name}"))
    prior_get = lambda name: prior.facts.get(name) if prior else None
    flags: list[dict[str, Any]] = []
    ni, ocf = get("net_income"), get("operating_cash_flow")
    if ni.value is not None and ocf.value is not None and ni.unit == ocf.unit and ni.value > ZERO and ocf.value < ZERO:
        flags.append(_flag("EARNINGS_CASH_DIVERGENCE", "red", "Positive earnings but negative operating cash flow", "Reported profit is not supported by operating cash in the period.", "net_income > 0 and operating_cash_flow < 0", [ni, ocf], {"net_income": ni.value, "operating_cash_flow": ocf.value}, snapshot.end_date))
    elif ni.value is not None and ocf.value is not None and ni.unit == ocf.unit and ni.value > ZERO:
        conversion = ocf.value / ni.value
        if conversion < Decimal("0.8"):
            flags.append(_flag("LOW_CASH_CONVERSION", "yellow", "Low cash conversion", "Operating cash flow is below 80% of net income; working-capital and one-off causes need review.", "operating_cash_flow / net_income < 0.8", [ocf, ni], {"cash_conversion": conversion}, snapshot.end_date))

    equity = get("total_equity")
    if equity.value is not None and equity.value <= ZERO:
        flags.append(_flag("NONPOSITIVE_EQUITY", "red", "Non-positive equity", "Book equity is zero or negative, so common leverage and return ratios become unreliable.", "total_equity <= 0", [equity], {"total_equity": equity.value}, snapshot.end_date))
    ebit = get("ebit") if get("ebit").value is not None else get("operating_profit")
    interest = get("interest_expense")
    coverage = _fact_ratio_value(ebit, interest)
    if coverage is not None and interest.value is not None and interest.value > ZERO:
        if coverage < ONE:
            flags.append(_flag("INTEREST_COVERAGE_CRITICAL", "red", "EBIT does not cover interest", "Operating earnings are below reported interest expense.", "EBIT / interest_expense < 1", [ebit, interest], {"interest_coverage": coverage}, snapshot.end_date))
        elif coverage < Decimal("2"):
            flags.append(_flag("INTEREST_COVERAGE_THIN", "yellow", "Thin interest coverage", "EBIT covers interest by less than two times.", "EBIT / interest_expense < 2", [ebit, interest], {"interest_coverage": coverage}, snapshot.end_date))
    goodwill = get("goodwill")
    goodwill_ratio = _fact_ratio_value(goodwill, equity)
    if goodwill_ratio is not None and equity.value is not None and equity.value > ZERO and goodwill_ratio > Decimal("0.5"):
        flags.append(_flag("HIGH_GOODWILL", "yellow", "Goodwill is large relative to equity", "A material impairment could meaningfully reduce book equity.", "goodwill / total_equity > 0.5", [goodwill, equity], {"goodwill_to_equity": goodwill_ratio}, snapshot.end_date))

    capex = get("capital_expenditure")
    fcf = _subtract(ocf, capex, "operating_cash_flow - capital_expenditure", unit)
    if ni.value is not None and ni.value > ZERO and fcf.value is not None and ni.unit == fcf.unit and fcf.value < ZERO:
        flags.append(_flag("PROFIT_WITH_NEGATIVE_FCF", "yellow", "Positive profit but negative free cash flow", "Capital expenditure absorbed more than operating cash flow in the period.", "net_income > 0 and operating_cash_flow - capital_expenditure < 0", [ni, ocf, capex], {"net_income": ni.value, "free_cash_flow": fcf.value}, snapshot.end_date))
    dividends = get("dividends_paid")
    if dividends.value is not None and dividends.value > ZERO and fcf.value is not None and fcf.unit == dividends.unit and fcf.value < dividends.value:
        flags.append(_flag("DIVIDEND_NOT_FCF_COVERED", "yellow", "Dividend not covered by free cash flow", "The period's free cash flow was below cash dividends paid.", "free_cash_flow / dividends_paid < 1", [fcf, dividends], {"free_cash_flow": fcf.value, "dividends_paid": dividends.value}, snapshot.end_date))

    if prior:
        revenue, p_revenue = get("revenue"), prior_get("revenue")
        receivables, p_receivables = get("accounts_receivable"), prior_get("accounts_receivable")
        inventory, p_inventory = get("inventory"), prior_get("inventory")
        rev_growth = _safe_ratio_value(revenue.value - p_revenue.value, abs(p_revenue.value)) if revenue.value is not None and p_revenue and p_revenue.value not in (None, ZERO) and revenue.unit == p_revenue.unit else None
        ar_growth = _safe_ratio_value(receivables.value - p_receivables.value, abs(p_receivables.value)) if receivables.value is not None and p_receivables and p_receivables.value not in (None, ZERO) and receivables.unit == p_receivables.unit else None
        inv_growth = _safe_ratio_value(inventory.value - p_inventory.value, abs(p_inventory.value)) if inventory.value is not None and p_inventory and p_inventory.value not in (None, ZERO) and inventory.unit == p_inventory.unit else None
        if rev_growth is not None and ar_growth is not None and ar_growth > rev_growth + Decimal("0.15") and ar_growth > Decimal("0.20"):
            flags.append(_flag("RECEIVABLES_OUTRUN_REVENUE", "yellow", "Receivables grew materially faster than revenue", "Receivables growth exceeded revenue growth by more than 15 percentage points.", "receivables_growth > revenue_growth + 0.15", [receivables, p_receivables, revenue, p_revenue], {"receivables_growth": ar_growth, "revenue_growth": rev_growth}, snapshot.end_date))
        if rev_growth is not None and inv_growth is not None and inv_growth > rev_growth + Decimal("0.15") and inv_growth > Decimal("0.20"):
            flags.append(_flag("INVENTORY_OUTRUN_REVENUE", "yellow", "Inventory grew materially faster than revenue", "Inventory growth exceeded revenue growth by more than 15 percentage points.", "inventory_growth > revenue_growth + 0.15", [inventory, p_inventory, revenue, p_revenue], {"inventory_growth": inv_growth, "revenue_growth": rev_growth}, snapshot.end_date))

    opinion = str(audit.get("opinion") or "").strip().lower()
    unqualified = {"unqualified", "standard unqualified", "clean", "标准无保留意见", "无保留意见"}
    if opinion and opinion not in unqualified:
        flags.append(
            {
                "id": "MODIFIED_AUDIT_OPINION",
                "severity": "red",
                "title": "Audit opinion requires review",
                "rationale": "The supplied audit opinion is not classified as a standard unqualified opinion.",
                "formula": "audit.opinion not in accepted unqualified labels",
                "values": {"opinion": audit.get("opinion")},
                "period_end": snapshot.end_date,
                "inputs": [{"fact": "audit_opinion", "value": audit.get("opinion"), "source": _json_safe(audit.get("source") or {})}],
                "interpretation_limit": "Read the complete auditor's report and basis paragraph.",
            }
        )
    return flags


def _assumption_lineage(name: str, value: Optional[Decimal], source: str = "valuation configuration") -> dict[str, Any]:
    return {"fact": name, "value": _number(value), "status": "assumption" if value is not None else "missing", "source": {"type": source}}


def _scenario_values(base: Decimal, growth_rates: list[Decimal], discount: Decimal, terminal_growth: Decimal) -> tuple[list[Decimal], Decimal, Decimal]:
    cash_flows: list[Decimal] = []
    current = base
    pv = ZERO
    for year, growth in enumerate(growth_rates, start=1):
        current = current * (ONE + growth)
        cash_flows.append(current)
        pv += current / ((ONE + discount) ** year)
    terminal_value = current * (ONE + terminal_growth) / (discount - terminal_growth)
    pv_terminal = terminal_value / ((ONE + discount) ** len(growth_rates))
    return cash_flows, pv, pv_terminal


def _valuation_cash_flow(snapshot: Optional[Snapshot], config: Mapping[str, Any], unit: str) -> tuple[DerivedFact, str]:
    basis = str(config.get("cash_flow_basis") or "fcfe").lower()
    explicit = _decimal(config.get("base_cash_flow"), "valuation.dcf.base_cash_flow")
    if explicit is not None:
        return DerivedFact(explicit, "explicit base_cash_flow assumption", (_assumption_lineage("base_cash_flow", explicit),), unit), basis
    if snapshot is None:
        return DerivedFact(None, "base cash flow", (), unit, "TTM snapshot unavailable"), basis
    if basis == "fcff":
        reported = snapshot.facts.get("free_cash_flow_to_firm")
        if reported and reported.value is not None:
            return reported, basis
        return DerivedFact(None, "reported free_cash_flow_to_firm", (), unit, "FCFF must be reported or explicitly supplied; CFO-capex is not silently treated as FCFF"), basis
    reported = snapshot.facts.get("free_cash_flow_to_equity")
    if reported and reported.value is not None:
        return reported, "fcfe"
    ocf = snapshot.facts.get("operating_cash_flow", DerivedFact(None, "reported(operating_cash_flow)", (), unit, "missing operating_cash_flow"))
    capex = snapshot.facts.get("capital_expenditure", DerivedFact(None, "reported(capital_expenditure)", (), unit, "missing capital_expenditure"))
    return _subtract(ocf, capex, "operating_cash_flow - capital_expenditure (levered FCFE proxy)", unit), "fcfe"


def _net_debt(snapshot: Optional[Snapshot], unit: str) -> DerivedFact:
    if snapshot is None:
        return DerivedFact(None, "interest_bearing_debt - cash", (), unit, "TTM/latest balance unavailable")
    debt = snapshot.facts.get("interest_bearing_debt", DerivedFact(None, "reported(interest_bearing_debt)", (), unit, "missing debt"))
    cash = snapshot.facts.get("cash", DerivedFact(None, "reported(cash)", (), unit, "missing cash"))
    return _subtract(debt, cash, "interest_bearing_debt - cash", unit)


def _dcf(snapshot: Optional[Snapshot], valuation: Mapping[str, Any], unit: str) -> dict[str, Any]:
    config = valuation.get("dcf") or {}
    if not isinstance(config, Mapping) or not config:
        return {"status": "not_requested", "scenarios": [], "reason": "No DCF assumptions supplied."}
    base, basis = _valuation_cash_flow(snapshot, config, unit)
    shares = _decimal(valuation.get("shares_outstanding"), "valuation.shares_outstanding")
    market_price = _decimal(valuation.get("market_price"), "valuation.market_price")
    net_debt = _net_debt(snapshot, unit)
    raw_scenarios = config.get("scenarios") or []
    if isinstance(raw_scenarios, Mapping):
        raw_scenarios = [dict(value, name=key) for key, value in raw_scenarios.items()]
    results: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_scenarios):
        if not isinstance(raw, Mapping):
            results.append({"name": f"scenario_{index + 1}", "status": "invalid", "reason": "Scenario must be an object."})
            continue
        name = str(raw.get("name") or f"scenario_{index + 1}")
        years = int(raw.get("forecast_years", config.get("forecast_years", 5)))
        discount = _decimal(raw.get("discount_rate", config.get("discount_rate")), f"dcf.{name}.discount_rate")
        terminal_growth = _decimal(raw.get("terminal_growth_rate", config.get("terminal_growth_rate")), f"dcf.{name}.terminal_growth_rate")
        supplied_growths = raw.get("growth_rates")
        if supplied_growths is not None:
            growth_rates = [_decimal(value, f"dcf.{name}.growth_rates") for value in supplied_growths]
            if any(value is None for value in growth_rates):
                growth_rates = []
            years = len(growth_rates)
        else:
            growth = _decimal(raw.get("growth_rate", config.get("growth_rate")), f"dcf.{name}.growth_rate")
            growth_rates = [growth] * years if growth is not None and years > 0 else []
        assumption_inputs = [
            _assumption_lineage("discount_rate", discount),
            _assumption_lineage("terminal_growth_rate", terminal_growth),
            *[_assumption_lineage(f"growth_rate_year_{year}", growth) for year, growth in enumerate(growth_rates, 1)],
            _assumption_lineage("shares_outstanding", shares),
        ]
        reason = ""
        if base.value is None:
            reason = base.missing_reason or "base cash flow missing"
        elif base.value <= ZERO:
            reason = "Base cash flow is non-positive; a stable-growth DCF is not meaningful."
        elif not growth_rates or discount is None or terminal_growth is None:
            reason = "Forecast years, growth, discount, or terminal growth assumption missing."
        elif discount <= ZERO:
            reason = "Discount rate must be positive."
        elif terminal_growth <= -ONE:
            reason = "Terminal growth rate must be greater than -100%."
        elif discount <= terminal_growth:
            reason = "Discount rate must be greater than terminal growth rate."
        elif any(growth <= -ONE for growth in growth_rates):
            reason = "Each growth rate must be greater than -100%."
        if reason:
            results.append({"name": name, "status": "not_calculable", "reason": reason, "cash_flow_basis": basis, "formula": "PV(forecast cash flows) + PV(terminal value)", "inputs": _inputs(base) + assumption_inputs})
            continue
        with localcontext() as context:
            context.prec = 36
            forecast, pv_forecast, pv_terminal = _scenario_values(base.value, growth_rates, discount, terminal_growth)
            value_before_debt = pv_forecast + pv_terminal
            if basis == "fcff":
                if net_debt.value is None:
                    results.append({"name": name, "status": "not_calculable", "reason": "FCFF valuation needs debt and cash to bridge enterprise value to equity value.", "cash_flow_basis": basis, "formula": "enterprise_value - net_debt", "inputs": _inputs(base, net_debt) + assumption_inputs})
                    continue
                enterprise_value = value_before_debt
                equity_value = enterprise_value - net_debt.value
            else:
                enterprise_value = None
                equity_value = value_before_debt
            per_share = equity_value / shares if shares is not None and shares > ZERO else None
            upside = per_share / market_price - ONE if per_share is not None and market_price is not None and market_price > ZERO else None
            results.append(
                {
                    "name": name,
                    "status": "ok",
                    "cash_flow_basis": basis,
                    "forecast_years": years,
                    "forecast_cash_flows": [_number(value) for value in forecast],
                    "present_value_forecast": _number(pv_forecast),
                    "present_value_terminal": _number(pv_terminal),
                    "terminal_value_share": _number(pv_terminal / value_before_debt) if value_before_debt != ZERO else None,
                    "enterprise_value": _number(enterprise_value),
                    "equity_value": _number(equity_value),
                    "value_per_share": _number(per_share),
                    "market_price": _number(market_price),
                    "upside_downside": _number(upside),
                    "formula": "CF_t = CF_(t-1)×(1+g_t); terminal = CF_n×(1+g_terminal)/(discount-g_terminal); FCFE gives equity value directly, FCFF subtracts net debt",
                    "inputs": _inputs(base, net_debt if basis == "fcff" else None) + assumption_inputs,
                    "warnings": ["Terminal value exceeds 75% of total present value."] if value_before_debt != ZERO and pv_terminal / value_before_debt > Decimal("0.75") else [],
                }
            )
    return {
        "status": "ok" if any(item.get("status") == "ok" for item in results) else "not_calculable",
        "base_cash_flow": _fact_metric(base),
        "cash_flow_basis": basis,
        "scenarios": results,
        "note": "DCF is assumption-sensitive. FCFE uses a levered CFO-capex proxy unless an explicit FCFE is supplied; FCFF is never inferred from that proxy.",
    }


def _dcf_sensitivity(snapshot: Optional[Snapshot], valuation: Mapping[str, Any], unit: str) -> dict[str, Any]:
    config = valuation.get("sensitivity") or {}
    if not isinstance(config, Mapping) or not config:
        return {"status": "not_requested", "reason": "No DCF sensitivity assumptions supplied."}
    base, basis = _valuation_cash_flow(snapshot, config, unit)
    shares = _decimal(valuation.get("shares_outstanding"), "valuation.shares_outstanding")
    net_debt = _net_debt(snapshot, unit)
    growth_rates = [_decimal(value, "sensitivity.growth_rates") for value in (config.get("growth_rates") or [])]
    discount_rates = [_decimal(value, "sensitivity.discount_rates") for value in (config.get("discount_rates") or [])]
    terminal_growth = _decimal(config.get("terminal_growth_rate"), "sensitivity.terminal_growth_rate")
    years = int(config.get("forecast_years", 5))
    inputs = _inputs(base, net_debt if basis == "fcff" else None) + [
        _assumption_lineage("shares_outstanding", shares, "market input"),
        _assumption_lineage("terminal_growth_rate", terminal_growth),
        *[_assumption_lineage(f"growth_rate_{index + 1}", value) for index, value in enumerate(growth_rates)],
        *[_assumption_lineage(f"discount_rate_{index + 1}", value) for index, value in enumerate(discount_rates)],
    ]
    reason = ""
    if base.value is None or base.value <= ZERO:
        reason = base.missing_reason or "A positive base cash flow is required."
    elif shares is None or shares <= ZERO:
        reason = "Positive shares outstanding are required."
    elif not growth_rates or not discount_rates or terminal_growth is None:
        reason = "Growth, discount, and terminal assumptions are required."
    elif any(value is None or value <= -ONE for value in growth_rates):
        reason = "Growth rates must be greater than -100%."
    elif any(value is None or value <= terminal_growth for value in discount_rates):
        reason = "Every discount rate must exceed terminal growth."
    elif not 1 <= len(growth_rates) <= 9 or not 1 <= len(discount_rates) <= 9 or not 1 <= years <= 15:
        reason = "Sensitivity grid supports 1-9 rows/columns and 1-15 forecast years."
    elif basis == "fcff" and net_debt.value is None:
        reason = "FCFF sensitivity needs debt and cash."
    if reason:
        return {"status": "not_calculable", "reason": reason, "formula": "DCF grid across growth and discount assumptions", "inputs": inputs}
    rows = []
    for discount in discount_rates:
        values = []
        for growth in growth_rates:
            forecast, pv_forecast, pv_terminal = _scenario_values(base.value, [growth] * years, discount, terminal_growth)  # type: ignore[list-item, arg-type]
            value = pv_forecast + pv_terminal
            equity = value - net_debt.value if basis == "fcff" else value
            values.append(_number(equity / shares))
        rows.append({"discount_rate": _number(discount), "values": values})
    return {
        "status": "ok",
        "cash_flow_basis": basis,
        "growth_rates": [_number(value) for value in growth_rates],
        "discount_rates": [_number(value) for value in discount_rates],
        "terminal_growth_rate": _number(terminal_growth),
        "forecast_years": years,
        "rows": rows,
        "formula": "value per share = [PV forecast cash flows + PV terminal value - net debt when FCFF] / shares",
        "inputs": inputs,
        "note": "This matrix exposes assumption sensitivity; it is not a probability distribution or target-price promise.",
    }


def _ddm(snapshot: Optional[Snapshot], valuation: Mapping[str, Any], unit: str) -> dict[str, Any]:
    config = valuation.get("ddm") or {}
    if not isinstance(config, Mapping) or not config:
        return {"status": "not_requested", "scenarios": [], "reason": "No DDM assumptions supplied."}
    shares = _decimal(valuation.get("shares_outstanding"), "valuation.shares_outstanding")
    market_price = _decimal(valuation.get("market_price"), "valuation.market_price")
    explicit_dps = _decimal(config.get("dividend_per_share"), "ddm.dividend_per_share")
    dividends = snapshot.facts.get("dividends_paid") if snapshot else None
    dps = explicit_dps
    dps_inputs = [_assumption_lineage("dividend_per_share", explicit_dps)] if explicit_dps is not None else _inputs(dividends)
    dps_formula = "explicit trailing dividend per share"
    if dps is None and dividends and dividends.value is not None and shares is not None and shares > ZERO:
        dps = dividends.value / shares
        dps_formula = "trailing cash dividends paid / shares outstanding"
        dps_inputs += [_assumption_lineage("shares_outstanding", shares, "market input")]
    raw_scenarios = config.get("scenarios") or []
    results = []
    for index, raw in enumerate(raw_scenarios):
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or f"scenario_{index + 1}")
        growth = _decimal(raw.get("growth_rate"), f"ddm.{name}.growth_rate")
        cost = _decimal(raw.get("cost_of_equity"), f"ddm.{name}.cost_of_equity")
        terminal = _decimal(raw.get("terminal_growth_rate"), f"ddm.{name}.terminal_growth_rate")
        years = int(raw.get("forecast_years", 5))
        assumption_inputs = dps_inputs + [
            _assumption_lineage("growth_rate", growth),
            _assumption_lineage("cost_of_equity", cost),
            _assumption_lineage("terminal_growth_rate", terminal),
        ]
        reason = ""
        if dps is None or dps <= ZERO:
            reason = "Positive trailing dividend per share is required."
        elif growth is None or cost is None or terminal is None:
            reason = "Growth, cost of equity, and terminal growth are required."
        elif growth <= -ONE or terminal <= -ONE or cost <= terminal or not 1 <= years <= 15:
            reason = "DDM requires valid growth, 1-15 years, and cost of equity above terminal growth."
        if reason:
            results.append({"name": name, "status": "not_calculable", "reason": reason, "inputs": assumption_inputs})
            continue
        current = dps
        pv_dividends = ZERO
        forecast = []
        for year in range(1, years + 1):
            current *= ONE + growth
            forecast.append(current)
            pv_dividends += current / ((ONE + cost) ** year)
        terminal_value = current * (ONE + terminal) / (cost - terminal)
        pv_terminal = terminal_value / ((ONE + cost) ** years)
        value = pv_dividends + pv_terminal
        results.append({
            "name": name,
            "status": "ok",
            "value_per_share": _number(value),
            "market_price": _number(market_price),
            "upside_downside": _number(value / market_price - ONE) if market_price and market_price > ZERO else None,
            "forecast_dividends_per_share": [_number(item) for item in forecast],
            "present_value_dividends": _number(pv_dividends),
            "present_value_terminal": _number(pv_terminal),
            "terminal_value_share": _number(pv_terminal / value) if value != ZERO else None,
            "formula": "PV(explicit dividends) + PV[DPS_n×(1+g_terminal)/(cost_of_equity-g_terminal)]",
            "inputs": assumption_inputs,
            "warnings": ["Terminal value exceeds 75% of DDM value."] if value != ZERO and pv_terminal / value > Decimal("0.75") else [],
        })
    return {
        "status": "ok" if any(item.get("status") == "ok" for item in results) else "not_calculable",
        "dividend_per_share": _number(dps),
        "dividend_formula": dps_formula,
        "scenarios": results,
        "note": "DDM is intended for sustainable distributions; a high current yield cannot substitute for dividend coverage and balance-sheet review.",
    }


def _relative_valuation(snapshot: Optional[Snapshot], valuation: Mapping[str, Any], unit: str) -> dict[str, Any]:
    config = valuation.get("relative") or {}
    if not isinstance(config, Mapping) or not config:
        return {"status": "not_requested", "scenarios": [], "reason": "No relative-valuation assumptions supplied."}
    shares = _decimal(valuation.get("shares_outstanding"), "valuation.shares_outstanding")
    market_price = _decimal(valuation.get("market_price"), "valuation.market_price")
    net_income = snapshot.facts.get("net_income") if snapshot else None
    equity = snapshot.facts.get("total_equity") if snapshot else None
    eps = net_income.value / shares if net_income and net_income.value is not None and shares and shares > ZERO else None
    bvps = equity.value / shares if equity and equity.value is not None and shares and shares > ZERO else None
    base_inputs = _inputs(net_income, equity) + [_assumption_lineage("shares_outstanding", shares, "market input")]
    results = []
    for index, raw in enumerate(config.get("scenarios") or []):
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or f"scenario_{index + 1}")
        target_pe = _decimal(raw.get("target_pe"), f"relative.{name}.target_pe")
        target_pb = _decimal(raw.get("target_pb"), f"relative.{name}.target_pb")
        pe_value = eps * target_pe if eps is not None and target_pe is not None and target_pe > ZERO else None
        pb_value = bvps * target_pb if bvps is not None and target_pb is not None and target_pb > ZERO else None
        available = [value for value in (pe_value, pb_value) if value is not None]
        low, high = (min(available), max(available)) if available else (None, None)
        results.append({
            "name": name,
            "status": "ok" if available else "not_calculable",
            "pe_anchor": _number(pe_value),
            "pb_anchor": _number(pb_value),
            "value_range_low": _number(low),
            "value_range_high": _number(high),
            "market_price": _number(market_price),
            "target_pe": _number(target_pe),
            "target_pb": _number(target_pb),
            "formula": "PE anchor = TTM EPS × target PE; PB anchor = latest BVPS × target PB; anchors are shown as a range, not averaged into a false-precision point",
            "inputs": base_inputs + [_assumption_lineage("target_pe", target_pe), _assumption_lineage("target_pb", target_pb)],
            "reason": "" if available else "EPS/BVPS or target multiples are unavailable.",
        })
    return {
        "status": "ok" if any(item.get("status") == "ok" for item in results) else "not_calculable",
        "earnings_per_share": _number(eps),
        "book_value_per_share": _number(bvps),
        "scenarios": results,
        "note": "Target multiples must be justified by comparable companies, cycle position, growth and accounting quality; the engine does not invent peer medians.",
    }


def _reverse_dcf(snapshot: Optional[Snapshot], valuation: Mapping[str, Any], unit: str) -> dict[str, Any]:
    config = valuation.get("reverse_dcf") or {}
    if not isinstance(config, Mapping) or not config:
        return {"status": "not_requested", "reason": "No reverse-DCF assumptions supplied."}
    base, basis = _valuation_cash_flow(snapshot, config, unit)
    price = _decimal(valuation.get("market_price"), "valuation.market_price")
    shares = _decimal(valuation.get("shares_outstanding"), "valuation.shares_outstanding")
    discount = _decimal(config.get("discount_rate"), "reverse_dcf.discount_rate")
    terminal_growth = _decimal(config.get("terminal_growth_rate"), "reverse_dcf.terminal_growth_rate")
    low = _decimal(config.get("growth_lower_bound", "-0.50"), "reverse_dcf.growth_lower_bound")
    high = _decimal(config.get("growth_upper_bound", "1.00"), "reverse_dcf.growth_upper_bound")
    years = int(config.get("forecast_years", 5))
    net_debt = _net_debt(snapshot, unit)
    inputs = _inputs(base, net_debt if basis == "fcff" else None) + [
        _assumption_lineage("market_price", price, "market input"),
        _assumption_lineage("shares_outstanding", shares, "market input"),
        _assumption_lineage("discount_rate", discount),
        _assumption_lineage("terminal_growth_rate", terminal_growth),
        _assumption_lineage("growth_lower_bound", low),
        _assumption_lineage("growth_upper_bound", high),
    ]
    reason = ""
    if base.value is None or base.value <= ZERO:
        reason = "A positive base cash flow is required."
    elif price is None or shares is None or price <= ZERO or shares <= ZERO:
        reason = "Positive market price and shares outstanding are required."
    elif discount is None or terminal_growth is None:
        reason = "Discount and terminal growth rates are required."
    elif discount <= ZERO:
        reason = "Discount rate must be positive."
    elif terminal_growth <= -ONE:
        reason = "Terminal growth rate must be greater than -100%."
    elif discount <= terminal_growth:
        reason = "Discount rate must be greater than terminal growth rate."
    elif low is None or high is None or low <= -ONE or low >= high:
        reason = "Valid growth bounds greater than -100% are required."
    elif years <= 0:
        reason = "forecast_years must be positive."
    elif basis == "fcff" and net_debt.value is None:
        reason = "FCFF reverse DCF needs debt and cash."
    if reason:
        return {"status": "not_calculable", "reason": reason, "cash_flow_basis": basis, "formula": "solve DCF(g) = market-implied value", "inputs": inputs}
    target_equity = price * shares
    target = target_equity + net_debt.value if basis == "fcff" else target_equity

    def value_at(growth: Decimal) -> Decimal:
        _, pv_forecast, pv_terminal = _scenario_values(base.value, [growth] * years, discount, terminal_growth)
        return pv_forecast + pv_terminal

    with localcontext() as context:
        context.prec = 40
        lower_value, upper_value = value_at(low), value_at(high)
        if target < lower_value or target > upper_value:
            return {
                "status": "no_solution_in_bounds",
                "reason": "Market-implied value lies outside the configured growth search interval.",
                "cash_flow_basis": basis,
                "target_value": _number(target),
                "value_at_lower_bound": _number(lower_value),
                "value_at_upper_bound": _number(upper_value),
                "formula": "solve DCF(constant forecast growth) = market capitalisation (+ net debt for FCFF)",
                "inputs": inputs,
            }
        left, right = low, high
        for _ in range(160):
            midpoint = (left + right) / Decimal("2")
            if value_at(midpoint) < target:
                left = midpoint
            else:
                right = midpoint
        implied = (left + right) / Decimal("2")
        solved_value = value_at(implied)
    return {
        "status": "ok",
        "cash_flow_basis": basis,
        "implied_constant_growth_rate": _number(implied),
        "forecast_years": years,
        "target_value": _number(target),
        "solved_value": _number(solved_value),
        "residual": _number(solved_value - target),
        "formula": "solve DCF(constant forecast growth) = market capitalisation (+ net debt for FCFF), using bisection",
        "inputs": inputs,
        "interpretation": "This is the growth embedded in the supplied price under the supplied discount and terminal assumptions, not a growth forecast.",
    }


def _is_financial_industry(company: CompanyData) -> bool:
    industry = company.industry.strip().lower()
    return any(keyword in industry for keyword in FINANCIAL_INDUSTRY_KEYWORDS)


def _is_bank_industry(company: CompanyData) -> bool:
    industry = company.industry.strip().lower()
    company_type = str(company.valuation.get("company_type") or "")
    return company_type == "2" or "银行" in industry or "bank" in industry


def _snapshot_payload(snapshot: Snapshot, prior: Optional[Snapshot], unit: str) -> dict[str, Any]:
    return {
        "label": snapshot.label,
        "end_date": snapshot.end_date,
        "fiscal_year": snapshot.fiscal_year,
        "construction": snapshot.construction,
        "period_type": snapshot.period_type,
        "duration_months": snapshot.duration_months,
        "cumulative": snapshot.cumulative,
        "audited": snapshot.audited,
        "report_scope": snapshot.report_scope or None,
        "restated": snapshot.restated,
        "basis": "reported_fy" if snapshot.period_type == "FY" else "constructed_ttm" if snapshot.period_type == "TTM" else "reported_cumulative_ytd" if snapshot.cumulative else "reported_discrete_quarter",
        "metrics": _snapshot_metrics(snapshot, prior, unit),
    }


def analyze_company(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Analyse canonical financial data and return a JSON-serialisable report.

    Unsupported financial industries are labelled and deliberately do not receive
    industrial-company ratios, Piotroski, DuPont, or DCF output.
    """

    company = CompanyData.from_input(raw)
    if _is_bank_industry(company):
        # Imported lazily so the bank plug-in can reuse the canonical fact and
        # Decimal helpers without creating a module-import cycle.
        from .bank_engine import analyze_bank

        return analyze_bank(company)
    from .financial_sector_engine import analyze_financial_sector_company, classify_financial_sector_template

    financial_sector_template = classify_financial_sector_template(company)
    if financial_sector_template:
        return analyze_financial_sector_company(company, financial_sector_template)
    from .sector_engine import analyze_sector_company, classify_sector_template

    sector_template = classify_sector_template(company)
    if sector_template:
        return analyze_sector_company(company, sector_template)
    ttm, ttm_notes = _build_ttm(company)
    quality = _data_quality(company, ttm_notes)
    annual_periods = _annual_periods(company)
    annual_snapshots = [_snapshot(period, company.unit) for period in annual_periods]
    interim_periods = [period for period in company.periods if period.duration_months < 12]
    interim_payload = []
    for period in interim_periods:
        comparable = _find_comparable(company.periods, period)
        interim_payload.append(
            _snapshot_payload(
                _snapshot(period, company.unit),
                _snapshot(comparable, company.unit) if comparable else None,
                company.unit,
            )
        )
    unsupported = _is_financial_industry(company)
    support = {
        "status": "unsupported_financial_industry" if unsupported else "supported_non_financial",
        "industry": company.industry,
        "reason": (
            "Banks, insurers, brokers, and similar financial firms require sector-specific balance-sheet, capital, and cash-flow models. Generic industrial ratios and DCF are intentionally disabled."
            if unsupported
            else "Generic non-financial corporate model enabled."
        ),
    }
    base = {
        "schema_version": SCHEMA_VERSION,
        "engine": "deterministic_decimal_finance_engine",
        "company": {
            "symbol": company.symbol,
            "name": company.name,
            "industry": company.industry,
            "currency": company.currency,
            "unit": company.unit,
        },
        "market": {
            "market_price": company.valuation.get("market_price"),
            "price_as_of": company.valuation.get("price_as_of"),
            "market_cap": company.valuation.get("market_cap"),
            "shares_outstanding": company.valuation.get("shares_outstanding"),
            "pe": company.valuation.get("pe"),
            "pe_ttm": company.valuation.get("pe_ttm"),
            "pb": company.valuation.get("pb"),
            "ps": company.valuation.get("ps"),
            "ps_ttm": company.valuation.get("ps_ttm"),
            "dividend_yield": company.valuation.get("dividend_yield"),
            "dividend_yield_ttm": company.valuation.get("dividend_yield_ttm"),
            "source": company.valuation.get("source"),
        },
        "as_of": company.periods[-1].end_date if company.periods else None,
        "support": support,
        "data_quality": quality,
        "methodology": {
            "missing_vs_zero": "Missing is null/status=missing; reported zero remains numeric zero and may produce status=not_calculable for division.",
            "period_policy": "FY uses reported 12-month cumulative facts. TTM uses latest FY, four discrete quarters, or FY + current YTD - prior YTD.",
            "precision": "All calculations use Python Decimal; values are converted to JSON numbers only at the output boundary.",
            "decision_policy": "Flags are deterministic screens, not accusations of fraud or personalised buy/sell advice.",
        },
    }
    if unsupported:
        base.update(
            {
                "periods": {"annual": [], "ttm": None},
                "flags": [],
                "piotroski": {"status": "unsupported", "reason": support["reason"]},
                "dupont": {"status": "unsupported", "reason": support["reason"]},
                "valuation": {
                    "dcf": {"status": "unsupported", "reason": support["reason"]},
                    "sensitivity": {"status": "unsupported", "reason": support["reason"]},
                    "reverse_dcf": {"status": "unsupported", "reason": support["reason"]},
                    "ddm": {"status": "unsupported", "reason": support["reason"]},
                    "relative": {"status": "unsupported", "reason": support["reason"]},
                },
            }
        )
        base["changes"] = detect_financial_changes(base)
        return _json_safe(base)

    annual_payload = []
    for index, snapshot in enumerate(annual_snapshots):
        prior = annual_snapshots[index - 1] if index else None
        annual_payload.append(_snapshot_payload(snapshot, prior, company.unit))
    latest_annual = annual_snapshots[-1] if annual_snapshots else None
    prior_annual = annual_snapshots[-2] if len(annual_snapshots) >= 2 else None
    if ttm and latest_annual and ttm.end_date == latest_annual.end_date:
        ttm_prior = prior_annual
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
            ttm_prior = constructed_prior_ttm or _balance_only_snapshot(comparable, company.unit)
        else:
            ttm_prior = None
    else:
        ttm_prior = latest_annual
    analysis_snapshot = ttm or latest_annual
    analysis_prior = ttm_prior if ttm else prior_annual
    flags = _financial_flags(analysis_snapshot, analysis_prior, company.audit, company.unit) if analysis_snapshot else []
    base.update(
        {
            "periods": {
                "annual": annual_payload,
                "interim": interim_payload,
                "ttm": _snapshot_payload(ttm, ttm_prior, company.unit) if ttm else None,
                "ttm_comparable": _snapshot_payload(ttm_prior, None, company.unit) if ttm and ttm_prior and (not latest_annual or ttm.end_date != latest_annual.end_date) else None,
            },
            "flags": sorted(flags, key=lambda item: (0 if item["severity"] == "red" else 1, item["id"])),
            "piotroski": _piotroski(latest_annual, prior_annual, company.unit) if latest_annual else {"status": "incomplete", "reason": "No annual period supplied."},
            "dupont": _dupont(analysis_snapshot, analysis_prior, company.unit) if analysis_snapshot else {"status": "incomplete", "reason": "No usable period supplied."},
            "valuation": {
                "dcf": _dcf(ttm, company.valuation, company.unit),
                "sensitivity": _dcf_sensitivity(ttm, company.valuation, company.unit),
                "reverse_dcf": _reverse_dcf(ttm, company.valuation, company.unit),
                "ddm": _ddm(ttm, company.valuation, company.unit),
                "relative": _relative_valuation(ttm, company.valuation, company.unit),
            },
        }
    )
    base["changes"] = detect_financial_changes(base)
    return _json_safe(base)


def demo_company() -> dict[str, Any]:
    """Fictional A-share industrial company with FY and YTD data.

    Values are in CNY million except shares (million shares) and per-share price.
    The company does not represent or resemble an investment recommendation.
    """

    def period(
        end_date: str,
        fiscal_year: int,
        period_type: str,
        months: int,
        facts: Mapping[str, Any],
        *,
        audited: bool,
    ) -> dict[str, Any]:
        document = f"Fictional Huaxing Equipment FY{fiscal_year} {period_type} report"
        return {
            "end_date": end_date,
            "fiscal_year": fiscal_year,
            "period_type": period_type,
            "duration_months": months,
            "cumulative": True,
            "audited": audited,
            "source": {"document": document, "filing_id": f"DEMO-{fiscal_year}-{period_type}"},
            "facts": {
                name: {
                    "value": value,
                    "unit": "CNY million" if name != "shares_outstanding" else "million shares",
                    "source": {"document": document, "page": 70 + index, "filing_id": f"DEMO-{fiscal_year}-{period_type}"},
                }
                for index, (name, value) in enumerate(facts.items())
            },
        }

    fy2023 = {
        "revenue": "8200", "cost_of_revenue": "5904", "gross_profit": "2296", "operating_profit": "960", "ebit": "980",
        "pretax_income": "900", "income_tax_expense": "135", "net_income": "765", "operating_cash_flow": "850", "capital_expenditure": "430",
        "dividends_paid": "230", "interest_expense": "90", "total_assets": "10500", "total_liabilities": "5600", "total_equity": "4900",
        "cash": "1200", "interest_bearing_debt": "2600", "current_assets": "4800", "current_liabilities": "3000", "accounts_receivable": "1250",
        "inventory": "1450", "goodwill": "260", "shares_outstanding": "1000",
    }
    fy2024 = {
        "revenue": "9000", "cost_of_revenue": "6300", "gross_profit": "2700", "operating_profit": "1170", "ebit": "1190",
        "pretax_income": "1100", "income_tax_expense": "165", "net_income": "935", "operating_cash_flow": "1080", "capital_expenditure": "470",
        "dividends_paid": "300", "interest_expense": "82", "total_assets": "11200", "total_liabilities": "5700", "total_equity": "5500",
        "cash": "1450", "interest_bearing_debt": "2450", "current_assets": "5200", "current_liabilities": "3050", "accounts_receivable": "1320",
        "inventory": "1500", "goodwill": "250", "shares_outstanding": "1000",
    }
    q3_2024 = {
        "revenue": "6600", "cost_of_revenue": "4650", "gross_profit": "1950", "operating_profit": "840", "ebit": "855",
        "pretax_income": "790", "income_tax_expense": "119", "net_income": "671", "operating_cash_flow": "730", "capital_expenditure": "330",
        "dividends_paid": "300", "interest_expense": "62", "total_assets": "10900", "total_liabilities": "5550", "total_equity": "5350",
        "cash": "1300", "interest_bearing_debt": "2500", "current_assets": "5000", "current_liabilities": "2950", "accounts_receivable": "1370",
        "inventory": "1520", "goodwill": "255", "shares_outstanding": "1000",
    }
    q3_2025 = {
        "revenue": "7350", "cost_of_revenue": "5072", "gross_profit": "2278", "operating_profit": "1050", "ebit": "1065",
        "pretax_income": "990", "income_tax_expense": "149", "net_income": "841", "operating_cash_flow": "940", "capital_expenditure": "360",
        "dividends_paid": "340", "interest_expense": "55", "total_assets": "11900", "total_liabilities": "5750", "total_equity": "6150",
        "cash": "1700", "interest_bearing_debt": "2300", "current_assets": "5600", "current_liabilities": "3000", "accounts_receivable": "1430",
        "inventory": "1580", "goodwill": "240", "shares_outstanding": "995",
    }
    return {
        "company": {"symbol": "600888", "name": "华星装备（虚构示例）", "industry": "Industrial Machinery", "currency": "CNY", "unit": "CNY million"},
        "periods": [
            period("2023-12-31", 2023, "FY", 12, fy2023, audited=True),
            period("2024-09-30", 2024, "Q3", 9, q3_2024, audited=False),
            period("2024-12-31", 2024, "FY", 12, fy2024, audited=True),
            period("2025-09-30", 2025, "Q3", 9, q3_2025, audited=False),
        ],
        "audit": {"opinion": "standard unqualified", "source": {"document": "Fictional FY2024 auditor report", "page": 5}},
        "valuation": {
            "market_price": "12.50",
            "shares_outstanding": "995",
            "dcf": {
                "cash_flow_basis": "fcfe",
                "forecast_years": 5,
                "scenarios": [
                    {"name": "stress", "growth_rate": "0.02", "discount_rate": "0.11", "terminal_growth_rate": "0.01"},
                    {"name": "base", "growth_rate": "0.06", "discount_rate": "0.095", "terminal_growth_rate": "0.025"},
                    {"name": "optimistic", "growth_rate": "0.10", "discount_rate": "0.09", "terminal_growth_rate": "0.03"},
                ],
            },
            "reverse_dcf": {"cash_flow_basis": "fcfe", "forecast_years": 5, "discount_rate": "0.095", "terminal_growth_rate": "0.025", "growth_lower_bound": "-0.30", "growth_upper_bound": "0.60"},
        },
    }


__all__ = [
    "CompanyData",
    "FinancialFact",
    "FinancialPeriod",
    "analyze_company",
    "demo_company",
]
