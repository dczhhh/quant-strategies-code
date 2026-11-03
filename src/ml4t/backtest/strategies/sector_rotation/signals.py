"""Immutable, availability-bounded total-return closes and a causal SPY state machine."""

import math
from bisect import bisect_right
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType

from quant_constraints.calendar import SessionCalendar
from quant_constraints.models import aware

from .config import SECTOR_ETFS, SectorRotationConfig


class MarketRegime(StrEnum):
    NORMAL = "normal"
    WARNING = "warning"
    BEAR = "bear"
    DEEP_BEAR = "deep_bear"

    @property
    def severity(self) -> int:
        return list(MarketRegime).index(self)

    @property
    def names(self) -> int:
        return (5, 4, 2, 1)[self.severity]

    def exposure(self, config: SectorRotationConfig) -> float:
        return (
            config.normal_exposure,
            config.warning_exposure,
            config.bear_exposure,
            config.deep_exposure,
        )[self.severity]


@dataclass(frozen=True)
class TotalReturnClose:
    session: date
    available_at: datetime
    values: Mapping[str, float]
    data_mode: str = "total_return_signal"

    def __post_init__(self):
        aware(self.available_at)
        session = SessionCalendar().session(self.session)
        if session is None or self.available_at < session.market_close:
            raise ValueError("A completed RTH close cannot be available before its actual close")
        if self.data_mode != "total_return_signal":
            raise ValueError("Explicit total_return_signal data is required")
        if any(
            isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) or v <= 0
            for v in self.values.values()
        ):
            raise ValueError("Total-return indices must be finite and positive")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


class CausalCloseSource:
    """Only reveal immutable rows whose availability time has been reached.

    The caller supplies a point-in-time total-return index, not Massive's split-only
    adjusted close. Rows must be chronological and cannot revise earlier sessions.
    """

    def __init__(self, closes: Sequence[TotalReturnClose]):
        self.closes = tuple(closes)
        if any(
            a.session >= b.session or a.available_at > b.available_at
            for a, b in zip(self.closes, self.closes[1:])
        ):
            raise ValueError("Close sessions and availability must be chronological and unique")
        self._availability = tuple(row.available_at for row in self.closes)

    def available(self, asof: datetime, offset: int = 0) -> tuple[TotalReturnClose, ...]:
        return self.closes[offset : bisect_right(self._availability, aware(asof))]


class SignalBook:
    def __init__(self, config: SectorRotationConfig):
        self.config = config
        self.calendar = SessionCalendar()
        self.history: deque[TotalReturnClose] = deque(maxlen=config.history_sessions)
        self.regime = MarketRegime.NORMAL
        self.below = self.above = 0
        self.market_ready = False
        self.recovered = False

    @property
    def latest(self) -> TotalReturnClose | None:
        return self.history[-1] if self.history else None

    def is_month_end(self, day: date) -> bool:
        return self.calendar.shift(day, 1).month != day.month

    def ingest(self, row: TotalReturnClose) -> None:
        previous = self.latest
        if previous and row.session <= previous.session:
            raise ValueError("Close revisions and out-of-order history are unsupported")
        if previous and row.session != self.calendar.shift(previous.session, 1):
            self.history.clear()
            self.below = self.above = 0
        self.history.append(row)
        c = self.config
        needed = max(c.long_ma, c.peak_lookback)
        rows = list(self.history)
        self.recovered = False
        self.market_ready = len(rows) >= needed and all("SPY" in r.values for r in rows[-needed:])
        # Build the confirmation streak once the long MA exists, even while
        # the longer momentum/peak window is still warming up.
        trend_ready = len(rows) >= c.long_ma and all("SPY" in r.values for r in rows[-c.long_ma :])
        if not trend_ready:
            self.below = self.above = 0
            return
        spy = [r.values["SPY"] for r in rows[-needed:] if "SPY" in r.values]
        current = spy[-1]
        short = sum(spy[-c.short_ma :]) / c.short_ma
        long = sum(spy[-c.long_ma :]) / c.long_ma
        drawdown = current / max(spy[-c.peak_lookback :]) - 1
        self.below = self.below + 1 if current < long else 0
        self.above = self.above + 1 if current > long else 0
        requested = MarketRegime.NORMAL
        if current < long and drawdown <= -c.deep_drawdown:
            requested = MarketRegime.DEEP_BEAR
        elif self.below >= c.down_confirm and current <= (1 - c.trend_buffer) * long:
            requested = MarketRegime.BEAR if short < long else MarketRegime.WARNING
        if requested.severity > self.regime.severity:
            self.regime = requested
        elif (
            self.market_ready
            and self.is_month_end(row.session)
            and self.above >= c.recovery_confirm
            and current >= (1 + c.trend_buffer) * long
        ):
            restored = MarketRegime.NORMAL if short >= long else MarketRegime.WARNING
            if restored.severity < self.regime.severity:
                self.regime = restored
                self.recovered = True

    def current_for(self, asof: datetime) -> bool:
        from quant_constraints.calendar import NY

        today = asof.astimezone(NY).date()
        session = self.calendar.session(today)
        expected = (
            today if session and asof >= session.market_close else self.calendar.shift(today, -1)
        )
        return self.latest is not None and self.latest.session == expected

    def scores(self) -> tuple[dict[str, float], frozenset[str]] | None:
        c = self.config
        rows = list(self.history)
        needed = max(max(c.lookbacks) + 1, c.long_ma)
        if len(rows) < needed:
            return None
        rows = rows[-needed:]
        if any({*SECTOR_ETFS, "SPY"} - row.values.keys() for row in rows):
            return None
        scores = {}
        eligible = set()
        for asset in SECTOR_ETFS:
            score = sum(
                weight
                * (
                    rows[-1].values[asset] / rows[-1 - h].values[asset]
                    - rows[-1].values["SPY"] / rows[-1 - h].values["SPY"]
                )
                for h, weight in zip(c.lookbacks, c.momentum_weights, strict=True)
            )
            if not math.isfinite(score):
                return None
            scores[asset] = score
            average = sum(r.values[asset] for r in rows[-c.long_ma :]) / c.long_ma
            if rows[-1].values[asset] > average:
                eligible.add(asset)
        return scores, frozenset(eligible)
