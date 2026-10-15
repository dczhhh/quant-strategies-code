"""Zero-market-request batch audit; raw-share math never repairs a rejected vendor view."""

from __future__ import annotations

import copy
import json
import os
import re
import runpy
import sys
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace

r = SimpleNamespace(**runpy.run_path(str(Path(__file__).with_name("real_etf_2025_recovery.py"))))
a, p, b, m = r.a, r.p, r.b, r.m
PREFIX = "research/verification/real_etf_2025_raw_audit_v1/"
POLICY = {
    "schema": "real_etf_2025_raw_audit_v1",
    "input_plan_sha256": a.PLAN_HASH,
    "recovery_plan_sha256": r.PLAN_HASH,
    "vendor_requests": 0,
    "official_requests": 0,
    "cash_basis": "original cash_amount paired only with original raw price shares",
    "vendor_split_adjusted_cash_used": False,
    "strict_vendor_comparison_preserved": True,
    "amount_tolerance_usd": "1E-8",
    "price_tolerance_usd": "0.0002",
    "signal_tolerance": "1E-28",
    "historical_known_at": None,
    "certification": "UNVERIFIED",
    **b.FALSE_FLAGS,
}
PLAN_HASH = m.sha256(m.canonical(POLICY))


class CachedArchive:
    """Cache content-addressed objects within one runner; never cache refs or visibility."""

    def __init__(self, destination):
        self.destination, self.token = destination, destination.token
        self.objects, self.bodies = {}, {}

    def request(self, method, suffix, payload=None):
        immutable = (
            method == "GET"
            and payload is None
            and re.fullmatch(
                r"/git/commits/[0-9a-f]{40}|/git/trees/[0-9a-f]{40}\?recursive=1", suffix
            )
        )
        if not immutable:
            return self.destination.request(method, suffix, payload)
        if suffix not in self.objects:
            self.objects[suffix] = self.destination.request(method, suffix)
        return copy.deepcopy(self.objects[suffix])

    def blob(self, sha, size=None):
        if sha not in self.bodies:
            self.bodies[sha] = self.destination.blob(sha, size)
        data = self.bodies[sha]
        p.need(size is None or len(data) == size, "CACHED_BLOB_SIZE_INVALID")
        p.need(m.git_blob(data) == sha, "CACHED_BLOB_HASH_INVALID")
        return data

    def ref(self, name):
        return self.destination.ref(name)

    def visibility(self, private):
        return self.destination.visibility(private)

    def write(self, suffix, payload):
        return self.destination.write(suffix, payload)


def guarded():
    """Keep the existing exact-main/owner/dispatch checks; authenticate the public metadata GET."""
    token = os.environ.get("GITHUB_TOKEN", "")
    p.need(
        type(token) is str and 0 < len(token) <= 4096 and not any(c.isspace() for c in token),
        "CODE_READ_TOKEN_UNAVAILABLE",
    )
    return a.guarded(code_token=token)


def raw_component(ticker, captured, documents):
    p.need(
        set(captured) == {"raw", "split-adjusted", "dividends", "splits"}
        and all(v["structure_accepted"] for v in captured.values()),
        "RAW_INPUT_STRUCTURE_UNVERIFIED",
    )
    parsed = {r.kind(v): a.market_rows(v, captured[r.kind(v)]["body"]) for v in a.products(ticker)}
    raw, adjusted, events, splits = (
        parsed[k] for k in ("raw", "split-adjusted", "dividends", "splits")
    )
    p.need(all(v["date"].isoformat() > a.END for v in splits), "SPLIT_INSIDE_WINDOW_UNSUPPORTED")
    scale = Decimal(1)
    for split in splits:
        scale *= split["from"] / split["to"]
    p.need(
        set(raw) == set(adjusted) and a.day(a.START) in raw and a.day(a.END) in raw,
        "RAW_PRICE_BOUNDARIES_UNVERIFIED",
    )
    p.need(
        all(abs(adjusted[d] - raw[d] * scale) <= Decimal("0.0002") for d in raw),
        "PRICE_SPLIT_BASIS_CONFLICT",
    )
    event = events[0]
    p.need(event["pay"] in raw, "PAY_BOUNDARY_MISSING")
    history = p.history_match(p.workbook(documents[p.DOCUMENTS[0]["id"]]["body"]), ticker, events)
    p.need(history["status"] == "ISSUER_HISTORY_FIELD_MATCH", "ISSUER_HISTORY_CONFLICT")
    tax = a.tax_fields(p.workbook(documents[p.DOCUMENTS[1]["id"]]["body"]), ticker, event, history)
    signal = p.return_probe(raw, adjusted, events)
    return {
        "status": "RAW_BASIS_RETROSPECTIVE_SIGNAL_COMPONENT_PASS",
        "issuer_history": history,
        "tax_fields": tax,
        "signal": signal,
        "vendor_split_adjusted_cash_used": False,
        "source_cash_amount_changed": False,
        "actual_broker_cash_verified": False,
        "performance_admission": False,
        "full_real_scenarios_accepted": 0,
        "classification": "ISSUER_FINALITY_AND_PIT_UNVERIFIED",
        **b.FALSE_FLAGS,
    }


def attempt(function):
    try:
        return function()
    except Exception as error:
        return {"status": "UNVERIFIED", "reason": r.code(error)}


def execute(destination, source, *, verify_only=False):
    """Read all existing inputs only; one writing batch and one independent read-only batch."""
    m.sha(source)
    archive = CachedArchive(destination)
    archive.visibility(True)
    documents = a.issuer_documents(archive)
    reports, summaries = [], []
    with localcontext() as context:
        context.prec = 60
        for ticker in p.TICKERS:
            captured, made = r.capture(
                archive,
                ticker,
                source,
                lambda: p.need(False, "AUDIT_CREDENTIAL_ACCESS_FORBIDDEN"),
                verify_only=True,
                fetch=lambda *_: p.need(False, "AUDIT_MARKET_REQUEST_FORBIDDEN"),
            )
            p.need(made == 0, "AUDIT_MARKET_REQUEST_FORBIDDEN")
            raw_case = attempt(
                lambda ticker=ticker, captured=captured: raw_component(ticker, captured, documents)
            )
            strict = attempt(
                lambda ticker=ticker, captured=captured: a.components(ticker, captured, documents)
            )
            diagnostic = r.diagnostics(ticker, captured, documents)
            report = {
                "schema": "real_etf_2025_raw_report_v1",
                "source_commit": source,
                "ticker": ticker,
                "plan_sha256": PLAN_HASH,
                "policy": POLICY,
                "inputs": {
                    k: {"commit": v["commit"], "receipt": v["receipt"]} for k, v in captured.items()
                },
                "issuer_documents": {
                    k: {"commit": v["commit"], "receipt": v["receipt"]}
                    for k, v in documents.items()
                },
                "strict_combined_case": strict,
                "raw_basis_case": raw_case,
                "diagnostics": diagnostic,
            }
            summary = {
                "ticker": ticker,
                "raw_component_status": raw_case["status"],
                "raw_component_reason": raw_case.get("reason"),
                "raw_signal_checkpoints": raw_case.get("signal", {}).get("checkpoints", 0),
                "strict_component_status": strict["status"],
                "strict_component_reason": strict.get("reason"),
                "vendor_adjusted_view_status": diagnostic["checks"]
                .get("dividend_basis", {})
                .get("status", "UNVERIFIED"),
                "vendor_split_adjusted_cash_used": False,
                "performance_admission": False,
                "full_real_scenarios_accepted": 0,
                "classification": "ISSUER_FINALITY_AND_PIT_UNVERIFIED",
                "certification": "UNVERIFIED",
                **b.FALSE_FLAGS,
            }
            path = PREFIX + source + "/" + ticker.lower() + "/"
            reports.append((path, report))
            summaries.append(summary)
    summary = {
        "schema": "real_etf_2025_raw_summary_v1",
        "plan_sha256": PLAN_HASH,
        "tickers": summaries,
        "vendor_requests": 0,
        "official_requests": 0,
        "full_real_scenarios_accepted": 0,
        "performance_admission": False,
        "certification": "UNVERIFIED",
        **b.FALSE_FLAGS,
    }
    files = {path + "report.json": m.canonical(report) for path, report in reports}
    summary_path = PREFIX + source + "/summary.json"
    files[summary_path] = m.canonical(summary)
    # Digest covers each private report without transferring any of its rows into public output.
    manifest = {path: m.sha256(body) for path, body in sorted(files.items())}
    digest = m.sha256(m.canonical(manifest))
    files[PREFIX + source + "/manifest.json"] = m.canonical(manifest)
    reference = "research-data/real-etf-2025-raw-audit-v1-" + source
    if archive.ref(reference) is None:
        p.need(not verify_only, "RAW_AUDIT_REPORT_MISSING")
        b.create(archive, reference, files, m.BOOTSTRAP)
    saved, parent = b.read(archive, reference, set(files))
    p.need(saved == files and parent == m.BOOTSTRAP, "RAW_AUDIT_REPORT_READBACK_MISMATCH")
    archive.visibility(True)
    return {**summary, "report_bundle_sha256": digest}


def main():
    phase = "context"
    try:
        p.need(len(sys.argv) == 2 and sys.argv[1] in {"audit", "verify"}, "INVALID_COMMAND")
        source = guarded()
        phase = "retained_inputs_and_reports"
        destination = m.GitHub(
            m.DESTINATION,
            os.environ.get("ARCHIVE_WRITE_TOKEN", ""),
            writable=sys.argv[1] == "audit",
        )
        print(
            json.dumps(
                execute(destination, source, verify_only=sys.argv[1] == "verify"),
                separators=(",", ":"),
            )
        )
        return 0
    except Exception as error:
        reset = getattr(error, "retry_at", None)
        now = datetime.now(UTC).timestamp()
        retry = (
            datetime.fromtimestamp(reset, UTC).isoformat()
            if type(reset) is int and now <= reset <= now + 86400
            else None
        )
        print(
            json.dumps(
                {
                    "status": "REAL_ETF_2025_RAW_AUDIT_STOPPED",
                    "phase": phase,
                    "code": r.code(error),
                    "retry_after_utc": retry,
                    "certification": "UNVERIFIED",
                },
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
