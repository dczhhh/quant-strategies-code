"""Invented interrupted captures; no market/account evidence or real-case credit."""

import os
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from tests.test_private_archive_migration import FakeGitHub
from tools import private_archive_migration as migration
from tools import real_action_recovery as r

p, m = r.p, r.m
STOPPED = (m.Stopped, migration.Stopped)


def body(product):
    rows = [{"ticker": product["ticker"]}]
    if product["kind"] == "bars":
        stamp = int(datetime.fromisoformat(product["start"]).replace(tzinfo=UTC).timestamp() * 1000)
        rows = [{"t": stamp, "o": 100, "h": 102, "l": 99, "c": 101, "v": 100}]
    return m.canonical(
        {
            "status": "OK",
            "ticker": product["ticker"],
            "adjusted": product.get("adjusted"),
            "results": rows,
        }
    )


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.destination, self.source = FakeGitHub(m.DESTINATION), "3" * 40
        count = 0

        def interrupted(product, _):
            nonlocal count
            count += 1
            if count == 2:
                raise TimeoutError("INVENTED timeout, not real evidence")
            return 200, body(product)

        with self.assertRaises(TimeoutError):
            p.execute(
                r.GROUP, self.destination, self.source, lambda: "INVENTED_KEY", fetch=interrupted
            )
        products = p.products(r.GROUP)
        raw_commit = self.destination.ref(p.refs(products[0]))
        raw_budget = self.destination.ref(p.refs(products[0], True))
        unknown_budget = self.destination.ref(p.refs(products[1], True))
        policy = dict(
            r.POLICY,
            original_raw_commit=raw_commit,
            original_raw_budget=raw_budget,
            original_unknown_budget=unknown_budget,
            original_source=self.source,
        )
        fixture_hash = m.sha256(m.canonical({"products": r.PRODUCTS, "policy": policy}))
        self.patcher = patch.multiple(
            r,
            ORIGINAL_RAW_COMMIT=raw_commit,
            ORIGINAL_RAW_BUDGET=raw_budget,
            ORIGINAL_UNKNOWN_BUDGET=unknown_budget,
            ORIGINAL_SOURCE=self.source,
            POLICY=policy,
            PLAN_HASH=fixture_hash,
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.original_refs = dict(self.destination.refs)
        self.calls = []

    def fetch(self, product, key):
        self.assertEqual(key, "INVENTED_KEY")
        self.assertIsNotNone(self.destination.ref(r.ref(product, True)))
        self.calls.append(product["id"])
        return 200, body(product)

    def execute(self, **changes):
        return r.execute(
            self.destination,
            self.source,
            lambda: "INVENTED_KEY",
            fetch=changes.pop("fetch", self.fetch),
            **changes,
        )

    def test_exact_eight_missing_products_preserve_all_old_refs(self):
        result = self.execute()
        self.assertEqual(result["vendor_requests"], 8)
        self.assertEqual(result["products"], 9)
        self.assertEqual(result["structure_accepted"], 9)
        self.assertEqual(result["reused_original_products"], 1)
        self.assertEqual(len(self.calls), 8)
        self.assertEqual(
            {name: self.destination.refs[name] for name in self.original_refs}, self.original_refs
        )
        self.assertTrue(result["original_refs_preserved"])
        self.assertFalse(result["previous_unknown_outcome_resolved"])
        self.assertEqual(result["certification"], "UNVERIFIED")
        self.assertTrue(all(result[flag] is False for flag in r.b.FALSE_FLAGS))

    def test_fresh_replay_is_read_only_without_key_or_vendor(self):
        self.execute()
        self.destination.events.clear()
        result = r.execute(
            self.destination,
            "4" * 40,
            lambda: self.fail("key used"),
            verify_only=True,
            fetch=lambda *_: self.fail("provider used"),
        )
        self.assertEqual(result["vendor_requests"], 0)
        self.assertEqual(result["reused_recovery_products"], 8)
        self.assertTrue(all(method == "GET" for method, _ in self.destination.events))

    def test_capture_reuses_completed_new_refs_without_key_or_write(self):
        self.execute()
        self.destination.events.clear()
        r.execute(
            self.destination,
            self.source,
            lambda: self.fail("key used"),
            fetch=lambda *_: self.fail("provider used"),
        )
        self.assertTrue(all(method == "GET" for method, _ in self.destination.events))

    def test_second_unknown_attempt_cannot_be_reset_or_retried(self):
        with self.assertRaises(TimeoutError):
            self.execute(fetch=lambda *_: (_ for _ in ()).throw(TimeoutError()))
        before = dict(self.destination.refs)
        with self.assertRaises(STOPPED):
            self.execute()
        self.assertEqual(self.destination.refs, before)
        self.assertFalse(self.calls)

    def test_changed_old_reservation_rejected_before_network(self):
        self.destination.refs[p.refs(r.PRODUCTS[0], True)] = "7" * 40
        with self.assertRaises(STOPPED):
            self.execute()
        self.assertFalse(self.calls)

    def test_changed_old_capture_rejected_before_network(self):
        self.destination.refs[p.refs(p.products(r.GROUP)[0])] = "7" * 40
        with self.assertRaises(STOPPED):
            self.execute()
        self.assertFalse(self.calls)

    def test_unexpected_old_ref_rejected_without_overwriting_it(self):
        name = p.refs(r.PRODUCTS[-1], True)
        self.destination.refs[name] = "7" * 40
        with self.assertRaises(STOPPED):
            self.execute()
        self.assertEqual(self.destination.refs[name], "7" * 40)
        self.assertFalse(self.calls)

    def test_verify_never_acquires_missing_inputs(self):
        before = dict(self.destination.refs)
        with self.assertRaises(STOPPED):
            self.execute(verify_only=True)
        self.assertEqual(self.destination.refs, before)
        self.assertFalse(self.calls)

    def test_public_archive_rejected_before_key_or_network(self):
        self.destination.private = False
        with self.assertRaises(STOPPED):
            self.execute()
        self.assertFalse(self.calls)

    def test_rejected_http_body_is_archived_but_not_certified(self):
        result = self.execute(fetch=lambda *_: (403, b'{"status":"NOT_AUTHORIZED"}'))
        self.assertEqual(result["structure_accepted"], 1)
        self.assertEqual(result["certification"], "UNVERIFIED")
        self.assertEqual(len(self.destination.refs), 19)

    def test_key_in_body_never_enters_archive(self):
        with self.assertRaises(STOPPED):
            self.execute(fetch=lambda *_: (200, b"INVENTED_KEY"))
        self.assertIsNone(self.destination.ref(r.ref(r.PRODUCTS[0])))
        self.assertIsNotNone(self.destination.ref(r.ref(r.PRODUCTS[0], True)))

    def test_changed_new_response_fails_independent_readback(self):
        self.execute()
        revision = self.destination.ref(r.ref(r.PRODUCTS[0]))
        tree = self.destination.trees[self.destination.commits[revision]["tree"]["sha"]]
        row = next(row for row in tree["tree"] if row["path"].endswith("response.bin"))
        self.destination.blobs[row["sha"]] = b"CHANGED PRIVATE BYTES"
        with self.assertRaises(STOPPED):
            r.verify_product(self.destination, r.PRODUCTS[0])

    def test_scope_cannot_expand_to_original_raw_or_arbitrary_urls(self):
        for product in [p.products(r.GROUP)[0], dict(r.PRODUCTS[0], url="https://example.org")]:
            with self.assertRaises(STOPPED):
                r.get(product, "INVENTED_KEY")

    def test_reviewed_guard_rejects_pr_branch_wrong_pin_and_debug(self):
        env = {
            "REAL_ACTION_RECOVERY_REVIEWED_SHA": self.source,
            "REAL_ACTION_RECOVERY_ENABLED": "true",
            "GITHUB_ACTIONS": "true",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "GITHUB_REPOSITORY": m.CODE,
            "GITHUB_ACTOR": "dczhhh",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": self.source,
        }
        for changes in [
            {"GITHUB_REF": "refs/heads/feature"},
            {"GITHUB_SHA": "4" * 40},
            {"GITHUB_ACTOR": "someone-else"},
            {"ACTIONS_STEP_DEBUG": "true"},
            {"ACTIONS_RUNNER_DEBUG": "true"},
            {"REAL_ACTION_RECOVERY_ENABLED": "false"},
            {"RUNNER_ENVIRONMENT": "self-hosted"},
        ]:
            with patch.dict(os.environ, {**env, **changes}, clear=True), self.assertRaises(STOPPED):
                r.guarded()


if __name__ == "__main__":
    unittest.main()
