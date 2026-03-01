"""
core/smc/key_levels.py — Key Price Levels per TF (ADR-0024b).

Обчислює горизонтальні цінові якорі для intraday стратегії:
  - Previous candle High/Low (PDH/PDL для D1, prev H4 H/L, prev H1 H/L, ...)
  - Current candle running High/Low (DH/DL, H4H/H4L, H1H/H1L, ...)

TF ієрархія для рівнів:
  D1   — глобальний контекст (PDH/PDL/DH/DL)
  H4   — глобальний контекст
  H1   — контекст та аналіз
  M30  — контекст
  M15  — підтвердження входу

S0: pure logic, NO I/O.
S2: deterministic — same bars → same levels.
S5: TF map — SSOT тут, не hardcoded в десяти місцях.

Python 3.7 compatible.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from core.model.bars import CandleBar
from core.smc.types import SmcLevel, make_level_id

# ── TF → (prev_high_kind, prev_low_kind, curr_high_kind, curr_low_kind) ──
# Тільки стратегічно значущі TF для intraday (M15+).
# M1/M3/M5 не генерують key levels (їх prev candle не є стратегічним якорем),
# але ВІДОБРАЖАЮТЬ HTF levels через cross-TF ін'єкцію.
_TF_KEY_LEVEL_MAP = {
    86400:  ("pdh",     "pdl",     "dh",     "dl"),      # D1: Previous/Current Day
    14400:  ("p_h4_h",  "p_h4_l",  "h4_h",   "h4_l"),    # H4
    3600:   ("p_h1_h",  "p_h1_l",  "h1_h",   "h1_l"),    # H1
    1800:   ("p_m30_h", "p_m30_l", "m30_h",  "m30_l"),   # M30
    900:    ("p_m15_h", "p_m15_l", "m15_h",  "m15_l"),   # M15
}  # type: Dict[int, Tuple[str, str, str, str]]

# Усі kinds, що генеруються цим модулем (для LEVEL_KINDS union)
KEY_LEVEL_KINDS = frozenset(
    kind
    for kinds in _TF_KEY_LEVEL_MAP.values()
    for kind in kinds
)

# HTF шари, з яких ін'єктуються рівні на нижчі ТФ (sorted desc)
_HTF_INJECT_ORDER = [86400, 14400, 3600, 1800, 900]


def compute_key_levels(bars: List[CandleBar]) -> List[SmcLevel]:
    """Обчислює key levels з серії барів одного TF.

    Повертає 2-4 рівні:
      - prev candle H/L (тільки якщо є ≥1 completed bar)
      - current candle running H/L (останній бар, якщо відрізняється від prev)

    Args:
        bars: відсортовані бари одного (symbol, tf_s), oldest first.

    Returns:
        Список SmcLevel. Порожній якщо TF не має key level map або недостатньо даних.
    """
    if not bars:
        return []

    tf_s = bars[0].tf_s
    kinds = _TF_KEY_LEVEL_MAP.get(tf_s)
    if kinds is None:
        return []

    symbol = bars[0].symbol
    prev_h_kind, prev_l_kind, curr_h_kind, curr_l_kind = kinds

    # Знайти останній completed бар
    completed = [b for b in bars if b.complete]
    if not completed:
        return []

    prev = completed[-1]  # Останній завершений бар
    last = bars[-1]       # Останній бар (може бути incomplete/preview)

    levels = []  # type: List[SmcLevel]

    # ── Previous candle High/Low ──
    levels.append(SmcLevel(
        id=make_level_id(prev_h_kind, symbol, tf_s, prev.h),
        symbol=symbol,
        tf_s=tf_s,
        kind=prev_h_kind,
        price=prev.h,
        time_ms=prev.open_time_ms,
        touches=1,
    ))
    levels.append(SmcLevel(
        id=make_level_id(prev_l_kind, symbol, tf_s, prev.low),
        symbol=symbol,
        tf_s=tf_s,
        kind=prev_l_kind,
        price=prev.low,
        time_ms=prev.open_time_ms,
        touches=1,
    ))

    # ── Current candle running High/Low ──
    # Показуємо тільки якщо поточний бар відрізняється від prev (новий)
    if last.open_time_ms != prev.open_time_ms:
        levels.append(SmcLevel(
            id=make_level_id(curr_h_kind, symbol, tf_s, last.h),
            symbol=symbol,
            tf_s=tf_s,
            kind=curr_h_kind,
            price=last.h,
            time_ms=last.open_time_ms,
            touches=1,
        ))
        levels.append(SmcLevel(
            id=make_level_id(curr_l_kind, symbol, tf_s, last.low),
            symbol=symbol,
            tf_s=tf_s,
            kind=curr_l_kind,
            price=last.low,
            time_ms=last.open_time_ms,
            touches=1,
        ))

    return levels


def collect_htf_levels(
    get_snapshot_fn,   # Callable[[str, int], Optional[SmcSnapshot]]
    symbol: str,
    viewing_tf_s: int,
) -> List[SmcLevel]:
    """Збирає key levels з ТФ вищих за viewing_tf_s.

    Використовується для cross-TF ін'єкції: коли трейдер дивиться на M15,
    він бачить D1/H4/H1 рівні як контекст.

    Args:
        get_snapshot_fn: функція (symbol, tf_s) → SmcSnapshot | None
        symbol: торговий символ
        viewing_tf_s: TF chart-у що відображається

    Returns:
        Список SmcLevel з усіх вищих TF. Без дублікатів по id.
    """
    seen_ids = set()  # type: set
    htf_levels = []   # type: List[SmcLevel]

    for htf_s in _HTF_INJECT_ORDER:
        if htf_s <= viewing_tf_s:
            continue  # Тільки ВИЩІ TF

        snap = get_snapshot_fn(symbol, htf_s)
        if snap is None:
            continue

        for lv in snap.levels:
            # Ін'єктуємо тільки key levels (не eq_highs/eq_lows з чужого TF)
            if lv.kind in KEY_LEVEL_KINDS and lv.id not in seen_ids:
                seen_ids.add(lv.id)
                htf_levels.append(lv)

    return htf_levels
