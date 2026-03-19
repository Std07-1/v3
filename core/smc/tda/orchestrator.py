"""
core/smc/tda/orchestrator.py — TDA Cascade Orchestrator (ADR-0040 §4.6).

Pure function combining all 5 stages into one daily cascade:
  D1 Macro → H4 Confirm → Session Narrative → M15 FVG Entry → Grade Gate.

If any stage fails, returns None (no signal that day).
Trade management (Stage 5) is external — called per-bar after entry.

Інваріанти:
  S0: pure logic, NO I/O
  S2: deterministic — same bars → same signal
  S3: signal_id = "tda_{symbol}_{date}" — deterministic
  S5: all thresholds from TdaCascadeConfig
"""

from __future__ import annotations

from typing import Dict, List, Optional

from core.model.bars import CandleBar
from core.smc.tda.types import (
    TdaCascadeConfig,
    TdaSignal,
    compute_grade,
    initial_trade_state,
    make_tda_signal_id,
)
from core.smc.tda.stage1_macro import get_macro_direction
from core.smc.tda.stage2_h4_confirm import h4_confirmed
from core.smc.tda.stage3_session import get_session_narrative
from core.smc.tda.stage4_fvg_entry import find_fvg_entry

_MS_PER_HOUR = 3_600_000

_GRADE_ORDER = {"A+": 4, "A": 3, "B": 2, "C": 1}


def run_tda_cascade(
    symbol: str,
    date_str: str,
    d1_bars: List[CandleBar],
    h4_bars: List[CandleBar],
    h1_bars: List[CandleBar],
    m15_bars: List[CandleBar],
    day_ms: int,
    cfg: TdaCascadeConfig,
    now_ms: int,
) -> Optional[TdaSignal]:
    """Run the full 4-stage TDA cascade for one symbol and one day.

    Returns TdaSignal if all stages pass and grade gate holds,
    otherwise None.

    Args:
        symbol: "XAU/USD" etc.
        date_str: "2026-01-09" — one signal per day max.
        d1_bars: Recent D1 completed bars (≥macro_min_bars).
        h4_bars: Recent H4 bars covering pre-London window.
        h1_bars: H1 bars covering Asia + London sessions.
        m15_bars: M15 bars covering London session for entry search.
        day_ms: Epoch ms of trading day at 00:00 UTC.
        cfg: TDA cascade config (S5 SSOT).
        now_ms: Current epoch ms (for created_ms / updated_ms).

    Returns:
        TdaSignal with initial trade state, or None.
    """
    # ── Stage 1: D1 Macro Direction ──
    macro = get_macro_direction(d1_bars, cfg)
    if macro is None or macro.direction == "CFL":
        return None

    # ── Stage 2: H4 Confirmation ──
    h4 = h4_confirmed(h4_bars, macro.direction, day_ms, cfg)
    if h4 is None or not h4.confirmed:
        return None

    # ── Stage 3: Session Narrative ──
    session = get_session_narrative(h1_bars, m15_bars, macro.direction, day_ms, cfg)
    if session.narrative in ("COUNTER_TREND", "NO_NARRATIVE"):
        return None
    if session.sweep_direction is None or session.sweep_price is None:
        return None

    # ── Stage 4: M15 FVG Entry ──
    entry = find_fvg_entry(
        m15_bars,
        session.sweep_direction,
        session.sweep_price,
        day_ms,
        cfg,
    )
    if entry is None:
        return None

    # ── Grading ──
    factors = _compute_factors(macro, session, entry, day_ms, cfg)
    grade, score = compute_grade(factors)

    if cfg.grade_enabled:
        if _GRADE_ORDER.get(grade, 0) < _GRADE_ORDER.get(cfg.min_grade_for_entry, 0):
            return None

    # ── Assemble signal ──
    signal_id = make_tda_signal_id(symbol, date_str)
    trade = initial_trade_state(entry)

    return TdaSignal(
        signal_id=signal_id,
        symbol=symbol,
        date_str=date_str,
        macro=macro,
        h4_confirm=h4,
        session=session,
        entry=entry,
        grade=grade,
        grade_score=score,
        grade_factors=factors,
        trade=trade,
        created_ms=now_ms,
        updated_ms=now_ms,
    )


def _compute_factors(
    macro,
    session,
    entry,
    day_ms: int,
    cfg: TdaCascadeConfig,
) -> Dict[str, bool]:
    """Compute 8-factor grade checklist from stage outputs."""
    entry_hour = (entry.entry_bar_ms - day_ms) // _MS_PER_HOUR

    # Build dict keyed by GRADE_FACTORS vocabulary
    return {
        "macro_determined": macro.confidence == "strong",
        "h4_confirmed": True,  # always True — we got past Stage 2 gate
        "htf_poi_nearby": session.sweep_price is not None,
        "session_sweep": session.narrative in ("HUNT_PREV_HIGH", "HUNT_PREV_LOW"),
        "fvg_formed": True,  # always True — we got past Stage 4
        "in_killzone": cfg.london_start_hour_utc
        <= entry_hour
        < cfg.london_end_hour_utc,
        "rr_above_3": entry.risk_reward >= cfg.rr_target,
        "no_blocking_zones": True,  # default — requires SmcEngine integration
    }
