"""Invented inputs; independent signal expectations and public Engine accounting."""

from dataclasses import replace
from datetime import date, datetime, timedelta

import polars as pl
import pytest

from ml4t.backtest import DataFeed, Engine
from ml4t.backtest.strategies.stock_momentum import (
    MomentumSnapshot,
    SnapshotSource,
    StockMomentumConfig,
    StockMomentumStrategy,
    StockObservation,
    select_stocks,
    stock_momentum_engine,
)
from ml4t.backtest.strategies.stock_momentum.signals import market_risk_off
from quant_constraints import (
    EarningsCoverage,
    EarningsEvent,
    InMemoryEarningsProvider,
    MarketContext,
)
from quant_constraints.calendar import NY, SessionCalendar

CAL = SessionCalendar()


def at(day="2026-10-01", clock="10:00"):
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=NY)


def snapshot(day=date(2026, 9, 30), *, scores=None, **changes):
    sessions = [day]
    for _ in range(252):
        sessions.insert(0, CAL.shift(sessions[0], -1))
    close = CAL.session(day).market_close
    stocks = {}
    for asset, score in (
        scores or {"A": 0.6, "B": 0.5, "C": 0.4, "D": 0.3, "E": 0.2, "F": 0.1}
    ).items():
        prices = [100.0] * 253
        prices[-22:] = [100 * (1 + score)] * 22
        stocks[asset] = StockObservation(
            f"LISTING:{asset}", close, asset, 20e9, 50e6, 100, tuple(prices)
        )
    return MomentumSnapshot(day, close, tuple(sessions), stocks, **changes)


def run_case(
    snapshots=None,
    *,
    cfg=None,
    events=(),
    coverage=True,
    days=("2026-10-01",),
    gap=False,
    clocks=None,
):
    snapshots = snapshots or (snapshot(),)
    source = SnapshotSource(snapshots)
    strategy = StockMomentumStrategy(source, cfg)
    symbols = sorted(set().union(*(s.stocks.keys() for s in snapshots)))
    rows = []
    for day in days:
        for clock in clocks or (
            "09:59",
            "10:00",
            "10:01",
            "10:02",
            "10:03",
            "11:29",
            "11:30",
            "15:30",
            "15:31",
        ):
            for asset in symbols:
                price = 100.0 + float(gap) * 0.1 if gap and clock >= "10:01" else 100.0
                rows.append(
                    {
                        "timestamp": at(day, clock),
                        "asset": asset,
                        "open": price,
                        "high": price,
                        "low": price,
                        "close": price,
                        "volume": 100000,
                    }
                )
    feed = DataFeed(prices_df=pl.DataFrame(rows))
    cov = (
        tuple(EarningsCoverage(a, at("2025-01-01"), at("2027-01-01"), "synthetic") for a in symbols)
        if coverage
        else ()
    )

    def context(now, asset, phase):
        current = source.latest(now)
        return MarketContext(
            now,
            100,
            sector=current.stocks[asset].industry,
            instrument="equity",
            spread=0.001,
            rvol=2,
            volume=100000,
            liquidity_available_at=now,
        )

    engine = stock_momentum_engine(
        feed, strategy, context, InMemoryEarningsProvider(tuple(events), cov)
    )
    result = engine.run()
    return engine, strategy, result


def test_exact_252_21_endpoints_skip_last_month_and_deterministic_tie():
    original = snapshot(scores={"Z": 0.2, "A": 0.2, "B": -0.1})
    stocks = dict(original.stocks)
    stock = stocks["A"]
    prices = list(stock.total_returns)
    prices[-21:] = [900.0] * 21
    stocks["A"] = replace(stock, total_returns=tuple(prices))
    decision = select_stocks(replace(original, stocks=stocks), StockMomentumConfig())
    assert decision.scores == pytest.approx({"A": 0.2, "Z": 0.2})
    assert list(decision.targets) == ["A", "Z"]
    assert sum(decision.targets.values()) == pytest.approx(0.3792)
    assert decision.excluded["B"] == "nonpositive_momentum"


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"country": "CA"}, "universe_exclusion"),
        ({"security_type": "ADRC"}, "universe_exclusion"),
        ({"security_type": "ETF"}, "universe_exclusion"),
        ({"eligible": False}, "universe_exclusion"),
        ({"market_cap": 1e9}, "market_cap"),
        ({"dollar_volume": 1e6}, "liquidity_or_price"),
        ({"raw_close": 2}, "liquidity_or_price"),
        ({"total_returns": (100,) * 30, "listed_on": date(2026, 9, 1)}, "insufficient_history"),
    ],
)
def test_historical_universe_exclusions(change, reason):
    snap = snapshot()
    stocks = dict(snap.stocks)
    stocks["A"] = replace(stocks["A"], **change)
    decision = select_stocks(replace(snap, stocks=stocks), StockMomentumConfig())
    assert "A" not in decision.targets
    assert decision.excluded["A"] == reason


def test_industry_cap_does_not_redistribute_missing_slots():
    snap = snapshot()
    snap = replace(snap, stocks={a: replace(s, industry="tech") for a, s in snap.stocks.items()})
    decision = select_stocks(snap, StockMomentumConfig())
    assert list(decision.targets) == ["A", "B"]
    assert sum(decision.targets.values()) < 0.40


def test_missing_data_modes_and_future_metadata():
    snap = snapshot()
    stocks = dict(snap.stocks)
    stocks["A"] = replace(stocks["A"], raw_close=float("nan"))
    bad = replace(snap, stocks=stocks)
    with pytest.raises(ValueError, match="missing_metadata"):
        select_stocks(bad, StockMomentumConfig())
    assert "A" not in select_stocks(bad, StockMomentumConfig(missing_data="skip")).targets
    stocks["A"] = replace(snap.stocks["A"], available_at=at())
    with pytest.raises(ValueError, match="Future metadata"):
        select_stocks(replace(snap, stocks=stocks), StockMomentumConfig())


def test_missing_session_duplicate_id_and_early_publication_rejected():
    snap = snapshot()
    with pytest.raises(ValueError, match="consecutive"):
        replace(snap, sessions=snap.sessions[:50] + snap.sessions[51:])
    with pytest.raises(ValueError, match="Duplicate"):
        replace(snap, stocks={"A": snap.stocks["A"], "B": snap.stocks["A"]})
    with pytest.raises(ValueError, match="completed session"):
        replace(snap, available_at=snap.available_at - timedelta(minutes=1))


def test_source_never_exposes_future_snapshot_and_is_immutable():
    snap = snapshot()
    source = SnapshotSource((snap,))
    assert source.latest(snap.available_at - timedelta(seconds=1)) is None
    with pytest.raises(TypeError):
        snap.stocks["X"] = snap.stocks["A"]


def test_spy_two_closes_confirmation_and_no_data_failure():
    cfg = StockMomentumConfig(market_filter=True)
    prices = [100.0] * 253
    prices[-1] = 80
    assert not market_risk_off(replace(snapshot(), spy_total_returns=tuple(prices)), cfg)
    prices[-2] = 90
    snap = replace(snapshot(), spy_total_returns=tuple(prices))
    assert select_stocks(snap, cfg).risk_off
    assert not select_stocks(snap, cfg).targets
    with pytest.raises(ValueError, match="SPY"):
        select_stocks(snapshot(), cfg)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"skip": 252},
        {"names": 10},
        {"exposure": 1.1},
        {"market_filter": 1},
        {"cost_buffer": float("nan")},
        {"frequency": "daily"},
    ],
)
def test_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        StockMomentumConfig(**kwargs)


def test_public_engine_next_bar_tenths_costs_and_no_short():
    engine, strategy, result = run_case(gap=True)
    assert len(strategy.decisions) == 1
    positions = engine.broker.get_positions()
    assert set(positions) == {"A", "B", "C", "D", "E"}
    assert all(
        p.quantity > 0 and p.quantity * 10 == pytest.approx(round(p.quantity * 10))
        for p in positions.values()
    )
    fills = [a for a in engine.broker.controller.audit if a.filled_quantity > 0]
    assert fills and all(a.timestamp >= at(clock="10:01") for a in fills)
    assert all(p.entry_price > 100 for p in positions.values())
    assert engine.broker.settled_cash >= 0
    assert engine.broker.fee_statistics()["total_fees"] > 0


def test_stale_snapshot_and_missing_earnings_do_not_buy():
    engine, _, _ = run_case(days=("2026-10-02",))
    assert not engine.broker.orders
    engine, strategy, _ = run_case(coverage=False)
    assert not engine.broker.get_positions()
    assert any("earnings_coverage_missing" in event for _, event in strategy.events)


def test_earnings_blackout_keeps_slot_in_cash():
    event = EarningsEvent(
        "A", "A-Q", at("2026-10-02", "16:30"), at("2026-09-01"), "AMC", "synthetic"
    )
    engine, strategy, _ = run_case(events=(event,))
    assert set(engine.broker.get_positions()) == {"B", "C", "D", "E"}
    assert "F" not in engine.broker.get_positions()
    assert any("earnings_blackout" in event for _, event in strategy.events)


def test_public_engine_market_exit_then_no_unscheduled_reentry():
    up = tuple([100.0] * 252 + [101.0])
    down = tuple([100.0] * 251 + [90.0, 80.0])
    snapshots = (
        snapshot(spy_total_returns=up),
        snapshot(date(2026, 10, 1), spy_total_returns=down),
        snapshot(date(2026, 10, 2), spy_total_returns=up),
    )
    engine, strategy, _ = run_case(
        snapshots,
        cfg=StockMomentumConfig(market_filter=True),
        days=("2026-10-01", "2026-10-02", "2026-10-05"),
    )
    assert not engine.broker.get_positions()
    assert len(strategy.decisions) == 1
    assert any(a.filled_quantity > 0 and a.side == "sell" for a in engine.broker.controller.audit)


def test_plain_engine_rejected_and_real_inputs_not_labelled_synthetic():
    feed = DataFeed(
        prices_df=pl.DataFrame(
            [
                {
                    "timestamp": at(),
                    "asset": "A",
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1000.0,
                }
            ]
        )
    )
    strategy = StockMomentumStrategy(SnapshotSource((snapshot(),)))
    with pytest.raises(ValueError, match="stock_momentum_engine"):
        Engine(feed, strategy).run()
    real = StockMomentumStrategy(
        SnapshotSource((replace(snapshot(), evidence_mode="retrospective_research"),))
    )
    with pytest.raises(ValueError, match="synthetic"):
        stock_momentum_engine(feed, real, None, InMemoryEarningsProvider())


def test_semimonthly_rotation_waits_for_settlement_and_resumes_once():
    swapped = {"A": -0.1, "B": 0.5, "C": 0.4, "D": 0.3, "E": 0.2, "F": 0.8}
    snapshots = (
        snapshot(),
        snapshot(date(2026, 10, 15), scores=swapped),
        snapshot(date(2026, 10, 16), scores=swapped),
    )
    engine, strategy, _ = run_case(
        snapshots,
        cfg=StockMomentumConfig(frequency="semi_monthly"),
        days=("2026-10-01", "2026-10-16", "2026-10-19"),
    )
    assert len(strategy.decisions) == 2
    assert set(engine.broker.get_positions()) == {"B", "C", "D", "E", "F"}
    fills = [a for a in engine.broker.controller.audit if a.filled_quantity > 0]
    assert [a.timestamp.date() for a in fills if a.asset == "A" and a.side == "sell"] == [
        date(2026, 10, 16)
    ]
    assert [a.timestamp.date() for a in fills if a.asset == "F" and a.side == "buy"] == [
        date(2026, 10, 19)
    ]
    assert any(
        a.decision.code == "rebalance_wait_settlement" for a in engine.broker.controller.audit
    )
    assert all(
        at(a.timestamp.date().isoformat())
        <= a.timestamp
        < at(a.timestamp.date().isoformat(), "11:30")
        for a in fills
    )


def test_monthly_does_not_rebalance_on_sixteenth():
    swapped = {"A": -0.1, "B": 0.5, "C": 0.4, "D": 0.3, "E": 0.2, "F": 0.8}
    engine, strategy, _ = run_case(
        (snapshot(), snapshot(date(2026, 10, 15), scores=swapped)),
        days=("2026-10-01", "2026-10-16"),
    )
    assert len(strategy.decisions) == 1
    assert set(engine.broker.get_positions()) == {"A", "B", "C", "D", "E"}


def test_earnings_risk_exit_still_works_after_morning_window():
    # Newly announced event: held before release; core risk monitor must run at 15:30.
    event = EarningsEvent(
        "A", "A-Q", at("2026-10-01", "16:30"), at(clock="12:00"), "AMC", "synthetic"
    )
    engine, _, _ = run_case(events=(event,))
    assert "A" not in engine.broker.get_positions()
    exits = [
        a
        for a in engine.broker.controller.audit
        if a.asset == "A" and a.side == "sell" and a.filled_quantity > 0
    ]
    assert exits and all(a.timestamp >= at(clock="15:30") for a in exits)


def test_missing_history_is_not_misclassified_as_new_listing():
    snap = snapshot()
    stocks = dict(snap.stocks)
    stocks["A"] = replace(stocks["A"], total_returns=(100.0,) * 30)
    with pytest.raises(ValueError, match="missing_prices"):
        select_stocks(replace(snap, stocks=stocks), StockMomentumConfig())


def test_pending_normal_orders_cannot_fill_after_morning_window():
    engine, _, _ = run_case(clocks=("11:29", "11:30", "15:30", "15:31"))
    assert engine.broker.orders
    assert not engine.broker.get_positions()
    assert not any(a.filled_quantity for a in engine.broker.controller.audit)


def test_large_gap_is_rejected_instead_of_spending_reserved_cash():
    engine, _, _ = run_case(gap=10)
    assert engine.broker.settled_cash >= 0
    assert any(a.decision.code == "cash_reserve" for a in engine.broker.controller.audit)
    assert all(p.quantity > 0 for p in engine.broker.get_positions().values())
