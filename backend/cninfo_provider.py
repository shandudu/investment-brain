"""Small, dependency-free adapter for CNINFO's public announcement search.

CNINFO is treated as a discovery source.  Returned PDF URLs are still passed
through the immutable filing archive, which validates the official host,
downloads the bytes, hashes them, and preserves the original metadata.

The public website endpoint is suitable for a personal, rate-limited local
workflow.  It is not a redistribution licence or a commercial data SLA.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


TOP_SEARCH_URL = "https://www.cninfo.com.cn/new/information/topSearch/query"
ANNOUNCEMENT_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
PDF_BASE = "https://static.cninfo.com.cn/"
BEIJING = timezone(timedelta(hours=8))
MAX_PAGE_SIZE = 30

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) InvestmentBrain/0.1",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.cninfo.com.cn/",
}

# Broad CNINFO categories; title classification below prevents unrelated
# announcements from entering the evidence vault during a critical sync.
CRITICAL_CATEGORY = ""


class CninfoError(RuntimeError):
    pass


HttpPost = Callable[[str, dict[str, str], float], Any]


def lookup_org_id(
    symbol: str,
    *,
    timeout: float = 12.0,
    http_post: HttpPost | None = None,
) -> tuple[str, str]:
    code = _normalize_symbol(symbol)
    post = http_post or _post_form_json
    payload = post(TOP_SEARCH_URL, {"keyWord": code, "maxNum": "10"}, timeout)
    if not isinstance(payload, list):
        raise CninfoError("巨潮证券检索返回格式异常")
    for item in payload:
        if isinstance(item, dict) and str(item.get("code") or "") == code and item.get("orgId"):
            return str(item["orgId"]), str(item.get("zwjc") or item.get("pinyin") or "")
    raise CninfoError(f"巨潮未找到股票代码 {code} 对应的公司标识")


def search_critical_filings(
    symbol: str,
    *,
    since: str,
    until: str,
    limit: int = 30,
    timeout: float = 15.0,
    http_post: HttpPost | None = None,
) -> list[dict[str, Any]]:
    code = _normalize_symbol(symbol)
    start = _normalize_date(since, "开始日期")
    end = _normalize_date(until, "结束日期")
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    if (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days > 730:
        raise ValueError("单次公告同步时间跨度不能超过两年")
    max_items = max(1, min(int(limit), 180))
    post = http_post or _post_form_json
    org_id, company_name = lookup_org_id(code, timeout=timeout, http_post=post)
    plate = _guess_plate(code)
    column = _guess_column(code)
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    page = 1
    while page <= 20 and len(results) < max_items:
        body = {
            "tabName": "fulltext",
            "pageSize": str(MAX_PAGE_SIZE),
            "pageNum": str(page),
            # CNINFO partitions its announcement archive by exchange.  Using
            # the Shenzhen column for every symbol silently omits Shanghai
            # issuers (for example 600036), even though the stock/org filters
            # themselves are valid.
            "column": column,
            "category": CRITICAL_CATEGORY,
            "plate": plate,
            "searchkey": "",
            "secid": "",
            "trade": "",
            "seDate": f"{start}~{end}",
            "stock": f"{code},{org_id}",
            "sortName": "time",
            "sortType": "desc",
            "isHLtitle": "true",
        }
        payload = post(ANNOUNCEMENT_QUERY_URL, body, timeout)
        if not isinstance(payload, dict):
            raise CninfoError("巨潮公告查询返回格式异常")
        items = payload.get("announcements") or []
        if not isinstance(items, list):
            raise CninfoError("巨潮公告列表格式异常")
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized = normalize_announcement(item, fallback_name=company_name)
            if normalized is None:
                continue
            key = (normalized["symbol"], normalized["disclosed_at"][:10], normalized["title"])
            if key in seen:
                continue
            seen.add(key)
            results.append(normalized)
            if len(results) >= max_items:
                break
        if not payload.get("hasMore") or not items:
            break
        page += 1
    return results


def normalize_announcement(item: dict[str, Any], *, fallback_name: str = "") -> dict[str, Any] | None:
    title = re.sub(r"</?em>", "", str(item.get("announcementTitle") or "")).strip()
    filing_type = classify_critical_filing(title)
    if filing_type is None:
        return None
    adjunct = str(item.get("adjunctUrl") or "").lstrip("/")
    if not adjunct.lower().endswith(".pdf"):
        return None
    symbol = _normalize_symbol(str(item.get("secCode") or ""))
    timestamp = item.get("announcementTime")
    try:
        disclosed = datetime.fromtimestamp(int(timestamp) / 1000, tz=BEIJING).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        disclosed = ""
    report_period_match = re.search(r"(20\d{2})\s*年", title)
    report_period = ""
    if report_period_match:
        year = report_period_match.group(1)
        report_period = {
            "annual_report": f"{year}-12-31",
            "interim_report": f"{year}-06-30",
            "quarterly_report": f"{year}-03-31" if "第一季度" in title else f"{year}-09-30",
            "audit_report": f"{year}-12-31",
            "internal_control": f"{year}-12-31",
            "correction": f"{year}-12-31" if "年度" in title else "",
        }.get(filing_type, "")
    return {
        "symbol": symbol,
        "company_name": str(item.get("secName") or fallback_name or ""),
        "title": title,
        "filing_type": filing_type,
        "report_period": report_period,
        "disclosed_at": disclosed,
        "source_provider": "cninfo_public",
        "source_notice_id": str(item.get("announcementId") or ""),
        "source_url": urllib.parse.urljoin(PDF_BASE, adjunct),
        "is_correction": filing_type == "correction" or bool(re.search(r"更正|修订|补充", title)),
        "metadata": {
            "adjunct_size_kb": item.get("adjunctSize"),
            "announcement_time_ms": timestamp,
            "org_id": item.get("orgId"),
        },
    }


def classify_critical_filing(title: str) -> str | None:
    compact = re.sub(r"\s+", "", title)
    if not compact:
        return None
    if re.search(r"问询函|监管工作函", compact):
        return "inquiry"
    if re.search(r"更正公告|补充公告|前期会计差错更正|追溯调整", compact):
        return "correction"
    if re.search(r"内部控制.*报告", compact):
        return "internal_control"
    if re.search(r"审计报告|审计意见", compact):
        return "audit_report"
    if "摘要" in compact:
        return None
    if re.search(r"半年度报告(?:（修订版）|\(修订版\))?$", compact):
        return "interim_report"
    if re.search(r"第[一三]季度报告(?:（修订版）|\(修订版\))?$", compact):
        return "quarterly_report"
    if re.search(r"年度报告(?:（修订版）|\(修订版\))?$", compact):
        return "annual_report"
    return None


def _post_form_json(url: str, body: dict[str, str], timeout: float) -> Any:
    raw_body = urllib.parse.urlencode(body).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(1, 4):
        request = urllib.request.Request(url, data=raw_body, headers=DEFAULT_HEADERS, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - fixed HTTPS endpoints
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(0.6 * (2 ** (attempt - 1)))
    assert last_error is not None
    raise CninfoError(f"巨潮公告查询失败（已重试 3 次）：{last_error}") from last_error


def _normalize_symbol(value: str) -> str:
    symbol = value.strip().upper().split(".", 1)[0]
    if not re.fullmatch(r"\d{6}", symbol):
        raise ValueError("股票代码必须是六位数字")
    return symbol


def _normalize_date(value: str, label: str) -> str:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是 YYYY-MM-DD") from exc


def _guess_plate(symbol: str) -> str:
    if symbol.startswith(("4", "8", "9")):
        return "bj"
    return "sh" if symbol.startswith(("5", "6")) else "sz"


def _guess_column(symbol: str) -> str:
    """Return CNINFO's exchange column for a normalised A-share symbol.

    ``plate`` is a market board filter (sh/sz/bj); ``column`` is a separate
    CNINFO archive partition, so it must not be inferred from a fixed default.
    """
    code = _normalize_symbol(symbol)
    if code.startswith(("4", "8", "9")):
        return "neeq"
    return "sse" if code.startswith(("5", "6")) else "szse"
