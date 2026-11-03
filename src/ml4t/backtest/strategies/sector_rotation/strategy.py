"""Monthly SPDR rotation decisions; the constrained broker owns all trading state."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Any

from quant_constraints import Intent, Kind
from quant_constraints.calendar import NY
from quant_constraints.models import aware

from ...strategy import Strategy
from ...types import OrderSide, OrderStatus
from .allocation import strength_weights
from .config import SECTOR_ETFS, SectorRotationConfig
from .signals import CausalCloseSource, MarketRegime, SignalBook


@dataclass(frozen=True)
class AllocationDecision:
    signal_session: date
    execute_session: date
    valid_until: datetime
    regime: MarketRegime
    targets: Mapping[str, float]
    entry_assets: frozenset[str]
    scores: Mapping[str, float]

    def __post_init__(self):
        object.__setattr__(self, "targets", MappingProxyType(dict(self.targets)))
        object.__setattr__(self, "scores", MappingProxyType(dict(self.scores)))


class SectorRotationStrategy(Strategy):
    """Use sector_rotation_engine() to bind the cash-account execution adapter.

    Signals are explicitly supplied causal total-return closes. Raw execution
    prices, liquidity, corporate actions and settlement remain broker concerns.
    """

    def __init__(self, source: CausalCloseSource, config: SectorRotationConfig | None = None):
        self.config = config or SectorRotationConfig()
        self.source = source
        self.book = SignalBook(self.config)
        self.decisions: list[AllocationDecision] = []
        self.events: list[dict[str, Any]] = []
        self._offset = 0
        self._monthly: AllocationDecision | None = None
        self._plans: dict[str, AllocationDecision] = {}
        self._monthly_scores: dict[str, float] = {}
        self._risk_pending = False
        self._forced_exits: set[str] = set()
        self._started = False

    def on_start(self, broker: Any) -> None:
        from .adapter import SectorRotationBroker

        if self._started:
            raise ValueError("Create a fresh rotation strategy for each run")
        if not isinstance(broker, SectorRotationBroker) or broker.rotation_strategy is not self:
            raise ValueError("SectorRotationStrategy requires sector_rotation_engine")
        if set(broker.get_positions()) - set(SECTOR_ETFS):
            raise ValueError("Rotation requires a dedicated portfolio of sector ETFs")
        self._started = True

    def _event(self, timestamp: datetime, code: str, **details: Any) -> None:
        self.events.append(
            dict(timestamp=timestamp, code=code, regime=self.book.regime.value, **details)
        )

    def _cancel_plans(self, broker: Any, reason: str) -> None:
        for plan_id in tuple(self._plans):
            plan = broker.rebalance_plans.get(plan_id)
            if plan is not None and plan.active:
                broker.plan_manager.cancel(plan_id, reason)
        self._monthly = None

    def _ingest(self, timestamp: datetime, broker: Any) -> None:
        for row in self.source.available(timestamp, self._offset):
            self._offset += 1
            previous = self.book.regime
            self.book.ingest(row)
            if self.book.regime.severity > previous.severity:
                self._cancel_plans(broker, "rotation_spy_downgrade")
                self._risk_pending = True
                self._event(timestamp, "spy_downgrade", signal_session=row.session)
            if self.book.recovered:
                self._event(timestamp, "spy_monthly_recovery", signal_session=row.session)
            if self.book.is_month_end(row.session):
                # Replay old rows for indicator/state warm-up, never for old trades.
                previous_session = self.book.calendar.shift(timestamp.astimezone(NY).date(), -1)
                if row.session >= previous_session:
                    self._stage_monthly(timestamp, broker, row.session)

    def _stage_monthly(self, timestamp: datetime, broker: Any, session: date) -> None:
        scored = self.book.scores()
        if not self.book.market_ready or scored is None:
            self._event(timestamp, "monthly_signal_unavailable", signal_session=session)
            return
        snapshot = broker.constraint_state()
        if snapshot.missing_marks:
            self._event(timestamp, "monthly_marks_missing", signal_session=session)
            return
        if set(snapshot.holdings) - set(SECTOR_ETFS):
            raise ValueError("Rotation cannot allocate a mixed external portfolio")
        scores, eligible = scored
        self._monthly_scores = scores
        ranked = sorted(scores, key=lambda a: (-scores[a], a))
        ranks = {a: i + 1 for i, a in enumerate(ranked)}
        regime = self.book.regime
        count = regime.names
        retained = [
            a
            for a in ranked
            if a in snapshot.holdings
            and a in eligible
            and ranks[a] <= count + self.config.rank_buffer
        ][:count]
        selected = (
            retained
            + [a for a in ranked if a not in retained and a in eligible and ranks[a] <= count][
                : count - len(retained)
            ]
        )
        invested = sum(h.quantity * h.price for h in snapshot.holdings.values()) / snapshot.equity
        while selected:
            exposure = regime.exposure(self.config) * len(selected) / count
            if regime is not MarketRegime.NORMAL and not self.book.recovered:
                exposure = min(exposure, invested)
            if exposure >= len(selected) * self.config.min_weight - 1e-12:
                break
            selected.pop()
        exposure = regime.exposure(self.config) * len(selected) / count
        if regime is not MarketRegime.NORMAL and not self.book.recovered:
            exposure = min(exposure, invested)
        targets = strength_weights(
            {a: scores[a] for a in selected},
            exposure,
            minimum=self.config.min_weight,
            maximum=self.config.max_weight,
        )
        execute = self.book.calendar.shift(session, 1)
        expiry_session = self.book.calendar.session(
            self.book.calendar.shift(execute, self.config.plan_sessions - 1)
        )
        assert expiry_session is not None
        decision = AllocationDecision(
            session,
            execute,
            expiry_session.market_close,
            regime,
            targets,
            frozenset(set(targets) - snapshot.holdings.keys()),
            scores,
        )
        self._cancel_plans(broker, "rotation_monthly_superseded")
        self._monthly = decision
        self.decisions.append(decision)
        self._event(timestamp, "monthly_allocation", signal_session=session, targets=dict(targets))

    def _before_execution(self, timestamp: datetime, broker: Any) -> None:
        """Called before automatic plan advancement and before pending fills."""
        self._ingest(timestamp, broker)
        for identifier, decision in tuple(self._plans.items()):
            plan = broker.rebalance_plans.get(identifier)
            if plan is None:
                continue
            if plan.active and not self._plan_eligible(timestamp, decision):
                broker.plan_manager.cancel(identifier, "rotation_signal_revalidation")
                self._event(timestamp, "plan_revalidation_canceled", plan_id=identifier)
            plan = broker.rebalance_plans[identifier]
            if plan.status in {"expired", "canceled"}:
                state = broker.constraint_state()
                for asset in decision.entry_assets:
                    held = state.holdings.get(asset)
                    if (
                        held
                        and held.quantity * held.price / state.equity
                        < self.config.min_weight - 1e-10
                    ):
                        self._forced_exits.add(asset)
                del self._plans[identifier]
            elif plan.status == "completed":
                del self._plans[identifier]

    def _plan_eligible(self, timestamp: datetime, decision: AllocationDecision) -> bool:
        if not self.book.market_ready or not self.book.current_for(timestamp):
            return False
        scored = self.book.scores()
        if scored is None or self.book.regime.severity > decision.regime.severity:
            return False
        scores, eligible = scored
        ranks = {a: i + 1 for i, a in enumerate(sorted(scores, key=lambda a: (-scores[a], a)))}
        for asset in decision.targets:
            boundary = self.book.regime.names + (
                0 if asset in decision.entry_assets else self.config.rank_buffer
            )
            if asset not in eligible or ranks[asset] > boundary:
                return False
        return True

    def _execution_targets(self, broker: Any, decision: AllocationDecision) -> dict[str, float]:
        """Reserve estimated costs and one whole-share quantum at holding bounds."""
        state = broker.constraint_state()
        if not decision.targets:
            return {}
        lower, upper = {}, {}
        costs = 0.0
        slips = 0.0
        for asset, target in decision.targets.items():
            price = broker.get_last_price(asset)
            if price is None or price <= 0:
                raise ValueError("Execution price unavailable")
            current = state.holdings.get(asset)
            quantity = target * state.equity / price - (current.quantity if current else 0)
            estimated = broker.estimate_execution_price(asset, quantity, price)
            costs += broker.estimate_order_fees(asset, quantity, estimated)
            slips = max(slips, abs(estimated / price - 1))
            quantum = price / state.equity if broker.share_type.value == "integer" else 0.0
            lower[asset] = self.config.min_weight + quantum
            upper[asset] = self.config.max_weight - quantum
        # Include exits too, as their commissions reduce the allocation NAV.
        for asset, held in state.holdings.items():
            if asset not in decision.targets:
                costs += broker.estimate_order_fees(asset, -held.quantity, held.price)
        cushion = costs / state.equity + slips + self.config.execution_cushion_bps / 10000
        for asset in upper:
            upper[asset] -= cushion
        exposure = min(sum(decision.targets.values()) - cushion, sum(upper.values()))
        return strength_weights(
            {a: decision.scores[a] for a in decision.targets},
            exposure,
            minimum=self.config.min_weight,
            maximum=self.config.max_weight,
            lower_bounds=lower,
            upper_bounds=upper,
        )

    def _submit_risk(self, timestamp: datetime, broker: Any) -> bool:
        snapshot = broker.constraint_state()
        if snapshot.missing_marks:
            return False
        self._forced_exits.intersection_update(snapshot.holdings)
        held = sorted(snapshot.holdings, key=lambda a: (-self._monthly_scores.get(a, 0), a))
        held = [
            a
            for a in held
            if a not in self._forced_exits
            and snapshot.holdings[a].quantity * snapshot.holdings[a].price / snapshot.equity
            >= self.config.min_weight - 1e-12
        ][: self.book.regime.names]
        caps = {
            a: min(
                self.config.max_weight,
                snapshot.holdings[a].quantity * snapshot.holdings[a].price / snapshot.equity,
            )
            for a in held
        }
        exposure = min(
            self.book.regime.exposure(self.config) * len(held) / self.book.regime.names,
            sum(caps.values()),
        )
        targets = strength_weights(
            {a: self._monthly_scores.get(a, 0) for a in held},
            exposure,
            minimum=self.config.min_weight,
            maximum=self.config.max_weight,
            upper_bounds=caps,
        )
        submitted = False
        for asset, holding in snapshot.holdings.items():
            target_quantity = targets.get(asset, 0) * snapshot.equity / holding.price
            if broker.share_type.value == "integer":
                target_quantity = math.floor(target_quantity + 1e-10)
                if (
                    target_quantity * holding.price / snapshot.equity
                    < self.config.min_weight - 1e-10
                ):
                    target_quantity = 0
            delta = min(0.0, target_quantity - holding.quantity)
            if delta < -1e-8 and not broker.get_pending_orders(asset):
                order = broker.submit_intent(Intent(asset, delta, kind=Kind.RISK))
                submitted = submitted or (order is not None and order.status is OrderStatus.PENDING)
        if submitted:
            self._event(timestamp, "risk_sell_only", targets=targets)
        self._risk_pending = submitted or any(
            o.side is OrderSide.SELL for o in broker.get_pending_orders()
        )
        return submitted

    def on_data(
        self, timestamp: datetime, data: dict[str, dict], context: dict[str, Any], broker: Any
    ) -> None:
        aware(timestamp)
        self._before_execution(timestamp, broker)
        if not broker.rotation_window(timestamp):
            return
        if self._risk_pending or self._forced_exits:
            self._submit_risk(timestamp, broker)
            return
        decision = self._monthly
        if decision is None:
            return
        today = timestamp.astimezone(NY).date()
        if timestamp >= decision.valid_until:
            self._event(timestamp, "monthly_plan_expired")
            self._monthly = None
            return
        if today < decision.execute_session:
            return
        # A missed creation window does not authorize a new non-monthly plan.
        if today != decision.execute_session or not self._plan_eligible(timestamp, decision):
            self._event(timestamp, "monthly_plan_not_executable")
            self._monthly = None
            return
        try:
            targets = self._execution_targets(broker, decision)
        except ValueError as error:
            self._event(timestamp, "execution_allocation_infeasible", reason=str(error))
            self._monthly = None
            return
        identifier = f"SPDR-{decision.signal_session.isoformat()}"
        self._plans[identifier] = decision
        plan = broker.create_rebalance_plan(
            targets,
            rebalance_id=identifier,
            valid_until=decision.valid_until,
            defensive_allocation=True,
        )
        self._monthly = None
        self._event(
            timestamp,
            "monthly_plan_created" if plan else "monthly_plan_rejected",
            plan_id=identifier,
            executable_targets=targets,
        )
