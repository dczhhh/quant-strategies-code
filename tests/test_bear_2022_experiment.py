"""Invented 2022 events, Engine accounting and independent negative controls only."""

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from ml4t.backtest.strategies.sector_rotation.research import (
    retrospective_rotation_engine,
)
from ml4t.backtest.strategies.sector_rotation.research_inputs import ResearchSource
from quant_constraints.calendar import settlement_date
from tests.strategies.test_rotation_research import ASSUMPTIONS, CAL, SOURCE, inputs, settings
from tests.test_private_archive_migration import FakeGitHub
from tools import bear_2022_experiment as e
from tools.rotation_frequency_oracle import FrequencyOracle
from tools.rotation_frequency_schedule import frequency_engine


def assumed_inputs():
    old = inputs(bear=True)
    days = sorted({r.session for r in old.closes})
    mapped = {day: CAL.shift(date(2022, 8, 1), i) for i, day in enumerate(days)}

    def clock(stamp):
        original, current = CAL.session(stamp.date()), CAL.session(mapped[stamp.date()])
        return current.market_open + (stamp - original.market_open)

    source = ResearchSource(
        "assumption:2022", SOURCE.sha256, SOURCE.observed_at,
        "https://example.invalid/explicit-invented-2022-assumption",
    )
    return replace(
        old, sources=(*old.sources, source),
        closes=tuple(replace(r, session=mapped[r.session]) for r in old.closes),
        bars=tuple(replace(b, end=clock(b.end)) for b in old.bars),
        distributions=tuple(replace(
            event, ex_date=mapped[event.ex_date], record_date=mapped[event.record_date],
            pay_date=mapped[event.pay_date], classification="assumed_ordinary_fund",
            classification_source_id=source.identifier,
        ) for event in old.distributions),
        splits=tuple(replace(event, session=mapped[event.session]) for event in old.splits),
        settlement_sessions=e.x.calendar.sessions((e.x.ROOT / e.x.CALENDAR_PATH).read_bytes()),
    )


@pytest.mark.parametrize("step", ["1", "0.1"])
@pytest.mark.parametrize("case", e.w.CASES)
def test_all_ten_cells_use_real_engine_and_independent_oracles_with_invented_2022(step, case):
    bundle, assumption = assumed_inputs(), e.assumptions(step)
    engine = frequency_engine(bundle, assumption, settings(), case)
    oracle = FrequencyOracle(engine, case)
    engine.run()
    assert oracle.finish()["status"] == "CONDITIONAL_DECISION_AND_SUBMITTED_INTENT_ORACLE_PASS"
    assert engine.research_report["independent_ledger"]["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    assert engine.broker.fills
    assert engine.research_report["PIT_verified"] is False
    assert engine.research_report["performance_admission"] is False


def test_whole_share_research_also_requires_explicit_2022_enablement():
    bundle = assumed_inputs()
    with pytest.raises(ValueError, match="explicit year-bound"):
        retrospective_rotation_engine(bundle, ASSUMPTIONS, settings())
    engine = retrospective_rotation_engine(
        bundle, replace(ASSUMPTIONS, assumed_ordinary_years=(2022,)), settings(),
    )
    engine.run()
    assert engine.research_report["independent_ledger"]["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"


@pytest.mark.parametrize("years", [(), (2024,)])
def test_missing_or_wrong_year_scenario_cannot_admit_assumed_2022(years):
    with pytest.raises(ValueError, match="explicit year-bound"):
        frequency_engine(assumed_inputs(), replace(e.assumptions("0.1"), assumed_ordinary_years=years), settings(), "M")


def test_assumption_source_cannot_impersonate_2024_or_a_real_issuer():
    bundle = assumed_inputs()
    for identifier in ("fixture", "assumption:2024"):
        extra = ResearchSource(identifier, SOURCE.sha256, SOURCE.observed_at, "https://example.invalid/wrong-year")
        sources = bundle.sources if identifier == "fixture" else (*bundle.sources, extra)
        changed = replace(bundle, sources=sources, distributions=tuple(
            replace(event, classification_source_id=identifier) for event in bundle.distributions
        ))
        with pytest.raises(ValueError, match="explicit year-bound"):
            frequency_engine(changed, e.assumptions("0.1"), settings(), "M")


@pytest.mark.parametrize("years", [(2021,), (2022, 2024), (2022, 2022), (True,), (2022, True)])
def test_2022_opt_in_does_not_enable_arbitrary_multi_year_or_boolean_scenarios(years):
    with pytest.raises(ValueError, match="2024 ordinary"):
        replace(e.assumptions("1"), assumed_ordinary_years=years)


def test_fixed_contract_keeps_cash_dates_costs_and_denied_sec_route_outside_assumption():
    value, body = e.x.contract()
    assert value["share_steps"] == ["1", "0.1"]
    assert value["cases"] == list(e.w.CASES)
    assert value["dividend_tax_rate"] == "0.10"
    assert value["ex_record_pay_dates_and_original_cash_are_assumed"] is False
    assert value["SEC_403_retry_authorized"] is False
    assert e.assumptions("0.1").assumed_ordinary_years == (2022,)
    assert e.assumptions("0.1").initial_cash == 100000
    assert e.assumptions("0.1").slippage_bps == 2
    assert e.assumptions("0.1").maximum_participation == Decimal("0.01")
    assert len(e.s.digest(body)) == 64


def test_all_251_dates_use_independently_transcribed_historical_T2_not_T1():
    dates = e.x.calendar.sessions((e.x.ROOT / e.x.CALENDAR_PATH).read_bytes())
    days = e.c.sessions(e.s.FIRST, e.s.LAST)
    assert len(days) == 251
    for day in days:
        assert settlement_date(day) == [d for d in dates if d > day][1]
    assert settlement_date(date(2022, 10, 7)) == date(2022, 10, 12)
    assert settlement_date(date(2022, 12, 30)) == date(2023, 1, 4)


def test_quarters_include_first_day_and_compound_to_full_window_return():
    rows = [
        {"date": date(2022, month, 3), "NAV": nav, "exposure": 0.5}
        for month, nav in ((1, 90), (4, 99), (7, 89.1), (10, 98.01))
    ]
    result = e.quarters(rows, 100)
    assert result[0]["return_pct"] == pytest.approx(-10)
    total = 1.0
    for quarter in result:
        total *= 1 + quarter["return_pct"] / 100
    assert total == pytest.approx(0.9801)


def test_2022_report_does_not_leak_old_2025_rebound_labels():
    metrics, _ = e.run_cell(assumed_inputs(), e.assumptions("0.1"), "M", settings())
    assert "phases" not in metrics
    assert not any(key.startswith("rebound_") for key in metrics)
    assert all(row["quarter"].startswith("2022Q") for row in metrics["quarters"])


def test_report_fresh_bytes_are_exact_and_cannot_overwrite_existing_results():
    payload = e.pack({name: b'{"invented":true}' for name in e.LOGICAL}, "b" * 40)
    archive = FakeGitHub(e.c.m.DESTINATION)
    first = e.retain(archive, payload, verify_only=False)
    before = dict(archive.refs)
    archive.events.clear()
    assert e.retain(archive, payload, verify_only=True) == first
    assert archive.refs == before and all(method == "GET" for method, _ in archive.events)
    path = next(key for key in payload if key.endswith(".bin"))
    payload[path] += b"changed"
    with pytest.raises(ValueError, match="FRESH_REPORT_BYTES"):
        e.retain(archive, payload, verify_only=False)
    assert archive.refs == before


def test_failure_never_prints_provider_or_private_values(monkeypatch, capsys):
    monkeypatch.setattr(e.sys, "argv", ["replay", "run", "b" * 40])
    monkeypatch.setattr(e, "guarded", lambda _: None)
    monkeypatch.setenv("ARCHIVE_WRITE_TOKEN", "INVENTED_KEY")

    def fail(*_args):
        raise KeyError("PRIVATE_VALUES_MUST_NOT_LEAK")

    monkeypatch.setattr(e, "build", fail)
    assert e.main() == 1
    report = capsys.readouterr().out
    assert "PRIVATE_VALUES_MUST_NOT_LEAK" not in report
    assert json.loads(report)["performance_admission"] is False
