"""Invented sources and deliberately corrupted decisions; no real/PIT acceptance."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from ml4t.backtest.strategies.sector_rotation.research import retrospective_rotation_engine
from tests.strategies.test_rotation_research import ASSUMPTIONS, inputs, settings
from tools import rotation_decision_oracle as o


@pytest.mark.parametrize("bear", [False, True])
def test_actual_engine_decisions_and_submitted_intents_match_independent_reference(bear):
    engine = retrospective_rotation_engine(inputs(bear=bear), ASSUMPTIONS, settings())
    oracle = o.DecisionOracle(engine)
    engine.run()
    report = oracle.finish()
    assert report["status"] == "CONDITIONAL_DECISION_AND_SUBMITTED_INTENT_ORACLE_PASS"
    assert report["monthly_decisions"] == 2
    assert report["submitted_order_intents"] > 0
    assert report["conditioned_on_verified_past_engine_ledger"] is True
    assert report["independent_fill_simulator"] is False
    assert report["all_unsubmitted_opportunities_verified"] is False
    if bear:
        assert "deep_bear" in report["observed_regimes"] and report["risk_calls"] > 0


def test_breakpoint_weights_match_hand_capped_allocation():
    result = o.bounded_weights({"A": 0, "B": 1, "C": 2}, Decimal(".60"), ".15", ".25")
    assert result == {"A": Decimal(".15"), "B": Decimal(".20"), "C": Decimal(".25")}
    equal = o.bounded_weights({"A": 0, "B": 0}, ".45", ".15", ".25", upper={"A": ".20", "B": ".25"})
    assert equal == {"A": Decimal(".20"), "B": Decimal(".25")}


def test_independent_reference_uses_original_raw_share_events():
    source = inputs()
    rows = o.reference_signals(source, settings())
    day = date(2025, 10, 8)
    asset = source.splits[0].asset
    prior = max(d for d in rows if d < day)
    prices = {(r.session, r.asset): r.close for r in source.closes}
    expected = rows[prior]["indices"][asset] * 2 * prices[day, asset] / prices[prior, asset]
    assert abs(rows[day]["indices"][asset] - expected) < Decimal("1E-25")


def test_fee_reservation_oracle_is_independent_of_production_helpers(monkeypatch):
    def deny(*_args, **_kwargs):
        pytest.fail("No production fee helper allowed")

    monkeypatch.setattr("quant_constraints.fees.ibkr_tiered.IBKRProTieredUSStock.estimate", deny)
    q, p = Decimal("2.5"), Decimal(100)
    broker = Decimal(".35") + Decimal(".5")
    expected = (
        broker * Decimal("1.000735")
        + q * Decimal(".0035")
        + q * Decimal(".0002")
        + q * Decimal(".000003")
    )
    assert o.fee_estimate(q, p) == expected


def test_future_source_changes_cannot_change_earlier_reference():
    source = inputs()
    modified = replace(
        source,
        closes=tuple(
            replace(r, close=r.close * 10) if r.session >= date(2025, 11, 3) else r
            for r in source.closes
        ),
        bars=tuple(
            replace(b, open=b.open * 10, high=b.high * 10, low=b.low * 10, close=b.close * 10)
            if b.end.date() >= date(2025, 11, 3)
            else b
            for b in source.bars
        ),
    )
    first, second = (
        o.reference_signals(source, settings()),
        o.reference_signals(modified, settings()),
    )
    assert {d: v for d, v in first.items() if d < date(2025, 11, 3)} == {
        d: v for d, v in second.items() if d < date(2025, 11, 3)
    }


def test_corrupt_signal_score_is_not_certified_by_matching_cash_ledger():
    engine = retrospective_rotation_engine(inputs(), ASSUMPTIONS, settings())
    original = engine.strategy.book.scores

    def corrupt():
        value = original()
        if value:
            scores, eligible = value
            scores[next(iter(scores))] += 0.1
            return scores, eligible
        return value

    engine.strategy.book.scores = corrupt
    o.DecisionOracle(engine)
    with pytest.raises(o.DecisionOracleMismatch, match="DECISION_ORACLE"):
        engine.run()


def test_missing_monthly_decision_is_rejected_even_without_corrupted_fills():
    engine = retrospective_rotation_engine(inputs(), ASSUMPTIONS, settings())
    engine.strategy._stage_monthly = lambda *_: None
    o.DecisionOracle(engine)
    with pytest.raises(o.DecisionOracleMismatch, match="MONTHLY_DECISION_COUNT"):
        engine.run()


def test_corrupted_execution_targets_are_rejected():
    engine = retrospective_rotation_engine(inputs(), ASSUMPTIONS, settings())
    original = engine.strategy._execution_targets

    def corrupt(*args):
        result = original(*args)
        for asset in result:
            result[asset] -= 0.01
        return result

    engine.strategy._execution_targets = corrupt
    oracle = o.DecisionOracle(engine)
    # An audit mismatch is not an ordinary allocation infeasibility.
    with pytest.raises(o.DecisionOracleMismatch, match="DECISION_ORACLE"):
        engine.run()
        oracle.finish()


def changed_prices(source, price):
    closes = tuple(replace(r, close=price(r.asset, r.session, r.close)) for r in source.closes)
    bars = []
    for b in source.bars:
        value = price(b.asset, b.end.date(), b.close)
        bars.append(replace(b, open=value, high=value, low=value, close=value))
    return replace(source, closes=closes, bars=tuple(bars))


def test_bear_downgrade_and_month_end_recovery_have_independent_states():
    def price(asset, day, old):
        if asset == "SPY" and day >= date(2025, 10, 20):
            return Decimal(120 + (day - date(2025, 10, 20)).days * 4)
        return old

    source = changed_prices(inputs(bear=True), price)
    engine = retrospective_rotation_engine(source, ASSUMPTIONS, settings())
    oracle = o.DecisionOracle(engine)
    engine.run()
    report = oracle.finish()
    assert oracle.reference[date(2025, 10, 9)]["regime"] == "deep_bear"
    assert oracle.reference[date(2025, 10, 31)]["recovered"] is True
    assert "normal" in report["observed_regimes"] and "deep_bear" in report["observed_regimes"]


def test_monthly_replacement_independently_checks_settlement_funding_blocks():
    def price(asset, day, old):
        if asset != "SPY" and day >= date(2025, 10, 30):
            return old * (
                Decimal("1.05") if asset in {"XLB", "XLC", "XLE", "XLF", "XLI"} else Decimal(".99")
            )
        return old

    source = changed_prices(inputs(end="2025-11-10"), price)
    engine = retrospective_rotation_engine(source, ASSUMPTIONS, settings())
    oracle = o.DecisionOracle(engine)
    engine.run()
    report = oracle.finish()
    assert report["settlement_blocks"] > 0
    assert all(row["unsettled_cash"] > 0 for row in report["cash_blocks"])
