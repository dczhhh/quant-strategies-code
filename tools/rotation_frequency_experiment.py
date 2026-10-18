"""Issue 42 frozen frequency comparison; retained private inputs, zero market requests."""

from __future__ import annotations

import gzip
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ml4t.backtest.strategies.sector_rotation.config import SectorRotationConfig  # noqa: E402
from ml4t.backtest.strategies.sector_rotation.research_ablation import (
    bind_uniform_parent_fees,  # noqa: E402
)
from ml4t.backtest.strategies.sector_rotation.strategy import SectorRotationStrategy  # noqa: E402
from ml4t.backtest.types import OrderSide  # noqa: E402
from tools import rotation_v_reversal_experiment as v  # noqa: E402
from tools.rotation_frequency_oracle import FrequencyOracle  # noqa: E402
from tools.rotation_frequency_schedule import CASES, frequency_engine  # noqa: E402

f, u, c, a = v.f, v.u, v.c, v.a
PREFIX = "research/verification/rotation_frequency_v1/"
REPORT_REF = "research-data/rotation-frequency-v1-engine-report"
WORKFLOW = ".github/workflows/rotation-frequency.yml"
BASE = "e627b042505a26173cdd97855c684446782829dc"
PIN = "c256fefb92587e2f81c00b2c861268e0fb6ed9f47347993a444baf2f4269fbc8"
LOGICAL = v.LOGICAL


def require(ok, code):
    if not ok:
        raise ValueError("FREQUENCY_" + code)


def load_anchor(archive):
    archive.visibility(True)
    revision = archive.ref(v.REPORT_REF)
    require(revision is not None, "ANCHOR_MISSING")
    commit = archive.request("GET", "/git/commits/" + c.m.sha(revision))
    tree = archive.request("GET", "/git/trees/" + c.m.sha(commit["tree"]["sha"]) + "?recursive=1")
    require(tree.get("truncated") is False, "ANCHOR_TREE")
    paths = {row["path"] for row in tree["tree"] if row["type"] != "tree"}
    require(0 < len(paths) <= 81 and all(p.startswith(v.PREFIX) for p in paths), "ANCHOR_PATHS")
    saved, parent = c.b.read(archive, v.REPORT_REF, paths)
    require(parent == c.m.BOOTSTRAP, "ANCHOR_PARENT")
    require(
        c.m.sha256(u.canonical({k: c.m.sha256(v) for k, v in sorted(saved.items())})) == PIN,
        "ANCHOR_BUNDLE",
    )
    manifest = f.d.decode(saved[v.PREFIX + "manifest.json"])
    require(manifest["source_commit"] == BASE, "ANCHOR_SOURCE")
    require(
        manifest["files"]
        == {k: c.m.sha256(body) for k, body in saved.items() if k != v.PREFIX + "manifest.json"},
        "ANCHOR_HASHES",
    )
    result = {}
    for name, item in manifest["logical_files"].items():
        parts = item["parts"]
        require(
            0 < len(parts) <= 16
            and parts == [v.PREFIX + name + f".gz.part-{i:02}.bin" for i in range(len(parts))],
            "ANCHOR_PARTS",
        )
        packed = b"".join(saved[path] for path in parts)
        require(c.m.sha256(packed) == item["compressed_sha256"], "ANCHOR_COMPRESSED_HASH")
        raw = f.d.g.bounded_gunzip(packed, 64 * 1024 * 1024)
        require(c.m.sha256(raw) == item["uncompressed_sha256"], "ANCHOR_LOGICAL_HASH")
        f.d.decode(raw)
        result[name] = raw
    return result


assumptions = v.assumptions
period = v.period
fixed_fill_diagnostic = v.fixed_fill_diagnostic


def run_cell(inputs, assumption, case, settings=None):
    settings = settings or SectorRotationConfig()
    engine = frequency_engine(inputs, assumption, settings, case)
    oracle = FrequencyOracle(engine, case)
    bind_uniform_parent_fees(engine)
    assert isinstance(engine.strategy, SectorRotationStrategy)
    result = u.engine_result(engine)
    if assumption.share_step == Decimal(".1"):
        result["parent_order_fee_audit"] = f.order_fee_audit(engine)
    signal_report = oracle.finish() if oracle else None
    metrics = f.summarize(inputs, assumption, result)
    portfolio = a.portfolio(inputs, assumption, result)
    daily = portfolio["daily"]
    fees = f.order_fee_audit(engine)
    components = {
        name: sum(float(row["components_usd"][name]) for row in fees["orders"])
        for name in (
            "broker_commission",
            "exchange_ecn_fees_or_rebates",
            "clearing_fees",
            "regulatory_fees",
            "pass_through_fees",
        )
    }
    metrics.update(
        status="CONDITIONAL_RESEARCH_PASS",
        end_exposure_pct=daily[-1]["exposure"] * 100,
        parent_order_day_groups=len(fees["orders"]),
        fee_components_usd=components,
        partial_fill_groups=sum(row["fill_count"] > 1 for row in fees["orders"]),
        mean_cash_pct=sum(row["cash"] / row["NAV"] for row in daily) / len(daily) * 100,
        dividend_accruals=result["summary"]["dividend_accruals"],
        modeled_credits=result["summary"]["modeled_credits"],
    )
    if signal_report:
        metrics["signal_oracle"] = {
            k: signal_report[k]
            for k in (
                "status",
                "signal_observations",
                "monthly_decisions",
                "submitted_order_intents",
                "risk_calls",
                "settlement_blocks",
            )
        }
    metrics["phases"] = [
        period(daily, date(2025, 2, 19), date(2025, 4, 8)),
        period(daily, date(2025, 4, 8), date(2025, 6, 30)),
    ]
    for n in (5, 10, 20):
        end = c.CAL.shift(date(2025, 4, 8), n)
        metrics[f"rebound_{n}_sessions"] = period(daily, date(2025, 4, 8), end)
    raw = {
        "result": result,
        "portfolio": portfolio,
        "signal_oracle": signal_report,
        "timed_spy_signals": getattr(engine.strategy, "observed_signals", []),
        "strategy_settings": asdict(settings),
        "decisions": list(engine.strategy.events),
        "parent_order_fee_audit": fees,
        "fixed_fill_diagnostic": fixed_fill_diagnostic(engine)
        if assumption.share_step == Decimal(".1")
        else None,
    }
    metrics["gross_traded_notional_usd"] = sum(
        abs(fill["quantity"] * fill["price"]) for fill in result["fills"]
    )
    metrics["gross_turnover_initial_capital"] = metrics["gross_traded_notional_usd"] / float(
        assumption.initial_cash
    )
    metrics["schedule"] = {
        "case": case,
        "signal_dates": sorted(oracle.rotation_days),
        "recovery_rule": "rotation_dates" if case.endswith("_SR") else "original_month_ends",
    }
    return metrics, raw


def matrix(inputs, *, primary, settings=None):
    summaries, raw = {}, {}
    for step in ("1", "0.1"):
        assumption = assumptions(step, primary=primary)
        for case in CASES:
            name = f"P{step}_{case}"
            summaries[name], raw[name] = run_cell(inputs, assumption, case, settings)
            v.progress(name, summaries[name], primary)
    comparisons = {}
    for step in ("1", "0.1"):
        baseline = summaries[f"P{step}_M"]
        comparisons[step] = {
            case: {
                "net_return_delta_percentage_points": summaries[f"P{step}_{case}"]["return_pct"]
                - baseline["return_pct"],
                "end_NAV_delta_usd": summaries[f"P{step}_{case}"]["end_NAV_usd"]
                - baseline["end_NAV_usd"],
            }
            for case in CASES
            if case != "M"
        }
    return {"cells": summaries, "comparisons": comparisons}, raw


def pack(logical, source):
    require(set(logical) == LOGICAL, "LOGICAL_FILE_SET")
    payload, index = {}, {}
    for name, body in sorted(logical.items()):
        compressed = gzip.compress(body, mtime=0)
        parts = []
        for i in range(0, len(compressed), c.m.FILE_LIMIT):
            path = PREFIX + name + f".gz.part-{i // c.m.FILE_LIMIT:02}.bin"
            payload[path] = compressed[i : i + c.m.FILE_LIMIT]
            parts.append(path)
        require(0 < len(parts) <= 16, "REPORT_SIZE")
        index[name] = {
            "parts": parts,
            "compressed_sha256": c.m.sha256(compressed),
            "uncompressed_sha256": c.m.sha256(body),
        }
    hashes = {
        name: c.m.sha256((Path(__file__).parent / name).read_bytes())
        for name in (
            "rotation_frequency_experiment.py",
            "rotation_frequency_oracle.py",
            "rotation_frequency_schedule.py",
        )
    }
    payload[PREFIX + "manifest.json"] = u.canonical(
        {
            "schema": "rotation_frequency_manifest_v1",
            "source_commit": source,
            "prior_bundle_sha256": PIN,
            "operators": hashes,
            "files": {k: c.m.sha256(v) for k, v in sorted(payload.items())},
            "logical_files": index,
        }
    )
    return payload


def retained_benchmark(result):
    """Restore only serialized execution clocks; preserve pinned economic values."""
    value = dict(result)
    sides = {str(side): side for side in OrderSide}
    for name in ("trace", "fills"):
        rows = []
        for row in result[name]:
            stamp = datetime.fromisoformat(row["timestamp"])
            require(stamp.utcoffset() is not None, "ANCHOR_TIMESTAMP")
            restored = dict(row, timestamp=stamp)
            if name == "fills":
                require(row["side"] in sides, "ANCHOR_SIDE")
                restored["side"] = sides[row["side"]]
            rows.append(restored)
        value[name] = rows
    return value


def build(factory, source):
    anchor = load_anchor(factory())
    inputs, normalized, provenance = f.x.build_inputs(
        factory, f.d.BASELINE_SOURCE, u.pa.ClosePriceAudit()
    )
    require(normalized == anchor["inputs_primary.json"], "PRIMARY_INPUT_BYTES_CHANGED")
    primary, primary_raw = matrix(inputs, primary=True)
    docs = f.x.s.documents(factory())[0]
    second_inputs, second_normalized = u.build_inputs(
        factory, f.d.BASELINE_SOURCE, docs, audit=u.pa.ClosePriceAudit()
    )
    require(second_normalized == anchor["inputs_secondary.json"], "SECONDARY_INPUT_BYTES_CHANGED")
    secondary, secondary_raw = matrix(second_inputs, primary=False)
    for window, raw in (("primary", primary_raw), ("secondary", secondary_raw)):
        previous = json.loads(anchor[window + ".json"])
        for step in ("1", "0.1"):
            require(
                u.canonical(raw[f"P{step}_M"]["result"])
                == u.canonical(previous[f"P{step}_S1_R1"]["result"]),
                "MONTHLY_ENGINE_BYTES_CHANGED",
            )
    for window, source_inputs, summaries, raw in (
        ("primary", inputs, primary, primary_raw),
        ("secondary", second_inputs, secondary, secondary_raw),
    ):
        previous = json.loads(anchor[window + ".json"])
        for name, entry in raw.items():
            step = name.split("_")[0][1:]
            analytics = a.describe(
                source_inputs,
                assumptions(step, primary=window == "primary"),
                entry["result"],
                retained_benchmark(previous[f"P{step}_SPY_passive"]["result"]),
                entry["signal_oracle"],
            )
            entry["analytics"] = analytics
            summaries["cells"][name].update(
                {
                    key: analytics[key]
                    for key in (
                        "regression",
                        "up_capture",
                        "down_capture",
                        "risk_signal_transitions",
                        "risk_signal_delays",
                        "ex_post_SPY_peak_trough_phases",
                    )
                }
            )
    summary = {
        "schema": "rotation_frequency_v1",
        "source_commit": source,
        "primary_window": ["2024-12-02", "2025-06-30"],
        "secondary_window": ["2025-08-29", "2025-11-05"],
        "primary": primary,
        "secondary": secondary,
        "four_monthly_engine_bytes_exactly_reproduced": True,
        "market_api_requests": 0,
        "broker_api_requests": 0,
        "initial_cash_usd": 100000,
        "thresholds_optimized": False,
        "secondary_is_blind_holdout": False,
        "PIT_verified": False,
        "performance_admission": False,
        "full_real_scenarios_accepted": 0,
        "complete_account_commission_reconciliation": False,
        "boundary_policy": "fixed_ex_post_phases_used_only_for_reporting_not_strategy_inputs",
        "provenance_note": provenance["strict_2024_classification_status"],
    }
    logical = {
        "comparison.json": u.canonical(summary),
        "primary.json": u.canonical(primary_raw),
        "secondary.json": u.canonical(secondary_raw),
        "inputs_primary.json": normalized,
        "inputs_secondary.json": second_normalized,
    }
    return summary, pack(logical, source)


def guarded(source):
    require(re.fullmatch(r"[0-9a-f]{40}", source) is not None, "SOURCE_SHA")
    require(
        os.environ.get("GITHUB_REPOSITORY") == c.m.CODE
        and os.environ.get("GITHUB_ACTOR") == "dczhhh"
        and os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_WORKFLOW_REF") == c.m.CODE + "/" + WORKFLOW + "@refs/heads/main"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("RUNNER_OS") == "Linux"
        and all(
            os.environ.get(key, "").lower() != "true"
            for key in ("ACTIONS_STEP_DEBUG", "ACTIONS_RUNNER_DEBUG")
        )
        and subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == source,
        "RUNNER_CONTEXT",
    )
    changes = subprocess.check_output(
        ["git", "diff", "--name-status", "--no-renames", BASE, source, "--", "src", "tests"],
        text=True,
    ).splitlines()
    require(all(row.startswith("A\t") for row in changes), "SEALED_SOURCE_OR_TEST_CHANGED")


def retain(archive, payload, *, verify_only):
    archive.visibility(True)
    require(
        0 < len(payload) <= 81
        and all(
            path.startswith(PREFIX) and len(body) <= c.m.FILE_LIMIT
            for path, body in payload.items()
        ),
        "PRIVATE_PAYLOAD_BUDGET",
    )
    if archive.ref(REPORT_REF) is None:
        require(not verify_only, "REPORT_MISSING")
        c.b.create(archive, REPORT_REF, payload, c.m.BOOTSTRAP)
    saved, parent = c.b.read(archive, REPORT_REF, set(payload))
    require(parent == c.m.BOOTSTRAP and saved == payload, "PRIVATE_REPORT_BYTES")
    return c.m.sha256(u.canonical({k: c.m.sha256(v) for k, v in sorted(payload.items())}))


def main():
    try:
        require(len(sys.argv) == 3 and sys.argv[1] in {"run", "verify"}, "ARGUMENTS")
        mode, source = sys.argv[1:]
        guarded(source)
        token = os.environ.get("ARCHIVE_WRITE_TOKEN", "")
        require(bool(token), "ARCHIVE_CREDENTIAL")
        objects, bodies = {}, {}

        def factory():
            archive = c.r.CachedArchive(
                c.PacedGitHub(c.m.DESTINATION, token, writable=mode == "run")
            )
            archive.objects, archive.bodies = objects, bodies
            return archive

        summary, payload = build(factory, source)
        digest = retain(factory(), payload, verify_only=mode == "verify")
        safe = {
            window: {
                "cells": {
                    name: {k: v for k, v in row.items() if k != "months"}
                    for name, row in summary[window]["cells"].items()
                },
                "comparisons": summary[window]["comparisons"],
            }
            for window in ("primary", "secondary")
        }
        print(
            json.dumps(
                {
                    "status": "FREQUENCY_CONDITIONAL_RESEARCH_REPLAYED",
                    "mode": mode,
                    "bundle_sha256": digest,
                    "summary": safe,
                    "PIT_verified": False,
                    "performance_admission": False,
                },
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                dict(
                    status="FREQUENCY_EXPERIMENT_BLOCKED",
                    reason=str(error)
                    if re.fullmatch(r"(?:DECISION_ORACLE|FREQUENCY)_[A-Z_0-9]+", str(error))
                    else "UNVERIFIED_RUNTIME_FAILURE",
                    **u.safe_failure_location(error),
                    PIT_verified=False,
                    performance_admission=False,
                )
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
