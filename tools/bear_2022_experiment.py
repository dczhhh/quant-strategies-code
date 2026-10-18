"""Frozen 2022 frequency stress replay, conditional ordinary-dividend assumption."""

from __future__ import annotations

import gzip
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml4t.backtest.strategies.sector_rotation.research_ablation import (  # noqa: E402
    bind_uniform_parent_fees,
)
from ml4t.backtest.strategies.sector_rotation.research_fractional import (  # noqa: E402
    ResearchAssumptions,
)
from tools import bear_2022_inputs as x  # noqa: E402
from tools import rotation_frequency_experiment as w  # noqa: E402

s, c, u = x.s, x.c, x.u
PREFIX = "research/verification/bear_2022_frequency_v1/"
REPORT_REF = "research-data/bear-2022-frequency-v1-engine-report"
WORKFLOW = ".github/workflows/bear-2022-experiment.yml"
LOGICAL = {"comparison.json", "results.json", "inputs.json"}
MAX_RAW, MAX_PARTS = 96 * 1024 * 1024, 24
MODIFIED_RESEARCH = {
    "src/ml4t/backtest/strategies/sector_rotation/research.py": "a56d06bd62cdab62ada78538be2db5caf843098fddc8abc34e83e9eca5e894be",
    "src/ml4t/backtest/strategies/sector_rotation/research_fractional.py": "596fed581b5a211afa51b8003989cbb43cab5f91851fa848855b44c79d507ade",
}


def assumptions(step):
    base = replace(
        u.ASSUMPTIONS,
        name="owner 10-percent tax plus EXPLICITLY ASSUMED 2022 ordinary classification",
        assumed_ordinary_years=(2022,),
    )
    return ResearchAssumptions(
        **asdict(base),
        share_step=Decimal(step),
        fractional_fee_policy="parent_order_tiered",
    )


def quarters(daily, initial_cash):
    """Calendar boundaries fixed before replay; include the first execution day's PnL."""
    result, before = [], float(initial_cash)
    for quarter in range(1, 5):
        rows = [row for row in daily if (row["date"].month - 1) // 3 + 1 == quarter]
        if not rows:
            continue
        end = rows[-1]["NAV"]
        result.append(
            {
                "quarter": f"2022Q{quarter}",
                "sessions": len(rows),
                "return_pct": (end / before - 1) * 100,
                "end_NAV_usd": end,
                "average_exposure_pct": 100 * sum(row["exposure"] for row in rows) / len(rows),
            }
        )
        before = end
    return result


def run_cell(inputs, assumption, case, settings=None):
    metrics, raw = w.run_cell(inputs, assumption, case, settings)
    # The existing execution/oracle path is reused unchanged. Its old 2025-only
    # presentation fields never participate in Engine inputs and are discarded.
    metrics.pop("phases")
    for n in (5, 10, 20):
        metrics.pop(f"rebound_{n}_sessions")
    metrics["quarters"] = quarters(raw["portfolio"]["daily"], assumption.initial_cash)
    return metrics, raw


def matrix(inputs, *, settings=None):
    summaries, raw, benchmarks = {}, {}, {}
    for step in ("1", "0.1"):
        assumption = assumptions(step)
        for case in w.CASES:
            name = f"P{step}_{case}"
            summaries[name], raw[name] = run_cell(inputs, assumption, case, settings)
        engine = w.f.tenth_spy_engine(inputs, assumption)
        bind_uniform_parent_fees(engine)
        result = u.engine_result(engine)
        benchmarks[step] = w.f.summarize(inputs, assumption, result)
        raw[f"P{step}_funded_SPY"] = {"result": result}
    comparisons = {
        step: {
            case: {
                "net_return_delta_percentage_points": summaries[f"P{step}_{case}"]["return_pct"]
                - summaries[f"P{step}_M"]["return_pct"],
                "drawdown_delta_percentage_points": summaries[f"P{step}_{case}"][
                    "maximum_daily_close_drawdown_pct"
                ]
                - summaries[f"P{step}_M"]["maximum_daily_close_drawdown_pct"],
            }
            for case in w.CASES
            if case != "M"
        }
        for step in ("1", "0.1")
    }
    return {"cells": summaries, "comparisons": comparisons, "funded_SPY": benchmarks}, raw


def pack(logical, source):
    s.need(set(logical) == LOGICAL, "EXPERIMENT_LOGICAL_SET")
    payload, index = {}, {}
    for name, body in sorted(logical.items()):
        s.need(0 < len(body) <= MAX_RAW, "EXPERIMENT_LOGICAL_BUDGET")
        packed = gzip.compress(body, mtime=0)
        parts = []
        for i in range(0, len(packed), c.m.FILE_LIMIT):
            path = PREFIX + name + f".gz.part-{i // c.m.FILE_LIMIT:02}.bin"
            payload[path] = packed[i : i + c.m.FILE_LIMIT]
            parts.append(path)
        s.need(0 < len(parts) <= MAX_PARTS, "EXPERIMENT_PART_BUDGET")
        index[name] = {
            "parts": parts,
            "compressed_sha256": s.digest(packed),
            "uncompressed_sha256": s.digest(body),
            "uncompressed_bytes": len(body),
        }
    payload[PREFIX + "manifest.json"] = s.canonical(
        {
            "schema": "bear_2022_frequency_manifest_v1",
            "source_commit": source,
            "logical_files": index,
            "files": {key: s.digest(body) for key, body in sorted(payload.items())},
        }
    )
    return payload


def build(factory, source):
    inputs, normalized, provenance = x.build_inputs(factory, source)
    results, raw = matrix(inputs)
    summary = {
        "schema": "bear_2022_frequency_assumed_ordinary_v1",
        "source_commit": source,
        "window": [str(s.FIRST), str(s.LAST)],
        "presample_closes_per_asset": 294,
        "execution_sessions": 251,
        "initial_cash_per_cell_usd": 100000,
        "results": results,
        "provenance": provenance,
        "dividend_classification": "ASSUMED_ORDINARY_NOT_FINAL_ISSUER_TAX_EVIDENCE",
        "dividend_tax_rate": "0.10",
        "fee_history_mode": "current_snapshot_backcast",
        "historical_settlement": "T+2",
        "market_api_requests": 0,
        "broker_api_requests": 0,
        "thresholds_optimized": False,
        "strategy_defaults_changed": False,
        "is_blind_holdout": False,
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
    s.need(
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
        "EXPERIMENT_RUNNER_CONTEXT",
    )
    changes = subprocess.check_output(
        ["git", "diff", "--name-status", "--no-renames", x.a.BASE, source, "--", "src", "tests"],
        text=True,
    ).splitlines()
    modified = {row[2:] for row in changes if row.startswith("M\t")}
    s.need(
        modified == set(MODIFIED_RESEARCH)
        and all(
            row.startswith("A\t") or (row.startswith("M\t") and row[2:] in MODIFIED_RESEARCH)
            for row in changes
        )
        and all(
            s.digest((x.ROOT / path).read_bytes()) == expected
            for path, expected in MODIFIED_RESEARCH.items()
        ),
        "EXPERIMENT_SEALED_STRATEGY_OR_TEST_CHANGED",
    )


def retain(archive, payload, *, verify_only):
    archive.visibility(True)
    s.need(
        0 < len(payload) <= 3 * MAX_PARTS + 1
        and all(
            path.startswith(PREFIX) and len(body) <= c.m.FILE_LIMIT
            for path, body in payload.items()
        ),
        "EXPERIMENT_PRIVATE_PAYLOAD_BUDGET",
    )
    if archive.ref(REPORT_REF) is None:
        s.need(not verify_only, "EXPERIMENT_REPORT_MISSING")
        c.b.create(archive, REPORT_REF, payload, c.m.BOOTSTRAP)
    saved, parent = c.b.read(archive, REPORT_REF, set(payload))
    s.need(parent == c.m.BOOTSTRAP and saved == payload, "EXPERIMENT_FRESH_REPORT_BYTES")
    return s.digest(s.canonical({key: s.digest(body) for key, body in sorted(payload.items())}))


def main():
    try:
        s.need(len(sys.argv) == 3 and sys.argv[1] in {"run", "verify"}, "EXPERIMENT_ARGUMENTS")
        mode, source = sys.argv[1:]
        guarded(source)
        token = os.environ.get("ARCHIVE_WRITE_TOKEN", "")
        s.need(bool(token), "EXPERIMENT_ARCHIVE_CREDENTIAL")
        objects, bodies = {}, {}

        def factory(writable=False):
            archive = c.r.CachedArchive(c.PacedGitHub(c.m.DESTINATION, token, writable=writable))
            archive.objects, archive.bodies = objects, bodies
            return archive

        summary, payload = build(factory, source)
        digest = retain(factory(mode == "run"), payload, verify_only=mode == "verify")
        safe = {
            name: {
                key: row[key]
                for key in (
                    "return_pct",
                    "end_NAV_usd",
                    "maximum_daily_close_drawdown_pct",
                    "average_exposure_pct",
                    "fees_usd",
                    "fills",
                    "signal_oracle",
                    "independent_ledger",
                )
            }
            for name, row in summary["results"]["cells"].items()
        }
        print(
            json.dumps(
                {
                    "status": "BEAR2022_CONDITIONAL_RESEARCH_REPLAYED",
                    "mode": mode,
                    "bundle_sha256": digest,
                    "cells": safe,
                    "comparisons": summary["results"]["comparisons"],
                    "funded_SPY": {
                        step: {
                            k: row[k]
                            for k in ("return_pct", "maximum_daily_close_drawdown_pct", "fees_usd")
                        }
                        for step, row in summary["results"]["funded_SPY"].items()
                    },
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
        if re.fullmatch(r"BEAR2022_[A-Z_0-9]+", reason) is None:
            reason = w.failure_reason(error)
        print(
            json.dumps(
                {
                    "status": "BEAR2022_EXPERIMENT_BLOCKED",
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
