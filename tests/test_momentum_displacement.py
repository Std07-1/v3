"""Мітки імпульсу (displacement) — поріг від ATR на момент бару, тож статус закритого бару не змінюється з новими
барами (ADR-0113 E-08a)."""
from __future__ import annotations

import random

from core.model.bars import CandleBar
from core.smc.momentum import detect_displacement
from core.smc.swings import compute_atr, rolling_atr

M5_MS = 300_000
T0 = 1_791_964_800_000
MIN_BODY_ATR = 1.5
MAX_WICK = 0.3


def _bar(i, o, h, low, c):
    open_ms = T0 + i * M5_MS
    return CandleBar("XAU/USD", 300, open_ms, open_ms + M5_MS, o, h, low, c, 1.0, True, "history")


def _quiet(start, count, base=4100.0):
    """Тихий ринок: діапазон 2, тіло 1."""
    return [_bar(start + k, base, base + 1.5, base - 0.5, base + 1.0) for k in range(count)]


def _marked(bars):
    return {s.time_ms for s in detect_displacement(bars, min_body_atr=MIN_BODY_ATR, max_wick_ratio=MAX_WICK)}


def test_rolling_atr_is_compute_atr_of_every_prefix_bit_for_bit():
    rng = random.Random(7)
    bars, price = [], 4100.0
    for i in range(80):
        o = price
        c = o + rng.uniform(-6, 6)
        bars.append(_bar(i, o, max(o, c) + rng.uniform(0, 3), min(o, c) - rng.uniform(0, 3), c))
        price = c + rng.uniform(-1, 1)  # гепи між барами — TR від попереднього close

    assert rolling_atr(bars, 14) == [compute_atr(bars[:i + 1], 14) for i in range(len(bars))]
    assert rolling_atr([], 14) == []


def test_closed_impulse_stays_marked_when_a_volatile_regime_follows():
    """Тихий ринок, сильна свічка, далі волатильність: з поточним ATR вікна свічку «розмітило» б назад."""
    impulse = _bar(30, 4100.0, 4105.2, 4099.9, 4105.0)  # тіло 5 при ATR тихого ринку ~2
    before = _quiet(0, 30) + [impulse]
    volatile = [_bar(31 + k, 4105.0, 4115.0, 4095.0, 4113.0 if k % 2 else 4097.0) for k in range(20)]  # діапазон 20

    assert impulse.open_time_ms in _marked(before)
    assert impulse.open_time_ms in _marked(before + volatile)
    # поточний ATR вікна (як було) після волатильності вже не бачить імпульсу
    assert 5.0 < MIN_BODY_ATR * compute_atr(before + volatile, 14)


def test_ordinary_candle_does_not_become_an_impulse_when_the_market_goes_quiet():
    """Звичайна для волатильного ринку свічка не стає «імпульсом», коли після неї ринок затих."""
    volatile = [_bar(k, 4105.0, 4115.0, 4095.0, 4113.0 if k % 2 else 4097.0) for k in range(30)]
    ordinary = _bar(30, 4100.0, 4106.5, 4099.5, 4106.0)  # тіло 6 при ATR ~20

    marked = _marked(volatile + [ordinary] + _quiet(31, 30, base=4106.0))

    assert ordinary.open_time_ms not in marked
    assert 6.0 >= MIN_BODY_ATR * compute_atr(_quiet(31, 30, base=4106.0), 14)  # поточний ATR назвав би її імпульсом


def test_wicks_and_direction_as_before():
    bars = _quiet(0, 30)
    bull = _bar(30, 4100.0, 4105.2, 4099.9, 4105.0)
    bear = _bar(31, 4105.0, 4105.1, 4099.8, 4100.0)
    wicky = _bar(32, 4100.0, 4112.0, 4093.0, 4105.0)  # тіло 5, діапазон 19 — тіні більше за 30 %

    kinds = {s.time_ms: s.kind for s in detect_displacement(bars + [bull, bear, wicky], MIN_BODY_ATR, MAX_WICK)}

    assert kinds.get(bull.open_time_ms) == "displacement_bull"
    assert kinds.get(bear.open_time_ms) == "displacement_bear"
    assert wicky.open_time_ms not in kinds
