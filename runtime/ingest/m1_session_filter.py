"""Єдине правило «чи йде M1 від брокера в SSOT» за календарем сесії.

Брокер віддає пласкі свічки (O=H=L=C, малий обсяг) і тоді, коли ринок закритий — після закриття сесії, у вихідні.
TradingView таких барів не показує, а в SSOT вони ламають графік. Правило одне для всіх записувачів M1:
- торгова хвилина → бар іде як є; плаский — з маркером `trading_flat`;
- хвилина паузи, плаский бар → не записується (шум брокера);
- хвилина паузи, неплаский бар → записується з маркером `calendar_pause_nonflat_anomaly`, записувач кричить у лог
  (ознака хибного календаря або DST, мовчки викидати не можна);
- ПЕРША торгова хвилина сесії, плаский бар → не записується: у брокера в цю мить ще немає тіків, і він віддає
  заглушку з ціною закриття попередньої сесії (17.09 22:00 NAS100 і SPX500: O=H=L=C, v=3, і той самий запит за
  12 хвилин уже не повертав цієї хвилини взагалі). Вимір на проді: 110 барів хвилини перевідкриття NAS100 мають
  обсяг 229…5811 (медіана 1482) і ненульовий діапазон — заглушка відрізняється на порядки, тож правило вузьке.

Модуль чистий і сумісний з Python 3.7: ним користуються і живий M1-полер, і `tools/fetch_tf_backfill` у .venv37.
"""
from __future__ import annotations

import dataclasses
from typing import Callable, List, Optional, Tuple

from core.model.bars import CandleBar

# SSOT порогу: config.json → flat_bar_max_volume; це лише дефолт, коли ключа нема.
FLAT_BAR_MAX_VOLUME_DEFAULT = 4

VERDICT_TRADING = "trading"
VERDICT_TRADING_FLAT = "trading_flat"
VERDICT_PAUSE_FLAT_DROPPED = "pause_flat_dropped"
VERDICT_PAUSE_NONFLAT_ANOMALY = "pause_nonflat_anomaly"
VERDICT_REOPEN_FLAT_DROPPED = "reopen_flat_dropped"


# Запас, який дає брокеру M1-полер, перш ніж вважати хвилину закритою: у цьому вікні FXCM ще доправляє щойно
# закриту хвилину. SSOT — config.json `m1_poller.safety_delay_s`.
_CLOSE_SAFETY_S_DEFAULT = 8


def resolve_close_safety_ms(cfg: dict) -> int:
    m1_cfg = cfg.get("m1_poller")
    raw = m1_cfg.get("safety_delay_s", _CLOSE_SAFETY_S_DEFAULT) if isinstance(m1_cfg, dict) else _CLOSE_SAFETY_S_DEFAULT
    try:
        return max(0, int(raw)) * 1000
    except (TypeError, ValueError):
        return _CLOSE_SAFETY_S_DEFAULT * 1000


def split_closed_bars(bars: List[CandleBar], now_ms: int, safety_ms: int) -> Tuple[List[CandleBar], List[CandleBar]]:
    """Ділить партію на закриті (close + запас <= now) і ті, що ще формуються або щойно закрились."""
    closed = [b for b in bars if b.close_time_ms + safety_ms <= now_ms]
    unclosed = [b for b in bars if b.close_time_ms + safety_ms > now_ms]
    return closed, unclosed


def resolve_flat_max_volume(cfg: dict) -> int:
    """Порог пласкості з config (SSOT `flat_bar_max_volume`), з тим самим clamp, що й у полері."""
    raw = cfg.get("flat_bar_max_volume")
    if raw is None:
        return FLAT_BAR_MAX_VOLUME_DEFAULT
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return FLAT_BAR_MAX_VOLUME_DEFAULT


def is_flat_m1(bar: CandleBar, flat_max_volume: int) -> bool:
    return bar.o == bar.h == bar.low == bar.c and bar.v <= flat_max_volume


def is_session_open_minute(open_ms: int, is_trading_fn: Callable[[int], bool]) -> bool:
    """Перша торгова хвилина сесії: сама торгова, а попередня — ні (перевідкриття після перерви чи вихідних)."""
    return is_trading_fn(open_ms) and not is_trading_fn(open_ms - 60_000)


def classify_m1_for_ssot(bar: CandleBar, trading: bool, flat_max_volume: int,
                         session_open_minute: bool = False) -> Tuple[Optional[CandleBar], str]:
    """Повертає (бар для запису або None, вердикт). Вхідний бар не змінюється."""
    flat = is_flat_m1(bar, flat_max_volume)
    if trading:
        if not flat:
            return bar, VERDICT_TRADING
        if session_open_minute:
            return None, VERDICT_REOPEN_FLAT_DROPPED
        return _with_marker(bar, "trading_flat"), VERDICT_TRADING_FLAT
    if flat:
        return None, VERDICT_PAUSE_FLAT_DROPPED
    return _with_marker(bar, "calendar_pause_nonflat_anomaly"), VERDICT_PAUSE_NONFLAT_ANOMALY


def _with_marker(bar: CandleBar, marker: str) -> CandleBar:
    return dataclasses.replace(bar, extensions={**bar.extensions, marker: True})
