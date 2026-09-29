"""ADR-0104, рішення власника 29.09: «сесії вчора» = попередня торгова доба (у понеділок — п'ятниця).

До рішення «вчора» було календарною добою мінус 1: у понеділок це неділя без сесій, тож п'ятничних H/L не було.
Свято в будній день не підміняється позавчорашнім днем (§3.6), а рівень з обрізаного буфера M1 не видається.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

from core.model.bars import CandleBar
from core.smc.sessions import _previous_trading_day_start, compute_session_levels, load_session_windows

REPO = pathlib.Path(__file__).resolve().parents[1]


def _windows():
    cfg = json.loads((REPO / "config.json").read_text(encoding="utf-8"))
    return load_session_windows(cfg["smc"]["sessions"]["definitions"])


def _ms(text):
    return int(dt.datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def _bar(t, h, low):
    open_ms = _ms(t)
    return CandleBar(symbol="XAU/USD", tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + 60000, o=low, h=h,
                     low=low, c=h, v=1.0, complete=True, src="history")


def _previous_levels(bars, now):
    levels, _states = compute_session_levels(bars, _windows(), _ms(now), "XAU/USD")
    return {lv.kind: lv for lv in levels if lv.kind.startswith("p_")}


# Буфер з четверга (покриває всю п'ятницю); Пт 25.09: Азія, Лондон, Нью-Йорк; Нд 27.09 — вечірнє відкриття ринку
# поза сесіями; Пн 28.09 — Азія
_FRIDAY = [_bar("2026-09-24 20:00", 3600.0, 3590.0), _bar("2026-09-25 01:00", 3710.0, 3700.0),
           _bar("2026-09-25 09:00", 3730.0, 3690.0), _bar("2026-09-25 14:00", 3750.0, 3720.0)]
_SUNDAY_OPEN = [_bar("2026-09-27 22:00", 3800.0, 3600.0)]
_MONDAY = [_bar("2026-09-28 02:00", 3760.0, 3740.0)]


def test_previous_trading_day_skips_the_weekend_and_keeps_weekdays():
    assert _previous_trading_day_start(_ms("2026-09-28 00:00")) == _ms("2026-09-25 00:00")  # Пн → Пт
    assert _previous_trading_day_start(_ms("2026-09-27 00:00")) == _ms("2026-09-25 00:00")  # Нд → Пт
    assert _previous_trading_day_start(_ms("2026-09-26 00:00")) == _ms("2026-09-25 00:00")  # Сб → Пт
    assert _previous_trading_day_start(_ms("2026-09-29 00:00")) == _ms("2026-09-28 00:00")  # Вт → Пн


def test_monday_previous_sessions_are_fridays_not_the_sunday_open():
    prev = _previous_levels(_FRIDAY + _SUNDAY_OPEN + _MONDAY, "2026-09-28 03:00")
    assert {k: v.price for k, v in prev.items()} == {
        "p_as_h": 3710.0, "p_as_l": 3700.0,
        "p_lon_h": 3750.0, "p_lon_l": 3690.0,   # Лондон 07–16 UTC включає і 14:00 (перекриття з NY)
        "p_ny_h": 3750.0, "p_ny_l": 3720.0,
    }
    assert prev["p_lon_h"].key == "london:high:XAU/USD:2026-09-25T07:00Z"
    assert all(v.state == "fixed" and v.auto is False for v in prev.values())


def test_sunday_before_the_open_shows_fridays_sessions_as_previous():
    prev = _previous_levels(_FRIDAY, "2026-09-27 12:00")
    assert prev["p_ny_h"].price == 3750.0


def test_weekday_holiday_is_not_replaced_by_the_day_before():
    # Ср 30.09 без жодного бару (свято): у четвер «вчора» — порожньо, а не вівторок
    tuesday = [_bar("2026-09-29 09:00", 3900.0, 3880.0)]
    thursday = [_bar("2026-10-01 01:00", 3950.0, 3940.0)]
    assert _previous_levels(tuesday + thursday, "2026-10-01 03:00") == {}


def test_session_that_opened_before_the_m1_buffer_start_is_not_published():
    # Буфер починається о 10:00 п'ятниці: Азія (00:00) і Лондон (07:00) обрізані, Нью-Йорк (12:00) — повний
    buffer = [_bar("2026-09-25 10:00", 3735.0, 3725.0), _bar("2026-09-25 14:00", 3750.0, 3720.0)] + _MONDAY
    prev = _previous_levels(buffer, "2026-09-28 03:00")
    assert set(prev) == {"p_ny_h", "p_ny_l"}
