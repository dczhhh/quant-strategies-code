"""Frozen continuous-sample preparation; metadata inspection is not Engine admission."""

from __future__ import annotations

from datetime import UTC, date, datetime
from importlib.resources import files
from urllib.parse import urlencode

from quant_constraints.calendar import SessionCalendar, settlement_date

from .archive_contracts import digest
from .demand import DemandError, deny
from .instrument_policy import admit_instrument
from .private_capture_cli import FLAGS
from .private_repo_ingest import _json
from .sample_contracts import security_evidence

PLAN_SHA256 = "2d28389406da6d46ff798ea46ff91927d54ea0607d62b9ebc8b804e7312f0e1a"
INPUT_HASHES = {
    "first_batch_report": "b377d919cacd22ecc2ebdfd09bcba50113b5af6ec1ded49bb19dc917c777ebea",
    "reused_manifest": "4397646e30a23dac019de8319c5fdfc07281f2d436420eda44ab86ae5effe3ad",
    "reused_checks": "5237f6aa8fdfc687dc801e80171ea02201c39254dfab50abf9e67b455a8ad555",
}
PENDING = (
    "fresh_reference_materials_do_not_certify_historical_identity_or_PIT",
    "independent_raw_adjusted_units_volume_and_action_coverage",
    "bounded_aggregate_executor_and_immutable_product_recovery",
    "explicit_exploratory_cash_fee_tax_and_causal_replay_contract",
    "end_of_window_receivables_remain_unsettled",
)


def frozen_plan() -> dict:
    content = files(__package__).joinpath("resources/continuous_batch_v1.json").read_bytes()
    if digest(content) != PLAN_SHA256:
        deny("CONTINUOUS_PLAN_CHANGED")
    return _json(content)


def planned_sessions() -> list[dict]:
    """Exact RTH requests only; never calls a transport or grants a capture permit."""
    plan, calendar = frozen_plan(), SessionCalendar()
    sessions = []
    for row in plan["proposed_slices"]:
        day = date.fromisoformat(row["day"])
        session = calendar.session(day)
        start, end = datetime.fromisoformat(row["start_at"]), datetime.fromisoformat(row["end_at"])
        if (
            session is None
            or row["symbol"] != "AAPL"
            or row["products"] != ["raw", "comparison"]
            or start != session.market_open
            or end != session.market_close
            or start.utcoffset() != UTC.utcoffset(start)
            or row["expected_minutes"] != 390
            or int((end - start).total_seconds()) != 390 * 60
        ):
            deny("CONTINUOUS_SESSION_CONFLICT")
        if sessions and calendar.shift(date.fromisoformat(sessions[-1]["day"]), 1) != day:
            deny("CONTINUOUS_SESSION_GAP")
        first, last = int(start.timestamp() * 1000), int(end.timestamp() * 1000) - 1
        requests = {
            p: f"https://api.massive.com/v2/aggs/ticker/AAPL/range/1/minute/{first}/{last}?"
            + urlencode({"adjusted": str(p == "comparison").lower(), "sort": "asc", "limit": 5000})
            for p in row["products"]
        }
        sessions.append({**row, "request_urls": requests})
    reuse = plan["reuse"]
    if (
        len(sessions) != 9
        or sessions[0]["day"] != "2026-09-28"
        or sessions[-1]["day"] != "2026-10-08"
        or calendar.shift(date.fromisoformat(sessions[-1]["day"]), 1).isoformat() != reuse["day"]
        or reuse["day"] != "2026-10-09"
    ):
        deny("CONTINUOUS_REUSE_BOUNDARY_CONFLICT")
    return sessions


def _metadata(content: bytes, expected: str) -> dict:
    if type(content) is not bytes or not 0 < len(content) <= 32768 or digest(content) != expected:
        deny("CONTINUOUS_METADATA_BINDING_INVALID")
    return _json(content)


def _untrusted(value: dict, clocks: bool = False) -> None:
    if any(value.get(k) is not False for k in FLAGS):
        deny("CONTINUOUS_TRUST_ESCALATION")
    if clocks and value.get("clocks") != dict.fromkeys(
        ("historically_known_at", "provider_available_at", "published_at"), None
    ):
        deny("CONTINUOUS_CLOCK_ESCALATION")


def prepare(inputs: dict[str, bytes]) -> dict:
    """Inspect exact, retrospective metadata copies, without paths/credentials/Engine."""
    if type(inputs) is not dict or set(inputs) != set(INPUT_HASHES):
        deny("CONTINUOUS_METADATA_SET_INVALID")
    report, manifest, checks = (
        _metadata(inputs[key], INPUT_HASHES[key])
        for key in ("first_batch_report", "reused_manifest", "reused_checks")
    )
    _untrusted(report)
    _untrusted(manifest, True)
    _untrusted(checks)
    if (
        report.get("engine_invoked") is not False
        or report.get("strategy_performance_computed") is not False
        or report.get("historical_availability_unknown") is not True
        or report.get("structural_and_case_checks_passed") is not True
        or len(report.get("slices", [])) != 7
        or checks.get("row_counts") != {"raw": 390, "comparison": 390}
        or checks.get("minute_coverage_complete") is not True
        or manifest.get("symbol") != "AAPL"
        or manifest["files"]["checks.json"]["sha256"] != INPUT_HASHES["reused_checks"]
    ):
        deny("CONTINUOUS_METADATA_SCOPE_INVALID")
    sessions, plan = planned_sessions(), frozen_plan()
    days = [date.fromisoformat(s["day"]) for s in sessions] + [date(2026, 10, 9)]
    evidence = security_evidence("AAPL")
    try:
        admit_instrument("AAPL", days[0], PLAN_SHA256, evidence, datetime(2026, 10, 13, tzinfo=UTC))
    except DemandError as error:
        admission_code = error.code
    else:
        # Even if a later version broadens evidence, preparation cannot enable transport.
        admission_code = "CLASSIFICATION_ONLY_NOT_EXECUTION_PERMISSION"
    settlements = [
        {
            "trade_day": day.isoformat(),
            "settlement_day": settlement_date(day).isoformat(),
            "inside_sample_window": settlement_date(day) <= days[-1],
        }
        for day in days
    ]
    return {
        "schema_version": "research_continuous_preparation_audit_v1",
        "status": "PREPARATION_ONLY",
        "plan_sha256": PLAN_SHA256,
        "market_requests": 0,
        "may_acquire_aggregates": False,
        "may_replay": False,
        "engine_invoked": False,
        "strategy_performance_computed": False,
        "source_metadata_is_retrospective": True,
        "historical_availability_unknown": True,
        "new_data_coverage": "not_acquired",
        "planned_trading_sessions": len(days),
        "planned_new_aggregate_gets": plan["proposed_aggregate_budget"]["expected_market_gets"],
        "planned_new_record_upper_bound": sum(s["expected_minutes"] * 2 for s in sessions),
        "reused_day": plan["reuse"]["day"],
        "reused_market_requests": 0,
        "reuse_manifest_sha256": digest(inputs["reused_manifest"]),
        "prior_admission_code": admission_code,
        "prior_cap_basis_age_days": (days[0] - date.fromisoformat(evidence["cap_basis_date"])).days,
        "month_end_crossing": ["2026-09-30", "2026-10-01"],
        "settlements": settlements,
        "end_receivable_policy": plan["account_contract"]["end_receivables"],
        "pending_technical_inputs": list(PENDING),
        **dict.fromkeys(FLAGS, False),
    }


def metadata_bytes(values: dict) -> dict[str, bytes]:
    """Decode only the three fixed text snapshots; the outer envelope is not authority."""
    if (
        type(values) is not dict
        or set(values) != {"schema_version", *INPUT_HASHES}
        or values["schema_version"] != "research_continuous_readback_snapshots_v1"
        or any(type(values[k]) is not str for k in INPUT_HASHES)
    ):
        deny("CONTINUOUS_METADATA_SET_INVALID")
    return {key: values[key].encode() for key in INPUT_HASHES}
