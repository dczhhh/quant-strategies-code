"""Executable SPY-only use of the unchanged causal market risk signal."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from ml4t.backtest.strategies.sector_rotation.research_fractional import (
    RetrospectiveRotationEngine,
    _ResearchBroker,
    _share_step_floor,
)
from ml4t.backtest.strategies.sector_rotation.research_fractional_benchmark import (
    _PassiveSPY,
    retrospective_spy_benchmark,
)
from ml4t.backtest.strategies.sector_rotation.signals import SignalBook
from quant_constraints import Intent, Kind
from quant_constraints.calendar import NY
from tools import rotation_decision_oracle as o


class TimedSPY(_PassiveSPY):
    def __init__(self, inputs, settings=None):
        super().__init__(inputs)
        if settings is not None:
            self.config = settings
            self.book = SignalBook(settings)
        self.reference = o.reference_signals(inputs, self.config)
        self.pending = None
        self.observed_signals = []

    def _before_execution(self, timestamp, broker):
        previous_day = self.book.calendar.shift(timestamp.astimezone(NY).date(), -1)
        for row in self.source.available(timestamp, self._offset):
            self._offset += 1
            before = self.book.regime
            self.book.ingest(row)
            ref = self.reference[row.session]
            o.require(self.book.regime.value == ref["regime"], "SPY_RISK_STATE")
            self.observed_signals.append({"signal_session": row.session, "regime": ref["regime"]})
            if row.session < previous_day:
                continue
            if self.book.regime.severity > before.severity:
                for identifier, plan in tuple(broker.rebalance_plans.items()):
                    if plan.active:
                        broker.plan_manager.cancel(identifier, "spy_downgrade")
                self.pending = row.session, "risk"
            if self.book.is_month_end(row.session):
                self.pending = row.session, "monthly"

    def on_data(self, timestamp, data, context, broker):
        self._before_execution(timestamp, broker)
        if not broker.rotation_window(timestamp) or broker.get_pending_orders("SPY"):
            return
        if not self.attempted:
            if self.book.latest is None:
                raise ValueError("Timed SPY requires previous completed-close history")
            self.pending = self.book.latest.session, "initial"
        if self.pending is None:
            return
        signal_day, reason = self.pending
        self.pending = None
        state = broker.constraint_state()
        price = broker.get_last_price("SPY")
        if price is None or state.missing_marks:
            raise ValueError("Timed SPY requires complete observed marks")
        exposure = self.book.regime.exposure(self.config)
        slip = float(broker.research_provider.assumptions.slippage_bps / 10000)
        desired = _share_step_floor(
            state.equity * (exposure - self.config.execution_cushion_bps / 10000 - slip) / price,
            broker.research_quantity_step,
        )
        held = state.holdings["SPY"].quantity if "SPY" in state.holdings else 0
        target = desired * price / state.equity
        if reason == "risk":
            delta = min(0, desired - held)
            if delta < -1e-8:
                broker.submit_intent(Intent("SPY", delta, kind=Kind.RISK))
        elif reason == "initial":
            estimated = price * (1 + slip)
            while desired > 0 and desired * estimated + broker.estimate_order_fees(
                "SPY", desired, estimated
            ) > state.equity * (exposure - self.config.execution_cushion_bps / 10000):
                desired = _share_step_floor(
                    desired - float(broker.research_quantity_step), broker.research_quantity_step
                )
            last = self.book.calendar.session(
                self.book.calendar.shift(timestamp.astimezone(NY).date(), 2)
            )
            broker.submit_intent(
                Intent(
                    "SPY",
                    desired,
                    kind=Kind.ENTRY,
                    target_weight=desired * price / state.equity,
                    valid_until=last.market_close,
                    time_in_force="GTD",
                )
            )
            self.attempted = True
        else:
            execute = self.book.calendar.shift(signal_day, 1)
            if timestamp.astimezone(NY).date() != execute:
                raise ValueError("Missed timed SPY monthly creation window")
            expiry = self.book.calendar.session(self.book.calendar.shift(execute, 2))
            broker.create_rebalance_plan(
                {"SPY": target},
                rebalance_id="SPY-" + str(signal_day),
                valid_until=expiry.market_close,
                defensive_allocation=True,
            )


def timed_spy_engine(inputs, assumptions, settings=None):
    # Reuse an unstarted benchmark's exact feed/controller/broker, with a new
    # explicit strategy binding and Engine lifecycle dispatcher.
    blueprint = retrospective_spy_benchmark(inputs, assumptions)
    strategy = TimedSPY(inputs, settings)
    broker = blueprint.broker
    assert isinstance(broker, _ResearchBroker)
    broker.bind_rotation(strategy)
    engine = RetrospectiveRotationEngine(
        blueprint.feed, strategy, blueprint.config, broker_factory=lambda *_args, **_kwargs: broker
    )
    engine.research_inputs, engine.research_assumptions = inputs, assumptions
    engine.research_trace = []
    engine.research_report = deepcopy(blueprint.research_report)
    engine.research_report.update(
        schema="rotation_timed_spy_research_v1",
        benchmark_policy={
            "signal": "unchanged_causal_SPY_risk_tiers",
            "monthly": "next_session_after_month_end",
            "downgrade": "next_legal_minute_sell_only",
            "initial": "first_execution_window_using_previous_completed_close",
            "exposure_matched": False,
            "cash_interest": Decimal(0),
        },
    )
    return engine
