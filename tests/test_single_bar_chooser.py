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
    result, _geom = _ensure_sorted_dedup([dict(b) for b in group])
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


D1_MS = 86_400_000
OT_21, OT_22 = 1_729_112_400_000, 1_729_116_000_000  # 16.10.2024 21:00 (літня сітка) і 22:00 UTC (поза сезоном)


def _d1(open_ms, *, fmt, partial=False, complete=True, src="history"):
    """Той самий D1-бар у формі, в якій його бачить кожен шлях читання.

    disk  — рядок part-файла: extensions є, ts немає;
    ram   — LWC-елемент RAM-вікна: extensions зберігаються, event_ts = close (uds.py ~1848);
    redis — канонічний бар з Redis-payload: extensions зрізано, event_ts = close (uds.py ~1940).
    """
    bar = {"open_time_ms": open_ms, "close_time_ms": open_ms + D1_MS, "o": 1.0, "h": 2.0, "low": 0.5,
           "c": 1.5, "v": 10.0, "complete": complete, "src": src}
    if fmt in ("disk", "ram") and partial:
        bar["extensions"] = {"partial": True}
    if fmt in ("ram", "redis") and complete:
        bar["event_ts"] = open_ms + D1_MS
    return bar


def _read_path_opens(fmt, earlier_kw, later_kw):
    result, _geom = _ensure_sorted_dedup([_d1(OT_21, fmt=fmt, **earlier_kw), _d1(OT_22, fmt=fmt, **later_kw)])
    return tuple(b["open_time_ms"] for b in result)


D1_PAIRS = {
    "повна нічия": ({}, {}),
    "ранній partial на диску": ({"partial": True}, {}),
    "пізній partial на диску": ({}, {"partial": True}),
    "пізній не final": ({}, {"src": "preview"}),
    "ранній не final": ({"src": "preview"}, {}),
}


@pytest.mark.parametrize("case", sorted(D1_PAIRS))
def test_d1_bars_of_one_day_are_not_merged_on_any_read_path(case):
    """ADR-0095 §3.3: колишній near-dedup D1 (поріг 2 год) зливав 21:00 і 22:00 одного дня і тим тихо ховав D1 поза
    сезонною сіткою. Читач сітку не фільтрує: на кожному шляху читання — обидва бари, однаково (ADR-0094)."""
    earlier_kw, later_kw = D1_PAIRS[case]
    opens = {fmt: _read_path_opens(fmt, earlier_kw, later_kw) for fmt in ("disk", "ram", "redis")}
    assert set(opens.values()) == {(OT_21, OT_22)}, opens


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


def test_repair_dedup_says_loudly_when_the_last_line_lost(tmp_path, capsys):
    """Після ADR-0094 перебудований partial може програти старому цілому — оператор мусить це бачити."""
    dedup_file(_write(tmp_path, GROUPS["partial_last"]))
    assert "DEDUP_KEPT_NOT_LAST" in capsys.readouterr().out


def test_repair_dedup_stays_quiet_when_the_last_line_wins(tmp_path, capsys):
    """Контроль: звичайна нічия — переміг останній рядок, тривоги немає."""
    dedup_file(_write(tmp_path, GROUPS["full_tie"]))
    out = capsys.readouterr().out
    assert "kept_not_last=0" in out and "DEDUP_KEPT_NOT_LAST" not in out
