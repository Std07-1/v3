"""Unit-тести для MarketCalendar: daily break (normal + wrap + multi-break) + weekend.

Покриває інваріанти S2.1 + S4:
- start < end → closed у [start, end)
- start > end (wrap) → closed у [start, 24h) або [0, end)
- start == end → break вимкнений
- daily_breaks: список додаткових break-інтервалів (HKG33 lunch)
"""
from __future__ import annotations

import datetime as dt
import unittest

from runtime.ingest.market_calendar import MarketCalendar


def _ms(year, month, day, hour, minute):
    # type: (...) -> int
    """Побудувати epoch ms UTC."""
    d = dt.datetime(year, month, day, hour, minute, tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


def _make_cal(break_start_hm, break_end_hm, break_enabled=True, daily_breaks=()):
    # type: (...) -> MarketCalendar
    """Створити календар без weekend-закриття (focus на daily break)."""
    return MarketCalendar(
        enabled=True,
        weekend_close_dow=4,
        weekend_close_hm="23:59",
        weekend_open_dow=6,
        weekend_open_hm="22:00",
        daily_break_start_hm=break_start_hm,
        daily_break_end_hm=break_end_hm,
        daily_break_enabled=break_enabled,
        daily_breaks=daily_breaks,
    )


class TestDailyBreakNormal(unittest.TestCase):
    """Break start < end (напр. cfd_us_22_23: 22:00-23:00)."""

    def setUp(self):
        self.cal = _make_cal("22:00", "23:00")

    def test_before_break_open(self):
        # Вівторок 21:59 → торгова хвилина
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 10, 21, 59)))

    def test_at_break_start_closed(self):
        # Вівторок 22:00 → closed (break)
        self.assertFalse(self.cal.is_trading_minute(_ms(2026, 2, 10, 22, 0)))

    def test_mid_break_closed(self):
        # Вівторок 22:30 → closed
        self.assertFalse(self.cal.is_trading_minute(_ms(2026, 2, 10, 22, 30)))

    def test_at_break_end_open(self):
        # Вівторок 23:00 → open (end є exclusive)
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 10, 23, 0)))

    def test_after_break_open(self):
        # Вівторок 23:01 → open
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 10, 23, 1)))


class TestDailyBreakWrap(unittest.TestCase):
    """Break start > end (wrap через північ, напр. cfd_eu_21_07: 21:00-07:00)."""

    def setUp(self):
        self.cal = _make_cal("21:00", "07:00")

    def test_before_break_open(self):
        # Вівторок 20:59 → торгова хвилина
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 10, 20, 59)))

    def test_at_break_start_closed(self):
        # Вівторок 21:00 → closed
        self.assertFalse(self.cal.is_trading_minute(_ms(2026, 2, 10, 21, 0)))

    def test_mid_break_before_midnight_closed(self):
        # Вівторок 23:30 → closed
        self.assertFalse(self.cal.is_trading_minute(_ms(2026, 2, 10, 23, 30)))

    def test_midnight_closed(self):
        # Середа 00:00 → closed (в інтервалі [0, end))
        self.assertFalse(self.cal.is_trading_minute(_ms(2026, 2, 11, 0, 0)))

    def test_mid_break_after_midnight_closed(self):
        # Середа 05:00 → closed
        self.assertFalse(self.cal.is_trading_minute(_ms(2026, 2, 11, 5, 0)))

    def test_at_break_end_open(self):
        # Середа 07:00 → open (end exclusive)
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 11, 7, 0)))

    def test_after_break_open(self):
        # Середа 08:00 → open
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 11, 8, 0)))


class TestDailyBreakWrapHK(unittest.TestCase):
    """Break start > end (wrap, cfd_hk_main: 19:00-01:15)."""

    def setUp(self):
        self.cal = _make_cal("19:00", "01:15")

    def test_before_break_open(self):
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 10, 18, 59)))

    def test_at_break_start_closed(self):
        self.assertFalse(self.cal.is_trading_minute(_ms(2026, 2, 10, 19, 0)))

    def test_midnight_closed(self):
        self.assertFalse(self.cal.is_trading_minute(_ms(2026, 2, 11, 0, 0)))

    def test_just_before_end_closed(self):
        self.assertFalse(self.cal.is_trading_minute(_ms(2026, 2, 11, 1, 14)))

    def test_at_break_end_open(self):
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 11, 1, 15)))

    def test_after_break_open(self):
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 11, 2, 0)))


class TestDailyBreakEqualDisabled(unittest.TestCase):
    """Break start == end → break вимкнений (0-довжина)."""

    def setUp(self):
        self.cal = _make_cal("00:00", "00:00")

    def test_midnight_open(self):
        # 00:00 → торгова хвилина (break disabled)
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 10, 0, 0)))

    def test_noon_open(self):
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 10, 12, 0)))

    def test_2359_open(self):
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 10, 23, 59)))


class TestDailyBreakDisabledFlag(unittest.TestCase):
    """daily_break_enabled=False → break не діє навіть якщо заповнені поля."""

    def setUp(self):
        self.cal = _make_cal("21:00", "07:00", break_enabled=False)

    def test_mid_break_open_when_disabled(self):
        # 23:00 → break disabled → open
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 10, 23, 0)))


class TestMultiBreakHKG33(unittest.TestCase):
    """HKG33: primary break 19:00-01:15 (wrap) + lunch 04:00-05:00 + afternoon 08:30-09:15."""

    def setUp(self):
        self.cal = _make_cal(
            "19:00", "01:15",
            daily_breaks=(("04:00", "05:00"), ("08:30", "09:15")),
        )

    # --- Primary break (wrap) ---
    def test_primary_before_break_open(self):
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 10, 18, 59)))

    def test_primary_at_start_closed(self):
        self.assertFalse(self.cal.is_trading_minute(_ms(2026, 2, 10, 19, 0)))

    def test_primary_midnight_closed(self):
        self.assertFalse(self.cal.is_trading_minute(_ms(2026, 2, 11, 0, 0)))

    def test_primary_at_end_open(self):
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 11, 1, 15)))

    # --- Lunch break 04:00-05:00 ---
    def test_lunch_before_open(self):
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 11, 3, 59)))

    def test_lunch_at_start_closed(self):
        self.assertFalse(self.cal.is_trading_minute(_ms(2026, 2, 11, 4, 0)))

    def test_lunch_mid_closed(self):
        self.assertFalse(self.cal.is_trading_minute(_ms(2026, 2, 11, 4, 30)))

    def test_lunch_at_end_open(self):
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 11, 5, 0)))

    # --- Afternoon break 08:30-09:15 ---
    def test_afternoon_before_open(self):
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 11, 8, 29)))

    def test_afternoon_at_start_closed(self):
        self.assertFalse(self.cal.is_trading_minute(_ms(2026, 2, 11, 8, 30)))

    def test_afternoon_mid_closed(self):
        self.assertFalse(self.cal.is_trading_minute(_ms(2026, 2, 11, 9, 0)))

    def test_afternoon_at_end_open(self):
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 11, 9, 15)))

    # --- Between breaks: open ---
    def test_between_lunch_and_afternoon_open(self):
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 11, 6, 0)))

    def test_after_all_breaks_open(self):
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 11, 10, 0)))


class TestMultiBreakNoOverlap(unittest.TestCase):
    """daily_breaks without primary break (primary disabled via 00:00-00:00)."""

    def setUp(self):
        self.cal = _make_cal(
            "00:00", "00:00",
            daily_breaks=(("12:00", "13:00"),),
        )

    def test_morning_open(self):
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 10, 10, 0)))

    def test_lunch_closed(self):
        self.assertFalse(self.cal.is_trading_minute(_ms(2026, 2, 10, 12, 30)))

    def test_afternoon_open(self):
        self.assertTrue(self.cal.is_trading_minute(_ms(2026, 2, 10, 14, 0)))


if __name__ == "__main__":
    unittest.main()
