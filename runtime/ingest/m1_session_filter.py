"""Єдине правило «чи йде M1 від брокера в SSOT» за календарем сесії.

Брокер віддає пласкі свічки (O=H=L=C, малий обсяг) і тоді, коли ринок закритий — після закриття сесії, у вихідні.
TradingView таких барів не показує, а в SSOT вони ламають графік. Правило одне для всіх записувачів M1:
- торгова хвилина → бар іде як є; плаский — з маркером `trading_flat`;
- хвилина паузи ГЛИБОКО в паузі (далі за `pause_noise_margin_min` від найближчої торгової хвилини) → не
  записується незалежно від пласкості й обсягу: це шум брокера (Сб 19.09 XAG 13 і US30 11 мікросвічок з v 2–5, діапазон
  1–2 кроки), а поріг обсягу його не відсікає — у XAG є суботні бари з v=5;
- хвилина паузи біля краю сесії, плаский бар → не записується (шум брокера);
- ПЕРША хвилина паузи після закриття, неплаский бар з малим обсягом (v ≤ flat_bar_max_volume × K) → не записується:
  це застарілі тіки брокера після закриття (клас «хвилина 21:00», ADR-0099 §3.2); у тижневому архіві брокера її немає;
- хвилина паузи біля краю сесії, неплаский бар → записується з маркером `calendar_pause_nonflat_anomaly`, записувач
  кричить у лог (ознака хибного календаря або DST — саме біля краю межа сесії зсувається, мовчки викидати не можна);
- ПЕРША торгова хвилина сесії, плаский бар → не записується: у брокера в цю мить ще немає тіків, і він віддає
  заглушку з ціною закриття попередньої сесії (17.09 22:00 NAS100 і SPX500: O=H=L=C, v=3, і той самий запит за
  12 хвилин уже не повертав цієї хвилини взагалі). Вимір на проді: 110 барів хвилини перевідкриття NAS100 мають
  обсяг 229…5811 (медіана 1482) і ненульовий діапазон — заглушка відрізняється на порядки, тож правило вузьке.

Обґрунтування і виміри — ADR-0099. Публічна точка правила одна — `classify_m1_by_calendar`; параметри паузи — один
`resolve_pause_policy` з config.

Модуль чистий і сумісний з Python 3.7: ним користуються і живий M1-полер, і `tools/fetch_tf_backfill` у .venv37.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Callable, FrozenSet, List, Optional, Tuple

from core.model.bars import CandleBar

# SSOT порогу: config.json → flat_bar_max_volume; це лише дефолт, коли ключа нема.
FLAT_BAR_MAX_VOLUME_DEFAULT = 4

VERDICT_TRADING = "trading"
VERDICT_TRADING_FLAT = "trading_flat"
VERDICT_PAUSE_FLAT_DROPPED = "pause_flat_dropped"
VERDICT_PAUSE_NONFLAT_ANOMALY = "pause_nonflat_anomaly"
VERDICT_REOPEN_FLAT_DROPPED = "reopen_flat_dropped"
VERDICT_PAUSE_NOISE_DROPPED = "pause_noise_dropped"
VERDICT_PAUSE_EDGE_STALE_DROPPED = "pause_edge_stale_dropped"

_M1_MS = 60_000

# SSOT запасу: config.json → m1_session_filter.pause_noise_margin_min; це лише дефолт, коли ключа нема. 60 хв = зсув
# межі сесії при переході DST: хвилина паузи, до якої торгова ближче за годину, може бути справжньою торговою
# хвилиною під хибним сезоном календаря, тому там лишається маркер anomaly, а не відкидання.
PAUSE_NOISE_MARGIN_MIN_DEFAULT = 60

# Застарілий край (ADR-0099 §3.2); SSOT — config.json → m1_session_filter.pause_edge_stale_volume_mult (K). Поріг
# обсягу = flat_bar_max_volume × K = 8: шум у паузі має v ≤ 5, клас 21:00 — ~3 тіки, а справжня хвилина 21:00 узимку
# (під несезонним календарем) з v ≤ 8 трапилась по 1 на символ за зиму — стільки ж, скільки при K=1. 0 вимикає правило.
# Діє лише для календарних груп із `pause_edge_stale_groups` (рев'ю D-03): у EUSTX50/GER30 справжня хвилина 20:00 UTC
# узимку має малий v (влітку та сама місцева година — v ≤ 8 у 44 з 123 днів), тож там правило з'їло б торгівлю.
# Ключа немає — правило вимкнене (WARN): відкидати дані за замовчуванням не можна.
PAUSE_EDGE_STALE_VOLUME_MULT_DEFAULT = 2

# Тривога хибного календаря (ADR-0099 §3.3); SSOT — config.json → m1_session_filter.pause_noise_alarm_*. Виміри
# 2025-10…2026-06: шум у паузі має v ≤ 5 і до 42 різних хвилин за 60 хв (XAG); справжні хвилини — v ≥ 20 у 99.7–99.9%
# і потік ~60 за 60 хв. Тому v ≥ 20 або понад 50 хвилин шуму за 60 хв часу барів — ознака, що у відсів пішла торгівля.
PAUSE_NOISE_ALARM_WINDOW_MIN_DEFAULT = 60
PAUSE_NOISE_ALARM_MAX_DROPPED_DEFAULT = 50
PAUSE_NOISE_ALARM_MIN_VOLUME_DEFAULT = 20


@dataclasses.dataclass(frozen=True)
class PausePolicy:
    """Правила M1→SSOT для хвилин паузи (ADR-0099), зведені з config `m1_session_filter` одним `resolve_pause_policy`.

    `edge_stale_max_volume` None — правило застарілого краю вимкнене (K=0). Поля `alarm_*` — пороги тривоги хибного
    календаря; `alarm_min_volume` — ще й єдиний критерій «бар схожий на торгівлю» (`is_trading_like_volume`) для
    тривоги полера, допуску засіву і прапора `--allow-off-calendar` (ADR-0099 §3.3, §3.5).
    """

    noise_margin_min: int
    edge_stale_max_volume: Optional[int] = None
    alarm_window_min: int = PAUSE_NOISE_ALARM_WINDOW_MIN_DEFAULT
    alarm_max_dropped: int = PAUSE_NOISE_ALARM_MAX_DROPPED_DEFAULT
    alarm_min_volume: int = PAUSE_NOISE_ALARM_MIN_VOLUME_DEFAULT
    calendar_suspected: bool = False

    def is_trading_like_volume(self, volume: float) -> bool:
        """Обсяг справжньої торгівлі, а не шуму паузи: шум v ≤ 5, справжні хвилини v ≥ 20 у 99.7–99.9%."""
        return volume >= self.alarm_min_volume

    def with_calendar_suspected(self) -> "PausePolicy":
        """Календар під підозрою (засів з `--allow-off-calendar`, ADR-0099 §3.5): бар, схожий на торгівлю, не
        відкидається за положенням у календарі — пишеться з маркером anomaly. Шум з малим обсягом відкидається, як і
        раніше: інакше суботні мікросвічки йшли б у SSOT."""
        return dataclasses.replace(self, calendar_suspected=True)


DEFAULT_PAUSE_POLICY = PausePolicy(noise_margin_min=PAUSE_NOISE_MARGIN_MIN_DEFAULT)


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
    """Порог пласкості з config (SSOT `flat_bar_max_volume`), спільний для всіх записувачів M1. Відсутній, битий або
    від'ємний — дефолт або 0 з WARNING (рев'ю D-07, I5): поріг вирішує, що з паузи не піде в SSOT."""
    return _resolve_config_int(cfg, "flat_bar_max_volume", FLAT_BAR_MAX_VOLUME_DEFAULT, 0)


def resolve_pause_policy(cfg: dict, symbol: str) -> PausePolicy:
    """Політика паузи символу з config (SSOT `m1_session_filter`), один resolve для всіх записувачів M1.

    Символ обов'язковий: правило застарілого краю залежить від календарної групи символу (рев'ю D-03).
    Запас глибини — щонайменше 1: кожна хвилина паузи лежить щонайменше за 1 хв від торгової, тож запас 0 тихо вимкнув
    би рейку anomaly біля країв (саме вона ловить DST і хибний календар).
    """
    section = cfg.get("m1_session_filter")
    if not isinstance(section, dict):
        logging.warning(
            "M1_SESSION_FILTER_CONFIG_MISSING raw=%r — секції m1_session_filter у config немає, правила паузи з дефолтів",
            section,
        )
        section = {}
    edge_stale_mult = _resolve_config_int(
        section, "pause_edge_stale_volume_mult", PAUSE_EDGE_STALE_VOLUME_MULT_DEFAULT, 0)
    edge_stale_on = edge_stale_mult > 0 and _calendar_group(cfg, symbol) in _resolve_edge_stale_groups(section)
    return PausePolicy(
        noise_margin_min=_resolve_config_int(section, "pause_noise_margin_min", PAUSE_NOISE_MARGIN_MIN_DEFAULT, 1),
        edge_stale_max_volume=resolve_flat_max_volume(cfg) * edge_stale_mult if edge_stale_on else None,
        alarm_window_min=_resolve_config_int(
            section, "pause_noise_alarm_window_min", PAUSE_NOISE_ALARM_WINDOW_MIN_DEFAULT, 1),
        alarm_max_dropped=_resolve_config_int(
            section, "pause_noise_alarm_max_dropped", PAUSE_NOISE_ALARM_MAX_DROPPED_DEFAULT, 1),
        alarm_min_volume=_resolve_config_int(
            section, "pause_noise_alarm_min_volume", PAUSE_NOISE_ALARM_MIN_VOLUME_DEFAULT, 1),
    )


def _calendar_group(cfg: dict, symbol: str) -> Optional[str]:
    groups = cfg.get("market_calendar_symbol_groups")
    return groups.get(symbol) if isinstance(groups, dict) else None


def _resolve_edge_stale_groups(section: dict) -> FrozenSet[str]:
    """Групи, де діє застарілий край. Відсутній або битий ключ — правило вимкнене (порожня множина) з WARNING."""
    raw = section.get("pause_edge_stale_groups")
    if isinstance(raw, list) and all(isinstance(group, str) for group in raw):
        return frozenset(raw)
    logging.warning(
        "M1_SESSION_FILTER_CONFIG_INVALID key=pause_edge_stale_groups raw=%r — очікується список груп календаря, "
        "правило застарілого краю вимкнене", raw,
    )
    return frozenset()


def _resolve_config_int(section: dict, key: str, default: int, minimum: int) -> int:
    """Ціле з config (секції `m1_session_filter` або верхнього рівня). Відсутнє або бите — дефолт, менше за minimum —
    clamp; обидва випадки дають WARNING із сирим значенням (I5), бо тихий дефолт тут змінює те, що пишеться в SSOT."""
    raw = section.get(key)
    if raw is None:
        logging.warning("M1_SESSION_FILTER_CONFIG_DEFAULT key=%s default=%d — ключа в config немає", key, default)
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logging.warning(
            "M1_SESSION_FILTER_CONFIG_INVALID key=%s raw=%r default=%d — не ціле число, взято дефолт", key, raw, default,
        )
        return default
    if value < minimum:
        logging.warning(
            "M1_SESSION_FILTER_CONFIG_CLAMPED key=%s raw=%r value=%d — менше за мінімум, взято мінімум", key, raw, minimum,
        )
        return minimum
    return value


def is_flat_m1(bar: CandleBar, flat_max_volume: int) -> bool:
    return bar.o == bar.h == bar.low == bar.c and bar.v <= flat_max_volume


def is_session_open_minute(open_ms: int, is_trading_fn: Callable[[int], bool]) -> bool:
    """Перша торгова хвилина сесії: сама торгова, а попередня — ні (перевідкриття після перерви чи вихідних)."""
    return is_trading_fn(open_ms) and not is_trading_fn(open_ms - _M1_MS)


def minutes_to_session_edge(open_ms: int, is_trading_fn: Callable[[int], bool], max_minutes: int) -> Optional[int]:
    """Відстань у хвилинах від хвилини `open_ms` до найближчої торгової: 0 — сама торгова, 1 — перша хвилина паузи
    або остання перед відкриттям. Пошук обмежений ±`max_minutes`; далі — None (хвилина глибоко в паузі)."""
    if is_trading_fn(open_ms):
        return 0
    for distance in range(1, max_minutes + 1):
        offset_ms = distance * _M1_MS
        if is_trading_fn(open_ms - offset_ms) or is_trading_fn(open_ms + offset_ms):
            return distance
    return None


def classify_m1_by_calendar(bar: CandleBar, is_trading_fn: Callable[[int], bool], flat_max_volume: int,
                            pause_policy: PausePolicy) -> Tuple[Optional[CandleBar], str]:
    """Правило M1→SSOT для записувача (ADR-0099 §3.1): факти про хвилину — з календаря, рішення — `_decide_verdict`.

    Єдина публічна точка правила для полера, засіву й ремонту дірок. Інакше кожен із трьох записувачів рахував би
    «перевідкриття» і «глибоко в паузі» по-своєму, і на тому самому барі вони розійшлися б.
    Повертає (бар для запису або None, вердикт); вхідний бар не змінюється.
    """
    open_ms = bar.open_time_ms
    trading = is_trading_fn(open_ms)
    # Календар під підозрою: бар, схожий на торгівлю, може бути справжньою хвилиною — положення в календарі його не
    # відкидає (лишається anomaly). Шум з малим обсягом відкидається за положенням і тоді.
    position_drops = not (pause_policy.calendar_suspected and pause_policy.is_trading_like_volume(bar.v))
    return _decide_verdict(
        bar,
        flat_max_volume=flat_max_volume,
        trading=trading,
        session_open_minute=trading and is_session_open_minute(open_ms, is_trading_fn),
        deep_in_pause=(not trading and position_drops
                       and minutes_to_session_edge(open_ms, is_trading_fn, pause_policy.noise_margin_min) is None),
        first_pause_minute=not trading and is_trading_fn(open_ms - _M1_MS),
        edge_stale_max_volume=pause_policy.edge_stale_max_volume if position_drops else None,
    )


def _decide_verdict(bar: CandleBar, *, flat_max_volume: int, trading: bool, session_open_minute: bool,
                    deep_in_pause: bool, first_pause_minute: bool,
                    edge_stale_max_volume: Optional[int]) -> Tuple[Optional[CandleBar], str]:
    """Таблиця ADR-0099 §3.1. Функція приватна, а факти про хвилину — обов'язкові keyword-only без дефолтів: записувач
    не може тихо лишитися без правила, забувши передати один із фактів (D15.2)."""
    flat = is_flat_m1(bar, flat_max_volume)
    if trading:
        if not flat:
            return bar, VERDICT_TRADING
        if session_open_minute:
            return None, VERDICT_REOPEN_FLAT_DROPPED
        return _with_marker(bar, "trading_flat"), VERDICT_TRADING_FLAT
    if deep_in_pause:
        return None, VERDICT_PAUSE_NOISE_DROPPED
    if flat:
        return None, VERDICT_PAUSE_FLAT_DROPPED
    if first_pause_minute and edge_stale_max_volume is not None and bar.v <= edge_stale_max_volume:
        return None, VERDICT_PAUSE_EDGE_STALE_DROPPED
    return _with_marker(bar, "calendar_pause_nonflat_anomaly"), VERDICT_PAUSE_NONFLAT_ANOMALY


def _with_marker(bar: CandleBar, marker: str) -> CandleBar:
    return dataclasses.replace(bar, extensions={**bar.extensions, marker: True})
