"""5D-2/3 synthetic versioned macro and tax evidence, with optional PR #8 factories.

No network, ledger, tax decision or calendar gate is implemented here. Inject the
existing PR #8 dataclasses when integrating; the sibling PR is not cherry-picked.
"""

from __future__ import annotations

import copy
import hashlib
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, replace
from datetime import date, datetime
from types import MappingProxyType
from typing import Any

from .contracts import ContractError, SourceProvenance
from .contracts.errors import day, text, timestamp, version
from .normalize import normalize_record
from .pit import (
    MAX_DOCUMENT_BYTES,
    MAX_REVISIONS,
    PITError,
    _canonical,
    _coverage_at,
    _coverage_component,
    _proof_digest,
    _reject,
    evidence_payload,
)

SUPPLY_VERSION = "research_pit_supply_synthetic_v1"
MACRO_TYPES = frozenset({"CPI", "PPI", "NFP", "PCE", "FOMC_STATEMENT", "FOMC_PRESS", "ISM"})
COLLECTIONS = ("macro_events", "macro_coverage", "tax_sources", "tax_rules", "qualifications")


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceRevision:
    evidence_id: str
    version: int
    revision_of: int | None
    historically_known_at: datetime | None
    published_at: datetime | None  # Publication of this source/schedule revision.
    provider_available_at: datetime | None
    source: SourceProvenance
    cancelled: bool
    document: bytes
    document_sha256: str
    schema_version: str = SUPPLY_VERSION


@dataclass(frozen=True, slots=True, kw_only=True)
class MacroScheduleRevision(EvidenceRevision):
    event_type: str
    scheduled_at: datetime
    released_at: datetime | None = None  # Economic release; not schedule publication.
    missing: bool = False

    @property
    def scheduled_known_at(self) -> datetime | None:
        return self.historically_known_at


@dataclass(frozen=True, slots=True, kw_only=True)
class MacroCoverageRevision(EvidenceRevision):
    covered_from: datetime
    covered_until: datetime
    event_types: frozenset[str]
    point_in_time: bool
    missing: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class TaxSourceRevision(EvidenceRevision):
    security_id: str
    issuer_id: str
    event_id: str
    source_country: str
    effective_from: date
    effective_to: date
    distribution_type: str = "ordinary_stock"
    currency: str = "USD"


@dataclass(frozen=True, slots=True, kw_only=True)
class TaxRuleRevision(EvidenceRevision):
    source_country: str
    rate: float
    effective_from: date
    effective_to: date
    verified_at: datetime
    distribution_type: str = "ordinary_stock"


@dataclass(frozen=True, slots=True, kw_only=True)
class QualificationRevision(EvidenceRevision):
    account_scenario_id: str
    tax_residence: str
    beneficial_owner: bool
    treaty_documents_valid: bool
    individual: bool
    non_us_ca_tax_resident: bool
    effective_from: date
    effective_to: date


TYPES = (
    MacroScheduleRevision,
    MacroCoverageRevision,
    TaxSourceRevision,
    TaxRuleRevision,
    QualificationRevision,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class PITSupplyDataset:
    macro_events: tuple[MacroScheduleRevision, ...] = ()
    macro_coverage: tuple[MacroCoverageRevision, ...] = ()
    tax_sources: tuple[TaxSourceRevision, ...] = ()
    tax_rules: tuple[TaxRuleRevision, ...] = ()
    qualifications: tuple[QualificationRevision, ...] = ()
    input_kind: str
    expected_sha256: str
    schema_version: str = SUPPLY_VERSION


def supply_input_sha256(dataset: PITSupplyDataset) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "schema_version": dataset.schema_version,
                "input_kind": dataset.input_kind,
                **{
                    name: sorted(_canonical(v).decode() for v in getattr(dataset, name))
                    for name in COLLECTIONS
                },
            }
        )
    ).hexdigest()


def _known(value: EvidenceRevision) -> datetime:
    if value.historically_known_at is None or value.published_at is None:
        _reject("PIT_KNOWLEDGE_UNKNOWN", "historically_known_at")
    return max(
        value.historically_known_at,
        value.published_at,
        value.provider_available_at or value.published_at,
    )


def _validate(
    value: MacroScheduleRevision
    | MacroCoverageRevision
    | TaxSourceRevision
    | TaxRuleRevision
    | QualificationRevision,
) -> None:
    if value.schema_version != SUPPLY_VERSION:
        _reject("UNSUPPORTED_PIT_SCHEMA", "schema_version")
    text(value.evidence_id, "evidence_id")
    version(value.version, "version")
    if value.revision_of is not None:
        version(value.revision_of, "revision_of")
    if value.version > MAX_REVISIONS:
        _reject("PIT_VERSION_BOUNDS", "version")
    if (value.version == 1 and value.revision_of is not None) or (
        value.version > 1 and value.revision_of != value.version - 1
    ):
        _reject("PIT_REVISION_LINK", "revision_of")
    if type(value.source) is not SourceProvenance:
        _reject("INVALID_TYPE", "source")
    normalize_record(
        SourceProvenance, {f.name: getattr(value.source, f.name) for f in fields(value.source)}
    )
    for name in ("historically_known_at", "published_at", "provider_available_at"):
        clock = getattr(value, name)
        if clock is not None and timestamp(clock, name) > value.source.ingested_at:
            _reject("PIT_TIME_ORDER", name)
    if (
        value.published_at is not None
        and value.historically_known_at is not None
        and value.published_at > value.historically_known_at
    ):
        _reject("PIT_TIME_ORDER", "historically_known_at")
    if (
        value.provider_available_at is not None
        and value.published_at is not None
        and value.provider_available_at < value.published_at
    ):
        _reject("PIT_TIME_ORDER", "provider_available_at")
    if type(value.cancelled) is not bool:
        _reject("INVALID_TYPE", "cancelled")
    if isinstance(value, MacroScheduleRevision):
        if type(value.event_type) is not str or value.event_type not in MACRO_TYPES:
            _reject("PIT_MACRO_TYPE", "event_type")
        timestamp(value.scheduled_at, "scheduled_at")
        if value.released_at is not None and (
            timestamp(value.released_at, "released_at") > value.source.ingested_at
        ):
            _reject("PIT_TIME_ORDER", "released_at")
        if value.released_at is not None and value.released_at > _known(value):
            _reject("PIT_RELEASE_KNOWLEDGE", "released_at")
        if type(value.missing) is not bool:
            _reject("INVALID_TYPE", "missing")
    elif isinstance(value, MacroCoverageRevision):
        timestamp(value.covered_from, "covered_from")
        timestamp(value.covered_until, "covered_until")
        if value.covered_from >= value.covered_until:
            _reject("PIT_COVERAGE_RANGE", "covered_until")
        if (
            type(value.event_types) is not frozenset
            or not value.event_types
            or not value.event_types <= MACRO_TYPES
        ):
            _reject("PIT_COVERAGE_KINDS", "event_types")
        if type(value.missing) is not bool or type(value.point_in_time) is not bool:
            _reject("INVALID_TYPE", "coverage")
    else:
        day(value.effective_from, "effective_from")
        day(value.effective_to, "effective_to")
        if value.effective_from >= value.effective_to:
            _reject("PIT_EFFECTIVE_RANGE", "effective_to")
        if isinstance(value, QualificationRevision):
            text(value.account_scenario_id, "account_scenario_id")
            text(value.tax_residence, "tax_residence")
            for name in (
                "beneficial_owner",
                "treaty_documents_valid",
                "individual",
                "non_us_ca_tax_resident",
            ):
                if type(getattr(value, name)) is not bool:
                    _reject("INVALID_TYPE", name)
        else:
            if type(value.source_country) is not str or value.source_country not in {"US", "CA"}:
                _reject("PIT_TAX_COUNTRY", "source_country")
            if value.distribution_type != "ordinary_stock":
                _reject("PIT_TAX_CLASSIFICATION", "distribution_type")
            if isinstance(value, TaxSourceRevision):
                for name in ("security_id", "issuer_id", "event_id"):
                    text(getattr(value, name), name)
                if value.currency != "USD":
                    _reject("PIT_TAX_CURRENCY", "currency")
            else:
                if (
                    type(value.rate) not in (int, float)
                    or not math.isfinite(value.rate)
                    or value.rate != {"US": 0.10, "CA": 0.15}[value.source_country]
                ):
                    _reject("PIT_TAX_SCENARIO_RATE", "rate")
                stamp = timestamp(value.verified_at, "verified_at")
                if stamp > value.source.ingested_at or stamp < _known(value):
                    _reject("PIT_TIME_ORDER", "verified_at")

    if type(value.document) is not bytes or not 0 < len(value.document) <= MAX_DOCUMENT_BYTES:
        _reject("PIT_DOCUMENT_BOUNDS", "document")
    if hashlib.sha256(value.document).hexdigest() != value.document_sha256:
        _reject("PIT_DOCUMENT_HASH", "document_sha256")
    if value.document != evidence_payload(value):
        _reject("PIT_DOCUMENT_TERMS", "document")


def _select(values: tuple[EvidenceRevision, ...], asof: datetime) -> tuple[EvidenceRevision, ...]:
    groups: dict[str, dict[int, EvidenceRevision]] = {}
    for value in values:
        if _known(value) > asof:
            continue
        group = groups.setdefault(value.evidence_id, {})
        old = group.get(value.version)
        if old is not None and old != value:
            _reject("PIT_REVISION_CONFLICT", "version")
        group[value.version] = value
    result = []
    for key in sorted(groups):
        group = groups[key]
        versions = sorted(group)
        if versions != list(range(1, versions[-1] + 1)):
            _reject("PIT_REVISION_GAP", "version")
        for before, after in zip(versions, versions[1:]):
            first, second = group[before], group[after]
            if _known(first) >= _known(second):
                _reject("PIT_REVISION_CLOCK", "published_at")
            if (
                isinstance(first, MacroScheduleRevision)
                and isinstance(second, MacroScheduleRevision)
                and first.event_type != second.event_type
            ):
                _reject("PIT_REVISION_KIND", "event_type")
        result.append(group[versions[-1]])
    return tuple(result)


@dataclass(frozen=True, slots=True)
class MacroSupplySnapshot:
    coverage: MacroCoverageRevision
    events: tuple[MacroScheduleRevision, ...]
    asof: datetime
    input_sha256: str
    coverage_proofs: tuple[MacroCoverageRevision, ...]
    event_coverage_proofs: tuple[MacroCoverageRevision, ...]

    def audit_dict(self) -> dict:
        return _audit(
            self.input_sha256,
            self.asof,
            (*self.coverage_proofs, *self.event_coverage_proofs, *self.events),
            len(self.events),
        )


def _complete_macro_coverage(proof: MacroCoverageRevision) -> bool:
    return (
        not proof.cancelled
        and not proof.missing
        and proof.point_in_time
        and proof.event_types == MACRO_TYPES
    )


def _audit(digest: str, asof: datetime, values: tuple, count: int) -> dict:
    return {
        "status": "synthetic_pit_contract_pass",
        "provider_version": SUPPLY_VERSION,
        "asof": asof.isoformat(),
        "input_sha256": digest,
        "record_count": count,
        "source_hashes": tuple(sorted({v.document_sha256 for v in values})),
        "raw_verified": False,
        "PIT_verified": False,
        "archive_ready": False,
        "trusted_entry": False,
    }


@dataclass(frozen=True, slots=True)
class TaxSupplySnapshot:
    source: TaxSourceRevision
    rule: TaxRuleRevision
    qualification: QualificationRevision
    asof: datetime
    effective_date: date
    input_sha256: str

    def audit_dict(self) -> dict:
        return _audit(self.input_sha256, self.asof, (self.source, self.rule, self.qualification), 3)

    def adapt(
        self,
        action: Any,
        *,
        rule_factory: Callable[..., Any],
        qualification_factory: Callable[..., Any],
    ) -> tuple[Any, Any, Any]:
        """Return PR #8 inputs, preserving independent account withholding/credit terms."""
        if (
            action.security_id != self.source.security_id
            or action.event_id != self.source.event_id
            or action.kind != "CASH_DIVIDEND"
            or action.effective_date != self.effective_date
            or action.currency != "USD"
            or action.terms != "ordinary"
            or action.cancelled
            or action.available_at > self.asof
            or getattr(action, "distribution_type", None) not in {None, "ordinary_stock"}
            or getattr(action, "tax_source_country", None) not in {None, self.source.source_country}
        ):
            _reject("PIT_TAX_EVENT_BINDING", "event_id")
        try:
            event = replace(
                action,
                tax_source_country=self.source.source_country,
                distribution_type=self.source.distribution_type,
                tax_source=f"synthetic:{self.source.document_sha256}",
                tax_source_known_at=_known(self.source),
            )
        except TypeError:
            _reject("PIT_PR8_INTERFACE_REQUIRED", "action")
        rule = rule_factory(
            source_country=self.rule.source_country,
            rate=self.rule.rate,
            effective_from=self.rule.effective_from,
            effective_to=self.rule.effective_to,
            known_at=_known(self.rule),
            verified_at=self.rule.verified_at,
            source=f"synthetic:{self.rule.document_sha256}",
            version=str(self.rule.version),
            distribution_type="ordinary_stock",
        )
        q = self.qualification
        qualification = qualification_factory(
            tax_residence=q.tax_residence,
            beneficial_owner=q.beneficial_owner,
            treaty_documents_valid=q.treaty_documents_valid,
            effective_from=q.effective_from,
            effective_to=q.effective_to,
            known_at=_known(q),
            source=f"synthetic:{q.document_sha256}",
            individual=q.individual,
            non_us_ca_tax_resident=q.non_us_ca_tax_resident,
        )
        return event, rule, qualification


@dataclass(frozen=True, slots=True, init=False, repr=False, eq=False)
class SyntheticPITSupply:
    _data: PITSupplyDataset
    _records: Mapping[str, Mapping[str, tuple[EvidenceRevision, ...]]]
    input_sha256: str

    def __init__(self, dataset: PITSupplyDataset):
        try:
            if type(dataset) is not PITSupplyDataset:
                _reject("INVALID_TYPE", "dataset")
            if dataset.schema_version != SUPPLY_VERSION:
                _reject("UNSUPPORTED_PIT_SCHEMA", "schema_version")
            if dataset.input_kind != "synthetic":
                _reject("REAL_DATA_DEFERRED", "input_kind")
            indexes = {}
            data = copy.deepcopy(dataset)
            for name, cls in zip(COLLECTIONS, TYPES, strict=True):
                values = getattr(data, name)
                if (
                    type(values) is not tuple
                    or len(values) > MAX_REVISIONS
                    or any(type(v) is not cls for v in values)
                ):
                    _reject("PIT_INPUT_BOUNDS_OR_TYPE", name)
                groups: dict[str, list[EvidenceRevision]] = {}
                owners: dict[str, tuple[str, ...]] = {}
                for value in values:
                    _validate(value)
                    owner = (
                        value.security_id
                        if isinstance(value, TaxSourceRevision)
                        else value.source_country
                        if isinstance(value, TaxRuleRevision)
                        else value.account_scenario_id
                        if isinstance(value, QualificationRevision)
                        else "macro"
                    )
                    owner_terms = (
                        (owner, value.issuer_id, value.event_id)
                        if isinstance(value, TaxSourceRevision)
                        else (owner,)
                    )
                    if owners.setdefault(value.evidence_id, owner_terms) != owner_terms:
                        _reject("PIT_EVIDENCE_OWNER", "evidence_id")
                    groups.setdefault(owner, []).append(value)
                indexes[name] = MappingProxyType({k: tuple(v) for k, v in groups.items()})
            digest = supply_input_sha256(data)
            if digest != data.expected_sha256:
                _reject("PIT_INPUT_HASH", "expected_sha256")
            object.__setattr__(self, "_data", data)
            object.__setattr__(self, "_records", MappingProxyType(indexes))
            object.__setattr__(self, "input_sha256", digest)
        except ContractError as error:
            raise PITError(error.code.value, error.field) from None

    def __reduce__(self):
        return type(self), (self._data,)

    def _visible(self, name: str, owner: str, asof: datetime) -> tuple:
        return _select(self._records[name].get(owner, ()), asof)

    def macro_snapshot(self, asof: datetime) -> MacroSupplySnapshot:
        asof = _query(asof)
        candidates = self._visible("macro_coverage", "macro", asof)
        # Select latest BEFORE range/missing/cancellation, never revive an old proof.
        proof = _coverage_at(candidates, asof, inclusive=False, complete=_complete_macro_coverage)
        proofs = _coverage_component(
            candidates, asof, inclusive=False, complete=_complete_macro_coverage
        )
        events = self._visible("macro_events", "macro", asof)
        event_proofs = []
        for event in events:
            if event.cancelled:
                continue  # Preserve tombstones without reviving their old schedule.
            if event.missing:
                _reject("PIT_MACRO_EVENT_COVERAGE", "scheduled_at")
            event_proofs.append(
                _coverage_at(
                    candidates,
                    event.scheduled_at,
                    inclusive=False,
                    complete=_complete_macro_coverage,
                    outside_code="PIT_MACRO_EVENT_COVERAGE",
                )
            )
        return MacroSupplySnapshot(
            proof, events, asof, self.input_sha256, proofs, tuple(event_proofs)
        )

    def macro_adapter(
        self, *, event_factory: Callable[..., Any], coverage_factory: Callable[..., Any]
    ) -> MacroSupplyAdapter:
        return MacroSupplyAdapter(self, event_factory, coverage_factory)

    def tax_snapshot(
        self,
        *,
        security_id: str,
        issuer_id: str,
        event_id: str,
        account_scenario_id: str,
        effective_date: date,
        asof: datetime,
    ) -> TaxSupplySnapshot:
        asof = _query(asof)
        try:
            day(effective_date, "effective_date")
            for name, value in (
                ("security_id", security_id),
                ("issuer_id", issuer_id),
                ("event_id", event_id),
                ("account_scenario_id", account_scenario_id),
            ):
                text(value, name)
        except ContractError as error:
            raise PITError(error.code.value, error.field) from None
        sources = [
            v
            for v in self._visible("tax_sources", security_id, asof)
            if v.event_id == event_id and v.issuer_id == issuer_id
        ]
        source = _one(sources, effective_date, "tax_source")
        rules = self._visible("tax_rules", source.source_country, asof)
        rule = _one(rules, effective_date, "tax_rule")
        if rule.verified_at > asof:
            _reject("PIT_TAX_RULE_UNVERIFIED", "verified_at")
        q = _one(
            self._visible("qualifications", account_scenario_id, asof),
            effective_date,
            "qualification",
        )
        if (
            q.tax_residence != "CN-mainland"
            or not q.beneficial_owner
            or not q.treaty_documents_valid
            or not q.individual
            or not q.non_us_ca_tax_resident
        ):
            _reject("PIT_TAX_QUALIFICATION", "qualification")
        return TaxSupplySnapshot(source, rule, q, asof, effective_date, self.input_sha256)


def _query(asof: datetime) -> datetime:
    try:
        return timestamp(asof, "asof")
    except ContractError as error:
        raise PITError(error.code.value, error.field) from None


def _one(values: tuple | list, effective_date: date, field: str) -> Any:
    active = [v for v in values if v.effective_from <= effective_date < v.effective_to]
    if len(active) != 1:
        _reject("PIT_TAX_UNKNOWN" if not active else "PIT_TAX_CONFLICT", field)
    if active[0].cancelled:
        _reject("PIT_TAX_UNKNOWN", field)
    return active[0]


@dataclass(frozen=True, slots=True)
class MacroSupplyAdapter:
    provider: SyntheticPITSupply
    event_factory: Callable[..., Any]
    coverage_factory: Callable[..., Any]

    def snapshot(self, asof: datetime) -> tuple[Any, tuple[Any, ...]]:
        snapshot = self.provider.macro_snapshot(asof)
        proofs = snapshot.coverage_proofs
        coverage = self.coverage_factory(
            available_at=max(_known(c) for c in proofs),
            covered_from=proofs[0].covered_from,
            covered_until=proofs[-1].covered_until,
            source=f"synthetic:{_proof_digest(proofs)}",
            event_types=MACRO_TYPES,
            point_in_time=True,
            missing=False,
            version="/".join(str(c.version) for c in proofs),
        )
        events = tuple(
            self.event_factory(
                event_id=v.evidence_id,
                event_type=v.event_type,
                scheduled_at=v.scheduled_at,
                scheduled_known_at=_known(v),
                source=f"synthetic:{v.document_sha256}",
                revision=v.version,
                cancelled=v.cancelled,
                missing=v.missing,
                published_at=v.released_at
                if v.released_at is not None and v.released_at <= asof
                else None,
            )
            for v in snapshot.events
        )
        return coverage, events
