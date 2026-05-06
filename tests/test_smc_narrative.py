"""
tests/test_smc_narrative.py — ADR-0033 Rev 2 test matrix (16 cases).

Covers: mode selection, trigger states, target resolution,
        FVG context, market phase, degraded fallback.
"""

from __future__ import annotations

import pytest

from core.smc.types import (
    ActiveScenario,
    NarrativeBlock,
    SmcLevel,
    SmcSnapshot,
    SmcSwing,
    SmcZone,
)
from core.smc.narrative import (
    synthesize_narrative,
    narrative_to_wire,
    _fallback_narrative_block,
    _resolve_htf_alignment,
    _detect_market_phase,
)

# ── Helpers ──────────────────────────────────────────────


def _zone(
    kind="ob_bear",
    high=5225.0,
    low=5144.0,
    zone_id="z1",
    status="active",
    tf_s=900,
    strength=0.8,
    anchor_bar_ms=1000,
    context_layer=None,
):
    return SmcZone(
        id=zone_id,
        symbol="XAU/USD",
        tf_s=tf_s,
        kind=kind,
        start_ms=anchor_bar_ms,
        end_ms=None,
        high=high,
        low=low,
        status=status,
        strength=strength,
        anchor_bar_ms=anchor_bar_ms,
        context_layer=context_layer,
    )


def _swing(kind="hh", price=5300.0, time_ms=2000, tf_s=900):
    return SmcSwing(
        id="sw_{}".format(time_ms),
        symbol="XAU/USD",
        tf_s=tf_s,
        kind=kind,
        price=price,
        time_ms=time_ms,
        confirmed=True,
    )


def _level(kind="pdl", price=5062.0, tf_s=86400):
    return SmcLevel(
        id="lv_{}_{}".format(kind, int(price * 100)),
        symbol="XAU/USD",
        tf_s=tf_s,
        kind=kind,
        price=price,
        time_ms=1000,
        touches=1,
    )


def _snap(zones=None, swings=None, levels=None, trend_bias=None):
    return SmcSnapshot(
        symbol="XAU/USD",
        tf_s=900,
        zones=zones or [],
        swings=swings or [],
        levels=levels or [],
        trend_bias=trend_bias,
        last_bos_ms=None,
        last_choch_ms=None,
        computed_at_ms=9999,
        bar_count=100,
    )


def _default_config():
    return {
        "trade_min_grade": "A",
        "trade_min_score": 6,
        "max_scenarios": 2,
        "market_phase_enabled": True,
        "phase_hysteresis_bars": 3,
        "fvg_context_enabled": True,
        "target_lookback_bars": 100,
        "trigger_structure_lookback_bars": 5,
        "trigger_proximity_atr": 3.0,
        "trigger_displacement_window": 3,
        "max_wire_bytes": 600,
    }


def _grade_a6(zone_id="z1"):
    return {
        zone_id: {
            "score": 6,
            "grade": "A",
            "factors": ["sweep +2", "fvg_after +2", "impulse +1", "pd_align +1"],
        }
    }


# ── 1. test_trade_aligned_bearish ────────────────────────


def test_trade_aligned_bearish():
    z = _zone(kind="ob_bear", zone_id="z1")
    snap = _snap(
        zones=[z],
        levels=[_level("pdl", 5062.0)],
        swings=[_swing("choch_bear", 5200.0, 1500)],
    )
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config()
    )

    assert nb.mode == "trade"
    assert nb.sub_mode == "aligned"
    assert "SELL" in nb.headline
    assert len(nb.scenarios) >= 1
    assert nb.scenarios[0].direction == "short"
    assert "PDL" in (nb.scenarios[0].target_desc or "")


# ── 2. test_trade_aligned_bullish ────────────────────────


def test_trade_aligned_bullish():
    z = _zone(kind="ob_bull", high=5100.0, low=5050.0, zone_id="z1")
    snap = _snap(
        zones=[z],
        swings=[_swing("choch_bull", 5080.0, 1500)],
        levels=[_level("pdh", 5200.0)],
    )
    bias = {"86400": "bullish", "14400": "bullish"}
    grades = _grade_a6("z1")

    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5060.0, 20.0, _default_config()
    )

    assert nb.mode == "trade"
    assert nb.sub_mode == "aligned"
    assert "BUY" in nb.headline


# ── 3. test_trade_reduced_mixed_htf ──────────────────────


def test_trade_reduced_mixed_htf():
    z = _zone(kind="ob_bear", zone_id="z1")
    snap = _snap(zones=[z], swings=[_swing("choch_bear", 5200.0, 1500)])
    bias = {"86400": "bearish", "14400": "bullish"}
    grades = _grade_a6("z1")

    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config()
    )

    assert nb.mode == "wait"  # D-02: mixed HTF = wait, not trade
    assert nb.sub_mode == "reduced"
    # D-02: wait mode gets generic wait headline (no scenarios built)
    assert "чекаємо" in nb.headline.lower()


# ── 4. test_wait_no_setup ────────────────────────────────


def test_wait_no_setup():
    z = _zone(kind="ob_bear", zone_id="z1")
    snap = _snap(zones=[z])
    bias = {"86400": "bearish", "14400": "bearish"}
    # Grade C (score 2) — below threshold
    grades = {"z1": {"score": 2, "grade": "C", "factors": []}}

    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config()
    )

    assert nb.mode == "wait"
    assert nb.scenarios == []
    assert nb.next_area != ""  # should still have next_area


# ── 5. test_wait_no_data ────────────────────────────────


def test_wait_no_data():
    z = _zone(kind="ob_bear", zone_id="z1")
    snap = _snap(zones=[z])
    bias = {}  # no HTF data
    grades = _grade_a6("z1")

    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config()
    )

    assert nb.mode == "wait"


# ── 6. test_two_scenarios ────────────────────────────────


def test_two_scenarios():
    z1 = _zone(kind="ob_bear", zone_id="z1", high=5225.0, low=5144.0)
    z2 = _zone(kind="ob_bull", zone_id="z2", high=5050.0, low=4980.0)
    snap = _snap(zones=[z1, z2])
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = {
        "z1": {"score": 7, "grade": "A", "factors": []},
        "z2": {"score": 6, "grade": "A", "factors": []},
    }

    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config()
    )

    assert len(nb.scenarios) == 2


# ── 7. test_trigger_approaching ──────────────────────────


def test_trigger_approaching():
    z = _zone(kind="ob_bear", zone_id="z1", high=5300.0, low=5250.0)
    snap = _snap(zones=[z])  # no structure breaks
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    # Price 5170 → distance=80 pts, ATR=20, 80/20=4.0 ATR → within 5.0 guard
    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5170.0, 20.0, _default_config()
    )

    assert nb.scenarios[0].trigger == "approaching"
    assert "пт до зони" in nb.scenarios[0].trigger_desc


# ── 8. test_trigger_in_zone ──────────────────────────────


def test_trigger_in_zone():
    z = _zone(kind="ob_bear", zone_id="z1", high=5225.0, low=5144.0)
    snap = _snap(zones=[z])  # no CHoCH → in_zone
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config()
    )

    assert nb.scenarios[0].trigger == "in_zone"
    assert "чекаємо" in nb.scenarios[0].trigger_desc.lower()


# ── 9. test_trigger_ready ────────────────────────────────


def test_trigger_ready():
    z = _zone(kind="ob_bear", zone_id="z1", high=5225.0, low=5144.0)
    snap = _snap(
        zones=[z],
        swings=[
            _swing("choch_bear", 5200.0, 1500),
            _swing("displacement_bear", 5190.0, 1600),  # institutional impulse
        ],
    )
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    # Price IN zone + CHoCH + displacement → ready
    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config()
    )

    assert nb.scenarios[0].trigger == "ready"
    assert "підтверджені" in nb.scenarios[0].trigger_desc.lower()


# ── 9a. CHoCH + in_zone but NO displacement → in_zone ───


def test_trigger_in_zone_choch_no_displacement():
    """Doctrine: MS Shift needs displacement, not just structure break."""
    z = _zone(kind="ob_bear", zone_id="z1", high=5225.0, low=5144.0)
    snap = _snap(
        zones=[z],
        swings=[_swing("choch_bear", 5200.0, 1500)],  # no displacement
    )
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config()
    )

    assert nb.scenarios[0].trigger == "in_zone"
    assert "choch" in nb.scenarios[0].trigger_desc.lower()


def test_trigger_approaching_far_with_choch():
    """CHoCH + displacement but price > 3 ATR from zone → approaching."""
    z = _zone(kind="ob_bear", zone_id="z1", high=5300.0, low=5250.0)
    snap = _snap(
        zones=[z],
        swings=[
            _swing("choch_bear", 5280.0, 1500),
            _swing("displacement_bear", 5270.0, 1600),
        ],
    )
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    # 5170 vs zone.low=5250 → 80 pts, ATR=20 → 4.0 ATR (within 5.0 guard, > 3.0 trigger_proximity)
    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5170.0, 20.0, _default_config()
    )

    assert nb.scenarios[0].trigger == "approaching"


# ── 9c. CHoCH + displacement + proximate → triggered ────


def test_trigger_triggered_proximate_with_displacement():
    """CHoCH + displacement + close to zone but NOT in zone → triggered."""
    z = _zone(kind="ob_bear", zone_id="z1", high=5225.0, low=5200.0)
    snap = _snap(
        zones=[z],
        swings=[
            _swing("choch_bear", 5210.0, 1500),
            _swing("displacement_bear", 5205.0, 1600),
        ],
    )
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    # 5185 vs zone.low=5200 → 15 pts, ATR=20 → 0.75 ATR (< 3.0)
    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5185.0, 20.0, _default_config()
    )

    assert nb.scenarios[0].trigger == "triggered"
    assert "імпульс" in nb.scenarios[0].trigger_desc.lower()


# ── 10. test_invalidation_crossed ────────────────────────


def test_invalidation_crossed():
    z = _zone(kind="ob_bear", zone_id="z1", high=5225.0, low=5144.0)
    snap = _snap(zones=[z])
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    # Price above zone.high → invalidated
    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5300.0, 20.0, _default_config()
    )

    assert nb.mode == "wait"  # zone discarded
    assert "scenario_invalidated" in nb.warnings


# ── 11. test_fvg_context_overlap ─────────────────────────


def test_fvg_context_overlap():
    ob = _zone(kind="ob_bear", zone_id="z1", high=5225.0, low=5144.0)
    fvg = _zone(kind="fvg_bear", zone_id="f1", high=5200.0, low=5155.0)
    snap = _snap(zones=[ob, fvg])
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config()
    )

    assert "уточнений" in nb.fvg_context or "FVG" in nb.fvg_context


# ── 12. test_fvg_context_empty ───────────────────────────


def test_fvg_context_empty():
    ob = _zone(kind="ob_bear", zone_id="z1", high=5225.0, low=5144.0)
    # FVG far away
    snap = _snap(zones=[ob])
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config()
    )

    assert nb.fvg_context == ""


# ── 13. test_market_phase_trending_down ──────────────────


def test_market_phase_trending_down():
    swings = [
        _swing("lh", 5200.0, 1000),
        _swing("ll", 5050.0, 2000),
        _swing("lh", 5150.0, 3000),
        _swing("ll", 5000.0, 4000),
    ]
    snap = _snap(swings=swings)
    bias = {"86400": "bearish", "14400": "bearish"}

    nb = synthesize_narrative(snap, bias, {}, {}, 900, 5100.0, 20.0, _default_config())

    assert nb.market_phase == "trending_down"


# ── 14. test_market_phase_ranging ────────────────────────


def test_market_phase_ranging():
    swings = [_swing("hh", 5200.0, 1000)]  # only 1 → ranging
    snap = _snap(swings=swings)
    bias = {"86400": "bearish", "14400": "bearish"}

    nb = synthesize_narrative(snap, bias, {}, {}, 900, 5100.0, 20.0, _default_config())

    assert nb.market_phase == "ranging"


# ── 15. test_target_none_fallback ────────────────────────


def test_target_none_fallback():
    z = _zone(kind="ob_bear", zone_id="z1")
    # No levels, no institutional zones, no swings
    snap = _snap(zones=[z])
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config()
    )

    assert nb.scenarios[0].target_desc is None
    assert "no_target_found" in nb.warnings


# ── 16. test_degraded_fallback ───────────────────────────


def test_degraded_fallback():
    """Trigger exception → fallback NarrativeBlock (N3)."""
    fb = _fallback_narrative_block()
    assert fb.mode == "wait"
    assert "недоступний" in fb.headline.lower()
    assert "computation_error" in fb.warnings

    # Also verify synthesize_narrative catches exceptions
    # Pass invalid snapshot type to trigger error
    nb = synthesize_narrative(None, {}, {}, {}, 900, 0.0, 0.0, {})  # type: ignore
    assert nb.mode == "wait"
    assert "computation_error" in nb.warnings


# ── Extra: wire serialization (BH-5) ────────────────────


def test_narrative_to_wire():
    z = _zone(kind="ob_bear", zone_id="z1")
    snap = _snap(
        zones=[z],
        levels=[_level("pdl", 5062.0)],
        swings=[_swing("choch_bear", 5200.0, 1500)],
    )
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    nb = synthesize_narrative(
        snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config()
    )
    wire = narrative_to_wire(nb)

    assert isinstance(wire, dict)
    assert wire["mode"] in ("trade", "wait")
    assert isinstance(wire["scenarios"], list)
    assert isinstance(wire["warnings"], list)


# ── Extra: HTF alignment helper ─────────────────────────


def test_htf_alignment_aligned():
    assert _resolve_htf_alignment({"86400": "bearish", "14400": "bearish"}) == (
        "aligned",
        "bearish",
    )


def test_htf_alignment_mixed():
    assert _resolve_htf_alignment({"86400": "bearish", "14400": "bullish"}) == (
        "mixed",
        None,
    )


def test_htf_alignment_no_data():
    assert _resolve_htf_alignment({}) == ("no_data", None)


def test_htf_alignment_partial_d1_only():
    """Partial: D1 bias present but no H4 → partial alignment, not no_data."""
    assert _resolve_htf_alignment({"86400": "bearish"}) == ("partial", "bearish")


def test_htf_alignment_partial_h4_only():
    """Partial: H4 bias present but no D1 → partial alignment."""
    assert _resolve_htf_alignment({"14400": "bullish"}) == ("partial", "bullish")


# ── P2: Target directional gate ─────────────────────────


def test_target_short_skips_level_above_price():
    """P2: PDL above current price must be skipped for short target."""
    from core.smc.narrative import _find_target_key_level

    pdl_above = _level("pdl", 5100.0)
    pdl_below = _level("pdl", 4900.0)
    # Only pdl_below should match — it's ahead of price for short
    result = _find_target_key_level(
        [pdl_above, pdl_below], "short", current_price=5000.0
    )
    assert result is not None
    assert result.price == 4900.0


def test_target_long_skips_level_below_price():
    """P2: PDH below current price must be skipped for long target."""
    from core.smc.narrative import _find_target_key_level

    pdh_below = _level("pdh", 4900.0)
    pdh_above = _level("pdh", 5100.0)
    result = _find_target_key_level(
        [pdh_below, pdh_above], "long", current_price=5000.0
    )
    assert result is not None
    assert result.price == 5100.0


def test_target_swing_respects_direction():
    """P2: target swing must be ahead of price."""
    from core.smc.narrative import _find_target_swing

    sw_above = _swing("ll", 5100.0, 3000)
    sw_below = _swing("ll", 4900.0, 2000)
    result = _find_target_swing([sw_above, sw_below], "short", current_price=5000.0)
    assert result is not None
    assert result.price == 4900.0


# ── P3: Market phase hysteresis ──────────────────────────


def test_market_phase_hysteresis_blocks_flicker():
    """P3: mixed [hh, ll, hh, hl] with hysteresis=3 → ranging (not enough consecutive)."""
    swings = [
        _swing("hh", 100, 1),
        _swing("ll", 90, 2),
        _swing("hh", 110, 3),
        _swing("hl", 95, 4),
    ]
    cfg = {"market_phase_enabled": True, "phase_hysteresis_bars": 3}
    assert _detect_market_phase(swings, cfg) == "ranging"


def test_market_phase_hysteresis_passes_with_consecutive():
    """P3: 3 consecutive bullish swings [hh, hl, hh] → trending_up."""
    swings = [
        _swing("ll", 80, 1),
        _swing("hh", 110, 2),
        _swing("hl", 95, 3),
        _swing("hh", 120, 4),
    ]
    cfg = {"market_phase_enabled": True, "phase_hysteresis_bars": 3}
    assert _detect_market_phase(swings, cfg) == "trending_up"


def test_market_phase_hysteresis_uses_config():
    """P3: hysteresis=2 is less strict — [hh, hl] in last 2 → trending_up."""
    swings = [_swing("ll", 80, 1), _swing("hh", 110, 2), _swing("hl", 95, 3)]
    cfg = {"market_phase_enabled": True, "phase_hysteresis_bars": 2}
    assert _detect_market_phase(swings, cfg) == "trending_up"


# ── P6: HTF-first candidate ranking ─────────────────────


def test_htf_aligned_zone_wins_over_higher_score_counter():
    """P6: HTF-aligned zone (lower raw score) beats counter-trend zone (higher raw score)."""
    from core.smc.narrative import _select_candidate_zones

    # ob_bear = short zone; HTF bearish = aligned
    z_aligned = _zone(kind="ob_bear", high=5200, low=5150, zone_id="z_aligned")
    # ob_bull = long zone; HTF bearish = counter-trend
    z_counter = _zone(kind="ob_bull", high=5100, low=5050, zone_id="z_counter")

    grades = {
        "z_aligned": {"score": 6, "grade": "A", "factors": []},
        "z_counter": {"score": 8, "grade": "A+", "factors": []},
    }
    result = _select_candidate_zones(
        [z_aligned, z_counter],
        grades,
        5180.0,
        "A",
        6,
        [],
        alignment="aligned",
        htf_direction="bearish",
    )
    assert len(result) == 2
    # z_aligned should be first despite lower raw score (6+3 bonus > 8)
    assert result[0].id == "z_aligned"


def test_htf_ranking_no_bonus_when_mixed():
    """P6: no alignment bonus when HTF is mixed."""
    from core.smc.narrative import _select_candidate_zones

    z1 = _zone(kind="ob_bear", high=5200, low=5150, zone_id="z1")
    z2 = _zone(kind="ob_bull", high=5100, low=5050, zone_id="z2")
    grades = {
        "z1": {"score": 6, "grade": "A", "factors": []},
        "z2": {"score": 8, "grade": "A+", "factors": []},
    }
    result = _select_candidate_zones(
        [z1, z2],
        grades,
        5120.0,
        "A",
        6,
        [],
        alignment="mixed",
        htf_direction=None,
    )
    # z2 wins by raw score when no alignment bonus
    assert result[0].id == "z2"


# ── P5B: FVG candidate gate tests ──


def test_fvg_candidate_blocked_by_high_threshold():
    """P5B: FVG zone with score 5 blocked when fvg_trade_min_score=99 (display only)."""
    from core.smc.narrative import _select_candidate_zones

    z_ob = _zone(kind="ob_bull", high=5100, low=5050, zone_id="ob1")
    z_fvg = _zone(kind="fvg_bull", high=5200, low=5150, zone_id="fvg1")
    grades = {
        "ob1": {"score": 7, "grade": "A", "factors": []},
        "fvg1": {"score": 5, "grade": "A", "factors": []},
    }
    result = _select_candidate_zones(
        [z_ob, z_fvg],
        grades,
        5120.0,
        "A",
        6,
        [],
        config={"fvg_trade_min_score": 99},
    )
    # FVG blocked by threshold, only OB remains
    assert len(result) == 1
    assert result[0].id == "ob1"


def test_fvg_candidate_allowed_when_threshold_lowered():
    """P5B: FVG zone passes when fvg_trade_min_score lowered to 4."""
    from core.smc.narrative import _select_candidate_zones

    z_fvg = _zone(kind="fvg_bull", high=5200, low=5150, zone_id="fvg1")
    grades = {
        "fvg1": {"score": 5, "grade": "A", "factors": []},
    }
    result = _select_candidate_zones(
        [z_fvg],
        grades,
        5120.0,
        "A",
        6,
        [],
        config={"fvg_trade_min_score": 4},
    )
    assert len(result) == 1
    assert result[0].id == "fvg1"


# ── D2: trade_max_distance_atr guard ─────────────────────


class TestTooFarGuard:
    """Price >N ATR from zone → mode=wait, sub_mode=too_far."""

    def _cfg(self, **overrides):
        c = _default_config()
        c.update(overrides)
        return c

    def test_too_far_bearish_zone(self):
        """Price far below bear OB → wait/too_far."""
        z = _zone(kind="ob_bear", high=5300.0, low=5250.0, zone_id="z1")
        snap = _snap(zones=[z], swings=[_swing("choch_bear", 5200.0, 1500)])
        bias = {"86400": "bearish", "14400": "bearish"}
        grades = _grade_a6("z1")
        # Distance = 5250 - 5000 = 250 pts.  ATR = 20.  250/20 = 12.5 ATR → too far
        nb = synthesize_narrative(
            snap, bias, grades, {}, 900, 5000.0, 20.0, self._cfg()
        )
        assert nb.mode == "wait"
        assert nb.sub_mode == "too_far"
        assert "zone_too_far" in nb.warnings
        assert "далеко" in nb.headline.lower()

    def test_too_far_bullish_zone(self):
        """Price far above bull OB → wait/too_far."""
        z = _zone(kind="ob_bull", high=5100.0, low=5050.0, zone_id="z1")
        snap = _snap(zones=[z], swings=[_swing("choch_bull", 5080.0, 1500)])
        bias = {"86400": "bullish", "14400": "bullish"}
        grades = _grade_a6("z1")
        # Distance = 5400 - 5100 = 300 pts.  ATR = 20.  300/20 = 15 → too far
        nb = synthesize_narrative(
            snap, bias, grades, {}, 900, 5400.0, 20.0, self._cfg()
        )
        assert nb.mode == "wait"
        assert nb.sub_mode == "too_far"
        assert "zone_too_far" in nb.warnings

    def test_close_to_zone_still_trade(self):
        """Price within 5 ATR → normal trade/aligned."""
        z = _zone(kind="ob_bear", high=5225.0, low=5144.0, zone_id="z1")
        snap = _snap(zones=[z], swings=[_swing("choch_bear", 5200.0, 1500)])
        bias = {"86400": "bearish", "14400": "bearish"}
        grades = _grade_a6("z1")
        # Distance = 5144 - 5060 = 84 pts.  ATR = 20.  84/20 = 4.2 → within 5.0
        nb = synthesize_narrative(
            snap, bias, grades, {}, 900, 5060.0, 20.0, self._cfg()
        )
        assert nb.mode == "trade"
        assert nb.sub_mode == "aligned"
        assert "zone_too_far" not in nb.warnings

    def test_inside_zone_never_too_far(self):
        """Price inside zone → distance=0 → trade."""
        z = _zone(kind="ob_bear", high=5225.0, low=5144.0, zone_id="z1")
        snap = _snap(zones=[z], swings=[_swing("choch_bear", 5200.0, 1500)])
        bias = {"86400": "bearish", "14400": "bearish"}
        grades = _grade_a6("z1")
        nb = synthesize_narrative(
            snap, bias, grades, {}, 900, 5180.0, 20.0, self._cfg()
        )
        assert nb.mode == "trade"
        assert "zone_too_far" not in nb.warnings

    def test_atr_zero_permissive(self):
        """ATR=0 guard: don't crash, don't activate too_far."""
        z = _zone(kind="ob_bear", high=5225.0, low=5144.0, zone_id="z1")
        snap = _snap(zones=[z])
        bias = {"86400": "bearish", "14400": "bearish"}
        grades = _grade_a6("z1")
        nb = synthesize_narrative(snap, bias, grades, {}, 900, 4000.0, 0.0, self._cfg())
        # atr=0 → guard skipped → should not crash
        assert nb is not None
        assert "zone_too_far" not in nb.warnings

    def test_custom_threshold(self):
        """trade_max_distance_atr=2.0 → tighter guard."""
        z = _zone(kind="ob_bear", high=5225.0, low=5144.0, zone_id="z1")
        snap = _snap(zones=[z], swings=[_swing("choch_bear", 5200.0, 1500)])
        bias = {"86400": "bearish", "14400": "bearish"}
        grades = _grade_a6("z1")
        # Distance = 5144 - 5060 = 84 pts.  ATR = 20.  84/20 = 4.2 → >2.0 → too far
        nb = synthesize_narrative(
            snap,
            bias,
            grades,
            {},
            900,
            5060.0,
            20.0,
            self._cfg(trade_max_distance_atr=2.0),
        )
        assert nb.mode == "wait"
        assert nb.sub_mode == "too_far"


# ── BH-5: NarrativeBlock.to_wire() instance method ─────────


class TestNarrativeBlockToWire:
    """`/api/v3/narrative/snapshot` calls `block.to_wire()` (instance method).
    Pre-fix: AttributeError 500 (method missing). Post-fix: must match the
    output of `narrative_to_wire(block)` byte-for-byte."""

    def _empty_block(self):
        return NarrativeBlock(
            mode="wait",
            sub_mode="",
            headline="empty",
            bias_summary="",
            scenarios=[],
            next_area="",
            fvg_context="",
            market_phase="ranging",
            warnings=[],
        )

    def _populated_block(self):
        sc = ActiveScenario(
            zone_id="z1",
            direction="long",
            entry_desc="OB▲ A 5144-5225",
            trigger="approaching",
            trigger_desc="approaching: 15 pts from zone",
            target_desc="PDL 5062",
            invalidation="Above 5230",
        )
        return NarrativeBlock(
            mode="trade",
            sub_mode="aligned",
            headline="🟢 BUY setup ready",
            bias_summary="HTF bullish aligned",
            scenarios=[sc],
            next_area="5144 BUY OB (A/6)",
            fvg_context="FVG bull 5100-5120",
            market_phase="trending_up",
            warnings=[],
            current_session="london",
            in_killzone=True,
            session_context="London KZ active — high probability",
            range_exhaustion_summary="Day exhausted 0.85",
        )

    def test_method_exists(self):
        """Pre-fix this raised AttributeError → 500 in /narrative/snapshot."""
        nb = self._empty_block()
        wire = nb.to_wire()
        assert isinstance(wire, dict)

    def test_method_matches_module_function_empty(self):
        """`block.to_wire()` === `narrative_to_wire(block)` byte-for-byte."""
        nb = self._empty_block()
        assert nb.to_wire() == narrative_to_wire(nb)

    def test_method_matches_module_function_populated(self):
        nb = self._populated_block()
        assert nb.to_wire() == narrative_to_wire(nb)

    def test_range_exhaustion_null_compact(self):
        """ADR-0053: range_exhaustion_summary excluded when empty (matches
        SmcSnapshot.to_wire() pattern)."""
        nb = self._empty_block()
        wire = nb.to_wire()
        assert "range_exhaustion_summary" not in wire

        nb_pop = self._populated_block()
        wire_pop = nb_pop.to_wire()
        assert wire_pop["range_exhaustion_summary"] == "Day exhausted 0.85"

    def test_scenario_to_wire_method(self):
        """ActiveScenario.to_wire() — also instance method, used inside
        NarrativeBlock.to_wire() scenarios serialization."""
        sc = ActiveScenario(
            zone_id="z1",
            direction="short",
            entry_desc="e",
            trigger="ready",
            trigger_desc="td",
            target_desc=None,
            invalidation="inv",
        )
        wire = sc.to_wire()
        assert wire == {
            "zone_id": "z1",
            "direction": "short",
            "entry_desc": "e",
            "trigger": "ready",
            "trigger_desc": "td",
            "target_desc": None,
            "invalidation": "inv",
        }

    def test_scenario_with_target(self):
        """target_desc is Optional[str] — None case + present case."""
        sc_with = ActiveScenario("z1", "long", "e", "approaching", "td", "PDL 5062", "inv")
        sc_none = ActiveScenario("z2", "long", "e", "approaching", "td", None, "inv")
        assert sc_with.to_wire()["target_desc"] == "PDL 5062"
        assert sc_none.to_wire()["target_desc"] is None

    def test_warnings_serialize_as_list(self):
        """`warnings` field — defensive `list(...)` preserves contents but
        also handles tuple inputs (legacy paths)."""
        nb = NarrativeBlock(
            mode="wait", sub_mode="", headline="", bias_summary="",
            scenarios=[], next_area="", fvg_context="", market_phase="ranging",
            warnings=["no_target_found", "computation_error"],
        )
        wire = nb.to_wire()
        assert wire["warnings"] == ["no_target_found", "computation_error"]
        assert isinstance(wire["warnings"], list)
