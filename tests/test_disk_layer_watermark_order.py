"""DiskLayer.last_open_ms — watermark не залежить від порядку рядків у part-файлі.

Чому цей тест існує. `tools/fetch_tf_backfill` сіє історію сторінками від сьогодні
назад, тому всередині однієї доби бари лягають двома шматками: спершу свіжіший
шматок, за ним старіший. Останній РЯДОК такого файла старіший за максимум, а
watermark читався саме з останнього рядка. Занижений watermark нічого не блокує —
навпаки, пускає назад бари, які на диску вже є (`uds._watermark_drop_reason`:
`open_ms > wm` → приймається), і вони дописуються вдруге; далі TAIL читає
last-wins, а RANGE first-wins, тобто одна й та сама свічка може відрізнятись
залежно від шляху читання.

Кожен випадок має контроль: той самий вміст у правильному порядку.
"""
from __future__ import annotations

import json
import logging
import os

from runtime.store.layers.disk_layer import DiskLayer

M1_MS = 60_000
BASE_MS = 1_780_000_000_000 // M1_MS * M1_MS
SYMBOL = "XAU/USD"


def _bar(open_ms: int) -> dict:
    return {
        "symbol": SYMBOL,
        "tf_s": 60,
        "open_time_ms": open_ms,
        "close_time_ms": open_ms + M1_MS,
        "o": 1.0,
        "h": 2.0,
        "low": 0.5,
        "c": 1.5,
        "v": 10.0,
        "complete": True,
        "src": "history",
    }


def _write_part(root, day: str, opens) -> None:
    d = os.path.join(str(root), SYMBOL.replace("/", "_"), "tf_60")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "part-%s.jsonl" % day), "w", encoding="utf-8") as fh:
        for open_ms in opens:
            fh.write(json.dumps(_bar(open_ms)) + "\n")


def _seam_opens():
    """Порядок рядків, який лишає сіяння: свіжа сторінка, за нею старіша."""
    newer = [BASE_MS + i * M1_MS for i in range(50, 100)]
    older = [BASE_MS + i * M1_MS for i in range(0, 50)]
    return newer + older


def test_unsorted_part_returns_max_not_last_line(tmp_path, caplog):
    _write_part(tmp_path, "20260601", _seam_opens())
    with caplog.at_level(logging.WARNING):
        got = DiskLayer(str(tmp_path)).last_open_ms(SYMBOL, 60)
    assert got == BASE_MS + 99 * M1_MS, "watermark мусить бути максимумом, не останнім рядком"
    assert "DISK_PART_UNSORTED" in caplog.text, "невідсортований файл має бути гучним (I5)"


def test_sorted_part_gives_same_value_and_stays_silent(tmp_path, caplog):
    """Контроль: той самий вміст у правильному порядку — те саме число і без тривоги."""
    _write_part(tmp_path, "20260601", sorted(_seam_opens()))
    with caplog.at_level(logging.WARNING):
        got = DiskLayer(str(tmp_path)).last_open_ms(SYMBOL, 60)
    assert got == BASE_MS + 99 * M1_MS
    assert "DISK_PART_UNSORTED" not in caplog.text


def test_newest_part_wins_over_older_part(tmp_path):
    """Контроль монотонності імен: максимум береться з найновішої доби."""
    _write_part(tmp_path, "20260601", [BASE_MS + i * M1_MS for i in range(0, 10)])
    _write_part(tmp_path, "20260602", [BASE_MS + 86_400_000 + i * M1_MS for i in range(0, 10)])
    got = DiskLayer(str(tmp_path)).last_open_ms(SYMBOL, 60)
    assert got == BASE_MS + 86_400_000 + 9 * M1_MS


def test_empty_newest_part_falls_back_instead_of_wiping_watermark(tmp_path):
    """Порожній найновіший файл раніше давав None = watermark немає = вся історія назад."""
    _write_part(tmp_path, "20260601", [BASE_MS + i * M1_MS for i in range(0, 10)])
    _write_part(tmp_path, "20260602", [])
    got = DiskLayer(str(tmp_path)).last_open_ms(SYMBOL, 60)
    assert got == BASE_MS + 9 * M1_MS, "мусить відкотитись до попереднього файла, а не віддати None"


def test_no_parts_returns_none(tmp_path):
    """Контроль: коли барів справді немає, None лишається правильною відповіддю."""
    assert DiskLayer(str(tmp_path)).last_open_ms(SYMBOL, 60) is None


def test_broken_lines_do_not_hide_the_max(tmp_path):
    """Биті рядки пропускаються, але максимум серед валідних — той самий."""
    d = os.path.join(str(tmp_path), SYMBOL.replace("/", "_"), "tf_60")
    os.makedirs(d)
    with open(os.path.join(d, "part-20260601.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(_bar(BASE_MS + 99 * M1_MS)) + "\n")
        fh.write("{не json\n")
        fh.write("\n")
        fh.write(json.dumps({"symbol": SYMBOL, "open_time_ms": "не число"}) + "\n")
        fh.write(json.dumps(_bar(BASE_MS)) + "\n")
    assert DiskLayer(str(tmp_path)).last_open_ms(SYMBOL, 60) == BASE_MS + 99 * M1_MS
