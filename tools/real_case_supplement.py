"""Fixed window-covered reverse-split/reorganization inputs, privately archived only."""

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
PREFIX = "research/data/real_case_supplement_v1/"
GROUPS = ("sqqq-reverse", "wolf-reorganization")
POLICY = {
    **p.POLICY,
    "window_review_date": "2026-10-14",
    "starter_bars_history_years": 5,
    "scope": "SQQQ_split_and_owner_named_WOLF_reorganization_validation_only",
    "SQQQ_expected_first_post_split_session": "2024-11-07",
    "SQQQ_expected_new_old_ratio": "1/5",
    "SQQQ_fractional_cash_credit_supported": False,
    "WOLF_expected_event_kind": "unsupported_reorganization_not_ordinary_split",
    "broker_statement_included": False,
}


def products(group: str) -> tuple[dict, ...]:
    m.require(group in GROUPS)
    ticker, start, end = (
        ("SQQQ", "2024-11-04", "2024-11-12")
        if group == "sqqq-reverse"
        else ("WOLF", "2025-09-24", "2025-10-03")
    )
    rows = []
    for kind, adjusted in (("raw", False), ("split-adjusted", True)):
        rows.append(
            {
                "id": f"{group}-{kind}",
                "ticker": ticker,
                "kind": "bars",
                "adjusted": adjusted,
                "start": start,
                "end": end,
                "url": f"https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}?adjusted={str(adjusted).lower()}&sort=asc&limit=5000",
            }
        )
    rows.append(
        {
            "id": f"{group}-splits",
            "ticker": ticker,
            "kind": "splits",
            "url": f"https://api.massive.com/stocks/v1/splits?ticker={ticker}&execution_date.gte={start}&execution_date.lte=2026-10-14&sort=execution_date.asc&limit=100",
        }
    )
    for label, day in (("identity-pre", start), ("identity-post", "2025-09-30")):
        if label == "identity-post" and group == "sqqq-reverse":
            continue
        rows.append(
            {
                "id": f"{group}-{label}",
                "ticker": ticker,
                "kind": "identity",
                "url": f"https://api.massive.com/v3/reference/tickers/{ticker}?date={day}",
            }
        )
    documents = (
        (
            (
                "issuer-announcement",
                "https://www.proshares.com/press-releases/proshares-announces-etf-share-splits3",
                "html",
            ),
            (
                "issuer-8937",
                "https://www.proshares.com/globalassets/proshares/form-8937/sqqq_form-8937_12.2.24.pdf",
                "pdf",
            ),
        )
        if group == "sqqq-reverse"
        else (
            (
                "issuer-reorganization",
                "https://www.sec.gov/Archives/edgar/data/895419/000119312525223057/d69265d8k.htm",
                "html",
            ),
            (
                "issuer-2026-release",
                "https://investor.wolfspeed.com/news/news-details/2026/Wolfspeed-Announces-CFIUS-Clearance-and-Completion-of-Equity-Issuance-to-Renesas-as-Part-of-Court-Approved-Restructuring/default.aspx",
                "html",
            ),
        )
    )
    for name, url, format_ in documents:
        rows.append(
            {
                "id": f"{group}-{name}",
                "ticker": ticker,
                "kind": "official",
                "format": format_,
                "url": url,
            }
        )
    return tuple(rows)


PLAN_HASH = m.sha256(m.canonical({"groups": {g: products(g) for g in GROUPS}, "policy": POLICY}))


def checked(product: dict) -> None:
    m.require(any(product == candidate for group in GROUPS for candidate in products(group)))


def ref(product: dict, budget=False) -> str:
    checked(product)
    family = "reservations" if budget else "data"
    return f"research-{family}/real-case-supplement-v1-{product['id']}"


def structure(product: dict, body: bytes, status: int) -> bool:
    checked(product)
    if status != 200:
        return False
    if product["kind"] == "official":
        return (
            (len(body) > 100 and body.startswith(b"%PDF-"))
            if product["format"] == "pdf"
            else (len(body) > 1000 and b"<html" in body.lower())
        )
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
            stamps = [row["t"] for row in rows]
            m.require(all(type(t) is int for t in stamps) and stamps == sorted(set(stamps)))
            m.require(
                all(
                    product["start"]
                    <= datetime.fromtimestamp(t / 1000, UTC).date().isoformat()
                    <= product["end"]
                    for t in stamps
                )
            )
            m.require(
                all(
                    all(b.numeric(row.get(k), True) for k in ("o", "h", "l", "c"))
                    and b.numeric(row.get("v"))
                    and row["l"] <= min(row["o"], row["c"])
                    and row["h"] >= max(row["o"], row["c"])
                    for row in rows
                )
            )
        else:
            m.require(all(row.get("ticker") == product["ticker"] for row in rows))
        return True
    except Exception:
        return False


def get(product: dict, key: str) -> tuple[int, bytes]:
    checked(product)
    headers = {"Accept": "application/json"}
    if product["kind"] == "official":
        headers["Accept"] = "application/pdf" if product["format"] == "pdf" else "text/html"
        headers["User-Agent"] = (
            "quant-strategies-code research https://github.com/dczhhh/quant-strategies-code"
        )
    else:
        m.require(0 < len(key) <= 4096 and not any(c.isspace() for c in key))
        headers["Authorization"] = "Bearer " + key
    request = Request(product["url"], headers=headers)
    try:
        with build_opener(m.NoRedirect()).open(request, timeout=45) as response:
            status, body = response.status, response.read(m.FILE_LIMIT + 1)
    except HTTPError as error:
        with error:
            status, body = error.code, error.read(m.FILE_LIMIT + 1)
    b.secret_free(body, (key,))
    return status, body


def reservation(product: dict, source: str) -> bytes:
    checked(product)
    return m.canonical(
        {
            "schema_version": "real_case_supplement_reservation_v1",
            "source_commit": m.sha(source),
            "plan_sha256": PLAN_HASH,
            "product": product,
            "policy": POLICY,
            "single_attempt": True,
            "unknown_outcome_must_not_be_reset": True,
        }
    )


def checks(product: dict, body: bytes, status: int) -> dict:
    return {
        "schema_version": "real_case_supplement_structure_v1",
        "structure_accepted": structure(product, body, status),
        "plan_sha256": PLAN_HASH,
        "certification": "UNVERIFIED",
        **b.FALSE_FLAGS,
    }


def verify_product(destination, product: dict) -> dict:
    prefix = PREFIX + product["id"] + "/"
    payload, parent = b.read(
        destination,
        ref(product),
        {prefix + x for x in ("response.bin", "receipt.json", "checks.json")},
    )
    budget, budget_parent = b.read(destination, ref(product, True), {prefix + "reservation.json"})
    body, receipt = payload[prefix + "response.bin"], b.decode(payload[prefix + "receipt.json"])
    b.secret_free(body, (destination.token,))
    m.require(
        parent == destination.ref(ref(product, True))
        and budget_parent == m.BOOTSTRAP
        and budget[prefix + "reservation.json"] == reservation(product, receipt["source_commit"])
    )
    m.require(
        receipt["schema_version"] == "real_case_supplement_receipt_v1"
        and receipt["plan_sha256"] == PLAN_HASH
        and receipt["product"] == product
        and receipt["policy"] == POLICY
        and receipt["body_bytes"] == len(body)
        and receipt["body_sha256"] == m.sha256(body)
    )
    check = checks(product, body, receipt["http_status"])
    m.require(payload[prefix + "checks.json"] == m.canonical(check))
    return check


def execute(
    group: str, destination, source: str, key_factory, *, verify_only=False, fetch=get
) -> dict:
    m.require(source == m.sha(source))
    destination.visibility(True)
    made, reused, accepted = 0, 0, 0
    for product in products(group):
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
            key = "" if product["kind"] == "official" else key_factory()
            status, body = fetch(product, key)
            made += product["kind"] != "official"
            b.secret_free(body, (key, destination.token))
            receipt = {
                "schema_version": "real_case_supplement_receipt_v1",
                "source_commit": source,
                "plan_sha256": PLAN_HASH,
                "product": product,
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
    destination.visibility(True)
    return {
        "status": "SUPPLEMENT_READBACK_COMPLETE_NOT_CERTIFIED",
        "group": group,
        "vendor_requests": made,
        "reused_products": reused,
        "products": len(products(group)),
        "structure_accepted": accepted,
        "certification": "UNVERIFIED",
        **b.FALSE_FLAGS,
    }


def guarded() -> str:
    source = m.sha(os.environ.get("REAL_CASE_SUPPLEMENT_REVIEWED_SHA", ""))
    m.require(
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("GITHUB_REPOSITORY") == m.CODE
        and os.environ.get("GITHUB_ACTOR") == "dczhhh"
        and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_SHA") == source
        and os.environ.get("REAL_CASE_SUPPLEMENT_ENABLED") == "true"
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
                    "status": "REAL_CASE_SUPPLEMENT_STOPPED",
                    "code": code,
                    "certification": "UNVERIFIED",
                },
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
