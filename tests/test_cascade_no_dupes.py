"""tests/test_cascade_no_dupes.py

Regression тест: cascade catchup НЕ повинен створювати
дублікати в JSONL при повторних рестартах.

Root cause (2026-03-15): reset_watermark(0) перед cascade
catchup дозволяв commit вже існуючих derived барів знову.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from core.model.bars import CandleBar
from runtime.store.ssot_jsonl import JsonlAppender


def _bar(sym: str, tf_s: int, open_ms: int, src: str = "derived") -> CandleBar:
    return CandleBar(
        symbol=sym,
        tf_s=tf_s,
        open_time_ms=open_ms,
        close_time_ms=open_ms + tf_s * 1000,
        o=100.0,
        h=101.0,
        low=99.0,
        c=100.5,
        v=10.0,
        complete=True,
        src=src,
    )


class TestCascadeNoDupes(unittest.TestCase):
    """Перевіряє що JsonlAppender + UDS watermark не пишуть дублі."""

    def test_jsonl_appender_writes_duplicates_without_guard(self):
        """JsonlAppender.append() — append-only, без dedup.

        Це документує ПОВЕДІНКУ: JsonlAppender не фільтрує.
        Захист від дублів — на рівні UDS watermark.
        """
        with tempfile.TemporaryDirectory() as tmp:
            app = JsonlAppender(tmp)
            bar = _bar("XAU/USD", 300, 1773612000000)
            app.append(bar)
            app.append(bar)  # duplicate
            app.close()

            fpath = os.path.join(tmp, "XAU_USD", "tf_300")
            files = os.listdir(fpath)
            self.assertEqual(len(files), 1)
            with open(os.path.join(fpath, files[0])) as fh:
                lines = [ln.strip() for ln in fh if ln.strip()]
            # JsonlAppender пише обидва рази — dedup на рівні UDS
            self.assertEqual(len(lines), 2)

    def test_uds_watermark_blocks_stale_and_duplicate(self):
        """UDS watermark блокує re-commit вже існуючих барів."""
        from runtime.store.uds import _watermark_drop_reason

        # None watermark = first run, all pass
        self.assertIsNone(_watermark_drop_reason(1000, None))

        # Exact duplicate
        self.assertEqual(_watermark_drop_reason(1000, 1000), "duplicate")

        # Stale (older than watermark)
        self.assertEqual(_watermark_drop_reason(500, 1000), "stale")

        # New bar (after watermark)
        self.assertIsNone(_watermark_drop_reason(2000, 1000))

    def test_watermark_init_from_disk_prevents_cascade_dupes(self):
        """Simulates: bars on disk → watermark init → cascade commit → no dupes.

        This is the exact scenario that was broken by reset_watermark(0).
        """
        from runtime.store.uds import _watermark_drop_reason

        # Simulate: disk has bars up to open_ms=5000
        disk_last_open_ms = 5000

        # Watermark initialized from disk
        wm = disk_last_open_ms

        # Cascade tries to commit bars 1000, 2000, 3000, 4000, 5000, 6000
        results = []
        for open_ms in [1000, 2000, 3000, 4000, 5000, 6000]:
            reason = _watermark_drop_reason(open_ms, wm)
            if reason is None:
                wm = open_ms  # advance watermark on commit
            results.append((open_ms, reason))

        # 1000-4000: stale, 5000: duplicate, 6000: new → committed
        self.assertEqual(results, [
            (1000, "stale"),
            (2000, "stale"),
            (3000, "stale"),
            (4000, "stale"),
            (5000, "duplicate"),
            (6000, None),       # only genuinely new bar passes
        ])


if __name__ == "__main__":
    unittest.main()
