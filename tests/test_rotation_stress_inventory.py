"""Invented bytes only: fixed stress scope, retained-source readback and no acquisition."""

import copy
import gzip
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from tests.test_private_archive_migration import FakeGitHub
from tests.test_real_etf_2025 import workbook
from tests.test_rotation_research_operator import SOURCE, market
from tests.test_rotation_research_operator import normalized_sources as _source_fixture
from tools import private_archive_migration as migration
from tools import rotation_stress_inventory as s

normalized_sources = _source_fixture
c = s.c


def invented_inventory(normalized_sources):
    docs, _, added = normalized_sources
    for item in docs.values():
        item["commit"] = "f" * 40
    captured = {
        ticker: [
            dict(added[ticker][p["id"]], product=p, commit="e" * 40)
            for p in c.products(ticker)
            if p["start"] <= str(s.LAST) and p["kind"] in {"daily", "splits", "dividends"}
        ]
        for ticker in c.p.TICKERS
    }
    unknown = {"status": "UNKNOWN_REQUEST_OUTCOME_RETAINED", "reservation_commit": "a" * 40}
    return docs, captured, unknown


def test_frozen_contract_does_not_change_strategy_or_claim_execution():
    assert str(s.FIRST) == "2024-12-02" and str(s.LAST) == "2025-06-30"
    assert s.CONTRACT["strategy"]["lookbacks"] == (63, 126, 252)
    assert s.CONTRACT["strategy"]["momentum_weights"] == (0.5, 0.3, 0.2)
    assert s.CONTRACT["strategy"]["normal_exposure"] == 0.95
    assert s.CONTRACT["assumptions"]["initial_cash"] == 100000
    assert s.CONTRACT["share_quantum"] == "1"
    assert s.CONTRACT["minimum_presample_close_points"] == 253
    assert s.CONTRACT["performance_admission"] is s.CONTRACT["engine_invoked"] is False
    assert s.CONTRACT["research_scenario_may_be_assumed_without_separate_contract"] is False


def test_exchange_dates_include_half_days_dst_and_carter_funeral():
    days = c.sessions(s.FIRST, s.LAST)
    assert len(days) == 143 and date(2025, 1, 9) not in days
    assert date(2025, 4, 18) not in days and date(2025, 6, 19) not in days
    assert len(c.sessions(s.START, s.LAST)) == 415
    assert len(c.sessions(s.START, date(2024, 11, 29))) == 272
    assert c.CAL.shift(s.FIRST, -253) == date(2023, 11, 29)
    for day, close_hour in ((date(2024, 12, 24), 13), (date(2025, 3, 10), 16)):
        required = s.required_minutes(day)
        assert len(required) == 13 and required[-1].astimezone(s.u.NY).hour == close_hour
        assert required[-2] < required[-1]
        p = s.product("SPY", "minute", day, day)
        endpoints = p["url"].split("?")[0].split("/")[-2:]
        assert int(endpoints[1]) == int(required[-1].timestamp() * 1000) - 1


def test_missing_plan_reuses_existing_raw_requests_only(normalized_sources):
    docs, captured, unknown = invented_inventory(normalized_sources)
    result = s.analyze(captured, docs, unknown)
    summary = result["summary"]
    assert summary["proposed_missing_input_requests"] == {
        "daily": 12,
        "dividends": 12,
        "splits": 12,
        "minute": 576,
    }
    assert summary["proposed_maximum_new_vendor_requests"] == 612
    assert summary["required_completed_minutes_per_asset"] == 1859
    assert summary["new_vendor_requests"] == summary["new_official_requests"] == 0
    assert summary["acquisition_executed"] is summary["engine_invoked"] is False
    for p in result["proposed_products_not_authorized_by_this_inventory"]:
        if p["kind"] == "daily":
            assert p["start"] == "2023-11-01" and p["end"] == "2024-08-23"
        elif p["kind"] in {"splits", "dividends"}:
            assert p["end"] == "2024-08-25"
        else:
            assert len(c.sessions(c.a.day(p["start"]), c.a.day(p["end"]))) <= 3
    assert result["classification_gaps"]["2024"]["event_year_document_present"] is False
    assert result["classification_gaps"]["2025"]["event_year_document_present"] is True
    assert "issuer_cells" not in json.dumps(summary)
    assert "body_sha256" not in json.dumps(summary)


def test_absent_requested_row_is_anomaly_not_permission_to_request_again(normalized_sources):
    docs, captured, unknown = invented_inventory(normalized_sources)
    item = captured["SPY"][0]
    value = json.loads(item["body"])
    value["results"].pop(0)
    value["resultsCount"] -= 1
    item["body"] = c.m.canonical(value)
    report = s.analyze(captured, docs, unknown)
    row = next(r for r in report["cases"] if r["ticker"] == "SPY")
    assert row["requested_but_absent_daily_sessions"] == [c.START]
    assert report["summary"]["proposed_maximum_new_vendor_requests"] == 612
    assert s.spans(
        {date(2025, 1, 2), date(2025, 1, 6)}, c.sessions(date(2025, 1, 2), date(2025, 1, 6))
    ) == [(date(2025, 1, 2), date(2025, 1, 2)), (date(2025, 1, 6), date(2025, 1, 6))]


def test_2024_cash_cannot_borrow_2025_classification(normalized_sources):
    docs, captured, unknown = invented_inventory(normalized_sources)
    item = next(v for v in captured["SPY"] if v["product"]["kind"] == "dividends")
    value = json.loads(item["body"])
    value["results"] = [
        {
            "ticker": "SPY",
            "id": "invented-2024-event",
            "currency": "USD",
            "cash_amount": 123.456,
            "ex_dividend_date": "2024-12-20",
            "record_date": "2024-12-20",
            "pay_date": "2025-01-31",
        }
    ]
    item["body"] = c.m.canonical(value)
    result = s.analyze(captured, docs, unknown)
    assert (
        result["cases"][-1]["execution_distribution_checks"][0]["classification_status"]
        == "EVENT_YEAR_FINAL_CLASSIFICATION_MISSING"
    )
    assert "123.456" not in json.dumps(result["summary"])
    assert result["summary"]["final_classification_2024_verified"] is False


def test_each_2025_event_requires_exact_same_year_issuer_fields(normalized_sources):
    docs, captured, unknown = invented_inventory(normalized_sources)
    for doc in c.p.DOCUMENTS[:2]:
        sheet = "dividend" if doc == c.p.DOCUMENTS[0] else "2025 ICI Primary"
        rows = c.p.workbook(docs[doc["id"]]["body"])[sheet]
        for row in rows:
            if row.get("B" if sheet == "dividend" else "C") == "SPY":
                row.update(
                    dict.fromkeys(("D", "E") if sheet == "dividend" else ("G", "H"), "2025-03-21")
                )
                row["F" if sheet == "dividend" else "I"] = "2025-04-30"
        docs[doc["id"]]["body"] = workbook(sheet, rows)
    item = next(v for v in captured["SPY"] if v["product"]["kind"] == "dividends")
    value = json.loads(item["body"])
    value["results"] = [
        {
            "ticker": "SPY",
            "id": "invented-2025-event",
            "currency": "USD",
            "cash_amount": 1,
            "ex_dividend_date": "2025-03-21",
            "record_date": "2025-03-21",
            "pay_date": "2025-04-30",
        }
    ]
    item["body"] = c.m.canonical(value)
    report = s.analyze(captured, docs, unknown)
    assert (
        report["cases"][-1]["execution_distribution_checks"][0]["classification_status"]
        == "ORDINARY_2025_RETROSPECTIVE_RESEARCH_FIELDS_MATCH"
    )
    assert report["summary"]["PIT_verified"] is False


def test_unknown_document_marker_is_read_only_and_not_retried(normalized_sources, monkeypatch):
    docs, _, _ = invented_inventory(normalized_sources)
    archive = FakeGitHub(c.m.DESTINATION)
    doc = c.p.DOCUMENTS[2]
    ref = c.p.document_ref(doc, True)
    path = c.p.DOCUMENT_PREFIX + doc["id"] + "/reservation.json"
    revision = c.b.create(archive, ref, {path: c.p.reservation(doc, SOURCE)}, c.m.BOOTSTRAP)
    monkeypatch.setattr(c.a, "issuer_documents", lambda _: docs)
    before = dict(archive.refs)
    archive.events.clear()
    _, old = s.documents(c.r.CachedArchive(archive))
    assert old == {"status": "UNKNOWN_REQUEST_OUTCOME_RETAINED", "reservation_commit": revision}
    assert archive.refs == before and all(method == "GET" for method, _ in archive.events)
    archive.refs[c.p.document_ref(doc)] = "f" * 40
    with pytest.raises(ValueError, match="STATE_CHANGED"):
        s.documents(archive)


def test_selective_read_verifies_group_but_does_not_read_later_minute_bodies():
    archive = FakeGitHub(c.m.DESTINATION)
    c.capture(
        archive,
        "SPY",
        SOURCE,
        lambda: "INVENTED_KEY",
        fetch=lambda p, _: (200, c.m.canonical(market(p))),
    )
    before = copy.deepcopy(archive.refs)
    archive.events.clear()
    result = s.retained_products(c.r.CachedArchive(archive), "SPY")
    assert {r["product"]["kind"] for r in result} == {"daily", "dividends", "splits"}
    assert archive.refs == before and all(method == "GET" for method, _ in archive.events)
    assert sum("/git/blobs/" in path for _, path in archive.events) <= 5


def test_tampered_retained_body_fails_before_report_creation():
    archive = FakeGitHub(c.m.DESTINATION)
    c.capture(
        archive,
        "SPY",
        SOURCE,
        lambda: "INVENTED_KEY",
        fetch=lambda p, _: (200, c.m.canonical(market(p))),
    )
    group = archive.commits[archive.ref(c.group_ref("SPY", 0))]
    tree = archive.trees[group["tree"]["sha"]]
    entry = next(e for e in tree["tree"] if "daily-2024" in e["path"])
    archive.blobs[entry["sha"]] += b" "
    with pytest.raises((c.m.Stopped, migration.Stopped)):
        s.retained_products(c.r.CachedArchive(archive), "SPY")
    assert archive.ref(s.REPORT_REF) is None


def test_two_fresh_inventories_have_identical_hashes_without_mutating_source_refs(
    normalized_sources, monkeypatch
):
    docs, captured, unknown = invented_inventory(normalized_sources)
    archive = FakeGitHub(c.m.DESTINATION)
    archive.refs["research-reservations/old-unknown"] = "a" * 40
    monkeypatch.setattr(s, "documents", lambda _: (docs, unknown))
    monkeypatch.setattr(s, "retained_products", lambda _, t: captured[t])
    first = s.execute(archive, SOURCE)
    refs = dict(archive.refs)
    archive.events.clear()
    second = s.execute(c.r.CachedArchive(archive), SOURCE, verify_only=True)
    assert first == second and refs == archive.refs
    assert all(method == "GET" for method, _ in archive.events)
    saved, _ = c.b.read(
        archive, s.REPORT_REF, {s.PREFIX + "inventory.json.gz", s.PREFIX + "manifest.json"}
    )
    report = json.loads(gzip.decompress(saved[s.PREFIX + "inventory.json.gz"]))
    assert report["summary"]["status"] == "G1_RETAINED_SOURCE_INVENTORY_COMPLETE_GAPS_OPEN"
    assert all(len(body) <= c.m.FILE_LIMIT for body in saved.values())
    with pytest.raises(ValueError, match="CONFLICT"):
        s.execute(archive, "c" * 40, verify_only=True)


def test_failures_do_not_print_private_values(monkeypatch, capsys):
    monkeypatch.setattr(s.sys, "argv", ["inventory", "run", SOURCE])
    monkeypatch.setattr(c, "guarded", lambda _: None)
    monkeypatch.setattr(c, "PacedGitHub", lambda *_args, **_kwargs: FakeGitHub(c.m.DESTINATION))

    def fail(*_args, **_kwargs):
        raise ValueError("invented private credential / raw body")

    monkeypatch.setattr(s, "execute", fail)
    assert s.main() == 1
    output = capsys.readouterr().out
    assert "invented private" not in output
    assert json.loads(output)["engine_invoked"] is False


def test_fixed_workflow_has_no_provider_secret_and_never_calls_engine():
    doc = yaml.safe_load(Path("docs/examples/rotation-stress-inventory.yml").read_text())
    assert doc.get("on", doc.get(True)) == {
        "push": {"branches": ["main"], "paths": [c.OPERATION_WORKFLOW]}
    }
    assert doc["env"]["SOURCE_SHA"] == "__REVIEWED_SOURCE_SHA__"
    assert "MASSIVE" not in json.dumps(doc)
    assert "ARCHIVE_WRITE_TOKEN" not in json.dumps(doc["jobs"]["preflight"])
    assert doc["jobs"]["verify"]["needs"] == "inventory"
    for job in doc["jobs"].values():
        assert job["steps"][0]["with"] == {
            "ref": "${{ env.SOURCE_SHA }}",
            "persist-credentials": False,
        }
    assert "rotation_stress_inventory.py verify" in json.dumps(doc["jobs"]["verify"])


@pytest.mark.parametrize(
    "ticker,kind,first,last",
    [
        ("GME", "daily", s.START, s.LAST),
        ("SPY", "daily", s.START - timedelta(days=1), s.LAST),
        ("SPY", "reference", s.START, s.LAST),
    ],
)
def test_missing_plan_cannot_expand_universe_dates_or_product_kind(ticker, kind, first, last):
    with pytest.raises(ValueError):
        s.product(ticker, kind, first, last)
