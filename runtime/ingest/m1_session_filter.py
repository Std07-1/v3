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
from typing import AbstractSet, Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

from core.model.bars import CandleBar
# Предикати розриву і прихованого бару — одні з display і health `chain_breaks` (ADR-0101 C4); open_breaks_chain
# лишається доступним звідси для наявних споживачів
from core.model.candle_chain import hole_possible_between, is_display_hidden, open_breaks_chain

# SSOT порогу: config.json → flat_bar_max_volume; це лише дефолт, коли ключа нема.
FLAT_BAR_MAX_VOLUME_DEFAULT = 4

VERDICT_TRADING = "trading"
VERDICT_TRADING_FLAT = "trading_flat"
VERDICT_PAUSE_FLAT_DROPPED = "pause_flat_dropped"
VERDICT_PAUSE_NONFLAT_ANOMALY = "pause_nonflat_anomaly"
VERDICT_REOPEN_FLAT_DROPPED = "reopen_flat_dropped"
VERDICT_PAUSE_NOISE_DROPPED = "pause_noise_dropped"
VERDICT_PAUSE_EDGE_STALE_DROPPED = "pause_edge_stale_dropped"
VERDICT_PAUSE_EDGE_STALE_FOLDED = "pause_edge_stale_folded"  # ADR-0101: вкладено в останню хвилину сесії (пакетні записувачі)
# ADR-0101 C3: тіки цієї хвилини вже вкладені в бар SSOT перед нею (повторний засів того самого вікна) — правки немає
VERDICT_PAUSE_EDGE_STALE_ALREADY_FOLDED = "pause_edge_stale_already_folded"

MARKER_OPEN_CHAINED = "open_chained_from"  # сирий open брокера на барі, чий open прив'язано до close попереднього
MARKER_LATE_TICKS_FOLDED = "late_ticks_folded"  # обсяг застарілого краю, вкладеного в останню хвилину сесії

# Причини правки наявного бару SSOT, якої вимагає правило послідовності на межі дозапису (ADR-0101 C3)
SSOT_EDIT_CHAIN = "chain"  # open бару SSOT ≠ close нового бару перед ним
SSOT_EDIT_FOLD = "fold"  # застарілий край, чия попередня хвилина (остання хвилина сесії) уже в SSOT
SSOT_EDIT_CHAIN_AFTER_FOLD = "chain_after_fold"  # перший видимий бар після вкладеного краю: open := вкладений close
_SSOT_EDIT_LOG_LIMIT = 10  # скільки правок називати поіменно; решта — лічильником у зведенні

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


# ---------------------------------------------------------------------------
# Суцільний ланцюг свічок (ADR-0101): open = close попереднього існуючого бару
# ---------------------------------------------------------------------------
def chain_open_to_prev_close(prev: CandleBar, bar: CandleBar) -> CandleBar:
    """ADR-0101 §3.2: open бару := close попереднього існуючого бару (конвенція PREVIOUS_CLOSE, як у TV).

    Ревізія брокера переписує close(t), а open(t+1) лишає; ми відкидаємо застарілий край, чий close уже став open
    наступної сесії. Без розриву повертає той самий об'єкт; з розривом — копію з h/low, що охоплюють новий open, і
    маркером `open_chained_from` (сирий open брокера).
    """
    if not open_breaks_chain(prev.c, bar.o):
        return bar
    chained_open = prev.c
    return dataclasses.replace(
        bar,
        o=chained_open,
        h=max(bar.h, chained_open),
        low=min(bar.low, chained_open),
        extensions={**bar.extensions, MARKER_OPEN_CHAINED: bar.o},
    )


def fold_edge_stale(last_session_bar: CandleBar, stale_bar: CandleBar) -> CandleBar:
    """ADR-0101 §3.2 (змінює ADR-0099 §3.2): пізні тіки першої хвилини паузи — в останній бар сесії, як у TV.

    h/low охоплюють обидва, close і обсяг — з урахуванням пізніх тіків; маркер `late_ticks_folded` = обсяг вкладеного.
    """
    return dataclasses.replace(
        last_session_bar,
        h=max(last_session_bar.h, stale_bar.h),
        low=min(last_session_bar.low, stale_bar.low),
        c=stale_bar.c,
        v=last_session_bar.v + stale_bar.v,
        extensions={**last_session_bar.extensions, MARKER_LATE_TICKS_FOLDED: stale_bar.v},
    )


def normalize_m1_sequence(
    bars: Sequence[CandleBar],
    *,
    is_trading_fn: Callable[[int], bool],
    flat_max_volume: int,
    pause_policy: PausePolicy,
    prev_bar: Optional[CandleBar] = None,
) -> Tuple[List[CandleBar], List[Tuple[CandleBar, str]]]:
    """Правило послідовності M1 для пакетних записувачів (ADR-0101 §3.2): класифікація ADR-0099, вкладення
    застарілого краю в попередню хвилину, ланцюг open = close попереднього.

    `bars` — бари вікна (порядок довільний, ключ open_time_ms унікальний); `prev_bar` — останній бар SSOT перед
    вікном, щоб ланцюг тримався і на межі. Повертає (бари для запису за зростанням; (вхідний бар, вердикт) по
    кожному вхідному). Застарілий край без попередньої хвилини у вікні відкидається, як раніше.

    Ланцюг тягнеться через будь-який проміжок між барами, тож `bars` мають бути суцільною вибіркою брокера, а
    `prev_bar` — баром, одразу за яким вікно починається в даних брокера. У брокера ланцюг суцільний і через його
    власні гепи (архів PREVIOUS_CLOSE 30.08–22.09: 135 гепів у торгових хвилинах, до 239 хв у свято, розривів 0).
    Через нашу діру тягнути не можна: перший бар після неї намалював би рух ціни за всю діру.
    """
    out: List[CandleBar] = []
    verdicts: List[Tuple[CandleBar, str]] = []
    last = prev_bar
    for bar in sorted(bars, key=lambda b: b.open_time_ms):
        classified, verdict = classify_m1_by_calendar(bar, is_trading_fn, flat_max_volume, pause_policy)
        if verdict == VERDICT_PAUSE_EDGE_STALE_DROPPED and out and out[-1].open_time_ms == bar.open_time_ms - _M1_MS:
            out[-1] = fold_edge_stale(out[-1], bar)
            last = out[-1]
            verdicts.append((bar, VERDICT_PAUSE_EDGE_STALE_FOLDED))
            continue
        verdicts.append((bar, verdict))
        if classified is None:
            continue
        if last is not None:
            classified = chain_open_to_prev_close(last, classified)
        out.append(classified)
        last = classified
    return out, verdicts


@dataclasses.dataclass(frozen=True)
class SsotEdit:
    """Правка наявного бару SSOT, якої вимагає правило послідовності на межі дозапису (ADR-0101 C3).

    Пакетний записувач (засів, ремонт дірок) працює поруч із живим полером і дописує лише ключі, яких у SSOT немає.
    Закомічений фінал він не змінює: друга версія ключа дописом — конфліктний дублікат, переможця якого вирішує
    порядок у файлі (ADR-0098 §3.7, health `dup_conflicting`), а заміна part-файла — лише офлайн при зупинених
    записувачах (ADR-0098 §3.6). Тож записувач правку лише називає, а робить її settle: рядок цілим з архіву брокера
    і та сама нормалізація послідовності (ADR-0098 §3.2, ADR-0101 §3.3).

    `current` — бар таким, яким він лежатиме в SSOT після дозапису: наявний або щойно дописаний (`chain_after_fold`).
    `target` — що дає правило з даних записувача: для дописаного бару — від брокерського, для наявного — від рядка SSOT
    як він є (розширення h/low від давнішого ланцюга рядок окремо не зберігає — їх знімає лише повна заміна settle).
    Правка одна на ключ, і набір плану замкнений: цілі, застосовані разом, не лишають розриву ланцюга між барами,
    які бачить план.
    """

    reason: str  # SSOT_EDIT_CHAIN | SSOT_EDIT_FOLD | SSOT_EDIT_CHAIN_AFTER_FOLD; кілька правил одного бару — через «+»
    current: CandleBar
    target: CandleBar


@dataclasses.dataclass(frozen=True)
class M1AppendPlan:
    """Що пакетний записувач M1 дописує в SSOT (ADR-0101 C3)."""

    to_write: Tuple[CandleBar, ...]  # нові ключі за зростанням: класифіковані, з вкладеним краєм, у ланцюгу
    verdicts: Tuple[Tuple[CandleBar, str], ...]  # вердикт по кожному новому бару партії
    already_in_ssot: int  # бари партії, ключ яких уже в SSOT: SSOT виграє, вони не пишуться і не класифікуються
    ssot_edits: Tuple[SsotEdit, ...]  # правки наявних барів — робить settle
    # (open попереднього, open наступного) — розриви, через які ланцюг не тягнуто: між ними може бути наша діра
    chain_gaps_left: Tuple[Tuple[int, int], ...] = ()

    @property
    def open_chained(self) -> int:
        return sum(1 for bar in self.to_write if MARKER_OPEN_CHAINED in bar.extensions)


def plan_m1_append(
    bars: Sequence[CandleBar],
    ssot_bars: Sequence[CandleBar],
    *,
    is_trading_fn: Callable[[int], bool],
    flat_max_volume: int,
    pause_policy: PausePolicy,
    occupied_opens: AbstractSet[int] = frozenset(),
    session_open_grace_min: int = 0,
    covered_ranges: Optional[Sequence[Tuple[int, int]]] = None,
) -> M1AppendPlan:
    """Правило послідовності (ADR-0101 §3.2) для пакетного записувача, що дописує в SSOT лише нові ключі (§3.3).

    `ssot_bars` — бари SSOT навколо партії так, як їх бачать читачі (`ssot_jsonl.read_m1_chain_context`): вікно
    разом із видимими сусідами. `occupied_opens` — ключі, зайняті на диску будь-яким рядком. Нові бари йдуть серіями
    між видимими барами SSOT через `normalize_m1_sequence` з prev_bar = попередній видимий бар, тож ланцюг тримається
    і на межі вікна, і навколо кожного наявного бару всередині. Бар з `calendar_pause_flat` display ховає: у ланцюг
    він не йде, але ключ його зайнятий.

    Наявного бару план не змінює, а називає правку (`SsotEdit`), одну на ключ: бар SSOT одразу після серії, чий open
    розходиться з close останнього нового бару; застарілий край, попередня хвилина якого вже в SSOT, і перший видимий
    бар після нього (open := вкладений close). Правки рахуються від `last` — останнього видимого бару таким, яким він
    стане після дозапису і правок, тож набір замкнений: застосовані разом, цілі не лишають розриву ланцюга.

    Через нашу діру ланцюг не тягнеться (§3.1): сусід SSOT з контексту може лежати за діркою, якої ця вибірка не
    покриває, і тоді open першого нового бару намалював би рух ціни за всю діру. `covered_ranges` — хвилини, які
    вибірка брокера засвідчує (включно; без нього — від першого до останнього бару `bars`); між баром і сусідом діра
    можлива лише в хвилинах поза ними (`candle_chain.hole_possible_between` із запізненням відкриття сесії
    `session_open_grace_min`). Такий розрив лишається, план його називає (`chain_gaps_left`); діру з ним закриває
    settle.
    """
    if covered_ranges is None:
        opens = [bar.open_time_ms for bar in bars]
        covered_ranges = [(min(opens), max(opens))] if opens else []

    def chain_blocked(prev: Optional[CandleBar], bar_open_ms: int) -> bool:
        return prev is not None and hole_possible_between(
            prev.open_time_ms, bar_open_ms, is_trading_fn=is_trading_fn,
            session_open_grace_min=session_open_grace_min, covered=covered_ranges)

    gaps_left: List[Tuple[int, int]] = []
    committed = set(occupied_opens) | {bar.open_time_ms for bar in ssot_bars}
    new_by_open = {bar.open_time_ms: bar for bar in bars if bar.open_time_ms not in committed}
    visible_by_open = {bar.open_time_ms: bar for bar in ssot_bars
                       if not is_display_hidden(bar.extensions)}
    to_write: List[CandleBar] = []
    verdicts: List[Tuple[CandleBar, str]] = []
    edits: Dict[int, SsotEdit] = {}  # open_ms → правка, у порядку називання
    last: Optional[CandleBar] = None  # останній видимий бар послідовності таким, яким він стане після дозапису і правок
    # Причина правки ланцюга для наступного бару SSOT. None — `last` такий, як на диску: розрив за ним (якщо є) не з
    # цього дозапису, і план його не називає.
    next_edit_reason: Optional[str] = None
    run: List[CandleBar] = []
    timeline: List[Optional[int]] = sorted(set(new_by_open) | set(visible_by_open))
    for open_ms in timeline + [None]:  # None — межа останньої серії
        if open_ms in new_by_open:
            run.append(new_by_open[open_ms])
            continue
        if run:
            run_prev = None if chain_blocked(last, run[0].open_time_ms) else last
            run_out, run_verdicts, folded_prev, run_planned = _normalize_run(
                run, run_prev, is_trading_fn=is_trading_fn, flat_max_volume=flat_max_volume, pause_policy=pause_policy)
            if run_prev is None and last is not None and run_out and open_breaks_chain(last.c, run_out[0].o):
                gaps_left.append((last.open_time_ms, run_out[0].open_time_ms))
            to_write.extend(run_out)
            verdicts.extend(run_verdicts)
            if folded_prev is not None:
                _name_ssot_edit(edits, SSOT_EDIT_FOLD, visible_by_open[folded_prev.open_time_ms], folded_prev)
                last, next_edit_reason = folded_prev, SSOT_EDIT_CHAIN_AFTER_FOLD
            for written, planned in zip(run_out, run_planned):
                if planned != written:
                    _name_ssot_edit(edits, SSOT_EDIT_CHAIN_AFTER_FOLD, written, planned)
            if run_planned:
                last, next_edit_reason = run_planned[-1], SSOT_EDIT_CHAIN
            run = []
        if open_ms is None:
            break
        ssot_bar = visible_by_open[open_ms]
        planned_ssot_bar = ssot_bar
        if next_edit_reason is not None:
            if chain_blocked(last, open_ms):
                if open_breaks_chain(last.c, ssot_bar.o):
                    gaps_left.append((last.open_time_ms, open_ms))
            else:
                planned_ssot_bar = chain_open_to_prev_close(last, ssot_bar)
                if planned_ssot_bar is not ssot_bar:
                    _name_ssot_edit(edits, next_edit_reason, ssot_bar, planned_ssot_bar)
        last, next_edit_reason = planned_ssot_bar, None
    return M1AppendPlan(tuple(to_write), tuple(verdicts), len(bars) - len(new_by_open), tuple(edits.values()),
                        tuple(gaps_left))


def _normalize_run(
    run: List[CandleBar], prev: Optional[CandleBar], *, is_trading_fn: Callable[[int], bool], flat_max_volume: int,
    pause_policy: PausePolicy,
) -> Tuple[List[CandleBar], List[Tuple[CandleBar, str]], Optional[CandleBar], List[CandleBar]]:
    """Серія нових барів між видимими барами SSOT: правило послідовності від `prev` (бару SSOT таким, яким він стане
    після правок, або None). Повертає (бари для запису; вердикти; `prev` із вкладеним краєм або None; бари серії
    такими, якими вони стануть після правок).

    Застарілий край на початку серії належить `prev`, якщо той — саме попередня хвилина. Вкласти його в закомічений
    бар може лише settle: правило бар відкидає, нові бари пишуться в ланцюгу з close, що лежить на диску (як у живого
    полера), а після правок серія йде від вкладеного close. `prev` з маркером `late_ticks_folded` уже містить тіки цієї
    хвилини (повторний засів після вкладення): вкладати вдруге — подвоїти обсяг, тож правки немає. Ревізію брокера
    вже вкладених тіків, як і будь-якого закоміченого ключа, перераховує settle.
    """
    out, verdicts = normalize_m1_sequence(run, is_trading_fn=is_trading_fn, flat_max_volume=flat_max_volume,
                                          pause_policy=pause_policy, prev_bar=prev)
    first_bar, first_verdict = verdicts[0]
    if not (first_verdict == VERDICT_PAUSE_EDGE_STALE_DROPPED and prev is not None
            and prev.open_time_ms == first_bar.open_time_ms - _M1_MS):
        return out, verdicts, None, out
    if MARKER_LATE_TICKS_FOLDED in prev.extensions:
        verdicts[0] = (first_bar, VERDICT_PAUSE_EDGE_STALE_ALREADY_FOLDED)
        return out, verdicts, None, out
    folded_prev = fold_edge_stale(prev, first_bar)
    planned, _ = normalize_m1_sequence(run, is_trading_fn=is_trading_fn, flat_max_volume=flat_max_volume,
                                       pause_policy=pause_policy, prev_bar=folded_prev)
    return out, verdicts, folded_prev, planned


def _name_ssot_edit(edits: Dict[int, SsotEdit], reason: str, current: CandleBar, target: CandleBar) -> None:
    """Одна правка на ключ. Той самий бар SSOT правиться вдруге лише так: ланцюг після серії перед ним, потім вкладення
    краю за ним. Ціль уже містить обидва правила (рахувалась від `last`), `current` лишається рядком SSOT."""
    earlier = edits.get(current.open_time_ms)
    if earlier is not None:
        reason, current = "%s+%s" % (earlier.reason, reason), earlier.current
    edits[current.open_time_ms] = SsotEdit(reason, current, target)


def report_m1_append_plan(plan: M1AppendPlan, *, where: str, symbol: str) -> None:
    """Правки значень пакетного записувача — гучно (I5, D15.3): зведення і перші правки наявних барів поіменно."""
    folded = sum(1 for _bar, verdict in plan.verdicts if verdict == VERDICT_PAUSE_EDGE_STALE_FOLDED)
    logging.log(
        logging.WARNING if plan.open_chained or folded or plan.ssot_edits else logging.INFO,
        "M1_BATCH_SEQUENCE where=%s symbol=%s to_write=%d open_chained=%d edge_folded=%d ssot_edits_pending=%d — "
        "open нових барів = close попереднього (маркер %s), застарілий край вкладено в останню хвилину сесії "
        "(late_ticks_folded); наявні бари SSOT записувач не переписує — їх правку робить settle (ADR-0098)",
        where, symbol, len(plan.to_write), plan.open_chained, folded, len(plan.ssot_edits), MARKER_OPEN_CHAINED,
    )
    for edit in plan.ssot_edits[:_SSOT_EDIT_LOG_LIMIT]:
        logging.warning(
            "M1_SSOT_EDIT_PENDING where=%s symbol=%s reason=%s open_ms=%d o=%.5f->%.5f h=%.5f->%.5f l=%.5f->%.5f "
            "c=%.5f->%.5f v=%.0f->%.0f — бар SSOT (наявний або щойно дописаний) записувач не переписує, до settle на "
            "графіку лишається як є",
            where, symbol, edit.reason, edit.current.open_time_ms, edit.current.o, edit.target.o, edit.current.h,
            edit.target.h, edit.current.low, edit.target.low, edit.current.c, edit.target.c, edit.current.v,
            edit.target.v,
        )
    if len(plan.ssot_edits) > _SSOT_EDIT_LOG_LIMIT:
        logging.warning("M1_SSOT_EDIT_PENDING where=%s symbol=%s ...+%d правок не показано", where, symbol,
                        len(plan.ssot_edits) - _SSOT_EDIT_LOG_LIMIT)
    if plan.chain_gaps_left:
        logging.warning(
            "M1_BATCH_CHAIN_GAP_LEFT where=%s symbol=%s n=%d first=%s — між сусідом SSOT і вибіркою може бути наша "
            "діра, ланцюг через неї не тягнуто (ADR-0101 §3.1); діру з розривом закриває settle",
            where, symbol, len(plan.chain_gaps_left), list(plan.chain_gaps_left[:_SSOT_EDIT_LOG_LIMIT]),
        )
