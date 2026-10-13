"""5E-1 bounded byte/metadata contracts, without domain validation or trust issuance.

Frozen hashes are integrity bindings, not authenticated authority. Real admission,
validator orchestration, filesystem handles and engine construction remain blocked.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, fields, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Never, cast

from .contracts import ContractError, PriceBasis, ShareUnit, VolumeUnit
from .contracts.errors import text, timestamp, version
from .pit import _canonical
from .raw_validation import VolumePolicy

BUNDLE_SCHEMA = "research_verified_bundle_v1"
CONTRACT_VERSION = "research_verification_contracts_v1"
MAX_SOURCES = 128
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_BINDINGS = 128
MAX_SOURCE_VERSION = 256


class VerificationContractError(ValueError):
    def __init__(self, code: str, field: str, phase: str = "contract"):
        self.code, self.field, self.phase = code, field, phase
        super().__init__(f"{code}:{field}:{phase}")

    def as_dict(self) -> dict[str, str]:
        return {
            "schema_version": BUNDLE_SCHEMA,
            "code": self.code,
            "field": self.field,
            "phase": self.phase,
        }


def _deny(code: str, field: str, phase: str = "contract") -> Never:
    raise VerificationContractError(code, field, phase) from None


def _schema(value: object) -> None:
    if type(value) is not str or value != BUNDLE_SCHEMA:
        _deny("UNSUPPORTED_BUNDLE_SCHEMA", "schema_version")


def _hash(value: object, field: str) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        _deny("INVALID_DIGEST", field)


def _text(value: object, field: str) -> None:
    text(value, field)
    if len(cast(str, value)) > (2048 if field == "source_ref" else 256):
        _deny("METADATA_BOUNDS", field)


def _reconstruct(value: Any, cls: type, field: str) -> Any:
    if type(value) is not cls:
        _deny("INVALID_TYPE", field)
    try:
        return replace(value)
    except (AttributeError, TypeError):
        _deny("INVALID_OBJECT_STATE", field)


def _strings(value: object, field: str) -> tuple[str, ...]:
    if type(value) is not tuple or any(type(v) is not str for v in value):
        _deny("INVALID_TYPE", field)
    if len(value) > MAX_BINDINGS:
        _deny("METADATA_BOUNDS", field)
    try:
        for item in value:
            _text(item, field)
    except ContractError as error:
        _deny(error.code.value, field)
    if len(set(value)) != len(value):
        _deny("DUPLICATE_BINDING", field)
    return tuple(sorted(value))


class EvidenceRole(StrEnum):
    RAW = "raw"
    COMPARISON = "comparison"
    ACTIONS = "actions"
    IDENTITIES = "identities"
    FACTS = "facts"
    DEFINITION = "definition"
    PIT_EVENTS = "pit_events"
    PIT_IDENTITIES = "pit_identities"
    PIT_COVERAGE = "pit_coverage"
    MACRO_EVENTS = "macro_events"
    MACRO_COVERAGE = "macro_coverage"
    TAX_SOURCES = "tax_sources"
    TAX_RULES = "tax_rules"
    TAX_QUALIFICATIONS = "tax_qualifications"
    ARCHIVE_MANIFEST = "archive_manifest"
    L0_RESPONSE = "l0_response"
    ACQUISITION_RECEIPT = "acquisition_receipt"
    L1_PARTITION = "l1_partition"
    LICENSE = "license"


BASE_ROLES = frozenset(
    {
        EvidenceRole.RAW,
        EvidenceRole.COMPARISON,
        EvidenceRole.ACTIONS,
        EvidenceRole.IDENTITIES,
        EvidenceRole.FACTS,
        EvidenceRole.DEFINITION,
        EvidenceRole.PIT_EVENTS,
        EvidenceRole.PIT_IDENTITIES,
        EvidenceRole.PIT_COVERAGE,
    }
)
MACRO_ROLES = frozenset({EvidenceRole.MACRO_EVENTS, EvidenceRole.MACRO_COVERAGE})
TAX_ROLES = frozenset(
    {EvidenceRole.TAX_SOURCES, EvidenceRole.TAX_RULES, EvidenceRole.TAX_QUALIFICATIONS}
)
ARCHIVE_ROLES = frozenset(
    {
        EvidenceRole.ARCHIVE_MANIFEST,
        EvidenceRole.L0_RESPONSE,
        EvidenceRole.ACQUISITION_RECEIPT,
        EvidenceRole.L1_PARTITION,
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ResearchRequest:
    input_kind: str
    security_id: str
    symbol: str
    venue: str
    currency: str
    frequency: str
    window_start: datetime
    window_end: datetime
    asof: datetime
    raw_product_id: str
    signal_product_id: str
    signal_basis: PriceBasis
    signal_share_unit: ShareUnit
    signal_volume_unit: VolumeUnit
    adjustment_version: str | None
    volume_policy: VolumePolicy
    strategy_config_sha256: str
    macro_enabled: bool
    dividend_tax_enabled: bool
    archive_present: bool
    execution_basis: PriceBasis = PriceBasis.RAW
    valuation_basis: PriceBasis = PriceBasis.RAW
    risk_basis: PriceBasis = PriceBasis.RAW
    execution_share_unit: ShareUnit = ShareUnit.AS_TRADED
    execution_volume_unit: VolumeUnit = VolumeUnit.AS_TRADED_SHARES
    timezone: str = "America/New_York"
    session_filter: str = "rth"
    schema_version: str = BUNDLE_SCHEMA

    def __post_init__(self):
        _schema(self.schema_version)
        try:
            for name in ("security_id", "symbol", "venue", "raw_product_id", "signal_product_id"):
                _text(getattr(self, name), name)
            for name in ("window_start", "window_end", "asof"):
                object.__setattr__(self, name, timestamp(getattr(self, name), name))
        except ContractError as error:
            _deny(error.code.value, error.field)
        if type(self.input_kind) is not str or self.input_kind not in {"synthetic", "real"}:
            _deny("INVALID_INPUT_KIND", "input_kind")
        if (
            self.currency != "USD"
            or type(self.frequency) is not str
            or self.frequency not in {"1m", "1d"}
        ):
            _deny("UNSUPPORTED_SCOPE", "currency_frequency")
        if self.timezone != "America/New_York" or self.session_filter != "rth":
            _deny("UNSUPPORTED_SESSION", "session_filter")
        if self.window_start >= self.window_end:
            _deny("INVALID_INTERVAL", "window")
        if self.raw_product_id == self.signal_product_id:
            _deny("PRODUCT_COLLISION", "product_id")
        if type(self.signal_basis) is not PriceBasis:
            _deny("UNIT_MISMATCH", "signal_basis")
        if self.signal_basis is PriceBasis.TOTAL_RETURN:
            _deny("UNIT_MISMATCH", "signal_basis")
        if type(self.signal_volume_unit) is not VolumeUnit:
            _deny("UNIT_MISMATCH", "signal_volume_unit")
        if (
            type(self.volume_policy) is not VolumePolicy
            or self.volume_policy is VolumePolicy.UNKNOWN
        ):
            _deny("VOLUME_POLICY_UNKNOWN", "volume_policy")
        if self.signal_basis is PriceBasis.RAW:
            if (
                self.signal_share_unit is not ShareUnit.AS_TRADED
                or self.signal_volume_unit is not VolumeUnit.AS_TRADED_SHARES
                or self.adjustment_version is not None
                or self.volume_policy is not VolumePolicy.AS_TRADED
            ):
                _deny("UNIT_MISMATCH", "raw_signal")
        elif (
            self.signal_share_unit is not ShareUnit.SPLIT_ADJUSTED
            or self.adjustment_version is None
        ):
            _deny("UNIT_MISMATCH", "adjusted_signal")
        if self.signal_basis is PriceBasis.SPLIT_ADJUSTED:
            expected_volume_unit = (
                VolumeUnit.SPLIT_ADJUSTED_SHARES
                if self.volume_policy is VolumePolicy.MULTIPLY_SPLIT_FACTOR
                else VolumeUnit.AS_TRADED_SHARES
            )
            if self.signal_volume_unit is not expected_volume_unit:
                _deny("VOLUME_UNIT_CONFLICT", "signal_volume_unit")
        if self.adjustment_version is not None:
            try:
                _text(self.adjustment_version, "adjustment_version")
            except ContractError as error:
                _deny(error.code.value, error.field)
        if (
            any(
                getattr(self, n) is not PriceBasis.RAW
                for n in ("execution_basis", "valuation_basis", "risk_basis")
            )
            or self.execution_share_unit is not ShareUnit.AS_TRADED
            or self.execution_volume_unit is not VolumeUnit.AS_TRADED_SHARES
        ):
            _deny("UNIT_MISMATCH", "execution_valuation_risk")
        for name in ("macro_enabled", "dividend_tax_enabled", "archive_present"):
            if type(getattr(self, name)) is not bool:
                _deny("INVALID_TYPE", name)
        _hash(self.strategy_config_sha256, "strategy_config_sha256")

    @classmethod
    def from_dict(cls, value: Any) -> ResearchRequest:
        """Strict declaration decoder, never a certificate/checkpoint decoder."""
        if type(value) is not dict:
            _deny("INVALID_TYPE", "request")
        names = {f.name for f in fields(cls)}
        if set(value) - names:
            _deny("UNKNOWN_FIELD", "request")
        # Wire requests explicitly carry even defaulted schema/policy fields.
        if names - set(value):
            _deny("MISSING_FIELD", "request")
        values = dict(value)
        try:
            for name in ("window_start", "window_end", "asof"):
                values[name] = datetime.fromisoformat(values[name])
            for name in ("signal_basis", "execution_basis", "valuation_basis", "risk_basis"):
                values[name] = PriceBasis(values[name])
            values["execution_share_unit"] = ShareUnit(values["execution_share_unit"])
            values["execution_volume_unit"] = VolumeUnit(values["execution_volume_unit"])
            values["signal_share_unit"] = ShareUnit(values["signal_share_unit"])
            values["signal_volume_unit"] = VolumeUnit(values["signal_volume_unit"])
            values["volume_policy"] = VolumePolicy(values["volume_policy"])
        except (TypeError, ValueError):
            _deny("INVALID_WIRE_VALUE", "request")
        return cls(**values)

    def required_roles(self) -> frozenset[EvidenceRole]:
        return (
            BASE_ROLES
            | (MACRO_ROLES if self.macro_enabled else frozenset())
            | (TAX_ROLES if self.dividend_tax_enabled else frozenset())
            | (ARCHIVE_ROLES if self.archive_present else frozenset())
            | (
                {EvidenceRole.LICENSE}
                if self.input_kind == "real" or self.archive_present
                else frozenset()
            )
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceClocks:
    effective_at: datetime | None
    historically_known_at: datetime | None
    published_at: datetime | None
    provider_available_at: datetime | None
    ingested_at: datetime

    def __post_init__(self):
        try:
            object.__setattr__(self, "ingested_at", timestamp(self.ingested_at, "ingested_at"))
            for name in (
                "effective_at",
                "historically_known_at",
                "published_at",
                "provider_available_at",
            ):
                value = getattr(self, name)
                if value is not None:
                    object.__setattr__(self, name, timestamp(value, name))
            clocks = (self.historically_known_at, self.published_at, self.provider_available_at)
            if any(c is not None and c > self.ingested_at for c in clocks):
                _deny("TIME_ORDER", "ingested_at")
            if (
                self.published_at is not None
                and self.historically_known_at is not None
                and self.published_at > self.historically_known_at
            ):
                _deny("TIME_ORDER", "historically_known_at")
            if (
                self.provider_available_at is not None
                and self.published_at is not None
                and self.provider_available_at < self.published_at
            ):
                _deny("TIME_ORDER", "provider_available_at")
        except ContractError as error:
            _deny(error.code.value, error.field)


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelineVersions:
    acquisition: str
    adapter: str
    dataset: str
    normalizer: str
    raw_validator: str
    pit_provider: str
    pit_supply: str
    code_revision: str
    contract: str = CONTRACT_VERSION

    def __post_init__(self):
        try:
            for item in fields(self):
                _text(getattr(self, item.name), item.name)
        except ContractError as error:
            _deny(error.code.value, error.field)
        if self.contract != CONTRACT_VERSION:
            _deny("UNSUPPORTED_CONTRACT_VERSION", "contract")
        if re.fullmatch(r"[0-9a-f]{40}", self.code_revision) is None:
            _deny("INVALID_CODE_REVISION", "code_revision")


@dataclass(frozen=True, slots=True, kw_only=True)
class FrozenEvidence:
    source_id: str
    role: EvidenceRole
    security_id: str
    event_ids: tuple[str, ...]
    source_ref: str
    provider: str
    dataset_id: str
    dataset_revision: str
    partition_id: str
    version: int
    window_start: datetime
    window_end: datetime
    clocks: EvidenceClocks
    depends_on: tuple[str, ...]
    content: bytes
    sha256: str
    schema_version: str = BUNDLE_SCHEMA

    def __post_init__(self):
        _schema(self.schema_version)
        try:
            for name in (
                "source_id",
                "security_id",
                "source_ref",
                "provider",
                "dataset_id",
                "dataset_revision",
                "partition_id",
            ):
                _text(getattr(self, name), name)
            version(self.version, "version")
            if self.version > MAX_SOURCE_VERSION:
                _deny("VERSION_BOUNDS", "version")
            for name in ("window_start", "window_end"):
                object.__setattr__(self, name, timestamp(getattr(self, name), name))
        except ContractError as error:
            _deny(error.code.value, error.field)
        if type(self.role) is not EvidenceRole or type(self.clocks) is not EvidenceClocks:
            _deny("INVALID_TYPE", "role_clocks")
        object.__setattr__(self, "clocks", _reconstruct(self.clocks, EvidenceClocks, "clocks"))
        if self.window_start >= self.window_end:
            _deny("INVALID_INTERVAL", "evidence_window")
        for name in ("event_ids", "depends_on"):
            object.__setattr__(self, name, _strings(getattr(self, name), name))
        if type(self.content) is not bytes or not 0 < len(self.content) <= MAX_SOURCE_BYTES:
            _deny("CONTENT_BOUNDS_OR_TYPE", "content")
        _hash(self.sha256, "sha256")
        if hashlib.sha256(self.content).hexdigest() != self.sha256:
            _deny("CONTENT_HASH_MISMATCH", "sha256", "binding")


def _ordered_sources(sources: tuple[FrozenEvidence, ...]) -> tuple[FrozenEvidence, ...]:
    by_id = {v.source_id: v for v in sources}
    if len(by_id) != len(sources):
        _deny("DUPLICATE_SOURCE", "source_id", "binding")
    if any(not set(v.depends_on) <= set(by_id) for v in sources):
        _deny("MISSING_SOURCE_DEPENDENCY", "depends_on", "binding")
    result, done = [], set()
    while len(done) < len(sources):
        ready = sorted(k for k, v in by_id.items() if k not in done and set(v.depends_on) <= done)
        if not ready:
            _deny("SOURCE_GRAPH_CYCLE", "depends_on", "binding")
        result.extend(by_id[k] for k in ready)
        done.update(ready)
    return tuple(result)


def snapshot_root(
    request: ResearchRequest, versions: PipelineVersions, sources: tuple[FrozenEvidence, ...]
) -> str:
    """Compute a declaration root; caller-selected roots confer no source authority."""
    return hashlib.sha256(
        _canonical(
            {
                "schema_version": BUNDLE_SCHEMA,
                "request": request,
                "versions": versions,
                "sources": _ordered_sources(sources),
            }
        )
    ).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class FrozenDatasetInput:
    request: ResearchRequest
    versions: PipelineVersions
    sources: tuple[FrozenEvidence, ...]
    expected_root_sha256: str
    schema_version: str = BUNDLE_SCHEMA

    def __post_init__(self):
        _schema(self.schema_version)
        if type(self.request) is not ResearchRequest or type(self.versions) is not PipelineVersions:
            _deny("INVALID_TYPE", "request_versions")
        if (
            type(self.sources) is not tuple
            or not 0 < len(self.sources) <= MAX_SOURCES
            or any(type(v) is not FrozenEvidence for v in self.sources)
        ):
            _deny("SOURCE_BOUNDS_OR_TYPE", "sources")
        request = _reconstruct(self.request, ResearchRequest, "request")
        versions = _reconstruct(self.versions, PipelineVersions, "versions")
        sources = _ordered_sources(
            tuple(_reconstruct(v, FrozenEvidence, "sources") for v in self.sources)
        )
        if sum(len(v.content) for v in sources) > MAX_SNAPSHOT_BYTES:
            _deny("SNAPSHOT_BOUNDS", "sources")
        if any(v.security_id != request.security_id for v in sources):
            _deny("SOURCE_SECURITY_MISMATCH", "security_id", "binding")
        if not request.required_roles() <= {v.role for v in sources}:
            _deny("MISSING_EVIDENCE_ROLE", "sources", "binding")
        if not request.archive_present and any(v.role in ARCHIVE_ROLES for v in sources):
            _deny("ARCHIVE_POLICY_MISMATCH", "archive_present", "binding")
        _hash(self.expected_root_sha256, "expected_root_sha256")
        if snapshot_root(request, versions, sources) != self.expected_root_sha256:
            _deny("SOURCE_ROOT_MISMATCH", "expected_root_sha256", "binding")
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "versions", versions)
        object.__setattr__(self, "sources", sources)

    def assert_bound(self, reviewed_root_sha256: str) -> None:
        """Reconstruct before comparing an independently held root; no trust is granted."""
        _reconstruct(self, FrozenDatasetInput, "snapshot")
        _hash(reviewed_root_sha256, "reviewed_root_sha256")
        if self.expected_root_sha256 != reviewed_root_sha256:
            _deny("SOURCE_ROOT_MISMATCH", "reviewed_root_sha256", "binding")

    def __reduce__(self):
        return _restore_snapshot, (
            self.request,
            self.versions,
            self.sources,
            self.expected_root_sha256,
            self.schema_version,
        )


def _restore_snapshot(request, versions, sources, root, schema_version):
    return FrozenDatasetInput(
        request=request,
        versions=versions,
        sources=sources,
        expected_root_sha256=root,
        schema_version=schema_version,
    )


class VerifiedDatasetBundle:
    """Reserved opaque type. No constructor or issuer can grant real trust in 5E-1."""

    __slots__ = ()

    def __new__(cls, *_args, **_kwargs):
        _deny("TRUSTED_ENTRY_BLOCKED", "bundle", "issuance")

    def __reduce__(self):
        _deny("TRUSTED_ENTRY_BLOCKED", "bundle", "restore")
