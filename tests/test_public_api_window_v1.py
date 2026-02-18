from __future__ import annotations

import unittest

from ui_chart_v3.server import (
    _normalize_bar_window_v1,
    _normalize_bars_window_v1,
    _normalize_update_events_window_v1,
)


class TestPublicApiWindowV1(unittest.TestCase):
    def test_normalize_compact_to_window_v1(self) -> None:
        raw = {
            "open_ms": 1_700_000_000_000,
            "close_ms": 1_700_000_300_000,
            "o": 100.0,
            "h": 101.0,
            "l": 99.5,
            "c": 100.5,
            "v": 10.0,
            "src": "history",
            "complete": True,
        }
        bar = _normalize_bar_window_v1(raw, symbol="XAU/USD", tf_s=300)
        self.assertIsNotNone(bar)
        if bar is None:
            return
        self.assertIn("time", bar)
        self.assertIn("open", bar)
        self.assertIn("high", bar)
        self.assertIn("low", bar)
        self.assertIn("close", bar)
        self.assertIn("volume", bar)
        self.assertNotIn("o", bar)
        self.assertNotIn("h", bar)
        self.assertNotIn("l", bar)
        self.assertNotIn("c", bar)
        self.assertNotIn("v", bar)
        self.assertNotIn("open_ms", bar)
        self.assertNotIn("close_ms", bar)

    def test_normalize_missing_fields_returns_none(self) -> None:
        raw = {
            "open_ms": 1_700_000_000_000,
            "o": 100.0,
        }
        bar = _normalize_bar_window_v1(raw, symbol="XAU/USD", tf_s=300)
        self.assertIsNone(bar)

    def test_normalize_bars_drop_invalid_and_no_compact_keys(self) -> None:
        bars = [
            {
                "open_ms": 1_700_000_000_000,
                "close_ms": 1_700_000_300_000,
                "o": 100.0,
                "h": 101.0,
                "l": 99.5,
                "c": 100.5,
                "v": 10.0,
                "src": "history",
                "complete": True,
            },
            {"open_time_ms": 1_700_000_300_000, "o": 100.0},
        ]
        out, dropped, examples = _normalize_bars_window_v1(
            bars,
            symbol="XAU/USD",
            tf_s=300,
        )
        self.assertEqual(dropped, 1)
        self.assertTrue(examples)
        self.assertEqual(len(out), 1)
        self.assertNotIn("o", out[0])
        self.assertNotIn("open_ms", out[0])

    def test_normalize_updates_no_compact_keys(self) -> None:
        events = [
            {
                "key": {"symbol": "XAU/USD", "tf_s": 300, "open_ms": 1_700_000_000_000},
                "bar": {
                    "open_ms": 1_700_000_000_000,
                    "close_ms": 1_700_000_300_000,
                    "o": 100.0,
                    "h": 101.0,
                    "l": 99.5,
                    "c": 100.5,
                    "v": 10.0,
                    "src": "history",
                    "complete": True,
                },
                "complete": True,
                "source": "history",
            },
            {"key": {"symbol": "XAU/USD", "tf_s": 300, "open_ms": 1}, "bar": "bad", "complete": True, "source": "history"},
        ]
        out, dropped, examples = _normalize_update_events_window_v1(
            events,
            symbol="XAU/USD",
            tf_s=300,
        )
        self.assertEqual(dropped, 1)
        self.assertTrue(examples)
        self.assertEqual(len(out), 1)
        self.assertNotIn("o", out[0]["bar"])
        self.assertNotIn("open_ms", out[0]["bar"])


if __name__ == "__main__":
    unittest.main()
