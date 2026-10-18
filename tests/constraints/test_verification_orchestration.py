"""Actual 5A/B/C/D calls on invented, frozen inputs; no engines or real market data."""

import copy
import hashlib
import json
import pickle
from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest
from test_raw_validation import KNOWN, bundle
from test_research_pit import document
from test_research_pit_supply import common
from test_verification_contracts import versions

from quant_constraints.calendar import NY
from quant_constraints.research.data.contracts import (
    ActionKind,
    CorporateActionRecord,
    PriceBasis,
    ShareUnit,
    VolumeUnit,
)
from quant_constraints.research.data.pit import (
    ACTION_KINDS,
    PITCoverage,
    PITEventRevision,
    PITIdentityRevision,
)
from quant_constraints.research.data.pit_supply import (
    MACRO_TYPES,
    MacroCoverageRevision,
    MacroScheduleRevision,
    QualificationRevision,
    TaxRuleRevision,
    TaxSourceRevision,
)
from quant_constraints.research.data.provenance import ResearchMode
from quant_constraints.research.data.verification import (
    LEGACY_IMPLEMENTATION_BASE,
    REVIEWED_IMPLEMENTATION_BASE,
    ROLE_DEPENDENCIES,
    TAX_RUNTIME_SOURCE_SHA256,
    verify_dataset,
)
from quant_constraints.research.data.verification_contracts import (
    EvidenceClocks,
    EvidenceRole,
    FrozenDatasetInput,
    FrozenEvidence,
    ResearchRequest,
    snapshot_root,
)
from quant_constraints.research.data.verification_wire import encode_records


def fixture(*, frequency="1m", macro=False, tax=False):
    raw = bundle(frequency=frequency)
    if frequency == "1m":
        raw = replace(
            raw,
            raw=raw.raw[:1],
            comparison=raw.comparison[:1],
            definition=replace(raw.definition, window_end=raw.raw[0].bar_end_at),
        )
    req = ResearchRequest(
        input_kind="synthetic",
        security_id="synthetic-A",
        symbol="AAA",
        venue="XNAS",
        currency="USD",
        frequency=frequency,
        window_start=raw.definition.window_start,
        window_end=raw.definition.window_end,
        asof=raw.raw[-1].bar_end_at,
        raw_product_id="raw",
        signal_product_id="comparison",
        signal_basis=PriceBasis.SPLIT_ADJUSTED,
        signal_share_unit=ShareUnit.SPLIT_ADJUSTED,
        signal_volume_unit=VolumeUnit.SPLIT_ADJUSTED_SHARES,
        adjustment_version=raw.definition.adjustment_version,
        volume_policy=raw.definition.volume_policy,
        strategy_config_sha256="a" * 64,
        macro_enabled=macro,
        dividend_tax_enabled=tax,
        archive_present=False,
    )
    identity = raw.identities.entries[0]
    source = identity.source
    event = document(
        PITEventRevision(
            record=raw.actions[0],
            published_at=KNOWN,
            provider_available_at=None,
            document=b"",
            document_sha256="",
        )
    )
    identity_proof = document(
        PITIdentityRevision(
            identity_id="identity",
            version=1,
            revision_of=None,
            record=identity,
            historically_known_at=KNOWN,
            published_at=KNOWN,
            provider_available_at=None,
            cancelled=False,
            document=b"",
            document_sha256="",
        )
    )
    proof = document(
        PITCoverage(
            security_id=req.security_id,
            coverage_id="coverage",
            version=1,
            revision_of=None,
            covered_from=date(2025, 11, 1),
            covered_until=date(2025, 11, 30),
            action_kinds=ACTION_KINDS,
            historically_known_at=KNOWN,
            published_at=KNOWN,
            provider_available_at=None,
            source=source,
            point_in_time=True,
            missing=False,
            cancelled=False,
            document=b"",
            document_sha256="",
        )
    )
    records = {
        EvidenceRole.RAW: raw.raw,
        EvidenceRole.COMPARISON: raw.comparison,
        EvidenceRole.ACTIONS: raw.actions,
        EvidenceRole.IDENTITIES: raw.identities.entries,
        EvidenceRole.FACTS: raw.facts,
        EvidenceRole.DEFINITION: (raw.definition,),
        EvidenceRole.PIT_EVENTS: (event,),
        EvidenceRole.PIT_IDENTITIES: (identity_proof,),
        EvidenceRole.PIT_COVERAGE: (proof,),
    }
    shared = common(source=source, historically_known_at=KNOWN, published_at=KNOWN)
    if macro:
        records[EvidenceRole.MACRO_EVENTS] = (
            document(
                MacroScheduleRevision(
                    **(shared | {"evidence_id": "cpi"}),
                    event_type="CPI",
                    scheduled_at=datetime(2025, 11, 14, 8, 30, tzinfo=NY),
                )
            ),
        )
        records[EvidenceRole.MACRO_COVERAGE] = (
            document(
                MacroCoverageRevision(
                    **shared,
                    covered_from=datetime(2025, 11, 1, tzinfo=NY),
                    covered_until=datetime(2025, 12, 1, tzinfo=NY),
                    event_types=MACRO_TYPES,
                    point_in_time=True,
                    missing=False,
                )
            ),
        )
    if tax:
        dividend = CorporateActionRecord(
            security_id=req.security_id,
            event_id="dividend",
            kind=ActionKind.CASH_DIVIDEND,
            effective_date=date(2025, 11, 14),
            ex_date=date(2025, 11, 14),
            dividend_per_share=1.0,
            payable_date=date(2025, 11, 18),
            historically_known_at=KNOWN,
            knowledge_evidence="fixture:dividend",
            event_version=1,
            source=raw.actions[0].source,
            currency="USD",
        )
        records[EvidenceRole.PIT_EVENTS] += (
            document(
                PITEventRevision(
                    record=dividend,
                    published_at=KNOWN,
                    provider_available_at=None,
                    document=b"",
                    document_sha256="",
                )
            ),
        )
        span = {"effective_from": date(2025, 1, 1), "effective_to": date(2026, 1, 1)}
        records[EvidenceRole.TAX_SOURCES] = (
            document(
                TaxSourceRevision(
                    **shared,
                    **span,
                    security_id=req.security_id,
                    issuer_id="private-issuer",
                    event_id="dividend",
                    source_country="US",
                )
            ),
        )
        records[EvidenceRole.TAX_RULES] = (
            document(
                TaxRuleRevision(**shared, **span, source_country="US", rate=0.10, verified_at=KNOWN)
            ),
        )
        records[EvidenceRole.TAX_QUALIFICATIONS] = (
            document(
                QualificationRevision(
                    **shared,
                    **span,
                    account_scenario_id="private-scenario",
                    tax_residence="CN-mainland",
                    beneficial_owner=True,
                    treaty_documents_valid=True,
                    individual=True,
                    non_us_ca_tax_resident=True,
                )
            ),
        )
    return req, records


def leaf(req, role, records, suffix=""):
    bodies = [getattr(v, "record", v) for v in records]
    prov = [v.source for v in records if hasattr(v, "source")]
    effective = []
    for body in bodies:
        stamp = next(
            (
                getattr(body, n)
                for n in (
                    "bar_start_at",
                    "effective_from",
                    "effective_date",
                    "trading_date",
                    "scheduled_at",
                    "covered_from",
                )
                if getattr(body, n, None) is not None
            ),
            None,
        )
        effective.append(
            stamp
            if isinstance(stamp, datetime) or stamp is None
            else datetime.combine(stamp, time(), NY)
        )

    def common_clock(name, values):
        stamps = [getattr(v, name, None) for v in values]
        return max(stamps) if stamps and all(v is not None for v in stamps) else None

    source = prov[0] if prov else None
    content = encode_records(role, records)
    ids = tuple(
        sorted(
            {
                getattr(v, "evidence_id", None)
                or getattr(v, "identity_id", None)
                or getattr(v, "coverage_id", None)
                or getattr(getattr(v, "record", v), "event_id", "")
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
                    getattr(v, "record", v),
                    "event_version",
                    getattr(getattr(v, "source", None), "record_version", 1),
                ),
            )
            for v in records
        ),
        default=1,
    )
    clocks = EvidenceClocks(
        effective_at=effective[0]
        if effective and all(v == effective[0] for v in effective)
        else None,
        historically_known_at=common_clock(
            "historically_known_at",
            [
                v if hasattr(v, "historically_known_at") else getattr(v, "record", v)
                for v in records
            ],
        ),
        published_at=common_clock("published_at", records),
        provider_available_at=common_clock("provider_available_at", records),
        ingested_at=common_clock("ingested_at", prov) or req.asof + timedelta(days=365),
    )
    return FrozenEvidence(
        source_id=role.value + suffix,
        role=role,
        security_id=req.security_id,
        event_ids=ids,
        source_ref=source.source_partition_ref
        if source
        else records[0].source_ref
        if role is EvidenceRole.FACTS and records
        else records[0].definition_ref
        if role is EvidenceRole.DEFINITION and records
        else "synthetic:empty",
        provider=source.provider
        if source
        else records[0].source_provider
        if role is EvidenceRole.FACTS and records
        else records[0].provider
        if role is EvidenceRole.DEFINITION and records
        else "synthetic-empty",
        dataset_id=source.dataset_id if source else "synthetic-declaration",
        dataset_revision=source.dataset_revision
        if source
        else records[0].dataset_revision
        if role is EvidenceRole.DEFINITION and records
        else "v1",
        partition_id=source.source_partition_id if source else role.value,
        version=revision,
        window_start=req.window_start,
        window_end=req.window_end,
        clocks=clocks,
        depends_on=(),
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def freeze(req, records, *, pipeline=None, extra=()):
    sources = tuple(leaf(req, role, items) for role, items in records.items()) + extra
    sources = tuple(
        replace(
            s,
            depends_on=tuple(
                v.source_id for v in sources if v.role in ROLE_DEPENDENCIES.get(s.role, set())
            ),
        )
        if s.role in ROLE_DEPENDENCIES
        else s
        for s in sources
    )
    return rebind(req, sources, pipeline=pipeline)


def rebind(req, sources, *, pipeline=None):
    pipeline = pipeline or versions(code_revision=REVIEWED_IMPLEMENTATION_BASE)
    return FrozenDatasetInput(
        request=req,
        versions=pipeline,
        sources=sources,
        expected_root_sha256=snapshot_root(req, pipeline, sources),
    )


def run(snapshot, *, root=None, mode=ResearchMode.SYNTHETIC_TEST, **query):
    if snapshot.request.dividend_tax_enabled:
        query = {
            "tax_issuer_id": "private-issuer",
            "account_scenario_id": "private-scenario",
        } | query
    return verify_dataset(
        snapshot, mode, reviewed_root_sha256=root or snapshot.expected_root_sha256, **query
    )


def rejected(report, code, phase=None):
    assert report["status"] == "reject" and report["code"] == code, report
    if phase:
        assert report["phase"] == phase
    assert report["stages"] == {} and report["actual_software"] == {}
    assert report["domain_validation_performed"] is False
    for flag in (
        "raw_verified",
        "PIT_verified",
        "archive_ready",
        "trusted_entry",
        "may_build_engine",
    ):
        assert report[flag] is False
    return report


@pytest.mark.parametrize("macro,tax", [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize("frequency", ["1m", "1d"])
def test_actual_interfaces_pass_deterministically_without_any_issuance(macro, tax, frequency):
    req, records = fixture(macro=macro, tax=tax, frequency=frequency)
    frozen = freeze(req, records)
    report = run(frozen)
    assert report["status"] == "synthetic_contract_pass", report
    assert report["code"] == "SYNTHETIC_DATASET_CONSISTENT"
    assert report["stages"]["raw"]["status"] == "offline_algorithm_pass"
    assert report["stages"]["pit"]["status"] == "synthetic_pit_contract_pass"
    assert report["domain_validation_performed"] is True
    assert run(copy.copy(frozen)) == report == run(pickle.loads(pickle.dumps(frozen)))
    assert "private-issuer" not in json.dumps(report) and "private-scenario" not in json.dumps(
        report
    )
    for flag in (
        "raw_verified",
        "PIT_verified",
        "archive_ready",
        "trusted_entry",
        "may_build_engine",
    ):
        assert report[flag] is False


@pytest.mark.parametrize(
    "kind,mode,status,code",
    [
        ("real", ResearchMode.SYNTHETIC_TEST, "real_evidence_pending", "REAL_EVIDENCE_DEFERRED"),
        ("real", ResearchMode.VERIFIED_RESEARCH, "real_evidence_pending", "REAL_EVIDENCE_DEFERRED"),
        ("synthetic", ResearchMode.VERIFIED_RESEARCH, "reject", "SYNTHETIC_NOT_TRUSTED"),
        ("synthetic", ResearchMode.LEGACY, "reject", "LEGACY_UNVERIFIED"),
        ("real", ResearchMode.LEGACY, "reject", "LEGACY_UNVERIFIED"),
    ],
)
def test_authority_stops_before_domain_decode(kind, mode, status, code, monkeypatch):
    req, records = fixture()
    req = replace(req, input_kind=kind)
    extra = ()
    if kind == "real":
        extra = (
            replace(
                leaf(req, EvidenceRole.FACTS, records[EvidenceRole.FACTS]),
                role=EvidenceRole.LICENSE,
                source_id="license",
                content=b"untrusted-opaque-license",
                sha256=hashlib.sha256(b"untrusted-opaque-license").hexdigest(),
            ),
        )
    frozen = freeze(req, records, extra=extra)
    monkeypatch.setattr(
        "quant_constraints.research.data.verification._decode",
        lambda _: pytest.fail("domain decoder must not run"),
    )
    report = run(frozen, mode=mode)
    assert report["status"] == status and report["code"] == code
    assert report["stages"] == {} and report["phase"] == "authority"


@pytest.mark.parametrize(
    "field",
    [
        "acquisition",
        "adapter",
        "dataset",
        "normalizer",
        "raw_validator",
        "pit_provider",
        "pit_supply",
        "contract",
        "code_revision",
    ],
)
def test_every_declared_software_version_is_checked(field):
    req, records = fixture()
    # A contract version is structurally checked by 5E-1; forging tests revalidation.
    if field == "contract":
        frozen = freeze(req, records)
        object.__setattr__(frozen.versions, "contract", "old")
        rejected(run(frozen), "UNSUPPORTED_CONTRACT_VERSION")
        return
    value = "b" * 40 if field == "code_revision" else "old-version"
    report = run(
        freeze(
            req,
            records,
            pipeline=versions(code_revision=REVIEWED_IMPLEMENTATION_BASE, **{field: value})
            if field != "code_revision"
            else versions(code_revision=value),
        )
    )
    rejected(report, "VERSION_MISMATCH", "versions")


def test_tax_runtime_overlay_preserves_legacy_pins_and_rejects_old_code_identity():
    from quant_constraints.research.data.verification import (
        BASE_SOURCE_SHA256,
        ENTRY_RUNTIME_SOURCE_SHA256,
        TAX_RUNTIME_SOURCE_REVISION,
        _versions,
    )

    req, records = fixture()
    proof = _versions(freeze(req, records))
    assert proof["checked_versions"]["code_revision"] == REVIEWED_IMPLEMENTATION_BASE
    assert proof["legacy_source_revision"] == LEGACY_IMPLEMENTATION_BASE
    assert BASE_SOURCE_SHA256["quant_constraints.corporate_actions.events"] == (
        "ea1cdddd7e599bf8e192e741c970bd623f226817d369c1036d82d18c2cb96ae4"
    )
    assert proof["tax_runtime_source_sha256"] == TAX_RUNTIME_SOURCE_SHA256
    assert proof["entry_runtime_source_sha256"] == ENTRY_RUNTIME_SOURCE_SHA256
    assert proof["tax_runtime_source_revision"] == TAX_RUNTIME_SOURCE_REVISION
    previous = freeze(req, records, pipeline=versions(code_revision=TAX_RUNTIME_SOURCE_REVISION))
    rejected(run(previous), "VERSION_MISMATCH", "versions")
    old = freeze(req, records, pipeline=versions(code_revision=LEGACY_IMPLEMENTATION_BASE))
    rejected(run(old), "VERSION_MISMATCH", "versions")


def test_research_overlay_preserves_previous_scope_pins_and_refuses_v5_snapshot():
    from quant_constraints.research.data.verification import (
        RESEARCH_RUNTIME_SOURCE_REVISION,
        RESEARCH_RUNTIME_SOURCE_SHA256,
        SECURITY_ENTRY_SOURCE_REVISION,
        _versions,
    )

    req, records = fixture()
    proof = _versions(freeze(req, records))
    assert proof["research_runtime_source_revision"] == RESEARCH_RUNTIME_SOURCE_REVISION
    assert proof["research_runtime_source_sha256"] == RESEARCH_RUNTIME_SOURCE_SHA256
    assert proof["entry_runtime_source_revision"] == SECURITY_ENTRY_SOURCE_REVISION
    old = freeze(req, records, pipeline=versions(code_revision=SECURITY_ENTRY_SOURCE_REVISION))
    rejected(run(old), "VERSION_MISMATCH", "versions")


def test_replay_integrity_overlay_preserves_v6_pins_and_rejects_its_old_snapshot():
    from quant_constraints.research.data.verification import (
        RESEARCH_INTEGRITY_SOURCE_REVISION,
        RESEARCH_INTEGRITY_SOURCE_SHA256,
        RESEARCH_RUNTIME_SOURCE_REVISION,
        RESEARCH_RUNTIME_SOURCE_SHA256,
        _versions,
    )

    req, records = fixture()
    proof = _versions(freeze(req, records))
    assert proof["research_integrity_source_revision"] == RESEARCH_INTEGRITY_SOURCE_REVISION
    assert proof["research_integrity_source_sha256"] == RESEARCH_INTEGRITY_SOURCE_SHA256
    assert proof["research_runtime_source_sha256"] == RESEARCH_RUNTIME_SOURCE_SHA256
    assert set(RESEARCH_INTEGRITY_SOURCE_SHA256) <= set(proof["reviewed_source_sha256"])
    old = freeze(req, records, pipeline=versions(code_revision=RESEARCH_RUNTIME_SOURCE_REVISION))
    rejected(run(old), "VERSION_MISMATCH", "versions")


def test_close_policy_overlay_preserves_v7_evidence_and_refuses_its_old_snapshot():
    from quant_constraints.research.data.verification import (
        RESEARCH_CLOSE_POLICY_SOURCE_REVISION,
        RESEARCH_CLOSE_POLICY_SOURCE_SHA256,
        RESEARCH_INTEGRITY_SOURCE_REVISION,
        RESEARCH_INTEGRITY_SOURCE_SHA256,
        _versions,
    )

    req, records = fixture()
    proof = _versions(freeze(req, records))
    assert proof["research_close_policy_source_revision"] == RESEARCH_CLOSE_POLICY_SOURCE_REVISION
    assert proof["research_close_policy_source_sha256"] == RESEARCH_CLOSE_POLICY_SOURCE_SHA256
    assert proof["research_integrity_source_sha256"] == RESEARCH_INTEGRITY_SOURCE_SHA256
    assert set(RESEARCH_CLOSE_POLICY_SOURCE_SHA256) <= set(proof["reviewed_source_sha256"])
    old = freeze(req, records, pipeline=versions(code_revision=RESEARCH_INTEGRITY_SOURCE_REVISION))
    rejected(run(old), "VERSION_MISMATCH", "versions")


def test_completed_close_overlay_preserves_v8_pins_and_rejects_its_old_snapshot():
    from quant_constraints.research.data.verification import (
        RESEARCH_ASSUMPTION_SOURCE_SHA256,
        RESEARCH_CLOSE_POLICY_SOURCE_REVISION,
        RESEARCH_CLOSE_POLICY_SOURCE_SHA256,
        RESEARCH_EXECUTION_SOURCE_REVISION,
        RESEARCH_EXECUTION_SOURCE_SHA256,
        _versions,
    )

    req, records = fixture()
    proof = _versions(freeze(req, records))
    assert proof["research_execution_source_revision"] == RESEARCH_EXECUTION_SOURCE_REVISION
    assert proof["research_execution_source_sha256"] == RESEARCH_EXECUTION_SOURCE_SHA256
    assert proof["research_close_policy_source_sha256"] == RESEARCH_CLOSE_POLICY_SOURCE_SHA256
    assert all(
        proof["reviewed_source_sha256"][name] == RESEARCH_ASSUMPTION_SOURCE_SHA256.get(name, sha)
        for name, sha in RESEARCH_EXECUTION_SOURCE_SHA256.items()
    )
    old = freeze(
        req, records, pipeline=versions(code_revision=RESEARCH_CLOSE_POLICY_SOURCE_REVISION)
    )
    rejected(run(old), "VERSION_MISMATCH", "versions")


def test_funded_benchmark_overlay_preserves_v9_sources_and_refuses_old_snapshot():
    from quant_constraints.research.data.verification import (
        RESEARCH_BENCHMARK_SOURCE_REVISION,
        RESEARCH_BENCHMARK_SOURCE_SHA256,
        RESEARCH_EXECUTION_SOURCE_REVISION,
        RESEARCH_EXECUTION_SOURCE_SHA256,
        _versions,
    )

    req, records = fixture()
    proof = _versions(freeze(req, records))
    assert proof["research_benchmark_source_revision"] == RESEARCH_BENCHMARK_SOURCE_REVISION
    assert proof["research_benchmark_source_sha256"] == RESEARCH_BENCHMARK_SOURCE_SHA256
    assert proof["research_execution_source_sha256"] == RESEARCH_EXECUTION_SOURCE_SHA256
    assert all(
        proof["reviewed_source_sha256"][name] == sha
        for name, sha in RESEARCH_BENCHMARK_SOURCE_SHA256.items()
    )
    old = freeze(req, records, pipeline=versions(code_revision=RESEARCH_EXECUTION_SOURCE_REVISION))
    rejected(run(old), "VERSION_MISMATCH", "versions")


def test_assumed_distribution_overlay_retains_v10_pins_and_rejects_old_snapshot():
    from quant_constraints.research.data.verification import (
        RESEARCH_ASSUMPTION_SOURCE_REVISION,
        RESEARCH_ASSUMPTION_SOURCE_SHA256,
        RESEARCH_BENCHMARK_SOURCE_REVISION,
        RESEARCH_BENCHMARK_SOURCE_SHA256,
        _versions,
    )

    req, records = fixture()
    proof = _versions(freeze(req, records))
    assert proof["research_assumption_source_revision"] == RESEARCH_ASSUMPTION_SOURCE_REVISION
    assert proof["research_benchmark_source_sha256"] == RESEARCH_BENCHMARK_SOURCE_SHA256
    assert proof["research_assumption_source_sha256"] == RESEARCH_ASSUMPTION_SOURCE_SHA256
    assert all(
        proof["reviewed_source_sha256"][name] == sha
        for name, sha in RESEARCH_ASSUMPTION_SOURCE_SHA256.items()
    )
    old = freeze(req, records, pipeline=versions(code_revision=RESEARCH_BENCHMARK_SOURCE_REVISION))
    rejected(run(old), "VERSION_MISMATCH", "versions")


@pytest.mark.parametrize(
    "name",
    [
        "ml4t.backtest.accounting.cash",
        "quant_constraints.adapter",
        "ml4t.backtest.strategies.sector_rotation.research",
        "ml4t.backtest.strategies.sector_rotation.research_inputs",
        "ml4t.backtest.strategies.sector_rotation.research_ledger",
        "ml4t.backtest.strategies.sector_rotation.research_benchmark",
    ],
)
def test_research_modules_are_bound_before_any_legacy_domain_replay(name, monkeypatch):
    from quant_constraints.research.data import verification

    original = verification._source_digest
    monkeypatch.setattr(
        verification,
        "_source_digest",
        lambda module: "0" * 64 if module == name else original(module),
    )
    req, records = fixture()
    rejected(run(freeze(req, records)), "IMPLEMENTATION_MISMATCH", "versions")


@pytest.mark.parametrize(
    "name",
    tuple(TAX_RUNTIME_SOURCE_SHA256)
    + (
        "quant_constraints.models",
        "quant_constraints.controller",
        "quant_constraints.security_policy",
        "ml4t.backtest.strategies.sector_rotation.config",
        "ml4t.backtest.strategies.sector_rotation.adapter",
    ),
)
def test_every_pinned_tax_runtime_module_is_checked_before_domain_replay(name, monkeypatch):
    from quant_constraints.research.data import verification

    original = verification._source_digest
    monkeypatch.setattr(
        verification,
        "_source_digest",
        lambda module: "0" * 64 if module == name else original(module),
    )
    req, records = fixture()
    rejected(run(freeze(req, records)), "IMPLEMENTATION_MISMATCH", "versions")


@pytest.mark.parametrize(
    "role",
    [
        encode_role
        for encode_role in EvidenceRole
        if encode_role
        not in {
            EvidenceRole.ARCHIVE_MANIFEST,
            EvidenceRole.L0_RESPONSE,
            EvidenceRole.ACQUISITION_RECEIPT,
            EvidenceRole.L1_PARTITION,
            EvidenceRole.LICENSE,
        }
    ],
)
def test_resealing_any_domain_role_cannot_replace_the_independently_held_root(role):
    req, records = fixture(macro=True, tax=True)
    original = freeze(req, records)
    sources = tuple(
        replace(s, content=s.content + b" ", sha256=hashlib.sha256(s.content + b" ").hexdigest())
        if s.role is role
        else s
        for s in original.sources
    )
    changed = rebind(req, sources)
    rejected(run(changed, root=original.expected_root_sha256), "SOURCE_ROOT_MISMATCH")


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "fake-provider"),
        ("dataset_id", "fake-dataset"),
        ("dataset_revision", "fake-revision"),
        ("partition_id", "fake-partition"),
        ("source_ref", "private-secret-url"),
        ("event_ids", ("fake-event",)),
        ("version", 2),
        ("window_end", None),
    ],
)
def test_resealed_metadata_must_match_the_decoded_record(field, value):
    req, records = fixture()
    frozen = freeze(req, records)
    value = value or req.window_end + timedelta(minutes=1)
    sources = tuple(
        replace(s, **{field: value}) if s.role is EvidenceRole.RAW else s for s in frozen.sources
    )
    rejected(run(rebind(req, sources)), "SOURCE_BINDING_MISMATCH", "binding")


def test_missing_actual_dependency_cannot_be_replaced_by_role_labels():
    req, records = fixture()
    frozen = freeze(req, records)
    sources = tuple(
        replace(s, depends_on=()) if s.role is EvidenceRole.COMPARISON else s
        for s in frozen.sources
    )
    rejected(run(rebind(req, sources)), "SOURCE_DEPENDENCY_MISSING", "binding")


@pytest.mark.parametrize(
    "change,code",
    [
        ({"raw_product_id": "other-raw"}, "REQUEST_BINDING_MISMATCH"),
        ({"signal_product_id": "other-signal"}, "REQUEST_BINDING_MISMATCH"),
        ({"symbol": "wrong-symbol"}, "IDENTITY_MISSING"),
        ({"asof": KNOWN}, "REQUEST_BINDING_MISMATCH"),
    ],
)
def test_request_and_domain_terms_must_agree(change, code):
    req, records = fixture()
    rejected(run(freeze(replace(req, **change), records)), code)


def test_raw_declaration_is_storage_only_not_a_supported_5c_comparison():
    req, records = fixture()
    from quant_constraints.research.data.raw_validation import VolumePolicy

    req = replace(
        req,
        signal_basis=PriceBasis.RAW,
        signal_share_unit=ShareUnit.AS_TRADED,
        signal_volume_unit=VolumeUnit.AS_TRADED_SHARES,
        adjustment_version=None,
        volume_policy=VolumePolicy.AS_TRADED,
    )
    rejected(run(freeze(req, records)), "UNIT_MISMATCH", "raw")


def test_consistent_5c_inputs_with_missing_minutes_still_fail_the_orchestration():
    req, records = fixture()
    req = replace(req, window_end=req.window_end + timedelta(minutes=1))
    records[EvidenceRole.DEFINITION] = (
        replace(records[EvidenceRole.DEFINITION][0], window_end=req.window_end),
    )
    rejected(run(freeze(req, records)), "RAW_COVERAGE_GAP", "raw")


def test_daily_missing_session_is_not_hidden_by_5c_minute_only_diagnostics():
    req, records = fixture(frequency="1d")
    records[EvidenceRole.RAW] = records[EvidenceRole.RAW][:1]
    records[EvidenceRole.COMPARISON] = records[EvidenceRole.COMPARISON][:1]
    rejected(run(freeze(req, records)), "RAW_COVERAGE_GAP", "raw")


def test_raw_price_and_independent_fact_document_reject_through_actual_5c():
    req, records = fixture()
    records[EvidenceRole.COMPARISON] = (replace(records[EvidenceRole.COMPARISON][0], close=101),)
    rejected(run(freeze(req, records)), "RAW_ADJUSTED_PRICE_CONFLICT", "raw")
    req, records = fixture()
    records[EvidenceRole.FACTS] = (
        replace(records[EvidenceRole.FACTS][0], document=b"private-document"),
    )
    rejected(run(freeze(req, records)), "SOURCE_HASH_MISMATCH", "raw")


@pytest.mark.parametrize(
    "change,code",
    [
        ({"missing": True}, "PIT_COVERAGE_UNKNOWN"),
        ({"point_in_time": False}, "PIT_COVERAGE_UNKNOWN"),
        ({"covered_until": date(2025, 11, 14)}, "PIT_EVENT_OUTSIDE_COVERAGE"),
        ({"historically_known_at": None}, "PIT_KNOWLEDGE_UNKNOWN"),
    ],
)
def test_factor_anchor_and_unknown_pit_coverage_fail_closed(change, code):
    req, records = fixture()
    records[EvidenceRole.PIT_COVERAGE] = (
        document(replace(records[EvidenceRole.PIT_COVERAGE][0], **change)),
    )
    rejected(run(freeze(req, records)), code, "pit")


def test_revised_or_cancelled_split_does_not_fall_back_to_5c_original_terms():
    req, records = fixture()
    first = records[EvidenceRole.PIT_EVENTS][0]
    second = document(
        replace(
            first,
            record=replace(
                first.record,
                event_version=2,
                revision_of=1,
                cancelled=True,
                historically_known_at=KNOWN + timedelta(days=1),
            ),
            published_at=KNOWN + timedelta(days=1),
        )
    )
    records[EvidenceRole.PIT_EVENTS] = (first, second)
    rejected(run(freeze(req, records)), "PIT_EVENT_BINDING", "pit")


def test_future_known_split_is_not_accepted_as_historical_factor_evidence():
    req, records = fixture()
    first = records[EvidenceRole.PIT_EVENTS][0]
    later = req.asof + timedelta(days=1)
    records[EvidenceRole.PIT_EVENTS] = (
        document(
            replace(
                first, record=replace(first.record, historically_known_at=later), published_at=later
            )
        ),
    )
    rejected(run(freeze(req, records)), "PIT_EVENT_BINDING", "pit")


def test_pit_identity_is_checked_in_addition_to_effective_identity():
    req, records = fixture()
    revision = records[EvidenceRole.PIT_IDENTITIES][0]
    records[EvidenceRole.PIT_IDENTITIES] = (
        document(
            replace(
                revision,
                record=replace(
                    revision.record, effective_from=req.window_start - timedelta(days=1)
                ),
            )
        ),
    )
    rejected(run(freeze(req, records)), "PIT_IDENTITY_BINDING", "pit")


@pytest.mark.parametrize("feature", ["macro", "tax"])
def test_disabled_feature_cannot_smuggle_unused_evidence(feature):
    req, records = fixture(macro=True, tax=True)
    req = replace(req, macro_enabled=feature != "macro", dividend_tax_enabled=feature != "tax")
    rejected(run(freeze(req, records)), "FEATURE_POLICY_MISMATCH", "binding")


def test_full_macro_session_requires_more_than_a_point_coverage_proof():
    req, records = fixture(macro=True)
    records[EvidenceRole.MACRO_COVERAGE] = (
        document(
            replace(
                records[EvidenceRole.MACRO_COVERAGE][0],
                covered_until=req.asof + timedelta(minutes=1),
            )
        ),
    )
    rejected(run(freeze(req, records)), "PIT_COVERAGE_UNKNOWN", "supply")


@pytest.mark.parametrize(
    "change,code",
    [
        ({"rate": 0.15}, "PIT_TAX_SCENARIO_RATE"),
        ({"source_country": "GB"}, "PIT_TAX_COUNTRY"),
        ({"verified_at": None}, "INVALID_WIRE_VALUE"),
    ],
)
def test_tax_terms_use_the_existing_supply_validator(change, code):
    req, records = fixture(tax=True)
    records[EvidenceRole.TAX_RULES] = (
        document(replace(records[EvidenceRole.TAX_RULES][0], **change)),
    )
    rejected(run(freeze(req, records)), code)


@pytest.mark.parametrize(
    "query,code",
    [
        ({"tax_issuer_id": "wrong-issuer"}, "PIT_TAX_UNKNOWN"),
        ({"account_scenario_id": "wrong-scenario"}, "PIT_TAX_UNKNOWN"),
        ({"account_scenario_id": None}, "INVALID_VALUE"),
        ({"account_scenario_id": True}, "INVALID_VALUE"),
        ({"account_scenario_id": "x" * 257}, "METADATA_BOUNDS"),
    ],
)
def test_explicit_tax_query_is_required_bound_and_redacted(query, code):
    req, records = fixture(tax=True)
    rejected(run(freeze(req, records), **query), code)


def test_qualification_failure_publishes_no_partial_domain_success():
    req, records = fixture(macro=True, tax=True)
    records[EvidenceRole.TAX_QUALIFICATIONS] = (
        document(
            replace(records[EvidenceRole.TAX_QUALIFICATIONS][0], treaty_documents_valid=False)
        ),
    )
    report = rejected(run(freeze(req, records)), "PIT_TAX_QUALIFICATION", "supply")
    assert "offline_algorithm_pass" not in json.dumps(report)


def test_verifier_accepts_only_the_frozen_inputs_not_prior_reports_or_fake_instances():
    req, records = fixture()
    frozen = freeze(req, records)
    report = run(frozen)
    report["trusted_entry"] = True
    rejected(
        verify_dataset(
            report, ResearchMode.SYNTHETIC_TEST, reviewed_root_sha256=frozen.expected_root_sha256
        ),
        "INVALID_TYPE",
    )
    rejected(
        verify_dataset(
            object.__new__(FrozenDatasetInput),
            ResearchMode.SYNTHETIC_TEST,
            reviewed_root_sha256=frozen.expected_root_sha256,
        ),
        "INVALID_OBJECT_STATE",
    )


def test_implementation_source_mismatch_and_unreadable_code_fail_closed(monkeypatch):
    req, records = fixture()
    frozen = freeze(req, records)
    monkeypatch.setattr(
        "quant_constraints.research.data.verification._source_digest", lambda _: "0" * 64
    )
    rejected(run(frozen), "IMPLEMENTATION_MISMATCH", "versions")

    def unreadable(_):
        raise OSError("private-path-must-not-leak")

    monkeypatch.setattr("quant_constraints.research.data.verification._source_digest", unreadable)
    report = rejected(run(frozen), "INVALID_INPUT", "versions")
    assert "private-path" not in json.dumps(report)


@pytest.mark.parametrize("role", [EvidenceRole.FACTS, EvidenceRole.DEFINITION])
def test_independent_fact_and_definition_metadata_must_match_terms(role):
    req, records = fixture()
    frozen = freeze(req, records)
    sources = tuple(
        replace(s, source_ref="private-wrong-reference") if s.role is role else s
        for s in frozen.sources
    )
    rejected(run(rebind(req, sources)), "SOURCE_BINDING_MISMATCH", "binding")


def test_record_owner_must_match_the_envelope_even_with_resealed_content():
    req, records = fixture()
    records[EvidenceRole.RAW] = (
        replace(records[EvidenceRole.RAW][0], security_id="other-security"),
    )
    rejected(run(freeze(req, records)), "SOURCE_OWNER_MISMATCH", "binding")


@pytest.mark.parametrize("role", [EvidenceRole.RAW, EvidenceRole.COMPARISON])
def test_record_product_id_does_not_follow_the_request_label(role):
    req, records = fixture()
    records[role] = (replace(records[role][0], product_id="other-product"),)
    rejected(run(freeze(req, records)), "REQUEST_BINDING_MISMATCH", "normalize")


def test_empty_definition_and_factor_coverage_tail_fail_closed():
    req, records = fixture()
    records[EvidenceRole.DEFINITION] = ()
    rejected(run(freeze(req, records)), "FACTOR_EVIDENCE_MISSING", "binding")
    req, records = fixture()
    records[EvidenceRole.PIT_COVERAGE] = (
        document(replace(records[EvidenceRole.PIT_COVERAGE][0], covered_until=date(2025, 11, 17))),
    )
    rejected(run(freeze(req, records)), "PIT_COVERAGE_UNKNOWN", "pit")


def test_fomc_previous_session_is_included_in_macro_coverage_scope():
    req, records = fixture(macro=True)
    schedule = records[EvidenceRole.MACRO_EVENTS][0]
    records[EvidenceRole.MACRO_EVENTS] = (
        document(
            replace(
                schedule,
                event_type="FOMC_STATEMENT",
                scheduled_at=req.window_start + timedelta(hours=5),
            )
        ),
    )
    frozen = freeze(req, records)
    assert run(frozen)["status"] == "synthetic_contract_pass"
    records[EvidenceRole.MACRO_COVERAGE] = (
        document(
            replace(
                records[EvidenceRole.MACRO_COVERAGE][0],
                covered_from=datetime.combine(req.window_start.astimezone(NY).date(), time(), NY),
            )
        ),
    )
    rejected(run(freeze(req, records)), "PIT_COVERAGE_UNKNOWN", "supply")


def test_synthetic_source_profile_handles_windows_checkout_newlines(monkeypatch):
    from pathlib import Path

    from quant_constraints.research.data.verification import _source_digest

    original = Path.read_bytes
    before = _source_digest("quant_constraints.research.data.pit")
    monkeypatch.setattr(Path, "read_bytes", lambda p: original(p).replace(b"\n", b"\r\n"))
    assert _source_digest("quant_constraints.research.data.pit") == before


def test_missing_module_source_and_disabled_tax_context_are_rejected(monkeypatch):
    from quant_constraints.research.data import pit

    req, records = fixture()
    frozen = freeze(req, records)
    rejected(
        run(frozen, account_scenario_id="private-context"), "FEATURE_POLICY_MISMATCH", "request"
    )
    monkeypatch.setattr(pit, "__file__", None)
    rejected(run(frozen), "IMPLEMENTATION_MISMATCH", "versions")


def test_actual_interface_order_and_no_engine_or_broker_construction(monkeypatch):
    from ml4t.backtest import Engine
    from ml4t.backtest.broker import Broker
    from quant_constraints.research.data import verification as module

    calls = []

    def forbidden(*args, **kwargs):
        pytest.fail("5E-2 must not construct account or engine state")

    monkeypatch.setattr(Engine, "__init__", forbidden)
    monkeypatch.setattr(Broker, "__init__", forbidden)
    for name, phase in (
        ("normalize_identities", "5A"),
        ("normalize_raw_bars", "5A"),
        ("normalize_signal_bars", "5A"),
    ):
        original = getattr(module, name)

        def wrap(*args, _original=original, _phase=phase, **kwargs):
            calls.append(_phase)
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, name, wrap)
    for cls, name, phase in (
        (module.RawOHLCVValidator, "validate", "5C"),
        (module.SyntheticPITProvider, "inspect", "5D"),
        (module.SyntheticPITProvider, "resolve_asof", "5D_identity"),
        (module.SyntheticPITSupply, "macro_snapshot", "macro"),
        (module.SyntheticPITSupply, "tax_snapshot", "tax"),
    ):
        original = getattr(cls, name)

        def wrap(self, *args, _original=original, _phase=phase, **kwargs):
            calls.append(_phase)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(cls, name, wrap)
    req, records = fixture(macro=True, tax=True)
    assert run(freeze(req, records))["status"] == "synthetic_contract_pass"
    assert calls == ["5A", "5A", "5A", "5C", "5D", "5D_identity", "macro", "tax"]
    calls.clear()
    records[EvidenceRole.COMPARISON] = (replace(records[EvidenceRole.COMPARISON][0], close=101),)
    rejected(run(freeze(req, records)), "RAW_ADJUSTED_PRICE_CONFLICT", "raw")
    assert calls == ["5A", "5A", "5A", "5C"]


def test_macro_cancellation_and_future_revision_preserve_asof_selection():
    req, records = fixture(macro=True)
    original = records[EvidenceRole.MACRO_EVENTS][0]
    later = KNOWN + timedelta(days=1)
    cancel = document(
        replace(
            original,
            version=2,
            revision_of=1,
            cancelled=True,
            historically_known_at=later,
            published_at=later,
        )
    )
    records[EvidenceRole.MACRO_EVENTS] = (original, cancel)
    report = run(freeze(req, records))
    assert report["status"] == "synthetic_contract_pass"
    hashes = report["stages"]["supply"]["macro"]["source_hashes"]
    assert cancel.document_sha256 in hashes and original.document_sha256 not in hashes
    future = req.asof + timedelta(days=1)
    records[EvidenceRole.MACRO_EVENTS] = (
        original,
        document(replace(cancel, historically_known_at=future, published_at=future)),
    )
    report = run(freeze(req, records))
    assert report["status"] == "synthetic_contract_pass"
    assert original.document_sha256 in report["stages"]["supply"]["macro"]["source_hashes"]
