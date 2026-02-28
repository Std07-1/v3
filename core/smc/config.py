"""
core/smc/config.py — SmcConfig dataclass (ADR-0024 §5.3).

SSOT: config.json:smc  →  SmcConfig.from_dict(cfg["smc"])
S5: параметри алгоритмів тільки з config, без hardcoded thresholds.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, Optional


@dataclasses.dataclass
class SmcObConfig:
    enabled: bool = True
    min_impulse_atr_mult: float = 1.5
    atr_period: int = 14
    max_active_per_side: int = 5
    track_breakers: bool = True

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SmcObConfig":
        return cls(
            enabled=bool(d.get("enabled", True)),
            min_impulse_atr_mult=float(d.get("min_impulse_atr_mult", 1.5)),
            atr_period=int(d.get("atr_period", 14)),
            max_active_per_side=int(d.get("max_active_per_side", 5)),
            track_breakers=bool(d.get("track_breakers", True)),
        )


@dataclasses.dataclass
class SmcFvgConfig:
    enabled: bool = True
    min_gap_atr_mult: float = 0.1
    max_active: int = 10

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SmcFvgConfig":
        return cls(
            enabled=bool(d.get("enabled", True)),
            min_gap_atr_mult=float(d.get("min_gap_atr_mult", 0.1)),
            max_active=int(d.get("max_active", 10)),
        )


@dataclasses.dataclass
class SmcStructureConfig:
    enabled: bool = True
    confirmation_bars: int = 1

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SmcStructureConfig":
        return cls(
            enabled=bool(d.get("enabled", True)),
            confirmation_bars=int(d.get("confirmation_bars", 1)),
        )


@dataclasses.dataclass
class SmcPerformanceConfig:
    max_compute_ms: int = 10
    log_slow_threshold_ms: int = 5

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SmcPerformanceConfig":
        return cls(
            max_compute_ms=int(d.get("max_compute_ms", 10)),
            log_slow_threshold_ms=int(d.get("log_slow_threshold_ms", 5)),
        )


@dataclasses.dataclass
class SmcConfig:
    """Повна конфігурація SMC Engine (SSOT: config.json:smc)."""
    enabled: bool = True
    lookback_bars: int = 500
    swing_period: int = 5
    max_zones_per_tf: int = 30
    ob: SmcObConfig = dataclasses.field(default_factory=SmcObConfig)
    fvg: SmcFvgConfig = dataclasses.field(default_factory=SmcFvgConfig)
    structure: SmcStructureConfig = dataclasses.field(default_factory=SmcStructureConfig)
    performance: SmcPerformanceConfig = dataclasses.field(default_factory=SmcPerformanceConfig)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "SmcConfig":
        """Побудова з config.json["smc"]. d=None → defaults."""
        if not d:
            return cls()
        return cls(
            enabled=bool(d.get("enabled", True)),
            lookback_bars=int(d.get("lookback_bars", 500)),
            swing_period=int(d.get("swing_period", 5)),
            max_zones_per_tf=int(d.get("max_zones_per_tf", 30)),
            ob=SmcObConfig.from_dict(d.get("ob", {})),
            fvg=SmcFvgConfig.from_dict(d.get("fvg", {})),
            structure=SmcStructureConfig.from_dict(d.get("structure", {})),
            performance=SmcPerformanceConfig.from_dict(d.get("performance", {})),
        )

    @property
    def max_compute_ms(self) -> int:
        return self.performance.max_compute_ms
