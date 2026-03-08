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

def _zone(kind="ob_bear", high=5225.0, low=5144.0, zone_id="z1",
          status="active", tf_s=900, strength=0.8,
          anchor_bar_ms=1000, context_layer=None):
    return SmcZone(
        id=zone_id, symbol="XAU/USD", tf_s=tf_s, kind=kind,
        start_ms=anchor_bar_ms, end_ms=None, high=high, low=low,
        status=status, strength=strength, anchor_bar_ms=anchor_bar_ms,
        context_layer=context_layer,
    )


def _swing(kind="hh", price=5300.0, time_ms=2000, tf_s=900):
    return SmcSwing(
        id="sw_{}".format(time_ms), symbol="XAU/USD", tf_s=tf_s,
        kind=kind, price=price, time_ms=time_ms, confirmed=True,
    )


def _level(kind="pdl", price=5062.0, tf_s=86400):
    return SmcLevel(
        id="lv_{}_{}".format(kind, int(price * 100)),
        symbol="XAU/USD", tf_s=tf_s, kind=kind,
        price=price, time_ms=1000, touches=1,
    )


def _snap(zones=None, swings=None, levels=None, trend_bias=None):
    return SmcSnapshot(
        symbol="XAU/USD", tf_s=900,
        zones=zones or [], swings=swings or [], levels=levels or [],
        trend_bias=trend_bias,
        last_bos_ms=None, last_choch_ms=None,
        computed_at_ms=9999, bar_count=100,
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
        "max_wire_bytes": 600,
    }


def _grade_a6(zone_id="z1"):
    return {zone_id: {"score": 6, "grade": "A", "factors": ["sweep +2", "fvg_after +2", "impulse +1", "pd_align +1"]}}


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

    nb = synthesize_narrative(snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config())

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

    nb = synthesize_narrative(snap, bias, grades, {}, 900, 5060.0, 20.0, _default_config())

    assert nb.mode == "trade"
    assert nb.sub_mode == "aligned"
    assert "BUY" in nb.headline


# ── 3. test_trade_reduced_mixed_htf ──────────────────────

def test_trade_reduced_mixed_htf():
    z = _zone(kind="ob_bear", zone_id="z1")
    snap = _snap(zones=[z], swings=[_swing("choch_bear", 5200.0, 1500)])
    bias = {"86400": "bearish", "14400": "bullish"}
    grades = _grade_a6("z1")

    nb = synthesize_narrative(snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config())

    assert nb.mode == "trade"
    assert nb.sub_mode == "reduced"
    assert "reduced" in nb.headline.lower()


# ── 4. test_wait_no_setup ────────────────────────────────

def test_wait_no_setup():
    z = _zone(kind="ob_bear", zone_id="z1")
    snap = _snap(zones=[z])
    bias = {"86400": "bearish", "14400": "bearish"}
    # Grade C (score 2) — below threshold
    grades = {"z1": {"score": 2, "grade": "C", "factors": []}}

    nb = synthesize_narrative(snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config())

    assert nb.mode == "wait"
    assert nb.scenarios == []
    assert nb.next_area != ""  # should still have next_area


# ── 5. test_wait_no_data ────────────────────────────────

def test_wait_no_data():
    z = _zone(kind="ob_bear", zone_id="z1")
    snap = _snap(zones=[z])
    bias = {}  # no HTF data
    grades = _grade_a6("z1")

    nb = synthesize_narrative(snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config())

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

    nb = synthesize_narrative(snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config())

    assert len(nb.scenarios) == 2


# ── 7. test_trigger_approaching ──────────────────────────

def test_trigger_approaching():
    z = _zone(kind="ob_bear", zone_id="z1", high=5300.0, low=5250.0)
    snap = _snap(zones=[z])  # no structure breaks
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    nb = synthesize_narrative(snap, bias, grades, {}, 900, 5100.0, 20.0, _default_config())

    assert nb.scenarios[0].trigger == "approaching"
    assert "pts from zone" in nb.scenarios[0].trigger_desc


# ── 8. test_trigger_in_zone ──────────────────────────────

def test_trigger_in_zone():
    z = _zone(kind="ob_bear", zone_id="z1", high=5225.0, low=5144.0)
    snap = _snap(zones=[z])  # no CHoCH → in_zone
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    nb = synthesize_narrative(snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config())

    assert nb.scenarios[0].trigger == "in_zone"
    assert "wait" in nb.scenarios[0].trigger_desc.lower()


# ── 9. test_trigger_ready ────────────────────────────────

def test_trigger_ready():
    z = _zone(kind="ob_bear", zone_id="z1", high=5225.0, low=5144.0)
    snap = _snap(
        zones=[z],
        swings=[_swing("choch_bear", 5200.0, 1500)],
    )
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    # Price IN zone + structure aligned → ready
    nb = synthesize_narrative(snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config())

    assert nb.scenarios[0].trigger == "ready"
    assert "confirmed" in nb.scenarios[0].trigger_desc.lower()


# ── 10. test_invalidation_crossed ────────────────────────

def test_invalidation_crossed():
    z = _zone(kind="ob_bear", zone_id="z1", high=5225.0, low=5144.0)
    snap = _snap(zones=[z])
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    # Price above zone.high → invalidated
    nb = synthesize_narrative(snap, bias, grades, {}, 900, 5300.0, 20.0, _default_config())

    assert nb.mode == "wait"  # zone discarded
    assert "scenario_invalidated" in nb.warnings


# ── 11. test_fvg_context_overlap ─────────────────────────

def test_fvg_context_overlap():
    ob = _zone(kind="ob_bear", zone_id="z1", high=5225.0, low=5144.0)
    fvg = _zone(kind="fvg_bear", zone_id="f1", high=5200.0, low=5155.0)
    snap = _snap(zones=[ob, fvg])
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    nb = synthesize_narrative(snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config())

    assert "refined by FVG" in nb.fvg_context


# ── 12. test_fvg_context_empty ───────────────────────────

def test_fvg_context_empty():
    ob = _zone(kind="ob_bear", zone_id="z1", high=5225.0, low=5144.0)
    # FVG far away
    snap = _snap(zones=[ob])
    bias = {"86400": "bearish", "14400": "bearish"}
    grades = _grade_a6("z1")

    nb = synthesize_narrative(snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config())

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

    nb = synthesize_narrative(snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config())

    assert nb.scenarios[0].target_desc is None
    assert "no_target_found" in nb.warnings


# ── 16. test_degraded_fallback ───────────────────────────

def test_degraded_fallback():
    """Trigger exception → fallback NarrativeBlock (N3)."""
    fb = _fallback_narrative_block()
    assert fb.mode == "wait"
    assert "unavailable" in fb.headline.lower()
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

    nb = synthesize_narrative(snap, bias, grades, {}, 900, 5180.0, 20.0, _default_config())
    wire = narrative_to_wire(nb)

    assert isinstance(wire, dict)
    assert wire["mode"] in ("trade", "wait")
    assert isinstance(wire["scenarios"], list)
    assert isinstance(wire["warnings"], list)


# ── Extra: HTF alignment helper ─────────────────────────

def test_htf_alignment_aligned():
    assert _resolve_htf_alignment({"86400": "bearish", "14400": "bearish"}) == ("aligned", "bearish")


def test_htf_alignment_mixed():
    assert _resolve_htf_alignment({"86400": "bearish", "14400": "bullish"}) == ("mixed", None)


def test_htf_alignment_no_data():
    assert _resolve_htf_alignment({}) == ("no_data", None)
    assert _resolve_htf_alignment({"86400": "bearish"}) == ("no_data", None)
