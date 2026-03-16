"""test_smc_confluence.py — ADR-0029 P-Φ1-1a/1b gate tests."""
from __future__ import annotations

import pytest
from core.smc.confluence import score_zone_confluence, score_fvg_strength, score_fvg_confluence


# ── Fixture helpers ──

def _ob_zone(kind="ob_bull", strength=0.8, anchor_ms=50000, high=2700.0, low=2690.0, tf_s=900):
    return {
        "id": "{}_XAU_{}_{}".format(kind, tf_s, anchor_ms),
        "kind": kind,
        "symbol": "XAU/USD",
        "tf_s": tf_s,
        "anchor_bar_ms": anchor_ms,
        "start_ms": anchor_ms,
        "end_ms": None,
        "high": high,
        "low": low,
        "status": "active",
        "strength": strength,
    }


def _swing(kind, price, time_ms):
    return {"kind": kind, "price": price, "time_ms": time_ms, "id": "sw_%d" % time_ms}


def _bar(open_ms, h, l):
    return {"open_time_ms": open_ms, "h": h, "l": l}


DEFAULT_CFG = {
    "sweep_lookback_bars": 10,
    "fvg_lookforward_bars": 3,
    "extremum_tolerance_atr": 0.3,
    "strong_impulse_threshold": 0.7,
    "grade_thresholds": {"a_plus": 8, "a": 6, "b": 4},
}


# ── AC-1: A+ zone (sweep + fvg + htf + impulse + extremum + structure + tf_sig) ──

class TestConfluenceScoring:
    def test_a_plus_zone(self):
        """AC-1: OB + sweep + FVG + HTF aligned + extremum + impulse + structure + H4 → A+."""
        zone = _ob_zone(kind="ob_bull", strength=0.85, anchor_ms=50000,
                        high=2700.0, low=2690.0, tf_s=14400)
        # F1: swing_low swept before anchor
        swings = [_swing("swing_low", 2689.0, 40000)]
        bars = [_bar(41000, 2695.0, 2688.0)]  # bar l < swing price → sweep
        # F2: FVG after anchor
        fvg_zone = {"kind": "fvg_bull", "anchor_bar_ms": 51000, "high": 2705, "low": 2700}
        all_zones = [zone, fvg_zone]
        # F3: HTF zone containing mid
        htf_zones = [{"high": 2710.0, "low": 2680.0, "status": "active"}]
        # F7: structure confirm
        structure = [{"kind": "bos_bull", "time_ms": 55000}]
        # current price in discount for F6
        result = score_zone_confluence(
            zone=zone, bars=bars, swings=swings, zones_all=all_zones,
            htf_zones=htf_zones, structure=structure,
            atr=10.0, current_price=2685.0, tf_s=14400, config=DEFAULT_CFG,
        )
        assert result["grade"] == "A+"
        assert result["score"] >= 8
        factor_names = [f.split(" ")[0] for f in result["factors"]]
        assert "sweep" in factor_names
        assert "fvg_after" in factor_names
        assert "htf_align" in factor_names
        assert "tf_sig" in factor_names

    def test_c_grade_minimal_context(self):
        """AC-2: OB без sweep, без HTF, weak impulse → C."""
        zone = _ob_zone(kind="ob_bull", strength=0.3, anchor_ms=50000, tf_s=900)
        result = score_zone_confluence(
            zone=zone, bars=[], swings=[], zones_all=[zone],
            htf_zones=[], structure=[],
            atr=10.0, current_price=2700.0, tf_s=900, config=DEFAULT_CFG,
        )
        assert result["grade"] == "C"
        assert result["score"] < 4

    def test_non_ob_zone_returns_c(self):
        """Non-OB zone → grade C, score 0."""
        zone = {"kind": "fvg_bull", "anchor_bar_ms": 50000, "high": 100, "low": 99}
        result = score_zone_confluence(
            zone=zone, bars=[], swings=[], zones_all=[zone],
            htf_zones=[], structure=[],
            atr=10.0, current_price=100.0, tf_s=900, config=DEFAULT_CFG,
        )
        assert result["score"] == 0
        assert result["grade"] == "C"

    def test_determinism_ac6(self):
        """AC-6: Same zone + same context → same score."""
        zone = _ob_zone(kind="ob_bear", strength=0.9, anchor_ms=50000, tf_s=3600)
        swings = [_swing("swing_high", 2710.0, 42000)]
        bars = [_bar(43000, 2711.0, 2700.0)]  # sweep above swing_high
        kwargs = dict(
            zone=zone, bars=bars, swings=swings, zones_all=[zone],
            htf_zones=[], structure=[], atr=10.0, current_price=2715.0,
            tf_s=3600, config=DEFAULT_CFG,
        )
        r1 = score_zone_confluence(**kwargs)
        r2 = score_zone_confluence(**kwargs)
        assert r1 == r2

    def test_b_grade_moderate(self):
        """B grade: score 4-5."""
        zone = _ob_zone(kind="ob_bull", strength=0.8, anchor_ms=50000, tf_s=900)
        # F2: FVG after
        fvg = {"kind": "fvg_bull", "anchor_bar_ms": 51000, "high": 100, "low": 99}
        # F5: strength >= 0.7
        # F7: structure confirm
        structure = [{"kind": "bos_bull", "time_ms": 55000}]
        result = score_zone_confluence(
            zone=zone, bars=[], swings=[], zones_all=[zone, fvg],
            htf_zones=[], structure=structure,
            atr=10.0, current_price=2700.0, tf_s=900, config=DEFAULT_CFG,
        )
        # F2(+2) + F5(+1) + F7(+1) = 4 → B
        assert result["grade"] == "B"
        assert result["score"] == 4


# ── P-Φ1-1b: FVG Strength ──

class TestFvgStrength:
    def test_ac4_large_gap(self):
        """AC-4: FVG gap > 1.5×ATR, not filled → strength ≥ 0.9."""
        fvg = {"high": 2720.0, "low": 2700.0}  # gap=20
        assert score_fvg_strength(fvg, atr=10.0) >= 0.9

    def test_ac5_partial_fill(self):
        """AC-5: FVG filled >50% → strength ×0.5."""
        fvg = {"high": 2720.0, "low": 2700.0}
        base = score_fvg_strength(fvg, atr=10.0, partial_fill_pct=0.0)
        filled = score_fvg_strength(fvg, atr=10.0, partial_fill_pct=0.6)
        assert filled == pytest.approx(base * 0.5, abs=0.01)

    def test_small_gap(self):
        """Tiny gap → low strength."""
        fvg = {"high": 100.2, "low": 100.0}  # gap=0.2
        assert score_fvg_strength(fvg, atr=10.0) <= 0.15

    def test_zero_atr(self):
        """atr=0 → ratio=0 → min base."""
        fvg = {"high": 110, "low": 100}
        assert score_fvg_strength(fvg, atr=0.0) == 0.1


# ── P5B: FVG confluence scoring ──

FVG_CFG = {
    "grade_thresholds": {"a_plus": 8, "a": 6, "b": 4},
    "fvg_grade_thresholds": {"a_plus": 6, "a": 4, "b": 2},
}


def _fvg_zone(kind="fvg_bull", anchor_ms=50000, high=2710.0, low=2690.0, tf_s=900):
    return {
        "id": "{}_XAU_{}_{}".format(kind, tf_s, anchor_ms),
        "kind": kind,
        "symbol": "XAU/USD",
        "tf_s": tf_s,
        "anchor_bar_ms": anchor_ms,
        "high": high,
        "low": low,
        "status": "active",
    }


class TestFvgConfluenceScoring:
    """P5B: score_fvg_confluence returns meaningful score/grade for FVG zones."""

    def test_fvg_large_gap_htf_structure(self):
        """FVG with big gap on H4 + HTF align + structure → score >= 6 → A+."""
        zone = _fvg_zone(kind="fvg_bull", tf_s=14400, high=2720.0, low=2700.0)
        htf_zones = [{"high": 2730.0, "low": 2690.0, "status": "active"}]
        structure = [{"kind": "bos_bull", "time_ms": 55000}]
        atr = 10.0  # gap=20, ratio=2.0 → +3
        result = score_fvg_confluence(zone, htf_zones, structure, 14400, atr, FVG_CFG)
        assert result["score"] >= 6
        assert result["grade"] == "A+"
        assert any("gap_size" in f for f in result["factors"])
        assert any("htf_align" in f for f in result["factors"])

    def test_fvg_small_gap_no_context(self):
        """FVG with tiny gap, no HTF, no structure → low score."""
        zone = _fvg_zone(kind="fvg_bear", tf_s=300, high=2701.0, low=2700.0)
        result = score_fvg_confluence(zone, [], [], 300, 10.0, FVG_CFG)
        assert result["score"] <= 1
        assert result["grade"] == "C"

    def test_fvg_dispatched_via_score_zone_confluence(self):
        """FVG zone dispatched correctly through score_zone_confluence."""
        zone = _fvg_zone(kind="fvg_bull", tf_s=14400, high=2720.0, low=2700.0)
        htf_zones = [{"high": 2730.0, "low": 2690.0, "status": "active"}]
        structure = [{"kind": "bos_bull", "time_ms": 55000}]
        result = score_zone_confluence(
            zone, bars=[], swings=[], zones_all=[], htf_zones=htf_zones,
            structure=structure, atr=10.0, current_price=2710.0,
            tf_s=14400, config=FVG_CFG,
        )
        # Must get non-zero score (was 0 before P5B)
        assert result["score"] > 0
        assert result["grade"] != "C"

    def test_fvg_uses_fvg_grade_thresholds(self):
        """FVG grade uses fvg_grade_thresholds, not OB thresholds."""
        zone = _fvg_zone(kind="fvg_bull", tf_s=14400, high=2710.0, low=2700.0)
        # gap/ATR=1.0 → +2, H4 → +1, no HTF/structure = total 3
        cfg_strict = {
            "fvg_grade_thresholds": {"a_plus": 6, "a": 5, "b": 4},
        }
        result = score_fvg_confluence(zone, [], [], 14400, 10.0, cfg_strict)
        assert result["score"] == 3
        assert result["grade"] == "C"  # 3 < b(4) → C with strict thresholds

        cfg_lenient = {
            "fvg_grade_thresholds": {"a_plus": 6, "a": 4, "b": 2},
        }
        result2 = score_fvg_confluence(zone, [], [], 14400, 10.0, cfg_lenient)
        assert result2["score"] == 3
        assert result2["grade"] == "B"  # 3 >= b(2) → B with lenient thresholds
