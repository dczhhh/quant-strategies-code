"""Run invented stock momentum inputs through the public Engine; no network or orders."""

import json
from datetime import date, datetime
from pathlib import Path

import polars as pl

from ml4t.backtest import DataFeed
from ml4t.backtest.strategies.stock_momentum import (
    MomentumSnapshot,
    SnapshotSource,
    StockMomentumConfig,
    StockMomentumStrategy,
    StockObservation,
    stock_momentum_engine,
)
from quant_constraints import EarningsCoverage, InMemoryEarningsProvider, MarketContext
from quant_constraints.calendar import NY, SessionCalendar


def main():
    config_path = Path(__file__).resolve().parents[1] / "config/stock_momentum_v1.json"
    config = StockMomentumConfig(**json.loads(config_path.read_text()))
    calendar = SessionCalendar()
    day = date(2026, 9, 30)
    sessions = [day]
    for _ in range(252):
        sessions.insert(0, calendar.shift(sessions[0], -1))
    close = calendar.session(day).market_close
    stocks = {}
    for index, asset in enumerate(("DEMO_A", "DEMO_B", "DEMO_C", "DEMO_D", "DEMO_E", "DEMO_F")):
        prices = (100.0,) * 231 + (160.0 - index * 10,) * 22
        stocks[asset] = StockObservation(
            f"synthetic:{asset}", close, f"industry-{index}", 20e9, 50e6, 100, prices
        )
    source = SnapshotSource((MomentumSnapshot(day, close, tuple(sessions), stocks),))
    strategy = StockMomentumStrategy(source, config)
    rows = []
    for minute in range(4):
        now = datetime(2026, 10, 1, 10, minute, tzinfo=NY)
        for asset in stocks:
            rows.append(
                {
                    "timestamp": now,
                    "asset": asset,
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 100000.0,
                }
            )
    coverage = tuple(
        EarningsCoverage(asset, close, datetime(2027, 1, 1, tzinfo=NY), "synthetic")
        for asset in stocks
    )

    def context(now, asset, _phase):
        return MarketContext(now, 100.0, sector=stocks[asset].industry, instrument="equity")

    engine = stock_momentum_engine(
        DataFeed(prices_df=pl.DataFrame(rows)),
        strategy,
        context,
        InMemoryEarningsProvider(coverage=coverage),
    )
    engine.run()
    print(
        json.dumps(
            {
                "evidence_mode": "synthetic",
                "performance_admission": False,
                "target_weights": dict(strategy.decisions[0].targets),
                "held_shares": {a: p.quantity for a, p in engine.broker.get_positions().items()},
                "settled_cash": engine.broker.settled_cash,
                "synthetic_runtime_fees": engine.broker.fee_statistics()["total_fees"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
