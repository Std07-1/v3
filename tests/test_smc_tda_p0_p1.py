"""
tests/test_smc_tda_p0_p1.py — ADR-0034 MVP: P0 (IFVG) + P1 (Breaker) tests.

Verify gate:
  P0: FVG filled → IFVG created with inverted kind + same boundaries + origin_zone_id
  P0: IFVG not created when tda.enabled=False (default)
  P0: IFVG lifecycle preserved on re-call (no overwrite of existing lifecycle state)
  P1: mitigated OB + CHoCH opposite → status="breaker"
  P1: mitigated OB without CHoCH → stays "mitigated"
  P1: breaker not deleted by mitigated TTL
"""

from __future__ import annotations

import dataclasses

import pytest

from core.model.bars import CandleBar
from core.smc.config import SmcConfig, SmcTdaConfig
from core.smc.engine import _update_zone_lifecycle
from core.smc.fvg import detect_fvg
from core.smc.types import SmcSwing, SmcZone


# ── Helpers ───────────────────────────────────────────────────────────────


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
    anchor_ms: int = 1_000_000,
    status: str = "active",
    end_ms: int = None,
    strength: float = 0.8,
    sym: str = "XAU/USD",
    tf_s: int = 300,
) -> SmcZone:
    return SmcZone(
        id="{}_{}_{}_{}".format(kind, sym, tf_s, anchor_ms),
        symbol=sym,
        tf_s=tf_s,
        kind=kind,
        start_ms=anchor_ms,
        end_ms=end_ms,
        high=high,
        low=low,
        status=status,
        strength=strength,
        anchor_bar_ms=anchor_ms,
    )


def _choch(kind: str, time_ms: int, sym: str = "XAU/USD", tf_s: int = 300) -> SmcSwing:
    return SmcSwing(
        id="{}_{}_{}_{}".format(kind, sym, tf_s, time_ms),
        symbol=sym,
        tf_s=tf_s,
        kind=kind,
        price=2000.0,
        time_ms=time_ms,
        confirmed=True,
    )


def _cfg_tda_on(**kwargs) -> SmcConfig:
    """SmcConfig with tda.enabled=True."""
    tda = SmcTdaConfig(enabled=True, **kwargs)
    return dataclasses.replace(SmcConfig(max_zones_per_tf=10), tda=tda)


# ── P0: IFVG Detection ────────────────────────────────────────────────────


class TestIfvgDetection:
    """P0: FVG fill creates IFVG zone via detect_fvg()."""

    def _make_bull_fvg_bars(self):
        """3 bars forming a bullish FVG: b0.high < b2.low."""
        sym, tf = "XAU/USD", 300
        return [
            _bar(0,       o=1990, h=1995, low=1985, c=1993, tf_s=tf, sym=sym),  # b0
            _bar(300_000, o=1996, h=2010, low=1994, c=2008, tf_s=tf, sym=sym),  # b1 (anchor)
            _bar(600_000, o=2005, h=2020, low=2000, c=2015, tf_s=tf, sym=sym),  # b2 gap: b0.h=1995 < b2.low=2000
            # fill bar: close below b0.h (1995)
            _bar(900_000, o=1998, h=2002, low=1990, c=1992, tf_s=tf, sym=sym),
        ]

    def test_ifvg_not_created_when_tda_disabled(self):
        """Default config (tda.enabled=False) → no IFVGs."""
        bars = self._make_bull_fvg_bars()
        cfg = SmcConfig()
        assert not cfg.tda.enabled
        zones = detect_fvg(bars, cfg)
        ifvg = [z for z in zones if z.kind.startswith("ifvg")]
        assert ifvg == [], "No IFVGs when TDA disabled"

    def test_ifvg_created_from_filled_bull_fvg(self):
        """fvg_bull filled → ifvg_bear with same boundaries."""
        bars = self._make_bull_fvg_bars()
        cfg = _cfg_tda_on(ifvg_enabled=True)
        zones = detect_fvg(bars, cfg)

        ifvg = [z for z in zones if z.kind == "ifvg_bear"]
        assert len(ifvg) == 1, f"Expected 1 ifvg_bear, got {len(ifvg)}"

        iz = ifvg[0]
        # boundaries = same as source FVG (b0.high=1995, b2.low=2000)
        assert iz.low == 1995, f"IFVG low should match source FVG low=b0.h: {iz.low}"
        assert iz.high == 2000, f"IFVG high should match source FVG high=b2.low: {iz.high}"
        assert iz.status == "active"
        assert iz.origin_zone_id is not None
        assert "fvg_bull" in iz.origin_zone_id

    def test_ifvg_bear_from_filled_bear_fvg(self):
        """fvg_bear filled → ifvg_bull."""
        sym, tf = "XAU/USD", 300
        bars = [
            _bar(0,       o=2020, h=2025, low=2015, c=2016, tf_s=tf, sym=sym),  # b0: low=2015
            _bar(300_000, o=2013, h=2014, low=2005, c=2007, tf_s=tf, sym=sym),  # b1 (anchor)
            _bar(600_000, o=2008, h=2010, low=2000, c=2003, tf_s=tf, sym=sym),  # b2: h=2010 < b0.low=2015 → bear FVG
            # fill bar: close above b0.low (2015)
            _bar(900_000, o=2012, h=2020, low=2010, c=2018, tf_s=tf, sym=sym),
        ]
        cfg = _cfg_tda_on(ifvg_enabled=True)
        zones = detect_fvg(bars, cfg)
        ifvg = [z for z in zones if z.kind == "ifvg_bull"]
        assert len(ifvg) == 1
        assert ifvg[0].status == "active"

    def test_ifvg_lifecycle_preserved_on_recall(self):
        """IFVG in active_zones keeps lifecycle status when detect_fvg re-runs."""
        bars = self._make_bull_fvg_bars()
        cfg = _cfg_tda_on(ifvg_enabled=True)

        # First call creates IFVG
        zones_first = detect_fvg(bars, cfg)
        ifvg_first = [z for z in zones_first if z.kind == "ifvg_bear"][0]

        # Simulate active_zones with the IFVG already tested
        active = {ifvg_first.id: dataclasses.replace(ifvg_first, status="tested")}

        # Run lifecycle with the same fresh_zones (IFVG in fresh again)
        result = _update_zone_lifecycle(zones_first, active, None, cfg, 300)

        ifvg_result = [z for z in result if z.kind == "ifvg_bear"]
        assert len(ifvg_result) == 1
        assert ifvg_result[0].status == "tested", (
            "IFVG lifecycle status must be preserved (no overwrite with 'active')"
        )

    def test_ifvg_wire_includes_origin_zone_id(self):
        """to_wire() includes origin_zone_id for IFVG zones."""
        bars = self._make_bull_fvg_bars()
        cfg = _cfg_tda_on(ifvg_enabled=True)
        zones = detect_fvg(bars, cfg)
        ifvg = [z for z in zones if z.kind == "ifvg_bear"][0]
        wire = ifvg.to_wire()
        assert "origin_zone_id" in wire
        assert wire["origin_zone_id"] == ifvg.origin_zone_id


# ── P1: Breaker Transition ────────────────────────────────────────────────


class TestBreakerTransition:
    """P1: mitigated OB + CHoCH opposite → status='breaker'."""

    def _run_lifecycle(self, zone, bar, choch_events, cfg=None):
        """Helper: run lifecycle with one zone + one bar + optional CHoCH events."""
        if cfg is None:
            cfg = _cfg_tda_on(breaker_enabled=True)
        active = {zone.id: zone}
        return _update_zone_lifecycle([zone], active, bar, cfg, 300, struct_events=choch_events)

    def test_ob_bull_mitigated_plus_choch_bear_becomes_breaker(self):
        """ob_bull mitigated → choch_bear after → breaker."""
        z = _zone("ob_bull", high=2050, low=2040, anchor_ms=100_000,
                  status="mitigated", end_ms=200_000)
        bar = _bar(500_000, o=2045, h=2055, low=2035, c=2042)
        choch = _choch("choch_bear", time_ms=300_000)  # after end_ms=200_000

        result = self._run_lifecycle(z, bar, [choch])
        breakers = [r for r in result if r.status == "breaker"]
        assert len(breakers) == 1
        assert breakers[0].id == z.id

    def test_ob_bear_mitigated_plus_choch_bull_becomes_breaker(self):
        """ob_bear mitigated → choch_bull after → breaker."""
        z = _zone("ob_bear", high=2060, low=2050, anchor_ms=100_000,
                  status="mitigated", end_ms=200_000)
        bar = _bar(500_000, o=2055, h=2065, low=2045, c=2052)
        choch = _choch("choch_bull", time_ms=300_000)

        result = self._run_lifecycle(z, bar, [choch])
        breakers = [r for r in result if r.status == "breaker"]
        assert len(breakers) == 1

    def test_ob_bull_mitigated_no_choch_stays_mitigated(self):
        """ob_bull mitigated + no CHoCH → stays mitigated."""
        z = _zone("ob_bull", high=2050, low=2040, anchor_ms=100_000,
                  status="mitigated", end_ms=200_000)
        # large TTL so zone survives
        cfg = dataclasses.replace(_cfg_tda_on(breaker_enabled=True),
                                  display=dataclasses.replace(
                                      _cfg_tda_on().display,
                                      mitigated_ttl_bars=1000))
        active = {z.id: z}
        bar = _bar(500_000, o=2045, h=2055, low=2035, c=2042)
        result = _update_zone_lifecycle([z], active, bar, cfg, 300, struct_events=[])
        statuses = [r.status for r in result if r.id == z.id]
        assert statuses == ["mitigated"], f"No CHoCH → stays mitigated, got {statuses}"

    def test_choch_before_mitigation_does_not_trigger_breaker(self):
        """CHoCH that occurred BEFORE OB mitigation → no breaker."""
        z = _zone("ob_bull", high=2050, low=2040, anchor_ms=100_000,
                  status="mitigated", end_ms=300_000)
        bar = _bar(500_000, o=2045, h=2055, low=2035, c=2042)
        choch = _choch("choch_bear", time_ms=200_000)  # BEFORE end_ms=300_000

        result = self._run_lifecycle(z, bar, [choch])
        for r in result:
            if r.id == z.id:
                assert r.status != "breaker", "CHoCH before mitigation must not trigger breaker"

    def test_breaker_not_deleted_by_ttl(self):
        """Breaker OB survives mitigated TTL (different status)."""
        z = _zone("ob_bull", high=2050, low=2040, anchor_ms=100_000,
                  status="mitigated", end_ms=100_300)  # mitigated 1 bar after anchor
        # TTL = 2 bars → zone would normally be deleted after 2 bars past end_ms
        cfg = _cfg_tda_on(breaker_enabled=True)
        cfg = dataclasses.replace(
            cfg,
            display=dataclasses.replace(cfg.display, mitigated_ttl_bars=2)
        )
        # bar is 10 bars after end_ms → TTL would delete a mitigated zone
        bar = _bar(100_300 + 10 * 300_000, o=2045, h=2055, low=2035, c=2042)
        choch = _choch("choch_bear", time_ms=100_300 + 1 * 300_000)

        active = {z.id: z}
        result = _update_zone_lifecycle([z], active, bar, cfg, 300, struct_events=[choch])
        # Zone promoted to breaker → NOT deleted by TTL
        assert any(r.id == z.id and r.status == "breaker" for r in result), (
            "Breaker must survive TTL deletion"
        )

    def test_breaker_disabled_in_tda_off(self):
        """tda.enabled=False → no breaker promotion even with CHoCH."""
        cfg = SmcConfig(max_zones_per_tf=10)  # tda.enabled=False by default
        z = _zone("ob_bull", high=2050, low=2040, anchor_ms=100_000,
                  status="mitigated", end_ms=200_000)
        bar = _bar(500_000, o=2045, h=2055, low=2035, c=2042)
        choch = _choch("choch_bear", time_ms=300_000)
        # large TTL
        cfg = dataclasses.replace(
            cfg,
            display=dataclasses.replace(cfg.display, mitigated_ttl_bars=1000)
        )
        active = {z.id: z}
        result = _update_zone_lifecycle([z], active, bar, cfg, 300, struct_events=[choch])
        for r in result:
            if r.id == z.id:
                assert r.status == "mitigated", "TDA disabled → no breaker promotion"
