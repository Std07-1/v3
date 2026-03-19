"""Tests for core.smc.tda.stage4_fvg_entry — M15 FVG Entry detection."""

import unittest

from core.model.bars import CandleBar
from core.smc.tda.types import TdaCascadeConfig
from core.smc.tda.stage4_fvg_entry import find_fvg_entry

_DAY_MS = 1_704_067_200_000  # 2024-01-01 00:00 UTC
_M15_S = 900
_M15_MS = _M15_S * 1000
_HOUR_MS = 3_600_000
_CFG = TdaCascadeConfig()

# Search window: 09:00–16:00 UTC
_SEARCH_START = _DAY_MS + 9 * _HOUR_MS
_SEARCH_END = _DAY_MS + 16 * _HOUR_MS

# Sweep price anchor (Asia level that was swept)
_SWEEP_PRICE = 2060.0


def _m15(open_ms: int, o: float, h: float, low: float, c: float) -> CandleBar:
    return CandleBar(
        symbol="XAU/USD",
        tf_s=_M15_S,
        open_time_ms=open_ms,
        close_time_ms=open_ms + _M15_MS,
        o=o,
        h=h,
        low=low,
        c=c,
        v=50.0,
        complete=True,
        src="test",
    )


def _window_bars(
    n: int, base: float = 2055.0, spacing: int = _M15_MS
) -> list[CandleBar]:
    """N generic M15 bars in search window, no FVG gaps."""
    bars = []
    for i in range(n):
        open_ms = _SEARCH_START + i * spacing
        bars.append(_m15(open_ms, base, base + 3, base - 3, base + 1))
    return bars


def _bull_fvg_bars(
    gap_size: float = 5.0,
    base: float = 2055.0,
) -> list[CandleBar]:
    """3 bars forming a bullish FVG in the search window.

    Bull FVG: b0.h < b2.low (gap up).
    b1 is bullish impulse (c > o).
    gap = b2.low - b0.h = gap_size.
    """
    t0 = _SEARCH_START
    b0 = _m15(t0, base, base + 3, base - 2, base + 1)  # h = base+3
    b1 = _m15(t0 + _M15_MS, base + 2, base + 15, base, base + 12)  # bullish impulse
    # b2.low = b0.h + gap_size = base + 3 + gap_size
    b2_low = base + 3 + gap_size
    # b2 closes AT fvg_high (= b2_low) → won't trigger entry (need strict >)
    b2 = _m15(t0 + 2 * _M15_MS, b2_low + 1, b2_low + 4, b2_low, b2_low)
    return [b0, b1, b2]


def _bear_fvg_bars(
    gap_size: float = 5.0,
    base: float = 2060.0,
) -> list[CandleBar]:
    """3 bars forming a bearish FVG in the search window.

    Bear FVG: b0.low > b2.h (gap down).
    b1 is bearish impulse (c < o).
    gap = b0.low - b2.h = gap_size.
    """
    t0 = _SEARCH_START
    b0 = _m15(t0, base, base + 2, base - 3, base - 1)  # low = base-3
    b1 = _m15(t0 + _M15_MS, base - 2, base, base - 15, base - 12)  # bearish impulse
    # b2.h = b0.low - gap_size = base - 3 - gap_size
    b2_h = base - 3 - gap_size
    # b2 closes AT fvg_low (= b2_h) → won't trigger entry (need strict <)
    b2 = _m15(t0 + 2 * _M15_MS, b2_h - 2, b2_h, b2_h - 4, b2_h)
    return [b0, b1, b2]


# ═══════════════════════════════════════════════════════
# Gate tests
# ═══════════════════════════════════════════════════════


class TestStage4Gate(unittest.TestCase):
    """Tests for insufficient data / rejection gates."""

    def test_empty_bars(self):
        result = find_fvg_entry([], "BULL", _SWEEP_PRICE, _DAY_MS, _CFG)
        assert result is None

    def test_fewer_than_3_bars(self):
        bars = _window_bars(2)
        result = find_fvg_entry(bars, "BULL", _SWEEP_PRICE, _DAY_MS, _CFG)
        assert result is None

    def test_no_fvg_in_flat_bars(self):
        bars = _window_bars(10)
        result = find_fvg_entry(bars, "BULL", _SWEEP_PRICE, _DAY_MS, _CFG)
        assert result is None

    def test_fvg_too_small(self):
        """FVG gap < fvg_min_abs_pts (1.0) → rejected."""
        bars = _bull_fvg_bars(gap_size=0.5)
        result = find_fvg_entry(bars, "BULL", _SWEEP_PRICE, _DAY_MS, _CFG)
        assert result is None

    def test_fvg_too_far_from_sweep(self):
        """FVG proximity > 200 pts from sweep_price → rejected."""
        # Base 2055, FVG mid ≈ 2060.5, sweep_price = 2300 (far away)
        bars = _bull_fvg_bars(gap_size=5.0)
        result = find_fvg_entry(bars, "BULL", 2300.0, _DAY_MS, _CFG)
        assert result is None

    def test_bars_outside_window(self):
        """M15 bars outside 09:00–16:00 → not considered."""
        # Create bars at 05:00 UTC (before search window)
        early = _DAY_MS + 5 * _HOUR_MS
        bars = [
            _m15(early, 2055, 2058, 2053, 2056),
            _m15(early + _M15_MS, 2056, 2070, 2054, 2068),
            _m15(early + 2 * _M15_MS, 2065, 2069, 2063, 2067),
        ]
        result = find_fvg_entry(bars, "BULL", _SWEEP_PRICE, _DAY_MS, _CFG)
        assert result is None


# ═══════════════════════════════════════════════════════
# Bull FVG tests
# ═══════════════════════════════════════════════════════


class TestStage4BullFvg(unittest.TestCase):
    """Bull FVG detection + LONG entry."""

    def test_bull_fvg_entry(self):
        """Bullish FVG → entry bar touches zone + closes above → LONG."""
        fvg_bars = _bull_fvg_bars(gap_size=5.0, base=2055.0)
        fvg_high = fvg_bars[2].low  # b2.low = 2055 + 3 + 5 = 2063
        fvg_low = fvg_bars[0].h  # b0.h = 2055 + 3 = 2058

        # Entry bar: touches fvg zone (low ≤ fvg_high) and closes above (c > fvg_high)
        entry_ms = fvg_bars[2].open_time_ms + _M15_MS
        entry_bar = _m15(entry_ms, 2062.0, 2066.0, 2061.0, 2064.0)
        # low=2061 ≤ fvg_high=2063 (touched), c=2064 > fvg_high=2063 (closed outside)

        all_bars = fvg_bars + [entry_bar]
        result = find_fvg_entry(all_bars, "BULL", _SWEEP_PRICE, _DAY_MS, _CFG)

        assert result is not None
        assert result.direction == "LONG"
        assert result.entry_price == 2064.0
        assert result.fvg_high == fvg_high
        assert result.fvg_low == fvg_low
        assert result.fvg_size == fvg_high - fvg_low
        # SL = fvg_low - fvg_size * 0.5 = 2058 - 5*0.5 = 2055.5
        assert result.stop_loss == 2058.0 - 5.0 * 0.5
        assert result.risk_reward == 3.0
        assert result.entry_bar_ms == entry_ms

    def test_bull_fvg_no_touch(self):
        """Bar doesn't touch FVG zone → no entry."""
        fvg_bars = _bull_fvg_bars(gap_size=5.0, base=2055.0)
        # fvg_high = fvg_bars[2].low = 2063

        # Bar completely above FVG (low > fvg_high, never touches zone)
        entry_ms = fvg_bars[2].open_time_ms + _M15_MS
        entry_bar = _m15(entry_ms, 2065.0, 2068.0, 2064.0, 2066.0)
        # low=2064 > fvg_high=2063 → NOT touched

        all_bars = fvg_bars + [entry_bar]
        result = find_fvg_entry(all_bars, "BULL", _SWEEP_PRICE, _DAY_MS, _CFG)
        assert result is None

    def test_bull_wrong_impulse(self):
        """FVG candle is bearish (c < o) → not a valid bull FVG."""
        t0 = _SEARCH_START
        b0 = _m15(t0, 2055, 2058, 2053, 2056)
        # b1: bearish impulse (c < o) — invalid for bull FVG
        b1 = _m15(t0 + _M15_MS, 2060, 2065, 2055, 2054)
        b2_low = 2058 + 5  # gap = 5
        b2 = _m15(t0 + 2 * _M15_MS, b2_low + 1, b2_low + 4, b2_low, b2_low + 2)
        result = find_fvg_entry([b0, b1, b2], "BULL", _SWEEP_PRICE, _DAY_MS, _CFG)
        assert result is None


# ═══════════════════════════════════════════════════════
# Bear FVG tests
# ═══════════════════════════════════════════════════════


class TestStage4BearFvg(unittest.TestCase):
    """Bear FVG detection + SHORT entry."""

    def test_bear_fvg_entry(self):
        """Bearish FVG → entry bar touches zone + closes below → SHORT."""
        fvg_bars = _bear_fvg_bars(gap_size=5.0, base=2060.0)
        fvg_high = fvg_bars[0].low  # b0.low = 2060 - 3 = 2057
        fvg_low = fvg_bars[2].h  # b2.h = 2057 - 5 = 2052

        # Entry bar: touches fvg zone (h ≥ fvg_low) and closes below (c < fvg_low)
        entry_ms = fvg_bars[2].open_time_ms + _M15_MS
        entry_bar = _m15(entry_ms, 2053.0, 2054.0, 2049.0, 2051.0)
        # h=2054 ≥ fvg_low=2052 (touched), c=2051 < fvg_low=2052 (closed outside)

        all_bars = fvg_bars + [entry_bar]
        result = find_fvg_entry(all_bars, "BEAR", _SWEEP_PRICE, _DAY_MS, _CFG)

        assert result is not None
        assert result.direction == "SHORT"
        assert result.entry_price == 2051.0
        assert result.fvg_high == fvg_high
        assert result.fvg_low == fvg_low
        assert result.fvg_size == fvg_high - fvg_low
        # SL = fvg_high + fvg_size * 0.5 = 2057 + 5*0.5 = 2059.5
        assert result.stop_loss == 2057.0 + 5.0 * 0.5
        assert result.risk_reward == 3.0

    def test_bear_fvg_no_close_below(self):
        """Bar touches FVG but doesn't close below → no entry."""
        fvg_bars = _bear_fvg_bars(gap_size=5.0, base=2060.0)
        # fvg_low = fvg_bars[2].h = 2052

        # Bar touches (h=2054 ≥ 2052) but closes ABOVE (c=2055 > 2052)
        entry_ms = fvg_bars[2].open_time_ms + _M15_MS
        entry_bar = _m15(entry_ms, 2053.0, 2054.0, 2051.0, 2055.0)

        all_bars = fvg_bars + [entry_bar]
        result = find_fvg_entry(all_bars, "BEAR", _SWEEP_PRICE, _DAY_MS, _CFG)
        assert result is None


# ═══════════════════════════════════════════════════════
# Entry trigger edge cases
# ═══════════════════════════════════════════════════════


class TestStage4EntryEdge(unittest.TestCase):
    """Edge cases for entry trigger logic."""

    def test_bull_entry_engulfed(self):
        """Bar closes below fvg_low (engulfed) → rejected."""
        fvg_bars = _bull_fvg_bars(gap_size=5.0, base=2055.0)
        # fvg_low = fvg_bars[0].h = 2058

        # Entry bar: touches (low=2050 ≤ 2063) and closes above (c=2064 > 2063)
        # but closes below fvg_low (c=2056 < 2058) — engulfed
        entry_ms = fvg_bars[2].open_time_ms + _M15_MS
        entry_bar = _m15(entry_ms, 2060.0, 2064.0, 2050.0, 2056.0)

        all_bars = fvg_bars + [entry_bar]
        result = find_fvg_entry(all_bars, "BULL", _SWEEP_PRICE, _DAY_MS, _CFG)
        # c=2056 < fvg_low=2058, AND c=2056 < fvg_high=2063: not_engulfed=False
        assert result is None

    def test_entry_at_exact_boundary(self):
        """Bar that closes exactly at fvg_high (not strictly above) → no entry for bull."""
        fvg_bars = _bull_fvg_bars(gap_size=5.0, base=2055.0)
        # fvg_high = fvg_bars[2].low = 2063

        entry_ms = fvg_bars[2].open_time_ms + _M15_MS
        entry_bar = _m15(entry_ms, 2062.0, 2065.0, 2060.0, 2063.0)
        # c=2063 == fvg_high → closed_outside = (2063 > 2063) = False

        all_bars = fvg_bars + [entry_bar]
        result = find_fvg_entry(all_bars, "BULL", _SWEEP_PRICE, _DAY_MS, _CFG)
        assert result is None


# ═══════════════════════════════════════════════════════
# FVG selection
# ═══════════════════════════════════════════════════════


class TestStage4Selection(unittest.TestCase):
    """Multiple FVGs — largest wins."""

    def test_largest_fvg_selected(self):
        """Two bull FVGs, larger one is selected."""
        t0 = _SEARCH_START

        # FVG #1: small gap (2 pts)
        b0 = _m15(t0, 2055, 2058, 2053, 2056)  # h=2058
        b1 = _m15(t0 + _M15_MS, 2057, 2068, 2056, 2065)  # bullish impulse
        b2 = _m15(t0 + 2 * _M15_MS, 2061, 2064, 2060, 2062)  # low=2060, gap=2060-2058=2

        # FVG #2: larger gap (8 pts)
        b3 = _m15(t0 + 3 * _M15_MS, 2060, 2063, 2058, 2061)  # h=2063
        b4 = _m15(t0 + 4 * _M15_MS, 2062, 2080, 2061, 2078)  # bullish impulse
        b5 = _m15(t0 + 5 * _M15_MS, 2072, 2076, 2071, 2074)  # low=2071, gap=2071-2063=8

        # Entry bar that touches larger FVG (fvg_high=2071)
        entry_bar = _m15(t0 + 6 * _M15_MS, 2070, 2074, 2069, 2072)
        # low=2069 ≤ 2071 (touched), c=2072 > 2071 (closed outside)

        all_bars = [b0, b1, b2, b3, b4, b5, entry_bar]
        result = find_fvg_entry(all_bars, "BULL", _SWEEP_PRICE, _DAY_MS, _CFG)

        assert result is not None
        # Should pick the 8pt FVG, not the 2pt one
        assert result.fvg_size == 8.0


# ═══════════════════════════════════════════════════════
# Determinism
# ═══════════════════════════════════════════════════════


class TestStage4Determinism(unittest.TestCase):
    def test_same_input_same_output(self):
        fvg_bars = _bull_fvg_bars(gap_size=5.0)
        entry_ms = fvg_bars[2].open_time_ms + _M15_MS
        entry_bar = _m15(entry_ms, 2062.0, 2066.0, 2061.0, 2064.0)
        all_bars = fvg_bars + [entry_bar]

        r1 = find_fvg_entry(all_bars, "BULL", _SWEEP_PRICE, _DAY_MS, _CFG)
        r2 = find_fvg_entry(all_bars, "BULL", _SWEEP_PRICE, _DAY_MS, _CFG)
        assert r1 == r2


# ═══════════════════════════════════════════════════════
# Config overrides
# ═══════════════════════════════════════════════════════


class TestStage4Config(unittest.TestCase):
    def test_custom_proximity(self):
        """Proximity filter set to 10 pts → rejects FVG far from sweep."""
        cfg = TdaCascadeConfig(fvg_proximity_pts=10.0)
        # FVG mid ≈ 2060, sweep_price = 2060 → within 10
        fvg_bars = _bull_fvg_bars(gap_size=5.0, base=2055.0)
        entry_ms = fvg_bars[2].open_time_ms + _M15_MS
        entry_bar = _m15(entry_ms, 2062.0, 2066.0, 2061.0, 2064.0)
        all_bars = fvg_bars + [entry_bar]
        result = find_fvg_entry(all_bars, "BULL", 2060.0, _DAY_MS, cfg)
        assert result is not None  # within 10 pts

        # Same FVG but sweep_price far away
        result2 = find_fvg_entry(all_bars, "BULL", 2090.0, _DAY_MS, cfg)
        assert result2 is None  # >10 pts away

    def test_custom_min_abs_pts(self):
        """fvg_min_abs_pts = 10 → rejects 5pt FVG."""
        cfg = TdaCascadeConfig(fvg_min_abs_pts=10.0)
        fvg_bars = _bull_fvg_bars(gap_size=5.0)
        entry_ms = fvg_bars[2].open_time_ms + _M15_MS
        entry_bar = _m15(entry_ms, 2062.0, 2066.0, 2061.0, 2064.0)
        all_bars = fvg_bars + [entry_bar]
        result = find_fvg_entry(all_bars, "BULL", _SWEEP_PRICE, _DAY_MS, cfg)
        assert result is None

    def test_custom_rr_target(self):
        """Custom rr_target = 4.0 → TP further out."""
        cfg = TdaCascadeConfig(rr_target=4.0)
        fvg_bars = _bull_fvg_bars(gap_size=5.0, base=2055.0)
        entry_ms = fvg_bars[2].open_time_ms + _M15_MS
        entry_bar = _m15(entry_ms, 2062.0, 2066.0, 2061.0, 2064.0)
        all_bars = fvg_bars + [entry_bar]
        result = find_fvg_entry(all_bars, "BULL", _SWEEP_PRICE, _DAY_MS, cfg)
        assert result is not None
        assert result.risk_reward == 4.0
        # TP = entry + sl_size * 4.0
        sl_size = result.entry_price - result.stop_loss
        assert abs(result.take_profit - (result.entry_price + sl_size * 4.0)) < 0.01


# ═══════════════════════════════════════════════════════
# to_wire
# ═══════════════════════════════════════════════════════


class TestStage4ToWire(unittest.TestCase):
    def test_to_wire(self):
        fvg_bars = _bull_fvg_bars(gap_size=5.0)
        entry_ms = fvg_bars[2].open_time_ms + _M15_MS
        entry_bar = _m15(entry_ms, 2062.0, 2066.0, 2061.0, 2064.0)
        all_bars = fvg_bars + [entry_bar]
        result = find_fvg_entry(all_bars, "BULL", _SWEEP_PRICE, _DAY_MS, _CFG)
        assert result is not None

        wire = result.to_wire()
        assert "entry_price" in wire
        assert "stop_loss" in wire
        assert "take_profit" in wire
        assert "risk_reward" in wire
        assert "direction" in wire
        assert wire["direction"] == "LONG"
        assert "fvg_high" in wire
        assert "fvg_low" in wire
        assert "fvg_size" in wire
        assert "entry_bar_ms" in wire


if __name__ == "__main__":
    unittest.main()
