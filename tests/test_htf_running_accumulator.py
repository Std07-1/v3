"""Tests for _HTFRunningAccumulator (HTF live preview from M1).

Covers: D1/H4 incremental aggregation, per-tick dedup (D-01/D-04),
bucket rollover, seed path, anchor alignment (D-03 fix), symbol isolation.
"""

from core.model.bars import CandleBar
from core.buckets import bucket_start_ms

from runtime.ingest.tick_preview_worker import _HTFRunningAccumulator, _RunningBar


def _make_m1(symbol, open_ms, o, h, low, c, v=100.0, complete=True):
    return CandleBar(
        symbol=symbol,
        tf_s=60,
        open_time_ms=open_ms,
        close_time_ms=open_ms + 60_000,
        o=o,
        h=h,
        low=low,
        c=c,
        v=v,
        complete=complete,
        src="test",
    )


# D1 anchor = 79200s (22:00 UTC), H4 anchor = 82800s (23:00 UTC)
D1_ANCHOR_MS = 79200 * 1000
H4_ANCHOR_MS = 82800 * 1000


class TestHTFRunningAccumulator:

    def _make_acc(self, tfs=None):
        tfs = tfs or [14400, 86400]
        anchors = {14400: H4_ANCHOR_MS, 86400: D1_ANCHOR_MS}
        return _HTFRunningAccumulator(tfs, anchors)

    def test_single_m1_produces_htf_previews(self):
        """1 M1 бар → 1 H4 + 1 D1 preview."""
        acc = self._make_acc()
        m1 = _make_m1("XAU/USD", 1742169600000, 2000.0, 2001.0, 1999.0, 2000.5)
        results = acc.update("XAU/USD", m1)
        assert len(results) == 2
        d1 = [r for r in results if r.tf_s == 86400][0]
        h4 = [r for r in results if r.tf_s == 14400][0]
        assert d1.o == 2000.0
        assert d1.h == 2001.0
        assert d1.low == 1999.0
        assert d1.c == 2000.5
        assert d1.complete is False
        assert d1.src == "htf_preview"
        assert h4.complete is False

    def test_incremental_merge_ohlcv(self):
        """Послідовні M1 бари коректно агрегуються."""
        acc = self._make_acc([86400])
        base_ms = 1742169600000

        acc.update("XAU/USD", _make_m1("XAU/USD", base_ms, 100, 105, 99, 103, 10))
        results = acc.update(
            "XAU/USD", _make_m1("XAU/USD", base_ms + 60000, 103, 110, 101, 108, 20)
        )

        d1 = results[0]
        assert d1.o == 100
        assert d1.h == 110
        assert d1.low == 99
        assert d1.c == 108
        assert d1.v == 30
        assert d1.extensions["m1_count"] == 2

    def test_bucket_rollover_resets_state(self):
        """При переході в новий D1 бакет — state скидається."""
        acc = self._make_acc([86400])
        bucket1_m1 = 1742169600000
        bucket2_m1 = bucket1_m1 + 86400 * 1000

        acc.update("XAU/USD", _make_m1("XAU/USD", bucket1_m1, 100, 110, 90, 105))
        results = acc.update(
            "XAU/USD", _make_m1("XAU/USD", bucket2_m1, 200, 210, 190, 205)
        )

        d1 = results[0]
        assert d1.o == 200
        assert d1.h == 210
        assert d1.extensions["m1_count"] == 1

    def test_seed_uses_update_path(self):
        """seed() = послідовний update(). Результат ідентичний."""
        acc1 = self._make_acc([86400])
        acc2 = self._make_acc([86400])
        base_ms = 1742169600000

        bars = [
            _make_m1("XAU/USD", base_ms + i * 60000, 100 + i, 105 + i, 99 + i, 103 + i)
            for i in range(5)
        ]

        acc1.seed("XAU/USD", bars)
        r1 = acc1.update(
            "XAU/USD", _make_m1("XAU/USD", base_ms + 5 * 60000, 106, 111, 105, 109)
        )

        for b in bars:
            acc2.update("XAU/USD", b)
        r2 = acc2.update(
            "XAU/USD", _make_m1("XAU/USD", base_ms + 5 * 60000, 106, 111, 105, 109)
        )

        d1_1 = [r for r in r1 if r.tf_s == 86400][0]
        d1_2 = [r for r in r2 if r.tf_s == 86400][0]
        assert d1_1.o == d1_2.o
        assert d1_1.h == d1_2.h
        assert d1_1.low == d1_2.low
        assert d1_1.c == d1_2.c
        assert d1_1.v == d1_2.v
        assert d1_1.extensions["m1_count"] == d1_2.extensions["m1_count"]

    def test_multiple_symbols_isolated(self):
        """Різні символи не інтерферують."""
        acc = self._make_acc([86400])
        base_ms = 1742169600000

        acc.update("XAU/USD", _make_m1("XAU/USD", base_ms, 2000, 2010, 1990, 2005))
        results = acc.update(
            "NAS100", _make_m1("NAS100", base_ms, 18000, 18100, 17900, 18050)
        )

        nas_d1 = results[0]
        assert nas_d1.o == 18000
        assert nas_d1.extensions["m1_count"] == 1

    def test_h4_anchor_alignment(self):
        """H4 бакет вирівнюється по anchor 82800 (23:00 UTC).

        D-03 fix: real assertion instead of `or True`.
        """
        acc = self._make_acc([14400])
        m1_ms = 1742173200000  # 2026-03-17 01:00 UTC
        results = acc.update("XAU/USD", _make_m1("XAU/USD", m1_ms, 100, 105, 99, 103))

        h4 = results[0]
        # Verify bucket matches SSOT bucket_start_ms
        expected_bucket = bucket_start_ms(m1_ms, 14400 * 1000, H4_ANCHOR_MS)
        assert h4.open_time_ms == expected_bucket
        # I2: close = open + tf_ms
        assert h4.close_time_ms == h4.open_time_ms + 14400 * 1000

    def test_d1_anchor_alignment(self):
        """D1 бакет вирівнюється по anchor 79200 (22:00 UTC)."""
        acc = self._make_acc([86400])
        m1_ms = 1742169600000  # 2026-03-17 00:00 UTC
        results = acc.update("XAU/USD", _make_m1("XAU/USD", m1_ms, 100, 105, 99, 103))

        d1 = results[0]
        expected_bucket = bucket_start_ms(m1_ms, 86400 * 1000, D1_ANCHOR_MS)
        assert d1.open_time_ms == expected_bucket
        assert d1.close_time_ms == d1.open_time_ms + 86400 * 1000

    def test_d1_only_mode(self):
        """Можна запустити тільки з D1 (без H4)."""
        acc = self._make_acc([86400])
        base_ms = 1742169600000
        results = acc.update("XAU/USD", _make_m1("XAU/USD", base_ms, 100, 105, 99, 103))
        assert len(results) == 1
        assert results[0].tf_s == 86400

    # ---------------------------------------------------------------
    # D-01 / D-04 fix: per-tick dedup tests
    # ---------------------------------------------------------------
    def test_same_m1_bar_updated_multiple_ticks(self):
        """Same M1 bar updated 5 times (simulates 5 ticks in one minute).

        D-04: regression test for D-01 (per-tick merge contract).
        m1_count MUST stay 1 (one distinct M1, not 5 ticks).
        Volume MUST NOT accumulate (v=0 for tick-preview bars).
        """
        acc = self._make_acc([86400])
        base_ms = 1742169600000

        # 5 ticks within the same M1 bucket, rising close
        for i in range(5):
            acc.update(
                "XAU/USD",
                _make_m1(
                    "XAU/USD", base_ms, 100, 100 + i, 100 - i, 100 + i * 0.5, v=0.0
                ),
            )

        # Get current state via one more update with new M1
        results = acc.update(
            "XAU/USD",
            _make_m1("XAU/USD", base_ms + 60000, 102, 103, 101, 102.5, v=0.0),
        )
        d1 = results[0]
        # After 5 ticks for first M1 + 1 new M1 = 2 distinct M1 bars
        assert d1.extensions["m1_count"] == 2

    def test_same_m1_dedup_preserves_ohlc(self):
        """Per-tick updates for same M1 correctly update h/low/c."""
        acc = self._make_acc([86400])
        base_ms = 1742169600000

        # Tick 1: initial
        acc.update("XAU/USD", _make_m1("XAU/USD", base_ms, 100, 102, 98, 101, v=0.0))
        # Tick 2: higher high, same low
        acc.update("XAU/USD", _make_m1("XAU/USD", base_ms, 100, 105, 98, 104, v=0.0))
        # Tick 3: lower low
        results = acc.update(
            "XAU/USD", _make_m1("XAU/USD", base_ms, 100, 105, 96, 99, v=0.0)
        )

        d1 = results[0]
        assert d1.o == 100  # unchanged
        assert d1.h == 105  # max across ticks
        assert d1.low == 96  # min across ticks
        assert d1.c == 99  # latest close
        assert d1.extensions["m1_count"] == 1  # only 1 distinct M1
        assert d1.v == 0.0  # no volume accumulation

    def test_volume_not_inflated_by_ticks(self):
        """Volume must not accumulate when same M1 is fed multiple times.

        Even if v != 0 in the future, m1_count guards correctness.
        """
        acc = self._make_acc([86400])
        base_ms = 1742169600000

        # Simulate tick-preview bars with v=10 (hypothetical future)
        for _ in range(10):
            acc.update(
                "XAU/USD",
                _make_m1("XAU/USD", base_ms, 100, 105, 99, 103, v=10.0),
            )

        results = acc.update(
            "XAU/USD",
            _make_m1("XAU/USD", base_ms + 60000, 103, 108, 101, 106, v=10.0),
        )
        d1 = results[0]
        # 10 ticks for M1#1 (10.0 once) + 1 new M1#2 (10.0) = 20.0 total
        assert d1.v == 20.0
        assert d1.extensions["m1_count"] == 2


class TestRunningBar:

    def test_merge_updates_hlcv(self):
        """merge() оновлює h, low, c, v, count."""
        m1 = _make_m1("X", 0, 100, 110, 90, 105, 10)
        rb = _RunningBar(0, 86400, m1)
        assert rb.count == 1

        m2 = _make_m1("X", 60000, 105, 115, 88, 112, 20)
        rb.merge(m2)
        assert rb.o == 100
        assert rb.h == 115
        assert rb.low == 88
        assert rb.c == 112
        assert rb.v == 30
        assert rb.count == 2

    def test_merge_no_change_when_inside(self):
        """merge() з баром всередині діапазону — h/low не змінюються."""
        m1 = _make_m1("X", 0, 100, 110, 90, 105, 10)
        rb = _RunningBar(0, 86400, m1)

        m2 = _make_m1("X", 60000, 102, 108, 92, 104, 5)
        rb.merge(m2)
        assert rb.h == 110
        assert rb.low == 90

    def test_update_forming_no_count_change(self):
        """update_forming() does not increment count or add volume."""
        m1 = _make_m1("X", 0, 100, 110, 90, 105, 10)
        rb = _RunningBar(0, 86400, m1)

        m2 = _make_m1("X", 0, 100, 115, 88, 112, 20)
        rb.update_forming(m2)
        assert rb.h == 115
        assert rb.low == 88
        assert rb.c == 112
        assert rb.v == 10  # unchanged
        assert rb.count == 1  # unchanged

    def test_to_candle_geometry(self):
        """to_candle() produces correct CandleBar with I2 compliant close_time_ms."""
        m1 = _make_m1("XAU/USD", 0, 100, 110, 90, 105, 10)
        rb = _RunningBar(1742169600000, 86400, m1)
        candle = rb.to_candle("XAU/USD")
        assert candle.tf_s == 86400
        assert candle.close_time_ms == candle.open_time_ms + 86400 * 1000
        assert candle.complete is False
        assert candle.src == "htf_preview"
