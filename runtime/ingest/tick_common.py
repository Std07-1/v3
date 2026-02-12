"""Спільні утиліти для tick_publisher та tick_preview_worker (DRY).

Функції тут — чисті (без I/O крім логування), залежать лише від
core.config_loader.env_str та runtime.ingest.market_calendar.MarketCalendar.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from core.config_loader import env_str
from runtime.ingest.market_calendar import MarketCalendar


# ---------------------------------------------------------------------------
# Канал tick (config.json > ENV > legacy ENV)
# ---------------------------------------------------------------------------
def pick_tick_channel(cfg: dict[str, Any] | None = None) -> Optional[str]:
    """Повертає ім'я Redis PubSub каналу для тиків."""
    if cfg:
        channels = cfg.get("channels")
        if isinstance(channels, dict):
            ch = channels.get("price_tick")
            if ch:
                return str(ch)
    channel = env_str("FXCM_PRICE_TICK_CHANNEL")
    if channel:
        return channel
    legacy = env_str("FXCM_PRICE_SNAPSHOT_CHANNEL")
    if legacy:
        logging.warning(
            "tick_common: FXCM_PRICE_TICK_CHANNEL не заданий, "
            "fallback до FXCM_PRICE_SNAPSHOT_CHANNEL"
        )
        return legacy
    return None


# ---------------------------------------------------------------------------
# Символи з конфігу
# ---------------------------------------------------------------------------
def symbols_from_cfg(cfg: dict[str, Any]) -> list[str]:
    """Повертає список символів з config.json (symbols[] або symbol)."""
    raw = cfg.get("symbols")
    if isinstance(raw, list) and raw:
        out = [str(x) for x in raw if str(x).strip()]
        if out:
            return out
    symbol = cfg.get("symbol")
    return [str(symbol)] if symbol else []


# ---------------------------------------------------------------------------
# Маппінг символів (canonical ↔ alias)
# ---------------------------------------------------------------------------
def build_symbol_aliases(symbols: Iterable[str]) -> dict[str, str]:
    """Будує маппінг alias→canonical (XAU/USD, XAUUSD, XAU_USD → XAU/USD)."""
    aliases: dict[str, str] = {}
    for sym in symbols:
        canon = str(sym).strip()
        if not canon:
            continue
        aliases[canon] = canon
        aliases[canon.replace("/", "")] = canon
        aliases[canon.replace("/", "_")] = canon
    return aliases


# ---------------------------------------------------------------------------
# Timestamp → epoch ms
# ---------------------------------------------------------------------------
def to_ms(raw: Any) -> Optional[int]:
    """Конвертує raw значення у epoch milliseconds (auto-detect sec vs ms)."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except Exception:
        return None
    if value <= 0:
        return None
    if value < 100_000_000_000:
        value *= 1000.0
    return int(value)


# ---------------------------------------------------------------------------
# Побудова MarketCalendar з config group
# ---------------------------------------------------------------------------
def calendar_from_group(group_cfg: dict) -> Optional[MarketCalendar]:
    """Побудова MarketCalendar з секції market_calendar_by_group."""
    try:
        daily_breaks_raw = group_cfg.get("market_daily_breaks", [])
        daily_breaks = tuple(
            (str(pair[0]), str(pair[1]))
            for pair in daily_breaks_raw
            if isinstance(pair, (list, tuple)) and len(pair) >= 2
        )
        return MarketCalendar(
            enabled=True,
            weekend_close_dow=int(group_cfg["market_weekend_close_dow"]),
            weekend_close_hm=str(group_cfg["market_weekend_close_hm"]),
            weekend_open_dow=int(group_cfg["market_weekend_open_dow"]),
            weekend_open_hm=str(group_cfg["market_weekend_open_hm"]),
            daily_break_start_hm=str(
                group_cfg.get("market_daily_break_start_hm", "00:00")
            ),
            daily_break_end_hm=str(
                group_cfg.get("market_daily_break_end_hm", "00:00")
            ),
            daily_break_enabled=True,
            daily_breaks=daily_breaks,
        )
    except Exception:
        return None
