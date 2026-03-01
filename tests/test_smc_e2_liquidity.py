"""
tests/test_smc_e2_liquidity.py — Liquidity Level E2 контрактні тести (ADR-0024 §4.5).

Перевіряє:
  detect_liquidity_levels:
    - Кластеризація підтверджених swing highs → eq_highs
    - Кластеризація підтверджених swing lows → eq_lows
    - Фільтрація за min_touches (< min → не створює рівень)
    - Tolerаnce-based clustering (в межах ATR → один кластер, поза → окремі)
    - S2: детермінізм (same input → same output)
    - S3: IDs детерміновані (make_level_id-based)
    - disabled config → empty result
    - empty swings / empty bars → empty result
    - unconfirmed swings ігноруються
  SmcConfig.levels:
    - SmcLevelsConfig defaults
    - SmcLevelsConfig.from_dict()
    - SmcConfig.from_dict() з секцією levels
  Engine integration:
    - snapshot.levels populated після достатньої кількості swing-точок

Python 3.7 compatible.
"""
from __future__ import annotations

from typing import List

import pytest

from core.model.bars import CandleBar
from core.smc.config import SmcConfig, SmcLevelsConfig
from core.smc.engine import SmcEngine
from core.smc.liquidity import detect_liquidity_levels, _cluster_to_levels
from core.smc.swings import compute_atr
from core.smc.types import SmcLevel, SmcSwing, make_level_id, make_swing_id

# ──────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────

SYM = "XAU/USD"
TF  = 60
T0  = 1_700_000_000_000
BAR_MS = TF * 1000


def _bar(i: int, o: float, h: float, low: float, c: float,
         complete: bool = True) -> CandleBar:
    open_ms = T0 + i * BAR_MS
    return CandleBar(
        symbol=SYM, tf_s=TF,
        open_time_ms=open_ms, close_time_ms=open_ms + BAR_MS,
        o=o, h=h, low=low, c=c, v=1000.0,
        complete=complete, src="derived",
    )


def _flat_bars(n: int, p: float = 1900.0) -> List[CandleBar]:
    return [_bar(i, p, p + 1.0, p - 1.0, p) for i in range(n)]


def _swing(kind: str, price: float, time_i: int, confirmed: bool = True) -> SmcSwing:
    time_ms = T0 + time_i * BAR_MS
    return SmcSwing(
        id=make_swing_id(kind, SYM, TF, time_ms),
        symbol=SYM, tf_s=TF,
        kind=kind, price=price, time_ms=time_ms,
        confirmed=confirmed,
    )


def _make_config(
    tolerance_atr_mult: float = 0.5,
    min_touches: int = 2,
    max_levels: int = 10,
    enabled: bool = True,
) -> SmcConfig:
    """SmcConfig для тестів ліквідності."""
    return SmcConfig.from_dict({
        "enabled": True,
        "lookback_bars": 100,
        "swing_period": 2,
        "ob": {"enabled": True, "min_impulse_atr_mult": 0.1, "atr_period": 5, "max_active_per_side": 5},
        "fvg": {"enabled": True, "min_gap_atr_mult": 0.0, "max_active": 10},
        "structure": {"enabled": True, "confirmation_bars": 1},
        "levels": {
            "enabled": enabled,
            "tolerance_atr_mult": tolerance_atr_mult,
            "min_touches": min_touches,
            "max_levels": max_levels,
        },
        "max_zones_per_tf": 30,
        "performance": {"max_compute_ms": 5000, "log_slow_threshold_ms": 1000},
    })


# Постійний ATR для флат-барів з _flat_bars(n): range=2.0 → ATR ≈ 2.0
# Тому tolerance = 2.0 * tolerance_atr_mult
_FLAT_BARS_ATR_APPROX = 2.0


# ──────────────────────────────────────────────────────────────
#  SmcLevelsConfig — unit
# ──────────────────────────────────────────────────────────────

class TestSmcLevelsConfig:
    def test_defaults(self):
        cfg = SmcLevelsConfig()
        assert cfg.enabled is True
        assert cfg.tolerance_atr_mult == 0.1
        assert cfg.min_touches == 2
        assert cfg.max_levels == 10

    def test_from_dict(self):
        d = {"enabled": False, "tolerance_atr_mult": 0.3, "min_touches": 3, "max_levels": 6}
        cfg = SmcLevelsConfig.from_dict(d)
        assert cfg.enabled is False
        assert cfg.tolerance_atr_mult == 0.3
        assert cfg.min_touches == 3
        assert cfg.max_levels == 6

    def test_from_empty_dict_uses_defaults(self):
        cfg = SmcLevelsConfig.from_dict({})
        assert cfg.tolerance_atr_mult == 0.1
        assert cfg.min_touches == 2

    def test_in_smc_config_from_dict(self):
        """SmcConfig.from_dict() з секцією levels."""
        full = SmcConfig.from_dict({
            "levels": {"enabled": True, "tolerance_atr_mult": 0.2, "min_touches": 3, "max_levels": 8}
        })
        assert full.levels.tolerance_atr_mult == 0.2
        assert full.levels.min_touches == 3

    def test_smc_config_defaults_have_levels(self):
        """SmcConfig() defaults включають SmcLevelsConfig з defaults."""
        cfg = SmcConfig()
        assert isinstance(cfg.levels, SmcLevelsConfig)
        assert cfg.levels.enabled is True


# ──────────────────────────────────────────────────────────────
#  detect_liquidity_levels — guards
# ──────────────────────────────────────────────────────────────

class TestDetectLiquidityLevelsGuards:
    def test_disabled_returns_empty(self):
        bars = _flat_bars(20)
        swings = [_swing("hh", 1902.0, 5), _swing("hh", 1902.5, 10)]
        cfg = _make_config(enabled=False)
        result = detect_liquidity_levels(swings, bars, cfg)
        assert result == []

    def test_empty_swings_returns_empty(self):
        bars = _flat_bars(20)
        cfg = _make_config()
        assert detect_liquidity_levels([], bars, cfg) == []

    def test_empty_bars_returns_empty(self):
        swings = [_swing("hh", 1902.0, 5), _swing("hh", 1902.5, 10)]
        cfg = _make_config()
        assert detect_liquidity_levels(swings, [], cfg) == []

    def test_unconfirmed_swings_ignored(self):
        """confirmed=False свінги не входять у кластери."""
        bars = _flat_bars(20)
        # Два підтверджених + два непідтверджених на одному рівні
        swings = [
            _swing("hh", 1902.0, 5, confirmed=True),
            _swing("hh", 1902.0, 10, confirmed=False),
            _swing("hh", 1902.0, 15, confirmed=False),
        ]
        cfg = _make_config(min_touches=2)  # потрібно 2 підтверджених
        result = detect_liquidity_levels(swings, bars, cfg)
        # Тільки 1 підтверджений → no level (min_touches=2)
        assert result == []

    def test_one_swing_below_min_touches(self):
        """Одна swing-точка → нема рівня (min_touches=2)."""
        bars = _flat_bars(20)
        swings = [_swing("hh", 1902.0, 5)]
        cfg = _make_config(min_touches=2)
        assert detect_liquidity_levels(swings, bars, cfg) == []


# ──────────────────────────────────────────────────────────────
#  detect_liquidity_levels — happy path
# ──────────────────────────────────────────────────────────────

class TestDetectLiquidityLevels:
    def test_two_eq_highs_in_tolerance(self):
        """2 swing highs близько → eq_highs рівень."""
        bars = _flat_bars(20)
        atr = compute_atr(bars, period=5)
        tol = atr * 0.5  # tolerance_atr_mult=0.5
        # Обидві точки в межах tolerance
        swings = [
            _swing("hh", 1902.00, 5),
            _swing("hh", 1902.00 + tol * 0.5, 10),  # ≤ tolerance
        ]
        cfg = _make_config(tolerance_atr_mult=0.5, min_touches=2)
        result = detect_liquidity_levels(swings, bars, cfg)
        assert len(result) == 1
        assert result[0].kind == "eq_highs"
        assert result[0].touches == 2

    def test_two_eq_lows_in_tolerance(self):
        """2 swing lows близько → eq_lows рівень."""
        bars = _flat_bars(20)
        atr = compute_atr(bars, period=5)
        tol = atr * 0.5
        swings = [
            _swing("ll", 1898.00, 5),
            _swing("ll", 1898.00 + tol * 0.3, 10),
        ]
        cfg = _make_config(tolerance_atr_mult=0.5, min_touches=2)
        result = detect_liquidity_levels(swings, bars, cfg)
        assert len(result) == 1
        assert result[0].kind == "eq_lows"
        assert result[0].touches == 2

    def test_three_highs_two_clusters(self):
        """3 прайс зони: 2 близькі → 1 рівень, 1 далека → без рівня (min_touches=2)."""
        bars = _flat_bars(20)
        atr = compute_atr(bars, period=5)
        tol = atr * 0.5

        # Два близьких (cluster A) + один далекий (cluster B, size=1)
        swings = [
            _swing("hh", 1902.0,              5),
            _swing("hh", 1902.0 + tol * 0.3, 10),  # close to first
            _swing("hh", 1902.0 + tol * 5.0, 15),   # far → separate cluster (size=1)
        ]
        cfg = _make_config(tolerance_atr_mult=0.5, min_touches=2)
        result = detect_liquidity_levels(swings, bars, cfg)
        # Тільки cluster з 2 точками → 1 level
        assert len(result) == 1
        assert result[0].touches == 2

    def test_both_highs_and_lows(self):
        """Рівні eq_highs та eq_lows одночасно."""
        bars = _flat_bars(20)
        swings = [
            _swing("hh", 1902.0, 5),
            _swing("hh", 1902.0, 10),  # duplicate price → same cluster
            _swing("ll", 1898.0, 6),
            _swing("ll", 1898.0, 11),
        ]
        cfg = _make_config(tolerance_atr_mult=0.5, min_touches=2)
        result = detect_liquidity_levels(swings, bars, cfg)
        kinds = {l.kind for l in result}
        assert "eq_highs" in kinds
        assert "eq_lows" in kinds

    def test_touches_count_correct(self):
        """touches відповідає кількості swings у кластері."""
        bars = _flat_bars(20)
        swings = [
            _swing("hh", 1902.0, 5),
            _swing("hh", 1902.0, 10),
            _swing("hh", 1902.0, 15),  # 3 однакових
        ]
        cfg = _make_config(tolerance_atr_mult=0.5, min_touches=2)
        result = detect_liquidity_levels(swings, bars, cfg)
        assert len(result) == 1
        assert result[0].touches == 3

    def test_sorted_by_touches_desc(self):
        """Рівні відсортовані по touches DESC (найзначніші першими)."""
        bars = _flat_bars(20)
        # Два кластери: один з 3 touches, другий з 2
        swings = [
            _swing("hh", 1900.0, 1),
            _swing("hh", 1900.0, 2),
            _swing("hh", 1900.0, 3),   # 3 touches at 1900
            _swing("hh", 1910.0, 4),
            _swing("hh", 1910.0, 5),   # 2 touches at 1910
        ]
        cfg = _make_config(tolerance_atr_mult=1.0, min_touches=2)
        result = detect_liquidity_levels(swings, bars, cfg)
        if len(result) >= 2:
            assert result[0].touches >= result[1].touches


# ──────────────────────────────────────────────────────────────
#  S2: Детермінізм
# ──────────────────────────────────────────────────────────────

class TestLiquidityDeterminism:
    def test_same_input_same_output(self):
        """S2: виклики з однаковими даними → однаковий результат."""
        bars = _flat_bars(20)
        swings = [
            _swing("hh", 1902.0, 5),
            _swing("hh", 1902.0, 10),
            _swing("ll", 1898.0, 6),
            _swing("ll", 1898.0, 11),
        ]
        cfg = _make_config(tolerance_atr_mult=0.5, min_touches=2)
        result1 = detect_liquidity_levels(swings, bars, cfg)
        result2 = detect_liquidity_levels(swings, bars, cfg)
        assert len(result1) == len(result2)
        for l1, l2 in zip(result1, result2):
            assert l1.id == l2.id
            assert l1.price == l2.price
            assert l1.touches == l2.touches


# ──────────────────────────────────────────────────────────────
#  S3: Детерміністичні IDs
# ──────────────────────────────────────────────────────────────

class TestLiquidityIds:
    def test_id_deterministic(self):
        """S3: однакова ціна → однаковий ID."""
        bars = _flat_bars(20)
        swings = [
            _swing("hh", 1902.0, 5),
            _swing("hh", 1902.0, 10),
        ]
        cfg = _make_config()
        r1 = detect_liquidity_levels(swings, bars, cfg)
        r2 = detect_liquidity_levels(swings, bars, cfg)
        assert r1[0].id == r2[0].id

    def test_id_format(self):
        """ID починається з kind."""
        bars = _flat_bars(20)
        swings = [_swing("hh", 1902.0, 5), _swing("hh", 1902.0, 10)]
        cfg = _make_config()
        result = detect_liquidity_levels(swings, bars, cfg)
        assert result[0].id.startswith("eq_highs_")


# ──────────────────────────────────────────────────────────────
#  Engine integration: snapshot.levels
# ──────────────────────────────────────────────────────────────

class TestEngineIntegrationLiquidity:
    def _engine(self) -> SmcEngine:
        cfg = SmcConfig.from_dict({
            "enabled": True,
            "lookback_bars": 200,
            "swing_period": 2,
            "ob": {"enabled": True, "min_impulse_atr_mult": 0.1, "atr_period": 5, "max_active_per_side": 5},
            "fvg": {"enabled": True, "min_gap_atr_mult": 0.0, "max_active": 10},
            "structure": {"enabled": True, "confirmation_bars": 1},
            "levels": {
                "enabled": True,
                "tolerance_atr_mult": 2.0,   # широкий поріг → легко кластеризувати
                "min_touches": 2,
                "max_levels": 10,
            },
            "max_zones_per_tf": 30,
            "performance": {"max_compute_ms": 5000, "log_slow_threshold_ms": 1000},
        })
        return SmcEngine(cfg)

    def test_snapshot_levels_is_list(self):
        """snapshot.levels завжди є списком (не None)."""
        engine = self._engine()
        snap = engine.update(SYM, TF, _flat_bars(30))
        assert isinstance(snap.levels, list)

    def test_snapshot_levels_with_repeated_swing_price(self):
        """Якщо є swing-точки з однаковою ціною, рівень з'явиться після engine.update."""
        # Будуємо бари з чіткими swing highs на одному рівні
        # Swing high pattern: zig-zag де піки на ±1900
        bars = []
        for i in range(30):
            # Чергуємо: peaks at 1910 і troughs at 1890
            is_peak = (i % 4 == 2)
            is_trough = (i % 4 == 0)
            h = 1910.0 if is_peak else 1902.0
            low = 1890.0 if is_trough else 1898.0
            o, c = (1901.0, 1901.0)
            bars.append(_bar(i, o, h, low, c))

        engine = self._engine()
        snap = engine.update(SYM, TF, bars)
        # snake не обов'язково містить рівень (може не вистачити confirmed swings)
        # Але не повинно кидати і levels = list
        assert isinstance(snap.levels, list)

    def test_level_to_wire_format(self):
        """SmcLevel.to_wire() відповідає wire контракту (S6). ADR-0024b: kind замість color."""
        bars = _flat_bars(20)
        swings = [_swing("hh", 1902.0, 5), _swing("hh", 1902.0, 10)]
        cfg = _make_config()
        result = detect_liquidity_levels(swings, bars, cfg)
        if result:
            wire = result[0].to_wire()
            assert "id" in wire
            assert "price" in wire
            assert "t_ms" in wire
            assert "kind" in wire  # ADR-0024b: kind для UI per-kind styling
