"""Offline causal decisions and public Engine/cash-account integration."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from math import isclose

import polars as pl
import pytest

from ml4t.backtest import DataFeed, Engine
from ml4t.backtest.config import CommissionType, ShareType, SlippageType
from ml4t.backtest.strategies.sector_rotation import (
    SECTOR_ETFS,
    SECTORS,
    CausalCloseSource,
    MarketRegime,
    SectorRotationConfig,
    SectorRotationStrategy,
    TotalReturnClose,
    sector_rotation_engine,
    strength_weights,
)
from ml4t.backtest.strategies.sector_rotation.signals import SignalBook
from ml4t.backtest.types import OrderSide
from quant_constraints import (
    ConstraintController,
    InMemoryEarningsProvider,
    MarketContext,
    cash_backtest_config,
)
from quant_constraints.calendar import NY, SessionCalendar

CAL = SessionCalendar()


def settings(**changes):
    base = replace(
        SectorRotationConfig(),
        lookbacks=(2, 3, 4),
        short_ma=2,
        long_ma=4,
        peak_lookback=4,
        down_confirm=2,
        recovery_confirm=2,
    )
    return replace(base, **changes)


def days(start, end):
    result = []
    current = date.fromisoformat(start)
    final = date.fromisoformat(end)
    while current <= final:
        if CAL.session(current):
            result.append(current)
        current += timedelta(days=1)
    return result


def stamp(day, clock):
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=NY)


def source_rows(*, switch=False, spy=None, missing=None, end="2026-11-06"):
    rows = []
    for index, day in enumerate(days("2026-08-17", end)):
        values = {}
        for j, asset in enumerate(SECTOR_ETFS):
            slope = (11 - j if switch else j + 1) * 0.001
            value = 100 * (1 + slope * index)
            if switch and day >= date(2026, 10, 15):
                elapsed = CAL.distance(date(2026, 10, 15), day) + 1
                value *= 1 + max(j - 4, 0) * elapsed * 0.12
            values[asset] = value
        values["SPY"] = spy(day, index) if spy else 100 * (1 + 0.0005 * index)
        if missing:
            values.pop(missing(day), None)
        session = CAL.session(day)
        rows.append(TotalReturnClose(day, session.market_close, values))
    return rows


def context(timestamp, asset, phase):
    return MarketContext(
        timestamp,
        100,
        sector=SECTORS.get(asset, "market"),
        instrument="plain_sector_etf" if asset in SECTORS else "equity",
        spread=0.0001,
        volume=10000,
        liquidity_available_at=timestamp,
        security_id=f"invented:{asset}",
        issuer_country="US",
        tax_source_country="US",
        security_type="ETF" if asset in SECTORS else "CS",
        is_depositary_receipt=False,
        security_metadata_available_at=timestamp,
        security_metadata_source="https://example.invalid/invented-security-facts",
        security_metadata_sha256="a" * 64,
        security_metadata_symbol=asset,
        security_metadata_security_id=f"invented:{asset}",
    )


def run(
    rows=None,
    *,
    end="2026-11-06",
    cycle="historical",
    real_fees=False,
    initial=100000,
    integer=False,
    participation=None,
):
    rows = rows if rows is not None else source_rows(end=end)
    strategy = SectorRotationStrategy(CausalCloseSource(rows), settings())
    prices = []
    for day in days("2026-09-30", end):
        for clock in (
            "09:30",
            "09:35",
            "09:36",
            "09:37",
            "09:38",
            "09:39",
            "09:40",
            "09:44",
            "09:45",
            "10:00",
            "16:00",
        ):
            for asset in (*SECTOR_ETFS, "SPY"):
                prices.append(
                    {
                        "timestamp": stamp(day, clock),
                        "asset": asset,
                        "open": 100.0,
                        "high": 100.0,
                        "low": 100.0,
                        "close": 100.0,
                        "volume": 10000.0,
                    }
                )
    feed = DataFeed(prices_df=pl.DataFrame(prices))
    constraints = strategy.config.constraints(settlement_cycle=cycle)
    if not real_fees:
        constraints = replace(constraints, pricing_plan="custom", slippage_mode="configured")
    controller = ConstraintController(constraints, InMemoryEarningsProvider())
    config = cash_backtest_config(initial_cash=initial)
    if integer:
        config = replace(config, share_type=ShareType.INTEGER)
    if not real_fees:
        config = replace(
            config,
            commission_type=CommissionType.NONE,
            commission_minimum=0,
            commission_per_share=0,
            slippage_type=SlippageType.NONE,
            slippage_rate=0,
        )
    engine = sector_rotation_engine(
        feed, strategy, context, config, controller=controller, validation_mode="synthetic"
    )
    if participation is not None:
        from ml4t.backtest.execution.limits import VolumeParticipationLimit

        engine.broker.execution_limits = VolumeParticipationLimit(participation)
    engine.run()
    return engine, strategy


@pytest.mark.parametrize("mode", ["real", "unverified", "", "REAL"])
def test_unverified_real_entry_is_blocked_before_engine_or_broker_construction(mode):
    strategy = SectorRotationStrategy(CausalCloseSource(source_rows()), settings())
    with pytest.raises(ValueError, match="UNVERIFIED.*real-corporate-actions"):
        sector_rotation_engine(None, strategy, None, validation_mode=mode)


@pytest.mark.parametrize(
    "scores",
    [
        {"A": 0.12, "B": 0.07, "C": 0.03, "D": 0, "E": 0},
        {"A": -1, "B": -0.8, "C": -0.6, "D": -0.4, "E": -0.2},
        dict.fromkeys("ABCDE", -2),
        {"A": 1000, **dict.fromkeys("BCDE", 0)},
    ],
)
def test_floating_weights_are_bounded_monotonic_and_sum_to_95(scores):
    weights = strength_weights(scores, 0.95)
    assert sum(weights.values()) == pytest.approx(0.95)
    assert all(0.15 - 1e-12 <= w <= 0.25 + 1e-12 for w in weights.values())
    for a in scores:
        for b in scores:
            if scores[a] > scores[b]:
                assert weights[a] + 1e-12 >= weights[b]
    if scores["A"] == 0.12:
        assert list(weights.values()) == pytest.approx([0.25, 0.22, 0.18, 0.15, 0.15])


@pytest.mark.parametrize(("budget", "count"), [(0.95, 5), (0.70, 4), (0.45, 2), (0.20, 1)])
def test_shortage_scaling_and_sell_only_caps_are_feasible(budget, count):
    for n in range(1, count + 1):
        scores = {str(i): float(i) for i in range(n)}
        weights = strength_weights(scores, budget * n / count)
        assert sum(weights.values()) == pytest.approx(budget * n / count)
        caps = dict.fromkeys(scores, min(0.25, budget / count))
        sell_only = strength_weights(
            scores, min(budget * n / count, sum(caps.values())), upper_bounds=caps
        )
        assert all(sell_only[a] <= caps[a] + 1e-12 for a in scores)


@pytest.mark.parametrize(
    ("scores", "exposure"), [({}, 0.2), ({"A": float("nan")}, 0.2), ({"A": 1}, 0.3)]
)
def test_invalid_allocations_fail_closed(scores, exposure):
    with pytest.raises(ValueError):
        strength_weights(scores, exposure)


def test_close_availability_and_future_mutation_cannot_change_earlier_decisions():
    rows = source_rows()
    point = rows[0]
    with pytest.raises(ValueError):
        TotalReturnClose(point.session, point.available_at - timedelta(minutes=1), point.values)
    with pytest.raises(ValueError):
        TotalReturnClose(point.session, point.available_at, point.values, "split_adjusted_signal")
    with pytest.raises(TypeError):
        point.values["SPY"] = 1
    provider = CausalCloseSource(rows)
    assert provider.available(point.available_at - timedelta(seconds=1)) == ()
    assert provider.available(point.available_at) == (point,)
    with pytest.raises(ValueError):
        CausalCloseSource([point, point])
    changed = [
        replace(row, values={a: v * 100 for a, v in row.values.items()})
        if row.session >= date(2026, 10, 15)
        else row
        for row in rows
    ]
    first, original = run(rows, end="2026-10-14")
    second, altered = run(changed, end="2026-10-14")
    assert original.decisions == altered.decisions
    assert [(f.timestamp, f.asset, f.quantity) for f in first.broker.fills] == [
        (f.timestamp, f.asset, f.quantity) for f in second.broker.fills
    ]


def test_engine_month_end_is_causal_weights_float_and_only_rth_window_fills():
    engine, strategy = run(end="2026-10-09")
    assert isinstance(engine, Engine)
    decision = strategy.decisions[0]
    assert decision.signal_session == date(2026, 9, 30)
    assert decision.execute_session == date(2026, 10, 1)
    assert sum(decision.targets.values()) == pytest.approx(0.95)
    assert len({round(v, 8) for v in decision.targets.values()}) > 1
    assert engine.broker.fills
    for fill in engine.broker.fills:
        local = fill.timestamp.astimezone(NY)
        assert local.date() >= date(2026, 10, 1)
        assert (9, 35) <= (local.hour, local.minute) < (9, 45)
    equity = engine.broker.get_account_value()
    weights = {a: p.quantity * 100 / equity for a, p in engine.broker.get_positions().items()}
    assert len(weights) == 5
    assert all(0.15 <= w <= 0.25 for w in weights.values())
    assert 0.948 <= sum(weights.values()) <= 0.95
    assert engine.broker.settled_cash >= 0.05 * equity


@pytest.mark.parametrize("cycle", ["T+1", "T+2"])
def test_rotation_rebuys_after_settlement_as_same_monthly_plan(cycle):
    engine, strategy = run(source_rows(switch=True), cycle=cycle)
    plan_id = "SPDR-2026-10-30"
    plan = engine.broker.rebalance_plans[plan_id]
    assert plan.status == "completed"
    assert any(
        a.decision.code == "rebalance_wait_settlement" for a in engine.broker.controller.audit
    )
    fills = [f for f in engine.broker.fills if f.rebalance_id == plan_id]
    sold = [f for f in fills if f.side is OrderSide.SELL]
    bought = [f for f in fills if f.side is OrderSide.BUY]
    assert sold and bought
    sale_day = min(f.timestamp.astimezone(NY).date() for f in sold)
    buy_day = min(f.timestamp.astimezone(NY).date() for f in bought)
    assert sale_day == date(2026, 11, 2)
    assert buy_day == date(2026, 11, 3 if cycle == "T+1" else 4)
    assert engine.broker.unsettled_cash == 0
    assert engine.broker.reserved_cash == 0
    assert (
        sum(p.quantity * 100 for p in engine.broker.get_positions().values())
        <= 0.95 * engine.broker.get_account_value()
    )


def test_spy_downgrade_cancels_waiting_buy_before_settlement_fill():
    def spy(day, index):
        return 70 if day >= date(2026, 11, 2) else 100 * (1 + 0.0005 * index)

    engine, strategy = run(source_rows(switch=True, spy=spy))
    plan = engine.broker.rebalance_plans["SPDR-2026-10-30"]
    assert plan.status == "canceled"
    assert plan.reason == "rotation_spy_downgrade"
    assert strategy.book.regime is MarketRegime.DEEP_BEAR
    later = [
        f for f in engine.broker.fills if f.timestamp.astimezone(NY).date() >= date(2026, 11, 3)
    ]
    assert all(f.side is OrderSide.SELL for f in later)
    assert len(engine.broker.get_positions()) <= 1
    assert (
        sum(p.quantity * 100 for p in engine.broker.get_positions().values())
        <= 0.20 * engine.broker.get_account_value()
    )


def test_recovery_does_not_buy_midmonth_and_only_restores_at_month_end():
    def spy(day, index):
        if date(2026, 10, 5) <= day < date(2026, 10, 20):
            return 70
        return (
            150 * (1 + 0.06 * CAL.distance(date(2026, 10, 20), day))
            if day >= date(2026, 10, 20)
            else 100 * (1 + 0.0005 * index)
        )

    engine, strategy = run(source_rows(spy=spy))
    middle = [
        f
        for f in engine.broker.fills
        if date(2026, 10, 6) <= f.timestamp.astimezone(NY).date() <= date(2026, 10, 30)
    ]
    assert middle and all(f.side is OrderSide.SELL for f in middle)
    assert any(e["code"] == "spy_monthly_recovery" for e in strategy.events)
    assert any(
        f.side is OrderSide.BUY and f.timestamp.astimezone(NY).date() == date(2026, 11, 2)
        for f in engine.broker.fills
    )
    assert strategy.book.regime is MarketRegime.NORMAL


def test_missing_spy_prevents_recovery_and_missing_sector_stops_monthly_allocation():
    rows = source_rows(missing=lambda d: "SPY" if d == date(2026, 10, 30) else "")
    engine, strategy = run(rows)
    assert any(
        e["code"] == "monthly_signal_unavailable" and e["signal_session"] == date(2026, 10, 30)
        for e in strategy.events
    )
    assert "SPDR-2026-10-30" not in engine.broker.rebalance_plans
    with_gap = CausalCloseSource(rows[:8] + rows[9:])
    book = SignalBook(settings())
    for row in with_gap.closes[:9]:
        book.ingest(row)
    assert len(book.history) < settings().history_sessions
    assert not book.market_ready


def test_normal_default_fees_and_integer_shares_do_not_cancel_cap_boundary_plan():
    rows = source_rows()
    strongest = SECTOR_ETFS[-1]
    modified = [replace(r, values={**r.values, strongest: r.values[strongest] ** 3}) for r in rows]
    engine, strategy = run(modified, real_fees=True, integer=True, end="2026-10-09")
    assert isclose(strategy.decisions[0].targets[strongest], 0.25, abs_tol=1e-12)
    assert engine.broker.rebalance_plans["SPDR-2026-09-30"].status == "completed"
    assert len(engine.broker.get_positions()) == 5
    assert engine.broker.settled_cash >= 0.05 * engine.broker.get_account_value()
    assert engine.broker.fee_records
    assert all(p.quantity == int(p.quantity) for p in engine.broker.get_positions().values())
    assert all(
        0.15 <= p.quantity * 100 / engine.broker.get_account_value() <= 0.25
        for p in engine.broker.get_positions().values()
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"lookbacks": (3, 2, 1)},
        {"momentum_weights": (0.5, 0.5, 0.5)},
        {"normal_exposure": 0.74},
        {"execution_start_minutes": 15},
        {"trend_buffer": float("nan")},
        {"short_ma": 200},
        {"plan_sessions": True},
    ],
)
def test_invalid_configuration_is_rejected(changes):
    with pytest.raises(ValueError):
        replace(SectorRotationConfig(), **changes)


def test_config_yaml_and_wrong_broker_fail_explicitly(tmp_path):
    path = tmp_path / "strategy.yaml"
    path.write_text("normal_exposure: 0.95\n", encoding="utf-8")
    assert SectorRotationConfig.from_yaml(path).normal_exposure == 0.95
    path.write_text("secret: forbidden\n", encoding="utf-8")
    with pytest.raises(ValueError):
        SectorRotationConfig.from_yaml(path)
    strategy = SectorRotationStrategy(CausalCloseSource(()))
    with pytest.raises(ValueError, match="sector_rotation_engine"):
        strategy.on_start(object())


def test_rank_buffer_retains_incumbent_seventh_instead_of_forcing_fifth():
    engine, strategy = run(source_rows(switch=True))
    decision = strategy.decisions[1]
    ranks = {
        a: i + 1
        for i, a in enumerate(sorted(decision.scores, key=lambda a: (-decision.scores[a], a)))
    }
    original = set(strategy.decisions[0].targets)
    buffered = set(decision.targets) & original
    assert any(ranks[a] in {6, 7} for a in buffered)
    assert all(ranks[a] <= 5 for a in set(decision.targets) - original)
    assert engine.broker.rebalance_plans["SPDR-2026-10-30"].status == "completed"


def test_warning_then_bear_are_distinct_and_recovery_is_latched():
    config = settings(short_ma=4, long_ma=8, peak_lookback=8, deep_drawdown=0.9)
    book = SignalBook(config)
    spy_values = [60, 60, 60, 200, 200, 180, 180, 110, 105, 90]
    sessions = days("2026-10-01", "2026-10-30")
    for i, value in enumerate(spy_values):
        row = TotalReturnClose(sessions[i], CAL.session(sessions[i]).market_close, {"SPY": value})
        book.ingest(row)
        if i == 8:
            assert book.regime is MarketRegime.WARNING
    assert book.regime is MarketRegime.BEAR
    day = sessions[len(spy_values)]
    book.ingest(TotalReturnClose(day, CAL.session(day).market_close, {"SPY": 250}))
    assert book.regime is MarketRegime.BEAR  # an ordinary rebound cannot restore exposure


def test_confirmation_streak_is_warmed_before_full_peak_window():
    config = settings(long_ma=4, peak_lookback=10, down_confirm=3, deep_drawdown=0.9)
    book = SignalBook(config)
    sessions = days("2026-10-01", "2026-10-30")
    for day, value in zip(sessions, [100, 100, 100, 90, 89, 88], strict=False):
        book.ingest(TotalReturnClose(day, CAL.session(day).market_close, {"SPY": value}))
    assert book.below == 3
    assert book.regime is MarketRegime.BEAR
    assert not book.market_ready


def test_plan_expiry_cleans_underweight_partial_entries_without_buying_again():
    engine, strategy = run(end="2026-10-09", participation=0.0001)
    plan = engine.broker.rebalance_plans["SPDR-2026-09-30"]
    assert plan.status == "expired"
    assert not engine.broker.get_positions()
    assert any(e["code"] == "risk_sell_only" for e in strategy.events)
    cutoff = date(2026, 10, 6)
    assert all(
        f.side is OrderSide.SELL
        for f in engine.broker.fills
        if f.timestamp.astimezone(NY).date() >= cutoff
    )


def test_execution_window_uses_new_york_dst_and_actual_half_day_session():
    engine, strategy = run(end="2026-10-09")
    broker = engine.broker
    assert broker.rotation_window(stamp("2026-11-27", "09:35"))
    assert not broker.rotation_window(stamp("2026-11-27", "09:45"))
    assert not broker.rotation_window(stamp("2026-11-26", "09:35"))
    assert stamp("2026-10-01", "09:35").utcoffset() != stamp("2026-11-02", "09:35").utcoffset()


def test_small_integer_account_skips_infeasible_lots_without_borrowing():
    engine, strategy = run(end="2026-10-09", integer=True, initial=1000)
    assert not engine.broker.get_positions()
    assert not engine.broker.fills
    assert engine.broker.settled_cash == 1000
    assert any(e["code"] == "execution_allocation_infeasible" for e in strategy.events)


def test_allocation_overflow_is_rejected_and_zero_cash_allocation_is_valid():
    assert strength_weights({}, 0) == {}
    with pytest.raises(ValueError, match="overflowed"):
        strength_weights({"A": 1e308, "B": -1e308}, 0.4)
    with pytest.raises(ValueError):
        strength_weights({"A": 1}, 0.2, upper_bounds={"A": 0.1})
