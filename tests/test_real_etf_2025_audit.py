"""Invented retained inputs; raw-share audit preserves every strict vendor-view rejection."""

import io
import json
import os
import unittest
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import yaml

from tests.test_private_archive_migration import FakeGitHub
from tests.test_real_etf_2025 import documents, market, workbook
from tools import private_archive_migration as migration
from tools import real_etf_2025_audit as u


def all_documents():
    records = [documents(t) for t in u.p.TICKERS]
    history = u.p.workbook(records[0][u.p.DOCUMENTS[0]["id"]]["body"])["dividend"][:1]
    primary = u.p.workbook(records[0][u.p.DOCUMENTS[1]["id"]]["body"])["2025 ICI Primary"][:13]
    for record in records:
        history += u.p.workbook(record[u.p.DOCUMENTS[0]["id"]]["body"])["dividend"][1:]
        primary += u.p.workbook(record[u.p.DOCUMENTS[1]["id"]]["body"])["2025 ICI Primary"][13:]
    result = deepcopy(records[0])
    result[u.p.DOCUMENTS[0]["id"]]["body"] = workbook("dividend", history)
    result[u.p.DOCUMENTS[1]["id"]]["body"] = workbook("2025 ICI Primary", primary)
    return result


class RawAuditTests(unittest.TestCase):
    def setUp(self):
        self.destination, self.source = FakeGitHub(u.m.DESTINATION), "d" * 40
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.docs = all_documents()
        self.stack.enter_context(patch.object(u.a, "issuer_documents", return_value=self.docs))

    def build_retained(self, *, derived_conflict=False):
        originals = {}
        for ticker in u.p.TICKERS:
            interrupted = {"XLE": 1, "XLP": 3}.get(ticker)
            count = []

            def fetch(product, _, count=count, interrupted=interrupted):
                count.append(product["id"])
                if interrupted == len(count):
                    raise TimeoutError("invented old unknown")
                return 200, u.m.canonical(market(product))

            if interrupted:
                with self.assertRaises(TimeoutError):
                    u.a.capture(
                        self.destination, ticker, u.r.ORIGINAL_SOURCE, lambda: "KEY", fetch=fetch
                    )
                for product in u.a.products(ticker):
                    originals[product["id"]] = (
                        self.destination.ref(u.a.ref(product)),
                        self.destination.ref(u.a.ref(product, True)),
                    )
            else:
                u.a.capture(
                    self.destination, ticker, u.r.ORIGINAL_SOURCE, lambda: "KEY", fetch=fetch
                )
        policy = {**u.r.POLICY, "originals": {k: list(v) for k, v in originals.items()}}
        self.stack.enter_context(
            patch.dict(u.r.capture.__globals__, {"ORIGINALS": originals, "POLICY": policy})
        )

        def recovery_fetch(product, _):
            value = market(product)
            if derived_conflict and product["id"] == "xle-dividends":
                value["results"][0]["split_adjusted_cash_amount"] = 0.5
            return 200, u.m.canonical(value)

        for ticker in ("XLE", "XLP"):
            u.r.capture(
                self.destination,
                ticker,
                self.source,
                lambda: "KEY",
                fetch=recovery_fetch,
            )
        self.original_refs = dict(self.destination.refs)
        self.destination.events.clear()

    def captured(self):
        return {
            u.r.kind(v): {"body": u.m.canonical(market(v)), "structure_accepted": True}
            for v in u.a.products("SPY")
        }

    def test_raw_component_excludes_conflicting_derived_view_without_changing_strict_case(self):
        captured = self.captured()
        value = json.loads(captured["dividends"]["body"])
        value["results"][0]["split_adjusted_cash_amount"] = 0.5
        captured["dividends"]["body"] = u.m.canonical(value)
        before = deepcopy(captured)
        with self.assertRaisesRegex(ValueError, "DIVIDEND_SPLIT_BASIS_CONFLICT"):
            u.a.components("SPY", captured, self.docs)
        result = u.raw_component("SPY", captured, self.docs)
        self.assertEqual(result["status"], "RAW_BASIS_RETROSPECTIVE_SIGNAL_COMPONENT_PASS")
        self.assertFalse(result["vendor_split_adjusted_cash_used"])
        self.assertFalse(result["source_cash_amount_changed"])
        self.assertEqual(captured, before)
        self.assertFalse(result["performance_admission"] or result["may_build_engine"])

    def test_bad_original_cash_or_price_or_window_cannot_be_hidden_by_quarantine(self):
        for kind, change in (
            ("dividends", lambda v: v["results"][0].update(cash_amount=0.5)),
            ("split-adjusted", lambda v: v["results"][0].update(c=50)),
            ("raw", lambda v: v["results"].pop()),
        ):
            captured = self.captured()
            value = json.loads(captured[kind]["body"])
            change(value)
            captured[kind]["body"] = u.m.canonical(value)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                u.raw_component("SPY", captured, self.docs)

    def test_entire_batch_and_fresh_replay_fit_call_budget_and_have_no_market_requests(self):
        self.build_retained()
        result = u.execute(self.destination, self.source)
        writer_calls = len(self.destination.events)
        self.assertLess(writer_calls, u.m.CALL_LIMIT - 40)
        self.assertEqual(result["vendor_requests"], 0)
        self.assertEqual(len(result["tickers"]), 12)
        self.assertEqual(sum(v["raw_signal_checkpoints"] for v in result["tickers"]), 60)
        self.assertTrue(
            all(
                v["raw_component_status"] == "RAW_BASIS_RETROSPECTIVE_SIGNAL_COMPONENT_PASS"
                for v in result["tickers"]
            )
        )
        self.destination.events.clear()
        replay = u.execute(self.destination, self.source, verify_only=True)
        self.assertEqual(replay["report_bundle_sha256"], result["report_bundle_sha256"])
        self.assertLess(len(self.destination.events), u.m.CALL_LIMIT - 40)
        self.assertTrue(all(method == "GET" for method, _ in self.destination.events))
        self.assertTrue(all(self.destination.refs[k] == v for k, v in self.original_refs.items()))
        print(
            f"Invented API calls: writing batch={writer_calls}, fresh batch={len(self.destination.events)}"
        )

    def test_missing_input_never_fetches_or_creates_it(self):
        before = dict(self.destination.refs)
        with self.assertRaisesRegex(ValueError, "CAPTURE_MISSING"):
            u.execute(self.destination, self.source)
        self.assertEqual(self.destination.refs, before)

    def test_batch_keeps_strict_failure_alongside_raw_component_pass(self):
        self.build_retained(derived_conflict=True)
        result = u.execute(self.destination, self.source)
        selected = next(v for v in result["tickers"] if v["ticker"] == "XLE")
        self.assertEqual(
            selected["raw_component_status"], "RAW_BASIS_RETROSPECTIVE_SIGNAL_COMPONENT_PASS"
        )
        self.assertEqual(selected["strict_component_status"], "UNVERIFIED")
        self.assertEqual(selected["strict_component_reason"], "DIVIDEND_SPLIT_BASIS_CONFLICT")
        self.assertEqual(selected["vendor_adjusted_view_status"], "UNVERIFIED")
        self.assertEqual(result["full_real_scenarios_accepted"], 0)
        self.assertFalse(selected["performance_admission"])

    def test_verifier_cannot_create_missing_report_bundle(self):
        self.build_retained()
        with self.assertRaisesRegex(ValueError, "RAW_AUDIT_REPORT_MISSING"):
            u.execute(self.destination, self.source, verify_only=True)
        self.assertEqual(self.destination.refs, self.original_refs)
        self.assertTrue(all(method == "GET" for method, _ in self.destination.events))

    def test_immutable_cache_never_hides_reference_or_visibility_changes(self):
        self.build_retained()
        cache = u.CachedArchive(self.destination)
        name = next(iter(self.destination.refs))
        sha = cache.ref(name)
        path = "/git/commits/" + sha
        first = cache.request("GET", path)
        first["parents"] = []
        self.assertTrue(cache.request("GET", path)["parents"])
        self.assertEqual(sum(s == path for _, s in self.destination.events), 1)
        self.destination.refs[name] = "9" * 40
        self.assertEqual(cache.ref(name), "9" * 40)
        cache.visibility(True)
        self.destination.private = False
        with self.assertRaises(migration.Stopped) as error:
            cache.visibility(True)
        self.assertEqual(type(error.exception).__name__, "Stopped")

    def test_guard_passes_only_code_read_token_to_existing_exact_main_guard(self):
        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "INVENTED_CODE_READ"}, clear=True),
            patch.object(u.a, "guarded", return_value=self.source) as guard,
        ):
            self.assertEqual(u.guarded(), self.source)
            guard.assert_called_once_with(code_token="INVENTED_CODE_READ")
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(ValueError, "CODE_READ_TOKEN_UNAVAILABLE"),
        ):
            u.guarded()

    def test_http_rate_limit_is_classified_only_with_explicit_headers(self):
        for remaining, expected in (("0", "GITHUB_PRIMARY_RATE_LIMIT"), ("123", "GITHUB_HTTP_403")):
            headers = Message()
            headers["X-RateLimit-Remaining"] = remaining
            headers["X-RateLimit-Reset"] = "1792054800"
            error = HTTPError(
                "https://example.invalid/PRIVATE", 403, "PRIVATE", headers, io.BytesIO(b"PRIVATE")
            )
            opener = Mock()
            opener.open.side_effect = error
            with (
                patch.dict(
                    u.m.GitHub.request.__globals__,
                    {"build_opener": lambda *_, opener=opener: opener},
                ),
                self.assertRaises(u.m.Stopped) as caught,
            ):
                u.m.GitHub(u.m.CODE).visibility(False)
            self.assertEqual(u.r.code(caught.exception), expected)
            self.assertEqual(caught.exception.retry_at, 1792054800 if remaining == "0" else None)

    def test_public_output_does_not_expose_amounts_receipts_or_rows(self):
        self.build_retained()
        value = json.dumps(u.execute(self.destination, self.source))
        for name in (
            "original_amount",
            "source_raw_close",
            "receipt",
            "issuer_cells",
            "000000001",
            "KEY",
            "body_sha256",
        ):
            self.assertNotIn(name, value)

    def test_new_workflow_has_two_batches_without_any_market_secret(self):
        path = Path(__file__).parents[1] / ".github/workflows/real-etf-2025-audit.yml"
        value = yaml.safe_load(path.read_text())
        self.assertEqual(set(value.get("on", value.get(True))), {"workflow_dispatch"})
        self.assertEqual(set(value["jobs"]), {"audit", "verify"})
        self.assertNotIn("MASSIVE", path.read_text())
        self.assertNotIn("matrix", path.read_text())
        self.assertNotIn("upload-artifact", path.read_text())
        self.assertEqual(value["permissions"], {"contents": "read"})
        self.assertIn(
            "github.sha == vars.REAL_ETF_PROBE_REVIEWED_SHA", value["jobs"]["audit"]["if"]
        )

    def test_cli_does_not_expose_private_http_error_text(self):
        out = io.StringIO()
        with (
            patch("sys.argv", ["tool", "verify"]),
            patch.object(u, "guarded", side_effect=RuntimeError("PRIVATE_URL_KEY")),
            redirect_stdout(out),
        ):
            self.assertEqual(u.main(), 1)
        self.assertNotIn("PRIVATE_URL_KEY", out.getvalue())
        self.assertEqual(json.loads(out.getvalue())["code"], "CHECK_FAILED")


if __name__ == "__main__":
    unittest.main()
