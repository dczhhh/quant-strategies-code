"""Frozen raw-share inputs for explicitly retrospective simulations.

Source observation clocks are retained separately from modeled session clocks.
Hash verification proves retained-byte integrity, never historical availability.
"""

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from types import MappingProxyType
from urllib.parse import urlsplit

from quant_constraints.calendar import NY, SessionCalendar
from quant_constraints.models import aware

from .config import SECTOR_ETFS
from .signals import CausalCloseSource, TotalReturnClose

UNIVERSE = (*SECTOR_ETFS, "SPY")
RAW_CLOSE_TOLERANCE = Decimal("0.0002")


def number(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Explicit numeric input required")
    try:
        result = Decimal(str(value))
    except ArithmeticError as exc:
        raise ValueError("Invalid decimal input") from exc
    if not result.is_finite():
        raise ValueError("Non-finite input")
    return result


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class ResearchSource:
    identifier: str
    sha256: str
    observed_at: datetime
    url: str

    def __post_init__(self):
        aware(self.observed_at)
        uri = urlsplit(self.url)
        if (
            not self.identifier
            or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None
            or uri.scheme != "https"
            or not uri.hostname
            or uri.username
            or uri.password
            or re.search(r"(?i)(?:apikey|api_key|access_token|authorization)=", uri.query)
        ):
            raise ValueError("Invalid retained research source")


@dataclass(frozen=True)
class RawClose:
    asset: str
    session: date
    close: Decimal
    source_id: str


@dataclass(frozen=True)
class ResearchBar:
    asset: str
    end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source_id: str


@dataclass(frozen=True)
class ResearchDistribution:
    asset: str
    event_id: str
    ex_date: date
    record_date: date
    pay_date: date
    cash_amount: Decimal
    classification: str
    source_id: str
    classification_source_id: str | None


@dataclass(frozen=True)
class ResearchSplit:
    asset: str
    event_id: str
    session: date
    new_per_old: Decimal
    source_id: str


@dataclass(frozen=True)
class ResearchSecurity:
    asset: str
    security_id: str
    issuer_country: str
    tax_source_country: str
    security_type: str
    is_depositary_receipt: bool
    source_id: str


@dataclass(frozen=True)
class ResearchInputs:
    sources: tuple[ResearchSource, ...]
    closes: tuple[RawClose, ...]
    bars: tuple[ResearchBar, ...]
    distributions: tuple[ResearchDistribution, ...]
    splits: tuple[ResearchSplit, ...]
    securities: tuple[ResearchSecurity, ...]
    settlement_sessions: tuple[date, ...]
    settlement_source_id: str
    origin: str = "retained_market_data"

    def __post_init__(self):
        for name in (
            "sources",
            "closes",
            "bars",
            "distributions",
            "splits",
            "securities",
            "settlement_sessions",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.origin not in {"retained_market_data", "invented_fixture"}:
            raise ValueError("Unknown research origin")
        sources = {s.identifier: s for s in self.sources}
        if not sources or len(sources) != len(self.sources):
            raise ValueError("Unique retained sources required")
        if self.settlement_source_id not in sources:
            raise ValueError("Independent settlement calendar source required")
        for item in (*self.closes, *self.bars, *self.distributions, *self.splits, *self.securities):
            if item.source_id not in sources or item.asset not in UNIVERSE:
                raise ValueError("Unbound source or asset outside the fixed ETF universe")
        calendar = SessionCalendar()
        close_keys = [(x.session, x.asset) for x in self.closes]
        if not close_keys or close_keys != sorted(set(close_keys)):
            raise ValueError("Unique chronological raw closes required")
        sessions = sorted({x.session for x in self.closes})
        if any(calendar.shift(a, 1) != b for a, b in zip(sessions, sessions[1:])):
            raise ValueError("Missing raw-close session")
        if any(calendar.session(d) is None for d in sessions):
            raise ValueError("Raw close outside trading sessions")
        if any(n != len(UNIVERSE) for n in Counter(x.session for x in self.closes).values()):
            raise ValueError("Every close session requires the full universe")
        if any(number(x.close) <= 0 for x in self.closes):
            raise ValueError("Raw closes must be positive")
        if any(
            sources[x.source_id].observed_at < calendar.session(x.session).market_close
            for x in self.closes
        ):
            raise ValueError("Source observation precedes the claimed completed close")
        bar_keys = [(x.end, x.asset) for x in self.bars]
        if not bar_keys or bar_keys != sorted(set(bar_keys)):
            raise ValueError("Unique chronological execution bars required")
        for bar in self.bars:
            aware(bar.end)
            session = calendar.session(bar.end.astimezone(NY).date())
            values = [number(getattr(bar, k)) for k in ("open", "high", "low", "close")]
            if (
                session is None
                or not session.market_open + timedelta(minutes=1) <= bar.end <= session.market_close
                or bar.end.second
                or bar.end.microsecond
                or min(values) <= 0
                or values[1] < max(values)
                or values[2] > min(values)
                or number(bar.volume) < 0
                or bar.end.astimezone(NY).date() not in sessions
                or sources[bar.source_id].observed_at < bar.end
            ):
                raise ValueError("Invalid completed RTH minute bar")
        if any(n != len(UNIVERSE) for n in Counter(x.end for x in self.bars).values()):
            raise ValueError("Execution timestamps require all ETF bars")
        by_day = {}
        for bar in self.bars:
            day = bar.end.astimezone(NY).date()
            by_day.setdefault(day, set()).add(bar.end)
        expected_days = {d for d in sessions if min(by_day) <= d <= max(by_day)}
        if set(by_day) != expected_days:
            raise ValueError("Missing execution session")
        for day, times in by_day.items():
            session = calendar.session(day)
            required = {session.market_open + timedelta(minutes=m) for m in (1, *range(5, 16))}
            required.add(session.market_close)
            if not required <= times:
                raise ValueError("Missing execution-window minute or daily NAV close")
        daily_closes = {(x.session, x.asset): number(x.close) for x in self.closes}
        for bar in self.bars:
            day = bar.end.astimezone(NY).date()
            if (
                bar.end == calendar.session(day).market_close
                and abs(number(bar.close) - daily_closes[day, bar.asset]) > RAW_CLOSE_TOLERANCE
            ):
                raise ValueError("Raw daily/minute close mismatch; reconcile source basis")
        # The processor keys all action kinds by security/event identity. Its
        # modeled credit also occupies that namespace; never overwrite a split
        # or another dividend while constructing the retrospective provider.
        action_keys = [(x.asset, x.event_id) for x in (*self.distributions, *self.splits)]
        action_keys.extend((x.asset, "modeled-credit:" + x.event_id) for x in self.distributions)
        if len(set(action_keys)) != len(action_keys):
            raise ValueError("Conflicting corporate action identity, including modeled credits")
        for group in (self.distributions, self.splits):
            if len({(x.asset, x.event_id) for x in group}) != len(group):
                raise ValueError("Duplicate corporate action")
        for event in self.distributions:
            signal_only = event.ex_date < self.bars[0].end.astimezone(NY).date()
            if (
                not event.event_id
                or (
                    event.classification_source_id not in sources
                    and not (signal_only and event.classification_source_id is None)
                )
                or event.classification
                not in (
                    {"ordinary_fund", "unclassified_signal_only"}
                    if signal_only
                    else {"ordinary_fund"}
                )
                or number(event.cash_amount) <= 0
                or event.ex_date > event.record_date
                or event.record_date > event.pay_date
                or calendar.session(event.ex_date) is None
            ):
                raise ValueError("Unsupported or unbound distribution classification")
        for split in self.splits:
            if (
                not split.event_id
                or number(split.new_per_old) <= 0
                or calendar.session(split.session) is None
            ):
                raise ValueError("Invalid split")
        if {s.asset for s in self.securities} != set(UNIVERSE) or len(self.securities) != len(
            UNIVERSE
        ):
            raise ValueError("Complete ETF identity coverage required")
        if len({s.security_id for s in self.securities}) != len(UNIVERSE):
            raise ValueError("Distinct stable ETF identities required")
        for security in self.securities:
            if (
                not security.security_id
                or security.issuer_country != "US"
                or security.tax_source_country != "US"
                or security.security_type != "ETF"
                or security.is_depositary_receipt is not False
            ):
                raise ValueError("Research universe excludes Canadian securities and all ADRs")
        dates = self.settlement_sessions
        first, last = self.bars[0].end.date(), self.bars[-1].end.date()
        if (
            not dates
            or tuple(sorted(set(dates))) != dates
            or any(type(d) is not date or d.weekday() >= 5 for d in dates)
            or dates[0] > first
            or dates[-1] <= last
        ):
            raise ValueError("Independent settlement calendar must span and extend the run")

    @property
    def observed_at(self) -> datetime:
        return max(s.observed_at for s in self.sources)

    @property
    def source_map(self) -> Mapping[str, ResearchSource]:
        return MappingProxyType({s.identifier: s for s in self.sources})

    @property
    def fingerprint(self) -> str:
        return digest(
            json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), default=str).encode()
        )

    @classmethod
    def from_bundle(cls, body: bytes, expected_sha256: str, source_bodies: Mapping[str, bytes]):
        """Verify normalized bundle and every referenced retained source before parsing."""
        if digest(body) != expected_sha256:
            raise ValueError("Normalized research bundle hash mismatch")
        document = json.loads(body, parse_float=Decimal)
        if document.get("schema") != "rotation_retrospective_inputs_v1":
            raise ValueError("Unsupported research schema")
        sources = tuple(
            ResearchSource(
                s["identifier"], s["sha256"], datetime.fromisoformat(s["observed_at"]), s["url"]
            )
            for s in document["sources"]
        )
        if set(source_bodies) != {s.identifier for s in sources} or any(
            digest(source_bodies[s.identifier]) != s.sha256 for s in sources
        ):
            raise ValueError("Missing or changed retained source bytes")
        return cls(
            sources,
            tuple(
                RawClose(
                    x["asset"], date.fromisoformat(x["session"]), number(x["close"]), x["source_id"]
                )
                for x in document["closes"]
            ),
            tuple(
                ResearchBar(
                    x["asset"],
                    datetime.fromisoformat(x["end"]),
                    number(x["open"]),
                    number(x["high"]),
                    number(x["low"]),
                    number(x["close"]),
                    number(x["volume"]),
                    x["source_id"],
                )
                for x in document["bars"]
            ),
            tuple(
                ResearchDistribution(
                    x["asset"],
                    x["event_id"],
                    date.fromisoformat(x["ex_date"]),
                    date.fromisoformat(x["record_date"]),
                    date.fromisoformat(x["pay_date"]),
                    number(x["cash_amount"]),
                    x["classification"],
                    x["source_id"],
                    x["classification_source_id"],
                )
                for x in document["distributions"]
            ),
            tuple(
                ResearchSplit(
                    x["asset"],
                    x["event_id"],
                    date.fromisoformat(x["session"]),
                    number(x["new_per_old"]),
                    x["source_id"],
                )
                for x in document["splits"]
            ),
            tuple(ResearchSecurity(**x) for x in document["securities"]),
            tuple(date.fromisoformat(d) for d in document["settlement_sessions"]),
            document["settlement_source_id"],
            document["origin"],
        )


def raw_total_returns(inputs: ResearchInputs) -> tuple[tuple[date, Mapping[str, Decimal]], ...]:
    """Ex-close gross reinvestment in original share units, including effective splits.

    This signal is not broker cash or DRIP. Later split normalization is never used.
    """
    rows: dict[date, dict[str, Decimal]] = {}
    for row in inputs.closes:
        rows.setdefault(row.session, {})[row.asset] = number(row.close)
    dividends: dict[tuple[date, str], Decimal] = {}
    splits: dict[tuple[date, str], Decimal] = {}
    for event in inputs.distributions:
        key = event.ex_date, event.asset
        dividends[key] = dividends.get(key, Decimal(0)) + number(event.cash_amount)
    for event in inputs.splits:
        key = event.session, event.asset
        splits[key] = splits.get(key, Decimal(1)) * number(event.new_per_old)
    result = []
    previous = None
    index = dict.fromkeys(UNIVERSE, Decimal(1))
    with localcontext() as context:
        context.prec = 60
        for day, closes in sorted(rows.items()):
            if previous is not None:
                index = {
                    asset: index[asset]
                    * splits.get((day, asset), Decimal(1))
                    * (closes[asset] + dividends.get((day, asset), Decimal(0)))
                    / previous[asset]
                    for asset in UNIVERSE
                }
            result.append((day, MappingProxyType(dict(index))))
            previous = closes
    return tuple(result)


class RetrospectiveCloseSource(CausalCloseSource):
    """Modeled close availability; source capture is expressly not historical PIT."""

    evidence_mode = "retrospective_research"

    def __init__(self, inputs: ResearchInputs):
        self.inputs = inputs
        calendar = SessionCalendar()
        rows = []
        for day, values in raw_total_returns(inputs):
            converted = {asset: float(value) for asset, value in values.items()}
            if not all(math.isfinite(v) and v > 0 for v in converted.values()):
                raise ValueError("Signal conversion is outside finite positive float range")
            session = calendar.session(day)
            assert session is not None
            rows.append(TotalReturnClose(day, session.market_close, converted))
        super().__init__(rows)
