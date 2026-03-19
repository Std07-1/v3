"""Tests for core.smc.tda.orchestrator — TDA Cascade Orchestrator."""

import unittest

from core.model.bars import CandleBar
from core.smc.tda.types import TdaCascadeConfig
from core.smc.tda.orchestrator import run_tda_cascade

_CFG = TdaCascadeConfig()


# ═══════════════════════════════════════════════════════
# Bar factory helpers
# ═══════════════════════════════════════════════════════

_D1_MS = 86_400_000
_H4_MS = 14_400_000
_H1_MS = 3_600_000
_M15_MS = 900_000

# 2026-01-15 00:00 UTC as day_ms
_DAY_MS = 1_768_435_200_000


def _d1(idx: int, o: float, h: float, low: float, c: float) -> CandleBar:
    """D1 bar ending `idx` days before day_ms (0 = prev day)."""
    ms = _DAY_MS - (idx + 1) * _D1_MS
    return CandleBar(
        symbol="XAU/USD",
        tf_s=86400,
        open_time_ms=ms,
        close_time_ms=ms + _D1_MS,
        o=o,
        h=h,
        low=low,
        c=c,
        v=1000.0,
        complete=True,
        src="test",
    )


def _h4(hour_offset: float, o: float, h: float, low: float, c: float) -> CandleBar:
    """H4 bar at hour_offset from day_ms (e.g., -8 = 16:00 previous day)."""
    ms = _DAY_MS + int(hour_offset * 3_600_000)
    return CandleBar(
        symbol="XAU/USD",
        tf_s=14400,
        open_time_ms=ms,
        close_time_ms=ms + _H4_MS,
        o=o,
        h=h,
        low=low,
        c=c,
        v=500.0,
        complete=True,
        src="test",
    )


def _h1(hour_offset: float, o: float, h: float, low: float, c: float) -> CandleBar:
    """H1 bar at hour_offset from day_ms."""
    ms = _DAY_MS + int(hour_offset * 3_600_000)
    return CandleBar(
        symbol="XAU/USD",
        tf_s=3600,
        open_time_ms=ms,
        close_time_ms=ms + _H1_MS,
        o=o,
        h=h,
        low=low,
        c=c,
        v=200.0,
        complete=True,
        src="test",
    )


def _m15(hour_offset: float, o: float, h: float, low: float, c: float) -> CandleBar:
    """M15 bar at hour_offset from day_ms."""
    ms = _DAY_MS + int(hour_offset * 3_600_000)
    return CandleBar(
        symbol="XAU/USD",
        tf_s=900,
        open_time_ms=ms,
        close_time_ms=ms + _M15_MS,
        o=o,
        h=h,
        low=low,
        c=c,
        v=50.0,
        complete=True,
        src="test",
    )


# ═══════════════════════════════════════════════════════
# Dataset builders
# ═══════════════════════════════════════════════════════


def _bullish_d1_bars(n: int = 10) -> list:
    """D1 bars forming a clear uptrend (zigzag with higher lows + highs)."""
    bars = []
    base = 2000.0
    for i in range(n):
        low = base + i * 20
        high = low + 30
        o = low + 5 if i % 2 == 0 else high - 5
        c = high - 5 if i % 2 == 0 else low + 5
        bars.append(_d1(n - 1 - i, o, high, low, c))
    return bars


def _h4_bull_confirm_bars() -> list:
    """H4 bars before London open — price above midpoint (BULL aligned).
    Need ≥5 bars before cutoff (07:00 UTC) for h4_min_bars gate.
    """
    return [
        _h4(-12, 2160, 2172, 2155, 2170),  # prev day 12:00
        _h4(-8, 2170, 2180, 2165, 2178),  # prev day 16:00
        _h4(-4, 2178, 2185, 2172, 2184),  # prev day 20:00
        _h4(0, 2184, 2192, 2180, 2190),  # day 00:00
        _h4(4, 2190, 2198, 2185, 2195),  # day 04:00
    ]


def _asia_h1_bars() -> list:
    """H1 bars for Asia session (23:00 prev day to 07:00 current day)."""
    # Asia: asia_high = 2195, asia_low = 2180
    return [
        _h1(-1, 2185, 2190, 2180, 2188),  # 23:00 prev day
        _h1(0, 2188, 2192, 2182, 2190),  # 00:00
        _h1(1, 2190, 2195, 2184, 2192),  # 01:00
        _h1(2, 2192, 2194, 2183, 2189),  # 02:00
        _h1(3, 2189, 2193, 2182, 2188),  # 03:00
        _h1(4, 2188, 2192, 2181, 2190),  # 04:00
        _h1(5, 2190, 2193, 2183, 2185),  # 05:00
        _h1(6, 2185, 2192, 2182, 2190),  # 06:00
    ]


def _london_sweep_high_h1_bars() -> list:
    """H1 bars for London session — sweep Asia high (2195) then return.

    BEAR macro means HUNT_PREV_HIGH → fvg_direction = "BEAR".
    But with BULL macro this would be COUNTER_TREND → skip.
    """
    return [
        _h1(8, 2192, 2198, 2190, 2196),  # 08:00 — swept above 2195
        _h1(9, 2196, 2197, 2188, 2190),  # 09:00 — returned below 2195 (c < 2195)
        _h1(10, 2190, 2193, 2185, 2187),  # 10:00
        _h1(11, 2187, 2190, 2182, 2184),  # 11:00
        _h1(12, 2184, 2188, 2180, 2183),  # 12:00
    ]


def _london_sweep_low_h1_bars() -> list:
    """H1 bars for London session — sweep Asia low (2180) then return.

    BULL macro → HUNT_PREV_LOW → fvg_direction = "BULL".
    """
    return [
        _h1(8, 2185, 2188, 2178, 2179),  # 08:00 — swept below 2180
        _h1(9, 2179, 2186, 2177, 2185),  # 09:00 — returned above 2180 (c > 2180)
        _h1(10, 2185, 2192, 2183, 2190),  # 10:00
        _h1(11, 2190, 2194, 2188, 2193),  # 11:00 — h=2194 ≤ asia_high
        _h1(12, 2193, 2194, 2190, 2193),  # 12:00 — h=2194 ≤ asia_high
    ]


def _bull_fvg_m15_bars(sweep_price: float) -> list:
    """M15 bars in search window (09:00-16:00) with a bull FVG near sweep_price.

    Bull FVG: b0.h < b2.low (gap up), b1 = bullish impulse.
    Then entry bar: touches fvg_high then closes above.
    """
    # FVG: b0.h=2182, b1 impulse up, b2.low=2186 → gap = [2182, 2186]
    base = sweep_price  # e.g., 2180
    return [
        _m15(9.0, base + 2, base + 4, base, base + 2),  # 09:00 — b0: h=2182
        _m15(
            9.25, base + 2, base + 12, base + 2, base + 10
        ),  # 09:15 — b1: bullish impulse
        _m15(
            9.5, base + 10, base + 12, base + 6, base + 8
        ),  # 09:30 — b2: low=2186 > b0.h=2182 ✗
    ]


def _full_bull_fvg_m15_bars() -> list:
    """M15 bars with valid BULL FVG + entry trigger."""
    # FVG: b0.h = 2182, b2.low = 2184 → gap [2182, 2184], size = 2
    # b1 = bullish impulse candle
    return [
        _m15(9.0, 2180, 2182, 2178, 2181),  # b0: h=2182
        _m15(9.25, 2182, 2192, 2181, 2190),  # b1: bullish impulse (c>o)
        _m15(9.5, 2190, 2191, 2184, 2190),  # b2: low=2184 > 2182 → gap=2
        _m15(9.75, 2190, 2192, 2185, 2191),  # filler
        _m15(10.0, 2191, 2193, 2183, 2185),  # entry: low=2183 ≤ fvg_high=2184,
        #        c=2185 > fvg_high=2184 ✓
        #        c=2185 ≥ fvg_low=2182 ✓
    ]


# ═══════════════════════════════════════════════════════
# Full cascade — happy path
# ═══════════════════════════════════════════════════════


class TestOrchHappyPath(unittest.TestCase):
    def test_full_hunt_low_bull(self):
        """BULL macro + sweep Asia low + return → HUNT_PREV_LOW → BULL FVG entry."""
        cfg = TdaCascadeConfig(
            fvg_min_abs_pts=1.0,
            fvg_min_atr_ratio=0.0,  # disable ATR-based filter for unit test
            fvg_proximity_pts=500.0,  # wide proximity for test
            min_rr=0.0,  # disable RR gate
            grade_enabled=False,  # skip grade gate for happy path
        )
        d1 = _bullish_d1_bars(10)
        h4 = _h4_bull_confirm_bars()
        h1 = _asia_h1_bars() + _london_sweep_low_h1_bars()
        m15 = _full_bull_fvg_m15_bars()

        sig = run_tda_cascade(
            "XAU/USD",
            "2026-01-15",
            d1,
            h4,
            h1,
            m15,
            _DAY_MS,
            cfg,
            _DAY_MS + 12 * 3_600_000,
        )
        assert sig is not None
        assert sig.signal_id == "tda_XAU_USD_2026-01-15"
        assert sig.macro.direction == "BULL"
        assert sig.h4_confirm.confirmed is True
        assert sig.session.narrative == "HUNT_PREV_LOW"
        assert sig.entry.direction == "LONG"
        assert sig.trade.status == "open"
        assert sig.grade_score >= 0

    def test_deterministic(self):
        """Same inputs → same signal."""
        cfg = TdaCascadeConfig(
            fvg_min_abs_pts=1.0,
            fvg_min_atr_ratio=0.0,
            fvg_proximity_pts=500.0,
            min_rr=0.0,
            grade_enabled=False,
        )
        d1 = _bullish_d1_bars(10)
        h4 = _h4_bull_confirm_bars()
        h1 = _asia_h1_bars() + _london_sweep_low_h1_bars()
        m15 = _full_bull_fvg_m15_bars()

        sig1 = run_tda_cascade(
            "XAU/USD",
            "2026-01-15",
            d1,
            h4,
            h1,
            m15,
            _DAY_MS,
            cfg,
            _DAY_MS,
        )
        sig2 = run_tda_cascade(
            "XAU/USD",
            "2026-01-15",
            d1,
            h4,
            h1,
            m15,
            _DAY_MS,
            cfg,
            _DAY_MS,
        )
        assert sig1 == sig2


# ═══════════════════════════════════════════════════════
# Stage fails → None
# ═══════════════════════════════════════════════════════


class TestOrchStageGates(unittest.TestCase):
    def test_empty_d1_returns_none(self):
        """No D1 bars → Stage 1 fails → None."""
        sig = run_tda_cascade(
            "XAU/USD",
            "2026-01-15",
            [],
            [],
            [],
            [],
            _DAY_MS,
            _CFG,
            _DAY_MS,
        )
        assert sig is None

    def test_cfl_macro_returns_none(self):
        """Conflicting D1 direction → None."""
        # Flat D1 bars: closes equal → slope ≈ 0 → CFL
        flat = [_d1(i, 2100, 2105, 2095, 2100) for i in range(10)]
        sig = run_tda_cascade(
            "XAU/USD",
            "2026-01-15",
            flat,
            [],
            [],
            [],
            _DAY_MS,
            _CFG,
            _DAY_MS,
        )
        assert sig is None

    def test_h4_not_confirmed_returns_none(self):
        """H4 does not confirm macro → None."""
        d1 = _bullish_d1_bars(10)
        # H4 bars below midpoint (bearish) — contradicts BULL macro
        h4_bear = [
            _h4(-8, 2100, 2105, 2095, 2098),
            _h4(-4, 2098, 2102, 2090, 2092),
            _h4(0, 2092, 2096, 2085, 2088),
            _h4(4, 2088, 2091, 2080, 2082),
            _h4(8, 2082, 2086, 2078, 2080),
        ]
        sig = run_tda_cascade(
            "XAU/USD",
            "2026-01-15",
            d1,
            h4_bear,
            [],
            [],
            _DAY_MS,
            _CFG,
            _DAY_MS,
        )
        assert sig is None

    def test_no_narrative_returns_none(self):
        """Session narrative = NO_NARRATIVE → None."""
        d1 = _bullish_d1_bars(10)
        h4 = _h4_bull_confirm_bars()
        # Only 1 Asia H1 bar (need min 2 = cfg.asia_min_h1_bars)
        h1 = [_h1(0, 2185, 2190, 2180, 2188)]
        sig = run_tda_cascade(
            "XAU/USD",
            "2026-01-15",
            d1,
            h4,
            h1,
            [],
            _DAY_MS,
            _CFG,
            _DAY_MS,
        )
        assert sig is None

    def test_counter_trend_returns_none(self):
        """BULL macro + sweep Asia high + return → COUNTER_TREND → None."""
        d1 = _bullish_d1_bars(10)
        h4 = _h4_bull_confirm_bars()
        # Sweep high with BULL macro → COUNTER_TREND (not HUNT_PREV_HIGH)
        h1 = _asia_h1_bars() + _london_sweep_high_h1_bars()
        sig = run_tda_cascade(
            "XAU/USD",
            "2026-01-15",
            d1,
            h4,
            h1,
            [],
            _DAY_MS,
            _CFG,
            _DAY_MS,
        )
        assert sig is None


# ═══════════════════════════════════════════════════════
# Grade gate
# ═══════════════════════════════════════════════════════


class TestOrchGrade(unittest.TestCase):
    def _make_signal(self, grade_enabled: bool, min_grade: str = "C"):
        cfg = TdaCascadeConfig(
            fvg_min_abs_pts=1.0,
            fvg_min_atr_ratio=0.0,
            fvg_proximity_pts=500.0,
            min_rr=0.0,
            grade_enabled=grade_enabled,
            min_grade_for_entry=min_grade,
        )
        d1 = _bullish_d1_bars(10)
        h4 = _h4_bull_confirm_bars()
        h1 = _asia_h1_bars() + _london_sweep_low_h1_bars()
        m15 = _full_bull_fvg_m15_bars()
        return run_tda_cascade(
            "XAU/USD",
            "2026-01-15",
            d1,
            h4,
            h1,
            m15,
            _DAY_MS,
            cfg,
            _DAY_MS,
        )

    def test_grade_disabled_passes(self):
        """grade_enabled=False → always passes grade gate."""
        sig = self._make_signal(grade_enabled=False)
        assert sig is not None

    def test_grade_c_passes(self):
        """min_grade_for_entry='C' → all grades pass."""
        sig = self._make_signal(grade_enabled=True, min_grade="C")
        assert sig is not None

    def test_grade_aplus_rejects_lower(self):
        """min_grade_for_entry='A+' → only A+ passes."""
        sig = self._make_signal(grade_enabled=True, min_grade="A+")
        # Test signal likely gets 6-7/8 (B or A) which is below A+
        if sig is not None:
            assert sig.grade == "A+"


# ═══════════════════════════════════════════════════════
# Signal structure
# ═══════════════════════════════════════════════════════


class TestOrchSignal(unittest.TestCase):
    def test_signal_structure(self):
        """Verify all TdaSignal fields are populated."""
        cfg = TdaCascadeConfig(
            fvg_min_abs_pts=1.0,
            fvg_min_atr_ratio=0.0,
            fvg_proximity_pts=500.0,
            min_rr=0.0,
            grade_enabled=False,
        )
        d1 = _bullish_d1_bars(10)
        h4 = _h4_bull_confirm_bars()
        h1 = _asia_h1_bars() + _london_sweep_low_h1_bars()
        m15 = _full_bull_fvg_m15_bars()

        sig = run_tda_cascade(
            "XAU/USD",
            "2026-01-15",
            d1,
            h4,
            h1,
            m15,
            _DAY_MS,
            cfg,
            _DAY_MS,
        )
        assert sig is not None
        assert sig.symbol == "XAU/USD"
        assert sig.date_str == "2026-01-15"
        assert sig.grade in ("A+", "A", "B", "C")
        assert len(sig.grade_factors) == 8
        assert sig.trade.status == "open"
        assert sig.trade.bars_elapsed == 0

    def test_to_wire(self):
        """TdaSignal.to_wire() produces valid dict."""
        cfg = TdaCascadeConfig(
            fvg_min_abs_pts=1.0,
            fvg_min_atr_ratio=0.0,
            fvg_proximity_pts=500.0,
            min_rr=0.0,
            grade_enabled=False,
        )
        d1 = _bullish_d1_bars(10)
        h4 = _h4_bull_confirm_bars()
        h1 = _asia_h1_bars() + _london_sweep_low_h1_bars()
        m15 = _full_bull_fvg_m15_bars()

        sig = run_tda_cascade(
            "XAU/USD",
            "2026-01-15",
            d1,
            h4,
            h1,
            m15,
            _DAY_MS,
            cfg,
            _DAY_MS,
        )
        assert sig is not None
        wire = sig.to_wire()
        assert "signal_id" in wire
        assert "macro" in wire
        assert "entry" in wire
        assert "trade" in wire
        assert "grade" in wire


if __name__ == "__main__":
    unittest.main()
