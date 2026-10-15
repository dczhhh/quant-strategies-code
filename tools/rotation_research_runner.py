"""Bounded retrospective input preparation and independent canonical Engine replay.

Licensed responses stay in GitHub-hosted runners and the private archive. Main
workflow code checks out one separately pinned, tested strategy revision; this
does not merge or certify that strategy. Public output is strictly allowlisted.
"""

from __future__ import annotations

import importlib
import json
import os
import runpy
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, build_opener

u = SimpleNamespace(**runpy.run_path(str(Path(__file__).with_name("real_etf_2025_audit.py"))))
r, a, p, b, m = u.r, u.a, u.p, u.b, u.m
ROOT = Path(__file__).resolve().parents[1]
# Filled with the exact CI-qualified PR #6 revision before this operator is published.
STRATEGY_SHA = "eb70296eef3c79ed9bc259703504ed568e13b3ae"
OLD_AUDIT = "04f22aef8f5881bb1754edc9001c4ee59f36c0e2"
OLD_CODE = "e0df084fbe316dbaf5db25de8516f0e20b606aca"
OLD_DIGEST = "819bbdb8c04f244e25af999f352245ebf0c9aebb6228258a4ef9a0534af3a01c"
WARM_START, WARM_END = "2024-08-01", "2025-09-14"
RUN_START, RUN_END = "2025-09-02", "2025-11-03"
PREFIX = "research/data/rotation_research_inputs_v1/"
COMPONENT_PREFIX = "research/verification/rotation_research_components_v1/"
REPORT_PREFIX = "research/verification/rotation_research_execution_v1/"
MAX_BODY = 8 * 1024 * 1024
SCENARIO_PATH = ROOT / "config/rotation_research_2025.json"


def products(ticker):
    p.need(type(ticker) is str and ticker in p.TICKERS, "TICKER_OUTSIDE_FIXED_SCOPE")
    return tuple(
        {"id": ticker.lower() + "-" + kind, "ticker": ticker, "kind": kind, "url": url}
        for kind, url in (
            (
                "warm-raw",
                f"https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/day/{WARM_START}/{WARM_END}?adjusted=false&sort=asc&limit=5000",
            ),
            (
                "minutes",
                f"https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/minute/{RUN_START}/{RUN_END}?adjusted=false&sort=asc&limit=50000",
            ),
            (
                "warm-dividends",
                f"https://api.massive.com/stocks/v1/dividends?ticker={ticker}&ex_dividend_date.gte={WARM_START}&ex_dividend_date.lte={WARM_END}&sort=ex_dividend_date.asc&limit=1000",
            ),
            (
                "warm-splits",
                f"https://api.massive.com/stocks/v1/splits?ticker={ticker}&execution_date.gte={WARM_START}&execution_date.lte={WARM_END}&sort=execution_date.asc&limit=1000",
            ),
        )
    )


POLICY = {
    "schema": "rotation_research_capture_plan_v1",
    "maximum_new_market_requests": 48,
    "maximum_per_ticker": 4,
    "maximum_response_bytes": MAX_BODY,
    "single_attempt": True,
    "automatic_retry": False,
    "old_unknown_reservations_unchanged": True,
    "new_issuer_workbook_requests": 0,
    "reuse_old_audit_commit": OLD_AUDIT,
    "reuse_old_bundle_digest": OLD_DIGEST,
    "signal_cash_field": "cash_amount",
    "execution_clock": "completed_minute_end",
    "evidence_mode": "retrospective_research",
    "PIT_verified": False,
    "performance_admission": False,
    "full_real_scenarios_accepted": 0,
}
PLAN_HASH = m.sha256(
    m.canonical({"products": {t: products(t) for t in p.TICKERS}, "policy": POLICY})
)


def checked(product):
    p.need(any(product == v for t in p.TICKERS for v in products(t)), "PRODUCT_OUTSIDE_FIXED_SCOPE")


def ref(product, reservation=False):
    checked(product)
    return (
        ("research-reservations/" if reservation else "research-data/")
        + "rotation-input-v1-"
        + product["id"]
    )


def hold_body(product, source):
    return m.canonical(
        {
            "schema": "rotation_input_reservation_v1",
            "source_commit": m.sha(source),
            "product": product,
            "policy": POLICY,
            "plan_sha256": PLAN_HASH,
        }
    )


def pinned_file(archive, commit, path):
    info = archive.request("GET", "/git/commits/" + m.sha(commit))
    p.need(info is not None and len(info["parents"]) == 1, "PINNED_COMMIT_INVALID")
    tree = archive.request("GET", "/git/trees/" + m.sha(info["tree"]["sha"]) + "?recursive=1")
    p.need(tree is not None and tree.get("truncated") is False, "PINNED_TREE_INVALID")
    rows = [
        x
        for x in tree["tree"]
        if x["path"] == path and x["type"] == "blob" and x["mode"] == "100644"
    ]
    p.need(len(rows) == 1, "PINNED_FILE_MISSING")
    return archive.blob(rows[0]["sha"], rows[0]["size"])


def body_secret_free(body, credentials):
    # Whole HTTP responses may exceed the shared per-blob limit. Keep that
    # existing limit unchanged and screen the entire body before chunking it.
    p.need(
        type(body) is bytes
        and len(body) <= MAX_BODY
        and not b.CREDENTIAL_PATTERN.search(body)
        and all(not key or key.encode() not in body for key in credentials),
        "RESEARCH_BODY_OR_CREDENTIAL_INVALID",
    )


def write_record(archive, name, prefix, body, metadata, parent):
    p.need(type(body) is bytes and len(body) <= MAX_BODY, "RESEARCH_BODY_BOUNDS")
    body_secret_free(body, (archive.token,))
    parts = [body[i : i + m.FILE_LIMIT] for i in range(0, len(body), m.FILE_LIMIT)]
    receipt = {
        **metadata,
        "record_schema": "rotation_chunked_record_v1",
        "part_count": len(parts),
        "body_bytes": len(body),
        "body_sha256": m.sha256(body),
    }
    files = {prefix + f"part-{i:03}.bin": part for i, part in enumerate(parts)}
    files[prefix + "receipt.json"] = m.canonical(receipt)
    return b.create(archive, name, files, parent)


def read_record(archive, name, prefix):
    revision = archive.ref(name)
    p.need(revision is not None, "RESEARCH_RECORD_MISSING")
    metadata = b.decode(pinned_file(archive, revision, prefix + "receipt.json"))
    count, size = metadata.get("part_count"), metadata.get("body_bytes")
    p.need(
        metadata.get("record_schema") == "rotation_chunked_record_v1"
        and type(count) is int
        and 0 <= count <= MAX_BODY // m.FILE_LIMIT
        and type(size) is int
        and 0 <= size <= MAX_BODY,
        "RESEARCH_RECORD_BOUNDS",
    )
    paths = {prefix + f"part-{i:03}.bin" for i in range(count)} | {prefix + "receipt.json"}
    files, parent = b.read(archive, name, paths)
    body = b"".join(files[prefix + f"part-{i:03}.bin"] for i in range(count))
    p.need(
        len(body) == size and m.sha256(body) == metadata.get("body_sha256"),
        "RESEARCH_RECORD_HASH_MISMATCH",
    )
    body_secret_free(body, (archive.token,))
    return body, metadata, parent, revision


def fetch_product(product, key):
    checked(product)
    p.need(
        type(key) is str and 0 < len(key) <= 4096 and not any(c.isspace() for c in key),
        "CREDENTIAL_UNAVAILABLE",
    )
    request = Request(
        product["url"], headers={"Authorization": "Bearer " + key, "Accept": "application/json"}
    )
    try:
        with build_opener(m.NoRedirect()).open(request, timeout=45) as response:
            status, body = response.status, response.read(MAX_BODY + 1)
    except HTTPError as error:
        with error:
            status, body = error.code, error.read(MAX_BODY + 1)
    body_secret_free(body, (key,))
    p.need(len(body) <= MAX_BODY, "RESEARCH_BODY_BOUNDS")
    return status, body


def capture_product(archive, product, source, key_factory, *, readonly=False, fetch=fetch_product):
    checked(product)
    prefix = PREFIX + product["id"] + "/"
    made = 0
    if archive.ref(ref(product)) is None:
        p.need(not readonly, "RESEARCH_INPUT_MISSING")
        p.need(archive.ref(ref(product, True)) is None, "UNKNOWN_REQUEST_OUTCOME_RETAINED")
        key = key_factory()
        p.need(
            type(key) is str and 0 < len(key) <= 4096 and not any(c.isspace() for c in key),
            "CREDENTIAL_UNAVAILABLE",
        )
        hold = b.create(
            archive,
            ref(product, True),
            {prefix + "reservation.json": hold_body(product, source)},
            m.BOOTSTRAP,
        )
        started = datetime.now(UTC).isoformat()
        status, body = fetch(product, key)
        made = 1
        p.need(type(status) is int and 100 <= status <= 599, "HTTP_STATUS_INVALID")
        body_secret_free(body, (key, archive.token))
        write_record(
            archive,
            ref(product),
            prefix,
            body,
            {
                "schema": "rotation_input_receipt_v1",
                "source_commit": source,
                "product": product,
                "plan_sha256": PLAN_HASH,
                "policy": POLICY,
                "http_status": status,
                "request_started_at": started,
                "response_received_at": datetime.now(UTC).isoformat(),
            },
            hold,
        )
    body, receipt, parent, revision = read_record(archive, ref(product), prefix)
    held, bootstrap = b.read(archive, ref(product, True), {prefix + "reservation.json"})
    p.need(
        parent == archive.ref(ref(product, True))
        and bootstrap == m.BOOTSTRAP
        and held[prefix + "reservation.json"] == hold_body(product, receipt["source_commit"])
        and receipt.get("schema") == "rotation_input_receipt_v1"
        and receipt.get("product") == product
        and receipt.get("policy") == POLICY
        and receipt.get("plan_sha256") == PLAN_HASH,
        "RESEARCH_RECEIPT_INVALID",
    )
    start, end = (
        datetime.fromisoformat(receipt[k]) for k in ("request_started_at", "response_received_at")
    )
    p.need(
        start.utcoffset() is not None and end.utcoffset() is not None and start <= end,
        "RESEARCH_SOURCE_CLOCK_INVALID",
    )
    p.need(receipt.get("http_status") == 200, "RESEARCH_INPUT_HTTP_UNVERIFIED")
    return {"body": body, "receipt": receipt, "commit": revision}, made


def source_record(identifier, record, url, *, issuer=False):
    return {
        "identifier": identifier,
        "sha256": m.sha256(record["body"]),
        "url": url,
        "observed_at": record["receipt"]["fetched_at" if issuer else "response_received_at"],
    }


def old_inputs(archive, ticker):
    root = u.PREFIX + OLD_CODE + "/"
    manifest_body = pinned_file(archive, OLD_AUDIT, root + "manifest.json")
    manifest = b.decode(manifest_body)
    p.need(m.sha256(m.canonical(manifest)) == OLD_DIGEST, "OLD_AUDIT_MANIFEST_MISMATCH")
    path = root + ticker.lower() + "/report.json"
    body = pinned_file(archive, OLD_AUDIT, path)
    p.need(m.sha256(body) == manifest[path], "OLD_AUDIT_REPORT_MISMATCH")
    report = b.decode(body)
    p.need(
        report["raw_basis_case"]["status"] == "RAW_BASIS_RETROSPECTIVE_SIGNAL_COMPONENT_PASS",
        "OLD_RAW_COMPONENT_UNVERIFIED",
    )
    captured, made = r.capture(
        archive,
        ticker,
        OLD_CODE,
        lambda: p.need(False, "OLD_INPUT_REQUEST_FORBIDDEN"),
        verify_only=True,
    )
    p.need(
        made == 0
        and all(
            v["commit"] == report["inputs"][k]["commit"]
            and v["receipt"] == report["inputs"][k]["receipt"]
            for k, v in captured.items()
        ),
        "OLD_INPUT_PIN_MISMATCH",
    )
    return captured, report["strict_combined_case"]["status"]


def rows(product, record, *, event=False):
    value = a.decode_market(record["body"])
    p.need(
        value.get("status") in {"OK", "DELAYED"} and not value.get("next_url"),
        "RESEARCH_RESPONSE_INCOMPLETE",
    )
    result = value.get("results")
    p.need(
        type(result) is list and len(result) <= (1000 if event else 50000),
        "RESEARCH_RESULTS_INVALID",
    )
    if not event:
        p.need(
            value.get("ticker") == product["ticker"] and value.get("adjusted") is False,
            "RESEARCH_BAR_BASIS_INVALID",
        )
    return result


def normalize_component(ticker, captured, old, documents):
    from quant_constraints.calendar import NY, SessionCalendar

    calendar = SessionCalendar()
    sources = []
    closes, bars, dividends, splits = [], [], [], []
    for kind, record in captured.items():
        product = next(v for v in products(ticker) if v["kind"] == kind)
        sources.append(source_record(product["id"], record, product["url"]))
        if kind not in {"warm-raw", "minutes"}:
            continue
        seen = set()
        for row in rows(product, record):
            p.need(
                type(row) is dict and type(row.get("t")) is int and row["t"] not in seen,
                "RESEARCH_BAR_CLOCK_INVALID",
            )
            seen.add(row["t"])
            stamp = datetime.fromtimestamp(row["t"] / 1000, UTC).astimezone(NY)
            values = {k: a.market_number(row.get(k), positive=True) for k in ("o", "h", "l", "c")}
            volume = a.market_number(row.get("v"))
            p.need(
                values["l"]
                <= min(values["o"], values["c"])
                <= max(values["o"], values["c"])
                <= values["h"],
                "RESEARCH_OHLC_INVALID",
            )
            if kind == "warm-raw":
                p.need(
                    stamp.time().isoformat() == "00:00:00"
                    and WARM_START <= stamp.date().isoformat() <= WARM_END,
                    "RESEARCH_WARMUP_WINDOW_INVALID",
                )
                closes.append(
                    {
                        "asset": ticker,
                        "session": stamp.date().isoformat(),
                        "close": str(values["c"]),
                        "source_id": product["id"],
                    }
                )
            else:
                p.need(
                    row["t"] % 60000 == 0 and RUN_START <= stamp.date().isoformat() <= RUN_END,
                    "RESEARCH_MINUTE_WINDOW_INVALID",
                )
                end = stamp + timedelta(minutes=1)
                session = calendar.session(stamp.date())
                if (
                    session
                    and session.market_open < end <= session.market_close
                    and (
                        end == session.market_close
                        or end == session.market_open + timedelta(minutes=1)
                        or session.market_open + timedelta(minutes=5)
                        <= end
                        <= session.market_open + timedelta(minutes=15)
                    )
                ):
                    bars.append(
                        {
                            "asset": ticker,
                            "end": end.isoformat(),
                            "open": str(values["o"]),
                            "high": str(values["h"]),
                            "low": str(values["l"]),
                            "close": str(values["c"]),
                            "volume": str(volume),
                            "source_id": product["id"],
                        }
                    )
    raw_product = next(v for v in a.products(ticker) if r.kind(v) == "raw")
    raw_id = "retained:" + raw_product["id"]
    sources.append(source_record(raw_id, old["raw"], raw_product["url"]))
    for day, close in a.market_rows(raw_product, old["raw"]["body"]).items():
        closes.append(
            {"asset": ticker, "session": day.isoformat(), "close": str(close), "source_id": raw_id}
        )
    primary_doc = documents[p.DOCUMENTS[1]["id"]]
    history_doc = documents[p.DOCUMENTS[0]["id"]]
    sources += [
        source_record("issuer-primary", primary_doc, p.DOCUMENTS[1]["url"], issuer=True),
        source_record("issuer-history", history_doc, p.DOCUMENTS[0]["url"], issuer=True),
    ]
    primary = p.workbook(primary_doc["body"])["2025 ICI Primary"]
    history = p.workbook(history_doc["body"])["dividend"]
    p.need(
        primary[0].get("A") == "SPDR ETF 2025 Tax Summary - Primary Layout"
        and all(primary[12].get(k) == v for k, v in a.TAX_HEADERS.items()),
        "RESEARCH_TAX_SCHEMA_INVALID",
    )
    identities = {v.get("B") for v in primary[13:] if v.get("C") == ticker}
    p.need(len(identities) == 1 and None not in identities, "RESEARCH_IDENTITY_AMBIGUOUS")
    cusip = next(iter(identities))
    for kind in ("dividends", "splits"):
        old_product = next(v for v in a.products(ticker) if r.kind(v) == kind)
        new_product = next(v for v in products(ticker) if v["kind"] == "warm-" + kind)
        for product, record, start, finish, identifier in (
            (new_product, captured["warm-" + kind], WARM_START, WARM_END, new_product["id"]),
            (
                old_product,
                old[kind],
                a.START,
                a.SPLIT_END if kind == "splits" else a.END,
                "retained:" + old_product["id"],
            ),
        ):
            if identifier.startswith("retained:"):
                sources.append(source_record(identifier, record, product["url"]))
            seen = set()
            for event in rows(product, record, event=True):
                p.need(
                    type(event) is dict
                    and event.get("ticker") == ticker
                    and type(event.get("id")) is str
                    and event["id"]
                    and event["id"] not in seen,
                    "RESEARCH_EVENT_IDENTITY_INVALID",
                )
                seen.add(event["id"])
                day = a.day(event.get("execution_date" if kind == "splits" else "ex_dividend_date"))
                p.need(start <= day.isoformat() <= finish, "RESEARCH_EVENT_WINDOW_INVALID")
                if day.isoformat() > RUN_END:
                    continue
                if kind == "splits":
                    before, after = (
                        a.market_number(event.get(k), positive=True)
                        for k in ("split_from", "split_to")
                    )
                    p.need(
                        event.get("adjustment_type") in {"forward_split", "reverse_split"},
                        "RESEARCH_SPLIT_UNSUPPORTED",
                    )
                    splits.append(
                        {
                            "asset": ticker,
                            "event_id": "split:" + event["id"],
                            "session": day.isoformat(),
                            "new_per_old": str(after / before),
                            "source_id": identifier,
                        }
                    )
                    continue
                amount = a.market_number(event.get("cash_amount"), positive=True)
                record_day, pay = (a.day(event.get(k)) for k in ("record_date", "pay_date"))
                p.need(
                    event.get("currency") == "USD" and day <= record_day <= pay,
                    "RESEARCH_DIVIDEND_INVALID",
                )
                classification, class_source = "unclassified_signal_only", None
                if day.isoformat() >= RUN_START:
                    selected = [
                        v
                        for v in primary[13:]
                        if v.get("C") == ticker and p.issuer_day(v.get("H")) == day
                    ]
                    matched = [
                        v
                        for v in history[1:]
                        if v.get("B") == ticker and p.issuer_day(v.get("D")) == day
                    ]
                    p.need(len(selected) == len(matched) == 1, "RESEARCH_ISSUER_EVENT_MISSING")
                    row, historical = selected[0], matched[0]
                    p.need(
                        row.get("B") == historical.get("C") == cusip
                        and row.get("D") == ""
                        and row.get("E") in {"", "R"}
                        and row.get("F") in {"", "C"}
                        and p.issuer_day(row.get("G"))
                        == p.issuer_day(historical.get("E"))
                        == record_day
                        and p.issuer_day(row.get("I")) == p.issuer_day(historical.get("F")) == pay,
                        "RESEARCH_ISSUER_STATUS_OR_IDENTITY_INVALID",
                    )
                    numbers = {k: p.number(row.get(k)) for k in a.TAX_NUMBERS}
                    p.need(
                        all(
                            abs(numbers[k] - amount) <= Decimal("1E-8")
                            for k in ("J", "M", "N", "Q")
                        )
                        and abs(p.number(historical.get("G")) - amount) <= Decimal("1E-8")
                        and all(numbers[k] == 0 for k in ("O", "P", "V", "Z", "AB", "AC", "AD")),
                        "RESEARCH_ORDINARY_FUND_CLASSIFICATION_UNVERIFIED",
                    )
                    classification, class_source = "ordinary_fund", "issuer-primary"
                dividends.append(
                    {
                        "asset": ticker,
                        "event_id": "dividend:" + event["id"],
                        "ex_date": day.isoformat(),
                        "record_date": record_day.isoformat(),
                        "pay_date": pay.isoformat(),
                        "cash_amount": str(amount),
                        "classification": classification,
                        "source_id": identifier,
                        "classification_source_id": class_source,
                    }
                )

    def trading_days(start, end):
        day, final, result = a.day(start), a.day(end), []
        while day <= final:
            if calendar.session(day):
                result.append(day)
            day += timedelta(days=1)
        return result

    expected = trading_days(WARM_START, RUN_END)
    p.need(
        len({v["session"] for v in closes}) == len(closes)
        and sorted(v["session"] for v in closes) == [d.isoformat() for d in expected]
        and sum(d.isoformat() < RUN_START for d in expected) >= 253,
        "RESEARCH_DAILY_COVERAGE_INCOMPLETE",
    )
    observed = {datetime.fromisoformat(v["end"]) for v in bars}
    for day in trading_days(RUN_START, RUN_END):
        session = calendar.session(day)
        required = {
            session.market_close,
            *(session.market_open + timedelta(minutes=n) for n in (1, *range(5, 16))),
        }
        p.need(required <= observed, "RESEARCH_MINUTE_COVERAGE_INCOMPLETE")
    return {
        "schema": "rotation_component_v1",
        "ticker": ticker,
        "closes": sorted(closes, key=lambda x: x["session"]),
        "bars": sorted(bars, key=lambda x: x["end"]),
        "distributions": dividends,
        "splits": splits,
        "sources": sources,
        "cusip": cusip,
        "plan_sha256": PLAN_HASH,
        "old_audit_commit": OLD_AUDIT,
    }


def component_ref(ticker, source):
    return "research-data/rotation-component-v1-" + ticker.lower() + "-" + m.sha(source)


def prepare(archive, ticker, source, key_factory, *, readonly=False, fetch=fetch_product):
    archive.visibility(True)
    old, strict = old_inputs(archive, ticker)
    documents = a.issuer_documents(archive)
    captured, made = {}, 0
    for product in products(ticker):
        item, count = capture_product(
            archive, product, source, key_factory, readonly=readonly, fetch=fetch
        )
        captured[product["kind"]] = item
        made += count
    component = normalize_component(ticker, captured, old, documents)
    component.update(collector_source=source, strict_vendor_component_status_preserved=strict)
    body = m.canonical(component)
    prefix = COMPONENT_PREFIX + source + "/" + ticker.lower() + "/"
    name = component_ref(ticker, source)
    if archive.ref(name) is None:
        p.need(not readonly, "NORMALIZED_COMPONENT_MISSING")
        write_record(
            archive,
            name,
            prefix,
            body,
            {
                "schema": "rotation_component_receipt_v1",
                "source_commit": source,
                "plan_sha256": PLAN_HASH,
                "ticker": ticker,
            },
            m.BOOTSTRAP,
        )
    saved, metadata, parent, _ = read_record(archive, name, prefix)
    p.need(
        saved == body
        and parent == m.BOOTSTRAP
        and metadata.get("source_commit") == source
        and metadata.get("plan_sha256") == PLAN_HASH
        and metadata.get("ticker") == ticker,
        "NORMALIZED_COMPONENT_READBACK_MISMATCH",
    )
    return {
        "ticker": ticker,
        "status": "RETROSPECTIVE_INPUT_COMPONENT_MATCH",
        "component_sha256": m.sha256(body),
        "new_market_requests": made,
        "new_issuer_workbook_requests": 0,
        "PIT_verified": False,
        "engine_invoked": False,
        "performance_admission": False,
        "full_real_scenarios_accepted": 0,
    }


def replay(archive, source, code_date, *, readonly=False):
    archive.visibility(True)
    # These components have already been regenerated from raw sources in the
    # preceding independent per-ticker jobs. This stage binds their exact bytes.
    runtime = importlib.import_module("ml4t.backtest.strategies.sector_rotation.research")
    model = importlib.import_module("ml4t.backtest.strategies.sector_rotation.research_inputs")

    runtime_file = runtime.__file__
    if not isinstance(runtime_file, str):
        raise p.Blocked("STRATEGY_SOURCE_PATH_MISSING")
    p.need(
        Path(runtime_file).resolve().is_relative_to((ROOT / "research-source/src").resolve()),
        "STRATEGY_IMPORT_OUTSIDE_PINNED_SOURCE",
    )
    scenario_body = SCENARIO_PATH.read_bytes()
    scenario = b.decode(scenario_body)
    components, sources = [], {}
    for ticker in p.TICKERS:
        body, metadata, parent, _ = read_record(
            archive,
            component_ref(ticker, source),
            COMPONENT_PREFIX + source + "/" + ticker.lower() + "/",
        )
        component = b.decode(body)
        p.need(
            parent == m.BOOTSTRAP
            and metadata.get("source_commit") == source
            and metadata.get("plan_sha256") == PLAN_HASH
            and component.get("plan_sha256") == PLAN_HASH
            and component.get("ticker") == ticker
            and component.get("old_audit_commit") == OLD_AUDIT,
            "REPLAY_COMPONENT_BINDING_INVALID",
        )
        for record in component["sources"]:
            old = sources.setdefault(record["identifier"], record)
            p.need(old == record, "REPLAY_SOURCE_CONFLICT")
        components.append(component)
    study_id = "reviewed-study-definition"
    sources[study_id] = {
        "identifier": study_id,
        "sha256": m.sha256(scenario_body),
        "observed_at": code_date,
        "url": "https://github.com/"
        + m.CODE
        + "/blob/"
        + source
        + "/config/rotation_research_2025.json",
    }
    definition = scenario["settlement_definition"]
    day, last = a.day(definition["from"]), a.day(definition["through"])
    settlement = []
    while day <= last:
        if (
            day.weekday() in definition["business_weekdays"]
            and day.isoformat() not in definition["excluded_dates"]
        ):
            settlement.append(day.isoformat())
        day += timedelta(days=1)
    document: dict[str, Any] = {
        "schema": "rotation_retrospective_inputs_v1",
        "sources": list(sources.values()),
        **{
            k: sorted(
                [v for c in components for v in c[k]],
                key=lambda x: (x.get("session", x.get("end", x.get("ex_date", ""))), x["asset"]),
            )
            for k in ("closes", "bars", "distributions", "splits")
        },
        "securities": [
            {
                "asset": c["ticker"],
                "security_id": c["cusip"],
                "issuer_country": scenario["issuer_country_assumption"],
                "tax_source_country": scenario["tax_source_country_assumption"],
                "security_type": scenario["security_type_assumption"],
                "is_depositary_receipt": scenario["depositary_receipt_assumption"],
                "source_id": study_id,
            }
            for c in components
        ],
        "settlement_sessions": settlement,
        "settlement_source_id": study_id,
        "origin": "retained_market_data",
    }
    # Source body verification occurred in independent per-ticker preparation.
    # Reconstruct the typed aggregate from the immutable normalized components.
    normalized = m.canonical(document)
    bundle = model.ResearchInputs(
        tuple(
            model.ResearchSource(
                v["identifier"], v["sha256"], datetime.fromisoformat(v["observed_at"]), v["url"]
            )
            for v in document["sources"]
        ),
        tuple(
            model.RawClose(v["asset"], a.day(v["session"]), Decimal(v["close"]), v["source_id"])
            for v in document["closes"]
        ),
        tuple(
            model.ResearchBar(
                v["asset"],
                datetime.fromisoformat(v["end"]),
                Decimal(v["open"]),
                Decimal(v["high"]),
                Decimal(v["low"]),
                Decimal(v["close"]),
                Decimal(v["volume"]),
                v["source_id"],
            )
            for v in document["bars"]
        ),
        tuple(
            model.ResearchDistribution(
                v["asset"],
                v["event_id"],
                a.day(v["ex_date"]),
                a.day(v["record_date"]),
                a.day(v["pay_date"]),
                Decimal(v["cash_amount"]),
                v["classification"],
                v["source_id"],
                v["classification_source_id"],
            )
            for v in document["distributions"]
        ),
        tuple(
            model.ResearchSplit(
                v["asset"],
                v["event_id"],
                a.day(v["session"]),
                Decimal(v["new_per_old"]),
                v["source_id"],
            )
            for v in document["splits"]
        ),
        tuple(model.ResearchSecurity(**v) for v in document["securities"]),
        tuple(a.day(v) for v in settlement),
        study_id,
        document["origin"],
    )
    assumptions = runtime.ResearchAssumptions(
        scenario["scenario_name"],
        *(
            Decimal(scenario[k])
            for k in (
                "initial_cash",
                "dividend_tax_rate",
                "modeled_spread",
                "slippage_bps",
                "maximum_participation",
            )
        ),
    )
    engine = runtime.retrospective_rotation_engine(bundle, assumptions)
    engine.run()
    p.need(
        engine.research_report.get("status") == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
        and engine.research_report.get("engine_invoked") is True
        and all(engine.research_report.get(flag) is False for flag in m.FLAGS)
        and engine.research_report.get("performance_admission") is False
        and engine.research_report.get("full_real_scenarios_accepted") == 0,
        "RESEARCH_RESULT_BOUNDARY_INVALID",
    )
    report = {
        "schema": "rotation_research_execution_report_v1",
        "collector_source": source,
        "strategy_source": STRATEGY_SHA,
        "plan_sha256": PLAN_HASH,
        "input_sha256": m.sha256(normalized),
        "engine": engine.research_report,
        "trace": engine.research_trace,
        "component_sha256": {c["ticker"]: m.sha256(m.canonical(c)) for c in components},
    }
    # Keep all strategy values and detailed observations private. No return metric
    # is published or promoted to strict performance admission.
    report_body = json.dumps(report, sort_keys=True, separators=(",", ":"), default=str).encode()
    envelope = m.canonical(
        {
            "schema": "rotation_research_retained_result_v1",
            "normalized_inputs": document,
            "report": b.decode(report_body),
        }
    )
    name, prefix = "research-data/rotation-execution-v1-" + source, REPORT_PREFIX + source + "/"
    if archive.ref(name) is None:
        p.need(not readonly, "RESEARCH_EXECUTION_REPORT_MISSING")
        write_record(
            archive,
            name,
            prefix,
            envelope,
            {
                "schema": "rotation_execution_receipt_v1",
                "source_commit": source,
                "strategy_source": STRATEGY_SHA,
                "plan_sha256": PLAN_HASH,
            },
            m.BOOTSTRAP,
        )
    saved, metadata, parent, _ = read_record(archive, name, prefix)
    p.need(
        saved == envelope
        and parent == m.BOOTSTRAP
        and metadata.get("source_commit") == source
        and metadata.get("strategy_source") == STRATEGY_SHA
        and metadata.get("plan_sha256") == PLAN_HASH,
        "RESEARCH_EXECUTION_READBACK_MISMATCH",
    )
    return {
        "schema": "rotation_research_public_summary_v1",
        "status": engine.research_report["status"],
        "strategy_source": STRATEGY_SHA,
        "plan_sha256": PLAN_HASH,
        "report_sha256": m.sha256(envelope),
        "targets": len(p.TICKERS),
        "engine_invoked": True,
        "independent_ledger_pass": True,
        "new_market_requests": 0,
        "new_issuer_workbook_requests": 0,
        "PIT_verified": False,
        "performance_admission": False,
        "full_real_scenarios_accepted": 0,
    }


def guarded():
    source = u.guarded()
    p.need(STRATEGY_SHA != "0" * 40, "STRATEGY_SOURCE_NOT_QUALIFIED")
    head = subprocess.run(
        ["git", "-C", str(ROOT / "research-source"), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    p.need(head == STRATEGY_SHA, "STRATEGY_SOURCE_MISMATCH")
    subprocess.run(
        ["git", "-C", str(ROOT / "research-source"), "diff", "--exit-code", "HEAD", "--"],
        capture_output=True,
        check=True,
    )
    return source


def main():
    try:
        if sys.argv[1:] == ["matrix"]:
            print("tickers=" + json.dumps(list(p.TICKERS)))
            return 0
        p.need(
            len(sys.argv) in {2, 3}
            and sys.argv[1] in {"prepare", "verify-input", "replay", "verify-replay"},
            "INVALID_COMMAND",
        )
        source = guarded()
        readonly = sys.argv[1].startswith("verify-")
        archive = u.CachedArchive(
            m.GitHub(
                m.DESTINATION, os.environ.get("ARCHIVE_WRITE_TOKEN", ""), writable=not readonly
            )
        )
        if sys.argv[1] in {"prepare", "verify-input"}:
            p.need(len(sys.argv) == 3, "INVALID_COMMAND")
            summary = prepare(
                archive,
                sys.argv[2],
                source,
                lambda: os.environ.get("MASSIVE_API_KEY", ""),
                readonly=readonly,
            )
        else:
            p.need(len(sys.argv) == 2, "INVALID_COMMAND")
            code = m.GitHub(m.CODE, os.environ["GITHUB_TOKEN"]).request(
                "GET", "/git/commits/" + source
            )
            p.need(code is not None, "CODE_COMMIT_UNAVAILABLE")
            summary = replay(archive, source, code["committer"]["date"], readonly=readonly)
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "ROTATION_RESEARCH_STOPPED",
                    "code": r.code(error),
                    "PIT_verified": False,
                    "performance_admission": False,
                    "full_real_scenarios_accepted": 0,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
