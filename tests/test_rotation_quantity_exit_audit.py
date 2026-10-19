"""Synthetic paired replay and corruption/immutable archive negative checks."""

import copy
import json

import pytest

from tests.strategies.test_rotation_research import inputs, settings
from tests.test_private_archive_migration import FakeGitHub
from tools import rotation_quantity_exit_audit as q


@pytest.mark.parametrize("step", ["1", "0.1"])
@pytest.mark.parametrize("case", q.w.CASES)
def test_capture_keeps_original_complete_engine_and_oracle_bytes(step, case):
    source, assumption = inputs(bear=True), q.w.assumptions(step, primary=False)
    _, previous = q.w.run_cell(source, assumption, case, settings())
    old = q.run_cell(source, assumption, case, corrected=False, settings=settings())
    new = q.run_cell(source, assumption, case, corrected=True, settings=settings())
    for key in ("result", "signal_oracle", "portfolio"):
        assert q.u.canonical(old[key]) == q.u.canonical(previous[key])
    report = q.bridge(old, new, assumption.initial_cash)
    assert report["asset_bridge_error_usd"] <= 1e-8
    assert old["order_admission"]
    assert all(r["order_id"] for r in old["order_admission"])
    assert all(r["final_status"] for r in new["order_admission"])


def test_bridge_rejects_pnl_corruption_and_changed_risk_path():
    source, assumption = inputs(bear=True), q.w.assumptions(".1", primary=False)
    old = q.run_cell(source, assumption, "M", corrected=False, settings=settings())
    new = copy.deepcopy(old)
    new["portfolio"]["end_NAV_usd"] += 1
    with pytest.raises(ValueError, match="ASSET_NAV_BRIDGE"):
        q.bridge(old, new, assumption.initial_cash)
    new = copy.deepcopy(old)
    new["signal_oracle"]["signals"][0]["regime"] = "CORRUPTED"
    with pytest.raises(ValueError, match="RISK_SIGNAL_PATH_CHANGED"):
        q.bridge(old, new, assumption.initial_cash)


@pytest.mark.parametrize("window", q.WINDOWS)
def test_report_create_only_read_only_verify_and_cross_window_rejected(window):
    archive = FakeGitHub(q.c.m.DESTINATION)
    payload = q.pack(dict.fromkeys(q.LOGICAL, b'{"invented":true}'), "b" * 40, window)
    with pytest.raises(ValueError, match="REPORT_MISSING"):
        q.retain(archive, payload, window, verify_only=True)
    digest = q.retain(archive, payload, window, verify_only=False)
    archive.events.clear()
    assert q.retain(archive, payload, window, verify_only=True) == digest
    assert all(method == "GET" for method, _ in archive.events)
    other = next(w for w in q.WINDOWS if w != window)
    with pytest.raises(ValueError, match="PRIVATE_BUDGET"):
        q.retain(archive, payload, other, verify_only=False)
    payload[next(iter(payload))] += b"changed"
    with pytest.raises(ValueError, match="FRESH_REPORT_BYTES"):
        q.retain(archive, payload, window, verify_only=False)


def test_window_and_logical_set_are_closed():
    with pytest.raises(ValueError, match="LOGICAL_SET"):
        q.pack({"unknown": b"{}"}, "b" * 40, "bear2022")
    with pytest.raises(ValueError, match="LOGICAL_SET"):
        q.pack(dict.fromkeys(q.LOGICAL, b"{}"), "b" * 40, "four_year")


def test_safe_failure_cannot_expose_provider_exception(monkeypatch, capsys):
    monkeypatch.setattr(q.sys, "argv", ["audit", "run", "b" * 40, "bear2022"])
    monkeypatch.setattr(q, "guarded", lambda _: None)
    monkeypatch.setenv("ARCHIVE_WRITE_TOKEN", "INVENTED")
    monkeypatch.setattr(q, "build", lambda *_: (_ for _ in ()).throw(KeyError("PRIVATE_VALUE")))
    assert q.main() == 1
    output = capsys.readouterr().out
    assert "PRIVATE_VALUE" not in output
    assert json.loads(output)["reason"] == "UNVERIFIED_RUNTIME_FAILURE"
