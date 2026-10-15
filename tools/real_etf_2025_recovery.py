"""Bounded append-only recovery and independent diagnostics; never grants real/PIT entry."""

from __future__ import annotations

import json
import os
import re
import runpy
import sys
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

a = SimpleNamespace(**runpy.run_path(str(Path(__file__).with_name("real_etf_2025.py"))))
p, b, m = a.p, a.b, a.m
ORIGINAL_SOURCE = "0ceaa1764c86286032b3af03f5c6a8a31b73b28d"
# Exact interrupted state from run 38695965896; None means no such old reference.
ORIGINALS = {
    "xle-raw": (None, "a2ce2a75e47fb16a88ba5f5fb39029ed47c6d742"),
    "xle-split-adjusted": (None, None),
    "xle-dividends": (None, None),
    "xle-splits": (None, None),
    "xlp-raw": (
        "4bf7c4256d37502112f089f08c7dd4d851287cd6",
        "1c3a2efa712f819ea79123199c4711d65eaf76bc",
    ),
    "xlp-split-adjusted": (
        "9eaa5a79cdb46a73ab522bb1710fa90d7305c397",
        "41cf937978c1e1d7f026b2f396ceddc7c5258ece",
    ),
    "xlp-dividends": (None, "ce51fbcdb1f4704bb0ac5363a32611d2e1e0aeac"),
    "xlp-splits": (None, None),
}
PRODUCTS = tuple(v for t in ("XLE", "XLP") for v in a.products(t) if ORIGINALS[v["id"]][0] is None)
PREFIX = "research/data/real_etf_2025_recovery_v1/"
REPORT_PREFIX = "research/verification/real_etf_2025_recovery_v1/"
POLICY = {
    "schema": "real_etf_2025_recovery_v1",
    "original_plan_sha256": a.PLAN_HASH,
    "original_source": ORIGINAL_SOURCE,
    "originals": {k: list(v) for k, v in ORIGINALS.items()},
    "maximum_new_vendor_requests": 6,
    "old_unknown_requests": 2,
    "previous_unknown_outcome_resolved": False,
    "single_attempt_per_new_reservation": True,
    "automatic_retry_allowed": False,
    "new_issuer_requests": 0,
    "amount_tolerance_usd": "1E-8",
    "diagnostics_never_repair_or_replace_source_fields": True,
    "certification": "UNVERIFIED",
    **b.FALSE_FLAGS,
}
PLAN_HASH = m.sha256(m.canonical({"products": PRODUCTS, "policy": POLICY}))


def code(error):
    if isinstance(error, p.Blocked) and re.fullmatch(r"[A-Z_]{1,80}", str(error)):
        return str(error)
    candidate = getattr(error, "code", None)
    if type(candidate) is str and re.fullmatch(
        r"GITHUB_HTTP_(?:401|403|404|429|FAILED)|GITHUB_PRIMARY_RATE_LIMIT|CHECK_FAILED", candidate
    ):
        return candidate
    if isinstance(error, TimeoutError):
        return "NETWORK_TIMEOUT"
    if isinstance(error, URLError):
        return "NETWORK_ERROR"
    return "CHECK_FAILED"


def kind(product):
    return (
        product["kind"]
        if product["kind"] != "bars"
        else ("split-adjusted" if product["adjusted"] else "raw")
    )


def ref(product, budget=False):
    p.need(product in PRODUCTS, "RECOVERY_PRODUCT_OUTSIDE_FIXED_SCOPE")
    return (
        ("research-reservations/" if budget else "research-data/")
        + "real-etf-2025-recovery-v1-"
        + product["id"]
    )


def predecessor(product):
    p.need(product in PRODUCTS, "RECOVERY_PRODUCT_OUTSIDE_FIXED_SCOPE")
    return {
        "capture_ref": a.ref(product),
        "reservation_ref": a.ref(product, True),
        "reservation_commit": ORIGINALS[product["id"]][1],
        "unknown_outcome_resolved": False,
    }


def reservation(product, source):
    return m.canonical(
        {
            "schema": "real_etf_2025_recovery_reservation_v1",
            "source_commit": m.sha(source),
            "plan_sha256": PLAN_HASH,
            "product": product,
            "predecessor": predecessor(product),
            "policy": POLICY,
        }
    )


def originals(destination, ticker):
    """Lease the original interrupted references; never write/reset an old reservation."""
    retained = {}
    for product in a.products(ticker):
        expected = ORIGINALS[product["id"]]
        p.need(
            (destination.ref(a.ref(product)), destination.ref(a.ref(product, True))) == expected,
            "ORIGINAL_INTERRUPTED_STATE_CHANGED",
        )
        if expected[1] is not None:
            path = a.PREFIX + product["id"] + "/reservation.json"
            saved, parent = b.read(destination, a.ref(product, True), {path})
            p.need(
                parent == m.BOOTSTRAP and saved[path] == a.reservation(product, ORIGINAL_SOURCE),
                "ORIGINAL_RESERVATION_INVALID",
            )
        if expected[0] is not None:
            retained[kind(product)] = a.load_product(destination, product)
    return retained


def read_product(destination, product):
    path = PREFIX + product["id"] + "/"
    files, parent = b.read(
        destination, ref(product), {path + "response.bin", path + "receipt.json"}
    )
    held, bootstrap = b.read(destination, ref(product, True), {path + "reservation.json"})
    receipt, body = b.decode(files[path + "receipt.json"]), files[path + "response.bin"]
    b.secret_free(body, (destination.token,))
    p.need(
        bootstrap == m.BOOTSTRAP
        and parent == destination.ref(ref(product, True))
        and held[path + "reservation.json"] == reservation(product, receipt["source_commit"])
        and receipt["schema"] == "real_etf_2025_recovery_receipt_v1"
        and receipt["plan_sha256"] == PLAN_HASH
        and receipt["policy"] == POLICY
        and receipt["product"] == product
        and receipt["predecessor"] == predecessor(product)
        and receipt["body_sha256"] == m.sha256(body)
        and receipt["body_bytes"] == len(body)
        and type(receipt["http_status"]) is int
        and 100 <= receipt["http_status"] <= 599,
        "RECOVERY_CAPTURE_READBACK_INVALID",
    )
    started, finished = (
        datetime.fromisoformat(receipt[k]) for k in ("request_started_at", "response_received_at")
    )
    p.need(
        started.utcoffset() is not None
        and finished.utcoffset() is not None
        and started <= finished,
        "RECOVERY_CLOCK_INVALID",
    )
    return {
        "body": body,
        "receipt": receipt,
        "commit": destination.ref(ref(product)),
        "structure_accepted": a.structure(product, body, receipt["http_status"]),
    }


def capture(
    destination,
    ticker,
    source,
    key_factory,
    *,
    verify_only=False,
    fetch=a.fetch_product,
    progress=None,
):
    tick = progress or (lambda *_: None)
    if ticker not in {"XLE", "XLP"}:
        # All ten completed v1 targets are strictly read-only, even in capture mode.
        tick("original_capture_read", None)
        return a.capture(destination, ticker, source, key_factory, verify_only=True, fetch=fetch)
    tick("original_state", None)
    captured, made = originals(destination, ticker), 0
    for product in (v for v in PRODUCTS if v["ticker"] == ticker):
        tick("recovery_inventory", product["id"])
        if destination.ref(ref(product)) is None:
            p.need(not verify_only, "RECOVERY_CAPTURE_MISSING")
            p.need(destination.ref(ref(product, True)) is None, "RECOVERY_UNKNOWN_OUTCOME_RETAINED")
            key = key_factory()
            p.need(
                type(key) is str and 0 < len(key) <= 4096 and not any(c.isspace() for c in key),
                "CREDENTIAL_UNAVAILABLE",
            )
            path = PREFIX + product["id"] + "/"
            tick("recovery_reservation_write", product["id"])
            budget = b.create(
                destination,
                ref(product, True),
                {path + "reservation.json": reservation(product, source)},
                m.BOOTSTRAP,
            )
            started = datetime.now(UTC).isoformat()
            tick("provider_request", product["id"])
            status, body = fetch(product, key)
            made += 1
            b.secret_free(body, (key, destination.token))
            p.need(type(status) is int and 100 <= status <= 599, "HTTP_STATUS_INVALID")
            receipt = {
                "schema": "real_etf_2025_recovery_receipt_v1",
                "source_commit": source,
                "plan_sha256": PLAN_HASH,
                "policy": POLICY,
                "product": product,
                "predecessor": predecessor(product),
                "http_status": status,
                "request_started_at": started,
                "response_received_at": datetime.now(UTC).isoformat(),
                "body_bytes": len(body),
                "body_sha256": m.sha256(body),
            }
            tick("recovery_capture_write", product["id"])
            b.create(
                destination,
                ref(product),
                {path + "response.bin": body, path + "receipt.json": m.canonical(receipt)},
                budget,
            )
        tick("recovery_readback", product["id"])
        captured[kind(product)] = read_product(destination, product)
    tick("original_state_recheck", None)
    originals(destination, ticker)
    return captured, made


def diagnostics(ticker, captured, documents):
    """Independent checks still run when the strict combined component rejects; no source repair."""
    parsed, checks = {}, {}
    for product in a.products(ticker):
        try:
            entry = captured[kind(product)]
            p.need(entry["structure_accepted"], "CAPTURE_STRUCTURE_UNVERIFIED")
            parsed[kind(product)] = a.market_rows(product, entry["body"])
            checks[kind(product)] = {"status": "STRUCTURE_PASS"}
        except Exception as error:
            checks[kind(product)] = {"status": "UNVERIFIED", "reason": code(error)}
    if len(parsed) != 4:
        return {"checks": checks}
    event, splits = parsed["dividends"][0], parsed["splits"]
    scale = Decimal(1)
    for split in splits:
        scale *= split["from"] / split["to"]
    raw, adjusted = parsed["raw"], parsed["split-adjusted"]
    checks["price_basis"] = {
        "status": "PASS"
        if set(raw) == set(adjusted)
        and all(s["date"].isoformat() > a.END for s in splits)
        and all(abs(adjusted[d] - raw[d] * scale) <= Decimal("0.0002") for d in raw)
        else "UNVERIFIED"
    }
    residual = event["split_amount"] - event["amount"] * scale
    checks["dividend_basis"] = {
        "status": "PASS" if abs(residual) <= Decimal("1E-8") else "UNVERIFIED",
        "strict_residual": str(residual),
        "captured_scale": str(scale),
        "original_amount": str(event["amount"]),
        "vendor_adjusted_amount": str(event["split_amount"]),
        "adjusted_equals_original": event["split_amount"] == event["amount"],
        "rounding_policy_verified": False,
    }
    try:
        history_sheets = p.workbook(documents[p.DOCUMENTS[0]["id"]]["body"])
        primary_sheets = p.workbook(documents[p.DOCUMENTS[1]["id"]]["body"])
        history = p.history_match(history_sheets, ticker, [event])
        checks["issuer_history"] = history
        # Test the other field as a diagnostic only; never substitute it into the cash event.
        checks["issuer_adjusted_field_diagnostic"] = p.history_match(
            history_sheets, ticker, [{**event, "amount": event["split_amount"]}]
        )
        try:
            checks["tax_fields"] = a.tax_fields(primary_sheets, ticker, event, history)
        except Exception as error:
            checks["tax_fields"] = {"status": "UNVERIFIED", "reason": code(error)}
    except Exception as error:
        checks["issuer_history"] = {"status": "UNVERIFIED", "reason": code(error)}
    return {"checks": checks, "source_fields_unchanged": True, "cash_or_engine_admission": False}


def execute(
    destination,
    ticker,
    source,
    key_factory,
    *,
    verify_only=False,
    fetch=a.fetch_product,
    load_documents=a.issuer_documents,
    progress=None,
):
    a.products(ticker)
    m.sha(source)
    tick = progress or (lambda *_: None)
    tick("private_visibility", None)
    destination.visibility(True)
    tick("pinned_issuer_read", None)
    documents = load_documents(destination)
    captured, made = capture(
        destination,
        ticker,
        source,
        key_factory,
        verify_only=verify_only,
        fetch=fetch,
        progress=tick,
    )
    tick("components_and_diagnostics", None)
    with localcontext() as ctx:
        ctx.prec = 60
        try:
            case = a.components(ticker, captured, documents)
        except Exception as error:
            case = {"status": "SAME_YEAR_COMPONENTS_UNVERIFIED", "reason": code(error)}
        diagnostic = diagnostics(ticker, captured, documents)
    value = {
        "schema": "real_etf_2025_recovery_report_v1",
        "source_commit": source,
        "ticker": ticker,
        "plan_sha256": PLAN_HASH,
        "policy": POLICY,
        "inputs": {
            k: {
                "receipt": v["receipt"],
                "commit": v["commit"],
                "structure_accepted": v["structure_accepted"],
            }
            for k, v in captured.items()
        },
        "issuer_documents": {
            k: {"commit": v["commit"], "receipt": v["receipt"], "historical_known_at": None}
            for k, v in documents.items()
        },
        "case": case,
        "diagnostics": diagnostic,
    }
    summary = {
        **a.public_summary(value),
        "schema": "real_etf_2025_recovery_summary_v1",
        "plan_sha256": PLAN_HASH,
        "original_plan_sha256": a.PLAN_HASH,
        "previous_unknown_outcome_resolved": False,
        "diagnostic_statuses": {k: v["status"] for k, v in diagnostic["checks"].items()},
        "vendor_adjusted_equals_original": diagnostic["checks"]
        .get("dividend_basis", {})
        .get("adjusted_equals_original"),
    }
    path = REPORT_PREFIX + ticker.lower() + "/" + source + "/"
    reference = "research-data/real-etf-2025-recovery-probe-v1-" + ticker.lower() + "-" + source
    files = {path + "report.json": m.canonical(value), path + "summary.json": m.canonical(summary)}
    tick("report_archive", None)
    if destination.ref(reference) is None:
        p.need(not verify_only, "RECOVERY_REPORT_MISSING")
        b.create(destination, reference, files, m.BOOTSTRAP)
    saved, parent = b.read(destination, reference, set(files))
    p.need(parent == m.BOOTSTRAP and saved == files, "RECOVERY_REPORT_READBACK_MISMATCH")
    destination.visibility(True)
    return {
        **summary,
        "vendor_requests": made,
        "official_requests": 0,
        "report_sha256": m.sha256(files[path + "report.json"]),
    }


def main():
    phase, product = "context", None

    def progress(next_phase, next_product):
        nonlocal phase, product
        phase, product = next_phase, next_product

    try:
        p.need(len(sys.argv) == 3 and sys.argv[1] in {"capture", "verify"}, "INVALID_COMMAND")
        source = a.guarded()
        verify = sys.argv[1] == "verify"
        destination = m.GitHub(
            m.DESTINATION, os.environ.get("ARCHIVE_WRITE_TOKEN", ""), writable=not verify
        )
        result = execute(
            destination,
            sys.argv[2],
            source,
            lambda: os.environ.get("MASSIVE_API_KEY", ""),
            verify_only=verify,
            progress=progress,
        )
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "REAL_ETF_2025_RECOVERY_STOPPED",
                    "phase": phase,
                    "product": product,
                    "code": code(error),
                    "certification": "UNVERIFIED",
                },
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
