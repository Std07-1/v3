"""Tests for M1→HTF (D1) preview derivation in tick_preview_worker.

Verifies that:
1. _M1toHTFBuffer correctly merges finalized M1 bars + current M1 preview → D1 bar
2. TickPreviewWorker derives D1 from M1 (not from ticks directly)
3. D1 preview bar is published through normal UDS pipeline
4. Cache refresh works correctly
"""

from __future__ import annotations

import datetime as dt
import unittest
from unittest.mock import MagicMock, patch

from core.model.bars import CandleBar
from runtime.ingest.tick_preview_worker import (
    TickPreviewWorker,
    _M1toHTFBuffer,
)


# ── helpers ──

D1_ANCHOR_OFFSET_MS = 79200 * 1000  # 22:00 UTC (config: day_anchor_offset_s_d1)

def _ts_ms(year, month, day, hour, minute):
    """UTC datetime → epoch ms."""
    d = dt.datetime(year, month, day, hour, minute, tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


def _make_m1_bar(symbol, open_time_ms, o=100.0, h=101.0, low=99.0, c=100.5, v=10.0, complete=True):
    return CandleBar(
        symbol=symbol,
        tf_s=60,
        open_time_ms=open_time_ms,
        close_time_ms=open_time_ms + 60_000,
        o=o, h=h, low=low, c=c, v=v,
        complete=complete,
        src="history" if complete else "preview_tick",
    )


def _make_tick(symbol, tick_ts_ms, mid=100.0):
    return {
        "v": 1,
        "symbol": symbol,
        "tick_ts_ms": tick_ts_ms,
        "mid": mid,
        "src": "test",
        "seq": 1,
    }


def _make_mock_uds(m1_bars=None):
    """Create a mock UDS that returns given M1 bars from read_tail_candles."""
    uds = MagicMock()
    uds.publish_preview_bar = MagicMock()
    uds.publish_promoted_bar = MagicMock(return_value=True)
    uds.read_tail_candles = MagicMock(return_value=m1_bars or [])
    return uds


# ── Tests for _M1toHTFBuffer ──

class TestM1toHTFBuffer(unittest.TestCase):
    """Tests for the _M1toHTFBuffer class."""

    def test_basic_merge_m1_with_current_preview(self):
        """Buffer merges finalized M1 bars + current M1 preview into D1 bar."""
        # D1 bucket at 2026-03-16 22:00 UTC (anchor=79200s=22:00)
        d1_open = _ts_ms(2026, 3, 16, 22, 0)

        # 3 finalized M1 bars in the D1 bucket
        m1_bars = [
            _make_m1_bar("XAU/USD", d1_open, o=2000.0, h=2005.0, low=1995.0, c=2003.0, v=100.0),
            _make_m1_bar("XAU/USD", d1_open + 60_000, o=2003.0, h=2010.0, low=2001.0, c=2008.0, v=150.0),
            _make_m1_bar("XAU/USD", d1_open + 120_000, o=2008.0, h=2012.0, low=2006.0, c=2010.0, v=200.0),
        ]
        uds = _make_mock_uds(m1_bars)
        buf = _M1toHTFBuffer(uds=uds, target_tf_s=86400, anchor_offset_ms=D1_ANCHOR_OFFSET_MS)

        # Current M1 preview (4th minute)
        current_m1 = _make_m1_bar(
            "XAU/USD", d1_open + 180_000,
            o=2010.0, h=2015.0, low=2009.0, c=2014.0, v=0.0, complete=False,
        )
        d1_bar = buf.update("XAU/USD", current_m1)

        self.assertIsNotNone(d1_bar)
        self.assertEqual(d1_bar.tf_s, 86400)
        self.assertEqual(d1_bar.open_time_ms, d1_open)
        self.assertEqual(d1_bar.o, 2000.0)       # from first M1
        self.assertEqual(d1_bar.h, 2015.0)       # max across all
        self.assertEqual(d1_bar.low, 1995.0)     # min across all
        self.assertEqual(d1_bar.c, 2014.0)       # from current M1 (last)
        self.assertEqual(d1_bar.v, 450.0)        # sum of volumes
        self.assertFalse(d1_bar.complete)
        self.assertEqual(d1_bar.src, "derived_m1")
        self.assertEqual(d1_bar.extensions.get("m1_count"), 4)

    def test_no_finalized_m1_only_preview(self):
        """When no finalized M1 bars, D1 is built from current M1 preview only."""
        d1_open = _ts_ms(2026, 3, 16, 22, 0)
        uds = _make_mock_uds([])  # No finalized M1
        buf = _M1toHTFBuffer(uds=uds, target_tf_s=86400, anchor_offset_ms=D1_ANCHOR_OFFSET_MS)

        current_m1 = _make_m1_bar(
            "XAU/USD", d1_open,
            o=2000.0, h=2001.0, low=1999.0, c=2000.5, v=0.0, complete=False,
        )
        d1_bar = buf.update("XAU/USD", current_m1)

        self.assertIsNotNone(d1_bar)
        self.assertEqual(d1_bar.o, 2000.0)
        self.assertEqual(d1_bar.h, 2001.0)
        self.assertEqual(d1_bar.low, 1999.0)
        self.assertEqual(d1_bar.c, 2000.5)
        self.assertEqual(d1_bar.extensions.get("m1_count"), 1)

    def test_current_m1_replaces_same_open(self):
        """Current M1 preview replaces finalized M1 with the same open_time_ms."""
        d1_open = _ts_ms(2026, 3, 16, 22, 0)

        # Finalized M1 at d1_open
        finalized = _make_m1_bar("XAU/USD", d1_open, o=2000.0, h=2005.0, low=1995.0, c=2003.0, v=100.0)
        uds = _make_mock_uds([finalized])
        buf = _M1toHTFBuffer(uds=uds, target_tf_s=86400, anchor_offset_ms=D1_ANCHOR_OFFSET_MS)

        # Current M1 preview with SAME open_time_ms (updates to latest tick data)
        current_m1 = _make_m1_bar(
            "XAU/USD", d1_open,
            o=2000.0, h=2007.0, low=1995.0, c=2006.0, v=0.0, complete=False,
        )
        d1_bar = buf.update("XAU/USD", current_m1)

        self.assertIsNotNone(d1_bar)
        # Should use the current M1 preview, not the finalized one
        self.assertEqual(d1_bar.h, 2007.0)  # from preview
        self.assertEqual(d1_bar.c, 2006.0)  # from preview
        self.assertEqual(d1_bar.extensions.get("m1_count"), 1)

    def test_cache_refresh(self):
        """Cache refreshes after _CACHE_REFRESH_S seconds."""
        d1_open = _ts_ms(2026, 3, 16, 22, 0)
        uds = _make_mock_uds([])
        buf = _M1toHTFBuffer(uds=uds, target_tf_s=86400, anchor_offset_ms=D1_ANCHOR_OFFSET_MS)

        current_m1 = _make_m1_bar("XAU/USD", d1_open, complete=False)

        # First call: loads from UDS
        buf.update("XAU/USD", current_m1)
        self.assertEqual(uds.read_tail_candles.call_count, 1)

        # Second call within refresh window: uses cache
        buf.update("XAU/USD", current_m1)
        self.assertEqual(uds.read_tail_candles.call_count, 1)

        # Simulate cache expiry
        buf._cache["XAU/USD"] = (buf._cache["XAU/USD"][0], buf._cache["XAU/USD"][1], 0)
        buf.update("XAU/USD", current_m1)
        self.assertEqual(uds.read_tail_candles.call_count, 2)

    def test_filters_m1_outside_bucket(self):
        """M1 bars outside the current D1 bucket are filtered out."""
        d1_open = _ts_ms(2026, 3, 16, 22, 0)

        # M1 bar from previous D1 bucket
        old_m1 = _make_m1_bar("XAU/USD", d1_open - 60_000, o=1990.0, h=1995.0, low=1985.0, c=1993.0)
        # M1 bar from current D1 bucket
        current_bucket_m1 = _make_m1_bar("XAU/USD", d1_open, o=2000.0, h=2005.0, low=1995.0, c=2003.0)
        uds = _make_mock_uds([old_m1, current_bucket_m1])
        buf = _M1toHTFBuffer(uds=uds, target_tf_s=86400, anchor_offset_ms=D1_ANCHOR_OFFSET_MS)

        current_m1 = _make_m1_bar("XAU/USD", d1_open + 60_000, complete=False, o=2003.0, h=2008.0, low=2001.0, c=2007.0)
        d1_bar = buf.update("XAU/USD", current_m1)

        self.assertIsNotNone(d1_bar)
        # Should NOT include old_m1 (outside bucket)
        self.assertEqual(d1_bar.o, 2000.0)  # from current_bucket_m1
        self.assertEqual(d1_bar.extensions.get("m1_count"), 2)

    def test_uds_read_failure_graceful(self):
        """UDS read failure is handled gracefully (returns bar from preview only)."""
        d1_open = _ts_ms(2026, 3, 16, 22, 0)
        uds = MagicMock()
        uds.read_tail_candles = MagicMock(side_effect=RuntimeError("disk error"))
        buf = _M1toHTFBuffer(uds=uds, target_tf_s=86400, anchor_offset_ms=D1_ANCHOR_OFFSET_MS)

        current_m1 = _make_m1_bar("XAU/USD", d1_open, complete=False)
        d1_bar = buf.update("XAU/USD", current_m1)

        self.assertIsNotNone(d1_bar)
        self.assertEqual(d1_bar.extensions.get("m1_count"), 1)


# ── Tests for TickPreviewWorker D1 derivation ──

class TestWorkerD1Derivation(unittest.TestCase):
    """Tests that TickPreviewWorker derives D1 from M1 instead of ticks."""

    def _make_worker(self, m1_bars=None, tfs=None):
        uds = _make_mock_uds(m1_bars or [])
        if tfs is None:
            tfs = [60, 180, 86400]
        worker = TickPreviewWorker(
            uds=uds,
            tfs=tfs,
            publish_min_interval_ms=0,
            curr_ttl_s=1800,
            symbols=["XAU/USD"],
            channel="test:chan",
            d1_anchor_offset_ms=D1_ANCHOR_OFFSET_MS,
        )
        return worker, uds

    def test_derive_d1_flag_enabled(self):
        """When both M1 and D1 in tfs, D1 derivation is enabled."""
        worker, _ = self._make_worker(tfs=[60, 180, 86400])
        self.assertTrue(worker._derive_d1)
        self.assertIsNotNone(worker._d1_buffer)

    def test_derive_d1_flag_disabled_no_d1(self):
        """When D1 not in tfs, D1 derivation is disabled."""
        worker, _ = self._make_worker(tfs=[60, 180])
        self.assertFalse(worker._derive_d1)
        self.assertIsNone(worker._d1_buffer)

    def test_derive_d1_flag_disabled_no_m1(self):
        """When M1 not in tfs, D1 derivation is disabled."""
        worker, _ = self._make_worker(tfs=[86400])
        self.assertFalse(worker._derive_d1)
        self.assertIsNone(worker._d1_buffer)

    def test_d1_not_in_tick_aggregator(self):
        """D1 should be excluded from TickAggregator when derived from M1."""
        worker, _ = self._make_worker(tfs=[60, 180, 86400])
        self.assertNotIn(86400, worker._agg._tf_allowlist)

    def test_d1_preview_published_on_tick(self):
        """D1 preview bar is published when ticks arrive (via M1→D1 derivation)."""
        # D1 bucket at 2026-03-16 22:00 UTC
        d1_open = _ts_ms(2026, 3, 16, 22, 0)

        # Finalized M1 bars on disk
        m1_bars = [
            _make_m1_bar("XAU/USD", d1_open, o=2000.0, h=2005.0, low=1995.0, c=2003.0, v=100.0),
            _make_m1_bar("XAU/USD", d1_open + 60_000, o=2003.0, h=2010.0, low=2001.0, c=2008.0, v=150.0),
        ]
        worker, uds = self._make_worker(m1_bars=m1_bars)

        # Send tick at minute 3 of the D1 bucket
        tick_ts = d1_open + 120_000 + 5000  # 3rd minute, 5s in
        tick = _make_tick("XAU/USD", tick_ts, mid=2012.0)
        worker.on_tick(tick)

        # Check that publish_preview_bar was called with D1 bar
        d1_calls = [
            call for call in uds.publish_preview_bar.call_args_list
            if call.args[0].tf_s == 86400
        ]
        self.assertGreater(len(d1_calls), 0, "D1 preview bar should be published")
        d1_bar = d1_calls[0].args[0]
        self.assertEqual(d1_bar.tf_s, 86400)
        self.assertEqual(d1_bar.open_time_ms, d1_open)
        self.assertEqual(d1_bar.o, 2000.0)     # from finalized M1
        self.assertGreaterEqual(d1_bar.h, 2010.0)  # at least from finalized M1
        self.assertFalse(d1_bar.complete)
        self.assertEqual(d1_bar.src, "derived_m1")

    def test_d1_volume_from_finalized_m1(self):
        """D1 preview bar includes volume from finalized M1 bars."""
        d1_open = _ts_ms(2026, 3, 16, 22, 0)
        m1_bars = [
            _make_m1_bar("XAU/USD", d1_open, v=100.0),
            _make_m1_bar("XAU/USD", d1_open + 60_000, v=200.0),
        ]
        worker, uds = self._make_worker(m1_bars=m1_bars)

        tick_ts = d1_open + 120_000 + 5000
        worker.on_tick(_make_tick("XAU/USD", tick_ts, mid=101.0))

        d1_calls = [
            call for call in uds.publish_preview_bar.call_args_list
            if call.args[0].tf_s == 86400
        ]
        self.assertGreater(len(d1_calls), 0)
        d1_bar = d1_calls[0].args[0]
        self.assertGreaterEqual(d1_bar.v, 300.0)  # 100 + 200 + tick's 0


if __name__ == "__main__":
    unittest.main()
