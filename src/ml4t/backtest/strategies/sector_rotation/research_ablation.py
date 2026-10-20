"""Pre-registered, isolated R/S research ablation; not a production or PIT entry.

Risk factor R toggles execution of the existing causal SPY risk tiers (not
the threshold values). Sector factor S toggles whether monthly rank and
eligible universe can update after the first tradable month. Nothing here
relaxes cash settlement, order timing, or corporate action guardrails.
"""

from __future__ import annotations

from datetime import date

from quant_constraints.calendar import NY
from quant_constraints.fees.bridge import FeeCommissionBridge
from quant_constraints.fees.ibkr_fractional_order import IBKRParentOrderFractionalResearch

from .config import SectorRotationConfig
from .research_fractional import (
    ResearchAssumptions,
    RetrospectiveRotationEngine,
    retrospective_rotation_engine,
)
from .research_inputs import ResearchInputs
from .signals import MarketRegime, SignalBook, TotalReturnClose
from .strategy import SectorRotationStrategy


class CausalAblationBook(SignalBook):
    """Disable one causal signal dimension without changing other dimensions."""

    def __init__(
        self,
        config: SectorRotationConfig,
        *,
        freeze_sector_at: date,
        sector_rotation: bool,
        risk_tiers: bool,
    ):
        super().__init__(config)
        self.freeze_sector_at = freeze_sector_at
        self.sector_rotation = sector_rotation
        self.risk_tiers = risk_tiers
        self._frozen_scores: tuple[dict[str, float], frozenset[str]] | None = None
        self.frozen_on: date | None = None
        self.shadow_regimes: list[tuple[date, MarketRegime]] = []
        self._shadow = SignalBook(config) if not risk_tiers else None

    def ingest(self, row: TotalReturnClose) -> None:
        if self._shadow is not None:
            self._shadow.ingest(row)
            self.shadow_regimes.append((row.session, self._shadow.regime))
        super().ingest(row)
        if not self.risk_tiers:
            self.regime = MarketRegime.NORMAL
            self.recovered = False
        # Freeze at ingestion, so read-only oracle calls cannot move the boundary.
        if not self.sector_rotation and row.session == self.freeze_sector_at:
            observed = super().scores()
            if observed is not None:
                scores, eligible = observed
                self._frozen_scores = dict(scores), frozenset(eligible)
                self.frozen_on = row.session

    def scores(self) -> tuple[dict[str, float], frozenset[str]] | None:
        observed = super().scores()
        if self.sector_rotation:
            return observed
        if self.latest is None or self.latest.session < self.freeze_sector_at:
            return observed
        if self._frozen_scores is None and observed is not None:
            scores, eligible = observed
            self._frozen_scores = (dict(scores), frozenset(eligible))
            self.frozen_on = self.latest.session
        if self._frozen_scores is None:
            return None
        frozen, eligible = self._frozen_scores
        return dict(frozen), eligible


def bind_uniform_parent_fees(engine):
    """Use one reservation/actual-fee policy across both P cells before starting."""
    broker = engine.broker
    if isinstance(broker.fee_model, IBKRParentOrderFractionalResearch):
        return
    if engine._has_run or broker.fills or broker.orders:
        raise ValueError("Fee policy must be fixed before any economic event")
    settings = broker.controller.config
    broker.fee_model = IBKRParentOrderFractionalResearch(
        initial_monthly_volume=settings.fee_initial_monthly_volume,
        initial_month=settings.fee_initial_month,
        unknown_venue_per_share=settings.fee_unknown_venue_per_share,
        unknown_venue_rate=settings.fee_unknown_venue_rate,
        history_mode=settings.fee_history_mode,
        snapshot_date=settings.fee_snapshot_date,
    )
    broker.fee_bridge = FeeCommissionBridge(broker, broker.fee_model)
    broker.commission_model = broker.fee_bridge
    broker.gatekeeper.commission_model = broker.fee_bridge
    engine.research_report["fee_reservation_policy"] = "uniform_parent_order_tiered"


def experimental_rotation_engine(
    inputs: ResearchInputs,
    assumptions: ResearchAssumptions,
    settings: SectorRotationConfig,
    *,
    sector_rotation: bool,
    risk_tiers: bool,
) -> RetrospectiveRotationEngine:
    """Create a new engine, replacing *only* its not-yet-started signal book.

    Original sealed strategy, original execution/ledger, and its published
    full A2 study remain untouched. Return an engine for a fresh isolated run.
    """
    if type(sector_rotation) is not bool or type(risk_tiers) is not bool:
        raise ValueError("Ablation switches must be explicit booleans")
    engine = retrospective_rotation_engine(inputs, assumptions, settings)
    assert isinstance(engine.strategy, SectorRotationStrategy)
    bind_uniform_parent_fees(engine)
    if sector_rotation and risk_tiers:
        return engine
    if not inputs.bars:
        raise ValueError("Ablation requires input execution sessions")
    first = inputs.bars[0].end.astimezone(NY).date()
    freeze_at = engine.strategy.book.calendar.shift(first, -1)
    # The first available monthly signal can occur after the execution data start.
    while engine.strategy.book.calendar.shift(freeze_at, 1).month == freeze_at.month:
        freeze_at = engine.strategy.book.calendar.shift(freeze_at, 1)
    if not isinstance(freeze_at, date):
        raise ValueError("No prior session for causal sector freeze")
    engine.strategy.book = CausalAblationBook(
        settings,
        freeze_sector_at=freeze_at,
        sector_rotation=sector_rotation,
        risk_tiers=risk_tiers,
    )
    return engine
