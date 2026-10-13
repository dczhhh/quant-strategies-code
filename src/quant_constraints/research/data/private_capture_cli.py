"""Local private operator for the reviewed AAPL candidate; no implicit permit issuance."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from . import demand_capture as capture
from .archive_contracts import digest
from .demand import DemandError, decode_demand_plan, deny
from .demand_storage import private_root

PLAN_SHA256 = "ab5de290a22b06f93299f9720d52a9ae2212d36b02c7ab476fe9b7e0b5052135"
ROOT_ENVIRONMENT = "QUANT_CAPTURE_ROOT"
FLAGS = ("raw_verified", "PIT_verified", "archive_ready", "trusted_entry", "may_build_engine")
PUBLIC_CODES = frozenset(
    {
        "INVALID_CLI_ARGUMENTS",
        "PRIVATE_ROOT_REQUIRED",
        "PRIVATE_ROOT_INVALID",
        "PRIVATE_ROOT_CHANGED",
        "PRIVATE_ACL_REQUIRED",
        "PRIVATE_ACL_PLATFORM_UNSUPPORTED",
        "ROOT_INSIDE_REPOSITORY",
        "PATH_INVALID",
        "EXECUTION_SCOPE_REVIEW_PENDING",
        "EXECUTION_REVIEW_EXPIRED_OR_REVOKED",
        "EXECUTION_SCOPE_BINDING_MISMATCH",
        "CREDENTIAL_UNAVAILABLE",
        "CREDENTIAL_INVALID",
        "PERMISSION_GAP",
        "TRANSPORT_STOPPED",
        "PAGINATION_UNVERIFIED",
        "PLAN_BINDING_MISMATCH",
    }
)


def fixed_plan() -> bytes:
    """Reproduce the accepted candidate bytes without filesystem or environment access."""
    value = {
        "schema_version": "research_demand_capture_v1",
        "capture_id": "aapl_2026_10_09_candidate",
        "purpose_ref": "issue5_raw_comparison_archive_smoke",
        "storage_root_ref": "private_archive_root_pending",
        "provider": "massive",
        "subscription_selected": "stocks_starter",
        "symbol": "AAPL",
        "start_at": "2026-10-09T13:30:00+00:00",
        "end_at": "2026-10-09T20:00:00+00:00",
        "products": ["raw", "comparison"],
        "page_limit": 5000,
        "max_requests": 8,
        "max_source_bytes": 524288,
        "max_total_bytes": 4194304,
        "timeout_seconds": 20,
        "minimum_interval_seconds": 1,
        "retention_days": 30,
        "prior_manifest_sha256": None,
        "refresh_reason_ref": None,
    }
    encoded = (json.dumps(value, indent=2) + "\n").encode()
    if digest(encoded) != PLAN_SHA256:
        deny("PLAN_BINDING_MISMATCH", "plan")
    return encoded


def _registered(review: str | None) -> capture._ExecutionPermit:
    # No root environment/path or credential access before the deployed registry.
    if type(review) is not str or re.fullmatch(r"[0-9a-f]{64}", review) is None:
        deny("EXECUTION_SCOPE_REVIEW_PENDING", "review")
    permit = next(
        (p for p in capture._ACCEPTED_EXECUTION_REVIEWS if p.review_sha256 == review), None
    )
    if permit is None:
        deny("EXECUTION_SCOPE_REVIEW_PENDING", "review")
    if (
        not permit.account_evidence_ref
        or not permit.license_evidence_ref
        or capture._now() >= permit.valid_until
    ):
        deny("EXECUTION_REVIEW_EXPIRED_OR_REVOKED", "review")
    if permit.plan_sha256 != digest(fixed_plan()):
        deny("EXECUTION_SCOPE_BINDING_MISMATCH", "plan")
    return permit


def _root_details(root: Path) -> dict:
    if not capture._posix_runtime():
        deny("PRIVATE_ACL_PLATFORM_UNSUPPORTED", "root")
    private_root(root)
    info = root.stat()
    if info.st_uid != capture._owner_uid():
        deny("PRIVATE_ROOT_CHANGED", "root")
    if info.st_mode & 0o777 != 0o700:
        deny("PRIVATE_ACL_REQUIRED", "root")
    return {
        "root_path_sha256": digest(str(root).encode()),
        "root_identity_sha256": capture.root_identity(root),
    }


def inspect_root(root: Path) -> dict:
    """Explicit local root metadata inspection only, not account/license authentication."""
    return {
        **audit("ROOT_INSPECTED_NOT_AUTHORIZED", "inspection_only"),
        **_root_details(root),
        "observed_at": capture._now().isoformat(),
    }


def preflight(review: str | None) -> Path:
    permit = _registered(review)
    if not capture._posix_runtime():
        deny("PRIVATE_ACL_PLATFORM_UNSUPPORTED", "root")
    value = os.environ.get(ROOT_ENVIRONMENT)
    if type(value) is not str or not 0 < len(value) <= 4096 or any(ord(c) < 32 for c in value):
        deny("PRIVATE_ROOT_REQUIRED", "root")
    root = Path(value)
    details = _root_details(root)
    if (
        details["root_path_sha256"] != permit.root_path_sha256
        or details["root_identity_sha256"] != permit.root_identity_sha256
    ):
        deny("EXECUTION_SCOPE_BINDING_MISMATCH", "root")
    # Check the clock and registry again after local root inspection.
    if _registered(review) != permit:
        deny("EXECUTION_REVIEW_EXPIRED_OR_REVOKED", "review")
    decode_demand_plan(fixed_plan(), capture._now())
    return root


def acquire(review: str | None) -> None:
    root = preflight(review)
    # The accepted implementation independently rechecks review/root before key access
    # and request/publication, preserving reservation, immutable cache and stop semantics.
    capture.capture_real_slice(fixed_plan(), root, reviewed_execution_sha256=review)


def audit(code: str, status: str, *, may_acquire: bool = False) -> dict:
    return {
        "schema_version": "research_private_operator_audit_v1",
        "status": status,
        "code": code,
        "may_acquire": may_acquire,
        "license_determination": "not_performed",
        **dict.fromkeys(FLAGS, False),
    }


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        deny("INVALID_CLI_ARGUMENTS", "arguments")


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _Parser(description="Private bounded capture; no self-granted execution review")
        parser.add_argument(
            "--mode",
            choices=("inspect-root", "preflight", "capture", "backtest-status"),
            required=True,
        )
        parser.add_argument("--root", type=Path)
        parser.add_argument("--review-sha256")
        args = parser.parse_args(argv)
        if args.mode == "inspect-root":
            if args.root is None or args.review_sha256 is not None:
                deny("INVALID_CLI_ARGUMENTS", "arguments")
            result = inspect_root(args.root)
        elif args.mode == "backtest-status":
            if args.root is not None or args.review_sha256 is not None:
                deny("INVALID_CLI_ARGUMENTS", "arguments")
            result = audit("REAL_BACKTEST_VERIFICATION_PENDING", "blocked")
        else:
            if args.root is not None:
                deny("INVALID_CLI_ARGUMENTS", "arguments")
            if args.mode == "preflight":
                preflight(args.review_sha256)
                result = audit(
                    "EXECUTION_SCOPE_REGISTERED", "capture_preflight_pass", may_acquire=True
                )
            else:
                acquire(args.review_sha256)
                result = audit("CAPTURE_COMPLETE_UNTRUSTED", "captured_untrusted")
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 2 if result["status"] == "blocked" else 0
    except DemandError as error:
        code = error.code if error.code in PUBLIC_CODES else "OPERATION_STOPPED"
    except (ValueError, TypeError, OSError, AttributeError, RecursionError):
        code = "INVALID_OPERATOR_INPUT"
    print(json.dumps(audit(code, "blocked"), sort_keys=True, separators=(",", ":")))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
