"""Календарні TF — тиждень (W1) і місяць (MN) над торговим днем (ADR-0115). Pure: без I/O.

W1/MN не зберігаються: це проєкція D1 SSOT. Бакет визначає **торгова дата** D1 — UTC-дата відкриття торгового дня
+ 12 год (D1 відкривається о 17:00 Нью-Йорка попереднього календарного дня, ADR-0095): тиждень — торгові дати
одного ISO-тижня Пн–Нд, місяць — одного календарного місяця. Так рахує TV `FX:` (виміряно 19.10.2026 на 8 символах:
бар TV W/1M = агрегат TV D1 за цим правилом). Ключ бакета — відкриття торгового дня понеділка / 1-го числа:
календарний, а не «перший бар, що є», тож свято не зсуває свічку. Кінець бакета — ключ наступного (end-excl, I2);
DST — лише через `core.session_anchor.trading_day_open_ms`.

`tf_s` W1 = 604 800, MN = 2 592 000 (30 діб) — ідентифікатори, не тривалість: місяць має 28–31 добу, тиждень на
вихідних переходу DST — 167 або 169 год.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Iterable, List, Optional, Sequence, Tuple

from core.session_anchor import (
    RULE_NY_CLOSE_FX,
    RULE_NY_CLOSE_US_DST,
    RULE_UTC_MIDNIGHT,
    trading_day_open_ms,
)

W1_S = 604_800
MN_S = 2_592_000
CALENDAR_TFS = frozenset({W1_S, MN_S})

# Скільки D1 щонайбільше дає бакет у звичайних даних (5 буднів + огризок; 23 будні місяця) — запас вікна читання D1
D1_PER_BUCKET = {W1_S: 6, MN_S: 23}

_DAY_MS = 86_400_000
_HALF_DAY_MS = _DAY_MS // 2
_EPOCH = dt.date(1970, 1, 1)
# Чи має правило якоря торгові дати на вихідних: FXCM (метали, індекси, валюти) — лише Пн–Пт, Binance — щодня.
# Ключі = усі правила `session_anchor.HTF_ANCHOR_RULES` (сторож — тест): нове правило без рядка тут — гучна відмова.
TRADES_WEEKENDS_BY_RULE = {RULE_NY_CLOSE_US_DST: False, RULE_NY_CLOSE_FX: False, RULE_UTC_MIDNIGHT: True}


@dataclasses.dataclass(frozen=True)
class DayBar:
    """Бар D1, з якого складається проєкція (display-приховані бари сюди не потрапляють)."""

    open_ms: int
    o: float
    h: float
    low: float
    c: float
    v: float


@dataclasses.dataclass(frozen=True)
class CalendarBar:
    """Бар W1/MN: ключ бакета, OHLCV, кількість D1 і чи бакет фінальний (I3)."""

    open_ms: int
    o: float
    h: float
    low: float
    c: float
    v: float
    days: int
    complete: bool


@dataclasses.dataclass(frozen=True)
class CalendarProjection:
    """Результат проєкції: фінальні бари (зростання ключа) і формуючий бар, якщо він є."""

    finals: Tuple[CalendarBar, ...]
    forming: Optional[CalendarBar]


def trading_date(ts_ms: int, rule: str) -> dt.date:
    """Торгова дата моменту — дата закриття торгового дня, що його містить (UTC-дата відкриття + 12 год)."""
    return _date_of_ms(trading_day_open_ms(ts_ms, rule) + _HALF_DAY_MS)


def calendar_bucket_start_ms(ts_ms: int, tf_s: int, rule: str) -> int:
    """Ключ бакета W1/MN, що містить момент `ts_ms`: відкриття торгового дня понеділка / 1-го числа."""
    return _date_open_ms(_bucket_first_date(trading_date(ts_ms, rule), tf_s), rule)


def calendar_next_bucket_start_ms(bucket_start_ms: int, tf_s: int, rule: str) -> int:
    """Ключ наступного бакета = кінець бакета `bucket_start_ms` (end-excl)."""
    return _date_open_ms(_next_first_date(trading_date(bucket_start_ms, rule), tf_s), rule)


def calendar_bar_close_ms(bucket_start_ms: int, tf_s: int, rule: str) -> int:
    """Закриття останнього торгового дня бакета за тижневим розкладом — ціль таймера «до закриття» (ADR-0115 §4.4).

    FXCM (`ny_close_*`): останній будній день — тиждень закривається в п'ятницю 17:00 NY, місяць — в останній будній
    день. Свят розклад не знає (ADR-0092). `utc_midnight` торгує щодня — закриття = кінець бакета.
    """
    if rule not in TRADES_WEEKENDS_BY_RULE:
        raise ValueError("calendar tf: unknown anchor rule=%r (allowed: %s)" % (rule, sorted(TRADES_WEEKENDS_BY_RULE)))
    last_day = _next_first_date(trading_date(bucket_start_ms, rule), tf_s) - dt.timedelta(days=1)
    if not TRADES_WEEKENDS_BY_RULE[rule]:
        while last_day.weekday() >= 5:
            last_day -= dt.timedelta(days=1)
    return _date_open_ms(last_day + dt.timedelta(days=1), rule)


def aggregate_calendar_bars(days: Sequence[DayBar], tf_s: int, rule: str) -> List[CalendarBar]:
    """D1 (зростання open_ms, без дублікатів) → бари W1/MN, complete=False: фінальність вирішує `project_calendar_bars`.

    o — перший D1 бакета, h/l — max/min, c — останній, v — сума. Невідсортований вхід — ValueError, а не тиха
    перестановка: порядок і дедуп — відповідальність читача (вибирач UDS).
    """
    _require_calendar_tf(tf_s)
    out: List[CalendarBar] = []
    group: List[DayBar] = []
    group_key: Optional[int] = None
    prev_open_ms: Optional[int] = None
    for day in days:
        if prev_open_ms is not None and day.open_ms <= prev_open_ms:
            raise ValueError(
                "calendar_days_not_ascending prev_open_ms=%d open_ms=%d" % (prev_open_ms, day.open_ms)
            )
        prev_open_ms = day.open_ms
        key = calendar_bucket_start_ms(day.open_ms, tf_s, rule)
        if key != group_key and group:
            out.append(_merge(group_key, group))  # type: ignore[arg-type]
            group = []
        group_key = key
        group.append(day)
    if group:
        out.append(_merge(group_key, group))  # type: ignore[arg-type]
    return out


def project_calendar_bars(
    final_days: Sequence[DayBar],
    forming_day: Optional[DayBar],
    *,
    tf_s: int,
    rule: str,
    now_ms: int,
    drop_first_bucket: bool = False,
) -> CalendarProjection:
    """Проєкція W1/MN з фінальних D1 і формуючої D1 (ADR-0115 §4.3).

    Бакет фінальний (I3), якщо його час минув і в ньому нема формуючої D1; інакше це формуюча свічка. Формуюча D1, чий
    ключ уже має фінал або старший за останній фінал, не враховується (фінал перемагає preview). `drop_first_bucket`
    — найстаріший бакет може бути обрізаний вікном читання D1: читач відкидає його, якщо не дійшов до початку історії.
    """
    days = list(final_days)
    forming_open_ms: Optional[int] = None
    if forming_day is not None and (not days or forming_day.open_ms > days[-1].open_ms):
        days.append(forming_day)
        forming_open_ms = forming_day.open_ms
    bars = aggregate_calendar_bars(days, tf_s, rule)
    if drop_first_bucket:
        bars = bars[1:]
    finals: List[CalendarBar] = []
    forming: Optional[CalendarBar] = None
    for bar in bars:
        end_ms = calendar_next_bucket_start_ms(bar.open_ms, tf_s, rule)
        holds_forming = forming_open_ms is not None and bar.open_ms <= forming_open_ms < end_ms
        ended = end_ms <= now_ms
        if ended and not holds_forming:
            finals.append(dataclasses.replace(bar, complete=True))
        elif forming is None:
            forming = bar
        else:
            raise AssertionError("calendar_projection_two_forming_buckets open_ms=%d" % bar.open_ms)
    return CalendarProjection(finals=tuple(finals), forming=forming)


def calendar_d1_window_limit(n_buckets: int, tf_s: int) -> int:
    """Скільки D1 читати для `n_buckets` бакетів з запасом на один обрізаний бакет на початку вікна."""
    _require_calendar_tf(tf_s)
    return (max(0, n_buckets) + 1) * D1_PER_BUCKET[tf_s]


def _merge(key: int, group: Iterable[DayBar]) -> CalendarBar:
    group = list(group)
    return CalendarBar(
        open_ms=key,
        o=group[0].o,
        h=max(d.h for d in group),
        low=min(d.low for d in group),
        c=group[-1].c,
        v=sum(d.v for d in group),
        days=len(group),
        complete=False,
    )


def _bucket_first_date(day: dt.date, tf_s: int) -> dt.date:
    _require_calendar_tf(tf_s)
    if tf_s == W1_S:
        return day - dt.timedelta(days=day.weekday())
    return day.replace(day=1)


def _next_first_date(first: dt.date, tf_s: int) -> dt.date:
    _require_calendar_tf(tf_s)
    if tf_s == W1_S:
        return first + dt.timedelta(days=7)
    return dt.date(first.year + first.month // 12, first.month % 12 + 1, 1)


def _date_open_ms(day: dt.date, rule: str) -> int:
    """Відкриття торгового дня з торговою датою `day`: торговий день, що містить полудень UTC цієї дати."""
    return trading_day_open_ms((day - _EPOCH).days * _DAY_MS + _HALF_DAY_MS, rule)


def _date_of_ms(ts_ms: int) -> dt.date:
    return _EPOCH + dt.timedelta(days=ts_ms // _DAY_MS)


def _require_calendar_tf(tf_s: int) -> None:
    if tf_s not in CALENDAR_TFS:
        raise ValueError("unknown calendar tf_s=%r (allowed: %s)" % (tf_s, sorted(CALENDAR_TFS)))

