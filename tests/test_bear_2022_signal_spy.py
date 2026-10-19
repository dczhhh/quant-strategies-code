"""Executable signal-only SPY controls and immutable report negative checks."""

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from tests.strategies.test_rotation_research import inputs, settings
from tests.test_bear_2022_experiment import assumed_inputs
from tests.test_private_archive_migration import FakeGitHub
from tests.test_rotation_frequency import recovery_inputs
from tools import bear_2022_signal_spy as b


@pytest.mark.parametrize("step", ["1", "0.1"])
def test_2022_causal_signal_sells_use_completed_close_and_real_ledger(step):
    source = assumed_inputs()
    source = replace(
        source,
        bars=tuple(
            replace(r, open=r.close * Decimal(".99"), low=r.close * Decimal(".99"))
            for r in source.bars
        ),
    )
    engine = b.signal_engine(source, b.e.assumptions(step), settings())
    b.e.bind_uniform_parent_fees(engine)
    engine.run()
    assert engine.strategy is engine.lifecycle_dispatcher.strategy
    assert engine.strategy.events
    bars = {(r.end, r.asset): r for r in source.bars}
    sells = [f for f in engine.broker.fills if f.side.value == "sell"]
    assert sells
    for fill in sells:
        assert fill.asset == "SPY"
        assert fill.price == pytest.approx(
            float(bars[fill.timestamp, "SPY"].close * Decimal(".9998"))
        )
        assert 35 <= fill.timestamp.astimezone(b.w.NY).minute < 45
    for row in engine.strategy.observed_signals:
        assert b.c.CAL.session(row["signal_session"]).market_close <= row["observed_at"]
    for fill in sells:
        assert any(
            r["reason"] == "risk" and r["signal_session"] < fill.timestamp.astimezone(b.w.NY).date()
            for r in engine.strategy.events
        )
    assert (
        engine.research_report["independent_ledger"]["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    )
    assert {f.asset for f in engine.broker.fills} == {"SPY"}


@pytest.mark.parametrize("step", ["1", "0.1"])
def test_no_calendar_topups_and_only_actual_recovery_can_rebuy(step):
    assumption = b.w.assumptions(step, primary=False)
    for source, recovery in ((inputs(bear=True), False), (recovery_inputs(), True)):
        engine = b.signal_engine(source, assumption, settings())
        b.e.bind_uniform_parent_fees(engine)
        engine.run()
        changes = engine.strategy.events
        assert any(r["reason"] == "recovery" for r in changes) is recovery
        buys = [f for f in engine.broker.fills if f.side.value == "buy"]
        initial_day = buys[0].timestamp.date()
        later_buys = [f for f in buys if f.timestamp.date() != initial_day]
        assert bool(later_buys) is recovery
        assert engine.research_report["benchmark_policy"]["monthly"] == "none_without_regime_change"


def test_prior_signal_comparison_checks_every_case_and_rejects_mismatch():
    observed = [{"signal_session": "2022-02-24", "regime": "warning"}]
    prior = {
        f"P{s}_{c}": {"signal_oracle": {"signals": observed.copy()}}
        for s in ("1", "0.1")
        for c in b.w.CASES
    }
    assert b.compare_signals(observed, prior) == 10
    prior["P0.1_T15_SR"]["signal_oracle"]["signals"] = [
        {"signal_session": "2022-02-24", "regime": "normal"}
    ]
    with pytest.raises(ValueError, match="PRIOR_RISK_PATH"):
        b.compare_signals(observed, prior)


def test_report_is_create_only_and_fresh_verify_is_read_only():
    archive = FakeGitHub(b.c.m.DESTINATION)
    payload = b.pack(dict.fromkeys(b.e.LOGICAL, b'{"invented":true}'), "b" * 40)
    with pytest.raises(ValueError, match="REPORT_MISSING"):
        b.retain(archive, payload, verify_only=True)
    first = b.retain(archive, payload, verify_only=False)
    archive.events.clear()
    assert b.retain(archive, payload, verify_only=True) == first
    assert all(method == "GET" for method, _ in archive.events)
    payload[next(iter(payload))] += b"changed"
    with pytest.raises(ValueError, match="FRESH_REPORT_BYTES"):
        b.retain(archive, payload, verify_only=False)


def test_anchor_pin_and_logical_bytes_are_verified(monkeypatch):
    archive = FakeGitHub(b.c.m.DESTINATION)
    logical = {name: b.u.canonical({"invented": name}) for name in b.e.LOGICAL}
    payload = b.e.pack(logical, b.BASE)
    digest = b.e.retain(archive, payload, verify_only=False)
    monkeypatch.setattr(b, "ANCHOR", digest)
    assert b.load_anchor(archive) == logical
    monkeypatch.setattr(b, "ANCHOR", "0" * 64)
    with pytest.raises(ValueError, match="ANCHOR_BUNDLE"):
        b.load_anchor(archive)


def test_independent_signal_arithmetic_rejects_corruption():
    engine = b.signal_engine(inputs(bear=True), b.w.assumptions("1", primary=False), settings())
    for row in engine.strategy.reference.values():
        row["regime"] = "deep_bear"
    with pytest.raises(ValueError, match="INDEPENDENT_RISK_STATE"):
        engine.run()


def test_safe_failure_never_exposes_private_exception(monkeypatch, capsys):
    monkeypatch.setattr(b.sys, "argv", ["replay", "run", "b" * 40])
    monkeypatch.setattr(b, "guarded", lambda _: None)
    monkeypatch.setenv("ARCHIVE_WRITE_TOKEN", "INVENTED")
    monkeypatch.setattr(b, "build", lambda *_: (_ for _ in ()).throw(KeyError("PRIVATE_VALUE")))
    assert b.main() == 1
    output = capsys.readouterr().out
    assert "PRIVATE_VALUE" not in output
    assert json.loads(output)["performance_admission"] is False
