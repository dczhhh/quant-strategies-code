"""Independent numerical boundaries and real Engine executions, invented data."""

from dataclasses import asdict, replace
from decimal import Decimal
from math import inf, nan, nextafter

import pytest

from ml4t.backtest.accounting import AccountState, Gatekeeper, UnifiedAccountPolicy
from ml4t.backtest.core.shared import quantity_zero_tolerance
from ml4t.backtest.models import NoCommission
from ml4t.backtest.strategies.sector_rotation.research_fractional import (
    ResearchAssumptions,
    retrospective_rotation_engine,
)
from quant_constraints import Intent, Kind
from quant_constraints.calendar import NY
from tests.strategies.test_rotation_research import ASSUMPTIONS, inputs, settings
from tools.rotation_quantity_exit import ResearchExitGatekeeper, bind_quantity_exit


def gate():
    return ResearchExitGatekeeper(AccountState(100000, UnifiedAccountPolicy()), NoCommission())


@pytest.mark.parametrize("quantity", [7.9, 120.5, 292.3, 1_000_000.1])
@pytest.mark.parametrize("sign", [-1, 1])
def test_ulp_residue_is_an_exit_but_real_reversal_stays_a_reversal(quantity, sign):
    subject = gate()
    current = sign * nextafter(quantity, 0)
    assert abs(current - sign * quantity) <= quantity_zero_tolerance(current, quantity)
    assert not subject._is_reversal(current, -sign * quantity)
    assert subject._is_reducing_order(current, -sign * quantity)
    for excess in (Decimal("0.00000001"), Decimal("0.1"), Decimal("1")):
        delta = -sign * (quantity + float(excess))
        assert subject._is_reversal(current, delta)
        assert not subject._is_reducing_order(current, delta)


@pytest.mark.parametrize(
    "current,delta", [(0, -1), (0, 1), (1, 1), (-1, -1), (nan, -1), (inf, -inf)]
)
def test_zero_same_direction_and_nonfinite_are_not_relabelled_as_exits(current, delta):
    assert not gate()._cancels(current, delta)


def probe_engine(*, corrected, excess=0.0):
    source = inputs(end="2025-10-03")
    assumption = ResearchAssumptions(
        **asdict(replace(ASSUMPTIONS, initial_cash=Decimal(100000))),
        share_step=Decimal("0.1"),
        fractional_fee_policy="parent_order_tiered",
    )
    engine = retrospective_rotation_engine(source, assumption, settings())
    # No direct ledger injection: the broker actually buys 160.1 then sells 30.3.
    # Their binary subtraction gives 129.79999999999998; the next sell is 129.8.
    engine.strategy._before_execution = lambda *_: None

    def orders(timestamp, data, context, broker):
        local = timestamp.astimezone(NY)
        if timestamp.date() == source.bars[0].end.date() and local.hour == 9:
            if local.minute == 35:
                broker.submit_intent(Intent("XLRE", 160.1, kind=Kind.ENTRY))
            elif local.minute == 37:
                broker.submit_intent(Intent("XLRE", -30.3, kind=Kind.RISK))
            elif local.minute == 39:
                broker.submit_intent(Intent("XLRE", -129.8 - excess, kind=Kind.RISK))

    engine.strategy.on_data = orders
    if corrected:
        bind_quantity_exit(engine)
    engine.run()
    return engine


def test_real_engine_reproduces_reject_and_corrected_fill_independent_ledger():
    original, corrected = probe_engine(corrected=False), probe_engine(corrected=True)
    rejected = original.broker.get_rejected_orders("XLRE")
    assert len(rejected) == 1
    assert rejected[0].rejection_reason == "Position reversal not allowed in cash account"
    assert rejected[0]._rejection_code == "account_restriction"
    assert original.broker.get_position("XLRE").quantity == 129.79999999999998
    assert not corrected.broker.get_rejected_orders("XLRE")
    assert corrected.broker.get_position("XLRE") is None
    assert len(corrected.broker.fills) == 3
    assert corrected.broker.fills[:2] == original.broker.fills
    for engine in (original, corrected):
        assert engine.research_report["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
        assert Decimal(
            engine.research_report["independent_ledger"]["maximum_monetary_error"]
        ) <= Decimal("1E-8")


def test_real_oversell_remains_rejected_without_fill_or_reservation():
    engine = probe_engine(corrected=True, excess=0.1)
    rejected = engine.broker.get_rejected_orders("XLRE")
    assert len(rejected) == 1
    assert rejected[0]._rejection_code == "short_sale"
    assert rejected[0]._reserved_cash == 0
    assert len(engine.broker.fills) == 2
    assert engine.broker.get_position("XLRE").quantity > 0


def test_binding_preserves_every_collaborator_setting_and_rejects_rebinding():
    assumption = ResearchAssumptions(**asdict(ASSUMPTIONS), share_step=Decimal(".1"))
    engine = retrospective_rotation_engine(inputs(), assumption, settings())
    old = engine.broker.gatekeeper
    bind_quantity_exit(engine)
    assert vars(old) == vars(engine.broker.gatekeeper)
    assert type(old) is Gatekeeper
    with pytest.raises(ValueError, match="CANONICAL_GATEKEEPER"):
        bind_quantity_exit(engine)
    engine.run()
    with pytest.raises(ValueError, match="FRESH_RESEARCH_ENGINE"):
        bind_quantity_exit(engine)
