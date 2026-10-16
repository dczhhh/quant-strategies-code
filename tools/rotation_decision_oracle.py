"""Read-only independent decision arithmetic, conditioned on verified past ledger state.

No SignalBook, strategy allocation helper, production fee helper or total-return
builder is used to form expected results. This is not a second trading engine.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, localcontext
from types import SimpleNamespace

from quant_constraints.calendar import NY, SessionCalendar

CAL = SessionCalendar()
REGIMES = ("normal", "warning", "bear", "deep_bear")
NAMES = (5, 4, 2, 1)
NUMERIC_TOLERANCE = Decimal("1E-10")


def D(value):
    return Decimal(str(value))


class DecisionOracleMismatch(RuntimeError):
    """Never swallowed as an ordinary strategy allocation infeasibility."""


def require(condition, code):
    if not condition:
        raise DecisionOracleMismatch("DECISION_ORACLE_" + code)


def equal(actual, expected, field, tolerance=NUMERIC_TOLERANCE):
    require(abs(D(actual) - D(expected)) <= tolerance, field)


def equal_mapping(actual, expected, field):
    require(set(actual) == set(expected), field + "_ASSETS")
    for asset in expected:
        equal(actual[asset], expected[asset], field)


def bounded_weights(scores, exposure, minimum, maximum, *, lower=None, upper=None):
    """Exact Decimal breakpoint allocation, distinct from production's residual loops."""
    if not scores:
        equal(exposure, 0, "EMPTY_EXPOSURE")
        return {}
    assets = sorted(scores)
    lo = {a: D(lower[a]) if lower else D(minimum) for a in assets}
    hi = {a: D(upper[a]) if upper else D(maximum) for a in assets}
    remaining = D(exposure) - sum(lo.values())
    require(
        remaining >= -NUMERIC_TOLERANCE and D(exposure) <= sum(hi.values()) + NUMERIC_TOLERANCE,
        "INFEASIBLE_ALLOCATION",
    )
    result = dict(lo)
    baseline = min(D(v) for v in scores.values())
    strengths = {a: D(scores[a]) - baseline for a in assets}
    for positive in (True, False):
        weights = {
            a: (strengths[a] if positive else Decimal(1))
            for a in assets
            if (strengths[a] > 0) == positive and hi[a] > lo[a]
        }
        active = set(weights)
        previous = Decimal(0)
        for point in sorted({(hi[a] - lo[a]) / weights[a] for a in active}):
            capacity = sum(weights[a] for a in sorted(active))
            step = (point - previous) * capacity
            if remaining <= step:
                level = previous + max(Decimal(0), remaining) / capacity
                for a in active:
                    result[a] = lo[a] + level * weights[a]
                return result
            remaining -= step
            capped = {a for a in active if (hi[a] - lo[a]) / weights[a] == point}
            for a in capped:
                result[a] = hi[a]
            active -= capped
            previous = point
    require(remaining <= NUMERIC_TOLERANCE, "UNALLOCATED_EXPOSURE")
    return result


def reference_signals(inputs, settings, *, close_overrides=None):
    """Original-share reinvestment and full chronological risk evolution from raw sources."""
    with localcontext() as context:
        context.prec = 60
        raw = defaultdict(dict)
        div, splits = defaultdict(Decimal), defaultdict(lambda: Decimal(1))
        for row in inputs.closes:
            value = (close_overrides or {}).get((row.session, row.asset), row.close)
            raw[row.session][row.asset] = D(value)
        for event in inputs.distributions:
            div[event.ex_date, event.asset] += D(event.cash_amount)
        for event in inputs.splits:
            splits[event.session, event.asset] *= D(event.new_per_old)
        history, references, previous = [], {}, None
        values = {asset: Decimal(1) for asset in next(iter(raw.values()))}
        regime = below = above = 0
        for day, prices in sorted(raw.items()):
            if previous is not None:
                values = {
                    a: values[a] * splits[day, a] * (prices[a] + div[day, a]) / previous[a]
                    for a in prices
                }
            history.append(dict(values))
            rows = history[-settings.history_sessions :]
            recovered = False
            market_ready = len(rows) >= max(settings.long_ma, settings.peak_lookback)
            if len(rows) >= settings.long_ma:
                current = rows[-1]["SPY"]
                long = sum(r["SPY"] for r in rows[-settings.long_ma :]) / settings.long_ma
                short = sum(r["SPY"] for r in rows[-settings.short_ma :]) / settings.short_ma
                dd = current / max(r["SPY"] for r in rows[-settings.peak_lookback :]) - 1
                below = below + 1 if current < long else 0
                above = above + 1 if current > long else 0
                requested = 0
                if current < long and dd <= -D(settings.deep_drawdown):
                    requested = 3
                elif (
                    below >= settings.down_confirm
                    and current <= (1 - D(settings.trend_buffer)) * long
                ):
                    requested = 2 if short < long else 1
                if requested > regime:
                    regime = requested
                elif (
                    market_ready
                    and CAL.shift(day, 1).month != day.month
                    and above >= settings.recovery_confirm
                    and current >= (1 + D(settings.trend_buffer)) * long
                ):
                    restored = 0 if short >= long else 1
                    if restored < regime:
                        regime, recovered = restored, True
            else:
                below = above = 0
            scores, eligible = {}, set()
            if len(rows) >= max(max(settings.lookbacks) + 1, settings.long_ma):
                for a in sorted(set(prices) - {"SPY"}):
                    scores[a] = sum(
                        D(w)
                        * (rows[-1][a] / rows[-1 - h][a] - rows[-1]["SPY"] / rows[-1 - h]["SPY"])
                        for h, w in zip(settings.lookbacks, settings.momentum_weights, strict=True)
                    )
                    if (
                        rows[-1][a]
                        > sum(r[a] for r in rows[-settings.long_ma :]) / settings.long_ma
                    ):
                        eligible.add(a)
            references[day] = {
                "regime": REGIMES[regime],
                "severity": regime,
                "below": below,
                "above": above,
                "recovered": recovered,
                "ready": market_ready,
                "scores": scores,
                "eligible": eligible,
                "indices": dict(values),
            }
            previous = prices
        return references


def exposure(settings, severity):
    return D(
        (
            settings.normal_exposure,
            settings.warning_exposure,
            settings.bear_exposure,
            settings.deep_exposure,
        )[severity]
    )


def monthly_expected(reference, state, settings):
    scores, eligible = reference["scores"], reference["eligible"]
    ranked = sorted(scores, key=lambda a: (-scores[a], a))
    count = NAMES[reference["severity"]]
    ranks = {a: i + 1 for i, a in enumerate(ranked)}
    retained = [
        a
        for a in ranked
        if a in state.holdings and a in eligible and ranks[a] <= count + settings.rank_buffer
    ][:count]
    selected = (
        retained
        + [a for a in ranked if a not in retained and a in eligible and ranks[a] <= count][
            : count - len(retained)
        ]
    )
    invested = sum(D(h.quantity) * D(h.price) for h in state.holdings.values()) / D(state.equity)

    def budget(names):
        value = exposure(settings, reference["severity"]) * names / count
        return (
            min(value, invested) if reference["severity"] and not reference["recovered"] else value
        )

    while selected and budget(len(selected)) < len(selected) * D(settings.min_weight) - Decimal(
        "1E-12"
    ):
        selected.pop()
    return bounded_weights(
        {a: scores[a] for a in selected},
        budget(len(selected)),
        settings.min_weight,
        settings.max_weight,
    )


def fee_estimate(quantity, price, *, parent_tiered=False):
    """Fresh minimum, first-tier quote including fractional estimate components, no state."""
    q, p = abs(D(quantity)), D(price)
    require(q > 0 and p >= 1, "FEE_QUOTE_SCOPE")
    whole = q // 1
    fraction = q - whole
    billed = q if parent_tiered else whole
    raw, lower = Decimal(0), Decimal(0)
    for bound, rate in (
        (300000, ".0035"),
        (3000000, ".002"),
        (20000000, ".0015"),
        (100000000, ".001"),
        (Decimal("Infinity"), ".0005"),
    ):
        raw += max(Decimal(0), min(billed, D(bound)) - lower) * D(rate)
        lower = D(bound)
    broker = min(max(raw, D(".35")), billed * p / 100) if billed else Decimal(0)
    if fraction > Decimal("1E-12") and not parent_tiered:
        broker += max(D(".01"), fraction * p / 100)
    regulatory = q * D(".000003")
    if D(quantity) < 0:
        regulatory += q * p * D(".0000206") + min(q * D(".000195"), D("9.79"))
    return (
        broker * D("1.000735")
        + (whole if parent_tiered else q) * D(".0035")
        + min(q * D(".0002"), q * p * D(".005"))
        + regulatory
    )


def conditional_close_sensitivity(inputs, oracle):
    """Alternate retained closes in indicators, on the same verified past holdings.

    Never mutate original inputs or pretend this is an alternate funding path.
    """
    overrides = {
        (b.end.astimezone(NY).date(), b.asset): b.close
        for b in inputs.bars
        if b.end == CAL.session(b.end.astimezone(NY).date()).market_close
    }
    alternate = reference_signals(inputs, oracle.settings, close_overrides=overrides)
    changed_regimes = [
        str(day) for day in alternate if alternate[day]["regime"] != oracle.reference[day]["regime"]
    ]
    rows = []
    for day, original in oracle.monthly.items():
        past = original["past_verified_state"]
        state = SimpleNamespace(
            equity=D(past["equity"]),
            holdings={
                a: SimpleNamespace(quantity=D(h["quantity"]), price=D(h["price"]))
                for a, h in past["holdings"].items()
            },
        )
        weights = monthly_expected(alternate[day], state, oracle.settings)
        rank = sorted(alternate[day]["scores"], key=lambda a: (-alternate[day]["scores"][a], a))
        difference = max(
            (
                abs(weights.get(a, 0) - original["targets"].get(a, 0))
                for a in sorted(set(weights) | original["targets"].keys())
            ),
            default=Decimal(0),
        )
        rows.append(
            {
                "signal_session": str(day),
                "original_regime": original["regime"],
                "alternate_regime": alternate[day]["regime"],
                "ranking_changed": rank != original["ranked"],
                "selected_assets_changed": set(weights) != set(original["targets"]),
                "maximum_target_weight_difference": str(difference),
                "alternate_targets": weights,
            }
        )
    return {
        "summary": {
            "scope": "conditional_monthly_choices_same_verified_past_holdings_NAV_execution_marks",
            "alternate_signal_close": "retained_final_RTH_minute_close_during_execution_window_only",
            "presample_minute_coverage_available": False,
            "signal_sessions_with_changed_regime": len(changed_regimes),
            "monthly_rankings_changed": sum(r["ranking_changed"] for r in rows),
            "monthly_selected_assets_changed": sum(r["selected_assets_changed"] for r in rows),
            "maximum_monthly_target_weight_difference": str(
                max((D(r["maximum_target_weight_difference"]) for r in rows), default=Decimal(0))
            ),
            "full_counterfactual_trading_funding_path_verified": False,
            "source_economic_cause_verified": False,
        },
        "changed_regime_sessions": changed_regimes,
        "monthly": rows,
    }


class DecisionOracle:
    """Observe real call boundaries; expectations never read the production signal book."""

    def __init__(self, engine):
        self.engine, self.strategy = engine, engine.strategy
        self.settings = engine.strategy.config
        self.share_step = D(engine.research_assumptions.share_step)
        self.parent_tiered = self.share_step == D(".1")
        self.reference = reference_signals(engine.research_inputs, self.settings)
        self.monthly, self.signal_rows, self.execution_targets, self.orders, self.cash_blocks = (
            {},
            {},
            [],
            [],
            [],
        )
        self.risk_calls = []
        self.scope, self.risk_expected = None, None
        self.originals = []
        self._attach()

    def _wrap(self, owner, name, factory):
        original = getattr(owner, name)
        self.originals.append((owner, name, original))
        setattr(owner, name, factory(original))

    def row_at(self, stamp):
        day = stamp.astimezone(NY).date()
        if stamp < CAL.session(day).market_close:
            day = CAL.shift(day, -1)
        require(day in self.reference, "MISSING_REFERENCE_CLOSE")
        return day, self.reference[day]

    def _attach(self):
        self._wrap(self.strategy, "_before_execution", self._before)
        self._wrap(self.strategy, "_stage_monthly", self._monthly)
        self._wrap(self.strategy, "_execution_targets", self._targets)
        self._wrap(self.strategy, "_plan_eligible", self._eligible)
        self._wrap(self.strategy, "_submit_risk", self._risk)
        self._wrap(self.engine.broker, "submit_order", self._order)
        self._wrap(self.engine.broker.plan_manager, "audit", self._plan_audit)

    def _before(self, original):
        def wrapped(stamp, broker):
            result = original(stamp, broker)
            day, expected = self.row_at(stamp)
            book = self.strategy.book
            require(
                book.regime.value == expected["regime"]
                and book.below == expected["below"]
                and book.above == expected["above"],
                "SIGNAL_STATE",
            )
            require(book.latest.session == day, "CAUSAL_SESSION")
            if expected["scores"]:
                scored = book.scores()
                require(
                    scored is not None and set(scored[1]) == expected["eligible"], "ELIGIBILITY"
                )
                equal_mapping(scored[0], expected["scores"], "MOMENTUM")
            self.signal_rows[stamp] = {
                "timestamp": stamp,
                "signal_session": day,
                "regime": expected["regime"],
                "below": expected["below"],
                "above": expected["above"],
            }
            return result

        return wrapped

    def _monthly(self, original):
        def wrapped(stamp, broker, day):
            reference = self.reference[day]
            snapshot = broker.constraint_state()
            targets = (
                monthly_expected(reference, snapshot, self.settings)
                if reference["ready"] and reference["scores"]
                else None
            )
            count = len(self.strategy.decisions)
            result = original(stamp, broker, day)
            require(
                len(self.strategy.decisions) == count + (targets is not None),
                "MONTHLY_DECISION_COUNT",
            )
            if targets is not None:
                decision = self.strategy.decisions[-1]
                execute = CAL.shift(day, 1)
                expiry = CAL.session(
                    CAL.shift(execute, self.settings.plan_sessions - 1)
                ).market_close
                require(
                    decision.signal_session == day
                    and decision.execute_session == execute
                    and decision.valid_until == expiry
                    and decision.regime.value == reference["regime"],
                    "MONTHLY_TIMING_REGIME",
                )
                equal_mapping(decision.scores, reference["scores"], "MONTHLY_SCORES")
                equal_mapping(decision.targets, targets, "MONTHLY_TARGETS")
                require(
                    set(decision.entry_assets) == set(targets) - snapshot.holdings.keys(),
                    "MONTHLY_ENTRIES",
                )
                self.monthly[day] = {
                    "timestamp": stamp,
                    "signal_session": day,
                    "execute_session": execute,
                    "valid_until": expiry,
                    "regime": reference["regime"],
                    "targets": targets,
                    "scores": reference["scores"],
                    "ranked": sorted(
                        reference["scores"], key=lambda a: (-reference["scores"][a], a)
                    ),
                    "past_verified_state": {
                        "equity": str(snapshot.equity),
                        "holdings": {
                            asset: {"quantity": str(h.quantity), "price": str(h.price)}
                            for asset, h in snapshot.holdings.items()
                        },
                    },
                }
            return result

        return wrapped

    def _eligible(self, original):
        def wrapped(stamp, decision):
            _, ref = self.row_at(stamp)
            ranked = sorted(ref["scores"], key=lambda a: (-ref["scores"][a], a))
            ranks = {a: i + 1 for i, a in enumerate(ranked)}
            expected = (
                ref["ready"]
                and bool(ref["scores"])
                and ref["severity"] <= REGIMES.index(decision.regime.value)
                and all(
                    a in ref["eligible"]
                    and ranks[a]
                    <= NAMES[ref["severity"]]
                    + (0 if a in decision.entry_assets else self.settings.rank_buffer)
                    for a in decision.targets
                )
            )
            result = original(stamp, decision)
            require(result == expected, "PLAN_ELIGIBILITY")
            return result

        return wrapped

    def _targets(self, original):
        def wrapped(broker, decision):
            expected = self.monthly[decision.signal_session]
            state = broker.constraint_state()
            nav = D(state.equity)
            lo, hi, fees, slip = (
                {},
                {},
                Decimal(0),
                D(self.engine.research_assumptions.slippage_bps) / 10000,
            )
            for a, weight in expected["targets"].items():
                price = D(broker.get_last_price(a))
                held = D(state.holdings[a].quantity) if a in state.holdings else Decimal(0)
                delta = weight * nav / price - held
                estimated = price * (1 + slip if delta > 0 else 1 - slip)
                fees += fee_estimate(delta, estimated, parent_tiered=self.parent_tiered)
                lo[a], hi[a] = (
                    D(self.settings.min_weight) + price * self.share_step / nav,
                    D(self.settings.max_weight) - price * self.share_step / nav,
                )
            for a, holding in state.holdings.items():
                if a not in expected["targets"]:
                    fees += fee_estimate(
                        -holding.quantity, holding.price, parent_tiered=self.parent_tiered
                    )
            cushion = fees / nav + slip + D(self.settings.execution_cushion_bps) / 10000
            hi = {a: value - cushion for a, value in hi.items()}
            if expected["targets"]:
                weights = bounded_weights(
                    {a: expected["scores"][a] for a in expected["targets"]},
                    min(sum(expected["targets"].values()) - cushion, sum(hi.values())),
                    self.settings.min_weight,
                    self.settings.max_weight,
                    lower=lo,
                    upper=hi,
                )
            else:
                weights = {}
            result = original(broker, decision)
            equal_mapping(result, weights, "EXECUTION_TARGETS")
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
            require(
                all(
                    D(state.holdings[a].quantity) * D(state.holdings[a].price) / D(state.equity)
                    < D(self.settings.min_weight) + NUMERIC_TOLERANCE
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
            ][: NAMES[ref["severity"]]]
            caps = {
                a: min(
                    D(self.settings.max_weight),
                    D(state.holdings[a].quantity) * D(state.holdings[a].price) / D(state.equity),
                )
                for a in held
            }
            total = min(
                exposure(self.settings, ref["severity"]) * len(held) / NAMES[ref["severity"]],
                sum(caps.values()),
            )
            weights = bounded_weights(
                {a: scores.get(a, 0) for a in held},
                total,
                self.settings.min_weight,
                self.settings.max_weight,
                upper=caps,
            )
            expected = {}
            for a, h in state.holdings.items():
                q = (
                    math.floor(
                        weights.get(a, 0) * D(state.equity) / D(h.price) / self.share_step
                        + D("1E-10")
                    )
                    * self.share_step
                )
                if D(q) * D(h.price) / D(state.equity) < D(self.settings.min_weight) - D("1E-10"):
                    q = 0
                delta = min(Decimal(0), D(q) - D(h.quantity))
                if delta < D("-1E-8") and not broker.get_pending_orders(a):
                    expected[a] = delta
            start = len(self.orders)
            self.scope, self.risk_expected = "strategy_risk", expected
            try:
                result = original(stamp, broker)
            finally:
                self.scope, self.risk_expected = None, None
            submitted = {row["asset"]: row["requested"] for row in self.orders[start:]}
            equal_mapping(submitted, expected, "RISK_SUBMISSION_COMPLETENESS")
            self.risk_calls.append({"timestamp": stamp, "expected_orders": expected})
            return result

        return wrapped

    def _order(self, original):
        def wrapped(asset, quantity, *args, **kwargs):
            broker = self.engine.broker
            stamp, state = broker.rotation_time, broker.constraint_state()
            session = CAL.session(stamp.astimezone(NY).date())
            require(
                session.market_open + timedelta(minutes=self.settings.execution_start_minutes)
                <= stamp
                < session.market_open + timedelta(minutes=self.settings.execution_end_minutes),
                "ORDER_WINDOW",
            )
            if self.scope == "strategy_risk":
                assert self.risk_expected is not None
                require(asset in self.risk_expected, "UNEXPECTED_RISK_ASSET")
                equal(quantity, self.risk_expected[asset], "RISK_QUANTITY")
            else:
                options = kwargs.get("_options")
                plan_id = getattr(options, "rebalance_id", None)
                require(plan_id is not None and plan_id.startswith("SPDR-"), "UNCLASSIFIED_ORDER")
                signal = next(
                    (
                        v
                        for v in self.monthly.values()
                        if "SPDR-" + str(v["signal_session"]) == plan_id
                    ),
                    None,
                )
                require(signal is not None, "UNEXPECTED_ORDER_PLAN")
                assert signal is not None
                target = next(
                    v["weights"]
                    for v in reversed(self.execution_targets)
                    if v["signal_session"] == signal["signal_session"]
                )
                weight = target.get(asset, Decimal(0))
                equal(kwargs["target_weight"], weight, "ORDER_TARGET")
                held = D(state.holdings[asset].quantity) if asset in state.holdings else Decimal(0)
                expected = weight * D(state.equity) / D(broker.get_last_price(asset)) - held
                equal(quantity, expected, "ORDER_QUANTITY", D("1E-8"))
                require(
                    stamp < signal["valid_until"]
                    and stamp.astimezone(NY).date() >= signal["execute_session"],
                    "ORDER_VALIDITY",
                )
            result = original(asset, quantity, *args, **kwargs)
            self.orders.append(
                {
                    "timestamp": stamp,
                    "asset": asset,
                    "requested": D(quantity),
                    "scope": self.scope or "monthly_plan",
                    "order_id": result.order_id if result else None,
                    "status": result.status.value if result else None,
                }
            )
            return result

        return wrapped

    def _plan_audit(self, original):
        def wrapped(plan, action, code):
            if code == "rebalance_wait_settlement":
                broker, nav = self.engine.broker, D(self.engine.broker.constraint_state().equity)
                state = broker.constraint_state()
                available = D(state.settled_cash) - D(state.reserved_cash)
                blocked = []
                for asset, weight in plan.targets.items():
                    price = D(broker.get_last_price(asset))
                    held = (
                        D(state.holdings[asset].quantity) if asset in state.holdings else Decimal(0)
                    )
                    q = D(weight) * nav / price - held
                    if q <= 0:
                        continue
                    estimate = price * (
                        1 + D(self.engine.research_assumptions.slippage_bps) / 10000
                    )
                    required = q * estimate + fee_estimate(
                        q, estimate, parent_tiered=self.parent_tiered
                    )
                    if required > available - D(broker.controller.config.cash_reserve) * nav + D(
                        "1E-8"
                    ):
                        blocked.append(asset)
                require(broker.unsettled_cash > 0 and bool(blocked), "FALSE_SETTLEMENT_BLOCK")
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

    def finish(self):
        stamps = sorted({b.end for b in self.engine.research_inputs.bars})
        require(sorted(self.signal_rows) == stamps, "OBSERVATION_COVERAGE")
        first = CAL.shift(stamps[0].astimezone(NY).date(), -1)
        last = stamps[-1].astimezone(NY).date()
        expected = {
            day
            for day, row in self.reference.items()
            if first <= day <= last
            and CAL.shift(day, 1).month != day.month
            and row["ready"]
            and row["scores"]
        }
        require(set(self.monthly) == expected, "MONTHLY_COVERAGE")
        require(
            self.engine.research_report["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS",
            "LEDGER_NOT_ACCEPTED",
        )
        regimes = sorted({row["regime"] for row in self.signal_rows.values()})
        return {
            "schema": "rotation_independent_decision_oracle_v1",
            "status": "CONDITIONAL_DECISION_AND_SUBMITTED_INTENT_ORACLE_PASS",
            "conditioned_on_verified_past_engine_ledger": True,
            "independent_fill_simulator": False,
            "all_unsubmitted_opportunities_verified": False,
            "signal_observations": len(self.signal_rows),
            "monthly_decisions": len(self.monthly),
            "submitted_order_intents": len(self.orders),
            "risk_calls": len(self.risk_calls),
            "settlement_blocks": len(self.cash_blocks),
            "observed_regimes": regimes,
            "signals": list(self.signal_rows.values()),
            "decisions": list(self.monthly.values()),
            "execution_targets": self.execution_targets,
            "orders": self.orders,
            "risk": self.risk_calls,
            "cash_blocks": self.cash_blocks,
            "PIT_verified": False,
            "performance_admission": False,
        }
