"""Build a source-bound ETF bundle and replay it only after all input gates pass."""

from __future__ import annotations

import gzip
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

# The runner invokes this file normally (not -I); only the named sibling is loaded.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rotation_research_capture as c  # noqa: E402

from ml4t.backtest.strategies.sector_rotation.research import (  # noqa: E402
    ResearchAssumptions,
    retrospective_rotation_engine,
)
from ml4t.backtest.strategies.sector_rotation.research_inputs import (  # noqa: E402
    RawClose,
    ResearchBar,
    ResearchDistribution,
    ResearchInputs,
    ResearchSecurity,
    ResearchSource,
    ResearchSplit,
    raw_total_returns,
)
from quant_constraints.adapter import ConstrainedBroker  # noqa: E402
from quant_constraints.calendar import NY  # noqa: E402

REPORT_PREFIX = "research/verification/rotation_research_2025_v1/"
REPORT_REF = "research-data/rotation-2025-v1-engine-report"
ASSUMPTIONS = ResearchAssumptions(
    "owner CN-mainland 10-percent ordinary-US-fund research scenario",
    Decimal(100000),
    Decimal("0.10"),
    Decimal("0.0001"),
    Decimal(2),
    Decimal("0.01"),
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def source(identifier, body, receipt, url):
    # Issuer documents predate the market capture protocol and use their own
    # receipt schema. Never substitute the current replay time for either one.
    if identifier in {doc["id"] for doc in c.p.DOCUMENTS[:2]}:
        c.p.need(
            receipt.get("schema") == "real_etf_issuer_receipt_v1", "ISSUER_RECEIPT_SCHEMA_INVALID"
        )
        stamp = datetime.fromisoformat(receipt["fetched_at"])
    else:
        stamp = datetime.fromisoformat(receipt["response_received_at"])
    return ResearchSource(identifier, c.m.sha256(body), stamp, url)


def safe_failure_location(error):
    """Report repository code positions, never exception messages or local values."""
    root = Path(__file__).resolve().parents[1]
    result = {}
    trace = error.__traceback__
    while trace is not None:
        path = Path(trace.tb_frame.f_code.co_filename)
        if path.is_absolute() and path.is_relative_to(root):
            relative = path.relative_to(root)
            if relative.parts[0] in {"tools", "src", "tests"} and path.suffix == ".py":
                result = {"file": relative.as_posix(), "line": trace.tb_lineno}
        trace = trace.tb_next
    kinds = (KeyError, TypeError, ValueError, AssertionError, IndexError, AttributeError)
    return {
        "error_type": next((k.__name__ for k in kinds if isinstance(error, k)), "Exception"),
        "location": result,
    }


def bars(product, body):
    value = c.decode_market(body)
    c.p.need(
        value.get("ticker") == product["ticker"] and value.get("adjusted") is False,
        "RAW_BAR_IDENTITY_INVALID",
    )
    rows = value.get("results", [])
    c.p.need(rows and value.get("resultsCount", len(rows)) == len(rows), "BAR_COUNT_INVALID")
    previous = -1
    result = []
    for row in rows:
        stamp = row.get("t")
        c.p.need(type(stamp) is int and stamp > previous, "BAR_ORDER_INVALID")
        previous = stamp
        at = datetime.fromtimestamp(stamp / 1000, UTC).astimezone(NY)
        c.p.need(product["start"] <= str(at.date()) <= product["end"], "BAR_OUTSIDE_REQUEST")
        values = {k: c.p.number(row.get(k), positive=True) for k in ("o", "h", "l", "c")}
        volume = c.p.number(row.get("v"))
        c.p.need(
            values["l"]
            <= min(values["o"], values["c"])
            <= max(values["o"], values["c"])
            <= values["h"],
            "BAR_OHLC_INVALID",
        )
        if product["kind"] == "daily":
            c.p.need(at.time() == time(), "DAILY_BAR_CLOCK_INVALID")
            result.append((at.date(), values, volume))
        else:
            c.p.need(not at.second and not at.microsecond, "MINUTE_BAR_CLOCK_INVALID")
            end = at + timedelta(minutes=1)
            session = c.CAL.session(at.date())
            if session is None:
                continue
            required = {session.market_open + timedelta(minutes=m) for m in (1, *range(5, 16))} | {
                session.market_close
            }
            if end in required:
                result.append((end, values, volume))
    return result


def actions(product, body):
    rows = c.decode_market(body).get("results", [])
    result, keys = [], set()
    for row in rows:
        c.p.need(row.get("ticker") == product["ticker"], "ACTION_TICKER_CONFLICT")
        kind = product["kind"]
        day = c.a.day(row.get("ex_dividend_date" if kind == "dividends" else "execution_date"))
        identifier = row.get("id") or "source-row:" + c.m.sha256(canonical(row))
        c.p.need(
            type(identifier) is str
            and bool(identifier)
            and identifier not in keys
            and product["start"] <= str(day) <= product["end"],
            "ACTION_ID_OR_DATE_INVALID",
        )
        keys.add(identifier)
        if kind == "splits":
            result.append(
                {
                    "date": day,
                    "id": identifier,
                    "ratio": c.p.number(row.get("split_to"), positive=True)
                    / c.p.number(row.get("split_from"), positive=True),
                }
            )
        else:
            c.p.need(row.get("currency") == "USD", "DIVIDEND_CURRENCY_INVALID")
            result.append(
                {
                    "ex": day,
                    "record": c.a.day(row.get("record_date")),
                    "pay": c.a.day(row.get("pay_date")),
                    "id": identifier,
                    "amount": c.p.number(row.get("cash_amount"), positive=True),
                }
            )
    return result


def classify(ticker, event, docs):
    history = c.p.history_match(c.p.workbook(docs[c.p.DOCUMENTS[0]["id"]]["body"]), ticker, [event])
    tax = c.a.tax_fields(c.p.workbook(docs[c.p.DOCUMENTS[1]["id"]]["body"]), ticker, event, history)
    fields = {k: Decimal(v) for k, v in tax["explicit_numeric_fields"].items()}
    allowed_flags = {"D": (None, ""), "E": (None, "", "R"), "F": (None, "", "C")}
    c.p.need(
        all(tax["source_status_flags_preserved"][k] in allowed_flags[k] for k in allowed_flags),
        "ISSUER_REPORT_STATUS_UNSUPPORTED",
    )
    # Match the retained issuer protocol's existing Decimal threshold. XLSX
    # numeric literals can differ below that threshold; never round or replace
    # the original event amount used for the modeled cash accrual.
    tolerance = Decimal(c.a.POLICY["issuer_amount_tolerance_usd"])
    c.p.need(
        all(abs(fields[k] - event["amount"]) <= tolerance for k in ("J", "M", "N", "Q")),
        "ORDINARY_INCOME_AMOUNT_CONFLICT",
    )
    c.p.need(
        all(fields[k] == 0 for k in ("O", "P", "V", "Z", "AB", "AC", "AD")),
        "NONORDINARY_DISTRIBUTION_UNSUPPORTED",
    )
    return history["rows"][0]["issuer_cells"]["C"]


def settlement_sessions():
    # Separate fixed reference for this bounded scenario, never a production helper.
    # Sep 1 is Labor Day; NSCC a9637 explicitly excludes Oct 13 settlement.
    holidays = {date(2025, 9, 1), date(2025, 10, 13)}
    day, result = c.FIRST, []
    while day <= date(2025, 11, 10):
        if day.weekday() < 5 and day not in holidays:
            result.append(day)
        day += timedelta(days=1)
    return tuple(result)


def build_inputs(archive_factory, source_commit, docs):
    sources, bodies, closes, minutes, distributions, splits, securities = {}, {}, [], [], [], [], []

    def bind(identifier, item, product):
        body, receipt = item["body"], item["receipt"]
        sources[identifier] = source(identifier, body, receipt, product["url"])
        bodies[identifier] = body
        return identifier

    for doc in c.p.DOCUMENTS[:2]:
        bind(doc["id"], docs[doc["id"]], doc)
    references, made = c.capture(
        archive_factory(), "REFERENCE", source_commit, lambda: "", verify_only=True
    )
    c.p.need(made == 0, "REPLAY_OFFICIAL_REQUEST_FORBIDDEN")
    for product in c.products("REFERENCE"):
        bind(product["id"], references[product["id"]], product)
    for ticker in c.p.TICKERS:
        archive = archive_factory()
        held, made = c.r.r.capture(
            archive,
            ticker,
            source_commit,
            lambda: c.p.need(False, "MARKET_CREDENTIAL_FORBIDDEN"),
            verify_only=True,
        )
        c.p.need(made == 0, "REPLAY_MARKET_REQUEST_FORBIDDEN")
        added, made = c.capture(
            archive,
            ticker,
            source_commit,
            lambda: c.p.need(False, "MARKET_CREDENTIAL_FORBIDDEN"),
            verify_only=True,
        )
        c.p.need(made == 0, "REPLAY_MARKET_REQUEST_FORBIDDEN")
        data = []
        # Derived adjusted views remain in the old archive but are not consumed.
        for kind in ("raw", "dividends", "splits"):
            item = held[kind]
            product = dict(item["receipt"]["product"])
            if kind == "raw":
                product.update(kind="daily", start=c.a.START, end=c.a.END)
            else:
                product.update(start=c.a.START, end=c.a.SPLIT_END if kind == "splits" else c.a.END)
            data.append(("retained-" + ticker.lower() + "-" + kind, item, product))
        data += [(product["id"], added[product["id"]], product) for product in c.products(ticker)]
        cusip = None
        seen_events = set()
        for identifier, item, product in data:
            sid = bind(identifier, item, product)
            kind = product["kind"]
            if kind in {"daily", "minute"}:
                for stamp, values, volume in bars(product, item["body"]):
                    if kind == "daily":
                        closes.append(RawClose(ticker, stamp, values["c"], sid))
                    else:
                        minutes.append(
                            ResearchBar(
                                ticker,
                                stamp,
                                values["o"],
                                values["h"],
                                values["l"],
                                values["c"],
                                volume,
                                sid,
                            )
                        )
            else:
                for event in actions(product, item["body"]):
                    day = event["ex"] if kind == "dividends" else event["date"]
                    if not c.START <= day <= c.LAST:
                        continue
                    key = ticker, event["id"]
                    c.p.need(key not in seen_events, "ACTION_COVERAGE_OVERLAP")
                    seen_events.add(key)
                    if kind == "splits":
                        splits.append(ResearchSplit(ticker, event["id"], day, event["ratio"], sid))
                    else:
                        execution = day >= c.FIRST
                        if execution:
                            matched = classify(ticker, event, docs)
                            c.p.need(cusip in (None, matched), "ETF_IDENTITY_CHANGED")
                            cusip = matched
                        distributions.append(
                            ResearchDistribution(
                                ticker,
                                event["id"],
                                day,
                                event["record"],
                                event["pay"],
                                event["amount"],
                                "ordinary_fund" if execution else "unclassified_signal_only",
                                sid,
                                c.p.DOCUMENTS[1]["id"] if execution else None,
                            )
                        )
        c.p.need(cusip is not None, "EXECUTION_DISTRIBUTION_IDENTITY_MISSING")
        assert isinstance(cusip, str)
        securities.append(
            ResearchSecurity(
                ticker, "CUSIP:" + cusip, "US", "US", "ETF", False, c.p.DOCUMENTS[1]["id"]
            )
        )
    inputs = ResearchInputs(
        tuple(sources[k] for k in sorted(sources)),
        tuple(sorted(closes, key=lambda x: (x.session, x.asset))),
        tuple(sorted(minutes, key=lambda x: (x.end, x.asset))),
        tuple(distributions),
        tuple(splits),
        tuple(securities),
        settlement_sessions(),
        "calendar-0",
    )
    body = canonical({"schema": "rotation_retrospective_inputs_v1", **asdict(inputs)})
    return ResearchInputs.from_bundle(body, c.m.sha256(body), bodies), body


def replay(inputs, progress=None):
    engine = retrospective_rotation_engine(inputs, ASSUMPTIONS)
    if progress is not None:
        progress["engine_invoked"] = True
    engine.run()
    assert isinstance(engine.broker, ConstrainedBroker)
    daily = [
        state
        for state in engine.research_trace
        if state["timestamp"]
        == c.CAL.session(state["timestamp"].astimezone(NY).date()).market_close
    ]
    peak, drawdown = float(ASSUMPTIONS.initial_cash), 0.0
    for state in daily:
        peak = max(peak, state["NAV"])
        drawdown = min(drawdown, state["NAV"] / peak - 1)
    records = engine.broker.corporate_action_evidence()["records"]
    reference = dict(raw_total_returns(inputs))
    summary = {
        "status": engine.research_report["status"],
        "execution_start": str(c.FIRST),
        "execution_end": str(c.LAST),
        "initial_model_cash_usd": str(ASSUMPTIONS.initial_cash),
        "calendar_mode": c.POLICY["calendar_mode"],
        "return_pct": (daily[-1]["NAV"] / float(ASSUMPTIONS.initial_cash) - 1) * 100,
        "spy_gross_total_return_pct": float(
            (reference[c.LAST]["SPY"] / reference[c.FIRST]["SPY"] - 1) * 100
        ),
        "benchmark_convention": "SPY_gross_ex_close_reinvestment_no_cost_no_tax",
        "maximum_daily_drawdown_pct": drawdown * 100,
        "fills": len(engine.broker.fills),
        "commission_usd": sum(f.commission for f in engine.broker.fills),
        "dividend_accruals": sum(r["status"] == "dividend_receivable" for r in records),
        "modeled_credits": sum(r["status"] == "cash_credited" for r in records),
        "PIT_verified": False,
        "actual_broker_execution_verified": False,
        "performance_admission": False,
        "full_real_scenarios_accepted": 0,
    }
    return {
        "schema": "rotation_research_result_v1",
        "summary": summary,
        "engine": engine.research_report,
        "trace": engine.research_trace,
        "fills": [asdict(f) for f in engine.broker.fills],
        "corporate_actions": records,
    }


def execute(
    archive_factory, source_commit, *, verify_only=False, load_documents=None, progress=None
):
    archive = archive_factory()
    archive.visibility(True)
    docs = (load_documents or c.a.issuer_documents)(archive)
    inputs, normalized = build_inputs(archive_factory, source_commit, docs)
    result = replay(inputs, progress)
    payload = {
        REPORT_PREFIX + "inputs.json.gz": gzip.compress(normalized, mtime=0),
        REPORT_PREFIX + "result.json.gz": gzip.compress(canonical(result), mtime=0),
    }
    payload[REPORT_PREFIX + "manifest.json"] = canonical(
        {
            "schema": "rotation_research_report_manifest_v1",
            "source_commit": source_commit,
            "plan_sha256": c.PLAN_HASH,
            "files": {name: c.m.sha256(body) for name, body in payload.items()},
        }
    )
    if archive.ref(REPORT_REF) is None:
        c.p.need(not verify_only, "RESEARCH_REPORT_MISSING")
        c.b.create(archive, REPORT_REF, payload, c.m.BOOTSTRAP)
    saved, parent = c.b.read(archive, REPORT_REF, set(payload))
    c.p.need(parent == c.m.BOOTSTRAP and saved == payload, "INDEPENDENT_REPLAY_REPORT_CONFLICT")
    return result["summary"] | {
        "report_bundle_sha256": c.m.sha256(
            canonical({k: c.m.sha256(v) for k, v in sorted(payload.items())})
        ),
        "new_vendor_requests": 0,
    }


def main():
    progress = {"engine_invoked": False}
    try:
        c.p.need(len(sys.argv) == 3 and sys.argv[1] in {"run", "verify"}, "ARGUMENTS_INVALID")
        mode, revision = sys.argv[1:]
        c.guarded(revision)

        def factory():
            return c.r.CachedArchive(
                c.PacedGitHub(
                    c.m.DESTINATION,
                    os.environ.get("ARCHIVE_WRITE_TOKEN", ""),
                    writable=mode == "run",
                )
            )

        print(
            json.dumps(execute(factory, revision, verify_only=mode == "verify", progress=progress))
        )
        return 0
    except Exception as error:
        reason = {
            "Raw daily/minute close mismatch; reconcile source basis": "RAW_DAILY_MINUTE_CLOSE_CONFLICT",
            "Missing raw-close session": "RAW_DAILY_SESSION_GAP",
            "Missing execution-window minute or daily NAV close": "REQUIRED_MINUTE_MISSING",
            "Missing execution session": "EXECUTION_SESSION_GAP",
            "Insufficient raw-close warm-up for the configured strategy": "SIGNAL_WARMUP_INCOMPLETE",
        }.get(str(error), c.r.r.code(error))
        print(
            json.dumps(
                {
                    "status": "RESEARCH_REPLAY_BLOCKED",
                    "reason": reason,
                    "engine_invoked": progress["engine_invoked"],
                    "performance_admission": False,
                    **safe_failure_location(error),
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
