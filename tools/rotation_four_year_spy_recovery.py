"""Signal-only SPY recovery plan authorization and canonical cash sizing.

No production gates, risk signals, fees, settlement rules or account state change.
"""

from __future__ import annotations

from ml4t.backtest.strategies.sector_rotation.adapter import RotationPlanManager
from ml4t.backtest.strategies.sector_rotation.research_fractional import (
    _ResearchBroker,
    _share_step_floor,
)
from quant_constraints import Action, Intent, Kind
from quant_constraints.calendar import NY
from quant_constraints.gates import RebalanceGate


def need(ok, code):
    if not ok:
        raise ValueError("FOUR_YEAR_SPY_RECOVERY_" + code)


class RecoveryGate(RebalanceGate):
    """Authorize plan creation only inside a validated causal recovery call."""

    def __init__(self, original):
        super().__init__(original.config, original.calendar)
        self.creation_time = None

    def scheduled(self, state, asof):
        return self.creation_time == asof


class RecoveryPlans(RotationPlanManager):
    def __init__(self, engine):
        super().__init__(engine.broker)
        self.engine, self.observations = engine, []
        self.gate = RecoveryGate(engine.broker.controller.rebalance)
        engine.broker.controller.rebalance = self.gate

    def create(self, targets, *, plan_id=None, valid_until=None, defensive_allocation=False):
        broker, strategy = self.broker, self.engine.strategy
        assert isinstance(broker, _ResearchBroker)
        now = broker.rotation_time
        event = strategy.events[-1] if strategy.events else {}
        signal = event.get("signal_session")
        need(
            now is not None
            and broker.rotation_window(now)
            and event.get("code") == "spy_signal_changed"
            and event.get("reason") == "recovery"
            and event["timestamp"] <= now
            and strategy.book.recovered
            and strategy.book.latest.session == signal
            and strategy.book.calendar.shift(signal, 1) == now.astimezone(NY).date()
            and plan_id == "SPY-" + str(signal)
            and set(targets) == {"SPY"}
            and defensive_allocation is True,
            "PLAN_SCOPE",
        )
        state = broker.constraint_state()
        price = broker.get_last_price("SPY")
        need(price is not None and price > 0 and not state.missing_marks, "MARKS")
        assert price is not None
        held = state.holdings["SPY"].quantity if "SPY" in state.holdings else 0.0
        step = broker.research_quantity_step
        requested = targets["SPY"]
        delta = _share_step_floor(max(0.0, requested * state.equity / price - held), step)
        initial_delta = delta
        attempts, decision = 0, None
        while delta > 0:
            execution = broker.estimate_execution_price("SPY", delta, price)
            fee = broker.estimate_order_fees("SPY", delta, execution, plan_id)
            context = broker.context_for("SPY", execution, "plan_check", fee, price)
            decision = broker.controller.account.check(
                Intent("SPY", delta, kind=Kind.REBALANCE), state, context
            )
            if decision.action is Action.ALLOW:
                break
            need(decision.code in {"cash_reserve", "settled_cash"}, "ACCOUNT_REJECTION")
            delta = _share_step_floor(delta - float(step), step)
            attempts += 1
        row = {
            "signal_session": signal,
            "creation_time": now,
            "plan_id": plan_id,
            "requested_target": requested,
            "requested_delta": initial_delta,
            "funded_delta": delta,
            "cash_rounding_steps": attempts,
            "share_step": str(step),
            "equity_usd": state.equity,
            "settled_cash_usd": state.settled_cash,
            "reserved_cash_usd": state.reserved_cash,
            "unpaid_receivables_usd": broker.account._receivable_value,
            "required_cash_reserve_usd": broker.controller.config.cash_reserve * state.equity,
            "account_decision": decision.code if decision else "no_increase",
            "account_guard_changed": False,
        }
        self.observations.append(row)
        if delta <= 0:
            row["status"] = "NO_SETTLED_CASH_FOR_ONE_QUANTUM"
            broker.audit_plan_rejection("spy_recovery_no_fundable_increase")
            return None
        funded = (held + delta) * price / state.equity
        need(0 < funded <= requested + 1e-12, "FUNDING_INCREASED_TARGET")
        row["funded_target"] = funded
        self.gate.creation_time = now
        try:
            plan = super().create(
                {"SPY": funded},
                plan_id=plan_id,
                valid_until=valid_until,
                defensive_allocation=defensive_allocation,
            )
        finally:
            self.gate.creation_time = None
        row["status"] = "ADMITTED" if plan is not None else "REJECTED_BY_ORIGINAL_PLAN_GUARDS"
        return plan

    def finish(self):
        first, last = [
            self.engine.research_inputs.bars[i].end.astimezone(NY).date() for i in (0, -1)
        ]
        severity = {"normal": 0, "warning": 1, "bear": 2, "deep_bear": 3}
        prior, expected = 0, []
        for day, row in sorted(self.engine.strategy.reference.items()):
            current = severity[row["regime"]]
            execute = self.gate.calendar.shift(day, 1)
            if current < prior and first <= execute <= last:
                expected.append(day)
            prior = current
        need([r["signal_session"] for r in self.observations] == expected, "REFERENCE_COVERAGE")
        return {
            "status": "CAUSAL_RECOVERY_CREATION_AND_CANONICAL_CASH_SIZING_PASS",
            "reference_recovery_sessions": expected,
            "plans": self.observations,
            "calendar_topups_enabled": False,
            "cash_or_settlement_guards_changed": False,
        }


def bind(engine):
    need(not engine._has_run, "FRESH_ENGINE")
    need(not engine.broker.plan_manager.records, "EXISTING_PLANS")
    plans = RecoveryPlans(engine)
    engine.broker.plan_manager = plans
    return plans
