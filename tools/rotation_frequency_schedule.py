"""Isolated fixed-frequency book; original signal and strategy files stay sealed."""

from __future__ import annotations

from datetime import timedelta

from ml4t.backtest.strategies.sector_rotation.research_ablation import (
    experimental_rotation_engine,
)
from ml4t.backtest.strategies.sector_rotation.signals import SignalBook
from ml4t.backtest.strategies.sector_rotation.strategy import SectorRotationStrategy
from quant_constraints.calendar import NY, SessionCalendar

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


def frequency_engine(inputs, assumptions, settings, case):
    if case not in CASES:
        raise ValueError("FREQUENCY_UNKNOWN_CASE")
    engine = experimental_rotation_engine(
        inputs, assumptions, settings, sector_rotation=True, risk_tiers=True
    )
    assert isinstance(engine.strategy, SectorRotationStrategy)
    if case != "M":
        first = inputs.bars[0].end.astimezone(NY).date()
        last = inputs.bars[-1].end.astimezone(NY).date()
        engine.strategy.book = FrequencyBook(settings, first, last, case)
    return engine
