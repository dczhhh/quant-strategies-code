"""Funded SPY reference using the research broker, Engine and independent ledger.

This is a passive 95%-initial-investment comparator, not an exposure-matched
portfolio or a strict real/PIT entry. Subsequent price drift is left alone and
net distributions remain cash. No sector-rotation parameters are changed.
"""

from dataclasses import asdict, replace
from datetime import datetime
from typing import Any

import polars as pl

from quant_constraints import ConstraintController, InMemoryEarningsProvider, Intent, Kind
from quant_constraints.adapter import cash_backtest_config
from quant_constraints.calendar import NY

from ... import DataFeed
from ...config import ExecutionPrice, ShareType, SlippageType
from ...execution.limits import VolumeParticipationLimit
from .config import SectorRotationConfig
from .research import (
    ResearchAssumptions,
    RetrospectiveRotationEngine,
    _ResearchBroker,
    _ResearchParticipationLimit,
    _ResearchProvider,
    _share_step_floor,
)
from .research_inputs import ResearchInputs, RetrospectiveCloseSource
from .strategy import SectorRotationStrategy

BENCHMARK_POLICY = {
    "name": "SPY_95pct_initial_buy_and_hold_net_cash_distributions",
    "initial_target_weight": "0.95",
    "entry": "first_execution_session_0935_decision_next_completed_minute_close",
    "entry_validity": "three_trading_sessions_no_retry_after_expiry",
    "execution_cushion_bps": "5",
    "subsequent_rebalance": False,
    "distribution_reinvestment": False,
    "terminal_liquidation": False,
    "cash_interest": "0",
    "valuation": "final_completed_RTH_minute_close_including_net_receivables",
    "costs_and_tax": "same_research_scenario_and_independent_ledger_as_rotation",
    "single_fund_cap": "1",
    "exposure_matched": False,
    "historical_PIT_verified": False,
}


class _PassiveSPY(SectorRotationStrategy):
    """Use the bound window adapter, without sector signals or risk timing."""

    def __init__(self, inputs: ResearchInputs):
        super().__init__(RetrospectiveCloseSource(inputs), SectorRotationConfig())
        self.attempted = False

    def _before_execution(self, timestamp: datetime, broker: Any) -> None:
        # The broker retains canonical order validity, cash, fees and actions.
        # A passive reference has no sector selection or tactical risk signal.
        pass

    def on_data(self, timestamp: datetime, data: dict, context: dict, broker: Any) -> None:
        if self.attempted or not broker.rotation_window(timestamp):
            return
        self.attempted = True
        state = broker.constraint_state()
        if state.holdings or state.missing_marks:
            raise ValueError("Passive reference requires an empty, marked initial portfolio")
        price = broker.get_last_price("SPY")
        if price is None or price <= 0:
            raise ValueError("Passive reference requires an observed SPY price")
        estimated = broker.estimate_execution_price("SPY", 1, price)
        # Same five-basis-point execution cushion as the rotation defaults.
        budget = state.equity * (0.95 - self.config.execution_cushion_bps / 10000)
        quantity = _share_step_floor(budget / estimated, broker.research_quantity_step)
        while quantity > 0:
            fees = broker.estimate_order_fees("SPY", quantity, estimated)
            if quantity * estimated + fees <= budget:
                break
            quantity = _share_step_floor(
                quantity - float(broker.research_quantity_step), broker.research_quantity_step
            )
        if quantity <= 0:
            if broker.research_quantity_step == 1:
                raise ValueError("Passive reference initial cash cannot fund one whole SPY share")
            raise ValueError("Passive reference initial cash cannot fund 0.1 SPY share")
        calendar = broker.controller.calendar
        last = calendar.session(calendar.shift(timestamp.astimezone(NY).date(), 2))
        assert last is not None
        broker.submit_intent(
            Intent(
                "SPY",
                quantity,
                kind=Kind.ENTRY,
                target_weight=quantity * price / state.equity,
                valid_until=last.market_close,
                time_in_force="GTD",
            )
        )


def retrospective_spy_benchmark(
    inputs: ResearchInputs, assumptions: ResearchAssumptions
) -> RetrospectiveRotationEngine:
    """Run SPY on the same observations, funding, tax and execution assumptions.

    A single diversified-fund comparator permits one SPY position up to 100%
    and disables sector-cap drift sales. Its initial order targets 95% before
    costs/cushion. These explicit portfolio differences apply only here.
    """
    if not isinstance(inputs, ResearchInputs) or not isinstance(assumptions, ResearchAssumptions):
        raise ValueError("Typed inputs and explicit assumptions required")
    strategy = _PassiveSPY(inputs)
    provider = _ResearchProvider(inputs, assumptions)
    constraints = replace(
        strategy.config.constraints(corporate_actions_enabled=True),
        min_target_weight=0.15,
        max_weight=1.0,
        industry_cap=1.0,
        preferred_min_names=1,
        max_names=1,
        drift_reduction="disabled",
        max_defer_sessions=3,
        slippage_mode="configured",
        pricing_plan="ibkr_pro_tiered",
        dividend_tax_profile="retrospective_scenario",
        dividend_tax_scenario=assumptions.name,
        dividend_tax_qualification=None,
        fee_initial_monthly_volume=0,
        fee_initial_month=None,
        fee_unknown_venue_per_share=0.0035,
        fee_unknown_venue_rate=0.0035,
        fee_history_mode="current_snapshot_backcast",
    )
    controller = ConstraintController(constraints, InMemoryEarningsProvider())
    config = cash_backtest_config(
        initial_cash=float(assumptions.initial_cash),
        share_type=ShareType.INTEGER if assumptions.share_step == 1 else ShareType.FRACTIONAL,
        execution_price=ExecutionPrice.CLOSE,
        slippage_type=SlippageType.PERCENTAGE,
        slippage_rate=float(assumptions.slippage_bps / 10000),
    )
    prices = pl.DataFrame(
        [
            {
                "timestamp": b.end,
                "asset": b.asset,
                **{k: float(getattr(b, k)) for k in ("open", "high", "low", "close", "volume")},
            }
            for b in inputs.bars
        ]
    )

    def factory(backtest_config, **kwargs):
        broker = _ResearchBroker.from_config(backtest_config, **kwargs)
        assert isinstance(broker, _ResearchBroker)
        broker.configure_constraints(controller, provider.context, backtest_config, None, provider)
        broker.bind_rotation(strategy)
        broker.execution_limits = (
            VolumeParticipationLimit(float(assumptions.maximum_participation))
            if assumptions.share_step == 1
            else _ResearchParticipationLimit(
                float(assumptions.maximum_participation), assumptions.share_step
            )
        )
        return broker

    engine = RetrospectiveRotationEngine(
        DataFeed(prices_df=prices), strategy, config, broker_factory=factory
    )
    engine.research_inputs, engine.research_assumptions = inputs, assumptions
    engine.research_trace = []
    engine.research_report = {
        "schema": "rotation_passive_spy_engine_v1",
        "status": "NOT_RUN",
        "evidence_mode": "retrospective_research",
        "data_origin": inputs.origin,
        "input_fingerprint": inputs.fingerprint,
        "benchmark_policy": dict(BENCHMARK_POLICY),
        "assumptions": {k: str(v) for k, v in asdict(assumptions).items()},
        "execution": "next_completed_minute_close_plus_scenario_slippage",
        "fee_mode": "2026-10-10_normal_charge_snapshot_backcast_unknown_venue",
        "credit_mode": "modeled_pay_date_not_actual_broker_confirmation",
        "price_conventions": inputs.close_comparison,
        "source_observed_at": inputs.observed_at.isoformat(),
        "engine_invoked": False,
        "PIT_verified": False,
        "actual_broker_execution_verified": False,
        "performance_admission": False,
        "full_real_scenarios_accepted": 0,
    }
    return engine
