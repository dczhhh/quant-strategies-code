"""No-secret causal factorial research switches on synthetic, never live inputs."""

from __future__ import annotations

import runpy
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import pytest

from ml4t.backtest.strategies.sector_rotation.research_ablation import (
    CausalAblationBook,
    experimental_rotation_engine,
)
from ml4t.backtest.strategies.sector_rotation.research_fractional import ResearchAssumptions


@pytest.mark.parametrize("sector", [True, False])
@pytest.mark.parametrize("risk", [True, False])
def test_factorial_books_preserve_synthetic_cash_and_fee_ledger(sector, risk):
    path = Path(__file__).resolve().parent / "test_rotation_research.py"
    fixtures = runpy.run_path(str(path))
    inputs = fixtures["inputs"](end="2025-10-03")
    assumptions = ResearchAssumptions(
        **asdict(fixtures["ASSUMPTIONS"]),
        share_step=Decimal("0.1"),
        fractional_fee_policy="parent_order_tiered",
    )
    engine = experimental_rotation_engine(
        inputs,
        assumptions,
        fixtures["settings"](),
        sector_rotation=sector,
        risk_tiers=risk,
    )
    engine.run()
    assert engine.research_report["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    assert engine.research_report["PIT_verified"] is False
    assert engine.broker.fills
    if not sector or not risk:
        assert isinstance(engine.strategy.book, CausalAblationBook)
        if not sector:
            assert engine.strategy.book.frozen_on is not None
            assert engine.strategy.book.frozen_on <= engine.strategy.book.freeze_sector_at
            assert engine.strategy.book._frozen_scores is not None
        if not risk:
            assert all(event["code"] != "spy_downgrade" for event in engine.strategy.events)


@pytest.mark.parametrize("sector,risk", [(0, True), (True, 1), ("yes", False)])
def test_factorial_switches_never_accept_nontyped_values(sector, risk):
    with pytest.raises(ValueError, match="explicit booleans"):
        experimental_rotation_engine(None, None, None, sector_rotation=sector, risk_tiers=risk)
