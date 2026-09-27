import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from backend.cninfo_provider import (
    ANNOUNCEMENT_QUERY_URL,
    _post_form_json,
    classify_critical_filing,
    normalize_announcement,
    search_critical_filings,
)


class CninfoProviderTests(unittest.TestCase):
    def test_audit_report_infers_fiscal_year_end(self):
        row = normalize_announcement(
            {
                "announcementId": "audit-1",
                "announcementTitle": "招商银行股份有限公司2025年度审计报告",
                "announcementTime": 0,
                "adjunctUrl": "finalpage/2026-03-28/audit.PDF",
                "secCode": "600036",
                "secName": "招商银行",
            }
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["filing_type"], "audit_report")
        self.assertEqual(row["report_period"], "2025-12-31")

    def test_title_classification_keeps_bodies_and_risk_documents(self):
        cases = {
            "贵州茅台2025年年度报告": "annual_report",
            "贵州茅台2025年年度报告摘要": None,
            "2025年半年度报告": "interim_report",
            "2025年第三季度报告": "quarterly_report",
            "关于前期会计差错更正的公告": "correction",
            "关于监管工作函的回复公告": "inquiry",
            "2025年度内部控制审计报告": "internal_control",
            "董事会决议公告": None,
        }
        for title, expected in cases.items():
            self.assertEqual(classify_critical_filing(title), expected, title)

    def test_revised_report_is_marked_as_correction_even_when_type_stays_report(self):
        row = normalize_announcement(
            {
                "announcementId": "revised-1",
                "announcementTitle": "贵州茅台2025年年度报告（修订版）",
                "announcementTime": 0,
                "adjunctUrl": "finalpage/2026-04-02/revised.PDF",
                "secCode": "600519",
                "secName": "贵州茅台",
            }
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["filing_type"], "annual_report")
        self.assertEqual(row["report_period"], "2025-12-31")
        self.assertTrue(row["is_correction"])

    def test_search_uses_org_id_filters_and_normalizes_pdf_metadata(self):
        captured = []
        disclosed_ms = int(datetime(2026, 3, 30, tzinfo=timezone.utc).timestamp() * 1000)

        def fake_post(url, body, timeout):
            captured.append((url, body, timeout))
            if "topSearch" in url:
                return [{"code": "600519", "orgId": "gssh0600519", "zwjc": "贵州茅台"}]
            return {
                "hasMore": False,
                "announcements": [
                    {
                        "announcementId": "abc",
                        "announcementTitle": "贵州茅台2025年年度报告",
                        "announcementTime": disclosed_ms,
                        "adjunctUrl": "finalpage/2026-03-30/abc.PDF",
                        "secCode": "600519",
                        "secName": "贵州茅台",
                    },
                    {
                        "announcementId": "summary",
                        "announcementTitle": "贵州茅台2025年年度报告摘要",
                        "announcementTime": disclosed_ms,
                        "adjunctUrl": "finalpage/2026-03-30/summary.PDF",
                        "secCode": "600519",
                    },
                ],
            }

        rows = search_critical_filings(
            "600519",
            since="2026-01-01",
            until="2026-04-30",
            http_post=fake_post,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["filing_type"], "annual_report")
        self.assertEqual(rows[0]["report_period"], "2025-12-31")
        self.assertEqual(rows[0]["source_notice_id"], "abc")
        self.assertTrue(rows[0]["source_url"].startswith("https://static.cninfo.com.cn/"))
        self.assertEqual(captured[1][1]["stock"], "600519,gssh0600519")
        self.assertEqual(captured[1][1]["plate"], "sh")
        self.assertEqual(captured[1][1]["column"], "sse")

    def test_search_uses_shenzhen_archive_for_shenzhen_symbols(self):
        captured = []

        def fake_post(url, body, timeout):
            captured.append((url, body, timeout))
            if "topSearch" in url:
                return [{"code": "000001", "orgId": "gssz0000001", "zwjc": "平安银行"}]
            return {"hasMore": False, "announcements": []}

        self.assertEqual(
            search_critical_filings(
                "000001", since="2026-01-01", until="2026-04-30", http_post=fake_post
            ),
            [],
        )
        self.assertEqual(captured[1][1]["plate"], "sz")
        self.assertEqual(captured[1][1]["column"], "szse")

    def test_search_rejects_excessive_or_reversed_date_ranges(self):
        with self.assertRaisesRegex(ValueError, "不能晚于"):
            search_critical_filings("600519", since="2026-03-01", until="2026-02-01", http_post=lambda *_: [])
        with self.assertRaisesRegex(ValueError, "两年"):
            search_critical_filings("600519", since="2022-01-01", until="2026-01-01", http_post=lambda *_: [])

    def test_http_post_retries_transient_failures_before_succeeding(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"announcements": []}'
        with patch(
            "backend.cninfo_provider.urllib.request.urlopen",
            side_effect=[TimeoutError("slow"), TimeoutError("slow again"), response],
        ) as urlopen, patch("backend.cninfo_provider.time.sleep") as sleep:
            result = _post_form_json(ANNOUNCEMENT_QUERY_URL, {"stock": "600519,org"}, 1.0)
        self.assertEqual(result, {"announcements": []})
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.6, 1.2])


if __name__ == "__main__":
    unittest.main()
