"""Entirely invented raw data and issuer workbook; no provider or account access."""

import copy
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests import test_bear_2022_sources as fixtures
from tests.test_private_archive_migration import FakeGitHub
from tests.test_real_etf_2025 import workbook
from tests.test_rotation_research_operator import all_documents, item, market
from tools import bear_2022_inputs as x


@pytest.fixture
def invented_sources(monkeypatch):
    monkeypatch.setattr(fixtures, "sessions", x.c.sessions)
    docs = all_documents()
    history_id = x.c.p.DOCUMENTS[0]["id"]
    original = x.c.p.workbook(docs[history_id]["body"])["dividend"]
    history = [original[0]]
    retained, added = {}, {}
    days = (date(2021, 6, 1), date(2022, 6, 1))
    for ticker in x.s.TICKERS:
        value = fixtures.fixture(ticker)
        yahoo = value["yahoo_daily"]["chart"]["result"][0]
        yahoo["events"]["dividends"][str(fixtures.stamp(days[1]))] = {
            "date": fixtures.stamp(days[1]), "amount": "0.25",
        }
        raw = fixtures.encoded(value)
        retained[ticker] = {
            p["id"]: dict(item(p, raw[p["id"]]), product=p) for p in x.s.products(ticker)
        }
        selected = []
        template = next(r for r in original[1:] if r.get("B") == ticker)
        for day in days:
            pay = x.c.CAL.shift(day, 5)
            selected.append({
                "ticker": ticker, "id": "invented:" + str(day),
                "ex_dividend_date": str(day), "record_date": str(day), "pay_date": str(pay),
                "currency": "USD", "cash_amount": 1, "split_adjusted_cash_amount": 999,
            })
            history.append(dict(template, D=str(day), E=str(day), F=str(pay), G=1))
        added[ticker] = {}
        for p in x.m.products(ticker):
            body = market(p)
            if p["kind"] == "dividends":
                body["results"] = selected
                body["resultsCount"] = len(selected)
            added[ticker][p["id"]] = dict(item(p, x.u.canonical(body)), product=p)
    docs[history_id]["body"] = workbook("dividend", history)
    monkeypatch.setattr(x, "daily_items", lambda _, ticker: retained[ticker])

    def capture(_archive, ticker, _source, key, **kwargs):
        assert kwargs["verify_only"] is True
        with pytest.raises(ValueError, match="NEW_MARKET_REQUEST_FORBIDDEN"):
            key()
        return added[ticker], 0

    monkeypatch.setattr(x.m, "capture", capture)

    class Archive:
        def visibility(self, private):
            assert private is True

        def ref(self, _name):
            return "a" * 40

    return docs, retained, added, Archive


def build(sources):
    docs, _, _, factory = sources
    return x.build_inputs(factory, "b" * 40, docs=docs, published_at=datetime(2026, 10, 18, tzinfo=UTC))


def test_complete_fixed_2022_inputs_preserve_raw_cash_clocks_and_assumption_labels(invented_sources):
    inputs, normalized, provenance = build(invented_sources)
    assert len(inputs.closes) == 545 * 12
    assert len(inputs.bars) == 251 * 13 * 12
    assert sum(e.classification == "assumed_ordinary_fund" for e in inputs.distributions) == 12
    assert sum(e.classification == "unclassified_signal_only" for e in inputs.distributions) == 12
    assert all(e.cash_amount == 1 for e in inputs.distributions)
    assert all(e.classification_source_id == "assumption:2022" for e in inputs.distributions if e.ex_date.year == 2022)
    assert inputs.origin == "retained_prices_with_assumed_distribution"
    assert inputs.bundle_schema.endswith("v3_assumed_distribution")
    assert provenance["new_provider_requests"] == 0
    assert provenance["historical_T_plus_2_all_251_dates_checked"] is True
    assert normalized == build(invented_sources)[1]
    for close in inputs.closes:
        assert close.source_id.endswith(":source:massive_daily") if close.session >= x.s.OVERLAP else close.source_id.endswith(":derived-original-closes")
    source = inputs.source_map["SPY:source:massive_daily"]
    assert source.observed_at == datetime(2026, 10, 15, tzinfo=UTC)


def test_missing_event_cannot_be_excused_by_request_coverage(invented_sources):
    _, _, added, _ = invented_sources
    held = added["SPY"]["dividends"]
    body = x.c.decode_market(held["body"])
    body["results"].pop()
    held["body"] = x.u.canonical(body)
    with pytest.raises(ValueError, match="INPUT_EVENT_COVERAGE_CONFLICT"):
        build(invented_sources)


@pytest.mark.parametrize("field,value", [("cash_amount", "1.0000001"), ("pay_date", "2022-06-10")])
def test_ordinary_assumption_does_not_authorize_cash_or_date_changes(invented_sources, field, value):
    _, _, added, _ = invented_sources
    held = added["SPY"]["dividends"]
    body = x.c.decode_market(held["body"])
    body["results"][-1][field] = value
    held["body"] = x.u.canonical(body)
    with pytest.raises(ValueError, match="INPUT_EVENT_FIELDS_CONFLICT"):
        build(invented_sources)


def test_daily_crossover_is_fixed_and_original_overlap_tolerance_is_not_relaxed(invented_sources):
    _, retained, _, _ = invented_sources
    held = retained["SPY"]["massive_daily"]
    body = x.s.decode(held["body"])
    body["results"][0]["c"] = str(Decimal(body["results"][0]["c"]) + Decimal("0.0003"))
    held["body"] = x.s.canonical(body)
    with pytest.raises(ValueError, match="INPUT_DAILY_OVERLAP_CONFLICT"):
        build(invented_sources)


def test_daily_receipts_are_original_and_fresh_reader_never_requests(monkeypatch):
    monkeypatch.setattr(fixtures, "sessions", x.c.sessions)
    bodies = fixtures.encoded(fixtures.fixture())
    archive = FakeGitHub(x.c.m.DESTINATION)
    x.a.capture(archive, "SPY", "b" * 40, lambda: "INVENTED", get=lambda p, _: (200, bodies[p["id"]]))
    x.a.audit(archive, "SPY", bodies, x.m.SOURCE_READER, verify_only=False)
    before = copy.deepcopy(archive.refs)
    archive.events.clear()
    held = x.daily_items(archive, "SPY")
    assert {key: item["body"] for key, item in held.items()} == bodies
    assert all(item["receipt"]["body_sha256"] == x.s.digest(item["body"]) for item in held.values())
    assert archive.refs == before
    assert all(method == "GET" for method, _ in archive.events)
