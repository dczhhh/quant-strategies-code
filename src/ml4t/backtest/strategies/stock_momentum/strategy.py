"""Opt-in public Engine integration using the canonical constrained cash broker."""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from types import MappingProxyType
from typing import Any

from quant_constraints import ConstraintConfig, ConstraintController, cash_backtest_config
from quant_constraints.adapter import ConstrainedBroker
from quant_constraints.calendar import NY
from quant_constraints.events import EarningsProvider
from quant_constraints.models import Action, Intent, Kind
from quant_constraints.plans import RebalancePlanManager

from ... import BacktestConfig, Engine, Strategy
from ...config import ShareType
from ...types import ExecutionMode
from .signals import MomentumDecision, SnapshotSource, StockMomentumConfig, select_stocks


def constraints_for(config: StockMomentumConfig) -> ConstraintConfig:
    return ConstraintConfig(
        cash_reserve=1 - config.exposure,
        min_target_weight=config.min_weight,
        max_weight=config.max_weight,
        equity_target=config.exposure,
        industry_cap=config.industry_cap,
        preferred_min_names=1,
        max_names=config.names,
        allow_defensive_underinvested=True,
        weekly_entries=config.names * 2,
        rebalance_mode=config.frequency,
        monthly_session="first",
        rebalance_plan_enabled=True,
        rebalance_plan_sessions=config.plan_sessions,
        signal_data_mode="total_return_signal",
        dividend_withholding_rate=0.10,
        dividend_tax_scenario="us_ordinary_mainland_china_10pct",
        weight_change_threshold=0.01,
    )


class StockMomentumStrategy(Strategy):
    """12-1 stock selector; optional daily SPY exit, scheduled re-entry only."""

    def __init__(self, source: SnapshotSource, config: StockMomentumConfig | None = None):
        self.source = source
        self.config = config or StockMomentumConfig()
        self.decisions: list[MomentumDecision] = []
        self.events: list[tuple[datetime, str]] = []
        self._attempted: set = set()
        self._latest = None
        self._decision: MomentumDecision | None = None
        self._ready = False

    def on_prepare(self, broker: Any, config: BacktestConfig | None = None) -> None:
        if not isinstance(broker, StockMomentumBroker):
            raise ValueError("Use stock_momentum_engine; an unconstrained broker is unsupported")
        if self._attempted or self.decisions:
            raise ValueError("Create a fresh strategy for each run")

    def before_execution(self, now: datetime, broker: "StockMomentumBroker") -> None:
        snapshot = self.source.latest(now)
        day = now.astimezone(NY).date()
        expected = broker.controller.calendar.shift(day, -1)
        self._ready = snapshot is not None and snapshot.session == expected
        if self._ready and snapshot is not self._latest:
            self._latest = snapshot
            assert snapshot is not None
            # Invalid data never becomes a liquidation signal. Abort or skip is explicit.
            self._decision = select_stocks(snapshot, self.config)
        for identifier, plan in tuple(broker.rebalance_plans.items()):
            if not plan.active:
                continue
            invalid = not self._ready or self._decision is None or self._decision.risk_off
            if self._ready and self._decision is not None:
                invalid |= any(
                    a not in self._decision.scores for a, w in plan.targets.items() if w > 0
                )
            if invalid:
                broker.plan_manager.cancel(identifier, "momentum_signal_unavailable_or_ineligible")

    def on_data(
        self, timestamp: datetime, data: dict[str, dict], context: dict[str, Any], broker: Any
    ) -> None:
        assert isinstance(broker, StockMomentumBroker)
        self.before_execution(timestamp, broker)
        if not self._ready or self._decision is None or not broker.in_window(timestamp):
            return
        state = broker.constraint_state()
        if self._decision.risk_off:
            for asset, holding in state.holdings.items():
                if not state.pending_sells.get(asset):
                    broker.submit_intent(Intent(asset, -holding.quantity, kind=Kind.RISK))
        day = timestamp.astimezone(NY).date()
        if day in self._attempted or not broker.controller.rebalance.scheduled(state, timestamp):
            return
        self._attempted.add(day)
        self.decisions.append(self._decision)
        if self._decision.risk_off or broker.get_pending_orders():
            return
        targets = dict(self._decision.targets)
        # No replacement with the sixth-ranked stock after seeing an execution rejection.
        # Preserve a vacant cash slot and recheck the whole plan at submission/fill.
        for asset in tuple(targets):
            ctx = broker.context_for(
                asset, data.get(asset, {}).get("close", 0), "momentum_selection"
            )
            earnings = broker.controller.earnings.check(Intent(asset, 1), state, ctx)
            if earnings.action is not Action.ALLOW:
                del targets[asset]
                self.events.append((timestamp, f"{asset}:{earnings.code}"))
        plan = broker.create_rebalance_plan(
            targets, rebalance_id=f"STOCK-MOM-{day}", defensive_allocation=True
        )
        self.events.append((timestamp, "plan_created" if plan else "plan_rejected"))


class MomentumPlanManager(RebalancePlanManager):
    def advance(self) -> None:
        broker = self.broker
        assert isinstance(broker, StockMomentumBroker)
        now = broker._market_state.time
        assert now is not None
        for identifier, plan in tuple(self.records.items()):
            if plan.active and now >= plan.valid_until:
                self.cancel(identifier, "rebalance_plan_expired", expired=True)
        if broker.in_window(now):
            super().advance()


class StockMomentumBroker(ConstrainedBroker):
    momentum_strategy: StockMomentumStrategy

    def in_window(self, now: datetime) -> bool:
        bounds = self.controller.calendar.bounds(now)
        return bool(
            bounds
            and bounds[0] + timedelta(minutes=30)
            <= now
            < min(bounds[1], bounds[0] + timedelta(minutes=120))
        )

    def _process_orders(self, *args: Any, **kwargs: Any):
        now = self._market_state.time
        assert now is not None
        self.momentum_strategy.before_execution(now, self)
        if not self.in_window(now):
            # Earnings/drift risk reductions still run during legal RTH, including late day.
            permitted = {
                o.order_id
                for o in self.get_pending_orders()
                if self.intent_for(o).kind is Kind.RISK
            }
            if kwargs.get("order_ids") is not None:
                permitted &= kwargs["order_ids"]
            kwargs["order_ids"] = permitted
        return super()._process_orders(*args, **kwargs)

    def submit_order(self, asset, quantity, *args: Any, **kwargs: Any):
        # Plan deltas are rounded toward zero so they cannot exceed authorized targets.
        # Full exits retain broker-owned residuals created by corporate actions.
        if kwargs.get("constraint_kind") is Kind.REBALANCE:
            held = self.account.get_position_quantity(asset)
            options = kwargs.get("_options")
            identifier = options.rebalance_id if options else None
            full_exit = quantity < 0 and abs(quantity + held) < 1e-8
            if not full_exit:
                quantity = float(
                    Decimal(str(quantity)).quantize(Decimal("0.1"), rounding=ROUND_DOWN)
                )
            if quantity == 0:
                if identifier in self.plan_manager.records:
                    plan = self.plan_manager.records[identifier]
                    self.plan_manager.records[identifier] = replace(
                        plan, completed_assets=plan.completed_assets | {asset}
                    )
                return None
            # Bind the rounded order to its actual weight, then revalidate the whole
            # allocation. The source decision retains the original nominal weights.
            if identifier in self.plan_manager.records:
                plan = self.plan_manager.records[identifier]
                effective = (
                    (held + quantity)
                    * self._market_state.prices[asset]
                    / self.constraint_state().equity
                )
                targets = {**plan.targets, asset: effective}
                active = {a: w for a, w in targets.items() if w > 0}
                now = self._market_state.time
                assert now is not None
                sectors = {a: self.context_provider(now, a, "plan_check").sector for a in active}
                check = self.controller.check_targets(
                    active,
                    {a: s for a, s in sectors.items() if s is not None},
                    defensive_allocation=True,
                )
                if check.action is not Action.ALLOW:
                    self.plan_manager.cancel(identifier, "momentum_rounded_target_invalid")
                    return None
                self.plan_manager.records[identifier] = replace(
                    plan, targets=MappingProxyType(targets)
                )
                kwargs["target_weight"] = effective
        return super().submit_order(asset, quantity, *args, **kwargs)


def stock_momentum_engine(
    feed: Any,
    strategy: StockMomentumStrategy,
    context_provider: Any,
    earnings: EarningsProvider,
    config: BacktestConfig | None = None,
    *,
    validation_mode: str = "synthetic",
) -> Engine:
    """V1 synthetic integration only; does not certify real research inputs or fee parity.

    Historical constituent/earnings/total-return provenance and the separately
    accepted fractional-fee runtime must be bound before real-data admission.
    """
    if validation_mode != "synthetic" or any(
        s.evidence_mode != "synthetic" for s in strategy.source.snapshots
    ):
        raise ValueError("V1 engine accepts explicit synthetic fixtures only")
    constraints = constraints_for(strategy.config)
    controller = ConstraintController(constraints, earnings)
    config = config or cash_backtest_config(share_type=ShareType.FRACTIONAL)
    if (
        config.execution_mode is not ExecutionMode.NEXT_BAR
        or config.share_type is not ShareType.FRACTIONAL
    ):
        raise ValueError("Stock momentum requires NEXT_BAR and fractional shares")

    def guarded_context(now, asset, phase):
        ctx = context_provider(now, asset, phase)
        snapshot = strategy.source.latest(now)
        stock = snapshot.stocks.get(asset) if snapshot else None
        # Explicit stock classification is checked at every submission and fill.
        if (
            stock is None
            or stock.available_at > now
            or stock.country != "US"
            or stock.security_type != "CS"
        ):
            return replace(ctx, instrument="unknown")
        if ctx.instrument != "equity" or ctx.sector != stock.industry:
            raise ValueError("Execution metadata must match the stock snapshot")
        return ctx

    def factory(settings: BacktestConfig, **kwargs: Any):
        broker = StockMomentumBroker.from_config(settings, **kwargs)
        assert isinstance(broker, StockMomentumBroker)
        broker.configure_constraints(controller, guarded_context, settings)
        broker.momentum_strategy = strategy
        broker.plan_manager = MomentumPlanManager(broker)
        return broker

    return Engine(feed, strategy, config, broker_factory=factory)
