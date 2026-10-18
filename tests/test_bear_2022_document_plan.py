"""Invented filing fragments only; no official or market requests."""

import unittest

from tools import bear_2022_document_plan as d


class DocumentPlanTests(unittest.TestCase):
    def test_fixed_scope_and_budget(self):
        self.assertEqual(len(d.DOCUMENTS), 3)
        self.assertEqual(d.POLICY["maximum_official_requests"], 3)
        self.assertEqual(d.POLICY["market_requests"], 0)
        self.assertEqual(d.DOCUMENT_LIMIT, 16 * d.POLICY["archive_blob_bytes"])
        self.assertEqual(len(d.PLAN_HASH), 64)
        with self.assertRaisesRegex(ValueError, "BEAR2022_DOCUMENT_SCOPE"):
            d.product("arbitrary")

    def test_readable_text_and_no_admission(self):
        body = (
            b"<h1>SPDR S&amp;P 500 ETF Trust</h1>"
            b"<script>tax character invented</script><style>tax character hidden</style>"
            b"<p>The tax <b>character</b> for 2022 is:</p>"
            b"<table><tr><td>Ordinary income</td><td>123</td></tr></table>"
        )
        result = d.evidence("spy-fy2023", body)
        self.assertEqual(len(result["tax_sections"]), 1)
        self.assertIn("Ordinary income 123", result["tax_sections"][0])
        self.assertNotIn("invented", result["tax_sections"][0])
        self.assertEqual(result["body_sha256"], d.digest(body))
        self.assertFalse(result["classification_automatically_admitted"])
        self.assertFalse(result["engine_invoked"])

    def test_fail_closed_identity_and_sections(self):
        for body, reason in (
            (b"<p>tax character: ordinary income</p>", "DOCUMENT_ISSUER_IDENTITY"),
            (b"<p>SPDR S&amp;P 500 ETF Trust</p>", "DOCUMENT_TAX_SECTION_COUNT"),
            (b"", "DOCUMENT_SIZE"),
        ):
            with (
                self.subTest(reason=reason),
                self.assertRaisesRegex(ValueError, "BEAR2022_" + reason),
            ):
                d.evidence("spy-fy2023", body)


if __name__ == "__main__":
    unittest.main()
