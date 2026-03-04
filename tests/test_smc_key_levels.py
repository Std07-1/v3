"""
tests/test_smc_key_levels.py — Тести key levels (ADR-0024b).

Перевіряє:
  - compute_key_levels: PDH/PDL, DH/DL, H4, H1, M30, M15
  - Edge cases: пусті бари, 1 бар, немає completed
  - collect_htf_levels: cross-TF ін'єкція
  - Wire format: kind з'являється у to_wire()
"""
from __future__ import annotations

import unittest
from typing import List, Optional

from core.model.bars import CandleBar
from core.smc.key_levels import compute_key_levels, collect_htf_levels, KEY_LEVEL_KINDS
from core.smc.types import SmcLevel, SmcSnapshot


def _bar(
    tf_s,         # type: int
    open_ms,      # type: int
    o,            # type: float
    h,            # type: float
    low,          # type: float
    c,            # type: float
    complete=True,  # type: bool
):
    # type: (...) -> CandleBar
    return CandleBar(
        symbol="XAU/USD",
        tf_s=tf_s,
        open_time_ms=open_ms,
        close_time_ms=open_ms + tf_s * 1000,
        o=o, h=h, low=low, c=c, v=100.0,
        complete=complete, src="test",
    )


class TestComputeKeyLevels(unittest.TestCase):
    """Тести основної функції compute_key_levels."""

    def test_d1_pdh_pdl(self):
        """D1 бари → PDH/PDL з останнього completed."""
        bars = [
            _bar(86400, 1000000, 2000, 2050, 1980, 2030, complete=True),
            _bar(86400, 1086400000, 2030, 2070, 2010, 2060, complete=True),
            _bar(86400, 1172800000, 2060, 2080, 2040, 2055, complete=False),
        ]
        levels = compute_key_levels(bars)
        kinds = {lv.kind for lv in levels}

        # Prev = бар 2 (last completed), curr = бар 3 (incomplete)
        self.assertIn("pdh", kinds)
        self.assertIn("pdl", kinds)
        self.assertIn("dh", kinds)
        self.assertIn("dl", kinds)

        pdh = next(lv for lv in levels if lv.kind == "pdh")
        pdl = next(lv for lv in levels if lv.kind == "pdl")
        self.assertEqual(pdh.price, 2070.0)  # bar2.h
        self.assertEqual(pdl.price, 2010.0)  # bar2.low

        dh = next(lv for lv in levels if lv.kind == "dh")
        dl = next(lv for lv in levels if lv.kind == "dl")
        self.assertEqual(dh.price, 2080.0)   # bar3.h
        self.assertEqual(dl.price, 2040.0)   # bar3.low

    def test_h1_prev_and_current(self):
        """H1 бари → prev H1 H/L + current H1 H/L."""
        bars = [
            _bar(3600, 100000, 100, 110, 90, 105, complete=True),
            _bar(3600, 3700000, 105, 112, 98, 108, complete=True),
            _bar(3600, 7300000, 108, 115, 100, 113, complete=False),
        ]
        levels = compute_key_levels(bars)
        kinds = {lv.kind for lv in levels}

        self.assertIn("p_h1_h", kinds)
        self.assertIn("p_h1_l", kinds)
        self.assertIn("h1_h", kinds)
        self.assertIn("h1_l", kinds)

        p_h = next(lv for lv in levels if lv.kind == "p_h1_h")
        self.assertEqual(p_h.price, 112.0)  # bar2.h (last completed)

    def test_h4_levels(self):
        """H4 бари → prev H4 + current H4."""
        bars = [
            _bar(14400, 100000, 50, 55, 45, 52, complete=True),
            _bar(14400, 14500000, 52, 58, 48, 56, complete=False),
        ]
        levels = compute_key_levels(bars)
        kinds = {lv.kind for lv in levels}

        self.assertIn("p_h4_h", kinds)
        self.assertIn("p_h4_l", kinds)
        self.assertIn("h4_h", kinds)
        self.assertIn("h4_l", kinds)

    def test_m15_no_levels(self):
        """M15 бари → не генерує key levels (трейдер бачить свічки)."""
        bars = [
            _bar(900, 100000, 1.3000, 1.3050, 1.2980, 1.3020, complete=True),
            _bar(900, 1000000, 1.3020, 1.3060, 1.2990, 1.3040, complete=False),
        ]
        self.assertEqual(compute_key_levels(bars), [])

    def test_m30_no_levels(self):
        """M30 бари → не генерує key levels (трейдер бачить свічки)."""
        bars = [
            _bar(1800, 100000, 50, 55, 45, 52, complete=True),
            _bar(1800, 1900000, 52, 58, 48, 56, complete=False),
        ]
        self.assertEqual(compute_key_levels(bars), [])

    def test_empty_bars(self):
        """Пусті бари → пустий список."""
        self.assertEqual(compute_key_levels([]), [])

    def test_no_completed_bars(self):
        """Тільки incomplete → пустий (нема prev)."""
        bars = [_bar(86400, 100000, 50, 55, 45, 52, complete=False)]
        self.assertEqual(compute_key_levels(bars), [])

    def test_single_completed_no_current(self):
        """Один completed = і prev, і last. Без current levels (same bar)."""
        bars = [_bar(86400, 100000, 50, 55, 45, 52, complete=True)]
        levels = compute_key_levels(bars)
        kinds = {lv.kind for lv in levels}
        # Тільки prev (pdh/pdl), без current (dh/dl) бо last == prev
        self.assertIn("pdh", kinds)
        self.assertIn("pdl", kinds)
        self.assertNotIn("dh", kinds)
        self.assertNotIn("dl", kinds)
        self.assertEqual(len(levels), 2)

    def test_unsupported_tf(self):
        """M1 (tf_s=60) → не генерує key levels (не стратегічний якір)."""
        bars = [
            _bar(60, 100000, 50, 55, 45, 52, complete=True),
            _bar(60, 160000, 52, 56, 50, 54, complete=True),
        ]
        self.assertEqual(compute_key_levels(bars), [])

    def test_m5_no_levels(self):
        """M5 (tf_s=300) → не генерує key levels."""
        bars = [
            _bar(300, 100000, 50, 55, 45, 52, complete=True),
            _bar(300, 400000, 52, 56, 50, 54, complete=True),
        ]
        self.assertEqual(compute_key_levels(bars), [])

    def test_deterministic(self):
        """S2: same bars → same levels (детермінізм)."""
        bars = [
            _bar(3600, 100000, 100, 110, 90, 105, complete=True),
            _bar(3600, 3700000, 105, 115, 95, 110, complete=False),
        ]
        levels1 = compute_key_levels(bars)
        levels2 = compute_key_levels(bars)
        self.assertEqual(
            [lv.id for lv in levels1],
            [lv.id for lv in levels2],
        )

    def test_level_ids_unique(self):
        """All level IDs unique within single compute."""
        bars = [
            _bar(86400, 1000000, 2000, 2050, 1980, 2030, complete=True),
            _bar(86400, 1086400000, 2030, 2070, 2010, 2060, complete=False),
        ]
        levels = compute_key_levels(bars)
        ids = [lv.id for lv in levels]
        self.assertEqual(len(ids), len(set(ids)))


class TestCollectHTFLevels(unittest.TestCase):
    """Тести cross-TF ін'єкції."""

    def _make_snap(self, tf_s, levels):
        # type: (int, List[SmcLevel]) -> SmcSnapshot
        return SmcSnapshot(
            symbol="XAU/USD", tf_s=tf_s,
            zones=[], swings=[], levels=levels,
            trend_bias=None, last_bos_ms=None, last_choch_ms=None,
            computed_at_ms=0, bar_count=0,
        )

    def test_m15_gets_d1_h4_h1_levels(self):
        """M15 chart → receives D1 + H4 + H1 key levels."""
        d1_levels = [
            SmcLevel(id="pdh_XAU_USD_86400_100", symbol="XAU/USD",
                     tf_s=86400, kind="pdh", price=2050.0, time_ms=100, touches=1),
            SmcLevel(id="eq_highs_XAU_USD_86400_200", symbol="XAU/USD",
                     tf_s=86400, kind="eq_highs", price=2060.0, time_ms=200, touches=3),
        ]
        h1_levels = [
            SmcLevel(id="p_h1_h_XAU_USD_3600_300", symbol="XAU/USD",
                     tf_s=3600, kind="p_h1_h", price=2045.0, time_ms=300, touches=1),
        ]

        snaps = {
            ("XAU/USD", 86400): self._make_snap(86400, d1_levels),
            ("XAU/USD", 3600):  self._make_snap(3600, h1_levels),
        }

        def get_snap(sym, tf):
            return snaps.get((sym, tf))

        htf = collect_htf_levels(get_snap, "XAU/USD", 900)  # M15 = 900

        kinds = {lv.kind for lv in htf}
        # pdh ін'єктується (key level kind), eq_highs — НЕ ін'єктується (liquidity, not key)
        self.assertIn("pdh", kinds)
        self.assertNotIn("eq_highs", kinds)
        self.assertIn("p_h1_h", kinds)

    def test_d1_no_injection(self):
        """D1 chart → no higher TF → empty."""
        htf = collect_htf_levels(
            lambda s, t: None, "XAU/USD", 86400,
        )
        self.assertEqual(htf, [])

    def test_dedup(self):
        """Levels with same id across TFs → no duplicates."""
        lv = SmcLevel(id="pdh_XAU_USD_86400_100", symbol="XAU/USD",
                      tf_s=86400, kind="pdh", price=2050.0, time_ms=100, touches=1)

        snaps = {("XAU/USD", 86400): self._make_snap(86400, [lv, lv])}

        def get_snap(sym, tf):
            return snaps.get((sym, tf))

        htf = collect_htf_levels(get_snap, "XAU/USD", 900)
        ids = [l.id for l in htf]
        self.assertEqual(len(ids), len(set(ids)))


class TestWireFormat(unittest.TestCase):
    """Перевірка wire format з kind."""

    def test_to_wire_includes_kind(self):
        """SmcLevel.to_wire() містить kind для UI styling."""
        lv = SmcLevel(
            id="pdh_XAU_USD_86400_205000",
            symbol="XAU/USD", tf_s=86400,
            kind="pdh", price=2050.0, time_ms=100000, touches=1,
        )
        wire = lv.to_wire()
        self.assertEqual(wire["kind"], "pdh")
        self.assertEqual(wire["price"], 2050.0)
        self.assertIn("t_ms", wire)
        # color field removed — UI handles styling via kind
        self.assertNotIn("color", wire)

    def test_key_level_kinds_complete(self):
        """KEY_LEVEL_KINDS contains all expected kinds (D1+H4+H1 only)."""
        expected = {"pdh", "pdl", "dh", "dl",
                    "p_h4_h", "p_h4_l", "h4_h", "h4_l",
                    "p_h1_h", "p_h1_l", "h1_h", "h1_l"}
        self.assertEqual(KEY_LEVEL_KINDS, expected)


class TestEngineIntegration(unittest.TestCase):
    """Перевірка інтеграції з SmcEngine."""

    def test_engine_snapshot_has_key_levels(self):
        """SmcEngine.update() з D1 барами → snapshot містить PDH/PDL."""
        from core.smc.config import SmcConfig
        from core.smc.engine import SmcEngine

        cfg = SmcConfig()
        engine = SmcEngine(cfg)

        bars = [
            _bar(86400, 1000000000, 2000, 2050, 1980, 2030, complete=True),
            _bar(86400, 1086400000, 2030, 2070, 2010, 2060, complete=True),
            _bar(86400, 1172800000, 2060, 2080, 2040, 2055, complete=False),
        ]
        snap = engine.update("XAU/USD", 86400, bars)
        kinds = {lv.kind for lv in snap.levels}

        self.assertIn("pdh", kinds)
        self.assertIn("pdl", kinds)
        self.assertIn("dh", kinds)
        self.assertIn("dl", kinds)

    def test_engine_htf_levels_injection(self):
        """get_snapshot_with_htf_levels: M15 chart sees D1 levels."""
        from core.smc.config import SmcConfig
        from core.smc.engine import SmcEngine

        cfg = SmcConfig()
        engine = SmcEngine(cfg)

        # Warmup D1
        d1_bars = [
            _bar(86400, 1000000000, 2000, 2050, 1980, 2030, complete=True),
            _bar(86400, 1086400000, 2030, 2070, 2010, 2060, complete=True),
        ]
        engine.update("XAU/USD", 86400, d1_bars)

        # Warmup M15
        m15_bars = [
            _bar(900, 1086400000, 2060, 2065, 2055, 2062, complete=True),
            _bar(900, 1087300000, 2062, 2068, 2058, 2066, complete=True),
        ]
        engine.update("XAU/USD", 900, m15_bars)

        # M15 snapshot without HTF
        snap_basic = engine.get_snapshot("XAU/USD", 900)
        basic_kinds = {lv.kind for lv in snap_basic.levels}

        # M15 snapshot with HTF injection
        snap_htf = engine.get_snapshot_with_htf_levels("XAU/USD", 900)
        htf_kinds = {lv.kind for lv in snap_htf.levels}

        # PDH/PDL should only appear in HTF-enriched snapshot
        self.assertNotIn("pdh", basic_kinds)
        self.assertIn("pdh", htf_kinds)
        self.assertIn("pdl", htf_kinds)

        # M15 own levels: M15 no longer generates key levels
        self.assertNotIn("p_m15_h", htf_kinds)

    def test_proximity_filter_disabled(self):
        """Display filter no longer removes levels by proximity."""
        from core.smc.config import SmcConfig
        from core.smc.engine import SmcEngine

        cfg = SmcConfig()
        engine = SmcEngine(cfg)

        # Bar set з великим розмахом (levels будуть далеко від ціни)
        bars = [
            _bar(3600, 100000, 100, 200, 50, 150, complete=True),    # prev: H=200, L=50
            _bar(3600, 3700000, 150, 155, 145, 152, complete=False), # current: near 152
        ]
        snap = engine.update("XAU/USD", 3600, bars)

        # Prev H1 H at 200.0 is far from current price 152
        # With old proximity filter, it would be dropped. Now it stays.
        kinds = {lv.kind for lv in snap.levels}
        self.assertIn("p_h1_h", kinds)   # prev H1 high = 200
        p_h1_h = next(lv for lv in snap.levels if lv.kind == "p_h1_h")
        self.assertEqual(p_h1_h.price, 200.0)


if __name__ == "__main__":
    unittest.main()
