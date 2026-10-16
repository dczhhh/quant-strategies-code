"""Invented source bundles exercise the canonical retrospective Engine and oracle."""

import copy
import json
import os
import subprocess
import sys
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
from ml4t.backtest.strategies.sector_rotation.research_benchmark import (
    BENCHMARK_POLICY,
    retrospective_spy_benchmark,
)
from ml4t.backtest.strategies.sector_rotation.research_inputs import (
    MATCHED_CLOSES,
    SEPARATE_CLOSES,
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


def test_funded_spy_is_causal_and_uses_completed_closes_with_the_same_ledger():
    original = inputs()
    bundle = replace(
        original,
        bars=tuple(
            replace(b, open=b.close - 1, low=b.close - 2, high=b.close + 2) for b in original.bars
        ),
    )
    engine = retrospective_spy_benchmark(bundle, ASSUMPTIONS)
    engine.run()
    assert engine.research_report["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    assert engine.research_report["benchmark_policy"] == BENCHMARK_POLICY
    assert engine.research_report["input_fingerprint"] == bundle.fingerprint
    assert engine.research_report["performance_admission"] is False
    assert len(engine.broker.orders) == len(engine.broker.fills) == 1
    order, fill = engine.broker.orders[0], engine.broker.fills[0]
    assert order.asset == fill.asset == "SPY"
    assert order.created_at == datetime(2025, 9, 30, 9, 35, tzinfo=NY)
    assert fill.timestamp == datetime(2025, 9, 30, 9, 36, tzinfo=NY)
    bar = next(b for b in bundle.bars if (b.end, b.asset) == (fill.timestamp, "SPY"))
    assert fill.price == pytest.approx(float(bar.close * Decimal("1.0002")))
    assert fill.commission > 0
    assert engine.research_trace[-1]["positions"] == {"SPY": fill.quantity}


@pytest.mark.parametrize("paid", [True, False])
def test_funded_spy_net_dividend_cash_and_receivable_match_hand_portfolio(paid):
    original = inputs()
    event = ResearchDistribution(
        "SPY",
        "spy-cash",
        date(2025, 10, 7),
        date(2025, 10, 7),
        date(2025, 10, 9) if paid else date(2025, 11, 20),
        Decimal("0.75"),
        "ordinary_fund",
        "fixture",
        "fixture",
    )
    bundle = replace(original, distributions=(event,), splits=())
    engine = retrospective_spy_benchmark(bundle, ASSUMPTIONS)
    engine.run()
    fill = engine.broker.fills[0]
    quantity = Decimal(str(fill.quantity))
    assert quantity == 94
    # Independent one-order, below-first-tier fee calculation for this invented input.
    fees = max(Decimal("0.35"), quantity * Decimal("0.0035")) * Decimal("1.000735")
    fees += quantity * (Decimal("0.0035") + Decimal("0.0002") + Decimal("0.000003"))
    net = quantity * Decimal("0.75") * Decimal("0.90")
    initial_close = next(
        b.close for b in bundle.bars if b.asset == "SPY" and b.end == fill.timestamp
    )
    expected_cash = ASSUMPTIONS.initial_cash - quantity * initial_close * Decimal("1.0002") - fees
    expected_cash += net if paid else 0
    final = engine.research_trace[-1]
    last_close = next(
        b.close for b in bundle.bars if b.asset == "SPY" and b.end == final["timestamp"]
    )
    assert abs(Decimal(str(final["cash"])) - expected_cash) <= Decimal("1E-8")
    assert abs(Decimal(str(final["receivables"])) - (0 if paid else net)) <= Decimal("1E-8")
    expected_nav = expected_cash + quantity * last_close + (0 if paid else net)
    assert abs(Decimal(str(final["NAV"])) - expected_nav) <= Decimal("1E-8")
    assert len(engine.broker.fills) == 1  # No ex-date or credit-date reinvestment.


def test_funded_spy_partial_entry_expires_after_three_sessions_without_resubmission():
    original = inputs()
    bundle = replace(original, bars=tuple(replace(b, volume=Decimal(100)) for b in original.bars))
    engine = retrospective_spy_benchmark(bundle, ASSUMPTIONS)
    engine.run()
    assert len(engine.broker.orders) == 1
    assert len(engine.broker.fills) > 1
    assert all(f.quantity == 1 and f.asset == "SPY" for f in engine.broker.fills)
    assert {f.timestamp.astimezone(NY).date() for f in engine.broker.fills} == {
        date(2025, 9, 30),
        date(2025, 10, 1),
        date(2025, 10, 2),
    }
    assert engine.broker.orders[0].filled_quantity < engine.broker.orders[0].quantity
    assert engine.research_report["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"


def test_funded_spy_retains_drift_without_sector_cap_sales_and_future_prices_cannot_size_entry():
    original = inputs()
    future = date(2025, 10, 20)
    changed = replace(
        original,
        closes=tuple(
            replace(c, close=c.close * 2) if c.asset == "SPY" and c.session >= future else c
            for c in original.closes
        ),
        bars=tuple(
            replace(b, **{k: getattr(b, k) * 2 for k in ("open", "high", "low", "close")})
            if b.asset == "SPY" and b.end.date() >= future
            else b
            for b in original.bars
        ),
    )
    engines = [retrospective_spy_benchmark(b, ASSUMPTIONS) for b in (original, changed)]
    for engine in engines:
        engine.run()
    first, second = (e.broker.fills for e in engines)
    assert len(first) == len(second) == 1
    assert asdict(first[0]) == asdict(second[0])
    state = engines[1].research_trace[-1]
    assert (state["NAV"] - state["cash"]) / state["NAV"] > 0.95
    assert engines[1].broker.controller.config.drift_reduction == "disabled"


def test_funded_spy_insufficient_cash_and_unfilled_entry_cannot_pass():
    original = inputs()
    engine = retrospective_spy_benchmark(original, replace(ASSUMPTIONS, initial_cash=Decimal(50)))
    with pytest.raises(ValueError, match="cannot fund one whole SPY share"):
        engine.run()
    assert not engine.broker.fills
    engine = retrospective_spy_benchmark(
        replace(original, bars=tuple(replace(b, volume=Decimal(0)) for b in original.bars)),
        ASSUMPTIONS,
    )
    with pytest.raises(ResearchLedgerMismatch, match="No executions"):
        engine.run()


def test_funded_spy_cannot_open_the_normal_real_or_synthetic_entry():
    engine = retrospective_spy_benchmark(inputs(), ASSUMPTIONS)
    for mode, message in (("real", "UNVERIFIED"), ("synthetic", "cannot be labelled synthetic")):
        with pytest.raises(ValueError, match=message):
            sector_rotation_engine(None, engine.strategy, None, validation_mode=mode)


def test_raw_share_returns_match_independent_hand_reinvestment_through_split_and_dividend():
    original = inputs(end="2025-10-09")
    selected = sessions("2025-10-06", "2025-10-09")
    asset = UNIVERSE[0]
    closes = tuple(
        RawClose(a, d, Decimal(p), "fixture")
        for d, p in zip(selected, (100, 102, 50, 51))
        for a in sorted(UNIVERSE)
    )
    marks = {row.session: row.close for row in closes}
    bundle = replace(
        original,
        closes=closes,
        bars=tuple(
            replace(b, **dict.fromkeys(("open", "high", "low", "close"), marks[b.end.date()]))
            for b in original.bars
            if b.end.date() in selected
        ),
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


@pytest.mark.parametrize("separate", [False, True])
def test_real_default_and_synthetic_modes_both_refuse_research_inputs(separate):
    bundle = paired_inputs() if separate else inputs()
    strategy = SectorRotationStrategy(RetrospectiveCloseSource(bundle), settings())
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
        bars=tuple(
            replace(b, **{k: getattr(b, k) * 10 for k in ("open", "high", "low", "close")})
            if b.end.astimezone(NY).date() >= date(2025, 10, 20)
            else b
            for b in bundle.bars
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


def test_cross_month_replay_matches_in_fresh_processes_with_different_hash_seeds():
    code = (
        "import hashlib,json,runpy; "
        "t=runpy.run_path('tests/strategies/test_rotation_research.py'); e=t['run'](); "
        "print(hashlib.sha256(json.dumps({'report':e.research_report,"
        "'trace':e.research_trace},sort_keys=True,default=str).encode()).hexdigest())"
    )
    proofs = [
        subprocess.check_output(
            [sys.executable, "-c", code], env={**os.environ, "PYTHONHASHSEED": seed}, text=True
        ).strip()
        for seed in ("1", "937")
    ]
    assert len(proofs[0]) == 64 and proofs[0] == proofs[1]


@pytest.mark.parametrize("gap", ["omitted", "duplicate", "extra", "reordered"])
def test_ledger_requires_exactly_one_observation_per_input_timestamp(gap):
    engine = run(end="2025-10-16")
    trace = copy.deepcopy(engine.research_trace)
    if gap == "omitted":
        del trace[-2]
    elif gap == "duplicate":
        trace.insert(-1, copy.deepcopy(trace[-2]))
    elif gap == "extra":
        extra = copy.deepcopy(trace[0])
        extra["timestamp"] += timedelta(minutes=1)
        trace.insert(1, extra)
    else:
        trace[1], trace[2] = trace[2], trace[1]
    with pytest.raises(ResearchLedgerMismatch, match="observation coverage"):
        verify_research_ledger(
            engine.research_inputs,
            ASSUMPTIONS,
            engine.broker.fills,
            engine.broker.orders,
            engine.broker.fee_records,
            trace,
        )


@pytest.mark.parametrize("collision", ["split-dividend", "generated-credit"])
def test_all_corporate_action_keys_are_unique_before_engine_construction(collision):
    bundle = inputs()
    event = bundle.distributions[0]
    identifier = (
        event.event_id if collision == "split-dividend" else "modeled-credit:" + event.event_id
    )
    with pytest.raises(ValueError, match="corporate action identity"):
        replace(bundle, splits=(replace(bundle.splits[0], event_id=identifier),))


def test_closing_minute_and_raw_daily_signal_price_must_share_the_same_basis():
    bundle = inputs()
    changed = replace(bundle.closes[-1], close=bundle.closes[-1].close * 2)
    with pytest.raises(ValueError, match="daily/minute close"):
        replace(bundle, closes=(*bundle.closes[:-1], changed))


def test_raw_close_comparison_preserves_the_existing_source_rounding_limit():
    bundle = inputs()
    for delta in (Decimal("0.0002"), Decimal("-0.0002")):
        changed = replace(bundle.closes[-1], close=bundle.closes[-1].close + delta)
        replace(bundle, closes=(*bundle.closes[:-1], changed))
    changed = replace(bundle.closes[-1], close=bundle.closes[-1].close + Decimal("0.00020001"))
    with pytest.raises(ValueError, match="daily/minute close"):
        replace(bundle, closes=(*bundle.closes[:-1], changed))


def paired_inputs():
    original = inputs()
    minute = replace(
        SOURCE,
        identifier="minute-fixture",
        sha256=digest(BODY + b" minute"),
        url="https://example.invalid/invented-minute",
    )
    return replace(
        original,
        sources=(*original.sources, minute),
        bars=tuple(replace(b, source_id=minute.identifier) for b in original.bars),
        close_policy=SEPARATE_CLOSES,
    )


def test_distinct_daily_signals_and_minute_nav_keep_both_prices_and_independent_ledger():
    original = paired_inputs()
    baseline = retrospective_rotation_engine(original, ASSUMPTIONS, settings())
    baseline.run()
    asset, position = next((a, p) for a, p in baseline.broker.positions.items() if p.quantity > 0)
    stamp = original.bars[-1].end
    changed = replace(
        original,
        bars=tuple(
            replace(
                b, **{k: getattr(b, k) + Decimal("0.25") for k in ("open", "high", "low", "close")}
            )
            if b.end == stamp and b.asset == asset
            else b
            for b in original.bars
        ),
    )
    assert changed.closes == original.closes
    assert raw_total_returns(changed) == raw_total_returns(original)
    assert changed.close_comparison["mismatches"] == 1
    assert changed.close_comparison["reconciled"] is False
    engine = retrospective_rotation_engine(changed, ASSUMPTIONS, settings())
    engine.run()
    assert engine.broker.fills == baseline.broker.fills
    assert engine.research_trace[-1]["NAV"] - baseline.research_trace[-1]["NAV"] == pytest.approx(
        position.quantity * 0.25, abs=1e-8
    )
    assert engine.research_report["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    assert engine.research_report["price_conventions"] == changed.close_comparison
    assert engine.research_report["performance_admission"] is False
    with pytest.raises(ValueError, match="daily/minute close"):
        replace(changed, close_policy=MATCHED_CLOSES)


def test_separate_close_policy_requires_separate_source_records_and_explicit_v2_bundle():
    with pytest.raises(ValueError, match="distinct retained sources"):
        replace(inputs(), close_policy=SEPARATE_CLOSES)
    with pytest.raises(ValueError, match="Unknown research close policy"):
        replace(inputs(), close_policy="ignore_errors")
    original = paired_inputs()
    bodies = {"fixture": BODY, "minute-fixture": BODY + b" minute"}
    document = {"schema": original.bundle_schema, **asdict(original)}
    body = json.dumps(document, default=str).encode()
    assert ResearchInputs.from_bundle(body, digest(body), bodies) == original
    document["schema"] = "rotation_retrospective_inputs_v1"
    body = json.dumps(document, default=str).encode()
    with pytest.raises(ValueError, match="v2 bundle"):
        ResearchInputs.from_bundle(body, digest(body), bodies)
    document["schema"] = original.bundle_schema
    del document["close_policy"]
    body = json.dumps(document, default=str).encode()
    with pytest.raises(ValueError, match="Explicit research close policy"):
        ResearchInputs.from_bundle(body, digest(body), bodies)


def test_old_v1_bundle_defaults_to_strict_comparison_and_new_mode_keeps_gap_rejection():
    original = inputs()
    document = {"schema": "rotation_retrospective_inputs_v1", **asdict(original)}
    del document["close_policy"]
    body = json.dumps(document, default=str).encode()
    assert ResearchInputs.from_bundle(body, digest(body), {"fixture": BODY}) == original
    paired = paired_inputs()
    with pytest.raises(ValueError):
        replace(paired, bars=paired.bars[1:])
    with pytest.raises(ValueError):
        replace(paired, closes=paired.closes[1:])


@pytest.mark.parametrize("bear,volume", [(False, 100000), (False, 1000), (True, 1000)])
def test_next_completed_close_survives_intraminute_prices_partial_fills_and_risk(bear, volume):
    original = paired_inputs()
    if bear:
        bearish = inputs(bear=True)
        original = replace(
            original,
            closes=bearish.closes,
            bars=tuple(replace(b, source_id="minute-fixture") for b in bearish.bars),
        )
    bars = []
    for index, bar in enumerate(original.bars):
        close = bar.close * (1 + Decimal((index * 17 % 101) - 50) / 10000)
        bars.append(
            replace(
                bar,
                open=close * Decimal("0.9995"),
                close=close,
                high=close * Decimal("1.005"),
                low=close * Decimal("0.995"),
                volume=Decimal(volume),
            )
        )
    bundle = replace(original, bars=tuple(bars))
    assumptions = replace(ASSUMPTIONS, initial_cash=Decimal(100000))
    engine = retrospective_rotation_engine(bundle, assumptions, settings())
    engine.run()
    assert engine.research_report["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    assert engine.broker.fills
    if volume == 1000:
        assert len(engine.broker.fills) > len({fill.order_id for fill in engine.broker.fills})
    by_time = {(b.end, b.asset): b for b in bars}
    for fill in engine.broker.fills:
        bar = by_time[fill.timestamp, fill.asset]
        signed_slip = Decimal("0.0002") * (1 if fill.side.value == "buy" else -1)
        assert fill.price == pytest.approx(float(bar.close * (1 + signed_slip)), abs=1e-8)
        assert abs(fill.price - float(bar.open * (1 + signed_slip))) > 0.00001
    assert Decimal(
        engine.research_report["independent_ledger"]["maximum_monetary_error"]
    ) <= Decimal("1E-8")


@pytest.mark.parametrize("field", ["asset", "side"])
def test_ledger_binds_each_fill_to_the_original_order_identity(field):
    from ml4t.backtest.types import OrderSide

    engine = run(end="2025-10-16")
    orders = copy.deepcopy(engine.broker.orders)
    order = next(o for o in orders if o.order_id == engine.broker.fills[0].order_id)
    setattr(order, field, "SPY" if field == "asset" else OrderSide.SELL)
    with pytest.raises(ResearchLedgerMismatch, match="order identity"):
        verify_research_ledger(
            engine.research_inputs,
            ASSUMPTIONS,
            engine.broker.fills,
            orders,
            engine.broker.fee_records,
            engine.research_trace,
        )


@pytest.mark.parametrize(
    "corruption", ["asset", "side", "amount", "execution-id", "order-id", "fill-order"]
)
def test_committed_fee_evidence_and_fill_sequence_are_independently_checked(corruption):
    engine = run(end="2025-10-16")
    records, orders, fills = (
        list(engine.broker.fee_records),
        list(engine.broker.orders),
        list(engine.broker.fills),
    )
    if corruption in {"asset", "side"}:
        records[0] = replace(
            records[0],
            context=replace(
                records[0].context, **{corruption: "SPY" if corruption == "asset" else "SELL"}
            ),
        )
    elif corruption == "amount":
        records[0] = replace(records[0], fees=replace(records[0].fees, broker_commission=100))
    elif corruption == "execution-id":
        records[-1] = replace(records[-1], execution_id=records[0].execution_id)
    elif corruption == "order-id":
        orders.append(copy.deepcopy(orders[0]))
    else:
        fills.reverse()
    with pytest.raises(ResearchLedgerMismatch):
        verify_research_ledger(
            engine.research_inputs, ASSUMPTIONS, fills, orders, records, engine.research_trace
        )
