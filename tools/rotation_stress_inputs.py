"""Assemble retained original-share stress sources; no source request function is called."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rotation_stress_capture as g  # noqa: E402

from ml4t.backtest.strategies.sector_rotation.research_inputs import (  # noqa: E402
    SEPARATE_CLOSES,
    RawClose,
    ResearchBar,
    ResearchDistribution,
    ResearchInputs,
    ResearchSecurity,
    ResearchSource,
    ResearchSplit,
)

s, c, u = g.s, g.c, g.u
ASSUMPTION_PATH = "config/research/rotation_stress_assumed_2024.json"


def settlement_sessions():
    # Disclosed bounded bank-holiday scenario from the retained 2024-2028 PDF.
    # Exchange closures and settlement closures are deliberately not conflated.
    holidays = {
        date(2024, 12, 25),
        date(2025, 1, 1),
        date(2025, 1, 20),
        date(2025, 2, 17),
        date(2025, 5, 26),
        date(2025, 6, 19),
        date(2025, 7, 4),
    }
    return tuple(
        day
        for day in s.calendar_dates(s.FIRST, date(2025, 7, 7))
        if day.weekday() < 5 and day not in holidays
    )


def check_event_completeness(ticker, events, history):
    rows = history.get("dividend", [])
    c.p.need(bool(rows) and rows[0] == c.p.HISTORY_HEADERS, "STRESS_ISSUER_HISTORY_SCHEMA_INVALID")
    dates = []
    for row in rows[1:]:
        if row.get("B") != ticker:
            continue
        day = c.p.issuer_day(row.get("D"))
        if s.START <= day <= s.LAST:
            dates.append(day)
    c.p.need(len(dates) == len(set(dates)), "STRESS_ISSUER_EVENT_AMBIGUOUS")
    actual = [event["ex"] for event in events]
    c.p.need(len(actual) == len(set(actual)), "STRESS_VENDOR_EVENT_AMBIGUOUS")
    c.p.need(set(actual) == set(dates), "STRESS_ISSUER_VENDOR_EVENT_COVERAGE_CONFLICT")
    evidence = c.p.history_match(history, ticker, events)
    c.p.need(
        evidence["status"] == "ISSUER_HISTORY_FIELD_MATCH", "STRESS_ISSUER_EVENT_FIELDS_CONFLICT"
    )
    return evidence


def build_inputs(archive_factory, source, audit, *, docs=None, published_at=None):
    archive = archive_factory()
    archive.visibility(True)
    docs, old = s.documents(archive) if docs is None else docs
    g.check_inventory(archive)
    history = c.p.workbook(docs[c.p.DOCUMENTS[0]["id"]]["body"])
    sources, bodies, closes, bars, distributions, splits, securities, event_checks = (
        {},
        {},
        [],
        [],
        [],
        [],
        [],
        [],
    )

    def bind(identifier, item, product):
        sources[identifier] = u.source(identifier, item["body"], item["receipt"], product["url"])
        bodies[identifier] = item["body"]
        return identifier

    for doc in c.p.DOCUMENTS[:2]:
        bind(doc["id"], docs[doc["id"]], doc)
    calendar, made = c.capture(
        archive,
        "REFERENCE",
        source,
        lambda: c.p.need(False, "OFFICIAL_REQUEST_FORBIDDEN"),
        verify_only=True,
    )
    c.p.need(made == 0, "OFFICIAL_REQUEST_FORBIDDEN")
    for product in c.products("REFERENCE"):
        bind(product["id"], calendar[product["id"]], product)
    assumption = Path(__file__).resolve().parents[1] / ASSUMPTION_PATH
    body = assumption.read_bytes()
    contract = json.loads(body)
    c.p.need(
        contract["event_year"] == 2024
        and contract["classification"] == "assumed_ordinary_fund"
        and contract["strict_real_admission"] is False,
        "STRESS_ASSUMPTION_CONTRACT_INVALID",
    )
    if published_at is None:
        published_at = datetime.fromisoformat(
            subprocess.check_output(
                ["git", "show", "-s", "--format=%cI", source], text=True
            ).strip()
        )
    sid = "assumption:2024"
    sources[sid] = ResearchSource(
        sid,
        c.m.sha256(body),
        published_at,
        f"https://github.com/{c.m.CODE}/blob/{source}/{ASSUMPTION_PATH}",
    )
    bodies[sid] = body
    for ticker in c.p.TICKERS:
        archive = archive_factory()
        old_items = s.retained_products(archive, ticker)
        added, made = g.capture(
            archive,
            ticker,
            source,
            lambda: c.p.need(False, "MARKET_CREDENTIAL_FORBIDDEN"),
            verify_only=True,
        )
        c.p.need(made == 0, "STRESS_REPLAY_NEW_REQUEST_FORBIDDEN")
        selected = old_items + [dict(added[p["id"]], product=p) for p in g.products(ticker)]
        events, source_ids = [], {}
        for item in selected:
            product = item["product"]
            identifier = ("retained-" if item in old_items else "stress-") + product["id"]
            bind(identifier, item, product)
            kind = product["kind"]
            if kind == "minute":
                audit.observe(product, sources[identifier], item["body"])
            if kind in {"daily", "minute"}:
                for stamp, values, volume in u.bars(product, item["body"]):
                    if kind == "daily" and s.START <= stamp <= s.LAST:
                        closes.append(RawClose(ticker, stamp, values["c"], identifier))
                    elif kind == "minute":
                        bars.append(
                            ResearchBar(
                                ticker,
                                stamp,
                                values["o"],
                                values["h"],
                                values["l"],
                                values["c"],
                                volume,
                                identifier,
                            )
                        )
            else:
                for event in u.actions(product, item["body"]):
                    day = event["ex"] if kind == "dividends" else event["date"]
                    if not s.START <= day <= s.LAST:
                        continue
                    if kind == "splits":
                        splits.append(
                            ResearchSplit(ticker, event["id"], day, event["ratio"], identifier)
                        )
                    else:
                        c.p.need(event["id"] not in source_ids, "STRESS_ACTION_DUPLICATE")
                        source_ids[event["id"]] = identifier
                        events.append(event)
        match = check_event_completeness(ticker, events, history)
        cusips = {row["issuer_cells"]["C"] for row in match["rows"]}
        c.p.need(len(cusips) == 1, "STRESS_ETF_IDENTITY_CHANGED")
        cusip = next(iter(cusips))
        classified, assumed = 0, 0
        for event in events:
            execution = event["ex"] >= s.FIRST
            classification, classification_source = "unclassified_signal_only", None
            if execution and event["ex"].year == 2024:
                classification, classification_source = "assumed_ordinary_fund", sid
                assumed += 1
            elif execution:
                c.p.need(
                    u.classify(ticker, event, docs) == cusip,
                    "STRESS_CLASSIFICATION_IDENTITY_CONFLICT",
                )
                classification, classification_source = "ordinary_fund", c.p.DOCUMENTS[1]["id"]
                classified += 1
            distributions.append(
                ResearchDistribution(
                    ticker,
                    event["id"],
                    event["ex"],
                    event["record"],
                    event["pay"],
                    event["amount"],
                    classification,
                    source_ids[event["id"]],
                    classification_source,
                )
            )
        securities.append(
            ResearchSecurity(
                ticker, "CUSIP:" + cusip, "US", "US", "ETF", False, c.p.DOCUMENTS[1]["id"]
            )
        )
        event_checks.append(
            {
                "ticker": ticker,
                "retained_events": len(events),
                "issuer_to_vendor_dates_complete": True,
                "vendor_to_issuer_fields_match": True,
                "execution_2025_classification_matches": classified,
                "execution_2024_classification_assumed": assumed,
            }
        )
    inputs = ResearchInputs(
        tuple(sources[k] for k in sorted(sources)),
        tuple(sorted(closes, key=lambda x: (x.session, x.asset))),
        tuple(sorted(bars, key=lambda x: (x.end, x.asset))),
        tuple(distributions),
        tuple(splits),
        tuple(securities),
        settlement_sessions(),
        "calendar-0",
        origin="retained_prices_with_assumed_distribution",
        close_policy=SEPARATE_CLOSES,
    )
    normalized = u.canonical({"schema": inputs.bundle_schema, **asdict(inputs)})
    verified = ResearchInputs.from_bundle(normalized, c.m.sha256(normalized), bodies)
    return (
        verified,
        normalized,
        {
            "event_checks": event_checks,
            "strict_2024_classification_status": "UNVERIFIED_REAL_2024_FINAL_SOURCE_MISSING",
            "old_2024_document": old,
            "assumption": contract,
            "assumption_source_sha256": sources[sid].sha256,
        },
    )
