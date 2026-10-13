"""Five fixed provider requests on reviewed public runners, with private immutable readback."""

from __future__ import annotations

import base64
import json
import math
import os
import re
import runpy
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, build_opener

# -I intentionally excludes the working directory from imports. Load only this reviewed sibling.
m = SimpleNamespace(**runpy.run_path(str(Path(__file__).with_name("private_archive_migration.py"))))
START = 1_790_602_200_000
END = 1_790_625_599_999
PRODUCTS = (
    {"id": "aapl-reference", "ticker": "AAPL", "kind": "reference"},
    {"id": "aapl-raw", "ticker": "AAPL", "kind": "raw"},
    {"id": "aapl-comparison", "ticker": "AAPL", "kind": "comparison"},
    {"id": "spy-raw", "ticker": "SPY", "kind": "raw"},
    {"id": "spy-comparison", "ticker": "SPY", "kind": "comparison"},
)
PREFIX = "research/data/pull_test_v1/"
PLAN_HASH = m.sha256(m.canonical({"products": PRODUCTS, "start": START, "end": END}))
FALSE_FLAGS = {**dict.fromkeys(m.FLAGS, False), "engine_invoked": False}
CREDENTIAL_PATTERN = re.compile(rb'(?i)(?:apiKey|api_key|access_token|authorization)\s*(?:=|"\s*:)')


def url(product: dict) -> str:
    m.require(product in PRODUCTS)
    if product["kind"] == "reference":
        return "https://api.massive.com/v3/reference/tickers/AAPL?date=2026-10-09"
    adjusted = "true" if product["kind"] == "comparison" else "false"
    return (
        f"https://api.massive.com/v2/aggs/ticker/{product['ticker']}/range/1/minute/{START}/{END}"
        f"?adjusted={adjusted}&sort=asc&limit=5000"
    )


def ref(product: dict, reservation: bool = False) -> str:
    m.require(product in PRODUCTS)
    family = "reservations" if reservation else "data"
    return f"research-{family}/pull-test-v1-{product['id']}"


def pairs(items):
    result = {}
    for key, value in items:
        m.require(key not in result)
        result[key] = value
    return result


def decode(body: bytes) -> dict:
    result = json.loads(body, object_pairs_hook=pairs, parse_constant=lambda _: m.require(False))
    m.require(type(result) is dict)
    return result


def numeric(value: object, positive: bool = False) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and (value > 0 if positive else value >= 0)
    )


def checks(product: dict, body: bytes, status: int) -> dict:
    result = {
        "schema_version": "bounded_pull_checks_v1",
        "plan_sha256": PLAN_HASH,
        "product": product["id"],
        "accepted": False,
        "code": "HTTP_UNEXPECTED" if status != 200 else "PAYLOAD_INVALID",
        "row_count": 0,
        "strategy_performance_computed": False,
        **FALSE_FLAGS,
    }
    if status != 200:
        return result
    try:
        value = decode(body)
        m.require(value.get("status") in {"OK", "DELAYED"} and not value.get("next_url"))
        if product["kind"] == "reference":
            facts = value.get("results", {})
            m.require(
                type(facts) is dict
                and facts.get("ticker") == "AAPL"
                and facts.get("cik") == "0000320193"
                and facts.get("type") == "CS"
                and facts.get("market") == "stocks"
                and facts.get("locale") == "us"
                and facts.get("currency_name") == "usd"
                and facts.get("primary_exchange") == "XNAS"
                and facts.get("active") is True
                and numeric(facts.get("market_cap"), True)
                and 10_500_000_000 <= facts["market_cap"] <= 1e18
            )
            count = 1
        else:
            adjusted = product["kind"] == "comparison"
            rows = value.get("results", [])
            m.require(
                value.get("ticker") == product["ticker"]
                and value.get("adjusted") is adjusted
                and type(rows) is list
                and len(rows) == 390
                and type(value.get("resultsCount")) is int
                and value["resultsCount"] == 390
            )
            for i, row in enumerate(rows):
                m.require(
                    type(row) is dict
                    and type(row.get("t")) is int
                    and row["t"] == START + i * 60_000
                    and all(numeric(row.get(key), True) for key in ("o", "h", "l", "c"))
                    and numeric(row.get("v"))
                    and row["h"] >= max(row["o"], row["c"], row["l"])
                    and row["l"] <= min(row["o"], row["c"])
                )
            count = len(rows)
    except Exception:
        return result  # Persist the rejected original bytes privately, without logging their text.
    result.update(accepted=True, code="STRUCTURE_ACCEPTED", row_count=count)
    return result


def secret_free(body: bytes, credentials: tuple[str, ...]) -> None:
    m.require(0 < len(body) <= m.FILE_LIMIT and not CREDENTIAL_PATTERN.search(body))
    m.require(all(not credential or credential.encode() not in body for credential in credentials))


def provider_get(product: dict, key: str) -> tuple[int, bytes]:
    m.require(0 < len(key) <= 4096 and not any(c.isspace() for c in key))
    request = Request(
        url(product), headers={"Authorization": "Bearer " + key, "Accept": "application/json"}
    )
    try:
        with build_opener(m.NoRedirect()).open(request, timeout=20) as response:
            status, body = response.status, response.read(m.FILE_LIMIT + 1)
    except HTTPError as error:
        with error:
            status, body = error.code, error.read(m.FILE_LIMIT + 1)
    m.require(type(status) is int and 100 <= status <= 599)
    secret_free(body, (key,))
    return status, body


def create(destination, name: str, payloads: dict[str, bytes], parent: str) -> str:
    entries = []
    for path, body in sorted(payloads.items()):
        secret_free(body, (destination.token,))
        blob = destination.write(
            "/git/blobs", {"encoding": "base64", "content": base64.b64encode(body).decode()}
        )
        m.require(blob.get("sha") == m.git_blob(body))
        entries.append({"path": path, "sha": blob["sha"], "type": "blob", "mode": "100644"})
    tree = destination.write("/git/trees", {"tree": entries})
    commit = destination.write(
        "/git/commits",
        {
            "message": "archive: bounded pull test v1",
            "tree": m.sha(tree["sha"]),
            "parents": [m.sha(parent)],
        },
    )
    revision = m.sha(commit["sha"])
    destination.write("/git/refs", {"ref": "refs/heads/" + name, "sha": revision})
    m.require(destination.ref(name) == revision)
    return revision


def read(destination, name: str, paths: set[str]) -> tuple[dict[str, bytes], str]:
    revision = destination.ref(name)
    m.require(revision is not None)
    commit = destination.request("GET", "/git/commits/" + m.sha(revision))
    m.require(commit is not None and len(commit["parents"]) == 1)
    parent = m.sha(commit["parents"][0]["sha"])
    tree = destination.request("GET", "/git/trees/" + m.sha(commit["tree"]["sha"]) + "?recursive=1")
    m.require(tree is not None and tree.get("truncated") is False)
    rows = [row for row in tree["tree"] if row["type"] != "tree"]
    m.require(len(rows) == len(paths) and {row["path"] for row in rows} == paths)
    m.require(all(row["type"] == "blob" and row["mode"] == "100644" for row in rows))
    return {row["path"]: destination.blob(row["sha"], row["size"]) for row in rows}, parent


def reservation(product: dict, source: str) -> bytes:
    return m.canonical(
        {
            "schema_version": "bounded_pull_reservation_v1",
            "plan_sha256": PLAN_HASH,
            "product": product["id"],
            "source_repository": m.CODE,
            "source_commit": m.sha(source),
            "destination_repository": m.DESTINATION,
            "request_url": url(product),
            "maximum_requests": 1,
            "maximum_response_bytes": m.FILE_LIMIT,
            "retry_allowed": False,
            **FALSE_FLAGS,
        }
    )


def verify_product(destination, product: dict) -> dict:
    prefix = PREFIX + product["id"] + "/"
    bodies, parent = read(
        destination,
        ref(product),
        {prefix + name for name in ("response.json", "receipt.json", "checks.json")},
    )
    receipt = decode(bodies[prefix + "receipt.json"])
    source = m.sha(receipt.get("source_commit"))
    hold, hold_parent = read(destination, ref(product, True), {"reservation.json"})
    m.require(hold_parent == m.BOOTSTRAP and parent == destination.ref(ref(product, True)))
    m.require(hold["reservation.json"] == reservation(product, source))
    body = bodies[prefix + "response.json"]
    secret_free(body, (destination.token,))
    m.require(
        receipt.get("schema_version") == "bounded_pull_receipt_v1"
        and receipt.get("plan_sha256") == PLAN_HASH
        and receipt.get("product") == product["id"]
        and receipt.get("source_repository") == m.CODE
        and receipt.get("destination_repository") == m.DESTINATION
        and receipt.get("request_url") == url(product)
        and receipt.get("reservation_commit") == parent
        and receipt.get("source_sha256") == m.sha256(body)
        and type(receipt.get("size_bytes")) is int
        and receipt["size_bytes"] == len(body)
        and type(receipt.get("http_status")) is int
        and 100 <= receipt["http_status"] <= 599
        and all(receipt.get(flag) is False for flag in FALSE_FLAGS)
        and receipt.get("PIT_publication_clock") is None
    )
    started = datetime.fromisoformat(receipt["request_started_at"].replace("Z", "+00:00"))
    finished = datetime.fromisoformat(receipt["response_received_at"].replace("Z", "+00:00"))
    m.require(
        started.utcoffset() is not None and finished.utcoffset() is not None and started <= finished
    )
    result = checks(product, body, receipt["http_status"])
    m.require(bodies[prefix + "checks.json"] == m.canonical(result))
    return result


def stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def execute(
    destination, source: str, get_key, fetch=provider_get, verify_only: bool = False
) -> dict:
    destination.visibility(True)
    requests = reused = bars = 0
    for product in PRODUCTS:
        if destination.ref(ref(product)) is not None:
            result = verify_product(destination, product)
            reused += 1
        else:
            m.require(not verify_only)
            # An existing reservation with no capture means unknown outcome. Never request again.
            m.require(destination.ref(ref(product, True)) is None)
            key = get_key()
            m.require(0 < len(key) <= 4096 and not any(c.isspace() for c in key))
            hold = create(
                destination,
                ref(product, True),
                {"reservation.json": reservation(product, source)},
                m.BOOTSTRAP,
            )
            started = stamp()
            status, body = fetch(product, key)
            requests += 1
            finished = stamp()
            secret_free(body, (key, destination.token))
            result = checks(product, body, status)
            receipt = {
                "schema_version": "bounded_pull_receipt_v1",
                "plan_sha256": PLAN_HASH,
                "product": product["id"],
                "source_repository": m.CODE,
                "source_commit": m.sha(source),
                "destination_repository": m.DESTINATION,
                "reservation_commit": hold,
                "request_url": url(product),
                "http_status": status,
                "request_started_at": started,
                "response_received_at": finished,
                "PIT_publication_clock": None,
                "source_sha256": m.sha256(body),
                "size_bytes": len(body),
                **FALSE_FLAGS,
            }
            prefix = PREFIX + product["id"] + "/"
            create(
                destination,
                ref(product),
                {
                    prefix + "response.json": body,
                    prefix + "receipt.json": m.canonical(receipt),
                    prefix + "checks.json": m.canonical(result),
                },
                hold,
            )
            m.require(verify_product(destination, product) == result)
        m.require(result["accepted"] is True)
        if product["kind"] != "reference":
            bars += result["row_count"]
    destination.visibility(True)
    return {
        "status": "PULL_TEST_VERIFIED",
        "vendor_requests": requests,
        "reused_products": reused,
        "products": len(PRODUCTS),
        "bar_records": bars,
        **FALSE_FLAGS,
    }


def guarded() -> str:
    source = m.sha(os.environ.get("PRIVATE_PULL_REVIEWED_SHA", ""))
    m.require(
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("GITHUB_REPOSITORY") == m.CODE
        and os.environ.get("GITHUB_ACTOR") == "dczhhh"
        and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_SHA") == source
        and os.environ.get("PRIVATE_PULL_ENABLED") == "true"
        and os.environ.get("ACTIONS_STEP_DEBUG", "").lower() != "true"
        and os.environ.get("ACTIONS_RUNNER_DEBUG", "").lower() != "true"
    )
    m.GitHub(m.CODE).visibility(False)
    return source


def main() -> int:
    phase = "context"
    try:
        m.require(sys.argv[1:] in [["capture"], ["verify"]])
        source = guarded()
        phase = "private_archive"
        destination = m.GitHub(
            m.DESTINATION, os.environ.get("ARCHIVE_WRITE_TOKEN", ""), writable=True
        )
        result = execute(
            destination,
            source,
            lambda: os.environ.get("MASSIVE_API_KEY", ""),
            verify_only=sys.argv[1] == "verify",
        )
    except Exception as error:
        code = (
            error.code
            if isinstance(error, m.Stopped)
            and re.fullmatch(r"GITHUB_HTTP_(?:401|403|404|429|FAILED)|CHECK_FAILED", error.code)
            else "CHECK_FAILED"
        )
        print(
            json.dumps(
                {"status": "PULL_TEST_STOPPED", "phase": phase, "code": code}, separators=(",", ":")
            )
        )
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
