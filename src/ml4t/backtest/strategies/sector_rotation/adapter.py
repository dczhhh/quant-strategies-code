"""Opt-in plan/window adapter over the existing canonical constrained broker."""

from datetime import datetime, timedelta
from typing import Any

from quant_constraints import ConstraintController, InMemoryEarningsProvider, cash_backtest_config
from quant_constraints.adapter import ConstrainedBroker
from quant_constraints.models import aware
from quant_constraints.plans import RebalancePlanManager

from ... import BacktestConfig, Engine
from ...types import ExecutionMode
from .strategy import SectorRotationStrategy


class RotationPlanManager(RebalancePlanManager):
    def advance(self) -> None:
        broker = self.broker
        assert isinstance(broker, SectorRotationBroker)
        now = broker.rotation_time
        assert now is not None
        # Terminal expiry still runs after the execution window has closed.
        for identifier, plan in tuple(self.records.items()):
            if plan.active and now >= plan.valid_until:
                self.cancel(identifier, "rebalance_plan_expired", expired=True)
        if broker.rotation_window(now):
            super().advance()


class SectorRotationBroker(ConstrainedBroker):
    """Adds timing and signal revalidation, never a replacement cash ledger."""

    rotation_strategy: SectorRotationStrategy

    @property
    def rotation_time(self) -> datetime | None:
        """Read-only observed event time for the plan collaborator."""
        return self._market_state.time

    def bind_rotation(self, strategy: SectorRotationStrategy) -> None:
        self.rotation_strategy = strategy
        self.plan_manager = RotationPlanManager(self)

    def rotation_window(self, asof: datetime) -> bool:
        bounds = self.controller.calendar.bounds(aware(asof))
        config = self.rotation_strategy.config
        return bool(
            bounds
            and bounds[0] + timedelta(minutes=config.execution_start_minutes)
            <= asof
            < min(bounds[1], bounds[0] + timedelta(minutes=config.execution_end_minutes))
        )

    def _process_orders(self, *args: Any, **kwargs: Any):
        now = self._market_state.time
        assert now is not None
        self.rotation_strategy._before_execution(now, self)
        if not self.rotation_window(now):
            # The canonical executor sees no selected orders outside the window.
            # Its session, account, cost and order-expiry rules remain unchanged.
            kwargs["order_ids"] = set()
        return super()._process_orders(*args, **kwargs)


def sector_rotation_engine(
    feed: Any,
    strategy: SectorRotationStrategy,
    context_provider: Any,
    config: BacktestConfig | None = None,
    *,
    controller: ConstraintController | None = None,
    corporate_action_provider: Any = None,
    fee_model: Any = None,
) -> Engine:
    """Build the existing public Engine with the opt-in rotation broker.

    context_provider must supply real/PIT ETF liquidity and explicit instrument
    metadata. No quotes, relative volume, total returns or dividends are invented.
    """
    settings = strategy.config
    controller = controller or ConstraintController(
        settings.constraints(corporate_actions_enabled=corporate_action_provider is not None),
        InMemoryEarningsProvider(),
    )
    constraints = controller.config
    if (
        abs(constraints.cash_reserve - (1 - settings.normal_exposure)) > 1e-12
        or constraints.min_target_weight != settings.min_weight
        or constraints.max_weight != settings.max_weight
        or constraints.equity_target != settings.normal_exposure
        or constraints.rebalance_mode != "monthly"
        or constraints.monthly_session != "first"
        or not constraints.rebalance_plan_enabled
        or constraints.rebalance_plan_sessions != settings.plan_sessions
        or constraints.signal_data_mode != "total_return_signal"
        or constraints.entry_delay_minutes != settings.execution_start_minutes
        or constraints.entry_close_buffer_minutes != 0
        or constraints.max_names != 5
        or constraints.weekly_entries < 5
    ):
        raise ValueError("Controller does not match the rotation execution contract")
    config = config or cash_backtest_config()
    if config.execution_mode is not ExecutionMode.NEXT_BAR:
        raise ValueError("Rotation requires NEXT_BAR causal execution")

    def factory(backtest_config: BacktestConfig, **kwargs: Any) -> SectorRotationBroker:
        broker = SectorRotationBroker.from_config(backtest_config, **kwargs)
        assert isinstance(broker, SectorRotationBroker)
        broker.configure_constraints(
            controller, context_provider, backtest_config, fee_model, corporate_action_provider
        )
        broker.bind_rotation(strategy)
        return broker

    return Engine(feed, strategy, config, broker_factory=factory)
