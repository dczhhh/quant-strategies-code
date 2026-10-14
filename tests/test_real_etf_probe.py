"""Invented issuer/price bytes; no fixture is a real/PIT acceptance case."""

import io
import json
import unittest
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import yaml

from tests.test_private_archive_migration import FakeGitHub
from tools import real_etf_probe as p


def fixture_xlsx(formula=False, serial="45555"):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Invented" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        z.writestr(
            "xl/styles.xml",
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>',
        )
        cell = "<f>1+1</f><v>2</v>" if formula else "<v>-2</v>"
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>SPY</t></is></c><c r="B1" s="1"><v>'
            + serial
            + '</v></c><c r="C1">'
            + cell
            + "</c></row></sheetData></worksheet>",
        )
    return content.getvalue()


def prices():
    return {
        date(2024, 9, 19): Decimal(100),
        date(2024, 9, 20): Decimal(99),
        date(2024, 9, 23): Decimal(101),
    }


class ETFProbeTests(unittest.TestCase):
    def test_issuer_dates_handle_text_and_numeric_date_output_without_year_swap(self):
        self.assertEqual(p.issuer_day("12/17/1999"), date(1999, 12, 17))
        self.assertEqual(p.issuer_day("1993-03-19"), date(1993, 3, 19))
        self.assertEqual(p.issuer_day("09/20/2024"), date(2024, 9, 20))
        self.assertEqual(p.issuer_day("2024-09-20"), date(2024, 9, 20))
        for value in ("20/09/2024", "02/30/2024", "9/20/24", {"formula": True}, None):
            with self.subTest(value=value), self.assertRaises(p.Blocked):
                p.issuer_day(value)
        self.assertEqual(
            p.schema_summary({"dividend": [{"A": "09/20/2024"}]})[0]["date_years"], ["2024"]
        )

    def test_exact_history_match_does_not_grant_tax_basis_cash_or_pit(self):
        event = {
            "ex": date(2024, 9, 20),
            "record": date(2024, 9, 20),
            "pay": date(2024, 9, 30),
            "amount": Decimal(1),
        }
        row = {
            "A": "Invented Fund",
            "B": "SPY",
            "C": "000000001",
            "D": "09/20/2024",
            "E": "09/20/2024",
            "F": "09/30/2024",
            "G": "1",
            "H": "",
            "I": "0",
            "J": "Quarterly",
        }
        value = p.history_match({"dividend": [p.HISTORY_HEADERS, row]}, "SPY", [event])
        self.assertEqual(value["status"], "ISSUER_HISTORY_FIELD_MATCH")
        self.assertEqual(value["rows"][0]["observed_capital_gain_fields"], {"H": None, "I": "0"})
        self.assertFalse(value["ordinary_cash_admission"])
        self.assertFalse(value["historical_PIT_verified"])
        self.assertIn("not independently", value["share_basis_attestation"])
        self.assertEqual(row["H"], "")

    def test_pre_2000_history_does_not_poison_a_unique_2024_event(self):
        event = {
            "ex": date(2024, 9, 20),
            "record": date(2024, 9, 20),
            "pay": date(2024, 9, 30),
            "amount": Decimal(1),
        }
        row = {
            "A": "Invented Fund",
            "B": "SPY",
            "C": "000000001",
            "D": "09/20/2024",
            "E": "09/20/2024",
            "F": "09/30/2024",
            "G": "1",
            "H": "",
            "I": "0",
            "J": "Quarterly",
        }
        historical = {**row, "D": "12/17/1999", "E": "12/17/1999", "F": "01/03/2000"}
        value = p.history_match({"dividend": [p.HISTORY_HEADERS, historical, row]}, "SPY", [event])
        self.assertEqual(value["status"], "ISSUER_HISTORY_FIELD_MATCH")
        self.assertEqual(len(value["rows"]), 1)
        self.assertEqual(value["rows"][0]["parsed_row_index"], 2)
        self.assertFalse(value["ordinary_cash_admission"])

    def test_history_ambiguity_conflicts_formulas_and_canonical_header_fail_closed(self):
        event = {
            "ex": date(2024, 9, 20),
            "record": date(2024, 9, 20),
            "pay": date(2024, 9, 30),
            "amount": Decimal(1),
        }
        row = {
            "A": "Invented Fund",
            "B": "SPY",
            "C": "000000001",
            "D": "09/20/2024",
            "E": "09/20/2024",
            "F": "09/30/2024",
            "G": "1",
            "H": "",
            "I": "0",
            "J": "Quarterly",
        }
        for change in (
            {"G": "0.5"},
            {"E": "09/23/2024"},
            {"G": {"formula": True}},
            {"C": "wrong"},
            {"I": "-1"},
        ):
            with self.subTest(change=change):
                value = p.history_match(
                    {"dividend": [p.HISTORY_HEADERS, {**row, **change}]}, "SPY", [event]
                )
                self.assertEqual(value["status"], "ISSUER_HISTORY_FIELDS_UNVERIFIED")
        for rows in (
            [p.HISTORY_HEADERS, row, row],
            [p.HISTORY_HEADERS, {**row, "B": "XLB"}],
            [{**p.HISTORY_HEADERS, "G": "Total distribution"}, row],
        ):
            self.assertEqual(
                p.history_match({"dividend": rows}, "SPY", [event])["status"],
                "ISSUER_HISTORY_FIELDS_UNVERIFIED",
            )

    def test_tax_year_binds_primary_title_and_sheet_not_report_clock(self):
        sheets = {
            "2025 ICI Primary": [
                {"A": "SPDR ETF 2025 Tax Summary - Primary Layout"},
                {"D": "TARGET DELIVERY DATE: JANUARY 20, 2026"},
            ]
        }
        value = p.classification_gaps({"ssga-ici-primary-current": sheets}, 2024)
        self.assertEqual(value["observed_primary_tax_years"], [2025])
        self.assertFalse(value["event_year_document_present"])
        self.assertFalse(
            p.classification_gaps({"ssga-ici-primary-current": sheets}, 2026)[
                "event_year_document_present"
            ]
        )
        self.assertFalse(value["ordinary_cash_admission"])
        self.assertIn("EVENT_YEAR_PRIMARY_TAX_DOCUMENT_MISSING", value["reasons"])
        changed = {"2024 ICI Primary": sheets["2025 ICI Primary"]}
        self.assertEqual(
            p.classification_gaps({"ssga-ici-primary-current": changed}, 2024)[
                "observed_primary_tax_years"
            ],
            [],
        )

    def test_read_only_dates_signed_values_and_formula_cache_exclusion(self):
        sheets = p.workbook(fixture_xlsx())
        self.assertEqual(sheets["Invented"][0], {"A": "SPY", "B": "2024-09-20", "C": "-2"})
        self.assertEqual(p.workbook(fixture_xlsx(True))["Invented"][0]["C"], {"formula": True})
        schema = p.schema_summary(sheets)[0]
        self.assertEqual(schema["target_tickers"], ["SPY"])
        self.assertEqual(schema["date_years"], ["2024"])
        self.assertNotIn("-2", json.dumps(schema))

    def test_xml_entity_and_zip_expansion_fail_closed(self):
        with self.assertRaisesRegex(p.Blocked, "XML_ENTITY_FORBIDDEN"):
            p.xml(b'<!DOCTYPE x [<!ENTITY x "payload">]><x>&x;</x>')
        with (
            patch.object(p, "XML_LIMIT", 1),
            self.assertRaisesRegex(p.Blocked, "ZIP_EXPANSION_LIMIT"),
        ):
            p.workbook(fixture_xlsx())

    def test_bad_serial_and_nonfinite_numeric_source_cells_reject(self):
        for value in ("nan", "1e1000", "-1"):
            with self.subTest(value=value), self.assertRaises(p.Blocked):
                p.workbook(fixture_xlsx(serial=value))

    def test_independent_reinvestment_units_and_price_only_series_diverge(self):
        raw = prices()
        adjusted = {day: close / 2 for day, close in raw.items()}
        dividend = [{"ex": date(2024, 9, 20), "amount": Decimal(1)}]
        result = p.return_probe(raw, adjusted, dividend)
        self.assertEqual(result["status"], "RETROSPECTIVE_GROSS_SIGNAL_MATH_PASS")
        self.assertEqual(Decimal(result["journal"][0]["gross_index"]), 1)
        self.assertEqual(Decimal(result["journal"][0]["price_only_index"]), Decimal("0.99"))
        self.assertLessEqual(Decimal(result["max_error"]), Decimal("1E-28"))
        self.assertFalse(result["cash_credit_assumed"])
        self.assertFalse(result["signal_added_to_cash_ledger"])
        self.assertFalse(result["historical_PIT_verified"])

    def test_missing_event_boundary_and_changing_anchor_reject(self):
        raw = prices()
        for dividends, comparison, code in (
            ([{"ex": date(2024, 9, 19), "amount": Decimal(1)}], raw, "EX_BOUNDARY_MISSING"),
            ([{"ex": date(2024, 9, 18), "amount": Decimal(1)}], raw, "EX_BOUNDARY_MISSING"),
            (
                [{"ex": date(2024, 9, 20), "amount": Decimal(1)}],
                {**raw, date(2024, 9, 20): Decimal(50)},
                "SPLIT_ANCHOR_NOT_CONSTANT",
            ),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(p.Blocked, code):
                p.return_probe(raw, comparison, dividends)

    def test_pinned_inputs_only_read_commit_graph_and_cache_immutable_reads(self):
        class Destination:
            token = "INVENTED_TOKEN"
            count = 0

            def request(self, method, path):
                self.count += 1
                self_outer.assertNotIn("/ref/", path)
                return {"parents": [{"sha": "a" * 40}]}

        self_outer = self
        dest = Destination()
        view = p.PinnedArchive(dest)
        data_ref = next(iter(p.INPUT_COMMITS))
        self.assertEqual(view.ref(data_ref), p.INPUT_COMMITS[data_ref])
        budget_ref = data_ref.replace("research-data/", "research-reservations/")
        self.assertEqual(view.ref(budget_ref), "a" * 40)
        self.assertEqual(view.ref(budget_ref), "a" * 40)
        self.assertEqual(dest.count, 1)
        with self.assertRaisesRegex(p.Blocked, "INPUT_WRITE_FORBIDDEN"):
            view.request("POST", "/git/refs", {})
        with self.assertRaisesRegex(p.Blocked, "UNREVIEWED_INPUT_REF"):
            view.ref("research-data/unreviewed")

    def test_three_source_requests_are_reserved_once_and_fresh_readback_has_zero_requests(self):
        dest = FakeGitHub(p.m.DESTINATION)
        seen = []

        def fetch(doc):
            self.assertIsNotNone(dest.ref(p.document_ref(doc, True)))
            seen.append(doc["id"])
            return 200, fixture_xlsx()

        docs, count = p.capture_documents(dest, "a" * 40, fetch=fetch)
        self.assertEqual(count, 3)
        self.assertEqual(len(seen), 3)
        self.assertEqual(len(docs), 3)
        dest.events.clear()
        again, count = p.capture_documents(
            dest, "b" * 40, verify_only=True, fetch=lambda _: self.fail("fetch during verify")
        )
        self.assertEqual(count, 0)
        self.assertEqual(again, docs)
        self.assertTrue(all(method == "GET" for method, _ in dest.events))

    def test_failed_request_leaves_unknown_reservation_and_is_never_retried(self):
        dest = FakeGitHub(p.m.DESTINATION)
        calls = []

        def fetch(doc):
            calls.append(doc)
            raise OSError("INVENTED_PRIVATE_FAILURE")

        with self.assertRaises(OSError):
            p.capture_documents(dest, "a" * 40, fetch=fetch)
        with self.assertRaisesRegex(p.Blocked, "UNKNOWN_DOCUMENT_REQUEST_NOT_RESET"):
            p.capture_documents(dest, "b" * 40, fetch=fetch)
        self.assertEqual(len(calls), 1)

    def test_document_larger_than_one_original_blob_roundtrips_without_changing_blob_limits(self):
        content = io.BytesIO(fixture_xlsx())
        with zipfile.ZipFile(content, "a", compression=zipfile.ZIP_STORED) as z:
            z.writestr("padding.bin", b"0" * p.m.FILE_LIMIT)
        large = content.getvalue()
        self.assertGreater(len(large), p.m.FILE_LIMIT)
        dest = FakeGitHub(p.m.DESTINATION)
        docs, made = p.capture_documents(dest, "a" * 40, fetch=lambda _: (200, large))
        self.assertEqual(made, 3)
        self.assertTrue(all(v["body"] == large for v in docs.values()))
        self.assertTrue(all(v["receipt"]["part_count"] == 2 for v in docs.values()))
        self.assertEqual(p.m.FILE_LIMIT, 524288)
        again, made = p.capture_documents(
            dest, "b" * 40, verify_only=True, fetch=lambda _: self.fail("repeated download")
        )
        self.assertEqual(made, 0)
        self.assertEqual(again, docs)

    def test_document_credential_detection_includes_cross_chunk_boundary(self):
        token = "INVENTED_ARCHIVE_CREDENTIAL"
        body = b"0" * (p.m.FILE_LIMIT - 4) + token.encode() + b"0"
        with self.assertRaisesRegex(p.Blocked, "DOCUMENT_CREDENTIAL_FOUND"):
            p.document_secret_free(body, (token,))
        with self.assertRaisesRegex(p.Blocked, "DOCUMENT_CREDENTIAL_PATTERN"):
            p.document_secret_free(b"0" * p.m.FILE_LIMIT + b"api_key=INVENTED", ())

    def test_inspection_retains_unknown_outcome_without_fetch_reset_or_source_write(self):
        dest = FakeGitHub(p.m.DESTINATION)

        def fetch(doc):
            if doc == p.DOCUMENTS[-1]:
                raise OSError("INVENTED_UNKNOWN_OUTCOME")
            return 200, fixture_xlsx()

        with self.assertRaises(OSError):
            p.capture_documents(dest, "a" * 40, fetch=fetch)
        dest.events.clear()
        docs = p.inspect_documents(dest)
        self.assertEqual(sum(v["receipt"] is not None for v in docs.values()), 2)
        unknown = docs[p.DOCUMENTS[-1]["id"]]
        self.assertIsNone(unknown["body"])
        self.assertEqual(unknown["unknown_reservation"]["source_commit"], "a" * 40)
        self.assertTrue(all(method == "GET" for method, _ in dest.events))
        self.assertEqual(p.inspect_documents(dest), docs)
        with self.assertRaisesRegex(p.Blocked, "UNKNOWN_DOCUMENT_REQUEST_NOT_RESET"):
            p.capture_documents(
                dest, "b" * 40, fetch=lambda _: self.fail("repeated source request")
            )

    def test_inspection_cannot_invent_an_attempt_or_accept_an_unreviewed_reservation(self):
        dest = FakeGitHub(p.m.DESTINATION)
        with self.assertRaisesRegex(p.Blocked, "DOCUMENT_NOT_ATTEMPTED"):
            p.inspect_documents(dest)
        doc = p.DOCUMENTS[0]
        path = p.DOCUMENT_PREFIX + doc["id"] + "/reservation.json"
        held = json.loads(p.reservation(doc, "a" * 40))
        held["plan_sha256"] = "0" * 64
        p.b.create(dest, p.document_ref(doc, True), {path: p.m.canonical(held)}, p.m.BOOTSTRAP)
        with self.assertRaisesRegex(p.Blocked, "DOCUMENT_RESERVATION_INVALID"):
            p.inspect_documents(dest)

    def test_non_200_bytes_archived_without_classifier_admission(self):
        dest = FakeGitHub(p.m.DESTINATION)
        docs, _ = p.capture_documents(dest, "a" * 40, fetch=lambda _: (403, b"INVENTED_DENIED"))
        self.assertTrue(all(item["receipt"]["http_status"] == 403 for item in docs.values()))
        self.assertTrue(all(item["body"] == b"INVENTED_DENIED" for item in docs.values()))

    def test_scope_is_finite_us_etfs_and_has_no_market_provider_credential(self):
        self.assertEqual(len(p.INPUT_COMMITS), 36)
        self.assertEqual(len(p.TICKERS), 12)
        self.assertTrue(all("ssga.com/" in v["url"] for v in p.DOCUMENTS))
        workflow = yaml.safe_load(
            (Path(__file__).parents[1] / ".github/workflows/real-etf-probe.yml").read_text()
        )
        for job in workflow["jobs"].values():
            text = json.dumps(job)
            self.assertIn("private-archive", text)
            self.assertNotIn("MASSIVE", text)
            self.assertNotIn("upload-artifact", text)
        self.assertEqual(p.POLICY["vendor_requests"], 0)
        self.assertFalse(p.POLICY["PIT_verified"])


if __name__ == "__main__":
    unittest.main()
