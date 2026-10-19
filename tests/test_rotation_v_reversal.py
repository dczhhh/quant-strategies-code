"""Invented factorial signal/ledger tests, including corrupt and observer cases."""

from dataclasses import asdict
from decimal import Decimal

import pytest

from ml4t.backtest.strategies.sector_rotation.research_ablation import experimental_rotation_engine
from ml4t.backtest.strategies.sector_rotation.research_fractional import ResearchAssumptions
from tests.strategies.test_rotation_research import ASSUMPTIONS, inputs, settings
from tests.test_private_archive_migration import FakeGitHub
from tools import rotation_decision_oracle as o
from tools import rotation_v_reversal_experiment as v
from tools.rotation_ablation_oracle import AblationOracle


@pytest.mark.parametrize("step", ["1", "0.1"])
@pytest.mark.parametrize("sector", [False, True])
@pytest.mark.parametrize("risk", [False, True])
def test_all_eight_cells_have_independent_signals_targets_and_ledger(step, sector, risk):
    source = inputs(bear=True)
    assumption = ResearchAssumptions(**asdict(ASSUMPTIONS), share_step=Decimal(step))
    engine = experimental_rotation_engine(
        source, assumption, settings(), sector_rotation=sector, risk_tiers=risk
    )
    oracle = AblationOracle(engine, sector_rotation=sector, risk_tiers=risk)
    engine.run()
    report = oracle.finish()
    assert report["status"] == "CONDITIONAL_DECISION_AND_SUBMITTED_INTENT_ORACLE_PASS"
    assert report["monthly_decisions"] == 2
    assert (
        engine.research_report["independent_ledger"]["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    )
    if not risk:
        assert report["observed_regimes"] == ["normal"]


def test_read_only_oracle_cannot_change_frozen_sector_boundary_or_cash_path():
    source = inputs(bear=True)
    assumption = ResearchAssumptions(**asdict(ASSUMPTIONS), share_step=Decimal(".1"))

    def run(observe):
        engine = experimental_rotation_engine(
            source, assumption, settings(), sector_rotation=False, risk_tiers=True
        )
        if observe:
            AblationOracle(engine, sector_rotation=False, risk_tiers=True)
        engine.run()
        return engine

    first, second = run(False), run(True)
    assert first.strategy.book.frozen_on == second.strategy.book.frozen_on
    assert first.research_trace == second.research_trace
    assert first.broker.fills == second.broker.fills


def test_corrupt_ablation_signals_are_rejected():
    engine = experimental_rotation_engine(
        inputs(),
        ResearchAssumptions(**asdict(ASSUMPTIONS)),
        settings(),
        sector_rotation=True,
        risk_tiers=False,
    )
    original = engine.strategy.book.scores

    def corrupt():
        value = original()
        if value:
            value[0][next(iter(value[0]))] += 0.2
        return value

    engine.strategy.book.scores = corrupt
    AblationOracle(engine, sector_rotation=True, risk_tiers=False)
    with pytest.raises(o.DecisionOracleMismatch, match="MOMENTUM|MONTHLY"):
        engine.run()


def test_snapshot_fee_reservation_independent_arithmetic():
    engine = experimental_rotation_engine(
        inputs(),
        ResearchAssumptions(**asdict(ASSUMPTIONS), share_step=Decimal(".1")),
        settings(),
        sector_rotation=True,
        risk_tiers=True,
    )
    oracle = AblationOracle(engine, sector_rotation=True, risk_tiers=True)
    expected = (
        Decimal(".35") * Decimal("1.000735")
        + 2 * Decimal(".0035")
        + Decimal("2.5") * Decimal(".000203")
    )
    assert oracle.fee(Decimal("2.5"), Decimal(100)) == expected
    assert oracle.fee(Decimal(".5"), Decimal(100)) > Decimal(".35")


@pytest.mark.parametrize("step", ["1", "0.1"])
def test_executable_spy_timing_uses_causal_risk_and_preserves_ledger(step):
    source = inputs(bear=True)
    assumption = ResearchAssumptions(**asdict(ASSUMPTIONS), share_step=Decimal(step))
    summary, raw = v.run_cell(source, assumption, benchmark="timed", settings=settings())
    assert summary["status"] == "CONDITIONAL_RESEARCH_PASS"
    assert summary["fills"] > 1
    assert {row["asset"] for row in raw["result"]["fills"]} == {"SPY"}
    assert (
        raw["result"]["engine"]["independent_ledger"]["status"]
        == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    )


def test_private_report_is_immutable_and_fresh_verify_cannot_create():
    archive = FakeGitHub(v.c.m.DESTINATION)
    payload = v.pack(dict.fromkeys(v.LOGICAL, b'{"invented":true}'), "b" * 40)
    with pytest.raises(ValueError, match="REPORT_MISSING"):
        v.retain(archive, payload, verify_only=True)
    digest = v.retain(archive, payload, verify_only=False)
    assert v.retain(archive, payload, verify_only=True) == digest
    changed = dict(payload)
    changed[next(iter(changed))] += b"x"
    with pytest.raises(ValueError, match="PRIVATE_REPORT_BYTES"):
        v.retain(archive, changed, verify_only=False)


def test_phase_boundaries_use_returns_after_start_close_once():
    from datetime import date

    daily = [{"date": date(2025, 4, n), "NAV": 100 + n, "exposure": 0.2} for n in (8, 9, 10)]
    result = v.period(daily, date(2025, 4, 8), date(2025, 4, 10))
    assert result["sessions"] == 2
    assert result["return_pct"] == pytest.approx((110 / 108 - 1) * 100)
