"""Спільні утиліти для tick_publisher та tick_preview_worker (DRY).

Функції тут — чисті (без I/O крім логування), залежать лише від
core.config_loader.env_str та runtime.ingest.market_calendar.MarketCalendar.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional, Dict, List, Sequence, Tuple

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
        logging.debug("TICK_COMMON_TO_MS_PARSE_FAILED raw=%r", raw, exc_info=True)
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
            daily_break_end_hm=str(group_cfg.get("market_daily_break_end_hm", "00:00")),
            daily_break_enabled=True,
            daily_breaks=daily_breaks,
        )
    except Exception:
        logging.debug(
            "TICK_COMMON_CALENDAR_BUILD_FAILED group_cfg=%r", group_cfg, exc_info=True
        )
        return None


# ---------------------------------------------------------------------------
# Fail-fast мапінгу календарів (ADR-0054 §3.1 P0.4)
# ---------------------------------------------------------------------------
def resolve_symbol_calendars(
    cfg: dict,
    symbols: Sequence[str],
    *,
    where: str,
) -> "Tuple[Dict[str, MarketCalendar], List[str]]":
    """Побудувати календар на кожен символ; символи без валідного — відсіяти гучно.

    Символ без запису в ``market_calendar_symbol_groups``, з групою, якої немає в
    ``market_calendar_by_group``, або з групою, що не будується — це помилка конфігурації.
    Мовчазний ``calendar=None`` означав би полінг 24/7 (усі споживачі трактують None як
    «ринок завжди відкритий»), тому такий символ не отримує календаря і не стартує.

    Args:
        cfg: повний config.json.
        symbols: символи, які збирається обслуговувати воркер.
        where: ім'я воркера для лог-префікса.

    Returns:
        ``(calendars, rejected)`` — мапа символ→календар і список відсіяних символів.
    """
    by_group = cfg.get("market_calendar_by_group") or {}
    sym_groups = cfg.get("market_calendar_symbol_groups") or {}
    calendars: Dict[str, MarketCalendar] = {}
    rejected: List[str] = []
    for sym in symbols:
        group = sym_groups.get(sym)
        if not group:
            logging.error(
                "CALENDAR_GROUP_MISSING where=%s symbol=%s reason=no_group_mapping "
                "— символ не стартує (додайте його у market_calendar_symbol_groups)",
                where, sym,
            )
            rejected.append(sym)
            continue
        group_cfg = by_group.get(group)
        if not isinstance(group_cfg, dict):
            logging.error(
                "CALENDAR_GROUP_MISSING where=%s symbol=%s group=%s reason=group_not_in_config "
                "— символ не стартує",
                where, sym, group,
            )
            rejected.append(sym)
            continue
        cal = calendar_from_group(group_cfg)
        if cal is None:
            logging.error(
                "CALENDAR_GROUP_MISSING where=%s symbol=%s group=%s reason=build_failed "
                "— символ не стартує",
                where, sym, group,
            )
            rejected.append(sym)
            continue
        calendars[sym] = cal
    if rejected:
        logging.error(
            "CALENDAR_SYMBOLS_REJECTED where=%s rejected=%s active=%s",
            where, ",".join(rejected), ",".join(sorted(calendars)),
        )
    return calendars, rejected
