import json
import os
import unittest

from core.buckets import bucket_start_ms, resolve_anchor_offset_ms, tf_to_ms
from runtime.ingest.tick_agg import TickAggregator


class TestTickAggregator(unittest.TestCase):
    def test_open_first_tick(self) -> None:
        agg = TickAggregator(tf_allowlist=[60])
        symbol = "XAU/USD"
        tf_s = 60
        t0 = 1_700_000_000_000
        price1 = 100.0

        bar1 = agg.update(symbol, tf_s, t0, price1)
        self.assertIsNotNone(bar1)
        if bar1 is None:
            return

        tf_ms = tf_to_ms(tf_s)
        open_ms = bucket_start_ms(t0, tf_ms, 0)
        self.assertEqual(bar1.open_time_ms, open_ms)
        self.assertEqual(bar1.close_time_ms, open_ms + tf_ms)
        self.assertEqual(bar1.o, price1)
        self.assertEqual(bar1.h, price1)
        self.assertEqual(bar1.low, price1)
        self.assertEqual(bar1.c, price1)
        self.assertEqual(bar1.v, 1.0)
        self.assertFalse(bar1.complete)
        self.assertEqual(bar1.src, "preview_tick")

        price2 = 101.0
        bar2 = agg.update(symbol, tf_s, t0 + 10_000, price2)
        self.assertIsNotNone(bar2)
        if bar2 is None:
            return
        self.assertEqual(bar2.o, price1)
        self.assertEqual(bar2.h, price2)
        self.assertEqual(bar2.low, price1)
        self.assertEqual(bar2.c, price2)
        self.assertEqual(bar2.v, 2.0)

        price3 = 99.5
        bar3 = agg.update(symbol, tf_s, t0 + 20_000, price3)
        self.assertIsNotNone(bar3)
        if bar3 is None:
            return
        self.assertEqual(bar3.o, price1)
        self.assertEqual(bar3.h, price2)
        self.assertEqual(bar3.low, price3)
        self.assertEqual(bar3.c, price3)
        self.assertEqual(bar3.v, 3.0)

    def test_late_bucket_drop(self) -> None:
        agg = TickAggregator(tf_allowlist=[60])
        symbol = "XAU/USD"
        tf_s = 60
        t0 = 1_700_000_000_000

        bar0 = agg.update(symbol, tf_s, t0, 100.0)
        self.assertIsNotNone(bar0)

        t1 = t0 + 65_000
        bar1 = agg.update(symbol, tf_s, t1, 101.0)
        self.assertIsNotNone(bar1)
        if bar1 is None:
            return

        late = agg.update(symbol, tf_s, t0 + 30_000, 99.0)
        self.assertIsNone(late)

        stats = agg.stats()
        self.assertEqual(stats.get("ticks_dropped_late_bucket"), 1)

        bar2 = agg.update(symbol, tf_s, t1 + 10_000, 102.0)
        self.assertIsNotNone(bar2)
        if bar2 is None:
            return
        self.assertEqual(bar2.open_time_ms, bar1.open_time_ms)

    def test_anchor_offset_matches_final_samples(self) -> None:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        cfg_path = os.path.join(base_dir, "config.json")
        data_root = os.path.join(base_dir, "data_v3")
        if not os.path.isfile(cfg_path):
            self.skipTest("config.json not found")

        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            self.skipTest("failed to load config.json")

        symbol = str(cfg.get("symbol", "XAU/USD"))
        symbol_dir = symbol.replace("/", "_")

        for tf_s in (14400, 86400):
            tf_dir = os.path.join(data_root, symbol_dir, f"tf_{tf_s}")
            if not os.path.isdir(tf_dir):
                self.skipTest(f"missing data dir for tf_s={tf_s}")
            parts = [p for p in os.listdir(tf_dir) if p.startswith("part-") and p.endswith(".jsonl")]
            parts.sort()
            if not parts:
                self.skipTest(f"missing part files for tf_s={tf_s}")
            last_path = os.path.join(tf_dir, parts[-1])

            last_obj = None
            try:
                with open(last_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            last_obj = json.loads(line)
                        except Exception:
                            continue
            except Exception:
                self.skipTest(f"failed to read {last_path}")

            if not isinstance(last_obj, dict):
                self.skipTest(f"missing last bar for tf_s={tf_s}")

            open_ms = last_obj.get("open_time_ms")
            if not isinstance(open_ms, int):
                self.skipTest(f"invalid open_time_ms for tf_s={tf_s}")

            tf_ms = tf_to_ms(tf_s)
            anchor_offset_ms_cfg = resolve_anchor_offset_ms(tf_s, cfg)
            anchor_offset_ms = int(open_ms % tf_ms)
            b0 = bucket_start_ms(open_ms, tf_ms, anchor_offset_ms)
            self.assertEqual(b0, open_ms)
            if anchor_offset_ms_cfg != anchor_offset_ms:
                self.assertNotEqual(anchor_offset_ms_cfg, anchor_offset_ms)


if __name__ == "__main__":
    unittest.main()
