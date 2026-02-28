"""
tests/test_smc_e1.py — SMC Engine E1 контрактні тести (ADR-0024).

Перевіряє:
  S2: детермінізм (same bars → same snapshot)
  S3: детерміністичні IDs (make_zone_id, make_swing_id, make_level_id)
  S4: on_bar() з incomplete bar — ігнорується
  S6: wire format відповідає контракту (ключі + типи)
  E1: FVG-патерн → виявлена bull_fvg зона
  E1: синтетичні бари без патернів → engine не падає, повертає валідний snapshot
  has_changes: SmcDelta.has_changes правильно рахує зміни

Python 3.7 compatible.
"""
from __future__ import annotations

import time
from typing import List

import pytest

from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.engine import SmcEngine
from core.smc.types import (
    SmcDelta,
    SmcSnapshot,
    make_level_id,
    make_swing_id,
    make_zone_id,
)

# ──────────────────────────────────────────────────────────────
#  Helpers: фабрика барів
# ──────────────────────────────────────────────────────────────

SYM = "XAU/USD"
TF = 60  # M1
T0 = 1_700_000_000_000  # arbitrary epoch ms
BAR_MS = TF * 1000


def _bar(i: int, o: float, h: float, low: float, c: float, complete: bool = True) -> CandleBar:
    """Допоміжна фабрика бару."""
    open_ms = T0 + i * BAR_MS
    return CandleBar(
        symbol=SYM,
        tf_s=TF,
        open_time_ms=open_ms,
        close_time_ms=open_ms + BAR_MS,
        o=o, h=h, low=low, c=c,
        v=1000.0,
        complete=complete,
        src="derived",
    )


def _flat_bars(n: int, price: float = 1900.0) -> List[CandleBar]:
    """n однакових флат-барів (жодного паттерну SMC)."""
    return [_bar(i, price, price + 1.0, price - 1.0, price) for i in range(n)]


def _make_engine() -> SmcEngine:
    cfg = SmcConfig.from_dict({
        "enabled": True,
        "lookback_bars": 200,
        "swing_period": 2,  # малий для тестів
        "ob": {"enabled": True, "min_impulse_atr_mult": 0.5, "atr_period": 5, "max_active_per_side": 5},
        "fvg": {"enabled": True, "min_gap_atr_mult": 0.0, "max_active": 10},
        "structure": {"enabled": True, "confirmation_bars": 1},
        "max_zones_per_tf": 30,
        "performance": {"max_compute_ms": 500, "log_slow_threshold_ms": 100},
    })
    return SmcEngine(cfg)


# ──────────────────────────────────────────────────────────────
#  S2: Детермінізм
# ──────────────────────────────────────────────────────────────

def test_s2_determinism_same_bars_same_snapshot() -> None:
    """S2: engine.update() з тими самими барами → ідентичний snapshot."""
    bars = _flat_bars(30)

    eng_a = _make_engine()
    snap_a = eng_a.update(SYM, TF, bars)

    eng_b = _make_engine()
    snap_b = eng_b.update(SYM, TF, bars)

    assert snap_a.zones == snap_b.zones, "S2 порушено: zones відрізняються"
    assert snap_a.swings == snap_b.swings, "S2 порушено: swings відрізняються"
    assert snap_a.trend_bias == snap_b.trend_bias, "S2 порушено: trend_bias відрізняється"


def test_s2_double_update_same_snapshot() -> None:
    """S2: два виклики update() з тими самими барами → той самий результат."""
    bars = _flat_bars(30)
    eng = _make_engine()

    snap1 = eng.update(SYM, TF, bars)
    snap2 = eng.update(SYM, TF, bars)

    assert snap1.zones == snap2.zones
    assert snap1.swings == snap2.swings


# ──────────────────────────────────────────────────────────────
#  S3: Детерміністичні IDs
# ──────────────────────────────────────────────────────────────

def test_s3_make_zone_id_deterministic() -> None:
    """S3: make_zone_id повертає однаковий результат для однакових аргументів."""
    id1 = make_zone_id("ob_bull", "XAU/USD", 60, 1_700_000_060_000)
    id2 = make_zone_id("ob_bull", "XAU/USD", 60, 1_700_000_060_000)
    assert id1 == id2


def test_s3_make_zone_id_format() -> None:
    """S3: zone ID містить kind, безпечний symbol, tf_s, anchor_ms."""
    zid = make_zone_id("ob_bear", "XAU/USD", 300, 1234567)
    assert "ob_bear" in zid
    assert "XAU_USD" in zid   # / → _
    assert "300" in zid
    assert "1234567" in zid


def test_s3_make_swing_id_format() -> None:
    """S3: swing ID містить kind, symbol_safe, tf_s, time_ms."""
    sid = make_swing_id("hh", "EUR/USD", 60, 9999000)
    assert "hh" in sid
    assert "EUR_USD" in sid
    assert "60" in sid
    assert "9999000" in sid


def test_s3_make_level_id_stable_for_close_prices() -> None:
    """S3: make_level_id стабільний для цін різного float repr."""
    lid1 = make_level_id("eq_highs", "XAU/USD", 60, 1900.0)
    lid2 = make_level_id("eq_highs", "XAU/USD", 60, 1900.0)
    assert lid1 == lid2


# ──────────────────────────────────────────────────────────────
#  S4: on_bar() ігнорує incomplete бари
# ──────────────────────────────────────────────────────────────

def test_s4_on_bar_skips_incomplete() -> None:
    """S4: preview bar (complete=False) → delta без нових зон/свінгів."""
    eng = _make_engine()

    # Спочатку прогріємо двома complete-барами (замало для паттернів)
    eng.update(SYM, TF, _flat_bars(5))

    # Preview bar → має бути проігноровано (empty delta)
    preview = _bar(99, 1900.0, 1901.0, 1899.0, 1900.5, complete=False)
    delta = eng.on_bar(preview)

    assert isinstance(delta, SmcDelta)
    assert not delta.has_changes, "on_bar() не повинен обробляти preview-бари"


def test_s4_on_bar_processes_complete() -> None:
    """S4: complete bar після warmup → delta повертається без помилок."""
    eng = _make_engine()
    eng.update(SYM, TF, _flat_bars(20))

    complete_bar = _bar(20, 1900.0, 1901.0, 1899.0, 1900.5, complete=True)
    delta = eng.on_bar(complete_bar)

    assert isinstance(delta, SmcDelta)
    # has_changes може бути True або False — головне що об'єкт коректний
    assert delta.symbol == SYM
    assert delta.tf_s == TF


# ──────────────────────────────────────────────────────────────
#  S6: Wire format контракти
# ──────────────────────────────────────────────────────────────

def test_s6_snapshot_to_wire_has_required_keys() -> None:
    """S6: SmcSnapshot.to_wire() повертає {zones, swings, levels}."""
    eng = _make_engine()
    snap = eng.update(SYM, TF, _flat_bars(30))
    wire = snap.to_wire()

    assert "zones" in wire
    assert "swings" in wire
    assert "levels" in wire
    assert isinstance(wire["zones"], list)
    assert isinstance(wire["swings"], list)
    assert isinstance(wire["levels"], list)


def test_s6_zone_wire_has_required_fields() -> None:
    """S6: якщо є зони — кожна має ключі wire-контракту."""
    from core.smc.types import SmcZone

    zone = SmcZone(
        id="ob_bull_XAU_USD_60_1234567",
        symbol=SYM, tf_s=TF, kind="ob_bull",
        start_ms=1234567, end_ms=None,
        high=1905.0, low=1900.0,
        status="active", strength=0.7,
        anchor_bar_ms=1234567,
    )
    wire = zone.to_wire()

    required = {"id", "start_ms", "end_ms", "high", "low", "kind", "status", "strength"}
    assert required == set(wire.keys()), f"Wire format mismatch: {set(wire.keys())}"


def test_s6_swing_wire_has_required_fields() -> None:
    """S6: SmcSwing.to_wire() повертає {id, a, b, label}."""
    from core.smc.types import SmcSwing

    swing = SmcSwing(
        id="hh_XAU_USD_60_9000000",
        symbol=SYM, tf_s=TF, kind="hh",
        price=1910.0, time_ms=9000000, confirmed=True,
    )
    wire = swing.to_wire()

    assert "id" in wire
    assert "a" in wire
    assert "b" in wire
    assert "label" in wire


def test_s6_delta_wire_has_required_fields() -> None:
    """S6: SmcDelta.to_wire() повертає всі поля delta-контракту."""
    from core.smc.types import SmcDelta

    delta = SmcDelta(
        symbol=SYM, tf_s=TF, bar_open_ms=T0,
        new_zones=[], mitigated_zones=[], updated_zones=[],
        new_swings=[], new_levels=[], removed_levels=[],
        trend_bias=None,
    )
    wire = delta.to_wire()

    required = {
        "new_zones", "mitigated_zone_ids", "updated_zones",
        "new_swings", "new_levels", "removed_level_ids", "trend_bias",
    }
    assert required == set(wire.keys()), f"Delta wire mismatch: {set(wire.keys())}"


# ──────────────────────────────────────────────────────────────
#  E1: FVG detection
# ──────────────────────────────────────────────────────────────

def _fvg_bull_bars() -> List[CandleBar]:
    """
    Синтетичні бари з бичачим FVG:
      b0: low=1900, h=1910
      b1 (impulse): gap
      b2: low=1912 (> b0.h → gap = b2.low - b0.h > 0 → bullish FVG)

    Додаємо 20 плоских барів перед та після для контексту.
    """
    bars: List[CandleBar] = []
    # 20 нейтральних барів
    for i in range(20):
        bars.append(_bar(i, 1900.0, 1902.0, 1898.0, 1900.0))
    # b0: звичайний бар
    n = len(bars)
    bars.append(_bar(n, 1900.0, 1910.0, 1899.0, 1908.0))
    # b1: сильний рух вгору
    n = len(bars)
    bars.append(_bar(n, 1908.0, 1920.0, 1907.0, 1918.0))
    # b2: low > b0.h → FVG між b0.h=1910 і b2.low=1912
    n = len(bars)
    bars.append(_bar(n, 1915.0, 1925.0, 1912.0, 1923.0))
    # ще кілька барів після
    for j in range(5):
        n = len(bars)
        bars.append(_bar(n, 1920.0, 1922.0, 1918.0, 1920.0))
    return bars


def test_e1_fvg_bull_detected() -> None:
    """E1: бичачий FVG-паттерн → в snapshot є fvg_bull зона."""
    eng = _make_engine()
    bars = _fvg_bull_bars()
    snap = eng.update(SYM, TF, bars)

    fvg_bull_zones = [z for z in snap.zones if z.kind == "fvg_bull"]
    assert len(fvg_bull_zones) >= 1, (
        f"E1: fvg_bull не виявлено. Всі зони: {[z.kind for z in snap.zones]}"
    )


def test_e1_fvg_zone_geometry_valid() -> None:
    """E1: FVG зона має high > low (базова геометрія)."""
    eng = _make_engine()
    bars = _fvg_bull_bars()
    snap = eng.update(SYM, TF, bars)

    for zone in snap.zones:
        assert zone.high > zone.low, f"Зона {zone.id}: high <= low ({zone.high} <= {zone.low})"


# ──────────────────────────────────────────────────────────────
#  E1: Empty / small dataset — no crash
# ──────────────────────────────────────────────────────────────

def test_e1_empty_bars_no_crash() -> None:
    """E1: порожній список барів → валідний snapshot без зон/свінгів."""
    eng = _make_engine()
    snap = eng.update(SYM, TF, [])

    assert isinstance(snap, SmcSnapshot)
    assert snap.zones == []
    assert snap.swings == []
    assert snap.bar_count == 0


def test_e1_few_bars_no_crash() -> None:
    """E1: 3 бари (менше ніж swing_period window) → валідний snapshot."""
    eng = _make_engine()
    snap = eng.update(SYM, TF, _flat_bars(3))

    assert isinstance(snap, SmcSnapshot)
    assert snap.symbol == SYM
    assert snap.tf_s == TF


# ──────────────────────────────────────────────────────────────
#  SmcDelta.has_changes
# ──────────────────────────────────────────────────────────────

def test_has_changes_empty_delta() -> None:
    """SmcDelta.has_changes == False для пустого delta."""
    delta = SmcDelta(
        symbol=SYM, tf_s=TF, bar_open_ms=T0,
        new_zones=[], mitigated_zones=[], updated_zones=[],
        new_swings=[], new_levels=[], removed_levels=[],
        trend_bias=None,
    )
    assert not delta.has_changes


def test_has_changes_with_new_zone() -> None:
    """SmcDelta.has_changes == True якщо є new_zones."""
    from core.smc.types import SmcZone

    zone = SmcZone(
        id="ob_bull_XAU_USD_60_1234567",
        symbol=SYM, tf_s=TF, kind="ob_bull",
        start_ms=1234567, end_ms=None,
        high=1905.0, low=1900.0,
        status="active", strength=0.7,
        anchor_bar_ms=1234567,
    )
    delta = SmcDelta(
        symbol=SYM, tf_s=TF, bar_open_ms=T0,
        new_zones=[zone], mitigated_zones=[], updated_zones=[],
        new_swings=[], new_levels=[], removed_levels=[],
        trend_bias=None,
    )
    assert delta.has_changes


# ──────────────────────────────────────────────────────────────
#  SmcConfig: from_dict defaults
# ──────────────────────────────────────────────────────────────

def test_config_from_dict_defaults() -> None:
    """SmcConfig.from_dict({}) не падає, дає розумні defaults."""
    cfg = SmcConfig.from_dict({})
    assert cfg.lookback_bars > 0
    assert cfg.swing_period > 0
    assert cfg.performance.max_compute_ms > 0


def test_config_from_dict_overrides() -> None:
    """SmcConfig.from_dict() коректно читає overrides."""
    cfg = SmcConfig.from_dict({"lookback_bars": 123, "swing_period": 7})
    assert cfg.lookback_bars == 123
    assert cfg.swing_period == 7
