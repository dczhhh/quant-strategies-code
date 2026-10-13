"""5F-0 uses invented review metadata, never real consent, prices or credentials."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from quant_constraints.research.data import permission as gate

AS_OF = datetime(2026, 10, 12, tzinfo=UTC)
TRUST_FLAGS = (
    "may_acquire",
    "raw_verified",
    "PIT_verified",
    "archive_ready",
    "trusted_entry",
    "may_build_engine",
)


def wire(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def fixture(split=True, daily=True):
    """Fictional authorizations and references, solely for offline branch exercise."""
    permission = {
        "schema_version": gate.PERMISSION_SCHEMA,
        "permission_id": "synthetic_permission",
        "revision": 1,
        "provider": "massive",
        "product": "us_stocks",
        "subscription_selected": "stocks_starter",
        "credential_upgrade": "user_reported",
        "entitlement_verified": "documented",
        "license_verified": "documented",
        "license_state": "licensed",
        "terms_version": "synthetic_terms_v1",
        "effective_at": "2026-01-01T00:00:00+00:00",
        "expires_at": "2027-01-01T00:00:00+00:00",
        "reviewed_at": "2026-10-11T00:00:00+00:00",
        "contact_ref": "synthetic_contact",
        "open_questions": [],
        "endpoints": ["minute_aggregates", "day_aggregates", "reference", "corporate_actions"],
        "history_start": "2021-10-01T00:00:00+00:00",
        "history_end": "2026-10-11T00:00:00+00:00",
        "delay_seconds": 900,
        "sample_ids": ["synthetic_slice"],
        "security_ids": ["synthetic_security"],
        "rights": {field.name: "allowed" for field in gate.fields(gate.UseRights)},
        "storage": {
            "root_ref": "synthetic_private_root",
            "location": "local_private",
            "retention_days": 30,
            "acl": "documented",
            "deletion_policy": "documented",
        },
    }
    products = [
        {
            "interval": interval,
            "adjusted": adjusted,
            "price_basis": "split_adjusted" if adjusted else "raw_execution",
            "session": "rth" if interval == "1m" else "vendor_daily",
            "price_unit": "usd_per_share",
            "share_unit": "split_adjusted_shares" if adjusted else "raw_shares",
            # Explicit comparison-volume policy; no inferred factor multiplication.
            "volume_unit": "raw_shares",
            "volume_policy": "documented",
        }
        for interval in (("1m", "1d") if daily else ("1m",))
        for adjusted in (False, True)
    ]
    endpoints = ["minute_aggregates", "reference", "corporate_actions"]
    if daily:
        endpoints.append("day_aggregates")
    sample = {
        "schema_version": gate.SAMPLE_SCHEMA,
        "sample_id": "synthetic_slice",
        "provider": "massive",
        "product": "us_stocks",
        "subscription_selected": "stocks_starter",
        "symbol": "AAA",
        "security_id": "synthetic_security",
        "identity": "documented",
        "calendar": "NYSE",
        "timezone": "America/New_York",
        "start_at": "2026-10-09T00:00:00+00:00",
        "end_at": "2026-10-10T00:00:00+00:00",
        "source_market": "us_consolidated",
        "execution_venue": "unresolved_smart",
        "products": products,
        "endpoints": endpoints,
        "clocks": {field.name: "documented" for field in gate.fields(gate.ClockPlan)},
        "corrections": "documented",
        "split_sample": split,
        "independent_factor": "documented",
        "private_cloud": False,
        "ci": False,
        "redistribution": False,
        "post_termination_retention": False,
        "storage_root_ref": "synthetic_private_root",
        "max_requests": 8,
        "max_source_bytes": 524288,
        "max_total_bytes": 4194304,
    }
    return permission, sample


def register(permission, sample):
    authorities = {
        "account": "provider_account",
        "license": "vendor_written",
        "user_scope": "user_written",
        "identity": "historical_source",
        "clocks": "historical_source",
    }
    if sample["split_sample"]:
        authorities["factor"] = "issuer"
    return {
        "schema_version": gate.EVIDENCE_SCHEMA,
        "permission_sha256": sha(wire(permission)),
        "sample_sha256": sha(wire(sample)),
        "records": [
            {
                "role": role,
                "authority": authority,
                "document_ref": "synthetic_" + role,
                "sample_id": sample["sample_id"],
                "permission_id": permission["permission_id"],
                "revision": permission["revision"],
                "terms_version": permission["terms_version"],
                "reviewed_at": permission["reviewed_at"],
            }
            for role, authority in authorities.items()
        ],
    }


def evaluate(permission, sample, evidence=None, **changes):
    evidence = register(permission, sample) if evidence is None else evidence
    arguments = {
        "review_bytes": wire(evidence),
        "reviewed_evidence_sha256": sha(wire(evidence)),
        "as_of": AS_OF,
    }
    arguments.update(changes)
    result = gate.evaluate_permission_preparation(wire(permission), wire(sample), **arguments)
    assert all(result[field] is False for field in TRUST_FLAGS)
    assert result["license_determination"] == "not_performed"
    assert result["real_status"] == "preparation_only"
    assert result["real_reason"] == "entitlement_and_license_scope_unverified"
    return result


def change(document, path, value):
    parts = path.split(".")
    parent = document
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    parent[int(parts[-1]) if isinstance(parent, list) else parts[-1]] = value


@pytest.mark.parametrize("split,daily", [(True, True), (False, True), (False, False)])
def test_positive_metadata_pass_is_deterministic_and_never_a_trust_credential(split, daily):
    permission, sample = fixture(split, daily)
    before = deepcopy((permission, sample))
    result = evaluate(permission, sample)
    assert result["result"] == "pass" and result["code"] == "OFFLINE_CONTRACT_PASS"
    assert evaluate(permission, sample) == result and (permission, sample) == before
    from quant_constraints.research.data.entry import build_research_engine
    from quant_constraints.research.data.verification_contracts import VerificationContractError

    with pytest.raises(VerificationContractError, match="INVALID_BUNDLE"):
        build_research_engine(result)
    forged = {**result, "trusted_entry": True, "may_build_engine": True, "approved": True}
    with pytest.raises(VerificationContractError, match="INVALID_BUNDLE"):
        build_research_engine(forged)


@pytest.mark.parametrize("state", ["unknown", "pending", "expired", "revoked", "conflicting"])
def test_license_state_machine_fails_closed(state):
    permission, sample = fixture()
    permission["license_state"] = state
    assert evaluate(permission, sample)["code"] == "LICENSE_" + state.upper()


@pytest.mark.parametrize("field", ["entitlement_verified", "license_verified"])
def test_paid_user_report_never_replaces_independent_documents(field):
    permission, sample = fixture()
    permission[field] = "pending"
    assert evaluate(permission, sample)["code"] == field.upper() + "_PENDING"


@pytest.mark.parametrize(
    "path,value,code",
    [
        ("expires_at", "2026-10-12T00:00:00+00:00", "LICENSE_EXPIRED"),
        ("effective_at", "2026-10-13T00:00:00+00:00", "LICENSE_TIME_CONFLICT"),
        ("reviewed_at", "2026-10-13T00:00:00+00:00", "LICENSE_TIME_CONFLICT"),
        ("effective_at", None, "LICENSE_CLOCKS_PENDING"),
        ("expires_at", None, "LICENSE_CLOCKS_PENDING"),
        ("reviewed_at", None, "LICENSE_CLOCKS_PENDING"),
        ("open_questions", ["non_display_unresolved"], "PERMISSION_REVIEW_PENDING"),
        ("revision", 0, "PERMISSION_REVIEW_PENDING"),
        ("endpoints", ["reference"], "ENDPOINT_SCOPE_MISSING"),
        ("history_start", None, "HISTORY_SCOPE_MISMATCH"),
        ("history_end", None, "HISTORY_SCOPE_MISMATCH"),
        ("history_start", "2026-10-10T00:00:00+00:00", "HISTORY_SCOPE_MISMATCH"),
        ("history_end", "2026-10-09T00:00:00+00:00", "HISTORY_SCOPE_MISMATCH"),
        ("delay_seconds", 0, "STARTER_DELAY_CONFLICT"),
        ("sample_ids", ["other_sample"], "SAMPLE_SCOPE_MISMATCH"),
        ("security_ids", ["other_security"], "SAMPLE_SCOPE_MISMATCH"),
        ("storage.root_ref", "other_private_root", "STORAGE_SCOPE_MISMATCH"),
        ("storage.acl", "pending", "STORAGE_SCOPE_MISMATCH"),
        ("storage.deletion_policy", "pending", "STORAGE_SCOPE_MISMATCH"),
        ("storage.retention_days", 0, "STORAGE_SCOPE_MISMATCH"),
        ("storage.retention_days", 3651, "STORAGE_SCOPE_MISMATCH"),
        ("storage.location", "private_cloud", "STORAGE_SCOPE_MISMATCH"),
    ],
)
def test_permission_scope_and_time_boundaries(path, value, code):
    permission, sample = fixture()
    change(permission, path, value)
    assert evaluate(permission, sample)["code"] == code


@pytest.mark.parametrize(
    "path,value,code",
    [
        ("identity", "pending", "IDENTITY_PENDING"),
        ("security_id", None, "SAMPLE_SCOPE_MISMATCH"),
        ("symbol", "aapl", "INVALID_VALUE"),
        ("start_at", "2026-10-01T00:00:00+00:00", "WINDOW_BOUNDS"),
        ("end_at", "2026-10-09T00:00:00+00:00", "WINDOW_BOUNDS"),
        ("end_at", "2026-10-13T00:00:00+00:00", "WINDOW_BOUNDS"),
        ("products", [], "RAW_COMPARISON_SCOPE_MISSING"),
        ("products.0.adjusted", True, "PRODUCT_UNITS_CONFLICT"),
        ("products.0.share_unit", "split_adjusted_shares", "PRODUCT_UNITS_CONFLICT"),
        ("products.0.session", "vendor_daily", "PRODUCT_UNITS_CONFLICT"),
        ("products.0.volume_unit", "unknown", "PRODUCT_UNITS_CONFLICT"),
        ("products.0.volume_policy", "pending", "PRODUCT_UNITS_CONFLICT"),
        ("endpoints", ["reference"], "ENDPOINT_SCOPE_MISSING"),
        ("corrections", "pending", "CORRECTIONS_PENDING"),
        ("independent_factor", "pending", "INDEPENDENT_FACTOR_PENDING"),
        ("max_requests", 0, "REQUEST_BUDGET_INVALID"),
        ("max_requests", 17, "REQUEST_BUDGET_INVALID"),
        ("max_source_bytes", 0, "REQUEST_BUDGET_INVALID"),
        ("max_source_bytes", 2097153, "REQUEST_BUDGET_INVALID"),
        ("max_total_bytes", 1, "REQUEST_BUDGET_INVALID"),
    ],
)
def test_sample_products_and_budget_reject(path, value, code):
    permission, sample = fixture()
    change(sample, path, value)
    assert evaluate(permission, sample)["code"] == code


@pytest.mark.parametrize(
    "field", ["effective", "published", "historically_known", "provider_available", "ingested"]
)
def test_unknown_pit_clock_is_not_backfilled(field):
    permission, sample = fixture()
    sample["clocks"][field] = "pending"
    assert evaluate(permission, sample)["code"] == "PIT_CLOCKS_PENDING"


@pytest.mark.parametrize("right", [field.name for field in gate.fields(gate.UseRights)])
@pytest.mark.parametrize("state", ["unknown", "denied"])
def test_each_requested_use_must_have_a_separate_declaration(right, state):
    permission, sample = fixture()
    if right in ("private_cloud", "ci", "redistribution", "post_termination_retention"):
        sample[right] = True
    permission["rights"][right] = state
    assert evaluate(permission, sample)["code"] == "USE_SCOPE_DENIED_OR_UNKNOWN"


def test_unrequested_uses_do_not_gain_permission_and_cloud_acl_still_needed():
    permission, sample = fixture()
    for field in ("ci", "private_cloud", "redistribution", "post_termination_retention"):
        permission["rights"][field] = "denied"
    assert evaluate(permission, sample)["result"] == "pass"
    permission["rights"]["private_cloud"] = "allowed"
    sample["private_cloud"] = True
    assert evaluate(permission, sample)["code"] == "STORAGE_SCOPE_MISMATCH"
    permission["storage"]["location"] = "private_cloud"
    assert evaluate(permission, sample)["result"] == "pass"


@pytest.mark.parametrize(
    "path,value,code",
    [
        ("records", [], "REVIEW_ROLES_MISSING"),
        ("records.0.authority", "user_written", "REVIEW_SOURCE_CONFLICT"),
        ("records.0.document_ref", "synthetic_license", "REVIEW_SOURCE_CONFLICT"),
        ("records.0.sample_id", "other_sample", "REVIEW_SCOPE_VERSION_MISMATCH"),
        ("records.0.permission_id", "other_permission", "REVIEW_SCOPE_VERSION_MISMATCH"),
        ("records.0.revision", 2, "REVIEW_SCOPE_VERSION_MISMATCH"),
        ("records.0.terms_version", "synthetic_terms_v2", "REVIEW_SCOPE_VERSION_MISMATCH"),
        ("records.0.reviewed_at", "2026-10-10T00:00:00+00:00", "REVIEW_TIME_CONFLICT"),
        ("permission_sha256", "a" * 64, "DECLARATION_BINDING_MISMATCH"),
        ("sample_sha256", "a" * 64, "DECLARATION_BINDING_MISMATCH"),
    ],
)
def test_review_register_roles_independence_scope_version_and_clocks(path, value, code):
    permission, sample = fixture()
    evidence = register(permission, sample)
    change(evidence, path, value)
    assert evaluate(permission, sample, evidence)["code"] == code


def test_self_reported_hash_and_mutated_rehashed_inputs_do_not_replace_reviewed_root():
    permission, sample = fixture()
    evidence = register(permission, sample)
    root = sha(wire(evidence))
    for value in (None, "approved", "A" * 64):
        assert (
            evaluate(permission, sample, evidence, reviewed_evidence_sha256=value)["code"]
            == "INDEPENDENT_REVIEW_REQUIRED"
        )
    permission["storage"]["retention_days"] = 31
    assert evaluate(permission, sample, evidence)["code"] == "DECLARATION_BINDING_MISMATCH"
    rehashed = register(permission, sample)
    assert (
        evaluate(permission, sample, rehashed, reviewed_evidence_sha256=root)["code"]
        == "REVIEW_ROOT_MISMATCH"
    )
    permission["approved"] = True
    assert evaluate(permission, sample)["code"] == "FIELDS_MISMATCH"


def test_missing_pair_duplicate_product_or_duplicate_role_cannot_substitute_evidence():
    permission, sample = fixture()
    sample["products"].pop()
    assert evaluate(permission, sample)["code"] == "RAW_COMPARISON_SCOPE_MISSING"
    sample["products"].append({**sample["products"][0], "volume_unit": "split_adjusted_shares"})
    assert evaluate(permission, sample)["code"] == "DUPLICATE_PRODUCT"
    permission, sample = fixture()
    evidence = register(permission, sample)
    evidence["records"][0]["role"] = "license"
    assert evaluate(permission, sample, evidence)["code"] == "REVIEW_ROLES_MISSING"


@pytest.mark.parametrize(
    "target,path,value,code",
    [
        ("permission", "schema_version", "research_source_permission_v0", "UNSUPPORTED_SCHEMA"),
        ("sample", "schema_version", "research_sample_plan_v2", "UNSUPPORTED_SCHEMA"),
        ("review", "schema_version", "research_permission_review_v0", "UNSUPPORTED_SCHEMA"),
        ("permission", "revision", True, "INVALID_TYPE"),
        ("permission", "revision", -1, "METADATA_BOUNDS"),
        ("sample", "max_total_bytes", 16777217, "METADATA_BOUNDS"),
        ("sample", "provider", "other_vendor", "INVALID_VALUE"),
        ("sample", "source_market", "XNAS", "INVALID_VALUE"),
        ("sample", "execution_venue", "us_consolidated", "INVALID_VALUE"),
        ("permission", "endpoints", ["quotes"], "INVALID_VALUE"),
        ("permission", "endpoints", ["reference", "reference"], "DUPLICATE_SCOPE"),
        ("permission", "sample_ids", "synthetic_slice", "METADATA_BOUNDS"),
        ("permission", "sample_ids", ["sample_" + str(i) for i in range(17)], "METADATA_BOUNDS"),
        ("permission", "effective_at", "2026-01-01", "INVALID_TIME"),
        ("permission", "effective_at", "2026-01-01T00:00:00-04:00", "INVALID_TIME"),
        ("permission", "effective_at", "not_a_time", "INVALID_TIME"),
        ("permission", "effective_at", 1, "INVALID_TIME"),
        ("permission", "effective_at", "x" * 41, "INVALID_TIME"),
        ("sample", "storage_root_ref", "../private/apiKey=DO_NOT_PRINT", "UNSAFE_METADATA"),
        (
            "review",
            "records.0.document_ref",
            "https://private/?token=DO_NOT_PRINT",
            "UNSAFE_METADATA",
        ),
        ("permission", "contact_ref", "secret\nDO_NOT_PRINT", "UNSAFE_METADATA"),
        ("review", "sample_sha256", "DO_NOT_PRINT", "UNSAFE_METADATA"),
    ],
)
def test_strict_wire_schema_source_and_secret_boundaries(target, path, value, code, capsys):
    permission, sample = fixture()
    evidence = register(permission, sample)
    change({"permission": permission, "sample": sample, "review": evidence}[target], path, value)
    result = evaluate(permission, sample, evidence)
    assert result["code"] == code
    assert "DO_NOT_PRINT" not in wire(result).decode()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "content,code",
    [
        (b"", "METADATA_BOUNDS"),
        (b" " * 32769, "METADATA_BOUNDS"),
        (b"{", "INVALID_JSON"),
        (b"\xff", "INVALID_JSON"),
        (b'{"schema_version":1,"schema_version":2}', "INVALID_JSON"),
        (b'{"secret":NaN}', "INVALID_JSON"),
        (b"[]", "FIELDS_MISMATCH"),
        ("DO_NOT_PRINT", "METADATA_BOUNDS"),
        (None, "METADATA_BOUNDS"),
        (b"[" * 14 + b"0" + b"]" * 14, "INVALID_JSON"),
    ],
    # PYTEST_CURRENT_TEST must fit Windows' 32767-character environment limit.
    # Keep the oversize payload intact while keeping case IDs bounded/redacted.
    ids=[
        "empty",
        "oversize",
        "broken_json",
        "invalid_utf8",
        "duplicate_fields",
        "nonfinite",
        "wrong_object",
        "wrong_type",
        "null",
        "deep_nesting",
    ],
)
def test_bounded_json_and_hostile_object_inputs_only_emit_fixed_diagnostics(content, code):
    result = gate.evaluate_permission_preparation(
        content, b"{}", review_bytes=b"{}", reviewed_evidence_sha256=None, as_of=AS_OF
    )
    assert result["code"] == code and all(result[field] is False for field in TRUST_FLAGS)
    assert "DO_NOT_PRINT" not in wire(result).decode()


@pytest.mark.parametrize("as_of", [None, "2026-10-12", datetime(2026, 10, 12)])
def test_clock_argument_is_explicit_and_utc(as_of):
    permission, sample = fixture()
    assert evaluate(permission, sample, as_of=as_of)["code"] == "INVALID_TIME"


def test_no_files_credentials_network_or_real_issuer_are_used(monkeypatch):
    permission, sample = fixture()
    import os
    import socket

    from quant_constraints.research.data import verification

    def forbidden(*args, **kwargs):
        raise AssertionError("5F-0 must remain pure metadata")

    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(verification, "verify_dataset", forbidden)
    assert evaluate(permission, sample)["result"] == "pass"


@pytest.mark.parametrize("target", ["permission", "sample", "review"])
@pytest.mark.parametrize("field", ["approved", "manifest", "apiKey", "/private/DO_NOT_PRINT"])
def test_submitted_approval_manifest_or_secret_fields_are_not_echoed(target, field):
    permission, sample = fixture()
    evidence = register(permission, sample)
    {"permission": permission, "sample": sample, "review": evidence}[target][field] = "DO_NOT_PRINT"
    result = evaluate(permission, sample, evidence)
    assert result["code"] == "FIELDS_MISMATCH" and "DO_NOT_PRINT" not in wire(result).decode()


def test_actual_preparation_templates_keep_starter_selection_and_all_pending_rights():
    root = Path(__file__).resolve().parents[2] / "docs/examples"
    content = (root / "research-source-permission-v1.json").read_bytes()
    declaration = gate.decode_permission_declaration(content)
    assert declaration.subscription_selected == "stocks_starter"
    assert declaration.credential_upgrade == "user_reported"
    assert declaration.entitlement_verified == declaration.license_verified == "pending"
    assert declaration.license_state == "unknown"
    assert all(
        getattr(declaration.rights, field.name) == "unknown"
        for field in gate.fields(gate.UseRights)
    )
    assert declaration.endpoints == declaration.security_ids == declaration.sample_ids == ()
    for symbol in ("aapl", "nflx"):
        sample_bytes = (root / f"research-sample-{symbol}-v1.json").read_bytes()
        sample = gate.decode_sample_plan(sample_bytes)
        assert sample.symbol == symbol.upper() and sample.security_id is None
        assert sample.identity == "pending"
        result = gate.evaluate_permission_preparation(
            content, sample_bytes, review_bytes=b"{}", reviewed_evidence_sha256=None, as_of=AS_OF
        )
        assert result["code"] == "LICENSE_UNKNOWN"
        assert all(result[field] is False for field in TRUST_FLAGS)
