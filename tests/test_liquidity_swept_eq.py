"""ADR-0104 (рішення 29.09): EQ, які ціна вже пройшла, не видаються — пул стопів знято.

Зріз проду 29.09: 48 зі 140 EQ стояли по інший бік ціни, щонайменше 100 ціна пробила після останнього дотику;
на D1 EQ кластери 2024–25 року. Визначення — з відновленням пулу: новий свінг на тій самій ціні після зняття
входить у кластер і стає останнім дотиком — стопи знову лежать там, рівень живий.
"""
from __future__ import annotations

from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.liquidity import detect_liquidity_levels
from core.smc.types import SmcSwing, make_swing_id

_SYM, _TF = "XAU/USD", 900
_T0 = 1_790_283_600_000  # 2026-09-24 21:00 UTC
_ATR = 2.0  # tolerance = 0.1 × ATR = 0.2


def _bars(highs_lows):
    return [CandleBar(symbol=_SYM, tf_s=_TF, open_time_ms=_T0 + i * _TF * 1000,
                      close_time_ms=_T0 + (i + 1) * _TF * 1000, o=low, h=high, low=low, c=high, v=1.0,
                      complete=True, src="test") for i, (high, low) in enumerate(highs_lows)]


def _swing(kind, bars, idx, price):
    t = bars[idx].open_time_ms
    return SmcSwing(id=make_swing_id(kind, _SYM, _TF, t), symbol=_SYM, tf_s=_TF, kind=kind, price=price, time_ms=t,
                    confirmed=True)


def _config(hide_swept=True):
    return SmcConfig.from_dict({"levels": {"enabled": True, "tolerance_atr_mult": 0.1, "min_touches": 2,
                                           "max_levels": 4, "hide_swept": hide_swept}})


def _prices(levels, kind):
    return sorted(round(lv.price, 2) for lv in levels if lv.kind == kind)


def test_equal_highs_taken_after_the_last_touch_are_not_published():
    bars = _bars([(100.0, 99.0)] * 6 + [(101.0, 99.5)] + [(99.8, 99.0)] * 3)  # бар 6 вийшов на 101 > 100.2
    swings = [_swing("hh", bars, 1, 100.0), _swing("hh", bars, 4, 100.05)]
    assert _prices(detect_liquidity_levels(swings, bars, _config(), atr=_ATR), "eq_highs") == []


def test_touch_within_tolerance_is_not_a_sweep():
    bars = _bars([(100.0, 99.0)] * 6 + [(100.15, 99.5)] + [(99.8, 99.0)] * 3)  # 100.15 ≤ 100.025 + 0.2
    swings = [_swing("hh", bars, 1, 100.0), _swing("hh", bars, 4, 100.05)]
    assert _prices(detect_liquidity_levels(swings, bars, _config(), atr=_ATR), "eq_highs") == [100.03]


def test_new_touch_after_the_sweep_restores_the_pool():
    bars = _bars([(100.0, 99.0)] * 3 + [(101.0, 99.5)] + [(99.9, 99.0)] * 3 + [(100.02, 99.5)] + [(99.8, 99.0)] * 2)
    swings = [_swing("hh", bars, 1, 100.0), _swing("hh", bars, 7, 100.02)]  # другий дотик — після зняття на барі 3
    assert _prices(detect_liquidity_levels(swings, bars, _config(), atr=_ATR), "eq_highs") == [100.01]


def test_equal_lows_below_which_price_traded_are_not_published():
    bars = _bars([(101.0, 100.0)] * 5 + [(100.5, 99.0)] + [(101.0, 100.4)] * 2)
    swings = [_swing("ll", bars, 1, 100.0), _swing("ll", bars, 3, 99.95)]
    assert _prices(detect_liquidity_levels(swings, bars, _config(), atr=_ATR), "eq_lows") == []


def test_swept_cluster_frees_its_place_for_the_next_live_one():
    """max_levels 4 → 2 на бік: знятий кластер відсіюється до ліміту, наступний живий займає місце."""
    bars = _bars([(100.0, 90.0)] * 12 + [(106.0, 95.0)] + [(98.0, 95.0)] * 2)
    swings = ([_swing("hh", bars, i, 105.0) for i in (1, 2, 3)]      # 3 дотики, але знятий баром 12
              + [_swing("hh", bars, i, 110.0) for i in (4, 5)]       # живий
              + [_swing("hh", bars, i, 115.0) for i in (6, 7)])      # живий
    assert _prices(detect_liquidity_levels(swings, bars, _config(), atr=_ATR), "eq_highs") == [110.0, 115.0]


def test_rollback_flag_keeps_every_cluster_as_before():
    bars = _bars([(100.0, 99.0)] * 6 + [(101.0, 99.5)] + [(99.8, 99.0)] * 3)
    swings = [_swing("hh", bars, 1, 100.0), _swing("hh", bars, 4, 100.05)]
    assert _prices(detect_liquidity_levels(swings, bars, _config(hide_swept=False), atr=_ATR), "eq_highs") == [100.03]
