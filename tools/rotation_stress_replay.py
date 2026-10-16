"""Fixed Issue #30 conditional stress replay, private reports and fresh byte-for-byte verification."""

from __future__ import annotations

import gzip
import json
import os
import sys
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rotation_decision_oracle as o  # noqa: E402
import rotation_stress_analytics as a  # noqa: E402
import rotation_stress_inputs as x  # noqa: E402
import rotation_stress_report_diagnostic as d  # noqa: E402

g, s, c, u = x.g, x.s, x.c, x.u
PREFIX = "research/verification/rotation_stress_202412_202506_v2/"
REPORT_REF = "research-data/rotation-stress-202412-202506-v2-engine-report"
ASSUMPTIONS = replace(
    u.ASSUMPTIONS,
    name="owner 10-percent ordinary-US-fund scenario plus EXPLICITLY ASSUMED 2024 ordinary classification",
    assumed_ordinary_years=(2024,),
)


def run_pair(inputs, assumptions, progress):
    engine = u.retrospective_rotation_engine(inputs, assumptions)
    oracle = o.DecisionOracle(engine)
    progress["engine_invoked"] = True
    rotation = u.engine_result(engine)
    decision = oracle.finish()
    funded = u.engine_result(u.retrospective_spy_benchmark(inputs, assumptions))
    decision["close_sensitivity"] = o.conditional_close_sensitivity(inputs, oracle)
    return rotation, funded, decision


def compressed_payload(logical, *, prefix=PREFIX):
    """Deterministic bounded parts; even a longer normalized minute bundle stays private."""
    payload, index = {}, {}
    for name, raw in logical.items():
        compressed = gzip.compress(raw, mtime=0)
        names = []
        for part, start in enumerate(range(0, len(compressed), c.m.FILE_LIMIT)):
            path = prefix + name + f".gz.part-{part:02}.bin"
            payload[path] = compressed[start : start + c.m.FILE_LIMIT]
            names.append(path)
        c.p.need(len(names) <= 8, "STRESS_REPORT_LOGICAL_FILE_TOO_LARGE")
        index[name] = {
            "uncompressed_sha256": c.m.sha256(raw),
            "compressed_sha256": c.m.sha256(compressed),
            "parts": names,
        }
    return payload, index


def module_hashes():
    result = {}
    for module in (o, a, x, d, sys.modules[__name__]):
        path = module.__file__
        c.p.need(path is not None, "STRESS_REPORT_MODULE_SOURCE_MISSING")
        assert path is not None
        result[Path(path).name] = c.m.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n"))
    return result


def compute_report(
    archive_factory,
    source,
    *,
    progress=None,
    input_builder=x.build_inputs,
    scenario=ASSUMPTIONS,
):
    progress = progress if progress is not None else {"engine_invoked": False}
    archive = archive_factory()
    archive.visibility(True)
    audit = u.pa.ClosePriceAudit()
    # Retain the original assumption document's actual publication identity;
    # a formatter-only code revision must not retag the frozen input fingerprint.
    inputs, normalized, provenance = input_builder(archive_factory, d.BASELINE_SOURCE, audit)
    rotation, funded, decisions = run_pair(inputs, scenario, progress)
    analytics = a.describe(inputs, scenario, rotation, funded, decisions)
    diagnostics = audit.finish(
        inputs, {"rotation": rotation, "funded_spy": funded}, scenario.initial_cash
    )
    variants, sensitivity = {}, []
    for rate in (Decimal(0), Decimal(".30")):
        assumptions = replace(
            scenario,
            name=scenario.name + " FIXED_TAX_SENSITIVITY_" + str(rate),
            dividend_tax_rate=rate,
        )
        strategy, benchmark, oracle = run_pair(inputs, assumptions, progress)
        key = str(rate)
        variants[key] = {
            "assumptions": asdict(assumptions),
            "rotation": strategy,
            "funded_SPY": benchmark,
            "decision_oracle": oracle,
        }
        sensitivity.append(
            {"tax_rate": key, "rotation": strategy["summary"], "funded_SPY": benchmark["summary"]}
        )
    summary = {
        "status": "RETROSPECTIVE_STRESS_CONDITIONAL_RESEARCH_PASS",
        "execution_start": str(s.FIRST),
        "execution_end": str(s.LAST),
        "execution_sessions": len(c.sessions(s.FIRST, s.LAST)),
        "initial_model_cash_usd": str(scenario.initial_cash),
        "share_quantum": str(scenario.share_step),
        "scenario": "REAL_RETAINED_PRICES_WITH_EXPLICIT_2024_ORDINARY_CLASSIFICATION_ASSUMPTION",
        "classification_2024_real_source_verified": False,
        "ordinary_2024_assumption_source": x.ASSUMPTION_PATH,
        "strict_classifier_status": provenance["strict_2024_classification_status"],
        "event_completeness": provenance["event_checks"],
        "rotation": rotation["summary"],
        "funded_SPY": funded["summary"],
        "rotation_portfolio": {
            k: v
            for k, v in analytics["rotation"].items()
            if k not in {"daily", "months", "asset_net_pnl_usd"}
        },
        "funded_SPY_portfolio": {
            k: v
            for k, v in analytics["funded_SPY"].items()
            if k not in {"daily", "months", "asset_net_pnl_usd"}
        },
        "rotation_minus_funded_SPY_percentage_points": rotation["summary"]["return_pct"]
        - funded["summary"]["return_pct"],
        "rotation_months": analytics["rotation"]["months"],
        "funded_SPY_months": analytics["funded_SPY"]["months"],
        "rotation_asset_net_pnl_usd": analytics["rotation"]["asset_net_pnl_usd"],
        "exposure_attribution": {
            k: v for k, v in analytics["exposure_matched_attribution"].items() if k != "daily"
        },
        "regression": analytics["regression"],
        "up_capture": analytics["up_capture"],
        "down_capture": analytics["down_capture"],
        "phases": analytics["ex_post_SPY_peak_trough_phases"],
        "risk_signal_transitions": analytics["risk_signal_transitions"],
        "risk_signal_delays": analytics["risk_signal_delays"],
        "decision_oracle": {
            k: decisions[k]
            for k in (
                "status",
                "conditioned_on_verified_past_engine_ledger",
                "independent_fill_simulator",
                "all_unsubmitted_opportunities_verified",
                "signal_observations",
                "monthly_decisions",
                "submitted_order_intents",
                "risk_calls",
                "settlement_blocks",
                "observed_regimes",
            )
        },
        "price_source_diagnostics": diagnostics["summary"],
        "conditional_signal_close_sensitivity": decisions["close_sensitivity"]["summary"],
        "tax_sensitivity_not_parameter_optimization": sensitivity,
        "new_vendor_requests": 0,
        "new_official_requests": 0,
        "settlement_calendar_mode": "bounded_bank_holiday_reference_not_NSCC_attestation",
        "PIT_verified": False,
        "actual_broker_execution_verified": False,
        "performance_admission": False,
        "full_real_scenarios_accepted": 0,
    }
    result = {
        "schema": "rotation_stress_result_v2",
        "source_commit": source,
        "summary": summary,
        "assumptions": asdict(scenario),
        "rotation": rotation,
        "funded_SPY": funded,
        "tax_sensitivity": variants,
    }
    logical = {
        "inputs.json": normalized,
        "result.json": u.canonical(result),
        "decisions.json": u.canonical(decisions),
        "analytics.json": u.canonical(analytics),
        "price-audit.json": u.canonical(diagnostics),
        "provenance.json": u.canonical(provenance),
    }
    return archive, logical, result, provenance, inputs


def execute(
    archive_factory,
    source,
    *,
    verify_only=False,
    progress=None,
    input_builder=x.build_inputs,
    diagnostic=False,
    baseline_checker=None,
):
    archive, logical, result, provenance, inputs = compute_report(
        archive_factory, source, progress=progress, input_builder=input_builder
    )
    summary = result["summary"]
    comparison = (baseline_checker or d.compare_to_baseline)(archive, logical, source)
    comparison["rebuilt_modules_sha256"] = module_hashes()
    if diagnostic:
        c.p.need(verify_only, "DIAGNOSTIC_MUST_BE_READ_ONLY")
        return comparison
    if not comparison["semantics_match"]:
        raise d.ReportDivergence(comparison)
    summary["prior_v1_exact_input_and_economic_values_match"] = True
    summary["prior_v1_reproducibility_diagnostic"] = {
        "baseline_bundle_sha256": comparison.get("baseline_bundle_sha256"),
        "zero_text_ambiguity_witness": comparison.get("zero_text_ambiguity_witness"),
        "declared_change_counts_by_file": {
            name: record["declared_change_counts"]
            for name, record in comparison.get("logical_files", {}).items()
            if record["declared_change_counts"]
        },
    }
    provenance["prior_v1_comparison"] = comparison
    logical["provenance.json"] = u.canonical(provenance)
    logical["result.json"] = u.canonical(result)
    payload, index = compressed_payload(logical)
    payload[PREFIX + "manifest.json"] = u.canonical(
        {
            "schema": "rotation_stress_report_manifest_v2",
            "source_commit": source,
            "input_fingerprint": inputs.fingerprint,
            "input_schema": inputs.bundle_schema,
            "input_assumption_source_revision": d.BASELINE_SOURCE,
            "prior_v1_bundle_sha256": d.BASELINE_BUNDLE,
            "capture_plan_sha256": g.PLAN_HASH,
            "inventory_bundle_sha256": g.INVENTORY_HASH,
            "modules_sha256": module_hashes(),
            "logical_files": index,
            "files": {name: c.m.sha256(body) for name, body in payload.items()},
        }
    )
    if archive.ref(REPORT_REF) is None:
        c.p.need(not verify_only, "STRESS_REPORT_MISSING")
        c.b.create(archive, REPORT_REF, payload, c.m.BOOTSTRAP)
    saved, parent = c.b.read(archive, REPORT_REF, set(payload))
    c.p.need(parent == c.m.BOOTSTRAP and saved == payload, "INDEPENDENT_STRESS_REPORT_CONFLICT")
    return summary | {
        "report_bundle_sha256": c.m.sha256(
            u.canonical({k: c.m.sha256(v) for k, v in sorted(payload.items())})
        )
    }


def main():
    progress = {"engine_invoked": False}
    try:
        c.p.need(
            len(sys.argv) == 3 and sys.argv[1] in {"run", "verify", "diagnose"}, "ARGUMENTS_INVALID"
        )
        mode, source = sys.argv[1:]
        c.guarded(source)

        def factory():
            return c.r.CachedArchive(
                c.PacedGitHub(
                    c.m.DESTINATION,
                    os.environ.get("ARCHIVE_WRITE_TOKEN", ""),
                    writable=mode == "run",
                )
            )

        report = execute(
            factory,
            source,
            verify_only=mode != "run",
            progress=progress,
            diagnostic=mode == "diagnose",
        )
        if mode == "diagnose":
            report["diagnostic_operator_source_commit"] = source
        print(json.dumps(report))
        return int(mode == "diagnose" and not report["semantics_match"])
    except Exception as error:
        reason = (
            "DECISION_ORACLE_MISMATCH"
            if isinstance(error, o.DecisionOracleMismatch)
            else "BASELINE_STRESS_SEMANTICS_CHANGED"
            if isinstance(error, d.ReportDivergence)
            else c.r.r.code(error)
        )
        print(
            json.dumps(
                {
                    "status": "STRESS_REPLAY_BLOCKED",
                    "reason": reason,
                    "engine_invoked": progress["engine_invoked"],
                    "performance_admission": False,
                    **u.safe_failure_location(error),
                    **(
                        {"diagnostic": error.diagnostic}
                        if isinstance(error, d.ReportDivergence)
                        else {}
                    ),
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
