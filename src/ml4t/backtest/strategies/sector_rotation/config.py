"""Explicit A2 research settings; defaults are hypotheses, not fitted parameters."""

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

from quant_constraints import ConstraintConfig

SECTOR_ETFS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
SECTORS = dict(
    zip(
        SECTOR_ETFS,
        (
            "materials",
            "communication",
            "energy",
            "financials",
            "industrials",
            "technology",
            "staples",
            "real_estate",
            "utilities",
            "health_care",
            "discretionary",
        ),
        strict=True,
    )
)


@dataclass(frozen=True)
class SectorRotationConfig:
    lookbacks: tuple[int, int, int] = (63, 126, 252)
    momentum_weights: tuple[float, float, float] = (0.5, 0.3, 0.2)
    short_ma: int = 50
    long_ma: int = 200
    peak_lookback: int = 252
    down_confirm: int = 5
    recovery_confirm: int = 10
    trend_buffer: float = 0.02
    deep_drawdown: float = 0.20
    min_weight: float = 0.15
    max_weight: float = 0.25
    normal_exposure: float = 0.95
    warning_exposure: float = 0.70
    bear_exposure: float = 0.45
    deep_exposure: float = 0.20
    rank_buffer: int = 2
    execution_start_minutes: int = 5
    execution_end_minutes: int = 15
    plan_sessions: int = 3
    execution_cushion_bps: float = 5.0

    def __post_init__(self):
        import math

        for name in ("lookbacks", "momentum_weights"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        for name in (
            "short_ma",
            "long_ma",
            "peak_lookback",
            "down_confirm",
            "recovery_confirm",
            "plan_sessions",
            "execution_start_minutes",
            "execution_end_minutes",
            "rank_buffer",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == "rank_buffer" else 1):
                raise ValueError(f"Invalid {name}")
        if len(self.lookbacks) != 3 or any(type(n) is not int or n < 1 for n in self.lookbacks):
            raise ValueError("Three positive integer lookbacks are required")
        if tuple(sorted(set(self.lookbacks))) != self.lookbacks:
            raise ValueError("Lookbacks must be strictly increasing")
        for name in (
            "trend_buffer",
            "deep_drawdown",
            "min_weight",
            "max_weight",
            "normal_exposure",
            "warning_exposure",
            "bear_exposure",
            "deep_exposure",
            "execution_cushion_bps",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
            ):
                raise ValueError(f"Invalid {name}")
        if (
            len(self.momentum_weights) != 3
            or any(
                isinstance(w, bool)
                or not isinstance(w, int | float)
                or not math.isfinite(w)
                or w < 0
                for w in self.momentum_weights
            )
            or not math.isclose(sum(self.momentum_weights), 1)
        ):
            raise ValueError("Momentum weights must be finite, nonnegative and sum to one")
        if not 1 <= self.short_ma < self.long_ma:
            raise ValueError("short_ma must be smaller than long_ma")
        if not 0 < self.min_weight < self.max_weight <= 1:
            raise ValueError("Invalid weight bounds")
        if not 0 <= self.trend_buffer < 1 or not 0 < self.deep_drawdown < 1:
            raise ValueError("Invalid market thresholds")
        if not 0 <= self.execution_cushion_bps < 100:
            raise ValueError("Invalid execution cushion")
        if self.execution_start_minutes >= self.execution_end_minutes:
            raise ValueError("Execution window must have positive length")
        exposures = (
            self.normal_exposure,
            self.warning_exposure,
            self.bear_exposure,
            self.deep_exposure,
        )
        if not 1 > exposures[0] > exposures[1] > exposures[2] > exposures[3] > 0:
            raise ValueError("Exposure tiers must decrease")
        for exposure, count in zip(exposures, (5, 4, 2, 1), strict=True):
            if not count * self.min_weight <= exposure <= count * self.max_weight:
                raise ValueError("Exposure tier is infeasible with its name count")

    @property
    def history_sessions(self) -> int:
        return max(max(self.lookbacks) + 1, self.long_ma, self.peak_lookback)

    def constraints(self, **changes) -> ConstraintConfig:
        """Compatible cash-account profile; existing defaults remain untouched."""
        settings: dict[str, Any] = {
            "cash_reserve": 1 - self.normal_exposure,
            "entry_delay_minutes": self.execution_start_minutes,
            "entry_close_buffer_minutes": 0,
            "min_target_weight": self.min_weight,
            "max_weight": self.max_weight,
            "preferred_min_names": 5,
            "max_names": 5,
            "equity_target": self.normal_exposure,
            "allow_defensive_underinvested": True,
            "industry_cap": self.max_weight,
            "rebalance_mode": "monthly",
            "monthly_session": "first",
            "rebalance_plan_enabled": True,
            "rebalance_plan_sessions": self.plan_sessions,
            "weight_change_threshold": 0.0,
            "weekly_entries": 11,
            "drift_reduction": "next_session",
            "signal_data_mode": "total_return_signal",
        }
        settings.update(changes)
        return ConstraintConfig(**settings)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SectorRotationConfig":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Sector rotation configuration must be a mapping")
        unknown = raw.keys() - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown sector rotation settings: {sorted(unknown)}")
        return cls(**raw)
