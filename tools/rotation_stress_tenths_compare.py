"""Frozen issue-30 stress replay: whole shares versus 0.1 shares, archived privately.

Historical scenario only. No provider API calls, no broker API, no strict PIT admission.
Uses retained source bytes and a newly labelled appendix rather than altering the
verified whole-share v2 report. Fresh verify uses a distinct hash seed.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

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
PREFIX = "research/verification/rotation_stress_202412_202506_tenths_v1/"
REPORT_REF = "research-data/rotation-stress-202412-202506-tenths-v1-engine-report"
BASELINE_ROTATION_NAV = Decimal("93770.0737159946")
BASELINE_FUNDED_SPY_NAV = Decimal("103030.43003401748")
ASSUMPTIONS = replace(
    u.ASSUMPTIONS,
    name="owner 10-percent ordinary-US-fund scenario plus EXPLICITLY ASSUMED 2024 ordinary classification",
    assumed_ordinary_years=(2024,),
)


def require(ok, code):
    c.p.need(ok, "TENTHS_" + code)


def replay(inputs, assumptions, *, fractional=False):
    rotation_engine = tenth_rotation_engine if fractional else u.retrospective_rotation_engine
    spy_engine = tenth_spy_engine if fractional else u.retrospective_spy_benchmark
    r = u.engine_result(rotation_engine(inputs, assumptions))
    b = u.engine_result(spy_engine(inputs, assumptions))
    return r, b


def summarize(inputs, assumptions, result):
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


def build(archive_factory, source):
    require(source and len(source) == 40, "SOURCE_SHA_INVALID")
    audit = u.pa.ClosePriceAudit()
    inputs, normalized, provenance = x.build_inputs(archive_factory, d.BASELINE_SOURCE, audit)
    require(
        inputs.bundle_schema == "rotation_retrospective_inputs_v3_assumed_distribution",
        "INPUT_SCHEMA_CHANGED",
    )
    cases = {}
    for tag, step in (("whole", Decimal("1")), ("tenths", Decimal("0.1"))):
        assumptions = (
            TenthResearchAssumptions(**asdict(ASSUMPTIONS), share_step=step)
            if tag == "tenths"
            else ASSUMPTIONS
        )
        rotation, funded = replay(inputs, assumptions, fractional=tag == "tenths")
        cases[tag] = {
            "step": str(step),
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
        "schema": "rotation_stress_tenths_comparison_v1",
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
        "PIT_verified": False,
        "IBKR_fractional_order_eligibility_verified": False,
        "strict_real_admission": False,
        "fractional_execution_is_simulated": True,
        "independent_fractional_signal_oracle": False,
        "whole": {k: v for k, v in whole.items() if k != "engine"},
        "tenths": {k: v for k, v in tenths.items() if k != "engine"},
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
                "schema": "rotation_stress_tenths_raw_engine_v1",
                "input_fingerprint": inputs.fingerprint,
                "assumptions": asdict(ASSUMPTIONS),
                "whole": whole["engine"],
                "tenths": tenths["engine"],
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
            "schema": "rotation_stress_tenths_manifest_v1",
            "source_commit": source,
            "baseline_v2_sha": d.BASELINE_SOURCE,
            "input_fingerprint": inputs.fingerprint,
            "files": {k: c.m.sha256(v) for k, v in payload.items()},
            "logical_files": index,
        }
    )
    return summary, payload


def main():
    try:
        require(
            len(sys.argv) == 3 and sys.argv[1] in {"run", "verify"},
            "ARGUMENTS",
        )
        mode, source = sys.argv[1:]
        require(os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted", "HOSTED_ONLY")
        token = os.environ.get("ARCHIVE_WRITE_TOKEN", "")
        require(bool(token), "ARCHIVE_CREDENTIAL_MISSING")

        def archive_factory():
            return c.r.CachedArchive(
                c.PacedGitHub(c.m.DESTINATION, token, writable=(mode == "run"))
            )

        summary, payload = build(archive_factory, source)
        archive = archive_factory()
        archive.visibility(True)
        if archive.ref(REPORT_REF) is None:
            require(mode == "run", "PRIVATE_REPORT_MISSING")
            c.b.create(archive, REPORT_REF, payload, c.m.BOOTSTRAP)
        saved, parent = c.b.read(archive, REPORT_REF, set(payload))
        require(
            parent == c.m.BOOTSTRAP and saved == payload,
            "PRIVATE_REPORT_BYTE_MISMATCH",
        )
        digest = c.m.sha256(u.canonical({k: c.m.sha256(v) for k, v in sorted(payload.items())}))
        print(
            json.dumps(
                {
                    "status": "TENTHS_CONDITIONAL_RESEARCH_VERIFIED",
                    "mode": mode,
                    "bundle_sha256": digest,
                    "summary": {
                        k: summary[k]
                        for k in (
                            "execution_window",
                            "whole",
                            "tenths",
                            "delta_tenths_minus_whole",
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
                    "status": "TENTHS_STRESS_BLOCKED",
                    "error_type": type(exc).__name__,
                    "reason": str(exc)[:160],
                    "PIT_verified": False,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
