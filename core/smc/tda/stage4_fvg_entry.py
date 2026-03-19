"""
core/smc/tda/stage4_fvg_entry.py — Stage 4: M15 FVG Entry (ADR-0040 §4.4).

Detects Fair Value Gaps on M15 timeframe in the post-sweep search window
and finds entry triggers (touch + close outside FVG).

Алгоритм (з TDA simulation):
  1. Фільтр M15 барів за search window (09:00–16:00 UTC)
  2. Пошук 3-bar FVG gaps відповідного напрямку
  3. Фільтр по min size та proximity до sweep_price
  4. Вибір найбільшого FVG
  5. Пошук entry: бар торкається FVG зони і закривається ЗОВНІ
  6. Обчислення SL/TP/R:R з config-driven параметрами
  7. Gate: R:R ≥ min_rr

Інваріанти:
  S0: pure logic, NO I/O
  S2: deterministic — same bars + config → same result
  S5: all thresholds from TdaCascadeConfig
"""

from __future__ import annotations

from typing import Optional

from core.model.bars import CandleBar
from core.smc.tda.types import FvgEntry, TdaCascadeConfig

_MS_PER_HOUR = 3_600_000


# ═══════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════


def find_fvg_entry(
    m15_bars: list[CandleBar],
    fvg_direction: str,
    sweep_price: float,
    day_ms: int,
    cfg: TdaCascadeConfig,
) -> Optional[FvgEntry]:
    """Find M15 FVG entry in post-sweep search window.

    Args:
        m15_bars: M15 бари (будь-який діапазон, фільтруються по часу).
        fvg_direction: "BULL" або "BEAR" — який FVG шукати.
        sweep_price: Asia рівень що був swept (якір proximity filter).
        day_ms: Day anchor ms (00:00 UTC торгового дня).
        cfg: TDA cascade config.

    Returns:
        FvgEntry якщо entry знайдений, None інакше.
    """
    search_start_ms = day_ms + cfg.entry_search_start_utc * _MS_PER_HOUR
    search_end_ms = day_ms + cfg.entry_search_end_utc * _MS_PER_HOUR

    # Фільтр M15 барів у search window
    window = [b for b in m15_bars if search_start_ms <= b.open_time_ms <= search_end_ms]
    if len(window) < 3:
        return None

    # --- Крок 1: Пошук FVG кандидатів ---
    fvg = _find_best_fvg(window, fvg_direction, sweep_price, cfg)
    if fvg is None:
        return None

    fvg_high, fvg_low, fvg_size, formed_ms = fvg

    # --- Крок 2: Пошук entry trigger ---
    # Entry window: від FVG формування до search_end
    entry_bars = [b for b in window if b.open_time_ms >= formed_ms]
    return _find_entry_trigger(
        entry_bars,
        fvg_direction,
        fvg_high,
        fvg_low,
        fvg_size,
        cfg,
    )


# ═══════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════


def _find_best_fvg(
    bars: list[CandleBar],
    direction: str,
    sweep_price: float,
    cfg: TdaCascadeConfig,
) -> Optional[tuple[float, float, float, int]]:
    """Знайти найбільший FVG кандидат відповідного напрямку.

    Returns: (fvg_high, fvg_low, size, formed_ms) або None.
    """
    candidates: list[tuple[float, float, float, int]] = []

    for i in range(1, len(bars) - 1):
        b0, b1, b2 = bars[i - 1], bars[i], bars[i + 1]

        if direction == "BULL":
            # Bullish FVG: gap між b0.h і b2.low (gap up)
            fvg_low = b0.h
            fvg_high = b2.low
            if fvg_high > fvg_low and b1.c > b1.o:
                size = fvg_high - fvg_low
                mid = (fvg_high + fvg_low) / 2
                if (
                    size >= cfg.fvg_min_abs_pts
                    and abs(mid - sweep_price) < cfg.fvg_proximity_pts
                ):
                    candidates.append((fvg_high, fvg_low, size, b2.open_time_ms))
        else:  # BEAR
            # Bearish FVG: gap між b0.low і b2.h (gap down)
            fvg_high = b0.low
            fvg_low = b2.h
            if fvg_high > fvg_low and b1.c < b1.o:
                size = fvg_high - fvg_low
                mid = (fvg_high + fvg_low) / 2
                if (
                    size >= cfg.fvg_min_abs_pts
                    and abs(mid - sweep_price) < cfg.fvg_proximity_pts
                ):
                    candidates.append((fvg_high, fvg_low, size, b2.open_time_ms))

    if not candidates:
        return None

    # Обираємо найбільший FVG
    candidates.sort(key=lambda x: -x[2])
    return candidates[0]


def _find_entry_trigger(
    bars: list[CandleBar],
    direction: str,
    fvg_high: float,
    fvg_low: float,
    fvg_size: float,
    cfg: TdaCascadeConfig,
) -> Optional[FvgEntry]:
    """Знайти перший бар що торкається FVG і закривається зовні.

    Returns: FvgEntry з SL/TP/R:R або None.
    """
    for bar in bars:
        if direction == "BULL":
            touched = bar.low <= fvg_high
            closed_outside = bar.c > fvg_high
            not_engulfed = bar.c >= fvg_low
            if touched and closed_outside and not_engulfed:
                entry_price = bar.c
                sl = fvg_low - fvg_size * cfg.sl_buffer_ratio
                sl_size = entry_price - sl
                if sl_size <= 0:
                    continue
                tp = entry_price + sl_size * cfg.rr_target
                rr = cfg.rr_target
                if rr < cfg.min_rr:
                    continue
                return FvgEntry(
                    entry_price=entry_price,
                    stop_loss=sl,
                    take_profit=tp,
                    risk_reward=rr,
                    direction="LONG",
                    fvg_high=fvg_high,
                    fvg_low=fvg_low,
                    fvg_size=fvg_size,
                    entry_bar_ms=bar.open_time_ms,
                )
        else:  # BEAR
            touched = bar.h >= fvg_low
            closed_outside = bar.c < fvg_low
            not_engulfed = bar.c <= fvg_high
            if touched and closed_outside and not_engulfed:
                entry_price = bar.c
                sl = fvg_high + fvg_size * cfg.sl_buffer_ratio
                sl_size = sl - entry_price
                if sl_size <= 0:
                    continue
                tp = entry_price - sl_size * cfg.rr_target
                rr = cfg.rr_target
                if rr < cfg.min_rr:
                    continue
                return FvgEntry(
                    entry_price=entry_price,
                    stop_loss=sl,
                    take_profit=tp,
                    risk_reward=rr,
                    direction="SHORT",
                    fvg_high=fvg_high,
                    fvg_low=fvg_low,
                    fvg_size=fvg_size,
                    entry_bar_ms=bar.open_time_ms,
                )
    return None
