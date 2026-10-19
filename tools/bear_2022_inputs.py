"""Source-bound assumed-ordinary 2022 inputs; retained bytes only, no provider calls."""

from __future__ import annotations

import base64
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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
from quant_constraints.calendar import settlement_date  # noqa: E402
from tools import bear_2022_calendar as calendar  # noqa: E402
from tools import bear_2022_market as m  # noqa: E402
from tools import bear_2022_recovery as r  # noqa: E402
from tools import rotation_research_replay as u  # noqa: E402

a, s, c = m.a, m.s, m.c
ROOT = Path(__file__).resolve().parents[1]
ASSUMPTION_PATH = "config/research/bear_2022_assumed_ordinary.json"
CALENDAR_PATH = "config/research/bear_2022_settlement_reference.json"


def contract():
    body = (ROOT / ASSUMPTION_PATH).read_bytes()
    value = s.decode(body)
    s.need(
        value["schema"] == "bear_2022_assumed_ordinary_v1"
        and value["event_year"] == 2022
        and value["classification"] == "assumed_ordinary_fund"
        and value["classification_source_id"] == "assumption:2022"
        and value["dividend_tax_rate"] == "0.10"
        and value["strict_real_admission"] is False
        and value["ex_record_pay_dates_and_original_cash_are_assumed"] is False
        and value["SEC_403_retry_authorized"] is False,
        "ASSUMPTION_CONTRACT",
    )
    return value, body


def daily_items(archive, ticker):
    """Re-open authenticated raw records to retain original observation clocks."""
    bodies = a.read_raw(archive, ticker)
    a.audit(archive, ticker, bodies, m.SOURCE_READER, verify_only=True)
    revision = archive.ref(a.ref(ticker, "raw"))
    commit = archive.request("GET", "/git/commits/" + revision)
    tree = archive.request("GET", "/git/trees/" + commit["tree"]["sha"] + "?recursive=1")
    files = {
        row["path"]: archive.blob(row["sha"], row["size"])
        for row in tree["tree"]
        if row["type"] != "tree"
    }
    prefix = a.PREFIX + ticker.lower() + "/"
    manifest = c.b.decode(files[prefix + "manifest.json"])
    packed = b"".join(files[prefix + f"part-{i:02}.bin"] for i in range(manifest["part_count"]))
    rows = c.b.decode(a.g.bounded_gunzip(packed, 4 * (c.m.FILE_LIMIT * 2 + 8192)))["rows"]
    result = {}
    for row in rows:
        receipt = row["receipt"]
        product = receipt["product"]
        body = base64.b64decode(row["body_base64"], validate=True)
        s.need(body == bodies[product["id"]], "INPUT_RAW_RECEIPT_BINDING")
        result[product["id"]] = {"body": body, "receipt": receipt, "product": product}
    return result


def check_events(ticker, events, history, yahoo_dividends):
    """Tax nature is assumed; dates, original cash and ETF identity are not."""
    rows = history.get("dividend", [])
    s.need(bool(rows) and rows[0] == c.p.HISTORY_HEADERS, "INPUT_ISSUER_HISTORY_SCHEMA")
    dates = [
        c.p.issuer_day(row.get("D"))
        for row in rows[1:]
        if row.get("B") == ticker and s.WARMUP <= c.p.issuer_day(row.get("D")) <= s.LAST
    ]
    actual = [event["ex"] for event in events]
    s.need(
        len(actual) == len(set(actual))
        and len(dates) == len(set(dates))
        and set(actual) == set(dates) == set(yahoo_dividends),
        "INPUT_EVENT_COVERAGE_CONFLICT",
    )
    s.need(
        all(
            abs(event["amount"] - yahoo_dividends[event["ex"]]) <= Decimal("0.0002")
            for event in events
        ),
        "INPUT_YAHOO_DIVIDEND_UNIT_CONFLICT",
    )
    evidence = c.p.history_match(history, ticker, events)
    s.need(evidence["status"] == "ISSUER_HISTORY_FIELD_MATCH", "INPUT_EVENT_FIELDS_CONFLICT")
    cusips = {row["issuer_cells"]["C"] for row in evidence["rows"]}
    s.need(len(cusips) == 1, "INPUT_ETF_IDENTITY_CHANGED")
    return next(iter(cusips)), evidence


def build_inputs(factory, source, *, docs=None, published_at=None):
    archive = factory()
    archive.visibility(True)
    docs = c.a.issuer_documents(archive) if docs is None else docs
    history_doc = c.p.DOCUMENTS[0]
    history = c.p.workbook(docs[history_doc["id"]]["body"])
    policy, policy_body = contract()
    if published_at is None:
        published_at = datetime.fromisoformat(
            subprocess.check_output(
                ["git", "show", "-s", "--format=%cI", source], text=True
            ).strip()
        )
    sources, bodies = {}, {}
    closes, bars, distributions, splits, securities, checks = [], [], [], [], [], []

    def bind(identifier, item, product):
        sources[identifier] = u.source(identifier, item["body"], item["receipt"], product["url"])
        bodies[identifier] = item["body"]
        return identifier

    def local(identifier, body, path):
        sources[identifier] = ResearchSource(
            identifier,
            s.digest(body),
            published_at,
            f"https://github.com/{c.m.CODE}/blob/{source}/{path}",
        )
        bodies[identifier] = body
        return identifier

    bind(history_doc["id"], docs[history_doc["id"]], history_doc)
    sid = local("assumption:2022", policy_body, ASSUMPTION_PATH)
    calendar_body = (ROOT / CALENDAR_PATH).read_bytes()
    calendar_id = local("reference:2022-settlement", calendar_body, CALENDAR_PATH)
    settlement = calendar.sessions(calendar_body)
    for day in c.sessions(s.FIRST, s.LAST):
        expected = [d for d in settlement if d > day][1]
        s.need(settlement_date(day) == expected, "INPUT_HISTORICAL_SETTLEMENT_CONFLICT")
    for ticker in s.TICKERS:
        archive = factory()
        retained = daily_items(archive, ticker)
        raw_bodies = {key: item["body"] for key, item in retained.items()}
        qualification = s.qualify(ticker, raw_bodies, c.sessions)
        s.need(
            qualification["summary"]["overlap_differences_above_original_threshold"] == 0,
            "INPUT_DAILY_OVERLAP_CONFLICT",
        )
        raw_ids = {
            key: bind(ticker + ":source:" + key, item, item["product"])
            for key, item in retained.items()
        }
        audit_body = s.canonical({"source_commit": m.SOURCE_READER, "audit": qualification})
        audit_ref = archive.ref(a.ref(ticker, "audit"))
        audit_id = ticker + ":derived-original-closes"
        sources[audit_id] = ResearchSource(
            audit_id,
            s.digest(audit_body),
            max(sources[identifier].observed_at for identifier in raw_ids.values()),
            f"https://github.com/{c.m.DESTINATION}/blob/{audit_ref}/"
            + a.AUDIT_PREFIX
            + ticker.lower()
            + "/source-audit.json",
        )
        bodies[audit_id] = audit_body
        derivation = qualification["derivation_not_source_replacement"]
        native = s.massive(raw_bodies["massive_daily"], ticker, "daily")
        for row in derivation["closes"]:
            day = row["session"]
            original = native[day] if day >= s.OVERLAP else row["close"]
            identifier = raw_ids["massive_daily"] if day >= s.OVERLAP else audit_id
            closes.append(RawClose(ticker, day, original, identifier))
        added, made = r.capture(
            archive,
            ticker,
            "7609dfd2afc68f4c84b547a9da83ac90359ceea3",
            lambda: s.need(False, "INPUT_NEW_MARKET_REQUEST_FORBIDDEN"),
            verify_only=True,
        )
        s.need(made == 0, "INPUT_NEW_MARKET_REQUEST_FORBIDDEN")
        events, event_sources, effective_splits = [], {}, {}
        for key, item in added.items():
            product = item["product"]
            identifier = bind(ticker + ":market:" + key, item, product)
            if product["kind"] == "minute":
                for stamp, values, volume in u.bars(product, item["body"]):
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
                    if product["kind"] == "splits":
                        effective_splits[event["date"]] = event["ratio"]
                        splits.append(
                            ResearchSplit(
                                ticker,
                                event["id"],
                                event["date"],
                                event["ratio"],
                                identifier,
                            )
                        )
                    else:
                        s.need(event["id"] not in event_sources, "INPUT_ACTION_DUPLICATE")
                        events.append(event)
                        event_sources[event["id"]] = identifier
        native_splits = s.massive(raw_bodies["massive_splits"], ticker, "splits")
        for day, ratio in native_splits.items():
            if day <= s.LAST:
                effective_splits[day] = ratio
                splits.append(
                    ResearchSplit(
                        ticker,
                        "retained-split:" + str(day),
                        day,
                        ratio,
                        raw_ids["massive_splits"],
                    )
                )
        s.need(
            effective_splits
            == {
                row["session"]: row["new_per_old"]
                for row in derivation["splits"]
                if row["session"] <= s.LAST
            },
            "INPUT_SPLIT_COVERAGE_CONFLICT",
        )
        yahoo_cash = {row["ex_date"]: row["original_share_cash"] for row in derivation["dividends"]}
        cusip, evidence = check_events(ticker, events, history, yahoo_cash)
        for event in sorted(events, key=lambda e: (e["ex"], e["id"])):
            execution = event["ex"] >= s.FIRST
            distributions.append(
                ResearchDistribution(
                    ticker,
                    event["id"],
                    event["ex"],
                    event["record"],
                    event["pay"],
                    event["amount"],
                    "assumed_ordinary_fund" if execution else "unclassified_signal_only",
                    event_sources[event["id"]],
                    sid if execution else None,
                )
            )
        securities.append(
            ResearchSecurity(
                ticker,
                "CUSIP:" + cusip,
                "US",
                "US",
                "ETF",
                False,
                history_doc["id"],
            )
        )
        checks.append(
            {
                "ticker": ticker,
                "events": len(events),
                "execution_events_assumed_ordinary": sum(e["ex"] >= s.FIRST for e in events),
                "issuer_field_match_count": len(evidence["rows"]),
                "three_source_ex_date_coverage_exact": True,
                "issuer_original_cash_tolerance_usd": "1E-8",
                "native_daily_closes": len(native),
                "derived_pre_overlap_closes": sum(
                    row["session"] < s.OVERLAP for row in derivation["closes"]
                ),
            }
        )
    inputs = ResearchInputs(
        tuple(sources[k] for k in sorted(sources)),
        tuple(sorted(closes, key=lambda r: (r.session, r.asset))),
        tuple(sorted(bars, key=lambda r: (r.end, r.asset))),
        tuple(distributions),
        tuple(splits),
        tuple(securities),
        settlement,
        calendar_id,
        origin="retained_prices_with_assumed_distribution",
        close_policy=SEPARATE_CLOSES,
    )
    normalized = u.canonical({"schema": inputs.bundle_schema, **asdict(inputs)})
    verified = ResearchInputs.from_bundle(normalized, s.digest(normalized), bodies)
    s.need(
        len(verified.closes) == 545 * 12 and len(verified.bars) == 251 * 13 * 12,
        "INPUT_FIXED_WINDOW_COVERAGE",
    )
    return (
        verified,
        normalized,
        {
            "assumption": policy,
            "assumption_sha256": s.digest(policy_body),
            "event_checks": checks,
            "historical_T_plus_2_all_251_dates_checked": True,
            "new_provider_requests": 0,
            "PIT_verified": False,
        },
    )
