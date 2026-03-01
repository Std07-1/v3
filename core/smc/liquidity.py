"""
core/smc/liquidity.py — Liquidity Level detection (ADR-0024 §4.5).

Реалізує E2 рівні:
  eq_highs — Equal Highs (кластери підтверджених swing highs в межах ATR-tolerance)
  eq_lows  — Equal Lows  (кластери підтверджених swing lows)

S0: pure logic, NO I/O.
S2: deterministic — same swings+bars → same levels.
S5: tolerance_atr_mult, min_touches, max_levels з SmcConfig.levels (не hardcoded).

PDH/PDL/PWH/PWL потребують cross-TF D1 даних → defer до E3.

Python 3.7 compatible.
"""
from __future__ import annotations

from typing import List, Optional

from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.swings import compute_atr
from core.smc.types import SmcLevel, SmcSwing, make_level_id

# Kinds що є "high swings" і "low swings"
_HIGH_SWING_KINDS = frozenset({"hh", "lh", "sh"})
_LOW_SWING_KINDS  = frozenset({"ll", "hl", "sl"})


def detect_liquidity_levels(
    swings: List[SmcSwing],
    bars: List[CandleBar],
    config: SmcConfig,
    atr: float = 0.0,       # F4: caller-supplied ATR (0 → compute internally)
) -> List[SmcLevel]:
    """Виявляє рівні ліквідності (Equal Highs / Equal Lows).

    Args:
        swings: класифіковані swing-точки (hh/hl/lh/ll + можливо bos/choch).
                Очікуються confirmed=True для кластеризації (unconfirmed ігноруються).
        bars:   бари однієї (symbol, tf_s) пари — для розрахунку ATR.
        config: SmcConfig (SSOT). config.levels управляє включенням/порогами.

    Returns:
        Список SmcLevel відсортований за touches desc (найзначніші перші).
        Порожній список якщо disabled, недостатньо барів або swings.
    """
    cfg = config.levels

    if not cfg.enabled:
        return []
    if not swings or not bars:
        return []

    # ATR-базований поріг для кластеризації (F4: prefer caller-supplied)
    if atr <= 0.0:
        atr = compute_atr(bars, period=config.ob.atr_period)
    if atr <= 0.0:
        return []

    tolerance = atr * cfg.tolerance_atr_mult
    symbol = bars[0].symbol
    tf_s   = bars[0].tf_s

    # Фільтруємо тільки підтверджені highs і lows
    high_swings = [s for s in swings if s.kind in _HIGH_SWING_KINDS and s.confirmed]
    low_swings  = [s for s in swings if s.kind in _LOW_SWING_KINDS  and s.confirmed]

    per_side = max(1, cfg.max_levels // 2)

    levels: List[SmcLevel] = []
    levels += _cluster_to_levels(
        high_swings, "eq_highs", tolerance, cfg.min_touches, per_side, symbol, tf_s,
    )
    levels += _cluster_to_levels(
        low_swings, "eq_lows", tolerance, cfg.min_touches, per_side, symbol, tf_s,
    )

    return levels


# ── Private ─────────────────────────────────────────────────────────

def _cluster_to_levels(
    swings: List[SmcSwing],
    kind: str,
    tolerance: float,
    min_touches: int,
    max_count: int,
    symbol: str,
    tf_s: int,
) -> List[SmcLevel]:
    """Кластеризує swing точки за ціновою близькістю → SmcLevel per cluster.

    Алгоритм: жадібна кластеризація по ціні (сортуємо, групуємо в межах tolerance).
    S2: входи відсортовані → детермінований результат.
    """
    if not swings:
        return []

    # Стабільне сортування по ціні (S2)
    ordered = sorted(swings, key=lambda s: s.price)

    clusters: List[List[SmcSwing]] = []
    current: List[SmcSwing] = [ordered[0]]

    for sw in ordered[1:]:
        # Порівнюємо з поточним середнім кластера
        cluster_mean = _mean_price(current)
        if abs(sw.price - cluster_mean) <= tolerance:
            current.append(sw)
        else:
            clusters.append(current)
            current = [sw]
    clusters.append(current)

    # Перетворюємо кластери на SmcLevel (тільки ≥ min_touches)
    levels: List[SmcLevel] = []
    for cluster in clusters:
        if len(cluster) < min_touches:
            continue
        price = _mean_price(cluster)
        earliest_ms = min(s.time_ms for s in cluster)
        level_id = make_level_id(kind, symbol, tf_s, price)
        levels.append(SmcLevel(
            id=level_id,
            symbol=symbol,
            tf_s=tf_s,
            kind=kind,
            price=price,
            time_ms=earliest_ms,
            touches=len(cluster),
        ))

    # Сортуємо по touches desc (найзначніші першими), ліміт
    levels.sort(key=lambda l: -l.touches)
    return levels[:max_count]


def _mean_price(swings: List[SmcSwing]) -> float:
    """Середня ціна групи swing-точок."""
    return sum(s.price for s in swings) / len(swings)
