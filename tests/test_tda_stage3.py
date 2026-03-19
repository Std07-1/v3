"""Tests for core/smc/tda/stage3_session.py — Stage 3: Session Narrative.

Covers:
- Gate: insufficient Asia bars, tight Asia range, no London bars
- HUNT_PREV_HIGH: Asia high swept + returned + BEAR macro
- HUNT_PREV_LOW: Asia low swept + returned + BULL macro
- CONTINUATION: sweep without return
- COUNTER_TREND: sweep + return against macro
- NO_NARRATIVE: double sweep, ambiguous
- M15 fallback: no H1 sweep → detects on M15
- Cutoff windows: Asia 23:00→07:00, London 08:00→13:00
- Determinism: same input → same output
- Config-driven: custom session windows
- to_wire serialization
"""

from core.model.bars import CandleBar
from core.smc.tda.stage3_session import get_session_narrative
from core.smc.tda.types import TdaCascadeConfig

_H1_S = 3600
_H1_MS = _H1_S * 1000
_M15_S = 900
_M15_MS = _M15_S * 1000
_CFG = TdaCascadeConfig()

# Day at 00:00 UTC → epoch ms
_DAY_MS = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC
_HOUR_MS = 3_600_000


def _h1(open_ms: int, o: float, h: float, low: float, c: float) -> CandleBar:
    return CandleBar(
        symbol="XAU/USD",
        tf_s=_H1_S,
        open_time_ms=open_ms,
        close_time_ms=open_ms + _H1_MS,
        o=o,
        h=h,
        low=low,
        c=c,
        v=100.0,
        complete=True,
        src="test",
    )


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


def _asia_bars(
    asia_high: float = 2060.0, asia_low: float = 2040.0, n: int = 4
) -> list[CandleBar]:
    """Create N H1 bars in Asia session (23:00 prev day → 07:00 current day).

    Bar 0 sets the range (h=asia_high, low=asia_low).
    Remaining bars stay within the range.
    """
    asia_start_ms = _DAY_MS - _HOUR_MS  # 23:00 prev day
    mid = (asia_high + asia_low) / 2
    half = (asia_high - asia_low) / 2
    # Interior spread: at most 60% of half-range, min 0.5 pt
    sp = max(min(half * 0.6, 2.0), 0.5)
    bars = []
    for i in range(n):
        open_ms = asia_start_ms + i * _H1_MS
        if i == 0:
            # First bar defines the range
            bars.append(_h1(open_ms, mid, asia_high, asia_low, mid))
        else:
            # Interior bars stay within the range
            bars.append(_h1(open_ms, mid, mid + sp, mid - sp, mid))
    return bars


def _london_h1_bars(
    n: int = 3,
    base_price: float = 2050.0,
    sweep_high_at: int | None = None,
    sweep_high_val: float = 0.0,
    sweep_low_at: int | None = None,
    sweep_low_val: float = 0.0,
    return_close: float | None = None,
    return_at: int | None = None,
) -> list[CandleBar]:
    """Create N London H1 bars starting at 08:00 UTC.

    Optional: inject a sweep at bar index sweep_high_at / sweep_low_at.
    """
    london_start_ms = _DAY_MS + 8 * _HOUR_MS  # 08:00
    bars = []
    for i in range(n):
        open_ms = london_start_ms + i * _H1_MS
        o = base_price
        h = base_price + 5
        low = base_price - 5
        c = base_price

        if sweep_high_at is not None and i == sweep_high_at:
            h = sweep_high_val
        if sweep_low_at is not None and i == sweep_low_at:
            low = sweep_low_val
        if return_close is not None and return_at is not None and i == return_at:
            c = return_close

        bars.append(_h1(open_ms, o, h, low, c))
    return bars


# ═══════════════════════════════════════════════════════
# Gate tests
# ═══════════════════════════════════════════════════════


class TestStage3Gate:
    def test_no_asia_bars(self):
        result = get_session_narrative([], [], "BULL", _DAY_MS, _CFG)
        assert result.narrative == "NO_NARRATIVE"
        assert result.asia_bar_count == 0

    def test_one_asia_bar(self):
        """Less than min 2 Asia bars → NO_NARRATIVE."""
        asia = [_h1(_DAY_MS - _HOUR_MS, 2050, 2060, 2040, 2050)]
        result = get_session_narrative(asia, [], "BULL", _DAY_MS, _CFG)
        assert result.narrative == "NO_NARRATIVE"
        assert result.asia_bar_count == 1

    def test_tight_asia_range(self):
        """Asia range < 5 pts → NO_NARRATIVE."""
        asia = _asia_bars(asia_high=2052.0, asia_low=2050.0, n=3)
        result = get_session_narrative(asia, [], "BULL", _DAY_MS, _CFG)
        assert result.narrative == "NO_NARRATIVE"
        assert result.asia_high == 2052.0
        assert result.asia_low == 2050.0

    def test_no_london_bars(self):
        """Valid Asia but no London bars → NO_NARRATIVE."""
        asia = _asia_bars()
        result = get_session_narrative(asia, [], "BULL", _DAY_MS, _CFG)
        assert result.narrative == "NO_NARRATIVE"
        assert result.asia_high == 2060.0


# ═══════════════════════════════════════════════════════
# HUNT narratives
# ═══════════════════════════════════════════════════════


class TestStage3HuntPrevHigh:
    def test_sweep_asia_high_returned_bear_macro(self):
        """Sweep Asia high + return below + BEAR macro → HUNT_PREV_HIGH."""
        asia = _asia_bars(asia_high=2060.0, asia_low=2040.0)
        london = _london_h1_bars(
            n=4,
            base_price=2055.0,
            sweep_high_at=1,
            sweep_high_val=2065.0,  # bar 1 sweeps high
            return_close=2055.0,
            return_at=2,  # bar 2 closes below 2060
        )
        result = get_session_narrative(asia, [], "BEAR", _DAY_MS, _CFG)
        # No London bars via h1_bars → check with combined list
        all_h1 = asia + london
        result = get_session_narrative(all_h1, [], "BEAR", _DAY_MS, _CFG)
        assert result.narrative == "HUNT_PREV_HIGH"
        assert result.sweep_direction == "BEAR"
        assert result.sweep_price == 2060.0


class TestStage3HuntPrevLow:
    def test_sweep_asia_low_returned_bull_macro(self):
        """Sweep Asia low + return above + BULL macro → HUNT_PREV_LOW."""
        asia = _asia_bars(asia_high=2060.0, asia_low=2040.0)
        london = _london_h1_bars(
            n=4,
            base_price=2045.0,
            sweep_low_at=1,
            sweep_low_val=2035.0,  # bar 1 sweeps low
            return_close=2045.0,
            return_at=2,  # bar 2 closes above 2040
        )
        all_h1 = asia + london
        result = get_session_narrative(all_h1, [], "BULL", _DAY_MS, _CFG)
        assert result.narrative == "HUNT_PREV_LOW"
        assert result.sweep_direction == "BULL"
        assert result.sweep_price == 2040.0


# ═══════════════════════════════════════════════════════
# CONTINUATION
# ═══════════════════════════════════════════════════════


class TestStage3Continuation:
    def test_sweep_high_no_return(self):
        """Sweep high + NO return → CONTINUATION (breakout up)."""
        asia = _asia_bars(asia_high=2060.0, asia_low=2040.0)
        london_start_ms = _DAY_MS + 8 * _HOUR_MS
        london = [
            _h1(london_start_ms, 2058.0, 2062.0, 2055.0, 2061.0),  # bar 0: no sweep
            _h1(
                london_start_ms + _H1_MS, 2061.0, 2068.0, 2060.5, 2067.0
            ),  # bar 1: sweeps + stays above
            _h1(
                london_start_ms + 2 * _H1_MS, 2067.0, 2072.0, 2065.0, 2070.0
            ),  # bar 2: no return
        ]
        all_h1 = asia + london
        result = get_session_narrative(all_h1, [], "BULL", _DAY_MS, _CFG)
        assert result.narrative == "CONTINUATION"
        assert result.sweep_direction == "BULL"

    def test_sweep_low_no_return(self):
        """Sweep low + NO return → CONTINUATION (breakout down)."""
        asia = _asia_bars(asia_high=2060.0, asia_low=2040.0)
        london_start_ms = _DAY_MS + 8 * _HOUR_MS
        london = [
            _h1(london_start_ms, 2042.0, 2045.0, 2041.0, 2042.0),
            _h1(
                london_start_ms + _H1_MS, 2041.0, 2042.0, 2035.0, 2036.0
            ),  # sweeps low, stays below
            _h1(london_start_ms + 2 * _H1_MS, 2036.0, 2038.0, 2032.0, 2033.0),
        ]
        all_h1 = asia + london
        result = get_session_narrative(all_h1, [], "BEAR", _DAY_MS, _CFG)
        assert result.narrative == "CONTINUATION"
        assert result.sweep_direction == "BEAR"


# ═══════════════════════════════════════════════════════
# COUNTER_TREND
# ═══════════════════════════════════════════════════════


class TestStage3CounterTrend:
    def test_sweep_high_returned_bull_macro(self):
        """Sweep high + return + BULL macro (not BEAR) → COUNTER_TREND."""
        asia = _asia_bars(asia_high=2060.0, asia_low=2040.0)
        london_start_ms = _DAY_MS + 8 * _HOUR_MS
        london = [
            _h1(london_start_ms, 2058.0, 2059.0, 2055.0, 2058.0),
            _h1(
                london_start_ms + _H1_MS, 2058.0, 2065.0, 2057.0, 2063.0
            ),  # sweeps high
            _h1(
                london_start_ms + 2 * _H1_MS, 2063.0, 2064.0, 2052.0, 2055.0
            ),  # returns below 2060
        ]
        all_h1 = asia + london
        result = get_session_narrative(all_h1, [], "BULL", _DAY_MS, _CFG)
        assert result.narrative == "COUNTER_TREND"
        assert result.sweep_direction == "BEAR"

    def test_sweep_low_returned_bear_macro(self):
        """Sweep low + return + BEAR macro (not BULL) → COUNTER_TREND."""
        asia = _asia_bars(asia_high=2060.0, asia_low=2040.0)
        london_start_ms = _DAY_MS + 8 * _HOUR_MS
        london = [
            _h1(london_start_ms, 2042.0, 2045.0, 2041.0, 2042.0),
            _h1(london_start_ms + _H1_MS, 2042.0, 2044.0, 2037.0, 2038.0),  # sweeps low
            _h1(
                london_start_ms + 2 * _H1_MS, 2038.0, 2048.0, 2037.0, 2045.0
            ),  # returns above 2040
        ]
        all_h1 = asia + london
        result = get_session_narrative(all_h1, [], "BEAR", _DAY_MS, _CFG)
        assert result.narrative == "COUNTER_TREND"
        assert result.sweep_direction == "BULL"


# ═══════════════════════════════════════════════════════
# NO_NARRATIVE: double sweep
# ═══════════════════════════════════════════════════════


class TestStage3DoubleSweep:
    def test_both_levels_swept(self):
        """Both Asia high AND low swept in London → NO_NARRATIVE."""
        asia = _asia_bars(asia_high=2060.0, asia_low=2040.0)
        london_start_ms = _DAY_MS + 8 * _HOUR_MS
        london = [
            _h1(london_start_ms, 2050.0, 2065.0, 2035.0, 2050.0),  # sweeps BOTH
            _h1(london_start_ms + _H1_MS, 2050.0, 2055.0, 2045.0, 2050.0),
        ]
        all_h1 = asia + london
        result = get_session_narrative(all_h1, [], "BULL", _DAY_MS, _CFG)
        assert result.narrative == "NO_NARRATIVE"
        assert result.sweep_direction is None


# ═══════════════════════════════════════════════════════
# M15 fallback
# ═══════════════════════════════════════════════════════


class TestStage3M15Fallback:
    def test_h1_no_sweep_m15_detects(self):
        """No H1 sweep but M15 detects sweep high + return → HUNT_PREV_HIGH."""
        asia = _asia_bars(asia_high=2060.0, asia_low=2040.0)
        # London H1: no sweep (stays within range)
        london_start_ms = _DAY_MS + 8 * _HOUR_MS
        london_h1 = [
            _h1(london_start_ms, 2055.0, 2058.0, 2048.0, 2055.0),
            _h1(london_start_ms + _H1_MS, 2055.0, 2059.0, 2050.0, 2056.0),
        ]
        # M15: sweep high on one bar, return on next
        m15_bars = [
            _m15(london_start_ms, 2058.0, 2062.0, 2057.0, 2061.0),  # sweeps high
            _m15(
                london_start_ms + _M15_MS, 2061.0, 2062.0, 2054.0, 2055.0
            ),  # returns below 2060
        ]
        all_h1 = asia + london_h1
        result = get_session_narrative(all_h1, m15_bars, "BEAR", _DAY_MS, _CFG)
        assert result.narrative == "HUNT_PREV_HIGH"
        assert result.sweep_direction == "BEAR"

    def test_m15_sweep_low_returned(self):
        """M15 detects sweep low + return → HUNT_PREV_LOW."""
        asia = _asia_bars(asia_high=2060.0, asia_low=2040.0)
        london_start_ms = _DAY_MS + 8 * _HOUR_MS
        london_h1 = [
            _h1(london_start_ms, 2045.0, 2048.0, 2042.0, 2045.0),
            _h1(london_start_ms + _H1_MS, 2045.0, 2047.0, 2041.0, 2044.0),
        ]
        m15_bars = [
            _m15(london_start_ms, 2042.0, 2043.0, 2038.0, 2039.0),  # sweeps low
            _m15(
                london_start_ms + _M15_MS, 2039.0, 2048.0, 2039.0, 2045.0
            ),  # returns above 2040
        ]
        all_h1 = asia + london_h1
        result = get_session_narrative(all_h1, m15_bars, "BULL", _DAY_MS, _CFG)
        assert result.narrative == "HUNT_PREV_LOW"
        assert result.sweep_direction == "BULL"


# ═══════════════════════════════════════════════════════
# Determinism & config
# ═══════════════════════════════════════════════════════


class TestStage3Determinism:
    def test_same_input_same_output(self):
        asia = _asia_bars()
        london_start_ms = _DAY_MS + 8 * _HOUR_MS
        london = [
            _h1(london_start_ms, 2058.0, 2065.0, 2055.0, 2063.0),
            _h1(london_start_ms + _H1_MS, 2063.0, 2064.0, 2052.0, 2055.0),
        ]
        all_h1 = asia + london
        r1 = get_session_narrative(all_h1, [], "BEAR", _DAY_MS, _CFG)
        r2 = get_session_narrative(all_h1, [], "BEAR", _DAY_MS, _CFG)
        assert r1 == r2


class TestStage3Config:
    def test_custom_asia_min_range(self):
        """Higher min range → tight range gets rejected."""
        asia = _asia_bars(asia_high=2055.0, asia_low=2040.0)  # range=15
        cfg_strict = TdaCascadeConfig(asia_min_range_pts=20.0)
        result = get_session_narrative(asia, [], "BULL", _DAY_MS, cfg_strict)
        assert result.narrative == "NO_NARRATIVE"

    def test_custom_asia_min_bars(self):
        """Higher min bars → rejects with fewer bars."""
        asia = _asia_bars(n=3)
        cfg_strict = TdaCascadeConfig(asia_min_h1_bars=5)
        result = get_session_narrative(asia, [], "BULL", _DAY_MS, cfg_strict)
        assert result.narrative == "NO_NARRATIVE"


# ═══════════════════════════════════════════════════════
# to_wire
# ═══════════════════════════════════════════════════════


class TestStage3ToWire:
    def test_to_wire(self):
        asia = _asia_bars()
        london_start_ms = _DAY_MS + 8 * _HOUR_MS
        london = [_h1(london_start_ms, 2055.0, 2058.0, 2048.0, 2055.0)]
        all_h1 = asia + london
        result = get_session_narrative(all_h1, [], "BULL", _DAY_MS, _CFG)
        wire = result.to_wire()
        assert "narrative" in wire
        assert isinstance(wire["narrative"], str)
