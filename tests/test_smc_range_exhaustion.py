"""Tests for ADR-0053 Range Exhaustion Detector (P2)."""

from __future__ import annotations

import pytest

from core.model.bars import CandleBar
from core.smc.config import SmcRangeExhaustionConfig
from core.smc.range_exhaustion import (
    _classify_phase,
    compute_range_exhaustion,
)


def _bar(o, h, low, c, open_time_ms=0, tf_s=86400):
    return CandleBar(
        symbol="XAUUSD",
        tf_s=tf_s,
        open_time_ms=open_time_ms,
        close_time_ms=open_time_ms + tf_s * 1000,
        o=o,
        h=h,
        low=low,
        c=c,
        v=1000.0,
        complete=True,
        src="test",
    )


def _d1_bars(n=14, rng=40.0):
    """N daily bars with h-low = rng → compute_atr (h-low TR) returns rng."""
    return [
        _bar(4600, 4600 + rng, 4600, 4620, open_time_ms=i * 86400_000, tf_s=86400)
        for i in range(n)
    ]


def _h1_bars(n=14, rng=6.0):
    return [
        _bar(4600, 4600 + rng, 4600, 4604, open_time_ms=i * 3600_000, tf_s=3600)
        for i in range(n)
    ]


# ── 1. Phase classification (parameterized boundaries) ─────────────────────


@pytest.mark.parametrize(
    "mult,expected",
    [
        (0.00, "early"),
        (0.20, "early"),
        (0.34, "early"),
        (0.35, "mid"),  # boundary: early_max exclusive
        (0.50, "mid"),
        (0.69, "mid"),
        (0.70, "late"),  # boundary: mid_max exclusive
        (0.99, "late"),
        (1.00, "exhausted"),  # boundary: late_max exclusive
        (1.30, "exhausted"),
        (2.50, "exhausted"),
    ],
)
def test_classify_phase_boundaries(mult, expected):
    cfg = SmcRangeExhaustionConfig()
    assert _classify_phase(mult, cfg) == expected


# ── 2. ADR TL;DR scenario (XAU/USD, D1 open=4612, price=4640.5, ATR=38.2) ──


def test_tldr_scenario_late_phase():
    cfg = SmcRangeExhaustionConfig()
    bars_d1 = _d1_bars(n=14, rng=38.2)  # ATR(14) = 38.2 on simplified TR
    snap = compute_range_exhaustion(
        symbol="XAUUSD",
        current_price=4640.5,
        bars_d1=bars_d1,
        bars_h1=_h1_bars(),
        anchors={"d1_open": (1713916800000, 4612.0)},
        active_session="asia",
        now_ms=1714000000000,
        cfg=cfg,
    )
    st = snap.primary
    assert st.anchor_kind == "d1_open"
    assert st.traveled_dir == 1
    assert abs(st.traveled_abs - 28.5) < 0.01
    assert abs(st.atr_baseline - 38.2) < 0.01
    assert abs(st.traveled_mult - 0.7461) < 0.001
    assert st.phase == "late"
    assert st.confidence_delta == -0.15
    assert st.degraded == []


# ── 3. Exhausted phase → capped remaining_budget + max negative delta ──────


def test_exhausted_phase_remaining_budget_capped():
    cfg = SmcRangeExhaustionConfig()  # exhaustion_cap=1.5
    bars_d1 = _d1_bars(n=14, rng=40.0)
    # Price 2.0 ATR above anchor → mult=2.0, remaining = max(0, 1.5-2.0) = 0
    snap = compute_range_exhaustion(
        symbol="XAUUSD",
        current_price=4612.0 + 80.0,
        bars_d1=bars_d1,
        bars_h1=_h1_bars(),
        anchors={"d1_open": (0, 4612.0)},
        cfg=cfg,
    )
    assert snap.primary.phase == "exhausted"
    assert snap.primary.confidence_delta == -0.30
    assert snap.primary.remaining_budget == 0.0  # floor at 0


# ── 4. Degraded-but-loud (I5) — explicit markers, not silent zero ──────────


def test_degraded_no_bars_marks_explicit_signal():
    cfg = SmcRangeExhaustionConfig()
    snap = compute_range_exhaustion(
        symbol="XAUUSD",
        current_price=4640.0,
        bars_d1=[],  # no bars → no ATR baseline
        bars_h1=[],
        anchors={"d1_open": (0, 4612.0)},
        cfg=cfg,
    )
    assert "no_bars_for_baseline" in snap.primary.degraded


def test_degraded_no_anchors_fallback_snapshot():
    cfg = SmcRangeExhaustionConfig()
    snap = compute_range_exhaustion(
        symbol="XAUUSD",
        current_price=4640.0,
        bars_d1=_d1_bars(),
        bars_h1=_h1_bars(),
        anchors={},  # no anchors at all
        cfg=cfg,
    )
    assert "no_anchors_available" in snap.primary.degraded
    assert snap.primary.traveled_mult == 0.0  # fallback: anchor_price == current_price


# ── 5. Multi-anchor: primary selection by active_session ────────────────────


def test_multi_anchor_primary_follows_session():
    cfg = SmcRangeExhaustionConfig()
    bars_d1 = _d1_bars(n=14, rng=40.0)
    bars_h1 = _h1_bars(n=14, rng=6.0)
    anchors = {
        "d1_open": (0, 4612.0),
        "london_open": (3600_000, 4615.0),
        "ny_open": (7200_000, 4618.0),
    }
    snap_l = compute_range_exhaustion(
        symbol="XAUUSD",
        current_price=4620.0,
        bars_d1=bars_d1,
        bars_h1=bars_h1,
        anchors=anchors,
        active_session="london",
        cfg=cfg,
    )
    assert snap_l.primary.anchor_kind == "london_open"
    assert len(snap_l.by_anchor) == 3

    snap_ny = compute_range_exhaustion(
        symbol="XAUUSD",
        current_price=4620.0,
        bars_d1=bars_d1,
        bars_h1=bars_h1,
        anchors=anchors,
        active_session="ny",
        cfg=cfg,
    )
    assert snap_ny.primary.anchor_kind == "ny_open"


def test_primary_falls_back_when_preferred_anchor_missing():
    cfg = SmcRangeExhaustionConfig()
    snap = compute_range_exhaustion(
        symbol="XAUUSD",
        current_price=4620.0,
        bars_d1=_d1_bars(),
        bars_h1=_h1_bars(),
        anchors={"d1_open": (0, 4612.0)},  # london_open absent
        active_session="london",  # prefers london_open → falls back to d1_open
        cfg=cfg,
    )
    assert snap.primary.anchor_kind == "d1_open"


# ── 6. Traveled direction sign semantics ────────────────────────────────────


@pytest.mark.parametrize(
    "current,expected_dir",
    [(4620.0, 1), (4600.0, -1), (4612.0, 0)],
)
def test_traveled_direction_sign(current, expected_dir):
    cfg = SmcRangeExhaustionConfig()
    snap = compute_range_exhaustion(
        symbol="XAUUSD",
        current_price=current,
        bars_d1=_d1_bars(),
        bars_h1=_h1_bars(),
        anchors={"d1_open": (0, 4612.0)},
        cfg=cfg,
    )
    assert snap.primary.traveled_dir == expected_dir


# ── 7. Wire serialization shape (P4/P8 consumer contract) ──────────────────


def test_wire_serialization_contains_required_fields():
    cfg = SmcRangeExhaustionConfig()
    snap = compute_range_exhaustion(
        symbol="XAUUSD",
        current_price=4620.0,
        bars_d1=_d1_bars(),
        bars_h1=_h1_bars(),
        anchors={"d1_open": (0, 4612.0)},
        cfg=cfg,
    )
    wire = snap.to_wire()
    assert set(wire.keys()) == {"primary", "by_anchor", "computed_at_ms"}
    required = {
        "anchor_kind", "anchor_ms", "anchor_price", "traveled_abs",
        "traveled_dir", "atr_baseline", "traveled_mult", "phase",
        "remaining_budget", "confidence_delta", "degraded",
    }
    assert required.issubset(set(wire["primary"].keys()))


# ── 8. P3: SmcRunner integration ────────────────────────────────────────────


def _make_runner_with_re(enabled: bool):
    """SmcRunner з increasingly enabled range_exhaustion."""
    from core.smc.config import SmcConfig
    from core.smc.engine import SmcEngine
    from runtime.smc.smc_runner import SmcRunner

    smc_cfg = {
        "enabled": True,
        "lookback_bars": 100,
        "swing_period": 2,
        "compute_tfs": [60, 3600, 86400],
        "ob": {"enabled": True, "min_impulse_atr_mult": 0.1, "atr_period": 5},
        "fvg": {"enabled": True, "min_gap_atr_mult": 0.0, "max_active": 10},
        "structure": {"enabled": True, "confirmation_bars": 1},
        "performance": {"max_compute_ms": 2000, "log_slow_threshold_ms": 500},
        "range_exhaustion": {"enabled": enabled, "atr_period": 5},
    }
    engine = SmcEngine(SmcConfig.from_dict(smc_cfg))
    runner = SmcRunner(
        full_cfg={
            "symbols": ["XAU/USD"],
            "tf_allowlist_s": [60, 3600, 86400],
            "smc": smc_cfg,
        },
        engine=engine,
    )
    # Seed D1 bars into engine directly
    for i, bar in enumerate(_d1_bars(n=14, rng=40.0)):
        engine._get_or_create("XAU/USD", 86400).append(bar)
    for bar in _h1_bars(n=14, rng=6.0):
        engine._get_or_create("XAU/USD", 3600).append(bar)
    # Seed last_price (would come from M1 feed in prod)
    runner._last_prices["XAU/USD"] = 4620.0
    return runner, engine


def test_p3_returns_none_when_disabled():
    """enabled=false → get_range_exhaustion returns None, snapshot.range_exhaustion is None."""
    runner, engine = _make_runner_with_re(enabled=False)
    assert runner.get_range_exhaustion("XAU/USD") is None


def test_p3_computes_when_enabled():
    """enabled=true + D1 bars + price → returns valid snapshot.

    SmcRunner passes active_session=None (ADR-0035 wiring deferred),
    тому primary.degraded має "session_context_unavailable" маркер.
    """
    runner, _ = _make_runner_with_re(enabled=True)
    snap = runner.get_range_exhaustion("XAU/USD")
    assert snap is not None
    assert snap.symbol == "XAU/USD"
    assert snap.primary.anchor_kind == "d1_open"
    # current=4620, anchor=4600 (first bar open), atr=40 → mult=0.5 → mid
    assert snap.primary.traveled_dir == 1
    assert snap.primary.phase == "mid"
    # F9/X34 compliance: session absence is a first-class loud signal.
    assert "session_context_unavailable" in snap.primary.degraded


def test_compute_active_session_none_adds_degraded_marker():
    """active_session=None → primary.degraded має 'session_context_unavailable'."""
    cfg = SmcRangeExhaustionConfig()
    snap = compute_range_exhaustion(
        symbol="XAUUSD",
        current_price=4620.0,
        bars_d1=_d1_bars(),
        bars_h1=_h1_bars(),
        anchors={"d1_open": (0, 4612.0)},
        active_session=None,
        cfg=cfg,
    )
    assert "session_context_unavailable" in snap.primary.degraded
    # States dict reflects the same updated primary
    assert "session_context_unavailable" in snap.by_anchor["d1_open"].degraded


def test_compute_active_session_explicit_no_marker():
    """active_session explicitly provided → no session_context_unavailable marker."""
    cfg = SmcRangeExhaustionConfig()
    snap = compute_range_exhaustion(
        symbol="XAUUSD",
        current_price=4620.0,
        bars_d1=_d1_bars(),
        bars_h1=_h1_bars(),
        anchors={"d1_open": (0, 4612.0)},
        active_session="asia",
        cfg=cfg,
    )
    assert "session_context_unavailable" not in snap.primary.degraded


def test_p3_cache_freshness_2s_window():
    """Consecutive call within 2s returns same cached snapshot (avoid recompute)."""
    runner, _ = _make_runner_with_re(enabled=True)
    s1 = runner.get_range_exhaustion("XAU/USD", now_ms=1000)
    s2 = runner.get_range_exhaustion("XAU/USD", now_ms=1500)  # 500ms later
    assert s1 is s2  # identity: same cached object

    # After cache expires (>2s) → fresh compute
    s3 = runner.get_range_exhaustion("XAU/USD", now_ms=4000)
    assert s3 is not s1  # different instance


def test_p3_no_last_price_returns_none():
    """No last_price → can't compute → None (guard, not crash)."""
    runner, _ = _make_runner_with_re(enabled=True)
    runner._last_prices.pop("XAU/USD", None)
    assert runner.get_range_exhaustion("XAU/USD") is None


# ── 10. P5: Confluence consumer (apply_range_exhaustion_penalty) ────────────


def _mk_zone(zid, kind):
    """Lightweight zone stub with .id and .kind for penalty helper."""
    from types import SimpleNamespace

    return SimpleNamespace(id=zid, kind=kind)


def _mk_range_state(phase="late", traveled_dir=1, confidence_delta=-0.15):
    from core.smc.types import RangeExhaustionState

    return RangeExhaustionState(
        anchor_kind="d1_open", anchor_ms=0, anchor_price=100.0, current_price=105.0,
        traveled_abs=5.0, traveled_dir=traveled_dir, atr_baseline=10.0,
        traveled_mult=0.80, phase=phase, remaining_budget=0.70,
        confidence_delta=confidence_delta, degraded=[],
    )


_CONF_CFG = {
    "grade_thresholds": {"a_plus": 8, "a": 6, "b": 4},
}


def test_p5_penalty_none_state_returns_copy():
    from core.smc.confluence import apply_range_exhaustion_penalty

    raw = {"ob_bull_1": {"score": 8, "grade": "A+", "factors": ["x +2"]}}
    out = apply_range_exhaustion_penalty(raw, [_mk_zone("ob_bull_1", "ob_bull")], None, _CONF_CFG)
    assert out == raw
    assert out is not raw  # new dict returned


def test_p5_penalty_early_phase_no_effect():
    """confidence_delta=0.0 (early/mid) → no-op."""
    from core.smc.confluence import apply_range_exhaustion_penalty

    raw = {"ob_bull_1": {"score": 10, "grade": "A+", "factors": []}}
    state = _mk_range_state(phase="early", confidence_delta=0.0)
    out = apply_range_exhaustion_penalty(raw, [_mk_zone("ob_bull_1", "ob_bull")], state, _CONF_CFG)
    assert out["ob_bull_1"]["score"] == 10
    assert out["ob_bull_1"]["grade"] == "A+"


def test_p5_penalty_same_direction_knocks_down():
    """Bullish zone + bullish exhausted day → penalty applied."""
    from core.smc.confluence import apply_range_exhaustion_penalty

    raw = {"ob_bull_1": {"score": 10, "grade": "A+", "factors": ["sweep +2"]}}
    state = _mk_range_state(phase="exhausted", traveled_dir=1, confidence_delta=-0.30)
    out = apply_range_exhaustion_penalty(raw, [_mk_zone("ob_bull_1", "ob_bull")], state, _CONF_CFG)
    # 10 + round(10 * -0.30) = 10 - 3 = 7 → grade A
    assert out["ob_bull_1"]["score"] == 7
    assert out["ob_bull_1"]["grade"] == "A"
    assert any("range_exhaust_exhausted" in f for f in out["ob_bull_1"]["factors"])
    # Original factors preserved
    assert "sweep +2" in out["ob_bull_1"]["factors"]


def test_p5_penalty_opposite_direction_no_effect():
    """Bullish zone + bearish day (mean reversion setup) → NO penalty."""
    from core.smc.confluence import apply_range_exhaustion_penalty

    raw = {"ob_bull_1": {"score": 8, "grade": "A+", "factors": []}}
    state = _mk_range_state(traveled_dir=-1, confidence_delta=-0.30)
    out = apply_range_exhaustion_penalty(raw, [_mk_zone("ob_bull_1", "ob_bull")], state, _CONF_CFG)
    assert out["ob_bull_1"]["score"] == 8
    assert out["ob_bull_1"]["grade"] == "A+"


def test_p5_penalty_traveled_dir_zero_no_effect():
    """Flat day (traveled_dir=0) → no direction match → no penalty."""
    from core.smc.confluence import apply_range_exhaustion_penalty

    raw = {"ob_bull_1": {"score": 10, "grade": "A+", "factors": []}}
    state = _mk_range_state(traveled_dir=0, confidence_delta=-0.30)
    out = apply_range_exhaustion_penalty(raw, [_mk_zone("ob_bull_1", "ob_bull")], state, _CONF_CFG)
    assert out["ob_bull_1"]["score"] == 10


def test_p5_penalty_bearish_zone_bearish_day_knocks_down():
    """ob_bear + bearish day → same-direction continuation → penalty."""
    from core.smc.confluence import apply_range_exhaustion_penalty

    raw = {"ob_bear_1": {"score": 8, "grade": "A+", "factors": []}}
    state = _mk_range_state(traveled_dir=-1, confidence_delta=-0.30)
    out = apply_range_exhaustion_penalty(raw, [_mk_zone("ob_bear_1", "ob_bear")], state, _CONF_CFG)
    # 8 + round(8 * -0.30) = 8 - 2 = 6 → grade A
    assert out["ob_bear_1"]["score"] == 6
    assert out["ob_bear_1"]["grade"] == "A"


def test_p5_penalty_zero_score_stays_zero():
    """Score=0 → adj=0 → no factor pollution, unchanged."""
    from core.smc.confluence import apply_range_exhaustion_penalty

    raw = {"ob_bull_1": {"score": 0, "grade": "C", "factors": []}}
    state = _mk_range_state(confidence_delta=-0.30)
    out = apply_range_exhaustion_penalty(raw, [_mk_zone("ob_bull_1", "ob_bull")], state, _CONF_CFG)
    assert out["ob_bull_1"]["score"] == 0
    assert out["ob_bull_1"]["grade"] == "C"
    assert out["ob_bull_1"]["factors"] == []


def test_p5_penalty_fvg_zone_uses_fvg_thresholds_with_fallback():
    """FVG zone kind routes to fvg_grade_thresholds (falls back to grade_thresholds)."""
    from core.smc.confluence import apply_range_exhaustion_penalty

    raw = {"fvg_bull_1": {"score": 6, "grade": "A", "factors": []}}
    state = _mk_range_state(phase="late", traveled_dir=1, confidence_delta=-0.30)
    out = apply_range_exhaustion_penalty(raw, [_mk_zone("fvg_bull_1", "fvg_bull")], state, _CONF_CFG)
    # 6 + round(6 * -0.30) = 6 - 2 = 4 → grade B (via fallback thresholds a=6 boundary)
    assert out["fvg_bull_1"]["score"] == 4
    assert out["fvg_bull_1"]["grade"] == "B"


def test_p5_penalty_runner_integration_disabled_passthrough():
    """Runner.get_zone_grades with enabled=false → raw grades unchanged."""
    runner, engine = _make_runner_with_re(enabled=False)
    # Seed fake grades onto engine
    engine._zone_grades[("XAU/USD", 900)] = {
        "ob_bull_1": {"score": 10, "grade": "A+", "factors": []},
    }
    out = runner.get_zone_grades("XAU/USD", 900)
    assert out["ob_bull_1"]["score"] == 10


def test_p5_penalty_runner_integration_enabled_applies_penalty():
    """Runner.get_zone_grades with enabled=true + bullish exhausted day → same-dir zone knocked down."""
    from core.smc.types import SmcSnapshot, SmcZone

    runner, engine = _make_runner_with_re(enabled=True)
    # Seed a bullish OB zone into engine cached snapshot.
    zone = SmcZone(
        id="ob_bull_test", symbol="XAU/USD", tf_s=900, kind="ob_bull",
        start_ms=0, end_ms=None, high=4620.0, low=4615.0,
        status="active", strength=1.0, anchor_bar_ms=0,
    )
    snap = SmcSnapshot(
        symbol="XAU/USD", tf_s=900, zones=[zone], swings=[], levels=[],
        trend_bias="bullish", last_bos_ms=None, last_choch_ms=None,
        computed_at_ms=0, bar_count=14,
    )
    engine._get_or_create("XAU/USD", 900).last_snapshot = snap
    engine._zone_grades[("XAU/USD", 900)] = {
        "ob_bull_test": {"score": 10, "grade": "A+", "factors": ["sweep +2"]},
    }
    # Set price well above D1 anchor → traveled_dir=1 (bullish), mult=0.5 (mid, delta=0)
    # Need exhausted phase → default config late=0.70, exhausted=1.00.
    # D1 bars have rng=40.0 → atr=40, anchor_price=4600 (first bar open).
    # Price 4660 → traveled=60 → mult=1.5 → exhausted, delta=-0.30
    runner._last_prices["XAU/USD"] = 4660.0
    # Clear cache to force fresh compute
    runner._range_exhaustion_cache.clear()

    out = runner.get_zone_grades("XAU/USD", 900)
    # 10 + round(10 * -0.30) = 10 - 3 = 7 → grade A
    assert out["ob_bull_test"]["score"] == 7
    assert out["ob_bull_test"]["grade"] == "A"
    assert any("range_exhaust_exhausted" in f for f in out["ob_bull_test"]["factors"])
    # Original factor preserved
    assert "sweep +2" in out["ob_bull_test"]["factors"]


# ── 9. P4: SmcSnapshot.to_wire() extension (wire frame contract) ──────────────


# ── 11. P6: Narrative consumer (_build_range_exhaustion_summary) ────────────


def test_p6_summary_none_returns_empty():
    from core.smc.narrative import _build_range_exhaustion_summary

    assert _build_range_exhaustion_summary(None) == ""


def test_p6_summary_early_phase_silent():
    """Clean chart doctrine: early phase → no phrase."""
    from core.smc.narrative import _build_range_exhaustion_summary

    state = _mk_range_state(phase="early", traveled_dir=1, confidence_delta=0.0)
    assert _build_range_exhaustion_summary(state) == ""


def test_p6_summary_mid_phase_silent():
    """Clean chart doctrine: mid phase → no phrase (movement healthy)."""
    from core.smc.narrative import _build_range_exhaustion_summary

    state = _mk_range_state(phase="mid", traveled_dir=1, confidence_delta=0.0)
    assert _build_range_exhaustion_summary(state) == ""


def test_p6_summary_flat_day_silent():
    """traveled_dir=0 → no direction → no phrase."""
    from core.smc.narrative import _build_range_exhaustion_summary

    state = _mk_range_state(phase="late", traveled_dir=0, confidence_delta=-0.15)
    assert _build_range_exhaustion_summary(state) == ""


def test_p6_summary_late_bullish_includes_anchor_and_hint():
    """Late phase + bullish day → 'D1 up 0.80 ATR (late) - <hint>'."""
    from core.smc.narrative import _build_range_exhaustion_summary

    state = _mk_range_state(phase="late", traveled_dir=1, confidence_delta=-0.15)
    summary = _build_range_exhaustion_summary(state)
    assert "D1" in summary
    assert "up" in summary
    assert "0.80" in summary
    assert "late" in summary
    assert "pullback" in summary or "consolidation" in summary


def test_p6_summary_exhausted_bearish_strong_warning():
    """Exhausted phase + bearish day → mentions reversal risk."""
    from core.smc.narrative import _build_range_exhaustion_summary
    from core.smc.types import RangeExhaustionState

    state = RangeExhaustionState(
        anchor_kind="d1_open", anchor_ms=0, anchor_price=4660.0, current_price=4600.0,
        traveled_abs=60.0, traveled_dir=-1, atr_baseline=40.0,
        traveled_mult=1.50, phase="exhausted", remaining_budget=0.0,
        confidence_delta=-0.30, degraded=[],
    )
    summary = _build_range_exhaustion_summary(state)
    assert "D1" in summary
    assert "down" in summary
    assert "1.50" in summary
    assert "exhausted" in summary
    assert "розворот" in summary or "reversal" in summary


def test_p6_summary_uses_anchor_label_per_kind():
    """Different anchors get appropriate labels (London/NY/Week)."""
    from core.smc.narrative import _build_range_exhaustion_summary
    from core.smc.types import RangeExhaustionState

    base = dict(
        anchor_ms=0, anchor_price=100.0, current_price=110.0,
        traveled_abs=10.0, traveled_dir=1, atr_baseline=10.0,
        traveled_mult=1.00, phase="exhausted", remaining_budget=0.5,
        confidence_delta=-0.30, degraded=[],
    )
    london = RangeExhaustionState(anchor_kind="london_open", **base)
    ny = RangeExhaustionState(anchor_kind="ny_open", **base)
    week = RangeExhaustionState(anchor_kind="week_open", **base)
    assert "London" in _build_range_exhaustion_summary(london)
    assert "NY" in _build_range_exhaustion_summary(ny)
    assert "Week" in _build_range_exhaustion_summary(week)


def test_p6_synthesize_narrative_passes_summary_to_block():
    """synthesize_narrative — when range_exhaustion provided → block.range_exhaustion_summary populated."""
    from core.smc.narrative import synthesize_narrative
    from core.smc.types import SmcSnapshot

    snap = SmcSnapshot(
        symbol="XAUUSD", tf_s=900, zones=[], swings=[], levels=[],
        trend_bias=None, last_bos_ms=None, last_choch_ms=None,
        computed_at_ms=0, bar_count=0,
    )
    cfg = {"max_scenarios": 2, "trade_min_grade": "A", "trade_min_score": 6}
    state = _mk_range_state(phase="late", traveled_dir=1, confidence_delta=-0.15)

    nb_with = synthesize_narrative(
        snap, {}, {}, {}, 900, 100.0, 10.0, cfg, range_exhaustion=state,
    )
    assert nb_with.range_exhaustion_summary != ""
    assert "late" in nb_with.range_exhaustion_summary

    # Without range_exhaustion → empty (default)
    nb_without = synthesize_narrative(
        snap, {}, {}, {}, 900, 100.0, 10.0, cfg,
    )
    assert nb_without.range_exhaustion_summary == ""


def test_p6_narrative_to_wire_omits_summary_when_empty():
    """Wire compactness: empty summary → no key in dict."""
    from core.smc.narrative import narrative_to_wire
    from core.smc.types import NarrativeBlock

    nb = NarrativeBlock(
        mode="wait", sub_mode="", headline="x", bias_summary="",
        scenarios=[], next_area="", fvg_context="", market_phase="ranging",
        warnings=[],
    )
    wire = narrative_to_wire(nb)
    assert "range_exhaustion_summary" not in wire


def test_p6_narrative_to_wire_includes_summary_when_set():
    from core.smc.narrative import narrative_to_wire
    from core.smc.types import NarrativeBlock

    nb = NarrativeBlock(
        mode="wait", sub_mode="", headline="x", bias_summary="",
        scenarios=[], next_area="", fvg_context="", market_phase="ranging",
        warnings=[],
        range_exhaustion_summary="D1 up 0.85 ATR (late) - test",
    )
    wire = narrative_to_wire(nb)
    assert wire["range_exhaustion_summary"] == "D1 up 0.85 ATR (late) - test"


def _empty_smc_snapshot(**overrides):
    from core.smc.types import SmcSnapshot

    kw = dict(
        symbol="XAUUSD", tf_s=900, zones=[], swings=[], levels=[],
        trend_bias=None, last_bos_ms=None, last_choch_ms=None,
        computed_at_ms=0, bar_count=0,
    )
    kw.update(overrides)
    return SmcSnapshot(**kw)


def test_p4_smc_snapshot_to_wire_omits_range_exhaustion_when_none():
    """Null-compact contract: key absent when range_exhaustion is None.

    Required so _build_full_frame and delta path skip injection cleanly
    (smc_wire.get('range_exhaustion') → None → no frame key).
    """
    snap = _empty_smc_snapshot()
    wire = snap.to_wire()
    assert "range_exhaustion" not in wire
    # Existing keys preserved
    assert set(wire.keys()) == {"zones", "swings", "levels", "trend_bias"}


def test_p4_smc_snapshot_to_wire_includes_range_exhaustion_when_set():
    """When range_exhaustion is populated — key present with nested snapshot wire."""
    cfg = SmcRangeExhaustionConfig()
    re_snap = compute_range_exhaustion(
        symbol="XAUUSD",
        current_price=4620.0,
        bars_d1=_d1_bars(),
        bars_h1=_h1_bars(),
        anchors={"d1_open": (0, 4612.0)},
        active_session="london",
        cfg=cfg,
    )
    snap = _empty_smc_snapshot(range_exhaustion=re_snap)
    wire = snap.to_wire()
    assert "range_exhaustion" in wire
    # Nested structure matches RangeExhaustionSnapshot.to_wire() contract
    assert set(wire["range_exhaustion"].keys()) == {"primary", "by_anchor", "computed_at_ms"}
    assert wire["range_exhaustion"]["primary"]["anchor_kind"] == "d1_open"
    assert wire["range_exhaustion"]["primary"]["phase"] in {"early", "mid", "late", "exhausted"}


def test_p3_no_d1_bars_produces_degraded_snapshot():
    """No D1 bars → compute returns fallback snapshot with `no_anchors_available` marker."""
    from core.smc.config import SmcConfig
    from core.smc.engine import SmcEngine
    from runtime.smc.smc_runner import SmcRunner

    smc_cfg = {
        "enabled": True,
        "lookback_bars": 100,
        "compute_tfs": [60],
        "range_exhaustion": {"enabled": True},
    }
    engine = SmcEngine(SmcConfig.from_dict(smc_cfg))
    runner = SmcRunner(
        full_cfg={"symbols": ["X"], "tf_allowlist_s": [60], "smc": smc_cfg},
        engine=engine,
    )
    runner._last_prices["X"] = 100.0
    snap = runner.get_range_exhaustion("X")
    assert snap is not None
    assert "no_anchors_available" in snap.primary.degraded
