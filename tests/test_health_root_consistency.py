"""Чесний health: вимір бачить те, що бачать читачі, і звіряє кожен derived-бар з коренем M1.

Навіщо цей файл. 14.09.2026 вимір каскаду показував 137 розбіжностей на XAU/XAG, а прямий звір
кожного derived-бару з M1 — 659, плюс 6 на SPX500, який health уже вважав GREEN. Причини в самому
вимірі: (1) батьків не дедуплікував — рахував переможений запис, якого графік не показує; (2) дітей
згортав позиційно, третім вибирачем у репо; (3) порівнював лише СУСІДНІ рівні, тож M30, зібраний зі
застарілого M15, для каскаду виглядав чистим. Кожен тест — синтетика з відомою відповіддю і контролем.
"""
from __future__ import annotations

from core.health import grade_symbol_tf, measure_cascade, measure_root_consistency, ssot_winners
from core.model.bars import CandleBar

M1_MS = 60_000
M15_MS = 900_000
M30_MS = 1_800_000
BASE = 1_767_225_600_000  # 2026-01-01 00:00 UTC


def _bar(open_ms, tf_ms, *, o, h, low, c, src="derived", ext=None, marker=""):
    return CandleBar(symbol="X", tf_s=tf_ms // 1000, open_time_ms=open_ms, close_time_ms=open_ms + tf_ms,
                     o=o, h=h, low=low, c=c, v=1.0, complete=True, src=src,
                     extensions=dict(ext or {}, **({"marker": marker} if marker else {})))


def _minutes(start_ms, n, *, bump=0.0):
    return [_bar(start_ms + i * M1_MS, M1_MS, o=10.0 + i, h=11.0 + i + bump, low=9.0 + i, c=10.5 + i, src="history")
            for i in range(n)]


def _aggregate(open_ms, tf_ms, children, **kw):
    return _bar(open_ms, tf_ms, o=children[0].o, h=max(b.h for b in children),
                low=min(b.low for b in children), c=children[-1].c, **kw)


def _partial(bar):
    return bool(bar.extensions.get("partial"))


# ── дедуп так, як читачі ────────────────────────────────────────────────────
def test_ssot_winners_prefers_whole_bar_and_later_record_on_tie():
    whole = _bar(BASE, M1_MS, o=1, h=2, low=0, c=1, marker="whole")
    partial = _bar(BASE, M1_MS, o=5, h=6, low=4, c=5, ext={"partial": True}, marker="partial")
    later = _bar(BASE + M1_MS, M1_MS, o=1, h=2, low=0, c=1, marker="later")
    earlier = _bar(BASE + M1_MS, M1_MS, o=3, h=4, low=2, c=3, marker="earlier")
    winners = ssot_winners([whole, earlier, partial, later])
    assert [b.extensions["marker"] for b in winners] == ["whole", "later"]


def test_cascade_ignores_the_losing_record_of_a_duplicated_parent():
    """Артефакт 1: переможений запис батька, якого графік не показує, не є розбіжністю."""
    kids = _minutes(BASE, 15)
    stale = _bar(BASE, M15_MS, o=0.0, h=99.0, low=0.0, c=0.0)
    right = _aggregate(BASE, M15_MS, kids)
    r = measure_cascade([stale, right], kids, target_tf_ms=M15_MS, source_tf_ms=M1_MS, anchor_offsets_ms=[0])
    assert (r.checked, r.mismatched) == (1, 0)


def test_cascade_control_winner_itself_wrong_is_still_caught():
    kids = _minutes(BASE, 15)
    right = _aggregate(BASE, M15_MS, kids)
    stale = _bar(BASE, M15_MS, o=0.0, h=99.0, low=0.0, c=0.0)
    r = measure_cascade([right, stale], kids, target_tf_ms=M15_MS, source_tf_ms=M1_MS, anchor_offsets_ms=[0])
    assert r.mismatched == 1


def test_cascade_picks_children_the_way_readers_do():
    """Артефакт 2: позиційний last-wins узяв би partial-дитину, яку читач відкидає."""
    kids = _minutes(BASE, 15)
    partial_last = _bar(kids[7].open_time_ms, M1_MS, o=50, h=99, low=1, c=50, ext={"partial": True})
    parent = _aggregate(BASE, M15_MS, kids)
    r = measure_cascade([parent], kids + [partial_last], target_tf_ms=M15_MS, source_tf_ms=M1_MS,
                        anchor_offsets_ms=[0])
    assert r.mismatched == 0


# ── корінь M1 ────────────────────────────────────────────────────────────────
def test_root_accepts_bar_equal_to_its_minutes():
    minutes = _minutes(BASE, 15)
    r = measure_root_consistency([_aggregate(BASE, M15_MS, minutes)], minutes, tf_ms=M15_MS)
    assert (r.checked, r.mismatched, r.uncovered) == (1, 0, 0)


def test_root_catches_bar_built_from_other_minutes():
    """M1 перезалили, бар не перебудували."""
    old_minutes = _minutes(BASE, 15)
    new_minutes = _minutes(BASE, 15, bump=3.0)
    r = measure_root_consistency([_aggregate(BASE, M15_MS, old_minutes)], new_minutes, tf_ms=M15_MS)
    assert (r.mismatched, r.mismatch_samples) == (1, (BASE,))
    assert grade_symbol_tf(root=r).grade == "RED"


def test_root_does_not_blame_bar_built_from_the_minutes_that_exist():
    """Контроль: бакет із дірками в M1 — не дефект, якщо бар зібрано саме з наявних хвилин."""
    minutes = [m for i, m in enumerate(_minutes(BASE, 15)) if i not in (3, 4, 11)]
    r = measure_root_consistency([_aggregate(BASE, M15_MS, minutes)], minutes, tf_ms=M15_MS)
    assert r.mismatched == 0


def test_root_declared_partial_is_reported_not_blamed():
    minutes = _minutes(BASE, 15, bump=3.0)
    bar = _aggregate(BASE, M15_MS, _minutes(BASE, 15), ext={"partial": True})
    r = measure_root_consistency([bar], minutes, tf_ms=M15_MS, declares_partial_fn=_partial)
    assert (r.mismatched, r.declared_partial) == (0, 1)


def test_root_bar_without_any_minutes_is_uncovered_not_ok():
    """Історія, старша за M1 (брокерський імпорт): перевірити нема чим — ні «ок», ні дефект."""
    r = measure_root_consistency([_bar(BASE, M15_MS, o=1, h=2, low=0, c=1)], _minutes(BASE + M15_MS, 15),
                                 tf_ms=M15_MS)
    assert (r.checked, r.uncovered, r.mismatched) == (0, 1, 0)


def test_root_sees_what_adjacent_cascade_cannot():
    """Навіщо корінь: M30 зі застарілого M15 узгоджений з тим M15, тож каскад мовчить, а M1 — ні."""
    new_minutes = _minutes(BASE, 30, bump=3.0)
    stale_m15 = [_aggregate(BASE, M15_MS, _minutes(BASE, 15)),
                 _aggregate(BASE + M15_MS, M15_MS, _minutes(BASE + M15_MS, 15))]
    m30_from_stale = _aggregate(BASE, M30_MS, stale_m15)
    cascade = measure_cascade([m30_from_stale], stale_m15, target_tf_ms=M30_MS, source_tf_ms=M15_MS,
                              anchor_offsets_ms=[0])
    root = measure_root_consistency([m30_from_stale], new_minutes, tf_ms=M30_MS)
    assert cascade.mismatched == 0, "каскад бачить узгоджену пару і мовчить"
    assert root.mismatched == 1, "корінь бачить, що обидва рівні застаріли"


def test_root_checks_the_winner_not_every_record():
    minutes = _minutes(BASE, 15)
    stale = _bar(BASE, M15_MS, o=0.0, h=99.0, low=0.0, c=0.0)
    r = measure_root_consistency([stale, _aggregate(BASE, M15_MS, minutes)], minutes, tf_ms=M15_MS)
    assert (r.checked, r.mismatched) == (1, 0)
