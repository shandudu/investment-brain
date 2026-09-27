import unittest
from datetime import date

from backend.free_provider import _akshare_quote_window, _company_name, _summed_statement_fact


class FreeProviderTests(unittest.TestCase):
    def test_builtin_bank_identity_is_not_reduced_to_security_code(self):
        self.assertEqual(_company_name("000001.SZ"), "平安银行股份有限公司")

    def test_akshare_quote_window_ends_on_requested_cutoff(self):
        start, end = _akshare_quote_window(date(2026, 9, 6))
        self.assertEqual(end, "20260906")
        self.assertEqual(start, "20260509")

    def test_short_debt_sum_requires_every_component(self):
        report_end = date(2026, 6, 30)
        fact = _summed_statement_fact(
            {"短期借款": 100_000_000, "一年内到期的非流动负债": 20_000_000},
            ("短期借款", "一年内到期的非流动负债"),
            "一年内到期有息债务",
            report_end,
        )
        self.assertEqual(fact["value"], 120)
        self.assertEqual(fact["source"]["completeness"], "complete; all debt components reported")
        self.assertIsNone(
            _summed_statement_fact(
                {"短期借款": 100_000_000},
                ("短期借款", "一年内到期的非流动负债"),
                "一年内到期有息债务",
                report_end,
            )
        )


if __name__ == "__main__":
    unittest.main()
