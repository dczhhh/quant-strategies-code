"""Invented report envelopes; no licensed prices or private account rows."""

import copy
import json
from types import SimpleNamespace

import pytest

from tests.test_private_archive_migration import FakeGitHub
from tools import rotation_fractional_replay as f

SOURCE = "e" * 40


def test_integer_compatibility_removes_only_declared_default_metadata(monkeypatch):
    calls = []

    def compare(archive, logical, source):
        calls.append((archive, logical, source))
        return {"semantics_match": True}

    monkeypatch.setattr(f.d, "compare_to_baseline", compare)
    value = {"assumptions": {"share_step": "1"}, "cash": "100.000000000001"}
    logical = {"result.json": f.u.canonical(value), "inputs.json": b"invented exact input"}
    answer = f.integer_baseline_check(None, logical, SOURCE)
    assert answer["declared_default_share_step_metadata_fields"] == 1
    assert json.loads(calls[0][1]["result.json"])["cash"] == value["cash"]
    assert calls[0][1]["inputs.json"] == logical["inputs.json"]
    assert json.loads(logical["result.json"])["assumptions"]["share_step"] == "1"


@pytest.mark.parametrize(
    "value", [{"assumptions": {"share_step": "0.1"}}, {"not_assumptions": {"share_step": "1"}}]
)
def test_default_metadata_exception_cannot_hide_fractional_values_or_other_paths(value):
    with pytest.raises(ValueError, match="INTEGER_STEP_METADATA_CONFLICT"):
        f.integer_baseline_check(None, {"result.json": f.u.canonical(value)}, SOURCE)


@pytest.fixture
def invented_computations(monkeypatch):
    archive = FakeGitHub(f.c.m.DESTINATION)
    calls = []

    def compute(factory, source, *, scenario=f.r.ASSUMPTIONS, **kwargs):
        calls.append(str(scenario.share_step))
        value = {"summary": {"execution_sessions": 143, "share_quantum": str(scenario.share_step)}}
        logical = {name: f.u.canonical({"invented": name}) for name in f.d.LOGICAL_FILES}
        logical["inputs.json"] = b'{"invented":"same normalized bytes"}'
        logical["result.json"] = f.u.canonical(value)
        return factory(), logical, value, {}, SimpleNamespace(fingerprint="a" * 64)

    monkeypatch.setattr(f.r, "compute_report", compute)
    return archive, calls


def test_separate_immutable_report_and_fresh_readonly_every_byte_check(invented_computations):
    archive, calls = invented_computations

    def checker(*_):
        return {"semantics_match": True, "status": "INVENTED_ONLY"}

    first = f.execute(lambda: archive, SOURCE, baseline_checker=checker)
    assert calls == ["1", "0.1"]
    assert first["performance_admission"] is False
    assert first["fee_policy_tenths"] == "parent_order_tiered"
    saved = copy.deepcopy(archive.refs)
    archive.events.clear()
    second = f.execute(lambda: archive, SOURCE, verify_only=True, baseline_checker=checker)
    assert first == second and archive.refs == saved
    assert set(archive.refs) == {f.REPORT_REF}
    assert all(method == "GET" for method, _ in archive.events)


def test_changed_integer_control_stops_before_fractional_run_or_archive(invented_computations):
    archive, calls = invented_computations
    with pytest.raises(ValueError, match="INTEGER_BASELINE_VALUES_CHANGED"):
        f.execute(lambda: archive, SOURCE, baseline_checker=lambda *_: {"semantics_match": False})
    assert calls == ["1"] and not archive.refs


def test_input_change_between_share_modes_blocks_before_any_archive(
    invented_computations, monkeypatch
):
    archive, _ = invented_computations
    original = f.r.compute_report

    def corrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get("scenario"):
            result[1]["inputs.json"] += b" "
        return result

    monkeypatch.setattr(f.r, "compute_report", corrupt)
    with pytest.raises(ValueError, match="FRACTIONAL_INPUT_BYTES_CONFLICT"):
        f.execute(lambda: archive, SOURCE, baseline_checker=lambda *_: {"semantics_match": True})
    assert not archive.refs


def test_missing_fresh_report_is_readonly_not_permission_to_create(invented_computations):
    archive, _ = invented_computations
    with pytest.raises(ValueError, match="FRACTIONAL_STRESS_REPORT_MISSING"):
        f.execute(
            lambda: archive,
            SOURCE,
            verify_only=True,
            baseline_checker=lambda *_: {"semantics_match": True},
        )
    assert not archive.refs


def test_cli_error_does_not_echo_private_details(monkeypatch, capsys):
    monkeypatch.setattr(f.sys, "argv", ["fractional", "run", SOURCE])
    monkeypatch.setattr(f.c, "guarded", lambda _: None)

    def fail(*_args, **_kwargs):
        raise ValueError("invented secret account fill price")

    monkeypatch.setattr(f, "execute", fail)
    assert f.main() == 1
    output = capsys.readouterr().out
    assert "secret account" not in output
    assert json.loads(output)["performance_admission"] is False
