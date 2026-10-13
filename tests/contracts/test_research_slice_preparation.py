"""Execution preparation cannot mutate an accepted plan or self-register real I/O."""

import json
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from quant_constraints.research.data import demand_capture as capture
from quant_constraints.research.data.demand import DemandError
from validation import check_research_slice_preparation as preparation

ROOT = Path(__file__).resolve().parents[2]


def inputs():
    return (
        (ROOT / preparation.PACKET_PATH).read_bytes(),
        (ROOT / preparation.PLAN_PATH).read_bytes(),
        {path: (ROOT / path).read_bytes() for path in preparation.SOURCE_SHA256},
    )


def evaluate_change(field, value):
    encoded, plan, sources = inputs()
    packet = json.loads(encoded)
    packet[field] = value
    return preparation.evaluate(json.dumps(packet).encode(), plan, sources)


def test_complete_pending_packet_is_deterministic_but_not_execution_approval():
    before = inputs()
    report = preparation.evaluate(*before)
    assert report == preparation.evaluate(*before)
    assert report["status"] == "PREPARATION_ONLY"
    assert report["code"] == "EXECUTION_REVIEW_PENDING"
    assert report["license_determination"] == "not_performed"
    assert not report["may_acquire"]
    assert all(report[flag] is False for flag in preparation.FLAGS)
    assert inputs() == before
    assert capture._ACCEPTED_EXECUTION_REVIEWS == ()


@pytest.mark.parametrize(
    "field,value",
    [
        ("approved", True),
        ("status", "approved"),
        ("accepted_implementation_commit", "a" * 40),
        ("development_review_id", True),
        ("development_review_id", float(preparation.REVIEW)),
        ("development_review_id", preparation.REVIEW + 1),
        ("plan_sha256", "b" * 64),
        ("owner_scope", "all_market"),
        ("credential_secret_name", "DO_NOT_PRINT_TOKEN"),
        ("execution_environment", "public_ci"),
        ("max_requests", 9),
        ("max_source_bytes", 524289),
        ("max_total_bytes", 4194305),
        ("retention_days", 31),
        ("retention_status", "approved"),
        ("public_sources", ["https://api.massive.com/?apiKey=DO_NOT_PRINT_TOKEN"]),
        ("pending_inputs", []),
        ("account_evidence_ref", "self_claim"),
        ("license_evidence_ref", "self_claim"),
        ("root_path_sha256", "c" * 64),
        ("root_identity_sha256", "d" * 64),
        ("root_acl_evidence_ref", "/DO_NOT_PRINT_PRIVATE"),
        ("deletion_policy_ref", "self_claim"),
        ("reviewed_execution_sha256", "e" * 64),
        ("valid_until", "2027-01-01T00:00:00+00:00"),
    ],
)
def test_scope_or_self_grant_cannot_turn_preparation_into_permission(field, value):
    report = evaluate_change(field, value)
    assert report["status"] == "FAIL_CLOSED"
    assert report["code"] == "PACKET_SCOPE_OR_STATUS_MISMATCH"
    assert not report["may_acquire"]
    assert "DO_NOT_PRINT" not in json.dumps(report)


@pytest.mark.parametrize("change", ["symbol", "window", "product", "key", "method", "limit"])
def test_initial_requests_are_exact_not_arbitrary_network_targets(change):
    encoded, plan, sources = inputs()
    packet = json.loads(encoded)
    request = packet["requests"][0]
    if change == "symbol":
        request["url"] = request["url"].replace("AAPL", "NFLX")
    elif change == "window":
        request["url"] = request["url"].replace("1791552600000", "1791552540000")
    elif change == "product":
        request["url"] = request["url"].replace("adjusted=false", "adjusted=true")
    elif change == "key":
        request["url"] += "&apiKey=DO_NOT_PRINT_TOKEN"
    elif change == "method":
        request["method"] = "POST"
    else:
        request["url"] = request["url"].replace("limit=5000", "limit=50000")
    report = preparation.evaluate(json.dumps(packet).encode(), plan, sources)
    assert report["code"] == "PACKET_SCOPE_OR_STATUS_MISMATCH"
    assert "DO_NOT_PRINT" not in json.dumps(report)


@pytest.mark.parametrize("which", ["plan", *preparation.SOURCE_SHA256])
def test_byte_drift_cannot_update_accepted_fingerprints(which):
    encoded, plan, sources = inputs()
    if which == "plan":
        plan += b" "
    else:
        sources[which] += b"\n"
    report = preparation.evaluate(encoded, plan, sources)
    assert report["code"] == (
        "PLAN_BINDING_MISMATCH" if which == "plan" else "IMPLEMENTATION_BINDING_MISMATCH"
    )


@pytest.mark.parametrize(
    "encoded",
    [b"", b" " * 32769, None, b"{", b"[]", b"true"],
    ids=["empty", "oversize", "wrong_type", "truncated_json", "array", "boolean"],
)
def test_invalid_packet_bounds_and_json_stop_with_safe_report(encoded):
    _, plan, sources = inputs()
    report = preparation.evaluate(encoded, plan, sources)
    assert report["status"] == "FAIL_CLOSED" and not report["may_acquire"]


def test_duplicate_fields_cannot_hide_an_approval_or_replace_metadata():
    encoded, plan, sources = inputs()
    encoded = b'{"status":"approved",' + encoded.lstrip()[1:]
    assert preparation.evaluate(encoded, plan, sources)["code"] == "PACKET_INVALID"


def test_missing_inputs_extra_source_and_large_source_never_hash_to_permission():
    encoded, plan, sources = inputs()
    assert (
        preparation.evaluate(encoded, cast(Any, None), sources)["code"] == "PLAN_BINDING_MISMATCH"
    )
    assert preparation.evaluate(encoded, b" " * 32769, sources)["code"] == "PLAN_BINDING_MISMATCH"
    for value in cast(tuple[Any, ...], (None, {}, {**sources, "private_path": b"DO_NOT_PRINT"})):
        assert (
            preparation.evaluate(encoded, plan, value)["code"] == "IMPLEMENTATION_BINDING_MISMATCH"
        )
    for value in (None, b" " * 131073):
        altered = cast(Any, {**sources, next(iter(sources)): value})
        assert (
            preparation.evaluate(encoded, plan, altered)["code"]
            == "IMPLEMENTATION_BINDING_MISMATCH"
        )


def test_packet_evaluation_is_pure_and_real_entry_still_stops_before_external_io(monkeypatch):
    data = inputs()

    def forbidden(*args, **kwargs):
        raise AssertionError("No environment, private path, credential or network access")

    with monkeypatch.context() as guard:
        guard.setattr(type(capture.os.environ), "get", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(capture, "build_opener", forbidden)
        assert preparation.evaluate(*data)["status"] == "PREPARATION_ONLY"
        with pytest.raises(DemandError, match="^EXECUTION_SCOPE_REVIEW_PENDING:review$"):
            capture.capture_real_slice(
                data[1], Path("/DO_NOT_OPEN_PRIVATE"), reviewed_execution_sha256="a" * 64
            )
    assert capture._ACCEPTED_EXECUTION_REVIEWS == ()


def test_main_reads_only_fixed_repo_inputs_and_reports_missing_files_safely(monkeypatch, capsys):
    assert preparation.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "PREPARATION_ONLY" and not report["may_acquire"]

    def missing(*args, **kwargs):
        raise OSError("DO_NOT_PRINT_PRIVATE")

    monkeypatch.setattr(preparation, "_read", missing)
    assert preparation.main() == 1
    output = capsys.readouterr().out
    assert "DO_NOT_PRINT" not in output
    assert json.loads(output)["code"] == "REPOSITORY_INPUT_UNREADABLE"


def test_repository_read_is_bounded_and_ci_preserves_capture_boundary(tmp_path):
    path = tmp_path / "metadata.json"
    path.write_bytes(b"x" * 100)
    assert len(preparation._read(path, 10)) == 11
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert "issue-5f1-demand-archive" in ci[True]["pull_request"]["branches"]
    commands = "\n".join(step.get("run", "") for step in ci["jobs"]["lint"]["steps"])
    assert "check_research_slice_preparation.py" in commands
    assert "secrets.massive" not in commands and "capture_real_slice" not in commands
    assert (
        "check_research_slice_preparation.py"
        in (ROOT / "validation/release_checks.txt").read_text()
    )


def test_preparation_case_ids_fit_windows_test_environment(request: pytest.FixtureRequest):
    # Pytest exports each complete node ID in PYTEST_CURRENT_TEST on every OS.
    cases = [item.nodeid for item in request.session.items if item.path == Path(__file__).resolve()]
    assert cases and max(map(len, cases)) < 1024
