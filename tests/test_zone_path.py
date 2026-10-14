"""Шлях ціни від народження зони (ADR-0113 Ф2a): рух до входу, вхід тінню після бару зламу, екстремуми після входу,
ATR на барі зламу, відмова на обрізаному вікні барів."""
from __future__ import annotations

import pytest

from core.model.bars import CandleBar
from core.smc.types import SmcZone
from core.smc.zone_path import LONG, SHORT, zone_direction, zone_path

H1 = 3600
H1_MS = H1 * 1000
T0 = 1_791_964_800_000  # open бару зламу (origin_event_ms)


def _zone(kind="ob_bull", low=100.0, high=102.0, origin=T0):
    return SmcZone(id="z", symbol="XAU/USD", tf_s=H1, kind=kind, start_ms=T0 - H1_MS, end_ms=None, high=high,
                   low=low, status="active", strength=0.8, anchor_bar_ms=T0 - H1_MS, origin_event_ms=origin)


def _bar(i, h, low, c=None):
    open_ms = T0 + i * H1_MS
    return CandleBar("XAU/USD", H1, open_ms, open_ms + H1_MS, low, h, low, h if c is None else c, 10.0, True, "history")


def test_long_entry_is_first_wick_into_zone_top_after_break_bar():
    bars = [_bar(-1, 104, 99), _bar(0, 108, 101), _bar(1, 112, 107), _bar(2, 110, 101.5), _bar(3, 120, 101)]

    path = zone_path(_zone(), bars)

    assert (path.direction, path.entry_edge, path.entered_ms) == (LONG, 102.0, T0 + 2 * H1_MS)
    assert path.pre_entry_extreme == 112.0
    assert (path.favorable_after_entry, path.adverse_since_entry) == (120.0, 101.0)


def test_entry_bar_favorable_extreme_belongs_to_the_move_before_entry():
    """Порядку всередині бару входу не видно: його high не може «взяти ціль» після входу (рецензія Ф2a, D-09)."""
    path = zone_path(_zone(), [_bar(0, 108, 103), _bar(1, 115, 101.5)])

    assert path.pre_entry_extreme == 115.0
    assert path.favorable_after_entry is None and path.adverse_since_entry == 101.5


def test_break_bar_touching_the_zone_is_the_impulse_not_an_entry():
    path = zone_path(_zone(), [_bar(0, 108, 99.5), _bar(1, 111, 105)])

    assert path.entered_ms is None and path.pre_entry_extreme == 111.0


def test_short_mirrors_long():
    zone = _zone(kind="ob_bear", low=200.0, high=202.0)
    bars = [_bar(0, 201, 195), _bar(1, 196, 188), _bar(2, 200.5, 190), _bar(3, 199, 180)]

    path = zone_path(zone, bars)

    assert zone_direction(zone) == SHORT
    assert (path.entry_edge, path.pre_entry_extreme, path.entered_ms) == (200.0, 188.0, T0 + 2 * H1_MS)
    assert (path.favorable_after_entry, path.adverse_since_entry) == (180.0, 200.5)


def test_birth_atr_is_measured_on_bars_up_to_break_bar_only():
    bars = [_bar(i, 103, 99, c=101) for i in range(-20, 0)] + [_bar(0, 110, 100, c=108), _bar(1, 160, 100)]

    path = zone_path(_zone(), bars)

    assert path.birth_atr == pytest.approx((13 * 4 + 10) / 14)  # 13 барів по TR 4 + бар зламу TR 10; бар 1 не входить


def test_no_birth_anchor_or_break_bar_outside_window_means_unknown():
    assert zone_path(_zone(origin=None), [_bar(0, 108, 101)]) is None
    assert zone_path(_zone(), [_bar(-3, 108, 101), _bar(-1, 107, 100)]) is None
    # вікно барів рушія почалось після бару зламу (зона старша за lookback) — шлях з середини руху хибний (D-06)
    assert zone_path(_zone(), [_bar(2, 110, 101.5), _bar(3, 120, 101)]) is None
