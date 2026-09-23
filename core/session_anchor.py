"""Сезонний якір торгового дня (ADR-0095): D1 відкривається о 17:00 America/New_York, H4 = D1/6.

Pure: без I/O і без tz-бази — арифметика правила DST США. Правило одне для будь-якого року, зокрема 1987–2006
(конвенція FXCM: 413 D1 XAU `src=history` на 21:00 UTC у вікнах, де старе правило США дало б 22:00). Збіг із
tz-базою 2007–2040 стереже тест-свідок зі `zoneinfo`: зміниться закон — CI почервоніє, а не тихий дрейф.

Сезон визначає календарна дата `d` відкриття торгового дня: літо (EDT, UTC−4), якщо друга неділя березня ≤ `d` <
перша неділя листопада, — відкриття `d` 21:00 UTC; інакше зима (EST, UTC−5) — `d` 22:00 UTC. Момент належить
торговому дню з найпізнішим відкриттям ≤ моменту; на вихідних переходу доби мають 23 і 25 год.
"""

from __future__ import annotations

import datetime as dt
import functools

RULE_NY_CLOSE_US_DST = "ny_close_us_dst"  # FXCM: метали, індекси, EU CFD, FX
RULE_UTC_MIDNIGHT = "utc_midnight"  # Binance
HTF_ANCHOR_RULES = frozenset({RULE_NY_CLOSE_US_DST, RULE_UTC_MIDNIGHT})

H4_S = 14_400
D1_S = 86_400
_DAY_MS = D1_S * 1000
_H4_MS = H4_S * 1000
_EPOCH = dt.date(1970, 1, 1)
# 17:00 Нью-Йорка в UTC
_NY_CLOSE_SUMMER_MS = 21 * 3_600_000
_NY_CLOSE_WINTER_MS = 22 * 3_600_000


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
    """Якір бакета TF у секундах від опівночі UTC: 0 для TF < H4; для H4 і D1 — час відкриття торгового дня."""
    _require_rule(rule)
    if tf_s < H4_S:
        return 0
    _require_htf(tf_s)
    return (trading_day_open_ms(ts_ms, rule) % _DAY_MS) // 1000


def htf_bucket_start_ms(ts_ms: int, tf_s: int, rule: str) -> int:
    """Початок бакета TF, що містить `ts_ms`: M1..H1 — від епохи; D1 — відкриття дня; H4 — відкриття + k·4 год."""
    _require_rule(rule)
    if not isinstance(tf_s, int) or tf_s <= 0:
        raise ValueError("invalid tf_s=%r" % (tf_s,))
    if tf_s < H4_S:
        tf_ms = tf_s * 1000
        return ts_ms - ts_ms % tf_ms
    _require_htf(tf_s)
    open_ms = trading_day_open_ms(ts_ms, rule)
    if tf_s == D1_S:
        return open_ms
    return open_ms + ((ts_ms - open_ms) // _H4_MS) * _H4_MS


def is_us_summer(day: dt.date) -> bool:
    """Літній час США за чинним правилом: друга неділя березня ≤ day < перша неділя листопада."""
    return _nth_sunday(day.year, 3, 2) <= day < _nth_sunday(day.year, 11, 1)


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


def _require_rule(rule: str) -> None:
    if rule not in HTF_ANCHOR_RULES:
        raise ValueError("unknown htf anchor rule=%r (allowed: %s)" % (rule, sorted(HTF_ANCHOR_RULES)))


def _require_htf(tf_s: int) -> None:
    if tf_s not in (H4_S, D1_S):
        raise ValueError("htf anchor defined only for H4/D1, got tf_s=%d" % tf_s)
