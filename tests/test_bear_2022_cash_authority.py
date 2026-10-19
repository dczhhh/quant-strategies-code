"""Invented secondary cash conflicts cannot alter issuer-verified economic inputs."""

from decimal import Decimal

import pytest

from tests.test_bear_2022_inputs import build
from tests.test_bear_2022_inputs import invented_sources as source_fixture
from tools import bear_2022_inputs as x


@pytest.fixture(name="source_inputs")
def shared_sources(monkeypatch):
    return source_fixture.__wrapped__(monkeypatch)


def change_yahoo_cash(sources, amount):
    _, retained, _, _ = sources
    held = retained["SPY"]["yahoo_daily"]
    body = x.s.decode(held["body"])
    events = body["chart"]["result"][0]["events"]["dividends"]
    for event in events.values():
        event["amount"] = amount
    held["body"] = x.s.canonical(body)


@pytest.mark.parametrize("amount", ["0.2499", "0.249", "0.1"])
def test_secondary_cash_conflict_is_retained_and_cannot_change_economics(source_inputs, amount):
    before, _, _ = build(source_inputs)
    change_yahoo_cash(source_inputs, amount)
    after, normalized, provenance = build(source_inputs)
    assert before.distributions == after.distributions
    assert before.closes == after.closes and before.bars == after.bars
    audit = next(row for row in provenance["event_checks"] if row["ticker"] == "SPY")
    diagnostic = audit["yahoo_cash_comparison"]
    assert diagnostic["role"] == "non_authoritative_diagnostic_only"
    assert diagnostic["used_for_signal_or_cash"] is False
    assert diagnostic["comparison_threshold_usd"] == "0.0002"
    assert all(row["issuer_matched_original_cash"] == 1 for row in diagnostic["rows"])
    assert all(
        row["yahoo_derived_original_cash"] == Decimal(amount) * 4 for row in diagnostic["rows"]
    )
    assert diagnostic["rows_above_original_threshold"] == (
        2 if abs(Decimal(amount) * 4 - 1) > Decimal("0.0002") else 0
    )
    assert normalized == build(source_inputs)[1]


@pytest.mark.parametrize("field,value", [("cash_amount", "1.0000001"), ("pay_date", "2022-06-10")])
def test_secondary_conflict_never_excuses_native_cash_or_date_conflict(source_inputs, field, value):
    change_yahoo_cash(source_inputs, "0.1")
    _, _, added, _ = source_inputs
    held = added["SPY"]["dividends"]
    body = x.c.decode_market(held["body"])
    body["results"][-1][field] = value
    held["body"] = x.u.canonical(body)
    with pytest.raises(ValueError, match="INPUT_EVENT_FIELDS_CONFLICT"):
        build(source_inputs)


def test_secondary_cash_diagnostic_cannot_excuse_missing_dividend(source_inputs):
    change_yahoo_cash(source_inputs, "0.1")
    _, retained, _, _ = source_inputs
    held = retained["SPY"]["yahoo_daily"]
    body = x.s.decode(held["body"])
    body["chart"]["result"][0]["events"]["dividends"].popitem()
    held["body"] = x.s.canonical(body)
    with pytest.raises(ValueError, match="INPUT_EVENT_COVERAGE_CONFLICT"):
        build(source_inputs)
