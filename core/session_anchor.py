"""Сезонний якір торгового дня (ADR-0095): D1 відкривається о 17:00 America/New_York, H4 — від відкриття сесії 18:00 NY.

Pure: без I/O і без tz-бази — арифметика правил DST США і ЄС з історією законів: США з 2007 — друга неділя березня …
перша неділя листопада, 1987–2006 — перша неділя квітня … остання неділя жовтня; ЄС з 1996 — остання неділя березня …
остання неділя жовтня, 1981–1995 — … остання неділя вересня. Так живе і брокер: нативний D1 FXCM (= TV) має 22:00
UTC 30.03.1995 і 28.03.2006 та 21:00 03.04.2006 (забір 23.09.2026). Раніше тут стояло «одне сучасне правило для будь-
якого року» з доказом «413 D1 XAU `src=history` на 21:00» — це був наш давній сід із фіксованим якорем 21:00, а не
брокер; через це 20–25 нативних D1 на рік за 1990–2006 були б «поза сіткою». Збіг із tz-базою 1987–2040 стереже
тест-свідок зі `zoneinfo`: зміниться закон — CI почервоніє, а не тихий дрейф.

Сезон визначає календарна дата `d` відкриття торгового дня: літо (EDT, UTC−4), якщо `d` у літньому часі США свого
року, — відкриття `d` 21:00 UTC; інакше зима (EST, UTC−5) — `d` 22:00 UTC. Момент належить торговому дню з
найпізнішим відкриттям ≤ моменту; на вихідних переходу доби мають 23 і 25 год.

H4 (ADR-0095 rev 24.09, рішення власника «H4 як у TV»): TV FX: ставить H4 від відкриття сесії після денної перерви —
18:00 NY (22/02/06/10/14/18 UTC улітку, 23/03/07/… узимку), а не від 17:00 NY. Тому H4 рахується від «сесійного дня»
— торгового дня, зсунутого на годину (`_htf_day_open_ms`); останній H4 доби, 18:00–22:00 UTC улітку, покриває денну
перерву, як у TV. D1 лишається від 17:00 NY — ключ нативного D1 FXCM; вміст D1 TV той самий, бо 17:00–18:00 NY —
перерва. Правило `utc_midnight` (Binance) — без зсуву.

Тут же сезон розкладу груп календаря (§3.5, `calendar_season`): момент переходу DST США (`us`) або ЄС (`eu`).
Уся арифметика DST платформи — в одному модулі.
"""

from __future__ import annotations

import datetime as dt
import functools

RULE_NY_CLOSE_US_DST = "ny_close_us_dst"  # FXCM: метали, індекси, EU CFD, FX
RULE_UTC_MIDNIGHT = "utc_midnight"  # Binance
HTF_ANCHOR_RULES = frozenset({RULE_NY_CLOSE_US_DST, RULE_UTC_MIDNIGHT})

# Правило сезону розкладу групи календаря (ADR-0095 §3.5): `market_calendar_by_group.<група>.season_rule`
SEASON_RULE_US = "us"  # неділя переходу навесні 07:00 UTC ≤ момент < неділя переходу восени 06:00 UTC (02:00 NY)
SEASON_RULE_EU = "eu"  # неділя переходу навесні 01:00 UTC ≤ момент < неділя переходу восени 01:00 UTC
SEASON_RULE_NONE = "none"  # розклад групи від DST не залежить (HK, crypto)
CALENDAR_SEASON_RULES = frozenset({SEASON_RULE_US, SEASON_RULE_EU, SEASON_RULE_NONE})
SEASON_SUMMER = "summer"  # мітки сезону season_label / calendar_season = ключі блоків розкладу в config
SEASON_WINTER = "winter"

H4_S = 14_400
D1_S = 86_400
_DAY_MS = D1_S * 1000
_H4_MS = H4_S * 1000
_EPOCH = dt.date(1970, 1, 1)
# 17:00 Нью-Йорка в UTC
_NY_CLOSE_SUMMER_MS = 21 * 3_600_000
_NY_CLOSE_WINTER_MS = 22 * 3_600_000
_LONGEST_DAY_MS = 25 * 3_600_000  # доба осінніх вихідних переходу DST
_H4_SESSION_SHIFT_MS = 3_600_000  # H4 від відкриття сесії: 18:00 NY = відкриття торгового дня 17:00 NY + 1 год (TV FX:)
# Момент переходу DST у мс від опівночі UTC неділі переходу: (весна, осінь)
_US_SWITCH_MS_OF_DAY = (7 * 3_600_000, 6 * 3_600_000)  # 02:00 за Нью-Йорком: 02:00 EST, 02:00 EDT
_EU_SWITCH_MS_OF_DAY = (3_600_000, 3_600_000)  # ЄС перемикає о 01:00 UTC


class OffSeasonGridError(ValueError):
    """Бар H4/D1 не на сезонній сітці свого символу (ADR-0095 §3.3): гучна відмова з очікуваним відкриттям."""

    def __init__(self, tf_s: int, open_ms: int, expected_open_ms: int, rule: str) -> None:
        self.tf_s = tf_s
        self.open_ms = open_ms
        self.expected_open_ms = expected_open_ms
        self.rule = rule
        super().__init__(
            "bar_off_season_grid tf_s=%d open_ms=%d expected_open_ms=%d rule=%s season=%s"
            % (tf_s, open_ms, expected_open_ms, rule, season_label(open_ms, rule))
        )


def trading_day_open_ms(ts_ms: int, rule: str) -> int:
    """Відкриття торгового дня, що містить `ts_ms` (UTC, мс)."""
    _require_rule(rule)
    day_index = ts_ms // _DAY_MS
    for index in (day_index, day_index - 1):
        open_ms = _day_open_ms(index, rule)
        if open_ms <= ts_ms:
            return open_ms
    # Недосяжне: відкриття попередньої доби ≤ її 22:00 < опівночі поточної ≤ ts_ms
    raise AssertionError("trading_day_open_ms: no open <= ts_ms=%d" % ts_ms)


def htf_anchor_offset_s(tf_s: int, ts_ms: int, rule: str) -> int:
    """Якір бакета TF у секундах від опівночі UTC: 0 для TF < H4; D1 — відкриття торгового дня; H4 — відкриття сесії."""
    _require_rule(rule)
    if tf_s < H4_S:
        return 0
    _require_htf(tf_s)
    return (_htf_day_open_ms(ts_ms, tf_s, rule) % _DAY_MS) // 1000


def htf_bucket_start_ms(ts_ms: int, tf_s: int, rule: str) -> int:
    """Початок бакета TF, що містить `ts_ms`: M1..H1 — від епохи; D1 — відкриття дня; H4 — відкриття + k·4 год."""
    _require_rule(rule)
    if not isinstance(tf_s, int) or tf_s <= 0:
        raise ValueError("invalid tf_s=%r" % (tf_s,))
    if tf_s < H4_S:
        tf_ms = tf_s * 1000
        return ts_ms - ts_ms % tf_ms
    _require_htf(tf_s)
    open_ms = _htf_day_open_ms(ts_ms, tf_s, rule)
    if tf_s == D1_S:
        return open_ms
    return open_ms + ((ts_ms - open_ms) // _H4_MS) * _H4_MS


def htf_next_bucket_start_ms(bucket_start_ms: int, tf_s: int, rule: str) -> int:
    """Початок наступного бакета = кінець вікна агрегації бакета `bucket_start_ms` (не open + tf).

    H4 не перетинає межу сесійного дня (18:00 NY): останній H4 доби на 23 год має 3 год, на 25 год — 1 год (обрубок;
    доби переходу DST — вихідні). D1 — до відкриття наступного торгового дня (23/24/25 год). M1..H1 — open + tf.
    """
    _require_rule(rule)
    if tf_s < H4_S:
        return bucket_start_ms + tf_s * 1000
    _require_htf(tf_s)
    day_open_ms = _htf_day_open_ms(bucket_start_ms, tf_s, rule)
    next_day_open_ms = _htf_day_open_ms(day_open_ms + _LONGEST_DAY_MS, tf_s, rule)
    if tf_s == D1_S:
        return next_day_open_ms
    return min(bucket_start_ms + _H4_MS, next_day_open_ms)


def assert_on_season_grid(open_ms: int, tf_s: int, rule: str) -> None:
    """Рівність сезонній сітці, а не членство в наборі якорів (ADR-0095 §3.3). Інакше — OffSeasonGridError."""
    expected_open_ms = htf_bucket_start_ms(open_ms, tf_s, rule)
    if expected_open_ms != open_ms:
        raise OffSeasonGridError(tf_s, open_ms, expected_open_ms, rule)


def season_label(ts_ms: int, rule: str) -> str:
    """Сезон торгового дня моменту: summer | winter для ny_close_us_dst, none для utc_midnight."""
    _require_rule(rule)
    if rule == RULE_UTC_MIDNIGHT:
        return "none"
    open_ms = trading_day_open_ms(ts_ms, rule)
    return SEASON_SUMMER if open_ms % _DAY_MS == _NY_CLOSE_SUMMER_MS else SEASON_WINTER


def calendar_season(ts_ms: int, season_rule: str) -> str:
    """Сезон розкладу групи календаря для моменту `ts_ms` (ADR-0095 §3.5): summer | winter | none.

    Розклад групи — години ринку за місцевим годинником, тож сезон береться за моментом переходу DST: `us` — 02:00
    за Нью-Йорком (чинне правило для будь-якого року, як у якоря §3.1), `eu` — 01:00 UTC. Не сезон торгового дня
    §3.1: восени доба 31.10–01.11 має 25 год і до Нд 22:00 UTC лишається «літньою», а FX відкривається о 17:00 EST
    = 22:00 UTC. Для `cfd_us` обидва правила дають ту саму торговість кожної хвилини: розбіжні години неділі
    закриті в обох розкладах. `none` — розклад один.
    """
    if season_rule in (SEASON_RULE_US, SEASON_RULE_EU):
        start_ms, end_ms = _summer_bounds_ms((_EPOCH + dt.timedelta(days=ts_ms // _DAY_MS)).year, season_rule)
        return SEASON_SUMMER if start_ms <= ts_ms < end_ms else SEASON_WINTER
    if season_rule == SEASON_RULE_NONE:
        return "none"
    raise ValueError(
        "unknown calendar season_rule=%r (allowed: %s)" % (season_rule, sorted(CALENDAR_SEASON_RULES))
    )


def is_us_summer(day: dt.date) -> bool:
    """Літній час США за законом свого року: [неділя переходу навесні, неділя переходу восени)."""
    spring, autumn = _us_dst_days(day.year)
    return spring <= day < autumn


@functools.lru_cache(maxsize=256)
def _us_dst_days(year: int) -> tuple[dt.date, dt.date]:
    """Неділі переходу США: з 2007 (Energy Policy Act 2005) — друга неділя березня / перша неділя листопада;
    1987–2006 — перша неділя квітня / остання неділя жовтня; раніше — остання неділя квітня / остання неділя жовтня."""
    if year >= 2007:
        return _nth_sunday(year, 3, 2), _nth_sunday(year, 11, 1)
    if year >= 1987:
        return _nth_sunday(year, 4, 1), _last_sunday(year, 10)
    return _last_sunday(year, 4), _last_sunday(year, 10)


def _eu_dst_days(year: int) -> tuple[dt.date, dt.date]:
    """Неділі переходу ЄС: з 1996 — остання неділя березня / остання неділя жовтня; раніше — … / остання вересня."""
    return _last_sunday(year, 3), _last_sunday(year, 10 if year >= 1996 else 9)


@functools.lru_cache(maxsize=256)
def _summer_bounds_ms(year: int, season_rule: str) -> tuple[int, int]:
    """Літній час року за правилом групи `us` | `eu`: [перехід навесні, перехід восени), мс UTC."""
    if season_rule == SEASON_RULE_US:
        switch_days, switch_ms_of_day = _us_dst_days(year), _US_SWITCH_MS_OF_DAY
    else:
        switch_days, switch_ms_of_day = _eu_dst_days(year), _EU_SWITCH_MS_OF_DAY
    spring_ms, autumn_ms = (
        (day - _EPOCH).days * _DAY_MS + ms_of_day for day, ms_of_day in zip(switch_days, switch_ms_of_day)
    )
    return spring_ms, autumn_ms


def _htf_day_open_ms(ts_ms: int, tf_s: int, rule: str) -> int:
    """Відкриття доби, від якої рахується бакет: D1 — торговий день (17:00 NY); H4 — сесійний день (18:00 NY =
    торговий день, зсунутий на годину, як TV FX:); utc_midnight — без зсуву."""
    if tf_s == D1_S or rule == RULE_UTC_MIDNIGHT:
        return trading_day_open_ms(ts_ms, rule)
    return trading_day_open_ms(ts_ms - _H4_SESSION_SHIFT_MS, rule) + _H4_SESSION_SHIFT_MS


@functools.lru_cache(maxsize=8192)  # гарячий шлях: якір на кожен бар/тік, доба рахується раз
def _day_open_ms(day_index: int, rule: str) -> int:
    midnight_ms = day_index * _DAY_MS
    if rule == RULE_UTC_MIDNIGHT:
        return midnight_ms
    day = _EPOCH + dt.timedelta(days=day_index)
    return midnight_ms + (_NY_CLOSE_SUMMER_MS if is_us_summer(day) else _NY_CLOSE_WINTER_MS)


def _nth_sunday(year: int, month: int, n: int) -> dt.date:
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def _last_sunday(year: int, month: int) -> dt.date:
    last = dt.date(year + month // 12, month % 12 + 1, 1) - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() + 1) % 7)


def _require_rule(rule: str) -> None:
    if rule not in HTF_ANCHOR_RULES:
        raise ValueError("unknown htf anchor rule=%r (allowed: %s)" % (rule, sorted(HTF_ANCHOR_RULES)))


def _require_htf(tf_s: int) -> None:
    if tf_s not in (H4_S, D1_S):
        raise ValueError("htf anchor defined only for H4/D1, got tf_s=%d" % tf_s)
