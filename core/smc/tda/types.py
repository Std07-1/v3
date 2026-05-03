"""
core/smc/tda/types.py — Канонічні типи TDA Cascade Signal Engine (ADR-0040).

4-stage daily cascade: D1 Macro → H4 Confirm → Session Narrative → M15 FVG Entry.
Trade management: Config F (partial 50% @1R + trail remaining).

Інваріанти:
  S0: чисті dataclasses, NO I/O
  S2: детерміновані — same input → same output
  S3: signal_id = "tda_{symbol}_{date}" — детермінований
  S5: config-driven, thresholds з config.json:smc.tda_cascade
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, Optional, Tuple

# ═══════════════════════════════════════════════════════
# Vocabulary constants (SSOT)
# ═══════════════════════════════════════════════════════

MACRO_DIRECTIONS = frozenset({"BULL", "BEAR", "CFL"})
MACRO_METHODS = frozenset({"pivot", "slope"})
MACRO_CONFIDENCES = frozenset({"strong", "moderate", "weak"})

TDA_NARRATIVES = frozenset(
    {
        "HUNT_PREV_HIGH",  # Swept Asia high + returned → reversal expected
        "HUNT_PREV_LOW",  # Swept Asia low + returned → reversal expected
        "CONTINUATION",  # Sweep aligned with macro direction
        "COUNTER_TREND",  # Macro vs sweep direction conflict → skip
        "NO_NARRATIVE",  # Ambiguous / double sweep / insufficient data
    }
)

TDA_SIGNAL_STATES = frozenset(
    {
        "pending",  # Signal created, waiting for entry trigger
        "active",  # Entry filled, position open (full size)
        "partial",  # Config F: 50% closed at 1R, trailing remaining
        "closed",  # Fully exited (outcome determined)
    }
)

TDA_OUTCOMES = frozenset(
    {
        "",  # Still open
        "WIN",  # Hit TP (both portions)
        "PARTIAL_WIN",  # Partial at 1R + trail exit
        "LOSS",  # Hit original SL
        "BE",  # Breakeven (partial at 1R, trail hit at entry)
        "TIMEOUT",  # max_open_bars exceeded
    }
)

TDA_GRADES = frozenset({"A+", "A", "B", "C"})

# 8-factor quality checklist (ADR-0040 §3.3, TDA_signal_redesign.md §6.4)
GRADE_FACTORS = (
    "macro_determined",  # D1 direction determined (strong)
    "h4_confirmed",  # H4 alignment confirms macro
    "htf_poi_nearby",  # Price at/near HTF POI
    "session_sweep",  # Prev session liquidity swept
    "fvg_formed",  # FVG entry formed on M15
    "in_killzone",  # In active session / killzone
    "rr_above_3",  # R:R ≥ 3.0
    "no_blocking_zones",  # Clear path entry → TP
)


# ═══════════════════════════════════════════════════════
# Stage outputs (frozen dataclasses)
# ═══════════════════════════════════════════════════════


@dataclasses.dataclass(frozen=True)
class MacroResult:
    """Stage 1 output: D1 macro direction assessment.

    Primary: 3-bar pivot detection on last N D1 completed bars.
    Fallback: linear slope on last 10 closes.
    """

    direction: str  # MACRO_DIRECTIONS: "BULL" | "BEAR" | "CFL"
    method: str  # MACRO_METHODS: "pivot" | "slope"
    confidence: str  # MACRO_CONFIDENCES: "strong" | "moderate" | "weak"
    pivot_count: int  # Number of pivots detected (0 if slope fallback)
    d1_bar_count: int  # Number of completed D1 bars used

    def to_wire(self) -> Dict[str, Any]:
        return {
            "direction": self.direction,
            "method": self.method,
            "confidence": self.confidence,
        }


@dataclasses.dataclass(frozen=True)
class H4ConfirmResult:
    """Stage 2 output: H4 directional confirmation.

    Checks H4 bars before London open cutoff for alignment with macro.
    """

    confirmed: bool
    close_price: float  # Last H4 close before cutoff
    midpoint: float  # (highest + lowest) / 2 of recent H4 range
    h4_bar_count: int  # Number of H4 bars in window
    reason: str  # "above_midpoint" | "trending" | "not_confirmed" | "insufficient_bars"

    def to_wire(self) -> Dict[str, Any]:
        return {
            "confirmed": self.confirmed,
            "reason": self.reason,
        }


@dataclasses.dataclass(frozen=True)
class SessionNarrative:
    """Stage 3 output: session sweep narrative.

    Detects sweep of Asia H/L during London session and classifies
    the narrative type (hunt/continuation/counter_trend/none).
    """

    narrative: str  # TDA_NARRATIVES
    asia_high: float
    asia_low: float
    sweep_direction: Optional[str]  # "BULL" | "BEAR" | None
    sweep_price: Optional[float]  # The Asia level that was swept
    asia_bar_count: int  # H1 bars in Asia window

    def to_wire(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "narrative": self.narrative,
            "asia_high": round(self.asia_high, 2),
            "asia_low": round(self.asia_low, 2),
        }
        if self.sweep_direction is not None:
            d["sweep_direction"] = self.sweep_direction
        if self.sweep_price is not None:
            d["sweep_price"] = round(self.sweep_price, 2)
        return d


@dataclasses.dataclass(frozen=True)
class FvgEntry:
    """Stage 4 output: M15 FVG entry parameters.

    Entry = bar touches FVG zone then closes outside.
    SL = fvg_edge - fvg_size × buffer_ratio.
    TP = entry ± risk × rr_target.
    """

    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    direction: str  # "LONG" | "SHORT"
    fvg_high: float
    fvg_low: float
    fvg_size: float
    entry_bar_ms: int  # open_time_ms of the entry bar

    @property
    def risk_pts(self) -> float:
        """Distance entry → SL in points (always positive)."""
        return abs(self.entry_price - self.stop_loss)

    def to_wire(self) -> Dict[str, Any]:
        return {
            "entry_price": round(self.entry_price, 2),
            "stop_loss": round(self.stop_loss, 2),
            "take_profit": round(self.take_profit, 2),
            "risk_reward": round(self.risk_reward, 2),
            "direction": self.direction,
            "fvg_high": round(self.fvg_high, 2),
            "fvg_low": round(self.fvg_low, 2),
            "fvg_size": round(self.fvg_size, 2),
            "entry_bar_ms": self.entry_bar_ms,
        }


# ═══════════════════════════════════════════════════════
# Trade Management (Config F)
# ═══════════════════════════════════════════════════════


@dataclasses.dataclass(frozen=True)
class TradeState:
    """Frozen snapshot of Config F trade management state.

    Runtime creates a new TradeState each bar via pure
    trade_management.update_trade() function.

    Config F flow:
    1. Open → MFE reaches 1R → close 50% (partial_r = 0.5)
    2. Move SL to entry (breakeven)
    3. MFE reaches 2R → trail SL = entry + (MFE - 1R)
    4. Exit: trail SL hit, original SL hit, TP hit, or timeout
    """

    status: str  # "open" | "partial" | "closed"
    partial_closed: bool  # True after 50% closed at 1R
    partial_r: float  # R locked from partial close (0.5 typical)
    max_favorable: float  # MFE in price points (absolute distance from entry)
    trail_sl: float  # Current trailing SL price
    bars_elapsed: int  # Bars since entry
    outcome: str  # TDA_OUTCOMES
    net_r: float  # Realized net R (0.0 if still open)

    def to_wire(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "partial_closed": self.partial_closed,
            "partial_r": round(self.partial_r, 3),
            "max_favorable_pts": round(self.max_favorable, 2),
            "trail_sl": round(self.trail_sl, 2),
            "bars_elapsed": self.bars_elapsed,
            "outcome": self.outcome,
            "net_r": round(self.net_r, 3),
        }


# ═══════════════════════════════════════════════════════
# Complete TDA Signal
# ═══════════════════════════════════════════════════════


@dataclasses.dataclass(frozen=True)
class TdaSignal:
    """Complete TDA cascade signal — replaces ADR-0039 SignalSpec
    when tda_cascade.enabled=true.

    One signal per symbol per day (daily cascade design).
    S3: signal_id = "tda_{symbol_safe}_{YYYY-MM-DD}" — deterministic.
    """

    signal_id: str  # make_tda_signal_id(symbol, date_str)
    symbol: str
    date_str: str  # "2026-01-09" — one signal per date max

    # Stage 1
    macro: MacroResult

    # Stage 2
    h4_confirm: H4ConfirmResult

    # Stage 3
    session: SessionNarrative

    # Stage 4 (entry params)
    entry: FvgEntry

    # Quality grading
    grade: str  # TDA_GRADES: "A+" | "A" | "B" | "C"
    grade_score: int  # Raw factor count (0–8)
    grade_factors: Dict[str, bool]  # Factor checklist

    # Config F trade management
    trade: TradeState

    # Timestamps
    created_ms: int
    updated_ms: int

    def to_wire(self) -> Dict[str, Any]:
        """Wire format for WS frame — consumed by ui_v4.

        Emits a superset of fields:

        * Native TDA fields (``macro``, ``h4_confirm``, ``session``, ``entry``,
          ``trade``, ``grade_factors`` …) — full cascade context.
        * SignalSpec-compatible top-level mirrors (``direction``,
          ``entry_price``, ``stop_loss``, ``take_profit``, ``risk_reward``,
          ``confidence``, ``state``, ``entry_method``, ``warnings``) consumed by
          the shell ``signal`` micro-card in ChartHud (ADR-0036/ADR-0039).

        Both ``SignalSpec.to_wire()`` and ``TdaSignal.to_wire()`` flow into the
        same ``shell.signal`` slot (see ``ws_server`` / ``shell_composer``), so
        the wire shape must be uniform from the renderer's perspective.
        ``direction`` is lowercased to match the SignalSpec convention
        (``"long"``/``"short"``).
        """
        # SignalSpec-compatible projections (UI shell.signal renderer)
        direction_lc = self.entry.direction.lower()
        # 8-factor checklist → 0..100 confidence (mirrors grade gate)
        confidence = int(round(self.grade_score / 8.0 * 100)) if self.grade_score else 0
        # trade.status ∈ {open, partial, closed} — pass through as state
        state = self.trade.status

        return {
            # ── Native TDA cascade payload ──
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "date": self.date_str,
            "macro": self.macro.to_wire(),
            "h4_confirm": self.h4_confirm.to_wire(),
            "session": self.session.to_wire(),
            "entry": self.entry.to_wire(),
            "grade": self.grade,
            "grade_score": self.grade_score,
            "grade_factors": self.grade_factors,
            "trade": self.trade.to_wire(),
            "created_ms": self.created_ms,
            "updated_ms": self.updated_ms,
            # ── SignalSpec-compatible top-level (shell.signal renderer) ──
            "direction": direction_lc,
            "entry_price": round(self.entry.entry_price, 2),
            "stop_loss": round(self.entry.stop_loss, 2),
            "take_profit": round(self.entry.take_profit, 2),
            "risk_reward": round(self.entry.risk_reward, 2),
            "confidence": confidence,
            "state": state,
            "entry_method": "tda_fvg",
            "warnings": [],
        }


# ═══════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════


def make_tda_signal_id(symbol: str, date_str: str) -> str:
    """S3: deterministic TDA signal ID — one per symbol per day."""
    sym_safe = symbol.replace("/", "_").replace(" ", "_")
    return f"tda_{sym_safe}_{date_str}"


def compute_grade(factors: Dict[str, bool]) -> Tuple[str, int]:
    """Compute TDA quality grade from 8-factor checklist.

    Returns: (grade_str, score_int)
    - 8/8 = A+, 7/8 = A, 6/8 = B, ≤5/8 = C
    """
    score = sum(1 for v in factors.values() if v)
    if score >= 8:
        grade = "A+"
    elif score >= 7:
        grade = "A"
    elif score >= 6:
        grade = "B"
    else:
        grade = "C"
    return grade, score


def initial_trade_state(entry: FvgEntry) -> TradeState:
    """Create initial trade state when position opens.

    trail_sl starts at the original SL (Config F Phase 1).
    """
    return TradeState(
        status="open",
        partial_closed=False,
        partial_r=0.0,
        max_favorable=0.0,
        trail_sl=entry.stop_loss,
        bars_elapsed=0,
        outcome="",
        net_r=0.0,
    )


# ═══════════════════════════════════════════════════════
# Config (SSOT: config.json → smc.tda_cascade)
# ═══════════════════════════════════════════════════════


@dataclasses.dataclass
class TdaCascadeConfig:
    """TDA Cascade Signal Engine config (ADR-0040 §3.4).

    SSOT: config.json → smc.tda_cascade
    Note: smc.tda is used by ADR-0034 (IFVG/Breaker); this is separate.
    S5: all thresholds from config, no hardcoded values.
    """

    enabled: bool = False

    # Stage 1: D1 Macro
    macro_min_bars: int = 5
    macro_lookback_bars: int = 20
    macro_slope_lookback: int = 10
    macro_slope_threshold: float = 0.03  # % slope for BULL/BEAR

    # Stage 2: H4 Confirmation
    h4_min_bars: int = 5  # Gate: need ≥ N H4 bars before cutoff
    h4_confirm_bars: int = 10
    h4_cutoff_hour_utc: int = 7  # Before London open

    # Stage 3: Session Narrative
    asia_start_hour_utc: int = 23  # Previous day UTC
    asia_end_hour_utc: int = 7
    london_start_hour_utc: int = 8
    london_end_hour_utc: int = 13
    asia_min_range_pts: float = 5.0
    asia_min_h1_bars: int = 2

    # Stage 4: M15 FVG Entry
    entry_search_start_utc: int = 9
    entry_search_end_utc: int = 16
    fvg_min_atr_ratio: float = 0.15
    fvg_min_abs_pts: float = 1.0
    fvg_proximity_pts: float = 200.0
    sl_buffer_ratio: float = 0.5  # SL = fvg_edge − fvg_size × ratio
    rr_target: float = 3.0  # TP at R:R target
    min_rr: float = 2.5  # Gate: reject entry if R:R < min_rr

    # Trade Management (Config F)
    max_open_bars_m15: int = 96  # ~24h
    partial_tp_enabled: bool = True
    partial_tp_at_r: float = 1.0  # Close 50% at 1R MFE
    partial_tp_pct: float = 0.5  # Fraction to close
    trail_start_r: float = 2.0  # Start trailing at 2R MFE
    trail_behind_r: float = 1.0  # Trail SL stays 1R behind max

    # Grading
    grade_enabled: bool = True
    min_grade_for_entry: str = "C"

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "TdaCascadeConfig":
        if not d:
            return cls()
        return cls(
            enabled=bool(d.get("enabled", False)),
            # Stage 1
            macro_min_bars=int(d.get("macro_min_bars", 5)),
            macro_lookback_bars=int(d.get("macro_lookback_bars", 20)),
            macro_slope_lookback=int(d.get("macro_slope_lookback", 10)),
            macro_slope_threshold=float(d.get("macro_slope_threshold", 0.03)),
            # Stage 2
            h4_min_bars=int(d.get("h4_min_bars", 5)),
            h4_confirm_bars=int(d.get("h4_confirm_bars", 10)),
            h4_cutoff_hour_utc=int(d.get("h4_cutoff_hour_utc", 7)),
            # Stage 3
            asia_start_hour_utc=int(d.get("asia_start_hour_utc", 23)),
            asia_end_hour_utc=int(d.get("asia_end_hour_utc", 7)),
            london_start_hour_utc=int(d.get("london_start_hour_utc", 8)),
            london_end_hour_utc=int(d.get("london_end_hour_utc", 13)),
            asia_min_range_pts=float(d.get("asia_min_range_pts", 5.0)),
            asia_min_h1_bars=int(d.get("asia_min_h1_bars", 2)),
            # Stage 4
            entry_search_start_utc=int(d.get("entry_search_start_utc", 9)),
            entry_search_end_utc=int(d.get("entry_search_end_utc", 16)),
            fvg_min_atr_ratio=float(d.get("fvg_min_atr_ratio", 0.15)),
            fvg_min_abs_pts=float(d.get("fvg_min_abs_pts", 1.0)),
            fvg_proximity_pts=float(d.get("fvg_proximity_pts", 200.0)),
            sl_buffer_ratio=float(d.get("sl_buffer_ratio", 0.5)),
            rr_target=float(d.get("rr_target", 3.0)),
            min_rr=float(d.get("min_rr", 2.5)),
            # Trade Management
            max_open_bars_m15=int(d.get("max_open_bars_m15", 96)),
            partial_tp_enabled=bool(d.get("partial_tp_enabled", True)),
            partial_tp_at_r=float(d.get("partial_tp_at_r", 1.0)),
            partial_tp_pct=float(d.get("partial_tp_pct", 0.5)),
            trail_start_r=float(d.get("trail_start_r", 2.0)),
            trail_behind_r=float(d.get("trail_behind_r", 1.0)),
            # Grading
            grade_enabled=bool(d.get("grade_enabled", True)),
            min_grade_for_entry=str(d.get("min_grade_for_entry", "C")),
        )
