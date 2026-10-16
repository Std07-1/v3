"""Шлях ціни від народження зони (ADR-0113 Ф2a): рух до входу, вхід тінню після бару зламу, екстремуми після входу,
ATR на барі зламу, відмова на обрізаному вікні барів."""
from __future__ import annotations

import pytest

from core.model.bars import CandleBar
from core.smc.types import SmcZone
from core.smc.zone_path import LONG, SHORT, FormingSpan, first_touch_end_ms, forming_spans, zone_direction, zone_path

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


M15 = 900
M15_MS = M15 * 1000


def _fine(i, h, low, tf_s=M15):
    """Бар дрібнішого TF: i-й M15 від відкриття бару зламу."""
    open_ms = T0 + i * tf_s * 1000
    return CandleBar("XAU/USD", tf_s, open_ms, open_ms + tf_s * 1000, low, h, low, h, 1.0, True, "history")


def _h1_bucket(ts_ms):
    return ts_ms - ts_ms % H1_MS


def test_forming_spans_group_finer_bars_after_last_closed_bar_into_zone_buckets():
    finer = [_fine(4, 111, 108), _fine(7, 112, 107), _fine(8, 113, 104), _fine(9, 115, 103), _fine(12, 109, 102.5)]

    spans = forming_spans(T0 + H1_MS, finer, _h1_bucket)  # закритий H1 №1 уже містить M15 4..7

    assert spans == [FormingSpan(T0 + 2 * H1_MS, 115, 103), FormingSpan(T0 + 3 * H1_MS, 109, 102.5)]
    assert forming_spans(T0 + 3 * H1_MS, finer, _h1_bucket) == []


def test_entry_in_forming_bucket_is_the_same_moment_as_after_its_bar_closes():
    """E-03: вхід видно до закриття бару TF зони, і після закриття він той самий — шлях лише продовжується."""
    closed = [_bar(0, 108, 103), _bar(1, 112, 107)]
    forming = forming_spans(T0 + H1_MS, [_fine(8, 110, 104), _fine(9, 109, 101.8)], _h1_bucket)

    while_forming = zone_path(_zone(), closed, forming)
    after_close = zone_path(_zone(), closed + [_bar(2, 110, 101.5)])

    assert zone_path(_zone(), closed).entered_ms is None
    assert while_forming.entered_ms == after_close.entered_ms == T0 + 2 * H1_MS
    assert (while_forming.adverse_since_entry, after_close.adverse_since_entry) == (101.8, 101.5)


def test_stop_in_forming_bucket_is_seen_before_the_zone_bar_closes():
    closed = [_bar(0, 108, 103), _bar(1, 112, 101.5)]
    forming = forming_spans(T0 + H1_MS, [_fine(8, 104, 99.0)], _h1_bucket)

    path = zone_path(_zone(), closed, forming)

    assert path.entered_ms == T0 + H1_MS and path.adverse_since_entry == 99.0


def test_first_touch_end_is_the_finest_series_bar_that_covers_the_entry_bucket():
    path = zone_path(_zone(), [_bar(0, 108, 103), _bar(1, 112, 107), _bar(2, 110, 101.5)])
    m15 = [_fine(i, 110, 105) for i in range(4, 9)] + [_fine(9, 109, 101.9), _fine(10, 108, 101.5)]
    m30 = [_fine(i, 110, 101.5, tf_s=1800) for i in range(2, 6)]

    bucket_end = T0 + 3 * H1_MS

    assert first_touch_end_ms(path, [m15, m30], bucket_end) == T0 + 10 * M15_MS  # кінець M15 №9
    # M15 почався всередині бакета входу (перший дотик міг бути раніше) — вирішує M30
    assert first_touch_end_ms(path, [m15[6:], m30], bucket_end) == T0 + 5 * 1800 * 1000
    assert first_touch_end_ms(path, [m15[6:]], bucket_end) is None
    no_touch = [_fine(i, 110, 105) for i in range(4, 14)]  # бакет входу пройшов, дрібні бари краю не торкнулись
    assert first_touch_end_ms(path, [no_touch], bucket_end) is None
    assert first_touch_end_ms(zone_path(_zone(), [_bar(0, 108, 103)]), [m15], bucket_end) is None  # входу немає
