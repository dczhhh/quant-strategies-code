"""Invented source inputs; isolated fee profile never changes sealed PIT APIs."""

import copy
from dataclasses import asdict, fields, replace
from decimal import Decimal

import pytest

from ml4t.backtest.strategies.sector_rotation.research_fractional import (
    ResearchAssumptions,
    retrospective_rotation_engine,
)
from ml4t.backtest.strategies.sector_rotation.research_fractional_benchmark import (
    retrospective_spy_benchmark,
)
from ml4t.backtest.strategies.sector_rotation.research_fractional_ledger import (
    ResearchLedgerMismatch,
    verify_research_ledger,
)
from quant_constraints import MarketContext
from quant_constraints.fees.ibkr_fractional_order import IBKRParentOrderFractionalResearch
from quant_constraints.research.data.verification import VERIFICATION_VERSION
from tests.strategies.test_rotation_research import ASSUMPTIONS, inputs, settings


def scenario(policy="parent_order_tiered"):
    return ResearchAssumptions(
        **asdict(ASSUMPTIONS), share_step=Decimal(".1"), fractional_fee_policy=policy
    )


def test_sealed_original_assumption_context_and_verifier_are_not_relaxed():
    assert not hasattr(ASSUMPTIONS, "share_step")
    assert "research_share_step" not in {field.name for field in fields(MarketContext)}
    assert VERIFICATION_VERSION.endswith("v11_assumed_distribution")


def test_tenth_share_rotation_parent_fees_and_independent_cash_ledger():
    source = inputs(end="2025-10-03")
    corrected = retrospective_rotation_engine(source, scenario(), settings())
    legacy = retrospective_rotation_engine(source, scenario("published_per_execution"), settings())
    corrected.run()
    legacy.run()
    assert isinstance(corrected.broker.fee_model, IBKRParentOrderFractionalResearch)
    assert not isinstance(legacy.broker.fee_model, IBKRParentOrderFractionalResearch)
    assert corrected.research_report["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    assert corrected.research_report["PIT_verified"] is False
    assert sum(f.commission for f in corrected.broker.fills) > 0
    assert sum(f.commission for f in corrected.broker.fills) < sum(
        f.commission for f in legacy.broker.fills
    )
    assert Decimal(
        corrected.research_report["independent_ledger"]["maximum_monetary_error"]
    ) <= Decimal("1E-8")
    assert any(Decimal(str(f.quantity)) % 1 for f in corrected.broker.fills)


def test_funded_spy_uses_the_same_explicit_profile_and_rejects_off_grid_corruption():
    engine = retrospective_spy_benchmark(inputs(end="2025-10-03"), scenario())
    engine.run()
    assert isinstance(engine.broker.fee_model, IBKRParentOrderFractionalResearch)
    assert engine.research_report["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    fills = copy.deepcopy(engine.broker.fills)
    fills[0].quantity += 0.05
    with pytest.raises(ResearchLedgerMismatch, match="share step"):
        verify_research_ledger(
            engine.research_inputs,
            engine.research_assumptions,
            fills,
            engine.broker.orders,
            engine.broker.fee_records,
            engine.research_trace,
        )


def test_valid_forward_split_keeps_tenth_grid_but_reverse_fraction_still_rejects():
    source = inputs()
    engine = retrospective_rotation_engine(source, scenario(), settings())
    engine.run()
    assert engine.research_report["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    reverse = replace(
        source, splits=tuple(replace(s, new_per_old=Decimal(".5")) for s in source.splits)
    )
    bad = retrospective_rotation_engine(reverse, scenario(), settings())
    with pytest.raises(ValueError, match="UNSUPPORTED_CASH_IN_LIEU"):
        bad.run()
    assert bad.research_report["status"] == "RETROSPECTIVE_RUN_FAILED"


def test_free_or_unknown_fractional_policy_is_not_supported():
    with pytest.raises(ValueError, match="Unsupported fractional fee"):
        scenario("free_fractional")
