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
from core.smc.types import (
    LEVEL_SIDE_HIGH,
    LEVEL_SIDE_LOW,
    LEVEL_STATE_FIXED,
    LEVEL_TIER_CONTEXT,
    SmcLevel,
    SmcSwing,
    level_price_key,
    make_level_id,
    make_level_key,
)

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

    swept_bars = bars if cfg.hide_swept else None
    levels: List[SmcLevel] = []
    levels += _cluster_to_levels(
        high_swings, "eq_highs", LEVEL_SIDE_HIGH, tolerance, cfg.min_touches, per_side, symbol, tf_s, swept_bars,
    )
    levels += _cluster_to_levels(
        low_swings, "eq_lows", LEVEL_SIDE_LOW, tolerance, cfg.min_touches, per_side, symbol, tf_s, swept_bars,
    )

    return levels


# ── Private ─────────────────────────────────────────────────────────

def _cluster_to_levels(
    swings: List[SmcSwing],
    kind: str,
    side: str,
    tolerance: float,
    min_touches: int,
    max_count: int,
    symbol: str,
    tf_s: int,
    swept_bars: Optional[List[CandleBar]] = None,
) -> List[SmcLevel]:
    """Кластеризує swing точки за ціновою близькістю → SmcLevel per cluster.

    Алгоритм: жадібна кластеризація по ціні (сортуємо, групуємо в межах tolerance).
    S2: входи відсортовані → детермінований результат.
    swept_bars — бари для перевірки зняття (config levels.hide_swept); None — знятих не відсіювати.
    Знятий кластер відсіюється ДО ліміту, тож на його місце стає наступний живий.
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
        if swept_bars is not None and _is_swept(price, cluster, side, swept_bars, tolerance):
            continue
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
            # ADR-0104 §3.2: у кластера немає періоду — key тримається ціни, як і id
            key=make_level_key("eq%d" % tf_s, side, symbol, str(level_price_key(price))),
            family="liquidity",
            state=LEVEL_STATE_FIXED,
            tier=LEVEL_TIER_CONTEXT,
        ))

    # Сортуємо по touches desc (найзначніші першими), ліміт
    levels.sort(key=lambda l: -l.touches)
    return levels[:max_count]


def _is_swept(price: float, cluster: List[SmcSwing], side: str, bars: List[CandleBar], tolerance: float) -> bool:
    """Пул знято: після ОСТАННЬОГО дотику кластера якийсь бар вийшов за рівень більше ніж на tolerance.

    Новий свінг на тій самій ціні після зняття входить у кластер і стає останнім дотиком — пул відновлено
    (там знову лежать стопи), рівень живий. Вихід у межах tolerance — ще дотик, не зняття.
    """
    last_touch_ms = max(s.time_ms for s in cluster)
    if side == LEVEL_SIDE_HIGH:
        return any(b.h > price + tolerance for b in bars if b.open_time_ms > last_touch_ms)
    return any(b.low < price - tolerance for b in bars if b.open_time_ms > last_touch_ms)


def _mean_price(swings: List[SmcSwing]) -> float:
    """Середня ціна групи swing-точок."""
    return sum(s.price for s in swings) / len(swings)
