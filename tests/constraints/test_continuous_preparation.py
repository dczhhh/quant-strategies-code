"""Metadata copies and invented transport/Git fixtures; zero real Market requests in CI."""

import io
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

from quant_constraints.research.data import continuous_materials as materials
from quant_constraints.research.data import continuous_preparation as preparation
from quant_constraints.research.data.archive_contracts import canonical, digest
from quant_constraints.research.data.demand import DemandError
from tests.constraints.test_private_samples import GitFixture

ROOT = Path(__file__).resolve().parents[2]


def inputs():
    value = json.loads(
        (ROOT / "docs/examples/research-continuous-inputs-synthetic-v1.json").read_text()
    )
    return preparation.metadata_bytes(value)


@pytest.fixture(autouse=True)
def invented_metadata_bindings():
    """Exercise the same checks using invented bytes; preserve production fingerprints."""
    hashes = {key: digest(content) for key, content in inputs().items()}
    with patch.dict(preparation.INPUT_HASHES, hashes, clear=True):
        yield


def test_exact_plan_metadata_and_censored_settlement_are_read_only(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    data = inputs()
    before = deepcopy(data)
    result = preparation.prepare(data)
    assert data == before and result["status"] == "PREPARATION_ONLY"
    assert result["market_requests"] == result["reused_market_requests"] == 0
    assert result["planned_new_aggregate_gets"] == 18
    assert result["planned_trading_sessions"] == 10
    assert result["planned_new_record_upper_bound"] == 7020
    assert result["prior_admission_code"] == "INSTRUMENT_SCOPE_OR_IDENTITY_CONFLICT"
    assert result["prior_cap_basis_age_days"] == 549
    assert result["settlements"][-1] == {
        "trade_day": "2026-10-09",
        "settlement_day": "2026-10-13",
        "inside_sample_window": False,
    }
    assert result["settlements"][4]["settlement_day"] == "2026-10-05"
    assert result["settlements"][2]["settlement_day"] == "2026-10-01"
    assert result["new_data_coverage"] == "not_acquired"
    assert not result["may_acquire_aggregates"] and not result["may_replay"]
    assert all(result[k] is False for k in materials.FLAGS)
    sessions = preparation.planned_sessions()
    assert sum(len(s["request_urls"]) for s in sessions) == 18
    assert all(
        "AAPL/range/1/minute/" in u and "apiKey" not in u
        for s in sessions
        for u in s["request_urls"].values()
    )
    plan = preparation.frozen_plan()
    assert plan["materials"]["max_requests"] == 2
    assert plan["proposed_aggregate_budget"]["max_market_attempts"] == 20
    assert plan["reuse"]["market_requests"] == 0


@pytest.mark.parametrize("key", preparation.INPUT_HASHES)
@pytest.mark.parametrize(
    "bad",
    [b"", b" ", b"x" * 32769, None, "not bytes"],
    ids=["empty", "whitespace", "oversize", "missing", "wrong_type"],
)
def test_wrong_bounded_metadata_never_produces_readiness(key, bad):
    data = inputs()
    data[key] = bad
    with pytest.raises(DemandError, match="METADATA_BINDING"):
        preparation.prepare(data)


@pytest.mark.parametrize("data", [{}, None, {"first_batch_report": b"x"}])
def test_missing_metadata_set(data):
    with pytest.raises(DemandError, match="METADATA_SET"):
        preparation.prepare(data)


@pytest.mark.parametrize(
    "key,field,value",
    [
        ("first_batch_report", "engine_invoked", True),
        ("first_batch_report", "strategy_performance_computed", True),
        ("first_batch_report", "structural_and_case_checks_passed", False),
        ("first_batch_report", "historical_availability_unknown", False),
        ("first_batch_report", "slices", []),
        ("reused_manifest", "symbol", "GME"),
        ("reused_checks", "row_counts", {"raw": True, "comparison": 390}),
        ("reused_checks", "minute_coverage_complete", False),
        ("reused_manifest", "clocks", {"published_at": "invented"}),
        ("reused_checks", "PIT_verified", True),
    ],
)
def test_semantic_scope_and_trust_fail_closed_even_in_invented_version(
    key, field, value, monkeypatch
):
    data = inputs()
    changed = json.loads(data[key])
    changed[field] = value
    data[key] = canonical(changed)
    monkeypatch.setitem(preparation.INPUT_HASHES, key, digest(data[key]))
    with pytest.raises(DemandError):
        preparation.prepare(data)


def test_checks_hash_and_future_classification_never_enable_execution(monkeypatch):
    data = inputs()
    changed = json.loads(data["reused_manifest"])
    changed["files"]["checks.json"]["sha256"] = "f" * 64
    data["reused_manifest"] = canonical(changed)
    monkeypatch.setitem(
        preparation.INPUT_HASHES, "reused_manifest", digest(data["reused_manifest"])
    )
    with pytest.raises(DemandError, match="METADATA_SCOPE"):
        preparation.prepare(data)
    monkeypatch.undo()
    monkeypatch.setattr(preparation, "admit_instrument", lambda *_: {})
    result = preparation.prepare(inputs())
    assert not result["may_acquire_aggregates"] and not result["may_replay"]


@pytest.mark.parametrize(
    "change", ["symbol", "products", "start", "minutes", "gap", "reuse", "length"]
)
def test_calendar_and_exact_continuity_are_not_just_row_counts(change, monkeypatch):
    plan = preparation.frozen_plan()
    if change == "symbol":
        plan["proposed_slices"][0]["symbol"] = "GME"
    elif change == "products":
        plan["proposed_slices"][0]["products"] = ["raw"]
    elif change == "start":
        plan["proposed_slices"][0]["start_at"] = "2026-09-28T12:30:00+00:00"
    elif change == "minutes":
        plan["proposed_slices"][0]["expected_minutes"] = 210
    elif change == "gap":
        plan["proposed_slices"].pop(1)
    elif change == "reuse":
        plan["reuse"]["day"] = "2026-10-12"
    else:
        plan["proposed_slices"] = []
    monkeypatch.setattr(preparation, "frozen_plan", lambda: plan)
    with pytest.raises(DemandError):
        preparation.planned_sessions()


def test_resource_tamper_and_metadata_envelope_rejected(monkeypatch):
    monkeypatch.setattr(preparation, "PLAN_SHA256", "f" * 64)
    with pytest.raises(DemandError, match="PLAN_CHANGED"):
        preparation.frozen_plan()
    for data in (
        None,
        {},
        {"schema_version": True},
        {
            "schema_version": "research_continuous_readback_snapshots_v1",
            **dict.fromkeys(preparation.INPUT_HASHES, None),
        },
    ):
        with pytest.raises(DemandError):
            preparation.metadata_bytes(data)


def provider():
    return {
        "status": "OK",
        "request_id": "invented_reference",
        "results": {
            "ticker": "AAPL",
            "cik": "0000320193",
            "type": "CS",
            "market": "stocks",
            "locale": "us",
            "currency_name": "usd",
            "primary_exchange": "XNAS",
            "active": True,
            "market_cap": 100_000_000_000,
        },
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("ticker", "GME"),
        ("cik", "0001326380"),
        ("type", "ETF"),
        ("market", "crypto"),
        ("active", 1),
        ("currency_name", "hkd"),
        ("primary_exchange", "XNYS"),
        ("market_cap", True),
        ("market_cap", None),
        ("market_cap", float("nan")),
        ("market_cap", float("inf")),
        ("market_cap", 10**1000),
        ("market_cap", 10_499_999_999),
    ],
)
def test_reference_identity_and_cap_are_fixed_not_owner_category_wildcards(field, value):
    body = provider()
    body["results"][field] = value
    with pytest.raises(DemandError):
        materials.reference_facts(json.dumps(body).encode())


@pytest.mark.parametrize(
    "field,value",
    [("status", "ERROR"), ("results", None), ("request_id", "bad request"), ("next_url", None)],
)
def test_reference_schema_pagination_and_bounds(field, value):
    body = provider()
    body[field] = value
    with pytest.raises(DemandError):
        materials.reference_facts(canonical(body))
    for content in (None, b"", b"x" * (materials.FILE_LIMIT + 1)):
        with pytest.raises(DemandError):
            materials.reference_facts(content)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    env = {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "GITHUB_REPOSITORY": materials.REPOSITORY,
        "GITHUB_ACTOR": "dczhhh",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": materials.EXECUTION_REF,
        "GITHUB_SHA": "a" * 40,
        "MASSIVE_CONTINUOUS_REVIEWED_SHA": "a" * 40,
        "MASSIVE_CONTINUOUS_ENABLED": "true",
        "QUANT_CONTINUOUS_ROOT": str(root),
        "MASSIVE_API_KEY": "fixture_provider_key",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(materials, "_ROOT_BINDING", None)
    monkeypatch.setattr(
        materials, "_root_details", lambda p: {"path": str(p), "inode": p.stat().st_ino}
    )
    github = GitFixture()
    calls, problem = [], {"kind": None}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        @property
        def status(self):
            return 429 if problem["kind"] == "status" else 200

        def read(self, count):
            assert count == materials.FILE_LIMIT + 1
            return (
                b" " * count
                if problem["kind"] == "size"
                else canonical(provider()).replace(
                    b"invented_reference",
                    b"fixture_provider_key" if problem["kind"] == "key" else b"invented_reference",
                )
            )

    class Transport:
        def open(self, request, timeout):
            assert (
                request.full_url == preparation.frozen_plan()["materials"]["url"] and timeout == 20
            )
            calls.append(request.full_url)
            if problem["kind"] == "http":
                raise HTTPError(
                    request.full_url, 403, "PRIVATE_DO_NOT_LOG", {}, io.BytesIO(b"secret")
                )
            if problem["kind"] == "transport":
                raise URLError("PRIVATE_DO_NOT_LOG")
            return Response()

    monkeypatch.setattr(materials, "GitHub", lambda: github)
    monkeypatch.setattr(materials, "build_opener", lambda *_: Transport())
    return root, github, calls, problem


def fresh(runtime, tmp_path, monkeypatch):
    root = tmp_path / "fresh"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("QUANT_CONTINUOUS_ROOT", str(root))
    monkeypatch.setattr(materials, "_ROOT_BINDING", None)
    return root


def test_one_actual_fixture_request_immutable_git_and_new_vm_zero_get(
    runtime, tmp_path, monkeypatch, capsys
):
    root, github, calls, _ = runtime
    assert materials.main(["preflight"]) == 0
    assert materials.main(["capture"]) == 0
    assert materials.main(["verify"]) == 0
    refs = deepcopy(github.refs)
    before = materials.read_materials(github)
    fresh(runtime, tmp_path, monkeypatch)
    monkeypatch.delenv("MASSIVE_API_KEY")
    assert materials.main(["capture"]) == 0 and materials.main(["verify"]) == 0
    assert len(calls) == 1 and github.refs == refs and materials.read_materials(github) == before
    assert len(before[1]) == 3
    out = capsys.readouterr().out
    assert '"cache_reused":true' in out and '"market_requests":0' in out
    assert "fixture_" not in out and str(root) not in out and "100000000000" not in out


@pytest.mark.parametrize("kind", ["http", "transport", "status", "size", "key"])
def test_failed_requests_are_reserved_not_automatically_retried(
    runtime, tmp_path, monkeypatch, kind
):
    root, github, calls, problem = runtime
    problem["kind"] = kind
    assert materials.main(["capture"]) == 1 and len(calls) == 1
    assert materials.main(["capture"]) == 1 and len(calls) == 1
    fresh(runtime, tmp_path, monkeypatch)
    assert materials.main(["capture"]) == 1 and len(calls) == 2
    assert materials.main(["capture"]) == 1 and len(calls) == 2
    assert len(github.refs) == 2
    assert all("attempt-" in ref for ref in github.refs)
    third = tmp_path / "third_vm"
    third.mkdir(mode=0o700)
    monkeypatch.setenv("QUANT_CONTINUOUS_ROOT", str(third))
    monkeypatch.setattr(materials, "_ROOT_BINDING", None)
    assert materials.main(["capture"]) == 1 and len(calls) == 2


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_ACTOR", "other"),
        ("GITHUB_ACTIONS", "false"),
        ("RUNNER_ENVIRONMENT", "self-hosted"),
        ("GITHUB_REPOSITORY", "other/repo"),
        ("GITHUB_REF", "refs/heads/main"),
        ("GITHUB_SHA", "b" * 40),
        ("MASSIVE_CONTINUOUS_ENABLED", "false"),
        ("MASSIVE_CONTINUOUS_REVIEWED_SHA", "bad"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("QUANT_CONTINUOUS_ROOT", ""),
        ("QUANT_CONTINUOUS_ROOT", "bad\nroot"),
    ],
)
def test_source_owner_host_root_guards_are_before_credential_release(
    runtime, monkeypatch, key, value
):
    _, _, calls, _ = runtime
    monkeypatch.setenv(key, value)
    monkeypatch.delenv("MASSIVE_API_KEY")
    assert materials.main(["capture"]) == 1 and calls == []


@pytest.mark.parametrize(
    "problem",
    ["public", "mode", "duplicate", "truncated", "encoding", "size", "wrong_blob", "wrong_ref"],
)
def test_tampered_git_cannot_be_reused_or_overwritten(runtime, problem):
    _, github, _, _ = runtime
    if problem not in {"public", "wrong_blob", "wrong_ref"}:
        assert materials.main(["capture"]) == 0
    github.problem = problem
    assert materials.main(["capture"]) == 1


def test_remaining_redacted_cli_root_receipt_publish_and_budget_guards(
    runtime, tmp_path, monkeypatch, capsys
):
    root, github, calls, _ = runtime
    assert materials.main(["verify"]) == 1
    assert materials.main(["unknown_PRIVATE"]) == 1
    assert "PRIVATE" not in capsys.readouterr().out
    with pytest.raises(DemandError):
        materials._publish(github, {})
    with pytest.raises(DemandError):
        materials.capture(tmp_path, github)
    materials.guarded_root()
    monkeypatch.setattr(materials, "_ROOT_BINDING", {"changed": True})
    assert materials.main(["preflight"]) == 1
    monkeypatch.setattr(materials, "_ROOT_BINDING", None)
    assert materials.main(["capture"]) == 0
    with pytest.raises(DemandError):
        materials._publish(github, materials.read_materials(github)[1])
    scope = preparation.frozen_plan()["materials"]
    revision = github.refs[scope["branch"]]
    receipt_sha = github.trees[github.commits[revision]["tree"]["sha"]][
        scope["path"] + "receipt.json"
    ]
    receipt = json.loads(github.blobs[receipt_sha])
    receipt["attempt"] = True
    changed = canonical(receipt)
    sha = materials._blob_sha(changed)
    github.blobs[sha] = changed
    github.trees[github.commits[revision]["tree"]["sha"]][scope["path"] + "receipt.json"] = sha
    assert materials.main(["verify"]) == 1 and len(calls) == 1


def test_receipt_time_and_marker_binding_fail_closed(runtime):
    _, github, _, _ = runtime
    assert materials.main(["capture"]) == 0
    scope = preparation.frozen_plan()["materials"]
    github.refs[scope["reservation_prefix"] + "0"] = "f" * 40
    assert materials.main(["verify"]) == 1
    assert datetime.now(UTC).year >= 2026
