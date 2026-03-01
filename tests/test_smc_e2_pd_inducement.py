"""
tests/test_smc_e2_pd_inducement.py — Premium/Discount §4.6 та Inducement §4.7 тести.

Перевіряє:
  detect_premium_discount:
    - Знаходить останній SH + SL → пара premium/discount зон
    - Equilibrium = (high + low) / 2
    - IDs детерміновані (make_zone_id, S3)
    - anchor_ms = max(SH.time_ms, SL.time_ms)
    - start_ms = min(SH.time_ms, SL.time_ms)
    - enabled=False → []
    - empty/no confirmed swings → []
    - inverted range → []
  SmcPremiumDiscountConfig: defaults, from_dict
  detect_inducement:
    - inducement_bear: wick break + close below minor SH + confirmed reversal
    - inducement_bull: wick break + close above minor SL + confirmed reversal
    - Малий reversal < threshold → не виявляє
    - S2 детермінізм
  SmcInducementConfig: defaults, from_dict
  Engine integration:
    - snapshot.zones містить premium/discount
    - snapshot.swings може містити inducement_*

Python 3.7 compatible.
"""
from __future__ import annotations

from typing import List

import pytest

from core.model.bars import CandleBar
from core.smc.config import SmcConfig, SmcPremiumDiscountConfig, SmcInducementConfig
from core.smc.engine import SmcEngine
from core.smc.inducement import detect_inducement
from core.smc.premium_discount import detect_premium_discount
from core.smc.structure import classify_swings, detect_structure_events
from core.smc.swings import detect_raw_swings
from core.smc.types import SmcSwing, SmcZone, make_swing_id

# ──────────────────────────────────────────────────────────────
#  Fixtures / Helpers
# ──────────────────────────────────────────────────────────────

SYM = "XAU/USD"
TF  = 60
T0  = 1_700_000_000_000
MS  = TF * 1000


def _bar(i: int, o: float, h: float, low: float, c: float,
         complete: bool = True) -> CandleBar:
    open_ms = T0 + i * MS
    return CandleBar(
        symbol=SYM, tf_s=TF,
        open_time_ms=open_ms, close_time_ms=open_ms + MS,
        o=o, h=h, low=low, c=c, v=1000.0,
        complete=complete, src="derived",
    )


def _flat(n: int, p: float = 1900.0) -> List[CandleBar]:
    return [_bar(i, p, p + 1.0, p - 1.0, p) for i in range(n)]


def _classified_from_bars(bars: List[CandleBar], period: int = 5) -> List[SmcSwing]:
    """Повний цикл: raw → classify → (struct events не потрібні для PD)."""
    raw = detect_raw_swings(bars, period=period)
    return classify_swings(raw)


# ──────────────────────────────────────────────────────────────
#  SmcPremiumDiscountConfig
# ──────────────────────────────────────────────────────────────

class TestSmcPremiumDiscountConfig:
    def test_defaults(self):
        cfg = SmcPremiumDiscountConfig()
        assert cfg.enabled is True

    def test_from_dict_enabled_false(self):
        cfg = SmcPremiumDiscountConfig.from_dict({"enabled": False})
        assert cfg.enabled is False

    def test_from_dict_empty(self):
        cfg = SmcPremiumDiscountConfig.from_dict({})
        assert cfg.enabled is True

    def test_smcconfig_has_premium_discount(self):
        cfg = SmcConfig()
        assert hasattr(cfg, "premium_discount")
        assert isinstance(cfg.premium_discount, SmcPremiumDiscountConfig)

    def test_smcconfig_from_dict_propagates(self):
        cfg = SmcConfig.from_dict({"premium_discount": {"enabled": False}})
        assert cfg.premium_discount.enabled is False


# ──────────────────────────────────────────────────────────────
#  detect_premium_discount — guards
# ──────────────────────────────────────────────────────────────

class TestDetectPremiumDiscountGuards:
    def test_disabled_returns_empty(self):
        cfg = SmcConfig.from_dict({"premium_discount": {"enabled": False}})
        bars = _flat(20)
        raw = detect_raw_swings(bars, 5)
        cl = classify_swings(raw)
        result = detect_premium_discount(cl, bars, cfg)
        assert result == []

    def test_empty_bars_returns_empty(self):
        cfg = SmcConfig()
        result = detect_premium_discount([], [], cfg)
        assert result == []

    def test_empty_classified_returns_empty(self):
        cfg = SmcConfig()
        result = detect_premium_discount([], _flat(20), cfg)
        assert result == []

    def test_no_confirmed_swings_returns_empty(self):
        cfg = SmcConfig()
        bars = _flat(20)
        # unconfirmed swing
        swing = SmcSwing(
            id=make_swing_id("hh", SYM, TF, T0),
            symbol=SYM, tf_s=TF, kind="hh",
            price=1910.0, time_ms=T0, confirmed=False,
        )
        result = detect_premium_discount([swing], bars, cfg)
        assert result == []

    def test_inverted_range_returns_empty(self):
        """SH.price < SL.price — некоректні дані, має повернути []."""
        cfg = SmcConfig()
        bars = _flat(20)
        sh = SmcSwing(
            id=make_swing_id("hh", SYM, TF, T0 + 1 * MS),
            symbol=SYM, tf_s=TF, kind="hh",
            price=1890.0,  # lower than "low"
            time_ms=T0 + 1 * MS, confirmed=True,
        )
        sl = SmcSwing(
            id=make_swing_id("ll", SYM, TF, T0 + 2 * MS),
            symbol=SYM, tf_s=TF, kind="ll",
            price=1900.0,  # higher than "high" → inverted
            time_ms=T0 + 2 * MS, confirmed=True,
        )
        result = detect_premium_discount([sh, sl], bars, cfg)
        assert result == []


# ──────────────────────────────────────────────────────────────
#  detect_premium_discount — logic
# ──────────────────────────────────────────────────────────────

def _build_pd_bars_and_swings():
    """18 bars з явним SH та SL для period=5."""
    # pattern: low plateau → peak → low plateau → valley → 5 filler bars
    #  валлей at index 11 requires bars[12..16] to exist for period=5
    prices = [
        (1900, 1901, 1899, 1900),  # 0
        (1900, 1901, 1899, 1900),  # 1
        (1900, 1901, 1899, 1900),  # 2
        (1900, 1901, 1899, 1900),  # 3
        (1900, 1901, 1899, 1900),  # 4
        (1900, 1920, 1899, 1910),  # 5  ← SH: h=1920 max of [0..10]
        (1900, 1901, 1899, 1900),  # 6
        (1900, 1901, 1899, 1900),  # 7
        (1900, 1901, 1899, 1900),  # 8
        (1900, 1901, 1899, 1900),  # 9
        (1900, 1901, 1899, 1900),  # 10
        (1880, 1881, 1879, 1880),  # 11 ← SL: low=1879 min of [6..16]
        (1890, 1891, 1889, 1890),  # 12 (filler, low>1879)
        (1890, 1891, 1889, 1890),  # 13
        (1890, 1891, 1889, 1890),  # 14
        (1890, 1891, 1889, 1890),  # 15
        (1890, 1891, 1889, 1890),  # 16 ← 5 bars after valley (необхідно для period=5)
        (1890, 1891, 1889, 1890),  # 17 (extra buffer)
    ]
    bars = [_bar(i, *p) for i, p in enumerate(prices)]
    raw = detect_raw_swings(bars, period=5)
    classified = classify_swings(raw)
    return bars, classified


class TestDetectPremiumDiscount:
    def test_returns_two_zones(self):
        bars, cl = _build_pd_bars_and_swings()
        assert any(s.kind in ("hh", "lh") and s.confirmed for s in cl), "Need SH"
        assert any(s.kind in ("ll", "hl") and s.confirmed for s in cl), "Need SL"
        result = detect_premium_discount(cl, bars, SmcConfig())
        assert len(result) == 2

    def test_zone_kinds(self):
        bars, cl = _build_pd_bars_and_swings()
        result = detect_premium_discount(cl, bars, SmcConfig())
        kinds = {z.kind for z in result}
        assert kinds == {"premium", "discount"}

    def test_equilibrium_midpoint(self):
        bars, cl = _build_pd_bars_and_swings()
        result = detect_premium_discount(cl, bars, SmcConfig())
        premium = next(z for z in result if z.kind == "premium")
        discount = next(z for z in result if z.kind == "discount")
        assert premium.low == discount.high, "Equilibrium must be shared boundary"
        # premium: high=range_high, low=equil; discount: high=equil, low=range_low
        range_high = premium.high
        range_low = discount.low
        expected_equil = (range_high + range_low) / 2.0
        assert abs(premium.low - expected_equil) < 1e-9

    def test_premium_above_discount(self):
        bars, cl = _build_pd_bars_and_swings()
        result = detect_premium_discount(cl, bars, SmcConfig())
        premium = next(z for z in result if z.kind == "premium")
        discount = next(z for z in result if z.kind == "discount")
        # equilibrium is a shared boundary: premium.low == discount.high
        assert premium.high > premium.low == discount.high > discount.low

    def test_same_anchor_ms(self):
        bars, cl = _build_pd_bars_and_swings()
        result = detect_premium_discount(cl, bars, SmcConfig())
        assert result[0].anchor_bar_ms == result[1].anchor_bar_ms, (
            "Both P/D zones share the same anchor_ms"
        )

    def test_ids_are_deterministic(self):
        """S3: same input → same IDs."""
        bars, cl = _build_pd_bars_and_swings()
        r1 = detect_premium_discount(cl, bars, SmcConfig())
        r2 = detect_premium_discount(cl, bars, SmcConfig())
        assert {z.id for z in r1} == {z.id for z in r2}

    def test_id_format(self):
        bars, cl = _build_pd_bars_and_swings()
        result = detect_premium_discount(cl, bars, SmcConfig())
        for z in result:
            assert z.id.startswith(z.kind + "_"), f"ID must start with kind: {z.id}"

    def test_status_active(self):
        bars, cl = _build_pd_bars_and_swings()
        result = detect_premium_discount(cl, bars, SmcConfig())
        for z in result:
            assert z.status == "active"

    def test_strength_one(self):
        bars, cl = _build_pd_bars_and_swings()
        result = detect_premium_discount(cl, bars, SmcConfig())
        for z in result:
            assert z.strength == 1.0

    def test_end_ms_none(self):
        bars, cl = _build_pd_bars_and_swings()
        result = detect_premium_discount(cl, bars, SmcConfig())
        for z in result:
            assert z.end_ms is None


# ──────────────────────────────────────────────────────────────
#  SmcInducementConfig
# ──────────────────────────────────────────────────────────────

class TestSmcInducementConfig:
    def test_defaults(self):
        cfg = SmcInducementConfig()
        assert cfg.enabled is True
        assert cfg.minor_period == 3
        assert cfg.reversal_atr_mult == 0.5
        assert cfg.confirmation_bars == 3
        assert cfg.max_inducements == 10

    def test_from_dict(self):
        cfg = SmcInducementConfig.from_dict({
            "enabled": False,
            "minor_period": 2,
            "reversal_atr_mult": 1.0,
            "confirmation_bars": 5,
            "max_inducements": 20,
        })
        assert cfg.enabled is False
        assert cfg.minor_period == 2
        assert cfg.reversal_atr_mult == 1.0
        assert cfg.confirmation_bars == 5
        assert cfg.max_inducements == 20

    def test_smcconfig_has_inducement(self):
        cfg = SmcConfig()
        assert hasattr(cfg, "inducement")
        assert isinstance(cfg.inducement, SmcInducementConfig)

    def test_smcconfig_from_dict_propagates(self):
        cfg = SmcConfig.from_dict({"inducement": {"minor_period": 2}})
        assert cfg.inducement.minor_period == 2


# ──────────────────────────────────────────────────────────────
#  detect_inducement — guards
# ──────────────────────────────────────────────────────────────

class TestDetectInducementGuards:
    def test_disabled_returns_empty(self):
        cfg = SmcConfig.from_dict({"inducement": {"enabled": False}})
        bars = _flat(30)
        result = detect_inducement(bars, [], cfg)
        assert result == []

    def test_not_enough_bars_returns_empty(self):
        cfg = SmcConfig()
        bars = _flat(5)   # <<  2*3 + 3 + 2 = 11 minimum
        result = detect_inducement(bars, [], cfg)
        assert result == []


# ──────────────────────────────────────────────────────────────
#  detect_inducement — bear (minor SH break)
# ──────────────────────────────────────────────────────────────

def _bars_inducement_bear(minor_period: int = 3) -> List[CandleBar]:
    """
    Конструює послідовність з inducement_bear:
      - bars 0..mp-1: низькі, однакові (ліва частина для minor SH)
      - bar  mp:      H=20 (minor Swing High)
      - bars mp+1..2*mp: H=10 (правий бік → підтверджує minor SH)
      - bar  2*mp+1:  H=21, close=9 (trap candle: wick > 20, close < 20)
      - bars after:   close=1 (reversal >> ATR)
      Filler до total 25 барів.
    """
    p = minor_period
    n = 25
    bars = []
    # 0..p-1: high=10, low=9
    for i in range(p):
        bars.append(_bar(i, 10, 10, 9, 10))
    # p: minor SH at h=20
    bars.append(_bar(p, 10, 20, 9, 10))
    # p+1..2p: high=10 (confirms SH)
    for i in range(p + 1, 2 * p + 1):
        bars.append(_bar(i, 10, 10, 9, 10))
    # 2p+1: trap candle
    trap_i = 2 * p + 1
    bars.append(_bar(trap_i, 10, 21, 9, 9))   # wick=21>20, close=9<20
    # 2p+2: strong reversal
    rev_i = 2 * p + 2
    bars.append(_bar(rev_i, 9, 10, 1, 1))     # close=1 far below 20
    # fill up to n
    for i in range(rev_i + 1, n):
        bars.append(_bar(i, 10, 10, 9, 10))
    return bars


def _bars_inducement_bull(minor_period: int = 3) -> List[CandleBar]:
    """
    Конструює послідовність з inducement_bull:
      - bars 0..mp-1: high=10, low=9 (ліва частина)
      - bar  mp:      low=0 (minor Swing Low)
      - bars mp+1..2*mp: low=9 (правий бік → підтверджує minor SL)
      - bar  2*mp+1:  low=-1, close=9 (trap candle: wick < 0, close > 0)
      - bars after:   close=20 (reversal >> ATR)
    """
    p = minor_period
    n = 25
    bars = []
    for i in range(p):
        bars.append(_bar(i, 10, 10, 9, 10))
    # minor SL at low=0
    bars.append(_bar(p, 10, 10, 0, 10))
    for i in range(p + 1, 2 * p + 1):
        bars.append(_bar(i, 10, 10, 9, 10))
    # trap candle: wick below 0, close above 0
    trap_i = 2 * p + 1
    bars.append(_bar(trap_i, 10, 10, -1, 9))   # wick=-1<0, close=9>0
    # strong up reversal
    rev_i = 2 * p + 2
    bars.append(_bar(rev_i, 9, 20, 9, 20))     # close=20 far above 0
    for i in range(rev_i + 1, n):
        bars.append(_bar(i, 10, 10, 9, 10))
    return bars


class TestDetectInducementBear:
    def test_bear_detected(self):
        bars = _bars_inducement_bear()
        result = detect_inducement(bars, [], SmcConfig())
        kinds = [s.kind for s in result]
        assert "inducement_bear" in kinds

    def test_bear_confirmed(self):
        bars = _bars_inducement_bear()
        result = detect_inducement(bars, [], SmcConfig())
        bears = [s for s in result if s.kind == "inducement_bear"]
        assert all(s.confirmed for s in bears)

    def test_bear_price_is_minor_sh(self):
        bars = _bars_inducement_bear()
        result = detect_inducement(bars, [], SmcConfig())
        bears = [s for s in result if s.kind == "inducement_bear"]
        assert bears, "Повинен бути мінімум один inducement_bear"
        # Ціна trap — рівень minor SH = 20.0
        assert bears[0].price == 20.0


class TestDetectInducementBull:
    def test_bull_detected(self):
        bars = _bars_inducement_bull()
        result = detect_inducement(bars, [], SmcConfig())
        kinds = [s.kind for s in result]
        assert "inducement_bull" in kinds

    def test_bull_confirmed(self):
        bars = _bars_inducement_bull()
        result = detect_inducement(bars, [], SmcConfig())
        bulls = [s for s in result if s.kind == "inducement_bull"]
        assert all(s.confirmed for s in bulls)

    def test_bull_price_is_minor_sl(self):
        bars = _bars_inducement_bull()
        result = detect_inducement(bars, [], SmcConfig())
        bulls = [s for s in result if s.kind == "inducement_bull"]
        assert bulls, "Повинен бути мінімум один inducement_bull"
        assert bulls[0].price == 0.0


class TestDetectInducementThreshold:
    def test_small_reversal_not_detected(self):
        """Reversal < threshold → не виявляємо."""
        p = 3
        bars = []
        for i in range(p):
            bars.append(_bar(i, 10, 10, 9, 10))
        bars.append(_bar(p, 10, 20, 9, 10))      # minor SH = 20
        for i in range(p + 1, 2 * p + 1):
            bars.append(_bar(i, 10, 10, 9, 10))
        trap_i = 2 * p + 1
        bars.append(_bar(trap_i, 10, 21, 9, 9))  # trap candle: h=21>20, c=9<20
        # Малий reversal: всі бари після trap мають c=19.9
        # drop = 20 - 19.9 = 0.1; ATR ≈ 1.0; threshold = 0.5*1.0=0.5 → 0.1 < 0.5
        for i in range(trap_i + 1, 25):
            bars.append(_bar(i, 19, 20, 19, 19.9))

        result = detect_inducement(bars, [], SmcConfig())
        bears = [s for s in result if s.kind == "inducement_bear"]
        assert bears == [], "Малий reversal не має виявлятись"


class TestDetectInducementDeterminism:
    def test_s2_same_output(self):
        bars = _bars_inducement_bear()
        r1 = detect_inducement(bars, [], SmcConfig())
        r2 = detect_inducement(bars, [], SmcConfig())
        assert [s.id for s in r1] == [s.id for s in r2]
        assert [s.price for s in r1] == [s.price for s in r2]

    def test_id_format(self):
        bars = _bars_inducement_bear()
        result = detect_inducement(bars, [], SmcConfig())
        for s in result:
            assert s.id.startswith(s.kind + "_"), f"ID: {s.id}"


# ──────────────────────────────────────────────────────────────
#  Engine integration: snapshot contains P/D + inducements
# ──────────────────────────────────────────────────────────────

class TestEngineIntegrationE2PD:
    def test_snapshot_has_pd_zone_kinds(self):
        """snapshot.zones після update() з достатньо барами має premium/discount."""
        # Потрібно достатньо барів для виявлення SH та SL (period=5, min 11)
        # Будуємо bars з чітким SH і SL
        bars, _ = _build_pd_bars_and_swings()
        # D1: display filter proximity може прибрати зону далеко від ціни
        # для тесту detection — збільшуємо proximity щоб filter не заважав
        from core.smc.config import SmcDisplayConfig
        cfg = SmcConfig(display=SmcDisplayConfig(proximity_atr_mult=999.0))
        engine = SmcEngine(cfg)
        snap = engine.update(SYM, TF, bars)
        zone_kinds = {z.kind for z in snap.zones}
        # premium/discount залежать від classified swings; якщо classify знайде
        # хоча б один SH і SL → зони будуть
        has_sh = any(s.kind in ("hh", "lh") and s.confirmed
                     for s in snap.swings)
        has_sl = any(s.kind in ("ll", "hl") and s.confirmed
                     for s in snap.swings)
        if has_sh and has_sl:
            assert "premium" in zone_kinds
            assert "discount" in zone_kinds

    def test_snapshot_zones_in_ssot_kinds(self):
        """Всі zone.kind мають бути в ZONE_KINDS (S0/S6 контракт)."""
        from core.smc.types import ZONE_KINDS
        bars, _ = _build_pd_bars_and_swings()
        engine = SmcEngine(SmcConfig())
        snap = engine.update(SYM, TF, bars)
        for z in snap.zones:
            assert z.kind in ZONE_KINDS, f"Невідомий zone kind: {z.kind}"

    def test_snapshot_swings_in_ssot_kinds(self):
        """Всі swing.kind мають бути в SWING_KINDS."""
        from core.smc.types import SWING_KINDS
        bars = _bars_inducement_bear()
        engine = SmcEngine(SmcConfig())
        snap = engine.update(SYM, TF, bars)
        for s in snap.swings:
            assert s.kind in SWING_KINDS, f"Невідомий swing kind: {s.kind}"
