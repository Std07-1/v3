"""Tests for core.smc.tda.stage5_trade_mgmt — Config F trade management."""

import unittest

from core.model.bars import CandleBar
from core.smc.tda.types import (
    FvgEntry,
    TradeState,
    TdaCascadeConfig,
    initial_trade_state,
)
from core.smc.tda.stage5_trade_mgmt import update_trade

_M15_S = 900
_M15_MS = _M15_S * 1000
_BASE_MS = 1_704_100_000_000  # Arbitrary start for entry bar
_CFG = TdaCascadeConfig()


def _m15(idx: int, o: float, h: float, low: float, c: float) -> CandleBar:
    """M15 bar at offset idx from entry."""
    ms = _BASE_MS + (idx + 1) * _M15_MS
    return CandleBar(
        symbol="XAU/USD",
        tf_s=_M15_S,
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


def _long_entry(ep: float = 2060.0, sl: float = 2050.0, tp: float = 2090.0) -> FvgEntry:
    """LONG entry: risk=10 pts, RR=3.0."""
    return FvgEntry(
        entry_price=ep,
        stop_loss=sl,
        take_profit=tp,
        risk_reward=3.0,
        direction="LONG",
        fvg_high=2063.0,
        fvg_low=2058.0,
        fvg_size=5.0,
        entry_bar_ms=_BASE_MS,
    )


def _short_entry(
    ep: float = 2060.0, sl: float = 2070.0, tp: float = 2030.0
) -> FvgEntry:
    """SHORT entry: risk=10 pts, RR=3.0."""
    return FvgEntry(
        entry_price=ep,
        stop_loss=sl,
        take_profit=tp,
        risk_reward=3.0,
        direction="SHORT",
        fvg_high=2063.0,
        fvg_low=2058.0,
        fvg_size=5.0,
        entry_bar_ms=_BASE_MS,
    )


# ═══════════════════════════════════════════════════════
# Basic state progression
# ═══════════════════════════════════════════════════════


class TestTradeBasic(unittest.TestCase):
    """Basic open → update → still open."""

    def test_first_bar_no_trigger(self):
        """Neutral bar → state remains open."""
        entry = _long_entry()
        state = initial_trade_state(entry)
        bar = _m15(0, 2060, 2063, 2058, 2061)  # MFE=3, no trigger

        new = update_trade(state, bar, entry, _CFG)
        assert new.status == "open"
        assert new.bars_elapsed == 1
        assert new.max_favorable == 3.0
        assert new.outcome == ""

    def test_already_closed(self):
        """Closed state → no change."""
        entry = _long_entry()
        closed = TradeState(
            status="closed",
            partial_closed=False,
            partial_r=0.0,
            max_favorable=5.0,
            trail_sl=2050.0,
            bars_elapsed=3,
            outcome="LOSS",
            net_r=-1.0,
        )
        bar = _m15(3, 2060, 2095, 2040, 2080)  # extreme bar
        result = update_trade(closed, bar, entry, _CFG)
        assert result is closed  # exact same object


# ═══════════════════════════════════════════════════════
# LOSS: original SL hit before partial
# ═══════════════════════════════════════════════════════


class TestTradeLoss(unittest.TestCase):
    def test_long_sl_hit(self):
        """LONG: bar.low ≤ SL → LOSS -1.0R."""
        entry = _long_entry()  # SL=2050
        state = initial_trade_state(entry)
        bar = _m15(0, 2058, 2062, 2049, 2055)  # low=2049 ≤ 2050

        new = update_trade(state, bar, entry, _CFG)
        assert new.status == "closed"
        assert new.outcome == "LOSS"
        assert new.net_r == -1.0

    def test_short_sl_hit(self):
        """SHORT: bar.h ≥ SL → LOSS -1.0R."""
        entry = _short_entry()  # SL=2070
        state = initial_trade_state(entry)
        bar = _m15(0, 2062, 2071, 2059, 2065)  # h=2071 ≥ 2070

        new = update_trade(state, bar, entry, _CFG)
        assert new.status == "closed"
        assert new.outcome == "LOSS"
        assert new.net_r == -1.0


# ═══════════════════════════════════════════════════════
# Partial close at 1R
# ═══════════════════════════════════════════════════════


class TestTradePartial(unittest.TestCase):
    def test_partial_close_long(self):
        """LONG: MFE reaches 1R (10 pts) → partial triggered."""
        entry = _long_entry()  # ep=2060, risk=10
        state = initial_trade_state(entry)
        bar = _m15(0, 2060, 2072, 2061, 2071)  # h=2072, MFE=12, low>trail

        new = update_trade(state, bar, entry, _CFG)
        assert new.status == "partial"
        assert new.partial_closed is True
        assert new.partial_r == 0.5  # 0.5 × 1.0R
        assert new.trail_sl == 2060.0  # breakeven

    def test_partial_close_short(self):
        """SHORT: MFE reaches 1R → partial triggered."""
        entry = _short_entry()  # ep=2060, risk=10
        state = initial_trade_state(entry)
        bar = _m15(0, 2059, 2059, 2048, 2049)  # low=2048, MFE=12, h<trail

        new = update_trade(state, bar, entry, _CFG)
        assert new.status == "partial"
        assert new.partial_closed is True
        assert new.partial_r == 0.5
        assert new.trail_sl == 2060.0


# ═══════════════════════════════════════════════════════
# Trail SL movement
# ═══════════════════════════════════════════════════════


class TestTradeTrail(unittest.TestCase):
    def test_trail_update_long(self):
        """After partial, MFE ≥ 2R → trail_sl moves."""
        entry = _long_entry()  # ep=2060, risk=10
        # State: partial already closed, trail_sl at breakeven
        state = TradeState(
            status="partial",
            partial_closed=True,
            partial_r=0.5,
            max_favorable=10.0,
            trail_sl=2060.0,
            bars_elapsed=5,
            outcome="",
            net_r=0.0,
        )
        bar = _m15(5, 2078, 2082, 2076, 2080)  # h=2082, MFE=22 ≥ 20 (2R)

        new = update_trade(state, bar, entry, _CFG)
        assert new.status == "partial"
        # trail_sl = ep + (max_fav - 1R) = 2060 + (22 - 10) = 2072
        assert new.trail_sl == 2072.0

    def test_trail_never_loosens(self):
        """Trail SL only tightens (for LONG: only goes up)."""
        entry = _long_entry()
        state = TradeState(
            status="partial",
            partial_closed=True,
            partial_r=0.5,
            max_favorable=25.0,
            trail_sl=2075.0,
            bars_elapsed=10,
            outcome="",
            net_r=0.0,
        )
        # Bar with lower MFE than previous max — trail shouldn't loosen
        bar = _m15(10, 2078, 2079, 2076, 2078)  # h=2079, new MFE=25 (same)

        new = update_trade(state, bar, entry, _CFG)
        assert new.trail_sl >= 2075.0  # never goes below


# ═══════════════════════════════════════════════════════
# Trail SL hit → PARTIAL_WIN or BE
# ═══════════════════════════════════════════════════════


class TestTradeTrailHit(unittest.TestCase):
    def test_trail_hit_partial_win(self):
        """Trail SL hit above entry → PARTIAL_WIN with positive net R."""
        entry = _long_entry()  # ep=2060, risk=10
        state = TradeState(
            status="partial",
            partial_closed=True,
            partial_r=0.5,
            max_favorable=25.0,
            trail_sl=2075.0,
            bars_elapsed=15,
            outcome="",
            net_r=0.0,
        )
        bar = _m15(15, 2076, 2078, 2074, 2075)  # low=2074 ≤ trail=2075

        new = update_trade(state, bar, entry, _CFG)
        assert new.status == "closed"
        assert new.outcome == "PARTIAL_WIN"
        # trail_captured = (2075 - 2060) / 10 = 1.5
        # trail_r = 0.5 × 1.5 = 0.75
        # net = 0.5 + 0.75 = 1.25
        assert abs(new.net_r - 1.25) < 0.001

    def test_trail_hit_breakeven(self):
        """Trail SL hit at entry level → BE, net_r=0.0."""
        entry = _long_entry()  # ep=2060, risk=10
        state = TradeState(
            status="partial",
            partial_closed=True,
            partial_r=0.5,
            max_favorable=15.0,
            trail_sl=2060.0,
            bars_elapsed=8,
            outcome="",
            net_r=0.0,
        )
        bar = _m15(8, 2062, 2064, 2059, 2061)  # low=2059 ≤ trail=2060

        new = update_trade(state, bar, entry, _CFG)
        assert new.status == "closed"
        # trail_captured = (2060-2060)/10 = 0, trail_r = 0
        # net = 0.5 + 0 = 0.5 → NOT BE (0.5 > 0.01)
        assert new.outcome == "PARTIAL_WIN"
        assert abs(new.net_r - 0.5) < 0.001

    def test_trail_hit_true_be(self):
        """Trail with near-zero captured → BE when net ≤ 0.01."""
        entry = _long_entry()  # ep=2060, risk=10
        # Partial not enabled, but simulating with partial_r=0.0
        # Using a scenario where partial_r rounds to ≈ 0
        cfg = TdaCascadeConfig(partial_tp_pct=0.01)  # tiny partial
        state = TradeState(
            status="partial",
            partial_closed=True,
            partial_r=0.01,  # 0.01 × 1.0 = 0.01R
            max_favorable=15.0,
            trail_sl=2060.0,
            bars_elapsed=5,
            outcome="",
            net_r=0.0,
        )
        bar = _m15(5, 2062, 2064, 2059, 2061)  # hits trail at 2060

        new = update_trade(state, bar, entry, cfg)
        # captured = 0, trail_r = 0.99 * 0 = 0, net = 0.01 ≤ 0.01 → BE
        assert new.outcome == "BE"
        assert new.net_r == 0.0

    def test_short_trail_hit(self):
        """SHORT: trail SL hit → PARTIAL_WIN."""
        entry = _short_entry()  # ep=2060, risk=10
        state = TradeState(
            status="partial",
            partial_closed=True,
            partial_r=0.5,
            max_favorable=25.0,
            trail_sl=2045.0,
            bars_elapsed=15,
            outcome="",
            net_r=0.0,
        )
        # trail_sl for SHORT = ep - (mfav - 1R) = 2060 - (25-10) = 2045
        bar = _m15(15, 2044, 2046, 2043, 2044)  # h=2046 ≥ trail=2045

        new = update_trade(state, bar, entry, _CFG)
        assert new.status == "closed"
        assert new.outcome == "PARTIAL_WIN"
        # captured = (2060 - 2045) / 10 = 1.5, trail_r = 0.5×1.5 = 0.75
        assert abs(new.net_r - 1.25) < 0.001


# ═══════════════════════════════════════════════════════
# WIN: TP hit
# ═══════════════════════════════════════════════════════


class TestTradeWin(unittest.TestCase):
    def test_win_full_no_partial(self):
        """TP hit before partial → WIN 3.0R (full exit)."""
        entry = _long_entry()  # TP=2090
        cfg = TdaCascadeConfig(partial_tp_enabled=False)
        state = initial_trade_state(entry)
        bar = _m15(0, 2080, 2091, 2078, 2089)  # h=2091 ≥ 2090

        new = update_trade(state, bar, entry, cfg)
        assert new.outcome == "WIN"
        assert new.net_r == 3.0

    def test_win_with_partial(self):
        """TP hit after partial → WIN 2.0R (0.5 + 1.5)."""
        entry = _long_entry()  # TP=2090
        state = TradeState(
            status="partial",
            partial_closed=True,
            partial_r=0.5,
            max_favorable=28.0,
            trail_sl=2078.0,
            bars_elapsed=20,
            outcome="",
            net_r=0.0,
        )
        bar = _m15(20, 2088, 2091, 2086, 2090)  # h=2091 ≥ TP=2090

        new = update_trade(state, bar, entry, _CFG)
        assert new.outcome == "WIN"
        assert abs(new.net_r - 2.0) < 0.001  # 0.5 + 0.5×3.0 = 2.0

    def test_short_tp_hit(self):
        """SHORT: TP hit → WIN."""
        entry = _short_entry()  # TP=2030
        state = initial_trade_state(entry)
        bar = _m15(0, 2035, 2038, 2029, 2031)  # low=2029 ≤ TP=2030

        cfg = TdaCascadeConfig(partial_tp_enabled=False)
        new = update_trade(state, bar, entry, cfg)
        assert new.outcome == "WIN"
        assert new.net_r == 3.0


# ═══════════════════════════════════════════════════════
# TIMEOUT
# ═══════════════════════════════════════════════════════


class TestTradeTimeout(unittest.TestCase):
    def test_timeout_no_partial(self):
        """Max bars reached, no partial → TIMEOUT, 0.0R."""
        entry = _long_entry()
        state = TradeState(
            status="open",
            partial_closed=False,
            partial_r=0.0,
            max_favorable=5.0,
            trail_sl=2050.0,
            bars_elapsed=95,
            outcome="",
            net_r=0.0,
        )
        bar = _m15(95, 2062, 2064, 2058, 2061)  # neutral, no SL/TP hit

        new = update_trade(state, bar, entry, _CFG)
        assert new.status == "closed"
        assert new.outcome == "TIMEOUT"
        assert new.net_r == 0.0
        assert new.bars_elapsed == 96

    def test_timeout_with_partial(self):
        """Max bars reached after partial → TIMEOUT with unrealized."""
        entry = _long_entry()  # ep=2060, risk=10
        state = TradeState(
            status="partial",
            partial_closed=True,
            partial_r=0.5,
            max_favorable=15.0,
            trail_sl=2065.0,
            bars_elapsed=95,
            outcome="",
            net_r=0.0,
        )
        bar = _m15(95, 2072, 2074, 2070, 2073)  # c=2073, no SL/TP hit

        new = update_trade(state, bar, entry, _CFG)
        assert new.status == "closed"
        assert new.outcome == "TIMEOUT"
        # unrealized = 0.5 × max((2073-2060)/10, 0) = 0.5 × 1.3 = 0.65
        # net = 0.5 + 0.65 = 1.15
        assert abs(new.net_r - 1.15) < 0.001


# ═══════════════════════════════════════════════════════
# Edge cases: same bar triggers partial + trail SL
# ═══════════════════════════════════════════════════════


class TestTradeEdge(unittest.TestCase):
    def test_same_bar_partial_and_trail_hit(self):
        """Bar hits 1R (partial) but also retraces to entry = trail hit."""
        entry = _long_entry()  # ep=2060, SL=2050, risk=10
        state = initial_trade_state(entry)
        # Bar goes up to 2071 (MFE=11 ≥ 1R=10) then back to 2058 (< trail=ep=2060)
        bar = _m15(0, 2060, 2071, 2058, 2059)

        new = update_trade(state, bar, entry, _CFG)
        assert new.status == "closed"
        # Partial triggers first (MFE=11 ≥ 10), trail_sl=2060
        # Then trail SL check: low=2058 ≤ trail=2060 → hit
        # captured = (2060-2060)/10 = 0, trail_r = 0
        # net = 0.5 + 0 = 0.5 → PARTIAL_WIN
        assert new.outcome == "PARTIAL_WIN"
        assert abs(new.net_r - 0.5) < 0.001

    def test_same_bar_mfe_and_sl_long(self):
        """Bar sweeps 1R+ AND original SL on same bar → partial triggers, not LOSS."""
        entry = _long_entry()  # ep=2060, SL=2050, risk=10
        state = initial_trade_state(entry)
        # Bar goes to 2072 (MFE=12 ≥ 10) then crashes to 2048 (below SL)
        bar = _m15(0, 2060, 2072, 2048, 2055)

        new = update_trade(state, bar, entry, _CFG)
        # Partial triggered (MFE=12 ≥ 10), trail_sl=2060
        # Original SL check skipped (partial_closed = True)
        # Trail SL check: low=2048 ≤ trail=2060 → hit
        # captured = 0, trail_r = 0, net = 0.5 → PARTIAL_WIN
        assert new.outcome == "PARTIAL_WIN"
        assert abs(new.net_r - 0.5) < 0.001

    def test_priority_sl_over_tp(self):
        """SL hit takes priority over TP on same bar (before partial)."""
        entry = _long_entry()  # SL=2050, TP=2090
        state = initial_trade_state(entry)
        cfg = TdaCascadeConfig(partial_tp_enabled=False)
        # Bar sweeps: low=2049 ≤ SL=2050 AND h=2091 ≥ TP=2090
        bar = _m15(0, 2060, 2091, 2049, 2070)

        new = update_trade(state, bar, entry, cfg)
        # SL checked before TP → LOSS wins
        assert new.outcome == "LOSS"
        assert new.net_r == -1.0


# ═══════════════════════════════════════════════════════
# Determinism
# ═══════════════════════════════════════════════════════


class TestTradeDeterminism(unittest.TestCase):
    def test_same_input_same_output(self):
        entry = _long_entry()
        state = initial_trade_state(entry)
        bar = _m15(0, 2060, 2072, 2058, 2070)

        r1 = update_trade(state, bar, entry, _CFG)
        r2 = update_trade(state, bar, entry, _CFG)
        assert r1 == r2


# ═══════════════════════════════════════════════════════
# Config overrides
# ═══════════════════════════════════════════════════════


class TestTradeConfig(unittest.TestCase):
    def test_partial_disabled(self):
        """partial_tp_enabled=False → no partial, direct SL or TP."""
        cfg = TdaCascadeConfig(partial_tp_enabled=False)
        entry = _long_entry()
        state = initial_trade_state(entry)
        bar = _m15(0, 2060, 2072, 2058, 2070)  # MFE=12 but no partial

        new = update_trade(state, bar, entry, cfg)
        assert new.status == "open"  # No partial triggered
        assert new.partial_closed is False

    def test_custom_max_bars(self):
        """Custom max_open_bars_m15 = 10 → earlier timeout."""
        cfg = TdaCascadeConfig(max_open_bars_m15=10)
        entry = _long_entry()
        state = TradeState(
            status="open",
            partial_closed=False,
            partial_r=0.0,
            max_favorable=5.0,
            trail_sl=2050.0,
            bars_elapsed=9,
            outcome="",
            net_r=0.0,
        )
        bar = _m15(9, 2062, 2064, 2058, 2061)

        new = update_trade(state, bar, entry, cfg)
        assert new.outcome == "TIMEOUT"
        assert new.bars_elapsed == 10


# ═══════════════════════════════════════════════════════
# to_wire
# ═══════════════════════════════════════════════════════


class TestTradeToWire(unittest.TestCase):
    def test_to_wire(self):
        entry = _long_entry()
        state = initial_trade_state(entry)
        bar = _m15(0, 2060, 2072, 2058, 2070)
        new = update_trade(state, bar, entry, _CFG)

        wire = new.to_wire()
        assert "status" in wire
        assert "partial_closed" in wire
        assert "partial_r" in wire
        assert "max_favorable_pts" in wire
        assert "trail_sl" in wire
        assert "bars_elapsed" in wire
        assert "outcome" in wire
        assert "net_r" in wire


if __name__ == "__main__":
    unittest.main()
