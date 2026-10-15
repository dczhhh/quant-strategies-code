"""Invented interrupted archives only; no real account or vendor data."""

import io
import json
import unittest
from contextlib import ExitStack, redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import yaml

from tests.test_private_archive_migration import FakeGitHub
from tests.test_real_etf_2025 import documents, market
from tools import private_archive_migration as migration
from tools import real_etf_2025_recovery as r


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.destination = FakeGitHub(r.m.DESTINATION)
        self.source, self.calls = "c" * 40, []
        originals = {}
        for ticker, stop_at in (("XLE", 1), ("XLP", 3)):
            attempt = []

            def old_fetch(product, _, attempt=attempt, stop_at=stop_at):
                attempt.append(product["id"])
                if len(attempt) == stop_at:
                    raise TimeoutError("PRIVATE EXCEPTION")
                return 200, r.m.canonical(market(product))

            with self.assertRaises(TimeoutError):
                r.a.capture(
                    self.destination, ticker, r.ORIGINAL_SOURCE, lambda: "KEY", fetch=old_fetch
                )
            for product in r.a.products(ticker):
                originals[product["id"]] = (
                    self.destination.ref(r.a.ref(product)),
                    self.destination.ref(r.a.ref(product, True)),
                )
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        policy = {**r.POLICY, "originals": {k: list(v) for k, v in originals.items()}}
        self.stack.enter_context(patch.object(r, "ORIGINALS", originals))
        self.stack.enter_context(patch.object(r, "POLICY", policy))
        self.stack.enter_context(
            patch.object(
                r,
                "PLAN_HASH",
                r.m.sha256(r.m.canonical({"products": r.PRODUCTS, "policy": policy})),
            )
        )
        self.old_refs = dict(self.destination.refs)

    def fetch(self, product, key):
        self.assertEqual(key, "KEY")
        self.assertIsNotNone(self.destination.ref(r.ref(product, True)))
        self.calls.append(product["id"])
        return 200, r.m.canonical(market(product))

    def execute(self, ticker="XLE", **changes):
        return r.execute(
            self.destination,
            ticker,
            self.source,
            changes.pop("key_factory", lambda: "KEY"),
            fetch=changes.pop("fetch", self.fetch),
            load_documents=lambda _: documents(ticker),
            **changes,
        )

    def test_six_missing_only_and_both_old_unknowns_remain_unchanged(self):
        self.assertEqual(len(r.PRODUCTS), 6)
        for ticker, expected in (("XLE", 4), ("XLP", 2)):
            result = self.execute(ticker)
            self.assertEqual(result["vendor_requests"], expected)
            self.assertEqual(result["component_status"], "SAME_YEAR_RETROSPECTIVE_COMPONENTS_PASS")
            self.assertFalse(result["previous_unknown_outcome_resolved"])
        self.assertEqual(len(self.calls), 6)
        self.assertTrue(all(self.destination.refs[k] == v for k, v in self.old_refs.items()))
        self.assertEqual(
            {k: v for k, v in self.destination.refs.items() if "2025-v1-" in k}, self.old_refs
        )

    def test_fresh_read_only_replay_has_no_keys_writes_or_requests(self):
        result = self.execute()
        self.destination.events.clear()
        fresh = self.execute(
            verify_only=True,
            key_factory=lambda: self.fail("key accessed"),
            fetch=lambda *_: self.fail("request"),
        )
        self.assertEqual(fresh["report_sha256"], result["report_sha256"])
        self.assertEqual(fresh["vendor_requests"], 0)
        self.assertTrue(all(method == "GET" for method, _ in self.destination.events))
        self.assertTrue(all(fresh[k] is False for k in r.b.FALSE_FLAGS))
        self.assertEqual(fresh["full_real_scenarios_accepted"], 0)

    def test_recovery_timeout_is_unknown_once_and_cannot_be_retried(self):
        with self.assertRaises(TimeoutError):
            self.execute(fetch=lambda *_: (_ for _ in ()).throw(TimeoutError("PRIVATE")))
        before = dict(self.destination.refs)
        with self.assertRaisesRegex(ValueError, "RECOVERY_UNKNOWN_OUTCOME_RETAINED"):
            self.execute()
        self.assertEqual(self.destination.refs, before)
        self.assertFalse(self.calls)

    def test_changed_old_state_or_invalid_credential_stops_before_new_reservation(self):
        with self.assertRaisesRegex(ValueError, "CREDENTIAL_UNAVAILABLE"):
            self.execute(key_factory=lambda: "")
        self.assertEqual(self.destination.refs, self.old_refs)
        self.destination.refs[r.a.ref(r.a.products("XLE")[0], True)] = "9" * 40
        before = dict(self.destination.refs)
        with self.assertRaisesRegex(ValueError, "ORIGINAL_INTERRUPTED_STATE_CHANGED"):
            self.execute()
        self.assertEqual(self.destination.refs, before)
        self.assertFalse(self.calls)

    def test_missing_original_or_recovery_input_is_not_created_by_verifier(self):
        before = dict(self.destination.refs)
        with self.assertRaisesRegex(ValueError, "RECOVERY_CAPTURE_MISSING"):
            self.execute(verify_only=True)
        with self.assertRaisesRegex(ValueError, "CAPTURE_MISSING"):
            self.execute("SPY")
        self.assertEqual(self.destination.refs, before)
        self.assertFalse(self.calls)

    def test_ten_completed_targets_are_reused_without_provider_credentials(self):
        r.a.capture(
            self.destination,
            "SPY",
            r.ORIGINAL_SOURCE,
            lambda: "KEY",
            fetch=lambda product, _: (200, r.m.canonical(market(product))),
        )
        self.calls.clear()
        result = self.execute("SPY", key_factory=lambda: self.fail("key accessed"))
        self.assertEqual(result["vendor_requests"], 0)
        self.assertEqual(result["component_status"], "SAME_YEAR_RETROSPECTIVE_COMPONENTS_PASS")
        self.assertFalse(self.calls)

    def test_recovery_receipt_parent_policy_and_source_tampering_are_rejected(self):
        self.execute()
        product = r.PRODUCTS[0]
        path = r.PREFIX + product["id"] + "/receipt.json"
        revision = self.destination.commits[self.destination.ref(r.ref(product))]
        tree = self.destination.trees[revision["tree"]["sha"]]["tree"]
        row = next(v for v in tree if v["path"] == path)
        self.destination.blobs[row["sha"]] = b"PRIVATE CHANGED BYTES"
        with self.assertRaises((r.m.Stopped, migration.Stopped)):
            r.read_product(self.destination, product)

    def test_http_denial_is_retained_without_becoming_component_pass(self):
        result = self.execute(fetch=lambda *_: (403, b'{"status":"NOT_AUTHORIZED"}'))
        self.assertEqual(result["component_status"], "SAME_YEAR_COMPONENTS_UNVERIFIED")
        self.assertEqual(result["vendor_requests"], 4)
        self.assertTrue(
            all(
                v["status"] == "UNVERIFIED"
                for v in self.private_report()["diagnostics"]["checks"].values()
            )
        )

    def private_report(self):
        name = "research-data/real-etf-2025-recovery-probe-v1-xle-" + self.source
        path = r.REPORT_PREFIX + "xle/" + self.source + "/"
        files, _ = r.b.read(self.destination, name, {path + "report.json", path + "summary.json"})
        return json.loads(files[path + "report.json"])

    def test_diagnostics_do_not_replace_source_fields_or_relax_combined_failure(self):
        def conflict(product, key):
            status, body = self.fetch(product, key)
            value = json.loads(body)
            if product["kind"] == "dividends":
                value["results"][0]["split_adjusted_cash_amount"] = 0.5
            return status, r.m.canonical(value)

        result = self.execute(fetch=conflict)
        self.assertEqual(result["component_status"], "SAME_YEAR_COMPONENTS_UNVERIFIED")
        self.assertEqual(result["component_reason"], "DIVIDEND_SPLIT_BASIS_CONFLICT")
        checks = self.private_report()["diagnostics"]["checks"]
        self.assertEqual(checks["issuer_history"]["status"], "ISSUER_HISTORY_FIELD_MATCH")
        self.assertEqual(checks["tax_fields"]["status"], "EVENT_YEAR_TAX_FIELDS_MATCH_NOT_FINALITY")
        self.assertEqual(Decimal(checks["dividend_basis"]["strict_residual"]), Decimal("-0.5"))
        self.assertFalse(checks["dividend_basis"]["rounding_policy_verified"])
        self.assertFalse(result["native_engine_verified"] or result["performance_admission"])
        for private in (
            "strict_residual",
            "original_amount",
            "vendor_adjusted_amount",
            "issuer_cells",
            "body_sha256",
        ):
            self.assertNotIn(private, json.dumps(result))

    def test_cli_error_is_safe_and_has_phase_product_and_network_category(self):
        out = io.StringIO()
        with (
            patch("sys.argv", ["tool", "capture", "XLE"]),
            patch.object(r.a, "guarded", side_effect=TimeoutError("PRIVATE_VALUE")),
            redirect_stdout(out),
        ):
            self.assertEqual(r.main(), 1)
        value = json.loads(out.getvalue())
        self.assertEqual(value["phase"], "context")
        self.assertEqual(value["code"], "NETWORK_TIMEOUT")
        self.assertNotIn("PRIVATE_VALUE", out.getvalue())

    def test_failure_phase_identifies_provider_call_without_printing_exception(self):
        phases = []
        with self.assertRaises(TimeoutError):
            self.execute(
                fetch=lambda *_: (_ for _ in ()).throw(TimeoutError("PRIVATE_URL_KEY")),
                progress=lambda phase, product: phases.append((phase, product)),
            )
        self.assertEqual(phases[-1], ("provider_request", "xle-raw"))
        self.assertTrue(all("PRIVATE" not in json.dumps(v) for v in phases))

    def test_key_in_response_never_reaches_capture_archive(self):
        before = set(self.destination.refs)
        with self.assertRaises((r.m.Stopped, migration.Stopped)):
            self.execute(fetch=lambda *_: (200, b"KEY"))
        added = set(self.destination.refs) - before
        self.assertEqual(added, {r.ref(r.PRODUCTS[0], True)})

    def test_workflow_keeps_dispatch_guard_and_verifies_independent_of_capture_failure(self):
        root = Path(__file__).resolve().parents[1]
        value = yaml.safe_load((root / ".github/workflows/real-etf-2025.yml").read_text())
        self.assertEqual(set(value.get("on", value.get(True))), {"workflow_dispatch"})
        job = value["jobs"]["verify"]
        self.assertIn("always()", job["if"])
        self.assertIn("needs.plan.result == 'success'", job["if"])
        self.assertNotIn("needs.capture.result == 'success'", job["if"])
        self.assertNotIn("MASSIVE", json.dumps(job))
        self.assertIn("real_etf_2025_recovery.py", json.dumps(job))
        self.assertEqual(value["jobs"]["capture"]["strategy"]["max-parallel"], 2)


if __name__ == "__main__":
    unittest.main()
