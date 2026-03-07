"""
core/smc/momentum.py — Displacement candle detection + momentum scoring.

Displacement = strong-body, small-wick candle (institutional aggression).
S0: pure logic, NO I/O.
S2: deterministic — same bars → same result.
S5: thresholds from SmcMomentumConfig (not hardcoded).
"""
from __future__ import annotations

from typing import List, Tuple

from core.model.bars import CandleBar
from core.smc.types import SmcSwing, make_swing_id


def detect_displacement(
    bars,            # type: List[CandleBar]
    atr,             # type: float
    min_body_atr,    # type: float
    max_wick_ratio,  # type: float
):
    # type: (...) -> List[SmcSwing]
    """Detect displacement candles — strong body, small wicks.

    A displacement candle satisfies:
      1) body >= min_body_atr * ATR   (institutional size)
      2) body / range >= 1 - max_wick_ratio  (clean, small wicks)

    Returns SmcSwing markers with kind='displacement_bull'/'displacement_bear'.
    """
    if atr <= 0 or not bars:
        return []
    result = []  # type: List[SmcSwing]
    for b in bars:
        body = abs(b.c - b.o)
        rng = b.h - b.low
        if rng <= 0:
            continue
        if body < min_body_atr * atr:
            continue
        if body / rng < (1.0 - max_wick_ratio):
            continue
        is_bull = b.c > b.o
        kind = "displacement_bull" if is_bull else "displacement_bear"
        result.append(SmcSwing(
            id=make_swing_id("db" if is_bull else "ds", b.symbol, b.tf_s, b.open_time_ms),
            symbol=b.symbol, tf_s=b.tf_s, kind=kind,
            price=b.c, time_ms=b.open_time_ms, confirmed=True,
        ))
    return result


def compute_momentum_score(
    bars,            # type: List[CandleBar]
    atr,             # type: float
    min_body_atr,    # type: float
    max_wick_ratio,  # type: float
    lookback,        # type: int
):
    # type: (...) -> Tuple[int, int]
    """Count displacement candles in last `lookback` bars.

    Returns (bull_count, bear_count).
    """
    if atr <= 0 or not bars:
        return (0, 0)
    recent = bars[-lookback:] if len(bars) > lookback else bars
    bull = 0
    bear = 0
    for b in recent:
        body = abs(b.c - b.o)
        rng = b.h - b.low
        if rng <= 0:
            continue
        if body < min_body_atr * atr:
            continue
        if body / rng < (1.0 - max_wick_ratio):
            continue
        if b.c > b.o:
            bull += 1
        else:
            bear += 1
    return (bull, bear)
