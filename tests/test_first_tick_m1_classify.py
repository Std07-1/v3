"""Класифікація ключа: замінюємо o/h/low лише там, де FIRST_TICK доведено той самий бар (ADR-0096 §3.3 B, B4).

Навіщо. Кожна категорія SKIP — окремий спосіб зіпсувати SSOT «ремонтом»: «запечений» open (§1.4) записав би
PREV назад під виглядом FIRST_TICK; інший close — чужий бар; розширений діапазон — інша версія даних;
O=H=L=C у паузі календаря — бар, який live відкинув би, без маркера зник би з графіка. Числа — ADR §1.3/§1.4.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json

import pytest

from ft_m1_support import DAY, at, calendar, line, ssot_bar, staged_row
from tools.repair.first_tick_m1 import classify
from tools.repair.first_tick_m1.classify import ClassifyContext, baked_scan, classify_key, extra_entry
from tools.repair.first_tick_m1.common import CLOSE_EPS_DEFAULT, day_key
from tools.repair.first_tick_m1.ssot_part import Winner

KEY = at(DAY, 22, 1)  # неділя 26.07.2026 22:01 — торгова хвилина


def _winner(bar, index=0, members=1, text=None):
    text = line(bar) if text is None else text
    return Winner(bar["open_time_ms"], index, text, json.loads(text), members)


def _ctx(**kw):
    base = dict(calendar=calendar(), close_eps=CLOSE_EPS_DEFAULT, baked_keys=frozenset(), suspect_days=frozenset())
    base.update(kw)
    return ClassifyContext(**base)


PREV_BAR = ssot_bar(KEY, 4055.42, 4093.19, 4055.42, 4092.36, v=31.0)
FIRST_TICK_ROW = staged_row(KEY, 4089.98, 4093.19, 4086.33, 4092.36, volume=31)


def test_prev_close_bar_with_true_first_tick_is_replace():
    entry = classify_key(_winner(PREV_BAR), FIRST_TICK_ROW, _ctx())
    assert entry["cat"] == "REPLACE"
    assert entry["new"] == {"o": 4089.98, "h": 4093.19, "low": 4086.33}
    assert entry["old"] == {"o": 4055.42, "h": 4093.19, "low": 4055.42, "c": 4092.36, "v": 31.0}
    assert (entry["trading_flat_add"], entry["v_differs"], entry["line"]) == (False, False, 0)


def test_identical_values_are_same():
    bar = ssot_bar(KEY, 4089.98, 4093.19, 4086.33, 4092.36, v=12.0)
    entry = classify_key(_winner(bar), FIRST_TICK_ROW, _ctx())
    assert (entry["cat"], entry["v_differs"]) == ("SAME", True)
    assert "suspect" not in entry


def test_open_outside_range_is_skip_baked_although_values_would_be_same():
    """Ловить відсутність перевірки прапорця: нормалізація «запеченого» рядка дає рівно PREV-значення → SAME."""
    baked_key = at(dt.date(2026, 9, 13), 22, 1)
    prev = ssot_bar(baked_key, 4346.23, 4346.23, 4330.62, 4331.55)
    row = staged_row(baked_key, 4346.23, 4337.69, 4330.62, 4331.55)
    entry = classify_key(_winner(prev), row, _ctx())
    assert (entry["cat"], entry["reason"]) == ("SKIP_BAKED", "open_outside_range")
    assert classify_key(_winner(prev), dict(row, raw_open_not_tick=False), _ctx())["cat"] == "SAME"


def _chain(day, closes_opens):
    """Рядки доби: (o, c) — h/low навколо, прапорець перераховується."""
    rows = []
    for minute, (o, h, low, c) in enumerate(closes_opens):
        rows.append(staged_row(at(day, 10, minute), o, h, low, c))
    return rows


def test_in_range_rows_chained_to_baked_row_are_skip_baked():
    monday = dt.date(2026, 7, 27)
    baked = _chain(monday, [(10.0, 10.6, 9.9, 10.5), (10.5, 10.9, 10.4, 10.8), (10.8, 10.7, 10.1, 10.2),
                            (10.2, 10.5, 10.0, 10.4), (10.45, 10.6, 10.3, 10.5)])
    scan = baked_scan([(day_key(monday), baked, True)], CLOSE_EPS_DEFAULT)
    assert scan.baked_keys == frozenset(row["open_time_ms"] for row in baked[1:4])
    assert scan.runs == ({"first_open_ms": baked[1]["open_time_ms"], "last_open_ms": baked[3]["open_time_ms"],
                          "rows": 3, "open_outside_range_rows": 1},)
    in_range = baked[1]
    bar = ssot_bar(in_range["open_time_ms"], 10.5, 10.9, 10.4, 10.8)
    assert classify_key(_winner(bar), in_range, _ctx(baked_keys=scan.baked_keys))["reason"] == "baked_run"
    after_run = ssot_bar(baked[4]["open_time_ms"], 10.45, 10.6, 10.3, 10.5)
    assert classify_key(_winner(after_run), baked[4], _ctx(baked_keys=scan.baked_keys))["cat"] == "SAME"

    control = [dict(r) for r in baked]
    control[2] = staged_row(control[2]["open_time_ms"], 10.8, 10.9, 10.1, 10.2)  # той самий ланцюжок, open у межах
    assert baked_scan([(day_key(monday), control, True)], CLOSE_EPS_DEFAULT).baked_keys == frozenset()

    friday, saturday, sunday = dt.date(2026, 7, 24), dt.date(2026, 7, 25), dt.date(2026, 7, 26)
    fri = _chain(friday, [(1.0, 1.2, 0.9, 1.1), (1.1, 1.0, 0.95, 0.97)])  # другий — поза межами, o == prev_c
    sun = _chain(sunday, [(0.97, 1.0, 0.96, 0.99)])  # o == останній close п'ятниці
    through_opaque = baked_scan([(day_key(friday), fri, True), (day_key(saturday), None, True),
                                 (day_key(sunday), sun, True)], CLOSE_EPS_DEFAULT)
    assert sun[0]["open_time_ms"] not in through_opaque.baked_keys
    through_transparent = baked_scan([(day_key(friday), fri, True), (day_key(saturday), None, False),
                                      (day_key(sunday), sun, True)], CLOSE_EPS_DEFAULT)
    assert sun[0]["open_time_ms"] in through_transparent.baked_keys


def test_close_delta_above_eps_is_mismatch_below_eps_replaces():
    far = dict(FIRST_TICK_ROW, BidClose=4092.36 + 2e-9)
    entry = classify_key(_winner(PREV_BAR), far, _ctx())
    assert (entry["cat"], entry["reason"]) == ("SKIP_CLOSE_MISMATCH", "close_delta")
    near = dict(FIRST_TICK_ROW, BidClose=4092.36 + 5e-10)
    assert classify_key(_winner(PREV_BAR), near, _ctx())["cat"] == "REPLACE"


def test_close_within_eps_but_outside_new_range_is_mismatch():
    bar = ssot_bar(KEY, 4055.42, 4093.19, 4055.42, 4093.19)
    row = staged_row(KEY, 4089.98, 4093.19 - 5e-10, 4086.33, 4093.19 - 5e-10)
    entry = classify_key(_winner(bar), row, _ctx())
    assert (entry["cat"], entry["reason"]) == ("SKIP_CLOSE_MISMATCH", "close_outside_new_range")


@pytest.mark.parametrize("field, value", [("BidHigh", 4093.19 + 1e-12), ("BidLow", 4055.42 - 1e-12)])
def test_range_expanding_row_is_skip_range_expands(field, value):
    """Ловить eps у порівнянні діапазону: розширення навіть на 1e-12 — інші дані, а не зняття розтягування."""
    entry = classify_key(_winner(PREV_BAR), dict(FIRST_TICK_ROW, **{field: value}), _ctx())
    assert entry["cat"] == "SKIP_RANGE_EXPANDS"


def test_missing_and_extra_are_reported_and_extra_never_planned_as_write():
    missing = classify_key(_winner(PREV_BAR), None, _ctx())
    assert missing == {"cat": "MISSING_IN_STAGING", "k": KEY, "line": 0, "line_sha256": missing["line_sha256"],
                       "members": 1}
    extra = extra_entry(FIRST_TICK_ROW)
    assert extra["cat"] == "EXTRA_IN_STAGING" and "line" not in extra and "new" not in extra


def test_flat_result_in_trading_minute_adds_trading_flat():
    bar = ssot_bar(KEY, 4055.42, 4092.36, 4055.42, 4092.36, v=3.0)
    row = staged_row(KEY, 4092.36, 4092.36, 4092.36, 4092.36, volume=3)
    entry = classify_key(_winner(bar), row, _ctx())
    assert (entry["cat"], entry["trading_flat_add"]) == ("REPLACE", True)
    busy = classify_key(_winner(dict(bar, v=5.0)), dict(row, Volume=5), _ctx())
    assert (busy["cat"], busy["trading_flat_add"]) == ("REPLACE", False)
    marked = classify_key(_winner(dict(bar, extensions={"trading_flat": True})), row, _ctx())
    assert (marked["cat"], marked["trading_flat_add"]) == ("REPLACE", False)


def test_flat_result_in_calendar_pause_is_skip_flat_non_trading():
    pause_key = at(dt.date(2026, 7, 27), 21, 30)
    bar = ssot_bar(pause_key, 4055.42, 4092.36, 4055.42, 4092.36, v=2.0)
    row = staged_row(pause_key, 4092.36, 4092.36, 4092.36, 4092.36, volume=2)
    assert classify_key(_winner(bar), row, _ctx())["cat"] == "SKIP_FLAT_NON_TRADING"


@pytest.mark.parametrize("mutate, reason", [
    (lambda b: dict(b, complete=False), "not_complete"),
    (lambda b: dict(b, src="derived_preview"), "not_final_source"),
    (lambda b: dict(b, o="4055.42"), "ohlcv_invalid"),
    (lambda b: {k: v for k, v in b.items() if k != "v"}, "ohlcv_invalid"),
    (lambda b: dict(b, extensions=["trading_flat"]), "extensions_not_object"),
])
def test_ineligible_winner_is_skipped(mutate, reason):
    entry = classify_key(_winner(mutate(PREV_BAR)), FIRST_TICK_ROW, _ctx())
    assert (entry["cat"], entry["reason"]) == ("SKIP_WINNER_INELIGIBLE", reason)


def test_winner_with_unknown_line_style_is_skipped():
    text = line(PREV_BAR).replace('"o":4055.42', '"o":4055.420')
    entry = classify_key(_winner(PREV_BAR, text=text), FIRST_TICK_ROW, _ctx())
    assert (entry["cat"], entry["reason"]) == ("SKIP_WINNER_INELIGIBLE", "line_style_unknown")


@pytest.mark.parametrize("eps", [0.0, -1e-9, 2e-6])
def test_close_eps_over_ceiling_refused(eps):
    with pytest.raises(ValueError, match="FT_CLOSE_EPS_OUT_OF_RANGE"):
        _ctx(close_eps=eps)


def test_suspect_day_share_marks_same_suspect():
    monday = dt.date(2026, 7, 27)
    rows, close = [], 10.0
    for minute in range(40):  # 39 з 39 попередників: o == prev_c, open у межах
        rows.append(staged_row(at(monday, 8, minute), close, close + 0.5, close - 0.5, close + 0.1))
        close += 0.1
    scan = baked_scan([(day_key(monday), rows, True)], CLOSE_EPS_DEFAULT)
    assert scan.suspect_days == frozenset({day_key(monday)}) and scan.baked_keys == frozenset()
    ctx = _ctx(suspect_days=scan.suspect_days)
    row = rows[5]
    same_bar = ssot_bar(row["open_time_ms"], row["BidOpen"], row["BidHigh"], row["BidLow"], row["BidClose"])
    assert classify_key(_winner(same_bar), row, ctx)["suspect"] is True
    prev_bar = dict(same_bar, o=row["BidOpen"] - 0.3, low=row["BidOpen"] - 0.6)
    assert classify_key(_winner(prev_bar), dict(row, BidLow=row["BidOpen"] - 0.55), ctx)["reason"] == "suspect_day"

    control, close = [], 10.0
    for minute in range(40):  # ~52% — як контроль Binance
        opens = close if minute % 2 == 0 else close + 0.05
        control.append(staged_row(at(monday, 8, minute), opens, opens + 0.5, opens - 0.5, opens + 0.1))
        close = opens + 0.1
    control_scan = baked_scan([(day_key(monday), control, True)], CLOSE_EPS_DEFAULT)
    assert control_scan.suspect_days == frozenset()
    assert 0.4 < control_scan.eq_prev_share[day_key(monday)] < 0.6
