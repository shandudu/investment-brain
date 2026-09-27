"""Free-first A-share provider adapters.

AKShare is used for public financial statements and daily prices. BaoStock is
kept as a lighter fallback for historical profit/share data when AKShare's
source page is unavailable. Both providers are convenience sources: every
fact is labelled as provisional and the UI must show its retrieval time.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping
import importlib.util
import sys

_BUNDLED_FREE_DATA = Path(__file__).resolve().parent.parent / "vendor" / "free-data"
if _BUNDLED_FREE_DATA.is_dir() and str(_BUNDLED_FREE_DATA) not in sys.path:
    # Keep optional providers on the spacious project drive when the system
    # Python volume is full.  Append instead of prepending so these optional
    # wheels cannot shadow the runtime's document/PDF dependencies.
    sys.path.append(str(_BUNDLED_FREE_DATA))

FREE_DOCS = {
    "akshare": "https://github.com/akfamily/akshare",
    "baostock": "https://www.baostock.com/",
}

# A small local identity catalogue keeps free-source snapshots tied to the
# security selected in the UI even when the public statement endpoint omits
# the issuer name.  It is deliberately limited to the built-in examples; an
# unknown symbol remains code-only and is still treated as provisional.
KNOWN_COMPANY_NAMES = {
    "000001": "平安银行股份有限公司",
    "600036": "招商银行股份有限公司",
    "601088": "中国神华能源股份有限公司",
    "600519": "贵州茅台酒股份有限公司",
    "000333": "美的集团股份有限公司",
}


def _company_name(ts_code: str) -> str:
    return KNOWN_COMPANY_NAMES.get(ts_code.split(".", 1)[0], ts_code.split(".", 1)[0])


class FreeProviderError(RuntimeError):
    pass


def free_provider_available() -> bool:
    """Whether at least one optional free adapter is installed."""
    return bool(importlib.util.find_spec("akshare") or importlib.util.find_spec("baostock"))


def _as_date(value: Any) -> date:
    if value in (None, ""):
        return date.today()
    text = str(value).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    raise ValueError("as_of 应为 YYYY-MM-DD 或 YYYYMMDD")


def _number(value: Any) -> float | None:
    if value in (None, "", "--", "NaN"):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _records(frame: Any) -> list[dict[str, Any]]:
    if frame is None or not hasattr(frame, "to_dict"):
        return []
    try:
        return [dict(row) for row in frame.to_dict("records")]
    except Exception as exc:  # pragma: no cover - defensive boundary around third-party dataframes
        raise FreeProviderError(f"免费数据源返回格式异常：{exc}") from exc


def _value(row: Mapping[str, Any], names: Iterable[str]) -> tuple[Any, str]:
    keys = [str(key) for key in row]
    fallback: tuple[Any, str] | None = None

    def present(value: Any) -> bool:
        if value is None:
            return False
        text = str(value).strip().lower()
        return text not in {"", "nan", "nat", "none", "null"}

    for name in names:
        if name in row:
            candidate = (row[name], name)
            fallback = fallback or candidate
            if present(row[name]):
                return candidate
    for name in names:
        for key in keys:
            if name in key:
                candidate = (row[key], key)
                fallback = fallback or candidate
                if present(row[key]):
                    return candidate
    return fallback or (None, "")


def _report_date(row: Mapping[str, Any]) -> date | None:
    value, _ = _value(row, ("报告期", "报告日", "报告日期", "统计日期", "截止日期", "日期"))
    if value is None and row:
        value = next(iter(row.values()))
    try:
        return _as_date(value)
    except ValueError:
        return None


def _fact(value: Any, label: str, statement: str, report_end: date) -> dict[str, Any] | None:
    number = _number(value)
    if number is None:
        return None
    return {
        "value": number / 1_000_000,
        "unit": "CNY million",
        "statement": statement,
        "as_reported_label": label,
        "source": {
            "provider": "AKShare",
            "api_name": "stock_financial_report_sina",
            "documentation": FREE_DOCS["akshare"],
            "report_end": report_end.isoformat(),
            "provisional": True,
        },
    }


def _share_fact(value: Any, label: str, report_end: date) -> dict[str, Any] | None:
    number = _number(value)
    if number is None:
        return None
    return {
        "value": number / 1_000_000,
        "unit": "million shares",
        "statement": "balancesheet",
        "as_reported_label": label,
        "source": {
            "provider": "AKShare",
            "api_name": "stock_financial_report_sina",
            "documentation": FREE_DOCS["akshare"],
            "report_end": report_end.isoformat(),
            "provisional": True,
        },
    }


def _summed_statement_fact(
    row: Mapping[str, Any],
    labels: tuple[str, ...],
    reported_label: str,
    report_end: date,
) -> dict[str, Any] | None:
    """Sum a debt bucket only when every named component is reported."""

    parts: list[tuple[str, float]] = []
    for label in labels:
        raw, matched = _value(row, (label,))
        number = _number(raw)
        if number is None:
            return None
        parts.append((matched or label, number))
    fact = _fact(sum(value for _, value in parts), reported_label, "balancesheet", report_end)
    if fact:
        fact["source"]["derived"] = True
        fact["source"]["calculation"] = " + ".join(label for label, _ in parts)
        fact["source"]["completeness"] = "complete; all debt components reported"
    return fact


def _baostock_latest_quote(ts_code: str, cutoff: date) -> tuple[float, str] | None:
    try:
        import baostock as bs
        code = ts_code.split(".", 1)[0]
        market_code = f"{'sh' if ts_code.endswith('.SH') else 'sz'}.{code}"
        login = bs.login()
        if str(login.error_code) != "0":
            return None
        try:
            result = bs.query_history_k_data_plus(
                market_code, "date,close", start_date=(cutoff - timedelta(days=120)).isoformat(),
                end_date=cutoff.isoformat(), frequency="d", adjustflag="3",
            )
            latest = None
            while result.next():
                row = dict(zip(result.fields, result.get_row_data()))
                close = _number(row.get("close"))
                if row.get("date") and close is not None:
                    latest = (close, row["date"])
            return latest
        finally:
            bs.logout()
    except Exception:
        return None


def _akshare_quote_window(cutoff: date) -> tuple[str, str]:
    """Use the requested cutoff as the quote end date instead of silently lagging it."""

    return (cutoff - timedelta(days=120)).strftime("%Y%m%d"), cutoff.strftime("%Y%m%d")


def _period_dates(statement_rows: Mapping[str, Mapping[date, Mapping[str, Any]]], cutoff: date) -> list[date]:
    all_dates = sorted({period for rows in statement_rows.values() for period in rows if period <= cutoff})
    annual = [period for period in all_dates if period.month == 12 and period.day >= 28]
    selected = set(annual[-5:])
    interim = [period for period in all_dates if period not in annual]
    if interim:
        latest = interim[-1]
        selected.add(latest)
        prior = date(latest.year - 1, latest.month, min(latest.day, 28 if latest.month == 2 else latest.day))
        matching = [period for period in interim if period.year == prior.year and period.month == prior.month]
        if matching:
            selected.add(matching[-1])
    return sorted(selected)


def _akshare_company(ts_code: str, *, as_of: date) -> dict[str, Any]:
    try:
        import akshare as ak
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise FreeProviderError("未安装 AKShare") from exc
    code = ts_code.split(".", 1)[0]
    statement_symbols = {"income": "利润表", "balancesheet": "资产负债表", "cashflow": "现金流量表"}
    rows_by_statement: dict[str, dict[date, Mapping[str, Any]]] = {}
    warnings: list[str] = []
    for statement, symbol in statement_symbols.items():
        try:
            frame = ak.stock_financial_report_sina(stock=f"{'sh' if ts_code.endswith('.SH') else 'sz'}{code}", symbol=symbol)
            rows = _records(frame)
            rows_by_statement[statement] = {period: row for row in rows if (period := _report_date(row)) is not None and period <= as_of}
        except Exception as exc:
            warnings.append(f"AKShare {symbol} 获取失败：{str(exc)[:160]}")
            rows_by_statement[statement] = {}
    selected_dates = _period_dates(rows_by_statement, as_of)
    if not selected_dates:
        raise FreeProviderError("AKShare 没有返回截至指定日期的财务报表")
    income_map = {
        "revenue": ("营业收入", "营业总收入"),
        "cost_of_revenue": ("营业成本", "营业总成本"),
        "net_interest_income": ("净利息收入", "利息净收入"),
        "net_fee_commission_income": ("手续费及佣金净收入",),
        "operating_profit": ("营业利润",),
        "pretax_income": ("利润总额",),
        "income_tax_expense": ("所得税费用",),
        "net_income": ("归属于母公司股东的净利润", "归属于母公司所有者的净利润", "净利润"),
        "interest_expense": ("利息费用", "财务费用"),
    }
    balance_map = {
        "total_assets": ("资产总计",), "total_liabilities": ("负债合计",),
        "total_equity": ("归属于母公司股东的权益", "归属于母公司股东权益合计", "归属于母公司股东权益", "归属于母公司所有者权益", "股东权益合计", "所有者权益合计", "股东权益"),
        "loans_and_advances": ("发放贷款和垫款净额", "发放贷款和垫款", "贷款及垫款净额", "贷款和垫款净额"),
        "customer_deposits": ("客户存款", "客户存款（吸收存款）", "客户存款(吸收存款)"),
        "cash": ("货币资金",),
        "current_assets": ("流动资产合计",), "current_liabilities": ("流动负债合计",),
        "accounts_receivable": ("应收账款",), "inventory": ("存货",), "goodwill": ("商誉",),
        "shares_outstanding": ("股本",),
    }
    cash_map = {
        "operating_cash_flow": ("经营活动产生的现金流量净额", "经营活动现金流量净额"),
        "capital_expenditure": ("购建固定资产、无形资产和其他长期资产支付的现金",),
        "depreciation_amortization": ("折旧", "折旧与摊销"),
    }
    periods: list[dict[str, Any]] = []
    for period in selected_dates:
        facts: dict[str, Any] = {}
        for name, labels in income_map.items():
            row = rows_by_statement["income"].get(period)
            if row:
                value, label = _value(row, labels)
                fact = _fact(value, label or labels[0], "income", period)
                if fact:
                    facts[name] = fact
        for name, labels in balance_map.items():
            row = rows_by_statement["balancesheet"].get(period)
            if row:
                value, label = _value(row, labels)
                fact = _share_fact(value, label or labels[0], period) if name == "shares_outstanding" else _fact(value, label or labels[0], "balancesheet", period)
                if fact:
                    facts[name] = fact
        balance_row = rows_by_statement["balancesheet"].get(period)
        if balance_row:
            short_debt = _summed_statement_fact(
                balance_row,
                ("短期借款", "一年内到期的非流动负债"),
                "一年内到期有息债务（短期借款 + 一年内到期的非流动负债）",
                period,
            )
            if short_debt:
                facts["debt_due_within_one_year"] = short_debt
            total_debt = _summed_statement_fact(
                balance_row,
                ("短期借款", "一年内到期的非流动负债", "长期借款", "应付债券", "租赁负债"),
                "有息负债（完整列报项目合计）",
                period,
            )
            if total_debt:
                facts["interest_bearing_debt"] = total_debt
        for name, labels in cash_map.items():
            row = rows_by_statement["cashflow"].get(period)
            if row:
                value, label = _value(row, labels)
                fact = _fact(value, label or labels[0], "cashflow", period)
                if fact:
                    facts[name] = fact
        periods.append({
            "end_date": period.isoformat(),
            "fiscal_year": period.year,
            "period_type": "FY" if period.month == 12 else f"Q{((period.month - 1) // 3) + 1}",
            "duration_months": period.month if period.month in (3, 6, 9, 12) else 12,
            "cumulative": True,
            "audited": period.month == 12,
            "source": {"provider": "AKShare", "documentation": FREE_DOCS["akshare"], "report_end": period.isoformat(), "provisional": True},
            "facts": facts,
        })
    latest = periods[-1].get("facts", {})
    shares = latest.get("shares_outstanding", {}).get("value")
    valuation: dict[str, Any] = {"source": {"provider": "AKShare", "documentation": FREE_DOCS["akshare"], "provisional": True}}
    try:
        start_date, end_date = _akshare_quote_window(as_of)
        quote = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="")
        quote_rows = _records(quote)
        latest_quote = None
        for row in quote_rows:
            qdate = _report_date(row)
            if qdate and qdate <= as_of and _value(row, ("收盘", "close"))[0] is not None:
                latest_quote = (qdate, row)
        if latest_quote:
            qdate, qrow = latest_quote
            close, _ = _value(qrow, ("收盘", "close"))
            valuation.update({"market_price": _number(close), "price_as_of": qdate.isoformat()})
    except Exception as exc:
        fallback_quote = _baostock_latest_quote(ts_code, as_of)
        if fallback_quote:
            valuation.update({"market_price": fallback_quote[0], "price_as_of": fallback_quote[1]})
            valuation["quote_provider"] = "BaoStock"
            warnings.append(f"AKShare 行情不可用，已使用 BaoStock 历史行情：{str(exc)[:120]}")
        else:
            warnings.append(f"免费行情获取失败：{str(exc)[:160]}")
    if shares:
        valuation["shares_outstanding"] = shares
        valuation["shares_outstanding_unit"] = "million shares"
        valuation["shares_as_of"] = str(periods[-1].get("end_date") or "")
    return {
        "company": {"symbol": ts_code, "name": _company_name(ts_code), "industry": "general_non_financial", "currency": "CNY", "unit": "CNY million"},
        "currency": "CNY", "unit": "CNY million", "periods": periods, "audit": {}, "valuation": valuation,
        "provider": {"name": "AKShare", "mode": "free", "as_of": as_of.isoformat(), "warnings": warnings, "provisional": True},
    }


def _baostock_company(ts_code: str, *, as_of: date) -> dict[str, Any]:
    try:
        import baostock as bs
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise FreeProviderError("未安装 BaoStock") from exc
    code = ts_code.split(".", 1)[0]
    market_code = f"{'sh' if ts_code.endswith('.SH') else 'sz'}.{code}"
    login = bs.login()
    if str(login.error_code) != "0":
        raise FreeProviderError(f"BaoStock 登录失败：{login.error_msg}")
    try:
        periods = []
        for year in range(as_of.year - 4, as_of.year + 1):
            result = bs.query_profit_data(code=market_code, year=year, quarter=4)
            while result.next():
                row = dict(zip(result.fields, result.get_row_data()))
                stat_date = _as_date(row.get("statDate"))
                pub_date = _as_date(row.get("pubDate"))
                if stat_date <= as_of and pub_date <= as_of:
                    facts = {}
                    for name, field in (("revenue", "MBRevenue"), ("net_income", "netProfit")):
                        fact = _fact(row.get(field), field, "income", stat_date)
                        if fact:
                            fact["source"]["provider"] = "BaoStock"
                            fact["source"]["documentation"] = FREE_DOCS["baostock"]
                            facts[name] = fact
                    share = _share_fact(row.get("totalShare"), "totalShare", stat_date)
                    if share:
                        share["source"]["provider"] = "BaoStock"
                        facts["shares_outstanding"] = share
                    if facts:
                        periods.append({"end_date": stat_date.isoformat(), "fiscal_year": stat_date.year, "period_type": "FY", "duration_months": 12, "cumulative": True, "audited": False, "source": {"provider": "BaoStock", "documentation": FREE_DOCS["baostock"], "provisional": True}, "facts": facts})
        if not periods:
            raise FreeProviderError("BaoStock 没有返回可用年度数据")
        history = bs.query_history_k_data_plus(market_code, "date,close", start_date=(as_of - timedelta(days=120)).isoformat(), end_date=as_of.isoformat(), frequency="d", adjustflag="3")
        latest_price = None
        while history.next():
            row = dict(zip(history.fields, history.get_row_data()))
            if row.get("date") and _number(row.get("close")) is not None:
                latest_price = (row["date"], _number(row["close"]))
        valuation = {"source": {"provider": "BaoStock", "documentation": FREE_DOCS["baostock"], "provisional": True}}
        if latest_price:
            valuation.update({"market_price": latest_price[1], "price_as_of": latest_price[0]})
        shares = periods[-1]["facts"].get("shares_outstanding", {}).get("value")
        if shares:
            valuation["shares_outstanding"] = shares
            valuation["shares_as_of"] = str(periods[-1].get("end_date") or "")
        return {"company": {"symbol": ts_code, "name": _company_name(ts_code), "industry": "general_non_financial", "currency": "CNY", "unit": "CNY million"}, "currency": "CNY", "unit": "CNY million", "periods": periods, "audit": {}, "valuation": valuation, "provider": {"name": "BaoStock", "mode": "free", "as_of": as_of.isoformat(), "warnings": ["BaoStock 免费回退仅提供部分年度利润与行情字段，不能替代完整三表。"], "provisional": True}}
    finally:
        bs.logout()


def fetch_free_company_financials(ts_code: str, *, as_of: str | date | None = None) -> dict[str, Any]:
    cutoff = _as_date(as_of)
    errors = []
    try:
        return _akshare_company(ts_code, as_of=cutoff)
    except Exception as exc:
        errors.append(f"AKShare：{str(exc)[:180]}")
    try:
        result = _baostock_company(ts_code, as_of=cutoff)
        result.setdefault("provider", {}).setdefault("warnings", []).extend(errors)
        return result
    except Exception as exc:
        errors.append(f"BaoStock：{str(exc)[:180]}")
    raise FreeProviderError("免费数据源均不可用：" + "；".join(errors))


__all__ = ["FreeProviderError", "fetch_free_company_financials", "free_provider_available"]
