"""Explicit real-data historical simulation, never a strict real/PIT entry.

Execution and NAV prices are completed-minute closes; daily aggregates drive
signals under the explicit input close policy. Spread, slippage, taxes, payment-day
credits and the dated fee snapshot are disclosed modeling assumptions. The
canonical constrained broker continues to own all economic state.
"""

from dataclasses import asdict, dataclass, replace
import math
from datetime import datetime, time
from decimal import Decimal
from typing import Any

import polars as pl

from quant_constraints import ConstraintController, InMemoryEarningsProvider, MarketContext
from quant_constraints.adapter import cash_backtest_config
from quant_constraints.calendar import NY
from quant_constraints.corporate_actions import CorporateAction, CorporateActionCoverage
from quant_constraints.corporate_actions.processor import CorporateActionProcessor

from ... import DataFeed, Engine
from ...config import ExecutionPrice, ShareType, SlippageType
from ...execution.limits import VolumeParticipationLimit
from .adapter import SectorRotationBroker, _build_rotation_engine
from .config import SECTORS, SectorRotationConfig
from .research_inputs import ResearchInputs, RetrospectiveCloseSource, number
from .research_ledger import ResearchLedgerMismatch, verify_research_ledger
from .strategy import SectorRotationStrategy


@dataclass(frozen=True)
class ResearchAssumptions:
    name: str
    initial_cash: Decimal
    dividend_tax_rate: Decimal
    modeled_spread: Decimal
    slippage_bps: Decimal
    maximum_participation: Decimal = Decimal("0.01")
    share_step: Decimal = Decimal("1")  # Optional 0.1-share research mode; baseline stays whole shares.

    def __post_init__(self):
        if not self.name.strip():
            raise ValueError("A named research scenario is required")
        for field in (
            "initial_cash",
            "dividend_tax_rate",
            "modeled_spread",
            "slippage_bps",
            "maximum_participation",
            "share_step",
        ):
            object.__setattr__(self, field, number(getattr(self, field)))
        if (
            self.initial_cash <= 0
            or not 0 <= self.dividend_tax_rate <= 1
            or not 0 <= self.modeled_spread <= Decimal("0.0015")
            or not 0 <= self.slippage_bps < 100
            or not 0 < self.maximum_participation <= 1
            or self.share_step not in {Decimal("1"), Decimal("0.1")}
        ):
            raise ValueError("Invalid research assumptions")


class _ResearchProvider:
    def __init__(self, inputs: ResearchInputs, assumptions: ResearchAssumptions):
        self.inputs, self.assumptions = inputs, assumptions
        self.bars = {(b.end, b.asset): b for b in inputs.bars}
        self.securities = {s.asset: s for s in inputs.securities}
        self.start = inputs.bars[0].end.astimezone(NY).date()
        self.end = inputs.bars[-1].end.astimezone(NY).date()
        self.events = []
        for split in inputs.splits:
            if self.start <= split.session <= self.end:
                self.events.append(
                    CorporateAction(
                        self.securities[split.asset].security_id,
                        split.event_id,
                        "SPLIT",
                        split.session,
                        datetime.combine(split.session, time(0), NY),
                        "retrospective-model:" + split.source_id,
                        split_ratio=float(split.new_per_old),
                    )
                )
        for event in inputs.distributions:
            if not self.start <= event.ex_date <= self.end:
                continue
            assert event.classification_source_id is not None
            sid = self.securities[event.asset].security_id
            self.events.append(
                CorporateAction(
                    sid,
                    event.event_id,
                    "CASH_DIVIDEND",
                    event.ex_date,
                    datetime.combine(event.ex_date, time(0), NY),
                    "retrospective-model:" + event.source_id,
                    dividend_per_share=float(event.cash_amount),
                    record_date=event.record_date,
                    payable_date=event.pay_date,
                    distribution_type="ordinary_fund",
                    tax_source_country="US",
                    tax_source=event.classification_source_id,
                    tax_source_known_at=inputs.source_map[
                        event.classification_source_id
                    ].observed_at,
                )
            )
            if event.pay_date <= self.end:
                self.events.append(
                    CorporateAction(
                        sid,
                        "modeled-credit:" + event.event_id,
                        "CASH_CREDIT",
                        event.pay_date,
                        datetime.combine(event.pay_date, time(0), NY),
                        "MODELED_PAYMENT_DATE_NOT_BROKER_CONFIRMATION:" + event.source_id,
                        parent_event_id=event.event_id,
                    )
                )
        self.events = tuple(self.events)

    def snapshot(self, security_id, asof):
        # This is explicitly false. The production processor must reject it.
        coverage = CorporateActionCoverage(
            security_id,
            self.inputs.observed_at,
            self.start,
            self.end,
            "retrospective-model:retained-inputs",
            point_in_time=False,
        )
        return coverage, tuple(
            e for e in self.events if e.security_id == security_id and e.available_at <= asof
        )

    def context(self, stamp, asset, phase):
        bar = self.bars.get((stamp, asset))
        if bar is None:
            raise ValueError("Missing completed research minute bar")
        security = self.securities[asset]
        source = self.inputs.source_map[security.source_id]
        return MarketContext(
            stamp,
            float(bar.close),
            phase=phase,
            instrument="plain_sector_etf",
            sector=SECTORS.get(asset, "market"),
            # Explicit scenario inputs, not fabricated observed bid/ask quotes.
            spread=float(self.assumptions.modeled_spread),
            volume=float(bar.volume),
            liquidity_available_at=stamp,
            security_id=security.security_id,
            execution_data_mode="raw_execution",
            risk_data_mode="raw_execution",
            signal_data_mode="total_return_signal",
            issuer_country=security.issuer_country,
            tax_source_country=security.tax_source_country,
            security_type=security.security_type,
            is_depositary_receipt=security.is_depositary_receipt,
            security_metadata_available_at=stamp,
            security_metadata_source=source.url,
            security_metadata_sha256=source.sha256,
            security_metadata_symbol=asset,
            security_metadata_security_id=security.security_id,
        )


class _ResearchActions(CorporateActionProcessor):
    def _validate_coverage(self, coverage, sid, asof, start, day):
        provider = self.provider
        if (
            not isinstance(self.broker, _ResearchBroker)
            or not isinstance(provider, _ResearchProvider)
            or self.broker.research_provider is not provider
            or coverage is None
            or coverage.point_in_time is not False
            or coverage.missing
            or coverage.security_id != sid
            or coverage.available_at != provider.inputs.observed_at
            or coverage.covered_from > start
            or coverage.covered_until < day
        ):
            raise ValueError("Unbound retrospective corporate action coverage")

    def _validate_dividend_tax(self, event, asset, asof):
        self._tax_rate(event, asof)

    def _tax_rate(self, event, asof):
        provider = self.provider
        if not isinstance(provider, _ResearchProvider) or (
            event.distribution_type != "ordinary_fund"
            or event.currency != "USD"
            or event.tax_source_country != "US"
            or event.terms != "ordinary"
        ):
            raise ValueError("Unsupported research distribution")
        return float(provider.assumptions.dividend_tax_rate), {
            "basis": "explicit_research_tax_scenario",
            "name": provider.assumptions.name,
            "investor_eligibility_verified": False,
            "historical_PIT_verified": False,
            "classification_observed_at": event.tax_source_known_at.isoformat()
            if event.tax_source_known_at
            else None,
        }

    def _credit_requires_confirmation(self):
        return False  # Only this isolated simulator models a payable-date credit.

    def credit(self, event, record):
        parent = event.security_id, event.parent_event_id
        if (
            parent not in self.state["entitlements"]
            and parent in self.state["processed"]
            and self.state["scopes"].get(parent) == "observed_only"
        ):
            record.update(
                status="modeled_no_entitlement_credit", parent_event_id=event.parent_event_id
            )
            return
        super().credit(event, record)

    def apply(self, asof, observed, prepared):
        previous = len(self.state["records"])
        super().apply(asof, observed, prepared)
        provider = self.provider
        assert isinstance(provider, _ResearchProvider)
        for record in self.state["records"][previous:]:
            record.update(
                evidence_mode="retrospective_research",
                source_observed_at=provider.inputs.observed_at.isoformat(),
                event_available_at_is_modeled=True,
                actual_broker_confirmation=False,
            )


def _share_step_floor(quantity: float, step: Decimal) -> float:
    """Round absolute order/fill sizes DOWN to a supported research share increment."""
    if not math.isfinite(quantity):
        raise ValueError("Non-finite research share quantity")
    unit = float(step)
    units = math.floor(abs(quantity) / unit + 1e-8)
    # Decimal conversion prevents 0.1 binary drift from reaching the order journal.
    return float(Decimal(units) * step)


class _ResearchParticipationLimit(VolumeParticipationLimit):
    """Apply the same step to partial fills; never round a volume cap upward."""

    def __init__(self, max_participation: float, share_step: Decimal):
        super().__init__(max_participation)
        self.share_step = share_step

    def calculate(self, order_quantity, bar_volume, price):
        if bar_volume is None:
            raise ValueError("Retrospective fractional fill needs observed volume")
        result = super().calculate(order_quantity, bar_volume, price)
        executable = _share_step_floor(result.fillable_quantity, self.share_step)
        return replace(
            result,
            fillable_quantity=executable,
            remaining_quantity=max(0.0, order_quantity - executable),
            participation_rate=executable / bar_volume if bar_volume > 0 else 0.0,
        )


class _ResearchBroker(SectorRotationBroker):
    research_provider: _ResearchProvider
    research_quantity_step: Decimal = Decimal("1")

    def submit_order(self, asset, quantity, *args, **kwargs):
        # Constrain ALL research entries/rebalances/risk exits, not just target sizing.
        if self.research_quantity_step != 1:
            size = _share_step_floor(quantity, self.research_quantity_step)
            if not size:
                return None
            quantity = math.copysign(size, quantity)
        return super().submit_order(asset, quantity, *args, **kwargs)

    def _process_orders(self, *args, **kwargs):
        # The canonical NEXT_BAR lifecycle supplies use_open=True. Here each
        # input event is a completed minute, so both prechecks and actual fills
        # must use that minute's close, not its earlier open or a stale trigger.
        if args:
            args = (False, *args[1:])
            kwargs.pop("use_open", None)
        else:
            kwargs["use_open"] = False
        for order in self._order_state.pending:
            order._risk_fill_price = None
        return super()._process_orders(*args, **kwargs)

    def configure_constraints(
        self, controller, context_provider, config, fee_model=None, corporate_action_provider=None
    ):
        if not isinstance(corporate_action_provider, _ResearchProvider):
            raise ValueError("Research broker requires its bound input provider")
        self.research_provider = corporate_action_provider
        self.research_quantity_step = corporate_action_provider.assumptions.share_step
        super().configure_constraints(
            controller, context_provider, config, fee_model, corporate_action_provider
        )
        self.corporate_action_processor = _ResearchActions(self, corporate_action_provider)


class RetrospectiveRotationEngine(Engine):
    """Canonical Engine plus immutable-input identity and read-only ledger checks."""

    research_inputs: ResearchInputs
    research_assumptions: ResearchAssumptions
    research_trace: list[dict[str, Any]]
    research_report: dict[str, Any]

    def _record_portfolio_state(self, timestamp):
        super()._record_portfolio_state(timestamp)
        self.research_trace.append(
            {
                "timestamp": timestamp,
                "cash": self.broker.cash,
                "settled_cash": self.broker.account.settled_cash,
                "receivables": self.broker.account._receivable_value,
                "NAV": self.equity_curve[-1][1],
                "positions": {a: p.quantity for a, p in self.broker.positions.items()},
            }
        )

    def run(self):
        if self._has_run:
            raise RuntimeError("Create a fresh retrospective Engine for each run")
        self.research_report["engine_invoked"] = True
        try:
            result = super().run()
            assert isinstance(self.broker, _ResearchBroker)
            ledger = verify_research_ledger(
                self.research_inputs,
                self.research_assumptions,
                tuple(self.broker.fills),
                tuple(self.broker.orders),
                self.broker.fee_records,
                self.research_trace,
            )
        except BaseException:
            self.research_report["status"] = "RETROSPECTIVE_RUN_FAILED"
            raise
        if not self.broker.fills:
            self.research_report["status"] = "RETROSPECTIVE_NO_EXECUTIONS"
            raise ResearchLedgerMismatch("No executions; cannot accept an empty economic replay")
        self.research_report.update(status=ledger["status"], independent_ledger=ledger)
        return result


def retrospective_rotation_engine(
    inputs: ResearchInputs,
    assumptions: ResearchAssumptions,
    settings: SectorRotationConfig | None = None,
) -> RetrospectiveRotationEngine:
    """Build a separately labeled historical simulation with no strict admission.

    Configurable one-share or 0.1-share steps, 2026 fee snapshot backcast, next completed-minute
    close plus explicit slippage, and modeled payment-day credit are fixed here.
    """
    if not isinstance(inputs, ResearchInputs) or not isinstance(assumptions, ResearchAssumptions):
        raise ValueError("Typed inputs and explicit assumptions required")
    settings = settings or SectorRotationConfig()
    source = RetrospectiveCloseSource(inputs)
    first = inputs.bars[0].end.astimezone(NY).date()
    prehistory = [row for row in source.closes if row.session < first]
    required = max(max(settings.lookbacks) + 1, settings.long_ma, settings.peak_lookback)
    if len(prehistory) < required:
        raise ValueError("Insufficient raw-close warm-up for the configured strategy")
    provider = _ResearchProvider(inputs, assumptions)
    strategy = SectorRotationStrategy(source, settings)
    constraints = replace(
        settings.constraints(corporate_actions_enabled=True),
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
    config = cash_backtest_config(
        initial_cash=float(assumptions.initial_cash),
        share_type=ShareType.INTEGER if assumptions.share_step == 1 else ShareType.FRACTIONAL,
        execution_price=ExecutionPrice.CLOSE,
        slippage_type=SlippageType.PERCENTAGE,
        slippage_rate=float(assumptions.slippage_bps / 10000),
    )
    engine = _build_rotation_engine(
        DataFeed(prices_df=prices),
        strategy,
        provider.context,
        config,
        controller,
        provider,
        None,
        broker_type=_ResearchBroker,
        engine_type=RetrospectiveRotationEngine,
    )
    assert isinstance(engine, RetrospectiveRotationEngine)
    engine.broker.execution_limits = (
        VolumeParticipationLimit(float(assumptions.maximum_participation))
        if assumptions.share_step == 1
        else _ResearchParticipationLimit(float(assumptions.maximum_participation), assumptions.share_step)
    )
    engine.research_inputs, engine.research_assumptions = inputs, assumptions
    engine.research_trace = []
    engine.research_report = {
        "schema": "rotation_retrospective_engine_v2"
        if inputs.bundle_schema.endswith("v2")
        else "rotation_retrospective_engine_v1",
        "status": "NOT_RUN",
        "evidence_mode": "retrospective_research",
        "data_origin": inputs.origin,
        "input_fingerprint": inputs.fingerprint,
        "source_observed_at": inputs.observed_at.isoformat(),
        "sources": [
            {
                "id": s.identifier,
                "sha256": s.sha256,
                "observed_at": s.observed_at.isoformat(),
                "url": s.url,
            }
            for s in inputs.sources
        ],
        "assumptions": {k: str(v) for k, v in asdict(assumptions).items()},
        "execution": "next_completed_minute_close_plus_scenario_slippage",
        "price_conventions": inputs.close_comparison,
        "fee_mode": "2026-10-10_normal_charge_snapshot_backcast_unknown_venue",
        "identity_mode": "fixed_domestic_SPDR_retrospective_metadata_not_PIT_attestation",
        "credit_mode": "modeled_pay_date_not_actual_broker_confirmation",
        "warmup_sessions": len(prehistory),
        "engine_invoked": False,
        "PIT_verified": False,
        "raw_verified": False,
        "archive_ready": False,
        "trusted_entry": False,
        "may_build_engine": False,
        "performance_admission": False,
        "full_real_scenarios_accepted": 0,
        "investor_tax_eligibility_verified": False,
    }
    return engine
