"""ADR-0115 §10: профіль тижня/місяця — слоти, вибірка, утримання екстремуму, Вілсон, тексти розділу «Розбору»."""

from __future__ import annotations

import datetime as dt

import pytest

from core.calendar_profile import (
    HIGH,
    LOW,
    MIN_SAMPLE,
    CalendarProfile,
    SideProfile,
    OLD_KEY_ERA_MIN_WEEKEND_DAYS,
    SECTION_KEY,
    build_profile,
    compose_profile_section,
    day_slot,
    live_sides,
    wilson_interval,
)
from core.calendar_tf import MN_S, W1_S, DayBar
from core.session_anchor import trading_day_open_ms

NY = "ny_close_us_dst"


def _open_ms(trading: dt.date) -> int:
    """Відкриття торгового дня з торговою датою `trading`: напередодні 17:00 NY (21:00 UTC улітку, 22:00 узимку)."""
    noon = int(dt.datetime(trading.year, trading.month, trading.day, 12, tzinfo=dt.timezone.utc).timestamp() * 1000)
    return trading_day_open_ms(noon, NY)


def _day(trading: dt.date, h: float, low: float) -> DayBar:
    return DayBar(_open_ms(trading), (h + low) / 2, h, low, (h + low) / 2, 1.0)


def _week(monday: dt.date, highs, lows):
    return [_day(monday + dt.timedelta(days=i), h, lo) for i, (h, lo) in enumerate(zip(highs, lows))]


NOW = _open_ms(dt.date(2026, 9, 1))
SINCE = 0
# Три тижні серпня 2026: хай пн / пт / ср, лой ср / пн / пт
WEEKS = (
    _week(dt.date(2026, 8, 3), (10, 9, 8, 9, 9), (5, 4, 3, 4, 4))
    + _week(dt.date(2026, 8, 10), (9, 9, 9, 9, 12), (2, 4, 4, 4, 4))
    + _week(dt.date(2026, 8, 17), (9, 9, 13, 9, 9), (4, 4, 4, 4, 1))
)


def test_day_slot_weekday_and_week_of_month():
    assert day_slot(_day(dt.date(2026, 10, 14), 1, 0), W1_S, NY) == 2  # середа
    assert day_slot(_day(dt.date(2026, 10, 14), 1, 0), MN_S, NY) == 1  # 8–14
    assert day_slot(_day(dt.date(2026, 10, 30), 1, 0), MN_S, NY) == 4  # 29–31
    assert day_slot(_day(dt.date(2026, 10, 31), 1, 0), W1_S, NY) is None  # торгова дата в суботу


def test_build_profile_slot_counts_and_hold_tables():
    prof = build_profile(WEEKS, tf_s=W1_S, rule=NY, now_ms=NOW, since_ms=SINCE)
    assert prof.buckets == 3
    assert prof.sides[HIGH].slot_counts == (1, 0, 1, 0, 1)
    assert prof.sides[LOW].slot_counts == (1, 0, 1, 0, 1)
    # станом на середу (k=2) хай стояв з понеділка у тижнях 1 і 2; лишився лише в тижні 1
    assert prof.sides[HIGH].hold_n[2][0] == 2 and prof.sides[HIGH].hold_ok[2][0] == 1
    # тиждень 3: станом на середу хай із середи (13) — лишився
    assert prof.sides[HIGH].hold_n[2][2] == 1 and prof.sides[HIGH].hold_ok[2][2] == 1


def test_unfinished_and_weekend_buckets_are_excluded():
    current = _week(dt.date(2026, 8, 31), (1, 2), (0, 0))  # тиждень ще йде
    stub_week = _week(dt.date(2026, 8, 24), (1, 1, 1, 1, 1), (0, 0, 0, 0, 0)) + [_day(dt.date(2026, 8, 29), 5, 0)]
    prof = build_profile(WEEKS + stub_week + current, tf_s=W1_S, rule=NY, now_ms=NOW, since_ms=SINCE)
    assert prof.buckets == 3, "огризок суботи виключає свій тиждень, а поточний тиждень не закінчився"


def test_old_key_era_is_cut_entirely():
    """≥ порога D1 на вихідних — вибірка після останньої такої D1, навіть тижні старої епохи без «суботи» не беруться."""
    old = []
    monday = dt.date(2025, 1, 6)
    for i in range(OLD_KEY_ERA_MIN_WEEKEND_DAYS):  # стара конвенція: торгові дати вт–сб
        old += _week(monday + dt.timedelta(days=7 * i + 1), (1, 1, 1, 1, 1), (0, 0, 0, 0, 0))
    holiday_week = _week(monday + dt.timedelta(days=7 * OLD_KEY_ERA_MIN_WEEKEND_DAYS + 1), (1, 1, 1, 1), (0, 0, 0, 0))
    prof = build_profile(old + holiday_week + WEEKS, tf_s=W1_S, rule=NY, now_ms=NOW, since_ms=SINCE)
    assert prof.buckets == 3 + 1  # тиждень-свято після останньої «суботи» вже за межею епохи
    few = build_profile(old[:5 * 3] + WEEKS, tf_s=W1_S, rule=NY, now_ms=NOW, since_ms=SINCE)
    assert few.buckets == 3, "мало «субот» — епохи нема, тижні з «суботою» відсіює правило чистого бакета"


def test_live_sides_current_week():
    current = _week(dt.date(2026, 10, 12), (5, 4, 3), (2, 1, 3))
    live = live_sides(current, tf_s=W1_S, rule=NY)
    assert (live[HIGH].slot, live[HIGH].price, live[HIGH].current_slot) == (0, 5, 2)
    assert (live[LOW].slot, live[LOW].price) == (1, 1)
    assert live_sides([], tf_s=W1_S, rule=NY) == {}


def test_wilson_interval_known_value():
    lo, hi = wilson_interval(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-4) and hi == pytest.approx(0.5962, abs=1e-4)
    with pytest.raises(ValueError):
        wilson_interval(3, 0)


def _big_profile():
    weeks = []
    for i in range(30):  # 30 тижнів: хай у понеділок, лой у п'ятницю; у кожному 3-му хай перебито в четвер
        monday = dt.date(2025, 6, 2) + dt.timedelta(days=7 * i)
        highs = (10, 9, 9, 11 if i % 3 == 0 else 9, 9)
        weeks += _week(monday, highs, (5, 5, 5, 5, 1))
    return build_profile(weeks, tf_s=W1_S, rule=NY, now_ms=_open_ms(dt.date(2026, 1, 1)), since_ms=SINCE)


def test_section_texts_w1():
    prof = _big_profile()
    live = live_sides(_week(dt.date(2026, 10, 12), (4217.31, 4200.0, 4170.0), (4154.28, 4146.9, 4073.42)),
                      tf_s=W1_S, rule=NY)
    sec = compose_profile_section(prof, live, price_digits=2, since_year=2025)
    assert sec.key == SECTION_KEY and sec.title == "Профіль тижня"
    high_live, low_live, high_dist, low_dist = sec.rows
    assert high_live.text == "Хай тижня поки з понеділка — 4217.31" and high_live.tf == "W1"
    assert high_live.notes == ("Історично, коли станом на середу хай тижня стояв з понеділка, він лишився хаєм тижня "
                               "в 67 % тижнів (49–81 %, вибірка 30)",)
    assert low_live.text == "Лой тижня поки із середи — 4073.42"
    assert "замало для частки" in low_live.notes[0]
    assert high_dist.text == "Хай тижня — у який день: Пн 67 · Вт 0 · Ср 0 · Чт 33 · Пт 0 %"
    assert high_dist.notes[0].startswith("30 тижнів з 2025, 95 % інтервал до ±")


def test_section_last_slot_has_no_conditional_and_small_sample_note():
    prof = build_profile(WEEKS, tf_s=W1_S, rule=NY, now_ms=NOW, since_ms=SINCE)
    friday = live_sides(_week(dt.date(2026, 10, 12), (1, 2, 3, 4, 5), (0, 0, 0, 0, 0)), tf_s=W1_S, rule=NY)
    sec = compose_profile_section(prof, friday, price_digits=2, since_year=None)
    assert sec.rows[0].notes == ()  # п'ятниця — умовна частка тривіальна
    assert sec.rows[2].notes == ("Вибірка замала (3 тижні)",)
    assert prof.buckets < MIN_SAMPLE


def _flat_profile(tf_s: int, buckets: int) -> CalendarProfile:
    side = SideProfile(slot_counts=(buckets, 0, 0, 0, 0), hold_n=((0,) * 5,) * 5, hold_ok=((0,) * 5,) * 5)
    return CalendarProfile(tf_s=tf_s, buckets=buckets, first_bucket_ms=None, sides={HIGH: side, LOW: side})


@pytest.mark.parametrize("buckets, noun", [(21, "місяць"), (22, "місяці"), (25, "місяців"), (111, "місяців")])
def test_section_month_labels_and_plural(buckets, noun):
    sec = compose_profile_section(_flat_profile(MN_S, buckets), {}, price_digits=2, since_year=2008)
    assert sec.title == "Профіль місяця" and len(sec.rows) == 2  # без поточного бакета — лише розподіли
    assert sec.rows[0].text == "Хай місяця — у які числа: 1–7 100 · 8–14 0 · 15–21 0 · 22–28 0 · 29–31 0 %"
    assert sec.rows[0].notes[0].startswith("%d %s з 2008, 95 %% інтервал до ±" % (buckets, noun))


def test_section_small_month_sample():
    sec = compose_profile_section(_flat_profile(MN_S, 12), {}, price_digits=2, since_year=2008)
    assert sec.rows[0].notes == ("Вибірка замала (12 місяців)",)
