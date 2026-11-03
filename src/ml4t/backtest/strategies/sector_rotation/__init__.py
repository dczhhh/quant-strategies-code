"""SPDR A2 strategy, isolated from the signal-free account constraint layer."""

from .adapter import SectorRotationBroker, sector_rotation_engine
from .allocation import strength_weights
from .config import SECTOR_ETFS, SECTORS, SectorRotationConfig
from .signals import CausalCloseSource, MarketRegime, TotalReturnClose
from .strategy import SectorRotationStrategy

__all__ = [
    "SECTOR_ETFS",
    "SECTORS",
    "CausalCloseSource",
    "MarketRegime",
    "SectorRotationBroker",
    "SectorRotationConfig",
    "SectorRotationStrategy",
    "TotalReturnClose",
    "sector_rotation_engine",
    "strength_weights",
]
