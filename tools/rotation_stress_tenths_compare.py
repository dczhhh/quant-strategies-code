"""Frozen issue-30 stress replay: whole shares versus 0.1 shares, archived privately.

Historical scenario only. No provider API calls, no broker API, no strict PIT admission.
Uses retained source bytes and a newly labelled appendix rather than altering the
verified whole-share v2 report. Fresh verify uses a distinct hash seed.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, replace
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rotation_stress_analytics as a  # noqa: E402
import rotation_stress_inputs as x  # noqa: E402
import rotation_stress_report_diagnostic as d  # noqa: E402

from ml4t.backtest.strategies.sector_rotation.research_fractional import (  # noqa: E402
    ResearchAssumptions as TenthResearchAssumptions,
)
from ml4t.backtest.strategies.sector_rotation.research_fractional import (  # noqa: E402
    retrospective_rotation_engine as tenth_rotation_engine,
)
from ml4t.backtest.strategies.sector_rotation.research_fractional_benchmark import (  # noqa: E402
    retrospective_spy_benchmark as tenth_spy_engine,
)

u, c = x.u, x.c
PREFIX = "research/verification/rotation_stress_202412_202506_tenths_order_fees_v1/"
REPORT_REF = "research-data/rotation-stress-202412-202506-tenths-order-fees-v1-engine-report"
PRIOR_PREFIX = "research/verification/rotation_stress_202412_202506_tenths_v1/"
PRIOR_REF = "research-data/rotation-stress-202412-202506-tenths-v1-engine-report"
PRIOR_SOURCE = "aabaf88f527a586113edd8e1d993e198eb3d8a3f"
PRIOR_BUNDLE = "a14b82c309bba6d57fcd964c49ecca584d9bef4e644ceccf6b013e417013c5bc"
WORKFLOW = ".github/workflows/rotation-stress-tenths-fees.yml"
BASELINE_ROTATION_NAV = Decimal("93770.0737159946")
BASELINE_FUNDED_SPY_NAV = Decimal("103030.43003401748")
ASSUMPTIONS = replace(
    u.ASSUMPTIONS,
    name="owner 10-percent ordinary-US-fund scenario plus EXPLICITLY ASSUMED 2024 ordinary classification",
    assumed_ordinary_years=(2024,),
)


class Case(TypedDict):
    step: str
    rotation: dict[str, Any]
    funded_SPY: dict[str, Any]
    engine: dict[str, Any]


def require(ok, code):
    c.p.need(ok, "TENTHS_FEE_" + code)


class PriorControlMismatch(ValueError):
    def __init__(self, diagnostic):
        super().__init__("TENTHS_FEE_PRIOR_CONTROL_BYTES_CHANGED")
        self.diagnostic = diagnostic


def load_prior(archive):
    """Authenticate only the exact immutable old preview; no write or unknown ref."""
    archive.visibility(True)
    revision = archive.ref(PRIOR_REF)
    require(revision is not None, "PRIOR_REPORT_MISSING")
    commit = archive.request("GET", "/git/commits/" + c.m.sha(revision))
    require(commit is not None, "PRIOR_COMMIT_MISSING")
    tree = archive.request("GET", "/git/trees/" + c.m.sha(commit["tree"]["sha"]) + "?recursive=1")
    require(tree is not None and tree.get("truncated") is False, "PRIOR_TREE_INVALID")
    paths = {row["path"] for row in tree["tree"] if row["type"] != "tree"}
    require(0 < len(paths) <= 17, "PRIOR_PATH_BUDGET")
    names = {"comparison.json", "results.json"}
    for path in paths:
        require(path.startswith(PRIOR_PREFIX), "PRIOR_PATH_INVALID")
        name = path.removeprefix(PRIOR_PREFIX)
        if name != "manifest.json":
            match = re.fullmatch(r"(.+\.json)\.gz\.part-0([0-7])\.bin", name)
            require(match is not None and match[1] in names, "PRIOR_PATH_INVALID")
    saved, parent = c.b.read(archive, PRIOR_REF, paths)
    require(parent == c.m.BOOTSTRAP, "PRIOR_PARENT_CHANGED")
    require(
        c.m.sha256(u.canonical({k: c.m.sha256(v) for k, v in sorted(saved.items())}))
        == PRIOR_BUNDLE,
        "PRIOR_BUNDLE_CHANGED",
    )
    manifest = c.b.decode(saved[PRIOR_PREFIX + "manifest.json"])
    require(manifest["source_commit"] == PRIOR_SOURCE, "PRIOR_SOURCE_CHANGED")
    require(set(manifest["logical_files"]) == names, "PRIOR_LOGICAL_FILES")
    require(
        manifest["files"]
        == {k: c.m.sha256(v) for k, v in saved.items() if k != PRIOR_PREFIX + "manifest.json"},
        "PRIOR_FILE_HASHES",
    )
    logical = {}
    for name, item in manifest["logical_files"].items():
        parts = item["parts"]
        require(
            0 < len(parts) <= 8
            and parts == [PRIOR_PREFIX + name + f".gz.part-{i:02}.bin" for i in range(len(parts))],
            "PRIOR_PART_ORDER",
        )
        packed = b"".join(saved[p] for p in parts)
        require(c.m.sha256(packed) == item["compressed_sha256"], "PRIOR_COMPRESSED_HASH")
        raw = d.g.bounded_gunzip(packed, 64 * 1024 * 1024)
        require(c.m.sha256(raw) == item["uncompressed_sha256"], "PRIOR_LOGICAL_HASH")
        d.decode(raw)  # reject duplicate JSON keys and non-finite numbers
        logical[name] = raw
    return manifest, logical


def prior_check(archive, inputs, normalized, control):
    manifest, saved = load_prior(archive)
    previous = json.loads(saved["comparison.json"])
    require(
        manifest["input_fingerprint"] == inputs.fingerprint
        and previous["input_fingerprint"] == inputs.fingerprint
        and previous["input_sha256"] == c.m.sha256(normalized),
        "PRIOR_INPUT_BYTES_CHANGED",
    )
    current = json.loads(u.canonical(control))
    # The only new legacy-control field is a label, never an economic value.
    for case in ("rotation", "funded_SPY"):
        assumptions = current["tenths"][case]["engine"]["assumptions"]
        require(
            assumptions.pop("fractional_fee_policy", None) == "published_per_execution",
            "CONTROL_POLICY_METADATA",
        )
    rebuilt = u.canonical(current)
    if rebuilt != saved["results.json"]:
        raise PriorControlMismatch(
            {
                "prior_sha256": c.m.sha256(saved["results.json"]),
                "rebuilt_sha256": c.m.sha256(rebuilt),
                **d.differences(d.decode(saved["results.json"]), d.decode(rebuilt)),
            }
        )
    return {"legacy_controls_exactly_reproduced": True, "prior_bundle_sha256": PRIOR_BUNDLE}


def module_hashes():
    names = (
        "quant_constraints.fees.ibkr_fractional_order",
        "quant_constraints.fees.ibkr_tiered",
        "ml4t.backtest.strategies.sector_rotation.research_fractional",
        "ml4t.backtest.strategies.sector_rotation.research_fractional_ledger",
        "ml4t.backtest.strategies.sector_rotation.research_fractional_benchmark",
        "ml4t.backtest.strategies.sector_rotation.strategy",
        "quant_constraints.research.data.verification",
    )
    hashes = {}
    for name in names:
        file = import_module(name).__file__
        require(file is not None, "MODULE_SOURCE_MISSING")
        assert file is not None
        hashes[name] = c.m.sha256(Path(file).read_bytes().replace(b"\r\n", b"\n"))
    for module in (a, x, d, sys.modules[__name__]):
        file = module.__file__
        require(file is not None, "OPERATOR_SOURCE_MISSING")
        assert file is not None
        hashes[Path(file).name] = c.m.sha256(Path(file).read_bytes().replace(b"\r\n", b"\n"))
    return hashes


def guarded(source):
    c.m.sha(source)
    require(
        os.environ.get("GITHUB_REPOSITORY") == c.m.CODE
        and os.environ.get("GITHUB_ACTOR") == "dczhhh"
        and os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_WORKFLOW_REF") == c.m.CODE + "/" + WORKFLOW + "@refs/heads/main"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("RUNNER_OS") == "Linux"
        and os.environ.get("ACTIONS_STEP_DEBUG", "").lower() != "true"
        and os.environ.get("ACTIONS_RUNNER_DEBUG", "").lower() != "true"
        and subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == source,
        "REVIEWED_RUNNER_CONTEXT",
    )
    allowed = {
        "src/quant_constraints/fees/ibkr_fractional_order.py",
        "src/ml4t/backtest/strategies/sector_rotation/research_fractional.py",
        "src/ml4t/backtest/strategies/sector_rotation/research_fractional_ledger.py",
        "src/ml4t/backtest/strategies/sector_rotation/research_fractional_benchmark.py",
    }
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", PRIOR_SOURCE, source, "--", "src"], text=True
    ).splitlines()
    require(set(changed) <= allowed, "SEALED_CORE_SOURCE_CHANGED")
    tests = subprocess.check_output(
        ["git", "diff", "--name-status", "--no-renames", PRIOR_SOURCE, source, "--", "tests"],
        text=True,
    ).splitlines()
    require(all(line.startswith("A\t") for line in tests), "SEALED_ORIGINAL_TEST_CHANGED")


def order_fee_audit(engine):
    """Private simulated parent-order attribution, not actual-account reconciliation."""
    groups: dict[Any, dict[str, Any]] = {}
    fields = (
        "broker_commission",
        "exchange_ecn_fees_or_rebates",
        "clearing_fees",
        "regulatory_fees",
        "pass_through_fees",
        "sec_fees",
        "taf_fees",
        "cat_fees",
    )
    for record in engine.broker.fee_records:
        context = record.context
        key = context.bucket
        if key not in groups:
            groups[key] = {
                "order_id": context.order_id,
                "generation": context.generation,
                "day": str(key[2]),
                "asset": context.asset,
                "side": context.side,
                "fill_count": 0,
                "quantity": Decimal(0),
                "notional_usd": Decimal(0),
                "components_usd": {field: Decimal(0) for field in fields},
                "commission_cap_applied": False,
                "executions": [],
            }
        row = groups[key]
        row["fill_count"] += 1
        quantity, price = Decimal(str(record.quantity)), Decimal(str(record.price))
        row["quantity"] += quantity
        row["notional_usd"] += quantity * price
        for field in fields:
            row["components_usd"][field] += Decimal(str(getattr(record.fees, field)))
        row["commission_cap_applied"] |= record.fees.commission_cap_applied
        row["executions"].append(asdict(record))
    parents = []
    for key in sorted(groups):
        row = groups[key]
        row["modeled_broker_minimum_usd"] = "0.35"
        row["modeled_broker_cap_usd"] = row["notional_usd"] / 100
        row["total_fees_usd"] = sum(row["components_usd"][field] for field in fields[:5])
        parents.append(row)
    total = sum((row["total_fees_usd"] for row in parents), Decimal(0))
    observed = sum((Decimal(str(fill.commission)) for fill in engine.broker.fills), Decimal(0))
    require(abs(total - observed) <= Decimal("1E-8"), "PARENT_FEE_REPORT_MISMATCH")
    return {
        "scope": "simulated_parent_order_not_account_statement",
        "orders": parents,
        "total_fees_usd": total,
        "actual_account_reconciliation": False,
    }


def replay(inputs, assumptions, *, fractional=False):
    rotation_engine = tenth_rotation_engine if fractional else u.retrospective_rotation_engine
    spy_engine = tenth_spy_engine if fractional else u.retrospective_spy_benchmark
    rotation, spy = rotation_engine(inputs, assumptions), spy_engine(inputs, assumptions)
    r, b = u.engine_result(rotation), u.engine_result(spy)
    if fractional and assumptions.fractional_fee_policy == "parent_order_tiered":
        r["parent_order_fee_audit"] = order_fee_audit(rotation)
        b["parent_order_fee_audit"] = order_fee_audit(spy)
    return r, b


def summarize(inputs, assumptions, result) -> dict[str, Any]:
    port = a.portfolio(inputs, assumptions, result)
    fills = result["fills"]
    quantum = getattr(assumptions, "share_step", Decimal("1"))
    for fill in fills:
        units = Decimal(str(fill["quantity"])) / quantum
        require(
            abs(units - units.to_integral_value()) <= Decimal("1E-9"),
            "FILL_NOT_ON_QUANTUM",
        )
    require(
        result["engine"]["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS",
        "CANONICAL_LEDGER_FAILED",
    )
    return {
        "return_pct": (port["end_NAV_usd"] / float(assumptions.initial_cash) - 1) * 100,
        "end_NAV_usd": port["end_NAV_usd"],
        "end_cash_usd": port["end_cash_usd"],
        "end_receivables_usd": port["end_receivables_usd"],
        "end_positions": port["end_holdings_weights"],
        "annualized_daily_volatility_pct": port["annualized_daily_volatility_pct"],
        "maximum_daily_close_drawdown_pct": a.drawdown(
            [point["NAV"] for point in port["daily"]], float(assumptions.initial_cash)
        ),
        "average_exposure_pct": 100
        * sum(point["exposure"] for point in port["daily"])
        / len(port["daily"]),
        "fills": len(fills),
        "fractional_fills": sum(Decimal(str(fill["quantity"])) % 1 != 0 for fill in fills),
        "fees_usd": sum(float(fill["commission"]) for fill in fills),
        "months": [
            {
                "month": row["month"],
                "return_pct": row["return_pct"],
                "fills": row["fills"],
                "fees_usd": row["commission_usd"],
                "cash_usd": row["cash_usd"],
                "holdings": row["end_holdings_weights"],
            }
            for row in port["months"]
        ],
        "asset_net_pnl_usd": port["asset_net_pnl_usd"],
        "independent_ledger": result["engine"]["independent_ledger"],
    }


def build(archive_factory, source, *, input_builder=x.build_inputs, prior_checker=prior_check):
    require(source and len(source) == 40, "SOURCE_SHA_INVALID")
    audit = u.pa.ClosePriceAudit()
    inputs, normalized, provenance = input_builder(archive_factory, d.BASELINE_SOURCE, audit)
    require(
        inputs.bundle_schema == "rotation_retrospective_inputs_v3_assumed_distribution",
        "INPUT_SCHEMA_CHANGED",
    )
    cases: dict[str, Case] = {}
    for tag, step in (("whole", Decimal("1")), ("legacy_tenths", Decimal("0.1"))):
        assumptions = (
            TenthResearchAssumptions(
                **asdict(ASSUMPTIONS),
                share_step=step,
                fractional_fee_policy="published_per_execution",
            )
            if tag == "legacy_tenths"
            else ASSUMPTIONS
        )
        rotation, funded = replay(inputs, assumptions, fractional=tag == "legacy_tenths")
        cases[tag] = {
            "step": str(step),
            "rotation": summarize(inputs, assumptions, rotation),
            "funded_SPY": summarize(inputs, assumptions, funded),
            "engine": {"rotation": rotation, "funded_SPY": funded},
        }
    old_control = {
        "schema": "rotation_stress_tenths_raw_engine_v1",
        "input_fingerprint": inputs.fingerprint,
        "assumptions": asdict(ASSUMPTIONS),
        "whole": cases["whole"]["engine"],
        "tenths": cases["legacy_tenths"]["engine"],
    }
    control_check = prior_checker(archive_factory(), inputs, normalized, old_control)
    require(control_check["legacy_controls_exactly_reproduced"], "OLD_CONTROL_NOT_REPRODUCED")
    assumptions = TenthResearchAssumptions(
        **asdict(ASSUMPTIONS),
        share_step=Decimal("0.1"),
        fractional_fee_policy="parent_order_tiered",
    )
    rotation, funded = replay(inputs, assumptions, fractional=True)
    cases["tenths"] = {
        "step": "0.1",
        "rotation": summarize(inputs, assumptions, rotation),
        "funded_SPY": summarize(inputs, assumptions, funded),
        "engine": {"rotation": rotation, "funded_SPY": funded},
    }
    require(
        abs(Decimal(str(cases["whole"]["rotation"]["end_NAV_usd"])) - BASELINE_ROTATION_NAV)
        <= Decimal("1E-8"),
        "PINNED_V2_ROTATION_CHANGED",
    )
    require(
        abs(Decimal(str(cases["whole"]["funded_SPY"]["end_NAV_usd"])) - BASELINE_FUNDED_SPY_NAV)
        <= Decimal("1E-8"),
        "PINNED_V2_BENCHMARK_CHANGED",
    )
    whole, tenths = cases["whole"], cases["tenths"]
    base, test = whole["rotation"], tenths["rotation"]
    reference_base, reference_test = whole["funded_SPY"], tenths["funded_SPY"]
    summary = {
        "schema": "rotation_stress_tenths_order_fee_comparison_v1",
        "source_commit": source,
        "input_fingerprint": inputs.fingerprint,
        "input_sha256": c.m.sha256(normalized),
        "execution_window": ["2024-12-02", "2025-06-30"],
        "initial_cash_usd": "100000",
        "scenario": "2024_DISTRIBUTION_ASSUMED_ORDINARY_RESEARCH_ONLY",
        "tax_rate": str(ASSUMPTIONS.dividend_tax_rate),
        "real_price_archive_reused": True,
        "market_api_requests": 0,
        "broker_api_requests": 0,
        "signals_reoptimized": False,
        "baseline_v2_reproduced_to_usd_1e_minus_8": True,
        "prior_whole_and_legacy_tenths_engine_reports_exactly_reproduced": True,
        "prior_bundle_sha256": PRIOR_BUNDLE,
        "parent_order_fee_policy": "account_observed_research_scenario_not_universal_tariff",
        "fractional_shares_are_commission_free": False,
        "complete_account_commission_reconciliation": False,
        "DRIP_fee_model": False,
        "performance_admission": False,
        "full_real_scenarios_accepted": 0,
        "PIT_verified": False,
        "IBKR_fractional_order_eligibility_verified": False,
        "strict_real_admission": False,
        "fractional_execution_is_simulated": True,
        "independent_fractional_signal_oracle": False,
        "whole": {k: v for k, v in whole.items() if k != "engine"},
        "tenths": {k: v for k, v in tenths.items() if k != "engine"},
        "legacy_tenths_percent_fee_control": {
            k: v for k, v in cases["legacy_tenths"].items() if k != "engine"
        },
        "delta_corrected_minus_legacy_tenths": {
            "rotation_return_percentage_points": test["return_pct"]
            - cases["legacy_tenths"]["rotation"]["return_pct"],
            "rotation_fees_usd": test["fees_usd"] - cases["legacy_tenths"]["rotation"]["fees_usd"],
            "funded_SPY_return_percentage_points": reference_test["return_pct"]
            - cases["legacy_tenths"]["funded_SPY"]["return_pct"],
            "note": "fee reservations can alter subsequent fills; not a pure ex-post fee subtraction",
        },
        "delta_tenths_minus_whole": {
            "rotation_return_percentage_points": test["return_pct"] - base["return_pct"],
            "rotation_end_NAV_usd": test["end_NAV_usd"] - base["end_NAV_usd"],
            "rotation_end_cash_usd": test["end_cash_usd"] - base["end_cash_usd"],
            "rotation_max_drawdown_percentage_points": (
                test["maximum_daily_close_drawdown_pct"] - base["maximum_daily_close_drawdown_pct"]
            ),
            "rotation_fees_usd": test["fees_usd"] - base["fees_usd"],
            "funded_SPY_return_percentage_points": (
                reference_test["return_pct"] - reference_base["return_pct"]
            ),
        },
        "provenance_note": provenance["strict_2024_classification_status"],
        "price_audit_note": (
            "daily-versus-minute differences remain uncorrected; no new vendor quotes"
        ),
    }
    # Store full underlying results privately; public logs contain aggregate metrics only.
    logical = {
        "comparison.json": u.canonical(summary),
        "results.json": u.canonical(
            {
                "schema": "rotation_stress_tenths_order_fee_engine_v1",
                "input_fingerprint": inputs.fingerprint,
                "assumptions": asdict(ASSUMPTIONS),
                "whole": whole["engine"],
                "tenths": tenths["engine"],
                "legacy_tenths_percent_fee_control": cases["legacy_tenths"]["engine"],
            }
        ),
        "inputs.json": normalized,
        "provenance.json": u.canonical(
            provenance
            | {
                "prior_control_verification": control_check,
                "modules_sha256": module_hashes(),
                "personal_account_rows_included": False,
                "fee_scope": "ordinary USD stock/ETF parent-order research scenario; no DRIP or universal tariff certification",
            }
        ),
    }
    payload = {}
    index = {}
    for name, content in logical.items():
        compressed = gzip.compress(content, mtime=0)
        parts = []
        for i in range(0, len(compressed), c.m.FILE_LIMIT):
            dest = PREFIX + name + f".gz.part-{i // c.m.FILE_LIMIT:02}.bin"
            payload[dest] = compressed[i : i + c.m.FILE_LIMIT]
            parts.append(dest)
        require(len(parts) > 0 and len(parts) <= 8, "REPORT_SIZE_LIMIT")
        index[name] = {
            "uncompressed_sha256": c.m.sha256(content),
            "compressed_sha256": c.m.sha256(compressed),
            "parts": parts,
        }
    payload[PREFIX + "manifest.json"] = u.canonical(
        {
            "schema": "rotation_stress_tenths_order_fee_manifest_v1",
            "source_commit": source,
            "baseline_v2_sha": d.BASELINE_SOURCE,
            "input_fingerprint": inputs.fingerprint,
            "prior_bundle_sha256": PRIOR_BUNDLE,
            "modules_sha256": module_hashes(),
            "files": {k: c.m.sha256(v) for k, v in payload.items()},
            "logical_files": index,
        }
    )
    return summary, payload


def retain_report(archive, payload, *, verify_only=False):
    archive.visibility(True)
    require(
        0 < len(payload) <= 33 and PREFIX + "manifest.json" in payload, "NEW_REPORT_PATH_BUDGET"
    )
    for path, body in payload.items():
        require(path.startswith(PREFIX) and len(body) <= c.m.FILE_LIMIT, "NEW_REPORT_PATH_OR_SIZE")
    if archive.ref(REPORT_REF) is None:
        require(not verify_only, "PRIVATE_REPORT_MISSING")
        c.b.create(archive, REPORT_REF, payload, c.m.BOOTSTRAP)
    saved, parent = c.b.read(archive, REPORT_REF, set(payload))
    require(parent == c.m.BOOTSTRAP and saved == payload, "PRIVATE_REPORT_BYTE_MISMATCH")
    return c.m.sha256(u.canonical({k: c.m.sha256(v) for k, v in sorted(payload.items())}))


def main():
    try:
        require(
            len(sys.argv) == 3 and sys.argv[1] in {"run", "verify"},
            "ARGUMENTS",
        )
        mode, source = sys.argv[1:]
        guarded(source)
        token = os.environ.get("ARCHIVE_WRITE_TOKEN", "")
        require(bool(token), "ARCHIVE_CREDENTIAL_MISSING")

        def archive_factory():
            return c.r.CachedArchive(
                c.PacedGitHub(c.m.DESTINATION, token, writable=(mode == "run"))
            )

        summary, payload = build(archive_factory, source)
        digest = retain_report(archive_factory(), payload, verify_only=mode == "verify")
        print(
            json.dumps(
                {
                    "status": "TENTHS_ORDER_FEE_CONDITIONAL_RESEARCH_VERIFIED",
                    "mode": mode,
                    "bundle_sha256": digest,
                    "summary": {
                        k: summary[k]
                        for k in (
                            "execution_window",
                            "whole",
                            "tenths",
                            "legacy_tenths_percent_fee_control",
                            "delta_corrected_minus_legacy_tenths",
                            "delta_tenths_minus_whole",
                            "prior_whole_and_legacy_tenths_engine_reports_exactly_reproduced",
                            "prior_bundle_sha256",
                            "fractional_shares_are_commission_free",
                            "complete_account_commission_reconciliation",
                            "performance_admission",
                            "full_real_scenarios_accepted",
                            "PIT_verified",
                            "IBKR_fractional_order_eligibility_verified",
                        )
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "TENTHS_ORDER_FEE_STRESS_BLOCKED",
                    "reason": str(exc)
                    if re.fullmatch(r"TENTHS_FEE_[A-Z_0-9]+", str(exc))
                    else "UNVERIFIED_RUNTIME_FAILURE",
                    **u.safe_failure_location(exc),
                    **(
                        {"diagnostic": exc.diagnostic}
                        if isinstance(exc, PriorControlMismatch)
                        else {}
                    ),
                    "PIT_verified": False,
                    "performance_admission": False,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
