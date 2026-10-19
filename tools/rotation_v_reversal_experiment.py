"""Issue 39 frozen factorial experiment; retained data only, private hosted replay.

Never uses a vendor/broker API. Raw sources and full reports stay private.
All eight cells are reported, including any blocked cell. No parameter search.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ml4t.backtest.strategies.sector_rotation.config import SectorRotationConfig  # noqa: E402
from ml4t.backtest.strategies.sector_rotation.research_ablation import (
    bind_uniform_parent_fees,  # noqa: E402
    experimental_rotation_engine,  # noqa: E402
)
from ml4t.backtest.strategies.sector_rotation.research_fractional_ledger import (
    _FeeReference,  # noqa: E402
)
from ml4t.backtest.strategies.sector_rotation.strategy import SectorRotationStrategy  # noqa: E402
from tools import rotation_stress_tenths_compare as f  # noqa: E402
from tools.rotation_ablation_oracle import AblationOracle  # noqa: E402
from tools.rotation_ablation_spy import timed_spy_engine  # noqa: E402

u, c, a = f.u, f.c, f.a
PREFIX = "research/verification/rotation_v_reversal_factorial_v1/"
REPORT_REF = "research-data/rotation-v-reversal-factorial-v1-engine-report"
WORKFLOW = ".github/workflows/rotation-v-reversal.yml"
BASE = "961bb5d1088bb76734b70d88849d0f0772f0c457"
PIN = "1c4ede59cfe8b3dac140d1f6fa44793acf2feeb2779d51421e5ee3bbd829eb70"
LOGICAL = {
    "comparison.json",
    "primary.json",
    "secondary.json",
    "inputs_primary.json",
    "inputs_secondary.json",
}


def require(ok, code):
    if not ok:
        raise ValueError("V_REVERSAL_" + code)


def load_anchor(archive):
    archive.visibility(True)
    revision = archive.ref(f.REPORT_REF)
    require(revision is not None, "ANCHOR_MISSING")
    commit = archive.request("GET", "/git/commits/" + c.m.sha(revision))
    tree = archive.request("GET", "/git/trees/" + c.m.sha(commit["tree"]["sha"]) + "?recursive=1")
    require(tree.get("truncated") is False, "ANCHOR_TREE")
    paths = {row["path"] for row in tree["tree"] if row["type"] != "tree"}
    require(0 < len(paths) <= 33 and all(p.startswith(f.PREFIX) for p in paths), "ANCHOR_PATHS")
    saved, parent = c.b.read(archive, f.REPORT_REF, paths)
    require(parent == c.m.BOOTSTRAP, "ANCHOR_PARENT")
    require(
        c.m.sha256(u.canonical({k: c.m.sha256(v) for k, v in sorted(saved.items())})) == PIN,
        "ANCHOR_BUNDLE",
    )
    manifest = f.d.decode(saved[f.PREFIX + "manifest.json"])
    require(manifest["source_commit"] == BASE, "ANCHOR_SOURCE")
    require(
        manifest["files"]
        == {k: c.m.sha256(v) for k, v in saved.items() if k != f.PREFIX + "manifest.json"},
        "ANCHOR_HASHES",
    )
    result = {}
    for name, item in manifest["logical_files"].items():
        parts = item["parts"]
        require(
            0 < len(parts) <= 8
            and parts == [f.PREFIX + name + f".gz.part-{i:02}.bin" for i in range(len(parts))],
            "ANCHOR_PARTS",
        )
        packed = b"".join(saved[path] for path in parts)
        require(c.m.sha256(packed) == item["compressed_sha256"], "ANCHOR_COMPRESSED_HASH")
        raw = f.d.g.bounded_gunzip(packed, 64 * 1024 * 1024)
        require(c.m.sha256(raw) == item["uncompressed_sha256"], "ANCHOR_LOGICAL_HASH")
        f.d.decode(raw)
        result[name] = raw
    return result


def assumptions(step, *, primary):
    return f.TenthResearchAssumptions(
        **asdict(f.ASSUMPTIONS if primary else u.ASSUMPTIONS),
        share_step=Decimal(step),
        fractional_fee_policy="parent_order_tiered",
    )


def period(daily, start, end):
    index = {row["date"]: i for i, row in enumerate(daily)}
    if start not in index or end not in index:
        return {"status": "OUTSIDE_WINDOW", "start_close": str(start), "end_close": str(end)}
    left, right = index[start], index[end]
    require(left < right, "PHASE_ORDER")
    rows = daily[left + 1 : right + 1]
    return {
        "status": "PASS",
        "start_close": str(start),
        "end_close": str(end),
        "sessions": right - left,
        "return_pct": (daily[right]["NAV"] / daily[left]["NAV"] - 1) * 100,
        "average_exposure_pct": sum(row["exposure"] for row in rows) / len(rows) * 100,
        "end_exposure_pct": daily[right]["exposure"] * 100,
    }


def fixed_fill_diagnostic(engine):
    old = _FeeReference(Decimal(".1"), "published_per_execution")
    legacy, buys, sells = Decimal(0), Decimal(0), Decimal(0)
    for record in engine.broker.fee_records:
        ctx = record.context
        fill = SimpleNamespace(
            order_id=ctx.order_id,
            timestamp=ctx.timestamp,
            quantity=record.quantity,
            price=record.price,
            side=SimpleNamespace(value=ctx.side.lower()),
        )
        legacy += old.charge(fill, ctx.generation)
        fraction = Decimal(str(record.quantity)) % 1 * Decimal(str(record.price))
        if ctx.side == "BUY":
            buys += fraction
        else:
            sells += fraction
    return {
        "scope": "FIXED_ORIGINAL_FILLS_EX_POST_NOT_EXECUTABLE_NO_REFUNDING",
        "legacy_minus_parent_fees_usd": legacy
        - sum(Decimal(str(r.fees.total_fees)) for r in engine.broker.fee_records),
        "fractional_buy_notional_usd": buys,
        "fractional_sell_notional_usd": sells,
        "NAV_delta_due_to_rounding_identified": False,
    }


def run_cell(inputs, assumption, *, sector=True, risk=True, benchmark=None, settings=None):
    settings = settings or SectorRotationConfig()
    oracle = None
    if benchmark == "passive":
        engine = f.tenth_spy_engine(inputs, assumption)
    elif benchmark == "timed":
        engine = timed_spy_engine(inputs, assumption, settings)
    else:
        engine = experimental_rotation_engine(
            inputs, assumption, settings, sector_rotation=sector, risk_tiers=risk
        )
        oracle = AblationOracle(engine, sector_rotation=sector, risk_tiers=risk)
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
    return metrics, raw


def matrix(inputs, *, primary, settings=None):
    summaries, raw = {}, {}
    for step in ("1", "0.1"):
        assumption = assumptions(step, primary=primary)
        for sector in (False, True):
            for risk in (False, True):
                name = f"P{step}_S{int(sector)}_R{int(risk)}"
                try:
                    summaries[name], raw[name] = run_cell(
                        inputs, assumption, sector=sector, risk=risk, settings=settings
                    )
                except Exception as error:
                    summaries[name] = {
                        "status": "BLOCKED",
                        **u.safe_failure_location(error),
                        "reason": str(error)
                        if re.fullmatch(r"(?:DECISION_ORACLE|V_REVERSAL)_[A-Z_0-9]+", str(error))
                        else "UNVERIFIED_RUNTIME_FAILURE",
                    }
                progress(name, summaries[name], primary)
        for benchmark in ("passive", "timed"):
            name = f"P{step}_SPY_{benchmark}"
            try:
                summaries[name], raw[name] = run_cell(
                    inputs, assumption, benchmark=benchmark, settings=settings
                )
            except Exception as error:
                summaries[name] = {
                    "status": "BLOCKED",
                    **u.safe_failure_location(error),
                    "reason": str(error)
                    if re.fullmatch(r"(?:DECISION_ORACLE|V_REVERSAL)_[A-Z_0-9]+", str(error))
                    else "UNVERIFIED_RUNTIME_FAILURE",
                }
            progress(name, summaries[name], primary)
    interactions = {}
    for step in ("1", "0.1"):
        keys = [f"P{step}_S{s}_R{r}" for s, r in ((1, 1), (0, 1), (1, 0), (0, 0))]
        if all(summaries[key]["status"] == "CONDITIONAL_RESEARCH_PASS" for key in keys):
            nav = [summaries[key]["end_NAV_usd"] for key in keys]
            interactions[step] = {"NAV_R_S_interaction_usd": nav[0] - nav[1] - nav[2] + nav[3]}
    # Existing independent analytics compute net SPY returns, HAC intervals and
    # explicitly ex-post lagged-exposure attribution, never a trading signal.
    for name, entry in raw.items():
        step = name.split("_")[0][1:]
        passive = raw.get(f"P{step}_SPY_passive")
        if passive is None:
            continue
        signals = entry["signal_oracle"] or {"signals": entry["timed_spy_signals"]}
        analytics = a.describe(
            inputs, assumptions(step, primary=primary), entry["result"], passive["result"], signals
        )
        entry["analytics"] = analytics
        summaries[name].update(
            {
                k: analytics[k]
                for k in (
                    "regression",
                    "up_capture",
                    "down_capture",
                    "risk_signal_transitions",
                    "risk_signal_delays",
                    "ex_post_SPY_peak_trough_phases",
                )
            }
        )
        attr = analytics["exposure_matched_attribution"]
        summaries[name]["ex_post_SPY_lagged_exposure_return_pct"] = attr["return_pct"]
    return {"cells": summaries, "interactions": interactions}, raw


def progress(name, result, primary):
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(
            json.dumps(
                {
                    "window": "primary" if primary else "secondary",
                    "cell": name,
                    **{
                        k: v
                        for k, v in result.items()
                        if k in {"status", "reason", "error_type", "location"}
                    },
                }
            ),
            flush=True,
        )


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
            "rotation_v_reversal_experiment.py",
            "rotation_ablation_oracle.py",
            "rotation_ablation_spy.py",
        )
    }
    payload[PREFIX + "manifest.json"] = u.canonical(
        {
            "schema": "rotation_v_reversal_manifest_v1",
            "source_commit": source,
            "prior_bundle_sha256": PIN,
            "operators": hashes,
            "files": {k: c.m.sha256(v) for k, v in sorted(payload.items())},
            "logical_files": index,
        }
    )
    return payload


def build(factory, source):
    anchor = load_anchor(factory())
    inputs, normalized, provenance = f.x.build_inputs(
        factory, f.d.BASELINE_SOURCE, u.pa.ClosePriceAudit()
    )
    require(normalized == anchor["inputs.json"], "NORMALIZED_INPUT_BYTES_CHANGED")
    primary, primary_raw = matrix(inputs, primary=True)
    previous = json.loads(anchor["results.json"])
    require(
        "P0.1_S1_R1" in primary_raw and "P0.1_SPY_passive" in primary_raw, "PRIMARY_CONTROL_BLOCKED"
    )
    for key, name in (("rotation", "P0.1_S1_R1"), ("funded_SPY", "P0.1_SPY_passive")):
        require(
            u.canonical(primary_raw[name]["result"]) == u.canonical(previous["tenths"][key]),
            "CORRECTED_ANCHOR_BYTES_CHANGED",
        )
    # The old whole-share reservation predates the uniform parent-fee policy.
    # Keep it as a separate exact control, not the new P=1 factorial cell.
    legacy_whole = u.engine_result(u.retrospective_rotation_engine(inputs, f.ASSUMPTIONS))
    require(
        u.canonical(legacy_whole) == u.canonical(previous["whole"]["rotation"]),
        "WHOLE_ANCHOR_CHANGED",
    )
    primary_raw["legacy_whole_anchor"] = legacy_whole
    docs = f.x.s.documents(factory())[0]
    second_inputs, second_normalized = u.build_inputs(
        factory, f.d.BASELINE_SOURCE, docs, audit=u.pa.ClosePriceAudit()
    )
    secondary, secondary_raw = matrix(second_inputs, primary=False)
    require(
        all(
            row["status"] == "CONDITIONAL_RESEARCH_PASS"
            for window in (primary, secondary)
            for row in window["cells"].values()
        ),
        "ONE_OR_MORE_CELLS_BLOCKED",
    )
    summary = {
        "schema": "rotation_v_reversal_factorial_v1",
        "source_commit": source,
        "primary_window": ["2024-12-02", "2025-06-30"],
        "secondary_window": ["2025-08-29", "2025-11-05"],
        "primary": primary,
        "secondary": secondary,
        "prior_corrected_engine_bytes_exactly_reproduced": True,
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
                "interactions": summary[window]["interactions"],
            }
            for window in ("primary", "secondary")
        }
        print(
            json.dumps(
                {
                    "status": "V_REVERSAL_CONDITIONAL_RESEARCH_REPLAYED",
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
                    status="V_REVERSAL_EXPERIMENT_BLOCKED",
                    reason=str(error)
                    if re.fullmatch(r"(?:DECISION_ORACLE|V_REVERSAL)_[A-Z_0-9]+", str(error))
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
