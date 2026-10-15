"""Invented 2025 source/transport fixtures only; no real tax, PIT or strategy admission."""

import io
import json
import os
import unittest
import zipfile
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

import yaml

from tests.test_private_archive_migration import FakeGitHub
from tools import private_archive_migration as migration
from tools import real_etf_2025 as a

STOPPED = (a.m.Stopped, migration.Stopped)


def workbook(sheet, rows):
    content = io.BytesIO()
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(content, "w") as z:
        z.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{namespace}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{escape(sheet)}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        data = []
        for i, row in enumerate(rows, 1):
            cells = [
                f'<c r="{column}{i}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'
                for column, value in row.items()
            ]
            data.append(f'<row r="{i}">' + "".join(cells) + "</row>")
        z.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{namespace}"><sheetData>'
            + "".join(data)
            + "</sheetData></worksheet>",
        )
    return content.getvalue()


def documents(ticker="SPY", changes=None):
    tax = dict.fromkeys(a.TAX_NUMBERS, "0")
    tax.update(dict.fromkeys(("J", "M", "N", "Q", "R", "U"), "1"))
    tax.update(
        {
            "A": "INVENTED ETF",
            "B": "000000001",
            "C": ticker,
            "D": "",
            "E": "",
            "F": "",
            "G": "2025-09-19",
            "H": "2025-09-19",
            "I": "2025-10-31",
            "K": "",
            "L": "",
        }
    )
    tax.update(changes or {})
    primary = (
        [{"A": "SPDR ETF 2025 Tax Summary - Primary Layout"}]
        + [{"A": "Invented padding"} for _ in range(11)]
        + [a.TAX_HEADERS, tax]
    )
    history = [
        a.p.HISTORY_HEADERS,
        {
            "A": "INVENTED ETF",
            "B": ticker,
            "C": "000000001",
            "D": "2025-09-19",
            "E": "2025-09-19",
            "F": "2025-10-31",
            "G": "1",
            "H": "",
            "I": "0",
            "J": "Quarterly",
        },
    ]
    return {
        doc["id"]: {
            "body": workbook(
                "dividend" if i == 0 else "2025 ICI Primary", history if i == 0 else primary
            ),
            "receipt": {"http_status": 200},
            "commit": str(i + 1) * 40,
        }
        for i, doc in enumerate(a.p.DOCUMENTS[:2])
    }


def market(product, scale=1):
    if product["kind"] == "bars":
        dates = [a.START, "2025-09-18", "2025-09-19", "2025-10-30", "2025-10-31", a.END]
        rows = []
        for i, day in enumerate(dates):
            at = datetime.fromisoformat(day).replace(tzinfo=a.p.NY)
            close = 100 + i
            price = close * scale if product["adjusted"] else close
            rows.append(
                {
                    "t": int(at.timestamp() * 1000),
                    "o": price,
                    "h": price,
                    "l": price,
                    "c": price,
                    "v": 50,
                }
            )
        return {
            "status": "OK",
            "ticker": product["ticker"],
            "adjusted": product["adjusted"],
            "results": rows,
        }
    if product["kind"] == "splits":
        rows = (
            []
            if scale == 1
            else [
                {
                    "id": "INVENTED_SPLIT",
                    "ticker": product["ticker"],
                    "execution_date": "2025-12-01",
                    "adjustment_type": "forward_split",
                    "split_from": 1,
                    "split_to": 2,
                }
            ]
        )
    else:
        rows = [
            {
                "id": "INVENTED_DIV",
                "ticker": product["ticker"],
                "currency": "USD",
                "ex_dividend_date": "2025-09-19",
                "record_date": "2025-09-19",
                "pay_date": "2025-10-31",
                "declaration_date": "2025-09-18",
                "cash_amount": 1,
                "split_adjusted_cash_amount": scale,
                "distribution_type": "recurring",
            }
        ]
    return {"status": "OK", "results": rows}


class ETF2025Tests(unittest.TestCase):
    def setUp(self):
        self.destination, self.source = FakeGitHub(a.m.DESTINATION), "a" * 40
        self.calls = []

    def fetch(self, product, key):
        self.assertIsNotNone(self.destination.ref(a.ref(product, True)))
        self.assertEqual(key, "INVENTED_KEY")
        self.calls.append(product["id"])
        return 200, a.m.canonical(market(product))

    def execute(self, **changes):
        return a.execute(
            self.destination,
            "SPY",
            self.source,
            changes.pop("key_factory", lambda: "INVENTED_KEY"),
            fetch=changes.pop("fetch", self.fetch),
            load_documents=changes.pop("load_documents", lambda _: documents()),
            **changes,
        )

    def captured(self, scale=1):
        result = {}
        for product in a.products("SPY"):
            kind = product["kind"]
            if kind == "bars":
                kind = "split-adjusted" if product["adjusted"] else "raw"
            body = a.m.canonical(market(product, scale))
            result[kind] = {"body": body, "structure_accepted": a.structure(product, body, 200)}
        return result

    def test_fixed_version_scope_uses_new_schema_and_preserves_2024_plan(self):
        products = [v for t in a.p.TICKERS for v in a.products(t)]
        self.assertEqual(len(products), 48)
        self.assertEqual(len({v["id"] for v in products}), 48)
        self.assertTrue(all("2025" in v["url"] for v in products))
        self.assertTrue(
            all("/stocks/v1/dividends?" in v["url"] for v in products if v["kind"] == "dividends")
        )
        self.assertEqual(
            a.p.PLAN_HASH, "4d332174ffd90b2bb526b9f928c2128086a034de1c04bb208bb5b962e9cc66ac"
        )
        self.assertNotEqual(a.PLAN_HASH, a.p.PLAN_HASH)
        self.assertTrue(all(len(v) == 40 for v in a.DOCUMENT_PINS.values()))

    def test_capture_and_fresh_verify_reproduce_report_without_keys_writes_or_requests(self):
        result = self.execute()
        self.assertEqual(result["vendor_requests"], 4)
        self.assertEqual(result["official_requests"], 0)
        self.assertEqual(result["component_status"], "SAME_YEAR_RETROSPECTIVE_COMPONENTS_PASS")
        self.assertEqual(result["tax_fields"], "EVENT_YEAR_TAX_FIELDS_MATCH_NOT_FINALITY")
        self.destination.events.clear()
        fresh = self.execute(
            verify_only=True,
            key_factory=lambda: self.fail("key accessed"),
            fetch=lambda *_: self.fail("network accessed"),
        )
        self.assertEqual(fresh["vendor_requests"], 0)
        self.assertEqual(fresh["report_sha256"], result["report_sha256"])
        self.assertTrue(all(method == "GET" for method, _ in self.destination.events))
        self.assertTrue(all(fresh[k] is False for k in a.b.FALSE_FLAGS))
        self.assertEqual(fresh["full_real_scenarios_accepted"], 0)

    def test_completed_capture_reuses_without_any_provider_credential(self):
        self.execute()
        result = self.execute(
            key_factory=lambda: self.fail("key"), fetch=lambda *_: self.fail("fetch")
        )
        self.assertEqual(result["vendor_requests"], 0)

    def test_unknown_outcome_is_reserved_once_and_never_retried(self):
        with self.assertRaises(TimeoutError):
            self.execute(fetch=lambda *_: (_ for _ in ()).throw(TimeoutError()))
        before = dict(self.destination.refs)
        with self.assertRaisesRegex(a.p.Blocked, "UNKNOWN_REQUEST_OUTCOME_RETAINED"):
            self.execute()
        self.assertEqual(self.destination.refs, before)
        self.assertFalse(self.calls)

    def test_missing_input_or_invalid_credential_never_starts_request(self):
        with self.assertRaisesRegex(a.p.Blocked, "CAPTURE_MISSING"):
            self.execute(verify_only=True)
        with self.assertRaisesRegex(a.p.Blocked, "CREDENTIAL_UNAVAILABLE"):
            self.execute(key_factory=lambda: "")
        self.assertFalse(self.destination.refs or self.calls)

    def test_public_archive_or_missing_issuer_proof_stops_before_market_request(self):
        self.destination.private = False
        with self.assertRaises(STOPPED):
            self.execute()
        self.destination.private = True
        with self.assertRaisesRegex(a.p.Blocked, "ISSUER_HTTP_UNVERIFIED"):
            self.execute(load_documents=lambda _: a.p.need(False, "ISSUER_HTTP_UNVERIFIED"))
        self.assertFalse(self.destination.refs or self.calls)

    def test_denied_payload_is_retained_but_never_becomes_component_pass(self):
        result = self.execute(fetch=lambda *_: (403, b'{"status":"NOT_AUTHORIZED"}'))
        self.assertEqual(result["component_status"], "SAME_YEAR_COMPONENTS_UNVERIFIED")
        self.assertEqual(result["component_reason"], "CAPTURE_STRUCTURE_UNVERIFIED")
        self.assertEqual(result["vendor_requests"], 4)
        self.assertEqual(result["certification"], "UNVERIFIED")

    def test_key_in_response_stops_before_private_payload_archive(self):
        with self.assertRaises(STOPPED):
            self.execute(fetch=lambda *_: (200, b"INVENTED_KEY"))
        self.assertEqual(len(self.destination.refs), 1)

    def test_changed_capture_or_report_bytes_are_rejected(self):
        self.execute()
        for suffix, verify in (
            ("response.bin", lambda: a.load_product(self.destination, a.products("SPY")[0])),
            ("report.json", lambda: self.execute(verify_only=True)),
        ):
            original = deepcopy(self.destination.blobs)
            reference = (
                a.ref(a.products("SPY")[0])
                if suffix == "response.bin"
                else "research-data/real-etf-2025-probe-v1-spy-" + self.source
            )
            commit = self.destination.commits[self.destination.ref(reference)]
            tree = self.destination.trees[commit["tree"]["sha"]]
            row = next(v for v in tree["tree"] if v["path"].endswith(suffix))
            self.destination.blobs[row["sha"]] = b"CHANGED INVENTED BYTES"
            with self.subTest(suffix=suffix), self.assertRaises((*STOPPED, a.p.Blocked)):
                verify()
            self.destination.blobs = original

    def test_post_window_split_checks_original_and_adjusted_dividend_units_separately(self):
        result = a.components("SPY", self.captured(0.5), documents())
        self.assertEqual(result["status"], "SAME_YEAR_RETROSPECTIVE_COMPONENTS_PASS")
        self.assertEqual(Decimal(result["observed_split_scale"]), Decimal("0.5"))
        self.assertFalse(result["signal"]["signal_added_to_cash_ledger"])
        self.assertFalse(result["tax_fields"]["ordinary_cash_admission"])
        captured = self.captured(0.5)
        value = json.loads(captured["dividends"]["body"])
        value["results"][0]["cash_amount"] = 0.5
        captured["dividends"]["body"] = a.m.canonical(value)
        with self.assertRaisesRegex(a.p.Blocked, "DIVIDEND_SPLIT_BASIS_CONFLICT"):
            a.components("SPY", captured, documents())

    def test_inside_window_split_is_unsupported_and_not_silently_normalized(self):
        captured = self.captured(0.5)
        value = json.loads(captured["splits"]["body"])
        value["results"][0]["execution_date"] = "2025-10-01"
        captured["splits"]["body"] = a.m.canonical(value)
        with self.assertRaisesRegex(a.p.Blocked, "SPLIT_INSIDE_WINDOW_UNSUPPORTED"):
            a.components("SPY", captured, documents())

    def test_event_year_dates_and_component_fields_must_match(self):
        for changes in (
            {"H": "2024-09-19"},
            {"B": "999999999"},
            {"I": "2025-10-30"},
            {"N": ""},
            {"V": "0.1"},
            {"U": "2"},
        ):
            with self.subTest(changes=changes), self.assertRaises(a.p.Blocked):
                a.components("SPY", self.captured(), documents(changes=changes))
        result = a.components("SPY", self.captured(), documents())
        tax = result["tax_fields"]
        self.assertEqual(tax["prior_next_year_cells_preserved"], {"K": "", "L": ""})
        self.assertEqual(tax["source_status_flags_preserved"], {"D": "", "E": "", "F": ""})
        self.assertFalse(tax["document_finality_verified"])
        self.assertFalse(tax["investor_withholding_qualification_verified"])

    def test_duplicate_events_pagination_old_schema_boolean_or_missing_numbers_reject(self):
        product = next(v for v in a.products("SPY") if v["kind"] == "dividends")
        original = market(product)
        values = []
        duplicate = deepcopy(original)
        duplicate["results"].append(deepcopy(duplicate["results"][0]))
        values.append(duplicate)
        values.append({**original, "next_url": "https://example.invalid"})
        for field, value in (
            ("distribution_type", "CD"),
            ("cash_amount", True),
            ("cash_amount", "1"),
            ("split_adjusted_cash_amount", None),
            ("currency", "CAD"),
            ("ticker", "OTHER"),
            ("ex_dividend_date", "2024-09-19"),
        ):
            changed = deepcopy(original)
            changed["results"][0][field] = value
            values.append(changed)
        self.assertTrue(all(not a.structure(product, a.m.canonical(v), 200) for v in values))
        self.assertFalse(a.structure(product, b'{"status":"OK","status":"OK"}', 200))
        self.assertFalse(a.structure(product, b'{"results":[NaN]}', 200))
        decoded = a.decode_market(b'{"amount":0.1234567890123456789}')
        self.assertEqual(decoded["amount"], Decimal("0.1234567890123456789"))

    def test_price_timeline_pay_boundary_and_split_scale_conflicts_reject(self):
        for kind, mutation in (
            ("raw", lambda v: v["results"].pop()),
            ("raw", lambda v: v["results"].pop(4)),
            ("split-adjusted", lambda v: v["results"][0].update(c=50)),
            (
                "splits",
                lambda v: v["results"].append(
                    {
                        "id": "x",
                        "ticker": "SPY",
                        "execution_date": "2025-12-01",
                        "adjustment_type": "forward_split",
                        "split_from": 1,
                        "split_to": 2,
                    }
                ),
            ),
        ):
            captured = self.captured()
            value = json.loads(captured[kind]["body"])
            mutation(value)
            captured[kind]["body"] = a.m.canonical(value)
            with self.subTest(kind=kind), self.assertRaises(a.p.Blocked):
                a.components("SPY", captured, documents())

    def test_pinned_issuer_reads_never_follow_mutable_refs_or_legacy_unknown_reservation(self):
        view = a.PinnedIssuerArchive(self.destination)
        for name, sha in a.DOCUMENT_PINS.items():
            self.assertEqual(view.ref(name), sha)
        self.assertFalse(self.destination.events)
        with self.assertRaisesRegex(a.p.Blocked, "ISSUER_REF_OUTSIDE_PINNED_SCOPE"):
            view.ref(a.p.document_ref(a.p.DOCUMENTS[2], True))
        with self.assertRaisesRegex(a.p.Blocked, "ISSUER_WRITE_FORBIDDEN"):
            view.request("POST", "/git/refs", {})

    def test_original_document_receipts_are_verified_through_pinned_commit_graph(self):
        invented = documents()

        def fetch(doc):
            if doc == a.p.DOCUMENTS[2]:
                raise TimeoutError("INVENTED_UNKNOWN_OUTCOME")
            return 200, invented[doc["id"]]["body"]

        with self.assertRaises(TimeoutError):
            a.p.capture_documents(self.destination, self.source, fetch=fetch)
        pins = {name: self.destination.ref(name) for name in a.DOCUMENT_PINS}
        for name in pins:
            self.destination.refs[name] = "9" * 40
        self.destination.events.clear()
        with patch.object(a, "DOCUMENT_PINS", pins):
            retained = a.issuer_documents(self.destination)
        self.assertEqual(
            {k: v["body"] for k, v in retained.items()}, {k: v["body"] for k, v in invented.items()}
        )
        self.assertTrue(
            all(method == "GET" and "/ref/" not in path for method, path in self.destination.events)
        )

    def test_public_output_has_no_prices_amounts_rows_fingerprints_or_tax_qualification(self):
        value = self.execute()
        text = json.dumps(value)
        for fragment in (
            "000000001",
            "INVENTED",
            "source_commit",
            "source_raw_close",
            "body_sha256",
            "2025-09-19",
        ):
            self.assertNotIn(fragment, text)
        self.assertFalse(value["native_engine_verified"] or value["performance_admission"])

    def test_guard_rejects_local_wrong_actor_SHA_ref_and_debug(self):
        env = {
            "REAL_ETF_PROBE_REVIEWED_SHA": self.source,
            "REAL_ETF_PROBE_ENABLED": "true",
            "GITHUB_ACTIONS": "true",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "GITHUB_REPOSITORY": a.m.CODE,
            "GITHUB_ACTOR": "dczhhh",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": self.source,
        }
        for changes in (
            {"GITHUB_ACTIONS": "false"},
            {"GITHUB_ACTOR": "other"},
            {"GITHUB_SHA": "b" * 40},
            {"GITHUB_REF": "refs/heads/feature"},
            {"ACTIONS_STEP_DEBUG": "true"},
            {"ACTIONS_RUNNER_DEBUG": "true"},
            {"RUNNER_ENVIRONMENT": "self-hosted"},
            {"GITHUB_EVENT_NAME": "push"},
        ):
            with (
                self.subTest(changes=changes),
                patch.dict(os.environ, {**env, **changes}, clear=True),
                self.assertRaises(a.p.Blocked),
            ):
                a.guarded()
        with patch.dict(os.environ, env, clear=True), patch.object(a.m.GitHub, "visibility"):
            self.assertEqual(a.guarded(), self.source)

    def test_matrix_and_cli_errors_are_bounded_and_do_not_expose_exception_text(self):
        for scope, expected in (("SPY", ["SPY"]), ("all", list(a.p.TICKERS))):
            out = io.StringIO()
            with patch("sys.argv", ["tool", "matrix", scope]), redirect_stdout(out):
                self.assertEqual(a.main(), 0)
            self.assertEqual(json.loads(out.getvalue().split("=", 1)[1]), expected)
        with patch("sys.argv", ["tool", "matrix", "arbitrary"]), redirect_stdout(io.StringIO()):
            self.assertEqual(a.main(), 1)
        out = io.StringIO()
        with (
            patch("sys.argv", ["tool", "capture", "SPY"]),
            patch.object(a, "guarded", side_effect=RuntimeError("PRIVATE_VALUE")),
            redirect_stdout(out),
        ):
            self.assertEqual(a.main(), 1)
        self.assertNotIn("PRIVATE_VALUE", out.getvalue())

    def test_workflow_never_runs_provider_capture_on_push_and_verifier_has_no_provider_key(self):
        path = Path(__file__).parents[1] / ".github/workflows/real-etf-2025.yml"
        workflow = yaml.safe_load(path.read_text())
        trigger = workflow.get("on", workflow.get(True))
        self.assertEqual(set(trigger), {"workflow_dispatch"})
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(workflow["concurrency"]["group"], "real-action-capture-v1")
        verify = json.dumps(workflow["jobs"]["verify"])
        self.assertNotIn("MASSIVE_API_KEY", verify)
        self.assertIn("needs: [plan, capture]", path.read_text())
        capture = workflow["jobs"]["capture"]
        self.assertIn("ticker", json.dumps(capture["strategy"]["matrix"]))
        self.assertNotIn("upload-artifact", path.read_text())
        self.assertIn("${{ secrets.MASSIVE }}", path.read_text())
        self.assertNotIn("secrets.MASSIVE_API_KEY", path.read_text())


if __name__ == "__main__":
    unittest.main()
