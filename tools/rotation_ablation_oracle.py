"""Independent Decimal signal/allocation oracle for the registered P/S/R study.

Extends the retained independent oracle, never a production signal/fee helper.
The verified historical ledger remains responsible for fills and settlement.
"""

from __future__ import annotations

import math
from decimal import Decimal

from tools import rotation_decision_oracle as o

D = o.D


class AblationOracle(o.DecisionOracle):
    def __init__(self, engine, *, sector_rotation: bool, risk_tiers: bool):
        self.step = engine.research_assumptions.share_step
        super().__init__(engine)
        self.shadow_reference = {day: row["regime"] for day, row in self.reference.items()}
        first = engine.research_inputs.bars[0].end.astimezone(o.NY).date()
        freeze = o.CAL.shift(first, -1)
        while o.CAL.shift(freeze, 1).month == freeze.month:
            freeze = o.CAL.shift(freeze, 1)
        frozen = self.reference[freeze]
        o.require(bool(frozen["scores"]), "NO_INITIAL_SECTOR_REFERENCE")
        for day, row in self.reference.items():
            if day >= freeze and not sector_rotation:
                row["scores"] = dict(frozen["scores"])
                row["eligible"] = set(frozen["eligible"])
            if not risk_tiers:
                row.update(regime="normal", severity=0, recovered=False)

    def finish(self):
        report = super().finish()
        report["shadow_risk_signals"] = [
            {
                "signal_session": row["signal_session"],
                "regime": self.shadow_reference[row["signal_session"]],
            }
            for row in report["signals"]
        ]
        return report

    def fee(self, quantity, price):
        q, p = abs(D(quantity)), D(price)
        if not q:
            return Decimal(0)
        o.require(p >= 1 and q <= 300000, "FEE_PRICE_SCOPE")
        # Independent first-tier reservation; a fresh minimum, as in the broker.
        broker = min(max(q * D(".0035"), D(".35")), q * p / 100)
        venue = (q // 1) * D(".0035")
        regulatory = q * D(".000003")
        if quantity < 0:
            regulatory += q * p * D(".0000206") + min(q * D(".000195"), D("9.79"))
        return broker * D("1.000735") + venue + q * D(".0002") + regulatory

    def _targets(self, original):
        def wrapped(broker, decision):
            expected = self.monthly[decision.signal_session]
            state = broker.constraint_state()
            nav = D(state.equity)
            lo, hi, fees = {}, {}, Decimal(0)
            slip = D(self.engine.research_assumptions.slippage_bps) / 10000
            for asset, weight in expected["targets"].items():
                price = D(broker.get_last_price(asset))
                held = D(state.holdings[asset].quantity) if asset in state.holdings else D(0)
                delta = weight * nav / price - held
                fees += self.fee(delta, price * (1 + slip if delta > 0 else 1 - slip))
                lo[asset] = D(self.settings.min_weight) + price * self.step / nav
                hi[asset] = D(self.settings.max_weight) - price * self.step / nav
            for asset, holding in state.holdings.items():
                if asset not in expected["targets"]:
                    fees += self.fee(-holding.quantity, holding.price)
            cushion = fees / nav + slip + D(self.settings.execution_cushion_bps) / 10000
            hi = {asset: value - cushion for asset, value in hi.items()}
            weights = (
                o.bounded_weights(
                    {asset: expected["scores"][asset] for asset in expected["targets"]},
                    min(sum(expected["targets"].values()) - cushion, sum(hi.values())),
                    self.settings.min_weight,
                    self.settings.max_weight,
                    lower=lo,
                    upper=hi,
                )
                if expected["targets"]
                else {}
            )
            result = original(broker, decision)
            o.equal_mapping(result, weights, "EXECUTION_TARGETS")
            self.execution_targets.append(
                {
                    "timestamp": broker.rotation_time,
                    "signal_session": decision.signal_session,
                    "weights": weights,
                }
            )
            return result

        return wrapped

    def _risk(self, original):
        def wrapped(stamp, broker):
            _, ref = self.row_at(stamp)
            state = broker.constraint_state()
            scores = next(reversed(self.monthly.values()))["scores"] if self.monthly else {}
            forced = self.strategy._forced_exits & state.holdings.keys()
            o.require(
                all(
                    D(state.holdings[a].quantity) * D(state.holdings[a].price) / D(state.equity)
                    < D(self.settings.min_weight) + o.NUMERIC_TOLERANCE
                    for a in forced
                ),
                "FORCED_EXIT_SCOPE",
            )
            held = [
                a
                for a in sorted(state.holdings, key=lambda a: (-scores.get(a, 0), a))
                if a not in forced
                and D(state.holdings[a].quantity) * D(state.holdings[a].price) / D(state.equity)
                >= D(self.settings.min_weight) - D("1E-12")
            ][: o.NAMES[ref["severity"]]]
            caps = {
                a: min(
                    D(self.settings.max_weight),
                    D(state.holdings[a].quantity) * D(state.holdings[a].price) / D(state.equity),
                )
                for a in held
            }
            total = min(
                o.exposure(self.settings, ref["severity"]) * len(held) / o.NAMES[ref["severity"]],
                sum(caps.values()),
            )
            weights = o.bounded_weights(
                {a: scores.get(a, 0) for a in held},
                total,
                self.settings.min_weight,
                self.settings.max_weight,
                upper=caps,
            )
            expected = {}
            for asset, holding in state.holdings.items():
                units = weights.get(asset, 0) * D(state.equity) / D(holding.price) / self.step
                quantity = D(math.floor(units + D("1E-10"))) * self.step
                if quantity * D(holding.price) / D(state.equity) < D(self.settings.min_weight) - D(
                    "1E-10"
                ):
                    quantity = D(0)
                delta = min(D(0), quantity - D(holding.quantity))
                if delta < D("-1E-8") and not broker.get_pending_orders(asset):
                    expected[asset] = delta
            start = len(self.orders)
            self.scope, self.risk_expected = "strategy_risk", expected
            try:
                result = original(stamp, broker)
            finally:
                self.scope, self.risk_expected = None, None
            submitted = {row["asset"]: row["requested"] for row in self.orders[start:]}
            o.equal_mapping(submitted, expected, "RISK_SUBMISSION_COMPLETENESS")
            self.risk_calls.append({"timestamp": stamp, "expected_orders": expected})
            return result

        return wrapped

    def _plan_audit(self, original):
        def wrapped(plan, action, code):
            if code == "rebalance_wait_settlement":
                broker = self.engine.broker
                state = broker.constraint_state()
                available = D(state.settled_cash) - D(state.reserved_cash)
                blocked = []
                nav = D(state.equity)
                for asset, weight in plan.targets.items():
                    price = D(broker.get_last_price(asset))
                    held = D(state.holdings[asset].quantity) if asset in state.holdings else D(0)
                    quantity = D(weight) * nav / price - held
                    if quantity <= 0:
                        continue
                    estimate = price * (
                        1 + D(self.engine.research_assumptions.slippage_bps) / 10000
                    )
                    required = quantity * estimate + self.fee(quantity, estimate)
                    if required > available - D(broker.controller.config.cash_reserve) * nav + D(
                        "1E-8"
                    ):
                        blocked.append(asset)
                o.require(broker.unsettled_cash > 0 and bool(blocked), "FALSE_SETTLEMENT_BLOCK")
                self.cash_blocks.append(
                    {
                        "timestamp": broker.rotation_time,
                        "assets": blocked,
                        "settled_available": available,
                        "unsettled_cash": D(broker.unsettled_cash),
                    }
                )
            return original(plan, action, code)

        return wrapped
