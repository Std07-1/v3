"""
tests/test_smc_d1_display_filter.py — D1 Display Filter Tests.

Covers:
  - Proximity filter: zones/levels far from price → removed
  - Height guard: giant FVG retroactively removed, P/D exempt
  - Zone cap: max_display_zones enforced
  - Level cap: max_display_levels enforced
  - Swing cap: only last N swings
  - Disappeared zones → delta mitigated_zones (UI removal)
  - Config: SmcDisplayConfig from_dict round-trip
"""

from __future__ import annotations

import dataclasses
from typing import List

import pytest
from core.model.bars import CandleBar
from core.smc.config import SmcConfig, SmcDisplayConfig
from core.smc.engine import SmcEngine, _filter_for_display, _diff_snapshots
from core.smc.types import SmcZone, SmcSwing, SmcLevel, SmcSnapshot


# ── Helpers ──────────────────────────────────────────────────────────────


def _bar(idx, o, h, low, c, tf_s=300, sym="XAU/USD"):
    # type: (int, float, float, float, float, int, str) -> CandleBar
    return CandleBar(
        symbol=sym,
        tf_s=tf_s,
        open_time_ms=1000000 + idx * tf_s * 1000,
        close_time_ms=1000000 + idx * tf_s * 1000 + tf_s * 1000,
        o=o,
        h=h,
        low=low,
        c=c,
        v=100.0,
        complete=True,
        src="test",
    )


def _zone(
    kind,
    high,
    low,
    anchor_idx=0,
    strength=0.8,
    status="active",
    tf_s=300,
    sym="XAU/USD",
):
    anchor_ms = 1000000 + anchor_idx * tf_s * 1000
    return SmcZone(
        id="{}_{}_{}_{}".format(kind, sym, tf_s, anchor_ms),
        symbol=sym,
        tf_s=tf_s,
        kind=kind,
        start_ms=anchor_ms,
        end_ms=None,
        high=high,
        low=low,
        status=status,
        strength=strength,
        anchor_bar_ms=anchor_ms,
    )


def _level(kind, price, touches=3, idx=0, tf_s=300, sym="XAU/USD"):
    return SmcLevel(
        id="{}_{}_{}_{}".format(kind, sym, tf_s, int(price * 100)),
        symbol=sym,
        tf_s=tf_s,
        kind=kind,
        price=price,
        time_ms=1000000 + idx * tf_s * 1000,
        touches=touches,
    )


def _swing(kind, time_ms, price, sym="XAU/USD", tf_s=300):
    from core.smc.types import make_swing_id

    return SmcSwing(
        id=make_swing_id(kind, sym, tf_s, time_ms),
        symbol=sym,
        tf_s=tf_s,
        kind=kind,
        price=price,
        time_ms=time_ms,
        confirmed=True,
    )


def _snap(zones=None, levels=None, swings=None, sym="XAU/USD", tf_s=300):
    import time

    return SmcSnapshot(
        symbol=sym,
        tf_s=tf_s,
        zones=zones or [],
        swings=swings or [],
        levels=levels or [],
        trend_bias=None,
        last_bos_ms=None,
        last_choch_ms=None,
        computed_at_ms=int(time.time() * 1000),
        bar_count=100,
    )


def _make_bars(n=50, base_price=2000.0, atr_approx=10.0, tf_s=300):
    """Create N bars around base_price with ~atr_approx range."""
    bars = []
    half = atr_approx / 2.0
    for i in range(n):
        o = base_price + (i % 3 - 1) * 2
        h = o + half
        low = o - half
        c = o + 1
        bars.append(_bar(i, o, h, low, c, tf_s=tf_s))
    return bars


# ── TestSmcDisplayConfig ────────────────────────────────────────────────


class TestSmcDisplayConfig:
    def test_defaults(self):
        cfg = SmcDisplayConfig()
        assert cfg.proximity_atr_mult == 6.0  # ADR-0028: tuned from 5.0
        assert cfg.max_display_zones == 10  # ADR-0028: tuned from 8
        assert cfg.max_display_levels == 6
        assert cfg.max_display_swings == 20
        # ADR-0028 Φ0 new fields
        assert cfg.min_display_strength == 0.25
        assert cfg.mitigated_ttl_bars == 20
        assert cfg.focus_budget_per_side == 3
        assert cfg.focus_budget_total == 12
        assert cfg.structure_label_max == 4
        assert cfg.fvg_display_cap == 4
        # F10: decay params moved to SmcConfig root
        scfg = SmcConfig()
        assert scfg.decay_start_bars == 30
        assert scfg.decay_fast_bars == 150

    def test_from_dict(self):
        d = {"proximity_atr_mult": 3.0, "max_display_zones": 5}
        cfg = SmcDisplayConfig.from_dict(d)
        assert cfg.proximity_atr_mult == 3.0
        assert cfg.max_display_zones == 5
        assert cfg.max_display_levels == 6  # default

    def test_smcconfig_has_display(self):
        cfg = SmcConfig()
        assert hasattr(cfg, "display")
        assert isinstance(cfg.display, SmcDisplayConfig)

    def test_smcconfig_from_dict_propagates(self):
        d = {"display": {"proximity_atr_mult": 7.0, "max_display_swings": 10}}
        cfg = SmcConfig.from_dict(d)
        assert cfg.display.proximity_atr_mult == 7.0
        assert cfg.display.max_display_swings == 10


# ── TestProximityFilter ─────────────────────────────────────────────────


class TestProximityFilter:
    """Zones/levels far from current price are removed."""

    def test_nearby_zone_kept(self):
        bars = _make_bars(50, base_price=2000.0, atr_approx=10.0)
        z = _zone("ob_bull", high=2005.0, low=1995.0)  # mid=2000, close to price
        snap = _snap(zones=[z])
        cfg = SmcConfig(display=SmcDisplayConfig(proximity_atr_mult=5.0))
        result = _filter_for_display(snap, bars, cfg)
        assert len(result.zones) == 1

    def test_far_zone_removed(self):
        bars = _make_bars(50, base_price=2000.0, atr_approx=10.0)
        # ATR ≈ 10, radius = 5*10 = 50. Zone at mid=2100 → 100 away → out
        z = _zone("ob_bull", high=2105.0, low=2095.0)
        snap = _snap(zones=[z])
        cfg = SmcConfig(display=SmcDisplayConfig(proximity_atr_mult=5.0))
        result = _filter_for_display(snap, bars, cfg)
        assert len(result.zones) == 0

    def test_nearby_level_kept(self):
        bars = _make_bars(50, base_price=2000.0, atr_approx=10.0)
        lv = _level("eq_high", price=2010.0)
        snap = _snap(levels=[lv])
        cfg = SmcConfig(display=SmcDisplayConfig(proximity_atr_mult=5.0))
        result = _filter_for_display(snap, bars, cfg)
        assert len(result.levels) == 1

    def test_far_level_removed(self):
        """ADR-0024b: proximity filter for levels DISABLED — far levels kept."""
        bars = _make_bars(50, base_price=2000.0, atr_approx=10.0)
        lv = _level("eq_high", price=2200.0)
        snap = _snap(levels=[lv])
        cfg = SmcConfig(display=SmcDisplayConfig(proximity_atr_mult=5.0))
        result = _filter_for_display(snap, bars, cfg)
        # ADR-0024b: levels no longer filtered by proximity — all shown
        assert len(result.levels) == 1


# ── TestHeightGuard ─────────────────────────────────────────────────────


class TestHeightGuard:
    """Giant zones retroactively filtered (FVG/OB only)."""

    def test_giant_fvg_filtered_by_height(self):
        """FVG zones are now height-guarded same as OBs."""
        bars = _make_bars(50, base_price=2000.0, atr_approx=10.0)
        # ATR ≈ 10, max_height = 4.0 * 10 = 40. Zone height = 200 → filtered out
        z = _zone("fvg_bull", high=2100.0, low=1900.0)
        snap = _snap(zones=[z])
        cfg = SmcConfig(
            max_zone_height_atr_mult=4.0,
            display=SmcDisplayConfig(proximity_atr_mult=999.0),
        )
        result = _filter_for_display(snap, bars, cfg)
        assert len(result.zones) == 0, "Giant FVG must be filtered by height guard"

    def test_normal_fvg_passes(self):
        bars = _make_bars(50, base_price=2000.0, atr_approx=10.0)
        z = _zone("fvg_bull", high=2010.0, low=2000.0)  # height=10 < 40
        snap = _snap(zones=[z])
        cfg = SmcConfig(
            max_zone_height_atr_mult=4.0,
            display=SmcDisplayConfig(proximity_atr_mult=999.0),
        )
        result = _filter_for_display(snap, bars, cfg)
        assert len(result.zones) == 1

    def test_pd_exempt_from_height_guard(self):
        """Premium/discount zones cover full SH-SL range — exempt from height guard."""
        bars = _make_bars(50, base_price=2000.0, atr_approx=10.0)
        z = _zone("premium", high=2100.0, low=2000.0)  # height=100, would fail guard
        snap = _snap(zones=[z])
        cfg = SmcConfig(
            max_zone_height_atr_mult=4.0,
            display=SmcDisplayConfig(proximity_atr_mult=999.0),
        )
        result = _filter_for_display(snap, bars, cfg)
        assert len(result.zones) == 1


# ── TestCaps ────────────────────────────────────────────────────────────


class TestCaps:
    """Display caps enforced."""

    def test_zone_cap(self):
        bars = _make_bars(50, base_price=2000.0, atr_approx=10.0)
        zones = [
            _zone(
                "ob_bull",
                high=2000.0 + i,
                low=1999.0 + i,
                anchor_idx=i,
                strength=round(1.0 - i * 0.05, 2),
            )
            for i in range(15)
        ]
        snap = _snap(zones=zones)
        cfg = SmcConfig(
            display=SmcDisplayConfig(proximity_atr_mult=999.0, max_display_zones=5)
        )
        result = _filter_for_display(snap, bars, cfg)
        assert len(result.zones) == 5

    def test_level_cap(self):
        """ADR-0024b: level cap DISABLED — all levels pass through."""
        bars = _make_bars(50, base_price=2000.0, atr_approx=10.0)
        levels = [_level("eq_high", price=2000.0 + i, idx=i) for i in range(12)]
        snap = _snap(levels=levels)
        cfg = SmcConfig(
            display=SmcDisplayConfig(proximity_atr_mult=999.0, max_display_levels=4)
        )
        result = _filter_for_display(snap, bars, cfg)
        # ADR-0024b: no level cap — all 12 levels shown
        assert len(result.levels) == 12

    def test_swing_cap(self):
        swings = [
            _swing("hh", time_ms=1000000 + i * 300000, price=2000.0 + i)
            for i in range(30)
        ]
        bars = _make_bars(50, base_price=2000.0, atr_approx=10.0)
        snap = _snap(swings=swings)
        cfg = SmcConfig(display=SmcDisplayConfig(max_display_swings=10))
        result = _filter_for_display(snap, bars, cfg)
        assert len(result.swings) == 10

    def test_swing_cap_keeps_latest(self):
        """Last N swings are kept (most recent)."""
        swings = [
            _swing("hh", time_ms=1000000 + i * 300000, price=2000.0 + i)
            for i in range(30)
        ]
        bars = _make_bars(50, base_price=2000.0, atr_approx=10.0)
        snap = _snap(swings=swings)
        cfg = SmcConfig(display=SmcDisplayConfig(max_display_swings=5))
        result = _filter_for_display(snap, bars, cfg)
        # Last 5 swings
        assert result.swings == swings[-5:]


# ── TestDisappearedInDelta ──────────────────────────────────────────────


class TestDisappearedInDelta:
    """Zones that leave display filter → mitigated_zones in delta (UI removal)."""

    def test_disappeared_in_mitigated_zones(self):
        z1 = _zone("ob_bull", high=2005.0, low=1995.0, anchor_idx=0)
        z2 = _zone("ob_bear", high=2105.0, low=2095.0, anchor_idx=1)  # far
        prev = _snap(zones=[z1, z2])
        curr = _snap(zones=[z1])  # z2 disappeared (display filter)
        delta = _diff_snapshots(prev, curr, bar_open_ms=2000000)
        assert z2.id in delta.mitigated_zones

    def test_disappeared_triggers_has_changes(self):
        z1 = _zone("ob_bull", high=2005.0, low=1995.0, anchor_idx=0)
        prev = _snap(zones=[z1])
        curr = _snap(zones=[])  # z1 disappeared
        delta = _diff_snapshots(prev, curr, bar_open_ms=2000000)
        assert delta.has_changes
        assert z1.id in delta.mitigated_zones


# ── TestEmptyBarsNoFilter ───────────────────────────────────────────────


class TestEmptyBarsNoFilter:
    """Empty bars → snapshot returned as-is (no crash)."""

    def test_empty_bars_passthrough(self):
        z = _zone("ob_bull", high=2005.0, low=1995.0)
        snap = _snap(zones=[z])
        cfg = SmcConfig()
        result = _filter_for_display(snap, [], cfg)
        assert result is snap  # exact same object — no filter applied


# ── TestDecayCurve ──────────────────────────────────────────────────────


class TestDecayCurve:
    """D1 decay: starts at decay_start_bars, accelerates at decay_fast_bars."""

    def _run_lifecycle(
        self, age_bars, initial_strength=0.8, decay_start=30, decay_fast=150
    ):
        from core.smc.engine import _update_zone_lifecycle

        tf_s = 300
        anchor_ms = 1000000
        bar_ms = anchor_ms + age_bars * tf_s * 1000
        z = _zone("ob_bull", high=2050.0, low=2040.0, strength=initial_strength)
        z = dataclasses.replace(z, anchor_bar_ms=anchor_ms)
        active = {z.id: z}
        # F10: decay params at SmcConfig root
        cfg = SmcConfig(
            max_zones_per_tf=100,
            decay_start_bars=decay_start,
            decay_fast_bars=decay_fast,
        )
        bar = _bar(0, 2060.0, 2065.0, 2055.0, 2060.0, tf_s=tf_s)
        bar = dataclasses.replace(bar, open_time_ms=bar_ms)
        from core.smc.engine import _update_zone_lifecycle

        result = _update_zone_lifecycle([z], active, bar, cfg, tf_s)
        return result[0] if result else None

    def test_no_decay_before_start(self):
        z = self._run_lifecycle(age_bars=20)
        assert z.strength == 0.8, "No decay before decay_start_bars"

    def test_gentle_decay_30_to_150(self):
        z = self._run_lifecycle(age_bars=80)
        # factor=0.97 → 0.8 * 0.97 = 0.776
        assert z.strength < 0.8
        assert z.strength > 0.7

    def test_aggressive_decay_after_150(self):
        z = self._run_lifecycle(age_bars=200)
        # factor=0.92 → 0.8 * 0.92 = 0.736
        assert z.strength < 0.8
        assert z.strength > 0.6  # single call, so only one decay step

    def test_floor_at_015(self):
        z = self._run_lifecycle(age_bars=300, initial_strength=0.16)
        # 0.16 * 0.92 = 0.1472, clamped to 0.15
        assert z.strength == 0.15
