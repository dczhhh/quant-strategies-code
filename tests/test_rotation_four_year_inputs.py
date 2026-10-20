"""Invented economic rows only; source mutation and continuous-boundary controls."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.strategies.test_rotation_research import inputs as invented_inputs
from tools import rotation_four_year_inputs as a


def test_namespace_and_overlap_are_deterministic_without_source_id_collision():
    source = invented_inputs()
    rows = a.Rows()
    rows.window("left", source)
    rows.window("right", source)
    assert len(rows.sources) == 2
    assert len(rows.bars) == len(source.bars)
    assert rows.overlaps["bars"] == len(source.bars)
    assert rows.overlaps["closes"] == len(source.closes)
    assert rows.overlaps["distributions"] == len(source.distributions)
    assert all(row.source_id == "left:fixture" for row in rows.bars.values())
    assert all(
        row.classification_source_id == "left:fixture" for row in rows.distributions.values()
    )


@pytest.mark.parametrize("kind", ["closes", "bars"])
def test_price_overlap_cannot_hide_one_different_asset_value(kind):
    source = invented_inputs()
    rows = a.Rows()
    rows.window("left", source)
    row = getattr(source, kind)[0]
    with pytest.raises(ValueError, match="PRICE_OVERLAP_CONFLICT"):
        rows.price(kind, replace(row, close=row.close + Decimal("0.00000001")))


@pytest.mark.parametrize(
    "field,value",
    [
        ("cash_amount", Decimal("1.00000001")),
        ("event_id", "other-native-id"),
        ("pay_date", date(2025, 10, 10)),
    ],
)
def test_dividend_dedup_requires_exact_native_identity_cash_and_dates(field, value):
    rows = a.Rows()
    event = invented_inputs().distributions[0]
    rows.distribution(event)
    with pytest.raises(ValueError, match="DISTRIBUTION_OVERLAP_CONFLICT"):
        rows.distribution(replace(event, **{field: value}))


def test_source_and_security_identity_conflicts_stop_before_output():
    source, rows = invented_inputs(), a.Rows()
    rows.window("left", source)
    with pytest.raises(ValueError, match="SOURCE_ID_COLLISION"):
        rows.source(replace(source.sources[0], identifier="left:fixture", sha256="f" * 64))
    securities = list(source.securities)
    securities[0] = replace(securities[0], security_id="CUSIP:other")
    with pytest.raises(ValueError, match="SECURITY_IDENTITY_CONFLICT"):
        rows.window("right", replace(source, securities=tuple(securities)))


def dividends():
    rows = a.Rows()
    rows.closes["SPY", date(2022, 1, 3)] = a.u.RawClose(
        "SPY", date(2022, 1, 3), Decimal(100), "native"
    )
    history = {"dividend": [a.c.p.HISTORY_HEADERS]}
    for year in range(2022, 2026):
        for ticker_index, ticker in enumerate(a.g.TICKERS):
            for month in (3, 6, 9, 12):
                day = a.c.sessions(date(year, month, 1), date(year, month, 28))[0]
                event = a.u.ResearchDistribution(
                    ticker,
                    f"invented:{year}:{month}",
                    day,
                    day,
                    day + timedelta(days=3),
                    Decimal(1),
                    "assumed_ordinary_fund" if year in (2022, 2024) else "unclassified_signal_only",
                    "native",
                    f"bear2022:assumption:{year}" if year in (2022, 2024) else None,
                )
                rows.distribution(event)
                history["dividend"].append(
                    {
                        "A": "INVENTED ETF",
                        "B": ticker,
                        "C": f"{ticker_index + 1:09}",
                        "D": str(day),
                        "E": str(day),
                        "F": str(event.pay_date),
                        "G": "1",
                        "H": "",
                        "I": "",
                        "J": "Quarterly",
                    }
                )
    return rows, history


def patch_tax(monkeypatch):
    monkeypatch.setattr(a.annual, "fields", lambda year, *_args: {"invented_year": year})
    monkeypatch.setattr(a.a, "ordinary_2025", lambda *_args: {"invented_year": 2025})


def test_all_192_native_cash_events_matched_and_only_2022_remains_assumed(monkeypatch):
    patch_tax(monkeypatch)
    rows, history = dividends()
    checks, counts = a.classify(rows, history, {}, {2023: {}, 2024: {}})
    assert len(checks) == 192 and sum(counts.values()) == 192
    for event in rows.distributions.values():
        year = event.ex_date.year
        assert event.classification == (
            "assumed_ordinary_fund" if year == 2022 else "ordinary_fund"
        )
        assert event.classification_source_id == (
            "bear2022:assumption:2022" if year == 2022 else f"four-year:tax:{year}"
        )


def test_december_spy_payable_in_2026_remains_a_receivable_date(monkeypatch):
    patch_tax(monkeypatch)
    rows, history = dividends()
    key = max(key for key in rows.distributions if key[0] == "SPY")
    prior = rows.distributions[key]
    rows.distributions[key] = replace(prior, pay_date=date(2026, 1, 30))
    for row in history["dividend"][1:]:
        if row["B"] == "SPY" and row["D"] == str(prior.ex_date):
            row["F"] = "2026-01-30"
    a.classify(rows, history, {}, {2023: {}, 2024: {}})
    assert rows.distributions[key].pay_date == date(2026, 1, 30)


@pytest.mark.parametrize("corrupt", ["missing", "cash", "assumption"])
def test_missing_native_event_wrong_cash_or_extended_assumption_fail_closed(monkeypatch, corrupt):
    patch_tax(monkeypatch)
    rows, history = dividends()
    key = next(iter(rows.distributions))
    if corrupt == "missing":
        del rows.distributions[key]
    elif corrupt == "cash":
        rows.distributions[key] = replace(rows.distributions[key], cash_amount=Decimal("1.000001"))
    else:
        rows.distributions[key] = replace(
            rows.distributions[key], classification_source_id="assumption:all-years"
        )
    with pytest.raises(ValueError):
        a.classify(rows, history, {}, {2023: {}, 2024: {}})


def test_old_bear_normalized_mutation_stops_before_other_windows(monkeypatch):
    monkeypatch.setattr(a.i, "load_inputs", lambda *_args: {"invented": True})
    monkeypatch.setattr(a.q.e.x, "build_inputs", lambda *_args: (None, b"changed", None))
    with pytest.raises(ValueError, match="BEAR_INPUT_BYTES"):
        a.old_inputs(lambda: None)


def test_incomplete_short_window_cannot_be_marked_continuous():
    rows = a.Rows()
    rows.window("fixture", invented_inputs())
    with pytest.raises(ValueError, match="WARMUP_OR_CLOSE_COVERAGE"):
        a.finish(rows, (), "fixture:fixture")


@pytest.fixture(scope="module")
def continuous_rows():
    sample, rows = invented_inputs(), a.Rows()
    rows.source(sample.sources[0])
    rows.source(
        replace(
            sample.sources[0],
            identifier="fixture:daily",
            sha256=a.c.m.sha256(b"invented daily prices"),
            url="https://example.invalid/invented-daily",
        )
    )
    rows.securities = {s.asset: s for s in sample.securities}
    value = Decimal(100)
    for day in a.c.sessions(a.q.e.x.s.WARMUP, a.LAST):
        session = a.c.CAL.session(day)
        for asset in a.g.TICKERS:
            rows.price("closes", a.u.RawClose(asset, day, value, "fixture:daily"))
            if day < a.FIRST:
                continue
            for stamp in sorted(
                {session.market_open + timedelta(minutes=m) for m in (1, *range(5, 16))}
                | {session.market_close}
            ):
                rows.price(
                    "bars",
                    a.u.ResearchBar(
                        asset, stamp, value, value, value, value, Decimal(100000), "fixture"
                    ),
                )
    return rows


def test_one_continuous_1003_session_input_preserves_full_cross_year_clock(continuous_rows):
    _, days, _ = a.settlement.reference()
    result = a.finish(continuous_rows, days, "fixture")
    assert len(result.bars) == 1003 * 13 * 12
    assert result.bars[0].end.astimezone(a.u.NY).date() == a.FIRST
    assert result.bars[-1].end.astimezone(a.u.NY).date() == a.LAST
    assert result.settlement_sessions[-1] == date(2026, 2, 6)
    assert result.origin == "retained_prices_with_assumed_distribution"


def test_a_single_asset_session_hole_is_not_hidden_by_global_session_count(continuous_rows):
    rows = deepcopy(continuous_rows)
    key = next(iter(rows.bars))
    del rows.bars[key]
    _, days, _ = a.settlement.reference()
    with pytest.raises(ValueError, match="EXECUTION_BOUNDARIES"):
        a.finish(rows, days, "fixture")


def test_same_bar_count_with_an_incomplete_universe_still_fails(continuous_rows):
    rows = deepcopy(continuous_rows)
    key = next(iter(rows.bars))
    missing = rows.bars.pop(key)
    extra = replace(missing, end=missing.end + timedelta(minutes=1))
    rows.bars[extra.asset, extra.end] = extra
    _, days, _ = a.settlement.reference()
    with pytest.raises(ValueError, match="Execution timestamps require all ETF bars"):
        a.finish(rows, days, "fixture")
