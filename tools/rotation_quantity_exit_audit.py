"""Paired, immutable old/new execution audit; no frequency parameter changes."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).parent)]
from tools import bear_2022_signal_spy as b  # noqa: E402
from tools.rotation_quantity_exit import bind_quantity_exit  # noqa: E402

e, w, u, s, c = b.e, b.w, b.u, b.s, b.c
BASE = "9da73f812b7451706065eb6628da540a3d0de7a5"
FREQUENCY_SOURCE = "453dd0dec04daf38b17baeb43eb650a585203b3b"
FREQUENCY_PIN = "a807c5025f66a195147bbade33bbda550d3d2ff1d713391b7b42eadb63e39b45"
WORKFLOW = ".github/workflows/rotation-quantity-exit.yml"
WINDOWS = ("bear2022", "primary", "secondary")
PREFIX = "research/verification/rotation_quantity_exit_v1/"
REPORT_REF = "research-data/rotation-quantity-exit-v1-"
ADDITIONS = {
    "tools/rotation_quantity_exit.py",
    "tools/rotation_quantity_exit_audit.py",
    "tests/test_rotation_quantity_exit.py",
    "tests/test_rotation_quantity_exit_audit.py",
    "docs/rotation-quantity-exit-audit.md",
    ".github/workflows/rotation-quantity-exit-checks.yml",
}
LOGICAL = {"comparison.json", "results.json", "inputs.json"}


def need(ok, code):
    if not ok:
        raise ValueError("QUANTITY_EXIT_" + code)


def load_frequency(archive):
    """Read the exact full frequency archive, including all old Engine outputs."""
    archive.visibility(True)
    revision = archive.ref(w.REPORT_REF)
    need(revision is not None, "FREQUENCY_ANCHOR_MISSING")
    commit = archive.request("GET", "/git/commits/" + c.m.sha(revision))
    tree = archive.request("GET", "/git/trees/" + c.m.sha(commit["tree"]["sha"]) + "?recursive=1")
    paths = {row["path"] for row in tree["tree"] if row["type"] != "tree"}
    need(
        tree.get("truncated") is False
        and 0 < len(paths) <= 81
        and all(p.startswith(w.PREFIX) for p in paths),
        "FREQUENCY_ANCHOR_PATHS",
    )
    saved, parent = c.b.read(archive, w.REPORT_REF, paths)
    need(parent == c.m.BOOTSTRAP, "FREQUENCY_ANCHOR_PARENT")
    need(
        s.digest(u.canonical({k: s.digest(v) for k, v in sorted(saved.items())})) == FREQUENCY_PIN,
        "FREQUENCY_ANCHOR_BUNDLE",
    )
    manifest = json.loads(saved[w.PREFIX + "manifest.json"])
    need(manifest["source_commit"] == FREQUENCY_SOURCE, "FREQUENCY_ANCHOR_SOURCE")
    need(set(manifest["logical_files"]) == w.LOGICAL, "FREQUENCY_LOGICAL_SET")
    need(
        manifest["files"]
        == {p: s.digest(body) for p, body in saved.items() if p != w.PREFIX + "manifest.json"},
        "FREQUENCY_FILE_HASHES",
    )
    result = {}
    for name, item in manifest["logical_files"].items():
        parts = item["parts"]
        need(
            0 < len(parts) <= 16
            and parts == [w.PREFIX + name + f".gz.part-{i:02}.bin" for i in range(len(parts))],
            "FREQUENCY_PARTS",
        )
        packed = b"".join(saved[p] for p in parts)
        need(s.digest(packed) == item["compressed_sha256"], "FREQUENCY_COMPRESSED")
        raw = w.f.d.g.bounded_gunzip(packed, e.MAX_RAW)
        need(s.digest(raw) == item["uncompressed_sha256"], "FREQUENCY_LOGICAL_HASH")
        result[name] = raw
    return result


def inputs_and_prior(factory, window):
    need(window in WINDOWS, "WINDOW")
    if window == "bear2022":
        anchor = b.load_anchor(factory())
        inputs, normalized, _ = e.x.build_inputs(factory, b.BASE)
        old = json.loads(anchor["results.json"])
        expected = anchor["inputs.json"]
    else:
        anchor = load_frequency(factory())
        if window == "primary":
            inputs, normalized, _ = w.f.x.build_inputs(
                factory, w.f.d.BASELINE_SOURCE, u.pa.ClosePriceAudit()
            )
        else:
            docs = w.f.x.s.documents(factory())[0]
            inputs, normalized = u.build_inputs(
                factory, w.f.d.BASELINE_SOURCE, docs, audit=u.pa.ClosePriceAudit()
            )
        expected = anchor["inputs_" + window + ".json"]
        old = json.loads(anchor[window + ".json"])
    need(normalized == expected, "INPUT_BYTES_CHANGED")
    return inputs, normalized, old


def run_cell(inputs, assumption, case, *, corrected, settings=None):
    """Wrap order admission read-only; keep the full original engine and oracles."""
    engine = w.frequency_engine(inputs, assumption, settings or w.SectorRotationConfig(), case)
    oracle = w.FrequencyOracle(engine, case)
    e.bind_uniform_parent_fees(engine)
    if corrected:
        bind_quantity_exit(engine)
    broker = engine.broker
    admitted, original = [], broker.submit_order

    def capture(asset, quantity, *args, **kwargs):
        row = {
            "timestamp": broker.rotation_time,
            "asset": asset,
            "requested": quantity,
            "held_before": broker.account.get_position_quantity(asset),
            "pending_before": [
                {"order_id": o.order_id, "quantity": o.quantity, "side": o.side.value}
                for o in broker.get_pending_orders(asset)
            ],
        }
        order = original(asset, quantity, *args, **kwargs)
        row.update(
            order_id=order.order_id if order else None,
            normalized_quantity=order.quantity if order else None,
            immediate_status=order.status.value if order else None,
            immediate_reason=order.rejection_reason if order else None,
            immediate_code=order._rejection_code if order else None,
        )
        admitted.append(row)
        return order

    broker.submit_order = capture
    result = u.engine_result(engine)
    if assumption.share_step != 1:
        result["parent_order_fee_audit"] = w.f.order_fee_audit(engine)
    orders = {o.order_id: o for o in broker.orders}
    for row in admitted:
        order = orders.get(row["order_id"])
        row.update(
            final_status=order.status.value if order else None,
            final_reason=order.rejection_reason if order else None,
            final_code=order._rejection_code if order else None,
        )
    return {
        "result": result,
        "signal_oracle": oracle.finish(),
        "portfolio": w.a.portfolio(inputs, assumption, result),
        "order_admission": admitted,
        "constraint_audit": [asdict(a) for a in broker.controller.audit],
        "orders": [asdict(o) for o in broker.orders],
    }


def bridge(old, new, initial_cash):
    """Exact prefix, all fills and every affected cash/NAV/holding observation."""
    left, right = old["result"], new["result"]
    need(len(left["trace"]) == len(right["trace"]), "TRACE_COVERAGE")
    changed, prefix = [], 0
    for before, after in zip(left["trace"], right["trace"], strict=True):
        need(before["timestamp"] == after["timestamp"], "TRACE_CLOCK")
        if u.canonical(before) == u.canonical(after):
            if not changed:
                prefix += 1
            continue
        changed.append(
            {
                "timestamp": before["timestamp"],
                "cash_delta": after["cash"] - before["cash"],
                "settled_cash_delta": after["settled_cash"] - before["settled_cash"],
                "receivables_delta": after["receivables"] - before["receivables"],
                "NAV_delta": after["NAV"] - before["NAV"],
                "positions_before": before["positions"],
                "positions_after": after["positions"],
            }
        )
    before_pnl = old["portfolio"]["asset_net_pnl_usd"]
    after_pnl = new["portfolio"]["asset_net_pnl_usd"]
    pnl_delta = {
        a: after_pnl.get(a, 0) - before_pnl.get(a, 0)
        for a in sorted(set(before_pnl) | set(after_pnl))
    }
    nav_delta = new["portfolio"]["end_NAV_usd"] - old["portfolio"]["end_NAV_usd"]
    need(abs(sum(pnl_delta.values()) - nav_delta) <= 1e-8, "ASSET_NAV_BRIDGE")
    need(
        u.canonical(old["signal_oracle"]["signals"])
        == u.canonical(new["signal_oracle"]["signals"]),
        "RISK_SIGNAL_PATH_CHANGED",
    )
    rejected = [r for r in old["order_admission"] if r["final_status"] == "rejected"]
    now_rejected = [r for r in new["order_admission"] if r["final_status"] == "rejected"]
    return {
        "old_summary": left["summary"],
        "new_summary": right["summary"],
        "end_NAV_delta_usd": nav_delta,
        "net_return_delta_pp": nav_delta / float(initial_cash) * 100,
        "old_rejections": len(rejected),
        "new_rejections": len(now_rejected),
        "old_rejection_codes": {
            k: sum(r["final_code"] == k for r in rejected)
            for k in sorted({str(r["final_code"]) for r in rejected})
        },
        "new_rejection_codes": {
            k: sum(r["final_code"] == k for r in now_rejected)
            for k in sorted({str(r["final_code"]) for r in now_rejected})
        },
        "exact_economic_result_unchanged": u.canonical(left) == u.canonical(right),
        "exact_trace_prefix_points": prefix,
        "first_changed_timestamp": changed[0]["timestamp"] if changed else None,
        "asset_net_pnl_delta_usd": pnl_delta,
        "asset_bridge_error_usd": sum(pnl_delta.values()) - nav_delta,
        "old_end_weights": old["portfolio"]["end_holdings_weights"],
        "new_end_weights": new["portfolio"]["end_holdings_weights"],
        "all_changed_observations": changed,
        "old_fills": left["fills"],
        "new_fills": right["fills"],
    }


def pack(logical, source, window):
    need(window in WINDOWS and set(logical) == LOGICAL, "LOGICAL_SET")
    prefix = PREFIX + window + "/"
    payload, index = {}, {}
    for name, raw in sorted(logical.items()):
        need(0 < len(raw) <= e.MAX_RAW, "LOGICAL_BUDGET")
        packed = gzip.compress(raw, mtime=0)
        parts = []
        for i in range(0, len(packed), c.m.FILE_LIMIT):
            path = prefix + name + f".gz.part-{i // c.m.FILE_LIMIT:02}.bin"
            parts.append(path)
            payload[path] = packed[i : i + c.m.FILE_LIMIT]
        need(0 < len(parts) <= e.MAX_PARTS, "PART_BUDGET")
        index[name] = {
            "parts": parts,
            "compressed_sha256": s.digest(packed),
            "uncompressed_sha256": s.digest(raw),
            "uncompressed_bytes": len(raw),
        }
    payload[prefix + "manifest.json"] = u.canonical(
        {
            "schema": "rotation_quantity_exit_manifest_v1",
            "source_commit": source,
            "window": window,
            "prior_bear2022_bundle": b.ANCHOR,
            "prior_frequency_bundle": FREQUENCY_PIN,
            "logical_files": index,
            "files": {p: s.digest(body) for p, body in sorted(payload.items())},
        }
    )
    return payload


def build(factory, source, window):
    inputs, normalized, prior = inputs_and_prior(factory, window)
    summaries, raw = {}, {}
    for step in ("1", "0.1"):
        assumption = (
            e.assumptions(step)
            if window == "bear2022"
            else w.assumptions(step, primary=window == "primary")
        )
        for case in w.CASES:
            name = f"P{step}_{case}"
            old = run_cell(inputs, assumption, case, corrected=False)
            need(
                u.canonical(old["result"]) == u.canonical(prior[name]["result"]),
                "ORIGINAL_ENGINE_BYTES_CHANGED",
            )
            need(
                u.canonical(old["signal_oracle"]) == u.canonical(prior[name]["signal_oracle"]),
                "ORIGINAL_ORACLE_BYTES_CHANGED",
            )
            new = run_cell(inputs, assumption, case, corrected=True)
            detail = bridge(old, new, assumption.initial_cash)
            summaries[name] = {
                k: v
                for k, v in detail.items()
                if k not in {"all_changed_observations", "old_fills", "new_fills"}
            }
            raw[name] = {"original": old, "corrected": new, "bridge": detail}
    summary = {
        "schema": "rotation_quantity_exit_comparison_v1",
        "source_commit": source,
        "window": window,
        "execution_start": str(inputs.bars[0].end.date()),
        "execution_end": str(inputs.bars[-1].end.date()),
        "original_engine_and_oracle_bytes_exact": True,
        "input_bytes_identical": True,
        "only_variable": "opt_in_Gatekeeper_zero_cancellation_consistent_with_existing_fill_executor",
        "strategy_defaults_changed": False,
        "frequency_settings_changed": False,
        "cash_or_quantity_ledger_tolerances_changed": False,
        "SPY_controls": "unchanged_frozen_controls_not_recomputed_in_this_execution_fix",
        "cells": summaries,
        "market_api_requests": 0,
        "broker_api_requests": 0,
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
        window,
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


def retain(archive, payload, window, *, verify_only):
    need(window in WINDOWS, "WINDOW")
    archive.visibility(True)
    need(
        0 < len(payload) <= 3 * e.MAX_PARTS + 1
        and all(
            p.startswith(PREFIX + window + "/") and len(body) <= c.m.FILE_LIMIT
            for p, body in payload.items()
        ),
        "PRIVATE_BUDGET",
    )
    ref = REPORT_REF + window
    if archive.ref(ref) is None:
        need(not verify_only, "REPORT_MISSING")
        c.b.create(archive, ref, payload, c.m.BOOTSTRAP)
    saved, parent = c.b.read(archive, ref, set(payload))
    need(parent == c.m.BOOTSTRAP and saved == payload, "FRESH_REPORT_BYTES")
    return s.digest(u.canonical({k: s.digest(v) for k, v in sorted(payload.items())}))


def main():
    try:
        need(len(sys.argv) == 4 and sys.argv[1] in {"run", "verify"}, "ARGUMENTS")
        mode, source, window = sys.argv[1:]
        need(window in WINDOWS, "WINDOW")
        guarded(source)
        token = os.environ.get("ARCHIVE_WRITE_TOKEN", "")
        need(bool(token), "ARCHIVE_CREDENTIAL")
        objects, bodies = {}, {}

        def factory(writable=False):
            archive = c.r.CachedArchive(c.PacedGitHub(c.m.DESTINATION, token, writable=writable))
            archive.objects, archive.bodies = objects, bodies
            return archive

        summary, payload = build(factory, source, window)
        digest = retain(factory(mode == "run"), payload, window, verify_only=mode == "verify")
        print(
            json.dumps(
                {
                    "status": "QUANTITY_EXIT_PAIRED_RESEARCH_REPLAYED",
                    "mode": mode,
                    "window": window,
                    "bundle_sha256": digest,
                    "cells": summary["cells"],
                    "PIT_verified": False,
                    "performance_admission": False,
                },
                sort_keys=True,
                default=str,
            )
        )
        return 0
    except Exception as error:
        message = str(error)
        reason = (
            message
            if message.startswith("QUANTITY_EXIT_")
            and all(ch.isupper() or ch.isdigit() or ch == "_" for ch in message)
            else "UNVERIFIED_RUNTIME_FAILURE"
        )
        print(
            json.dumps(
                {
                    "status": "QUANTITY_EXIT_REPLAY_BLOCKED",
                    "reason": reason,
                    **u.safe_failure_location(error),
                    "PIT_verified": False,
                    "performance_admission": False,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
