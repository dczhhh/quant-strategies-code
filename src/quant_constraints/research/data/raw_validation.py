"""5C offline split-consistency checks, NOT a real-market certificate or engine token.

This first stage accepts explicitly synthetic canonical 5A inputs only. Hashes bind
the actual inputs, not the truth of their declarations. No network, archive, broker,
PIT provider or VerifiedDataset is created by this module.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Any

from quant_constraints.calendar import NY, SessionCalendar

from .contracts import (
    ActionKind,
    ContractError,
    CorporateActionRecord,
    IdentityMap,
    PriceBasis,
    RawBar,
    SignalBar,
    VolumeUnit,
)
from .contracts.errors import day, number, text, timestamp
from .normalize import normalize_raw_bars, normalize_record, normalize_signal_bars

VALIDATOR_VERSION = "research_raw_offline_v1"
MAX_BARS = 12_500
MAX_SPLITS = 128
MAX_FACT_BYTES = 16_384
HASH_ROLES = ("raw", "comparison", "actions", "identities", "facts", "definition")
PENDING = (
    "synthetic_only_not_market_evidence",
    "provider_definition_and_volume_policy_unverified",
    "independent_source_authenticity_unverified",
    "event_coverage_unverified",
    "identity_and_market_data_PIT_unverified",
    "license_and_real_archive_deferred_by_user",
    "trusted_entry_blocked",
)


class VolumePolicy(StrEnum):
    AS_TRADED = "as_traded"
    MULTIPLY_SPLIT_FACTOR = "multiply_split_factor"
    UNKNOWN = "unknown"


class ValidationError(ValueError):
    def __init__(self, code: str, field: str):
        self.code, self.field = code, field
        # Never include submitted values, source document bodies or prices.
        super().__init__(f"{code}:{field}")


def _reject(code: str, field: str) -> None:
    raise ValidationError(code, field)


def _wire(value: Any) -> Any:
    if isinstance(value, datetime):
        return timestamp(value, "timestamp").isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
    if isinstance(value, IdentityMap):
        return _wire(value.entries)  # Never serialize the derived lookup cache.
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _wire(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_wire(item) for item in value]
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(_wire(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True, slots=True, kw_only=True)
class SplitFact:
    """Synthetic independent fact, with exact document bytes and publication clock.

    trading_date is the first session in NEW share units, not the legal amendment
    or after-close distribution date. Real issuer/SEC parsers are future work.
    """

    security_id: str
    event_id: str
    event_version: int
    trading_date: date
    new_shares: int
    old_shares: int
    published_at: datetime
    source_provider: str
    source_ref: str
    document: bytes
    document_sha256: str

    def payload(self) -> bytes:
        return _canonical(
            {
                "security_id": self.security_id,
                "event_id": self.event_id,
                "event_version": self.event_version,
                "trading_date": self.trading_date,
                "new_shares": self.new_shares,
                "old_shares": self.old_shares,
                "published_at": self.published_at,
                "source_provider": self.source_provider,
                "source_ref": self.source_ref,
            }
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class FactorDefinition:
    """Declared synthetic adjustment policy and bounded comparison precision."""

    security_id: str
    provider: str
    dataset_revision: str
    raw_product_id: str
    comparison_product_id: str
    adjustment_version: str
    definition_ref: str
    frequency: str
    window_start: datetime
    window_end: datetime
    adjustment_anchor: datetime
    action_coverage_start: datetime
    action_coverage_end: datetime
    volume_policy: VolumePolicy
    input_kind: str
    raw_price_quantum: float = 0.0001
    comparison_price_quantum: float = 0.0001
    raw_volume_quantum: float = 1.0
    comparison_volume_quantum: float = 1.0


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidationInput:
    raw: tuple[RawBar, ...]
    comparison: tuple[SignalBar, ...]
    actions: tuple[CorporateActionRecord, ...]
    identities: IdentityMap
    facts: tuple[SplitFact, ...]
    definition: FactorDefinition
    expected_hashes: tuple[tuple[str, str], ...]


def input_hashes(value: ValidationInput) -> tuple[tuple[str, str], ...]:
    """Freeze six actual canonical inputs BEFORE validation; never a trust signature.

    Recomputing this binding after changing data does not prove authenticity. The
    result remains synthetic-only even when every new hash is internally valid.
    """
    return tuple(
        (role, hashlib.sha256(_canonical(getattr(value, role))).hexdigest()) for role in HASH_ROLES
    )


@dataclass(frozen=True, slots=True)
class ValidationResult:
    status: str
    code: str
    field: str
    input_sha256: str | None
    source_hashes: tuple[tuple[str, str], ...] = ()
    compared_bars: int = 0
    first_bar_at: datetime | None = None
    last_bar_at: datetime | None = None
    missing_rth_minutes: tuple[tuple[str, int], ...] = ()
    validator_version: str = VALIDATOR_VERSION
    pending_evidence: tuple[str, ...] = PENDING
    raw_verified: bool = False
    PIT_verified: bool = False
    archive_ready: bool = False
    trusted_entry: bool = False

    def as_dict(self) -> dict:
        """Only diagnostics, counts, clocks and hashes; never input market values."""
        return _wire(self)


def _definition(value: FactorDefinition) -> None:
    if type(value) is not FactorDefinition:
        _reject("INVALID_TYPE", "definition")
    if value.input_kind != "synthetic":
        _reject("REAL_DATA_DEFERRED", "input_kind")
    for field in (
        "security_id",
        "provider",
        "dataset_revision",
        "raw_product_id",
        "comparison_product_id",
        "adjustment_version",
        "definition_ref",
    ):
        text(getattr(value, field), field)
    if value.raw_product_id == value.comparison_product_id:
        _reject("PRODUCT_COLLISION", "product_id")
    if value.frequency not in {"1m", "1d"}:
        _reject("UNSUPPORTED_FREQUENCY", "frequency")
    if (
        not isinstance(value.volume_policy, VolumePolicy)
        or value.volume_policy is VolumePolicy.UNKNOWN
    ):
        _reject("VOLUME_POLICY_UNKNOWN", "volume_policy")
    for field in (
        "window_start",
        "window_end",
        "adjustment_anchor",
        "action_coverage_start",
        "action_coverage_end",
    ):
        timestamp(getattr(value, field), field)
    if value.frequency == "1m" and any(
        at.second or at.microsecond for at in (value.window_start, value.window_end)
    ):
        _reject("BAR_RANGE_INVALID", "window")
    if (
        not value.window_start < value.window_end <= value.adjustment_anchor
        or value.window_end - value.window_start > timedelta(days=31)
        or value.action_coverage_start > value.window_start
        or value.action_coverage_end <= value.adjustment_anchor
    ):
        _reject("FACTOR_COVERAGE_GAP", "coverage")
    for field in ("raw_price_quantum", "comparison_price_quantum"):
        if number(getattr(value, field), field, positive=True) > 0.01:
            _reject("PRECISION_UNSUPPORTED", field)
    for field in ("raw_volume_quantum", "comparison_volume_quantum"):
        if number(getattr(value, field), field, positive=True) > 1:
            _reject("PRECISION_UNSUPPORTED", field)


def _fact(fact: SplitFact, action: CorporateActionRecord, definition: FactorDefinition) -> Decimal:
    if type(fact) is not SplitFact:
        _reject("INVALID_TYPE", "facts")
    for field in ("security_id", "event_id", "source_provider", "source_ref"):
        text(getattr(fact, field), field)
    day(fact.trading_date, "trading_date")
    published = timestamp(fact.published_at, "published_at")
    if (
        type(fact.event_version) is not int
        or fact.event_version < 1
        or any(
            type(n) is not int or not 1 <= n <= 10**12 for n in (fact.new_shares, fact.old_shares)
        )
    ):
        _reject("FACTOR_INVALID", "ratio")
    if (
        type(fact.document) is not bytes
        or not 0 < len(fact.document) <= MAX_FACT_BYTES
        or hashlib.sha256(fact.document).hexdigest() != fact.document_sha256
    ):
        _reject("SOURCE_HASH_MISMATCH", "fact.document")
    if fact.document != fact.payload():
        _reject("SOURCE_CONTENT_MISMATCH", "fact.document")
    if fact.source_provider == definition.provider:
        _reject("INDEPENDENT_SOURCE_MISSING", "source_provider")
    if action.historically_known_at is None or action.knowledge_evidence != fact.source_ref:
        _reject("KNOWLEDGE_EVIDENCE_MISSING", "knowledge_evidence")
    if published != action.historically_known_at or published > definition.adjustment_anchor:
        _reject("KNOWLEDGE_TIME_CONFLICT", "historically_known_at")
    ratio = Decimal(fact.new_shares) / Decimal(fact.old_shares)
    if (
        fact.security_id != action.security_id
        or fact.event_id != action.event_id
        or fact.event_version != action.event_version
        or fact.trading_date != action.effective_date
        or action.split_ratio is None
        or not math.isclose(float(ratio), action.split_ratio, rel_tol=1e-12, abs_tol=0)
    ):
        _reject("FACTOR_CONFLICT", "split_fact")
    if not Decimal("0.000001") <= ratio <= Decimal("1000000"):
        _reject("FACTOR_INVALID", "ratio")
    return ratio


def _events(
    value: ValidationInput, calendar: SessionCalendar
) -> tuple[tuple[datetime, Decimal], ...]:
    definition = value.definition
    if (
        type(value.actions) is not tuple
        or type(value.facts) is not tuple
        or not 0 < len(value.actions) <= MAX_SPLITS
        or len(value.actions) != len(value.facts)
    ):
        _reject("FACTOR_EVIDENCE_MISSING", "actions")
    facts: dict[str, SplitFact] = {}
    for fact in value.facts:
        if type(fact) is not SplitFact or fact.event_id in facts:
            _reject("FACTOR_CONFLICT", "facts")
        facts[fact.event_id] = fact
    seen: set[str] = set()
    events = []
    for original in value.actions:
        if type(original) is not CorporateActionRecord:
            _reject("INVALID_TYPE", "actions")
        action = normalize_record(CorporateActionRecord, asdict(original))
        if (
            action.kind is not ActionKind.SPLIT
            or action.cancelled
            or action.event_version != 1
            or action.revision_of is not None
        ):
            _reject("ACTION_REVISION_UNSUPPORTED", "actions")
        if action.security_id != definition.security_id:
            _reject("IDENTITY_CONFLICT", "action.security_id")
        if action.event_id in seen or action.event_id not in facts:
            _reject("FACTOR_CONFLICT", "event_id")
        seen.add(action.event_id)
        session = calendar.session(action.effective_date)
        if session is None:
            _reject("EVENT_SESSION_INVALID", "effective_date")
        at = session.market_open
        if not definition.action_coverage_start <= at < definition.action_coverage_end:
            _reject("FACTOR_COVERAGE_GAP", "effective_date")
        events.append((at, _fact(facts[action.event_id], action, definition)))
    return tuple(sorted(events, key=lambda event: event[0]))


def _interval(bar: RawBar, definition: FactorDefinition, calendar: SessionCalendar) -> None:
    if not definition.window_start <= bar.bar_start_at < bar.bar_end_at <= definition.window_end:
        _reject("BAR_RANGE_INVALID", "bar_start_at")
    session = calendar.session(bar.bar_start_at.astimezone(NY).date())
    if session is None:
        _reject("SESSION_INVALID", "bar_start_at")
    if definition.frequency == "1m":
        if (
            bar.bar_start_at.second
            or bar.bar_start_at.microsecond
            or bar.bar_end_at - bar.bar_start_at != timedelta(minutes=1)
            or not session.market_open <= bar.bar_start_at < bar.bar_end_at <= session.market_close
        ):
            _reject("SESSION_INVALID", "bar_start_at")
    else:
        local = bar.bar_start_at.astimezone(NY)
        end = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), NY)
        if local.time() != datetime.min.time() or bar.bar_end_at != end:
            _reject("DAILY_TIME_BASIS_INVALID", "bar_start_at")


def _missing(bars: tuple[RawBar, ...], definition: FactorDefinition, calendar: SessionCalendar):
    if definition.frequency != "1m":
        return ()
    counts: dict[str, int] = {}
    for bar in bars:
        key = bar.bar_start_at.astimezone(NY).date().isoformat()
        counts[key] = counts.get(key, 0) + 1
    result = []
    at = definition.window_start.astimezone(NY).date()
    last = definition.window_end.astimezone(NY).date()
    while at <= last:
        session = calendar.session(at)
        if session:
            lower = max(session.market_open, definition.window_start)
            upper = min(session.market_close, definition.window_end)
            expected = max(0, int((upper - lower).total_seconds() / 60))
            if expected:
                result.append((at.isoformat(), expected - counts.get(at.isoformat(), 0)))
        at += timedelta(days=1)
    return tuple(result)  # Missing minutes are unknown, never filled or called zero trading.


class RawOHLCVValidator:
    """Return a sanitized synthetic-algorithm result; never mutate account state."""

    def validate(self, value: ValidationInput) -> ValidationResult:
        hashes: tuple[tuple[str, str], ...] = ()
        combined = None
        try:
            if type(value) is not ValidationInput:
                _reject("INVALID_TYPE", "input")
            _definition(value.definition)
            if (
                type(value.raw) is not tuple
                or type(value.comparison) is not tuple
                or not 0 < len(value.raw) <= MAX_BARS
                or not 0 < len(value.comparison) <= MAX_BARS
                or type(value.identities) is not IdentityMap
                or not 0 < len(value.identities.entries) <= 256
                or not all(type(b) is RawBar for b in value.raw)
                or not all(type(b) is SignalBar for b in value.comparison)
                or type(value.actions) is not tuple
                or type(value.facts) is not tuple
                or len(value.actions) > MAX_SPLITS
                or len(value.facts) > MAX_SPLITS
                or any(
                    type(f) is not SplitFact
                    or type(f.document) is not bytes
                    or len(f.document) > MAX_FACT_BYTES
                    for f in value.facts
                )
            ):
                _reject("INVALID_TYPE_OR_SIZE", "bars")
            hashes = input_hashes(value)
            combined = hashlib.sha256(_canonical(hashes)).hexdigest()
            if value.expected_hashes != hashes:
                _reject("INPUT_HASH_MISMATCH", "expected_hashes")
            # Reconstruct contracts rather than treating frozen instances as certificates.
            identities = IdentityMap(
                tuple(normalize_record(type(e), asdict(e)) for e in value.identities.entries)
            )
            raw = normalize_raw_bars((asdict(b) for b in value.raw), identities)
            comparison = normalize_signal_bars((asdict(b) for b in value.comparison), identities)
            calendar = SessionCalendar()
            with localcontext() as context:
                context.prec = 50
                events = _events(value, calendar)
                self._compare(raw, comparison, value.definition, events, calendar)
            return ValidationResult(
                "offline_algorithm_pass",
                "SYNTHETIC_SPLIT_CONSISTENT",
                "",
                combined,
                hashes,
                len(raw),
                raw[0].bar_start_at,
                raw[-1].bar_start_at,
                _missing(raw, value.definition, calendar),
            )
        except (ValidationError, ContractError) as error:
            return ValidationResult("reject", str(error.code), error.field, combined, hashes)
        except (TypeError, ValueError, OverflowError, AttributeError):
            return ValidationResult("reject", "INVALID_INPUT", "input", combined, hashes)

    @staticmethod
    def _compare(raw, comparison, definition, events, calendar) -> None:
        def key(bar):
            return bar.security_id, bar.venue, bar.bar_start_at, bar.bar_end_at

        paired = {key(bar): bar for bar in comparison}
        if set(paired) != {key(bar) for bar in raw}:
            _reject("COMPARISON_COVERAGE_GAP", "comparison")
        contrast = False
        factors: dict[date, Decimal] = {}
        for bar in raw:
            signal = paired[key(bar)]
            _interval(bar, definition, calendar)
            for record in (bar, signal):
                if (
                    record.security_id != definition.security_id
                    or record.currency != "USD"
                    or record.source.provider != definition.provider
                    or record.source.dataset_revision != definition.dataset_revision
                    or record.source.source_timezone != "America/New_York"
                ):
                    _reject("PROVENANCE_CONFLICT", "source")
            if (
                bar.symbol != signal.symbol
                or signal.available_at < signal.bar_end_at
                or bar.product_id != definition.raw_product_id
                or signal.product_id != definition.comparison_product_id
                or signal.raw_product_id != bar.product_id
                or signal.price_basis is not PriceBasis.SPLIT_ADJUSTED
                or signal.adjustment_version != definition.adjustment_version
            ):
                _reject("PRODUCT_OR_ADJUSTMENT_CONFLICT", "comparison")
            trading_day = bar.bar_start_at.astimezone(NY).date()
            factor = factors.get(trading_day)
            if factor is None:
                factor = Decimal(1)
                # New/old share ratios: backward-adjusted price = raw price / factor.
                # All accepted minutes are RTH; same-session bars already use new
                # shares. Daily midnight bars follow that same TRADING date rule.
                for effective, ratio in events:
                    if (
                        trading_day < effective.astimezone(NY).date()
                        and effective <= definition.adjustment_anchor
                    ):
                        factor *= ratio
                factors[trading_day] = factor
            contrast |= factor != 1
            for field in ("open", "high", "low", "close"):
                observed = Decimal(str(getattr(signal, field)))
                expected = Decimal(str(getattr(bar, field))) / factor
                tolerance = (
                    Decimal(str(definition.comparison_price_quantum))
                    + Decimal(str(definition.raw_price_quantum)) / factor
                ) / 2
                if tolerance >= min(observed, expected) * Decimal("0.005"):
                    _reject("PRECISION_UNSUPPORTED", "price_quantum")
                if abs(observed - expected) > tolerance:
                    _reject("RAW_ADJUSTED_PRICE_CONFLICT", field)
            scaled = definition.volume_policy is VolumePolicy.MULTIPLY_SPLIT_FACTOR
            expected_unit = (
                VolumeUnit.SPLIT_ADJUSTED_SHARES if scaled else VolumeUnit.AS_TRADED_SHARES
            )
            if signal.volume_unit is not expected_unit:
                _reject("VOLUME_UNIT_CONFLICT", "volume_unit")
            multiplier = factor if scaled else Decimal(1)
            expected_volume = Decimal(str(bar.volume)) * multiplier
            tolerance = (
                Decimal(str(definition.comparison_volume_quantum))
                + Decimal(str(definition.raw_volume_quantum)) * multiplier
            ) / 2
            if (bar.volume == 0 and signal.volume != 0) or abs(
                Decimal(str(signal.volume)) - expected_volume
            ) > tolerance:
                _reject("RAW_ADJUSTED_VOLUME_CONFLICT", "volume")
        if not contrast:
            _reject("NO_SPLIT_CONTRAST", "comparison")
