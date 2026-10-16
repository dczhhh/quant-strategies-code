"""Separate integer/tenth-share stress comparison; retained private sources only."""

from __future__ import annotations

import importlib
import json
import os
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rotation_stress_replay as r  # noqa: E402

c, u, d = r.c, r.u, r.d
PREFIX = "research/verification/rotation_stress_202412_202506_tenths_v1/"
REPORT_REF = "research-data/rotation-stress-202412-202506-tenths-v1-engine-report"
FEE_POLICY = "parent_order_tiered"


def integer_baseline_check(archive, logical, source):
    """Remove only NEW default-step metadata, never numbers or financial leaf values."""
    result = d.decode(logical["result.json"])
    count = 0

    def visit(node, parent=None):
        nonlocal count
        if isinstance(node, dict):
            if "share_step" in node:
                c.p.need(
                    parent == "assumptions" and node["share_step"] == "1",
                    "INTEGER_STEP_METADATA_CONFLICT",
                )
                del node["share_step"]
                count += 1
            for key, value in node.items():
                visit(value, key)
        elif isinstance(node, list):
            for value in node:
                visit(value, parent)

    visit(result)
    # Preserve original numeric JSON token types. d.decode is only for validation;
    # a plain decode/re-encode keeps the existing canonical float spelling.
    plain = json.loads(logical["result.json"])

    def remove(node):
        if isinstance(node, dict):
            node.pop("share_step", None)
            for value in node.values():
                remove(value)
        elif isinstance(node, list):
            for value in node:
                remove(value)

    remove(plain)
    checked = logical | {"result.json": u.canonical(plain)}
    comparison = d.compare_to_baseline(archive, checked, source)
    comparison["declared_default_share_step_metadata_fields"] = count
    if not comparison["semantics_match"]:
        raise d.ReportDivergence(comparison)
    return comparison


def module_hashes():
    modules = (
        "ml4t.backtest.strategies.sector_rotation.research",
        "ml4t.backtest.strategies.sector_rotation.research_ledger",
        "ml4t.backtest.strategies.sector_rotation.research_benchmark",
        "ml4t.backtest.strategies.sector_rotation.strategy",
        "quant_constraints.fees.ibkr_tiered",
        "quant_constraints.gates",
        "quant_constraints.models",
    )
    hashes = r.module_hashes()
    for name in modules:
        path = importlib.import_module(name).__file__
        c.p.need(path is not None, "FRACTIONAL_MODULE_SOURCE_MISSING")
        assert path is not None
        hashes[name] = c.m.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n"))
    hashes[Path(__file__).name] = c.m.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n"))
    return hashes


def execute(
    archive_factory,
    source,
    *,
    verify_only=False,
    progress=None,
    input_builder=r.x.build_inputs,
    baseline_checker=integer_baseline_check,
):
    progress = progress if progress is not None else {"engine_invoked": False}
    archive, integer, int_result, provenance, int_inputs = r.compute_report(
        archive_factory, source, progress=progress, input_builder=input_builder
    )
    baseline = baseline_checker(archive, integer, source)
    c.p.need(baseline["semantics_match"], "INTEGER_BASELINE_VALUES_CHANGED")
    _, tenths, tenth_result, _, tenth_inputs = r.compute_report(
        archive_factory,
        source,
        progress=progress,
        input_builder=input_builder,
        scenario=replace(r.ASSUMPTIONS, share_step=Decimal("0.1")),
    )
    c.p.need(
        integer["inputs.json"] == tenths["inputs.json"]
        and int_inputs.fingerprint == tenth_inputs.fingerprint,
        "FRACTIONAL_INPUT_BYTES_CONFLICT",
    )
    whole, tenth = int_result["summary"], tenth_result["summary"]
    summary = {
        "status": "TENTH_SHARE_CONDITIONAL_COMPARISON_PASS",
        "integer_baseline_input_and_economic_values_match_original": True,
        "input_bytes_exactly_equal_between_share_modes": True,
        "execution_sessions": whole["execution_sessions"],
        "integer": whole,
        "tenths": tenth,
        "fee_policy_integer": "retained_published_snapshot_whole_share_baseline",
        "fee_policy_tenths": FEE_POLICY,
        "fee_policy_is_explicit_account_observed_scenario_not_universal_tariff": True,
        "entire_historical_account_commission_reconciled": False,
        "DRIP_commissions_modeled": False,
        "PIT_verified": False,
        "actual_broker_execution_verified": False,
        "performance_admission": False,
        "full_real_scenarios_accepted": 0,
        "new_vendor_requests": 0,
        "new_official_requests": 0,
    }
    logical = {"inputs.json": integer["inputs.json"]}
    for name in ("decisions.json", "analytics.json", "price-audit.json"):
        logical[name] = u.canonical(
            {"integer": json.loads(integer[name]), "tenths": json.loads(tenths[name])}
        )
    logical["result.json"] = u.canonical(
        {
            "schema": "rotation_fractional_stress_comparison_v1",
            "source_commit": source,
            "summary": summary,
            "integer": int_result,
            "tenths": tenth_result,
        }
    )
    logical["provenance.json"] = u.canonical(
        provenance
        | {
            "prior_integer_comparison": baseline,
            "modules_sha256": module_hashes(),
            "fractional_fee_scope": {
                "policy": FEE_POLICY,
                "minimum_and_cap_unit": "parent order/day/successful-amendment generation",
                "fractional_external_venue_proxy": "zero internalized scenario; not zero total fees",
                "ordinary_cash_dividends_not_DRIP": True,
                "official_disclosure": "https://www.interactivebrokers.com/en/pricing/commissions-stocks.php",
                "internalization_source": "https://ibkrguides.com/orgportal/internalization.htm",
                "known_conflict": "published fractional percent footnote is retained separately; account observations do not certify a universal replacement tariff",
                "actual_account_rows_included": False,
                "historical_rates_verified": False,
            },
        }
    )
    payload, index = r.compressed_payload(logical, prefix=PREFIX)
    payload[PREFIX + "manifest.json"] = u.canonical(
        {
            "schema": "rotation_fractional_stress_manifest_v1",
            "source_commit": source,
            "input_fingerprint": int_inputs.fingerprint,
            "input_assumption_source_revision": d.BASELINE_SOURCE,
            "prior_v1_bundle_sha256": d.BASELINE_BUNDLE,
            "modules_sha256": module_hashes(),
            "logical_files": index,
            "files": {name: c.m.sha256(body) for name, body in payload.items()},
        }
    )
    if archive.ref(REPORT_REF) is None:
        c.p.need(not verify_only, "FRACTIONAL_STRESS_REPORT_MISSING")
        c.b.create(archive, REPORT_REF, payload, c.m.BOOTSTRAP)
    saved, parent = c.b.read(archive, REPORT_REF, set(payload))
    c.p.need(
        parent == c.m.BOOTSTRAP and saved == payload,
        "INDEPENDENT_FRACTIONAL_STRESS_REPORT_CONFLICT",
    )
    return summary | {
        "report_bundle_sha256": c.m.sha256(
            u.canonical({k: c.m.sha256(v) for k, v in sorted(payload.items())})
        )
    }


def main():
    progress = {"engine_invoked": False}
    try:
        c.p.need(len(sys.argv) == 3 and sys.argv[1] in {"run", "verify"}, "ARGUMENTS_INVALID")
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

        print(json.dumps(execute(factory, source, verify_only=mode == "verify", progress=progress)))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "FRACTIONAL_STRESS_REPLAY_BLOCKED",
                    "reason": "BASELINE_STRESS_SEMANTICS_CHANGED"
                    if isinstance(error, d.ReportDivergence)
                    else c.r.r.code(error),
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
