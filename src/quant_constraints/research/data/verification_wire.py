"""Bounded synthetic wire records; decode bytes, never caller-supplied live objects.

This is a transport for the existing 5A/C/D contracts, not a domain validator or
source authentication format. Documents retain their exact bytes as hexadecimal.
"""

from __future__ import annotations

import json
import types
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Never, get_args, get_origin, get_type_hints

from .contracts import (
    ContractError,
    CorporateActionRecord,
    RawBar,
    SecurityIdentity,
    SignalBar,
    SourceProvenance,
)
from .normalize import normalize_record
from .pit import PITCoverage, PITEventRevision, PITIdentityRevision
from .pit_supply import (
    MacroCoverageRevision,
    MacroScheduleRevision,
    QualificationRevision,
    TaxRuleRevision,
    TaxSourceRevision,
)
from .raw_validation import FactorDefinition, SplitFact
from .verification_contracts import MAX_SOURCE_BYTES, EvidenceRole, VerificationContractError, _deny

PAYLOAD_VERSION = "research_verification_payload_v1"
RECORD_TYPES = {
    EvidenceRole.RAW: RawBar,
    EvidenceRole.COMPARISON: SignalBar,
    EvidenceRole.ACTIONS: CorporateActionRecord,
    EvidenceRole.IDENTITIES: SecurityIdentity,
    EvidenceRole.FACTS: SplitFact,
    EvidenceRole.DEFINITION: FactorDefinition,
    EvidenceRole.PIT_EVENTS: PITEventRevision,
    EvidenceRole.PIT_IDENTITIES: PITIdentityRevision,
    EvidenceRole.PIT_COVERAGE: PITCoverage,
    EvidenceRole.MACRO_EVENTS: MacroScheduleRevision,
    EvidenceRole.MACRO_COVERAGE: MacroCoverageRevision,
    EvidenceRole.TAX_SOURCES: TaxSourceRevision,
    EvidenceRole.TAX_RULES: TaxRuleRevision,
    EvidenceRole.TAX_QUALIFICATIONS: QualificationRevision,
}
_CLASSES = frozenset((*RECORD_TYPES.values(), SourceProvenance))
_NORMALIZED = frozenset(
    {RawBar, SignalBar, CorporateActionRecord, SecurityIdentity, SourceProvenance}
)


def _bad() -> Never:
    _deny("INVALID_WIRE_VALUE", "payload", "decode")


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _deny("DUPLICATE_FIELD", "payload", "decode")
        result[key] = value
    return result


def decode_json(content: bytes) -> Any:
    if type(content) is not bytes or not 0 < len(content) <= MAX_SOURCE_BYTES:
        _deny("SOURCE_BOUNDS", "payload", "decode")
    try:
        result = json.loads(content, object_pairs_hook=_pairs, parse_constant=lambda _: _bad())
    except VerificationContractError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        _bad()
    stack = [(result, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > 12:
            _bad()
        if isinstance(value, dict | list) and len(value) > 12_500:
            _deny("SOURCE_BOUNDS", "payload", "decode")
        if isinstance(value, dict):
            stack.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            stack.extend((v, depth + 1) for v in value)
    return result


def _encode(value: Any) -> Any:
    if type(value) in _CLASSES:
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, StrEnum):
        return value.value
    if type(value) in (datetime, date):
        return value.isoformat()
    if type(value) is bytes:
        return {"hex": value.hex()}
    if type(value) is tuple:
        return [_encode(v) for v in value]
    if type(value) is frozenset:
        return sorted(_encode(v) for v in value)
    if value is None or type(value) in (str, bool, int, float):
        return value
    _bad()


def encode_records(role: EvidenceRole, records: tuple) -> bytes:
    """Explicit fixture transport, not freezing, validation or credential issuance."""
    if (
        type(role) is not EvidenceRole
        or role not in RECORD_TYPES
        or type(records) is not tuple
        or len(records) > 12_500
        or any(type(v) is not RECORD_TYPES[role] for v in records)
    ):
        _bad()
    result = json.dumps(
        {
            "schema_version": PAYLOAD_VERSION,
            "role": role.value,
            "records": [_encode(v) for v in records],
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    if len(result) > MAX_SOURCE_BYTES:
        _deny("SOURCE_BOUNDS", "payload", "decode")
    return result


def _decode(annotation: Any, value: Any, depth: int = 0) -> Any:
    if depth > 12:
        _bad()
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is types.UnionType:
        if value is None and type(None) in args:
            return None
        return _decode(next(a for a in args if a is not type(None)), value, depth + 1)
    if origin in (tuple, frozenset):
        if type(value) is not list or len(value) > 12_500:
            _bad()
        items = tuple(_decode(args[0], v, depth + 1) for v in value)
        if origin is frozenset:
            if len(set(items)) != len(items):
                _bad()
            return frozenset(items)
        return items
    if annotation in _CLASSES:
        names = {f.name for f in fields(annotation)}
        if type(value) is not dict or set(value) != names:
            _deny("WIRE_FIELDS_MISMATCH", "payload", "decode")
        hints = get_type_hints(annotation)
        values = {name: _decode(hints[name], v, depth + 1) for name, v in value.items()}
        if annotation in _NORMALIZED:
            return normalize_record(annotation, values)
        return annotation(**values)
    if annotation is bytes:
        if (
            type(value) is not dict
            or set(value) != {"hex"}
            or type(value["hex"]) is not str
            or len(value["hex"]) > 2 * 16_384
        ):
            _bad()
        result = bytes.fromhex(value["hex"])
        if result.hex() != value["hex"]:
            _bad()
        return result
    if annotation in (datetime, date):
        if type(value) is not str:
            _bad()
        return annotation.fromisoformat(value)
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        if type(value) is not str:
            _bad()
        return annotation(value)
    if annotation is float:
        if type(value) not in (int, float):
            _bad()
        return float(value)
    if type(value) is not annotation:
        _bad()
    return value


def decode_records(role: EvidenceRole, content: bytes) -> tuple:
    if type(role) is not EvidenceRole or role not in RECORD_TYPES:
        _deny("UNSUPPORTED_PAYLOAD_SCHEMA", "payload", "decode")
    payload = decode_json(content)
    if (
        type(payload) is not dict
        or set(payload) != {"schema_version", "role", "records"}
        or payload["schema_version"] != PAYLOAD_VERSION
        or payload["role"] != role.value
        or type(payload["records"]) is not list
        or len(payload["records"]) > 12_500
        or role not in RECORD_TYPES
    ):
        _deny("UNSUPPORTED_PAYLOAD_SCHEMA", "payload", "decode")
    try:
        return tuple(_decode(RECORD_TYPES[role], value) for value in payload["records"])
    except (VerificationContractError, ContractError):
        raise
    except (TypeError, ValueError, OverflowError, RecursionError):
        _bad()


def safe_field(field: str) -> str:
    """A malformed submitted field name must not leak through 5A diagnostics."""
    names = {f.name for cls in _CLASSES if is_dataclass(cls) for f in fields(cls)}
    names |= {
        "snapshot",
        "payload",
        "sources",
        "source_metadata",
        "dependencies",
        "window",
        "coverage",
        "identity",
        "input",
        "bars",
        "raw_signal",
        "fact.document",
        "code_revision",
        "macro",
        "tax",
        "archive",
        "manifest",
        "files",
        "receipt",
        "source_manifest",
        "action.security_id",
        "split_fact",
        "ratio",
        "volume",
        "price",
        "mode",
        "request",
        "reviewed_root_sha256",
        "root_sha256",
        "expected_root_sha256",
        "account_scenario_id",
        "tax_issuer_id",
        "window_start",
        "window_end",
    }
    return field if field in names else "input"
