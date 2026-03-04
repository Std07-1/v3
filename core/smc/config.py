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
class SmcLevelsConfig:
    """Конфігурація виявлення рівнів ліквідності (ADR-0024 §4.5)."""
    enabled: bool = True
    tolerance_atr_mult: float = 0.1   # ATR для кластеризації
    min_touches: int = 2               # Мінімальна кількість swing в кластері
    max_levels: int = 10               # Макс. рівнів (половина eq_highs, половина eq_lows)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SmcLevelsConfig":
        return cls(
            enabled=bool(d.get("enabled", True)),
            tolerance_atr_mult=float(d.get("tolerance_atr_mult", 0.1)),
            min_touches=int(d.get("min_touches", 2)),
            max_levels=int(d.get("max_levels", 10)),
        )


@dataclasses.dataclass
class SmcPremiumDiscountConfig:
    """§4.6 Premium/Discount Zones — фільтр якості OB/FVG."""
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SmcPremiumDiscountConfig":
        return cls(enabled=bool(d.get("enabled", True)))


@dataclasses.dataclass
class SmcContextStackConfig:
    """ADR-0024c §3.2: Context Stack — cross-TF zone selection."""
    enabled: bool = True
    institutional_budget: int = 1  # Max zones from D1+H4
    intraday_budget: int = 1       # Max zones from H1
    local_budget: int = 2           # Max zones from viewer TF

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SmcContextStackConfig":
        return cls(
            enabled=bool(d.get("enabled", True)),
            institutional_budget=int(d.get("institutional_budget", 1)),
            intraday_budget=int(d.get("intraday_budget", 1)),
            local_budget=int(d.get("local_budget", 2)),
        )


@dataclasses.dataclass
class SmcInducementConfig:
    """§4.7 Inducement / False Breakout Trap detection."""
    enabled: bool = True
    minor_period: int = 3              # Fractal period для minor swings (< swing_period)
    reversal_atr_mult: float = 0.5     # Мінімальний відкат після trap (у ATR)
    confirmation_bars: int = 3         # Вікно підтвердження (барів після trap candle)
    max_inducements: int = 10          # Макс. inducements у snapshot

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SmcInducementConfig":
        return cls(
            enabled=bool(d.get("enabled", True)),
            minor_period=int(d.get("minor_period", 3)),
            reversal_atr_mult=float(d.get("reversal_atr_mult", 0.5)),
            confirmation_bars=int(d.get("confirmation_bars", 3)),
            max_inducements=int(d.get("max_inducements", 10)),
        )


@dataclasses.dataclass
class SmcDisplayConfig:
    """D1: Display filter — proximity + caps before sending to UI."""
    proximity_atr_mult: float = 5.0     # zones/levels within N×ATR of price
    max_display_zones: int = 8          # hard cap after proximity filter
    max_display_levels: int = 6         # hard cap on levels
    max_display_swings: int = 20        # only last N swings

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SmcDisplayConfig":
        return cls(
            proximity_atr_mult=float(d.get("proximity_atr_mult", 5.0)),
            max_display_zones=int(d.get("max_display_zones", 8)),
            max_display_levels=int(d.get("max_display_levels", 6)),
            max_display_swings=int(d.get("max_display_swings", 20)),
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
    max_zones_per_tf: int = 10
    max_zone_height_atr_mult: float = 5.0
    hide_mitigated: bool = False
    # TFs on which SMC is computed (SSOT). Other TFs get cross-TF injection.
    compute_tfs: tuple = (900, 3600, 14400, 86400)  # M15, H1, H4, D1
    # F10: decay params are lifecycle, not display — live at config root
    decay_start_bars: int = 30          # start strength decay after N bars
    decay_fast_bars: int = 150          # aggressive decay threshold
    ob: SmcObConfig = dataclasses.field(default_factory=SmcObConfig)
    fvg: SmcFvgConfig = dataclasses.field(default_factory=SmcFvgConfig)
    structure: SmcStructureConfig = dataclasses.field(default_factory=SmcStructureConfig)
    levels: SmcLevelsConfig = dataclasses.field(default_factory=SmcLevelsConfig)
    premium_discount: SmcPremiumDiscountConfig = dataclasses.field(default_factory=SmcPremiumDiscountConfig)
    inducement: SmcInducementConfig = dataclasses.field(default_factory=SmcInducementConfig)
    context_stack: SmcContextStackConfig = dataclasses.field(default_factory=SmcContextStackConfig)
    display: SmcDisplayConfig = dataclasses.field(default_factory=SmcDisplayConfig)
    performance: SmcPerformanceConfig = dataclasses.field(default_factory=SmcPerformanceConfig)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "SmcConfig":
        """Побудова з config.json["smc"]. d=None → defaults."""
        if not d:
            return cls()
        # F10: decay params — read from root; fallback to display sub-dict for compat
        disp_d = d.get("display", {})
        return cls(
            enabled=bool(d.get("enabled", True)),
            lookback_bars=int(d.get("lookback_bars", 500)),
            swing_period=int(d.get("swing_period", 5)),
            max_zones_per_tf=int(d.get("max_zones_per_tf", 10)),
            max_zone_height_atr_mult=float(d.get("max_zone_height_atr_mult", 5.0)),
            hide_mitigated=bool(d.get("hide_mitigated", False)),
            compute_tfs=tuple(int(x) for x in d.get("compute_tfs", [900, 3600, 14400, 86400])),
            decay_start_bars=int(d.get("decay_start_bars",
                                       disp_d.get("decay_start_bars", 30))),
            decay_fast_bars=int(d.get("decay_fast_bars",
                                      disp_d.get("decay_fast_bars", 150))),
            ob=SmcObConfig.from_dict(d.get("ob", {})),
            fvg=SmcFvgConfig.from_dict(d.get("fvg", {})),
            structure=SmcStructureConfig.from_dict(d.get("structure", {})),
            levels=SmcLevelsConfig.from_dict(d.get("levels", {})),
            premium_discount=SmcPremiumDiscountConfig.from_dict(d.get("premium_discount", {})),
            inducement=SmcInducementConfig.from_dict(d.get("inducement", {})),
            context_stack=SmcContextStackConfig.from_dict(d.get("context_stack", {})),
            display=SmcDisplayConfig.from_dict(disp_d),
            performance=SmcPerformanceConfig.from_dict(d.get("performance", {})),
        )

    @property
    def max_compute_ms(self) -> int:
        return self.performance.max_compute_ms
