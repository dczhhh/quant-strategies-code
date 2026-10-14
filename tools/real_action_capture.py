"""Archive reviewed real-event inputs privately, never certify PIT or account evidence.

Owner authorization removes the old five-request scope for this separate operator.
Requests are still finite, immutable and purpose-bound. Original bytes never enter
public logs, artifacts or a ChatGPT runtime. Replays read existing captures first.
"""

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

b = SimpleNamespace(**runpy.run_path(str(Path(__file__).with_name("bounded_pull_test.py"))))
m = b.m
GROUPS = {
    "nvda-2024": ("NVDA",),
    "ge-2021": ("GE",),
    "td-source": ("TD",),
    "etf-1": ("XLB", "XLC", "XLE"),
    "etf-2": ("XLF", "XLI", "XLK"),
    "etf-3": ("XLP", "XLRE", "XLU"),
    "etf-4": ("XLV", "XLY", "SPY"),
}
PREFIX = "research/data/real_actions_v1/"
OFFICIAL = {
    "nvda-2024": "https://investor.nvidia.com/news/press-release-details/2024/NVIDIA-Announces-Financial-Results-for-First-Quarter-Fiscal-2025/",
    "ge-2021": "https://www.ge.com/news/press-releases/ge-completes-one-for-eight-reverse-stock-split",
}
POLICY = {
    "provider": "Massive",
    "bars_adjustment": "split_only_not_dividend_total_return",
    "anchor": "provider_current_share_basis_at_capture_not_historical_PIT",
    "definition_url": "https://massive.com/knowledge-base/article/is-massives-stock-data-adjusted-for-splits-or-dividends",
    "historical_known_at": None,
    "broker_statement_supplied": False,
    "certification": "UNVERIFIED",
    **b.FALSE_FLAGS,
}


def products(group: str) -> tuple[dict, ...]:
    m.require(group in GROUPS)
    result = []
    for ticker in GROUPS[group]:
        start, end = (
            ("2024-06-03", "2024-06-14")
            if ticker == "NVDA"
            else ("2021-07-26", "2021-08-06")
            if ticker == "GE"
            else ("2024-09-16", "2024-09-27")
        )
        if group != "td-source":
            for kind, adjusted in (("raw", "false"), ("split-adjusted", "true")):
                result.append(
                    {
                        "id": f"{group}-{ticker.lower()}-{kind}",
                        "ticker": ticker,
                        "kind": "bars",
                        "adjusted": adjusted == "true",
                        "start": start,
                        "end": end,
                        "url": f"https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}?adjusted={adjusted}&sort=asc&limit=5000",
                    }
                )
        if group in {"nvda-2024", "ge-2021"}:
            result.append(
                {
                    "id": f"{group}-splits",
                    "ticker": ticker,
                    "kind": "splits",
                    "url": f"https://api.massive.com/stocks/v1/splits?ticker={ticker}&execution_date.gte={start}&execution_date.lte={end}&sort=execution_date.asc&limit=100",
                }
            )
        if group != "ge-2021":
            result.append(
                {
                    "id": f"{group}-{ticker.lower()}-dividends",
                    "ticker": ticker,
                    "kind": "dividends",
                    "url": f"https://api.massive.com/stocks/v1/dividends?ticker={ticker}&ex_dividend_date.gte={start}&ex_dividend_date.lte={end}&sort=ex_dividend_date.asc&limit=100",
                }
            )
        if group in {"nvda-2024", "ge-2021", "td-source"}:
            result.append(
                {
                    "id": f"{group}-identity",
                    "ticker": ticker,
                    "kind": "identity",
                    "url": f"https://api.massive.com/v3/reference/tickers/{ticker}?date={start}",
                }
            )
    if group in OFFICIAL:
        result.append(
            {
                "id": f"{group}-issuer",
                "ticker": GROUPS[group][0],
                "kind": "official",
                "url": OFFICIAL[group],
            }
        )
    return tuple(result)


PLAN = {"groups": {name: products(name) for name in GROUPS}, "policy": POLICY}
PLAN_HASH = m.sha256(m.canonical(PLAN))


def checked_product(product: dict) -> None:
    m.require(any(product == p for group in GROUPS for p in products(group)))


def refs(product: dict, reservation: bool = False) -> str:
    checked_product(product)
    family = "reservations" if reservation else "data"
    return f"research-{family}/real-actions-v1-{product['id']}"


def structure(product: dict, body: bytes, status: int) -> bool:
    """Transport/schema only. No current official page becomes a PIT certificate."""
    checked_product(product)
    if status != 200:
        return False
    if product["kind"] == "official":
        return len(body) > 1000 and b"<html" in body.lower()
    try:
        data = b.decode(body)
        m.require(data.get("status") in {"OK", "DELAYED"} and not data.get("next_url"))
        rows = data.get("results")
        if product["kind"] == "identity":
            return type(rows) is dict and rows.get("ticker") == product["ticker"]
        m.require(type(rows) is list and len(rows) > 0)
        if product["kind"] == "bars":
            m.require(
                data.get("ticker") == product["ticker"]
                and data.get("adjusted") is product["adjusted"]
            )
            stamps = [r["t"] for r in rows]
            m.require(all(type(t) is int for t in stamps) and stamps == sorted(set(stamps)))
            m.require(
                all(
                    product["start"]
                    <= datetime.fromtimestamp(t / 1000, UTC).date().isoformat()
                    <= product["end"]
                    for t in stamps
                )
            )
            for r in rows:
                m.require(
                    all(b.numeric(r.get(k), True) for k in ("o", "h", "l", "c"))
                    and b.numeric(r.get("v"))
                    and r["l"] <= min(r["o"], r["c"])
                    and r["h"] >= max(r["o"], r["c"])
                )
        else:
            m.require(all(r.get("ticker") == product["ticker"] for r in rows))
        return True
    except Exception:
        return False


def get(product: dict, key: str) -> tuple[int, bytes]:
    checked_product(product)
    headers = {"Accept": "text/html" if product["kind"] == "official" else "application/json"}
    if product["kind"] != "official":
        m.require(0 < len(key) <= 4096 and not any(c.isspace() for c in key))
        headers["Authorization"] = "Bearer " + key
    request = Request(product["url"], headers=headers)
    try:
        with build_opener(m.NoRedirect()).open(request, timeout=20) as response:
            status, body = response.status, response.read(m.FILE_LIMIT + 1)
    except HTTPError as error:
        with error:
            status, body = error.code, error.read(m.FILE_LIMIT + 1)
    b.secret_free(body, (key,))
    return status, body


def reservation(product: dict, source: str) -> bytes:
    return m.canonical(
        {
            "schema_version": "real_action_reservation_v1",
            "source_commit": source,
            "plan_sha256": PLAN_HASH,
            "product": product,
            "single_attempt": True,
            "unknown_outcome_must_not_be_reset": True,
            **POLICY,
        }
    )


def verify_product(destination, product: dict, source: str) -> dict:
    prefix = PREFIX + product["id"] + "/"
    payload, parent = b.read(
        destination,
        refs(product),
        {prefix + x for x in ("response.bin", "receipt.json", "checks.json")},
    )
    budget, budget_parent = b.read(destination, refs(product, True), {prefix + "reservation.json"})
    m.require(parent == destination.ref(refs(product, True)))
    body, receipt = payload[prefix + "response.bin"], b.decode(payload[prefix + "receipt.json"])
    m.require(source == m.sha(source))
    archived_source = m.sha(receipt["source_commit"])
    m.require(
        budget_parent == m.BOOTSTRAP
        and budget[prefix + "reservation.json"] == reservation(product, archived_source)
    )
    b.secret_free(body, (destination.token,))
    m.require(
        receipt["plan_sha256"] == PLAN_HASH
        and receipt["product"] == product
        and receipt["body_sha256"] == m.sha256(body)
        and receipt["body_bytes"] == len(body)
        and receipt["policy"] == POLICY
    )
    check = {
        "schema_version": "real_action_structure_v1",
        "structure_accepted": structure(product, body, receipt["http_status"]),
        "certification": "UNVERIFIED",
        "plan_sha256": PLAN_HASH,
        **b.FALSE_FLAGS,
    }
    m.require(payload[prefix + "checks.json"] == m.canonical(check))
    destination.visibility(True)
    return check


def execute(
    group: str, destination, source: str, key_factory, *, verify_only=False, fetch=get
) -> dict:
    destination.visibility(True)
    made, reused, accepted = 0, 0, 0
    for product in products(group):
        prefix = PREFIX + product["id"] + "/"
        if destination.ref(refs(product)) is not None:
            reused += 1
        else:
            m.require(not verify_only and destination.ref(refs(product, True)) is None)
            budget = b.create(
                destination,
                refs(product, True),
                {prefix + "reservation.json": reservation(product, source)},
                m.BOOTSTRAP,
            )
            key = "" if product["kind"] == "official" else key_factory()
            status, body = fetch(product, key)
            made += product["kind"] != "official"
            receipt = {
                "schema_version": "real_action_receipt_v1",
                "source_commit": source,
                "plan_sha256": PLAN_HASH,
                "product": product,
                "http_status": status,
                "fetched_at": datetime.now(UTC).isoformat(),
                "body_sha256": m.sha256(body),
                "body_bytes": len(body),
                "policy": POLICY,
            }
            check = {
                "schema_version": "real_action_structure_v1",
                "structure_accepted": structure(product, body, status),
                "certification": "UNVERIFIED",
                "plan_sha256": PLAN_HASH,
                **b.FALSE_FLAGS,
            }
            b.create(
                destination,
                refs(product),
                {
                    prefix + "response.bin": body,
                    prefix + "receipt.json": m.canonical(receipt),
                    prefix + "checks.json": m.canonical(check),
                },
                budget,
            )
        accepted += verify_product(destination, product, source)["structure_accepted"]
    return {
        "status": "CAPTURE_READBACK_COMPLETE_NOT_CERTIFIED",
        "group": group,
        "vendor_requests": made,
        "reused_products": reused,
        "products": len(products(group)),
        "structure_accepted": accepted,
        "certification": "UNVERIFIED",
        **b.FALSE_FLAGS,
    }


def guarded() -> str:
    source = m.sha(os.environ.get("REAL_ACTION_REVIEWED_SHA", ""))
    m.require(
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("GITHUB_REPOSITORY") == m.CODE
        and os.environ.get("GITHUB_ACTOR") == "dczhhh"
        and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_SHA") == source
        and os.environ.get("REAL_ACTION_ENABLED") == "true"
        and os.environ.get("ACTIONS_STEP_DEBUG", "").lower() != "true"
        and os.environ.get("ACTIONS_RUNNER_DEBUG", "").lower() != "true"
    )
    m.GitHub(m.CODE).visibility(False)
    return source


def main() -> int:
    try:
        m.require(len(sys.argv) == 3 and sys.argv[1] in {"capture", "verify"})
        source = guarded()
        destination = m.GitHub(
            m.DESTINATION,
            os.environ.get("ARCHIVE_WRITE_TOKEN", ""),
            writable=sys.argv[1] == "capture",
        )
        result = execute(
            sys.argv[2],
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
                    "status": "REAL_ACTION_CAPTURE_STOPPED",
                    "code": code,
                    "certification": "UNVERIFIED",
                },
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
