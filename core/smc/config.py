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
    tolerance_atr_mult: float = 0.1  # ATR для кластеризації
    min_touches: int = 2  # Мінімальна кількість swing в кластері
    max_levels: int = 10  # Макс. рівнів (половина eq_highs, половина eq_lows)

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
    """§4.6 Premium/Discount Zones — фільтр якості OB/FVG.

    ADR-0041: calc/display decoupled.
      calc_enabled  — engine завжди рахує P/D state (narrative, confluence)
      show_badge    — HUD chip [DISCOUNT 38%] (UI only)
      show_eq_line  — EQ horizontal dashed line (UI only)
      show_zones    — filled rectangles (default OFF — visual clutter)
      eq_pdh_coincidence_atr_mult — D8: hide EQ if |EQ - PDH/PDL| < mult * ATR
    """

    calc_enabled: bool = True
    show_badge: bool = True
    show_eq_line: bool = True
    show_zones: bool = False
    eq_pdh_coincidence_atr_mult: float = 0.5

    # Backward compat: old code reads .enabled
    @property
    def enabled(self) -> bool:
        return self.calc_enabled

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SmcPremiumDiscountConfig":
        # ADR-0041: migrate old {"enabled": bool} → new granular config
        calc = d.get("calc_enabled", d.get("enabled", True))
        return cls(
            calc_enabled=bool(calc),
            show_badge=bool(d.get("show_badge", True)),
            show_eq_line=bool(d.get("show_eq_line", True)),
            show_zones=bool(d.get("show_zones", False)),
            eq_pdh_coincidence_atr_mult=float(
                d.get("eq_pdh_coincidence_atr_mult", 0.5)
            ),
        )


@dataclasses.dataclass
class SmcContextStackConfig:
    """ADR-0024c §3.2: Context Stack — cross-TF zone selection."""

    enabled: bool = True
    institutional_budget: int = 1  # Max zones from D1+H4
    intraday_budget: int = 1  # Max zones from H1
    local_budget: int = 2  # Max zones from viewer TF

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
    minor_period: int = 3  # Fractal period для minor swings (< swing_period)
    reversal_atr_mult: float = 0.5  # Мінімальний відкат після trap (у ATR)
    confirmation_bars: int = 3  # Вікно підтвердження (барів після trap candle)
    max_inducements: int = 10  # Макс. inducements у snapshot

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
class SmcMomentumConfig:
    """Displacement candle detection + momentum scoring."""

    enabled: bool = True
    min_body_atr_mult: float = 1.5  # body >= 1.5 * ATR
    max_wick_ratio: float = 0.3  # wicks < 30% of candle range
    lookback_bars: int = 10  # window for momentum score
    max_display: int = 15  # cap displacement markers

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SmcMomentumConfig":
        return cls(
            enabled=bool(d.get("enabled", True)),
            min_body_atr_mult=float(d.get("min_body_atr_mult", 1.5)),
            max_wick_ratio=float(d.get("max_wick_ratio", 0.3)),
            lookback_bars=int(d.get("lookback_bars", 10)),
            max_display=int(d.get("max_display", 15)),
        )


@dataclasses.dataclass
class SmcDisplayConfig:
    """D1: Display filter — proximity + caps before sending to UI.

    ADR-0028 Φ0: extended with strength gate, mitigated TTL,
    per-side budget, FVG cap, structure label cap.
    """

    proximity_atr_mult: float = 6.0  # zones/levels within N×ATR of price
    max_display_zones: int = 10  # hard cap after proximity filter (research payload)
    max_display_levels: int = 6  # hard cap on levels
    max_display_swings: int = 20  # only last N swings
    max_display_fractals: int = 30  # Williams fractal markers cap
    # ── ADR-0028 Φ0: new fields ──
    min_display_strength: float = (
        0.25  # zones below this strength excluded (decay floor 0.15 ≠ this)
    )
    mitigated_ttl_bars: int = 20  # bars after mitigation before zone is removed
    focus_budget_per_side: int = 3  # max zones per side (supply/demand) in Focus mode
    focus_budget_total: int = 12  # hard cap on ALL SMC objects in Focus mode
    structure_label_max: int = 4  # max structure labels (BOS/CHoCH) in Focus mode
    fvg_display_cap: int = 4  # server-side cap on FVG zones
    fvg_ob_overlap_hide: bool = True  # ADR-0033 SC-2: hide FVG overlapping active OB

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SmcDisplayConfig":
        inst = cls(
            proximity_atr_mult=float(d.get("proximity_atr_mult", 6.0)),
            max_display_zones=int(d.get("max_display_zones", 10)),
            max_display_levels=int(d.get("max_display_levels", 6)),
            max_display_swings=int(d.get("max_display_swings", 20)),
            max_display_fractals=int(d.get("max_display_fractals", 30)),
            min_display_strength=float(d.get("min_display_strength", 0.25)),
            mitigated_ttl_bars=int(d.get("mitigated_ttl_bars", 20)),
            focus_budget_per_side=int(d.get("focus_budget_per_side", 3)),
            focus_budget_total=int(d.get("focus_budget_total", 12)),
            structure_label_max=int(d.get("structure_label_max", 4)),
            fvg_display_cap=int(d.get("fvg_display_cap", 4)),
            fvg_ob_overlap_hide=bool(d.get("fvg_ob_overlap_hide", True)),
        )
        _validate_display_budget(inst)
        return inst


def _validate_display_budget(disp):
    # type: (SmcDisplayConfig) -> None
    """ADR-0028 D3: budget cap validation — loud error on overflow."""
    budget_sum = disp.focus_budget_per_side * 2 + disp.structure_label_max
    if budget_sum > disp.focus_budget_total:
        raise ValueError(
            "[ADR-0028 D3] Budget overflow: "
            "zones(%d*2) + structure(%d) = %d > total(%d)"
            % (
                disp.focus_budget_per_side,
                disp.structure_label_max,
                budget_sum,
                disp.focus_budget_total,
            )
        )


@dataclasses.dataclass
class SmcSessionsConfig:
    """ADR-0035: Trading Sessions & Killzones config (S5: SSOT)."""

    enabled: bool = False
    level_budget_per_session: int = 2
    previous_session_ttl_bars: int = 500
    sweep_lookback_bars: int = 30
    sweep_body_ok: bool = False
    # Session definitions: {name: {open_utc, close_utc, kz_start, kz_end}}
    _definitions: Dict[str, Dict[str, Any]] = dataclasses.field(
        default_factory=dict,
        repr=False,
    )

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SmcSessionsConfig":
        return cls(
            enabled=bool(d.get("enabled", False)),
            level_budget_per_session=int(d.get("level_budget_per_session", 2)),
            previous_session_ttl_bars=int(d.get("previous_session_ttl_bars", 500)),
            sweep_lookback_bars=int(d.get("sweep_lookback_bars", 30)),
            sweep_body_ok=bool(d.get("sweep_body_ok", False)),
            _definitions=dict(d.get("definitions", {})),
        )


@dataclasses.dataclass
class SmcConfluenceConfig:
    """ADR-0029: OB Confluence Scoring thresholds (S5: config-driven)."""

    sweep_lookback_bars: int = 10
    fvg_lookforward_bars: int = 3
    extremum_tolerance_atr: float = 0.3
    strong_impulse_threshold: float = 0.7
    grade_thresholds_a_plus: int = 8
    grade_thresholds_a: int = 6
    grade_thresholds_b: int = 4

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SmcConfluenceConfig":
        gt = d.get("grade_thresholds", {})
        return cls(
            sweep_lookback_bars=int(d.get("sweep_lookback_bars", 10)),
            fvg_lookforward_bars=int(d.get("fvg_lookforward_bars", 3)),
            extremum_tolerance_atr=float(d.get("extremum_tolerance_atr", 0.3)),
            strong_impulse_threshold=float(d.get("strong_impulse_threshold", 0.7)),
            grade_thresholds_a_plus=int(gt.get("a_plus", 8)),
            grade_thresholds_a=int(gt.get("a", 6)),
            grade_thresholds_b=int(gt.get("b", 4)),
        )

    def to_scoring_dict(self):
        # type: () -> Dict[str, Any]
        """Config dict format expected by score_zone_confluence()."""
        return {
            "sweep_lookback_bars": self.sweep_lookback_bars,
            "fvg_lookforward_bars": self.fvg_lookforward_bars,
            "extremum_tolerance_atr": self.extremum_tolerance_atr,
            "strong_impulse_threshold": self.strong_impulse_threshold,
            "grade_thresholds": {
                "a_plus": self.grade_thresholds_a_plus,
                "a": self.grade_thresholds_a,
                "b": self.grade_thresholds_b,
            },
        }


@dataclasses.dataclass
class SmcTdaConfig:
    """ADR-0034: TDA — IFVG (P0) + Breaker (P1). Master toggle disabled by default."""

    enabled: bool = False
    ifvg_enabled: bool = True
    ifvg_max_active: int = 4
    breaker_enabled: bool = True
    breaker_choch_lookback_bars: int = 10

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SmcTdaConfig":
        return cls(
            enabled=bool(d.get("enabled", False)),
            ifvg_enabled=bool(d.get("ifvg_enabled", True)),
            ifvg_max_active=int(d.get("ifvg_max_active", 4)),
            breaker_enabled=bool(d.get("breaker_enabled", True)),
            breaker_choch_lookback_bars=int(d.get("breaker_choch_lookback_bars", 10)),
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
    fractal_period: int = 2
    max_zones_per_tf: int = 10
    max_zone_height_atr_mult: float = 5.0
    hide_mitigated: bool = False
    # TFs on which SMC is computed (SSOT). Other TFs get cross-TF injection.
    compute_tfs: tuple = (900, 3600, 14400, 86400)  # M15, H1, H4, D1
    # F10: decay params are lifecycle, not display — live at config root
    decay_start_bars: int = 30  # start strength decay after N bars
    decay_fast_bars: int = 150  # aggressive decay threshold
    ob: SmcObConfig = dataclasses.field(default_factory=SmcObConfig)
    fvg: SmcFvgConfig = dataclasses.field(default_factory=SmcFvgConfig)
    structure: SmcStructureConfig = dataclasses.field(
        default_factory=SmcStructureConfig
    )
    levels: SmcLevelsConfig = dataclasses.field(default_factory=SmcLevelsConfig)
    premium_discount: SmcPremiumDiscountConfig = dataclasses.field(
        default_factory=SmcPremiumDiscountConfig
    )
    inducement: SmcInducementConfig = dataclasses.field(
        default_factory=SmcInducementConfig
    )
    context_stack: SmcContextStackConfig = dataclasses.field(
        default_factory=SmcContextStackConfig
    )
    display: SmcDisplayConfig = dataclasses.field(default_factory=SmcDisplayConfig)
    momentum: SmcMomentumConfig = dataclasses.field(default_factory=SmcMomentumConfig)
    sessions: SmcSessionsConfig = dataclasses.field(default_factory=SmcSessionsConfig)
    confluence: SmcConfluenceConfig = dataclasses.field(
        default_factory=SmcConfluenceConfig
    )
    performance: SmcPerformanceConfig = dataclasses.field(
        default_factory=SmcPerformanceConfig
    )
    tda: SmcTdaConfig = dataclasses.field(default_factory=SmcTdaConfig)

    # tf_overrides: raw dict from config.json, keyed by str(tf_s)
    _tf_overrides: Dict[str, Dict[str, Any]] = dataclasses.field(
        default_factory=dict,
        repr=False,
    )

    def for_tf(self, tf_s: int) -> "SmcConfig":
        """Return a SmcConfig with per-TF overrides merged (S5 SSOT).

        Shallow merge: top-level scalars replaced, sub-dicts (ob, fvg, …)
        rebuilt from merged dicts.  Returns self unchanged if no override.
        """
        ovr = self._tf_overrides.get(str(tf_s))
        if not ovr:
            return self
        kw: Dict[str, Any] = {}
        # scalar overrides
        for key in (
            "swing_period",
            "fractal_period",
            "lookback_bars",
            "max_zones_per_tf",
            "max_zone_height_atr_mult",
        ):
            if key in ovr:
                kw[key] = type(getattr(self, key))(ovr[key])
        # sub-config overrides (merge base dict + override dict)
        _SUB: Dict[str, Any] = {
            "ob": SmcObConfig,
            "fvg": SmcFvgConfig,
            "structure": SmcStructureConfig,
            "levels": SmcLevelsConfig,
            "inducement": SmcInducementConfig,
        }
        for sub_key, sub_cls in _SUB.items():
            if sub_key in ovr:
                base_d = dataclasses.asdict(getattr(self, sub_key))
                base_d.update(ovr[sub_key])
                kw[sub_key] = sub_cls.from_dict(base_d)
        if not kw:
            return self
        return dataclasses.replace(self, **kw)

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
            fractal_period=int(d.get("fractal_period", 2)),
            max_zones_per_tf=int(d.get("max_zones_per_tf", 10)),
            max_zone_height_atr_mult=float(d.get("max_zone_height_atr_mult", 5.0)),
            hide_mitigated=bool(d.get("hide_mitigated", False)),
            compute_tfs=tuple(
                int(x) for x in d.get("compute_tfs", [900, 3600, 14400, 86400])
            ),
            decay_start_bars=int(
                d.get("decay_start_bars", disp_d.get("decay_start_bars", 30))
            ),
            decay_fast_bars=int(
                d.get("decay_fast_bars", disp_d.get("decay_fast_bars", 150))
            ),
            ob=SmcObConfig.from_dict(d.get("ob", {})),
            fvg=SmcFvgConfig.from_dict(d.get("fvg", {})),
            structure=SmcStructureConfig.from_dict(d.get("structure", {})),
            levels=SmcLevelsConfig.from_dict(d.get("levels", {})),
            premium_discount=SmcPremiumDiscountConfig.from_dict(
                d.get("premium_discount", {})
            ),
            inducement=SmcInducementConfig.from_dict(d.get("inducement", {})),
            context_stack=SmcContextStackConfig.from_dict(d.get("context_stack", {})),
            display=SmcDisplayConfig.from_dict(disp_d),
            momentum=SmcMomentumConfig.from_dict(d.get("momentum", {})),
            sessions=SmcSessionsConfig.from_dict(d.get("sessions", {})),
            confluence=SmcConfluenceConfig.from_dict(d.get("confluence", {})),
            performance=SmcPerformanceConfig.from_dict(d.get("performance", {})),
            tda=SmcTdaConfig.from_dict(d.get("tda", {})),
            _tf_overrides=d.get("tf_overrides", {}),
        )

    @property
    def max_compute_ms(self) -> int:
        return self.performance.max_compute_ms
