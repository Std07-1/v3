"""Тести calendar gate для TickPreviewWorker (P2X.6-T1)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock
import datetime as dt

from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.tick_preview_worker import TickPreviewWorker


def _ts_ms(year, month, day, hour, minute):
    """UTC datetime → epoch ms."""
    d = dt.datetime(year, month, day, hour, minute, tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


def _make_tick(symbol, tick_ts_ms, mid=100.0):
    return {
        "v": 1,
        "symbol": symbol,
        "tick_ts_ms": tick_ts_ms,
        "mid": mid,
        "src": "test",
        "seq": 1,
    }


def _make_worker(symbols, calendars):
    uds = MagicMock()
    uds.publish_preview_bar = MagicMock()
    worker = TickPreviewWorker(
        uds=uds,
        tfs=[60, 180],
        publish_min_interval_ms=0,
        curr_ttl_s=1800,
        symbols=symbols,
        channel="test:chan",
        calendars=calendars,
    )
    return worker, uds


# --- Calendars ---

# cfd_us_22_23: daily break 22:00-23:00
CAL_US = MarketCalendar(
    enabled=True,
    weekend_close_dow=4, weekend_close_hm="21:45",
    weekend_open_dow=6,  weekend_open_hm="23:00",
    daily_break_start_hm="22:00", daily_break_end_hm="23:00",
    daily_break_enabled=True,
)

# cfd_eu_21_07: daily break 21:00-07:00 (wrap через північ)
CAL_EU = MarketCalendar(
    enabled=True,
    weekend_close_dow=4, weekend_close_hm="21:00",
    weekend_open_dow=0,  weekend_open_hm="07:00",
    daily_break_start_hm="21:00", daily_break_end_hm="07:00",
    daily_break_enabled=True,
)

# cfd_hk_main: multi-break (19:00-01:15 primary + lunch 04:00-05:00 + afternoon 08:30-09:15)
CAL_HK = MarketCalendar(
    enabled=True,
    weekend_close_dow=4, weekend_close_hm="19:00",
    weekend_open_dow=0,  weekend_open_hm="01:15",
    daily_break_start_hm="19:00", daily_break_end_hm="01:15",
    daily_break_enabled=True,
    daily_breaks=(("04:00", "05:00"), ("08:30", "09:15")),
)


class TestCalendarGateTickDrop(unittest.TestCase):
    """Тест: тик у break → drop."""

    def test_tick_in_break_dropped(self):
        # Середа 2026-02-11 22:30 UTC — всередині US break 22:00-23:00
        ts = _ts_ms(2026, 2, 11, 22, 30)
        self.assertFalse(CAL_US.is_trading_minute(ts))
        worker, uds = _make_worker(["XAU/USD"], {"XAU/USD": CAL_US})
        worker.on_tick(_make_tick("XAU/USD", ts))
        uds.publish_preview_bar.assert_not_called()
        self.assertGreater(worker._stats.get("ticks_dropped_calendar_closed", 0), 0)

    def test_tick_in_open_accepted(self):
        # Середа 2026-02-11 20:00 UTC — відкритий ринок
        ts = _ts_ms(2026, 2, 11, 20, 0)
        self.assertTrue(CAL_US.is_trading_minute(ts))
        worker, uds = _make_worker(["XAU/USD"], {"XAU/USD": CAL_US})
        worker.on_tick(_make_tick("XAU/USD", ts))
        self.assertTrue(uds.publish_preview_bar.called)


class TestCalendarGateWrapBreak(unittest.TestCase):
    """Тест: wrap-break (EU 21:00→07:00) — коректно closed вночі."""

    def test_eu_night_dropped(self):
        # Вівторок 2026-02-10 03:00 UTC — ніч, GER30 закритий
        ts = _ts_ms(2026, 2, 10, 3, 0)
        self.assertFalse(CAL_EU.is_trading_minute(ts))
        worker, uds = _make_worker(["GER30"], {"GER30": CAL_EU})
        worker.on_tick(_make_tick("GER30", ts))
        uds.publish_preview_bar.assert_not_called()

    def test_eu_day_accepted(self):
        # Вівторок 2026-02-10 12:00 UTC — день, GER30 відкритий
        ts = _ts_ms(2026, 2, 10, 12, 0)
        self.assertTrue(CAL_EU.is_trading_minute(ts))
        worker, uds = _make_worker(["GER30"], {"GER30": CAL_EU})
        worker.on_tick(_make_tick("GER30", ts))
        self.assertTrue(uds.publish_preview_bar.called)


class TestCalendarGateMultiBreak(unittest.TestCase):
    """Тест: multi-break (HKG33 lunch 04:00-05:00)."""

    def test_hk_lunch_dropped(self):
        # Вівторок 2026-02-10 04:30 UTC — HKG33 lunch break
        ts = _ts_ms(2026, 2, 10, 4, 30)
        self.assertFalse(CAL_HK.is_trading_minute(ts))
        worker, uds = _make_worker(["HKG33"], {"HKG33": CAL_HK})
        worker.on_tick(_make_tick("HKG33", ts))
        uds.publish_preview_bar.assert_not_called()

    def test_hk_trading_accepted(self):
        # Вівторок 2026-02-10 06:00 UTC — HKG33 між ланчем і afternoon
        ts = _ts_ms(2026, 2, 10, 6, 0)
        self.assertTrue(CAL_HK.is_trading_minute(ts))
        worker, uds = _make_worker(["HKG33"], {"HKG33": CAL_HK})
        worker.on_tick(_make_tick("HKG33", ts))
        self.assertTrue(uds.publish_preview_bar.called)


class TestTickAggVolumeZero(unittest.TestCase):
    """Тест: preview volume завжди 0, ticks_n в extensions."""

    def test_volume_zero_ticks_n(self):
        from runtime.ingest.tick_agg import TickAggregator

        agg = TickAggregator(tf_allowlist=[60])
        ts = _ts_ms(2026, 2, 10, 12, 0)
        promoted1, bar1 = agg.update("TEST", 60, ts, 100.0)
        self.assertIsNone(promoted1)
        self.assertIsNotNone(bar1)
        self.assertEqual(bar1.v, 0.0)
        self.assertEqual(bar1.extensions.get("ticks_n"), 1)
        promoted2, bar2 = agg.update("TEST", 60, ts + 5000, 101.0)
        self.assertIsNone(promoted2)
        self.assertIsNotNone(bar2)
        self.assertEqual(bar2.v, 0.0)
        self.assertEqual(bar2.extensions.get("ticks_n"), 2)


if __name__ == "__main__":
    unittest.main()
