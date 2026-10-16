"""Independent invented NAV/asset bridges and explicitly inconclusive CAPM summaries."""

import copy

import numpy as np
import pytest

from ml4t.backtest.strategies.sector_rotation.research import retrospective_rotation_engine
from ml4t.backtest.strategies.sector_rotation.research_benchmark import retrospective_spy_benchmark
from tests.strategies.test_rotation_research import ASSUMPTIONS, inputs, settings
from tools import rotation_decision_oracle as o
from tools import rotation_research_replay as u
from tools import rotation_stress_analytics as a


@pytest.fixture
def results():
    source = inputs(bear=True)
    engine = retrospective_rotation_engine(source, ASSUMPTIONS, settings())
    oracle = o.DecisionOracle(engine)
    rotation = u.engine_result(engine)
    return (
        source,
        rotation,
        u.engine_result(retrospective_spy_benchmark(source, ASSUMPTIONS)),
        oracle.finish(),
    )


def test_asset_net_pnl_equals_nav_change_with_fees_dividend_and_split(results):
    source, rotation, _, _ = results
    report = a.portfolio(source, ASSUMPTIONS, rotation)
    assert sum(report["asset_net_pnl_usd"].values()) == pytest.approx(
        rotation["trace"][-1]["NAV"] - float(ASSUMPTIONS.initial_cash), abs=1e-8
    )
    assert len(report["months"]) == 3
    assert all("annualized_daily_volatility_pct" in m for m in report["months"])
    assert report["end_NAV_usd"] == rotation["trace"][-1]["NAV"]
    compounded = np.prod([1 + r["return_pct"] / 100 for r in report["months"]]) - 1
    assert compounded * 100 == pytest.approx(rotation["summary"]["return_pct"])


def test_corrupt_nav_or_fee_breaks_asset_attribution_bridge(results):
    source, rotation, _, _ = results
    modified = copy.deepcopy(rotation)
    modified["fills"][0]["commission"] += 0.01
    with pytest.raises(ValueError, match="ASSET_PNL_NAV_BRIDGE"):
        a.portfolio(source, ASSUMPTIONS, modified)


def test_common_close_attribution_is_not_called_a_funded_trading_strategy(results):
    source, rotation, funded, oracle = results
    report = a.describe(source, ASSUMPTIONS, rotation, funded, oracle)
    matched = report["exposure_matched_attribution"]
    assert matched["purpose"].startswith("EX_POST_ATTRIBUTION_NOT_AN_EXECUTABLE")
    assert matched["first_partial_trading_day_excluded"] is True
    assert [m["month"] for m in matched["monthly"]] == ["2025-10", "2025-11"]
    assert report["regression"]["status"] == "INCONCLUSIVE"
    assert report["regression"]["observations"] == len(report["rotation"]["daily"]) - 1
    assert report["phase_boundaries_were_not_strategy_inputs"] is True
    assert report["performance_admission"] is False
    for row, prior in zip(matched["daily"], report["rotation"]["daily"], strict=False):
        assert row["lagged_actual_A2_exposure"] == prior["exposure"]


def test_capm_recovers_hand_linear_beta_but_short_selected_sample_is_inconclusive():
    market = np.linspace(-0.03, 0.03, 60)
    report = a.regression(0.001 + 1.5 * market, market)
    assert report["beta"] == pytest.approx(1.5)
    assert report["daily_alpha_pct"] == pytest.approx(0.1)
    assert report["status"] == "INCONCLUSIVE"
    assert report["confidence_level"] == 0.95


def test_constant_market_or_short_samples_do_not_produce_nan_claims():
    assert a.regression([0] * 30, [0] * 30)["reason"] == "INSUFFICIENT_OR_CONSTANT_MARKET_SAMPLE"
    assert a.regression([0.01] * 5, [0.02] * 5)["status"] == "INCONCLUSIVE"


def test_decimal_HAC5_matches_an_independent_statsmodels_reference():
    import statsmodels.api as sm

    rng = np.random.default_rng(32768)
    market = rng.normal(0, 0.01, 142)
    noise = rng.normal(0, 0.002, 142)
    noise[1:] += 0.4 * noise[:-1]
    returns = 0.0003 + 0.7 * market + noise
    expected = sm.OLS(returns, sm.add_constant(market)).fit(
        cov_type="HAC", cov_kwds={"maxlags": 5, "use_correction": False}
    )
    report = a.regression(returns, market)
    assert report["beta"] == pytest.approx(expected.params[1], abs=1e-12)
    assert report["beta_95pct_CI"] == pytest.approx(expected.conf_int()[1], abs=1e-12)
    assert report["daily_alpha_95pct_CI_pct"] == pytest.approx(
        expected.conf_int()[0] * 100, abs=1e-12
    )


def test_drawdown_includes_initial_capital_peak():
    assert a.drawdown([90, 95, 80, 120, 108], 100) == pytest.approx(-20)
