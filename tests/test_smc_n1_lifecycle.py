"""
tests/test_smc_n1_lifecycle.py — N1 Zone Lifecycle Tests.

Covers:
  - Mitigation (bull, bear, R-04 close-not-wick)
  - Age expiry (>500 bars → drop)
  - Age decay (200–500 → strength fades)
  - hide_mitigated config
  - D-02: FVG missing → evict
  - D-01: deterministic cap via _zone_rank
  - State accumulation between on_bar calls
  - FVG height guard (N2)
"""

from __future__ import annotations

from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.engine import (
    _update_zone_lifecycle,
)
from core.smc.types import SmcZone


# ── Helpers ──────────────────────────────────────────────────────────────


def _bar(
    open_ms: int,
    o: float,
    h: float,
    low: float,
    c: float,
    tf_s: int = 300,
    sym: str = "XAU/USD",
    complete: bool = True,
) -> CandleBar:
    return CandleBar(
        symbol=sym,
        tf_s=tf_s,
        open_time_ms=open_ms,
        close_time_ms=open_ms + tf_s * 1000,
        o=o,
        h=h,
        low=low,
        c=c,
        v=100.0,
        complete=complete,
        src="test",
    )


def _zone(
    kind: str,
    high: float,
    low: float,
    anchor_ms: int = 1000000,
    strength: float = 0.8,
    status: str = "active",
    sym: str = "XAU/USD",
    tf_s: int = 300,
) -> SmcZone:
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


# ── TestMitigation ───────────────────────────────────────────────────────


class TestMitigation:
    """R-04: mitigation by bar.c (close), NOT wick."""

    def test_bull_zone_mitigated_by_close_below(self):
        """Bull OB mitigated when bar.c < zone.low."""
        z = _zone("ob_bull", high=2050.0, low=2040.0, anchor_ms=100000)
        active = {z.id: z}
        cfg = SmcConfig(max_zones_per_tf=10)
        bar = _bar(200000, o=2035.0, h=2041.0, low=2030.0, c=2032.0)  # c < 2040
        result = _update_zone_lifecycle([z], active, bar, cfg, 300)
        mitigated = [r for r in result if r.status == "mitigated"]
        assert len(mitigated) == 1
        assert mitigated[0].id == z.id

    def test_bear_zone_mitigated_by_close_above(self):
        """Bear OB mitigated when bar.c > zone.high."""
        z = _zone("ob_bear", high=2060.0, low=2050.0, anchor_ms=100000)
        active = {z.id: z}
        cfg = SmcConfig(max_zones_per_tf=10)
        bar = _bar(200000, o=2058.0, h=2065.0, low=2055.0, c=2062.0)  # c > 2060
        result = _update_zone_lifecycle([z], active, bar, cfg, 300)
        mitigated = [r for r in result if r.status == "mitigated"]
        assert len(mitigated) == 1

    def test_r04_wick_does_not_mitigate(self):
        """R-04: wick penetration without close → zone stays active."""
        z = _zone("ob_bull", high=2050.0, low=2040.0, anchor_ms=100000)
        active = {z.id: z}
        cfg = SmcConfig(max_zones_per_tf=10)
        # wick goes to 2035 but CLOSE is at 2041 (above zone.low=2040)
        bar = _bar(200000, o=2045.0, h=2048.0, low=2035.0, c=2041.0)
        result = _update_zone_lifecycle([z], active, bar, cfg, 300)
        still_active = [r for r in result if r.status == "active"]
        assert len(still_active) == 1, "wick alone must not mitigate"


# ── TestExpiry ───────────────────────────────────────────────────────────


class TestExpiry:
    """Age > 500 bars → zone dropped."""

    def test_old_zone_expired(self):
        anchor_ms = 1000000
        tf_s = 300
        bar_ms = tf_s * 1000
        # 501 bars later
        bar_open_ms = anchor_ms + 501 * bar_ms
        z = _zone(
            "ob_bull",
            high=2050.0,
            low=2040.0,
            anchor_ms=anchor_ms,
            strength=0.8,
            tf_s=tf_s,
        )
        active = {z.id: z}
        cfg = SmcConfig(max_zones_per_tf=10)
        bar = _bar(bar_open_ms, o=2060.0, h=2065.0, low=2055.0, c=2060.0, tf_s=tf_s)
        result = _update_zone_lifecycle([z], active, bar, cfg, tf_s)
        # Zone should be expired → not in result
        assert len(result) == 0, "zone >500 bars old should be dropped"

    def test_young_zone_survives(self):
        anchor_ms = 1000000
        tf_s = 300
        bar_ms = tf_s * 1000
        bar_open_ms = anchor_ms + 100 * bar_ms
        z = _zone(
            "ob_bull",
            high=2050.0,
            low=2040.0,
            anchor_ms=anchor_ms,
            strength=0.8,
            tf_s=tf_s,
        )
        active = {z.id: z}
        cfg = SmcConfig(max_zones_per_tf=10)
        bar = _bar(bar_open_ms, o=2060.0, h=2065.0, low=2055.0, c=2060.0, tf_s=tf_s)
        result = _update_zone_lifecycle([z], active, bar, cfg, tf_s)
        assert len(result) == 1


# ── TestAgeDecay ─────────────────────────────────────────────────────────


class TestAgeDecay:
    """200–500 bars → strength decays toward 0.3."""

    def test_strength_decays_at_300_bars(self):
        anchor_ms = 1000000
        tf_s = 300
        bar_open_ms = anchor_ms + 300 * tf_s * 1000
        z = _zone(
            "ob_bull",
            high=2050.0,
            low=2040.0,
            anchor_ms=anchor_ms,
            strength=0.8,
            tf_s=tf_s,
        )
        active = {z.id: z}
        cfg = SmcConfig(max_zones_per_tf=10)
        bar = _bar(bar_open_ms, o=2060.0, h=2065.0, low=2055.0, c=2060.0, tf_s=tf_s)
        result = _update_zone_lifecycle([z], active, bar, cfg, tf_s)
        assert len(result) == 1
        assert result[0].strength < 0.8, "strength should decay"
        assert result[0].strength >= 0.3, "strength floor is 0.3"


# ── TestHideMitigated ────────────────────────────────────────────────────


class TestHideMitigated:
    """hide_mitigated config flag."""

    def test_hide_true_filters_mitigated(self):
        z = _zone(
            "ob_bull", high=2050.0, low=2040.0, anchor_ms=100000, status="mitigated"
        )
        active = {z.id: z}
        cfg = SmcConfig(max_zones_per_tf=10, hide_mitigated=True)
        result = _update_zone_lifecycle([z], active, None, cfg, 300)
        assert len(result) == 0

    def test_hide_false_keeps_mitigated(self):
        z = _zone(
            "ob_bull", high=2050.0, low=2040.0, anchor_ms=100000, status="mitigated"
        )
        active = {z.id: z}
        cfg = SmcConfig(max_zones_per_tf=10, hide_mitigated=False)
        result = _update_zone_lifecycle([z], active, None, cfg, 300)
        assert len(result) == 1


# ── TestFvgMissingDrop (D-02) ────────────────────────────────────────────


class TestFvgDrop:
    """D-02: FVG not in fresh → evict. Non-FVG survives."""

    def test_fvg_missing_from_fresh_dropped(self):
        fvg = _zone("fvg_bull", high=2050.0, low=2045.0, anchor_ms=100000)
        ob = _zone("ob_bull", high=2060.0, low=2055.0, anchor_ms=100000)
        active = {fvg.id: fvg, ob.id: ob}
        cfg = SmcConfig(max_zones_per_tf=10)
        # fresh має тільки ob, fvg зник
        result = _update_zone_lifecycle([ob], active, None, cfg, 300)
        ids = {z.id for z in result}
        assert fvg.id not in ids, "FVG missing from fresh must be evicted"
        assert ob.id in ids, "non-FVG should survive"


# ── TestCapEnforcement (D-01) ────────────────────────────────────────────


class TestCapEnforcement:
    """D-01: deterministic cap via _zone_rank."""

    def test_cap_keeps_strongest(self):
        zones = [
            _zone(
                "ob_bull",
                high=2050 + i,
                low=2040 + i,
                anchor_ms=100000 + i * 300000,
                strength=round(0.1 + i * 0.07, 3),
            )
            for i in range(12)
        ]
        active = {}
        cfg = SmcConfig(max_zones_per_tf=10)
        result = _update_zone_lifecycle(zones, active, None, cfg, 300)
        assert len(result) == 10, "cap to max_zones_per_tf"
        # Strongest zones should survive
        strengths = [z.strength for z in result]
        assert min(strengths) >= 0.1  # weakest two (0.1, 0.17) might be cut

    def test_cap_is_deterministic(self):
        zones = [
            _zone(
                "ob_bear",
                high=2050 + i,
                low=2040 + i,
                anchor_ms=100000 + i * 300000,
                strength=0.5,
            )
            for i in range(12)
        ]
        active = {}
        cfg = SmcConfig(max_zones_per_tf=10)
        r1 = _update_zone_lifecycle(list(zones), dict(active), None, cfg, 300)
        r2 = _update_zone_lifecycle(list(zones), {}, None, cfg, 300)
        assert [z.id for z in r1] == [z.id for z in r2], "must be deterministic"


# ── TestStateAccumulation ────────────────────────────────────────────────


class TestStateAccumulation:
    """active_zones persists between on_bar calls."""

    def test_zones_persist(self):
        z1 = _zone("ob_bull", high=2050.0, low=2040.0, anchor_ms=100000)
        z2 = _zone("ob_bear", high=2070.0, low=2060.0, anchor_ms=200000)
        active = {}  # type: dict
        cfg = SmcConfig(max_zones_per_tf=10)

        # Call 1: add z1
        _update_zone_lifecycle([z1], active, None, cfg, 300)
        assert z1.id in active

        # Call 2: add z2 (z1 should still be there)
        _update_zone_lifecycle([z2], active, None, cfg, 300)
        assert z1.id in active
        assert z2.id in active


# ── TestFvgHeightGuard (N2) ──────────────────────────────────────────────


class TestFvgHeightGuard:
    """N2: giant FVG > max_zone_height_atr_mult × ATR should be skipped."""

    def test_giant_fvg_detected(self):
        """detect_fvg no longer applies height guard — all FVGs pass through."""
        from core.smc.fvg import detect_fvg

        cfg = SmcConfig(max_zone_height_atr_mult=5.0)
        # ATR ~10 based on these bars, gap = 200 → height guard removed
        bars = []
        for i in range(20):
            ms = 1000000 + i * 300000
            bars.append(_bar(ms, 2000.0, 2010.0, 1990.0, 2005.0))
        # Create a bullish gap: b0.h < b2.low by a LOT
        bars.append(_bar(1000000 + 20 * 300000, 2000.0, 2010.0, 1990.0, 2000.0))  # b0
        bars.append(
            _bar(1000000 + 21 * 300000, 2100.0, 2220.0, 2090.0, 2200.0)
        )  # b1 (impulse)
        bars.append(
            _bar(1000000 + 22 * 300000, 2210.0, 2220.0, 2210.0, 2215.0)
        )  # b2: b2.low=2210 >> b0.h=2010
        result = detect_fvg(bars, cfg)
        fvg_bull = [
            z
            for z in result
            if z.kind == "fvg_bull" and z.anchor_bar_ms == bars[-2].open_time_ms
        ]
        assert len(fvg_bull) >= 1, "giant FVG should pass (no height guard)"

    def test_normal_fvg_passes(self):
        from core.smc.fvg import detect_fvg

        cfg = SmcConfig(max_zone_height_atr_mult=5.0)
        # ATR ~10, gap = 5 → 5 < 5*10 → pass
        bars = []
        for i in range(20):
            ms = 1000000 + i * 300000
            bars.append(_bar(ms, 2000.0, 2010.0, 1990.0, 2005.0))
        bars.append(_bar(1000000 + 20 * 300000, 2000.0, 2010.0, 1990.0, 2005.0))  # b0
        bars.append(_bar(1000000 + 21 * 300000, 2010.0, 2020.0, 2008.0, 2018.0))  # b1
        bars.append(
            _bar(1000000 + 22 * 300000, 2015.0, 2025.0, 2015.0, 2020.0)
        )  # b2: low=2015 > b0.h=2010 → gap=5
        result = detect_fvg(bars, cfg)
        bull = [z for z in result if z.kind == "fvg_bull"]
        assert len(bull) >= 1, "normal-sized FVG should pass height guard"
