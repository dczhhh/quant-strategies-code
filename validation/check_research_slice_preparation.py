"""Offline review-packet consistency only; no credential, capture or permission grant."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from quant_constraints.research.data.demand import DemandError, decode_demand_plan, fixed_url
from quant_constraints.research.data.verification_contracts import VerificationContractError
from quant_constraints.research.data.verification_wire import decode_json

BASE = "f84115a47c84150651ade89563a494e3f6fa10ff"
REVIEW = 5486454320
PLAN_PATH = "docs/examples/research-demand-aapl-v1.json"
PACKET_PATH = "docs/examples/research-slice-preparation-v1.json"
PLAN_SHA256 = "ab5de290a22b06f93299f9720d52a9ae2212d36b02c7ab476fe9b7e0b5052135"
SOURCE_SHA256 = {
    "src/quant_constraints/research/data/demand.py": "7d12d8a4df8d34a0c6cca1020a42c5a4a0f618bfc2107049f23c952caffa3de2",
    "src/quant_constraints/research/data/demand_capture.py": "cfb0e8e27eabbf9f957886cad2e7cc850ca4d3fa3661e89e009778d8e8b8de07",
    "src/quant_constraints/research/data/demand_storage.py": "24ffa10df375d9a5241f09622700dbd09f68b4845e266e4f968b202be00950c5",
}
PENDING = (
    "account_entitlement_evidence",
    "applicable_license_and_retention_evidence",
    "private_posix_root_acl_and_deletion_evidence",
    "independent_execution_review_and_registration",
)
FLAGS = ("raw_verified", "PIT_verified", "archive_ready", "trusted_entry", "may_build_engine")
PUBLIC_SOURCES = (
    "https://massive.com/stocks",
    "https://massive.com/docs/rest/stocks/aggregates/custom-bars",
    "https://massive.com/legal/individuals-terms-of-service",
    "https://massive.com/legal/market-data-terms-of-service",
)


def _report(code: str, valid: bool = False) -> dict:
    return {
        "schema_version": "research_slice_preparation_audit_v1",
        "status": "PREPARATION_ONLY" if valid else "FAIL_CLOSED",
        "code": code,
        "may_acquire": False,
        "license_determination": "not_performed",
        "pending_inputs": list(PENDING),
        **dict.fromkeys(FLAGS, False),
    }


def evaluate(packet_bytes: bytes, plan_bytes: bytes, implementation: dict[str, bytes]) -> dict:
    """Check this version's pending proposal from bytes; never resolve private references.

    A later actual review needs its own accepted change. This exact packet version
    does not accept an approved flag, real review digest, private path or registry.
    """
    if type(packet_bytes) is not bytes or not 0 < len(packet_bytes) <= 32768:
        return _report("PACKET_BOUNDS")
    if (
        type(plan_bytes) is not bytes
        or not 0 < len(plan_bytes) <= 32768
        or hashlib.sha256(plan_bytes).hexdigest() != PLAN_SHA256
    ):
        return _report("PLAN_BINDING_MISMATCH")
    if type(implementation) is not dict or set(implementation) != set(SOURCE_SHA256):
        return _report("IMPLEMENTATION_BINDING_MISMATCH")
    if any(
        type(implementation[path]) is not bytes
        or not 0 < len(implementation[path]) <= 131072
        or hashlib.sha256(implementation[path]).hexdigest() != digest
        for path, digest in SOURCE_SHA256.items()
    ):
        return _report("IMPLEMENTATION_BINDING_MISMATCH")
    try:
        packet = decode_json(packet_bytes)
        plan = decode_demand_plan(plan_bytes, datetime(2026, 10, 12, 4, 12, 38, tzinfo=UTC))
    except (VerificationContractError, DemandError):
        return _report("PACKET_INVALID")
    expected = {
        "schema_version": "research_slice_execution_preparation_v1",
        "status": "preparation_only",
        "accepted_implementation_commit": BASE,
        "development_review_id": REVIEW,
        "plan_sha256": PLAN_SHA256,
        "owner_scope": "approved_bounded_personal_noncommercial",
        "credential_secret_name": "massive",
        "execution_environment": "owner_private_posix_pending",
        "requests": [
            {"product": product, "method": "GET", "url": fixed_url(plan, product)}
            for product in plan.products
        ],
        "max_requests": plan.max_requests,
        "max_source_bytes": plan.max_source_bytes,
        "max_total_bytes": plan.max_total_bytes,
        "retention_days": plan.retention_days,
        "retention_status": "proposed_not_approved",
        "public_sources": list(PUBLIC_SOURCES),
        "public_sources_checked_on": "2026-10-12",
        "pending_inputs": list(PENDING),
        "account_evidence_ref": None,
        "license_evidence_ref": None,
        "root_path_sha256": None,
        "root_identity_sha256": None,
        "root_acl_evidence_ref": None,
        "deletion_policy_ref": None,
        "reviewed_execution_sha256": None,
        "valid_until": None,
    }
    # JSON equality alone equates bools with ints; bind types and values recursively.
    if type(packet) is not dict or json.dumps(packet, sort_keys=True) != json.dumps(
        expected, sort_keys=True
    ):
        return _report("PACKET_SCOPE_OR_STATUS_MISMATCH")
    return _report("EXECUTION_REVIEW_PENDING", True)


def _read(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        return stream.read(limit + 1)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        # Fixed repository metadata/source paths only; no packet-selected path is opened.
        report = evaluate(
            _read(root / PACKET_PATH, 32768),
            _read(root / PLAN_PATH, 32768),
            {path: _read(root / path, 131072) for path in SOURCE_SHA256},
        )
    except OSError:
        report = _report("REPOSITORY_INPUT_UNREADABLE")
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    # Zero means the pending proposal is internally consistent, never may_acquire.
    return int(report["status"] != "PREPARATION_ONLY")


if __name__ == "__main__":
    raise SystemExit(main())
