"""Tushare Pro BYOK provider for canonical A-share financial data.

Only Python's standard library is used.  The user's token is read from the
``TUSHARE_TOKEN`` environment variable for each fetch; it is never written to
disk, returned in a result, or included in an exception.

The field and HTTP contracts follow Tushare's official documentation:

* HTTP API: https://tushare.pro/document/2?doc_id=130
* income: https://tushare.pro/document/2?doc_id=33
* balancesheet: https://tushare.pro/document/2?doc_id=36
* cashflow: https://tushare.pro/document/2?doc_id=44
* daily_basic: https://tushare.pro/document/2?doc_id=32
* fina_audit: https://tushare.pro/document/2?doc_id=80

Tushare reports statement amounts in CNY yuan while the finance engine's
canonical dataset produced here uses CNY million.  ``daily_basic.total_share``
is in ten-thousand shares and is converted to million shares.  Every converted
fact carries the original API field, unit, report scope, and version metadata.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import os
import re
import socket
from typing import Any, Optional
from urllib import error as urlerror
from urllib import request as urlrequest


API_ENDPOINT = "https://api.tushare.pro"
TOKEN_ENV = "TUSHARE_TOKEN"
PROVIDER_NAME = "Tushare Pro"
MONEY_DIVISOR = Decimal("1000000")
DAILY_SHARE_DIVISOR = Decimal("100")  # 万股 -> 百万股
DAILY_MARKET_CAP_DIVISOR = Decimal("100")  # 万元 -> 百万元

OFFICIAL_DOCS = {
    "stock_basic": "https://tushare.pro/document/1?doc_id=25",
    "income": "https://tushare.pro/document/2?doc_id=33",
    "balancesheet": "https://tushare.pro/document/2?doc_id=36",
    "cashflow": "https://tushare.pro/document/2?doc_id=44",
    "daily_basic": "https://tushare.pro/document/2?doc_id=32",
    "fina_audit": "https://tushare.pro/document/2?doc_id=80",
}


STOCK_FIELDS = (
    "ts_code",
    "symbol",
    "name",
    "industry",
    "market",
    "exchange",
    "curr_type",
    "list_status",
)

COMMON_STATEMENT_FIELDS = (
    "ts_code",
    "ann_date",
    "f_ann_date",
    "end_date",
    "report_type",
    "comp_type",
    "end_type",
    "update_flag",
)

INCOME_FIELDS = COMMON_STATEMENT_FIELDS + (
    "revenue",
    "total_revenue",
    "oper_cost",
    "operate_profit",
    "total_profit",
    "income_tax",
    "n_income",
    "n_income_attr_p",
    "ebit",
    "fin_exp_int_exp",
    "int_income",
    "int_exp",
    "comm_income",
    "comm_exp",
    "n_commis_income",
    "credit_impa_loss",
    "assets_impair_loss",
)

BALANCE_FIELDS = COMMON_STATEMENT_FIELDS + (
    "total_share",
    "money_cap",
    "accounts_receiv",
    "inventories",
    "total_cur_assets",
    "goodwill",
    "total_assets",
    "st_borr",
    "non_cur_liab_due_1y",
    "total_cur_liab",
    "lt_borr",
    "bond_payable",
    "lease_liab",
    "total_liab",
    "total_hldr_eqy_exc_min_int",
    "total_hldr_eqy_inc_min_int",
    "cash_reser_cb",
    "decr_in_disbur",
    "depos_ib_deposits",
)

CASHFLOW_FIELDS = COMMON_STATEMENT_FIELDS + (
    "n_cashflow_act",
    "im_net_cashflow_oper_act",
    "c_pay_acq_const_fiolta",
    "free_cashflow",
    "depr_fa_coga_dpba",
    "amort_intang_assets",
    "lt_amort_deferred_exp",
)

DAILY_BASIC_FIELDS = (
    "ts_code",
    "trade_date",
    "close",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "total_mv",
)

AUDIT_FIELDS = (
    "ts_code",
    "ann_date",
    "end_date",
    "audit_result",
    "audit_agency",
    "audit_sign",
)

# report_type 1 is the latest consolidated cumulative statement; 4 is a
# consolidated comparative statement adjusted by a later filing.  Types 2/3
# are single-quarter statements, 5 is pre-adjustment, and 6-12 are parent-only.
CONSOLIDATED_CUMULATIVE_REPORT_TYPES = frozenset({"1", "4"})
ADJUSTED_REPORT_TYPES = frozenset({"3", "4"})
FINANCIAL_COMPANY_TYPES = {"2": "银行", "3": "保险", "4": "证券"}
FINANCIAL_INDUSTRY_KEYWORDS = (
    "银行",
    "保险",
    "证券",
    "券商",
    "多元金融",
    "金融服务",
    "bank",
    "insurance",
    "insurer",
    "brokerage",
    "securities",
    "financial services",
)


class TushareProviderError(RuntimeError):
    """Base class for safe, user-facing provider failures."""


class TushareConfigurationError(TushareProviderError):
    """The BYOK provider is not configured correctly."""


class TushareTransportError(TushareProviderError):
    """Tushare could not be reached or returned malformed HTTP data."""


class TushareAPIError(TushareProviderError):
    """Tushare returned a non-zero API status."""

    def __init__(self, api_name: str, code: Any, message: str):
        self.api_name = api_name
        self.code = code
        self.api_message = message
        super().__init__(f"Tushare {api_name} 接口失败（code={code}）：{message}")


class TushareDataError(TushareProviderError):
    """The response is structurally valid but unusable for analysis."""


class UnsupportedFinancialIndustryError(TushareProviderError):
    """The generic industrial-company engine must not analyse this company."""


HttpPost = Callable[[str, bytes, Mapping[str, str], float], Any]


def is_configured() -> bool:
    """Return whether a non-empty Tushare token is available, without exposing it."""

    return bool(os.environ.get(TOKEN_ENV, "").strip())


def normalize_ts_code(symbol: str) -> str:
    """Normalize a six-digit A-share symbol to Tushare's exchange suffix."""

    value = str(symbol or "").strip().upper()
    if re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", value):
        return value
    if not re.fullmatch(r"\d{6}", value):
        raise ValueError("A股代码应为六位数字，或形如 600000.SH / 000001.SZ / 830799.BJ")
    if value.startswith(("4", "8", "92")):
        exchange = "BJ"
    elif value.startswith(("5", "6", "9")):
        exchange = "SH"
    else:
        exchange = "SZ"
    return f"{value}.{exchange}"


def _redact(message: Any, token: str) -> str:
    text = str(message or "未知错误")
    if token:
        text = text.replace(token, "[REDACTED]")
    # Defensive cleanup if an upstream error serialises a token field.
    return re.sub(r'(?i)(["\']?token["\']?\s*[:=]\s*)["\']?[^\s,}\"]+', r"\1[REDACTED]", text)


def _default_http_post(endpoint: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
    req = urlrequest.Request(endpoint, data=body, headers=dict(headers), method="POST")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urlerror.HTTPError as exc:
        # Do not include the response body: an intermediary could reflect the
        # submitted credential.  The status code is enough for a safe error.
        raise TushareTransportError(f"Tushare HTTP 请求失败（状态码 {exc.code}）") from exc
    except (urlerror.URLError, TimeoutError, socket.timeout) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise TushareTransportError(f"Tushare 请求超时（{timeout:g} 秒）") from exc
        raise TushareTransportError(f"无法连接 Tushare：{reason}") from exc


class _TushareClient:
    """Small HTTP client whose repr intentionally never includes its token."""

    __slots__ = ("_token", "endpoint", "timeout", "_http_post")

    def __init__(
        self,
        token: str,
        *,
        endpoint: str = API_ENDPOINT,
        timeout: float = 15.0,
        http_post: Optional[HttpPost] = None,
    ) -> None:
        self._token = token
        self.endpoint = endpoint
        self.timeout = timeout
        self._http_post = http_post or _default_http_post

    def __repr__(self) -> str:  # pragma: no cover - trivial, but security-relevant
        return f"_TushareClient(endpoint={self.endpoint!r}, timeout={self.timeout!r}, token=[REDACTED])"

    def query(
        self,
        api_name: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        fields: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        payload = {
            "api_name": api_name,
            "token": self._token,
            "params": dict(params or {}),
            "fields": ",".join(fields),
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            raw = self._http_post(
                self.endpoint,
                body,
                {"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
                self.timeout,
            )
        except TushareProviderError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise TushareTransportError(f"Tushare {api_name} 请求超时（{self.timeout:g} 秒）") from exc
        except Exception as exc:
            raise TushareTransportError(
                f"Tushare {api_name} 请求失败：{_redact(exc, self._token)}"
            ) from exc

        if isinstance(raw, (bytes, bytearray)):
            try:
                response = json.loads(bytes(raw).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TushareTransportError(f"Tushare {api_name} 返回了无效 JSON") from exc
        elif isinstance(raw, str):
            try:
                response = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise TushareTransportError(f"Tushare {api_name} 返回了无效 JSON") from exc
        else:
            response = raw

        if not isinstance(response, Mapping):
            raise TushareTransportError(f"Tushare {api_name} 响应不是 JSON 对象")
        code = response.get("code")
        if str(code) != "0":
            raise TushareAPIError(api_name, code, _redact(response.get("msg"), self._token))
        data = response.get("data")
        if data in (None, ""):
            return []
        if not isinstance(data, Mapping):
            raise TushareTransportError(f"Tushare {api_name} 响应缺少 data 对象")
        response_fields = data.get("fields")
        items = data.get("items")
        if not isinstance(response_fields, list) or not isinstance(items, list):
            raise TushareTransportError(f"Tushare {api_name} 响应缺少 fields/items 数组")
        if len(set(map(str, response_fields))) != len(response_fields):
            raise TushareTransportError(f"Tushare {api_name} 响应含重复字段")
        rows: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if not isinstance(item, list) or len(item) != len(response_fields):
                raise TushareTransportError(f"Tushare {api_name} 第 {index + 1} 行字段数不一致")
            rows.append(dict(zip(map(str, response_fields), item)))
        return rows


def _as_date(value: date | datetime | str | None) -> date:
    if value is None:
        return datetime.now(timezone.utc).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError("as_of 应为 YYYY-MM-DD 或 YYYYMMDD")


def _parse_yyyymmdd(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def _iso_date(value: Any) -> str:
    parsed = _parse_yyyymmdd(value)
    return parsed.isoformat() if parsed else ""


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _row_announcement(row: Mapping[str, Any]) -> str:
    return str(row.get("f_ann_date") or row.get("ann_date") or "")


def _report_type_label(report_type: Any) -> str:
    return {
        "1": "合并报表",
        "2": "单季合并",
        "3": "调整单季合并表",
        "4": "调整合并报表",
        "5": "调整前合并报表",
        "6": "母公司报表",
    }.get(str(report_type or ""), f"报表类型 {report_type}")


def _select_statement_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    api_name: str,
    as_of: Optional[date] = None,
    allowed_comp_types: frozenset[str] = frozenset({"1"}),
) -> dict[str, dict[str, Any]]:
    """Choose one latest consolidated cumulative row for every report period."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for source_row in rows:
        row = dict(source_row)
        end_date = str(row.get("end_date") or "")
        if not _parse_yyyymmdd(end_date):
            continue
        announced = _parse_yyyymmdd(_row_announcement(row))
        if as_of is not None and announced is not None and announced > as_of:
            continue
        if str(row.get("report_type") or "") not in CONSOLIDATED_CUMULATIVE_REPORT_TYPES:
            continue
        comp_type = str(row.get("comp_type") or "")
        if comp_type and comp_type not in allowed_comp_types:
            continue
        grouped.setdefault(end_date, []).append(row)

    selected: dict[str, dict[str, Any]] = {}
    for end_date, candidates in grouped.items():
        def score(row: Mapping[str, Any]) -> tuple[int, str, int, int]:
            populated = sum(
                value is not None and value != ""
                for key, value in row.items()
                if key not in COMMON_STATEMENT_FIELDS
            )
            return (
                1 if str(row.get("update_flag") or "") == "1" else 0,
                _row_announcement(row),
                1 if str(row.get("report_type") or "") == "4" else 0,
                populated,
            )

        chosen = max(candidates, key=score)
        distinct_versions = {
            (
                str(row.get("report_type") or ""),
                str(row.get("update_flag") or ""),
                _row_announcement(row),
            )
            for row in candidates
        }
        chosen["_provider_meta"] = {
            "api_name": api_name,
            "candidate_versions": len(distinct_versions),
            "restated": str(chosen.get("report_type") or "") in ADJUSTED_REPORT_TYPES
            or len(distinct_versions) > 1,
        }
        selected[end_date] = chosen
    return selected


def _financial_comp_type(rows_by_api: Mapping[str, Iterable[Mapping[str, Any]]]) -> Optional[str]:
    candidates: list[tuple[str, str]] = []
    for rows in rows_by_api.values():
        for row in rows:
            comp_type = str(row.get("comp_type") or "")
            end_date = str(row.get("end_date") or "")
            if comp_type in FINANCIAL_COMPANY_TYPES and _parse_yyyymmdd(end_date):
                candidates.append((end_date, comp_type))
    return max(candidates)[1] if candidates else None


def _is_financial_industry(industry: str) -> bool:
    lowered = str(industry or "").strip().lower()
    return any(keyword in lowered for keyword in FINANCIAL_INDUSTRY_KEYWORDS)


def _is_bank_industry(industry: str) -> bool:
    lowered = str(industry or "").strip().lower()
    return "银行" in lowered or "bank" in lowered


def _source(row: Mapping[str, Any], api_name: str, raw_fields: Sequence[str], *, derived: bool = False) -> dict[str, Any]:
    report_type = str(row.get("report_type") or "")
    meta = row.get("_provider_meta") if isinstance(row.get("_provider_meta"), Mapping) else {}
    return {
        "provider": PROVIDER_NAME,
        "api_name": api_name,
        "official_documentation": OFFICIAL_DOCS[api_name],
        "ts_code": row.get("ts_code"),
        "report_end": _iso_date(row.get("end_date")),
        "announcement_date": _iso_date(row.get("ann_date")),
        "final_announcement_date": _iso_date(row.get("f_ann_date")),
        "report_type": report_type,
        "report_type_label": _report_type_label(report_type),
        "report_scope": "consolidated",
        "basis": "cumulative" if report_type in CONSOLIDATED_CUMULATIVE_REPORT_TYPES else "unknown",
        "company_type": str(row.get("comp_type") or ""),
        "update_flag": str(row.get("update_flag") or ""),
        "selected_as_latest": str(row.get("update_flag") or "") == "1",
        "candidate_versions": int(meta.get("candidate_versions") or 1),
        "raw_fields": list(raw_fields),
        "raw_unit": "CNY yuan",
        "normalization": "divide by 1,000,000",
        "derived_from_multiple_fields": derived,
    }


def _money_fact(
    row: Optional[Mapping[str, Any]],
    api_name: str,
    canonical_name: str,
    candidates: Sequence[tuple[str, str]],
) -> Optional[dict[str, Any]]:
    if not row:
        return None
    for raw_field, label in candidates:
        value = _decimal(row.get(raw_field))
        if value is not None:
            meta = row.get("_provider_meta") if isinstance(row.get("_provider_meta"), Mapping) else {}
            return {
                "value": _decimal_text(value / MONEY_DIVISOR),
                "unit": "CNY million",
                "statement": api_name,
                "as_reported_label": label,
                "restated": bool(meta.get("restated")),
                "source": _source(row, api_name, [raw_field]),
            }
    return None


def _summed_money_fact(
    row: Optional[Mapping[str, Any]],
    api_name: str,
    label: str,
    raw_fields: Sequence[str],
    *,
    require_complete: bool = False,
) -> Optional[dict[str, Any]]:
    if not row:
        return None
    values = [(field, _decimal(row.get(field))) for field in raw_fields]
    present = [(field, value) for field, value in values if value is not None]
    if not present:
        return None
    if require_complete and len(present) != len(values):
        # Missing is not zero.  In particular, a partial sum of borrowing
        # fields would silently understate leverage and contaminate every debt
        # ratio downstream.
        return None
    total = sum((value for _, value in present), Decimal("0"))
    source = _source(row, api_name, [field for field, _ in present], derived=True)
    missing_fields = [field for field, value in values if value is None]
    source["calculation"] = " + ".join(field for field, _ in present)
    source["missing_components"] = missing_fields
    source["completeness"] = "complete" if not missing_fields else "partial; null components were not assumed to be zero"
    meta = row.get("_provider_meta") if isinstance(row.get("_provider_meta"), Mapping) else {}
    return {
        "value": _decimal_text(total / MONEY_DIVISOR),
        "unit": "CNY million",
        "statement": api_name,
        "as_reported_label": label,
        "restated": bool(meta.get("restated")),
        "source": source,
    }


def _difference_money_fact(
    row: Optional[Mapping[str, Any]],
    api_name: str,
    label: str,
    minuend_field: str,
    subtrahend_field: str,
) -> Optional[dict[str, Any]]:
    """Create a derived amount only when both reported components exist."""

    if not row:
        return None
    minuend = _decimal(row.get(minuend_field))
    subtrahend = _decimal(row.get(subtrahend_field))
    if minuend is None or subtrahend is None:
        return None
    meta = row.get("_provider_meta") if isinstance(row.get("_provider_meta"), Mapping) else {}
    source = _source(row, api_name, [minuend_field, subtrahend_field], derived=True)
    source["calculation"] = f"{minuend_field} - {subtrahend_field}"
    source["completeness"] = "complete; both components reported"
    return {
        "value": _decimal_text((minuend - subtrahend) / MONEY_DIVISOR),
        "unit": "CNY million",
        "statement": api_name,
        "as_reported_label": label,
        "restated": bool(meta.get("restated")),
        "source": source,
    }


def _shares_fact(row: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    if not row:
        return None
    value = _decimal(row.get("total_share"))
    if value is None:
        return None
    meta = row.get("_provider_meta") if isinstance(row.get("_provider_meta"), Mapping) else {}
    source = _source(row, "balancesheet", ["total_share"])
    source["raw_unit"] = "shares"
    source["normalization"] = "divide by 1,000,000"
    return {
        "value": _decimal_text(value / MONEY_DIVISOR),
        "unit": "million shares",
        "statement": "balancesheet",
        "as_reported_label": "期末总股本",
        "restated": bool(meta.get("restated")),
        "source": source,
    }


def _period_shape(end_date: date) -> tuple[str, int]:
    month_day = (end_date.month, end_date.day)
    return {
        (3, 31): ("Q1", 3),
        (6, 30): ("H1", 6),
        (9, 30): ("Q3", 9),
        (12, 31): ("FY", 12),
    }.get(month_day, ("OTHER", 0))


def _latest_audit_by_period(
    rows: Iterable[Mapping[str, Any]], *, as_of: Optional[date] = None
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        end_date = str(row.get("end_date") or "")
        if not _parse_yyyymmdd(end_date):
            continue
        announced = _parse_yyyymmdd(row.get("ann_date"))
        if as_of is not None and announced is not None and announced > as_of:
            continue
        current = result.get(end_date)
        if current is None or str(row.get("ann_date") or "") > str(current.get("ann_date") or ""):
            result[end_date] = dict(row)
    return result


def _audit_source(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "provider": PROVIDER_NAME,
        "api_name": "fina_audit",
        "official_documentation": OFFICIAL_DOCS["fina_audit"],
        "ts_code": row.get("ts_code"),
        "report_end": _iso_date(row.get("end_date")),
        "announcement_date": _iso_date(row.get("ann_date")),
        "audit_agency": row.get("audit_agency"),
        "audit_sign": row.get("audit_sign"),
    }


def _selected_period_keys(all_keys: Iterable[str], as_of: date, years: int) -> list[str]:
    parsed = sorted(
        {parsed for key in all_keys if (parsed := _parse_yyyymmdd(key)) is not None and parsed <= as_of}
    )
    annual = [item for item in parsed if _period_shape(item)[0] == "FY"][-years:]
    interim = [item for item in parsed if _period_shape(item)[0] in {"Q1", "H1", "Q3"}]
    selected = set(annual)
    if interim:
        latest = interim[-1]
        # If an FY for the same or a later period exists, the interim is stale
        # and should not become the current analysis period.
        if not annual or latest > annual[-1]:
            selected.add(latest)
            comparable = next(
                (
                    item
                    for item in reversed(interim[:-1])
                    if item.year == latest.year - 1 and (item.month, item.day) == (latest.month, latest.day)
                ),
                None,
            )
            if comparable:
                selected.add(comparable)
    return [item.strftime("%Y%m%d") for item in sorted(selected)]


def _market_valuation(rows: Iterable[Mapping[str, Any]], as_of: date) -> dict[str, Any]:
    eligible = [
        dict(row)
        for row in rows
        if (trade_date := _parse_yyyymmdd(row.get("trade_date"))) is not None and trade_date <= as_of
    ]
    if not eligible:
        return {}
    latest = max(eligible, key=lambda row: str(row.get("trade_date") or ""))
    source = {
        "provider": PROVIDER_NAME,
        "api_name": "daily_basic",
        "official_documentation": OFFICIAL_DOCS["daily_basic"],
        "ts_code": latest.get("ts_code"),
        "trade_date": _iso_date(latest.get("trade_date")),
    }
    valuation: dict[str, Any] = {"source": source}
    valuation["price_as_of"] = source["trade_date"]
    simple_fields = {
        "market_price": "close",
        "pe": "pe",
        "pe_ttm": "pe_ttm",
        "pb": "pb",
        "ps": "ps",
        "ps_ttm": "ps_ttm",
        "dividend_yield": "dv_ratio",
        "dividend_yield_ttm": "dv_ttm",
    }
    for canonical, raw_field in simple_fields.items():
        value = _decimal(latest.get(raw_field))
        if value is not None:
            valuation[canonical] = _decimal_text(value)
    shares = _decimal(latest.get("total_share"))
    if shares is not None:
        valuation["shares_outstanding"] = _decimal_text(shares / DAILY_SHARE_DIVISOR)
        valuation["shares_outstanding_unit"] = "million shares"
        valuation["shares_as_of"] = source["trade_date"]
    market_cap = _decimal(latest.get("total_mv"))
    if market_cap is not None:
        valuation["market_cap"] = _decimal_text(market_cap / DAILY_MARKET_CAP_DIVISOR)
        valuation["market_cap_unit"] = "CNY million"
    return valuation


def canonicalize_tushare_data(
    ts_code: str,
    *,
    stock_rows: Iterable[Mapping[str, Any]],
    income_rows: Iterable[Mapping[str, Any]],
    balance_rows: Iterable[Mapping[str, Any]],
    cashflow_rows: Iterable[Mapping[str, Any]],
    audit_rows: Iterable[Mapping[str, Any]] = (),
    daily_basic_rows: Iterable[Mapping[str, Any]] = (),
    years: int = 5,
    as_of: date | datetime | str | None = None,
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    """Map raw Tushare rows to ``finance_engine.analyze_company`` input.

    Five most recent annual periods are retained.  If newer interim data exists,
    the latest cumulative interim and its prior-year comparable are also kept so
    the deterministic engine can build TTM without mixing cumulative and
    single-quarter figures.
    """

    code = normalize_ts_code(ts_code)
    if not isinstance(years, int) or isinstance(years, bool) or not 1 <= years <= 20:
        raise ValueError("years 应为 1 到 20 的整数")
    cutoff = _as_date(as_of)
    stock = [dict(row) for row in stock_rows]
    income = [dict(row) for row in income_rows]
    balance = [dict(row) for row in balance_rows]
    cashflow = [dict(row) for row in cashflow_rows]
    audits = [dict(row) for row in audit_rows]
    daily = [dict(row) for row in daily_basic_rows]

    metadata = next((row for row in stock if str(row.get("ts_code") or "").upper() == code), stock[0] if stock else {})
    industry = str(metadata.get("industry") or "未知行业")
    financial_type = _financial_comp_type({"income": income, "balancesheet": balance, "cashflow": cashflow})
    if financial_type == "2" or _is_bank_industry(industry):
        return canonicalize_tushare_bank_data(
            code,
            stock_rows=stock,
            income_rows=income,
            balance_rows=balance,
            cashflow_rows=cashflow,
            audit_rows=audits,
            daily_basic_rows=daily,
            years=years,
            as_of=cutoff,
            warnings=warnings,
        )
    lowered_industry = industry.strip().lower()
    inferred_financial_type = financial_type
    if inferred_financial_type is None and any(keyword in lowered_industry for keyword in ("保险", "insurance", "insurer")):
        inferred_financial_type = "3"
    if inferred_financial_type is None and any(keyword in lowered_industry for keyword in ("证券", "券商", "brokerage", "securities")):
        inferred_financial_type = "4"
    if inferred_financial_type in {"3", "4"}:
        return canonicalize_tushare_financial_sector_data(
            code,
            company_type=inferred_financial_type,
            stock_rows=stock,
            income_rows=income,
            balance_rows=balance,
            cashflow_rows=cashflow,
            audit_rows=audits,
            daily_basic_rows=daily,
            years=years,
            as_of=cutoff,
            warnings=warnings,
        )
    if _is_financial_industry(industry) and not _is_bank_industry(industry):
        raise UnsupportedFinancialIndustryError(
            f"{code} 所属行业“{industry}”需要银行/保险/证券专用模型，当前一般企业财务模型拒绝分析。"
        )
    if financial_type:
        label = FINANCIAL_COMPANY_TYPES[financial_type]
        raise UnsupportedFinancialIndustryError(
            f"{code} 的 Tushare comp_type={financial_type}（{label}），当前一般企业财务模型拒绝分析。"
        )

    selected_income = _select_statement_rows(income, api_name="income", as_of=cutoff)
    selected_balance = _select_statement_rows(balance, api_name="balancesheet", as_of=cutoff)
    selected_cashflow = _select_statement_rows(cashflow, api_name="cashflow", as_of=cutoff)
    all_period_keys = set(selected_income) | set(selected_balance) | set(selected_cashflow)
    period_keys = _selected_period_keys(all_period_keys, cutoff, years)
    if not period_keys:
        raise TushareDataError(f"{code} 没有可用的合并累计财务报表（仅接受 report_type 1/4、comp_type 1）")

    audit_by_period = _latest_audit_by_period(audits, as_of=cutoff)
    periods: list[dict[str, Any]] = []
    for period_key in period_keys:
        parsed_end = _parse_yyyymmdd(period_key)
        if parsed_end is None:  # guarded by _selected_period_keys
            continue
        period_type, months = _period_shape(parsed_end)
        inc = selected_income.get(period_key)
        bal = selected_balance.get(period_key)
        cfs = selected_cashflow.get(period_key)
        facts: dict[str, Any] = {}

        income_mappings = {
            "revenue": (("revenue", "营业收入"), ("total_revenue", "营业总收入")),
            "cost_of_revenue": (("oper_cost", "营业成本"),),
            "operating_profit": (("operate_profit", "营业利润"),),
            "ebit": (("ebit", "息税前利润"),),
            "pretax_income": (("total_profit", "利润总额"),),
            "income_tax_expense": (("income_tax", "所得税费用"),),
            "net_income": (("n_income_attr_p", "归属于母公司股东的净利润"), ("n_income", "净利润（含少数股东损益）")),
            "interest_expense": (("fin_exp_int_exp", "财务费用：利息费用"),),
        }
        for canonical_name, candidates in income_mappings.items():
            fact = _money_fact(inc, "income", canonical_name, candidates)
            if fact:
                facts[canonical_name] = fact

        balance_mappings = {
            "total_assets": (("total_assets", "资产总计"),),
            "total_liabilities": (("total_liab", "负债合计"),),
            "total_equity": (("total_hldr_eqy_inc_min_int", "股东权益合计（含少数股东权益）"), ("total_hldr_eqy_exc_min_int", "股东权益合计（不含少数股东权益）")),
            "cash": (("money_cap", "货币资金"),),
            "current_assets": (("total_cur_assets", "流动资产合计"),),
            "current_liabilities": (("total_cur_liab", "流动负债合计"),),
            "accounts_receivable": (("accounts_receiv", "应收账款"),),
            "inventory": (("inventories", "存货"),),
            "goodwill": (("goodwill", "商誉"),),
        }
        for canonical_name, candidates in balance_mappings.items():
            fact = _money_fact(bal, "balancesheet", canonical_name, candidates)
            if fact:
                facts[canonical_name] = fact
        debt = _summed_money_fact(
            bal,
            "balancesheet",
            "有息负债（短期借款 + 一年内到期非流动负债 + 长期借款 + 应付债券 + 租赁负债）",
            ("st_borr", "non_cur_liab_due_1y", "lt_borr", "bond_payable", "lease_liab"),
            require_complete=True,
        )
        if debt:
            facts["interest_bearing_debt"] = debt
        short_debt = _summed_money_fact(
            bal,
            "balancesheet",
            "一年内到期有息债务（短期借款 + 一年内到期非流动负债）",
            ("st_borr", "non_cur_liab_due_1y"),
            require_complete=True,
        )
        if short_debt:
            facts["debt_due_within_one_year"] = short_debt
        shares = _shares_fact(bal)
        if shares:
            facts["shares_outstanding"] = shares

        cashflow_mappings = {
            "operating_cash_flow": (("n_cashflow_act", "经营活动产生的现金流量净额"), ("im_net_cashflow_oper_act", "经营活动现金流量净额（间接法）")),
            "capital_expenditure": (("c_pay_acq_const_fiolta", "购建固定资产、无形资产和其他长期资产支付的现金"),),
            "free_cash_flow_to_firm": (("free_cashflow", "企业自由现金流量"),),
        }
        for canonical_name, candidates in cashflow_mappings.items():
            fact = _money_fact(cfs, "cashflow", canonical_name, candidates)
            if fact:
                facts[canonical_name] = fact
        depreciation = _summed_money_fact(
            cfs,
            "cashflow",
            "折旧与摊销合计",
            ("depr_fa_coga_dpba", "amort_intang_assets", "lt_amort_deferred_exp"),
        )
        if depreciation:
            facts["depreciation_amortization"] = depreciation

        audit_row = audit_by_period.get(period_key)
        statement_sources = [
            _source(row, api_name, [])
            for api_name, row in (("income", inc), ("balancesheet", bal), ("cashflow", cfs))
            if row
        ]
        period_source: dict[str, Any] = {
            "provider": PROVIDER_NAME,
            "ts_code": code,
            "report_end": parsed_end.isoformat(),
            "statement_sources": statement_sources,
        }
        if audit_row:
            period_source["audit_source"] = _audit_source(audit_row)
        periods.append(
            {
                "end_date": parsed_end.isoformat(),
                "fiscal_year": parsed_end.year,
                "period_type": period_type,
                "duration_months": months,
                "cumulative": True,
                # Annual date alone does not prove an audit.  It is true only
                # when fina_audit supplies an opinion for the same period.
                "audited": bool(audit_row and str(audit_row.get("audit_result") or "").strip()),
                "source": period_source,
                "facts": facts,
            }
        )

    latest_audited_annual = next(
        (
            (period, audit_by_period.get(period["end_date"].replace("-", "")))
            for period in reversed(periods)
            if period["period_type"] == "FY" and period["audited"]
        ),
        None,
    )
    audit: dict[str, Any] = {}
    if latest_audited_annual:
        _, audit_row = latest_audited_annual
        if audit_row:
            audit = {
                "opinion": audit_row.get("audit_result"),
                "agency": audit_row.get("audit_agency"),
                "signatories": audit_row.get("audit_sign"),
                "source": _audit_source(audit_row),
            }

    valuation = _market_valuation(daily, cutoff)
    company = {
        "symbol": code,
        "name": str(metadata.get("name") or code),
        "industry": industry,
        "currency": str(metadata.get("curr_type") or "CNY"),
        "unit": "CNY million",
    }
    return {
        "company": company,
        "currency": company["currency"],
        "unit": company["unit"],
        "periods": periods,
        "audit": audit,
        "valuation": valuation,
        "provider": {
            "name": PROVIDER_NAME,
            "ts_code": code,
            "as_of": cutoff.isoformat(),
            "statement_policy": "latest consolidated cumulative rows only (report_type 1/4; comp_type 1)",
            "period_policy": "latest five FY plus latest interim and prior-year comparable when available",
            "unit_policy": "statement CNY yuan normalized to CNY million; shares normalized to million shares",
            "official_documentation": OFFICIAL_DOCS,
            "warnings": list(warnings),
        },
    }


def canonicalize_tushare_bank_data(
    ts_code: str,
    *,
    stock_rows: Iterable[Mapping[str, Any]],
    income_rows: Iterable[Mapping[str, Any]],
    balance_rows: Iterable[Mapping[str, Any]],
    cashflow_rows: Iterable[Mapping[str, Any]],
    audit_rows: Iterable[Mapping[str, Any]] = (),
    daily_basic_rows: Iterable[Mapping[str, Any]] = (),
    years: int = 5,
    as_of: date | datetime | str | None = None,
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    """Canonicalise Tushare ``comp_type=2`` rows for the bank plug-in.

    Tushare's three statements cover bank income and balance-sheet amounts but
    do not provide every regulatory disclosure.  NIM, NPL, provision coverage
    and capital ratios are intentionally left missing until a source-labelled
    annual-report extraction or manual import supplies them.
    """

    code = normalize_ts_code(ts_code)
    if not isinstance(years, int) or isinstance(years, bool) or not 1 <= years <= 20:
        raise ValueError("years 应为 1 到 20 的整数")
    cutoff = _as_date(as_of)
    stock = [dict(row) for row in stock_rows]
    income = [dict(row) for row in income_rows]
    balance = [dict(row) for row in balance_rows]
    cashflow = [dict(row) for row in cashflow_rows]
    audits = [dict(row) for row in audit_rows]
    daily = [dict(row) for row in daily_basic_rows]
    metadata = next((row for row in stock if str(row.get("ts_code") or "").upper() == code), stock[0] if stock else {})
    industry = str(metadata.get("industry") or "银行")
    financial_type = _financial_comp_type({"income": income, "balancesheet": balance, "cashflow": cashflow})
    if financial_type not in {None, "2"}:
        label = FINANCIAL_COMPANY_TYPES.get(financial_type, financial_type)
        raise UnsupportedFinancialIndustryError(f"{code} 的 comp_type={financial_type}（{label}）不是银行，当前银行插件拒绝套用。")

    bank_types = frozenset({"2"})
    selected_income = _select_statement_rows(income, api_name="income", as_of=cutoff, allowed_comp_types=bank_types)
    selected_balance = _select_statement_rows(balance, api_name="balancesheet", as_of=cutoff, allowed_comp_types=bank_types)
    selected_cashflow = _select_statement_rows(cashflow, api_name="cashflow", as_of=cutoff, allowed_comp_types=bank_types)
    period_keys = _selected_period_keys(set(selected_income) | set(selected_balance) | set(selected_cashflow), cutoff, years)
    if not period_keys:
        raise TushareDataError(f"{code} 没有可用的银行合并累计财务报表（仅接受 report_type 1/4、comp_type 2）")

    audit_by_period = _latest_audit_by_period(audits, as_of=cutoff)
    periods: list[dict[str, Any]] = []
    for period_key in period_keys:
        parsed_end = _parse_yyyymmdd(period_key)
        if parsed_end is None:
            continue
        period_type, months = _period_shape(parsed_end)
        inc = selected_income.get(period_key)
        bal = selected_balance.get(period_key)
        cfs = selected_cashflow.get(period_key)
        facts: dict[str, Any] = {}

        for canonical_name, candidates in {
            "revenue": (("total_revenue", "营业总收入"), ("revenue", "营业收入")),
            "interest_income": (("int_income", "利息收入"),),
            "interest_expense": (("int_exp", "利息支出"),),
            "fee_commission_income": (("comm_income", "手续费及佣金收入"),),
            "fee_commission_expense": (("comm_exp", "手续费及佣金支出"),),
            "net_fee_commission_income": (("n_commis_income", "手续费及佣金净收入"),),
            "operating_profit": (("operate_profit", "营业利润"),),
            "pretax_income": (("total_profit", "利润总额"),),
            "income_tax_expense": (("income_tax", "所得税费用"),),
            "net_income": (("n_income_attr_p", "归属于母公司股东的净利润"), ("n_income", "净利润（含少数股东损益）")),
            "credit_impairment_loss": (("credit_impa_loss", "信用减值损失"),),
            "asset_impairment_loss": (("assets_impair_loss", "资产减值损失"),),
        }.items():
            fact = _money_fact(inc, "income", canonical_name, candidates)
            if fact:
                facts[canonical_name] = fact
        net_interest = _difference_money_fact(inc, "income", "利息净收入（利息收入－利息支出）", "int_income", "int_exp")
        if net_interest:
            facts["net_interest_income"] = net_interest

        for canonical_name, candidates in {
            "total_assets": (("total_assets", "资产总计"),),
            "total_liabilities": (("total_liab", "负债合计"),),
            "total_equity": (("total_hldr_eqy_inc_min_int", "股东权益合计（含少数股东权益）"), ("total_hldr_eqy_exc_min_int", "归属于母公司股东权益")),
            "cash": (("cash_reser_cb", "现金及存放中央银行款项"), ("money_cap", "货币资金")),
            "loans_and_advances": (("decr_in_disbur", "发放贷款及垫款"),),
            "customer_deposits": (("depos_ib_deposits", "吸收存款及同业存放"),),
        }.items():
            fact = _money_fact(bal, "balancesheet", canonical_name, candidates)
            if fact:
                facts[canonical_name] = fact
        shares = _shares_fact(bal)
        if shares:
            facts["shares_outstanding"] = shares

        audit_row = audit_by_period.get(period_key)
        statement_sources = [_source(row, api_name, []) for api_name, row in (("income", inc), ("balancesheet", bal), ("cashflow", cfs)) if row]
        period_source: dict[str, Any] = {"provider": PROVIDER_NAME, "ts_code": code, "report_end": parsed_end.isoformat(), "statement_sources": statement_sources, "company_type": "2"}
        if audit_row:
            period_source["audit_source"] = _audit_source(audit_row)
        periods.append({
            "end_date": parsed_end.isoformat(),
            "fiscal_year": parsed_end.year,
            "period_type": period_type,
            "duration_months": months,
            "cumulative": True,
            "audited": bool(audit_row and str(audit_row.get("audit_result") or "").strip()),
            "source": period_source,
            "facts": facts,
        })

    latest_audit = next((audit_by_period.get(period["end_date"].replace("-", "")) for period in reversed(periods) if period["period_type"] == "FY" and period["audited"]), None)
    audit = {
        "opinion": latest_audit.get("audit_result"),
        "agency": latest_audit.get("audit_agency"),
        "signatories": latest_audit.get("audit_sign"),
        "source": _audit_source(latest_audit),
    } if latest_audit else {}
    valuation = _market_valuation(daily, cutoff)
    valuation["company_type"] = "2"
    company = {"symbol": code, "name": str(metadata.get("name") or code), "industry": industry or "银行", "currency": str(metadata.get("curr_type") or "CNY"), "unit": "CNY million"}
    warning_list = list(warnings)
    warning_list.append("Tushare 三表不含全部银行监管指标；净息差、不良、关注、逾期、拨备和资本充足率缺失时必须从法定报告补录，不能推断为正常。")
    return {
        "company": company,
        "currency": company["currency"],
        "unit": company["unit"],
        "periods": periods,
        "audit": audit,
        "valuation": valuation,
        "provider": {
            "name": PROVIDER_NAME,
            "ts_code": code,
            "as_of": cutoff.isoformat(),
            "company_type": "2",
            "statement_policy": "latest consolidated cumulative rows only (report_type 1/4; comp_type 2)",
            "period_policy": "latest five FY plus latest interim and prior-year comparable when available",
            "unit_policy": "statement CNY yuan normalized to CNY million; shares normalized to million shares",
            "regulatory_metric_policy": "missing until supplied by a traceable statutory-report extraction or manual import",
            "official_documentation": OFFICIAL_DOCS,
            "warnings": warning_list,
        },
    }


def canonicalize_tushare_financial_sector_data(
    ts_code: str,
    *,
    company_type: str,
    stock_rows: Iterable[Mapping[str, Any]],
    income_rows: Iterable[Mapping[str, Any]],
    balance_rows: Iterable[Mapping[str, Any]],
    cashflow_rows: Iterable[Mapping[str, Any]],
    audit_rows: Iterable[Mapping[str, Any]] = (),
    daily_basic_rows: Iterable[Mapping[str, Any]] = (),
    years: int = 5,
    as_of: date | datetime | str | None = None,
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    """Canonicalise comp_type 3/4 statement facts without inventing sector KPIs."""

    if company_type not in {"3", "4"}:
        raise ValueError("company_type must be 3 (insurance) or 4 (securities)")
    code = normalize_ts_code(ts_code)
    if not isinstance(years, int) or isinstance(years, bool) or not 1 <= years <= 20:
        raise ValueError("years 应为 1 到 20 的整数")
    cutoff = _as_date(as_of)
    stock = [dict(row) for row in stock_rows]
    income = [dict(row) for row in income_rows]
    balance = [dict(row) for row in balance_rows]
    cashflow = [dict(row) for row in cashflow_rows]
    audits = [dict(row) for row in audit_rows]
    daily = [dict(row) for row in daily_basic_rows]
    metadata = next((row for row in stock if str(row.get("ts_code") or "").upper() == code), stock[0] if stock else {})
    label = FINANCIAL_COMPANY_TYPES[company_type]
    selected_types = frozenset({company_type})
    selected_income = _select_statement_rows(income, api_name="income", as_of=cutoff, allowed_comp_types=selected_types)
    selected_balance = _select_statement_rows(balance, api_name="balancesheet", as_of=cutoff, allowed_comp_types=selected_types)
    selected_cashflow = _select_statement_rows(cashflow, api_name="cashflow", as_of=cutoff, allowed_comp_types=selected_types)
    period_keys = _selected_period_keys(set(selected_income) | set(selected_balance) | set(selected_cashflow), cutoff, years)
    if not period_keys:
        raise TushareDataError(f"{code} 没有可用的{label}合并累计财务报表（仅接受 report_type 1/4、comp_type {company_type}）")
    audit_by_period = _latest_audit_by_period(audits, as_of=cutoff)
    periods: list[dict[str, Any]] = []
    for period_key in period_keys:
        parsed_end = _parse_yyyymmdd(period_key)
        if parsed_end is None:
            continue
        period_type, months = _period_shape(parsed_end)
        inc, bal, cfs = selected_income.get(period_key), selected_balance.get(period_key), selected_cashflow.get(period_key)
        facts: dict[str, Any] = {}
        for canonical_name, candidates in {
            "revenue": (("total_revenue", "营业总收入"), ("revenue", "营业收入")),
            "operating_profit": (("operate_profit", "营业利润"),),
            "pretax_income": (("total_profit", "利润总额"),),
            "income_tax_expense": (("income_tax", "所得税费用"),),
            "net_income": (("n_income_attr_p", "归属于母公司股东的净利润"), ("n_income", "净利润")),
            "fee_commission_income": (("comm_income", "手续费及佣金收入"),),
            "fee_commission_expense": (("comm_exp", "手续费及佣金支出"),),
            "net_fee_commission_income": (("n_commis_income", "手续费及佣金净收入"),),
            "credit_impairment_loss": (("credit_impa_loss", "信用减值损失"),),
            "asset_impairment_loss": (("assets_impair_loss", "资产减值损失"),),
        }.items():
            fact = _money_fact(inc, "income", canonical_name, candidates)
            if fact:
                facts[canonical_name] = fact
        net_interest = _difference_money_fact(inc, "income", "利息净收入（利息收入－利息支出）", "int_income", "int_exp")
        if net_interest:
            facts["net_interest_income"] = net_interest
        for canonical_name, candidates in {
            "total_assets": (("total_assets", "资产总计"),),
            "total_liabilities": (("total_liab", "负债合计"),),
            "total_equity": (("total_hldr_eqy_inc_min_int", "股东权益合计"), ("total_hldr_eqy_exc_min_int", "归属于母公司股东权益")),
            "cash": (("money_cap", "货币资金"), ("cash_reser_cb", "现金及存放中央银行款项")),
        }.items():
            fact = _money_fact(bal, "balancesheet", canonical_name, candidates)
            if fact:
                facts[canonical_name] = fact
        ocf = _money_fact(cfs, "cashflow", "operating_cash_flow", (("n_cashflow_act", "经营活动现金流量净额"), ("im_net_cashflow_oper_act", "经营活动现金流量净额（间接法）")))
        if ocf:
            facts["operating_cash_flow"] = ocf
        shares = _shares_fact(bal)
        if shares:
            facts["shares_outstanding"] = shares
        audit_row = audit_by_period.get(period_key)
        statement_sources = [_source(row, api_name, []) for api_name, row in (("income", inc), ("balancesheet", bal), ("cashflow", cfs)) if row]
        period_source: dict[str, Any] = {"provider": PROVIDER_NAME, "ts_code": code, "report_end": parsed_end.isoformat(), "statement_sources": statement_sources, "company_type": company_type}
        if audit_row:
            period_source["audit_source"] = _audit_source(audit_row)
        periods.append({"end_date": parsed_end.isoformat(), "fiscal_year": parsed_end.year, "period_type": period_type, "duration_months": months, "cumulative": True, "audited": bool(audit_row and str(audit_row.get("audit_result") or "").strip()), "source": period_source, "facts": facts})
    latest_audit = next((audit_by_period.get(period["end_date"].replace("-", "")) for period in reversed(periods) if period["period_type"] == "FY" and period["audited"]), None)
    audit = {"opinion": latest_audit.get("audit_result"), "agency": latest_audit.get("audit_agency"), "signatories": latest_audit.get("audit_sign"), "source": _audit_source(latest_audit)} if latest_audit else {}
    valuation = _market_valuation(daily, cutoff)
    valuation["company_type"] = company_type
    valuation["financial_sector_template"] = "insurance" if company_type == "3" else "brokerage"
    industry = str(metadata.get("industry") or label)
    company = {"symbol": code, "name": str(metadata.get("name") or code), "industry": industry, "currency": str(metadata.get("curr_type") or "CNY"), "unit": "CNY million"}
    warning_list = list(warnings)
    warning_list.append(f"Tushare 三表只提供部分{label}会计科目；偿付能力/净资本、业务分部和 EV/NBV 等专用指标必须从法定报告补录，缺失不得推断为正常。")
    return {"company": company, "currency": company["currency"], "unit": company["unit"], "periods": periods, "audit": audit, "valuation": valuation, "provider": {"name": PROVIDER_NAME, "ts_code": code, "as_of": cutoff.isoformat(), "company_type": company_type, "statement_policy": f"latest consolidated cumulative rows only (report_type 1/4; comp_type {company_type})", "period_policy": "latest five FY plus latest interim and prior-year comparable when available", "unit_policy": "statement CNY yuan normalized to CNY million; shares normalized to million shares", "regulatory_metric_policy": "missing until supplied by a traceable statutory-report extraction or manual import", "official_documentation": OFFICIAL_DOCS, "warnings": warning_list}}


def fetch_company_financials(
    ts_code: str,
    *,
    years: int = 5,
    as_of: date | datetime | str | None = None,
    include_market: bool = True,
    timeout: float = 15.0,
    endpoint: str = API_ENDPOINT,
    http_post: Optional[HttpPost] = None,
) -> dict[str, Any]:
    """Fetch and canonicalize one non-financial A-share company.

    Credentials are deliberately not accepted as an argument.  Set
    ``TUSHARE_TOKEN`` in the process environment.  ``http_post`` exists for
    deterministic tests and embedded runtimes; it must follow ``HttpPost``.
    """

    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise TushareConfigurationError(
            "尚未配置 Tushare：请在环境变量 TUSHARE_TOKEN 中设置个人 token（不会写入数据库）。"
        )
    if timeout <= 0:
        raise ValueError("timeout 必须大于 0")
    code = normalize_ts_code(ts_code)
    cutoff = _as_date(as_of)
    client = _TushareClient(token, endpoint=endpoint, timeout=float(timeout), http_post=http_post)

    stock_rows = client.query("stock_basic", params={"ts_code": code}, fields=STOCK_FIELDS)
    metadata = stock_rows[0] if stock_rows else {}
    industry = str(metadata.get("industry") or "")

    statement_params = {"ts_code": code}
    income_rows = client.query("income", params=statement_params, fields=INCOME_FIELDS)
    balance_rows = client.query("balancesheet", params=statement_params, fields=BALANCE_FIELDS)
    cashflow_rows = client.query("cashflow", params=statement_params, fields=CASHFLOW_FIELDS)

    warnings: list[str] = []
    try:
        audit_rows = client.query("fina_audit", params=statement_params, fields=AUDIT_FIELDS)
    except TushareProviderError as exc:
        audit_rows = []
        warnings.append(f"审计意见未获取：{exc}")

    daily_rows: list[dict[str, Any]] = []
    if include_market:
        start = cutoff - timedelta(days=31)
        try:
            daily_rows = client.query(
                "daily_basic",
                params={
                    "ts_code": code,
                    "start_date": start.strftime("%Y%m%d"),
                    "end_date": cutoff.strftime("%Y%m%d"),
                },
                fields=DAILY_BASIC_FIELDS,
            )
        except TushareProviderError as exc:
            warnings.append(f"市场估值数据未获取：{exc}")

    return canonicalize_tushare_data(
        code,
        stock_rows=stock_rows,
        income_rows=income_rows,
        balance_rows=balance_rows,
        cashflow_rows=cashflow_rows,
        audit_rows=audit_rows,
        daily_basic_rows=daily_rows,
        years=years,
        as_of=cutoff,
        warnings=warnings,
    )


__all__ = [
    "API_ENDPOINT",
    "TOKEN_ENV",
    "TushareProviderError",
    "TushareConfigurationError",
    "TushareTransportError",
    "TushareAPIError",
    "TushareDataError",
    "UnsupportedFinancialIndustryError",
    "canonicalize_tushare_data",
    "canonicalize_tushare_bank_data",
    "canonicalize_tushare_financial_sector_data",
    "fetch_company_financials",
    "is_configured",
    "normalize_ts_code",
]
