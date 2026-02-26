from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.model.bars import CandleBar
from runtime.store.uds import UnifiedDataStore


class _MetricCounterStub:
    def __init__(self) -> None:
        self.count = 0

    def inc(self, value: float = 1.0) -> None:
        self.count += int(value)


class _MetricGaugeStub:
    def __init__(self) -> None:
        self.values: list[int] = []

    def set(self, value: float) -> None:
        self.values.append(int(value))


class _DiskStub:
    def last_open_ms(self, symbol: str, tf_s: int):
        _ = symbol, tf_s
        return None


class TestUDSCommitSplitBrain(unittest.TestCase):
    def _bar(self) -> CandleBar:
        return CandleBar(
            symbol="XAU_USD",
            tf_s=60,
            open_time_ms=1740000000000,
            close_time_ms=1740000060000,
            o=2850.0,
            h=2855.0,
            low=2848.0,
            c=2853.0,
            v=10.0,
            complete=True,
            src="history",
        )

    @patch.dict(os.environ, {"UDS_COMMIT_DEGRADED_REASON_FF": "1"}, clear=False)
    def test_disk_ok_redis_pubsub_fail_commit_ok_and_split_brain_marked(self) -> None:
        jsonl = Mock()
        redis_writer = Mock()
        redis_writer.put_bar.side_effect = RuntimeError("redis down")
        updates_bus = Mock()
        updates_bus.publish.side_effect = RuntimeError("pubsub down")

        redis_fail = _MetricCounterStub()
        pubsub_fail = _MetricCounterStub()
        split_brain = _MetricGaugeStub()

        with patch("runtime.store.uds._METRIC_REDIS_WRITE_FAIL_TOTAL", redis_fail), \
             patch("runtime.store.uds._METRIC_PUBSUB_FAIL_TOTAL", pubsub_fail), \
             patch("runtime.store.uds._METRIC_SPLIT_BRAIN_ACTIVE", split_brain), \
             self.assertLogs("uds", level="WARNING") as logs:
            uds = UnifiedDataStore(
                data_root="./data_v3",
                boot_id="test-boot",
                tf_allowlist={60},
                min_coldload_bars={60: 10},
                role="writer",
                disk_layer=_DiskStub(),
                jsonl_appender=jsonl,
                redis_snapshot_writer=redis_writer,
                updates_bus=updates_bus,
            )
            bar = self._bar()
            result = uds.commit_final_bar(bar)

        self.assertTrue(result.ok)
        self.assertTrue(result.ssot_written)
        self.assertFalse(result.redis_written)
        self.assertFalse(result.updates_published)
        self.assertIn("degraded_reason:redis_write_failed,pubsub_failed", result.warnings)
        self.assertEqual(uds.get_watermark_open_ms(bar.symbol, bar.tf_s), bar.open_time_ms)

        self.assertEqual(redis_fail.count, 1)
        self.assertEqual(pubsub_fail.count, 1)
        self.assertIn(1, split_brain.values)

        logs_joined = "\n".join(logs.output)
        self.assertIn("[DEGRADED] [XAU_USD] [UDS]", logs_joined)
        self.assertIn("head:3", logs_joined)
        self.assertIn("tail:3", logs_joined)


if __name__ == "__main__":
    unittest.main()
