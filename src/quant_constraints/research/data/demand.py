"""5F-1 bounded demand plans and untrusted aggregate wire parsing.

No source authentication, identity/PIT certification or permission judgment.
The only public real capture entry remains closed pending slice execution review.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Never, cast
from urllib.parse import parse_qsl, urlencode, urlsplit
from zoneinfo import ZoneInfo

from quant_constraints.calendar import SessionCalendar

from .archive_contracts import ArchiveError, canonical
from .massive import check_credential_content
from .pagination import _endpoint_time
from .permission import PermissionPreparationError, _document
from .verification_contracts import VerificationContractError
from .verification_wire import decode_json

DEMAND_VERSION = "research_demand_capture_v1"
MANIFEST_VERSION = "research_demand_archive_v1"
Product = Literal["raw", "comparison"]
NY = ZoneInfo("America/New_York")


class DemandError(ValueError):
    def __init__(self, code: str, field: str):
        self.code, self.field = code, field
        super().__init__(f"{code}:{field}")


def deny(code: str, field: str = "capture") -> Never:
    raise DemandError(code, field) from None


@dataclass(frozen=True, slots=True)
class DemandPlan:
    schema_version: Literal["research_demand_capture_v1"]
    capture_id: str
    purpose_ref: str
    storage_root_ref: str
    provider: Literal["massive"]
    subscription_selected: Literal["stocks_starter"]
    symbol: str
    start_at: datetime
    end_at: datetime
    products: tuple[Product, ...]
    page_limit: int
    max_requests: int
    max_source_bytes: int
    max_total_bytes: int
    timeout_seconds: int
    minimum_interval_seconds: int
    retention_days: int
    prior_manifest_sha256: str | None
    refresh_reason_ref: str | None


def decode_demand_plan(content: bytes, as_of: datetime) -> DemandPlan:
    try:
        plan = cast(DemandPlan, _document(content, DemandPlan))
    except PermissionPreparationError as error:
        deny(error.code, error.field)
    if type(as_of) is not datetime or as_of.utcoffset() != timedelta(0):
        deny("INVALID_TIME", "as_of")
    if re.fullmatch(r"[A-Z]{1,10}", plan.symbol) is None or plan.products != ("raw", "comparison"):
        deny("UNSUPPORTED_DEMAND_SCOPE", "products")
    if not 1 <= plan.page_limit <= 50000 or not 2 <= plan.max_requests <= 8:
        deny("REQUEST_BUDGET_INVALID", "max_requests")
    if (
        not 1 <= plan.max_source_bytes <= 512 * 1024
        or not plan.max_source_bytes <= plan.max_total_bytes <= 4 * 1024 * 1024
    ):
        deny("BYTE_BUDGET_INVALID", "max_source_bytes")
    if (
        not 1 <= plan.timeout_seconds <= 30
        or not 0 <= plan.minimum_interval_seconds <= 60
        or not 1 <= plan.retention_days <= 30
    ):
        deny("RESOURCE_POLICY_INVALID", "timeout_seconds")
    if plan.prior_manifest_sha256 is None and plan.refresh_reason_ref is not None:
        deny("REFRESH_REQUIRES_PREDECESSOR", "prior_manifest_sha256")
    local = plan.start_at.astimezone(NY)
    if (
        not timedelta(0) < plan.end_at - plan.start_at <= timedelta(days=7)
        or plan.end_at > as_of
        or any(at.second or at.microsecond for at in (plan.start_at, plan.end_at))
    ):
        deny("WINDOW_BOUNDS", "start_at")
    session = SessionCalendar().session(local.date())
    if (
        session is None
        or not session.market_open <= plan.start_at < plan.end_at <= session.market_close
    ):
        deny("SINGLE_RTH_SESSION_REQUIRED", "start_at")
    return plan


def fixed_url(plan: DemandPlan, product: Product) -> str:
    first = int(plan.start_at.timestamp() * 1000)
    last = int(plan.end_at.timestamp() * 1000) - 1
    return (
        f"https://api.massive.com/v2/aggs/ticker/{plan.symbol}/range/1/minute/{first}/{last}?"
        + urlencode(
            {
                "adjusted": str(product == "comparison").lower(),
                "sort": "asc",
                "limit": plan.page_limit,
            }
        )
    )


def checked_scope(url: str, plan: DemandPlan, product: Product) -> tuple[int, int, dict[str, str]]:
    # Separate 5F-1 transport format; the reviewed raw-only 5B source stays pinned.
    if (
        type(url) is not str
        or not 0 < len(url) <= 4096
        or any(ord(c) <= 32 or ord(c) == 127 for c in url)
        or re.search(r"%(?![0-9A-Fa-f]{2})", url)
    ):
        deny("PAGE_INVALID", "url")
    try:
        parsed = urlsplit(url)
        pairs = parse_qsl(
            parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=8
        )
    except ValueError:
        deny("PAGE_INVALID", "url")
    prefix = f"/v2/aggs/ticker/{plan.symbol}/range/1/minute/"
    boundaries = parsed.path.removeprefix(prefix).split("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.massive.com"
        or parsed.fragment
        or not parsed.path.startswith(prefix)
        or len(boundaries) != 2
    ):
        deny("PAGE_INVALID", "url")
    if any(k.lower() in {"apikey", "api_key", "token", "access_token"} for k, _ in pairs):
        deny("KEY_EXPOSURE", "url")
    query = dict(pairs)
    if len(query) != len(pairs) or set(query) - {"adjusted", "sort", "limit", "cursor"}:
        deny("PAGE_INVALID", "url")
    expected = {
        "adjusted": str(product == "comparison").lower(),
        "sort": "asc",
        "limit": str(plan.page_limit),
    }
    if any(k in query and query[k] != value for k, value in expected.items()):
        deny("PAGE_INVALID", "url")
    cursor = query.get("cursor")
    if cursor is not None:
        if re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,2048}", cursor) is None:
            deny("PAGE_INVALID", "url")
    elif any(query.get(k) != value for k, value in expected.items()):
        deny("PAGE_INVALID", "url")
    try:
        first = _endpoint_time(boundaries[0], inclusive_end=False)
        last = _endpoint_time(boundaries[1], inclusive_end=True)
    except ArchiveError:
        deny("PAGE_INVALID", "url")
    if (
        not int(plan.start_at.timestamp() * 1000) <= first <= last
        or last != int(plan.end_at.timestamp() * 1000) - 1
        or first % 60000
    ):
        deny("PAGE_INVALID", "url")
    return first, last, query


def check_secrets(content: bytes, key: str | None = None) -> None:
    try:
        check_credential_content(content, key)
    except ArchiveError:
        deny("KEY_EXPOSURE", "content")
    if re.search(rb"(?i)(?:password|secret|token|set-cookie)\s*(?:=|[\"]\s*:)", content):
        deny("KEY_EXPOSURE", "content")


def parse_untrusted_page(content: bytes, plan: DemandPlan, product: Product) -> dict:
    check_secrets(content)
    try:
        page = decode_json(content)
    except VerificationContractError:
        deny("INVALID_PROVIDER_JSON", "response")
    required = {"ticker", "adjusted", "status", "queryCount", "resultsCount", "request_id"}
    if (
        type(page) is not dict
        or not required <= set(page)
        or set(page) - required - {"results", "next_url"}
    ):
        deny("PROVIDER_SCHEMA_CONFLICT", "response")
    if (
        page["ticker"] != plan.symbol
        or type(page["adjusted"]) is not bool
        or page["adjusted"] != (product == "comparison")
        or type(page["status"]) is not str
        or page["status"] not in {"OK", "DELAYED"}
    ):
        deny("PRODUCT_SOURCE_CONFLICT", "response")
    if type(page["request_id"]) is not str or not 0 < len(page["request_id"]) <= 256:
        deny("PROVIDER_ID_INVALID", "response")
    rows = page.get("results", [])
    if (
        type(rows) is not list
        or len(rows) > 390
        or type(page["queryCount"]) is not int
        or type(page["resultsCount"]) is not int
        or not len(rows) == page["resultsCount"] <= page["queryCount"] <= plan.page_limit
    ):
        deny("PROVIDER_COUNTS_INVALID", "response")
    if "next_url" in page:
        if type(page["next_url"]) is not str:
            deny("PAGINATION_INCOMPLETE", "next_url")
        checked_scope(page["next_url"], plan, product)
    elif page["queryCount"] == plan.page_limit:
        deny("PAGINATION_INCOMPLETE", "queryCount")
    if not rows and (page["queryCount"] != 0 or "next_url" in page):
        deny("PAGINATION_INCOMPLETE", "results")
    for row in rows:
        if (
            type(row) is not dict
            or not {"o", "h", "l", "c", "v", "t"} <= set(row)
            or set(row) - {"o", "h", "l", "c", "v", "t", "vw", "n", "otc"}
        ):
            deny("BAR_SCHEMA_CONFLICT", "results")
        if (
            type(row["t"]) is not int
            or row["t"] % 60000
            or not int(plan.start_at.timestamp() * 1000)
            <= row["t"]
            < int(plan.end_at.timestamp() * 1000)
        ):
            deny("BAR_TIME_CONFLICT", "results")
        for name in ("o", "h", "l", "c", "v"):
            value = row[name]
            if type(value) not in (int, float):
                deny("BAR_VALUE_INVALID", "results")
            try:
                if not math.isfinite(value) or value < 0 or (name != "v" and value == 0):
                    deny("BAR_VALUE_INVALID", "results")
            except OverflowError:
                deny("BAR_VALUE_INVALID", "results")
        if not row["l"] <= min(row["o"], row["c"]) <= max(row["o"], row["c"]) <= row["h"]:
            deny("BAR_VALUE_INVALID", "results")
    return page


class UntrustedChain:
    def __init__(self, plan: DemandPlan, product: Product, *, fixture: bool):
        self.plan, self.product, self.fixture = plan, product, fixture
        self.next_url: str | None = fixed_url(plan, product)
        self.urls: set[str] = set()
        self.cursors: set[str] = set()
        self.stamps: set[int] = set()
        self.last: int | None = None

    def accept(self, url: str, page: dict) -> None:
        if url != self.next_url or url in self.urls:
            deny("PAGINATION_CHAIN_CONFLICT", "url")
        first, _, query = checked_scope(url, self.plan, self.product)
        cursor = query.get("cursor")
        if cursor is not None:
            if not self.fixture:
                deny("PAGINATION_UNVERIFIED", "cursor")
            if cursor in self.cursors:
                deny("PAGINATION_LOOP", "cursor")
            self.cursors.add(cursor)
        self.urls.add(url)
        for row in page.get("results", []):
            stamp = row["t"]
            if (
                stamp < first
                or stamp in self.stamps
                or (self.last is not None and stamp < self.last)
            ):
                deny("BAR_ORDER_OR_DUPLICATE", "results")
            self.stamps.add(stamp)
            self.last = stamp
        if len(self.stamps) > 390:
            deny("RECORD_BOUNDS", "results")
        following = page.get("next_url")
        if following is not None:
            next_first, _, next_query = checked_scope(following, self.plan, self.product)
            if following in self.urls or next_query.get("cursor") in self.cursors:
                deny("PAGINATION_LOOP", "next_url")
            if next_first != first and (self.last is None or next_first != self.last + 60000):
                deny("PAGINATION_RANGE_CONFLICT", "next_url")
            if next_first == first and "cursor" not in next_query:
                deny("PAGINATION_LOOP", "next_url")
            if "cursor" in next_query and not self.fixture:
                deny("PAGINATION_UNVERIFIED", "cursor")
        self.next_url = following


@dataclass(frozen=True, slots=True)
class FileBinding:
    artifact_id: str
    role: Literal["plan", "request", "response", "receipt", "l1", "ledger", "diagnostics"]
    sha256: str
    size_bytes: int

    @property
    def filename(self) -> str:
        return self.artifact_id + (".parquet" if self.role == "l1" else ".json")


@dataclass(frozen=True, slots=True)
class PageBinding:
    request: FileBinding
    response: FileBinding
    receipt: FileBinding


@dataclass(frozen=True, slots=True)
class ProductBinding:
    product: Product
    pages: tuple[PageBinding, ...]
    l1: FileBinding
    row_count: int


@dataclass(frozen=True, slots=True)
class DemandManifest:
    schema_version: Literal["research_demand_archive_v1"]
    mode: Literal["offline_fixture", "licensed_private_unverified"]
    execution_review_sha256: str | None
    plan: FileBinding
    ledger: FileBinding
    diagnostics: FileBinding
    products: tuple[ProductBinding, ...]
    prior_manifest_sha256: str | None
    acquired_start_at: datetime
    acquired_end_at: datetime
    retention_expires_at: datetime
    encoder_version: Literal["research_demand_l1_v1"]
    polars_version_ref: str

    def bindings(self) -> tuple[FileBinding, ...]:
        return (
            self.plan,
            self.ledger,
            self.diagnostics,
            *(
                item
                for product in self.products
                for item in (
                    product.l1,
                    *(
                        f
                        for page in product.pages
                        for f in (page.request, page.response, page.receipt)
                    ),
                )
            ),
        )


def manifest_bytes(manifest: DemandManifest) -> bytes:
    from dataclasses import asdict

    return canonical(asdict(manifest))


def demand_audit(
    code: str, *, requests: int = 0, received_bytes: int = 0, fixture: bool = True
) -> dict:
    if (
        code != "UNTRUSTED_ARTIFACTS_COMPLETE"
        or type(requests) is not int
        or not 0 <= requests <= 8
        or type(received_bytes) is not int
        or not 0 <= received_bytes <= 4 * 1024 * 1024
        or type(fixture) is not bool
    ):
        deny("INVALID_AUDIT_ARGUMENT", "audit")
    return {
        "schema_version": "research_demand_audit_v1",
        "code": code,
        "requests": requests,
        "received_bytes": received_bytes,
        "owner_scope": "bounded_on_demand",
        "data_authority": "untrusted",
        "license_determination": "not_performed",
        "real_execution": "slice_review_pending" if fixture else "untrusted_capture_only",
        "raw_verified": False,
        "PIT_verified": False,
        "archive_ready": False,
        "trusted_entry": False,
        "may_build_engine": False,
    }
