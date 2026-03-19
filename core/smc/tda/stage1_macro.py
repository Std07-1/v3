"""
core/smc/tda/stage1_macro.py — Stage 1: D1 Macro Direction (ADR-0040 §2.2).

Algorithm:
  1. Filter to complete D1 bars only
  2. 3-bar pivot detection on last N completed bars
  3. HH+HL → BULL (strong); LH+LL → BEAR (strong)
  4. Partial evidence (HH or HL, LH or LL) → moderate confidence
  5. Fallback: linear slope on last 10 closes → weak confidence
  6. If still ambiguous → CFL (conflict/ranging)

Інваріанти:
  S0: pure, NO I/O
  S2: deterministic — same bars → same output
  S5: thresholds from TdaCascadeConfig
"""

from __future__ import annotations

from typing import List

from core.model.bars import CandleBar
from core.smc.tda.types import MacroResult, TdaCascadeConfig


def get_macro_direction(
    d1_bars: List[CandleBar],
    cfg: TdaCascadeConfig,
) -> MacroResult:
    """Stage 1: Assess D1 macro direction from D1 bars.

    Filters to complete bars internally. Returns MacroResult with
    direction, method, confidence, pivot_count, bar_count.

    Gate: ≥ cfg.macro_min_bars completed D1 bars required, else CFL.
    """
    completed = [b for b in d1_bars if b.complete]
    bar_count = len(completed)

    if bar_count < cfg.macro_min_bars:
        return MacroResult(
            direction="CFL",
            method="pivot",
            confidence="weak",
            pivot_count=0,
            d1_bar_count=bar_count,
        )

    recent = completed[-cfg.macro_lookback_bars :]

    # ── Phase 1: 3-bar pivot detection ──
    piv_highs: list[float] = []
    piv_lows: list[float] = []

    for i in range(1, len(recent) - 1):
        if recent[i].h >= recent[i - 1].h and recent[i].h >= recent[i + 1].h:
            piv_highs.append(recent[i].h)
        if recent[i].low <= recent[i - 1].low and recent[i].low <= recent[i + 1].low:
            piv_lows.append(recent[i].low)

    pivot_count = len(piv_highs) + len(piv_lows)

    if len(piv_highs) >= 2 and len(piv_lows) >= 2:
        hh = piv_highs[-1] > piv_highs[-2]
        lh = piv_highs[-1] < piv_highs[-2]
        hl = piv_lows[-1] > piv_lows[-2]
        ll = piv_lows[-1] < piv_lows[-2]

        # Strong: both conditions (classic trend structure)
        if hh and hl:
            return MacroResult("BULL", "pivot", "strong", pivot_count, bar_count)
        if lh and ll:
            return MacroResult("BEAR", "pivot", "strong", pivot_count, bar_count)

        # Moderate: partial evidence (one condition)
        if hh or hl:
            return MacroResult("BULL", "pivot", "moderate", pivot_count, bar_count)
        if lh or ll:
            return MacroResult("BEAR", "pivot", "moderate", pivot_count, bar_count)

    # ── Phase 2: Linear slope fallback ──
    slope_window = recent[-cfg.macro_slope_lookback :]
    closes = [b.c for b in slope_window]
    n = len(closes)

    if n >= 2:
        x_mean = (n - 1) / 2.0
        y_mean = sum(closes) / n
        num = sum((i - x_mean) * (closes[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))

        if den > 0 and y_mean > 0:
            slope_pct = (num / den) / y_mean * 100.0
            if slope_pct > cfg.macro_slope_threshold:
                return MacroResult("BULL", "slope", "weak", pivot_count, bar_count)
            if slope_pct < -cfg.macro_slope_threshold:
                return MacroResult("BEAR", "slope", "weak", pivot_count, bar_count)

    # ── Phase 3: Ambiguous → CFL ──
    return MacroResult("CFL", "slope", "weak", pivot_count, bar_count)
