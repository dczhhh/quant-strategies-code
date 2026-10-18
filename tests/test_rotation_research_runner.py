"""Invented-only operator regressions; no licensed responses or account records."""

import io
import json
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

from quant_constraints.calendar import NY, SessionCalendar
from tests.test_private_archive_migration import FakeGitHub
from tests.test_real_etf_2025 import documents, market
from tools import rotation_research_runner as v

SOURCE = "d" * 40
OBSERVED = "2026-10-15T10:00:00+00:00"


def record(value, *, issuer=False):
    return {
        "body": value if isinstance(value, bytes) else v.m.canonical(value),
        "receipt": {"fetched_at" if issuer else "response_received_at": OBSERVED},
        "commit": "e" * 40,
    }


def invented_sources(ticker="SPY"):
    calendar = SessionCalendar()
    day, final = v.a.day(v.WARM_START), v.a.day(v.RUN_END)
    warm, retained, minutes = [], [], []
    while day <= final:
        session = calendar.session(day)
        if session:
            row = {
                "t": int(datetime.combine(day, time(), NY).timestamp() * 1000),
                "o": 100,
                "h": 100,
                "l": 100,
                "c": 100,
                "v": 100000,
            }
            (warm if day.isoformat() <= v.WARM_END else retained).append(row)
            if day.isoformat() >= v.RUN_START:
                for offset in (0, *range(4, 15)):
                    start = session.market_open + timedelta(minutes=offset)
                    minutes.append({**row, "t": int(start.timestamp() * 1000)})
                minutes.append(
                    {
                        **row,
                        "t": int((session.market_close - timedelta(minutes=1)).timestamp() * 1000),
                    }
                )
        day += timedelta(days=1)
    old = {}
    for product in v.a.products(ticker):
        kind = v.r.kind(product)
        value = market(product)
        if kind in {"raw", "split-adjusted"}:
            value["results"] = retained
        old[kind] = record(value)
    captured = {
        p["kind"]: record(
            {
                "ticker": ticker,
                "status": "OK",
                "adjusted": False,
                "results": warm
                if p["kind"] == "warm-raw"
                else minutes
                if p["kind"] == "minutes"
                else [],
            }
        )
        for p in v.products(ticker)
    }
    docs = documents(ticker)
    for item in docs.values():
        item["receipt"]["fetched_at"] = OBSERVED
    return captured, old, docs


class RotationOperatorTests(unittest.TestCase):
    def setUp(self):
        self.archive = FakeGitHub(v.m.DESTINATION)
        self.product = v.products("SPY")[0]

    def test_scope_is_exactly_48_new_products_not_an_unbounded_api_permission(self):
        products = [p for ticker in v.p.TICKERS for p in v.products(ticker)]
        self.assertEqual(len(products), 48)
        self.assertEqual(len({p["url"] for p in products}), 48)
        self.assertEqual(v.m.CALL_LIMIT, 800)
        self.assertEqual(v.m.FILE_LIMIT, 524288)
        self.assertTrue(
            all(
                "adjusted=false" in p["url"]
                for p in products
                if p["kind"] in {"warm-raw", "minutes"}
            )
        )
        for bad in ("WOLF", "RY", "TSM", "spy", None):
            with self.subTest(ticker=bad), self.assertRaisesRegex(v.p.Blocked, "FIXED_SCOPE"):
                v.products(bad)
        self.assertFalse(v.POLICY["automatic_retry"])
        self.assertFalse(v.POLICY["PIT_verified"])

    def test_large_record_round_trip_uses_bounded_blobs_and_rejects_tampered_hash(self):
        body = b"invented " * 90000
        name, prefix = "research-data/invented-large", "research/data/invented/"
        commit = v.write_record(self.archive, name, prefix, body, {"invented": True}, v.m.BOOTSTRAP)
        result, metadata, parent, revision = v.read_record(self.archive, name, prefix)
        self.assertEqual(result, body)
        self.assertEqual((parent, revision), (v.m.BOOTSTRAP, commit))
        self.assertEqual(metadata["part_count"], 2)
        self.assertTrue(all(len(blob) <= v.m.FILE_LIMIT for blob in self.archive.blobs.values()))
        self.assertLess(len(self.archive.events), 40)
        metadata["body_sha256"] = "0" * 64
        v.b.create(
            self.archive,
            name + "-bad",
            {
                prefix + "receipt.json": v.m.canonical(metadata),
                **{
                    prefix + f"part-{i:03}.bin": body[i * v.m.FILE_LIMIT : (i + 1) * v.m.FILE_LIMIT]
                    for i in range(2)
                },
            },
            v.m.BOOTSTRAP,
        )
        with self.assertRaisesRegex(v.p.Blocked, "HASH_MISMATCH"):
            v.read_record(self.archive, name + "-bad", prefix)

    def test_credential_check_spans_chunk_boundary_and_keeps_old_limits(self):
        secret = self.archive.token.encode()
        bad = b"x" * (v.m.FILE_LIMIT - 4) + secret + b"x"
        for body in (bad, b'{"apiKey":"invented"}', b"x" * (v.MAX_BODY + 1)):
            with self.subTest(size=len(body)), self.assertRaises(v.p.Blocked):
                v.write_record(
                    self.archive,
                    "research-data/secret",
                    "research/data/x/",
                    body,
                    {},
                    v.m.BOOTSTRAP,
                )
        self.assertEqual(self.archive.events, [])

    def test_capture_reuse_and_readonly_never_access_market_key_or_write(self):
        fetch = Mock(return_value=(200, b'{"invented":true}'))
        key = Mock(return_value="INVENTED_MARKET_CREDENTIAL")
        first, made = v.capture_product(self.archive, self.product, SOURCE, key, fetch=fetch)
        self.assertEqual(made, 1)
        key.assert_called_once()
        fetch.assert_called_once()
        before = deepcopy(self.archive.refs)
        self.archive.events.clear()
        for readonly in (True, False):
            again, count = v.capture_product(
                self.archive,
                self.product,
                "f" * 40,
                lambda: self.fail("cached input accessed credential"),
                readonly=readonly,
                fetch=lambda *_: self.fail("cached input requested"),
            )
            self.assertEqual((again, count), (first, 0))
        self.assertEqual(self.archive.refs, before)
        self.assertTrue(all(method == "GET" for method, _ in self.archive.events))

    def test_missing_readonly_and_unknown_reservation_never_request(self):
        forbidden = Mock(side_effect=AssertionError("credential must not be accessed"))
        with self.assertRaisesRegex(v.p.Blocked, "INPUT_MISSING"):
            v.capture_product(self.archive, self.product, SOURCE, forbidden, readonly=True)
        with self.assertRaises(TimeoutError):
            v.capture_product(
                self.archive,
                self.product,
                SOURCE,
                lambda: "KEY",
                fetch=Mock(side_effect=TimeoutError("invented")),
            )
        before = deepcopy(self.archive.refs)
        with self.assertRaisesRegex(v.p.Blocked, "UNKNOWN_REQUEST_OUTCOME_RETAINED"):
            v.capture_product(self.archive, self.product, SOURCE, forbidden)
        self.assertEqual(self.archive.refs, before)
        forbidden.assert_not_called()

    def test_non_200_is_retained_once_and_not_retried(self):
        fetch = Mock(return_value=(403, b"invented HTTP denial"))
        for _ in range(2):
            with self.assertRaisesRegex(v.p.Blocked, "HTTP_UNVERIFIED"):
                v.capture_product(self.archive, self.product, SOURCE, lambda: "KEY", fetch=fetch)
        fetch.assert_called_once()
        body, metadata, _, _ = v.read_record(
            self.archive, v.ref(self.product), v.PREFIX + self.product["id"] + "/"
        )
        self.assertEqual(body, b"invented HTTP denial")
        self.assertEqual(metadata["http_status"], 403)

    def test_response_basis_and_pagination_are_fail_closed(self):
        for change in (
            {"adjusted": True},
            {"ticker": "TSM"},
            {"next_url": "https://example.invalid/next"},
            {"status": "ERROR"},
        ):
            value = {"ticker": "SPY", "status": "OK", "adjusted": False, "results": []} | change
            with self.subTest(change=change), self.assertRaises(v.p.Blocked):
                v.rows(self.product, record(value))

    def test_normalization_matches_explicit_original_cash_not_derived_cash(self):
        captured, old, docs = invented_sources()
        value = json.loads(old["dividends"]["body"])
        value["results"][0]["split_adjusted_cash_amount"] = 999
        old["dividends"]["body"] = v.m.canonical(value)
        component = v.normalize_component("SPY", captured, old, docs)
        self.assertGreater(len(component["closes"]), 300)
        self.assertEqual(component["distributions"][0]["cash_amount"], "1")
        self.assertEqual(component["distributions"][0]["classification"], "ordinary_fund")
        self.assertEqual(len(component["bars"]), 13 * 45)
        self.assertTrue(all(s["observed_at"] == OBSERVED for s in component["sources"]))
        self.assertEqual(component["old_audit_commit"], v.OLD_AUDIT)

    def test_normalization_rejects_missing_minutes_daily_gaps_special_and_estimated_tax(self):
        for defect in ("minute", "daily", "special", "estimated", "blank"):
            captured, old, docs = invented_sources()
            if defect in {"minute", "daily"}:
                item = captured["minutes" if defect == "minute" else "warm-raw"]
                value = json.loads(item["body"])
                value["results"].pop(0)
                item["body"] = v.m.canonical(value)
            else:
                docs = documents(
                    changes={"O": "1"}
                    if defect == "special"
                    else {"D": "E"}
                    if defect == "estimated"
                    else {"O": ""}
                )
                for item in docs.values():
                    item["receipt"]["fetched_at"] = OBSERVED
            with self.subTest(defect=defect), self.assertRaises(v.p.Blocked):
                v.normalize_component("SPY", captured, old, docs)

    def test_prepare_independent_regeneration_and_component_corruption_fail(self):
        captured, old, docs = invented_sources()
        with (
            patch.object(v, "old_inputs", return_value=(old, "DIVIDEND_SPLIT_BASIS_CONFLICT")),
            patch.object(v.a, "issuer_documents", return_value=docs),
        ):

            def fetch(product, _):
                return 200, captured[product["kind"]]["body"]

            first = v.prepare(self.archive, "SPY", SOURCE, lambda: "KEY", fetch=fetch)
            self.assertEqual(first["new_market_requests"], 4)
            self.archive.events.clear()
            second = v.prepare(
                self.archive,
                "SPY",
                SOURCE,
                lambda: self.fail("readonly credential access"),
                readonly=True,
            )
            self.assertEqual(second["new_market_requests"], 0)
            self.assertEqual(first["component_sha256"], second["component_sha256"])
            self.assertFalse(second["engine_invoked"])
            self.assertLess(len(self.archive.events), 200)
            self.assertTrue(all(method == "GET" for method, _ in self.archive.events))
            with (
                patch.object(v, "normalize_component", return_value={"changed": True}),
                self.assertRaisesRegex(v.p.Blocked, "READBACK_MISMATCH"),
            ):
                v.prepare(self.archive, "SPY", SOURCE, lambda: "", readonly=True)

    def test_guard_keeps_exact_source_and_checks_checkout_before_reading_archive(self):
        with (
            patch.object(v.u, "guarded", return_value=SOURCE),
            patch.object(v, "STRATEGY_SHA", "a" * 40),
            patch.object(v.subprocess, "run", return_value=Mock(stdout="a" * 40 + "\n")) as run,
        ):
            self.assertEqual(v.guarded(), SOURCE)
            self.assertEqual(run.call_count, 2)
            run.return_value.stdout = "b" * 40
            with self.assertRaisesRegex(v.p.Blocked, "STRATEGY_SOURCE_MISMATCH"):
                v.guarded()

    def replay_components(self, *, bad_plan=False):
        # Transport/wiring fixture only. These empty records are deliberately
        # not a valid market bundle and the fake runtime never claims to be Engine.
        for index, ticker in enumerate(v.p.TICKERS):
            component = {
                "ticker": ticker,
                "cusip": f"{index:09}",
                "plan_sha256": "0" * 64 if bad_plan else v.PLAN_HASH,
                "old_audit_commit": v.OLD_AUDIT,
                "sources": [],
                **{key: [] for key in ("closes", "bars", "distributions", "splits")},
            }
            v.write_record(
                self.archive,
                v.component_ref(ticker, SOURCE),
                v.COMPONENT_PREFIX + SOURCE + "/" + ticker.lower() + "/",
                v.m.canonical(component),
                {"source_commit": SOURCE, "plan_sha256": v.PLAN_HASH},
                v.m.BOOTSTRAP,
            )

    def fake_runtime(self):
        engine = SimpleNamespace(
            run=Mock(),
            research_report={
                "status": "RETROSPECTIVE_ENGINE_LEDGER_PASS",
                "engine_invoked": True,
                **dict.fromkeys(v.m.FLAGS, False),
                "performance_admission": False,
                "full_real_scenarios_accepted": 0,
            },
            research_trace=[{"cash": "12345.67", "invented_transport_fixture": True}],
        )
        runtime = SimpleNamespace(
            __file__=str(v.ROOT / "research-source/src/research.py"),
            ResearchAssumptions=Mock(),
            retrospective_rotation_engine=Mock(return_value=engine),
        )
        model = SimpleNamespace(
            **{
                name: Mock()
                for name in (
                    "ResearchInputs",
                    "ResearchSource",
                    "RawClose",
                    "ResearchBar",
                    "ResearchDistribution",
                    "ResearchSplit",
                    "ResearchSecurity",
                )
            }
        )
        return runtime, model, engine

    def test_replay_wiring_retains_details_privately_and_fresh_readonly_matches(self):
        self.replay_components()
        runtime, model, _ = self.fake_runtime()
        with patch.object(
            v.importlib,
            "import_module",
            side_effect=lambda name: model if name.endswith("research_inputs") else runtime,
        ):
            first = v.replay(self.archive, SOURCE, OBSERVED)
            self.archive.events.clear()
            second = v.replay(self.archive, SOURCE, OBSERVED, readonly=True)
            self.assertEqual(first, second)
            self.assertLess(len(self.archive.events), 200)
            self.assertTrue(all(method == "GET" for method, _ in self.archive.events))
        self.assertEqual(model.ResearchInputs.call_args.args[-1], "retained_market_data")
        self.assertNotIn("12345", json.dumps(first))
        self.assertNotIn("trace", first)
        self.assertFalse(first["PIT_verified"] or first["performance_admission"])
        self.assertEqual(runtime.retrospective_rotation_engine.call_count, 2)
        body, _, _, _ = v.read_record(
            self.archive,
            "research-data/rotation-execution-v1-" + SOURCE,
            v.REPORT_PREFIX + SOURCE + "/",
        )
        self.assertIn(b"12345.67", body)

    def test_replay_rejects_component_rebinding_before_invoking_runtime(self):
        self.replay_components(bad_plan=True)
        runtime, model, engine = self.fake_runtime()
        with (
            patch.object(
                v.importlib,
                "import_module",
                side_effect=lambda name: model if name.endswith("research_inputs") else runtime,
            ),
            self.assertRaisesRegex(v.p.Blocked, "REPLAY_COMPONENT_BINDING_INVALID"),
        ):
            v.replay(self.archive, SOURCE, OBSERVED)
        engine.run.assert_not_called()

    def test_replay_refuses_false_certification_and_different_fresh_results(self):
        self.replay_components()
        runtime, model, engine = self.fake_runtime()
        with patch.object(
            v.importlib,
            "import_module",
            side_effect=lambda name: model if name.endswith("research_inputs") else runtime,
        ):
            engine.research_report["PIT_verified"] = True
            with self.assertRaisesRegex(v.p.Blocked, "RESULT_BOUNDARY_INVALID"):
                v.replay(self.archive, SOURCE, OBSERVED)
            engine.research_report["PIT_verified"] = False
            v.replay(self.archive, SOURCE, OBSERVED)
            engine.research_trace[0]["cash"] = "wrong fresh result"
            with self.assertRaisesRegex(v.p.Blocked, "READBACK_MISMATCH"):
                v.replay(self.archive, SOURCE, OBSERVED, readonly=True)

    def test_cli_does_not_publish_error_bodies_amounts_or_credentials(self):
        with (
            patch.object(v.sys, "argv", ["runner", "replay"]),
            patch.object(v, "guarded", side_effect=RuntimeError("PRIVATE amount=123 token=SECRET")),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(v.main(), 1)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["code"], "CHECK_FAILED")
        self.assertNotIn("PRIVATE", output.getvalue())
        self.assertFalse(summary["PIT_verified"] or summary["performance_admission"])

    def test_workflow_is_manual_owner_main_reviewed_with_no_key_on_replay_or_verify(self):
        path = Path(__file__).parents[1] / ".github/workflows/rotation-research-2025.yml"
        document = yaml.safe_load(path.read_text())
        self.assertEqual(set(document.get("on", document.get(True))), {"workflow_dispatch"})
        guard = document["jobs"]["plan"]["if"]
        for text in (
            "github.actor == 'dczhhh'",
            "github.ref == 'refs/heads/main'",
            "github.sha == vars.REAL_ETF_PROBE_REVIEWED_SHA",
        ):
            self.assertIn(text, guard)
        for name in ("prepare", "verify-input", "replay", "verify-replay"):
            job = document["jobs"][name]
            self.assertEqual(job["environment"], "private-archive")
            self.assertEqual(job["runs-on"], "ubuntu-24.04")
            text = json.dumps(job)
            self.assertNotIn("upload-artifact", text)
            if name != "prepare":
                self.assertNotIn("MASSIVE", text)
            strategy = [
                step
                for step in job["steps"]
                if step.get("with", {}).get("path") == "research-source"
            ]
            self.assertEqual(strategy[0]["with"]["ref"], v.STRATEGY_SHA)
            installs = [
                i for i, step in enumerate(job["steps"]) if "uv sync" in step.get("run", "")
            ]
            credentials = [
                i
                for i, step in enumerate(job["steps"])
                if "ARCHIVE_WRITE_TOKEN" in step.get("env", {})
            ]
            self.assertLess(installs[0], credentials[0])


if __name__ == "__main__":
    unittest.main()
