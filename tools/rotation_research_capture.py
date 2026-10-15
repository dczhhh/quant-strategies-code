"""Finite missing-input capture for the owner's retrospective ETF replay.

Only GitHub-hosted runners receive credentials. Raw responses stay in the private
data repository. Earlier reservations and rejected derived views are never reset.
"""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from quant_constraints.calendar import SessionCalendar

r = SimpleNamespace(**runpy.run_path(str(Path(__file__).with_name("real_etf_2025_audit.py"))))
a, p, b, m = r.a, r.p, r.b, r.m
CAL = SessionCalendar()
START, FIRST, LAST = date(2024, 8, 26), date(2025, 8, 29), date(2025, 11, 5)
OPERATION_BRANCH = "main"
OPERATION_WORKFLOW = ".github/workflows/rotation-research-operation.yml"
PREFIX = "research/data/rotation_research_2025_v1/"
POLICY = {
    "schema": "rotation_research_capture_v1",
    "universe": list(p.TICKERS),
    "warmup_start": str(START),
    "execution_start": str(FIRST),
    "execution_end": str(LAST),
    "raw_original_share_basis": True,
    "reuse_existing_2025_components": True,
    "maximum_response_bytes": m.FILE_LIMIT,
    "single_attempt_per_reserved_group": True,
    "unknown_outcome_must_not_be_reset": True,
    "historical_PIT_verified": False,
    "calendar_mode": "bounded_weekday_reference_excluding_observed_bank_holidays_not_NSCC_attestation",
}
REFERENCE_URLS = (
    "https://www.federalreserveplaza.com/-/media/FRP/pdf/2024_2028_Federal_Holidays.pdf?sc_lang=en",
)
REFERENCE_PDF_SHA256 = "b8b7ee22fd6f3c275c579b1052e0aa4bfac5394f4ae6c1c502067c6ad7c8284a"


def sessions(first=FIRST, last=LAST):
    result = []
    while first <= last:
        if CAL.session(first) is not None:
            result.append(first)
        first += timedelta(days=1)
    return tuple(result)


def products(ticker):
    if ticker == "REFERENCE":
        return tuple(
            {"id": f"calendar-{i}", "ticker": ticker, "kind": "official_pdf", "url": url}
            for i, url in enumerate(REFERENCE_URLS)
        )
    p.need(ticker in p.TICKERS, "TICKER_OUTSIDE_FIXED_SCOPE")
    rows = []
    for kind, start, end in (
        ("daily", START, date(2025, 9, 14)),
        ("daily", date(2025, 11, 4), LAST),
        ("dividends", START, date(2025, 9, 14)),
        ("dividends", date(2025, 11, 4), LAST),
        ("splits", START, date(2025, 9, 14)),
    ):
        endpoint = (
            f"v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}?adjusted=false&sort=asc&limit=5000"
            if kind == "daily"
            else f"stocks/v1/{kind}?ticker={ticker}&{'ex_dividend_date' if kind == 'dividends' else 'execution_date'}.gte={start}&{'ex_dividend_date' if kind == 'dividends' else 'execution_date'}.lte={end}&sort={'ex_dividend_date' if kind == 'dividends' else 'execution_date'}.asc&limit=100"
        )
        rows.append(
            {
                "id": f"{ticker.lower()}-{kind}-{start}",
                "ticker": ticker,
                "kind": kind,
                "start": str(start),
                "end": str(end),
                "url": "https://api.massive.com/" + endpoint,
            }
        )
    days = sessions()
    for index in range(0, len(days), 3):
        chunk = days[index : index + 3]
        start = int(CAL.session(chunk[0]).market_open.timestamp() * 1000)
        end = int(CAL.session(chunk[-1]).market_close.timestamp() * 1000) - 1
        rows.append(
            {
                "id": f"{ticker.lower()}-minute-{chunk[0]}",
                "ticker": ticker,
                "kind": "minute",
                "start": str(chunk[0]),
                "end": str(chunk[-1]),
                "url": f"https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/minute/{start}/{end}?adjusted=false&sort=asc&limit=5000",
            }
        )
    return tuple(rows)


PLAN_HASH = m.sha256(
    m.canonical({"policy": POLICY, "products": {t: products(t) for t in (*p.TICKERS, "REFERENCE")}})
)


def groups(ticker):
    rows = products(ticker)
    return tuple(rows[i : i + 5] for i in range(0, len(rows), 5))


def group_ref(ticker, index, budget=False):
    p.need(type(index) is int and 0 <= index < len(groups(ticker)), "CAPTURE_GROUP_INVALID")
    return (
        "research-reservations/" if budget else "research-data/"
    ) + f"rotation-2025-v1-{ticker.lower()}-{index}"


def reservation(ticker, index, source):
    return m.canonical(
        {
            "schema": "rotation_group_reservation_v1",
            "plan_sha256": PLAN_HASH,
            "source_commit": m.sha(source),
            "products": groups(ticker)[index],
            "policy": POLICY,
        }
    )


def get(product, key):
    p.need(
        any(product in products(t) for t in (*p.TICKERS, "REFERENCE")),
        "PRODUCT_OUTSIDE_FIXED_SCOPE",
    )
    headers = {"Accept": "application/pdf"}
    if product["kind"] != "official_pdf":
        p.need(
            type(key) is str and 0 < len(key) <= 4096 and not any(c.isspace() for c in key),
            "CREDENTIAL_UNAVAILABLE",
        )
        headers = {"Authorization": "Bearer " + key, "Accept": "application/json"}
    request = Request(product["url"], headers=headers)
    try:
        with build_opener(m.NoRedirect()).open(request, timeout=45) as response:
            status, body = response.status, response.read(m.FILE_LIMIT + 1)
    except HTTPError as error:
        with error:
            status, body = error.code, error.read(m.FILE_LIMIT + 1)
    b.secret_free(body, (key,))
    return status, body


def decode_market(body):
    from decimal import Decimal

    value = json.loads(
        body,
        object_pairs_hook=b.pairs,
        parse_float=Decimal,
        parse_constant=lambda _: p.need(False, "NONFINITE_MARKET_NUMBER"),
    )
    p.need(
        type(value) is dict
        and value.get("status") in {"OK", "DELAYED"}
        and not value.get("next_url"),
        "MARKET_RESPONSE_INCOMPLETE",
    )
    p.need(type(value.get("results", [])) is list, "MARKET_RESULTS_INVALID")
    return value


def read_group(destination, ticker, index):
    prefix = PREFIX + f"{ticker.lower()}/{index}/"
    plan = groups(ticker)[index]
    paths = {prefix + row["id"] + ".json" for row in plan} | {prefix + "receipts.json"}
    files, parent = b.read(destination, group_ref(ticker, index), paths)
    held, bootstrap = b.read(
        destination, group_ref(ticker, index, True), {prefix + "reservation.json"}
    )
    receipts = b.decode(files[prefix + "receipts.json"])
    p.need(
        bootstrap == m.BOOTSTRAP
        and parent == destination.ref(group_ref(ticker, index, True))
        and receipts.get("plan_sha256") == PLAN_HASH
        and held[prefix + "reservation.json"]
        == reservation(ticker, index, receipts["source_commit"])
        and len(receipts.get("rows", [])) == len(plan),
        "CAPTURE_GROUP_BINDING_INVALID",
    )
    result = {}
    for product, receipt in zip(plan, receipts["rows"], strict=True):
        body = files[prefix + product["id"] + ".json"]
        b.secret_free(body, (destination.token,))
        started, finished = (
            datetime.fromisoformat(receipt[k])
            for k in ("request_started_at", "response_received_at")
        )
        p.need(
            receipt["product"] == product
            and receipt["body_sha256"] == m.sha256(body)
            and receipt["body_bytes"] == len(body)
            and receipt["http_status"] == 200
            and started.utcoffset() is not None
            and finished.utcoffset() is not None
            and started <= finished,
            "CAPTURE_RECEIPT_INVALID",
        )
        if product["kind"] == "official_pdf":
            p.need(
                body.startswith(b"%PDF-") and m.sha256(body) == REFERENCE_PDF_SHA256,
                "OFFICIAL_PDF_INVALID",
            )
        else:
            decode_market(body)
        result[product["id"]] = {"body": body, "receipt": receipt}
    return result


def capture(destination, ticker, source, key_factory, *, verify_only=False, fetch=get):
    """Reserve at most five requests atomically, archive once, and read back exact bytes."""
    destination.visibility(True)
    captured, made = {}, 0
    for index, plan in enumerate(groups(ticker)):
        name = group_ref(ticker, index)
        if destination.ref(name) is None:
            p.need(not verify_only, "CAPTURE_GROUP_MISSING")
            p.need(
                destination.ref(group_ref(ticker, index, True)) is None,
                "UNKNOWN_GROUP_OUTCOME_RETAINED",
            )
            key = key_factory()
            if ticker != "REFERENCE":
                p.need(
                    type(key) is str
                    and 0 < len(key) <= 4096
                    and not any(char.isspace() for char in key),
                    "CREDENTIAL_UNAVAILABLE",
                )
            prefix = PREFIX + f"{ticker.lower()}/{index}/"
            hold = b.create(
                destination,
                group_ref(ticker, index, True),
                {prefix + "reservation.json": reservation(ticker, index, source)},
                m.BOOTSTRAP,
            )
            payload, receipts = {}, []
            for product in plan:
                started = datetime.now(UTC).isoformat()
                status, body = fetch(product, key)
                made += 1
                b.secret_free(body, (key, destination.token))
                p.need(type(status) is int and 100 <= status <= 599, "HTTP_STATUS_INVALID")
                payload[prefix + product["id"] + ".json"] = body
                receipts.append(
                    {
                        "product": product,
                        "http_status": status,
                        "body_sha256": m.sha256(body),
                        "body_bytes": len(body),
                        "request_started_at": started,
                        "response_received_at": datetime.now(UTC).isoformat(),
                    }
                )
            payload[prefix + "receipts.json"] = m.canonical(
                {"plan_sha256": PLAN_HASH, "source_commit": source, "rows": receipts}
            )
            b.create(destination, name, payload, hold)
        captured.update(read_group(destination, ticker, index))
    return captured, made


def guarded(source):
    m.sha(source)
    p.need(
        os.environ.get("GITHUB_REPOSITORY") == m.CODE
        and os.environ.get("GITHUB_ACTOR") == "dczhhh"
        and os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("GITHUB_REF") == "refs/heads/" + OPERATION_BRANCH
        and os.environ.get("GITHUB_WORKFLOW_REF")
        == m.CODE + "/" + OPERATION_WORKFLOW + "@refs/heads/main"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("RUNNER_OS") == "Linux"
        and os.environ.get("ACTIONS_STEP_DEBUG", "").lower() != "true"
        and os.environ.get("ACTIONS_RUNNER_DEBUG", "").lower() != "true"
        and subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == source,
        "REVIEWED_RUNNER_CONTEXT_REQUIRED",
    )


def main():
    try:
        p.need(len(sys.argv) == 4 and sys.argv[1] in {"capture", "verify"}, "ARGUMENTS_INVALID")
        mode, ticker, source = sys.argv[1:]
        guarded(source)
        archive = r.CachedArchive(
            m.GitHub(
                m.DESTINATION, os.environ.get("ARCHIVE_WRITE_TOKEN", ""), writable=mode == "capture"
            )
        )
        _, made = capture(
            archive,
            ticker,
            source,
            lambda: os.environ.get("MASSIVE_API_KEY", ""),
            verify_only=mode == "verify",
        )
        print(
            json.dumps(
                {
                    "status": "RETAINED_RESEARCH_INPUTS_CAPTURED",
                    "ticker": ticker,
                    "plan_sha256": PLAN_HASH,
                    "new_vendor_requests": 0 if ticker == "REFERENCE" else made,
                    "new_official_requests": made if ticker == "REFERENCE" else 0,
                    "products": len(products(ticker)),
                    "engine_invoked": False,
                    "PIT_verified": False,
                }
            )
        )
        return 0
    except Exception as error:
        print(json.dumps({"status": "BLOCKED", "reason": r.r.code(error), "engine_invoked": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
