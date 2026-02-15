from __future__ import annotations

import unittest

from runtime.store.layers.ram_layer import RamLayer
from ui_chart_v3.server import _build_no_data_payload


class TestApiBarsNoDataRails(unittest.TestCase):
    def test_no_data_keeps_runtime_warning(self) -> None:
        payload, warnings = _build_no_data_payload(
            symbol="XAU/USD",
            tf_s=14400,
            boot_id="boot-1",
            res_meta={"source": "degraded", "redis_hit": False, "boot_id": "boot-1"},
            final_extra_warnings=[],
            res_warnings=["disk_disabled_cache_miss"],
        )
        self.assertEqual(payload.get("note"), "no_data")
        self.assertEqual(payload.get("bars"), [])
        self.assertIn("disk_disabled_cache_miss", warnings)
        self.assertIn("disk_disabled_cache_miss", payload.get("warnings", []))

    def test_no_data_without_reason_gets_rail_warning(self) -> None:
        payload, warnings = _build_no_data_payload(
            symbol="XAU/USD",
            tf_s=86400,
            boot_id="boot-2",
            res_meta={"source": "degraded", "redis_hit": False, "boot_id": "boot-2"},
            final_extra_warnings=[],
            res_warnings=[],
        )
        self.assertTrue(warnings)
        self.assertIn("no_data_unexplained", warnings)
        self.assertIn("no_data_unexplained", payload.get("warnings", []))


class TestRamLayerShortWindowRail(unittest.TestCase):
    def test_short_window_returns_partial(self) -> None:
        ram = RamLayer(max_keys=4, max_bars=100)
        bars = []
        for idx in range(3):
            bars.append({"open_time_ms": 1700000000000 + idx * 14400000, "close_time_ms": 1700000000000 + (idx + 1) * 14400000})
        ram.set_window("XAU/USD", 14400, bars)

        got = ram.get_window("XAU/USD", 14400, 10)
        self.assertIsNotNone(got)
        self.assertEqual(len(got or []), 3)


if __name__ == "__main__":
    unittest.main()
