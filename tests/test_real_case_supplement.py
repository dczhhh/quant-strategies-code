"""Invented archive transport tests; no real corporate-action or account acceptance."""

import os
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from tests.test_private_archive_migration import FakeGitHub
from tools import private_archive_migration as migration
from tools import real_case_supplement as s

STOPPED = (s.m.Stopped, migration.Stopped)


def body(product):
    if product["kind"] == "official":
        return (
            b"%PDF-" if product["format"] == "pdf" else b"<html>"
        ) + b"INVENTED NOT REAL EVIDENCE " * 60
    rows = (
        {"ticker": product["ticker"]}
        if product["kind"] == "identity"
        else [{"ticker": product["ticker"]}]
    )
    if product["kind"] == "bars":
        stamp = int(datetime.fromisoformat(product["start"]).replace(tzinfo=UTC).timestamp() * 1000)
        rows = [{"t": stamp, "o": 100, "h": 102, "l": 99, "c": 101, "v": 100}]
    return s.m.canonical(
        {
            "status": "OK",
            "ticker": product["ticker"],
            "adjusted": product.get("adjusted"),
            "results": rows,
        }
    )


class SupplementTests(unittest.TestCase):
    def setUp(self):
        self.destination, self.source = FakeGitHub(s.m.DESTINATION), "3" * 40
        self.calls = []

    def fetch(self, product, key):
        self.assertIsNotNone(self.destination.ref(s.ref(product, True)))
        self.assertEqual(key, "" if product["kind"] == "official" else "INVENTED_KEY")
        self.calls.append(product["id"])
        return 200, body(product)

    def execute(self, group="sqqq-reverse", **changes):
        return s.execute(
            group,
            self.destination,
            self.source,
            lambda: "INVENTED_KEY",
            fetch=changes.pop("fetch", self.fetch),
            **changes,
        )

    def test_fixed_scope_has_thirteen_products_and_window_covered_bars(self):
        products = [product for group in s.GROUPS for product in s.products(group)]
        self.assertEqual(len(products), 13)
        self.assertEqual(len({product["id"] for product in products}), 13)
        self.assertEqual({product["ticker"] for product in products}, {"SQQQ", "WOLF"})
        self.assertEqual(sum(product["kind"] != "official" for product in products), 9)
        self.assertTrue(
            all(
                product["start"] > "2021-10-14" for product in products if product["kind"] == "bars"
            )
        )
        self.assertEqual(
            s.POLICY["WOLF_expected_event_kind"], "unsupported_reorganization_not_ordinary_split"
        )
        self.assertEqual(s.PLAN_HASH != s.p.PLAN_HASH, True)

    def test_capture_never_certifies_pit_engine_or_reorganization(self):
        result = self.execute()
        self.assertEqual(result["vendor_requests"], 4)
        self.assertEqual(result["products"], 6)
        self.assertEqual(result["structure_accepted"], 6)
        self.assertEqual(result["certification"], "UNVERIFIED")
        self.assertTrue(all(result[flag] is False for flag in s.b.FALSE_FLAGS))
        self.assertTrue(all("real-case-supplement-v1-" in ref for ref in self.destination.refs))

    def test_wolf_uses_pre_and_post_identity_not_ticker_price_continuity(self):
        result = self.execute("wolf-reorganization")
        self.assertEqual(result["vendor_requests"], 5)
        self.assertEqual(result["products"], 7)
        self.assertEqual(
            sum(product["kind"] == "identity" for product in s.products("wolf-reorganization")), 2
        )

    def test_fresh_read_only_verifier_has_no_key_provider_or_writes(self):
        self.execute()
        self.destination.events.clear()
        result = s.execute(
            "sqqq-reverse",
            self.destination,
            "4" * 40,
            lambda: self.fail("key used"),
            verify_only=True,
            fetch=lambda *_: self.fail("provider used"),
        )
        self.assertEqual(result["vendor_requests"], 0)
        self.assertEqual(result["reused_products"], 6)
        self.assertTrue(all(method == "GET" for method, _ in self.destination.events))

    def test_completed_capture_reuses_bytes_without_key_or_write(self):
        self.execute()
        self.destination.events.clear()
        s.execute(
            "sqqq-reverse",
            self.destination,
            self.source,
            lambda: self.fail("key used"),
            fetch=lambda *_: self.fail("provider used"),
        )
        self.assertTrue(all(method == "GET" for method, _ in self.destination.events))

    def test_unknown_outcome_leaves_immutable_reservation(self):
        with self.assertRaises(TimeoutError):
            self.execute(fetch=lambda *_: (_ for _ in ()).throw(TimeoutError()))
        before = dict(self.destination.refs)
        with self.assertRaises(STOPPED):
            self.execute()
        self.assertEqual(self.destination.refs, before)
        self.assertFalse(self.calls)

    def test_missing_verify_input_never_creates_budget_or_fetches(self):
        with self.assertRaises(STOPPED):
            self.execute(verify_only=True)
        self.assertFalse(self.calls or self.destination.refs)

    def test_denied_official_and_market_bodies_are_not_accepted(self):
        result = self.execute(fetch=lambda *_: (403, b'{"status":"NOT_AUTHORIZED"}'))
        self.assertEqual(result["structure_accepted"], 0)
        self.assertEqual(len(self.destination.refs), 12)
        self.assertEqual(result["certification"], "UNVERIFIED")

    def test_public_destination_rejected_before_credential_or_network(self):
        self.destination.private = False
        with self.assertRaises(STOPPED):
            self.execute()
        self.assertFalse(self.calls)

    def test_unknown_case_or_arbitrary_url_cannot_expand_scope(self):
        with self.assertRaises(STOPPED):
            s.products("ALL_STOCKS")
        with self.assertRaises(STOPPED):
            s.get(dict(s.products("sqqq-reverse")[0], url="https://example.org"), "INVENTED_KEY")

    def test_pagination_and_empty_split_response_not_silently_accepted(self):
        product = next(
            product for product in s.products("wolf-reorganization") if product["kind"] == "splits"
        )
        for value in [
            {"status": "OK", "results": []},
            {"status": "OK", "results": [{"ticker": "WOLF"}], "next_url": "https://example.org"},
        ]:
            self.assertFalse(s.structure(product, s.m.canonical(value), 200))

    def test_modified_private_body_rejected_by_readback(self):
        self.execute()
        revision = self.destination.ref(s.ref(s.products("sqqq-reverse")[0]))
        tree = self.destination.trees[self.destination.commits[revision]["tree"]["sha"]]
        row = next(row for row in tree["tree"] if row["path"].endswith("response.bin"))
        self.destination.blobs[row["sha"]] = b"CHANGED PRIVATE BODY"
        with self.assertRaises(STOPPED):
            s.verify_product(self.destination, s.products("sqqq-reverse")[0])

    def test_provider_key_in_payload_stops_before_archive(self):
        with self.assertRaises(STOPPED):
            self.execute(fetch=lambda *_: (200, b"INVENTED_KEY"))
        self.assertEqual(len(self.destination.refs), 1)

    def test_guard_rejects_wrong_code_pr_actor_and_debug(self):
        env = {
            "REAL_CASE_SUPPLEMENT_REVIEWED_SHA": self.source,
            "REAL_CASE_SUPPLEMENT_ENABLED": "true",
            "GITHUB_ACTIONS": "true",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "GITHUB_REPOSITORY": s.m.CODE,
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
            {"REAL_CASE_SUPPLEMENT_ENABLED": "false"},
            {"RUNNER_ENVIRONMENT": "self-hosted"},
        ]:
            with patch.dict(os.environ, {**env, **changes}, clear=True), self.assertRaises(STOPPED):
                s.guarded()


if __name__ == "__main__":
    unittest.main()
