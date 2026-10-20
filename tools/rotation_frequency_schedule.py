"""Isolated fixed-frequency book; original signal and strategy files stay sealed."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from ml4t.backtest.strategies.sector_rotation.adapter import SectorRotationBroker
from ml4t.backtest.strategies.sector_rotation.research_ablation import (
    experimental_rotation_engine,
)
from ml4t.backtest.strategies.sector_rotation.signals import SignalBook
from ml4t.backtest.strategies.sector_rotation.strategy import SectorRotationStrategy
from quant_constraints.adapter import ConstraintFillExecutor
from quant_constraints.calendar import NY, SessionCalendar
from quant_constraints.gates import RebalanceGate

CASES = ("M", "C15_S", "C15_SR", "T15_S", "T15_SR")


def schedule(first, last, case):
    if case not in CASES:
        raise ValueError("FREQUENCY_UNKNOWN_CASE")
    calendar = SessionCalendar()
    start = calendar.shift(first, -1)
    while calendar.shift(start, 1).month == start.month:
        start = calendar.shift(start, 1)
    anchor = calendar.shift(start, 1)
    limit = calendar.shift(last, 1)
    executions = []
    if case == "M":
        day = start
        while day <= last:
            if calendar.shift(day, 1).month != day.month:
                executions.append(calendar.shift(day, 1))
            day = calendar.shift(day, 1)
    else:
        target = anchor
        while target <= limit:
            day = target
            while calendar.session(day) is None:
                day += timedelta(days=1)
            if day <= limit:
                executions.append(day)
            target = (
                target + timedelta(days=15) if case.startswith("C") else calendar.shift(target, 15)
            )
    signals = frozenset(calendar.shift(day, -1) for day in executions)
    return start, anchor, signals


class FrequencyBook(SignalBook):
    """Separate the existing calendar hook's recovery and allocation call sites."""

    def __init__(self, config, first, last, case):
        super().__init__(config)
        self.case = case
        self.first_signal, self.anchor, self.rotation_days = schedule(first, last, case)
        self._risk_update = False

    def ingest(self, row):
        self._risk_update = True
        try:
            super().ingest(row)
        finally:
            self._risk_update = False

    def is_month_end(self, day):
        if day < self.first_signal or (self._risk_update and not self.case.endswith("_SR")):
            return super().is_month_end(day)
        return day in self.rotation_days


class FrequencyGate(RebalanceGate):
    """Change only plan-creation dates; retain every original order/plan check."""

    def __init__(self, original, rotation_days):
        super().__init__(original.config, original.calendar)
        self.execution_days = frozenset(self.calendar.shift(day, 1) for day in rotation_days)

    def scheduled(self, state, asof):
        return asof.astimezone(NY).date() in self.execution_days


class CompletedMinuteRiskExecutor(ConstraintFillExecutor):
    """Honor the existing research close-price contract for deferred risk too.

    The inherited executor reads ``opens`` for deferred risk. Give only that
    collaborator a per-call view of the completed close; original market state,
    raw input OHLC, every guard and canonical economic state remain intact.
    """

    def execute(self, order, base_price):
        broker, market = self.broker, self.market
        assert isinstance(broker, SectorRotationBroker)
        if (
            order.order_id not in broker.deferred_risk
            or order.asset not in market.opens
            or order.asset not in market.prices
        ):
            return super().execute(order, base_price)
        self.market = replace(
            market, opens={**market.opens, order.asset: market.prices[order.asset]}
        )
        try:
            return super().execute(order, base_price)
        finally:
            self.market = market


def bind_completed_minute_risk(engine):
    broker = engine.broker
    assert isinstance(broker, SectorRotationBroker)
    original = broker._fill_executor
    assert isinstance(original, ConstraintFillExecutor)
    executor = CompletedMinuteRiskExecutor(
        broker,
        account=original.account,
        market=original.market,
        orders=original.orders,
        risk=original.risk,
        journal=original.journal,
        record_pnl=original.record_pnl,
    )
    executor.fill_engine = original.fill_engine
    broker._fill_executor = executor
    broker._fill_engine.executor = executor


def frequency_engine(inputs, assumptions, settings, case):
    if case not in CASES:
        raise ValueError("FREQUENCY_UNKNOWN_CASE")
    engine = experimental_rotation_engine(
        inputs, assumptions, settings, sector_rotation=True, risk_tiers=True
    )
    assert isinstance(engine.strategy, SectorRotationStrategy)
    bind_completed_minute_risk(engine)
    if case != "M":
        first = inputs.bars[0].end.astimezone(NY).date()
        last = inputs.bars[-1].end.astimezone(NY).date()
        engine.strategy.book = FrequencyBook(settings, first, last, case)
        assert isinstance(engine.broker, SectorRotationBroker)
        controller = engine.broker.controller
        controller.rebalance = FrequencyGate(
            controller.rebalance, engine.strategy.book.rotation_days
        )
    return engine
