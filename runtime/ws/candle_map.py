"""
runtime/ws/candle_map.py — Конвертація v3 bar dict → ui_v4 Candle dict.

Закриває Risk R2 (mapping confusion) назавжди.
Обробляє ОБА формати: LWC (read_window) та SHORT (CandleBar.to_dict / event.bar).

Еталон: ui_chart_v3/server.py:692 _normalize_bar_window_v1().
Контракт виходу: types.ts:8-15 Candle {t_ms, o, h, l, c, v}.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

_log = logging.getLogger(__name__)


def _pick(bar: dict, primary: str, fallback: str) -> Optional[float]:
    """Вибирає числове значення з bar: спочатку primary, потім fallback."""
    val = bar.get(primary)
    if val is None:
        val = bar.get(fallback)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _is_display_flat_bar(bar: dict) -> bool:
    """Flat bar = O==H==L==C + low volume (≤4). Weekend/pause artifact від брокера.

    Фільтрується на рівні display (не SSOT). Відповідає logic
    m1_poller._is_flat + overlay._is_flat_preview_bar.
    calendar_pause_flat extension теж рахується як flat.
    """
    ext = bar.get("extensions", {})
    if isinstance(ext, dict) and ext.get("calendar_pause_flat"):
        return True
    o = bar.get("open", bar.get("o"))
    h = bar.get("high", bar.get("h"))
    lo = bar.get("low", bar.get("l"))
    c = bar.get("close", bar.get("c"))
    v = bar.get("volume", bar.get("v", 0.0))
    if all(isinstance(x, (int, float)) for x in (o, h, lo, c)):
        if o == h == lo == c and float(v) <= 4.0:
            return True
    return False


def map_bar_to_candle_v4(bar: dict) -> Optional[dict]:
    """Конвертує один v3 bar dict → ui_v4 Candle dict або None (rejected).

    Вхід: LWC dict (open/high/low/close/volume/open_time_ms) АБО
          SHORT dict (o/h/low/c/v/open_time_ms).
    Вихід: {"t_ms": int, "o": float, "h": float, "l": float, "c": float, "v": float}
    Flat бари (O==H==L==C, v≤4, calendar_pause_flat) фільтруються (I5: degraded-but-loud).
    """
    if not isinstance(bar, dict):
        _log.warning("CANDLE_MAP_REJECT reason=not_dict type=%s", type(bar).__name__)
        return None

    # Flat bar filter — display-only (SSOT не змінюється)
    if _is_display_flat_bar(bar):
        return None

    # --- t_ms (epoch ms) ---
    t_ms = bar.get("open_time_ms")
    if t_ms is None:
        t_ms = bar.get("open_ms")
    if t_ms is None:
        time_sec = bar.get("time")
        if isinstance(time_sec, (int, float)):
            t_ms = int(time_sec) * 1000
    if not isinstance(t_ms, (int, float)) or t_ms <= 0:
        _log.warning("CANDLE_MAP_REJECT reason=bad_t_ms raw=%s", bar.get("open_time_ms"))
        return None
    t_ms = int(t_ms)

    # --- OHLC (price) ---
    o = _pick(bar, "open", "o")
    h = _pick(bar, "high", "h")
    low = _pick(bar, "low", "l")  # NOTE: CandleBar uses "low", preview може "l"
    c = _pick(bar, "close", "c")

    if None in (o, h, low, c):
        _log.warning(
            "CANDLE_MAP_REJECT reason=missing_ohlc o=%s h=%s l=%s c=%s t_ms=%s",
            o, h, low, c, t_ms,
        )
        return None

    # --- Volume (optional, default 0) ---
    v = _pick(bar, "volume", "v")
    if v is None:
        v = 0.0

    return {"t_ms": t_ms, "o": o, "h": h, "l": low, "c": c, "v": v}


def map_bars_to_candles_v4(
    bars: List[dict],
) -> Tuple[List[dict], int]:
    """Batch-конвертація барів → candles.

    Returns: (candles, dropped_count).
    Caller має додати warning якщо dropped > 0 (degraded-but-loud).
    """
    candles: List[dict] = []
    dropped = 0
    for bar in bars:
        candle = map_bar_to_candle_v4(bar)
        if candle is None:
            dropped += 1
        else:
            candles.append(candle)
    return candles, dropped
