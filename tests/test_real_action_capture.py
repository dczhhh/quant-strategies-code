"""Invented transport responses validate the archive operator, not real economics."""

import unittest
from unittest.mock import patch

from tests.test_private_archive_migration import FakeGitHub
from tools import private_archive_migration as migration
from tools import real_action_capture as p

STOPPED = (p.m.Stopped, migration.Stopped)


def body(product):
    if product["kind"] == "official":
        return b"<html>" + b"INVENTED OFFICIAL DOCUMENT NOT REAL EVIDENCE " * 50 + b"</html>"
    rows = (
        {"ticker": product["ticker"]}
        if product["kind"] == "identity"
        else [{"ticker": product["ticker"]}]
    )
    if product["kind"] == "bars":
        rows = [{"t": 1717372800000, "o": 100, "h": 102, "l": 99, "c": 101, "v": 100}]
    return p.m.canonical(
        {
            "status": "OK",
            "ticker": product["ticker"],
            "adjusted": product.get("adjusted"),
            "results": rows,
        }
    )


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.destination, self.source = FakeGitHub(p.m.DESTINATION), "3" * 40
        self.calls = []

    def fetch(self, product, key):
        self.assertIsNotNone(self.destination.ref(p.refs(product, True)))
        self.assertEqual(key, "" if product["kind"] == "official" else "INVENTED_KEY")
        self.calls.append(product["id"])
        return 200, body(product)

    def capture(self, **changes):
        return p.execute(
            "nvda-2024",
            self.destination,
            self.source,
            lambda: "INVENTED_KEY",
            fetch=changes.pop("fetch", self.fetch),
            **changes,
        )

    def test_unique_scoped_plan_covers_both_splits_and_all_sector_etfs(self):
        products = [product for group in p.GROUPS for product in p.products(group)]
        self.assertEqual(len(products), 49)
        self.assertEqual(len({product["id"] for product in products}), len(products))
        self.assertEqual(
            {
                ticker
                for group, tickers in p.GROUPS.items()
                if group.startswith("etf-")
                for ticker in tickers
            },
            {"XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY", "SPY"},
        )
        for product in products:
            self.assertNotIn("apiKey=", product["url"])

    def test_archive_structure_is_never_real_or_tax_certification(self):
        result = self.capture()
        self.assertEqual(result["vendor_requests"], 5)
        self.assertEqual(result["products"], 6)
        self.assertEqual(result["certification"], "UNVERIFIED")
        self.assertEqual(result["structure_accepted"], 6)
        for flag in p.b.FALSE_FLAGS:
            self.assertIs(result[flag], False)

    def test_replay_without_key_provider_or_any_write(self):
        self.capture()
        self.destination.events.clear()
        result = p.execute(
            "nvda-2024",
            self.destination,
            "4" * 40,
            lambda: self.fail("key used"),
            fetch=lambda *_: self.fail("network used"),
            verify_only=True,
        )
        self.assertEqual(result["vendor_requests"], 0)
        self.assertEqual(result["reused_products"], 6)
        self.assertTrue(all(method == "GET" for method, _ in self.destination.events))

    def test_unknown_outcome_does_not_reset_old_reservation(self):
        with self.assertRaises(TimeoutError):
            self.capture(fetch=lambda *_: (_ for _ in ()).throw(TimeoutError()))
        with self.assertRaises(STOPPED):
            self.capture()
        self.assertEqual(len(self.destination.refs), 1)

    def test_public_destination_rejected_before_key_or_network(self):
        self.destination.private = False
        with self.assertRaises(STOPPED):
            self.capture()
        self.assertFalse(self.calls)

    def test_denied_original_response_archived_without_false_acceptance(self):
        result = self.capture(fetch=lambda *_: (403, b'{"status":"NOT_AUTHORIZED"}'))
        self.assertEqual(result["structure_accepted"], 0)
        self.assertEqual(result["certification"], "UNVERIFIED")
        self.assertEqual(len(self.destination.refs), 12)

    def test_missing_verify_inputs_never_start_acquisition(self):
        with self.assertRaises(STOPPED):
            self.capture(verify_only=True)
        self.assertFalse(self.calls)

    def test_invalid_case_or_url_cannot_expand_scope(self):
        with self.assertRaises(STOPPED):
            p.products("ALL_STOCKS")
        product = dict(p.products("nvda-2024")[0], url="https://example.org/leak")
        with self.assertRaises(STOPPED):
            p.get(product, "INVENTED_KEY")

    def test_pagination_is_not_silently_followed(self):
        product = p.products("nvda-2024")[0]
        self.assertFalse(
            p.structure(
                product,
                p.m.canonical({"status": "OK", "next_url": "https://example.org", "results": []}),
                200,
            )
        )

    def test_modified_receipt_or_response_is_rejected(self):
        self.capture()
        product = p.products("nvda-2024")[0]
        revision = self.destination.ref(p.refs(product))
        tree = self.destination.trees[self.destination.commits[revision]["tree"]["sha"]]
        row = next(row for row in tree["tree"] if row["path"].endswith("response.bin"))
        self.destination.blobs[row["sha"]] = b"CHANGED_PRIVATE_BYTES"
        with self.assertRaises(STOPPED):
            p.verify_product(self.destination, product, self.source)

    def test_guard_cannot_use_pr_branch_or_debug_output(self):
        import os

        env = {
            "REAL_ACTION_REVIEWED_SHA": self.source,
            "GITHUB_ACTIONS": "true",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "GITHUB_REPOSITORY": p.m.CODE,
            "GITHUB_ACTOR": "dczhhh",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": self.source,
            "REAL_ACTION_ENABLED": "true",
        }
        for changes in (
            {"GITHUB_REF": "refs/heads/feature"},
            {"ACTIONS_STEP_DEBUG": "true"},
            {"GITHUB_SHA": "4" * 40},
            {"GITHUB_ACTOR": "someone-else"},
        ):
            with patch.dict(os.environ, {**env, **changes}, clear=True), self.assertRaises(STOPPED):
                p.guarded()


if __name__ == "__main__":
    unittest.main()
