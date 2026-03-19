"""
core/smc/tda/stage3_session.py — Stage 3: Session Narrative (ADR-0040 §2.4).

Algorithm:
  1. Asia session (prev-day start → current-day end) range from H1 bars
  2. London KZ H1 sweep detection (first bar to exceed Asia H/L)
  3. Double sweep → NO_NARRATIVE
  4. Sweep + return: aligned with macro → HUNT_PREV_HIGH/LOW, against → COUNTER_TREND
  5. Sweep without return → CONTINUATION
  6. M15 fallback if H1 found no sweep

Інваріанти:
  S0: pure, NO I/O
  S2: deterministic — same bars → same output
  S5: thresholds from TdaCascadeConfig
"""

from __future__ import annotations

from typing import List

from core.model.bars import CandleBar
from core.smc.tda.types import SessionNarrative, TdaCascadeConfig

_MS_PER_HOUR = 3_600_000

_N = "NO_NARRATIVE"


def get_session_narrative(
    h1_bars: List[CandleBar],
    m15_bars: List[CandleBar],
    macro_dir: str,
    day_ms: int,
    cfg: TdaCascadeConfig,
) -> SessionNarrative:
    """Stage 3: Detect Asia range + London sweep narrative.

    Args:
        h1_bars: H1 bars covering Asia + London (any completeness).
        m15_bars: M15 bars for London fallback.
        macro_dir: "BULL" or "BEAR" from Stage 1.
        day_ms: Epoch ms of current trading day at 00:00 UTC.
        cfg: TDA cascade config (session windows, thresholds).

    Returns:
        SessionNarrative with narrative, asia levels, sweep info.
    """
    # Asia wraps midnight: start is on previous calendar day
    asia_start_ms = day_ms - (24 - cfg.asia_start_hour_utc) * _MS_PER_HOUR
    asia_end_ms = day_ms + cfg.asia_end_hour_utc * _MS_PER_HOUR

    # London: same calendar day
    london_start_ms = day_ms + cfg.london_start_hour_utc * _MS_PER_HOUR
    london_end_ms = day_ms + cfg.london_end_hour_utc * _MS_PER_HOUR

    asia_h1 = [b for b in h1_bars if asia_start_ms <= b.open_time_ms < asia_end_ms]

    if len(asia_h1) < cfg.asia_min_h1_bars:
        return SessionNarrative(
            narrative=_N,
            asia_high=0.0,
            asia_low=0.0,
            sweep_direction=None,
            sweep_price=None,
            asia_bar_count=len(asia_h1),
        )

    asia_high = max(b.h for b in asia_h1)
    asia_low = min(b.low for b in asia_h1)
    asia_range = asia_high - asia_low
    asia_count = len(asia_h1)

    if asia_range < cfg.asia_min_range_pts:
        return SessionNarrative(
            narrative=_N,
            asia_high=asia_high,
            asia_low=asia_low,
            sweep_direction=None,
            sweep_price=None,
            asia_bar_count=asia_count,
        )

    london_h1 = [
        b for b in h1_bars if london_start_ms <= b.open_time_ms <= london_end_ms
    ]

    if not london_h1:
        return SessionNarrative(
            narrative=_N,
            asia_high=asia_high,
            asia_low=asia_low,
            sweep_direction=None,
            sweep_price=None,
            asia_bar_count=asia_count,
        )

    # ── H1 sweep detection (first bar to exceed Asia level) ──
    swept_high_bar = None
    swept_low_bar = None
    for bar in london_h1:
        if bar.h > asia_high and swept_high_bar is None:
            swept_high_bar = bar
        if bar.low < asia_low and swept_low_bar is None:
            swept_low_bar = bar

    # Double sweep → ambiguous
    if swept_high_bar is not None and swept_low_bar is not None:
        return SessionNarrative(
            narrative=_N,
            asia_high=asia_high,
            asia_low=asia_low,
            sweep_direction=None,
            sweep_price=None,
            asia_bar_count=asia_count,
        )

    result = _classify_h1_sweep(
        swept_high_bar,
        swept_low_bar,
        london_h1,
        asia_high,
        asia_low,
        asia_count,
        macro_dir,
    )
    if result is not None:
        return result

    # ── M15 fallback ──
    result = _m15_fallback(
        m15_bars,
        london_start_ms,
        london_end_ms,
        asia_high,
        asia_low,
        asia_count,
        macro_dir,
    )
    if result is not None:
        return result

    return SessionNarrative(
        narrative=_N,
        asia_high=asia_high,
        asia_low=asia_low,
        sweep_direction=None,
        sweep_price=None,
        asia_bar_count=asia_count,
    )


def _classify_h1_sweep(
    swept_high_bar: CandleBar | None,
    swept_low_bar: CandleBar | None,
    london_h1: List[CandleBar],
    asia_high: float,
    asia_low: float,
    asia_count: int,
    macro_dir: str,
) -> SessionNarrative | None:
    """Classify H1 sweep narrative. Returns None if no single sweep found."""
    if swept_high_bar is not None:
        idx = london_h1.index(swept_high_bar)
        subsequent = london_h1[idx:]
        returned = any(b.c < asia_high for b in subsequent)
        if returned:
            nar = "HUNT_PREV_HIGH" if macro_dir == "BEAR" else "COUNTER_TREND"
            return SessionNarrative(
                nar, asia_high, asia_low, "BEAR", asia_high, asia_count
            )
        return SessionNarrative(
            "CONTINUATION", asia_high, asia_low, "BULL", asia_high, asia_count
        )

    if swept_low_bar is not None:
        idx = london_h1.index(swept_low_bar)
        subsequent = london_h1[idx:]
        returned = any(b.c > asia_low for b in subsequent)
        if returned:
            nar = "HUNT_PREV_LOW" if macro_dir == "BULL" else "COUNTER_TREND"
            return SessionNarrative(
                nar, asia_high, asia_low, "BULL", asia_low, asia_count
            )
        return SessionNarrative(
            "CONTINUATION", asia_high, asia_low, "BEAR", asia_low, asia_count
        )

    return None


def _m15_fallback(
    m15_bars: List[CandleBar],
    london_start_ms: int,
    london_end_ms: int,
    asia_high: float,
    asia_low: float,
    asia_count: int,
    macro_dir: str,
) -> SessionNarrative | None:
    """M15 fallback sweep detection when H1 found nothing."""
    london_m15 = [
        b for b in m15_bars if london_start_ms <= b.open_time_ms <= london_end_ms
    ]
    if not london_m15:
        return None

    m15_high = max(b.h for b in london_m15)
    m15_low = min(b.low for b in london_m15)

    # One-sided guard: only detect high sweep if low wasn't also swept
    if m15_high > asia_high and m15_low >= asia_low:
        swept = next((b for b in london_m15 if b.h > asia_high), None)
        if swept is not None:
            sub = london_m15[london_m15.index(swept) :]
            if any(b.c < asia_high for b in sub):
                nar = "HUNT_PREV_HIGH" if macro_dir == "BEAR" else "COUNTER_TREND"
                return SessionNarrative(
                    nar, asia_high, asia_low, "BEAR", asia_high, asia_count
                )

    if m15_low < asia_low and m15_high <= asia_high:
        swept = next((b for b in london_m15 if b.low < asia_low), None)
        if swept is not None:
            sub = london_m15[london_m15.index(swept) :]
            if any(b.c > asia_low for b in sub):
                nar = "HUNT_PREV_LOW" if macro_dir == "BULL" else "COUNTER_TREND"
                return SessionNarrative(
                    nar, asia_high, asia_low, "BULL", asia_low, asia_count
                )

    return None
