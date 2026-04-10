"""
tests/test_structure_v2.py — ADR-0047: Structure Detection V2 canonical BOS/CHoCH.

Verifies:
  - BOS = continuation (HH break in uptrend, LL break in downtrend)
  - CHoCH = reversal via internal structure (HL break in uptrend, LH break in downtrend)
  - trend_bias=None cold start: first break = BOS
  - confirmation_bars > 1 behaviour
  - Wire format compatibility (kinds unchanged)
"""

import pytest
from core.model.bars import CandleBar
from core.smc.config import SmcStructureConfig
from core.smc.structure import classify_swings, detect_structure_events
from core.smc.types import SmcSwing, make_swing_id

SYM = "XAU/USD"
TF = 3600  # H1


def _bar(
    open_ms: int, o: float, h: float, low: float, c: float, complete: bool = True
) -> CandleBar:
    return CandleBar(
        symbol=SYM,
        tf_s=TF,
        open_time_ms=open_ms,
        close_time_ms=open_ms + TF * 1000,
        o=o,
        h=h,
        low=low,
        c=c,
        v=100.0,
        complete=complete,
        src="test",
    )


def _swing(kind: str, price: float, time_ms: int) -> SmcSwing:
    return SmcSwing(
        id=make_swing_id(kind, SYM, TF, time_ms),
        symbol=SYM,
        tf_s=TF,
        kind=kind,
        price=price,
        time_ms=time_ms,
        confirmed=True,
    )


# ── 1. Cold start: first break = BOS ──────────────────────────────


class TestColdStart:
    def test_first_hh_break_is_bos_bull(self):
        """trend_bias=None, break above HH → BOS_BULL, not CHoCH."""
        swings = [_swing("hh", 2000.0, 1000)]
        bars = [_bar(2000, 1990, 2010, 1985, 2005)]  # c=2005 > 2000
        events, bias, bos_ms, choch_ms = detect_structure_events(swings, bars)
        assert len(events) == 1
        assert events[0].kind == "bos_bull"
        assert bias == "bullish"
        assert bos_ms == 2000
        assert choch_ms is None

    def test_first_ll_break_is_bos_bear(self):
        """trend_bias=None, break below LL → BOS_BEAR."""
        swings = [_swing("ll", 1900.0, 1000)]
        bars = [_bar(2000, 1910, 1920, 1890, 1895)]  # c=1895 < 1900
        events, bias, bos_ms, choch_ms = detect_structure_events(swings, bars)
        assert len(events) == 1
        assert events[0].kind == "bos_bear"
        assert bias == "bearish"

    def test_hl_break_ignored_when_no_trend(self):
        """trend_bias=None, HL break → ignored (no CHoCH without established trend)."""
        swings = [_swing("hl", 1950.0, 1000)]
        bars = [_bar(2000, 1960, 1970, 1940, 1945)]  # c=1945 < 1950 HL
        events, bias, _, _ = detect_structure_events(swings, bars)
        assert len(events) == 0
        assert bias is None

    def test_lh_break_ignored_when_no_trend(self):
        """trend_bias=None, LH break → ignored."""
        swings = [_swing("lh", 1980.0, 1000)]
        bars = [_bar(2000, 1970, 1990, 1960, 1985)]  # c=1985 > 1980 LH
        events, bias, _, _ = detect_structure_events(swings, bars)
        assert len(events) == 0
        assert bias is None


# ── 2. BOS continuation ──────────────────────────────────────────


class TestBOSContinuation:
    def test_bos_bull_in_uptrend(self):
        """Bullish trend established, new HH break → BOS_BULL (continuation)."""
        swings = [
            _swing("hh", 2000.0, 1000),
            _swing("hh", 2050.0, 5000),
        ]
        bars = [
            _bar(2000, 1990, 2010, 1985, 2005),  # break first HH → BOS
            _bar(6000, 2040, 2060, 2030, 2055),  # break second HH → BOS continuation
        ]
        events, bias, bos_ms, choch_ms = detect_structure_events(swings, bars)
        assert len(events) == 2
        assert events[0].kind == "bos_bull"
        assert events[1].kind == "bos_bull"
        assert bias == "bullish"
        assert choch_ms is None

    def test_bos_bear_in_downtrend(self):
        """Bearish trend established, new LL break → BOS_BEAR (continuation)."""
        swings = [
            _swing("ll", 1900.0, 1000),
            _swing("ll", 1850.0, 5000),
        ]
        bars = [
            _bar(2000, 1910, 1920, 1890, 1895),  # break first LL → BOS
            _bar(6000, 1860, 1870, 1840, 1845),  # break second LL → BOS
        ]
        events, bias, bos_ms, choch_ms = detect_structure_events(swings, bars)
        assert len(events) == 2
        assert events[0].kind == "bos_bear"
        assert events[1].kind == "bos_bear"
        assert bias == "bearish"

    def test_bos_bull_consecutive_trending(self):
        """Multiple consecutive BOS in strong uptrend (HH after HH)."""
        swings = [
            _swing("hh", 2000.0, 1000),
            _swing("hh", 2050.0, 4000),
            _swing("hh", 2100.0, 7000),
        ]
        bars = [
            _bar(2000, 1990, 2010, 1985, 2005),
            _bar(5000, 2040, 2060, 2030, 2055),
            _bar(8000, 2090, 2110, 2080, 2105),
        ]
        events, bias, _, _ = detect_structure_events(swings, bars)
        assert len(events) == 3
        assert all(e.kind == "bos_bull" for e in events)
        assert bias == "bullish"


# ── 3. CHoCH reversal via internal structure ─────────────────────


class TestCHoCHReversal:
    def test_choch_bear_via_hl_break(self):
        """Uptrend → break below HL → CHoCH_BEAR (canonical ICT reversal)."""
        swings = [
            _swing("hh", 2050.0, 1000),
            _swing("hl", 2020.0, 3000),  # internal structure low
        ]
        bars = [
            _bar(2000, 2040, 2060, 2030, 2055),  # break HH → BOS_BULL
            _bar(4000, 2025, 2030, 2010, 2015),  # c=2015 < HL(2020) → CHoCH_BEAR
        ]
        events, bias, bos_ms, choch_ms = detect_structure_events(swings, bars)
        assert len(events) == 2
        assert events[0].kind == "bos_bull"
        assert events[1].kind == "choch_bear"
        assert bias == "bearish"
        assert choch_ms == 4000

    def test_choch_bull_via_lh_break(self):
        """Downtrend → break above LH → CHoCH_BULL (canonical ICT reversal)."""
        swings = [
            _swing("ll", 1900.0, 1000),
            _swing("lh", 1940.0, 3000),  # internal structure high
        ]
        bars = [
            _bar(2000, 1910, 1920, 1890, 1895),  # break LL → BOS_BEAR
            _bar(4000, 1935, 1950, 1930, 1945),  # c=1945 > LH(1940) → CHoCH_BULL
        ]
        events, bias, bos_ms, choch_ms = detect_structure_events(swings, bars)
        assert len(events) == 2
        assert events[0].kind == "bos_bear"
        assert events[1].kind == "choch_bull"
        assert bias == "bullish"
        assert choch_ms == 4000

    def test_choch_bear_price_matches_hl(self):
        """CHoCH event price should reference the HL level that was broken."""
        swings = [
            _swing("hh", 2050.0, 1000),
            _swing("hl", 2020.0, 3000),
        ]
        bars = [
            _bar(2000, 2040, 2060, 2030, 2055),
            _bar(4000, 2025, 2030, 2010, 2015),
        ]
        events, _, _, _ = detect_structure_events(swings, bars)
        choch = [e for e in events if e.kind == "choch_bear"]
        assert len(choch) == 1
        assert choch[0].price == 2020.0  # HL price, not LL


# ── 4. Mixed sequence (ranging market) ──────────────────────────


class TestRangingMarket:
    def test_bos_then_choch_then_bos(self):
        """BOS_BULL → CHoCH_BEAR → BOS_BEAR sequence."""
        swings = [
            _swing("hh", 2050.0, 1000),
            _swing("hl", 2020.0, 3000),
            _swing("ll", 1980.0, 6000),
        ]
        bars = [
            _bar(2000, 2040, 2060, 2030, 2055),  # BOS_BULL (break HH@2050)
            _bar(4000, 2025, 2030, 2010, 2015),  # CHoCH_BEAR (break HL@2020)
            _bar(7000, 1985, 1990, 1970, 1975),  # BOS_BEAR (break LL@1980)
        ]
        events, bias, _, _ = detect_structure_events(swings, bars)
        assert len(events) == 3
        assert events[0].kind == "bos_bull"
        assert events[1].kind == "choch_bear"
        assert events[2].kind == "bos_bear"
        assert bias == "bearish"

    def test_no_double_fire_on_consumed_level(self):
        """Once a swing level is consumed (broken), it shouldn't fire again."""
        swings = [_swing("hh", 2050.0, 1000)]
        bars = [
            _bar(2000, 2040, 2060, 2030, 2055),  # break HH → BOS
            _bar(5000, 2040, 2060, 2030, 2055),  # same close > 2050 but HH consumed
        ]
        events, _, _, _ = detect_structure_events(swings, bars)
        assert len(events) == 1  # only one event


# ── 5. Incomplete bars ───────────────────────────────────────────


class TestIncompleteBars:
    def test_incomplete_bar_skipped(self):
        """Incomplete bars should not trigger structure events."""
        swings = [_swing("hh", 2050.0, 1000)]
        bars = [_bar(2000, 2040, 2060, 2030, 2055, complete=False)]
        events, bias, _, _ = detect_structure_events(swings, bars)
        assert len(events) == 0
        assert bias is None


# ── 6. confirmation_bars > 1 ─────────────────────────────────────


class TestConfirmationBars:
    def test_confirmation_bars_1_immediate(self):
        """Default confirmation_bars=1: event fires on first qualifying bar."""
        swings = [_swing("hh", 2050.0, 1000)]
        bars = [_bar(2000, 2040, 2060, 2030, 2055)]
        cfg = SmcStructureConfig(confirmation_bars=1)
        events, _, _, _ = detect_structure_events(swings, bars, config=cfg)
        assert len(events) == 1

    def test_confirmation_bars_2_needs_two(self):
        """confirmation_bars=2: needs two consecutive bar closes beyond level."""
        swings = [_swing("hh", 2050.0, 1000)]
        bars = [
            _bar(2000, 2040, 2060, 2030, 2055),  # first close > 2050
            _bar(5000, 2050, 2065, 2045, 2060),  # second close > 2050 → confirmed
        ]
        cfg = SmcStructureConfig(confirmation_bars=2)
        events, _, _, _ = detect_structure_events(swings, bars, config=cfg)
        assert len(events) == 1
        assert events[0].kind == "bos_bull"
        # Event time should be from the first qualifying bar
        assert events[0].time_ms == 2000

    def test_confirmation_bars_2_interrupted(self):
        """confirmation_bars=2: if second bar doesn't qualify, no event."""
        swings = [_swing("hh", 2050.0, 1000)]
        bars = [
            _bar(2000, 2040, 2060, 2030, 2055),  # close > 2050
            _bar(5000, 2050, 2055, 2040, 2045),  # close < 2050 → reset
        ]
        cfg = SmcStructureConfig(confirmation_bars=2)
        events, _, _, _ = detect_structure_events(swings, bars, config=cfg)
        assert len(events) == 0

    def test_confirmation_bars_3(self):
        """confirmation_bars=3: needs three consecutive."""
        swings = [_swing("hh", 2050.0, 1000)]
        bars = [
            _bar(2000, 2040, 2060, 2030, 2055),
            _bar(5000, 2050, 2065, 2045, 2060),
            _bar(8000, 2055, 2070, 2050, 2065),
        ]
        cfg = SmcStructureConfig(confirmation_bars=3)
        events, _, _, _ = detect_structure_events(swings, bars, config=cfg)
        assert len(events) == 1


# ── 7. Wire format compatibility ─────────────────────────────────


class TestWireFormat:
    def test_event_kinds_are_canonical(self):
        """All event kinds match the canonical set."""
        valid_kinds = {"bos_bull", "bos_bear", "choch_bull", "choch_bear"}
        swings = [
            _swing("hh", 2050.0, 1000),
            _swing("hl", 2020.0, 3000),
        ]
        bars = [
            _bar(2000, 2040, 2060, 2030, 2055),  # BOS_BULL
            _bar(4000, 2025, 2030, 2010, 2015),  # CHoCH_BEAR
        ]
        events, _, _, _ = detect_structure_events(swings, bars)
        for e in events:
            assert e.kind in valid_kinds, f"Unexpected kind: {e.kind}"

    def test_event_has_required_fields(self):
        """Events have all SmcSwing fields."""
        swings = [_swing("hh", 2050.0, 1000)]
        bars = [_bar(2000, 2040, 2060, 2030, 2055)]
        events, _, _, _ = detect_structure_events(swings, bars)
        assert len(events) == 1
        e = events[0]
        assert e.id is not None
        assert e.symbol == SYM
        assert e.tf_s == TF
        assert e.price > 0
        assert e.time_ms > 0
        assert e.confirmed is True


# ── 8. Empty / edge cases ────────────────────────────────────────


class TestEdgeCases:
    def test_empty_swings(self):
        events, bias, _, _ = detect_structure_events(
            [], [_bar(1000, 100, 110, 90, 105)]
        )
        assert events == []
        assert bias is None

    def test_empty_bars(self):
        events, bias, _, _ = detect_structure_events([_swing("hh", 100.0, 1000)], [])
        assert events == []
        assert bias is None

    def test_both_empty(self):
        events, bias, _, _ = detect_structure_events([], [])
        assert events == []

    def test_no_break_no_event(self):
        """Bar close doesn't reach swing level → no event."""
        swings = [_swing("hh", 2050.0, 1000)]
        bars = [_bar(2000, 2040, 2048, 2030, 2045)]  # c=2045 < 2050
        events, _, _, _ = detect_structure_events(swings, bars)
        assert len(events) == 0

    def test_temporal_guard_prevents_same_bar_break(self):
        """Swing at bar.open_time_ms → break checked. Swing AFTER bar → not yet available."""
        swings = [_swing("hh", 2050.0, 3000)]  # swing at t=3000
        bars = [_bar(2000, 2040, 2060, 2030, 2055)]  # bar at t=2000 < swing t=3000
        events, _, _, _ = detect_structure_events(swings, bars)
        # Swing at t=3000 is processed during bar iteration (swing_times[i] <= bar.open_time_ms)
        # bar.open_time_ms=2000 < swing time 3000, so swing NOT yet available
        assert len(events) == 0


# ── 9. Integration with classify_swings ──────────────────────────


class TestIntegrationWithClassify:
    def test_full_pipeline_produces_v2_events(self):
        """Raw swings → classify → detect_structure → V2 BOS/CHoCH."""
        raw = [
            _swing("sh", 2050.0, 1000),  # will be classified as HH (first high)
            _swing("sl", 2020.0, 2000),  # will be LL (first low)
            _swing("sh", 2030.0, 3000),  # < 2050 → LH
            _swing("sl", 2025.0, 4000),  # > 2020 → HL
        ]
        classified = classify_swings(raw)
        kinds = {s.kind for s in classified}
        assert "hh" in kinds
        assert "ll" in kinds

        # Bars that break levels
        bars = [
            _bar(1500, 2040, 2060, 2030, 2055),  # break HH@2050 → BOS_BULL
        ]
        events, bias, _, _ = detect_structure_events(classified, bars)
        assert len(events) == 1
        assert events[0].kind == "bos_bull"
        assert bias == "bullish"


# ── 10. V2 vs V1 semantic difference ─────────────────────────────


class TestV2Semantics:
    def test_v2_choch_is_internal_not_external_break(self):
        """V2 key difference: CHoCH breaks internal structure (HL/LH), not HH/LL.
        In V1 this would be: BOS→CHoCH→CHoCH (trend flipping on each HH/LL break).
        In V2: BOS→CHoCH only when HL or LH is broken.
        """
        swings = [
            _swing("hh", 2050.0, 1000),
            _swing("hl", 2020.0, 2000),
            _swing("ll", 1980.0, 3000),
        ]
        bars = [
            # Break HH in no-trend → BOS_BULL
            _bar(1500, 2040, 2060, 2030, 2055),
            # Break HL in uptrend → CHoCH_BEAR (V2) vs would need LL+bearish bias in V1
            _bar(2500, 2025, 2030, 2010, 2015),
            # Now bearish, break LL → BOS_BEAR continuation
            _bar(4000, 1985, 1990, 1970, 1975),
        ]
        events, bias, _, _ = detect_structure_events(swings, bars)
        assert events[0].kind == "bos_bull"
        assert events[1].kind == "choch_bear"
        assert events[1].price == 2020.0  # HL level, not LL
        assert events[2].kind == "bos_bear"
        assert bias == "bearish"
