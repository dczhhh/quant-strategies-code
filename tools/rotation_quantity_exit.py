"""Opt-in research correction: canonical zero cancellation at exit admission.

The fill executor already collapses the same residue using quantity_zero_tolerance.
This adapter makes its pre-fill Gatekeeper classify that boundary consistently.
It never changes orders, fills, quantities, account state, or portfolio gates.
"""

from __future__ import annotations

import math

from ml4t.backtest.accounting.gatekeeper import Gatekeeper
from ml4t.backtest.core.shared import quantity_zero_tolerance
from ml4t.backtest.strategies.sector_rotation.research_fractional import (
    RetrospectiveRotationEngine,
)


class ResearchExitGatekeeper(Gatekeeper):
    """Use the existing executor's quantity-zero boundary, not a looser one."""

    @staticmethod
    def _cancels(current: float, delta: float) -> bool:
        return (
            math.isfinite(current)
            and math.isfinite(delta)
            and current * delta < 0
            and abs(current + delta) <= quantity_zero_tolerance(current, delta)
        )

    def _is_reversal(self, current_qty: float, order_qty_delta: float) -> bool:
        if self._cancels(current_qty, order_qty_delta):
            return False
        return super()._is_reversal(current_qty, order_qty_delta)

    def _is_reducing_order(self, current_qty: float, order_qty_delta: float) -> bool:
        if self._cancels(current_qty, order_qty_delta):
            return True
        return super()._is_reducing_order(current_qty, order_qty_delta)


def bind_quantity_exit(engine: RetrospectiveRotationEngine) -> None:
    """Bind once, before a fresh retrospective Engine runs; production is unchanged."""
    if not isinstance(engine, RetrospectiveRotationEngine) or engine._has_run:
        raise ValueError("QUANTITY_EXIT_FRESH_RESEARCH_ENGINE_REQUIRED")
    old = engine.broker.gatekeeper
    if type(old) is not Gatekeeper:
        raise ValueError("QUANTITY_EXIT_CANONICAL_GATEKEEPER_REQUIRED")
    engine.broker.gatekeeper = ResearchExitGatekeeper(
        old.account,
        old.commission_model,
        cash_buffer_pct=old.cash_buffer_pct,
        settlement_reduces_buying_power=old.settlement_reduces_buying_power,
        multiplier_resolver=old.multiplier_resolver,
        reserved_cash_resolver=old.reserved_cash_resolver,
    )
