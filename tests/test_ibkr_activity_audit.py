"""Invented statement bytes only; no personal account fixture or real/PIT admission."""

import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

from tools import ibkr_activity_audit as a


def fixture_rows():
    rows = [
        ["账户信息", "Data", "基础货币", "USD"],
        ["Statement", "Data", "Period", "一月 1, 2024 - 六月 30, 2024"],
        ["注释/法律注释", "Data", "注", "交易执行时间以东部时间显示。"],
    ]
    data = {
        "现金报告": [
            ["期初现金", "USD", "50", "50", "0"],
            ["交易（买入）", "USD", "-20", "-20", "0"],
            ["佣金", "USD", "-0.4", "-0.4", "0"],
            ["股息", "USD", "1.5", "1.5", "0"],
            ["代扣税款", "USD", "-0.15", "-0.15", "0"],
            ["期末现金", "USD", "30.95", "30.95", "0"],
            ["期末已结算现金", "USD", "30.95", "30.95", "0"],
        ],
        "按市值计算的表现总结": [
            ["股票", "NVDA", "2", "4", "10", "12", "0", "0", "0", "0", "0", ""],
            ["外汇", "USD", "50", "30.95", "1", "1", "0", "0", "0", "0", "0", ""],
        ],
        "未平仓持仓": [
            ["Summary", "股票", "USD", "NVDA", "4", "1", "10", "40", "12", "48", "8", ""]
        ],
        "交易": [
            [
                "Order",
                "股票",
                "USD",
                "NVDA",
                "2024-01-02, 10:00:00",
                "1",
                "10",
                "10",
                "-10",
                "-0.2",
                "10.2",
                "0",
                "0",
                "O",
            ],
            [
                "Order",
                "股票",
                "USD",
                "NVDA",
                "2024-03-04, 20:30:00",
                "1",
                "10",
                "10",
                "-10",
                "-0.2",
                "10.2",
                "0",
                "0",
                "O",
            ],
        ],
        "股息": [
            ["USD", "2024-03-15 00:00:00", "NVDA(INVENTED) 现金红利", "1.5"],
            ["总数", "", "", "1.5"],
        ],
        "代扣税": [
            ["USD", "2024-03-15 00:00:00", "NVDA(INVENTED) 税收", "-0.15", ""],
            ["总数", "", "", "-0.15", ""],
        ],
        "应计股息的变化": [
            [
                "股票",
                "USD",
                "NVDA",
                "2024-03-01 00:00:00",
                "2024-03-04 00:00:00",
                "2024-03-15 00:00:00",
                "3",
                "0.15",
                "0",
                "0.5",
                "1.5",
                "1.35",
                "Po",
            ],
            [
                "股票",
                "USD",
                "NVDA",
                "2024-03-15 00:00:00",
                "2024-03-04 00:00:00",
                "2024-03-15 00:00:00",
                "3",
                "-0.15",
                "0",
                "0.5",
                "-1.5",
                "-1.35",
                "Re",
            ],
        ],
        "净资产值变更": [
            ["开始价值", "70"],
            ["按市值计价", "8"],
            ["股息", "1.5"],
            ["代扣税款", "-0.15"],
            ["佣金", "-0.4"],
            ["结束价值", "78.95"],
        ],
    }
    for section, headers in a.HEADERS.items():
        rows.append([section, "Header", *headers[0]])
        rows.extend([section, "Data", *values] for values in data.get(section, []))
    return rows


def xlsx(rows, formula=False):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Invented" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        xml_rows = []
        for i, values in enumerate(rows, 1):
            cells = []
            for j, value in enumerate(values):
                content = (
                    "<f>1+1</f><v>2</v>"
                    if formula and i == 1 and j == 3
                    else "<is><t>" + escape(value) + "</t></is>"
                )
                cells.append(f'<c r="{chr(65 + j)}{i}" t="inlineStr">{content}</c>')
            xml_rows.append(f'<row r="{i}">' + "".join(cells) + "</row>")
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            + "".join(xml_rows)
            + "</sheetData></worksheet>",
        )
    return buffer.getvalue()


def find(rows, section, label):
    return next(r for r in rows if r[:3] == [section, "Data", label])


class ActivityAuditTests(unittest.TestCase):
    def test_detail_replay_and_ex_date_quantity_without_seeding_receivables(self):
        report = a.analyze(xlsx(fixture_rows()))
        summary = a.public_summary(report)
        self.assertTrue(summary["cash_detail_reconciles"])
        self.assertTrue(summary["position_quantity_reconciles"])
        self.assertTrue(summary["ordinary_US_dividend_lifecycles_reconcile"])
        self.assertTrue(summary["ending_reported_value_NAV_reconciles"])
        event = report["ordinary_US_dividend_lifecycles"][0]
        self.assertEqual(event["eligible_quantity"]["computed"], "3")
        self.assertFalse(summary["native_engine_verified"])
        self.assertFalse(summary["historical_PIT_verified"])
        self.assertEqual(summary["certification"], "UNVERIFIED")

    def test_fx_counter_leg_and_USD_commission_are_not_quote_currency_fees(self):
        rows = fixture_rows()
        rows.extend(
            [
                ["交易", "Header", *a.HEADERS["交易"][1]],
                [
                    "交易",
                    "Data",
                    "Order",
                    "外汇",
                    "EUR",
                    "USD.EUR",
                    "2024-02-01, 10:00:00",
                    "2",
                    "0.9",
                    "",
                    "-1.8",
                    "-1",
                    "",
                    "",
                    "0",
                    "",
                ],
            ]
        )
        for label, value in (("佣金", "-1.4"), ("期末现金", "31.95"), ("期末已结算现金", "31.95")):
            find(rows, "现金报告", label)[4:6] = [value, value]
        rows.extend([["现金报告", "Data", "交易（卖出）", "USD", "2", "2", "0"]])
        for label, value in (
            ("期初现金", "10"),
            ("交易（买入）", "-1.8"),
            ("期末现金", "8.2"),
            ("期末已结算现金", "8.2"),
        ):
            rows.append(["现金报告", "Data", label, "EUR", value, value, "0"])
        rows.append(
            [
                "按市值计算的表现总结",
                "Data",
                "外汇",
                "EUR",
                "10",
                "8.2",
                "1.1",
                "1.1",
                "0",
                "0",
                "0",
                "0",
                "0",
                "",
            ]
        )
        report = a.analyze(xlsx(rows))
        self.assertTrue(a.public_summary(report)["cash_detail_reconciles"])
        eur = next(c for c in report["cash"] if c["currency"] == "EUR")
        self.assertNotIn("佣金", eur["categories"])

    def test_stock_combined_commission_tax_is_split_by_exact_detail_once(self):
        rows = fixture_rows()
        find(rows, "交易", "Order")[11] = "-0.3"
        rows.append(
            [
                "交易费用",
                "Data",
                "股票",
                "USD",
                "2024-01-02, 10:00:00",
                "NVDA",
                "Invented tax",
                "1",
                "10",
                "-0.1",
                "",
            ]
        )
        rows.append(["现金报告", "Data", "交易费用", "USD", "-0.1", "-0.1", "0"])
        for label in ("期末现金", "期末已结算现金"):
            find(rows, "现金报告", label)[4:6] = ["30.85", "30.85"]
        report = a.analyze(xlsx(rows))
        self.assertTrue(a.public_summary(report)["cash_detail_reconciles"])
        self.assertEqual(report["cash"][0]["categories"]["佣金"]["computed"], "-0.4")
        find(rows, "交易费用", "股票")[5] = "OTHER"
        with self.assertRaises(a.p.Blocked):
            a.analyze(xlsx(rows))

    def test_unknown_opening_cash_missing_sections_or_timezone_never_become_zero(self):
        original = fixture_rows()
        unknown = deepcopy(original)
        find(unknown, "现金报告", "期初现金")[4] = "--"
        missing = [r for r in original if r[0] != "存款和取款"]
        timezone = [r for r in original if r[0] != "注释/法律注释"]
        for rows in (unknown, missing, timezone):
            with self.subTest(rows=rows), self.assertRaises(a.p.Blocked):
                a.analyze(xlsx(rows))

    def test_formula_duplicate_headers_and_mislabelled_dated_totals_are_rejected(self):
        with self.assertRaises(a.p.Blocked):
            a.analyze(xlsx(fixture_rows(), formula=True))
        rows = fixture_rows()
        rows.append(["股息", "Data", "总数", "2024-03-15 00:00:00", "", "1.5"])
        with self.assertRaises(a.p.Blocked):
            a.analyze(xlsx(rows))
        rows = fixture_rows()
        next(r for r in rows if r[:2] == ["交易", "Header"])[11] = "佣金 USD"
        with self.assertRaises(a.p.Blocked):
            a.analyze(xlsx(rows))

    def test_double_credit_missing_tax_and_missing_receivable_removal_fail_closed(self):
        original = fixture_rows()
        double = deepcopy(original)
        double.append(deepcopy(find(double, "股息", "USD")))
        no_tax = [r for r in original if r[:3] != ["代扣税", "Data", "USD"]]
        no_reversal = [r for r in original if not (r[0] == "应计股息的变化" and r[-1] == "Re")]
        for rows in (double, no_tax, no_reversal):
            with self.subTest(rows=rows), self.assertRaises(a.p.Blocked):
                a.analyze(xlsx(rows))

    def test_cent_rounding_is_explicit_and_does_not_change_cash_tolerance(self):
        rows = fixture_rows()
        for r in rows:
            if r[0] == "应计股息的变化" and r[1] == "Data":
                r[11] = "0.335"
                r[12:14] = ["1.01", "0.86"] if r[14] == "Po" else ["-1.01", "-0.86"]
        find(rows, "股息", "USD")[5] = "1.01"
        find(rows, "现金报告", "股息")[4] = "1.01"
        for label in ("期末现金", "期末已结算现金"):
            find(rows, "现金报告", label)[4] = "30.46"
        find(rows, "净资产值变更", "股息")[3] = "1.01"
        find(rows, "净资产值变更", "结束价值")[3] = "78.46"
        report = a.analyze(xlsx(rows))
        event = report["ordinary_US_dividend_lifecycles"][0]
        self.assertFalse(event["gross_unrounded"]["within_tolerance"])
        self.assertTrue(event["gross_USD_cent_rounding"]["within_tolerance"])
        self.assertTrue(a.public_summary(report)["ordinary_US_dividend_lifecycles_reconcile"])
        self.assertEqual(event["actual_cash_credit"]["tolerance_units"], "1E-8")

    def test_cash_and_position_discrepancies_remain_visible_without_admission(self):
        rows = fixture_rows()
        find(rows, "现金报告", "期末现金")[4] = "30.96"
        find(rows, "按市值计算的表现总结", "股票")[5] = "5"
        summary = a.public_summary(a.analyze(xlsx(rows)))
        self.assertFalse(summary["cash_detail_reconciles"])
        self.assertFalse(summary["position_quantity_reconciles"])
        self.assertFalse(summary["performance_admission"])

    def test_ADR_accruals_do_not_post_cash_or_grant_new_buy_admission(self):
        rows = fixture_rows()
        rows.append(
            [
                "应计股息的变化",
                "Data",
                "股票",
                "USD",
                "TSM",
                "2024-03-01 00:00:00",
                "2024-03-04 00:00:00",
                "2024-03-15 00:00:00",
                "20",
                "1",
                "0",
                "0.5",
                "10",
                "9",
                "Po",
            ]
        )
        report = a.analyze(xlsx(rows))
        self.assertTrue(a.public_summary(report)["cash_detail_reconciles"])
        self.assertEqual(len(report["ordinary_US_dividend_lifecycles"]), 1)
        self.assertFalse(report["performance_admission"])

    def test_public_summary_is_allowlisted_and_contains_no_source_fingerprint(self):
        report = a.analyze(xlsx(fixture_rows()))
        text = json.dumps(a.public_summary(report))
        for value in (report["source_sha256_private"], "NVDA", "30.95", "2024-03-04", "INVENTED"):
            self.assertNotIn(value, text)

    def test_cli_preserves_source_and_existing_report_and_writes_private_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "source.xlsx", Path(folder) / "audit.json"
            body = xlsx(fixture_rows())
            source.write_bytes(body)
            argv = ["audit", str(source), "--private-report", str(target)]
            output = io.StringIO()
            if not a.private_report_supported():
                with patch("sys.argv", argv), redirect_stdout(output):
                    self.assertEqual(a.main(), 1)
                self.assertFalse(target.exists())
                self.assertEqual(source.read_bytes(), body)
                self.assertIn("PRIVATE_REPORT_PERMISSION_PLATFORM_UNSUPPORTED", output.getvalue())
                return
            with patch("sys.argv", argv), redirect_stdout(output):
                self.assertEqual(a.main(), 0)
            original_report = target.read_bytes()
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(source.read_bytes(), body)
            self.assertNotIn(str(source), output.getvalue())
            with patch("sys.argv", argv), redirect_stdout(io.StringIO()):
                self.assertEqual(a.main(), 1)
            self.assertEqual(target.read_bytes(), original_report)
            with (
                patch("sys.argv", ["audit", str(source), "--private-report", str(source)]),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(a.main(), 1)
            self.assertEqual(source.read_bytes(), body)

    def test_unsupported_private_permission_platform_blocks_before_source_read_or_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "missing.xlsx", Path(folder) / "new.json"
            argv = ["audit", str(source), "--private-report", str(target)]
            output = io.StringIO()
            with (
                patch("sys.argv", argv),
                patch.object(a, "private_report_supported", return_value=False),
                redirect_stdout(output),
            ):
                self.assertEqual(a.main(), 1)
            self.assertIn("PRIVATE_REPORT_PERMISSION_PLATFORM_UNSUPPORTED", output.getvalue())
            self.assertFalse(target.exists())

    def test_unexpected_exception_text_never_reaches_public_stdout(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.xlsx"
            source.write_bytes(b"invented")
            argv = ["audit", str(source), "--private-report", str(Path(folder) / "new.json")]
            output = io.StringIO()
            with (
                patch("sys.argv", argv),
                patch.object(a, "private_report_supported", return_value=True),
                patch.object(a, "analyze", side_effect=RuntimeError("PRIVATE_ACCOUNT_VALUE")),
                redirect_stdout(output),
            ):
                self.assertEqual(a.main(), 1)
            self.assertNotIn("PRIVATE_ACCOUNT_VALUE", output.getvalue())
            self.assertIn("STATEMENT_AUDIT_FAILED", output.getvalue())


if __name__ == "__main__":
    unittest.main()
