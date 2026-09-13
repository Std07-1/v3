"""ADR-0094 P1.2–P1.5: усі місця дедупу обирають переможця одним правилом.

Корінь, який тут закрито: дві однойменні `_choose_better_bar` з протилежним тай-брейком, через які
139 ключів на живих символах малювались різною свічкою в TAIL (cold-load) і RANGE (scrollback).
Тести перевіряють не «функцію імпортовано», а ПОВЕДІНКУ кожного шляху на тих самих групах, плюс гейт,
що другої копії вибирача в репо більше немає.
"""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

from runtime.store.layers.disk_layer import _dedup_open_ms
from runtime.store.uds import _ensure_sorted_dedup
from tools.repair.dedup_jsonl_lastwins import dedup_file

REPO = pathlib.Path(__file__).resolve().parents[1]
OPEN_MS = 1_774_974_960_000
TF_MS = 180_000


def _bar(marker, *, partial=None, complete=True, src="derived", open_ms=OPEN_MS):
    bar = {"symbol": "XAU/USD", "tf_s": 180, "open_time_ms": open_ms, "close_time_ms": open_ms + TF_MS,
           "o": 1.0, "h": 2.0, "low": 0.5, "c": 1.5, "v": 10.0, "complete": complete, "src": src,
           "marker": marker}
    if partial is not None:
        bar["extensions"] = {"partial": partial}
    return bar


GROUPS = {
    "partial_first": [_bar("partial", partial=True), _bar("whole", partial=False)],
    "partial_last": [_bar("whole", partial=False), _bar("partial", partial=True)],
    "full_tie": [_bar("earlier"), _bar("later")],
    "incomplete_last": [_bar("final"), _bar("preview", complete=False)],
}
EXPECTED = {"partial_first": "whole", "partial_last": "whole", "full_tie": "later", "incomplete_last": "final"}


def _tail_winner(group):
    result, _dropped = _dedup_open_ms([dict(b) for b in group])
    assert len(result) == 1
    return result[0]["marker"]


def _range_winner(group):
    result, _geom = _ensure_sorted_dedup([dict(b) for b in group], tf_ms=TF_MS)
    assert len(result) == 1
    return result[0]["marker"]


@pytest.mark.parametrize("name", sorted(GROUPS))
def test_tail_and_range_pick_the_same_record(name):
    """Суть ADR-0094: один ключ — одна свічка, незалежно від шляху читання."""
    group = GROUPS[name]
    assert _tail_winner(group) == _range_winner(group) == EXPECTED[name]


def _write(tmp_path, bars):
    path = tmp_path / "part-20260601.jsonl"
    path.write_text("".join(json.dumps(b) + "\n" for b in bars), encoding="utf-8")
    return path


@pytest.mark.parametrize("name", sorted(GROUPS))
def test_repair_dedup_keeps_what_readers_show(tmp_path, capsys, name):
    """Ремонт на диску не має права лишити запис, який читач відкинув би."""
    path = _write(tmp_path, GROUPS[name])
    dedup_file(path)
    kept = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [b["marker"] for b in kept] == [EXPECTED[name]]
    assert kept[0]["marker"] == _range_winner(GROUPS[name])


def test_repair_dedup_used_to_keep_the_partial_bar(tmp_path, capsys):
    """Контроль того, що саме змінилось: чистий last-wins залишив би partial."""
    group = GROUPS["partial_last"]
    assert group[-1]["marker"] == "partial"
    path = _write(tmp_path, group)
    dedup_file(path)
    kept = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert kept["marker"] == "whole"


def test_repair_dedup_dry_run_changes_nothing(tmp_path, capsys):
    path = _write(tmp_path, GROUPS["partial_last"])
    before = path.read_text(encoding="utf-8")
    dedup_file(path, dry_run=True)
    assert path.read_text(encoding="utf-8") == before


def test_near_dedup_keeps_earlier_bar_on_tie_but_prefers_whole():
    """Near-dedup D1: нічия як і раніше за раннім баром; partial програє повному в обидва боки."""
    ot_21, ot_22 = 1_729_112_400_000, 1_729_116_000_000
    tie, _ = _ensure_sorted_dedup([_bar("21", open_ms=ot_21, src="history"),
                                   _bar("22", open_ms=ot_22)], tf_ms=86_400_000)
    assert [b["marker"] for b in tie] == ["21"]
    early_partial, _ = _ensure_sorted_dedup([_bar("21p", open_ms=ot_21, partial=True),
                                             _bar("22w", open_ms=ot_22, partial=False)], tf_ms=86_400_000)
    assert [b["marker"] for b in early_partial] == ["22w"]
    late_partial, _ = _ensure_sorted_dedup([_bar("21w", open_ms=ot_21, partial=False),
                                            _bar("22p", open_ms=ot_22, partial=True)], tf_ms=86_400_000)
    assert [b["marker"] for b in late_partial] == ["21w"]


def test_only_one_bar_chooser_exists_in_the_repo():
    """Гейт: друга однойменна функція вибору і стала причиною ADR-0094 — вона не мусить повернутись."""
    definitions = []
    for top in ("core", "runtime", "tools", "app"):
        for path in (REPO / top).rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.lstrip("_") == "choose_better_bar":
                    definitions.append("%s:%d" % (path.relative_to(REPO).as_posix(), node.lineno))
    assert len(definitions) == 1 and definitions[0].startswith("core/model/bar_choice.py:"), definitions
