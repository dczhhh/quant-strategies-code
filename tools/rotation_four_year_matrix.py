"""Fixed 2x2 rotation/recovery factors with causal matching SPY controls."""

from __future__ import annotations

from ml4t.backtest.strategies.sector_rotation.config import SectorRotationConfig
from ml4t.backtest.strategies.sector_rotation.research_fractional_benchmark import (
    retrospective_spy_benchmark,
)
from ml4t.backtest.strategies.sector_rotation.signals import SignalBook
from tools import bear_2022_signal_spy as spy
from tools.rotation_frequency_oracle import FrequencyOracle, reference_dates, reference_signals
from tools.rotation_frequency_schedule import NY, frequency_engine, schedule
from tools.rotation_quantity_exit import bind_quantity_exit

CASES = ("M", "M_T15R", "T15_S", "T15_SR", "C15_S")
FACTORS = {
    "M": ("monthly", "monthly"),
    "M_T15R": ("monthly", "trading_15"),
    "T15_S": ("trading_15", "monthly"),
    "T15_SR": ("trading_15", "trading_15"),
    "C15_S": ("calendar_15", "monthly"),
}
SPY_CASES = ("SPY_PASSIVE", "SPY_SIGNAL_M", "SPY_SIGNAL_T15R")


def need(ok, code):
    if not ok:
        raise ValueError("FOUR_YEAR_MATRIX_" + code)


def bounds(inputs):
    return tuple(inputs.bars[i].end.astimezone(NY).date() for i in (0, -1))


class MonthlyTradingRecoveryBook(SignalBook):
    """Change only the risk-recovery call site; allocation remains monthly."""

    def __init__(self, settings, first, last):
        super().__init__(settings)
        self.first_signal, self.anchor, self.recovery_days = schedule(first, last, "T15_SR")
        self._risk_update = False

    def ingest(self, row):
        self._risk_update = True
        try:
            return super().ingest(row)
        finally:
            self._risk_update = False

    def is_month_end(self, day):
        if self._risk_update and day >= self.first_signal:
            return day in self.recovery_days
        return super().is_month_end(day)


def recovery_reference(inputs, settings):
    first, last = bounds(inputs)
    initial, days = reference_dates(first, last, "T15_SR")
    calendar = SignalBook(settings).calendar
    return reference_signals(
        inputs,
        settings,
        lambda day: (calendar.shift(day, 1).month != day.month if day < initial else day in days),
    )


def bind_execution(engine):
    spy.e.bind_uniform_parent_fees(engine)
    bind_quantity_exit(engine)
    return engine


def matrix_engine(inputs, assumptions, case, settings=None):
    need(case in CASES, "UNKNOWN_CASE")
    settings = settings or SectorRotationConfig()
    engine = frequency_engine(inputs, assumptions, settings, "M" if case == "M_T15R" else case)
    if case == "M_T15R":
        engine.strategy.book = MonthlyTradingRecoveryBook(settings, *bounds(inputs))
    return bind_execution(engine)


class MatrixOracle(FrequencyOracle):
    def __init__(self, engine, case):
        need(case in CASES, "UNKNOWN_CASE")
        super().__init__(engine, "M" if case == "M_T15R" else case)
        if case == "M_T15R":
            self.reference = recovery_reference(engine.research_inputs, self.settings)


def benchmark_engine(inputs, assumptions, case, settings=None):
    need(case in SPY_CASES, "UNKNOWN_SPY_CASE")
    if case == "SPY_PASSIVE":
        return bind_execution(retrospective_spy_benchmark(inputs, assumptions))
    engine = spy.signal_engine(inputs, assumptions, settings)
    if case == "SPY_SIGNAL_T15R":
        engine.strategy.book = MonthlyTradingRecoveryBook(engine.strategy.config, *bounds(inputs))
        engine.strategy.reference = recovery_reference(inputs, engine.strategy.config)
        engine.research_report["benchmark_policy"]["recovery_calendar"] = "trading_15"
    else:
        engine.research_report["benchmark_policy"]["recovery_calendar"] = "monthly"
    return bind_execution(engine)


def matching_spy(case):
    need(case in CASES, "UNKNOWN_CASE")
    return "SPY_SIGNAL_T15R" if FACTORS[case][1] == "trading_15" else "SPY_SIGNAL_M"
