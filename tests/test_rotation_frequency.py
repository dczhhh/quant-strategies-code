"""Engine-level frequency tests with invented sources and independent negative checks."""

import json
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal

import pytest

from ml4t.backtest.strategies.sector_rotation.research_fractional import ResearchAssumptions
from tests.strategies.test_rotation_research import ASSUMPTIONS, inputs, settings
from tests.test_private_archive_migration import FakeGitHub
from tools import rotation_decision_oracle as o
from tools import rotation_frequency_experiment as w
from tools import rotation_v_reversal_experiment as v
from tools.rotation_frequency_oracle import FrequencyOracle, reference_dates
from tools.rotation_frequency_schedule import CASES, frequency_engine, schedule


@pytest.mark.parametrize("step", ["1", "0.1"])
@pytest.mark.parametrize("case", CASES)
def test_frequency_real_engine_signals_targets_fees_and_ledger(step, case):
    assumption = ResearchAssumptions(
        **asdict(ASSUMPTIONS),
        share_step=Decimal(step),
        fractional_fee_policy="parent_order_tiered",
    )
    engine = frequency_engine(inputs(bear=True), assumption, settings(), case)
    oracle = FrequencyOracle(engine, case)
    engine.run()
    report = oracle.finish()
    assert report["status"] == "CONDITIONAL_DECISION_AND_SUBMITTED_INTENT_ORACLE_PASS"
    assert (
        engine.research_report["independent_ledger"]["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    )
    assert engine.broker.fills
    first = engine.strategy.decisions[0]
    assert all(fill.timestamp.date() >= first.execute_session for fill in engine.broker.fills)
    assert all(
        35 <= fill.timestamp.astimezone(o.NY).minute < 45
        and fill.timestamp.astimezone(o.NY).hour == 9
        for fill in engine.broker.fills
    )
    expected = reference_dates(date(2025, 9, 30), date(2025, 11, 5), case)[1]
    assert set(oracle.monthly) == set(expected)


@pytest.mark.parametrize("case", CASES)
def test_independent_calendar_covers_holidays_without_anchor_drift(case):
    initial, anchor, signals = schedule(date(2024, 12, 2), date(2025, 6, 30), case)
    assert initial == date(2024, 11, 29)
    assert anchor == date(2024, 12, 2)
    assert signals == reference_dates(date(2024, 12, 2), date(2025, 6, 30), case)[1]
    if case.startswith("C"):
        execution = {o.CAL.shift(day, 1) for day in signals}
        assert date(2025, 1, 2) in execution
        assert date(2025, 1, 16) in execution  # Not Jan17 after holiday roll.
    if case.startswith("T"):
        assert o.CAL.shift(anchor, 15) in {o.CAL.shift(day, 1) for day in signals}


@pytest.mark.parametrize("step", ["1", "0.1"])
def test_monthly_full_engine_results_reproduce_original(step):
    assumption = w.assumptions(step, primary=False)
    _, control = v.run_cell(inputs(bear=True), assumption, settings=settings())
    _, observed = w.run_cell(inputs(bear=True), assumption, "M", settings())
    assert w.u.canonical(observed["result"]) == w.u.canonical(control["result"])


def recovery_inputs():
    source = inputs(bear=True)

    def price(day, asset, value):
        return (
            Decimal(110) + Decimal((day - date(2025, 10, 13)).days) * 2
            if (asset == "SPY" and day >= date(2025, 10, 13))
            else value
        )

    closes = tuple(
        replace(row, close=price(row.session, row.asset, row.close)) for row in source.closes
    )
    bars = tuple(
        replace(
            row,
            **dict.fromkeys(
                ("open", "high", "low", "close"),
                price(row.end.astimezone(o.NY).date(), row.asset, row.close),
            ),
        )
        for row in source.bars
    )
    return replace(source, closes=closes, bars=bars)


def test_recovery_frequency_changes_only_the_registered_call_site():
    source = recovery_inputs()
    recovered = {}
    for case in ("M", "C15_S", "C15_SR"):
        engine = frequency_engine(source, w.assumptions("0.1", primary=False), settings(), case)
        oracle = FrequencyOracle(engine, case)
        engine.run()
        oracle.finish()
        recovered[case] = [
            day
            for day, row in oracle.reference.items()
            if row["recovered"] and day >= date(2025, 10, 1)
        ]
    assert recovered["C15_S"] == recovered["M"] == [date(2025, 10, 31)]
    assert recovered["C15_SR"] == [date(2025, 10, 15)]


def test_oracle_rejects_a_wrong_rotation_calendar():
    engine = frequency_engine(inputs(), w.assumptions("0.1", primary=False), settings(), "C15_SR")
    oracle = FrequencyOracle(engine, "C15_SR")
    engine.strategy.book.rotation_days = frozenset({date(2025, 9, 30)})
    engine.run()
    with pytest.raises(o.DecisionOracleMismatch, match="FREQUENCY_COVERAGE"):
        oracle.finish()


@pytest.mark.parametrize("case", ["half_month", "15", None, True])
def test_unsupported_calendar_labels_are_rejected(case):
    with pytest.raises(ValueError, match="UNKNOWN_CASE"):
        schedule(date(2025, 1, 2), date(2025, 3, 1), case)


def test_private_frequency_report_is_immutable():
    archive = FakeGitHub(w.c.m.DESTINATION)
    payload = w.pack(dict.fromkeys(w.LOGICAL, b'{"invented":true}'), "b" * 40)
    with pytest.raises(ValueError, match="REPORT_MISSING"):
        w.retain(archive, payload, verify_only=True)
    digest = w.retain(archive, payload, verify_only=False)
    assert w.retain(archive, payload, verify_only=True) == digest
    changed = dict(payload)
    changed[next(iter(changed))] += b"x"
    with pytest.raises(ValueError, match="PRIVATE_REPORT_BYTES"):
        w.retain(archive, changed, verify_only=False)


def test_verified_anchor_roundtrip_and_wrong_root_are_checked(monkeypatch):
    archive = FakeGitHub(w.c.m.DESTINATION)
    logical = {name: w.u.canonical({"invented": name}) for name in v.LOGICAL}
    payload = v.pack(logical, w.BASE)
    digest = v.retain(archive, payload, verify_only=False)
    monkeypatch.setattr(w, "PIN", digest)
    assert w.load_anchor(archive) == logical
    monkeypatch.setattr(w, "PIN", "0" * 64)
    with pytest.raises(ValueError, match="ANCHOR_BUNDLE"):
        w.load_anchor(archive)


def test_retained_timestamp_roundtrip_preserves_full_economics_and_analytics():
    source = inputs(bear=True)
    assumption = w.assumptions("0.1", primary=False)
    _, raw = w.run_cell(source, assumption, "M", settings())
    serialized = json.loads(w.u.canonical(raw["result"]))
    restored = w.retained_benchmark(serialized)
    assert w.u.canonical(restored) == w.u.canonical(raw["result"])
    assert isinstance(serialized["trace"][0]["timestamp"], str)
    original = w.a.describe(
        source, assumption, raw["result"], raw["result"], raw["signal_oracle"]
    )
    observed = w.a.describe(
        source, assumption, raw["result"], restored, raw["signal_oracle"]
    )
    assert w.u.canonical(original) == w.u.canonical(observed)


def test_retained_naive_clock_is_rejected():
    with pytest.raises(ValueError, match="ANCHOR_TIMESTAMP"):
        w.retained_benchmark({"trace": [{"timestamp": "2025-01-02T16:00:00"}], "fills": []})
