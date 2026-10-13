"""5F-0 offline metadata completeness checks; never licensing or real admission.

Only bounded JSON bytes enter this gate. An independently held review-register
digest binds declarations, not source authenticity. Nothing here opens a path,
reads credentials, calls a provider, or produces a dataset/Engine credential.
"""

from __future__ import annotations

import hashlib
import re
import types
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Never, cast, get_args, get_origin, get_type_hints

from .verification_contracts import VerificationContractError
from .verification_wire import decode_json

PERMISSION_SCHEMA = "research_source_permission_v1"
SAMPLE_SCHEMA = "research_sample_plan_v1"
EVIDENCE_SCHEMA = "research_permission_review_v1"
AUDIT_SCHEMA = "research_permission_preparation_audit_v1"
MAX_METADATA_BYTES = 32_768
State = Literal["pending", "documented"]
Right = Literal["unknown", "allowed", "denied"]
Endpoint = Literal["minute_aggregates", "day_aggregates", "reference", "corporate_actions"]


class PermissionPreparationError(ValueError):
    def __init__(self, code: str, field: str):
        self.code, self.field = code, field
        super().__init__(f"{code}:{field}")


def _deny(code: str, field: str) -> Never:
    raise PermissionPreparationError(code, field) from None


@dataclass(frozen=True, slots=True)
class UseRights:
    personal_algorithmic_backtest: Right
    non_display: Right
    raw_response: Right
    l0: Right
    l1: Right
    parquet: Right
    private_storage: Right
    private_cloud: Right
    ci: Right
    derived_data: Right
    redistribution: Right
    post_termination_retention: Right


@dataclass(frozen=True, slots=True)
class StorageDeclaration:
    # Opaque review-register reference, never a filesystem path or cloud URL.
    root_ref: str
    location: Literal["local_private", "private_cloud"]
    retention_days: int
    acl: State
    deletion_policy: State


@dataclass(frozen=True, slots=True)
class SourcePermission:
    schema_version: Literal["research_source_permission_v1"]
    permission_id: str
    revision: int
    provider: Literal["massive"]
    product: Literal["us_stocks"]
    subscription_selected: Literal["stocks_starter"]
    credential_upgrade: Literal["user_reported"]
    entitlement_verified: State
    license_verified: State
    license_state: Literal["unknown", "pending", "licensed", "expired", "revoked", "conflicting"]
    terms_version: str
    effective_at: datetime | None
    expires_at: datetime | None
    reviewed_at: datetime | None
    contact_ref: str
    open_questions: tuple[str, ...]
    endpoints: tuple[Endpoint, ...]
    history_start: datetime | None
    history_end: datetime | None
    delay_seconds: int
    sample_ids: tuple[str, ...]
    security_ids: tuple[str, ...]
    rights: UseRights
    storage: StorageDeclaration


@dataclass(frozen=True, slots=True)
class ProductPlan:
    interval: Literal["1m", "1d"]
    adjusted: bool
    price_basis: Literal["raw_execution", "split_adjusted"]
    session: Literal["rth", "vendor_daily"]
    price_unit: Literal["usd_per_share"]
    share_unit: Literal["raw_shares", "split_adjusted_shares"]
    volume_unit: Literal["raw_shares", "split_adjusted_shares", "unknown"]
    volume_policy: State


@dataclass(frozen=True, slots=True)
class ClockPlan:
    effective: State
    published: State
    historically_known: State
    provider_available: State
    ingested: State


@dataclass(frozen=True, slots=True)
class SamplePlan:
    schema_version: Literal["research_sample_plan_v1"]
    sample_id: str
    provider: Literal["massive"]
    product: Literal["us_stocks"]
    subscription_selected: Literal["stocks_starter"]
    symbol: str
    security_id: str | None
    identity: State
    calendar: Literal["NYSE"]
    timezone: Literal["America/New_York"]
    start_at: datetime
    end_at: datetime
    source_market: Literal["us_consolidated"]
    execution_venue: Literal["unresolved_smart"]
    products: tuple[ProductPlan, ...]
    endpoints: tuple[Endpoint, ...]
    clocks: ClockPlan
    corrections: State
    split_sample: bool
    independent_factor: State
    private_cloud: bool
    ci: bool
    redistribution: bool
    post_termination_retention: bool
    storage_root_ref: str
    max_requests: int
    max_source_bytes: int
    max_total_bytes: int


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    role: Literal["account", "license", "user_scope", "identity", "clocks", "factor"]
    authority: Literal[
        "provider_account", "vendor_written", "user_written", "historical_source", "issuer"
    ]
    document_ref: str
    sample_id: str
    permission_id: str
    revision: int
    terms_version: str
    reviewed_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewRegister:
    schema_version: Literal["research_permission_review_v1"]
    permission_sha256: str
    sample_sha256: str
    records: tuple[ReviewRecord, ...]


def _timestamp(value: object, field: str) -> datetime:
    try:
        if type(value) is not str or len(value) > 40:
            _deny("INVALID_TIME", field)
        result = datetime.fromisoformat(value)
    except ValueError:
        _deny("INVALID_TIME", field)
    if result.utcoffset() != timedelta(0):
        _deny("INVALID_TIME", field)
    return result


def _decode(annotation: Any, value: Any, field: str) -> Any:
    origin, arguments = get_origin(annotation), get_args(annotation)
    if origin is Literal:
        if not any(type(value) is type(item) and value == item for item in arguments):
            _deny("UNSUPPORTED_SCHEMA" if field == "schema_version" else "INVALID_VALUE", field)
        return value
    if origin is types.UnionType:
        return None if value is None else _decode(arguments[0], value, field)
    if origin is tuple:
        if type(value) is not list or len(value) > 16:
            _deny("METADATA_BOUNDS", field)
        result = tuple(_decode(arguments[0], item, field) for item in value)
        if len(set(result)) != len(result):
            _deny("DUPLICATE_SCOPE", field)
        return result
    if isinstance(annotation, type) and is_dataclass(annotation):
        names = {f.name for f in fields(annotation)}
        if type(value) is not dict or set(value) != names:
            _deny("FIELDS_MISMATCH", field)
        hints = get_type_hints(annotation)
        return annotation(
            **{name: _decode(hints[name], item, name) for name, item in value.items()}
        )
    if annotation is datetime:
        return _timestamp(value, field)
    if type(value) is not annotation:
        _deny("INVALID_TYPE", field)
    if annotation is str:
        pattern = r"[0-9a-f]{64}" if field.endswith("sha256") else r"[A-Za-z][A-Za-z0-9_-]{0,63}"
        if re.fullmatch(pattern, value) is None:
            _deny("UNSAFE_METADATA", field)
    if annotation is int and not 0 <= value <= 16 * 1024 * 1024:
        _deny("METADATA_BOUNDS", field)
    return value


def _document(content: bytes, cls: type) -> Any:
    if type(content) is not bytes or not 0 < len(content) <= MAX_METADATA_BYTES:
        _deny("METADATA_BOUNDS", "input")
    try:
        payload = decode_json(content)
    except VerificationContractError:
        _deny("INVALID_JSON", "input")
    return _decode(cls, payload, "input")


def decode_permission_declaration(content: bytes) -> SourcePermission:
    """Decode pending declarations without promoting them to a permission grant."""
    return cast(SourcePermission, _document(content, SourcePermission))


def decode_sample_plan(content: bytes) -> SamplePlan:
    """Decode proposed scope; no session, identity or market-data authentication."""
    return cast(SamplePlan, _document(content, SamplePlan))


def _check_states(permission: SourcePermission, as_of: datetime) -> None:
    if type(as_of) is not datetime or as_of.utcoffset() != timedelta(0):
        _deny("INVALID_TIME", "as_of")
    if permission.license_state != "licensed":
        _deny("LICENSE_" + permission.license_state.upper(), "license_state")
    for field in ("entitlement_verified", "license_verified"):
        if getattr(permission, field) != "documented":
            _deny(field.upper() + "_PENDING", field)
    start, end, reviewed = permission.effective_at, permission.expires_at, permission.reviewed_at
    if start is None or end is None or reviewed is None:
        _deny("LICENSE_CLOCKS_PENDING", "license_state")
    if not start <= reviewed <= as_of < end:
        _deny("LICENSE_EXPIRED" if as_of >= end else "LICENSE_TIME_CONFLICT", "license_state")
    if permission.open_questions or permission.revision < 1:
        _deny("PERMISSION_REVIEW_PENDING", "open_questions")


def _check_scope(permission: SourcePermission, sample: SamplePlan, as_of: datetime) -> None:
    if (
        sample.sample_id not in permission.sample_ids
        or sample.security_id not in permission.security_ids
    ):
        _deny("SAMPLE_SCOPE_MISMATCH", "sample_id")
    if sample.identity != "documented" or sample.security_id is None:
        _deny("IDENTITY_PENDING", "identity")
    if re.fullmatch(r"[A-Z]{1,10}", sample.symbol) is None:
        _deny("INVALID_VALUE", "symbol")
    if (
        not timedelta(0) < sample.end_at - sample.start_at <= timedelta(days=7)
        or sample.end_at > as_of
    ):
        _deny("WINDOW_BOUNDS", "start_at")
    if (
        permission.history_start is None
        or permission.history_end is None
        or not permission.history_start <= sample.start_at < sample.end_at <= permission.history_end
    ):
        _deny("HISTORY_SCOPE_MISMATCH", "history_start")
    needed = {"reference", "corporate_actions"}
    pairs: dict[str, set[bool]] = {}
    for product in sample.products:
        if (
            product.adjusted != (product.price_basis == "split_adjusted")
            or product.share_unit != ("split_adjusted_shares" if product.adjusted else "raw_shares")
            or product.session != ("rth" if product.interval == "1m" else "vendor_daily")
            or product.volume_unit == "unknown"
            or product.volume_policy != "documented"
        ):
            _deny("PRODUCT_UNITS_CONFLICT", "products")
        if product.adjusted in pairs.setdefault(product.interval, set()):
            _deny("DUPLICATE_PRODUCT", "products")
        pairs[product.interval].add(product.adjusted)
        needed.add("minute_aggregates" if product.interval == "1m" else "day_aggregates")
    if not pairs or any(pair != {False, True} for pair in pairs.values()):
        _deny("RAW_COMPARISON_SCOPE_MISSING", "products")
    if set(sample.endpoints) != needed or not needed <= set(permission.endpoints):
        _deny("ENDPOINT_SCOPE_MISSING", "endpoints")
    if permission.delay_seconds != 900:
        _deny("STARTER_DELAY_CONFLICT", "delay_seconds")
    if any(getattr(sample.clocks, f.name) != "documented" for f in fields(ClockPlan)):
        _deny("PIT_CLOCKS_PENDING", "clocks")
    if sample.corrections != "documented":
        _deny("CORRECTIONS_PENDING", "corrections")
    if sample.split_sample and sample.independent_factor != "documented":
        _deny("INDEPENDENT_FACTOR_PENDING", "independent_factor")
    required = {
        "personal_algorithmic_backtest",
        "non_display",
        "raw_response",
        "l0",
        "l1",
        "parquet",
        "private_storage",
        "derived_data",
    }
    required.update(
        field
        for field in ("private_cloud", "ci", "redistribution", "post_termination_retention")
        if getattr(sample, field)
    )
    for field in sorted(required):
        if getattr(permission.rights, field) != "allowed":
            _deny("USE_SCOPE_DENIED_OR_UNKNOWN", field)
    storage = permission.storage
    if (
        storage.root_ref != sample.storage_root_ref
        or (storage.location == "private_cloud") != sample.private_cloud
        or storage.acl != "documented"
        or storage.deletion_policy != "documented"
        or not 1 <= storage.retention_days <= 3650
    ):
        _deny("STORAGE_SCOPE_MISMATCH", "storage")
    if (
        not 1 <= sample.max_requests <= 16
        or not 1 <= sample.max_source_bytes <= 2 * 1024 * 1024
        or not sample.max_source_bytes <= sample.max_total_bytes <= 16 * 1024 * 1024
    ):
        _deny("REQUEST_BUDGET_INVALID", "max_requests")


def _check_review(
    permission: SourcePermission,
    sample: SamplePlan,
    permission_bytes: bytes,
    sample_bytes: bytes,
    review_bytes: bytes,
    reviewed_evidence_sha256: str | None,
    as_of: datetime,
) -> None:
    if (
        type(reviewed_evidence_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", reviewed_evidence_sha256) is None
    ):
        _deny("INDEPENDENT_REVIEW_REQUIRED", "review")
    register = cast(ReviewRegister, _document(review_bytes, ReviewRegister))
    if hashlib.sha256(review_bytes).hexdigest() != reviewed_evidence_sha256:
        _deny("REVIEW_ROOT_MISMATCH", "review")
    if (
        register.permission_sha256 != hashlib.sha256(permission_bytes).hexdigest()
        or register.sample_sha256 != hashlib.sha256(sample_bytes).hexdigest()
    ):
        _deny("DECLARATION_BINDING_MISMATCH", "review")
    authorities = {
        "account": "provider_account",
        "license": "vendor_written",
        "user_scope": "user_written",
        "identity": "historical_source",
        "clocks": "historical_source",
    }
    if sample.split_sample:
        authorities["factor"] = "issuer"
    if len(register.records) != len(authorities) or {r.role for r in register.records} != set(
        authorities
    ):
        _deny("REVIEW_ROLES_MISSING", "review")
    if len({r.document_ref for r in register.records}) != len(register.records):
        _deny("REVIEW_SOURCE_CONFLICT", "review")
    for record in register.records:
        if record.authority != authorities[record.role]:
            _deny("REVIEW_SOURCE_CONFLICT", "review")
        if (
            record.sample_id != sample.sample_id
            or record.permission_id != permission.permission_id
            or record.revision != permission.revision
            or record.terms_version != permission.terms_version
        ):
            _deny("REVIEW_SCOPE_VERSION_MISMATCH", "review")
        if permission.reviewed_at != record.reviewed_at or record.reviewed_at > as_of:
            _deny("REVIEW_TIME_CONFLICT", "review")


def evaluate_permission_preparation(
    permission_bytes: bytes,
    sample_bytes: bytes,
    *,
    review_bytes: bytes,
    reviewed_evidence_sha256: str | None,
    as_of: datetime,
) -> dict[str, str | bool]:
    """Check metadata against a separately reviewed register, always without trust.

    A caller-selected register digest cannot authenticate vendor consent. Even a
    complete synthetic fixture returns only an offline contract pass. Reports
    contain fixed diagnostics, never caller IDs, paths, keys or manifest bytes.
    """
    code, field = "OFFLINE_CONTRACT_PASS", "input"
    try:
        permission = decode_permission_declaration(permission_bytes)
        sample = decode_sample_plan(sample_bytes)
        _check_states(permission, as_of)
        _check_scope(permission, sample, as_of)
        _check_review(
            permission,
            sample,
            permission_bytes,
            sample_bytes,
            review_bytes,
            reviewed_evidence_sha256,
            as_of,
        )
    except PermissionPreparationError as error:
        code, field = error.code, error.field
    result_status = "pass" if code == "OFFLINE_CONTRACT_PASS" else "reject"
    return {
        "schema_version": AUDIT_SCHEMA,
        "mode": "offline_preparation",
        "result": result_status,
        "code": code,
        "field": field,
        "license_determination": "not_performed",
        "real_status": "preparation_only",
        "real_reason": "entitlement_and_license_scope_unverified",
        "may_acquire": False,
        "raw_verified": False,
        "PIT_verified": False,
        "archive_ready": False,
        "trusted_entry": False,
        "may_build_engine": False,
    }
