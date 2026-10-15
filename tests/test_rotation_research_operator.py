"""Invented inputs only: bounded capture, source binding and actual Engine replay."""

import copy
import json
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from tests.test_private_archive_migration import FakeGitHub
from tests.test_real_etf_2025 import documents, workbook
from tools import private_archive_migration as migration
from tools import rotation_research_replay as u

c = u.c
SOURCE = "d" * 40
OBSERVED = "2026-10-15T00:00:00+00:00"


def item(product, body):
    return {"body": body, "receipt": {"product": product, "response_received_at": OBSERVED}}


def all_documents():
    template = documents()
    history = c.p.workbook(template[c.p.DOCUMENTS[0]["id"]]["body"])["dividend"][:1]
    primary = c.p.workbook(template[c.p.DOCUMENTS[1]["id"]]["body"])["2025 ICI Primary"][:13]
    for i, ticker in enumerate(c.p.TICKERS):
        docs = documents(ticker)
        h = c.p.workbook(docs[c.p.DOCUMENTS[0]["id"]]["body"])["dividend"][1]
        tax = c.p.workbook(docs[c.p.DOCUMENTS[1]["id"]]["body"])["2025 ICI Primary"][13]
        h["C"] = tax["B"] = f"{i + 1:09d}"
        history.append(h)
        primary.append(tax)
    return {
        doc["id"]: item(
            doc,
            workbook("dividend" if i == 0 else "2025 ICI Primary", history if i == 0 else primary),
        )
        for i, doc in enumerate(c.p.DOCUMENTS[:2])
    }


def price(ticker, day):
    slope = Decimal(c.p.TICKERS.index(ticker) + 1) / 100
    return float(100 + slope * (day - c.START).days)


def market(product):
    ticker, kind = product["ticker"], product["kind"]
    rows = []
    first, last = (c.a.day(product[k]) for k in ("start", "end"))
    if kind in {"daily", "minute"}:
        for day in c.sessions(first, last):
            at = datetime.combine(day, time(), u.NY)
            ends = (
                [at]
                if kind == "daily"
                else [
                    c.CAL.session(day).market_open + timedelta(minutes=m)
                    for m in (1, *range(5, 16))
                ]
                + [c.CAL.session(day).market_close]
            )
            for end in ends:
                stamp = end if kind == "daily" else end - timedelta(minutes=1)
                value = price(ticker, day)
                rows.append(
                    {
                        "t": int(stamp.timestamp() * 1000),
                        "o": value,
                        "h": value,
                        "l": value,
                        "c": value,
                        "v": 1000000,
                    }
                )
    elif kind == "dividends" and first <= c.a.day("2025-09-19") <= last:
        rows.append(
            {
                "ticker": ticker,
                "id": "invented-dividend-" + ticker,
                "ex_dividend_date": "2025-09-19",
                "record_date": "2025-09-19",
                "pay_date": "2025-10-31",
                "cash_amount": 1,
                "split_adjusted_cash_amount": 999,
                "currency": "USD",
            }
        )
    return {
        "status": "OK",
        "ticker": ticker,
        "adjusted": False,
        "results": rows,
        "resultsCount": len(rows),
    }


@pytest.fixture
def normalized_sources(monkeypatch):
    old, new = {}, {}
    for ticker in c.p.TICKERS:
        old[ticker] = {}
        for kind in ("raw", "dividends", "splits"):
            product = next(p for p in c.a.products(ticker) if c.r.r.kind(p) == kind)
            normalized = dict(
                product,
                kind="daily" if kind == "raw" else kind,
                start=c.a.START,
                end=c.a.SPLIT_END if kind == "splits" else c.a.END,
            )
            old[ticker][kind] = item(product, c.m.canonical(market(normalized)))
        new[ticker] = {p["id"]: item(p, c.m.canonical(market(p))) for p in c.products(ticker)}
    new["REFERENCE"] = {
        p["id"]: item(p, b"%PDF-invented calendar reference") for p in c.products("REFERENCE")
    }
    monkeypatch.setattr(
        c.r.r, "capture", lambda _archive, ticker, *_args, **_kwargs: (old[ticker], 0)
    )
    monkeypatch.setattr(c, "capture", lambda _archive, ticker, *_args, **_kwargs: (new[ticker], 0))
    return all_documents(), old, new


def test_plan_fetches_only_missing_daily_intervals_and_finite_three_session_minutes():
    assert len(c.sessions()) == 48
    assert len(c.sessions(c.START, c.CAL.shift(c.FIRST, -1))) == 253
    assert sum(len(c.products(t)) for t in c.p.TICKERS) == 252
    for ticker in c.p.TICKERS:
        rows = c.products(ticker)
        assert len(rows) == 21
        assert all("adjusted=false" in p["url"] for p in rows if p["kind"] in {"daily", "minute"})
        assert all(
            len(c.sessions(c.a.day(p["start"]), c.a.day(p["end"]))) == 3
            for p in rows
            if p["kind"] == "minute"
        )
    with pytest.raises(ValueError, match="SCOPE"):
        c.products("GME")


def test_capture_is_append_only_and_verify_never_asks_for_market_credentials():
    archive = FakeGitHub(c.m.DESTINATION)
    count = []

    def fetch(product, key):
        count.append(product["id"])
        return 200, c.m.canonical(market(product))

    _, made = c.capture(archive, "SPY", SOURCE, lambda: "KEY", fetch=fetch)
    assert made == len(count) == 21
    saved = copy.deepcopy(archive.refs)
    _, made = c.capture(
        archive,
        "SPY",
        SOURCE,
        lambda: pytest.fail("No secret needed"),
        verify_only=True,
        fetch=lambda *_: pytest.fail("No request allowed"),
    )
    assert made == 0 and archive.refs == saved


def test_full_pool_capture_and_two_independent_replays_fit_archive_call_budget():
    archive = FakeGitHub(c.m.DESTINATION)
    c.capture(
        c.r.CachedArchive(archive),
        "SPY",
        SOURCE,
        lambda: "KEY",
        fetch=lambda product, _: (200, c.m.canonical(market(product))),
    )
    capture_calls = len(archive.events)
    capture_writes = sum(method == "POST" for method, _ in archive.events)
    archive.events.clear()
    c.capture(
        c.r.CachedArchive(archive), "SPY", SOURCE, lambda: pytest.fail("No key"), verify_only=True
    )
    verify_calls = len(archive.events)
    # Four retained legacy products cost at most 48 reads per target; the two
    # interrupted legacy targets add 32. Allow 150 calls for issuer/calendar/
    # result metadata and all remaining one-off private writes and readbacks.
    total = 12 * (capture_calls + 2 * verify_calls) + 2 * (12 * 48 + 32) + 150
    assert total < 5000, (capture_calls, verify_calls, total)
    assert 12 * capture_writes + 60 < 500
    assert len(c.groups("SPY")) == 1


def test_an_unknown_group_outcome_is_not_retried():
    archive = FakeGitHub(c.m.DESTINATION)
    archive.refs[c.group_ref("SPY", 0, True)] = "e" * 40
    with pytest.raises(ValueError, match="UNKNOWN_GROUP_OUTCOME_RETAINED"):
        c.capture(
            archive,
            "SPY",
            SOURCE,
            lambda: pytest.fail("No credential lookup"),
            fetch=lambda *_: pytest.fail("No retry"),
        )


def test_failed_source_response_is_retained_privately_and_not_admitted():
    archive = FakeGitHub(c.m.DESTINATION)
    with pytest.raises(ValueError, match="CAPTURE_RECEIPT_INVALID"):
        c.capture(
            archive,
            "SPY",
            SOURCE,
            lambda: "KEY",
            fetch=lambda *_: (403, b'{"private":"invented denied response"}'),
        )
    assert archive.ref(c.group_ref("SPY", 0)) is not None
    assert any(b"invented denied response" in body for body in archive.blobs.values())


def test_mutated_response_bytes_cannot_be_replayed():
    archive = FakeGitHub(c.m.DESTINATION)
    c.capture(
        archive,
        "SPY",
        SOURCE,
        lambda: "KEY",
        fetch=lambda product, _: (200, c.m.canonical(market(product))),
    )
    target = next(k for k, value in archive.blobs.items() if b'"results"' in value)
    archive.blobs[target] += b" "
    with pytest.raises((c.m.Stopped, migration.Stopped, ValueError)):
        c.read_group(archive, "SPY", 0)


def test_complete_source_bundle_runs_canonical_engine_and_checks_dividend_cash(normalized_sources):
    docs, _, _ = normalized_sources
    inputs, body = u.build_inputs(lambda: None, SOURCE, docs)
    assert inputs.fingerprint
    assert len(inputs.bars) == 48 * 13 * 12
    assert json.loads(body)["origin"] == "retained_market_data"
    result = u.replay(replace(inputs, origin="invented_fixture"))
    assert result["summary"]["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    assert result["summary"]["fills"] > 0
    assert result["summary"]["dividend_accruals"] > 0
    assert result["summary"]["modeled_credits"] == result["summary"]["dividend_accruals"]
    assert result["engine"]["warmup_sessions"] == 253
    assert result["summary"]["performance_admission"] is False


@pytest.mark.parametrize(
    "gap", ["minute", "daily", "daily-close-conflict", "special-tax", "estimated-tax"]
)
def test_incomplete_or_conflicting_sources_reject_before_any_engine_call(
    normalized_sources, gap, monkeypatch
):
    docs, old, new = normalized_sources
    if gap in {"minute", "daily", "daily-close-conflict"}:
        kind = "daily" if gap == "daily" else "minute"
        product = next(p for p in c.products("SPY") if p["kind"] == kind)
        value = json.loads(new["SPY"][product["id"]]["body"])
        if gap == "daily-close-conflict":
            row = value["results"][12]
            row.update(dict.fromkeys(("o", "h", "l", "c"), 1))
        else:
            value["results"].pop(0)
            value["resultsCount"] -= 1
        new["SPY"][product["id"]]["body"] = c.m.canonical(value)
    else:
        name = c.p.DOCUMENTS[1]["id"]
        rows = c.p.workbook(docs[name]["body"])["2025 ICI Primary"]
        rows[13]["V" if gap == "special-tax" else "D"] = "1" if gap == "special-tax" else "E"
        docs[name]["body"] = workbook("2025 ICI Primary", rows)
    monkeypatch.setattr(
        u, "retrospective_rotation_engine", lambda *_: pytest.fail("Engine must not be built")
    )
    with pytest.raises(ValueError):
        u.build_inputs(lambda: None, SOURCE, docs)


def test_calendar_reference_does_not_call_the_production_settlement_function(monkeypatch):
    monkeypatch.setattr(
        "quant_constraints.calendar.settlement_date", lambda *_: pytest.fail("Not independent")
    )
    days = u.settlement_sessions()
    assert c.a.day("2025-10-13") not in days
    assert next(d for d in days if d > c.a.day("2025-10-10")) == c.a.day("2025-10-14")


def test_private_report_readback_and_independent_fresh_engine_run_match(normalized_sources):
    docs, _, _ = normalized_sources
    archive = FakeGitHub(c.m.DESTINATION)
    first = u.execute(lambda: archive, SOURCE, load_documents=lambda _: docs)
    before = dict(archive.refs)
    second = u.execute(lambda: archive, SOURCE, verify_only=True, load_documents=lambda _: docs)
    assert first == second and archive.refs == before
    assert all(len(body) <= c.m.FILE_LIMIT for body in archive.blobs.values())


@pytest.mark.parametrize(
    "field", ["GITHUB_REF", "GITHUB_ACTOR", "RUNNER_ENVIRONMENT", "ACTIONS_STEP_DEBUG"]
)
def test_credentials_are_not_usable_outside_bound_owner_hosted_context(field, monkeypatch):
    env = {
        "GITHUB_REPOSITORY": c.m.CODE,
        "GITHUB_ACTOR": "dczhhh",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/" + c.OPERATION_BRANCH,
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux",
        "GITHUB_WORKFLOW_REF": c.m.CODE + "/" + c.OPERATION_WORKFLOW + "@refs/heads/main",
    }
    with (
        patch.dict("os.environ", env, clear=True),
        patch.object(c.subprocess, "check_output", return_value=SOURCE + "\n"),
    ):
        c.guarded(SOURCE)
        monkeypatch.setenv(field, "true" if field == "ACTIONS_STEP_DEBUG" else "untrusted")
        with pytest.raises(ValueError, match="CONTEXT"):
            c.guarded(SOURCE)


def test_operation_template_limits_credentials_to_the_bound_private_pipeline():
    document = yaml.safe_load(Path("docs/examples/rotation-research-operation.yml").read_text())
    trigger = document.get("on", document.get(True))
    assert trigger == {"push": {"branches": [c.OPERATION_BRANCH], "paths": [c.OPERATION_WORKFLOW]}}
    assert document["permissions"] == {"contents": "read"}
    assert document["env"]["SOURCE_SHA"] == "__REVIEWED_SOURCE_SHA__"
    jobs = document["jobs"]
    assert "ARCHIVE_WRITE_TOKEN" not in json.dumps(jobs["preflight"])
    assert "MASSIVE" not in json.dumps(jobs["capture_reference"])
    assert "MASSIVE" not in json.dumps(jobs["replay"]) + json.dumps(jobs["verify"])
    assert jobs["capture"]["strategy"]["max-parallel"] == 2
    assert jobs["verify"]["needs"] == "replay"
    for job in jobs.values():
        checkout = job["steps"][0]
        assert checkout["with"]["ref"] == "${{ env.SOURCE_SHA }}"
        assert checkout["with"]["persist-credentials"] is False


def test_failure_after_engine_invocation_reports_the_actual_phase(monkeypatch, capsys):
    def failure(*_args, progress, **_kwargs):
        progress["engine_invoked"] = True
        raise ValueError("invented internal failure")

    monkeypatch.setattr(u.sys, "argv", ["replay", "run", SOURCE])
    monkeypatch.setattr(c, "guarded", lambda _: None)
    monkeypatch.setattr(u, "execute", failure)
    assert u.main() == 1
    assert json.loads(capsys.readouterr().out)["engine_invoked"] is True


def test_missing_credential_does_not_leave_a_false_unknown_reservation():
    archive = FakeGitHub(c.m.DESTINATION)
    with pytest.raises(ValueError, match="CREDENTIAL_UNAVAILABLE"):
        c.capture(archive, "SPY", SOURCE, lambda: "")
    assert not archive.refs
