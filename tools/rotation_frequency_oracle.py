"""Independent date enumeration and Decimal frequency oracle, no production schedule."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, localcontext

from tools import rotation_decision_oracle as o
from tools.rotation_ablation_oracle import AblationOracle

CAL, D, NY = o.CAL, o.D, o.NY
REGIMES = o.REGIMES
require = o.require


def reference_dates(first, last, case):
    if case not in ("M", "C15_S", "C15_SR", "T15_S", "T15_SR"):
        raise ValueError("FREQUENCY_UNKNOWN_CASE")
    prior = CAL.shift(first, -1)
    days = []
    day = prior
    while day <= CAL.shift(last, 1):
        if CAL.session(day) is not None:
            days.append(day)
        day += timedelta(days=1)
    initial = next(day for day in days if CAL.shift(day, 1).month != day.month)
    anchor = CAL.shift(initial, 1)
    selected = set()
    for day in days:
        if day < anchor:
            continue
        if case == "M":
            selected_day = CAL.shift(day, -1).month != day.month
        elif case.startswith("T"):
            selected_day = sum(anchor <= d < day for d in days) % 15 == 0
        else:
            nominal = anchor + timedelta(days=15 * ((day - anchor).days // 15))
            selected_day = CAL.shift(day, -1) < nominal <= day
        if selected_day:
            selected.add(CAL.shift(day, -1))
    return initial, frozenset(selected)


def reference_signals(inputs, settings, recovery_day, *, close_overrides=None):
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
                    and recovery_day(day)
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


class FrequencyOracle(AblationOracle):
    def __init__(self, engine, case):
        super().__init__(engine, sector_rotation=True, risk_tiers=True)
        first = engine.research_inputs.bars[0].end.astimezone(NY).date()
        last = engine.research_inputs.bars[-1].end.astimezone(NY).date()
        initial, self.rotation_days = reference_dates(first, last, case)

        def recovery_day(day):
            if day < initial or not case.endswith("_SR"):
                return CAL.shift(day, 1).month != day.month
            return day in self.rotation_days

        self.reference = reference_signals(engine.research_inputs, self.settings, recovery_day)

    def _before(self, original):
        observe = super()._before(original)

        def wrapped(stamp, broker):
            result = observe(stamp, broker)
            _, expected = self.row_at(stamp)
            require(
                self.strategy.book.recovered == expected["recovered"]
                and self.strategy.book.market_ready == expected["ready"],
                "RECOVERY_STATE",
            )
            return result

        return wrapped

    def finish(self):
        stamps = sorted({b.end for b in self.engine.research_inputs.bars})
        require(sorted(self.signal_rows) == stamps, "OBSERVATION_COVERAGE")
        first = CAL.shift(stamps[0].astimezone(NY).date(), -1)
        last = stamps[-1].astimezone(NY).date()
        expected = {
            day
            for day, row in self.reference.items()
            if first <= day <= last and day in self.rotation_days and row["ready"] and row["scores"]
        }
        require(set(self.monthly) == expected, "FREQUENCY_COVERAGE")
        require(
            self.engine.research_report["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS",
            "LEDGER_NOT_ACCEPTED",
        )
        regimes = sorted({row["regime"] for row in self.signal_rows.values()})
        return {
            "schema": "rotation_frequency_decision_oracle_v1",
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
