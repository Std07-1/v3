"""Слот джерела входить в агрегат, якщо в ньому є хоч одна торгова хвилина (ADR-0054 §3.8 п.2).

Раніше has_range/range_bars/missing_count і boundary-tolerant збір судили слот за першою хвилиною. Сесія, що
відкривається не на межі слота, губила цілий слот із торговими хвилинами:
- GER30 (перерва 20:00–00:30): H4 22:00 без H1 00:00, тобто без 00:30–00:59 — щодня; на засіяних 15.09 даних
  40 із 239 H4 з хибним open і обсягом;
- HK (перерва 19:00–01:15): H4 22:00 не будувався зовсім — єдиний торговий H1 01:00 вважався перервою;
- FX (перерва 20:55–21:30): H4 18:00 без H1 21:00, тобто без 21:30–21:59.
Для груп із reopen на межі слота (XAU, US-індекси) результат не змінюється — перевірено rebuild XAU старим і
новим кодом: усі 7 TF байт-у-байт.
"""
from __future__ import annotations

import datetime as dt

from core.derive import GenericBuffer, derive_bar
from core.model.bars import CandleBar
from runtime.ingest.market_calendar import MarketCalendar

H1_S = 3600
H4_S = 14400
H4_ANCHOR_S = 79200  # літо: 22:00 UTC

CAL_GER30 = MarketCalendar(
    enabled=True,
    weekend_close_dow=4, weekend_close_hm="20:00",
    weekend_open_dow=0, weekend_open_hm="00:30",
    daily_break_start_hm="20:00", daily_break_end_hm="00:30",
    daily_break_enabled=True,
)
CAL_HK = MarketCalendar(
    enabled=True,
    weekend_close_dow=4, weekend_close_hm="19:00",
    weekend_open_dow=0, weekend_open_hm="01:15",
    daily_break_start_hm="19:00", daily_break_end_hm="01:15",
    daily_break_enabled=True,
    daily_breaks=(("04:00", "05:00"), ("08:30", "09:15")),
)
CAL_FX = MarketCalendar(
    enabled=True,
    weekend_close_dow=4, weekend_close_hm="20:55",
    weekend_open_dow=6, weekend_open_hm="21:00",
    daily_break_start_hm="00:00", daily_break_end_hm="00:00",
    daily_break_enabled=True,
    daily_breaks=(("20:55", "21:30"),),
)


def _ms(day: int, hour: int) -> int:
    return int(dt.datetime(2026, 9, day, hour, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _h1(open_ms: int, *, o: float, c: float, v: float) -> CandleBar:
    return CandleBar(symbol="SYM", tf_s=H1_S, open_time_ms=open_ms, close_time_ms=open_ms + H1_S * 1000,
                     o=o, h=max(o, c) + 1.0, low=min(o, c) - 1.0, c=c, v=v, complete=True, src="derived")


def _derive_h4(bucket_open_ms: int, bars, calendar: MarketCalendar):
    buf = GenericBuffer(tf_s=H1_S, max_keep=64)
    buf.upsert_many(list(bars))
    return derive_bar(symbol="SYM", target_tf_s=H4_S, source_buffer=buf, bucket_open_ms=bucket_open_ms,
                      anchor_offset_s=H4_ANCHOR_S, is_trading_fn=calendar.is_trading_minute)


def test_derive_h4_includes_partial_first_slot_ger30_0030():
    """Вт 00:30 reopen: H1 00:00 несе 00:30–00:59 і відкриває H4 22:00 (Пн 22:00 → Вт 02:00)."""
    reopen_hour = _h1(_ms(8, 0), o=100.0, c=101.0, v=30.0)
    next_hour = _h1(_ms(8, 1), o=101.0, c=102.0, v=60.0)
    out = _derive_h4(_ms(7, 22), [reopen_hour, next_hour], CAL_GER30)
    assert out is not None
    assert (out.o, out.c, out.v) == (100.0, 102.0, 90.0)
    assert not out.extensions.get("partial")


def test_derive_h4_includes_partial_first_slot_hk_0115():
    """HK: єдиний торговий H1 бакета 22:00 — 01:00 (01:15–01:59); раніше H4 не будувався зовсім."""
    only_trading_hour = _h1(_ms(8, 1), o=200.0, c=201.0, v=45.0)
    out = _derive_h4(_ms(7, 22), [only_trading_hour], CAL_HK)
    assert out is not None
    assert (out.o, out.c, out.v) == (200.0, 201.0, 45.0)


def test_derive_h4_includes_partial_first_slot_fx_2130():
    """FX: H1 21:00 (21:30–21:59) закриває H4 18:00; раніше close і обсяг брались з H1 20:00."""
    hours = [_h1(_ms(8, h), o=300.0 + h, c=301.0 + h, v=10.0) for h in (18, 19, 20, 21)]
    out = _derive_h4(_ms(8, 18), hours, CAL_FX)
    assert out is not None
    assert (out.o, out.c, out.v) == (318.0, 322.0, 40.0)


def test_missing_reopen_slot_is_loud_boundary_partial_not_silently_short():
    """Без H1 00:00 бар GER30 22:00 не видає себе за повний: boundary_partial, expected рахує і слот reopen."""
    next_hour = _h1(_ms(8, 1), o=101.0, c=102.0, v=60.0)
    out = _derive_h4(_ms(7, 22), [next_hour], CAL_GER30)
    assert out is not None
    assert out.extensions.get("boundary_partial") is True
    assert out.extensions.get("expected_count") == 2


def test_slot_fully_inside_break_stays_excluded_and_not_required():
    """H1 22:00 і 23:00 GER30 лежать повністю в перерві: їх нема в буфері, і це не дірка."""
    buf = GenericBuffer(tf_s=H1_S, max_keep=64)
    buf.upsert_many([_h1(_ms(8, 0), o=1.0, c=1.0, v=1.0), _h1(_ms(8, 1), o=1.0, c=1.0, v=1.0)])
    assert buf.has_range(_ms(7, 22), _ms(8, 2), CAL_GER30.is_trading_minute)
    assert buf.missing_count(_ms(7, 22), _ms(8, 2), CAL_GER30.is_trading_minute) == 0


def test_m1_source_slot_is_judged_by_its_own_minute():
    """Для M1-джерела предикат — рівно одна хвилина, як і до зміни: хвилина перерви не рахується відсутньою."""
    minute_ms = 60_000
    buf = GenericBuffer(tf_s=60, max_keep=16)
    open_ms = _ms(8, 0) + 30 * minute_ms  # 00:30 — перша торгова хвилина GER30
    assert buf.missing_count(open_ms - minute_ms, open_ms + minute_ms, CAL_GER30.is_trading_minute) == 1
