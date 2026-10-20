"""Recoveries must create fundable plans on their own causal signal dates."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from ml4t.backtest.strategies.sector_rotation.research_inputs import ResearchDistribution
from quant_constraints.gates import RebalanceGate
from tests.strategies.test_rotation_research import settings
from tests.test_bear_2022_experiment import assumed_inputs
from tests.test_rotation_frequency import recovery_inputs
from tools import rotation_four_year_matrix as m
from tools import rotation_four_year_spy_recovery as r
from tools import rotation_quantity_exit_audit as q


def run(case, step, *, corrected, data=None):
    engine = m.benchmark_engine(
        data or recovery_inputs(), q.w.assumptions(step, primary=False), case, settings()
    )
    audit = r.bind(engine) if corrected else None
    result = q.u.engine_result(engine)
    return engine, result, audit.finish() if audit else None


@pytest.mark.parametrize("step", ["0.1", "1"])
def test_trading_recovery_buys_next_session_outside_monthly_calendar(step):
    old, _, _ = run("SPY_SIGNAL_T15R", step, corrected=False)
    engine, result, audit = run("SPY_SIGNAL_T15R", step, corrected=True)
    recovery = [e for e in engine.strategy.events if e["reason"] == "recovery"]
    assert recovery
    original = RebalanceGate(engine.broker.controller.config, engine.broker.controller.calendar)
    for event in recovery:
        day = original.calendar.shift(event["signal_session"], 1)
        assert any(
            f.side.value == "buy" and f.timestamp.astimezone(r.NY).date() == day
            for f in engine.broker.fills
        )
    assert any(row.decision.code == "rebalance_schedule" for row in old.broker.controller.audit)
    assert not any(
        row.decision.code == "rebalance_schedule" for row in engine.broker.controller.audit
    )
    assert audit["calendar_topups_enabled"] is False
    assert result["engine"]["independent_ledger"]["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    assert engine.broker.controller.config == old.broker.controller.config
    initial_day = min(f.timestamp.astimezone(r.NY).date() for f in engine.broker.fills)
    allowed = {initial_day, *(original.calendar.shift(e["signal_session"], 1) for e in recovery)}
    assert all(
        f.timestamp.astimezone(r.NY).date() in allowed
        for f in engine.broker.fills
        if f.side.value == "buy"
    )
    assert all(
        abs(f.quantity / float(Decimal(step)) - round(f.quantity / float(Decimal(step)))) < 1e-8
        for f in engine.broker.fills
    )


def unpaid_recovery_inputs():
    data = recovery_inputs()
    unpaid = ResearchDistribution(
        "SPY",
        "invented-unpaid-dividend",
        date(2025, 10, 31),
        date(2025, 10, 31),
        date(2025, 11, 7),
        Decimal(10),
        "ordinary_fund",
        "fixture",
        "fixture",
    )
    return replace(data, distributions=(*data.distributions, unpaid))


@pytest.mark.parametrize("step", ["0.1", "1"])
def test_recovery_sizes_down_without_spending_unpaid_dividend_or_cash_reserve(step):
    data = unpaid_recovery_inputs()
    old, _, _ = run("SPY_SIGNAL_M", step, corrected=False, data=data)
    engine, result, audit = run("SPY_SIGNAL_M", step, corrected=True, data=data)
    assert any(row.decision.code == "cash_reserve" for row in old.broker.controller.audit)
    assert any(p["cash_rounding_steps"] > 0 for p in audit["plans"])
    assert all(p["funded_target"] <= p["requested_target"] + 1e-12 for p in audit["plans"])
    assert not any(row.decision.code == "cash_reserve" for row in engine.broker.controller.audit)
    assert len([f for f in engine.broker.fills if f.side.value == "buy"]) > 1
    assert engine.broker.controller.config.cash_reserve == old.broker.controller.config.cash_reserve
    assert result["trace"][-1]["receivables"] > 0


@pytest.mark.parametrize("step", ["0.1", "1"])
@pytest.mark.parametrize("case", ["SPY_SIGNAL_M", "SPY_SIGNAL_T15R"])
def test_downgrade_only_2022_engine_results_remain_byte_exact(step, case):
    data, assumption = assumed_inputs(), q.e.assumptions(step)
    old = m.benchmark_engine(data, assumption, case, settings())
    prior = q.u.engine_result(old)
    engine = m.benchmark_engine(data, assumption, case, settings())
    manager = r.bind(engine)
    current = q.u.engine_result(engine)
    assert not manager.finish()["reference_recovery_sessions"]
    assert q.u.canonical(current) == q.u.canonical(prior)


def test_unobserved_recovery_cannot_authorize_arbitrary_plan_or_calendar_topup():
    engine = m.benchmark_engine(
        recovery_inputs(), q.w.assumptions("0.1", primary=False), "SPY_SIGNAL_M", settings()
    )
    manager = r.bind(engine)
    with pytest.raises(ValueError, match="PLAN_SCOPE"):
        manager.create({"SPY": 0.9}, plan_id="SPY-fake", defensive_allocation=True)
    assert manager.gate.scheduled(None, object()) is False


def test_independent_reference_detects_a_missing_recovery_creation():
    engine, _, _ = run("SPY_SIGNAL_T15R", "0.1", corrected=True)
    engine.broker.plan_manager.observations.clear()
    with pytest.raises(ValueError, match="REFERENCE_COVERAGE"):
        engine.broker.plan_manager.finish()
