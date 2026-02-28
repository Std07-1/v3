"""
core/smc — SMC Engine (ADR-0024, E1 MVP).

Public exports:
  SmcEngine   — orchestrator (передавати SmcConfig)
  SmcConfig   — конфіг (SmcConfig.from_dict(cfg.get("smc", {})))
  Types       — SmcZone, SmcSwing, SmcLevel, SmcSnapshot, SmcDelta
"""
from core.smc.types import (
    SmcZone,
    SmcSwing,
    SmcLevel,
    SmcSnapshot,
    SmcDelta,
    ZONE_KINDS,
    SWING_KINDS,
    LEVEL_KINDS,
    make_zone_id,
    make_swing_id,
    make_level_id,
)
from core.smc.config import SmcConfig
from core.smc.engine import SmcEngine

__all__ = [
    "SmcEngine",
    "SmcConfig",
    "SmcZone",
    "SmcSwing",
    "SmcLevel",
    "SmcSnapshot",
    "SmcDelta",
    "ZONE_KINDS",
    "SWING_KINDS",
    "LEVEL_KINDS",
    "make_zone_id",
    "make_swing_id",
    "make_level_id",
]
