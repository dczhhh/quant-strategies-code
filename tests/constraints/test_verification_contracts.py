"""5E-1 schema/root regressions; this stage neither validates markets nor runs engines."""

import copy
import hashlib
import json
import pickle
from dataclasses import FrozenInstanceError, replace
from datetime import datetime

import pytest
from test_adapter import at

from quant_constraints.research.data.contracts import PriceBasis, ShareUnit, VolumeUnit
from quant_constraints.research.data.pit import _canonical
from quant_constraints.research.data.provenance import (
    ResearchMode,
    VerificationStatus,
    contract_decision,
    data_provenance,
)
from quant_constraints.research.data.raw_validation import VolumePolicy
from quant_constraints.research.data.verification_contracts import (
    BASE_ROLES,
    BUNDLE_SCHEMA,
    EvidenceClocks,
    EvidenceRole,
    FrozenDatasetInput,
    FrozenEvidence,
    PipelineVersions,
    ResearchRequest,
    VerificationContractError,
    VerifiedDatasetBundle,
    snapshot_root,
)


def request(**changes):
    return ResearchRequest(
        **(
            {
                "input_kind": "synthetic",
                "security_id": "stable-A",
                "symbol": "A",
                "venue": "XNYS",
                "currency": "USD",
                "frequency": "1m",
                "window_start": at("2026-10-01", "00:00"),
                "window_end": at("2026-11-01", "00:00"),
                "asof": at("2026-10-12"),
                "raw_product_id": "raw-1m",
                "signal_product_id": "signal-1m",
                "signal_basis": PriceBasis.SPLIT_ADJUSTED,
                "signal_share_unit": ShareUnit.SPLIT_ADJUSTED,
                "signal_volume_unit": VolumeUnit.AS_TRADED_SHARES,
                "adjustment_version": "synthetic-split-v1",
                "volume_policy": VolumePolicy.AS_TRADED,
                "strategy_config_sha256": "a" * 64,
                "macro_enabled": True,
                "dividend_tax_enabled": True,
                "archive_present": True,
            }
            | changes
        )
    )


def clocks(**changes):
    return EvidenceClocks(
        **(
            {
                "effective_at": at("2026-10-12"),
                "historically_known_at": at("2026-10-01"),
                "published_at": at("2026-09-30"),
                "provider_available_at": at("2026-10-02"),
                "ingested_at": at("2026-10-20"),
            }
            | changes
        )
    )


def versions(**changes):
    return PipelineVersions(
        **(
            {
                "acquisition": "research_acquisition_v1",
                "adapter": "massive_aggregates_v2",
                "dataset": "research_dataset_v1",
                "normalizer": "research_normalizer_v2",
                "raw_validator": "research_raw_offline_v1",
                "pit_provider": "research_pit_synthetic_v1",
                "pit_supply": "research_pit_supply_synthetic_v1",
                "code_revision": "2968d1a86db8f021f9ebd73341343329925e046f",
            }
            | changes
        )
    )


def evidence(role=EvidenceRole.RAW, **changes):
    content = ("private-fixture-body:" + str(role)).encode()
    return FrozenEvidence(
        **(
            {
                "source_id": str(role),
                "role": role,
                "security_id": "stable-A",
                "event_ids": ("private-tax-identity",),
                "source_ref": "synthetic:private-ref-with-token",
                "provider": "synthetic-fixture",
                "dataset_id": "private-dataset",
                "dataset_revision": "private-revision-v1",
                "partition_id": "private-partition",
                "version": 1,
                "window_start": at("2026-10-01", "00:00"),
                "window_end": at("2026-11-01", "00:00"),
                "clocks": clocks(),
                "depends_on": (),
                "content": content,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            | changes
        )
    )


def snapshot(req=None, sources=None, pipeline=None, root=None, **changes):
    req, pipeline = req or request(), pipeline or versions()
    if sources is None:
        sources = tuple(evidence(role) for role in sorted(req.required_roles()))
    root = root or snapshot_root(req, pipeline, sources)
    return FrozenDatasetInput(
        request=req, versions=pipeline, sources=sources, expected_root_sha256=root, **changes
    )


def fails(code, call, phase=None):
    with pytest.raises(VerificationContractError) as caught:
        call()
    error = caught.value
    assert error.code == code
    assert str(error) == f"{code}:{error.field}:{error.phase}"
    assert set(error.as_dict()) == {"schema_version", "code", "field", "phase"}
    if phase:
        assert error.phase == phase
    return error


def test_strict_wire_roundtrip_and_utc_clocks():
    original = request()
    wire = json.loads(_canonical(original))
    assert ResearchRequest.from_dict(wire) == original
    assert original.window_start.isoformat().endswith("+00:00")
    assert wire["schema_version"] == BUNDLE_SCHEMA


@pytest.mark.parametrize(
    "change,code",
    [
        ({"input_kind": "licensed_private"}, "INVALID_INPUT_KIND"),
        ({"input_kind": []}, "INVALID_INPUT_KIND"),
        ({"frequency": []}, "UNSUPPORTED_SCOPE"),
        ({"frequency": "5m"}, "UNSUPPORTED_SCOPE"),
        ({"currency": "EUR"}, "UNSUPPORTED_SCOPE"),
        ({"timezone": "UTC"}, "UNSUPPORTED_SESSION"),
        ({"session_filter": "all"}, "UNSUPPORTED_SESSION"),
        ({"window_end": at("2026-10-01", "00:00")}, "INVALID_INTERVAL"),
        ({"window_start": datetime(2026, 10, 1)}, "NAIVE_TIMESTAMP"),
        ({"symbol": " secret "}, "INVALID_VALUE"),
        ({"raw_product_id": "signal-1m"}, "PRODUCT_COLLISION"),
        ({"signal_basis": "raw"}, "UNIT_MISMATCH"),
        ({"signal_volume_unit": "as_traded_shares"}, "UNIT_MISMATCH"),
        ({"signal_share_unit": ShareUnit.AS_TRADED}, "UNIT_MISMATCH"),
        ({"adjustment_version": None}, "UNIT_MISMATCH"),
        ({"adjustment_version": ""}, "INVALID_VALUE"),
        ({"volume_policy": VolumePolicy.UNKNOWN}, "VOLUME_POLICY_UNKNOWN"),
        ({"volume_policy": "as_traded"}, "VOLUME_POLICY_UNKNOWN"),
        ({"execution_basis": PriceBasis.SPLIT_ADJUSTED}, "UNIT_MISMATCH"),
        ({"valuation_basis": PriceBasis.TOTAL_RETURN}, "UNIT_MISMATCH"),
        ({"risk_basis": PriceBasis.SPLIT_ADJUSTED}, "UNIT_MISMATCH"),
        ({"execution_share_unit": ShareUnit.SPLIT_ADJUSTED}, "UNIT_MISMATCH"),
        ({"execution_volume_unit": VolumeUnit.SPLIT_ADJUSTED_SHARES}, "UNIT_MISMATCH"),
        ({"macro_enabled": 1}, "INVALID_TYPE"),
        ({"dividend_tax_enabled": None}, "INVALID_TYPE"),
        ({"archive_present": "false"}, "INVALID_TYPE"),
        ({"strategy_config_sha256": "A" * 64}, "INVALID_DIGEST"),
        ({"schema_version": "research_verified_bundle_v0"}, "UNSUPPORTED_BUNDLE_SCHEMA"),
    ],
)
def test_bad_request_declarations_reject(change, code):
    error = fails(code, lambda: request(**change))
    assert "secret" not in json.dumps(error.as_dict())


@pytest.mark.parametrize(
    "change,code",
    [
        ({"verified": True}, "UNKNOWN_FIELD"),
        ({"schema_version": "old"}, "UNSUPPORTED_BUNDLE_SCHEMA"),
        ({"signal_basis": "invented"}, "INVALID_WIRE_VALUE"),
        ({"asof": None}, "INVALID_WIRE_VALUE"),
        ({"asof": "not a time"}, "INVALID_WIRE_VALUE"),
    ],
)
def test_wire_cannot_add_verified_claim_or_use_old_schema(change, code):
    fails(code, lambda: ResearchRequest.from_dict(json.loads(_canonical(request())) | change))


def test_wire_missing_policy_or_object_type_rejects():
    wire = json.loads(_canonical(request()))
    wire.pop("macro_enabled")
    fails("MISSING_FIELD", lambda: ResearchRequest.from_dict(wire))
    fails("INVALID_TYPE", lambda: ResearchRequest.from_dict([]))


def test_raw_signal_declaration_requires_separate_matching_units_and_no_adjustment():
    valid = request(
        signal_basis=PriceBasis.RAW,
        signal_share_unit=ShareUnit.AS_TRADED,
        signal_volume_unit=VolumeUnit.AS_TRADED_SHARES,
        adjustment_version=None,
    )
    assert valid.signal_product_id != valid.raw_product_id
    for change in (
        {"signal_share_unit": ShareUnit.SPLIT_ADJUSTED},
        {"signal_volume_unit": VolumeUnit.SPLIT_ADJUSTED_SHARES},
        {"adjustment_version": "adjusted"},
        {"volume_policy": VolumePolicy.MULTIPLY_SPLIT_FACTOR},
    ):
        fails("UNIT_MISMATCH", lambda change=change: replace(valid, **change))


@pytest.mark.parametrize("wire", [False, True], ids=["constructor", "wire"])
@pytest.mark.parametrize(
    "basis,policy,volume_unit,code,field",
    [
        (
            PriceBasis.SPLIT_ADJUSTED,
            VolumePolicy.AS_TRADED,
            VolumeUnit.AS_TRADED_SHARES,
            None,
            None,
        ),
        (
            PriceBasis.SPLIT_ADJUSTED,
            VolumePolicy.MULTIPLY_SPLIT_FACTOR,
            VolumeUnit.SPLIT_ADJUSTED_SHARES,
            None,
            None,
        ),
        (
            PriceBasis.SPLIT_ADJUSTED,
            VolumePolicy.AS_TRADED,
            VolumeUnit.SPLIT_ADJUSTED_SHARES,
            "VOLUME_UNIT_CONFLICT",
            "signal_volume_unit",
        ),
        (
            PriceBasis.SPLIT_ADJUSTED,
            VolumePolicy.MULTIPLY_SPLIT_FACTOR,
            VolumeUnit.AS_TRADED_SHARES,
            "VOLUME_UNIT_CONFLICT",
            "signal_volume_unit",
        ),
        (PriceBasis.RAW, VolumePolicy.AS_TRADED, VolumeUnit.AS_TRADED_SHARES, None, None),
        (
            PriceBasis.RAW,
            VolumePolicy.AS_TRADED,
            VolumeUnit.SPLIT_ADJUSTED_SHARES,
            "UNIT_MISMATCH",
            "raw_signal",
        ),
        (
            PriceBasis.RAW,
            VolumePolicy.MULTIPLY_SPLIT_FACTOR,
            VolumeUnit.AS_TRADED_SHARES,
            "UNIT_MISMATCH",
            "raw_signal",
        ),
        (
            PriceBasis.RAW,
            VolumePolicy.MULTIPLY_SPLIT_FACTOR,
            VolumeUnit.SPLIT_ADJUSTED_SHARES,
            "UNIT_MISMATCH",
            "raw_signal",
        ),
        *[
            (PriceBasis.TOTAL_RETURN, policy, volume_unit, "UNIT_MISMATCH", "signal_basis")
            for policy in (VolumePolicy.AS_TRADED, VolumePolicy.MULTIPLY_SPLIT_FACTOR)
            for volume_unit in (VolumeUnit.AS_TRADED_SHARES, VolumeUnit.SPLIT_ADJUSTED_SHARES)
        ],
    ],
)
def test_signal_basis_policy_and_volume_matrix(basis, policy, volume_unit, code, field, wire):
    changes = {
        "signal_basis": basis,
        "signal_share_unit": ShareUnit.AS_TRADED
        if basis is PriceBasis.RAW
        else ShareUnit.SPLIT_ADJUSTED,
        "signal_volume_unit": volume_unit,
        "adjustment_version": None if basis is PriceBasis.RAW else "synthetic-split-v1",
        "volume_policy": policy,
    }

    def construct():
        if wire:
            wire_request = json.loads(_canonical(request())) | changes
            return ResearchRequest.from_dict(json.loads(json.dumps(wire_request)))
        return request(**changes)

    if code is not None:
        error = fails(code, construct, "contract")
        assert error.field == field
        return
    declaration = construct()
    assert declaration.signal_basis is basis
    assert declaration.signal_volume_unit is volume_unit
    assert declaration.volume_policy is policy
    frozen = snapshot(req=declaration)
    decision = contract_decision(
        frozen, ResearchMode.SYNTHETIC_TEST, reviewed_root_sha256=frozen.expected_root_sha256
    )
    assert decision.status is VerificationStatus.SYNTHETIC_CONTRACT_PASS
    assert decision.may_build_engine is False


@pytest.mark.parametrize(
    "change,code",
    [
        ({"ingested_at": datetime(2026, 10, 20)}, "NAIVE_TIMESTAMP"),
        ({"historically_known_at": at("2026-10-21")}, "TIME_ORDER"),
        ({"published_at": at("2026-10-03")}, "TIME_ORDER"),
        ({"provider_available_at": at("2026-09-29")}, "TIME_ORDER"),
    ],
)
def test_clock_order_and_timezone_rejects(change, code):
    fails(code, lambda: clocks(**change))


def test_unknown_knowledge_remains_unknown_never_ingestion():
    unknown = clocks(historically_known_at=None, published_at=None, provider_available_at=None)
    assert unknown.historically_known_at is None and unknown.ingested_at is not None
    p = snapshot(sources=tuple(evidence(role, clocks=unknown) for role in EvidenceRole))
    audit = data_provenance(
        p, ResearchMode.SYNTHETIC_TEST, reviewed_root_sha256=p.expected_root_sha256
    )
    assert all(v["clocks"]["historically_known_at"] is None for v in audit["source_roles"])
    assert not audit["domain_validation_performed"] and not audit["PIT_verified"]


@pytest.mark.parametrize(
    "change,code",
    [
        ({"adapter": ""}, "INVALID_VALUE"),
        ({"code_revision": "branch-name"}, "INVALID_CODE_REVISION"),
        ({"contract": "old"}, "UNSUPPORTED_CONTRACT_VERSION"),
    ],
)
def test_version_contract_is_explicit(change, code):
    fails(code, lambda: versions(**change))


@pytest.mark.parametrize(
    "change,code",
    [
        ({"role": "raw"}, "INVALID_TYPE"),
        ({"clocks": None}, "INVALID_TYPE"),
        ({"source_ref": " private "}, "INVALID_VALUE"),
        ({"version": True}, "INVALID_VALUE"),
        ({"version": 257}, "VERSION_BOUNDS"),
        ({"content": bytearray(b"secret")}, "CONTENT_BOUNDS_OR_TYPE"),
        ({"content": b""}, "CONTENT_BOUNDS_OR_TYPE"),
        ({"sha256": "0" * 64}, "CONTENT_HASH_MISMATCH"),
        ({"sha256": None}, "INVALID_DIGEST"),
        ({"event_ids": ["one"]}, "INVALID_TYPE"),
        ({"event_ids": (1,)}, "INVALID_TYPE"),
        ({"event_ids": ("",)}, "INVALID_VALUE"),
        ({"event_ids": ("one", "one")}, "DUPLICATE_BINDING"),
        ({"window_end": at("2026-09-30")}, "INVALID_INTERVAL"),
        ({"window_start": "2026-10-01"}, "INVALID_TYPE"),
        ({"schema_version": "old"}, "UNSUPPORTED_BUNDLE_SCHEMA"),
    ],
)
def test_bad_evidence_is_not_a_bound_source(change, code):
    fails(code, lambda: evidence(**change))


@pytest.mark.parametrize("role", list(EvidenceRole))
def test_every_role_content_replacement_rejects_even_after_updating_leaf_hash(role):
    original = snapshot()
    content = b"replaced-private-body"
    changed = tuple(
        replace(v, content=content, sha256=hashlib.sha256(content).hexdigest())
        if v.role is role
        else v
        for v in original.sources
    )
    fails(
        "SOURCE_ROOT_MISMATCH",
        lambda: snapshot(sources=changed, root=original.expected_root_sha256),
        "binding",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_ref", "synthetic:new-source"),
        ("provider", "different-provider"),
        ("dataset_id", "different-dataset"),
        ("dataset_revision", "different-revision"),
        ("partition_id", "different-partition"),
        ("version", 2),
        ("event_ids", ("different-event",)),
        ("window_end", at("2026-11-02")),
        ("clocks", clocks(historically_known_at=at("2026-10-03"))),
    ],
)
def test_metadata_is_bound_along_with_bytes(field, value):
    original = snapshot()
    changed = (replace(original.sources[0], **{field: value}), *original.sources[1:])
    fails(
        "SOURCE_ROOT_MISMATCH",
        lambda: snapshot(sources=changed, root=original.expected_root_sha256),
    )


def test_updated_entire_local_root_cannot_replace_external_reviewed_root():
    original = snapshot()
    changed = snapshot(req=replace(original.request, asof=at("2026-10-13")))
    fails(
        "SOURCE_ROOT_MISMATCH",
        lambda: changed.assert_bound(original.expected_root_sha256),
        "binding",
    )
    # A caller can calculate new declaration hashes, but obtains no real authority.
    real = snapshot(req=request(input_kind="real"))
    for mode in (ResearchMode.VERIFIED_RESEARCH, ResearchMode.SYNTHETIC_TEST):
        decision = contract_decision(real, mode, reviewed_root_sha256=real.expected_root_sha256)
        assert decision.status is VerificationStatus.REAL_EVIDENCE_PENDING
        assert not decision.may_validate_synthetic and not decision.may_build_engine


@pytest.mark.parametrize("feature", ["macro_enabled", "dividend_tax_enabled", "archive_present"])
def test_enabled_feature_roles_cannot_be_silently_missing(feature):
    req = request(**{feature: False})
    p = snapshot(req=req)
    upgraded = replace(req, **{feature: True})
    fails("MISSING_EVIDENCE_ROLE", lambda: snapshot(req=upgraded, sources=p.sources))


def test_explicit_disabled_features_require_no_invented_optional_evidence():
    req = request(macro_enabled=False, dividend_tax_enabled=False, archive_present=False)
    assert req.required_roles() == BASE_ROLES
    p = snapshot(req=req)
    assert len(p.sources) == len(BASE_ROLES)
    fails(
        "ARCHIVE_POLICY_MISMATCH",
        lambda: snapshot(req=req, sources=(*p.sources, evidence(EvidenceRole.L0_RESPONSE))),
    )


def test_source_graph_requires_all_dependencies_and_is_order_independent():
    p = snapshot()
    changed = tuple(
        replace(v, depends_on=("raw",)) if v.role is EvidenceRole.COMPARISON else v
        for v in p.sources
    )
    first = snapshot(sources=changed)
    assert first == snapshot(sources=tuple(reversed(changed)))
    assert [v.source_id for v in first.sources].index("raw") < [
        v.source_id for v in first.sources
    ].index("comparison")
    missing = tuple(
        replace(v, depends_on=("missing",)) if v.role is EvidenceRole.COMPARISON else v
        for v in p.sources
    )
    fails("MISSING_SOURCE_DEPENDENCY", lambda: snapshot(sources=missing))
    cycle = tuple(
        replace(v, depends_on=("raw",))
        if v.role is EvidenceRole.COMPARISON
        else replace(v, depends_on=("comparison",))
        if v.role is EvidenceRole.RAW
        else v
        for v in p.sources
    )
    fails("SOURCE_GRAPH_CYCLE", lambda: snapshot(sources=cycle))
    fails("DUPLICATE_SOURCE", lambda: snapshot(sources=(*p.sources, p.sources[0])))


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("request", None, "INVALID_TYPE"),
        ("versions", None, "INVALID_TYPE"),
        ("sources", [], "SOURCE_BOUNDS_OR_TYPE"),
        ("sources", (), "SOURCE_BOUNDS_OR_TYPE"),
        ("sources", (object(),), "SOURCE_BOUNDS_OR_TYPE"),
        ("expected_root_sha256", "bad", "INVALID_DIGEST"),
        ("schema_version", "old", "UNSUPPORTED_BUNDLE_SCHEMA"),
    ],
)
def test_snapshot_shape_and_schema_reject(field, value, code):
    p = snapshot()
    fails(code, lambda: replace(p, **{field: value}))


def test_security_source_owner_and_size_bounds_reject(monkeypatch):
    from quant_constraints.research.data import verification_contracts as c

    p = snapshot()
    changed = (replace(p.sources[0], security_id="other-security"), *p.sources[1:])
    fails("SOURCE_SECURITY_MISMATCH", lambda: snapshot(sources=changed))
    monkeypatch.setattr(c, "MAX_SOURCES", 1)
    fails("SOURCE_BOUNDS_OR_TYPE", lambda: replace(p))
    monkeypatch.setattr(c, "MAX_SOURCES", 128)
    monkeypatch.setattr(c, "MAX_SNAPSHOT_BYTES", 1)
    fails("SNAPSHOT_BOUNDS", lambda: replace(p))
    monkeypatch.setattr(c, "MAX_SOURCE_BYTES", 1)
    fails("CONTENT_BOUNDS_OR_TYPE", evidence)


def test_metadata_cardinality_and_reference_bounds_are_enforced():
    fails("METADATA_BOUNDS", lambda: request(symbol="a" * 257))
    fails("METADATA_BOUNDS", lambda: evidence(source_ref="a" * 2049))
    fails("METADATA_BOUNDS", lambda: evidence(event_ids=tuple(str(i) for i in range(129))))


def test_snapshot_copy_and_checkpoint_revalidate_original_bytes_and_no_aliases():
    req, pipeline = request(), versions()
    source_inputs = tuple(evidence(role) for role in EvidenceRole)
    p = snapshot(req, source_inputs, pipeline)
    old = copy.deepcopy(p)
    object.__setattr__(source_inputs[0], "content", b"changed")
    object.__setattr__(source_inputs[1].clocks, "ingested_at", at("2026-10-19"))
    object.__setattr__(req, "symbol", "changed")
    object.__setattr__(pipeline, "normalizer", "changed")
    assert p == old
    assert pickle.loads(pickle.dumps(p)) == p
    with pytest.raises(FrozenInstanceError):
        p.expected_root_sha256 = "0" * 64
    object.__setattr__(p.sources[0], "content", b"changed")
    fails("CONTENT_HASH_MISMATCH", lambda: p.assert_bound(old.expected_root_sha256))
    fails("CONTENT_HASH_MISMATCH", lambda: pickle.loads(pickle.dumps(p)))


def test_uninitialized_object_and_invalid_external_root_reject():
    p = snapshot()
    fails(
        "INVALID_OBJECT_STATE",
        lambda: contract_decision(
            object.__new__(FrozenDatasetInput),
            ResearchMode.SYNTHETIC_TEST,
            reviewed_root_sha256=p.expected_root_sha256,
        ),
    )
    fails(
        "INVALID_TYPE",
        lambda: contract_decision(
            object(), ResearchMode.SYNTHETIC_TEST, reviewed_root_sha256=p.expected_root_sha256
        ),
    )
    fails("INVALID_DIGEST", lambda: p.assert_bound(None))
    sources = (object.__new__(FrozenEvidence), *p.sources[1:])
    fails("INVALID_OBJECT_STATE", lambda: replace(p, sources=sources))


@pytest.mark.parametrize(
    "kind,mode,status,code,may_validate",
    [
        (
            "synthetic",
            ResearchMode.SYNTHETIC_TEST,
            VerificationStatus.SYNTHETIC_CONTRACT_PASS,
            "SCHEMA_BOUND_SYNTHETIC",
            True,
        ),
        (
            "synthetic",
            ResearchMode.VERIFIED_RESEARCH,
            VerificationStatus.REJECT,
            "SYNTHETIC_NOT_TRUSTED",
            False,
        ),
        ("synthetic", ResearchMode.LEGACY, VerificationStatus.REJECT, "LEGACY_UNVERIFIED", False),
        (
            "real",
            ResearchMode.SYNTHETIC_TEST,
            VerificationStatus.REAL_EVIDENCE_PENDING,
            "REAL_EVIDENCE_DEFERRED",
            False,
        ),
        (
            "real",
            ResearchMode.VERIFIED_RESEARCH,
            VerificationStatus.REAL_EVIDENCE_PENDING,
            "REAL_EVIDENCE_DEFERRED",
            False,
        ),
        ("real", ResearchMode.LEGACY, VerificationStatus.REJECT, "LEGACY_UNVERIFIED", False),
    ],
)
def test_authority_matrix_has_no_engine_or_real_trusted_state(
    kind, mode, status, code, may_validate
):
    p = snapshot(req=request(input_kind=kind))
    decision = contract_decision(p, mode, reviewed_root_sha256=p.expected_root_sha256)
    assert decision.status is status and decision.code == code
    assert decision.may_validate_synthetic is may_validate and not decision.may_build_engine
    assert decision.status is not VerificationStatus.TRUSTED_VERIFIED


def test_modes_and_fake_verified_tokens_never_become_credentials():
    p = snapshot()
    fails(
        "INVALID_MODE",
        lambda: contract_decision(p, "synthetic_test", reviewed_root_sha256=p.expected_root_sha256),
        "authority",
    )
    fails("TRUSTED_ENTRY_BLOCKED", lambda: VerifiedDatasetBundle(verified=True), "issuance")
    fake = object.__new__(VerifiedDatasetBundle)
    fails(
        "INVALID_TYPE",
        lambda: contract_decision(
            fake, ResearchMode.VERIFIED_RESEARCH, reviewed_root_sha256=p.expected_root_sha256
        ),
    )
    fails("TRUSTED_ENTRY_BLOCKED", lambda: pickle.dumps(fake), "restore")
    fails("TRUSTED_ENTRY_BLOCKED", lambda: copy.deepcopy(fake), "restore")


def test_provenance_is_deterministic_redacted_and_not_a_validation_certificate():
    p = snapshot()
    audit = data_provenance(
        p, ResearchMode.SYNTHETIC_TEST, reviewed_root_sha256=p.expected_root_sha256
    )
    repeat = data_provenance(
        copy.deepcopy(p), ResearchMode.SYNTHETIC_TEST, reviewed_root_sha256=p.expected_root_sha256
    )
    assert audit == repeat
    for field in (
        "raw_verified",
        "PIT_verified",
        "archive_ready",
        "trusted_entry",
        "coverage_verified",
        "domain_validation_performed",
        "may_build_engine",
    ):
        assert audit[field] is False
    assert audit["stage"] == "5E-1_schema_and_binding_only"
    assert audit["feature_policy"] == {
        "macro": "enabled",
        "dividend_tax": "enabled",
        "archive": "declared_present",
    }
    encoded = json.dumps(audit)
    for marker in ("private-fixture-body", "private-tax-identity", "private-ref-with-token"):
        assert marker not in encoded
    digest = audit.pop("audit_sha256")
    assert hashlib.sha256(_canonical(audit)).hexdigest() == digest
    audit["trusted_entry"] = True
    fails(
        "INVALID_TYPE",
        lambda: contract_decision(
            audit, ResearchMode.VERIFIED_RESEARCH, reviewed_root_sha256=p.expected_root_sha256
        ),
    )
    disabled = snapshot(
        req=request(macro_enabled=False, dividend_tax_enabled=False, archive_present=False)
    )
    assert set(
        data_provenance(
            disabled,
            ResearchMode.VERIFIED_RESEARCH,
            reviewed_root_sha256=disabled.expected_root_sha256,
        )["feature_policy"].values()
    ) == {"disabled", "absent"}
