"""Real Engine/ledger checks with invented bars; no retained performance fixtures."""

from datetime import date

import pytest

from tests.strategies.test_rotation_research import inputs, settings
from tests.test_rotation_frequency import recovery_inputs
from tools import rotation_four_year_matrix as m
from tools import rotation_quantity_exit_audit as q
from tools.rotation_decision_oracle import DecisionOracleMismatch


@pytest.mark.parametrize("case", m.CASES)
@pytest.mark.parametrize("step", ["1", "0.1"])
def test_fixed_factor_engine_passes_independent_intents_and_cash_ledger(case, step):
    engine = m.matrix_engine(
        recovery_inputs(), q.w.assumptions(step, primary=False), case, settings()
    )
    oracle = m.MatrixOracle(engine, case)
    result = q.u.engine_result(engine)
    assert oracle.finish()["status"] == "CONDITIONAL_DECISION_AND_SUBMITTED_INTENT_ORACLE_PASS"
    assert result["engine"]["independent_ledger"]["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    assert engine.strategy is engine.lifecycle_dispatcher.strategy
    assert engine.broker.fills


@pytest.mark.parametrize("case", ["M", "T15_S", "T15_SR", "C15_S"])
@pytest.mark.parametrize("step", ["1", "0.1"])
def test_existing_cases_reproduce_corrected_full_results_byte_exact(case, step):
    source, assumption = inputs(bear=True), q.w.assumptions(step, primary=False)
    prior = q.run_cell(source, assumption, case, corrected=True, settings=settings())
    engine = m.matrix_engine(source, assumption, case, settings())
    m.MatrixOracle(engine, case)
    observed = q.u.engine_result(engine)
    if assumption.share_step != 1:
        observed["parent_order_fee_audit"] = q.w.f.order_fee_audit(engine)
    assert q.u.canonical(observed) == q.u.canonical(prior["result"])


def test_missing_factor_changes_recovery_without_advancing_monthly_rotation():
    observed = {}
    for case in m.CASES:
        engine = m.matrix_engine(
            recovery_inputs(), q.w.assumptions("0.1", primary=False), case, settings()
        )
        oracle = m.MatrixOracle(engine, case)
        engine.run()
        oracle.finish()
        observed[case] = {
            "rotation": set(oracle.monthly),
            "recovery": [
                day
                for day, row in oracle.reference.items()
                if row["recovered"] and day >= date(2025, 10, 1)
            ],
            "risk": {day: row["regime"] for day, row in oracle.reference.items()},
        }
    assert observed["M"]["rotation"] == observed["M_T15R"]["rotation"]
    assert observed["T15_S"]["rotation"] == observed["T15_SR"]["rotation"]
    assert observed["M"]["risk"] == observed["T15_S"]["risk"] == observed["C15_S"]["risk"]
    assert observed["M_T15R"]["risk"] == observed["T15_SR"]["risk"]
    assert observed["M"]["recovery"] == [date(2025, 10, 31)]
    assert observed["M_T15R"]["recovery"] != observed["M"]["recovery"]


@pytest.mark.parametrize("case", m.CASES)
@pytest.mark.parametrize("step", ["1", "0.1"])
def test_each_sector_factor_has_exact_matching_spy_risk_path(case, step):
    source, assumption = recovery_inputs(), q.w.assumptions(step, primary=False)
    sector = m.matrix_engine(source, assumption, case, settings())
    oracle = m.MatrixOracle(sector, case)
    control = m.benchmark_engine(source, assumption, m.matching_spy(case), settings())
    control.run()
    assert {fill.asset for fill in control.broker.fills} == {"SPY"}
    assert (
        control.research_report["independent_ledger"]["status"]
        == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    )
    changes = control.strategy.events
    assert any(row["reason"] == "recovery" for row in changes)
    assert all(
        oracle.reference[row["signal_session"]]["regime"] == row["regime"]
        for row in control.strategy.observed_signals
    )
    assert control.research_report["benchmark_policy"]["monthly"] == "none_without_regime_change"


@pytest.mark.parametrize("step", ["1", "0.1"])
def test_passive_spy_remains_original_95pct_initial_control(step):
    engine = m.benchmark_engine(inputs(), q.w.assumptions(step, primary=False), "SPY_PASSIVE")
    engine.run()
    assert engine.broker.fills
    assert {fill.asset for fill in engine.broker.fills} == {"SPY"}
    assert (
        engine.research_report["independent_ledger"]["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    )


def test_broken_recovery_schedule_cannot_pass_the_independent_oracle():
    engine = m.matrix_engine(
        recovery_inputs(), q.w.assumptions("0.1", primary=False), "M_T15R", settings()
    )
    m.MatrixOracle(engine, "M_T15R")
    engine.strategy.book.recovery_days = frozenset()
    with pytest.raises(DecisionOracleMismatch, match="SIGNAL_STATE|REGIME|RECOVERY"):
        engine.run()
