"""Demand-driven 5F-1 capture with immutable L0/L1/L2 and a closed real gate.

Fixture transport is an exact byte mapping, not a caller callback. The real
execution registry is empty until a specific slice review is independently
accepted and deployed. Owner intent and 5F-0 reports cannot populate it.
"""

from __future__ import annotations

import io
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

import polars as pl

from .archive_contracts import canonical, digest
from .demand import (
    DEMAND_VERSION,
    MANIFEST_VERSION,
    DemandError,
    DemandManifest,
    DemandPlan,
    FileBinding,
    PageBinding,
    ProductBinding,
    UntrustedChain,
    check_secrets,
    checked_scope,
    decode_demand_plan,
    demand_audit,
    deny,
    parse_untrusted_page,
)
from .demand_storage import (
    DemandArchive,
    artifact_path,
    load_demand_archive,
    private_read,
    private_root,
    private_write,
    publish_demand_archive,
    read_checkpoint,
)
from .massive import NoRedirect


@dataclass(frozen=True, slots=True)
class FixtureResponse:
    url: str = field(repr=False)
    status: int
    body: bytes = field(repr=False)
    started_at: datetime
    ingested_at: datetime


class FixtureResponses:
    """Pure local response mapping. No credential, environment or socket lookup."""

    def __init__(self, responses: tuple[FixtureResponse, ...]):
        if (
            type(responses) is not tuple
            or not 1 <= len(responses) <= 8
            or any(
                type(r) is not FixtureResponse
                or type(r.url) is not str
                or not 0 < len(r.url) <= 4096
                or type(r.status) is not int
                or not 0 <= r.status <= 599
                or type(r.body) is not bytes
                or len(r.body) > 2 * 1024 * 1024
                for r in responses
            )
        ):
            deny("INVALID_FIXTURE_TRANSPORT", "fixture")
        if len({r.url for r in responses}) != len(responses):
            deny("DUPLICATE_FIXTURE_URL", "fixture")
        for response in responses:
            check_secrets(response.url.encode())
            check_secrets(response.body)
            if (
                type(response.started_at) is not datetime
                or type(response.ingested_at) is not datetime
                or response.started_at.utcoffset() != timedelta(0)
                or response.ingested_at.utcoffset() != timedelta(0)
            ):
                deny("INVALID_FIXTURE_CLOCK", "fixture")
        self._responses = tuple(
            FixtureResponse(r.url, r.status, bytes(r.body), r.started_at, r.ingested_at)
            for r in responses
        )
        self.calls: list[str] = []

    @property
    def binding(self) -> str:
        return digest(
            canonical(
                [
                    {
                        "url": r.url,
                        "status": r.status,
                        "body_sha256": digest(r.body),
                        "started_at": r.started_at,
                        "ingested_at": r.ingested_at,
                    }
                    for r in self._responses
                ]
            )
        )

    def expected(self, url: str) -> FixtureResponse:
        for response in self._responses:
            if response.url == url:
                return response
        deny("FIXTURE_RESPONSE_MISSING", "fixture")

    def fetch(self, url: str, limit: int) -> FixtureResponse:
        self.calls.append(url)
        response = self.expected(url)
        return FixtureResponse(
            url,
            response.status,
            response.body[: limit + 1],
            response.started_at,
            response.ingested_at,
        )


@dataclass(frozen=True, slots=True)
class _ExecutionPermit:
    review_sha256: str
    plan_sha256: str
    root_path_sha256: str
    root_identity_sha256: str
    account_evidence_ref: str
    license_evidence_ref: str
    valid_until: datetime


# Intentionally no approved execution scope, account or private root yet.
# A caller-provided bool, report, JSON record or self-selected hash cannot add one.
_ACCEPTED_EXECUTION_REVIEWS: tuple[_ExecutionPermit, ...] = ()


def _now() -> datetime:
    return datetime.now(UTC)


def _posix_runtime() -> bool:
    return os.name == "posix"


def _owner_uid() -> int:
    return getattr(os, "getuid", lambda: -1)()


def root_identity(root: Path) -> str:
    item = root.stat()
    return digest(canonical([item.st_dev, item.st_ino]))


def _assert_archive_use(manifest: DemandManifest, root: Path) -> None:
    permit = next(
        (
            p
            for p in _ACCEPTED_EXECUTION_REVIEWS
            if p.review_sha256 == manifest.execution_review_sha256
        ),
        None,
    )
    if permit is None or _now() >= permit.valid_until:
        deny("EXECUTION_REVIEW_EXPIRED_OR_REVOKED", "review")
    if (
        manifest.plan.sha256 != permit.plan_sha256
        or digest(str(root).encode()) != permit.root_path_sha256
        or root_identity(root) != permit.root_identity_sha256
    ):
        deny("EXECUTION_SCOPE_BINDING_MISMATCH", "review")


class _NativeResponses:
    def __init__(self, plan: DemandPlan, permit: _ExecutionPermit, root: Path, key: str):
        if not key or any(c.isspace() for c in key):
            deny("CREDENTIAL_INVALID", "credential")
        self.plan, self.permit, self.root, self._key = plan, permit, root, key

    @property
    def binding(self) -> str:
        return self.permit.review_sha256

    def check_scope(self) -> None:
        if self.permit not in _ACCEPTED_EXECUTION_REVIEWS or _now() >= self.permit.valid_until:
            deny("EXECUTION_REVIEW_EXPIRED_OR_REVOKED", "review")
        private_root(self.root)
        if root_identity(self.root) != self.permit.root_identity_sha256:
            deny("PRIVATE_ROOT_CHANGED", "root")

    def fetch(self, url: str, limit: int) -> FixtureResponse:
        self.check_scope()
        if type(limit) is not int or not 0 < limit <= 512 * 1024:
            deny("BYTE_BUDGET_INVALID", "response")
        try:
            _, _, query = checked_scope(url, self.plan, "raw")
        except DemandError:
            _, _, query = checked_scope(url, self.plan, "comparison")
        if "cursor" in query:
            deny("PAGINATION_UNVERIFIED", "cursor")
        time.sleep(self.plan.minimum_interval_seconds)
        self.check_scope()
        started = _now()
        request = Request(
            url,
            headers={"Accept": "application/json", "Authorization": "Bearer " + self._key},
            method="GET",
        )
        try:
            with build_opener(NoRedirect()).open(
                request, timeout=self.plan.timeout_seconds
            ) as response:
                result = FixtureResponse(
                    url, response.status, response.read(limit + 1), started, _now()
                )
        except HTTPError as error:
            with error:
                result = FixtureResponse(url, error.code, error.read(limit + 1), started, _now())
        except (URLError, TimeoutError, OSError):
            result = FixtureResponse(url, 0, b"", started, _now())
        check_secrets(result.body, self._key)
        return result


def capture_real_slice(
    plan_bytes: bytes, root: Path, *, reviewed_execution_sha256: str | None = None
) -> DemandArchive:
    """Closed until a concrete slice review is registered; check before key/file I/O."""
    permit = next(
        (p for p in _ACCEPTED_EXECUTION_REVIEWS if p.review_sha256 == reviewed_execution_sha256),
        None,
    )
    if permit is None:
        deny("EXECUTION_SCOPE_REVIEW_PENDING", "review")
    as_of = _now()
    plan = decode_demand_plan(plan_bytes, as_of)
    if (
        digest(plan_bytes) != permit.plan_sha256
        or not isinstance(root, Path)
        or digest(str(root).encode()) != permit.root_path_sha256
    ):
        deny("EXECUTION_SCOPE_BINDING_MISMATCH", "review")
    if not _posix_runtime():
        deny("PRIVATE_ACL_PLATFORM_UNSUPPORTED", "root")
    private_root(root)
    if root_identity(root) != permit.root_identity_sha256 or root.stat().st_uid != _owner_uid():
        deny("PRIVATE_ROOT_CHANGED", "root")
    if (
        not permit.account_evidence_ref
        or not permit.license_evidence_ref
        or as_of >= permit.valid_until
    ):
        deny("EXECUTION_REVIEW_EXPIRED_OR_REVOKED", "review")
    key = os.environ.get("MASSIVE_API_KEY")
    if key is None:
        deny("CREDENTIAL_UNAVAILABLE", "credential")
    source = _NativeResponses(plan, permit, root, key)
    return _capture(plan_bytes, plan, root, source, as_of=as_of, fixture=False)


def capture_fixture_slice(
    plan_bytes: bytes, root: Path, responses: FixtureResponses, *, as_of: datetime
) -> DemandArchive:
    if type(responses) is not FixtureResponses:
        deny("INVALID_FIXTURE_TRANSPORT", "fixture")
    plan = decode_demand_plan(plan_bytes, as_of)
    return _capture(plan_bytes, plan, root, responses, as_of=as_of, fixture=True)


def _binding(artifact_id: str, role, content: bytes) -> FileBinding:
    return FileBinding(artifact_id, role, digest(content), len(content))


def _same_scope(left: DemandPlan, right: DemandPlan) -> bool:
    ignored = {"capture_id", "prior_manifest_sha256", "refresh_reason_ref"}
    return {k: v for k, v in asdict(left).items() if k not in ignored} == {
        k: v for k, v in asdict(right).items() if k not in ignored
    }


def _l1(rows: list[dict]) -> bytes:
    schema = {
        "bar_start_at": pl.Datetime("us", "UTC"),
        "bar_end_at": pl.Datetime("us", "UTC"),
        "ingested_at": pl.Datetime("us", "UTC"),
        "provider_available_at": pl.Datetime("us", "UTC"),
        "published_at": pl.Datetime("us", "UTC"),
        "historically_known_at": pl.Datetime("us", "UTC"),
        **dict.fromkeys(("open", "high", "low", "close", "volume"), pl.Float64),
        "source_sha256": pl.String,
        "product": pl.String,
        "volume_unit": pl.String,
        "price_basis": pl.String,
        "price_unit": pl.String,
        "share_unit": pl.String,
        "currency": pl.String,
        "identity_status": pl.String,
        "correction_status": pl.String,
    }
    buffer = io.BytesIO()
    pl.DataFrame(rows, schema=schema).write_parquet(buffer, compression="zstd", statistics=True)
    return buffer.getvalue()


def _capture(
    plan_bytes: bytes,
    plan: DemandPlan,
    root: Path,
    source: FixtureResponses | _NativeResponses,
    *,
    as_of: datetime,
    fixture: bool,
) -> DemandArchive:
    private_root(root)
    identity = root_identity(root)
    prior = None
    if plan.prior_manifest_sha256 is not None:
        prior = load_demand_archive(root, plan.prior_manifest_sha256, as_of=as_of)
        previous = decode_demand_plan(prior.read(prior.manifest.plan), as_of)
        if not _same_scope(previous, plan) or prior.manifest.mode != (
            "offline_fixture" if fixture else "licensed_private_unverified"
        ):
            deny("PREDECESSOR_SCOPE_CONFLICT", "prior")
        if plan.refresh_reason_ref is None:
            return prior
    base = "sessions/" + digest(plan_bytes) + "/"
    binding = canonical(
        {
            "schema_version": DEMAND_VERSION,
            "plan_sha256": digest(plan_bytes),
            "transport_binding": source.binding,
            "root_identity_sha256": identity,
        }
    )
    private_write(root, base + "binding.json", binding)
    private_write(root, base + "capture_plan.json", plan_bytes)
    completed = artifact_path(root, base + "completed.json")
    if completed.exists():
        # Only this immutable, independently reconstructible binding can be reused.
        result = _checkpoint_json(root, base + "completed.json")
        if (
            type(result) is not dict
            or set(result) != {"manifest_sha256", "binding_sha256"}
            or result["binding_sha256"] != digest(binding)
        ):
            deny("SESSION_BINDING_MISMATCH", "session")
        return load_demand_archive(root, result["manifest_sha256"], as_of=as_of)
    content = {"capture_plan": plan_bytes}
    products = []
    total, requests = 0, 0
    first: datetime | None = None
    last: datetime | None = None
    for product in plan.products:
        chain = UntrustedChain(plan, product, fixture=fixture)
        pages, rows = [], []
        for index in range(plan.max_requests):
            url = chain.next_url
            if url is None:
                break
            checked_scope(url, plan, product)
            if root_identity(root) != identity:
                deny("PRIVATE_ROOT_CHANGED", "root")
            if isinstance(source, _NativeResponses):
                source.check_scope()
            prefix = f"{product}_p{index:03d}"
            request_record = canonical(
                {
                    "schema_version": DEMAND_VERSION,
                    "url": url,
                    "product": product,
                    "method": "GET",
                    "credential_policy": "bearer_not_retained",
                    "plan_sha256": digest(plan_bytes),
                    "body_limit": min(plan.max_source_bytes, plan.max_total_bytes - total),
                    "ordinal": requests,
                }
            )
            response_path = artifact_path(root, base + prefix + "_response.json")
            receipt_path = artifact_path(root, base + prefix + "_receipt.json")
            request_path = artifact_path(root, base + prefix + "_request.json")
            if requests >= plan.max_requests or total >= plan.max_total_bytes:
                deny("CAPTURE_BUDGET_EXHAUSTED", "budget")
            if request_path.exists():
                # Ambiguous outstanding requests never reset the request budget.
                if not response_path.exists() or not receipt_path.exists():
                    deny("INCOMPLETE_ATTEMPT_REVIEW_REQUIRED", "session")
                private_read(root, base + prefix + "_request.json", digest(request_record))
                if isinstance(source, _NativeResponses):
                    deny("REAL_SESSION_RESUME_REVIEW_REQUIRED", "session")
                receipt = _checkpoint_json(root, base + prefix + "_receipt.json")
                if type(receipt) is not dict or set(receipt) != {
                    "schema_version",
                    "url",
                    "product",
                    "status",
                    "started_at",
                    "ingested_at",
                    "response_sha256",
                    "response_bytes",
                }:
                    deny("RECEIPT_SCHEMA_CONFLICT", "session")
                body = private_read(
                    root, base + prefix + "_response.json", receipt["response_sha256"]
                )
                try:
                    started_at = datetime.fromisoformat(receipt["started_at"])
                    ingested_at = datetime.fromisoformat(receipt["ingested_at"])
                except (ValueError, TypeError):
                    deny("RECEIPT_SCHEMA_CONFLICT", "session")
                response = FixtureResponse(
                    url,
                    receipt["status"],
                    body,
                    started_at,
                    ingested_at,
                )
                if (
                    receipt["url"] != url
                    or receipt["product"] != product
                    or receipt["response_bytes"] != len(body)
                    or receipt["schema_version"] != DEMAND_VERSION
                ):
                    deny("RECEIPT_BINDING_MISMATCH", "session")
                if isinstance(source, FixtureResponses) and response != source.expected(url):
                    deny("FIXTURE_CACHE_CHANGED", "session")
            else:
                private_write(root, base + prefix + "_request.json", request_record)
                response = source.fetch(
                    url, min(plan.max_source_bytes, plan.max_total_bytes - total)
                )
                private_write(
                    root,
                    base + prefix + "_outcome.json",
                    canonical({"status": response.status, "received_bytes": len(response.body)}),
                )
            requests += 1
            if len(response.body) > min(plan.max_source_bytes, plan.max_total_bytes - total):
                deny("RESPONSE_BOUNDS", "response")
            total += len(response.body)
            check_secrets(response.body)
            if response.status != 200:
                # Stop without retaining possibly sensitive denial/redirect bodies.
                deny(
                    "PERMISSION_GAP" if response.status in {401, 403} else "TRANSPORT_STOPPED",
                    "status",
                )
            current_time = as_of if fixture else _now()
            if (
                type(response.started_at) is not datetime
                or type(response.ingested_at) is not datetime
                or response.started_at.utcoffset() != timedelta(0)
                or response.ingested_at.utcoffset() != timedelta(0)
                or not plan.end_at <= response.started_at <= response.ingested_at <= current_time
                or (last is not None and response.started_at < last)
            ):
                deny("CAPTURE_CLOCK_CONFLICT", "clocks")
            page = parse_untrusted_page(response.body, plan, product)
            chain.accept(url, page)
            first = response.started_at if first is None else first
            last = response.ingested_at
            receipt_record = canonical(
                {
                    "schema_version": DEMAND_VERSION,
                    "url": url,
                    "product": product,
                    "status": response.status,
                    "started_at": response.started_at,
                    "ingested_at": response.ingested_at,
                    "response_sha256": digest(response.body),
                    "response_bytes": len(response.body),
                }
            )
            private_write(root, base + prefix + "_response.json", response.body)
            private_write(root, base + prefix + "_receipt.json", receipt_record)
            for role, payload in (
                ("request", request_record),
                ("response", response.body),
                ("receipt", receipt_record),
            ):
                content[prefix + "_" + role] = payload
            pages.append(
                PageBinding(
                    *(
                        _binding(prefix + "_" + role, role, content[prefix + "_" + role])
                        for role in ("request", "response", "receipt")
                    )
                )
            )
            for row in page.get("results", []):
                start = datetime.fromtimestamp(row["t"] / 1000, UTC)
                rows.append(
                    {
                        "bar_start_at": start,
                        "bar_end_at": start + timedelta(minutes=1),
                        "ingested_at": response.ingested_at,
                        "provider_available_at": None,
                        "published_at": None,
                        "historically_known_at": None,
                        **{
                            dest: float(row[src])
                            for src, dest in (
                                ("o", "open"),
                                ("h", "high"),
                                ("l", "low"),
                                ("c", "close"),
                                ("v", "volume"),
                            )
                        },
                        "source_sha256": digest(response.body),
                        "product": product,
                        "volume_unit": "unknown",
                        "price_basis": "provider_declared_raw"
                        if product == "raw"
                        else "provider_declared_split_adjusted",
                        "price_unit": "provider_declared_usd_per_share_unverified",
                        "share_unit": "unknown",
                        "currency": "USD",
                        "identity_status": "unverified",
                        "correction_status": "unverified",
                    }
                )
        if chain.next_url is not None:
            deny("PAGINATION_INCOMPLETE", "budget")
        l1_id = product + "_l1"
        content[l1_id] = _l1(rows)
        products.append(
            ProductBinding(product, tuple(pages), _binding(l1_id, "l1", content[l1_id]), len(rows))
        )
    if first is None or last is None:
        deny("EMPTY_CAPTURE", "capture")
    content["budget_ledger"] = canonical(
        {
            "schema_version": DEMAND_VERSION,
            "requests": requests,
            "received_bytes": total,
            "automatic_retries": 0,
        }
    )
    content["diagnostics"] = canonical(
        {
            **demand_audit(
                "UNTRUSTED_ARTIFACTS_COMPLETE",
                requests=requests,
                received_bytes=total,
                fixture=fixture,
            ),
            "missing_bars": {
                p.product: int((plan.end_at - plan.start_at).total_seconds() / 60) - p.row_count
                for p in products
            },
            "availability_method": "unknown_not_ingestion",
            "identity_status": "unverified",
            "corrections": (
                "same_supplier_bytes_not_correction_proof"
                if tuple(
                    p.response.sha256 for product in prior.manifest.products for p in product.pages
                )
                == tuple(p.response.sha256 for product in products for p in product.pages)
                else "bytes_differ_not_proof_of_correction"
            )
            if prior
            else "no_predecessor",
            "prior_manifest_sha256": plan.prior_manifest_sha256,
        }
    )
    manifest = DemandManifest(
        MANIFEST_VERSION,
        "offline_fixture" if fixture else "licensed_private_unverified",
        None if fixture else source.binding,
        _binding("capture_plan", "plan", plan_bytes),
        _binding("budget_ledger", "ledger", content["budget_ledger"]),
        _binding("diagnostics", "diagnostics", content["diagnostics"]),
        tuple(products),
        plan.prior_manifest_sha256,
        first,
        last,
        last + timedelta(days=plan.retention_days),
        "research_demand_l1_v1",
        "polars_" + pl.__version__.replace(".", "_"),
    )
    if isinstance(source, _NativeResponses):
        source.check_scope()
    if root_identity(root) != identity:
        deny("PRIVATE_ROOT_CHANGED", "root")
    archive = publish_demand_archive(root, manifest, content, as_of=as_of if fixture else _now())
    private_write(
        root,
        base + "completed.json",
        canonical({"manifest_sha256": archive.revision, "binding_sha256": digest(binding)}),
    )
    return archive


def _checkpoint_json(root: Path, relative: str):
    from .verification_contracts import VerificationContractError
    from .verification_wire import decode_json

    try:
        return decode_json(read_checkpoint(root, relative))
    except VerificationContractError:
        deny("CHECKPOINT_INVALID", "session")
