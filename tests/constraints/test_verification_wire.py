"""Strict input transport rejects malformed values without exposing submitted content."""

import hashlib
import json
from dataclasses import replace

import pytest
from test_verification_orchestration import fixture, freeze, rebind, rejected, run

from quant_constraints.research.data.verification_contracts import (
    EvidenceRole,
    VerificationContractError,
)
from quant_constraints.research.data.verification_wire import (
    MAX_SOURCE_BYTES,
    decode_json,
    decode_records,
    encode_records,
)


def update_payload(frozen, role, mutate):
    sources = []
    for source in frozen.sources:
        if source.role is role:
            value = json.loads(source.content)
            mutate(value)
            content = json.dumps(
                value, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            source = replace(source, content=content, sha256=hashlib.sha256(content).hexdigest())
        sources.append(source)
    return rebind(frozen.request, tuple(sources))


@pytest.mark.parametrize(
    "content,code",
    [
        (b"\xff", "INVALID_WIRE_VALUE"),
        (b"{", "INVALID_WIRE_VALUE"),
        (b'{"secret":1,"secret":2}', "DUPLICATE_FIELD"),
        (b'{"x":NaN}', "INVALID_WIRE_VALUE"),
        (b"[" * 2000 + b"]" * 2000, "INVALID_WIRE_VALUE"),
        (b"", "SOURCE_BOUNDS"),
        (b"x" * (MAX_SOURCE_BYTES + 1), "SOURCE_BOUNDS"),
        ("not bytes", "SOURCE_BOUNDS"),
        (b"[" + b"0," * 12500 + b"0]", "SOURCE_BOUNDS"),
    ],
    ids=[
        "utf8",
        "syntax",
        "duplicate",
        "nan",
        "depth",
        "empty",
        "oversized",
        "type",
        "array-bounds",
    ],
)
def test_json_transport_is_bounded_and_rejects_duplicate_fields(content, code):
    with pytest.raises(VerificationContractError) as caught:
        decode_json(content)
    assert (
        caught.value.code == code
        and caught.value.field == "payload"
        and caught.value.phase == "decode"
    )
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "role,field,value,code",
    [
        (EvidenceRole.RAW, "private-apiKey-secret", True, "WIRE_FIELDS_MISMATCH"),
        (EvidenceRole.RAW, "open", True, "INVALID_WIRE_VALUE"),
        (EvidenceRole.RAW, "price_basis", 1, "INVALID_WIRE_VALUE"),
        (EvidenceRole.RAW, "price_basis", "invented", "INVALID_WIRE_VALUE"),
        (EvidenceRole.RAW, "bar_start_at", 4, "INVALID_WIRE_VALUE"),
        (EvidenceRole.RAW, "bar_end_at", "bad-time", "INVALID_WIRE_VALUE"),
        (EvidenceRole.RAW, "currency", 4, "INVALID_WIRE_VALUE"),
        (EvidenceRole.PIT_EVENTS, "document", ["hex"], "INVALID_WIRE_VALUE"),
        (EvidenceRole.PIT_EVENTS, "document", {"hex": True}, "INVALID_WIRE_VALUE"),
        (EvidenceRole.PIT_EVENTS, "document", {"hex": "AB"}, "INVALID_WIRE_VALUE"),
        (EvidenceRole.PIT_EVENTS, "document", {"hex": "0"}, "INVALID_WIRE_VALUE"),
        (EvidenceRole.PIT_EVENTS, "document", {"hex": "00" * 16385}, "INVALID_WIRE_VALUE"),
        (EvidenceRole.PIT_EVENTS, "published_at", 5, "INVALID_WIRE_VALUE"),
        (EvidenceRole.PIT_IDENTITIES, "version", True, "INVALID_WIRE_VALUE"),
        (EvidenceRole.PIT_COVERAGE, "action_kinds", "split", "INVALID_WIRE_VALUE"),
        (EvidenceRole.PIT_COVERAGE, "covered_from", False, "INVALID_WIRE_VALUE"),
        (EvidenceRole.MACRO_COVERAGE, "event_types", ["CPI", "CPI"], "INVALID_WIRE_VALUE"),
        (EvidenceRole.MACRO_COVERAGE, "event_types", "CPI", "INVALID_WIRE_VALUE"),
        (EvidenceRole.MACRO_COVERAGE, "point_in_time", 1, "INVALID_WIRE_VALUE"),
    ],
)
def test_existing_record_shapes_and_primitive_types_are_strict(role, field, value, code):
    req, records = fixture(macro=True, tax=True)
    frozen = freeze(req, records)
    changed = update_payload(frozen, role, lambda v: v["records"][0].update({field: value}))
    report = rejected(run(changed), code, "decode")
    assert "private-apiKey-secret" not in json.dumps(report)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v.update(schema_version="old"),
        lambda v: v.update(role="identities"),
        lambda v: v.update(records={}),
        lambda v: v.pop("schema_version"),
        lambda v: v["records"][0].pop("schema_version"),
    ],
)
def test_old_missing_or_conflicting_wire_schema_cannot_be_accepted(mutation):
    req, records = fixture()
    frozen = freeze(req, records)
    changed = update_payload(frozen, EvidenceRole.RAW, mutation)
    report = run(changed)
    assert report["code"] in {"UNSUPPORTED_PAYLOAD_SCHEMA", "WIRE_FIELDS_MISMATCH"}
    rejected(report, report["code"], "decode")


@pytest.mark.parametrize(
    "role,records",
    [
        (EvidenceRole.LICENSE, ()),
        (EvidenceRole.RAW, []),
        ("raw", ()),
        (EvidenceRole.RAW, (object(),)),
        (EvidenceRole.RAW, (None,)),
        (EvidenceRole.RAW, (1,) * 12501),
    ],
)
def test_encoder_never_accepts_live_unknown_or_unbounded_records(role, records):
    with pytest.raises(VerificationContractError) as caught:
        encode_records(role, records)
    assert caught.value.code == "INVALID_WIRE_VALUE"


def test_encoder_checks_unknown_nested_object_and_source_byte_bounds():
    _, records = fixture()
    definition = records[EvidenceRole.DEFINITION][0]
    with pytest.raises(VerificationContractError):
        encode_records(EvidenceRole.DEFINITION, (replace(definition, definition_ref=object()),))
    with pytest.raises(VerificationContractError) as caught:
        encode_records(
            EvidenceRole.DEFINITION, (replace(definition, definition_ref="x" * MAX_SOURCE_BYTES),)
        )
    assert caught.value.code == "SOURCE_BOUNDS"


def test_decode_returns_new_objects_and_keeps_original_document_bytes():
    _, records = fixture()
    original = records[EvidenceRole.PIT_EVENTS][0]
    restored = decode_records(
        EvidenceRole.PIT_EVENTS, encode_records(EvidenceRole.PIT_EVENTS, (original,))
    )[0]
    assert (
        restored == original and restored is not original and restored.record is not original.record
    )
    assert restored.document == original.document


@pytest.mark.parametrize("role", ["raw", EvidenceRole.LICENSE, None])
def test_decoder_does_not_use_arbitrary_role_or_class_declarations(role):
    with pytest.raises(VerificationContractError) as error:
        decode_records(role, b"{}")
    assert error.value.code == "UNSUPPORTED_PAYLOAD_SCHEMA"
