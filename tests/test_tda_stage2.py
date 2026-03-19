"""Tests for core/smc/tda/stage2_h4_confirm.py — Stage 2: H4 Confirmation.

Covers:
- Gate: insufficient bars → not confirmed
- BULL confirmation: above midpoint, trending
- BEAR confirmation: below midpoint, trending
- Cutoff logic: only bars before 07:00 UTC considered
- CFL macro → always not confirmed
- Determinism: same input → same output
- Config-driven: custom min_bars, confirm_bars, cutoff
- to_wire serialization
"""

from core.model.bars import CandleBar
from core.smc.tda.stage2_h4_confirm import h4_confirmed
from core.smc.tda.types import TdaCascadeConfig

_H4_S = 14400
_H4_MS = _H4_S * 1000
_CFG = TdaCascadeConfig()


def _h4(open_ms: int, o: float, h: float, low: float, c: float) -> CandleBar:
    """Create a complete H4 bar."""
    return CandleBar(
        symbol="XAU/USD",
        tf_s=_H4_S,
        open_time_ms=open_ms,
        close_time_ms=open_ms + _H4_MS,
        o=o,
        h=h,
        low=low,
        c=c,
        v=100.0,
        complete=True,
        src="test",
    )


# Day starts at 00:00 UTC → epoch ms
_DAY_MS = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC


def _make_h4_bars_before_cutoff(
    n: int, base_price: float, slope: float = 0.0
) -> list[CandleBar]:
    """Create N evenly-spaced H4 bars ending just before 07:00 UTC.

    Bars spaced at 4h intervals, last bar opens at cutoff - 4h = 03:00 UTC.
    So first bar opens at 03:00 - (n-1)*4h.
    """
    cutoff_ms = _DAY_MS + 7 * 3_600_000  # 07:00 UTC
    bars = []
    for i in range(n):
        open_ms = cutoff_ms - (n - i) * _H4_MS
        price = base_price + i * slope
        bars.append(_h4(open_ms, price, price + 5, price - 5, price))
    return bars


# ═══════════════════════════════════════════════════════
# Gate tests
# ═══════════════════════════════════════════════════════


class TestStage2Gate:
    def test_empty_bars(self):
        result = h4_confirmed([], "BULL", _DAY_MS, _CFG)
        assert not result.confirmed
        assert result.reason == "insufficient_bars"
        assert result.h4_bar_count == 0

    def test_below_minimum(self):
        bars = _make_h4_bars_before_cutoff(3, 2800.0)
        result = h4_confirmed(bars, "BULL", _DAY_MS, _CFG)
        assert not result.confirmed
        assert result.reason == "insufficient_bars"
        assert result.h4_bar_count == 3

    def test_exact_minimum(self):
        bars = _make_h4_bars_before_cutoff(5, 2800.0)
        result = h4_confirmed(bars, "BULL", _DAY_MS, _CFG)
        assert result.h4_bar_count == 5
        assert result.reason != "insufficient_bars"


# ═══════════════════════════════════════════════════════
# BULL confirmation
# ═══════════════════════════════════════════════════════


class TestStage2Bull:
    def test_above_midpoint(self):
        """Close clearly above midpoint of range → confirmed."""
        bars = []
        cutoff_ms = _DAY_MS + 7 * 3_600_000
        for i in range(8):
            open_ms = cutoff_ms - (8 - i) * _H4_MS
            if i < 4:
                # Lower bars to establish range bottom
                bars.append(_h4(open_ms, 2780.0, 2790.0, 2770.0, 2780.0))
            else:
                # Higher bars — close well above midpoint
                bars.append(_h4(open_ms, 2850.0, 2870.0, 2845.0, 2860.0))
        # Range: highest=2870, lowest=2770, mid=2820, close=2860 > 2820
        result = h4_confirmed(bars, "BULL", _DAY_MS, _CFG)
        assert result.confirmed
        assert result.reason == "above_midpoint"
        assert result.close_price == 2860.0
        assert result.midpoint == (2870.0 + 2770.0) / 2

    def test_trending_up(self):
        """Last 3 closes trending up even if below midpoint → confirmed."""
        bars = []
        cutoff_ms = _DAY_MS + 7 * 3_600_000
        for i in range(8):
            open_ms = cutoff_ms - (8 - i) * _H4_MS
            # Establish high range artificially
            if i == 0:
                bars.append(_h4(open_ms, 2900.0, 2950.0, 2890.0, 2900.0))
            elif i < 5:
                bars.append(_h4(open_ms, 2800.0, 2810.0, 2790.0, 2800.0))
            else:
                # Last 3 bars: trending up 2805 → 2810 → 2815
                price = 2805.0 + (i - 5) * 5
                bars.append(_h4(open_ms, price, price + 5, price - 3, price))
        # midpoint = (2950 + 2790) / 2 = 2870; close=2815 < 2870
        # But last3 trending: 2815 >= 2805 → confirmed
        result = h4_confirmed(bars, "BULL", _DAY_MS, _CFG)
        assert result.confirmed
        assert result.reason == "trending"

    def test_not_confirmed_bull(self):
        """Close below midpoint AND last3 trending down → not confirmed."""
        bars = []
        cutoff_ms = _DAY_MS + 7 * 3_600_000
        for i in range(8):
            open_ms = cutoff_ms - (8 - i) * _H4_MS
            if i == 0:
                # Set high point
                bars.append(_h4(open_ms, 2900.0, 2950.0, 2890.0, 2900.0))
            elif i < 5:
                bars.append(_h4(open_ms, 2850.0, 2860.0, 2840.0, 2850.0))
            else:
                # Last 3 bars: clearly trending DOWN (2830 → 2820 → 2810)
                price = 2830.0 - (i - 5) * 10
                bars.append(_h4(open_ms, price + 5, price + 10, price - 5, price))
        # midpoint = (2950 + 2795) / 2 = 2872.5; close=2810 < 2872.5
        # last3: 2810 >= 2830? NO → trending not met, above_mid not met
        result = h4_confirmed(bars, "BULL", _DAY_MS, _CFG)
        assert not result.confirmed
        assert result.reason == "not_confirmed"


# ═══════════════════════════════════════════════════════
# BEAR confirmation
# ═══════════════════════════════════════════════════════


class TestStage2Bear:
    def test_below_midpoint(self):
        """Close clearly below midpoint → confirmed BEAR."""
        bars = []
        cutoff_ms = _DAY_MS + 7 * 3_600_000
        for i in range(8):
            open_ms = cutoff_ms - (8 - i) * _H4_MS
            if i < 4:
                # Higher bars to establish range top
                bars.append(_h4(open_ms, 2900.0, 2920.0, 2890.0, 2900.0))
            else:
                # Lower bars — close well below midpoint
                bars.append(_h4(open_ms, 2830.0, 2840.0, 2820.0, 2825.0))
        # Range: highest=2920, lowest=2820, mid=2870, close=2825 < 2870
        result = h4_confirmed(bars, "BEAR", _DAY_MS, _CFG)
        assert result.confirmed
        assert result.reason == "below_midpoint"

    def test_trending_down(self):
        """Last 3 closes trending down even if above midpoint → confirmed BEAR."""
        bars = []
        cutoff_ms = _DAY_MS + 7 * 3_600_000
        for i in range(8):
            open_ms = cutoff_ms - (8 - i) * _H4_MS
            if i == 0:
                # Low point to pull midpoint down
                bars.append(_h4(open_ms, 2750.0, 2760.0, 2700.0, 2750.0))
            elif i < 5:
                bars.append(_h4(open_ms, 2880.0, 2890.0, 2870.0, 2880.0))
            else:
                # Last 3: trending down 2876 → 2872 → 2868
                price = 2876.0 - (i - 5) * 4
                bars.append(_h4(open_ms, price + 3, price + 5, price - 3, price))
        # midpoint = (2890 + 2700) / 2 = 2795; close=2868 > 2795
        # But last3: 2868 <= 2876 → trending down → confirmed
        result = h4_confirmed(bars, "BEAR", _DAY_MS, _CFG)
        assert result.confirmed
        assert result.reason == "trending"

    def test_not_confirmed_bear(self):
        """Close above midpoint AND last3 trending up → not confirmed BEAR."""
        bars = []
        cutoff_ms = _DAY_MS + 7 * 3_600_000
        for i in range(8):
            open_ms = cutoff_ms - (8 - i) * _H4_MS
            if i == 0:
                bars.append(_h4(open_ms, 2780.0, 2790.0, 2770.0, 2780.0))
            elif i < 5:
                bars.append(_h4(open_ms, 2830.0, 2840.0, 2820.0, 2830.0))
            else:
                # Last 3: trending UP 2835 → 2840 → 2845
                price = 2835.0 + (i - 5) * 5
                bars.append(_h4(open_ms, price - 3, price + 5, price - 5, price))
        # midpoint = (2845 + 2770) / 2 = 2807.5; close=2845 > 2807.5
        # last3: 2845 <= 2835? NO
        result = h4_confirmed(bars, "BEAR", _DAY_MS, _CFG)
        assert not result.confirmed


# ═══════════════════════════════════════════════════════
# Cutoff logic
# ═══════════════════════════════════════════════════════


class TestStage2Cutoff:
    def test_bars_after_cutoff_excluded(self):
        """Bars opening at or after 07:00 UTC must be ignored."""
        cutoff_ms = _DAY_MS + 7 * 3_600_000
        bars = []
        # 4 bars BEFORE cutoff
        for i in range(4):
            open_ms = cutoff_ms - (4 - i) * _H4_MS
            bars.append(_h4(open_ms, 2800.0, 2810.0, 2790.0, 2800.0))
        # 6 bars AT or AFTER cutoff
        for i in range(6):
            open_ms = cutoff_ms + i * _H4_MS
            bars.append(_h4(open_ms, 2900.0, 2950.0, 2890.0, 2900.0))
        # Only 4 bars pass filter → insufficient
        result = h4_confirmed(bars, "BULL", _DAY_MS, _CFG)
        assert not result.confirmed
        assert result.reason == "insufficient_bars"
        assert result.h4_bar_count == 4


# ═══════════════════════════════════════════════════════
# CFL macro
# ═══════════════════════════════════════════════════════


class TestStage2CFL:
    def test_cfl_not_confirmed(self):
        """CFL macro direction → always not confirmed."""
        bars = _make_h4_bars_before_cutoff(10, 2800.0, slope=5.0)
        result = h4_confirmed(bars, "CFL", _DAY_MS, _CFG)
        assert not result.confirmed
        assert result.reason == "not_confirmed"


# ═══════════════════════════════════════════════════════
# Determinism & config
# ═══════════════════════════════════════════════════════


class TestStage2Determinism:
    def test_same_input_same_output(self):
        bars = _make_h4_bars_before_cutoff(10, 2800.0, slope=3.0)
        r1 = h4_confirmed(bars, "BULL", _DAY_MS, _CFG)
        r2 = h4_confirmed(bars, "BULL", _DAY_MS, _CFG)
        assert r1 == r2


class TestStage2Config:
    def test_custom_min_bars(self):
        """Higher min_bars gate → may reject fewer bars."""
        bars = _make_h4_bars_before_cutoff(6, 2800.0)
        cfg_strict = TdaCascadeConfig(h4_min_bars=10)
        result = h4_confirmed(bars, "BULL", _DAY_MS, cfg_strict)
        assert not result.confirmed
        assert result.reason == "insufficient_bars"

    def test_custom_cutoff(self):
        """Custom cutoff 05:00 UTC → fewer bars qualify."""
        cutoff_5am = _DAY_MS + 5 * 3_600_000
        # Create bars around 05:00
        bars = []
        for i in range(8):
            open_ms = cutoff_5am - (8 - i) * _H4_MS
            bars.append(_h4(open_ms, 2800.0 + i, 2810.0 + i, 2790.0 + i, 2800.0 + i))
        cfg_early = TdaCascadeConfig(h4_cutoff_hour_utc=5)
        result = h4_confirmed(bars, "BULL", _DAY_MS, cfg_early)
        assert result.h4_bar_count <= 8


# ═══════════════════════════════════════════════════════
# to_wire
# ═══════════════════════════════════════════════════════


class TestStage2ToWire:
    def test_to_wire(self):
        bars = _make_h4_bars_before_cutoff(8, 2800.0, slope=2.0)
        result = h4_confirmed(bars, "BULL", _DAY_MS, _CFG)
        wire = result.to_wire()
        assert "confirmed" in wire
        assert "reason" in wire
        assert isinstance(wire["confirmed"], bool)
