"""Invented source bundles exercise the canonical retrospective Engine and oracle."""

import copy
import json
from dataclasses import asdict, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from ml4t.backtest.strategies.sector_rotation import SectorRotationStrategy, sector_rotation_engine
from ml4t.backtest.strategies.sector_rotation.config import SectorRotationConfig
from ml4t.backtest.strategies.sector_rotation.research import (
    ResearchAssumptions,
    retrospective_rotation_engine,
)
from ml4t.backtest.strategies.sector_rotation.research_inputs import (
    UNIVERSE,
    RawClose,
    ResearchBar,
    ResearchDistribution,
    ResearchInputs,
    ResearchSecurity,
    ResearchSource,
    ResearchSplit,
    RetrospectiveCloseSource,
    digest,
    raw_total_returns,
)
from ml4t.backtest.strategies.sector_rotation.research_ledger import (
    ResearchLedgerMismatch,
    verify_research_ledger,
)
from quant_constraints import resolve_dividend_tax_rate
from quant_constraints.calendar import NY, SessionCalendar
from quant_constraints.corporate_actions import CorporateActionProcessor

CAL = SessionCalendar()
BODY = b"wholly invented source, no account or licensed market records"
SOURCE = ResearchSource(
    "fixture", digest(BODY), datetime(2026, 10, 15, tzinfo=NY), "https://example.invalid/invented"
)
ASSUMPTIONS = ResearchAssumptions(
    "invented CN-resident 10-percent scenario",
    Decimal(10000),
    Decimal("0.10"),
    Decimal("0.0001"),
    Decimal(2),
)


def sessions(start, end):
    day = date.fromisoformat(start)
    result = []
    while day <= date.fromisoformat(end):
        if CAL.session(day):
            result.append(day)
        day += timedelta(days=1)
    return result


def settings():
    return replace(
        SectorRotationConfig(),
        lookbacks=(2, 3, 4),
        short_ma=2,
        long_ma=4,
        peak_lookback=4,
        down_confirm=1,
        recovery_confirm=2,
    )


def inputs(*, bear=False, end="2025-11-05"):
    closes, bars = [], []
    affected = UNIVERSE[-2]
    days = sessions("2025-08-01", end)
    for index, day in enumerate(days):
        for j, asset in enumerate(sorted(UNIVERSE)):
            slope = Decimal(j + 1) / 100
            value = Decimal(100) + slope * index
            if asset == affected and day >= date(2025, 10, 7):
                value -= 1
            if asset == affected and day >= date(2025, 10, 8):
                value /= 2
            if bear and asset == "SPY" and day >= date(2025, 10, 9):
                value = Decimal(75)
            closes.append(RawClose(asset, day, value, "fixture"))
            if day >= date(2025, 9, 30):
                for hour, minute in [(9, 31), *[(9, m) for m in range(35, 46)], (16, 0)]:
                    bars.append(
                        ResearchBar(
                            asset,
                            datetime.combine(day, time(hour, minute), NY),
                            value,
                            value,
                            value,
                            value,
                            Decimal(100000),
                            "fixture",
                        )
                    )
    settlement = tuple(
        d
        for d in sessions("2025-09-29", "2025-11-14")
        if d not in {date(2025, 10, 13), date(2025, 11, 11)}
    )
    return ResearchInputs(
        (SOURCE,),
        tuple(sorted(closes, key=lambda x: (x.session, x.asset))),
        tuple(sorted(bars, key=lambda x: (x.end, x.asset))),
        (
            ResearchDistribution(
                affected,
                "invented-dividend",
                date(2025, 10, 7),
                date(2025, 10, 7),
                date(2025, 10, 9),
                Decimal(1),
                "ordinary_fund",
                "fixture",
                "fixture",
            ),
        ),
        (ResearchSplit(affected, "invented-split", date(2025, 10, 8), Decimal(2), "fixture"),),
        tuple(
            ResearchSecurity(a, "invented:" + a, "US", "US", "ETF", False, "fixture")
            for a in UNIVERSE
        ),
        settlement,
        "fixture",
        "invented_fixture",
    )


def run(**changes):
    bundle = inputs(**changes)
    engine = retrospective_rotation_engine(bundle, ASSUMPTIONS, settings())
    engine.run()
    return engine


def test_raw_share_returns_match_independent_hand_reinvestment_through_split_and_dividend():
    original = inputs(end="2025-10-09")
    selected = sessions("2025-10-06", "2025-10-09")
    asset = UNIVERSE[0]
    closes = tuple(
        RawClose(a, d, Decimal(p), "fixture")
        for d, p in zip(selected, (100, 102, 50, 51))
        for a in sorted(UNIVERSE)
    )
    bundle = replace(
        original,
        closes=closes,
        bars=tuple(b for b in original.bars if b.end.date() in selected),
        distributions=(
            ResearchDistribution(
                asset,
                "hand-dividend",
                selected[2],
                selected[2],
                selected[3],
                Decimal("0.5"),
                "ordinary_fund",
                "fixture",
                "fixture",
            ),
        ),
        splits=(ResearchSplit(asset, "hand-split", selected[2], Decimal(2), "fixture"),),
    )
    actual = raw_total_returns(bundle)
    # One original share becomes two; $1 dividend buys .02 new shares at $50.
    assert actual[-1][1][asset] == Decimal("103.02") / 100
    assert actual[-1][1]["SPY"] == Decimal(51) / 100


def test_real_default_and_synthetic_modes_both_refuse_research_inputs():
    strategy = SectorRotationStrategy(RetrospectiveCloseSource(inputs()), settings())
    with pytest.raises(ValueError, match="UNVERIFIED"):
        sector_rotation_engine(None, strategy, None)
    with pytest.raises(ValueError, match="cannot be labelled synthetic"):
        sector_rotation_engine(None, strategy, None, validation_mode="synthetic")


def test_canonical_engine_checks_every_cash_quantity_nav_and_credit_without_certifying_PIT():
    engine = run(end="2025-10-16")
    proof = engine.research_report
    assert proof["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    assert proof["engine_invoked"] is True and proof["data_origin"] == "invented_fixture"
    assert proof["PIT_verified"] is False and proof["full_real_scenarios_accepted"] == 0
    assert (
        proof["performance_admission"] is False
        and proof["investor_tax_eligibility_verified"] is False
    )
    ledger = proof["independent_ledger"]
    assert ledger["fills_checked"] > 0
    assert ledger["monetary_checkpoints"] == 4 * len(engine.research_trace)
    assert Decimal(ledger["maximum_monetary_error"]) <= Decimal("1E-8")
    records = engine.broker.corporate_action_evidence()["records"]
    entitlements = [r for r in records if r["status"] == "dividend_receivable"]
    credits = [r for r in records if r["status"] == "cash_credited"]
    assert len(entitlements) == len(credits) == 1
    assert entitlements[0]["withholding"] == pytest.approx(entitlements[0]["gross"] / 10)
    assert credits[0]["withholding_applied_again"] is False
    assert credits[0]["actual_broker_confirmation"] is False
    assert datetime.fromisoformat(credits[0]["source_observed_at"]).year == 2026
    assert datetime.fromisoformat(credits[0]["available_at"]).year == 2025
    assert credits[0]["event_available_at_is_modeled"] is True
    with pytest.raises(RuntimeError, match="fresh"):
        engine.run()


def test_retro_coverage_stays_false_and_strict_processor_rejects_it():
    engine = retrospective_rotation_engine(inputs(), ASSUMPTIONS, settings())
    provider = engine.broker.research_provider
    stamp = provider.inputs.bars[0].end
    sid = provider.inputs.securities[0].security_id
    coverage, _ = provider.snapshot(sid, stamp)
    assert coverage.point_in_time is False and coverage.available_at > stamp
    strict = CorporateActionProcessor(engine.broker, provider)
    with pytest.raises(ValueError, match="point-in-time"):
        strict._validate_coverage(coverage, sid, stamp, stamp.date(), stamp.date())


@pytest.mark.parametrize(
    "field", ["cash", "settled_cash", "receivables", "NAV", "positions", "commission"]
)
def test_independent_oracle_rejects_corrupted_canonical_results(field):
    engine = run(end="2025-10-16")
    trace = copy.deepcopy(engine.research_trace)
    fills = copy.deepcopy(engine.broker.fills)
    if field == "commission":
        fills[0].commission += 1
    elif field == "positions":
        trace[-1][field][next(iter(trace[-1][field]))] += 1
    else:
        trace[-1][field] += 1
    with pytest.raises(ResearchLedgerMismatch):
        verify_research_ledger(
            engine.research_inputs,
            ASSUMPTIONS,
            fills,
            engine.broker.orders,
            engine.broker.fee_records,
            trace,
        )


def test_friday_sale_is_not_spendable_during_monday_settlement_holiday():
    engine = run(bear=True, end="2025-10-16")
    sales = [f for f in engine.broker.fills if f.side.value == "sell"]
    assert any(f.timestamp.date() == date(2025, 10, 10) for f in sales)
    for day in (date(2025, 10, 10), date(2025, 10, 13)):
        state = [x for x in engine.research_trace if x["timestamp"].date() == day][-1]
        assert state["settled_cash"] < state["cash"]
    state = next(x for x in engine.research_trace if x["timestamp"].date() == date(2025, 10, 14))
    assert state["settled_cash"] == state["cash"]


def test_future_closes_and_actions_cannot_change_earlier_signal_values():
    bundle = inputs()
    changed = replace(
        bundle,
        closes=tuple(
            replace(c, close=c.close * 10) if c.session >= date(2025, 10, 20) else c
            for c in bundle.closes
        ),
        distributions=(
            *bundle.distributions,
            ResearchDistribution(
                UNIVERSE[0],
                "future",
                date(2025, 10, 21),
                date(2025, 10, 21),
                date(2025, 10, 24),
                Decimal(3),
                "ordinary_fund",
                "fixture",
                "fixture",
            ),
        ),
    )
    a, b = raw_total_returns(bundle), raw_total_returns(changed)
    assert [x for x in a if x[0] < date(2025, 10, 20)] == [
        x for x in b if x[0] < date(2025, 10, 20)
    ]


def test_bundle_hashes_and_sources_must_match_before_normalized_inputs_are_loaded():
    original = inputs()
    document = {"schema": "rotation_retrospective_inputs_v1", **asdict(original)}
    body = json.dumps(document, default=str).encode()
    loaded = ResearchInputs.from_bundle(body, digest(body), {"fixture": BODY})
    assert loaded == original and loaded.fingerprint == original.fingerprint
    with pytest.raises(ValueError, match="bundle hash"):
        ResearchInputs.from_bundle(body + b" ", digest(body), {"fixture": BODY})
    with pytest.raises(ValueError, match="source bytes"):
        ResearchInputs.from_bundle(body, digest(body), {"fixture": b"changed"})


@pytest.mark.parametrize(
    "kind",
    [
        "canadian",
        "adr",
        "missing-security",
        "missing-close",
        "duplicate-bar",
        "future-observation",
        "special",
        "missing-settlement",
    ],
)
def test_missing_or_ineligible_inputs_are_rejected_before_engine_construction(kind):
    bundle = inputs()
    with pytest.raises(ValueError):
        if kind in {"canadian", "adr"}:
            security = replace(
                bundle.securities[0],
                **(
                    {"issuer_country": "CA"}
                    if kind == "canadian"
                    else {"is_depositary_receipt": True}
                ),
            )
            replace(bundle, securities=(security, *bundle.securities[1:]))
        elif kind == "missing-security":
            replace(bundle, securities=bundle.securities[1:])
        elif kind == "missing-close":
            replace(bundle, closes=bundle.closes[1:])
        elif kind == "duplicate-bar":
            replace(bundle, bars=(*bundle.bars, bundle.bars[-1]))
        elif kind == "future-observation":
            replace(bundle, sources=(replace(SOURCE, observed_at=datetime(2025, 1, 1, tzinfo=NY)),))
        elif kind == "special":
            replace(
                bundle,
                distributions=(
                    replace(bundle.distributions[0], classification="return_of_capital"),
                ),
            )
        else:
            replace(bundle, settlement_sessions=())


def test_incomplete_warmup_is_not_an_empty_passing_run():
    with pytest.raises(ValueError, match="warm-up"):
        retrospective_rotation_engine(inputs(), ASSUMPTIONS)


def test_research_tax_profile_is_refused_by_the_production_resolver():
    engine = retrospective_rotation_engine(inputs(), ASSUMPTIONS, settings())
    event = next(e for e in engine.broker.research_provider.events if e.kind == "CASH_DIVIDEND")
    with pytest.raises(ValueError, match="isolated research processor"):
        resolve_dividend_tax_rate(engine.broker.controller.config, event, event.available_at)


def test_warmup_gross_signal_does_not_require_hindsight_tax_classification():
    bundle = inputs()
    warmup = ResearchDistribution(
        UNIVERSE[0],
        "unknown-tax-warmup",
        date(2025, 8, 12),
        date(2025, 8, 12),
        date(2025, 8, 15),
        Decimal(1),
        "unclassified_signal_only",
        "fixture",
        None,
    )
    updated = replace(bundle, distributions=(warmup, *bundle.distributions))
    assert (
        raw_total_returns(updated)[-1][1][UNIVERSE[0]]
        > raw_total_returns(bundle)[-1][1][UNIVERSE[0]]
    )
    with pytest.raises(ValueError, match="classification"):
        replace(
            bundle,
            distributions=(
                replace(
                    warmup,
                    ex_date=date(2025, 10, 7),
                    record_date=date(2025, 10, 7),
                    pay_date=date(2025, 10, 9),
                ),
            ),
        )


@pytest.mark.parametrize("gap", ["minute", "session"])
def test_missing_execution_observations_are_not_forward_filled(gap):
    bundle = inputs()
    with pytest.raises(ValueError, match="Missing execution"):
        replace(
            bundle,
            bars=tuple(
                b
                for b in bundle.bars
                if not (
                    b.end.date() == date(2025, 10, 7)
                    and (gap == "session" or b.end.time() == time(9, 37))
                )
            ),
        )


def test_partial_fills_respect_participation_and_order_minimum_lifecycle():
    assumption = replace(ASSUMPTIONS, maximum_participation=Decimal("0.00002"))
    engine = retrospective_rotation_engine(inputs(end="2025-10-16"), assumption, settings())
    engine.run()
    assert len(engine.broker.fills) > 5
    assert all(f.quantity <= 2 for f in engine.broker.fills)
    assert engine.research_report["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"


def test_an_empty_no_cash_replay_cannot_be_counted_as_success():
    engine = retrospective_rotation_engine(
        inputs(end="2025-10-16"), replace(ASSUMPTIONS, initial_cash=Decimal(1)), settings()
    )
    with pytest.raises(ResearchLedgerMismatch, match="No executions"):
        engine.run()
    assert engine.research_report["status"] == "RETROSPECTIVE_NO_EXECUTIONS"
