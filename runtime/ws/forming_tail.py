"""
runtime/ws/forming_tail.py — формуюча свічка в хвості full-кадру (D4, розслідування 21.09).

Full-кадр читає лише фінальну площину (UDS.read_window), тож формуючої свічки в ньому нема
на жодному TF: вона з'являлась лише з другою delta (~2 с після connect/switch). Тут preview-бар
(UDS.read_preview_window — лише Redis preview:curr/tail) додається В ХВІСТ candles кадру, якщо
це справді формуюча свічка поточного бакета.

Навмисно НЕ мержимо preview в UDS.read_window: NoMix (I3) для SMC/API-споживачів read_window.
Фінал завжди перемагає: preview-бар бакета, для якого фінал уже є, відкидається.

«Прострочений» preview = бар, чий бакет [open, open + tf) не містить поточного часу (preview
завис на старому бакеті або годинник з майбутнього). Поза цим свіжість тримає TTL Redis
(preview_curr_ttl_s; tail = 2x): ключі зникли → preview порожній → формуючої нема.

Pure: select_forming_candle. Impure (Redis через UDS, blocking — лише в executor): read_forming_candle.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional, Sequence

from runtime.ws.candle_map import map_bar_to_candle_v4

_log = logging.getLogger(__name__)


def _candle_shape_ok(candle: dict) -> bool:
    """Формуюча свічка обходить output guard full-кадру, тож форму перевіряємо тут: скінченні o/h/l/c, l ≤ min(o,c), h ≥ max(o,c)."""
    prices = [candle.get(key) for key in ("o", "h", "l", "c")]
    if not all(isinstance(p, (int, float)) and math.isfinite(p) for p in prices):
        return False
    open_px, high_px, low_px, close_px = prices
    return low_px <= min(open_px, close_px) and high_px >= max(open_px, close_px)


def select_forming_candle(
    final_candles: Sequence[dict],
    preview_bars: Sequence[dict],
    *,
    tf_s: int,
    now_ms: int,
) -> Optional[dict]:
    """Формуюча свічка (формат candle_map: t_ms/o/h/l/c/v) для хвоста full-кадру або None.

    None, якщо: preview порожній; останній preview-бар complete; його бакет не містить now_ms;
    фінал цього чи пізнішого бакета вже є серед final_candles (I3: final > preview).
    """
    if not preview_bars or tf_s <= 0:
        return None
    preview_bar = preview_bars[-1]
    if not isinstance(preview_bar, dict) or preview_bar.get("complete", False):
        return None
    candle = map_bar_to_candle_v4(preview_bar, tf_s=tf_s)
    if candle is None:
        return None
    if not _candle_shape_ok(candle):
        _log.warning("WS_FORMING_TAIL_BAD_SHAPE tf_s=%s candle=%s", tf_s, candle)
        return None
    open_ms = candle["t_ms"]
    if not open_ms <= now_ms < open_ms + tf_s * 1000:
        return None
    if final_candles:
        last_final_ms = final_candles[-1].get("t_ms")
        if isinstance(last_final_ms, (int, float)) and open_ms <= last_final_ms:
            return None
    return candle


def read_forming_candle(
    uds: Any,
    symbol: str,
    tf_s: int,
    final_candles: Sequence[dict],
    now_ms: int,
) -> Optional[dict]:
    """Читає поточний preview-бар (limit=1) і відбирає формуючу. Blocking I/O — викликати в executor."""
    window = uds.read_preview_window(symbol, tf_s, 1)
    preview_warnings = getattr(window, "warnings", None)
    if preview_warnings:
        _log.warning("WS_FORMING_TAIL_PREVIEW_WARNINGS sym=%s tf_s=%s warnings=%s", symbol, tf_s, preview_warnings)
    return select_forming_candle(
        final_candles,
        getattr(window, "bars_lwc", None) or [],
        tf_s=tf_s,
        now_ms=now_ms,
    )
