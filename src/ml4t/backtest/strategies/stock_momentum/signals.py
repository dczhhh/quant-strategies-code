"""Causal, long-only cross-sectional momentum. No execution prices or cash ledger."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from types import MappingProxyType
from typing import Literal

from quant_constraints.calendar import SessionCalendar
from quant_constraints.models import aware


@dataclass(frozen=True)
class StockMomentumConfig:
    lookback: int = 252
    skip: int = 21
    names: int = 5
    exposure: float = 0.95
    cost_buffer: float = 0.002
    min_weight: float = 0.10
    max_weight: float = 0.25
    industry_cap: float = 0.40
    min_market_cap: float = 10_000_000_000.0
    min_dollar_volume: float = 20_000_000.0
    min_price: float = 5.0
    missing_data: str = "abort"
    market_filter: bool = False
    market_ma: int = 200
    market_confirmation: int = 2
    frequency: Literal["monthly", "semi_monthly"] = "monthly"
    plan_sessions: int = 5

    def __post_init__(self):
        for name in (
            "lookback",
            "skip",
            "names",
            "market_ma",
            "market_confirmation",
            "plan_sessions",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"Invalid {name}")
        for name in (
            "exposure",
            "cost_buffer",
            "min_weight",
            "max_weight",
            "industry_cap",
            "min_market_cap",
            "min_dollar_volume",
            "min_price",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isfinite(value) or value <= 0:
                raise ValueError(f"Invalid {name}")
        if not (self.skip < self.lookback and 0.10 <= self.min_weight <= self.max_weight <= 0.25):
            raise ValueError("Invalid momentum window or concentration limits")
        if not (self.max_weight <= self.industry_cap <= 1 and self.exposure <= 0.95):
            raise ValueError("Invalid exposure or industry cap")
        if not self.min_weight <= self.weight <= self.max_weight:
            raise ValueError("Per-name allocation including cost buffer is infeasible")
        if self.missing_data not in {"abort", "skip"}:
            raise ValueError("missing_data must be abort or skip")
        if self.frequency not in {"monthly", "semi_monthly"}:
            raise ValueError("frequency must be monthly or semi_monthly")
        if type(self.market_filter) is not bool:
            raise ValueError("market_filter must be boolean")

    @property
    def weight(self) -> float:
        return (self.exposure - self.cost_buffer) / self.names


@dataclass(frozen=True)
class StockObservation:
    """All metadata is effective and known at snapshot time; history is gross TR.

    A listing/share-class ID must be stable across ticker changes. Market cap and
    trailing 20-session *raw* close x volume must be historical, not today's values.
    `eligible=False` represents a known exclusion, not a missing observation.
    """

    security_id: str
    available_at: datetime
    industry: str
    market_cap: float
    dollar_volume: float
    raw_close: float
    total_returns: tuple[float, ...]
    country: str = "US"
    security_type: str = "CS"
    currency: str = "USD"
    exchange: str = "XNAS"
    eligible: bool = True
    midcap_reason: str = ""
    listed_on: date = date(1900, 1, 1)

    def __post_init__(self):
        aware(self.available_at)
        object.__setattr__(self, "total_returns", tuple(self.total_returns))
        if not self.security_id or type(self.eligible) is not bool:
            raise ValueError("Stable security identity and explicit eligibility are required")
        if type(self.listed_on) is not date:
            raise ValueError("listed_on must be a date")


@dataclass(frozen=True)
class MomentumSnapshot:
    session: date
    available_at: datetime
    sessions: tuple[date, ...]
    stocks: Mapping[str, StockObservation]
    spy_total_returns: tuple[float, ...] = ()
    evidence_mode: str = "synthetic"

    def __post_init__(self):
        aware(self.available_at)
        object.__setattr__(self, "sessions", tuple(self.sessions))
        object.__setattr__(self, "stocks", MappingProxyType(dict(self.stocks)))
        object.__setattr__(self, "spy_total_returns", tuple(self.spy_total_returns))
        if self.evidence_mode not in {"synthetic", "retrospective_research"}:
            raise ValueError("V1 does not certify PIT or admit production performance")
        calendar = SessionCalendar()
        close = calendar.session(self.session)
        if close is None or self.available_at < close.market_close:
            raise ValueError("Snapshot cannot precede its completed session close")
        if not self.sessions or self.sessions[-1] != self.session:
            raise ValueError("History must end on snapshot session")
        if any(calendar.session(day) is None for day in self.sessions) or any(
            calendar.shift(a, 1) != b for a, b in zip(self.sessions, self.sessions[1:])
        ):
            raise ValueError("History must use consecutive exchange sessions")
        if len({s.security_id for s in self.stocks.values()}) != len(self.stocks):
            raise ValueError("Duplicate security IDs would double-count a holding")


@dataclass(frozen=True)
class MomentumDecision:
    session: date
    targets: Mapping[str, float]
    scores: Mapping[str, float]
    excluded: Mapping[str, str]
    risk_off: bool

    def __post_init__(self):
        for name in ("targets", "scores", "excluded"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


def market_risk_off(snapshot: MomentumSnapshot, config: StockMomentumConfig) -> bool:
    if not config.market_filter:
        return False
    prices = snapshot.spy_total_returns
    required = config.market_ma + config.market_confirmation - 1
    if (
        len(prices) != len(snapshot.sessions)
        or len(prices) < required
        or any(not isfinite(p) or p <= 0 for p in prices)
    ):
        raise ValueError("Missing aligned SPY total-return history")
    return all(
        prices[-1 - offset]
        < sum(prices[len(prices) - offset - config.market_ma : len(prices) - offset])
        / config.market_ma
        for offset in range(config.market_confirmation)
    )


def select_stocks(snapshot: MomentumSnapshot, config: StockMomentumConfig) -> MomentumDecision:
    scores: dict[str, float] = {}
    excluded: dict[str, str] = {}
    for asset, stock in sorted(snapshot.stocks.items()):
        reason = ""
        if stock.available_at > snapshot.available_at:
            raise ValueError(f"Future metadata: {asset}")
        if (
            not stock.eligible
            or stock.listed_on > snapshot.session
            or stock.country != "US"
            or stock.security_type != "CS"
            or stock.currency != "USD"
            or stock.exchange not in {"XNYS", "XNAS", "XASE"}
            or asset in {"GME", "AMC", "KOSS"}
        ):
            reason = "universe_exclusion"
        elif not stock.industry or any(
            not isfinite(v) or v <= 0
            for v in (stock.market_cap, stock.dollar_volume, stock.raw_close)
        ):
            reason = "missing_metadata"
        elif stock.market_cap < config.min_market_cap:
            reason = "market_cap"
        elif stock.market_cap < 10_000_000_000 and not stock.midcap_reason.strip():
            reason = "midcap_reason_required"
        elif stock.market_cap < 2_000_000_000:
            reason = "small_cap"
        elif stock.dollar_volume < config.min_dollar_volume or stock.raw_close < config.min_price:
            reason = "liquidity_or_price"
        elif len(stock.total_returns) < config.lookback + 1:
            required_start = SessionCalendar().shift(snapshot.session, -config.lookback)
            reason = (
                "insufficient_history" if stock.listed_on > required_start else "missing_prices"
            )
        elif len(stock.total_returns) != len(snapshot.sessions) or any(
            not isfinite(p) or p <= 0 for p in stock.total_returns
        ):
            reason = "missing_prices"
        if reason:
            if config.missing_data == "abort" and reason in {"missing_metadata", "missing_prices"}:
                raise ValueError(f"{reason}: {asset}")
            excluded[asset] = reason
            continue
        # 253 endpoints: return from session t-252 through t-21, excluding latest 21.
        score = (
            stock.total_returns[-1 - config.skip] / stock.total_returns[-1 - config.lookback] - 1
        )
        if not isfinite(score):
            raise ValueError(f"Invalid momentum: {asset}")
        if score <= 0:
            excluded[asset] = "nonpositive_momentum"
        else:
            scores[asset] = score
    risk_off = market_risk_off(snapshot, config)
    targets: dict[str, float] = {}
    industries: dict[str, float] = {}
    if not risk_off:
        for asset in sorted(scores, key=lambda a: (-scores[a], a)):
            industry = snapshot.stocks[asset].industry
            if industries.get(industry, 0) + config.weight > config.industry_cap + 1e-12:
                excluded[asset] = "industry_cap"
                continue
            targets[asset] = config.weight
            industries[industry] = industries.get(industry, 0) + config.weight
            if len(targets) == config.names:
                break
    return MomentumDecision(snapshot.session, targets, scores, excluded, risk_off)


class SnapshotSource:
    """Immutable fixture/research source; future snapshots never reach the strategy."""

    def __init__(self, snapshots: tuple[MomentumSnapshot, ...]):
        self.snapshots = tuple(sorted(snapshots, key=lambda s: s.available_at))
        if not self.snapshots or len({s.session for s in snapshots}) != len(snapshots):
            raise ValueError("Supply one immutable snapshot per session")
        if any(a.session >= b.session for a, b in zip(self.snapshots, self.snapshots[1:])):
            raise ValueError("Snapshot publication order must preserve session order")
        if len({s.evidence_mode for s in snapshots}) != 1:
            raise ValueError("Do not mix synthetic and real research inputs")

    def latest(self, asof: datetime) -> MomentumSnapshot | None:
        aware(asof)
        return next((s for s in reversed(self.snapshots) if s.available_at <= asof), None)
