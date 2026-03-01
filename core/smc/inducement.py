"""
core/smc/inducement.py — Inducement / False Breakout Trap detection (ADR-0024 §4.7).

Концепт: Smart money навмисно "проводить" ціну через minor swing рівні, забирає
стопи retail-трейдерів, а потім розвертає ринок. Сигнал: wick пробиває minor
level, але close повертається — і наступні бари підтверджують рух назад.

  inducement_bear: minor SH пробитий (wick above, close below) → retail buys trapped
                   →확인 (reversal down ≥ reversal_atr_mult × ATR за confirmation_bars)
  inducement_bull: minor SL пробитий (wick below, close above) → retail sells trapped
                   → підтвердження (reversal up ≥ reversal_atr_mult × ATR)

S0: pure logic, NO I/O.
S2: deterministic — same bars → same inducements.
S5: параметри з SmcConfig (config.json:smc.inducement).

Python 3.7 compatible.
"""
from __future__ import annotations

from typing import Dict, List, Set

from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.swings import compute_atr, detect_raw_swings
from core.smc.types import SmcSwing, make_swing_id

# Максимальне вікно пошуку trap candle після minor swing (барів)
_SEARCH_WINDOW = 20


def detect_inducement(
    bars: List[CandleBar],
    classified: List[SmcSwing],  # noqa: ARG001 — для однорідності підпису
    config: SmcConfig,
    atr: float = 0.0,       # F4: caller-supplied ATR (0 → compute internally)
) -> List[SmcSwing]:
    """Виявляє inducement (false breakout trap) на основі minor swings.

    Алгоритм:
      1. Detect minor swings з period=minor_period (менший за основний swing_period).
      2. Для кожного minor Swing High:
         - Шукаємо trap candle: wick > SH і close < SH (wick break + rejection).
         - Перевіряємо reversal: за confirmation_bars після trap — drop ≥ thresh.
         - Якщо підтверджено → SmcSwing(kind="inducement_bear").
      3. Для кожного minor Swing Low — аналогічно (inducement_bull).
      4. Обмеження max_inducements (останні N за часом).

    Args:
        bars: OHLCV bars (мінімум 2*minor_period + confirmation_bars + 1).
        classified: classified swings з structure.py (не використовуємо напряму,
                    інтерфейс уніфіковано з іншими E2 детекторами).
        config: SmcConfig.

    Returns:
        List[SmcSwing] відсортований за time_ms, кожен kind ∈ SWING_KINDS.
    """
    cfg = config.inducement
    if not cfg.enabled:
        return []

    min_bars = 2 * cfg.minor_period + cfg.confirmation_bars + 2
    if len(bars) < min_bars:
        return []

    # Minor swings із меншим period (знаходить більше, дрібних рівнів)
    minor_swings = detect_raw_swings(bars, period=cfg.minor_period)
    if not minor_swings:
        return []

    if atr <= 0.0:
        atr = compute_atr(bars)
    reversal_thresh = cfg.reversal_atr_mult * atr

    # Rail: якщо ATR некоректний — пропускаємо (I5 degraded-but-loud не тут,
    # бо це pure core — caller логує якщо треба)
    if reversal_thresh <= 0.0:
        return []

    # Індекс bar_idx: open_time_ms → позиція у bars[]
    bar_idx: Dict[int, int] = {b.open_time_ms: i for i, b in enumerate(bars)}

    results: List[SmcSwing] = []
    seen_ids: Set[str] = set()

    # ── Scan minor SH → inducement_bear ──────────────────────────
    for ms in minor_swings:
        if ms.kind != "hh":          # raw detect_raw_swings повертає "hh" / "ll"
            continue
        ms_i = bar_idx.get(ms.time_ms)
        if ms_i is None:
            continue
        # Шукаємо trap candle після minor SH
        end_search = min(len(bars), ms_i + 1 + _SEARCH_WINDOW)
        for j in range(ms_i + 1, end_search):
            b = bars[j]
            if b.h > ms.price and b.c < ms.price:
                # Trap candle знайдений: wick пробив SH, close нижче
                # Перевіряємо reversal за confirmation_bars після trap
                reversal = 0.0
                end_conf = min(len(bars), j + 1 + cfg.confirmation_bars)
                for k in range(j + 1, end_conf):
                    drop = ms.price - bars[k].c
                    if drop > reversal:
                        reversal = drop
                if reversal >= reversal_thresh:
                    ind_id = make_swing_id(
                        "inducement_bear", b.symbol, b.tf_s, b.open_time_ms
                    )
                    if ind_id not in seen_ids:
                        seen_ids.add(ind_id)
                        results.append(SmcSwing(
                            id=ind_id,
                            symbol=b.symbol,
                            tf_s=b.tf_s,
                            kind="inducement_bear",
                            price=ms.price,    # ціна minor SH (рівень trap)
                            time_ms=b.open_time_ms,
                            confirmed=True,
                        ))
                break  # один trap на minor swing — не шукаємо далі

    # ── Scan minor SL → inducement_bull ──────────────────────────
    for ms in minor_swings:
        if ms.kind != "ll":
            continue
        ms_i = bar_idx.get(ms.time_ms)
        if ms_i is None:
            continue
        end_search = min(len(bars), ms_i + 1 + _SEARCH_WINDOW)
        for j in range(ms_i + 1, end_search):
            b = bars[j]
            if b.low < ms.price and b.c > ms.price:
                # Trap candle: wick пробив SL, close вище
                reversal = 0.0
                end_conf = min(len(bars), j + 1 + cfg.confirmation_bars)
                for k in range(j + 1, end_conf):
                    rise = bars[k].c - ms.price
                    if rise > reversal:
                        reversal = rise
                if reversal >= reversal_thresh:
                    ind_id = make_swing_id(
                        "inducement_bull", b.symbol, b.tf_s, b.open_time_ms
                    )
                    if ind_id not in seen_ids:
                        seen_ids.add(ind_id)
                        results.append(SmcSwing(
                            id=ind_id,
                            symbol=b.symbol,
                            tf_s=b.tf_s,
                            kind="inducement_bull",
                            price=ms.price,    # ціна minor SL (рівень trap)
                            time_ms=b.open_time_ms,
                            confirmed=True,
                        ))
                break

    results.sort(key=lambda s: s.time_ms)

    # Обмеження: залишаємо останні max_inducements (найактуальніші)
    if len(results) > cfg.max_inducements:
        results = results[-cfg.max_inducements:]

    return results
