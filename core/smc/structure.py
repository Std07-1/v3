"""
core/smc/structure.py — Market Structure: HH/HL/LH/LL класифікація + BOS/CHoCH V2 (ADR-0024 §4.2, ADR-0047).

S0: pure logic, NO I/O.
S2: deterministic.

V2 (ADR-0047): Canonical ICT BOS/CHoCH.
  - BOS = continuation: break HH (uptrend) або LL (downtrend).
  - CHoCH = reversal via internal structure: break HL (uptrend→bearish) або LH (downtrend→bullish).
  - Tracks all 4 swing types: HH, HL, LH, LL.
"""

from __future__ import annotations

import dataclasses
from typing import List, Optional, Tuple

from core.model.bars import CandleBar
from core.smc.config import SmcStructureConfig
from core.smc.types import SmcSwing, make_swing_id

# ── Internal helpers ────────────────────────────────────────────────


def _is_swing_high(swing: SmcSwing) -> bool:
    return swing.kind in ("hh", "lh", "sh")


def _is_swing_low(swing: SmcSwing) -> bool:
    return swing.kind in ("ll", "hl", "sl")


# ── Main classification ─────────────────────────────────────────────


def classify_swings(raw_swings: List[SmcSwing]) -> List[SmcSwing]:
    """Переводить generic sh/sl → HH/HL/LH/LL на основі послідовності.

    Алгоритм:
      - Окремо обробляємо high-swings та low-swings у хронологічному порядку.
      - High swings: якщо поточний > попереднього → HH, інакше → LH.
      - Low swings:  якщо поточний < попереднього → LL, інакше → HL.
    """
    if not raw_swings:
        return []

    highs = [s for s in raw_swings if s.kind in ("sh", "hh", "lh")]
    lows = [s for s in raw_swings if s.kind in ("sl", "ll", "hl")]

    classified: List[SmcSwing] = []

    # Classify highs
    prev_high: Optional[float] = None
    for s in highs:
        if prev_high is None:
            new_kind = "hh"  # перший — за замовчуванням HH
        elif s.price > prev_high:
            new_kind = "hh"
        else:
            new_kind = "lh"
        prev_high = s.price
        classified.append(
            dataclasses.replace(
                s,
                kind=new_kind,
                id=make_swing_id(new_kind, s.symbol, s.tf_s, s.time_ms),
            )
        )

    # Classify lows
    prev_low: Optional[float] = None
    for s in lows:
        if prev_low is None:
            new_kind = "ll"  # перший — за замовчуванням LL
        elif s.price < prev_low:
            new_kind = "ll"
        else:
            new_kind = "hl"
        prev_low = s.price
        classified.append(
            dataclasses.replace(
                s,
                kind=new_kind,
                id=make_swing_id(new_kind, s.symbol, s.tf_s, s.time_ms),
            )
        )

    classified.sort(key=lambda s: s.time_ms)
    return classified


def detect_structure_events(
    classified_swings: List[SmcSwing],
    bars: List[CandleBar],
    config: Optional[SmcStructureConfig] = None,
) -> Tuple[List[SmcSwing], Optional[str], Optional[int], Optional[int]]:
    """Виявляє BOS та CHoCH на основі підтверджених свінгів і цінових барів (V2, ADR-0047).

    Returns:
        (events, trend_bias, last_bos_ms, last_choch_ms)

    V2 Canonical ICT definitions:

    Uptrend (bullish): HH → HL → HH → HL ...
      BOS_BULL:  close > last swing high (HH)  → continuation
      CHoCH_BEAR: close < last HL (internal structure) → reversal → trend = bearish

    Downtrend (bearish): LL → LH → LL → LH ...
      BOS_BEAR:  close < last swing low (LL)   → continuation
      CHoCH_BULL: close > last LH (internal structure) → reversal → trend = bullish

    trend_bias=None (cold start):
      Only BOS allowed (first HH break or LL break establishes trend).
      HL/LH breaks ignored until trend is established.
    """
    if not classified_swings or not bars:
        return [], None, None, None

    confirmation_bars = 1
    if config is not None:
        confirmation_bars = max(1, config.confirmation_bars)

    events: List[SmcSwing] = []
    trend_bias: Optional[str] = None
    last_bos_ms: Optional[int] = None
    last_choch_ms: Optional[int] = None

    # Track all 4 swing types
    last_hh: Optional[SmcSwing] = None
    last_hl: Optional[SmcSwing] = None
    last_ll: Optional[SmcSwing] = None
    last_lh: Optional[SmcSwing] = None

    swing_map = {s.time_ms: s for s in classified_swings}
    swing_times = sorted(swing_map.keys())
    swing_idx = 0

    # For multi-bar confirmation: count consecutive bars beyond level
    confirm_kind: Optional[str] = None
    confirm_count: int = 0
    confirm_level: Optional[SmcSwing] = None
    confirm_bar: Optional[CandleBar] = None

    def _emit(kind: str, level: SmcSwing, bar_: CandleBar) -> None:
        nonlocal trend_bias, last_bos_ms, last_choch_ms
        nonlocal last_hh, last_hl, last_ll, last_lh

        if kind.startswith("bos_"):
            last_bos_ms = bar_.open_time_ms
        else:
            last_choch_ms = bar_.open_time_ms

        if kind.endswith("_bull"):
            trend_bias = "bullish"
        else:
            trend_bias = "bearish"

        events.append(
            SmcSwing(
                id=make_swing_id(kind, bar_.symbol, bar_.tf_s, bar_.open_time_ms),
                symbol=bar_.symbol,
                tf_s=bar_.tf_s,
                kind=kind,
                price=level.price,
                time_ms=bar_.open_time_ms,
                confirmed=True,
            )
        )

        # Consume the broken level so we don't re-trigger
        if kind == "bos_bull":
            last_hh = None
        elif kind == "choch_bear":
            last_hl = None
        elif kind == "bos_bear":
            last_ll = None
        elif kind == "choch_bull":
            last_lh = None

    for bar in bars:
        # Update swing tracking from confirmed swings up to this bar
        while (
            swing_idx < len(swing_times) and swing_times[swing_idx] <= bar.open_time_ms
        ):
            s = swing_map[swing_times[swing_idx]]
            if s.kind == "hh":
                last_hh = s
            elif s.kind == "hl":
                last_hl = s
            elif s.kind == "ll":
                last_ll = s
            elif s.kind == "lh":
                last_lh = s
            swing_idx += 1

        if not bar.complete:
            continue

        # Detect break candidates with temporal guard
        candidate_kind: Optional[str] = None
        candidate_level: Optional[SmcSwing] = None

        if trend_bias == "bullish" or trend_bias is None:
            # BOS_BULL: break above last HH → continuation
            if (
                last_hh is not None
                and bar.c > last_hh.price
                and bar.open_time_ms > last_hh.time_ms
            ):
                candidate_kind = "bos_bull"
                candidate_level = last_hh

            # CHoCH_BEAR: break below last HL → reversal (only if trend established)
            elif (
                trend_bias == "bullish"
                and last_hl is not None
                and bar.c < last_hl.price
                and bar.open_time_ms > last_hl.time_ms
            ):
                candidate_kind = "choch_bear"
                candidate_level = last_hl

        if candidate_kind is None and (trend_bias == "bearish" or trend_bias is None):
            # BOS_BEAR: break below last LL → continuation
            if (
                last_ll is not None
                and bar.c < last_ll.price
                and bar.open_time_ms > last_ll.time_ms
            ):
                candidate_kind = "bos_bear"
                candidate_level = last_ll

            # CHoCH_BULL: break above last LH → reversal (only if trend established)
            elif (
                trend_bias == "bearish"
                and last_lh is not None
                and bar.c > last_lh.price
                and bar.open_time_ms > last_lh.time_ms
            ):
                candidate_kind = "choch_bull"
                candidate_level = last_lh

        # Multi-bar confirmation
        if candidate_kind is not None and candidate_level is not None:
            if (
                confirm_kind == candidate_kind
                and confirm_level is not None
                and confirm_level.time_ms == candidate_level.time_ms
            ):
                confirm_count += 1
            else:
                confirm_kind = candidate_kind
                confirm_level = candidate_level
                confirm_count = 1
                confirm_bar = bar

            if (
                confirm_count >= confirmation_bars
                and confirm_level is not None
                and confirm_bar is not None
            ):
                _emit(confirm_kind, confirm_level, confirm_bar)
                confirm_kind = None
                confirm_count = 0
                confirm_level = None
                confirm_bar = None
        else:
            # Reset confirmation if no candidate this bar
            confirm_kind = None
            confirm_count = 0
            confirm_level = None
            confirm_bar = None

    return events, trend_bias, last_bos_ms, last_choch_ms
