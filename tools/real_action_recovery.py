"""One reviewed append-only recovery for the interrupted ETF group; never reset v1."""

from __future__ import annotations

import json
import os
import re
import runpy
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, build_opener

p = SimpleNamespace(**runpy.run_path(str(Path(__file__).with_name("real_action_capture.py"))))
b, m = p.b, p.m
GROUP = "etf-1"
PRODUCTS = p.products(GROUP)[1:]
PREFIX = "research/data/real_actions_recovery_v1/"
ORIGINAL_RAW_COMMIT = "c253894155bfea290234a359e96d9b67613a4b1a"
ORIGINAL_RAW_BUDGET = "5bf1400916c32d463e332caf9eb37018d1b1efd2"
ORIGINAL_UNKNOWN_BUDGET = "a209fab014e3e78cf39ca7302682c7bb2c308638"
ORIGINAL_SOURCE = "6b4298673410703f136c380b0b55bfdaa0fbf789"
POLICY = {
    **p.POLICY,
    "recovery_scope": "eight_missing_etf_1_products_only",
    "original_raw_commit": ORIGINAL_RAW_COMMIT,
    "original_raw_budget": ORIGINAL_RAW_BUDGET,
    "original_unknown_budget": ORIGINAL_UNKNOWN_BUDGET,
    "original_plan_sha256": p.PLAN_HASH,
    "original_source": ORIGINAL_SOURCE,
    "previous_unknown_request_count": 1,
    "previous_unknown_outcome_resolved": False,
    "single_attempt_per_new_reservation": True,
    "automatic_retry_allowed": False,
}
PLAN_HASH = m.sha256(m.canonical({"products": PRODUCTS, "policy": POLICY}))


def checked(product: dict) -> None:
    m.require(product in PRODUCTS)


def ref(product: dict, budget: bool = False) -> str:
    checked(product)
    family = "reservations" if budget else "data"
    return f"research-{family}/real-actions-recovery-v1-{product['id']}"


def originals(destination, source: str) -> None:
    """Require the precise interrupted state; only read old namespaces."""
    destination.visibility(True)
    for index, product in enumerate(p.products(GROUP)):
        m.require(destination.ref(p.refs(product)) == (ORIGINAL_RAW_COMMIT if index == 0 else None))
        expected = (
            ORIGINAL_RAW_BUDGET if index == 0 else ORIGINAL_UNKNOWN_BUDGET if index == 1 else None
        )
        m.require(destination.ref(p.refs(product, True)) == expected)
    m.require(p.verify_product(destination, p.products(GROUP)[0], source)["structure_accepted"])


def predecessor(product: dict) -> dict:
    checked(product)
    return {
        "original_capture_ref": p.refs(product),
        "original_reservation_ref": p.refs(product, True),
        "original_reservation_commit": ORIGINAL_UNKNOWN_BUDGET if product == PRODUCTS[0] else None,
    }


def reservation(product: dict, source: str) -> bytes:
    return m.canonical(
        {
            "schema_version": "real_action_recovery_reservation_v1",
            "source_commit": m.sha(source),
            "plan_sha256": PLAN_HASH,
            "product": product,
            "predecessor": predecessor(product),
            "unknown_outcome_must_not_be_reset": True,
            **POLICY,
        }
    )


def checks(product: dict, body: bytes, status: int) -> dict:
    return {
        "schema_version": "real_action_recovery_structure_v1",
        "plan_sha256": PLAN_HASH,
        "structure_accepted": p.structure(product, body, status),
        "certification": "UNVERIFIED",
        **b.FALSE_FLAGS,
    }


def get(product: dict, key: str) -> tuple[int, bytes]:
    checked(product)
    m.require(0 < len(key) <= 4096 and not any(c.isspace() for c in key))
    request = Request(
        product["url"], headers={"Accept": "application/json", "Authorization": "Bearer " + key}
    )
    try:
        with build_opener(m.NoRedirect()).open(request, timeout=45) as response:
            status, body = response.status, response.read(m.FILE_LIMIT + 1)
    except HTTPError as error:
        with error:
            status, body = error.code, error.read(m.FILE_LIMIT + 1)
    m.require(type(status) is int and 100 <= status <= 599)
    b.secret_free(body, (key,))
    return status, body


def verify_product(destination, product: dict) -> dict:
    checked(product)
    prefix = PREFIX + product["id"] + "/"
    payload, parent = b.read(
        destination,
        ref(product),
        {prefix + x for x in ("response.bin", "receipt.json", "checks.json")},
    )
    budget, budget_parent = b.read(destination, ref(product, True), {prefix + "reservation.json"})
    body, receipt = payload[prefix + "response.bin"], b.decode(payload[prefix + "receipt.json"])
    source = m.sha(receipt["source_commit"])
    b.secret_free(body, (destination.token,))
    m.require(
        parent == destination.ref(ref(product, True))
        and budget_parent == m.BOOTSTRAP
        and budget[prefix + "reservation.json"] == reservation(product, source)
        and receipt["schema_version"] == "real_action_recovery_receipt_v1"
        and receipt["product"] == product
        and receipt["plan_sha256"] == PLAN_HASH
        and receipt["predecessor"] == predecessor(product)
        and receipt["policy"] == POLICY
        and receipt["body_bytes"] == len(body)
        and receipt["body_sha256"] == m.sha256(body)
    )
    check = checks(product, body, receipt["http_status"])
    m.require(payload[prefix + "checks.json"] == m.canonical(check))
    return check


def execute(destination, source: str, key_factory, *, verify_only=False, fetch=get) -> dict:
    m.require(source == m.sha(source))
    originals(destination, source)
    made, reused, accepted = 0, 0, 1
    for product in PRODUCTS:
        prefix = PREFIX + product["id"] + "/"
        if destination.ref(ref(product)) is not None:
            reused += 1
        else:
            m.require(not verify_only and destination.ref(ref(product, True)) is None)
            budget = b.create(
                destination,
                ref(product, True),
                {prefix + "reservation.json": reservation(product, source)},
                m.BOOTSTRAP,
            )
            key = key_factory()
            status, body = fetch(product, key)
            made += 1
            m.require(type(status) is int and 100 <= status <= 599)
            b.secret_free(body, (key, destination.token))
            receipt = {
                "schema_version": "real_action_recovery_receipt_v1",
                "source_commit": source,
                "plan_sha256": PLAN_HASH,
                "product": product,
                "predecessor": predecessor(product),
                "policy": POLICY,
                "http_status": status,
                "fetched_at": datetime.now(UTC).isoformat(),
                "body_bytes": len(body),
                "body_sha256": m.sha256(body),
            }
            b.create(
                destination,
                ref(product),
                {
                    prefix + "response.bin": body,
                    prefix + "receipt.json": m.canonical(receipt),
                    prefix + "checks.json": m.canonical(checks(product, body, status)),
                },
                budget,
            )
        accepted += verify_product(destination, product)["structure_accepted"]
    originals(destination, source)
    return {
        "status": "RECOVERY_READBACK_COMPLETE_NOT_CERTIFIED",
        "group": GROUP,
        "vendor_requests": made,
        "reused_recovery_products": reused,
        "reused_original_products": 1,
        "products": len(PRODUCTS) + 1,
        "structure_accepted": accepted,
        "original_refs_preserved": True,
        "previous_unknown_outcome_resolved": False,
        "certification": "UNVERIFIED",
        **b.FALSE_FLAGS,
    }


def guarded() -> str:
    source = m.sha(os.environ.get("REAL_ACTION_RECOVERY_REVIEWED_SHA", ""))
    m.require(
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("GITHUB_REPOSITORY") == m.CODE
        and os.environ.get("GITHUB_ACTOR") == "dczhhh"
        and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_SHA") == source
        and os.environ.get("REAL_ACTION_RECOVERY_ENABLED") == "true"
        and os.environ.get("ACTIONS_STEP_DEBUG", "").lower() != "true"
        and os.environ.get("ACTIONS_RUNNER_DEBUG", "").lower() != "true"
    )
    m.GitHub(m.CODE).visibility(False)
    return source


def main() -> int:
    try:
        m.require(len(sys.argv) == 2 and sys.argv[1] in {"capture", "verify"})
        source = guarded()
        destination = m.GitHub(
            m.DESTINATION,
            os.environ.get("ARCHIVE_WRITE_TOKEN", ""),
            writable=sys.argv[1] == "capture",
        )
        result = execute(
            destination,
            source,
            lambda: os.environ.get("MASSIVE_API_KEY", ""),
            verify_only=sys.argv[1] == "verify",
        )
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except Exception as error:
        code = (
            error.code
            if isinstance(error, m.Stopped)
            and re.fullmatch(r"GITHUB_HTTP_(?:401|403|404|429|FAILED)|CHECK_FAILED", error.code)
            else "CHECK_FAILED"
        )
        print(
            json.dumps(
                {
                    "status": "REAL_ACTION_RECOVERY_STOPPED",
                    "code": code,
                    "certification": "UNVERIFIED",
                },
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
