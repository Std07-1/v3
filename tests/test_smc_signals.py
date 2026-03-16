"""Tests for core.smc.signals — ADR-0039 Signal Engine.

Covers: entry/SL/TP resolution, R:R, confidence, lifecycle, alerts.
"""

from typing import List, Optional

from core.smc.types import (
    ActiveScenario,
    NarrativeBlock,
    SignalSpec,
    SmcLevel,
    SmcSnapshot,
    SmcSwing,
    SmcZone,
)
from core.smc.signals import (
    _resolve_entry,
    _resolve_stop_loss,
    _resolve_take_profit,
    _calc_risk_reward,
    _calc_confidence,
    _determine_state,
    synthesize_signals,
)


# ── Fixtures ──────────────────────────────────────────────────────


def _zone(
    zone_id="ob_bull_XAU_USD_900_100000",
    high=2870.0,
    low=2860.0,
    kind="ob_bull",
    status="active",
) -> SmcZone:
    return SmcZone(
        id=zone_id,
        symbol="XAU/USD",
        tf_s=900,
        kind=kind,
        start_ms=100000,
        end_ms=None,
        high=high,
        low=low,
        status=status,
        strength=0.8,
        anchor_bar_ms=100000,
    )


def _swing(kind="hh", price=2880.0, time_ms=200000) -> SmcSwing:
    return SmcSwing(
        id=f"{kind}_XAU_USD_900_{time_ms}",
        symbol="XAU/USD",
        tf_s=900,
        kind=kind,
        price=price,
        time_ms=time_ms,
        confirmed=True,
    )


def _level(kind="pdh", price=2890.0) -> SmcLevel:
    return SmcLevel(
        id=f"{kind}_XAU_USD_900_{int(price*100)}",
        symbol="XAU/USD",
        tf_s=900,
        kind=kind,
        price=price,
        time_ms=150000,
        touches=2,
    )


def _snapshot(
    zones: Optional[List[SmcZone]] = None,
    swings: Optional[List[SmcSwing]] = None,
    levels: Optional[List[SmcLevel]] = None,
) -> SmcSnapshot:
    return SmcSnapshot(
        symbol="XAU/USD",
        tf_s=900,
        trend_bias="bullish",
        zones=zones if zones is not None else [_zone()],
        swings=(
            swings
            if swings is not None
            else [_swing("hh", 2880.0), _swing("ll", 2845.0)]
        ),
        levels=(
            levels
            if levels is not None
            else [_level("pdh", 2890.0), _level("pdl", 2840.0)]
        ),
        last_bos_ms=None,
        last_choch_ms=None,
        computed_at_ms=300000,
        bar_count=100,
    )


def _scenario(
    zone_id="ob_bull_XAU_USD_900_100000",
    direction="long",
    trigger="approaching",
) -> ActiveScenario:
    return ActiveScenario(
        zone_id=zone_id,
        direction=direction,
        entry_desc="OB▲ A(6) 2860–2870",
        trigger=trigger,
        trigger_desc="Approaching zone",
        target_desc="PDH 2890",
        invalidation="Below 2855",
    )


def _narrative(
    scenarios: Optional[List[ActiveScenario]] = None,
    mode: str = "trade",
) -> NarrativeBlock:
    return NarrativeBlock(
        mode=mode,
        sub_mode="aligned",
        headline="▲ BUY setup approaching",
        bias_summary="D1 bullish, H4 bullish",
        scenarios=scenarios if scenarios is not None else [_scenario()],
        next_area="2860–2870 OB (A/6)",
        fvg_context="",
        market_phase="trending_up",
        warnings=[],
        current_session="london",
        in_killzone=True,
        session_context="London KZ active",
    )


CFG = {
    "enabled": True,
    "entry_method": "ote",
    "sl_buffer_atr": 0.2,
    "tp_atr_multiplier": 2.0,
    "approach_atr_mult": 1.5,
    "signal_ttl_bars": 50,
    "max_active_signals": 3,
    "min_risk_reward": 1.5,
    "confidence_weights": {
        "bias_alignment": 0.30,
        "structure": 0.25,
        "confluence_grade": 0.20,
        "session": 0.15,
        "momentum": 0.10,
    },
}


# ── Entry resolution tests ────────────────────────────────────────


class TestResolveEntry:
    def test_ote_long(self):
        z = _zone(high=2870.0, low=2860.0)
        price, desc = _resolve_entry(z, "long", "ote", atr=5.0)
        # OTE long = high - 0.618 * range = 2870 - 6.18 = 2863.82
        assert abs(price - 2863.82) < 0.01
        assert "OTE" in desc

    def test_ote_short(self):
        z = _zone(high=2870.0, low=2860.0)
        price, desc = _resolve_entry(z, "short", "ote", atr=5.0)
        # OTE short = low + 0.618 * range = 2860 + 6.18 = 2866.18
        assert abs(price - 2866.18) < 0.01
        assert "OTE" in desc

    def test_zone_edge_long(self):
        z = _zone(high=2870.0, low=2860.0)
        price, _ = _resolve_entry(z, "long", "zone_edge", atr=5.0)
        assert price == 2860.0

    def test_zone_edge_short(self):
        z = _zone(high=2870.0, low=2860.0)
        price, _ = _resolve_entry(z, "short", "zone_edge", atr=5.0)
        assert price == 2870.0

    def test_zone_mid(self):
        z = _zone(high=2870.0, low=2860.0)
        price, _ = _resolve_entry(z, "long", "zone_mid", atr=5.0)
        assert price == 2865.0

    def test_thin_zone_falls_back_to_edge(self):
        """Zone smaller than 0.5*ATR → edge entry instead of OTE."""
        z = _zone(high=2862.0, low=2860.0)  # 2pt zone vs 5pt ATR
        price, desc = _resolve_entry(z, "long", "ote", atr=5.0)
        assert price == 2860.0  # edge, not OTE
        assert "краю" in desc


# ── SL / TP tests ─────────────────────────────────────────────────


class TestStopLoss:
    def test_sl_long(self):
        z = _zone(low=2860.0)  # zone range=10, 10*0.25=2.5 > 5*0.03=0.15
        sl = _resolve_stop_loss(z, "long", atr=5.0, buffer_atr=0.2)
        assert sl == 2860.0 - 2.5  # max(10*0.25, 5*0.03) = 2.5

    def test_sl_short(self):
        z = _zone(high=2870.0)  # zone range=10, buf=2.5
        sl = _resolve_stop_loss(z, "short", atr=5.0, buffer_atr=0.2)
        assert sl == 2870.0 + 2.5


class TestTakeProfit:
    def test_tp_key_level(self):
        """Key level found → use it."""
        snap = _snapshot(levels=[_level("pdh", 2890.0)])
        z = _zone()
        tp, fallback = _resolve_take_profit(snap, z, "long", 2858.0, 5.0, 2863.82, 2.0)
        assert tp == 2890.0
        assert fallback is False

    def test_tp_swing_fallback(self):
        """No key level → swing."""
        snap = _snapshot(levels=[], swings=[_swing("hh", 2880.0)])
        z = _zone()
        tp, fallback = _resolve_take_profit(snap, z, "long", 2858.0, 5.0, 2863.82, 2.0)
        assert tp == 2880.0
        assert fallback is False

    def test_tp_atr_fallback(self):
        """No key level, no swing → ATR."""
        snap = _snapshot(levels=[], swings=[])
        z = _zone()
        tp, fallback = _resolve_take_profit(snap, z, "long", 2858.0, 5.0, 2863.82, 2.0)
        assert abs(tp - (2863.82 + 10.0)) < 0.01
        assert fallback is True

    def test_tp_short_key_level(self):
        """Short TP uses PDL below current price."""
        snap = _snapshot(levels=[_level("pdl", 2840.0)])
        z = _zone()
        tp, _ = _resolve_take_profit(snap, z, "short", 2872.0, 5.0, 2866.18, 2.0)
        assert tp == 2840.0


# ── R:R tests ─────────────────────────────────────────────────────


class TestRiskReward:
    def test_long_rr(self):
        rr = _calc_risk_reward(entry=2864.0, sl=2859.0, tp=2890.0, direction="long")
        # risk=5, reward=26 → R:R=5.2
        assert rr == 5.2

    def test_short_rr(self):
        rr = _calc_risk_reward(entry=2866.0, sl=2871.0, tp=2840.0, direction="short")
        # risk=5, reward=26 → R:R=5.2
        assert rr == 5.2

    def test_zero_risk(self):
        rr = _calc_risk_reward(entry=2860.0, sl=2860.0, tp=2880.0, direction="long")
        assert rr == 0.0


# ── Confidence tests ──────────────────────────────────────────────


class TestConfidence:
    def test_max_confidence(self):
        """All factors max → 100."""
        bias_map = {"86400": "bullish", "14400": "bullish"}
        gi = {"score": 8}
        snap = _snapshot(swings=[_swing("bos", 2880.0), _swing("choch", 2870.0)])
        momentum = {"900": {"b": 3, "r": 0}}
        weights = CFG["confidence_weights"]
        score, factors = _calc_confidence(
            bias_map, "long", gi, snap, ("london", True), momentum, weights
        )
        assert score == 100
        assert factors["bias_alignment"] == 100
        assert factors["session"] == 100

    def test_zero_confidence(self):
        """All factors zero → 0."""
        score, _ = _calc_confidence(
            {}, "long", {}, _snapshot(swings=[]), None, {}, CFG["confidence_weights"]
        )
        assert score == 0

    def test_partial_alignment(self):
        """Only D1 aligned → 50."""
        bias_map = {"86400": "bullish"}
        _, factors = _calc_confidence(
            bias_map,
            "long",
            {},
            _snapshot(swings=[]),
            None,
            {},
            CFG["confidence_weights"],
        )
        assert factors["bias_alignment"] == 50


# ── State machine tests ──────────────────────────────────────────


class TestDetermineState:
    def test_pending(self):
        z = _zone()
        state, _ = _determine_state(
            "", 2862.0, z, 2863.82, 2855.0, 2890.0, "long", 5.0, 1.5
        )
        assert state == "pending"

    def test_approaching(self):
        z = _zone()
        state, _ = _determine_state(
            "approaching", 2862.0, z, 2863.82, 2855.0, 2890.0, "long", 5.0, 1.5
        )
        assert state == "approaching"

    def test_active(self):
        z = _zone()
        state, _ = _determine_state(
            "in_zone", 2865.0, z, 2863.82, 2859.0, 2890.0, "long", 5.0, 1.5
        )
        assert state == "active"

    def test_ready(self):
        z = _zone()
        state, _ = _determine_state(
            "ready", 2865.0, z, 2863.82, 2859.0, 2890.0, "long", 5.0, 1.5
        )
        assert state == "ready"

    def test_invalidated_long(self):
        z = _zone()
        state, _ = _determine_state(
            "active", 2850.0, z, 2863.82, 2859.0, 2890.0, "long", 5.0, 1.5
        )
        assert state == "invalidated"

    def test_completed_long(self):
        z = _zone()
        state, _ = _determine_state(
            "active", 2891.0, z, 2863.82, 2859.0, 2890.0, "long", 5.0, 1.5
        )
        assert state == "completed"


# ── Integration: synthesize_signals ───────────────────────────────


class TestSynthesizeSignals:

    def test_basic_long_signal(self):
        narr = _narrative()
        snap = _snapshot()
        grades = {"ob_bull_XAU_USD_900_100000": {"grade": "A", "score": 6}}
        bias = {"86400": "bullish", "14400": "bullish"}
        mom = {"900": {"b": 2, "r": 0}}

        sigs, alerts = synthesize_signals(
            narrative=narr,
            snapshot=snap,
            zone_grades=grades,
            bias_map=bias,
            momentum_map=mom,
            current_price=2862.0,
            atr=5.0,
            config=CFG,
            previous_signals=[],
            now_ms=300000,
            session_info=("london", True),
        )

        assert len(sigs) == 1
        sig = sigs[0]
        assert sig.symbol == "XAU/USD"
        assert sig.direction == "long"
        assert sig.entry_price > 0
        assert sig.stop_loss < sig.entry_price
        assert sig.take_profit > sig.entry_price
        assert sig.risk_reward > 0
        assert sig.confidence > 0
        assert sig.state == "approaching"
        assert sig.grade == "A"
        assert sig.in_killzone is True
        assert sig.session == "london"
        assert sig.bars_alive == 0

    def test_signal_with_previous(self):
        """Lifecycle continuity — bars_alive increments, created_ms preserved."""
        prev_sig = SignalSpec(
            signal_id="sig_prev",
            zone_id="ob_bull_XAU_USD_900_100000",
            symbol="XAU/USD",
            tf_s=900,
            direction="long",
            entry_price=2863.82,
            stop_loss=2859.0,
            take_profit=2890.0,
            risk_reward=5.2,
            entry_method="ote",
            entry_desc="OTE 2860–2870",
            confidence=80,
            confidence_factors={},
            grade="A",
            state="approaching",
            state_reason="Zone approaching",
            created_ms=100000,
            updated_ms=200000,
            bars_alive=3,
            session="london",
            in_killzone=True,
            warnings=[],
        )
        narr = _narrative()
        snap = _snapshot()
        grades = {"ob_bull_XAU_USD_900_100000": {"grade": "A", "score": 6}}

        sigs, _ = synthesize_signals(
            narrative=narr,
            snapshot=snap,
            zone_grades=grades,
            bias_map={"86400": "bullish", "14400": "bullish"},
            momentum_map={"900": {"b": 2, "r": 0}},
            current_price=2862.0,
            atr=5.0,
            config=CFG,
            previous_signals=[prev_sig],
            now_ms=300000,
            session_info=("london", True),
        )

        assert len(sigs) == 1
        assert sigs[0].created_ms == 100000  # preserved
        assert sigs[0].bars_alive == 4  # incremented
        assert sigs[0].signal_id == "sig_prev"  # preserved

    def test_terminal_state_preserved(self):
        """Once invalidated, stays invalidated."""
        prev_sig = SignalSpec(
            signal_id="sig_done",
            zone_id="ob_bull_XAU_USD_900_100000",
            symbol="XAU/USD",
            tf_s=900,
            direction="long",
            entry_price=2863.82,
            stop_loss=2859.0,
            take_profit=2890.0,
            risk_reward=5.2,
            entry_method="ote",
            entry_desc="test",
            confidence=80,
            confidence_factors={},
            grade="A",
            state="invalidated",
            state_reason="Broke SL",
            created_ms=100000,
            updated_ms=200000,
            bars_alive=10,
            session="",
            in_killzone=False,
            warnings=[],
        )
        narr = _narrative()
        snap = _snapshot()
        grades = {"ob_bull_XAU_USD_900_100000": {"grade": "A", "score": 6}}

        sigs, _ = synthesize_signals(
            narrative=narr,
            snapshot=snap,
            zone_grades=grades,
            bias_map={},
            momentum_map={},
            current_price=2870.0,
            atr=5.0,
            config=CFG,
            previous_signals=[prev_sig],
            now_ms=300000,
        )

        assert sigs[0].state == "invalidated"

    def test_ttl_expiry(self):
        """Signal exceeds TTL → expired."""
        prev_sig = SignalSpec(
            signal_id="sig_old",
            zone_id="ob_bull_XAU_USD_900_100000",
            symbol="XAU/USD",
            tf_s=900,
            direction="long",
            entry_price=2863.82,
            stop_loss=2859.0,
            take_profit=2890.0,
            risk_reward=5.2,
            entry_method="ote",
            entry_desc="OTE",
            confidence=70,
            confidence_factors={},
            grade="A",
            state="pending",
            state_reason="Zone pending",
            created_ms=100000,
            updated_ms=200000,
            bars_alive=50,  # TTL = 50
            session="",
            in_killzone=False,
            warnings=[],
        )
        narr = _narrative()
        snap = _snapshot()
        grades = {"ob_bull_XAU_USD_900_100000": {"grade": "A", "score": 6}}

        sigs, _ = synthesize_signals(
            narrative=narr,
            snapshot=snap,
            zone_grades=grades,
            bias_map={},
            momentum_map={},
            current_price=2858.0,
            atr=5.0,
            config=CFG,
            previous_signals=[prev_sig],
            now_ms=300000,
        )

        assert sigs[0].state == "expired"

    def test_alert_on_state_change(self):
        """Alert generated when state changes."""
        narr = _narrative(scenarios=[_scenario(trigger="ready")])
        snap = _snapshot()
        grades = {"ob_bull_XAU_USD_900_100000": {"grade": "A+", "score": 9}}

        sigs, alerts = synthesize_signals(
            narrative=narr,
            snapshot=snap,
            zone_grades=grades,
            bias_map={"86400": "bullish", "14400": "bullish"},
            momentum_map={},
            current_price=2865.0,
            atr=5.0,
            config=CFG,
            previous_signals=[],
            now_ms=300000,
            session_info=("london", True),
        )

        assert sigs[0].state == "ready"
        assert len(alerts) == 1
        assert alerts[0].alert_type == "ready"
        assert alerts[0].priority == "high"

    def test_no_alert_same_state(self):
        """No alert if state unchanged."""
        prev_sig = SignalSpec(
            signal_id="sig_same",
            zone_id="ob_bull_XAU_USD_900_100000",
            symbol="XAU/USD",
            tf_s=900,
            direction="long",
            entry_price=2863.82,
            stop_loss=2855.0,
            take_profit=2890.0,
            risk_reward=5.2,
            entry_method="ote",
            entry_desc="OTE",
            confidence=70,
            confidence_factors={},
            grade="A",
            state="approaching",
            state_reason="zone approaching",
            created_ms=100000,
            updated_ms=200000,
            bars_alive=5,
            session="",
            in_killzone=False,
            warnings=[],
        )
        narr = _narrative(scenarios=[_scenario(trigger="approaching")])
        snap = _snapshot()
        grades = {"ob_bull_XAU_USD_900_100000": {"grade": "A", "score": 6}}

        _, alerts = synthesize_signals(
            narrative=narr,
            snapshot=snap,
            zone_grades=grades,
            bias_map={},
            momentum_map={},
            current_price=2862.0,
            atr=5.0,
            config=CFG,
            previous_signals=[prev_sig],
            now_ms=300000,
        )
        assert len(alerts) == 0

    def test_low_rr_warning(self):
        """When R:R < min → warning."""
        # Create zone where R:R will be small (no targets → fallback)
        snap = _snapshot(levels=[], swings=[], zones=[_zone(high=2862.0, low=2860.0)])
        narr = _narrative(scenarios=[_scenario()])
        grades = {"ob_bull_XAU_USD_900_100000": {"grade": "C", "score": 2}}

        sigs, _ = synthesize_signals(
            narrative=narr,
            snapshot=snap,
            zone_grades=grades,
            bias_map={},
            momentum_map={},
            current_price=2862.0,
            atr=5.0,
            config=CFG,
            previous_signals=[],
            now_ms=300000,
        )

        assert len(sigs) == 1
        # No target found → ATR fallback warning
        assert "no_target_found" in sigs[0].warnings

    def test_to_wire_roundtrip(self):
        """SignalSpec.to_wire() produces valid dict."""
        narr = _narrative()
        snap = _snapshot()
        grades = {"ob_bull_XAU_USD_900_100000": {"grade": "A", "score": 6}}

        sigs, _ = synthesize_signals(
            narrative=narr,
            snapshot=snap,
            zone_grades=grades,
            bias_map={"86400": "bullish"},
            momentum_map={},
            current_price=2858.0,
            atr=5.0,
            config=CFG,
            previous_signals=[],
            now_ms=300000,
        )

        wire = sigs[0].to_wire()
        assert isinstance(wire, dict)
        assert "signal_id" in wire
        assert "entry_price" in wire
        assert "risk_reward" in wire
        assert "confidence" in wire
        assert "state" in wire
        assert isinstance(wire["entry_price"], float)

    def test_empty_scenarios(self):
        """No scenarios → no signals."""
        narr = _narrative(scenarios=[])
        snap = _snapshot()
        sigs, alerts = synthesize_signals(
            narrative=narr,
            snapshot=snap,
            zone_grades={},
            bias_map={},
            momentum_map={},
            current_price=2858.0,
            atr=5.0,
            config=CFG,
            previous_signals=[],
            now_ms=300000,
        )
        assert sigs == []
        assert alerts == []

    def test_wait_mode_still_generates_signals(self):
        """Signals generated even in wait mode (scenario still present)."""
        narr = _narrative(mode="wait")
        snap = _snapshot()
        grades = {"ob_bull_XAU_USD_900_100000": {"grade": "B", "score": 4}}

        sigs, _ = synthesize_signals(
            narrative=narr,
            snapshot=snap,
            zone_grades=grades,
            bias_map={},
            momentum_map={},
            current_price=2858.0,
            atr=5.0,
            config=CFG,
            previous_signals=[],
            now_ms=300000,
        )

        # Scenarios present → signals generated (UI decides whether to show)
        assert len(sigs) == 1

    def test_max_active_signals_cap(self):
        """Only max_active_signals scenario processed."""
        scenarios = [
            _scenario(zone_id=f"ob_bull_XAU_USD_900_{i}00000") for i in range(5)
        ]
        zones = [_zone(zone_id=f"ob_bull_XAU_USD_900_{i}00000") for i in range(5)]
        narr = _narrative(scenarios=scenarios)
        snap = _snapshot(zones=zones)
        grades = {z.id: {"grade": "A", "score": 6} for z in zones}

        sigs, _ = synthesize_signals(
            narrative=narr,
            snapshot=snap,
            zone_grades=grades,
            bias_map={},
            momentum_map={},
            current_price=2858.0,
            atr=5.0,
            config={**CFG, "max_active_signals": 2},
            previous_signals=[],
            now_ms=300000,
        )

        assert len(sigs) == 2
