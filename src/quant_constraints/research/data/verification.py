"""5E-2 synthetic verification orchestration. Reports are never engine tokens.

All domain objects are reconstructed from the same bounded byte snapshot. The
existing 5A/B/C/D implementations own normalization and their domain algorithms.
Real requests stop at authority; no VerifiedDatasetBundle, engine or ledger is
created. Python code execution is outside this integrity threat model.
"""

from __future__ import annotations

import hashlib
import importlib
from dataclasses import fields
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, cast

from quant_constraints.calendar import NY, SessionCalendar

from .archive_contracts import ACQUISITION_VERSION, ADAPTER_VERSION, ArchiveError
from .contracts import ActionKind, ContractError, PriceBasis
from .contracts.errors import text
from .datasets import DATASET_VERSION
from .normalize import (
    NORMALIZER_VERSION,
    normalize_identities,
    normalize_raw_bars,
    normalize_signal_bars,
)
from .pit import (
    PIT_VERSION,
    PITDataset,
    PITError,
    PITEventRevision,
    SnapshotState,
    SyntheticPITProvider,
    _canonical,
    _complete_action_coverage,
    _coverage_component,
    _latest,
    pit_input_sha256,
)
from .pit_supply import (
    SUPPLY_VERSION,
    PITSupplyDataset,
    SyntheticPITSupply,
    _complete_macro_coverage,
    _select,
    supply_input_sha256,
)
from .provenance import ResearchMode, VerificationStatus, contract_decision, data_provenance
from .raw_validation import VALIDATOR_VERSION, RawOHLCVValidator, ValidationInput, input_hashes
from .verification_archive import audit_bound_archive
from .verification_contracts import (
    ARCHIVE_ROLES,
    CONTRACT_VERSION,
    MACRO_ROLES,
    TAX_ROLES,
    EvidenceRole,
    FrozenDatasetInput,
    FrozenEvidence,
    ResearchRequest,
    VerificationContractError,
    _deny,
    _reconstruct,
)
from .verification_wire import RECORD_TYPES, _encode, decode_records, safe_field

VERIFICATION_VERSION = "research_verification_orchestration_synthetic_v6_research_isolation"
LEGACY_IMPLEMENTATION_BASE = "fb870fd70a5d3438c20adcdb3c606c04533719a0"
TAX_RUNTIME_SOURCE_REVISION = "9938de9c441e289b9eb5b5e5bf3814e2ff726ebb"
SECURITY_ENTRY_SOURCE_REVISION = "f38b3e8b75e1e555d3d66d8dad0b609371515e22"
RESEARCH_RUNTIME_SOURCE_REVISION = "0b2b716ed44e00f8a1027b6ef2ee3ca9257e60c6"
REVIEWED_IMPLEMENTATION_BASE = RESEARCH_RUNTIME_SOURCE_REVISION
# Source bytes of the accepted 5A/B/C/D implementations and their direct contracts.
# Wheel source files must match too; a declared git SHA alone is not evidence.
BASE_SOURCE_SHA256 = {
    "quant_constraints.calendar": "5b3facb1dda5a7d0b6346af45e1d11965825c6034df3c2597a607ed9a3fc650c",
    "quant_constraints.models": "482ed3d0568d849176dc9da156674c766bc384ff932e419fc87f320149afafa8",
    "quant_constraints.corporate_actions.events": "ea1cdddd7e599bf8e192e741c970bd623f226817d369c1036d82d18c2cb96ae4",
    "ml4t.backtest.calendar": "89a1b46f8ca65c646f334c8dbccc276413eeecceff65f8ffb088c7befd63723c",
    "ml4t.backtest.accounting.settlement": "3f41df5d741d0bcdf5da934ae40ddf9871670d25a667587fa4e674b9bea78059",
    "quant_constraints.research.data.archive": "04d71c0df2b39fb18fb8518fa1d43f036fdde582c44fb8932bf547da594109b1",
    "quant_constraints.research.data.archive_contracts": "d9c2984f6c1e9b8c9c6a1c6f688c5145bb6e206015fc42ffc548eddd3030a9bb",
    "quant_constraints.research.data.datasets": "35531fd8089b0d911746ca5720a1ed36c278e19e770c85a79a1a2dfc7958c3a2",
    "quant_constraints.research.data.massive": "75ec5afbd8cc7e59998c55625543869c500892e63da63aae1e3f5adc02dbe62f",
    "quant_constraints.research.data.pagination": "27a11e581e4931896845d7167168697271c64a7e246fb5ec62ae1b69fa268490",
    "quant_constraints.research.data.normalize": "878ea9c6de96f9940fd06794d307f96a92a5ed7dc65d2ae6fd85b96d45955504",
    "quant_constraints.research.data.raw_validation": "3e222335997756569970570359705804328537eee547ee50eb7ae58a6787bd5c",
    "quant_constraints.research.data.pit": "eb4c7dc5ab343c2959c4f783f6819730b351a9f7571e792748f3d1601bc4c838",
    "quant_constraints.research.data.pit_supply": "1a0fe079a9332668073b78711e5b69177047e6810d8a7d2206c51f502508c03c",
    "quant_constraints.research.data.verification_contracts": "f82a809aa398e6b8f43c537245ff4d8b38f2c224be519a33f52a0924a9f47016",
    "quant_constraints.research.data.provenance": "7910f8943e73811451ce007082fd5c2e868b0b49ca3330a9ac15d2a8e67f6eec",
    "quant_constraints.research.data.contracts.errors": "85a2c9bb2cab7144b47697f12ba963099541f3d1500bc934abad66d8ed054b0f",
    "quant_constraints.research.data.contracts.provenance": "3158bd53c0b77948e47532bea05af0b6dc19c58c3875b721db32b6ce1f217b87",
    "quant_constraints.research.data.contracts.market_data": "f6792e6d64bd340a67f0f9b90c3fd392295a69d004482a4791a5e4a01f0c6b00",
    "quant_constraints.research.data.contracts.identities": "44c16b70caa76c0282a786b8b6bf19921b460ea2ed5b12bfa609a3aa6b53787a",
    "quant_constraints.research.data.contracts.corporate_actions": "9c60d4d1406fbd3a009a0e4952bfe406629c942c27f3b6bf21368dd77828b074",
}
# Preserve the original evidence pins verbatim. The additive tax-runtime source
# commit is separately identified; old snapshots must not silently certify new code.
TAX_RUNTIME_SOURCE_SHA256 = {
    "quant_constraints.config": "85c6b0fa6712208c6e210defa5aa872a6527d9cc5a1f9645c7427895abae7e67",
    "quant_constraints.dividend_tax": "822e144f2e35c26f57d38234d55b55f28a47307d6afac004a25dc9d047ce3991",
    "quant_constraints.corporate_actions.events": "84f9a54dc6c6252bb4661d65773be03d920ce3aad1d43398a605391145c2ae42",
    "quant_constraints.corporate_actions.processor": "36cb7e98c52efb9629d98d0a73ef884a347fbbb21c22cb26dff9d3c3983d8e3e",
}
# Preserve the accepted tax/legacy provenance above. The owner entry policy is
# a new source overlay, never a rewrite of old archives or PIT evidence.
ENTRY_RUNTIME_SOURCE_SHA256 = {
    "quant_constraints.config": "4f88c236dd6c4dc542af493a3daa24ed934e7154c44b057e1b466b093e80513c",
    "quant_constraints.models": "9f949c47a8c93b7009c86af871dde32fe74d50b47fafede209a4de00648db068",
    "quant_constraints.controller": "0ac31cb3109606eee280b41a2c0fa2955fd74386ff56684544bab20ae71468b9",
    "quant_constraints.security_policy": "945e821e9cb1908648cc0b3190375b0e2dbeb3562059252d52cc567f62f50d33",
    "ml4t.backtest.strategies.sector_rotation.config": "b21a2973b2ac71b41cc217b179c15ac9144a058ca0567d2f3162a7e729f3eda7",
    "ml4t.backtest.strategies.sector_rotation.adapter": "86bd3c30a98a25b5b44e97d8f14e76b113ebb3fe709b8b836987c7394b41b50c",
}
# Separate additive source overlay. The default real rejection and all older
# evidence pins remain intact; v5 snapshots cannot silently approve this runtime.
RESEARCH_RUNTIME_SOURCE_SHA256 = {
    "ml4t.backtest.accounting.cash": "bfd5c0fe0d3b79ee782e2d1744fd8ac641056f3446e326a011eb36fd59ff588c",
    "quant_constraints.adapter": "f058753278e3ac807be125da6e08b7f34e8e415b13f842510aed49320b3f3f16",
    "quant_constraints.config": "4932b6442da22e2a2b029b280e3fd291a5328b2c2fd6b2529f323bfbd0bb61db",
    "quant_constraints.dividend_tax": "f8cc7a1e41226ec8486a534ed26e1dd9511307eeff7a73dd638aa96665ff1171",
    "quant_constraints.corporate_actions.processor": "12adc3d0b23060ac63ef4f12930294d7bba56946721edaf5f30abf2ab50a59d7",
    "ml4t.backtest.strategies.sector_rotation.adapter": "0ce40f90325053003945cb438d0d1ff8e11b0f935da09ff7ee0f2c5fead84ee3",
    "ml4t.backtest.strategies.sector_rotation.research": "1e5a571c48db7f60a7d49533d9fd48311644a28d0bdddcd2214b90b8f91942ed",
    "ml4t.backtest.strategies.sector_rotation.research_inputs": "8a8e173f289eb32fca4010cf2516b0d7e2bf9445159b71216c49578f5557c0c3",
    "ml4t.backtest.strategies.sector_rotation.research_ledger": "60501663ab43c562e3617d9ac2c080b93e9a14d474db94dee1c00a6801b8c308",
}
ROLE_DEPENDENCIES = {
    EvidenceRole.RAW: {EvidenceRole.IDENTITIES},
    EvidenceRole.COMPARISON: {
        EvidenceRole.RAW,
        EvidenceRole.ACTIONS,
        EvidenceRole.FACTS,
        EvidenceRole.DEFINITION,
        EvidenceRole.IDENTITIES,
    },
    EvidenceRole.DEFINITION: {EvidenceRole.ACTIONS, EvidenceRole.FACTS},
    EvidenceRole.PIT_EVENTS: {EvidenceRole.PIT_IDENTITIES, EvidenceRole.PIT_COVERAGE},
    EvidenceRole.MACRO_EVENTS: {EvidenceRole.MACRO_COVERAGE},
    EvidenceRole.TAX_SOURCES: {EvidenceRole.TAX_RULES, EvidenceRole.TAX_QUALIFICATIONS},
}


def _source_digest(name: str) -> str:
    module = importlib.import_module(name)
    path = module.__file__
    if path is None:
        _deny("IMPLEMENTATION_MISMATCH", "code_revision", "versions")
    # Git checks out Python source with CRLF on some Windows environments.
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _versions(snapshot: FrozenDatasetInput) -> dict:
    versions = {
        "acquisition": ACQUISITION_VERSION,
        "adapter": ADAPTER_VERSION,
        "dataset": DATASET_VERSION,
        "normalizer": NORMALIZER_VERSION,
        "raw_validator": VALIDATOR_VERSION,
        "pit_provider": PIT_VERSION,
        "pit_supply": SUPPLY_VERSION,
        "contract": CONTRACT_VERSION,
        "code_revision": REVIEWED_IMPLEMENTATION_BASE,
    }
    for name, actual in versions.items():
        if getattr(snapshot.versions, name) != actual:
            _deny("VERSION_MISMATCH", name, "versions")
    expected_sources = {
        **BASE_SOURCE_SHA256,
        **TAX_RUNTIME_SOURCE_SHA256,
        **ENTRY_RUNTIME_SOURCE_SHA256,
        **RESEARCH_RUNTIME_SOURCE_SHA256,
    }
    actual_sources = {name: _source_digest(name) for name in expected_sources}
    if actual_sources != expected_sources:
        _deny("IMPLEMENTATION_MISMATCH", "code_revision", "versions")
    return {
        "checked_versions": versions,
        "code_revision_scope": "reviewed_5A_B_C_D_5E1_plus_tax_security_and_isolated_research_runtime",
        "legacy_source_revision": LEGACY_IMPLEMENTATION_BASE,
        "legacy_source_sha256": BASE_SOURCE_SHA256,
        "tax_runtime_source_sha256": TAX_RUNTIME_SOURCE_SHA256,
        "tax_runtime_source_revision": TAX_RUNTIME_SOURCE_REVISION,
        "entry_runtime_source_sha256": ENTRY_RUNTIME_SOURCE_SHA256,
        "entry_runtime_source_revision": SECURITY_ENTRY_SOURCE_REVISION,
        "research_runtime_source_sha256": RESEARCH_RUNTIME_SOURCE_SHA256,
        "research_runtime_source_revision": RESEARCH_RUNTIME_SOURCE_REVISION,
        "reviewed_source_sha256": actual_sources,
        "orchestration_version": VERIFICATION_VERSION,
        "orchestration_source_sha256": {
            name: _source_digest("quant_constraints.research.data." + name)
            for name in ("verification", "verification_wire", "verification_archive")
        },
    }


def _record(value: Any) -> Any:
    return getattr(value, "record", value)


def _effective(value: Any) -> datetime | None:
    item = _record(value)
    for name in (
        "bar_start_at",
        "effective_from",
        "effective_date",
        "trading_date",
        "scheduled_at",
        "covered_from",
    ):
        stamp = getattr(item, name, None)
        if stamp is not None:
            return stamp if isinstance(stamp, datetime) else datetime.combine(stamp, time(), NY)
    return None


def _common(values: tuple, name: str) -> Any:
    found = tuple(getattr(v, name, None) for v in values)
    return max(found) if found and all(v is not None for v in found) else None


def _check_source(source: FrozenEvidence, records: tuple, request: ResearchRequest) -> None:
    if source.window_start != request.window_start or source.window_end != request.window_end:
        _deny("SOURCE_BINDING_MISMATCH", "window", "binding")
    owners = [_record(v) for v in records]
    if any(getattr(v, "security_id", request.security_id) != request.security_id for v in owners):
        _deny("SOURCE_OWNER_MISMATCH", "security_id", "binding")
    provenances = tuple(v.source for v in records if hasattr(v, "source"))
    for value in provenances:
        if any(
            getattr(source, outer) != getattr(value, inner)
            for outer, inner in (
                ("provider", "provider"),
                ("dataset_id", "dataset_id"),
                ("dataset_revision", "dataset_revision"),
                ("partition_id", "source_partition_id"),
                ("source_ref", "source_partition_ref"),
            )
        ):
            _deny("SOURCE_BINDING_MISMATCH", "source_metadata", "binding")
    for value in records:
        if source.role is EvidenceRole.FACTS and (
            source.provider != value.source_provider or source.source_ref != value.source_ref
        ):
            _deny("SOURCE_BINDING_MISMATCH", "source_metadata", "binding")
        if source.role is EvidenceRole.DEFINITION and (
            source.provider != value.provider
            or source.dataset_revision != value.dataset_revision
            or source.source_ref != value.definition_ref
        ):
            _deny("SOURCE_BINDING_MISMATCH", "source_metadata", "binding")
    ids = tuple(
        sorted(
            {
                getattr(v, "evidence_id", None)
                or getattr(v, "identity_id", None)
                or getattr(v, "coverage_id", None)
                or getattr(_record(v), "event_id", "")
                for v in records
            }
            - {""}
        )
    )
    revision = max(
        (
            getattr(
                v,
                "version",
                getattr(
                    _record(v),
                    "event_version",
                    getattr(getattr(v, "source", None), "record_version", 1),
                ),
            )
            for v in records
        ),
        default=1,
    )
    known_values = tuple(v if hasattr(v, "historically_known_at") else _record(v) for v in records)
    effective = tuple(_effective(v) for v in records)
    expected_effective = (
        effective[0] if effective and all(v == effective[0] for v in effective) else None
    )
    if (
        source.event_ids != ids
        or source.version != revision
        or source.clocks.effective_at != expected_effective
        or source.clocks.historically_known_at != _common(known_values, "historically_known_at")
        or source.clocks.published_at != _common(records, "published_at")
        or source.clocks.provider_available_at != _common(records, "provider_available_at")
        or (provenances and source.clocks.ingested_at != _common(provenances, "ingested_at"))
    ):
        _deny("SOURCE_BINDING_MISMATCH", "source_metadata", "binding")


def _decode(snapshot: FrozenDatasetInput) -> dict[EvidenceRole, tuple]:
    request = snapshot.request
    disabled = (MACRO_ROLES if not request.macro_enabled else frozenset()) | (
        TAX_ROLES if not request.dividend_tax_enabled else frozenset()
    )
    allowed = set(RECORD_TYPES) | (
        ARCHIVE_ROLES | {EvidenceRole.LICENSE} if request.archive_present else set()
    )
    grouped = dict.fromkeys(RECORD_TYPES, ())
    for source in snapshot.sources:
        if source.role in disabled or source.role not in allowed:
            _deny("FEATURE_POLICY_MISMATCH", "sources", "binding")
        for role in ROLE_DEPENDENCIES.get(source.role, set()):
            required = {s.source_id for s in snapshot.sources if s.role is role}
            if not required <= set(source.depends_on):
                _deny("SOURCE_DEPENDENCY_MISSING", "dependencies", "binding")
        if source.role in RECORD_TYPES:
            records = decode_records(source.role, source.content)
            _check_source(source, records, request)
            grouped[source.role] += records
    for role, records in grouped.items():
        if len(records) > (12_500 if role in {EvidenceRole.RAW, EvidenceRole.COMPARISON} else 256):
            _deny("SOURCE_BOUNDS", "sources", "binding")
    if len(grouped[EvidenceRole.DEFINITION]) != 1:
        _deny("FACTOR_EVIDENCE_MISSING", "definition", "binding")
    return grouped


def _normalize(records: dict, request: ResearchRequest) -> ValidationInput:
    if request.signal_basis is not PriceBasis.SPLIT_ADJUSTED:
        _deny("UNIT_MISMATCH", "signal_basis", "raw")
    identities = normalize_identities(_encode(v) for v in records[EvidenceRole.IDENTITIES])
    raw = normalize_raw_bars((_encode(v) for v in records[EvidenceRole.RAW]), identities)
    comparison = normalize_signal_bars(
        (_encode(v) for v in records[EvidenceRole.COMPARISON]), identities
    )
    definition = records[EvidenceRole.DEFINITION][0]
    if any(
        getattr(definition, left) != getattr(request, right)
        for left, right in (
            ("security_id", "security_id"),
            ("frequency", "frequency"),
            ("window_start", "window_start"),
            ("window_end", "window_end"),
            ("raw_product_id", "raw_product_id"),
            ("comparison_product_id", "signal_product_id"),
            ("adjustment_version", "adjustment_version"),
            ("volume_policy", "volume_policy"),
            ("input_kind", "input_kind"),
        )
    ):
        _deny("REQUEST_BINDING_MISMATCH", "definition", "normalize")
    for bar in (*raw, *comparison):
        if (
            any(
                getattr(bar, name) != getattr(request, name)
                for name in ("security_id", "venue", "currency")
            )
            or bar.available_at > request.asof
        ):
            _deny("REQUEST_BINDING_MISMATCH", "bars", "normalize")
    for bar in raw:
        if bar.product_id != request.raw_product_id:
            _deny("REQUEST_BINDING_MISMATCH", "raw_product_id", "normalize")
    for bar in comparison:
        if (
            bar.product_id != request.signal_product_id
            or bar.raw_product_id != request.raw_product_id
            or bar.price_basis is not request.signal_basis
            or bar.share_unit is not request.signal_share_unit
            or bar.volume_unit is not request.signal_volume_unit
            or bar.adjustment_version != request.adjustment_version
        ):
            _deny("REQUEST_BINDING_MISMATCH", "comparison_product_id", "normalize")
    identity = identities.resolve_effective(request.symbol, request.venue, request.window_start)
    if identity.security_id != request.security_id:
        _deny("IDENTITY_CONFLICT", "security_id", "normalize")
    value = ValidationInput(
        raw=raw,
        comparison=comparison,
        actions=records[EvidenceRole.ACTIONS],
        identities=identities,
        facts=records[EvidenceRole.FACTS],
        definition=definition,
        expected_hashes=(),
    )
    return ValidationInput(
        raw=value.raw,
        comparison=value.comparison,
        actions=value.actions,
        identities=value.identities,
        facts=value.facts,
        definition=definition,
        expected_hashes=input_hashes(value),
    )


def _raw(value: ValidationInput) -> dict:
    result = RawOHLCVValidator().validate(value)
    if result.status != "offline_algorithm_pass":
        _deny(result.code, result.field, "raw")
    if any(count != 0 for _, count in result.missing_rth_minutes):
        _deny("RAW_COVERAGE_GAP", "coverage", "raw")
    if value.definition.frequency == "1d":
        calendar, expected = SessionCalendar(), set()
        day = value.definition.window_start.astimezone(NY).date()
        last = (value.definition.window_end - timedelta(microseconds=1)).astimezone(NY).date()
        while day <= last:
            if calendar.session(day) is not None:
                expected.add(day)
            day += timedelta(days=1)
        if {bar.bar_start_at.astimezone(NY).date() for bar in value.raw} != expected:
            _deny("RAW_COVERAGE_GAP", "coverage", "raw")
    return result.as_dict()


def _terms(value: Any, excluded: frozenset[str] = frozenset()) -> bytes:
    return _canonical(
        {
            f.name: getattr(value, f.name)
            for f in fields(value)
            if f.name not in excluded | {"source", "schema_version", "knowledge_evidence"}
        }
    )


def _pit(records: dict, value: ValidationInput, request: ResearchRequest) -> tuple[dict, tuple]:
    dataset = PITDataset(
        events=records[EvidenceRole.PIT_EVENTS],
        identities=records[EvidenceRole.PIT_IDENTITIES],
        coverage=records[EvidenceRole.PIT_COVERAGE],
        input_kind="synthetic",
        expected_sha256="",
    )
    dataset = PITDataset(
        events=dataset.events,
        identities=dataset.identities,
        coverage=dataset.coverage,
        input_kind="synthetic",
        expected_sha256=pit_input_sha256(dataset),
    )
    provider = SyntheticPITProvider(dataset)
    inspected = provider.inspect(request.security_id, request.asof)
    if inspected.state in {SnapshotState.UNKNOWN, SnapshotState.CONFLICT}:
        _deny(inspected.code, inspected.field, "pit")
    selected = _latest(
        tuple(v for v in dataset.coverage if v.security_id == request.security_id), request.asof
    )
    start = value.definition.action_coverage_start.astimezone(NY).date()
    end = (value.definition.action_coverage_end - timedelta(microseconds=1)).astimezone(NY).date()
    proofs = _coverage_component(
        selected, start, inclusive=True, complete=_complete_action_coverage
    )
    if proofs[-1].covered_until < end:
        _deny("PIT_COVERAGE_UNKNOWN", "coverage", "pit")
    for bar in value.raw:
        identity = provider.resolve_asof(bar.symbol, bar.venue, bar.bar_start_at, request.asof)
        effective = value.identities.resolve_effective(bar.symbol, bar.venue, bar.bar_start_at)
        if _terms(identity) != _terms(effective) or (
            identity.effective_until is not None and identity.effective_until < bar.bar_end_at
        ):
            _deny("PIT_IDENTITY_BINDING", "identity", "pit")
    visible = _latest(
        tuple(v for v in dataset.events if v.record.security_id == request.security_id),
        request.asof,
    )
    calendar = SessionCalendar()
    selected_splits = []
    for revision in cast(tuple[PITEventRevision, ...], visible):
        if revision.record.kind is not ActionKind.SPLIT:
            continue
        session = calendar.session(revision.record.effective_date)
        if session is None:
            _deny("EVENT_SESSION_INVALID", "effective_date", "pit")
        if (
            value.definition.action_coverage_start
            <= session.market_open
            < value.definition.action_coverage_end
        ):
            selected_splits.append(revision.record)
    split_records = tuple(selected_splits)
    if sorted(_terms(v) for v in split_records) != sorted(_terms(v) for v in value.actions):
        _deny("PIT_EVENT_BINDING", "event_id", "pit")
    report = inspected.audit_dict()
    report["factor_coverage_sha256"] = hashlib.sha256(_canonical(proofs)).hexdigest()
    return report, inspected.events


def _supply(
    records: dict,
    request: ResearchRequest,
    events: tuple,
    tax_issuer_id: str | None,
    account_scenario_id: str | None,
) -> dict:
    if not request.macro_enabled and not request.dividend_tax_enabled:
        return {"macro": "disabled", "dividend_tax": "disabled"}
    dataset = PITSupplyDataset(
        macro_events=records[EvidenceRole.MACRO_EVENTS],
        macro_coverage=records[EvidenceRole.MACRO_COVERAGE],
        tax_sources=records[EvidenceRole.TAX_SOURCES],
        tax_rules=records[EvidenceRole.TAX_RULES],
        qualifications=records[EvidenceRole.TAX_QUALIFICATIONS],
        input_kind="synthetic",
        expected_sha256="",
    )
    dataset = PITSupplyDataset(
        macro_events=dataset.macro_events,
        macro_coverage=dataset.macro_coverage,
        tax_sources=dataset.tax_sources,
        tax_rules=dataset.tax_rules,
        qualifications=dataset.qualifications,
        input_kind="synthetic",
        expected_sha256=supply_input_sha256(dataset),
    )
    provider, result = SyntheticPITSupply(dataset), {}
    if request.macro_enabled:
        macro = provider.macro_snapshot(request.asof)
        proofs = _select(dataset.macro_coverage, request.asof)
        calendar, intervals, sessions = SessionCalendar(), [], {}
        day = request.window_start.astimezone(NY).date()
        last = (request.window_end - timedelta(microseconds=1)).astimezone(NY).date()
        while day <= last:
            session = calendar.session(day)
            if session is not None:
                sessions[day] = session
                intervals.append((session.market_open, session.market_close))
            day += timedelta(days=1)
        for event in macro.events:
            if not event.cancelled and event.event_type in {"FOMC_STATEMENT", "FOMC_PRESS"}:
                event_day = event.scheduled_at.astimezone(NY).date()
                if calendar.session(event_day) is None:
                    continue  # PR #8 never fabricates a blackout on the next trading day.
                affected = calendar.first_affected_session(event.scheduled_at)
                # A late/half-day release may already first affect the next session.
                # Shift the actual event day, not `affected`, to avoid an extra day.
                related = {affected, calendar.shift(event_day, 1)} & sessions.keys()
                for study_day in sorted(related):
                    previous = calendar.session(calendar.shift(study_day, -1))
                    # Require one connected proof through the studied session, as
                    # the actual next-session gate does, including closed-day gaps.
                    intervals.append((previous.market_open, sessions[study_day].market_close))
        for start, end in intervals:
            covered = _coverage_component(
                proofs, start, inclusive=False, complete=_complete_macro_coverage
            )
            if covered[-1].covered_until < end:
                _deny("PIT_COVERAGE_UNKNOWN", "macro", "supply")
        result["macro"] = macro.audit_dict()
        result["macro"]["session_coverage_sha256"] = hashlib.sha256(
            _canonical(intervals)
        ).hexdigest()
    else:
        result["macro"] = "disabled"
    if request.dividend_tax_enabled:
        reports = []
        for event in events:
            if event.kind == "CASH_DIVIDEND" and not event.cancelled:
                tax = provider.tax_snapshot(
                    security_id=request.security_id,
                    issuer_id=cast(str, tax_issuer_id),
                    event_id=event.event_id,
                    account_scenario_id=cast(str, account_scenario_id),
                    effective_date=event.effective_date,
                    asof=request.asof,
                )
                reports.append(tax.audit_dict())
        result["dividend_tax"] = (
            tuple(reports) if reports else "not_applicable_no_visible_dividends"
        )
    else:
        result["dividend_tax"] = "disabled"
    return result


def verify_dataset(
    snapshot: FrozenDatasetInput,
    mode: ResearchMode,
    *,
    reviewed_root_sha256: str,
    tax_issuer_id: str | None = None,
    account_scenario_id: str | None = None,
) -> dict:
    """Run existing synthetic interfaces atomically; return only a redacted report.

    Account/issuer query identifiers are explicitly hash-bound in the report; no
    configuration approval is inferred. Consumers must not accept this dictionary
    as a certificate or substitute it for re-verifying the original byte inputs.
    """
    phase, provenance = "binding", None
    status, code, field, stages, actual = (
        VerificationStatus.REJECT,
        "INVALID_INPUT",
        "input",
        {},
        {},
    )
    try:
        snapshot = _reconstruct(snapshot, FrozenDatasetInput, "snapshot")
        decision = contract_decision(snapshot, mode, reviewed_root_sha256=reviewed_root_sha256)
        provenance = data_provenance(snapshot, mode, reviewed_root_sha256=reviewed_root_sha256)
        phase = "authority"
        if not decision.may_validate_synthetic:
            status, code, field = decision.status, decision.code, "input_kind"
        else:
            phase = "request"
            for name, supplied in (
                ("tax_issuer_id", tax_issuer_id),
                ("account_scenario_id", account_scenario_id),
            ):
                if snapshot.request.dividend_tax_enabled:
                    text(supplied, name)
                    if len(cast(str, supplied)) > 256:
                        _deny("METADATA_BOUNDS", name, "request")
                elif supplied is not None:
                    _deny("FEATURE_POLICY_MISMATCH", name, "request")
            phase = "versions"
            actual = _versions(snapshot)
            phase = "decode"
            records = _decode(snapshot)
            phase = "archive"
            archive = (
                audit_bound_archive(snapshot.sources, snapshot.request, records[EvidenceRole.RAW])
                if snapshot.request.archive_present
                else {"status": "absent"}
            )
            phase = "normalize"
            value = _normalize(records, snapshot.request)
            phase = "raw"
            raw = _raw(value)
            phase = "pit"
            pit, events = _pit(records, value, snapshot.request)
            phase = "supply"
            supply = _supply(records, snapshot.request, events, tax_issuer_id, account_scenario_id)
            snapshot.assert_bound(reviewed_root_sha256)
            stages = {
                "archive": archive,
                "normalizer": {"status": "normalized", "version": NORMALIZER_VERSION},
                "raw": raw,
                "pit": pit,
                "supply": supply,
            }
            status, code, field, phase = (
                VerificationStatus.SYNTHETIC_CONTRACT_PASS,
                "SYNTHETIC_DATASET_CONSISTENT",
                "",
                "complete",
            )
    except (VerificationContractError, ContractError, PITError, ArchiveError) as error:
        code, field = str(error.code), safe_field(error.field)
        if isinstance(error, VerificationContractError):
            phase = error.phase if error.phase != "contract" else phase
    except (TypeError, ValueError, OverflowError, AttributeError, OSError, RecursionError):
        code, field = "INVALID_INPUT", "input"
    query_sha256 = (
        hashlib.sha256(
            _canonical({"tax_issuer_id": tax_issuer_id, "account_scenario_id": account_scenario_id})
        ).hexdigest()
        if type(tax_issuer_id) in (str, type(None))
        and type(account_scenario_id) in (str, type(None))
        else None
    )
    result = provenance or {"schema_version": "data_provenance_v1", "input_root_sha256": None}
    result.pop("audit_sha256", None)
    result.update(
        {
            "stage": "5E-2_synthetic_verification_orchestration",
            "status": status.value,
            "code": code,
            "field": field,
            "phase": phase,
            "query_sha256": query_sha256,
            "domain_validation_performed": phase == "complete",
            "coverage_status": "synthetic_complete" if phase == "complete" else "not_accepted",
            "coverage_verified": False,
            "actual_software": actual if phase == "complete" else {},
            "stages": stages,
            "may_build_engine": False,
            "raw_verified": False,
            "PIT_verified": False,
            "archive_ready": False,
            "trusted_entry": False,
            "source_authority": "unverified",
            "license_verified": False,
            "strategy_config_approved": False,
            "archive_status": "deferred_by_user / awaiting_paid_plan_and_license_review",
        }
    )
    result["audit_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result
