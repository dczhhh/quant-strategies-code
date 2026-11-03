"""Invented quarterly events, full warm-up and completed-minute stress inputs."""

import copy
from datetime import UTC, date, datetime

import pytest

from tests.test_real_etf_2025 import workbook
from tests.test_rotation_research_operator import all_documents, item, market
from tools import rotation_research_price_audit as pa
from tools import rotation_stress_inputs as x

c, g, u = x.c, x.g, x.u
EVENT_DAYS = (
    date(2023, 12, 15),
    date(2024, 3, 15),
    date(2024, 6, 21),
    date(2024, 9, 20),
    date(2024, 12, 20),
    date(2025, 3, 21),
    date(2025, 6, 20),
)


def event(ticker, day):
    return {
        "ticker": ticker,
        "id": "invented-" + ticker + str(day),
        "ex_dividend_date": str(day),
        "record_date": str(day),
        "pay_date": str(c.CAL.shift(day, 5)),
        "currency": "USD",
        "cash_amount": 1,
        "split_adjusted_cash_amount": 999,
    }


def invented_market(product):
    value = market(product)
    if product["kind"] == "dividends":
        value["results"] = [
            event(product["ticker"], day)
            for day in EVENT_DAYS
            if product["start"] <= str(day) <= product["end"]
        ]
        value["resultsCount"] = len(value["results"])
    return c.m.canonical(value)


@pytest.fixture
def stress_sources(monkeypatch):
    docs = all_documents()
    hist_id, tax_id = (d["id"] for d in c.p.DOCUMENTS[:2])
    history = c.p.workbook(docs[hist_id]["body"])["dividend"]
    tax = c.p.workbook(docs[tax_id]["body"])["2025 ICI Primary"]
    history_rows, tax_rows = [history[0]], tax[:13]
    for ticker in c.p.TICKERS:
        h = next(r for r in history if r.get("B") == ticker)
        t = next(r for r in tax if r.get("C") == ticker)
        for day in EVENT_DAYS:
            row = dict(h, D=str(day), E=str(day), F=str(c.CAL.shift(day, 5)))
            history_rows.append(row)
            if day.year == 2025:
                tax_rows.append(dict(t, G=str(day), H=str(day), I=str(c.CAL.shift(day, 5))))
    docs[hist_id]["body"] = workbook("dividend", history_rows)
    docs[tax_id]["body"] = workbook("2025 ICI Primary", tax_rows)
    old, added = {}, {}
    for ticker in c.p.TICKERS:
        old[ticker] = [
            dict(item(p, invented_market(p)), product=p, commit="d" * 40)
            for p in c.products(ticker)
            if p["start"] <= str(g.s.LAST) and p["kind"] in {"daily", "dividends", "splits"}
        ]
        added[ticker] = {
            p["id"]: dict(item(p, invented_market(p)), commit="e" * 40) for p in g.products(ticker)
        }
    references = {p["id"]: item(p, b"%PDF-invented reference") for p in c.products("REFERENCE")}
    monkeypatch.setattr(g, "check_inventory", lambda _: None)
    monkeypatch.setattr(g.s, "retained_products", lambda _, ticker: old[ticker])
    monkeypatch.setattr(g, "capture", lambda _, ticker, *_args, **_kwargs: (added[ticker], 0))
    monkeypatch.setattr(c, "capture", lambda *_args, **_kwargs: (references, 0))

    class Archive:
        def visibility(self, private):
            assert private is True

    return docs, old, added, Archive


def build(stress_sources):
    docs, _, _, archive = stress_sources
    return x.build_inputs(
        archive,
        "b" * 40,
        pa.ClosePriceAudit(),
        docs=(docs, {"status": "UNKNOWN_REQUEST_OUTCOME_RETAINED"}),
        published_at=datetime(2026, 10, 16, tzinfo=UTC),
    )


def test_all_inputs_have_complete_415_day_warmup_143_session_execution(stress_sources):
    inputs, _, evidence = build(stress_sources)
    assert len(inputs.closes) == 415 * 12
    assert len(inputs.bars) == 143 * 13 * 12
    assert len(inputs.distributions) == 84
    assert sum(e.classification == "assumed_ordinary_fund" for e in inputs.distributions) == 12
    assert sum(e.classification == "ordinary_fund" for e in inputs.distributions) == 24
    assert all(row["issuer_to_vendor_dates_complete"] for row in evidence["event_checks"])
    assert all(e.cash_amount == 1 for e in inputs.distributions)
    assert inputs.bundle_schema.endswith("v3_assumed_distribution")
    assert inputs.origin == "retained_prices_with_assumed_distribution"


def test_missing_vendor_event_cannot_be_hidden_by_request_date_coverage(stress_sources):
    _, old, _, _ = stress_sources
    item = next(v for v in old["SPY"] if v["product"]["kind"] == "dividends")
    value = c.decode_market(item["body"])
    value["results"].pop()
    value["resultsCount"] -= 1
    item["body"] = u.canonical(value)
    with pytest.raises(ValueError, match="EVENT_COVERAGE_CONFLICT"):
        build(stress_sources)


def test_missing_issuer_event_does_not_disappear_from_the_comparison(stress_sources):
    docs, _, _, _ = stress_sources
    doc = c.p.DOCUMENTS[0]["id"]
    history = c.p.workbook(docs[doc]["body"])["dividend"]
    history = [r for r in history if not (r.get("B") == "SPY" and r.get("D") == "2024-12-20")]
    docs[doc]["body"] = workbook("dividend", history)
    with pytest.raises(ValueError, match="EVENT_COVERAGE_CONFLICT"):
        build(stress_sources)


def test_2025_nonordinary_field_is_not_assumed_away(stress_sources):
    docs, _, _, _ = stress_sources
    doc = c.p.DOCUMENTS[1]["id"]
    tax = c.p.workbook(docs[doc]["body"])["2025 ICI Primary"]
    next(r for r in tax if r.get("C") == "SPY")["V"] = "0.01"
    docs[doc]["body"] = workbook("2025 ICI Primary", tax)
    with pytest.raises(ValueError):
        build(stress_sources)


def test_missing_minute_is_not_replaced_by_daily_price(stress_sources):
    _, _, added, _ = stress_sources
    p = next(p for p in g.products("SPY") if p["kind"] == "minute")
    value = c.decode_market(added["SPY"][p["id"]]["body"])
    value["results"].pop(0)
    value["resultsCount"] -= 1
    added["SPY"][p["id"]]["body"] = u.canonical(value)
    with pytest.raises(
        ValueError, match="Missing execution-window|Execution timestamps require all ETF bars"
    ):
        build(stress_sources)


def test_settlement_calendar_does_not_confuse_exchange_and_bank_closures():
    dates = x.settlement_sessions()
    assert date(2025, 1, 9) in dates and c.CAL.session(date(2025, 1, 9)) is None
    assert date(2025, 4, 18) in dates and c.CAL.session(date(2025, 4, 18)) is None
    assert date(2025, 6, 19) not in dates
    assert date(2025, 7, 1) in dates


def test_event_coverage_rejects_duplicate_vendor_ex_date(stress_sources):
    docs, _, _, _ = stress_sources
    history = c.p.workbook(docs[c.p.DOCUMENTS[0]["id"]]["body"])
    events = [
        {"id": str(i), "ex": day, "record": day, "pay": c.CAL.shift(day, 5), "amount": 1}
        for i, day in enumerate(EVENT_DAYS)
    ]
    events.append(copy.deepcopy(events[0]))
    with pytest.raises(ValueError, match="VENDOR_EVENT_AMBIGUOUS"):
        x.check_event_completeness("SPY", events, history)
