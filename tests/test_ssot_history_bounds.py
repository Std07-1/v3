"""Межі історії на диску — min/max, а не перший/останній рядок part-файла.

`tools/rebuild_from_m1` бере ці дві функції як типові `--start` / `--end`, коли
оператор не задав діапазон явно. Part-файл не зобов'язаний бути відсортованим:
`tools/fetch_tf_backfill` сіє сторінками від сьогодні назад і лишає шов на кожному
кроці ланцюжка. На шві «останній рядок» старіший за максимум, а «перший рядок»
новіший за мінімум — тобто перебудова мовчки не доходила ні до найсвіжіших барів,
ні до найстаріших. Кожен випадок має контроль: той самий вміст у правильному порядку.
"""
from __future__ import annotations

import json
import logging
import os

from runtime.store.ssot_jsonl import head_first_bar_time_ms, tail_last_bar_time_ms

M1_MS = 60_000
BASE_MS = 1_780_000_000_000 // M1_MS * M1_MS
SYMBOL = "XAU/USD"
OLDEST = BASE_MS
NEWEST = BASE_MS + 99 * M1_MS


def _write_part(root, day: str, opens) -> None:
    d = os.path.join(str(root), SYMBOL.replace("/", "_"), "tf_60")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "part-%s.jsonl" % day), "w", encoding="utf-8") as fh:
        for open_ms in opens:
            fh.write(json.dumps({
                "symbol": SYMBOL, "tf_s": 60, "open_time_ms": open_ms,
                "close_time_ms": open_ms + M1_MS, "o": 1.0, "h": 2.0, "low": 0.5,
                "c": 1.5, "v": 10.0, "complete": True, "src": "history",
            }) + "\n")


def _seam_opens():
    """Порядок рядків, який лишає сіяння: свіжа сторінка, за нею старіша."""
    newer = [BASE_MS + i * M1_MS for i in range(50, 100)]
    older = [BASE_MS + i * M1_MS for i in range(0, 50)]
    return newer + older


def test_tail_on_seam_returns_max_not_last_line(tmp_path, caplog):
    _write_part(tmp_path, "20260601", _seam_opens())
    with caplog.at_level(logging.WARNING):
        got = tail_last_bar_time_ms(str(tmp_path), SYMBOL, 60)
    assert got == NEWEST, "кінець історії мусить бути максимумом"
    assert "SSOT_PART_UNSORTED" in caplog.text


def test_head_on_seam_returns_min_not_first_line(tmp_path, caplog):
    _write_part(tmp_path, "20260601", _seam_opens())
    with caplog.at_level(logging.WARNING):
        got = head_first_bar_time_ms(str(tmp_path), SYMBOL, 60)
    assert got == OLDEST, "початок історії мусить бути мінімумом"
    assert "SSOT_PART_UNSORTED" in caplog.text


def test_sorted_part_gives_same_bounds_and_stays_silent(tmp_path, caplog):
    """Контроль: той самий вміст у правильному порядку — ті самі межі, без тривоги."""
    _write_part(tmp_path, "20260601", sorted(_seam_opens()))
    with caplog.at_level(logging.WARNING):
        assert tail_last_bar_time_ms(str(tmp_path), SYMBOL, 60) == NEWEST
        assert head_first_bar_time_ms(str(tmp_path), SYMBOL, 60) == OLDEST
    assert "SSOT_PART_UNSORTED" not in caplog.text


def test_bounds_span_several_parts(tmp_path):
    """Контроль монотонності імен: початок з найстарішої доби, кінець з найновішої."""
    _write_part(tmp_path, "20260601", [BASE_MS + i * M1_MS for i in range(0, 10)])
    _write_part(tmp_path, "20260602", [BASE_MS + 86_400_000 + i * M1_MS for i in range(0, 10)])
    assert head_first_bar_time_ms(str(tmp_path), SYMBOL, 60) == BASE_MS
    assert tail_last_bar_time_ms(str(tmp_path), SYMBOL, 60) == BASE_MS + 86_400_000 + 9 * M1_MS


def test_empty_edge_parts_fall_back_to_neighbours(tmp_path):
    """Порожній крайній файл не мусить обривати межу історії в None."""
    _write_part(tmp_path, "20260601", [])
    _write_part(tmp_path, "20260602", [BASE_MS + i * M1_MS for i in range(0, 10)])
    _write_part(tmp_path, "20260603", [])
    assert head_first_bar_time_ms(str(tmp_path), SYMBOL, 60) == BASE_MS
    assert tail_last_bar_time_ms(str(tmp_path), SYMBOL, 60) == BASE_MS + 9 * M1_MS


def test_no_data_returns_none(tmp_path):
    """Контроль: коли барів справді немає, None лишається правильною відповіддю."""
    assert head_first_bar_time_ms(str(tmp_path), SYMBOL, 60) is None
    assert tail_last_bar_time_ms(str(tmp_path), SYMBOL, 60) is None
