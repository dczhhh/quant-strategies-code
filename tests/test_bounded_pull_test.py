"""Invented responses only; exercise budgets, immutable readback and credential boundaries."""

import copy
import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from tests.test_private_archive_migration import FakeGitHub
from tools import bounded_pull_test as p
from tools import private_archive_migration as migration

STOPPED = (p.m.Stopped, migration.Stopped)


def invented_body(product):
    if product["kind"] == "reference":
        return p.m.canonical(
            {
                "status": "OK",
                "results": {
                    "ticker": "AAPL",
                    "cik": "0000320193",
                    "type": "CS",
                    "market": "stocks",
                    "locale": "us",
                    "currency_name": "usd",
                    "primary_exchange": "XNAS",
                    "active": True,
                    "market_cap": 20_000_000_000,
                },
            }
        )
    return p.m.canonical(
        {
            "status": "OK",
            "ticker": product["ticker"],
            "adjusted": product["kind"] == "comparison",
            "resultsCount": 390,
            "results": [
                {"t": p.START + i * 60_000, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100}
                for i in range(390)
            ],
        }
    )


class PullTests(unittest.TestCase):
    def setUp(self):
        self.destination = FakeGitHub(p.m.DESTINATION)
        self.source = "3" * 40
        self.fetches = []

    def fetch(self, product, key):
        self.assertEqual(key, "INVENTED_PROVIDER_CREDENTIAL")
        self.assertIsNotNone(self.destination.ref(p.ref(product, True)))
        self.fetches.append(product["id"])
        return 200, invented_body(product)

    def capture(self, fetch=None):
        return p.execute(
            self.destination,
            self.source,
            lambda: "INVENTED_PROVIDER_CREDENTIAL",
            fetch or self.fetch,
        )

    def tree(self, name):
        commit = self.destination.commits[self.destination.refs[name]]
        return self.destination.trees[commit["tree"]["sha"]]

    def test_five_requests_reserved_before_acquisition_with_exact_readback(self):
        result = self.capture()
        self.assertEqual(result["vendor_requests"], 5)
        self.assertEqual(result["bar_records"], 1560)
        self.assertEqual(len(self.fetches), 5)
        self.assertEqual(len(self.destination.refs), 10)
        for product in p.PRODUCTS:
            self.assertTrue(p.verify_product(self.destination, product)["accepted"])
            self.assertEqual(len(self.tree(p.ref(product))["tree"]), 3)
        for flag in p.FALSE_FLAGS:
            self.assertIs(result[flag], False)
        for i, event in enumerate(self.destination.events):
            if event[0] == "POST":
                self.assertEqual(self.destination.events[i - 1], ("GET", ""))

    def test_fresh_runner_reuses_without_key_access_provider_or_writes(self):
        self.capture()
        self.destination.events.clear()

        def forbidden():
            raise AssertionError("Credential accessed during reuse")

        result = p.execute(
            self.destination, "4" * 40, forbidden, fetch=lambda *_: self.fail("Provider called")
        )
        self.assertEqual(result["vendor_requests"], 0)
        self.assertEqual(result["reused_products"], 5)
        self.assertTrue(all(method == "GET" for method, _ in self.destination.events))
        self.assertEqual(
            p.execute(self.destination, self.source, forbidden, verify_only=True)[
                "vendor_requests"
            ],
            0,
        )

    def test_verify_missing_products_never_reads_key_or_writes(self):
        with self.assertRaises(STOPPED):
            p.execute(
                self.destination, self.source, lambda: self.fail("Key accessed"), verify_only=True
            )
        self.assertTrue(all(method == "GET" for method, _ in self.destination.events))

    def test_unknown_outcome_consumes_single_budget_and_prevents_retry(self):
        def crash(product, key):
            self.fetches.append(product["id"])
            raise TimeoutError("INVENTED_PRIVATE_TEXT")

        with self.assertRaises(TimeoutError):
            self.capture(crash)
        with self.assertRaises(STOPPED):
            self.capture()
        self.assertEqual(len(self.fetches), 1)
        self.assertEqual(len(self.destination.refs), 1)

    def test_partial_success_is_preserved_and_reused_before_stopping(self):
        def second_fails(product, key):
            if product == p.PRODUCTS[1]:
                raise TimeoutError()
            return self.fetch(product, key)

        with self.assertRaises(TimeoutError):
            self.capture(second_fails)
        self.assertTrue(p.verify_product(self.destination, p.PRODUCTS[0])["accepted"])
        with self.assertRaises(STOPPED):
            self.capture()
        self.assertEqual(self.fetches, ["aapl-reference"])

    def test_public_destination_stops_before_key_or_acquisition(self):
        self.destination.private = False
        with self.assertRaises(STOPPED):
            self.capture()
        self.assertEqual(self.fetches, [])

    def test_visibility_change_at_reservation_write_prevents_provider_call(self):
        def change(method, suffix, payload):
            if method == "GET" and suffix == "" and len(self.destination.events) > 1:
                self.destination.private = False

        self.destination.before = change
        with self.assertRaises(STOPPED):
            self.capture()
        self.assertEqual(self.fetches, [])

    def test_rejected_http_body_is_archived_then_stops_and_never_retried(self):
        with self.assertRaises(STOPPED):
            self.capture(lambda *_: (403, b'{"status":"NOT_AUTHORIZED","invented":true}'))
        result = p.verify_product(self.destination, p.PRODUCTS[0])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["code"], "HTTP_UNEXPECTED")
        with self.assertRaises(STOPPED):
            self.capture(lambda *_: self.fail("Rejected response was retried"))

    def test_invalid_success_body_preserved_without_admission(self):
        with self.assertRaises(STOPPED):
            self.capture(lambda *_: (200, b'{"invented":"invalid"}'))
        self.assertEqual(
            p.verify_product(self.destination, p.PRODUCTS[0])["code"], "PAYLOAD_INVALID"
        )

    def test_credentials_in_response_block_private_archival(self):
        for body in (
            b'{"apiKey":"invented"}',
            b"INVENTED_PROVIDER_CREDENTIAL",
            b"INVENTED_ARCHIVE_CREDENTIAL",
        ):
            self.destination = FakeGitHub(p.m.DESTINATION)
            with self.subTest(body=body), self.assertRaises(STOPPED):
                self.capture(lambda *_, captured=body: (200, captured))
            self.assertEqual(len(self.destination.refs), 1)

    def test_corrupt_readback_never_calls_provider(self):
        self.capture()
        row = next(
            row
            for row in self.tree(p.ref(p.PRODUCTS[0]))["tree"]
            if row["path"].endswith("response.json")
        )
        self.destination.blobs[row["sha"]] = b"INVENTED_CORRUPTION"
        with self.assertRaises(STOPPED):
            self.capture(lambda *_: self.fail("Provider reacquired corrupt archive"))

    def test_ancestry_extra_files_modes_and_checks_tampering_are_rejected(self):
        self.capture()
        baseline = copy.deepcopy((self.destination.trees, self.destination.commits))
        name = p.ref(p.PRODUCTS[0])
        for mutation in ("ancestry", "extra", "mode", "checks"):
            self.destination.trees, self.destination.commits = copy.deepcopy(baseline)
            tree = self.tree(name)
            if mutation == "ancestry":
                self.destination.commits[self.destination.refs[name]]["parents"][0]["sha"] = (
                    "9" * 40
                )
            elif mutation == "extra":
                tree["tree"].append({**tree["tree"][0], "path": "invented-extra.json"})
            elif mutation == "mode":
                tree["tree"][0]["mode"] = "100755"
            else:
                row = next(row for row in tree["tree"] if row["path"].endswith("checks.json"))
                body = p.m.canonical({"invented": True})
                row.update(sha=p.m.git_blob(body), size=len(body))
                self.destination.blobs[row["sha"]] = body
            with self.subTest(mutation=mutation), self.assertRaises(STOPPED):
                p.verify_product(self.destination, p.PRODUCTS[0])

    def test_exact_bar_count_timestamps_ohlc_volume_and_adjustment_required(self):
        product = p.PRODUCTS[1]
        baseline = p.decode(invented_body(product))
        for mutation in (
            "count",
            "missing",
            "timestamp",
            "order",
            "ohlc",
            "volume",
            "boolean",
            "adjusted",
            "pagination",
        ):
            value = copy.deepcopy(baseline)
            if mutation == "count":
                value["resultsCount"] = True
            elif mutation == "missing":
                value["results"].pop()
            elif mutation == "timestamp":
                value["results"][0]["t"] += 1
            elif mutation == "order":
                value["results"].reverse()
            elif mutation == "ohlc":
                value["results"][0]["h"] = 1
            elif mutation == "volume":
                value["results"][0]["v"] = -1
            elif mutation == "boolean":
                value["results"][0]["c"] = True
            elif mutation == "adjusted":
                value["adjusted"] = True
            else:
                value["next_url"] = "https://example.invalid/invented"
            with self.subTest(mutation=mutation):
                self.assertFalse(p.checks(product, p.m.canonical(value), 200)["accepted"])

    def test_duplicate_keys_nonfinite_and_wrong_reference_rejected(self):
        product = p.PRODUCTS[0]
        for body in (
            b'{"status":"OK","status":"OK"}',
            b'{"status":"OK","results":NaN}',
            b'{"status":"OK","results":Infinity}',
        ):
            self.assertFalse(p.checks(product, body, 200)["accepted"])
        baseline = p.decode(invented_body(product))
        for field, invalid in (
            ("market_cap", 1),
            ("market_cap", True),
            ("ticker", "GME"),
            ("active", False),
            ("type", "ETF"),
            ("cik", "1"),
        ):
            value = copy.deepcopy(baseline)
            value["results"][field] = invalid
            self.assertFalse(p.checks(product, p.m.canonical(value), 200)["accepted"])

    def test_fixed_origin_scope_and_512kib_cap(self):
        self.assertEqual(len({p.url(product) for product in p.PRODUCTS}), 5)
        self.assertTrue(
            all(p.url(product).startswith("https://api.massive.com/") for product in p.PRODUCTS)
        )
        with self.assertRaises(STOPPED):
            p.url({"id": "gme-raw", "ticker": "GME", "kind": "raw"})
        with self.assertRaises(STOPPED):
            p.secret_free(b"x" * (p.m.FILE_LIMIT + 1), ())

    def test_transport_header_timeout_limit_and_no_retry(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, maximum):
                self.maximum = maximum
                return invented_body(p.PRODUCTS[0])

        response = Response()
        with patch.object(p, "build_opener") as opener:
            opener.return_value.open.return_value = response
            p.provider_get(p.PRODUCTS[0], "INVENTED_KEY")
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer INVENTED_KEY")
            self.assertNotIn("INVENTED_KEY", request.full_url)
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 20)
            self.assertEqual(response.maximum, p.m.FILE_LIMIT + 1)
            self.assertEqual(opener.return_value.open.call_count, 1)
            self.assertIsInstance(opener.call_args.args[0], p.m.NoRedirect)

    def test_http_failure_is_not_retried_or_printed(self):
        error = HTTPError(
            p.url(p.PRODUCTS[0]), 429, "INVENTED_PRIVATE_TEXT", {}, io.BytesIO(b'{"invented":true}')
        )
        with patch.object(p, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            status, body = p.provider_get(p.PRODUCTS[0], "INVENTED_KEY")
            self.assertEqual((status, body), (429, b'{"invented":true}'))
            self.assertEqual(opener.return_value.open.call_count, 1)

    def test_context_requires_manual_owner_reviewed_public_main_hosted_no_debug(self):
        environment = {
            "PRIVATE_PULL_REVIEWED_SHA": self.source,
            "GITHUB_ACTIONS": "true",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "GITHUB_REPOSITORY": p.m.CODE,
            "GITHUB_ACTOR": "dczhhh",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": self.source,
            "PRIVATE_PULL_ENABLED": "true",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(p.m.GitHub, "visibility"),
        ):
            self.assertEqual(p.guarded(), self.source)
            for key in environment:
                with (
                    patch.dict(os.environ, {key: "INVENTED_WRONG"}),
                    self.assertRaises(STOPPED),
                ):
                    p.guarded()
            for key in ("ACTIONS_STEP_DEBUG", "ACTIONS_RUNNER_DEBUG"):
                with patch.dict(os.environ, {key: "true"}), self.assertRaises(STOPPED):
                    p.guarded()

    def test_main_error_output_does_not_leak_private_exception(self):
        output = io.StringIO()
        with (
            patch.object(p, "guarded", side_effect=RuntimeError("INVENTED_SECRET")),
            patch.object(p.sys, "argv", ["bounded_pull_test.py", "capture"]),
            redirect_stdout(output),
        ):
            self.assertEqual(p.main(), 1)
        self.assertNotIn("INVENTED_SECRET", output.getvalue())
        self.assertIn('"code":"CHECK_FAILED"', output.getvalue())

    def test_workflow_is_manual_guarded_without_payload_artifacts_caches(self):
        workflow = Path(".github/workflows/bounded-pull-test.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("github.sha == vars.PRIVATE_PULL_REVIEWED_SHA", workflow)
        self.assertIn("environment: private-archive", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertEqual(workflow.count("secrets.MASSIVE"), 1)
        self.assertNotIn("upload-artifact", workflow)
        self.assertNotIn("cache:", workflow)


if __name__ == "__main__":
    unittest.main()
