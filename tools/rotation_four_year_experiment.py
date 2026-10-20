"""Sixteen continuous accounts, with original Engine and independent oracles."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import rotation_four_year_input_archive as inputs_archive  # noqa: E402
from tools import rotation_four_year_matrix as m  # noqa: E402
from tools import rotation_four_year_metrics as metrics  # noqa: E402

a, c, u = inputs_archive.a, inputs_archive.c, inputs_archive.u
q = a.q
INPUT_SOURCE = "ddaac328d7745324ff3aa864ef0fa39ad5d028e7"
STEPS = ("0.1", "1")
CASES = (*m.CASES, *m.SPY_CASES)
PREFIX = "research/verification/rotation_four_year_engine_v1/"
REF = "research-data/rotation-four-year-continuous-engine-v1"
WORKFLOW = ".github/workflows/rotation-four-year-engine.yml"
MAX_RAW, MAX_PARTS, MAX_FILES = 128 * 1024 * 1024, 64, 401


def need(ok, code):
    if not ok:
        raise ValueError("FOUR_YEAR_ENGINE_" + code)


def run_cell(inputs, assumption, case, settings=None):
    need(case in CASES, "CASE")
    engine = (
        m.matrix_engine(inputs, assumption, case, settings)
        if case in m.CASES
        else m.benchmark_engine(inputs, assumption, case, settings)
    )
    oracle = m.MatrixOracle(engine, case) if case in m.CASES else None
    broker, admitted = engine.broker, []
    original = broker.submit_order

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
    signal = oracle.finish() if oracle else None
    need(result["engine"]["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS", "LEDGER")
    fees = q.w.f.order_fee_audit(engine)
    if assumption.share_step < 1:
        result["parent_order_fee_audit"] = fees
    quantum = assumption.share_step
    for fill in result["fills"]:
        units = Decimal(str(fill["quantity"])) / quantum
        need(abs(units - units.to_integral_value()) <= Decimal("1E-9"), "QUANTITY_QUANTUM")
    orders = {order.order_id: order for order in broker.orders}
    for row in admitted:
        order = orders.get(row["order_id"])
        row.update(
            final_status=order.status.value if order else None,
            final_reason=order.rejection_reason if order else None,
            final_code=order._rejection_code if order else None,
        )
    portfolio = q.w.a.portfolio(inputs, assumption, result)
    summary = metrics.path_metrics(portfolio["daily"], assumption.initial_cash)
    notional = sum(abs(fill["quantity"] * fill["price"]) for fill in result["fills"])
    summary.update(
        status="CONDITIONAL_RETROSPECTIVE_ENGINE_AND_LEDGER_PASS",
        share_step=str(quantum),
        initial_cash_usd=str(assumption.initial_cash),
        fills=len(result["fills"]),
        fees_usd=sum(fill["commission"] for fill in result["fills"]),
        dividend_withholding_usd=sum(
            row["withholding"]
            for row in result["corporate_actions"]
            if row["status"] == "dividend_receivable"
        ),
        gross_traded_notional_usd=notional,
        gross_turnover_initial_capital=notional / float(assumption.initial_cash),
        order_statuses=dict(sorted(Counter(order.status.value for order in broker.orders).items())),
        rejection_codes=dict(
            sorted(
                Counter(
                    str(order._rejection_code)
                    for order in broker.orders
                    if order.status.value == "rejected"
                ).items()
            )
        ),
        parent_order_day_groups=len(fees["orders"]),
        partial_fill_groups=sum(row["fill_count"] > 1 for row in fees["orders"]),
        independent_ledger=result["engine"]["independent_ledger"],
        phases=[
            q.w.period(portfolio["daily"], date(2025, 2, 19), date(2025, 4, 8)),
            q.w.period(portfolio["daily"], date(2025, 4, 8), date(2025, 6, 30)),
        ],
        PIT_verified=False,
        performance_admission=False,
    )
    details = {
        "result": result,
        "portfolio": portfolio,
        "signal_oracle": signal,
        "strategy_settings": asdict(engine.strategy.config),
        "assumptions": asdict(assumption),
        "observed_spy_signals": getattr(engine.strategy, "observed_signals", []),
        "strategy_events": list(engine.strategy.events),
        "parent_order_fee_audit": fees,
        "order_admission": admitted,
        "orders": [asdict(o) for o in broker.orders],
        "constraint_audit": [asdict(row) for row in broker.controller.audit],
        "monthly_net_trades": metrics.monthly_net_trades(result["fills"]),
        "summary": summary,
    }
    reference = {day: row["regime"] for day, row in oracle.reference.items()} if oracle else None
    return details, summary, reference


def check_spy_path(inputs, reference, observed):
    last = inputs.bars[-1].end.astimezone(u.NY).date()
    expected = {day: regime for day, regime in reference.items() if day <= last}
    actual = {row["signal_session"]: row["regime"] for row in observed}
    need(len(actual) == len(observed) and actual == expected, "MATCHING_SPY_RISK_PATH")
    return len(actual)


def add_logical(files, index, name, raw):
    need(name not in index and 0 < len(raw) <= MAX_RAW, "LOGICAL_BUDGET")
    packed = gzip.compress(raw, mtime=0)
    parts = [packed[k : k + c.m.FILE_LIMIT] for k in range(0, len(packed), c.m.FILE_LIMIT)]
    need(0 < len(parts) <= MAX_PARTS, "PART_BUDGET")
    paths = []
    for k, body in enumerate(parts):
        path = PREFIX + name + f".gz.part-{k:02}.bin"
        files[path] = body
        paths.append(path)
    index[name] = {
        "parts": paths,
        "compressed_sha256": c.m.sha256(packed),
        "uncompressed_sha256": c.m.sha256(raw),
        "uncompressed_bytes": len(raw),
    }


def matrix(inputs, source, *, input_bundle, settings=None, progress=None):
    files, index, cells, contrasts, risk_checks = {}, {}, {}, {}, {}
    for step in STEPS:
        reference_by_control = {}
        cells[step] = {}
        for case in CASES:
            details, summary, reference = run_cell(inputs, q.e.assumptions(step), case, settings)
            cells[step][case] = summary
            if case in m.CASES:
                control = m.matching_spy(case)
                need(
                    control not in reference_by_control
                    or reference_by_control[control] == reference,
                    "SECTOR_RISK_PATH",
                )
                reference_by_control[control] = reference
            elif case != "SPY_PASSIVE":
                risk_checks[step + ":" + case] = check_spy_path(
                    inputs, reference_by_control[case], details["observed_spy_signals"]
                )
            add_logical(files, index, f"P{step}_{case}.json", u.canonical(details))
            if progress is not None:
                progress(step, case, summary)
        contrasts[step] = {
            "fixed_2x2": metrics.factor_effects(cells[step]),
            "matching_SPY": {
                case: {
                    "control": m.matching_spy(case),
                    "return_delta_pp": cells[step][case]["return_pct"]
                    - cells[step][m.matching_spy(case)]["return_pct"],
                    "annual_return_deltas_pp": [
                        {"year": x["period"], "return_delta_pp": x["return_pct"] - y["return_pct"]}
                        for x, y in zip(
                            cells[step][case]["years"],
                            cells[step][m.matching_spy(case)]["years"],
                            strict=True,
                        )
                    ],
                }
                for case in m.CASES
            },
        }
    comparison = {
        "schema": "rotation_four_year_continuous_experiment_v1",
        "source_commit": source,
        "input_source_commit": INPUT_SOURCE,
        "input_bundle_sha256": input_bundle,
        "execution_first": str(inputs.bars[0].end.astimezone(u.NY).date()),
        "execution_last": str(inputs.bars[-1].end.astimezone(u.NY).date()),
        "accounts": 16,
        "cells": cells,
        "contrasts": contrasts,
        "matching_risk_observations": risk_checks,
        "annual_source_provenance": a.annual.provenance(),
        "one_initial_cash_per_account": True,
        "annual_reset_or_return_splicing": False,
        "anchor_or_parameter_search": False,
        "cash_interest_rate": 0,
        "cash_model": "historical_T2_to_T1_by_trade_date_independent_settlement_reference",
        "terminal_positions_liquidated": False,
        "only_assumed_ordinary_years": [2022],
        "fee_snapshot_backcast": True,
        "new_vendor_requests": 0,
        "new_official_requests": 0,
        "engine_invoked": True,
        "PIT_verified": False,
        "performance_admission": False,
    }
    add_logical(files, index, "comparison.json", u.canonical(comparison))
    files[PREFIX + "manifest.json"] = u.canonical(
        {
            "schema": "rotation_four_year_engine_manifest_v1",
            "source_commit": source,
            "input_source_commit": INPUT_SOURCE,
            "input_bundle_sha256": input_bundle,
            "logical_files": index,
            "files": {p: c.m.sha256(b) for p, b in files.items()},
        }
    )
    need(len(files) <= MAX_FILES, "ARCHIVE_FILE_BUDGET")
    return comparison, files


def retain(archive, files, *, verify_only):
    archive.visibility(True)
    manifest_path = PREFIX + "manifest.json"
    need(
        manifest_path in files
        and 1 < len(files) <= MAX_FILES
        and all(
            path.startswith(PREFIX) and 0 < len(body) <= c.m.FILE_LIMIT
            for path, body in files.items()
        ),
        "ARCHIVE_SCOPE",
    )
    manifest = json.loads(files[manifest_path])
    need(
        set(manifest["logical_files"])
        == {"comparison.json", *(f"P{step}_{case}.json" for step in STEPS for case in CASES)},
        "LOGICAL_SET",
    )
    expected = {manifest_path}
    for name, item in manifest["logical_files"].items():
        paths = item["parts"]
        need(
            0 < len(paths) <= MAX_PARTS
            and paths == [PREFIX + name + f".gz.part-{k:02}.bin" for k in range(len(paths))],
            "PART_PATHS",
        )
        expected.update(paths)
    need(
        expected == set(files)
        and manifest["files"] == {p: c.m.sha256(b) for p, b in files.items() if p != manifest_path},
        "FILE_HASH_SET",
    )
    if archive.ref(REF) is None:
        need(not verify_only, "REPORT_MISSING")
        c.b.create(archive, REF, files, c.m.BOOTSTRAP)
    saved, parent = c.b.read(archive, REF, set(files))
    need(parent == c.m.BOOTSTRAP and saved == files, "FRESH_REPORT_BYTES")
    return c.m.sha256(u.canonical({p: c.m.sha256(b) for p, b in sorted(files.items())}))


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
            os.environ.get(key, "").lower() != "true"
            for key in ("ACTIONS_STEP_DEBUG", "ACTIONS_RUNNER_DEBUG")
        )
        and subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == source,
        "HOSTED_CONTEXT",
    )
    changes = subprocess.check_output(
        ["git", "diff", "--name-status", "--no-renames", INPUT_SOURCE, source], text=True
    ).splitlines()
    need(changes and all(row.startswith("A\t") for row in changes), "FROZEN_INPUT_SOURCE_CHANGED")


def main():
    invoked = False
    try:
        need(len(sys.argv) == 3 and sys.argv[1] in {"run", "verify"}, "ARGUMENTS")
        mode, source = sys.argv[1:]
        guarded(source)
        factory = inputs_archive.Factory(os.environ.get("ARCHIVE_WRITE_TOKEN", ""))
        inputs, normalized, audit = a.build(factory, INPUT_SOURCE)
        need(audit["retrospective_model_inputs_ready"] is True, "INPUTS_NOT_READY")
        bundle = inputs_archive.retain(
            factory(),
            inputs_archive.pack(
                {"inputs.json": normalized, "audit.json": u.canonical(audit)}, INPUT_SOURCE
            ),
            verify_only=True,
        )
        invoked = True

        def progress(step, case, summary):
            print(
                json.dumps(
                    {
                        "status": "FOUR_YEAR_CELL_PASS",
                        "step": step,
                        "case": case,
                        "return_pct": summary["return_pct"],
                        "fills": summary["fills"],
                    }
                ),
                flush=True,
            )

        _, files = matrix(inputs, source, input_bundle=bundle, progress=progress)
        digest = retain(factory(writable=mode == "run"), files, verify_only=mode == "verify")
        print(
            json.dumps(
                {
                    "status": "FOUR_YEAR_16_ACCOUNT_REPLAY_RETAINED",
                    "bundle_sha256": digest,
                    "input_bundle_sha256": bundle,
                    "new_vendor_requests": 0,
                    "engine_invoked": True,
                    "PIT_verified": False,
                    "performance_admission": False,
                }
            ),
            flush=True,
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "FOUR_YEAR_ENGINE_BLOCKED",
                    "reason": c.r.r.code(error),
                    "github_retry_at": getattr(error, "retry_at", None),
                    "engine_invoked": invoked,
                    **u.safe_failure_location(error),
                }
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
