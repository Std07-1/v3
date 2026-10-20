"""ADR-0115 S1: межі W1/MN за торговою датою D1 і проєкція D1 → W1/MN (core.calendar_tf, pure)."""

from __future__ import annotations

import datetime as dt

import pytest

from core.calendar_tf import (
    MN_S,
    TRADES_WEEKENDS_BY_RULE,
    W1_S,
    DayBar,
    aggregate_calendar_bars,
    calendar_bar_close_ms,
    calendar_bucket_start_ms,
    calendar_d1_window_limit,
    calendar_next_bucket_start_ms,
    project_calendar_bars,
    trading_date,
)
from core.session_anchor import HTF_ANCHOR_RULES, RULE_NY_CLOSE_FX, RULE_NY_CLOSE_US_DST, RULE_UTC_MIDNIGHT

NY = RULE_NY_CLOSE_US_DST


def _ms(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> int:
    return int(dt.datetime(y, m, d, h, mi, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _day(open_ms: int, o: float, h: float, low: float, c: float, v: float = 1.0) -> DayBar:
    return DayBar(open_ms=open_ms, o=o, h=h, low=low, c=c, v=v)


def test_trading_date_is_ny_close_date_summer_and_winter():
    assert trading_date(_ms(2026, 10, 11, 21), NY) == dt.date(2026, 10, 12)  # D1 пн, відкриття нд 17:00 EDT
    assert trading_date(_ms(2026, 11, 8, 22), NY) == dt.date(2026, 11, 9)  # зима: нд 17:00 EST = 22:00 UTC
    assert trading_date(_ms(2026, 10, 12, 0), RULE_UTC_MIDNIGHT) == dt.date(2026, 10, 12)


def test_w1_bucket_starts_sunday_17_ny_summer_and_winter():
    assert calendar_bucket_start_ms(_ms(2026, 10, 14, 12), W1_S, NY) == _ms(2026, 10, 11, 21)
    assert calendar_bucket_start_ms(_ms(2026, 11, 11, 12), W1_S, NY) == _ms(2026, 11, 8, 22)
    assert calendar_bucket_start_ms(_ms(2026, 10, 14, 12), W1_S, RULE_NY_CLOSE_FX) == _ms(2026, 10, 11, 21)


def test_w1_weekend_belongs_to_ending_week_until_sunday_17_ny():
    week_key = _ms(2026, 10, 11, 21)
    assert calendar_bucket_start_ms(_ms(2026, 10, 17, 10), W1_S, NY) == week_key  # субота
    assert calendar_bucket_start_ms(_ms(2026, 10, 18, 20, 59), W1_S, NY) == week_key  # нд 16:59 NY
    assert calendar_bucket_start_ms(_ms(2026, 10, 18, 21), W1_S, NY) == _ms(2026, 10, 18, 21)  # нд 17:00 NY


def test_w1_dst_weeks_are_167_and_169_hours():
    autumn = _ms(2026, 10, 25, 21)
    assert calendar_next_bucket_start_ms(autumn, W1_S, NY) == _ms(2026, 11, 1, 22)  # 169 год
    spring = _ms(2026, 3, 1, 22)
    assert calendar_next_bucket_start_ms(spring, W1_S, NY) == _ms(2026, 3, 8, 21)  # 167 год


def test_mn_bucket_is_trading_date_month():
    october = _ms(2026, 9, 30, 21)  # торговий день 1 жовтня (чт) відкривається в ср 17:00 NY
    assert calendar_bucket_start_ms(_ms(2026, 9, 30, 21), MN_S, NY) == october
    assert calendar_bucket_start_ms(_ms(2026, 9, 30, 20, 59), MN_S, NY) == _ms(2026, 8, 31, 21)  # ще вересень
    assert calendar_next_bucket_start_ms(october, MN_S, NY) == _ms(2026, 10, 31, 21)  # 1 листопада ще літній час


def test_mn_month_starting_on_weekend_keeps_weekend_in_previous_month_content():
    august = calendar_bucket_start_ms(_ms(2026, 8, 3, 12), MN_S, NY)
    assert august == _ms(2026, 7, 31, 21)  # торговий день з датою 1 серпня (сб) — календарний ключ
    assert calendar_bucket_start_ms(_ms(2026, 7, 30, 21), MN_S, NY) == _ms(2026, 6, 30, 21)  # D1 пт 31.07 — липень


def test_utc_midnight_rule_week_monday_month_first():
    assert calendar_bucket_start_ms(_ms(2026, 10, 14, 5), W1_S, RULE_UTC_MIDNIGHT) == _ms(2026, 10, 12)
    assert calendar_bucket_start_ms(_ms(2026, 12, 31, 23), MN_S, RULE_UTC_MIDNIGHT) == _ms(2026, 12, 1)
    assert calendar_next_bucket_start_ms(_ms(2026, 12, 1), MN_S, RULE_UTC_MIDNIGHT) == _ms(2027, 1, 1)


def test_bar_close_is_last_weekday_close_for_fxcm_rules():
    assert calendar_bar_close_ms(_ms(2026, 10, 11, 21), W1_S, NY) == _ms(2026, 10, 16, 21)  # пт 17:00 NY
    assert calendar_bar_close_ms(_ms(2026, 9, 30, 21), MN_S, NY) == _ms(2026, 10, 30, 21)  # 31.10 — субота
    assert calendar_bar_close_ms(_ms(2026, 8, 31, 21), MN_S, NY) == _ms(2026, 9, 30, 21)  # 30.09 — середа
    assert calendar_bar_close_ms(_ms(2026, 11, 8, 22), W1_S, RULE_NY_CLOSE_FX) == _ms(2026, 11, 13, 22)


def test_bar_close_utc_midnight_is_bucket_end():
    assert calendar_bar_close_ms(_ms(2026, 10, 12), W1_S, RULE_UTC_MIDNIGHT) == _ms(2026, 10, 19)


def test_every_anchor_rule_declares_weekend_trading():
    assert set(TRADES_WEEKENDS_BY_RULE) == set(HTF_ANCHOR_RULES)


def test_unknown_tf_and_rule_fail_loud():
    with pytest.raises(ValueError, match="unknown calendar tf_s"):
        calendar_bucket_start_ms(_ms(2026, 10, 14), 86_400, NY)
    with pytest.raises(ValueError, match="unknown"):
        calendar_bar_close_ms(_ms(2026, 10, 11, 21), W1_S, "nope")


def _week_oct12() -> list:
    """D1 пн–пт тижня 12.10.2026 (відкриття нд–чт 21:00 UTC), ціни — синтетичні."""
    return [
        _day(_ms(2026, 10, 11, 21), 10.0, 12.0, 9.0, 11.0, 5),
        _day(_ms(2026, 10, 12, 21), 11.0, 15.0, 10.5, 14.0, 6),
        _day(_ms(2026, 10, 13, 21), 14.0, 14.5, 8.0, 9.5, 7),
        _day(_ms(2026, 10, 14, 21), 9.5, 13.0, 9.0, 12.0, 8),
        _day(_ms(2026, 10, 15, 21), 12.0, 12.5, 11.0, 11.5, 9),
    ]


def test_aggregate_week_ohlcv():
    (bar,) = aggregate_calendar_bars(_week_oct12(), W1_S, NY)
    assert (bar.open_ms, bar.o, bar.h, bar.low, bar.c, bar.v, bar.days) == (
        _ms(2026, 10, 11, 21), 10.0, 15.0, 8.0, 11.5, 35, 5,
    )


def test_aggregate_rejects_unsorted_days():
    days = _week_oct12()
    with pytest.raises(ValueError, match="calendar_days_not_ascending"):
        aggregate_calendar_bars([days[1], days[0]], W1_S, NY)


def test_aggregate_old_index_convention_friday_session_stays_in_week():
    """Індекси 1990–2008: ключ сесії — її кінець (пт 17:00 NY → торгова дата «субота») — той самий ISO-тиждень."""
    friday_session = _day(_ms(2005, 3, 11, 22), 1.0, 2.0, 0.5, 1.5)  # пт 11.03.2005 17:00 EST
    monday = _day(_ms(2005, 3, 6, 22), 1.0, 1.0, 1.0, 1.0)
    bars = aggregate_calendar_bars([monday, friday_session], W1_S, NY)
    assert len(bars) == 1 and bars[0].days == 2


def test_projection_current_week_is_forming_until_bucket_ends():
    days = _week_oct12()
    saturday = project_calendar_bars(days, None, tf_s=W1_S, rule=NY, now_ms=_ms(2026, 10, 17, 12))
    assert saturday.finals == () and saturday.forming is not None and not saturday.forming.complete
    monday = project_calendar_bars(days, None, tf_s=W1_S, rule=NY, now_ms=_ms(2026, 10, 19, 12))
    assert len(monday.finals) == 1 and monday.finals[0].complete and monday.forming is None


def test_projection_forming_day_extends_new_week():
    forming_day = _day(_ms(2026, 10, 18, 21), 11.5, 13.0, 11.0, 12.5, 2)
    proj = project_calendar_bars(_week_oct12(), forming_day, tf_s=W1_S, rule=NY, now_ms=_ms(2026, 10, 19, 9))
    assert [b.open_ms for b in proj.finals] == [_ms(2026, 10, 11, 21)]
    assert proj.forming is not None
    assert (proj.forming.open_ms, proj.forming.o, proj.forming.c, proj.forming.days) == (
        _ms(2026, 10, 18, 21), 11.5, 12.5, 1,
    )


def test_projection_stale_forming_day_is_ignored_final_wins():
    days = _week_oct12()
    stale = _day(days[-1].open_ms, 0.0, 99.0, 0.0, 0.0)  # preview того ж ключа, що вже має фінал
    proj = project_calendar_bars(days, stale, tf_s=W1_S, rule=NY, now_ms=_ms(2026, 10, 19, 12))
    assert proj.forming is None and proj.finals[0].h == 15.0


def test_projection_ended_month_with_unfinished_last_day_stays_forming():
    """Місяць закінчився в ср 30.09 17:00 NY, фінал D1 ще не записано (≤ 3 хв, ADR-0102) — бар не фінальний (I3)."""
    sep_29 = _day(_ms(2026, 9, 28, 21), 1.0, 2.0, 0.5, 1.5)
    sep_30_forming = _day(_ms(2026, 9, 29, 21), 1.5, 3.0, 1.0, 2.5)
    proj = project_calendar_bars([sep_29], sep_30_forming, tf_s=MN_S, rule=NY, now_ms=_ms(2026, 9, 30, 21, 1))
    assert proj.finals == ()
    assert proj.forming is not None and proj.forming.days == 2 and proj.forming.h == 3.0


def test_projection_drop_first_bucket():
    prev_week_friday = _day(_ms(2026, 10, 8, 21), 1.0, 1.0, 1.0, 1.0)
    proj = project_calendar_bars(
        [prev_week_friday] + _week_oct12(), None, tf_s=W1_S, rule=NY, now_ms=_ms(2026, 10, 19, 12),
        drop_first_bucket=True,
    )
    assert [b.open_ms for b in proj.finals] == [_ms(2026, 10, 11, 21)]


def test_d1_window_limit_has_one_bucket_margin():
    assert calendar_d1_window_limit(520, W1_S) == 521 * 6
    assert calendar_d1_window_limit(240, MN_S) == 241 * 23
