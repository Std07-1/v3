"""Спільні утиліти для tick_publisher та tick_preview_worker (DRY).

Функції тут — чисті (без I/O крім логування), залежать лише від
core.config_loader.env_str, core.session_anchor (сезони DST) та runtime.ingest.market_calendar.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Optional, Dict, List, Sequence, Tuple

from core.config_loader import env_str
from core.session_anchor import (
    CALENDAR_SEASON_RULES,
    SEASON_RULE_NONE,
    SEASON_SUMMER,
    SEASON_WINTER,
    calendar_season,
)
from runtime.ingest.market_calendar import MarketCalendar, SeasonalMarketCalendar


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


# Ключі сезонного календаря групи в `market_calendar_by_group` (ADR-0095 §3.5)
SEASON_RULE_KEY = "season_rule"
SEASON_BLOCK_KEYS = (SEASON_SUMMER, SEASON_WINTER)


def calendar_for_symbol(cfg: dict, symbol: str) -> SeasonalMarketCalendar:
    """Календар символу, що обирає розклад за сезоном хвилини (ADR-0095 §3.5; фабрика ADR-0092 шар 0).

    Група декларує ``season_rule``: ``us`` | ``eu`` — блоки ``summer`` і ``winter`` з полями розкладу (однаковий
    набір ключів); ``none`` — плоскі поля групи, без блоків. Неповний конфіг — ValueError з причиною, а не тихий
    календар 24/7 чи розклад не того сезону.

    До S6b (дедлайн 25.10.2026) живі споживачі беруть плоскі поля через ``resolve_symbol_calendars``, тож плоскі
    поля сезонної групи = блок поточного сезону (``flat_calendar_off_season``: тест-сторож і ERROR на старті живого
    процесу); фабрика — для health, ``rebuild_from_m1`` та інструмента міграції S7.
    """
    group = (cfg.get("market_calendar_symbol_groups") or {}).get(symbol)
    group_cfg = (cfg.get("market_calendar_by_group") or {}).get(group) if group else None
    if not isinstance(group_cfg, dict):
        raise ValueError("CALENDAR_GROUP_MISSING symbol=%s group=%s" % (symbol, group))
    season_rule = group_cfg.get(SEASON_RULE_KEY)
    if season_rule not in CALENDAR_SEASON_RULES:
        raise ValueError(
            "CALENDAR_SEASON_RULE_INVALID symbol=%s group=%s %s=%r allowed=%s"
            % (symbol, group, SEASON_RULE_KEY, season_rule, sorted(CALENDAR_SEASON_RULES))
        )
    if season_rule == SEASON_RULE_NONE:
        present_blocks = [key for key in SEASON_BLOCK_KEYS if key in group_cfg]
        if present_blocks:
            raise ValueError(
                "CALENDAR_SEASON_BLOCKS_UNEXPECTED symbol=%s group=%s blocks=%s — season_rule=none має один розклад"
                % (symbol, group, present_blocks)
            )
        single = _build_group_schedule(group_cfg, symbol, group, "flat")
        return SeasonalMarketCalendar(season_rule, summer=single, winter=single)
    summer_cfg, winter_cfg = (group_cfg.get(key) for key in SEASON_BLOCK_KEYS)
    if not isinstance(summer_cfg, dict) or not isinstance(winter_cfg, dict) or set(summer_cfg) != set(winter_cfg):
        raise ValueError(
            "CALENDAR_SEASON_BLOCKS_INVALID symbol=%s group=%s — season_rule=%s вимагає блоки summer і winter "
            "з однаковим набором полів" % (symbol, group, season_rule)
        )
    return SeasonalMarketCalendar(
        season_rule,
        summer=_build_group_schedule(summer_cfg, symbol, group, SEASON_SUMMER),
        winter=_build_group_schedule(winter_cfg, symbol, group, SEASON_WINTER),
    )


def _build_group_schedule(schedule_cfg: dict, symbol: str, group: str, block: str) -> MarketCalendar:
    calendar = calendar_from_group(schedule_cfg)
    if calendar is None:
        raise ValueError(
            "CALENDAR_SCHEDULE_BUILD_FAILED symbol=%s group=%s block=%s" % (symbol, group, block)
        )
    return calendar


def flat_calendar_off_season(group_cfg: dict, now_ms: int) -> Optional[str]:
    """Сезон моменту ``now_ms``, блок якого плоскі поля сезонної групи не повторюють; None — повторюють.

    До S6b живий календар — плоскі поля (``resolve_symbol_calendars``), а розклад сезону — блок ``summer`` чи
    ``winter`` (``calendar_for_symbol``). Плоскі поля мусять дорівнювати блоку сезону за годинником: влітку — як є,
    після переходу DST — після перемикання ранбуком `dst_transition` (ADR-0095 §3.5). Розбіжність — живий календар
    не того сезону. Порівнюються і поля блоку, і ефективний календар: зайве плоске поле розкладу — теж розсинхрон.
    Група ``none``, з невідомим правилом чи без блоку сезону — None: це відмови ``calendar_for_symbol``, а живий
    календар від них не залежить.
    """
    season_rule = group_cfg.get(SEASON_RULE_KEY)
    if season_rule not in CALENDAR_SEASON_RULES or season_rule == SEASON_RULE_NONE:
        return None
    season = calendar_season(now_ms, season_rule)
    block = group_cfg.get(season)
    if not isinstance(block, dict):
        return None
    flat_fields = {key: group_cfg.get(key) for key in block}
    if flat_fields == block and calendar_from_group(group_cfg) == calendar_from_group(block):
        return None
    return season


# ---------------------------------------------------------------------------
# Fail-fast мапінгу календарів (ADR-0054 §3.1 P0.4)
# ---------------------------------------------------------------------------
def resolve_symbol_calendars(
    cfg: dict,
    symbols: Sequence[str],
    *,
    where: str,
    now_ms: Optional[int] = None,
) -> "Tuple[Dict[str, MarketCalendar], List[str]]":
    """Побудувати календар на кожен символ; символи без валідного — відсіяти гучно.

    Символ без запису в ``market_calendar_symbol_groups``, з групою, якої немає в
    ``market_calendar_by_group``, або з групою, що не будується — це помилка конфігурації.
    Мовчазний ``calendar=None`` означав би полінг 24/7 (усі споживачі трактують None як
    «ринок завжди відкритий»), тому такий символ не отримує календаря і не стартує.

    Плоскі поля сезонної групи не того сезону (``flat_calendar_off_season``: перехід DST настав, а ранбук не
    перемкнув їх і S6b не зроблено) — ERROR ``CALENDAR_FLAT_OFF_SEASON``. Символ стартує: календар неточний у
    годинах перерв і вихідних, а не відсутній.

    Args:
        cfg: повний config.json.
        symbols: символи, які збирається обслуговувати воркер.
        where: ім'я воркера для лог-префікса.
        now_ms: момент перевірки сезону (типово — годинник процесу).

    Returns:
        ``(calendars, rejected)`` — мапа символ→календар і список відсіяних символів.
    """
    by_group = cfg.get("market_calendar_by_group") or {}
    sym_groups = cfg.get("market_calendar_symbol_groups") or {}
    calendars: Dict[str, MarketCalendar] = {}
    rejected: List[str] = []
    season_check_ms = int(time.time() * 1000) if now_ms is None else now_ms
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
        off_season = flat_calendar_off_season(group_cfg, season_check_ms)
        if off_season is not None:
            logging.error(
                "CALENDAR_FLAT_OFF_SEASON where=%s symbol=%s group=%s season=%s — плоскі поля групи (живий "
                "календар до S6b) не повторюють блок сезону: перемкніть їх ранбуком dst_transition або завершіть "
                "S6b (ADR-0095 §3.5)",
                where, sym, group, off_season,
            )
        calendars[sym] = cal
    if rejected:
        logging.error(
            "CALENDAR_SYMBOLS_REJECTED where=%s rejected=%s active=%s",
            where, ",".join(rejected), ",".join(sorted(calendars)),
        )
    return calendars, rejected
