"""
tests/test_tda_stage1.py — Stage 1: D1 Macro Direction (ADR-0040).

Тестує:
  - Pivot-based BULL/BEAR/CFL з confidence
  - Slope fallback
  - Gate: insufficient bars → CFL
  - Детермінізм: same input → same output
  - Крайові випадки: flat market, single pivot, equal pivots
"""

from core.model.bars import CandleBar
from core.smc.tda.types import TdaCascadeConfig
from core.smc.tda.stage1_macro import get_macro_direction


# ── Helpers ──


def _d1(
    open_ms: int, o: float, h: float, low: float, c: float, complete: bool = True
) -> CandleBar:
    """Create a D1 CandleBar for testing."""
    return CandleBar(
        symbol="XAU/USD",
        tf_s=86400,
        open_time_ms=open_ms,
        close_time_ms=open_ms + 86400_000,
        o=o,
        h=h,
        low=low,
        c=c,
        v=100.0,
        complete=complete,
        src="history",
    )


_CFG = TdaCascadeConfig()
_DAY_MS = 86400_000  # 1 day in ms


def _make_uptrend_bars(n: int = 12) -> list[CandleBar]:
    """Create N D1 bars with clear uptrend: HH + HL zigzag pattern.

    Even bars = rally peaks (high-high, moderate-low).
    Odd bars  = pullback troughs (low-high, deep-low).
    Amplitude (25pt) >> trend drift (5pt/bar) → clean pivots.
    """
    bars = []
    base = 2800.0
    for i in range(n):
        trend = base + i * 5
        if i % 2 == 0:  # peak bar
            o, h, low, c = trend, trend + 30, trend + 5, trend + 25
        else:  # trough bar
            o, h, low, c = trend, trend + 5, trend - 20, trend - 15
        bars.append(_d1(i * _DAY_MS, o, h, low, c))
    return bars


def _make_downtrend_bars(n: int = 12) -> list[CandleBar]:
    """Create N D1 bars with clear downtrend: LH + LL zigzag pattern.

    Odd bars  = relief-rally peaks (high-high, moderate-low).
    Even bars = sell-off troughs (low-high, deep-low).
    """
    bars = []
    base = 2900.0
    for i in range(n):
        trend = base - i * 5
        if i % 2 == 0:  # trough bar
            o, h, low, c = trend, trend + 5, trend - 30, trend - 25
        else:  # peak bar (relief rally)
            o, h, low, c = trend, trend + 20, trend - 5, trend + 15
        bars.append(_d1(i * _DAY_MS, o, h, low, c))
    return bars


def _make_flat_bars(n: int = 12) -> list[CandleBar]:
    """Create N D1 bars with flat/ranging market."""
    bars = []
    for i in range(n):
        o = 2850.0 + (i % 2) * 2  # tiny oscillation
        h = o + 3
        low = o - 3
        c = o + 1
        bars.append(_d1(i * _DAY_MS, o, h, low, c))
    return bars


# ═══════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════


class TestStage1Gate:
    """Gate: insufficient completed bars → CFL."""

    def test_empty_bars(self):
        result = get_macro_direction([], _CFG)
        assert result.direction == "CFL"
        assert result.d1_bar_count == 0

    def test_below_minimum(self):
        bars = _make_uptrend_bars(4)  # Need 5
        result = get_macro_direction(bars, _CFG)
        assert result.direction == "CFL"
        assert result.d1_bar_count == 4
        assert result.confidence == "weak"

    def test_incomplete_bars_filtered(self):
        """Incomplete bars should be excluded from counting."""
        bars = _make_uptrend_bars(6)
        # Mark last 2 as incomplete → only 4 complete bars
        modified = bars[:4] + [
            _d1(b.open_time_ms, b.o, b.h, b.low, b.c, complete=False) for b in bars[4:]
        ]
        result = get_macro_direction(modified, _CFG)
        assert result.direction == "CFL"
        assert result.d1_bar_count == 4

    def test_exact_minimum(self):
        """Exactly macro_min_bars completed → should process (may be slope fallback)."""
        bars = _make_uptrend_bars(5)
        result = get_macro_direction(bars, _CFG)
        assert result.d1_bar_count == 5
        # With 5 bars, 3 inner bars → may have pivots
        assert result.direction in ("BULL", "BEAR", "CFL")


class TestStage1PivotBull:
    """Clear uptrend: HH + HL → BULL with strong confidence."""

    def test_clear_uptrend(self):
        bars = _make_uptrend_bars(12)
        result = get_macro_direction(bars, _CFG)
        assert result.direction == "BULL"
        assert result.method == "pivot"
        assert result.confidence == "strong"
        assert result.pivot_count > 0
        assert result.d1_bar_count == 12

    def test_uptrend_20_bars(self):
        bars = _make_uptrend_bars(20)
        result = get_macro_direction(bars, _CFG)
        assert result.direction == "BULL"
        assert result.confidence == "strong"

    def test_determinism(self):
        """Same input → same output (S2)."""
        bars = _make_uptrend_bars(15)
        r1 = get_macro_direction(bars, _CFG)
        r2 = get_macro_direction(bars, _CFG)
        assert r1 == r2


class TestStage1PivotBear:
    """Clear downtrend: LH + LL → BEAR with strong confidence."""

    def test_clear_downtrend(self):
        bars = _make_downtrend_bars(12)
        result = get_macro_direction(bars, _CFG)
        assert result.direction == "BEAR"
        assert result.method == "pivot"
        assert result.confidence == "strong"
        assert result.pivot_count > 0

    def test_downtrend_20_bars(self):
        bars = _make_downtrend_bars(20)
        result = get_macro_direction(bars, _CFG)
        assert result.direction == "BEAR"
        assert result.confidence == "strong"


class TestStage1SlopeFallback:
    """Insufficient pivots → slope-based direction."""

    def test_slope_bull(self):
        """Steady rise without clear pivots (small range)."""
        bars = []
        for i in range(10):
            # Monotonic rise, no local peaks/troughs
            price = 2800.0 + i * 5
            bars.append(_d1(i * _DAY_MS, price, price + 1, price - 1, price + 0.5))
        result = get_macro_direction(bars, _CFG)
        assert result.direction == "BULL"
        assert result.method == "slope"
        assert result.confidence == "weak"

    def test_slope_bear(self):
        """Steady decline without clear pivots."""
        bars = []
        for i in range(10):
            price = 2900.0 - i * 5
            bars.append(_d1(i * _DAY_MS, price, price + 1, price - 1, price - 0.5))
        result = get_macro_direction(bars, _CFG)
        assert result.direction == "BEAR"
        assert result.method == "slope"
        assert result.confidence == "weak"


class TestStage1CFL:
    """Conflict/ranging → CFL."""

    def test_flat_market(self):
        bars = _make_flat_bars(12)
        result = get_macro_direction(bars, _CFG)
        assert result.direction == "CFL"

    def test_equal_pivots(self):
        """Equal pivot highs and lows → no HH/LH/HL/LL → fall through."""
        bars = []
        for i in range(12):
            # Alternating but same highs and same lows
            if i % 2 == 0:
                bars.append(_d1(i * _DAY_MS, 2850.0, 2860.0, 2840.0, 2855.0))
            else:
                bars.append(_d1(i * _DAY_MS, 2855.0, 2860.0, 2840.0, 2845.0))
        result = get_macro_direction(bars, _CFG)
        # With equal pivots, falls through to slope which should be ~flat
        assert result.direction == "CFL"


class TestStage1Config:
    """Config-driven behavior (S5)."""

    def test_custom_min_bars(self):
        cfg = TdaCascadeConfig(macro_min_bars=10)
        bars = _make_uptrend_bars(8)
        result = get_macro_direction(bars, cfg)
        assert result.direction == "CFL"
        assert result.d1_bar_count == 8

    def test_custom_slope_threshold(self):
        """Higher threshold → more likely CFL."""
        # Create bars with moderate slope
        bars = []
        for i in range(10):
            price = 2800.0 + i * 0.5  # very gentle slope
            bars.append(_d1(i * _DAY_MS, price, price + 1, price - 1, price + 0.3))
        # Default threshold 0.03 → might detect slope
        _ = get_macro_direction(bars, _CFG)
        # High threshold 1.0 → CFL
        cfg_strict = TdaCascadeConfig(macro_slope_threshold=1.0)
        r_strict = get_macro_direction(bars, cfg_strict)
        assert r_strict.direction == "CFL"

    def test_custom_lookback(self):
        """Custom lookback uses fewer bars for pivots."""
        bars = _make_uptrend_bars(25)
        cfg_short = TdaCascadeConfig(macro_lookback_bars=5)
        result = get_macro_direction(bars, cfg_short)
        # With only 5 bars, 3 inner bars for pivots
        assert result.d1_bar_count == 25  # all 25 complete


class TestStage1ToWire:
    """MacroResult.to_wire() produces correct format."""

    def test_to_wire(self):
        bars = _make_uptrend_bars(12)
        result = get_macro_direction(bars, _CFG)
        wire = result.to_wire()
        assert "direction" in wire
        assert "method" in wire
        assert "confidence" in wire
        assert wire["direction"] in ("BULL", "BEAR", "CFL")
