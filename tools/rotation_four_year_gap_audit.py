"""Reopen new raw gaps and bind them to accepted row coverage and issuer cash."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import rotation_four_year_capture as g  # noqa: E402
from tools import rotation_four_year_inventory as i  # noqa: E402
from tools.rotation_four_year_settlement import audit as settlement_audit  # noqa: E402

c, u = i.c, i.u
CAPTURE_SOURCE = "f6f0575042fecf33a92b70d31ffe04c9cbe8eb91"
PREFIX = "research/verification/rotation_four_year_gap_audit_v1/"
REPORT_REF = "research-data/rotation-four-year-gap-audit-v1"
MAX_PARTS = 8
WORKFLOW = ".github/workflows/rotation-four-year-input-audit.yml"


def need(ok, code):
    if not ok:
        raise ValueError("FOUR_YEAR_GAP_AUDIT_" + code)


def issuer_match(history, ticker, event):
    result = c.p.history_match(history, ticker, [event])
    need(result["status"] == "ISSUER_HISTORY_FIELD_MATCH", "ORIGINAL_CASH_OR_DATES")
    return result


def ordinary_2025(history, tax, ticker, event):
    need(event["ex"].year == 2025, "CLASSIFICATION_YEAR")
    match = issuer_match(history, ticker, event)
    fields = c.a.tax_fields(tax, ticker, event, match)
    allowed = {"D": (None, ""), "E": (None, "", "R"), "F": (None, "", "C")}
    need(
        all(fields["source_status_flags_preserved"][key] in allowed[key] for key in allowed),
        "ISSUER_STATUS",
    )
    numbers = {key: Decimal(value) for key, value in fields["explicit_numeric_fields"].items()}
    tolerance = Decimal(c.a.POLICY["issuer_amount_tolerance_usd"])
    need(
        all(abs(numbers[key] - event["amount"]) <= tolerance for key in ("J", "M", "N", "Q"))
        and all(numbers[key] == 0 for key in ("O", "P", "V", "Z", "AB", "AC", "AD")),
        "NONORDINARY_OR_AMOUNT_CONFLICT",
    )
    return fields


def issuer_2025(history, tax):
    result = {}
    for ticker in g.TICKERS:
        rows = [
            row
            for row in history["dividend"][1:]
            if row.get("B") == ticker and c.p.issuer_day(row["D"]).year == 2025
        ]
        need(len(rows) == 4, "ISSUER_2025_EVENT_COVERAGE")
        result[ticker] = []
        for row in rows:
            event = {
                "ex": c.p.issuer_day(row["D"]),
                "record": c.p.issuer_day(row["E"]),
                "pay": c.p.issuer_day(row["F"]),
                "amount": Decimal(row["G"]),
            }
            ordinary_2025(history, tax, ticker, event)
            result[ticker].append(
                {
                    "ex": str(event["ex"]),
                    "pay": str(event["pay"]),
                    "year_end_receivable_payable_in_2026": event["pay"].year == 2026,
                }
            )
    return result


def require_complete(report):
    summary = report["summary"]
    need(
        summary["expected_execution_sessions"] == 1003
        and summary["accepted_complete_execution_sessions"] == 1003
        and summary["accepted_daily_sessions"] == 1003
        and summary["missing_execution_sessions"] == 0
        and summary["missing_daily_sessions"] == 0
        and summary["any_requested_but_unrepresented_rows"] is False
        and report["proposed_products_not_executed"] == [],
        "CONTINUOUS_ROW_COVERAGE",
    )
    need(set(report["per_asset"]) == set(g.TICKERS), "UNIVERSE")
    for value in report["per_asset"].values():
        need(
            value["daily_sessions"] == value["complete_execution_sessions"] == 1003
            and not value["partial_execution_sessions"],
            "ASSET_ROW_COVERAGE",
        )


def build(factory):
    inputs = {window: i.load_inputs(factory(), window) for window in i.INPUT_PINS}
    splits = i.read_splits(factory(), inputs["bear2022"])
    docs = c.a.issuer_documents(factory())
    history = c.p.workbook(docs[c.p.DOCUMENTS[0]["id"]]["body"])
    tax = c.p.workbook(docs[c.p.DOCUMENTS[1]["id"]]["body"])
    events_2025 = issuer_2025(history, tax)
    target, captures, cash_checks = inputs["primary"], {}, 0
    for ticker in g.TICKERS:
        held, made = g.capture(
            factory(),
            ticker,
            CAPTURE_SOURCE,
            lambda: need(False, "PROVIDER_CREDENTIAL_FORBIDDEN"),
            verify_only=True,
        )
        need(made == 0 and len(held) == 192, "CAPTURE_SCOPE")
        captures[ticker] = []
        for spec in g.products(ticker):
            item = held[spec["id"]]
            sid = "four-year-gap:" + spec["id"]
            source = u.source(sid, item["body"], item["receipt"], spec["url"])
            target["sources"].append(
                {
                    "identifier": source.identifier,
                    "sha256": source.sha256,
                    "observed_at": source.observed_at.isoformat(),
                    "url": source.url,
                }
            )
            captures[ticker].append(
                {
                    "product_id": spec["id"],
                    "capture_commit": item["commit"],
                    "body_sha256": source.sha256,
                }
            )
            if spec["kind"] in {"daily", "minute"}:
                for stamp, values, volume in u.bars(spec, item["body"]):
                    if spec["kind"] == "daily":
                        target["closes"].append(
                            {
                                "asset": ticker,
                                "session": str(stamp),
                                "close": str(values["c"]),
                                "source_id": sid,
                            }
                        )
                    else:
                        target["bars"].append(
                            {
                                "asset": ticker,
                                "end": stamp.isoformat(),
                                "source_id": sid,
                                **{
                                    name: str(values[key])
                                    for name, key in (
                                        ("open", "o"),
                                        ("high", "h"),
                                        ("low", "l"),
                                        ("close", "c"),
                                    )
                                },
                                "volume": str(volume),
                            }
                        )
            else:
                events = u.actions(spec, item["body"])
                expected = {
                    c.p.issuer_day(row["D"])
                    for row in history["dividend"][1:]
                    if row.get("B") == ticker
                    and spec["start"] <= str(c.p.issuer_day(row["D"])) <= spec["end"]
                }
                need({event["ex"] for event in events} == expected, "DIVIDEND_EVENT_COVERAGE")
                for event in events:
                    issuer_match(history, ticker, event)
                    cash_checks += 1
                    classification = "pending_same_year_2023_classification"
                    if event["ex"].year == 2025:
                        ordinary_2025(history, tax, ticker, event)
                        classification = "ordinary_fund"
                    else:
                        need(event["ex"].year == 2023, "UNEXPECTED_GAP_EVENT_YEAR")
                    target["distributions"].append(
                        {
                            "asset": ticker,
                            "event_id": event["id"],
                            "source_id": sid,
                            "ex_date": str(event["ex"]),
                            "record_date": str(event["record"]),
                            "pay_date": str(event["pay"]),
                            "cash_amount": str(event["amount"]),
                            "classification": classification,
                        }
                    )
    coverage = i.analyze(inputs, splits)
    require_complete(coverage)
    need(cash_checks == 48, "NATIVE_ISSUER_EVENT_COUNT")
    return {
        "schema": "rotation_four_year_gap_audit_v1",
        "capture_source": CAPTURE_SOURCE,
        "capture_plan_sha256": g.PLAN_HASH,
        "new_raw_products_verified": 2304,
        "native_dividend_events_matched_to_issuer": cash_checks,
        "issuer_2025_events_verified": events_2025,
        "coverage": coverage,
        "capture_receipt_index": captures,
        "settlement_reference_audit": settlement_audit(),
        "accepted_old_inputs_read_by_fixed_normalized_pins": True,
        "all_old_minute_raw_sources_revalidated_by_this_audit": False,
        "unresolved_execution_distribution_classification_years": [2023, 2024],
        "continuous_prices_observations_complete": True,
        "continuous_inputs_ready": False,
        "new_vendor_requests": 0,
        "new_official_requests": 0,
        "engine_invoked": False,
        "PIT_verified": False,
        "performance_admission": False,
    }


def pack(report, source):
    c.m.sha(source)
    raw = u.canonical(report)
    need(0 < len(raw) <= 32 * 1024 * 1024, "REPORT_EXPANSION")
    packed = gzip.compress(raw, mtime=0)
    parts = [packed[k : k + c.m.FILE_LIMIT] for k in range(0, len(packed), c.m.FILE_LIMIT)]
    need(0 < len(parts) <= MAX_PARTS, "REPORT_BUDGET")
    files = {PREFIX + f"report.json.gz.part-{k:02}.bin": body for k, body in enumerate(parts)}
    files[PREFIX + "manifest.json"] = u.canonical(
        {
            "schema": "rotation_four_year_gap_audit_manifest_v1",
            "source_commit": source,
            "capture_source": CAPTURE_SOURCE,
            "compressed_sha256": c.m.sha256(packed),
            "uncompressed_sha256": c.m.sha256(raw),
            "files": {path: c.m.sha256(body) for path, body in files.items()},
        }
    )
    return files


def retain(archive, files, *, verify_only):
    archive.visibility(True)
    need(
        set(files)
        == {
            PREFIX + "manifest.json",
            *(PREFIX + f"report.json.gz.part-{k:02}.bin" for k in range(len(files) - 1)),
        }
        and 2 <= len(files) <= MAX_PARTS + 1
        and all(0 < len(body) <= c.m.FILE_LIMIT for body in files.values()),
        "REPORT_PATHS_OR_SIZE",
    )
    if archive.ref(REPORT_REF) is None:
        need(not verify_only, "REPORT_MISSING")
        c.b.create(archive, REPORT_REF, files, c.m.BOOTSTRAP)
    saved, parent = c.b.read(archive, REPORT_REF, set(files))
    need(parent == c.m.BOOTSTRAP and saved == files, "FRESH_REPORT_BYTES")
    return c.m.sha256(u.canonical({p: c.m.sha256(body) for p, body in sorted(files.items())}))


def guarded(source):
    c.m.sha(source)
    need(
        os.environ.get("GITHUB_REPOSITORY") == c.m.CODE
        and os.environ.get("GITHUB_ACTOR") == "dczhhh"
        and os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_WORKFLOW_REF") == c.m.CODE + "/" + WORKFLOW + "@refs/heads/main"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("RUNNER_OS") == "Linux"
        and os.environ.get("ACTIONS_STEP_DEBUG", "").lower() != "true"
        and os.environ.get("ACTIONS_RUNNER_DEBUG", "").lower() != "true"
        and subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == source,
        "HOSTED_CONTEXT",
    )


def main():
    try:
        need(len(sys.argv) == 3 and sys.argv[1] in {"run", "verify"}, "ARGUMENTS")
        mode, source = sys.argv[1:]
        guarded(source)

        def factory(*, writable=False):
            return c.r.CachedArchive(
                c.PacedGitHub(
                    c.m.DESTINATION,
                    os.environ.get("ARCHIVE_WRITE_TOKEN", ""),
                    writable=writable,
                )
            )

        report = build(factory)
        digest = retain(
            factory(writable=mode == "run"), pack(report, source), verify_only=mode == "verify"
        )
        print(
            json.dumps(
                {
                    "status": "FOUR_YEAR_GAPS_AND_ISSUER_CASH_VERIFIED",
                    "bundle_sha256": digest,
                    "new_raw_products_verified": 2304,
                    "execution_sessions": 1003,
                    "native_dividend_events_matched_to_issuer": report[
                        "native_dividend_events_matched_to_issuer"
                    ],
                    "unresolved_execution_distribution_classification_years": [2023, 2024],
                    "new_vendor_requests": 0,
                    "new_official_requests": 0,
                    "engine_invoked": False,
                    "PIT_verified": False,
                    "performance_admission": False,
                }
            )
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "FOUR_YEAR_GAP_AUDIT_BLOCKED",
                    "reason": c.r.r.code(error),
                    "github_retry_at": getattr(error, "retry_at", None),
                    "engine_invoked": False,
                    **u.safe_failure_location(error),
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
