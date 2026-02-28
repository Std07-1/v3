"""
core/smc/structure.py — Market Structure: HH/HL/LH/LL класифікація + BOS/CHoCH (ADR-0024 §4.2).

S0: pure logic, NO I/O.
S2: deterministic.
"""
from __future__ import annotations

import dataclasses
from typing import List, Optional, Tuple

from core.model.bars import CandleBar
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
    lows  = [s for s in raw_swings if s.kind in ("sl", "ll", "hl")]

    classified: List[SmcSwing] = []

    # Classify highs
    prev_high: Optional[float] = None
    for s in highs:
        if prev_high is None:
            new_kind = "hh"      # перший — за замовчуванням HH
        elif s.price > prev_high:
            new_kind = "hh"
        else:
            new_kind = "lh"
        prev_high = s.price
        classified.append(dataclasses.replace(
            s,
            kind=new_kind,
            id=make_swing_id(new_kind, s.symbol, s.tf_s, s.time_ms),
        ))

    # Classify lows
    prev_low: Optional[float] = None
    for s in lows:
        if prev_low is None:
            new_kind = "ll"      # перший — за замовчуванням LL
        elif s.price < prev_low:
            new_kind = "ll"
        else:
            new_kind = "hl"
        prev_low = s.price
        classified.append(dataclasses.replace(
            s,
            kind=new_kind,
            id=make_swing_id(new_kind, s.symbol, s.tf_s, s.time_ms),
        ))

    classified.sort(key=lambda s: s.time_ms)
    return classified


def detect_structure_events(
    classified_swings: List[SmcSwing],
    bars: List[CandleBar],
) -> Tuple[List[SmcSwing], Optional[str], Optional[int], Optional[int]]:
    """Виявляє BOS та CHoCH на основі підтверджених свінгів і цінових барів.

    Returns:
        (events, trend_bias, last_bos_ms, last_choch_ms)

    BOS (bullish): бар закривається ВИЩЕ попереднього HH → підтверджує bullish тренд.
    BOS (bearish): бар закривається НИЖЧЕ попереднього LL → підтверджує bearish тренд.
    CHoCH (bullish): бар закривається ВИЩЕ попереднього HH, поточний тренд = bearish → розворот.
    CHoCH (bearish): бар закривається НИЖЧЕ попереднього LL, поточний тренд = bullish → розворот.

    Алгоритм:
      1. Беремо підтверджені HH та LL свінги (хронологічно).
      2. Для кожного бару перевіряємо: чи close пробиває останній значимий рівень?
      3. Визначаємо тип події (BOS vs CHoCH) на основі поточного тренду.
    """
    if not classified_swings or not bars:
        return [], None, None, None

    events: List[SmcSwing] = []
    trend_bias: Optional[str] = None
    last_bos_ms: Optional[int] = None
    last_choch_ms: Optional[int] = None

    # Будуємо ітераційний стан: останній HH та LL
    last_hh: Optional[SmcSwing] = None
    last_ll: Optional[SmcSwing] = None
    swing_map = {s.time_ms: s for s in classified_swings}
    swing_times = sorted(swing_map.keys())

    swing_idx = 0  # поточний свінг у хронологічному порядку

    for bar in bars:
        # Апдейтити останні HH/LL на основі підтверджених свінгів до цього бару
        while swing_idx < len(swing_times) and swing_times[swing_idx] <= bar.open_time_ms:
            s = swing_map[swing_times[swing_idx]]
            if s.kind == "hh":
                last_hh = s
            elif s.kind == "ll":
                last_ll = s
            swing_idx += 1

        if not bar.complete:
            continue

        # Перевіряємо BOS/CHoCH
        if last_hh is not None and bar.c > last_hh.price and bar.open_time_ms > last_hh.time_ms:
            # Break above last HH
            if trend_bias == "bearish":
                kind = "choch_bull"
                last_choch_ms = bar.open_time_ms
            else:
                kind = "bos_bull"
                last_bos_ms = bar.open_time_ms
            trend_bias = "bullish"
            events.append(SmcSwing(
                id=make_swing_id(kind, bar.symbol, bar.tf_s, bar.open_time_ms),
                symbol=bar.symbol,
                tf_s=bar.tf_s,
                kind=kind,
                price=last_hh.price,
                time_ms=bar.open_time_ms,
                confirmed=True,
            ))
            last_hh = None  # consumed — не повторювати той самий рівень

        elif last_ll is not None and bar.c < last_ll.price and bar.open_time_ms > last_ll.time_ms:
            # Break below last LL
            if trend_bias == "bullish":
                kind = "choch_bear"
                last_choch_ms = bar.open_time_ms
            else:
                kind = "bos_bear"
                last_bos_ms = bar.open_time_ms
            trend_bias = "bearish"
            events.append(SmcSwing(
                id=make_swing_id(kind, bar.symbol, bar.tf_s, bar.open_time_ms),
                symbol=bar.symbol,
                tf_s=bar.tf_s,
                kind=kind,
                price=last_ll.price,
                time_ms=bar.open_time_ms,
                confirmed=True,
            ))
            last_ll = None  # consumed

    return events, trend_bias, last_bos_ms, last_choch_ms
