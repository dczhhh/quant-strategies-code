"""Issue #30 G1: retained-source inventory and a finite plan, never acquisition or Engine."""

from __future__ import annotations

import gzip
import json
import os
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rotation_research_replay as u  # noqa: E402

from ml4t.backtest.strategies.sector_rotation.config import SectorRotationConfig  # noqa: E402

c = u.c
START, FIRST, LAST = date(2023, 11, 1), date(2024, 12, 2), date(2025, 6, 30)
BASELINE = "2b5c6029a6cd5fbb9e261cf5726836077b10039e"
PREFIX = "research/verification/rotation_stress_202412_202506_inventory_v1/"
REPORT_REF = "research-data/rotation-stress-202412-202506-inventory-v1"
MAX_PROPOSED_REQUESTS = 700
CONTRACT = {
    "schema": "rotation_stress_inventory_contract_v1",
    "issue": 30,
    "baseline_source": BASELINE,
    "warmup_start": str(START),
    "execution_start": str(FIRST),
    "execution_end": str(LAST),
    "universe": list(c.p.TICKERS),
    "strategy": asdict(SectorRotationConfig()),
    "assumptions": {
        name: getattr(u.ASSUMPTIONS, name)
        for name in (
            "name",
            "initial_cash",
            "dividend_tax_rate",
            "modeled_spread",
            "slippage_bps",
            "maximum_participation",
        )
    },
    "share_quantum": "1",
    "minimum_presample_close_points": 253,
    "minute_policy": "same_13_completed_minutes_per_session_as_frozen_v4",
    "daily_minute_close_tolerance_usd": "0.0002",
    "calendar": "NYSE_exchange_sessions_not_NSCC_settlement_attestation",
    "inventory_scope": "reviewed_2024_ETF_sources_and_rotation_2025_v1_groups",
    "unrelated_private_archives_searched": False,
    "new_vendor_requests": 0,
    "new_official_requests": 0,
    "maximum_proposed_vendor_requests": MAX_PROPOSED_REQUESTS,
    "automatic_retry_allowed": False,
    "unknown_reservations_must_not_be_reset": True,
    "strict_2024_classification_may_use_2025_document": False,
    "research_scenario_may_be_assumed_without_separate_contract": False,
    "engine_invoked": False,
    "PIT_verified": False,
    "actual_broker_execution_verified": False,
    "performance_admission": False,
    "full_real_scenarios_accepted": 0,
}
CONTRACT_HASH = c.m.sha256(u.canonical(CONTRACT))


def required_minutes(day):
    session = c.CAL.session(day)
    c.p.need(session is not None, "REQUIRED_DAY_NOT_EXCHANGE_SESSION")
    return tuple(
        sorted(
            {session.market_open + timedelta(minutes=m) for m in (1, *range(5, 16))}
            | {session.market_close}
        )
    )


def spans(days, required):
    """Consecutive missing required dates; never include an already covered date."""
    result = []
    previous = -2
    for index, day in enumerate(required):
        if day not in days:
            continue
        if index == previous + 1:
            result[-1] = (result[-1][0], day)
        else:
            result.append((day, day))
        previous = index
    return result


def calendar_dates(first, last):
    return tuple(first + timedelta(days=i) for i in range((last - first).days + 1))


def product(ticker, kind, first, last):
    c.p.need(ticker in c.p.TICKERS, "TICKER_OUTSIDE_FIXED_SCOPE")
    c.p.need(START <= first <= last <= LAST, "STRESS_PRODUCT_OUTSIDE_FIXED_WINDOW")
    if kind in {"daily", "minute"}:
        lo, hi = str(first), str(last)
        if kind == "minute":
            lo = str(int(c.CAL.session(first).market_open.timestamp() * 1000))
            hi = str(int(c.CAL.session(last).market_close.timestamp() * 1000) - 1)
        url = (
            f"v2/aggs/ticker/{ticker}/range/1/{'day' if kind == 'daily' else 'minute'}/{lo}/{hi}"
            "?adjusted=false&sort=asc&limit=5000"
        )
    else:
        c.p.need(kind in {"dividends", "splits"}, "STRESS_PRODUCT_KIND_INVALID")
        field = "ex_dividend_date" if kind == "dividends" else "execution_date"
        url = f"stocks/v1/{kind}?ticker={ticker}&{field}.gte={first}&{field}.lte={last}&sort={field}.asc&limit=100"
    return {
        "id": f"{ticker.lower()}-{kind}-{first}-{last}",
        "ticker": ticker,
        "kind": kind,
        "start": str(first),
        "end": str(last),
        "url": "https://api.massive.com/" + url,
    }


def retained_products(archive, ticker):
    """Verify exact group tree/reservation; fetch only bodies intersecting this contract.

    The old 2024 raw/event requests are wholly contained in the larger retained
    rotation requests. Their pins and ranges are recorded, not re-downloaded.
    Adjusted prices and later execution minutes cannot add unique coverage here.
    """
    plan = c.groups(ticker)[0]
    prefix = c.PREFIX + ticker.lower() + "/0/"
    ref = c.group_ref(ticker, 0)
    revision = archive.ref(ref)
    c.p.need(revision is not None, "RETAINED_GROUP_MISSING")
    commit = archive.request("GET", "/git/commits/" + c.m.sha(revision))
    c.p.need(commit is not None and len(commit["parents"]) == 1, "RETAINED_GROUP_PARENT_INVALID")
    tree = archive.request("GET", "/git/trees/" + commit["tree"]["sha"] + "?recursive=1")
    c.p.need(tree is not None and tree.get("truncated") is False, "RETAINED_TREE_INVALID")
    rows = [row for row in tree["tree"] if row["type"] != "tree"]
    expected = {prefix + p["id"] + ".json" for p in plan} | {prefix + "receipts.json"}
    c.p.need(
        len(rows) == len(expected)
        and {r["path"] for r in rows} == expected
        and all(r["mode"] == "100644" and r["type"] == "blob" for r in rows),
        "RETAINED_TREE_SCOPE_INVALID",
    )
    entries = {r["path"]: r for r in rows}

    def body(name):
        row = entries[prefix + name]
        value = archive.blob(row["sha"], row["size"])
        c.b.secret_free(value, (archive.token,))
        return value

    receipt = c.b.decode(body("receipts.json"))
    budget = c.group_ref(ticker, 0, True)
    held, parent = c.b.read(archive, budget, {prefix + "reservation.json"})
    c.p.need(
        parent == c.m.BOOTSTRAP
        and commit["parents"][0]["sha"] == archive.ref(budget)
        and receipt["plan_sha256"] == c.PLAN_HASH
        and held[prefix + "reservation.json"] == c.reservation(ticker, 0, receipt["source_commit"])
        and len(receipt["rows"]) == len(plan),
        "RETAINED_RESERVATION_INVALID",
    )
    selected = []
    for spec, row in zip(plan, receipt["rows"], strict=True):
        c.p.need(row["product"] == spec, "RETAINED_PRODUCT_RECEIPT_INVALID")
        if spec["kind"] not in {"daily", "dividends", "splits"} or spec["start"] > str(LAST):
            continue
        raw = body(spec["id"] + ".json")
        start, end = (
            datetime.fromisoformat(row[k]) for k in ("request_started_at", "response_received_at")
        )
        c.p.need(
            row["http_status"] == 200
            and type(row["http_status"]) is int
            and row["body_sha256"] == c.m.sha256(raw)
            and row["body_bytes"] == len(raw)
            and start.utcoffset() is not None
            and end.utcoffset() is not None
            and start <= end,
            "RETAINED_SOURCE_RECEIPT_INVALID",
        )
        selected.append({"product": spec, "body": raw, "receipt": row, "commit": revision})
    c.p.need(len(selected) == 3, "RETAINED_PRODUCT_COVERAGE_INVALID")
    return selected


def documents(archive):
    docs = c.a.issuer_documents(archive)
    # Inspect only the outstanding 2024 document's old marker. No retry path exists.
    doc = c.p.DOCUMENTS[2]
    c.p.need(archive.ref(c.p.document_ref(doc)) is None, "OLD_2024_DOCUMENT_STATE_CHANGED")
    ref = c.p.document_ref(doc, True)
    revision = archive.ref(ref)
    c.p.need(revision is not None, "OLD_2024_DOCUMENT_RESERVATION_MISSING")
    path = c.p.DOCUMENT_PREFIX + doc["id"] + "/reservation.json"
    held, parent = c.b.read(archive, ref, {path})
    value = c.b.decode(held[path])
    c.p.need(
        parent == c.m.BOOTSTRAP and held[path] == c.p.reservation(doc, value["source_commit"]),
        "OLD_2024_DOCUMENT_RESERVATION_INVALID",
    )
    return docs, {"status": "UNKNOWN_REQUEST_OUTCOME_RETAINED", "reservation_commit": revision}


def analyze(captured, docs, old_document):
    required = c.sessions(START, LAST)
    event_dates = calendar_dates(START, LAST)
    execution = c.sessions(FIRST, LAST)
    presample = [d for d in required if d < FIRST]
    c.p.need(len(presample) >= 253, "STRESS_WARMUP_CONTRACT_INVALID")
    for doc in c.p.DOCUMENTS[:2]:
        item = docs[doc["id"]]
        u.source(doc["id"], item["body"], item["receipt"], doc["url"])
    history = c.p.workbook(docs[c.p.DOCUMENTS[0]["id"]]["body"])
    tax_sheets = {c.p.DOCUMENTS[1]["id"]: c.p.workbook(docs[c.p.DOCUMENTS[1]["id"]]["body"])}
    gaps = {str(year): c.p.classification_gaps(tax_sheets, year) for year in (2024, 2025)}
    cases, proposed = [], []
    for ticker in c.p.TICKERS:
        daily, requested = {}, {k: set() for k in ("daily", "dividends", "splits")}
        events, sources = [], []
        for item in captured[ticker]:
            spec, raw = item["product"], item["body"]
            kind = spec["kind"]
            c.p.need(
                spec["ticker"] == ticker and kind in requested, "INVENTORY_SOURCE_SCOPE_INVALID"
            )
            dates = c.sessions if kind == "daily" else calendar_dates
            requested[kind].update(dates(c.a.day(spec["start"]), min(c.a.day(spec["end"]), LAST)))
            sources.append({"product": spec, "receipt": item["receipt"], "commit": item["commit"]})
            if kind == "daily":
                for day, values, _ in u.bars(spec, raw):
                    c.p.need(c.CAL.session(day) is not None, "RETAINED_DAILY_NONSESSION")
                    if START <= day <= LAST:
                        c.p.need(day not in daily, "RETAINED_DAILY_DUPLICATE")
                        daily[day] = values["c"]
            else:
                parsed = u.actions(spec, raw)
                if kind == "dividends":
                    events += [e for e in parsed if FIRST <= e["ex"] <= LAST]
        missing_daily = set(required) - daily.keys()
        # A requested-but-absent row is a source anomaly, not a new request license.
        absent_rows = missing_daily & requested["daily"]
        for kind, covered in requested.items():
            dates = required if kind == "daily" else event_dates
            for first, last in spans(set(dates) - covered, dates):
                proposed.append(product(ticker, kind, first, last))
        # Existing reviewed ETF minute requests all begin after this fixed window.
        c.p.need(
            all(p["start"] > str(LAST) for p in c.products(ticker) if p["kind"] == "minute"),
            "RETAINED_MINUTE_SCOPE_CHANGED",
        )
        for i in range(0, len(execution), 3):
            chunk = execution[i : i + 3]
            proposed.append(product(ticker, "minute", chunk[0], chunk[-1]))
        classified = []
        seen = set()
        for event in events:
            c.p.need(event["id"] not in seen, "RETAINED_EVENT_DUPLICATE")
            seen.add(event["id"])
            match = c.p.history_match(history, ticker, [event])
            status = "EVENT_YEAR_FINAL_CLASSIFICATION_MISSING"
            if event["ex"].year == 2025:
                try:
                    u.classify(ticker, event, docs)
                    status = "ORDINARY_2025_RETROSPECTIVE_RESEARCH_FIELDS_MATCH"
                except Exception as error:
                    status = c.r.r.code(error)
            classified.append(
                {"event": event, "issuer_history": match, "classification_status": status}
            )
        cases.append(
            {
                "ticker": ticker,
                "sources": sources,
                "daily_sessions_present": len(daily),
                "presample_close_points_present": sum(day < FIRST for day in daily),
                "missing_daily_sessions": sorted(missing_daily),
                "requested_but_absent_daily_sessions": sorted(absent_rows),
                "missing_completed_minutes": {str(d): required_minutes(d) for d in execution},
                "execution_distribution_checks": classified,
            }
        )
    counts = {}
    for row in proposed:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    c.p.need(
        len(proposed) <= MAX_PROPOSED_REQUESTS,
        "STRESS_REQUEST_PLAN_EXCEEDS_FIXED_CEILING",
    )
    summary = {
        "status": "G1_RETAINED_SOURCE_INVENTORY_COMPLETE_GAPS_OPEN",
        "contract_sha256": CONTRACT_HASH,
        "execution_sessions": len(execution),
        "required_daily_sessions_per_asset": len(required),
        "required_presample_close_points": 253,
        "conservative_presample_sessions": len(presample),
        "required_completed_minutes_per_asset": sum(len(required_minutes(d)) for d in execution),
        "per_asset": [
            {
                "ticker": row["ticker"],
                "daily_sessions_present": row["daily_sessions_present"],
                "presample_close_points_present": row["presample_close_points_present"],
                "daily_sessions_missing": len(row["missing_daily_sessions"]),
                "requested_but_absent_daily_sessions": len(
                    row["requested_but_absent_daily_sessions"]
                ),
                "minute_sessions_missing": len(row["missing_completed_minutes"]),
                "distribution_classification_status_counts": {
                    status: sum(
                        e["classification_status"] == status
                        for e in row["execution_distribution_checks"]
                    )
                    for status in sorted(
                        {e["classification_status"] for e in row["execution_distribution_checks"]}
                    )
                },
            }
            for row in cases
        ],
        "proposed_missing_input_requests": counts,
        "proposed_maximum_new_vendor_requests": len(proposed),
        "acquisition_executed": False,
        "old_2024_document_status": old_document["status"],
        "final_classification_2024_verified": False,
        "independent_decision_oracle_verified": False,
        "close_source_diagnostics_verified": False,
        "settlement_calendar_verified": False,
        "engine_invoked": False,
        "new_vendor_requests": 0,
        "new_official_requests": 0,
        "PIT_verified": False,
        "actual_broker_execution_verified": False,
        "performance_admission": False,
        "full_real_scenarios_accepted": 0,
    }
    return {
        "schema": "rotation_stress_inventory_v1",
        "contract": CONTRACT,
        "summary": summary,
        "cases": cases,
        "classification_gaps": gaps,
        "old_2024_document": old_document,
        "proposed_products_not_authorized_by_this_inventory": proposed,
        "redundant_2024_source_pins_not_downloaded": c.p.INPUT_COMMITS,
        "issuer_documents": {
            k: {key: v[key] for key in ("receipt", "commit")} for k, v in docs.items()
        },
    }


def execute(archive, source, *, verify_only=False):
    c.m.sha(source)
    archive.visibility(True)
    docs, old_document = documents(archive)
    captured = {ticker: retained_products(archive, ticker) for ticker in c.p.TICKERS}
    report = analyze(captured, docs, old_document)
    payload = {PREFIX + "inventory.json.gz": gzip.compress(u.canonical(report), mtime=0)}
    payload[PREFIX + "manifest.json"] = u.canonical(
        {
            "schema": "rotation_stress_inventory_manifest_v1",
            "source_commit": source,
            "contract_sha256": CONTRACT_HASH,
            "module_sha256": c.m.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")),
            "files": {name: c.m.sha256(body) for name, body in payload.items()},
        }
    )
    if archive.ref(REPORT_REF) is None:
        c.p.need(not verify_only, "STRESS_INVENTORY_REPORT_MISSING")
        c.b.create(archive, REPORT_REF, payload, c.m.BOOTSTRAP)
    saved, parent = c.b.read(archive, REPORT_REF, set(payload))
    c.p.need(saved == payload and parent == c.m.BOOTSTRAP, "INDEPENDENT_STRESS_INVENTORY_CONFLICT")
    return report["summary"] | {
        "report_bundle_sha256": c.m.sha256(
            u.canonical({k: c.m.sha256(v) for k, v in sorted(payload.items())})
        )
    }


def main():
    try:
        c.p.need(len(sys.argv) == 3 and sys.argv[1] in {"run", "verify"}, "ARGUMENTS_INVALID")
        mode, source = sys.argv[1:]
        c.guarded(source)
        archive = c.r.CachedArchive(
            c.PacedGitHub(
                c.m.DESTINATION, os.environ.get("ARCHIVE_WRITE_TOKEN", ""), writable=mode == "run"
            )
        )
        print(json.dumps(execute(archive, source, verify_only=mode == "verify")))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "STRESS_INVENTORY_BLOCKED",
                    "reason": c.r.r.code(error),
                    "engine_invoked": False,
                    **u.safe_failure_location(error),
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
