"""core.model.bar_choice — єдиний вибирач переможця серед дублікатів open_time_ms (ADR-0094).

Тести читаються як специфікація порядку критеріїв і як запис того, ЧИМ нове правило відрізняється від
двох колишніх однойменних функцій. Колишні правила зафіксовано нижче як еталон (`_legacy_tail`,
`_legacy_range`) — саме так вони виглядали в `runtime/store/layers/disk_layer.py:183` і
`runtime/store/uds.py:1965` до ADR-0094. Їхні копії в модулях видаляються, а еталон лишається тут,
щоб різниця семантики не загубилась разом із ними.
"""
from __future__ import annotations

import itertools

import pytest

from core.model.bar_choice import (
    choose_better_bar,
    is_complete,
    is_final_source,
    is_partial,
    ts_priority,
)
from core.model.bars import FINAL_SOURCES


def _bar(complete=True, src="derived", partial=None, ts=None, marker="x", **extra):
    bar = {"open_time_ms": 1_774_974_960_000, "complete": complete, "src": src, "marker": marker}
    if partial is not None:
        bar["extensions"] = {"partial": partial}
    if ts is not None:
        bar["event_ts"] = ts
    bar.update(extra)
    return bar


# ── еталони колишньої поведінки (до ADR-0094) ───────────────────────────────
def _legacy_final(bar, empty_is_history):
    src = bar.get("src")
    if not isinstance(src, str):
        return False
    if empty_is_history and src == "":
        src = "history"
    return src in FINAL_SOURCES


def _legacy_ts(bar):
    for key in ("event_ts", "ssot_write_ts_ms"):
        if isinstance(bar.get(key), int):
            return bar[key]
    return None


def _legacy_tail(existing, incoming):
    """disk_layer._choose_better_bar: complete → final → ts → нічия LAST."""
    ec, ic = bool(existing.get("complete")), bool(incoming.get("complete"))
    if ic and not ec:
        return incoming
    if ec and not ic:
        return existing
    ef, inf = _legacy_final(existing, True), _legacy_final(incoming, True)
    if inf and not ef:
        return incoming
    if ef and not inf:
        return existing
    et, it = _legacy_ts(existing), _legacy_ts(incoming)
    if it is not None and et is not None:
        if it > et:
            return incoming
        if it < et:
            return existing
    elif it is not None:
        return incoming
    elif et is not None:
        return existing
    return incoming


def _legacy_range(existing, incoming):
    """uds._choose_better_bar: complete → final → нічия FIRST, без ts і без порожнього src = history."""
    ec, ic = bool(existing.get("complete")), bool(incoming.get("complete"))
    if ic and not ec:
        return incoming
    if ec and not ic:
        return existing
    ef, inf = _legacy_final(existing, False), _legacy_final(incoming, False)
    if inf and not ef:
        return incoming
    if ef and not inf:
        return existing
    return existing


# ── порядок критеріїв ────────────────────────────────────────────────────────
@pytest.mark.parametrize("order", ["better_first", "better_second"])
def test_complete_beats_incomplete(order):
    good, bad = _bar(complete=True, marker="good"), _bar(complete=False, marker="bad")
    pair = (good, bad) if order == "better_first" else (bad, good)
    assert choose_better_bar(*pair)["marker"] == "good"


@pytest.mark.parametrize("order", ["better_first", "better_second"])
def test_final_source_beats_non_final(order):
    good, bad = _bar(src="history", marker="good"), _bar(src="preview", marker="bad")
    pair = (good, bad) if order == "better_first" else (bad, good)
    assert choose_better_bar(*pair)["marker"] == "good"


@pytest.mark.parametrize("order", ["better_first", "better_second"])
def test_whole_bar_beats_partial(order):
    """Новий критерій ADR-0094: найсильніший виміряний предиктор правди."""
    good, bad = _bar(partial=False, marker="good"), _bar(partial=True, marker="bad")
    pair = (good, bad) if order == "better_first" else (bad, good)
    assert choose_better_bar(*pair)["marker"] == "good"


def test_missing_partial_flag_counts_as_whole():
    assert choose_better_bar(_bar(partial=True, marker="p"), _bar(marker="w"))["marker"] == "w"


def test_complete_outranks_partial():
    """I3 вище за повноту: повний-але-preview не перемагає завершений partial."""
    assert choose_better_bar(
        _bar(complete=True, partial=True, marker="final_partial"),
        _bar(complete=False, partial=False, marker="preview_whole"),
    )["marker"] == "final_partial"


def test_final_source_outranks_partial():
    assert choose_better_bar(
        _bar(src="derived", partial=True, marker="final_partial"),
        _bar(src="preview", partial=False, marker="nonfinal_whole"),
    )["marker"] == "final_partial"


def test_partial_outranks_timestamp():
    assert choose_better_bar(
        _bar(partial=False, ts=1, marker="whole_old"),
        _bar(partial=True, ts=2, marker="partial_new"),
    )["marker"] == "whole_old"


def test_newer_timestamp_wins_and_present_beats_absent():
    assert choose_better_bar(_bar(ts=2, marker="new"), _bar(ts=1, marker="old"))["marker"] == "new"
    assert choose_better_bar(_bar(marker="none"), _bar(ts=1, marker="ts"))["marker"] == "ts"
    assert choose_better_bar(_bar(ts=1, marker="ts"), _bar(marker="none"))["marker"] == "ts"


def test_full_tie_goes_to_later_record():
    """Крок 5: пізніший у порядку файла = пізніший запис."""
    assert choose_better_bar(_bar(marker="earlier"), _bar(marker="later"))["marker"] == "later"


# ── предикати ────────────────────────────────────────────────────────────────
def test_empty_src_is_history():
    assert is_final_source({"src": ""}) is True
    assert is_final_source({"src": None}) is False
    assert is_final_source({}) is False


def test_final_sources_override_is_respected():
    assert is_final_source({"src": "history"}, frozenset({"derived"})) is False


def test_bool_is_not_a_timestamp():
    assert ts_priority({"event_ts": True}) is None
    assert ts_priority({"event_ts": False, "ssot_write_ts_ms": 7}) == 7


def test_partial_needs_a_mapping():
    assert is_partial({"extensions": {"partial": True}}) is True
    assert is_partial({"extensions": "partial"}) is False
    assert is_partial({}) is False


def test_complete_predicate():
    assert is_complete({"complete": True}) and not is_complete({"complete": False}) and not is_complete({})


# ── виміряні випадки (ADR-0094 §1.3–1.4) ─────────────────────────────────────
def test_measured_case_partial_written_first_full_second():
    """XAU/USD M3 1774974960000: у файлі partial, потім повний. RANGE брав partial (хибно)."""
    partial = _bar(partial=True, marker="partial", o=4619.98, v=2119.0)
    whole = _bar(partial=False, marker="whole", o=4616.17, v=4903.0)
    assert _legacy_range(partial, whole)["marker"] == "partial"
    assert _legacy_tail(partial, whole)["marker"] == "whole"
    assert choose_better_bar(partial, whole)["marker"] == "whole"


def test_measured_case_full_written_first_partial_second():
    """Дзеркальний клас (15 груп): TAIL брав пізніший partial (хибно), RANGE — повний."""
    whole = _bar(partial=False, marker="whole")
    partial = _bar(partial=True, marker="partial")
    assert _legacy_tail(whole, partial)["marker"] == "partial"
    assert _legacy_range(whole, partial)["marker"] == "whole"
    assert choose_better_bar(whole, partial)["marker"] == "whole"


def test_group_fold_in_file_order_finds_the_whole_bar():
    members = [_bar(partial=True, marker="a"), _bar(partial=False, marker="b"), _bar(partial=True, marker="c")]
    winner = members[0]
    for bar in members[1:]:
        winner = choose_better_bar(winner, bar)
    assert winner["marker"] == "b"


# ── межі зміни поведінки ─────────────────────────────────────────────────────
_GRID = list(itertools.product(
    [True, False, None],                        # complete
    ["history", "derived", "", "preview", None],  # src
    [None, False, True],                        # partial
    [None, 1, 2],                               # ts
))


def _grid_bar(spec, marker):
    complete, src, partial, ts = spec
    return _bar(complete=complete, src=src, partial=partial, ts=ts, marker=marker)


def test_without_partial_flags_new_rule_equals_legacy_tail():
    """Отже для TAIL змінюються ЛИШЕ групи, де є partial — рівно 19 ключів на живих символах."""
    specs = [s for s in _GRID if s[2] is not True]
    for a, b in itertools.product(specs, repeat=2):
        existing, incoming = _grid_bar(a, "e"), _grid_bar(b, "i")
        assert choose_better_bar(existing, incoming)["marker"] == _legacy_tail(existing, incoming)["marker"], (a, b)


def test_new_rule_differs_from_legacy_range_only_by_documented_steps():
    """RANGE змінюється лише через кроки, яких у ньому не було: partial, ts, нічия LAST, порожній src."""
    for a, b in itertools.product(_GRID, repeat=2):
        existing, incoming = _grid_bar(a, "e"), _grid_bar(b, "i")
        new = choose_better_bar(existing, incoming)["marker"]
        old = _legacy_range(existing, incoming)["marker"]
        if new == old:
            continue
        ec, esrc, ep, ets = a
        ic, isrc, ip, its = b
        explained = (
            bool(ep) != bool(ip)                                   # partial
            or ets != its                                           # ts
            or (bool(ec) == bool(ic) and (esrc == "" or isrc == ""))  # порожній src
            or (bool(ec) == bool(ic)                                # нічия: LAST замість FIRST
                and _legacy_final(existing, True) == _legacy_final(incoming, True))
        )
        assert explained, (a, b, new, old)


def test_boundary_partial_alone_is_not_partial():
    """38 018 барів на диску мають boundary_partial без partial — це звичайні бари на межі сесії."""
    assert is_partial({"extensions": {"boundary_partial": True}}) is False
    assert is_partial({"extensions": {"boundary_partial": True, "partial": True}}) is True
