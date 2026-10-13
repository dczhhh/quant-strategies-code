"""5D-1 synthetic, offline PIT revisions; not an authenticated research entrance.

5A effective facts are retained, never substituted for historical knowledge.
Evidence documents are canonical *synthetic* declarations, not issuer/broker proof.
The explicit fixture adapter implements the existing corporate-action protocol.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Never

from quant_constraints.calendar import NY
from quant_constraints.corporate_actions import CorporateAction, CorporateActionCoverage

from .contracts import (
    ActionKind,
    ContractError,
    CorporateActionRecord,
    CreditEvidence,
    IdentityMap,
    SecurityIdentity,
    SourceProvenance,
)
from .contracts.errors import day, text, timestamp, version
from .normalize import normalize_record

PIT_VERSION = "research_pit_synthetic_v1"
MAX_REVISIONS = 256
MAX_DOCUMENT_BYTES = 16_384
ACTION_KINDS = (ActionKind.SPLIT, ActionKind.CASH_DIVIDEND, ActionKind.CASH_CREDIT)
PENDING = (
    "synthetic_only_not_historical_source_authentication",
    "real_raw_and_PIT_evidence_unverified",
    "authentic_macro_calendar_and_dividend_tax_evidence_unverified",
    "licensed_archive_deferred_by_user",
    "trusted_entry_5E_and_real_E2E_5F_blocked",
)


class PITError(ValueError):
    def __init__(self, code: str, field: str):
        self.code, self.field = code, field
        super().__init__(f"{code}:{field}")  # No supplied values or document text.


def _reject(code: str, field: str) -> Never:
    raise PITError(code, field)


def _coverage_at(
    values: tuple,
    point: date | datetime,
    *,
    inclusive: bool,
    complete: Callable[[Any], bool],
    outside_code: str = "PIT_COVERAGE_UNKNOWN",
) -> Any:
    candidates = [
        v
        for v in values
        if v.covered_from <= point
        and (point <= v.covered_until if inclusive else point < v.covered_until)
    ]
    if len(candidates) > 1:
        _reject("PIT_COVERAGE_CONFLICT", "coverage")
    if not candidates or not complete(candidates[0]):
        _reject(outside_code, "coverage")
    return candidates[0]


def _coverage_component(
    values: tuple,
    point: date | datetime,
    *,
    inclusive: bool,
    complete: Callable[[Any], bool],
) -> tuple:
    """Contiguous complete proofs around the query; never bridge gaps or overlaps."""
    anchor = _coverage_at(values, point, inclusive=inclusive, complete=complete)
    ordered = sorted(values, key=lambda v: (v.covered_from, v.covered_until))
    first = last = ordered.index(anchor)
    gap = timedelta(days=1) if inclusive else timedelta(0)
    for direction in (-1, 1):
        index = first - 1 if direction < 0 else last + 1
        while 0 <= index < len(ordered):
            left, right = (
                (ordered[index], ordered[first])
                if direction < 0
                else (ordered[last], ordered[index])
            )
            if left.covered_until > right.covered_from or (
                inclusive and left.covered_until == right.covered_from
            ):
                _reject("PIT_COVERAGE_CONFLICT", "coverage")
            if right.covered_from - left.covered_until != gap or not complete(ordered[index]):
                break
            if direction < 0:
                first = index
            else:
                last = index
            index += direction
    return tuple(ordered[first : last + 1])


def _proof_digest(proofs: tuple) -> str:
    hashes = sorted({v.document_sha256 for v in proofs})
    return hashes[0] if len(hashes) == 1 else hashlib.sha256(_canonical(hashes)).hexdigest()


def _complete_action_coverage(proof: PITCoverage) -> bool:
    return (
        not proof.missing
        and not proof.cancelled
        and proof.point_in_time
        and set(proof.action_kinds) == set(ACTION_KINDS)
    )


def _wire(value: Any) -> Any:
    if isinstance(value, datetime):
        return timestamp(value, "timestamp").isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _wire(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {k: _wire(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [_wire(v) for v in value]
    if isinstance(value, frozenset):
        return sorted(_wire(v) for v in value)
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(_wire(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True, slots=True, kw_only=True)
class PITEventRevision:
    record: CorporateActionRecord
    published_at: datetime | None
    provider_available_at: datetime | None
    document: bytes
    document_sha256: str
    schema_version: str = PIT_VERSION

    @property
    def historically_known_at(self) -> datetime | None:
        return self.record.historically_known_at

    @property
    def source(self) -> SourceProvenance:
        return self.record.source


@dataclass(frozen=True, slots=True, kw_only=True)
class PITIdentityRevision:
    identity_id: str
    version: int
    revision_of: int | None
    record: SecurityIdentity
    historically_known_at: datetime | None
    published_at: datetime | None
    provider_available_at: datetime | None
    cancelled: bool
    document: bytes
    document_sha256: str
    schema_version: str = PIT_VERSION

    @property
    def source(self) -> SourceProvenance:
        return self.record.source


@dataclass(frozen=True, slots=True, kw_only=True)
class PITCoverage:
    security_id: str
    coverage_id: str
    version: int
    revision_of: int | None
    covered_from: date
    covered_until: date  # Inclusive, matching the existing Provider contract.
    action_kinds: tuple[ActionKind, ...]
    historically_known_at: datetime | None
    published_at: datetime | None
    provider_available_at: datetime | None
    source: SourceProvenance
    point_in_time: bool
    missing: bool
    cancelled: bool
    document: bytes
    document_sha256: str
    schema_version: str = PIT_VERSION


Revision = PITEventRevision | PITIdentityRevision | PITCoverage


def evidence_payload(value: Any) -> bytes:
    """Exact bounded synthetic document to bind terms and all four clocks.

    No inference/migration from ingested_at or effective_at into public knowledge.
    This generator is intentionally usable by fixtures; it establishes no trust.
    """
    return _canonical(
        {
            f.name: getattr(value, f.name)
            for f in fields(value)
            if f.name not in {"document", "document_sha256"}
        }
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class PITDataset:
    events: tuple[PITEventRevision, ...]
    identities: tuple[PITIdentityRevision, ...]
    coverage: tuple[PITCoverage, ...]
    input_kind: str
    expected_sha256: str
    schema_version: str = PIT_VERSION


def pit_input_sha256(value: PITDataset) -> str:
    """Order-independent actual input binding; re-sealing is NOT source authentication."""
    return hashlib.sha256(
        _canonical(
            {
                "schema_version": value.schema_version,
                "input_kind": value.input_kind,
                **{
                    name: sorted(_canonical(v).decode() for v in getattr(value, name))
                    for name in ("events", "identities", "coverage")
                },
            }
        )
    ).hexdigest()


class SnapshotState(StrEnum):
    EVENTS = "events"
    PROVEN_NO_EVENTS = "proven_no_events"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class PITSnapshot:
    state: SnapshotState
    code: str
    field: str
    asof: datetime
    input_sha256: str
    coverage: CorporateActionCoverage | None = None
    events: tuple[CorporateAction, ...] = ()
    source_hashes: tuple[str, ...] = ()
    provider_version: str = PIT_VERSION
    raw_verified: bool = False
    PIT_verified: bool = False
    archive_ready: bool = False
    trusted_entry: bool = False

    def audit_dict(self) -> dict:
        """Safe report: do not emit source documents, economic terms or identities."""
        return {
            "status": "synthetic_pit_contract_pass"
            if self.state in {SnapshotState.EVENTS, SnapshotState.PROVEN_NO_EVENTS}
            else "rejected",
            "state": self.state.value,
            "code": self.code,
            "field": self.field,
            "asof": self.asof.isoformat(),
            "input_sha256": self.input_sha256,
            "source_hashes": self.source_hashes,
            "event_count": len(self.events),
            "provider_version": self.provider_version,
            "pending_evidence": PENDING,
            "raw_verified": False,
            "PIT_verified": False,
            "archive_ready": False,
            "trusted_entry": False,
        }


def _available(value: Revision) -> datetime | None:
    known, published = value.historically_known_at, value.published_at
    if known is None or published is None:
        return None
    clocks = [known, published]
    if value.provider_available_at is not None:
        clocks.append(value.provider_available_at)
    return max(clocks)


def _key(value: Revision) -> tuple[str, str]:
    if isinstance(value, PITEventRevision):
        return value.record.security_id, value.record.event_id
    if isinstance(value, PITIdentityRevision):
        return value.record.security_id, value.identity_id
    return value.security_id, value.coverage_id


def _version(value: Revision) -> int:
    return value.record.event_version if isinstance(value, PITEventRevision) else value.version


def _validate(value: Revision) -> None:
    if value.schema_version != PIT_VERSION:
        _reject("UNSUPPORTED_PIT_SCHEMA", "schema_version")
    if isinstance(value, PITEventRevision) and type(value.record) is not CorporateActionRecord:
        _reject("INVALID_TYPE", "record")
    if isinstance(value, PITIdentityRevision) and type(value.record) is not SecurityIdentity:
        _reject("INVALID_TYPE", "record")
    if type(value.source) is not SourceProvenance:
        _reject("INVALID_TYPE", "source")
    version(_version(value), "version")
    if _version(value) > MAX_REVISIONS:
        _reject("PIT_VERSION_BOUNDS", "version")
    normalize_record(SourceProvenance, asdict(value.source))
    known, published, ingested = (
        value.historically_known_at,
        value.published_at,
        value.source.ingested_at,
    )
    for name in ("historically_known_at", "published_at", "provider_available_at"):
        clock = getattr(value, name)
        if clock is not None and timestamp(clock, name) > ingested:
            _reject("PIT_TIME_ORDER", name)
    if published is not None and known is not None and published > known:
        _reject("PIT_TIME_ORDER", "historically_known_at")
    if (
        published is not None
        and value.provider_available_at is not None
        and (value.provider_available_at < published)
    ):
        _reject("PIT_TIME_ORDER", "provider_available_at")
    if type(value.document) is not bytes or not 0 < len(value.document) <= MAX_DOCUMENT_BYTES:
        _reject("PIT_DOCUMENT_BOUNDS", "document")
    if hashlib.sha256(value.document).hexdigest() != value.document_sha256:
        _reject("PIT_DOCUMENT_HASH", "document_sha256")
    if value.document != evidence_payload(value):
        _reject("PIT_DOCUMENT_TERMS", "document")
    if isinstance(value, PITEventRevision):
        # Reconstruction preserves strict enum/type rules even for mutated frozen objects.
        record = copy.copy(value.record)
        CorporateActionRecord(**{f.name: getattr(record, f.name) for f in fields(record)})
        if value.record.kind is ActionKind.CASH_CREDIT and (
            value.record.credit_evidence is not CreditEvidence.SIMULATED_ASSUMPTION
        ):
            _reject("SYNTHETIC_CREDIT_REQUIRED", "credit_evidence")
    else:
        version(value.version, "version")
        if value.revision_of is not None:
            version(value.revision_of, "revision_of")
        if (value.version == 1 and value.revision_of is not None) or (
            value.version > 1 and value.revision_of != value.version - 1
        ):
            _reject("PIT_REVISION_LINK", "revision_of")
        if type(value.cancelled) is not bool:
            _reject("INVALID_TYPE", "cancelled")
        if isinstance(value, PITIdentityRevision):
            text(value.identity_id, "identity_id")
            SecurityIdentity(
                **{f.name: getattr(value.record, f.name) for f in fields(value.record)}
            )
        else:
            for name in ("security_id", "coverage_id"):
                text(getattr(value, name), name)
            day(value.covered_from, "covered_from")
            day(value.covered_until, "covered_until")
            if value.covered_from > value.covered_until:
                _reject("PIT_COVERAGE_RANGE", "covered_until")
            if (
                type(value.action_kinds) is not tuple
                or not value.action_kinds
                or any(
                    kind not in ACTION_KINDS or not isinstance(kind, ActionKind)
                    for kind in value.action_kinds
                )
                or len(set(value.action_kinds)) != len(value.action_kinds)
            ):
                _reject("PIT_COVERAGE_KINDS", "action_kinds")
            if type(value.point_in_time) is not bool or type(value.missing) is not bool:
                _reject("INVALID_TYPE", "coverage")


def _latest(values: tuple[Revision, ...], asof: datetime) -> tuple[Revision, ...]:
    """Only visible versions participate; future conflicts cannot rewrite the past."""
    groups: dict[tuple[str, str], dict[int, Revision]] = {}
    for value in values:
        available = _available(value)
        if available is None:
            _reject("PIT_KNOWLEDGE_UNKNOWN", "historically_known_at")
        if available > asof:
            continue
        group = groups.setdefault(_key(value), {})
        previous = group.get(_version(value))
        if previous is not None and previous != value:
            _reject("PIT_REVISION_CONFLICT", "version")
        group[_version(value)] = value
    result = []
    for key in sorted(groups):
        group = groups[key]
        versions = sorted(group)
        if versions != list(range(1, versions[-1] + 1)):
            _reject("PIT_REVISION_GAP", "version")
        for first, second in zip(versions, versions[1:]):
            before, after = group[first], group[second]
            # _available is non-None because these revisions are visible.
            before_at, after_at = _available(before), _available(after)
            if before_at is None or after_at is None:
                _reject("PIT_KNOWLEDGE_UNKNOWN", "historically_known_at")
            if before_at >= after_at:
                _reject("PIT_REVISION_CLOCK", "published_at")
            if (
                isinstance(before, PITEventRevision)
                and isinstance(after, PITEventRevision)
                and (before.record.kind is not after.record.kind)
            ):
                _reject("PIT_REVISION_KIND", "kind")
        result.append(group[versions[-1]])
    return tuple(result)


def _index(values: tuple[Revision, ...]) -> Mapping[str, tuple[Revision, ...]]:
    groups: dict[str, list[Revision]] = {}
    for value in values:
        groups.setdefault(_key(value)[0], []).append(value)
    return MappingProxyType({key: tuple(group) for key, group in groups.items()})


def _action(value: PITEventRevision) -> CorporateAction:
    record, available = value.record, _available(value)
    if available is None:
        _reject("PIT_KNOWLEDGE_UNKNOWN", "historically_known_at")
    if record.kind is ActionKind.CASH_CREDIT:
        # An explicitly simulated plan is not an early broker confirmation.
        available = max(available, datetime.combine(record.effective_date, time(), NY))
    return CorporateAction(
        security_id=record.security_id,
        event_id=record.event_id,
        kind=record.kind.value.upper(),
        effective_date=record.effective_date,
        available_at=available,
        source=f"synthetic:{value.document_sha256}",
        version=record.event_version,
        currency=record.currency,
        split_ratio=record.split_ratio,
        dividend_per_share=record.dividend_per_share,
        record_date=record.record_date,
        payable_date=record.payable_date,
        parent_event_id=record.parent_event_id,
        credited_net=record.credited_net,
        cancelled=record.cancelled,
    )


@dataclass(frozen=True, slots=True, init=False, repr=False, eq=False)
class SyntheticPITProvider:
    """Explicit fixture-only, copied and hash-checked at construction, read-only queries.

    Indexed by stable security ID, never reads a broker or unrelated security history.
    Replaced input bytes must construct/revalidate a new provider. Python private
    objects are not a tamper-proof security boundary; 5E remains pending.
    """

    _data: PITDataset
    input_sha256: str
    _events: Mapping[str, tuple[Revision, ...]]
    _identities: Mapping[str, tuple[Revision, ...]]
    _coverage: Mapping[str, tuple[Revision, ...]]
    _symbols: Mapping[tuple[str, str], tuple[str, ...]]

    def __init__(self, dataset: PITDataset):
        try:
            if type(dataset) is not PITDataset:
                _reject("INVALID_TYPE", "dataset")
            if dataset.schema_version != PIT_VERSION:
                _reject("UNSUPPORTED_PIT_SCHEMA", "schema_version")
            if dataset.input_kind != "synthetic":
                _reject("REAL_DATA_DEFERRED", "input_kind")
            for name, cls in (
                ("events", PITEventRevision),
                ("identities", PITIdentityRevision),
                ("coverage", PITCoverage),
            ):
                values = getattr(dataset, name)
                if (
                    type(values) is not tuple
                    or len(values) > MAX_REVISIONS
                    or any(type(value) is not cls for value in values)
                ):
                    _reject("PIT_INPUT_BOUNDS_OR_TYPE", name)
                for value in values:
                    _validate(value)
            data = copy.deepcopy(dataset)
            digest = pit_input_sha256(data)
            if digest != data.expected_sha256:
                _reject("PIT_INPUT_HASH", "expected_sha256")
            object.__setattr__(self, "_data", data)
            object.__setattr__(self, "input_sha256", digest)
            for name in ("events", "identities", "coverage"):
                object.__setattr__(self, "_" + name, _index(getattr(data, name)))
            # Reject ticker reuse even across nonoverlapping/future knowledge versions.
            symbols: dict[tuple[str, str], set[str]] = {}
            for value in data.identities:
                identity = value.record
                symbols.setdefault((identity.symbol, identity.venue), set()).add(
                    identity.security_id
                )
            if any(len(group) > 1 for group in symbols.values()):
                _reject("UNSUPPORTED_IDENTITY", "symbol")
            object.__setattr__(
                self,
                "_symbols",
                MappingProxyType({key: tuple(sorted(group)) for key, group in symbols.items()}),
            )
            owners: dict[str, str] = {}
            for value in data.identities:
                old = owners.setdefault(value.identity_id, value.record.security_id)
                if old != value.record.security_id:
                    _reject("PIT_IDENTITY_OWNER", "identity_id")
        except ContractError as error:
            raise PITError(error.code.value, error.field) from None

    def __reduce__(self):
        return type(self), (self._data,)  # Recheck canonical input and rebuild indexes.

    def inspect(self, security_id: str, asof: datetime) -> PITSnapshot:
        """Four explicit states, with no silent empty-event fallback or account mutation."""
        try:
            asof = timestamp(asof, "asof")
            text(security_id, "security_id")
        except ContractError as error:
            raise PITError(error.code.value, error.field) from None
        try:
            today = asof.astimezone(NY).date()
            selected = _latest(self._coverage.get(security_id, ()), asof)
            proofs = _coverage_component(
                selected, today, inclusive=True, complete=_complete_action_coverage
            )
            revisions = _latest(self._events.get(security_id, ()), asof)
            events = tuple(
                _action(value) for value in revisions if isinstance(value, PITEventRevision)
            )
            event_proofs = tuple(
                _coverage_at(
                    selected,
                    event.effective_date,
                    inclusive=True,
                    complete=_complete_action_coverage,
                    outside_code="PIT_EVENT_OUTSIDE_COVERAGE",
                )
                for event in events
            )
            visible = {event.event_id: event for event in events}
            credited = set()
            for event in events:
                if event.kind != "CASH_CREDIT" or event.cancelled or event.available_at > asof:
                    continue
                parent = visible.get(event.parent_event_id)
                if (
                    parent is None
                    or parent.cancelled
                    or parent.kind != "CASH_DIVIDEND"
                    or (parent.effective_date > event.effective_date)
                ):
                    _reject("PIT_CREDIT_PARENT", "parent_event_id")
                if event.parent_event_id in credited:
                    _reject("PIT_DUPLICATE_CREDIT", "parent_event_id")
                parent_proofs = _coverage_component(
                    selected,
                    parent.effective_date,
                    inclusive=True,
                    complete=_complete_action_coverage,
                )
                if event.effective_date > parent_proofs[-1].covered_until:
                    _reject("PIT_CREDIT_COVERAGE", "effective_date")
                credited.add(event.parent_event_id)
            events = tuple(event for event in events if event.available_at <= asof)
            # Tombstones are supplied to the Processor, not silently removed.
            state = (
                SnapshotState.EVENTS
                if any(not event.cancelled for event in events)
                else (SnapshotState.PROVEN_NO_EVENTS)
            )
            # _latest has already required and bounded every proof's knowledge clock.
            coverage_at = max(_available(proof) for proof in proofs)
            coverage = CorporateActionCoverage(
                security_id,
                coverage_at,
                proofs[0].covered_from,
                proofs[-1].covered_until,
                f"synthetic:{_proof_digest(proofs)}",
                point_in_time=True,
            )
            hashes = tuple(
                sorted({v.document_sha256 for v in (*proofs, *event_proofs, *revisions)})
            )
            return PITSnapshot(
                state,
                "SYNTHETIC_PIT_CONSISTENT",
                "",
                asof,
                self.input_sha256,
                coverage,
                events,
                hashes,
            )
        except PITError as error:
            state = (
                SnapshotState.UNKNOWN
                if error.code in {"PIT_KNOWLEDGE_UNKNOWN", "PIT_COVERAGE_UNKNOWN"}
                else SnapshotState.CONFLICT
            )
            return PITSnapshot(state, error.code, error.field, asof, self.input_sha256)

    def snapshot(
        self, security_id: str, asof: datetime
    ) -> tuple[CorporateActionCoverage | None, tuple[CorporateAction, ...]]:
        """Existing protocol; rejecting UNKNOWN/CONFLICT happens before ledger changes."""
        result = self.inspect(security_id, asof)
        if result.state in {SnapshotState.UNKNOWN, SnapshotState.CONFLICT}:
            _reject(result.code, result.field)
        return result.coverage, result.events

    def resolve_asof(
        self, symbol: str, venue: str, effective_at: datetime, known_at: datetime
    ) -> SecurityIdentity:
        """A separate knowledge-aware lookup; 5A resolve_effective stays unchanged."""
        try:
            text(symbol, "symbol")
            text(venue, "venue")
            effective_at, known_at = (
                timestamp(effective_at, "effective_at"),
                timestamp(known_at, "known_at"),
            )
            # Inspect only securities indexed for this symbol/venue, not every identity.
            securities = self._symbols.get((symbol, venue), ())
            entries = []
            for security_id in sorted(securities):
                values = _latest(self._identities.get(security_id, ()), known_at)
                entries.extend(
                    value.record
                    for value in values
                    if isinstance(value, PITIdentityRevision) and not value.cancelled
                )
            result = IdentityMap(tuple(entries)).resolve_effective(symbol, venue, effective_at)
            # Do not return an alias into the provider's canonical checkpoint.
            return normalize_record(SecurityIdentity, asdict(result))
        except ContractError as error:
            raise PITError(error.code.value, error.field) from None
