"""ADR-0054 Фаза 1 — pure-виміри здоров'я символу на синтетиці з відомою відповіддю.

Кожен тест підсовує ряд, у якому дефект (або його відсутність) відомий наперед:
інструмент, якому ми збираємось вірити, спершу доводить себе там, де істина відома.
"""
from __future__ import annotations

import pytest

from core.health import (
    check_anchor_on_session_edge,
    grade_symbol_tf,
    measure_age,
    measure_cascade,
    measure_depth,
    measure_geometry,
    measure_holes,
    normalize_open_to_grid,
)
from core.model.bars import CandleBar

M1_MS = 60_000
H1_MS = 3_600_000
D1_MS = 86_400_000
BASE = 1_767_225_600_000  # 2026-01-01 00:00 UTC, вирівняно на добу
ALWAYS = lambda _ms: True  # noqa: E731 — календар «24/7» для вимірів без сесій


def _bar(open_ms: int, tf_ms: int, *, o=1.0, h=2.0, low=0.5, c=1.5, src="derived") -> CandleBar:
    return CandleBar(
        symbol="X", tf_s=tf_ms // 1000, open_time_ms=open_ms, close_time_ms=open_ms + tf_ms,
        o=o, h=h, low=low, c=c, v=1.0, complete=True, src=src,
    )


# ── geometry ────────────────────────────────────────────────────────────────
def test_clean_series_has_no_defects():
    bars = [_bar(BASE + i * M1_MS, M1_MS) for i in range(10)]
    g = measure_geometry(bars, tf_ms=M1_MS, anchor_offsets_ms=[0])
    assert (g.total, g.exact_dup, g.unsorted, g.align_bad, g.close_bad, g.ohlc_bad) == (10, 0, 0, 0, 0, 0)


def test_duplicate_and_unsorted_are_counted_separately():
    bars = [_bar(BASE, M1_MS), _bar(BASE, M1_MS), _bar(BASE + 2 * M1_MS, M1_MS), _bar(BASE + M1_MS, M1_MS)]
    g = measure_geometry(bars, tf_ms=M1_MS, anchor_offsets_ms=[0])
    assert g.exact_dup == 1 and g.unsorted == 1


def test_off_grid_bar_is_align_bad():
    bars = [_bar(BASE + 30_000, M1_MS)]  # пів-хвилини — не на сітці
    assert measure_geometry(bars, tf_ms=M1_MS, anchor_offsets_ms=[0]).align_bad == 1


def test_dst_alt_anchor_bar_is_legal_not_align_bad():
    """D1 21:00 влітку і 22:00 взимку — обидва легальні; вимір не має кричати."""
    summer, winter = 75_600_000, 79_200_000
    bars = [_bar(BASE + summer, D1_MS), _bar(BASE + winter - D1_MS, D1_MS)]
    strict = measure_geometry(bars, tf_ms=D1_MS, anchor_offsets_ms=[summer])
    tolerant = measure_geometry(bars, tf_ms=D1_MS, anchor_offsets_ms=[summer, winter])
    assert strict.align_bad == 1, "лише з primary один із барів виглядає зсунутим"
    assert tolerant.align_bad == 0, "з DST-альтернативою обидва легальні"


def test_broken_close_ms_and_ohlc_are_caught():
    bad_close = CandleBar(symbol="X", tf_s=60, open_time_ms=BASE, close_time_ms=BASE + 999,
                          o=1, h=2, low=0.5, c=1.5, v=1, complete=True, src="history")
    bad_ohlc = CandleBar(symbol="X", tf_s=60, open_time_ms=BASE + M1_MS, close_time_ms=BASE + 2 * M1_MS,
                         o=5, h=2, low=3, c=1, v=1, complete=True, src="history")
    g = measure_geometry([bad_close, bad_ohlc], tf_ms=M1_MS, anchor_offsets_ms=[0])
    assert g.close_bad == 1 and g.ohlc_bad == 1


def test_normalize_returns_none_only_for_truly_shifted():
    assert normalize_open_to_grid(BASE, tf_ms=M1_MS, anchor_offsets_ms=[0]) == BASE
    assert normalize_open_to_grid(BASE + 7, tf_ms=M1_MS, anchor_offsets_ms=[0]) is None


# ── holes ───────────────────────────────────────────────────────────────────
def test_holes_counts_only_trading_buckets():
    opens = [BASE, BASE + 2 * M1_MS]  # середня хвилина відсутня
    h = measure_holes(opens, start_ms=BASE, end_ms=BASE + 3 * M1_MS, tf_ms=M1_MS,
                      anchor_offset_ms=0, is_trading_fn=ALWAYS)
    assert (h.expected, h.present, h.missing) == (3, 2, 1)
    assert h.missing_samples == (BASE + M1_MS,)


def test_closed_market_minutes_are_not_holes():
    """Вихідні — не дірка: очікуємо лише торгові бакети."""
    open_only_first = lambda ms: ms == BASE  # noqa: E731
    h = measure_holes([BASE], start_ms=BASE, end_ms=BASE + 5 * M1_MS, tf_ms=M1_MS,
                      anchor_offset_ms=0, is_trading_fn=open_only_first)
    assert h.expected == 1 and h.missing == 0


# ── age ─────────────────────────────────────────────────────────────────────
def test_age_zero_when_last_bar_is_the_last_closed_bucket():
    now = BASE + 10 * M1_MS + 30_000
    a = measure_age([BASE + 9 * M1_MS], now_ms=now, tf_ms=M1_MS, anchor_offset_ms=0, is_trading_fn=ALWAYS)
    assert a.age_buckets == 0


def test_age_counts_missed_closed_buckets():
    now = BASE + 10 * M1_MS
    a = measure_age([BASE + 6 * M1_MS], now_ms=now, tf_ms=M1_MS, anchor_offset_ms=0, is_trading_fn=ALWAYS)
    assert a.age_buckets == 3


def test_age_is_none_without_bars():
    assert measure_age([], now_ms=BASE, tf_ms=M1_MS, anchor_offset_ms=0, is_trading_fn=ALWAYS).age_buckets is None


# ── cascade ─────────────────────────────────────────────────────────────────
def _m1_hour(start_ms: int, *, high: float = 2.0) -> list[CandleBar]:
    return [
        _bar(start_ms + i * M1_MS, M1_MS, o=1.0 + i, h=high + i, low=0.5, c=1.5 + i, src="history")
        for i in range(60)
    ]


def test_cascade_accepts_correct_aggregation():
    src = _m1_hour(BASE)
    h1 = _bar(BASE, H1_MS, o=src[0].o, h=max(b.h for b in src), low=min(b.low for b in src), c=src[-1].c)
    r = measure_cascade([h1], src, target_tf_ms=H1_MS, source_tf_ms=M1_MS, anchor_offsets_ms=[0])
    assert (r.checked, r.mismatched) == (1, 0)


def test_cascade_catches_wrong_high():
    src = _m1_hour(BASE)
    h1 = _bar(BASE, H1_MS, o=src[0].o, h=999.0, low=min(b.low for b in src), c=src[-1].c)
    r = measure_cascade([h1], src, target_tf_ms=H1_MS, source_tf_ms=M1_MS, anchor_offsets_ms=[0])
    assert r.mismatched == 1 and r.mismatch_samples == (BASE,)


def test_cascade_skips_incomplete_bucket_instead_of_blaming_it():
    """Неповний набір на межі сесії — не дефект деривації."""
    src = _m1_hour(BASE)[:10]
    h1 = _bar(BASE, H1_MS)
    r = measure_cascade([h1], src, target_tf_ms=H1_MS, source_tf_ms=M1_MS, anchor_offsets_ms=[0])
    assert (r.checked, r.mismatched, r.skipped_incomplete) == (0, 0, 1)


# ── depth / anchor / grading ────────────────────────────────────────────────
def test_depth_reports_span_and_sufficiency():
    opens = [BASE + i * D1_MS for i in range(10)]
    d = measure_depth(opens, required_bars=20)
    assert d.bars == 10 and d.span_days == 9.0 and d.enough is False
    assert measure_depth(opens, required_bars=5).enough is True


def test_anchor_on_session_edge_detects_mid_session_anchor():
    edge = BASE + 79_200_000
    on_edge = lambda ms: ms >= edge  # noqa: E731
    assert check_anchor_on_session_edge(edge, tf_ms=D1_MS, is_trading_fn=on_edge) is True
    assert check_anchor_on_session_edge(edge + M1_MS, tf_ms=D1_MS, is_trading_fn=on_edge) is False


def test_grading_red_beats_yellow_and_lists_reasons():
    geo = measure_geometry([_bar(BASE + 7, M1_MS)], tf_ms=M1_MS, anchor_offsets_ms=[0])
    depth = measure_depth([BASE], required_bars=100)
    g = grade_symbol_tf(geometry=geo, depth=depth)
    assert g.grade == "RED" and any("align_bad" in r for r in g.reasons)
    assert any("depth" in r for r in g.reasons), "YELLOW-причини не губляться у RED-вердикті"


def test_grading_green_for_clean_data():
    bars = [_bar(BASE + i * M1_MS, M1_MS) for i in range(5)]
    g = grade_symbol_tf(
        geometry=measure_geometry(bars, tf_ms=M1_MS, anchor_offsets_ms=[0]),
        depth=measure_depth([b.open_time_ms for b in bars], required_bars=5),
        holes=measure_holes([b.open_time_ms for b in bars], start_ms=BASE, end_ms=BASE + 5 * M1_MS,
                            tf_ms=M1_MS, anchor_offset_ms=0, is_trading_fn=ALWAYS),
    )
    assert g.grade == "GREEN" and g.reasons == []


@pytest.mark.parametrize("missing_ratio,expected", [(0.0, "RED"), (1.0, "YELLOW")])
def test_holes_tolerance_moves_verdict(missing_ratio, expected):
    holes = measure_holes([BASE], start_ms=BASE, end_ms=BASE + 3 * M1_MS, tf_ms=M1_MS,
                          anchor_offset_ms=0, is_trading_fn=ALWAYS)
    assert grade_symbol_tf(holes=holes, max_missing_ratio=missing_ratio).grade == expected
