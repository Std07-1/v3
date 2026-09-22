"""
runtime/ws/candle_map.py — Конвертація v3 bar dict → ui_v4 Candle dict.

Закриває Risk R2 (mapping confusion) назавжди.
Обробляє ОБА формати: LWC (read_window) та SHORT (CandleBar.to_dict / event.bar).

Еталон: runtime/ws/ws_server.py _normalize_bar_window_v1().
Контракт виходу: types.ts:8-15 Candle {t_ms, o, h, l, c, v}.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple, cast

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
        _log.debug(
            "CANDLE_MAP_PICK_PARSE_FAILED primary=%s fallback=%s raw=%r",
            primary,
            fallback,
            val,
            exc_info=True,
        )
        return None


def _is_display_flat_bar(bar: dict) -> bool:
    """Ховати бар = ЛИШЕ явна позначка інжесту `calendar_pause_flat` (артефакт паузи/вихідних від брокера).

    Геометрія O=H=L=C — НЕ критерій відсутності: торгова хвилина законно пласка (один тік; у режимі
    PREVIOUS_CLOSE — коли той тік дорівнює close перед ним), і таких багато (вимір 21.09 на проді, дані
    епохи FIRST_TICK: EUSTX50 5287/106293 M1 ховалось, 4.97%, з них 5156 усередині сесії; GER30 675).
    Правило не залежить від режиму ціни відкриття (ADR-0096, підтверджено ADR-0100): «flat + v≤10 →
    сховати» видаляло справжні часові точки з серії — це і був «рваний графік», а поріг 10 ще й ширший
    за інжестовий 4 (бар v=5..10 інжест взагалі
    не вважає пласким). Binance зберігає пласкі бакети і в klines, і в uiKlines; у LWC плаский OHLC —
    валідний data point, «нема торгів» виражається окремим whitespace. Існування бару визначає
    upstream-класифікація (m1_session_filter: артефакт → маркер або не пишеться), display не
    перекласифіковує за геометрією і днем тижня.
    """
    ext = bar.get("extensions", {})
    return isinstance(ext, dict) and bool(ext.get("calendar_pause_flat"))


def map_bar_to_candle_v4(bar: dict, *, tf_s: int = 0) -> Optional[dict]:
    """Конвертує один v3 bar dict → ui_v4 Candle dict або None (rejected).

    Вхід: LWC dict (open/high/low/close/volume/open_time_ms) АБО
          SHORT dict (o/h/low/c/v/open_time_ms).
    Вихід: {"t_ms": int, "o": float, "h": float, "l": float, "c": float, "v": float}
    Ховаються лише бари з явним маркером `calendar_pause_flat` — однаково для всіх TF; жодної
    перекласифікації за геометрією, обсягом чи днем тижня (див. _is_display_flat_bar).
    """
    if not isinstance(bar, dict):
        _log.warning("CANDLE_MAP_REJECT reason=not_dict type=%s", type(bar).__name__)
        return None

    # Display-фільтр (SSOT не змінюється): лише явний маркер інжесту
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
        _log.warning(
            "CANDLE_MAP_REJECT reason=bad_t_ms raw=%s", bar.get("open_time_ms")
        )
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
            o,
            h,
            low,
            c,
            t_ms,
        )
        return None

    o = float(cast(float, o))
    h = float(cast(float, h))
    low = float(cast(float, low))
    c = float(cast(float, c))

    # --- Tail normalization (safety net) ---
    # Навіть якщо source нормалізований — display-шар гарантує що
    # LWC ніколи не отримає h < close або low > open.
    h = max(o, h, low, c)
    low = min(o, h, low, c)

    # --- Volume (optional, default 0; clamp negative) ---
    v = _pick(bar, "volume", "v")
    if v is None or v < 0.0:
        v = 0.0

    return {"t_ms": t_ms, "o": o, "h": h, "l": low, "c": c, "v": v}


def map_bars_to_candles_v4(
    bars: List[dict],
    *,
    tf_s: int = 0,
) -> Tuple[List[dict], int]:
    """Batch-конвертація барів → candles.

    Args:
        bars: список v3 bar dicts.
        tf_s: TF у секундах (для HTF flat filter skip).
    Returns: (candles, dropped_count).
    Caller має додати warning якщо dropped > 0 (degraded-but-loud).
    """
    candles: List[dict] = []
    dropped = 0
    for bar in bars:
        candle = map_bar_to_candle_v4(bar, tf_s=tf_s)
        if candle is None:
            dropped += 1
        else:
            candles.append(candle)
    return candles, dropped
