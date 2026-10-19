"""Add signal-only SPY controls to the immutable 2022 frequency experiment."""

from __future__ import annotations

import gzip
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bear_2022_experiment as e  # noqa: E402
from tools.rotation_ablation_spy import TimedSPY, timed_spy_engine  # noqa: E402
from tools.rotation_frequency_schedule import bind_completed_minute_risk  # noqa: E402

s, c, u, w = e.s, e.c, e.u, e.w
BASE = "d45d8e553eff9d2778aa6b8370cb6204c31333d0"
ANCHOR = "fd8a5f947102d6d48e36c05a35c9c17b6db3ad40fd9becc3096a8ed3812e532e"
PREFIX = "research/verification/bear_2022_signal_spy_v1/"
REPORT_REF = "research-data/bear-2022-signal-spy-v1-engine-report"
WORKFLOW = ".github/workflows/bear-2022-signal-spy.yml"
ADDITIONS = {
    "tools/bear_2022_signal_spy.py",
    "tests/test_bear_2022_signal_spy.py",
    "docs/bear-2022-signal-spy.md",
    ".github/workflows/bear-2022-signal-spy-checks.yml",
}


def need(ok, code):
    s.need(ok, "SIGNAL_SPY_" + code)


class SignalSPY(TimedSPY):
    """Causal risk tiers only: no calendar top-ups between actual signal changes."""

    def _before_execution(self, timestamp, broker):
        previous_day = self.book.calendar.shift(timestamp.astimezone(w.NY).date(), -1)
        for row in self.source.available(timestamp, self._offset):
            self._offset += 1
            before = self.book.regime
            self.book.ingest(row)
            actual = self.book.regime.value
            need(actual == self.reference[row.session]["regime"], "INDEPENDENT_RISK_STATE")
            self.observed_signals.append(
                {"signal_session": row.session, "regime": actual, "observed_at": timestamp}
            )
            if row.session < previous_day or self.book.regime is before:
                continue
            # Cancel stale buys before automatic execution sees the new risk state.
            for identifier, plan in tuple(broker.rebalance_plans.items()):
                if plan.active:
                    broker.plan_manager.cancel(identifier, "spy_signal_changed")
            for order in broker.get_pending_orders("SPY"):
                broker.cancel_order(order.order_id)
            reason = "risk" if self.book.regime.severity > before.severity else "recovery"
            self.pending = row.session, reason
            self._event(
                timestamp,
                "spy_signal_changed",
                signal_session=row.session,
                target_exposure=self.book.regime.exposure(self.config),
                reason=reason,
            )


def signal_engine(inputs, assumptions, settings=None):
    blueprint = timed_spy_engine(inputs, assumptions, settings)
    strategy = SignalSPY(inputs, settings)
    broker = blueprint.broker
    broker.bind_rotation(strategy)
    engine = type(blueprint)(
        blueprint.feed, strategy, blueprint.config, broker_factory=lambda *_a, **_k: broker
    )
    engine.research_inputs, engine.research_assumptions = inputs, assumptions
    engine.research_trace = []
    engine.research_report = blueprint.research_report
    bind_completed_minute_risk(engine)
    engine.research_report.update(schema="bear_2022_signal_only_spy_v1")
    engine.research_report["benchmark_policy"].update(
        monthly="none_without_regime_change",
        recovery="actual_causal_recovery_signal_only",
        exposure_matched=False,
        target_exposures=[0.95, 0.70, 0.45, 0.20],
        execution_cushion_and_rounding="same_TimedSPY_cost_cushion_and_share_quantum",
    )
    return engine


def run_cell(inputs, assumption, settings=None):
    engine = signal_engine(inputs, assumption, settings)
    e.bind_uniform_parent_fees(engine)
    result = u.engine_result(engine)
    if assumption.share_step < 1:
        result["parent_order_fee_audit"] = w.f.order_fee_audit(engine)
    metrics = w.f.summarize(inputs, assumption, result)
    portfolio = w.a.portfolio(inputs, assumption, result)
    metrics.update(
        quarters=e.quarters(portfolio["daily"], assumption.initial_cash),
        end_exposure_pct=portfolio["daily"][-1]["exposure"] * 100,
        independent_signal_checks=len(engine.strategy.observed_signals),
        signal_changes=engine.strategy.events,
    )
    return metrics, {
        "result": result,
        "portfolio": portfolio,
        "observed_signals": engine.strategy.observed_signals,
        "signal_changes": engine.strategy.events,
        "parent_order_fee_audit": w.f.order_fee_audit(engine),
    }


def load_anchor(archive):
    """Pin every byte of the completed prior experiment, including full raw results."""
    archive.visibility(True)
    revision = archive.ref(e.REPORT_REF)
    need(revision is not None, "ANCHOR_MISSING")
    commit = archive.request("GET", "/git/commits/" + c.m.sha(revision))
    tree = archive.request("GET", "/git/trees/" + c.m.sha(commit["tree"]["sha"]) + "?recursive=1")
    paths = {r["path"] for r in tree["tree"] if r["type"] != "tree"}
    need(
        tree.get("truncated") is False
        and 0 < len(paths) <= 3 * e.MAX_PARTS + 1
        and all(p.startswith(e.PREFIX) for p in paths),
        "ANCHOR_PATHS",
    )
    saved, parent = c.b.read(archive, e.REPORT_REF, paths)
    need(parent == c.m.BOOTSTRAP, "ANCHOR_PARENT")
    need(
        s.digest(s.canonical({k: s.digest(v) for k, v in sorted(saved.items())})) == ANCHOR,
        "ANCHOR_BUNDLE",
    )
    manifest = json.loads(saved[e.PREFIX + "manifest.json"])
    need(manifest["source_commit"] == BASE, "ANCHOR_SOURCE")
    need(
        manifest["files"]
        == {k: s.digest(v) for k, v in saved.items() if k != e.PREFIX + "manifest.json"},
        "ANCHOR_FILES",
    )
    need(set(manifest["logical_files"]) == e.LOGICAL, "ANCHOR_LOGICAL_SET")
    result = {}
    for name, item in manifest["logical_files"].items():
        parts = item["parts"]
        need(
            0 < len(parts) <= e.MAX_PARTS
            and parts == [e.PREFIX + name + f".gz.part-{i:02}.bin" for i in range(len(parts))],
            "ANCHOR_PARTS",
        )
        packed = b"".join(saved[p] for p in parts)
        need(s.digest(packed) == item["compressed_sha256"], "ANCHOR_COMPRESSED")
        raw = w.f.d.g.bounded_gunzip(packed, e.MAX_RAW)
        need(
            s.digest(raw) == item["uncompressed_sha256"] and len(raw) == item["uncompressed_bytes"],
            "ANCHOR_LOGICAL_HASH",
        )
        result[name] = raw
    return result


def compare_signals(observed, prior):
    by_session = {str(r["signal_session"]): r["regime"] for r in observed}
    checks = 0
    for step in ("1", "0.1"):
        for case in w.CASES:
            rows = prior[f"P{step}_{case}"]["signal_oracle"]["signals"]
            need(bool(rows), "PRIOR_SIGNAL_EMPTY")
            for row in rows:
                need(by_session.get(row["signal_session"]) == row["regime"], "PRIOR_RISK_PATH")
                checks += 1
    return checks


def pack(logical, source):
    need(set(logical) == e.LOGICAL, "LOGICAL_SET")
    payload, index = {}, {}
    for name, body in sorted(logical.items()):
        need(0 < len(body) <= e.MAX_RAW, "LOGICAL_BUDGET")
        packed = gzip.compress(body, mtime=0)
        parts = []
        for i in range(0, len(packed), c.m.FILE_LIMIT):
            path = PREFIX + name + f".gz.part-{i // c.m.FILE_LIMIT:02}.bin"
            payload[path] = packed[i : i + c.m.FILE_LIMIT]
            parts.append(path)
        need(0 < len(parts) <= e.MAX_PARTS, "PART_BUDGET")
        index[name] = {
            "parts": parts,
            "compressed_sha256": s.digest(packed),
            "uncompressed_sha256": s.digest(body),
            "uncompressed_bytes": len(body),
        }
    payload[PREFIX + "manifest.json"] = s.canonical(
        {
            "schema": "bear_2022_signal_spy_manifest_v1",
            "source_commit": source,
            "anchor_bundle_sha256": ANCHOR,
            "logical_files": index,
            "files": {k: s.digest(v) for k, v in sorted(payload.items())},
        }
    )
    return payload


def build(factory, source):
    anchor = load_anchor(factory())
    inputs, normalized, provenance = e.x.build_inputs(factory, BASE)
    need(normalized == anchor["inputs.json"], "IDENTICAL_INPUTS")
    prior = json.loads(anchor["results.json"])
    original = json.loads(anchor["comparison.json"])["results"]
    cells, raw, passive = {}, {}, {}
    for step in ("1", "0.1"):
        assumption = e.assumptions(step)
        name = f"P{step}_signal_SPY"
        cells[name], raw[name] = run_cell(inputs, assumption)
        cells[name]["prior_strategy_risk_path_checks"] = compare_signals(
            raw[name]["observed_signals"], prior
        )
        engine = w.f.tenth_spy_engine(inputs, assumption)
        e.bind_uniform_parent_fees(engine)
        result = u.engine_result(engine)
        need(
            u.canonical(result) == u.canonical(prior[f"P{step}_funded_SPY"]["result"]),
            "PASSIVE_CONTROL_EXACT",
        )
        passive[step] = w.f.summarize(inputs, assumption, result)
        raw[f"P{step}_funded_SPY"] = {"result": result}
    summary = {
        "schema": "bear_2022_signal_spy_comparison_v1",
        "source_commit": source,
        "anchor_source_commit": BASE,
        "anchor_bundle_sha256": ANCHOR,
        "window": [str(s.FIRST), str(s.LAST)],
        "initial_cash_usd": 100000,
        "input_bytes_identical": True,
        "passive_control_engine_results_identical": True,
        "signal_SPY": cells,
        "funded_SPY": passive,
        "retained_strategy_cells": original["cells"],
        "provenance": provenance,
        "actual_exposure_matched": False,
        "dividend_classification": "ASSUMED_ORDINARY_NOT_FINAL_ISSUER_TAX_EVIDENCE",
        "dividend_tax_rate": "0.10",
        "fee_history_mode": "current_snapshot_backcast",
        "historical_settlement": "T+2",
        "market_api_requests": 0,
        "broker_api_requests": 0,
        "strategy_defaults_changed": False,
        "PIT_verified": False,
        "performance_admission": False,
    }
    return summary, pack(
        {
            "comparison.json": u.canonical(summary),
            "results.json": u.canonical(raw),
            "inputs.json": normalized,
        },
        source,
    )


def guarded(source):
    c.m.sha(source)
    need(
        os.environ.get("GITHUB_REPOSITORY") == c.m.CODE
        and os.environ.get("GITHUB_ACTOR") == "dczhhh"
        and os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_WORKFLOW_REF") == c.m.CODE + "/" + WORKFLOW + "@refs/heads/main"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("RUNNER_OS") == "Linux"
        and not os.environ.get("MASSIVE_API_KEY")
        and all(
            os.environ.get(k, "").lower() != "true"
            for k in ("ACTIONS_STEP_DEBUG", "ACTIONS_RUNNER_DEBUG")
        )
        and subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == source,
        "RUNNER_CONTEXT",
    )
    changes = subprocess.check_output(
        ["git", "diff", "--name-status", "--no-renames", BASE, source], text=True
    ).splitlines()
    need(set(changes) == {"A\t" + p for p in ADDITIONS}, "SEALED_BASE")


def retain(archive, payload, *, verify_only):
    archive.visibility(True)
    need(
        0 < len(payload) <= 3 * e.MAX_PARTS + 1
        and all(p.startswith(PREFIX) and len(b) <= c.m.FILE_LIMIT for p, b in payload.items()),
        "PRIVATE_BUDGET",
    )
    if archive.ref(REPORT_REF) is None:
        need(not verify_only, "REPORT_MISSING")
        c.b.create(archive, REPORT_REF, payload, c.m.BOOTSTRAP)
    saved, parent = c.b.read(archive, REPORT_REF, set(payload))
    need(parent == c.m.BOOTSTRAP and saved == payload, "FRESH_REPORT_BYTES")
    return s.digest(s.canonical({k: s.digest(v) for k, v in sorted(payload.items())}))


def main():
    try:
        need(len(sys.argv) == 3 and sys.argv[1] in {"run", "verify"}, "ARGUMENTS")
        mode, source = sys.argv[1:]
        guarded(source)
        token = os.environ.get("ARCHIVE_WRITE_TOKEN", "")
        need(bool(token), "ARCHIVE_CREDENTIAL")
        objects, bodies = {}, {}

        def factory(writable=False):
            archive = c.r.CachedArchive(c.PacedGitHub(c.m.DESTINATION, token, writable=writable))
            archive.objects, archive.bodies = objects, bodies
            return archive

        summary, payload = build(factory, source)
        digest = retain(factory(mode == "run"), payload, verify_only=mode == "verify")
        keys = (
            "return_pct",
            "end_NAV_usd",
            "maximum_daily_close_drawdown_pct",
            "average_exposure_pct",
            "end_exposure_pct",
            "fees_usd",
            "fills",
            "independent_signal_checks",
            "prior_strategy_risk_path_checks",
            "independent_ledger",
        )
        print(
            json.dumps(
                {
                    "status": "SIGNAL_SPY_CONDITIONAL_RESEARCH_REPLAYED",
                    "mode": mode,
                    "bundle_sha256": digest,
                    "signal_SPY": {
                        n: {k: r[k] for k in keys} for n, r in summary["signal_SPY"].items()
                    },
                    "input_bytes_identical": True,
                    "passive_control_engine_results_identical": True,
                    "PIT_verified": False,
                    "performance_admission": False,
                },
                sort_keys=True,
                default=str,
            )
        )
        return 0
    except Exception as error:
        reason = str(error)
        if re.fullmatch(r"SIGNAL_SPY_[A-Z_0-9]+", reason) is None:
            reason = w.failure_reason(error)
        print(
            json.dumps(
                {
                    "status": "SIGNAL_SPY_BLOCKED",
                    "reason": reason,
                    **u.safe_failure_location(error),
                    "performance_admission": False,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
