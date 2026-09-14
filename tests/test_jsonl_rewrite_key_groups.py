"""Групи ключів part-файла і переможець кожної — так, як їх бачать читачі (ADR-0094, ADR-0096 §3.3 B).

Навіщо. Ремонт значень open/high/low патчить у файлі рівно один рядок ключа — переможця. Якщо його
визначити інакше, ніж читачі (first-/last-wins замість `choose_better_bar` у порядку файла), новий open
ляже в рядок, якого графік не показує: запис «успішний», свічка незмінна. Тому переможця тут звіряємо з
реальним кодом читача (`_select_newest_keys` + `_dedup_open_ms`) у ОБОХ порядках рядків.
"""
from __future__ import annotations

import json

import pytest

from runtime.store.layers.disk_layer import _dedup_open_ms, _select_newest_keys
from tools.repair.jsonl_rewrite import KeyGroup, key_groups, read_lines

OPEN_MS = 1_785_103_260_000  # 2026-07-26 22:01 UTC


def _bar(marker, *, partial=None, ts=None, complete=True, src="history", open_ms=OPEN_MS):
    bar = {"symbol": "XAU/USD", "tf_s": 60, "open_time_ms": open_ms, "close_time_ms": open_ms + 60_000,
           "o": 1.0, "h": 2.0, "low": 0.5, "c": 1.5, "v": 10.0, "complete": complete, "src": src, "marker": marker}
    if partial is not None:
        bar["extensions"] = {"partial": partial}
    if ts is not None:
        bar["event_ts"] = ts
    return bar


GROUPS = {
    "whole_vs_partial": [_bar("whole", partial=False), _bar("partial", partial=True)],
    "ts_smaller_later": [_bar("ts_big", ts=5), _bar("ts_small", ts=3)],
    "complete_vs_preview": [_bar("final"), _bar("preview", complete=False)],
    "full_tie": [_bar("first"), _bar("second")],
}


def _reader_marker(path):
    window = _select_newest_keys([str(path)], None, None, 10, final_only=False, skip_preview=False,
                                 final_sources=None)
    deduped, _dropped = _dedup_open_ms(window)
    assert len(deduped) == 1
    return deduped[0]["marker"]


@pytest.mark.parametrize("order", ["file_order", "reversed"])
@pytest.mark.parametrize("name", sorted(GROUPS))
def test_key_groups_winner_matches_readers_in_both_orders(tmp_path, name, order):
    """Ловить first-wins і last-wins: кожен із них хибний хоча б в одному порядку одного з кейсів."""
    bars = GROUPS[name] if order == "file_order" else list(reversed(GROUPS[name]))
    path = tmp_path / "part-20260726.jsonl"
    path.write_text("".join(json.dumps(b) + "\n" for b in bars), encoding="utf-8")
    lines = read_lines(str(path))
    groups = key_groups(lines)
    assert list(groups) == [OPEN_MS]
    group = groups[OPEN_MS]
    assert group.members == (0, 1)
    assert json.loads(lines[group.winner])["marker"] == _reader_marker(path)


def test_key_groups_skip_lines_readers_skip():
    """Рядок без цілого open_time_ms читачі пропускають — у групу не входить; індекси — у порядку файла."""
    other = OPEN_MS + 60_000
    lines = [
        json.dumps(_bar("a")),
        json.dumps(dict(_bar("str_key"), open_time_ms=str(OPEN_MS))),
        "not json",
        json.dumps(_bar("b", open_ms=other)),
        json.dumps(_bar("c")),
    ]
    groups = key_groups(lines)
    assert groups == {OPEN_MS: KeyGroup(winner=4, members=(0, 4)), other: KeyGroup(winner=3, members=(3,))}
    assert list(groups) == sorted(groups)
