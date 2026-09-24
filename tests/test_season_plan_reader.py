"""План S7.1 (ADR-0095): джерело плану — SSOT так, як його бачить читач, і межі джерела символу.

Бари джерела бере `DiskLayer` (вибирач дублікатів ADR-0094, без чужих і нефінальних рядків), тож план будує
похідні з того самого, що малює графік. Рядок без OHLC у джерело не йде і не мовчки. Кінець джерела — крок за
хвостом M1 (без M1 — за хвостом H1), не далі вікна: формуючий хвіст не фіналізується.
"""
from __future__ import annotations

import json
import logging

from core.session_anchor import RULE_NY_CLOSE_US_DST
from tools.repair import season_plan as sp

H1_MS = 3_600_000
OPEN_MS = 1_773_136_800_000  # Вт 10.03.2026 10:00 UTC


def _row(open_ms: int, **fields) -> str:
    row = {"symbol": "XAU/USD", "tf_s": 3600, "open_time_ms": open_ms, "close_time_ms": open_ms + H1_MS, "o": 1.0,
           "h": 2.0, "low": 0.5, "c": 1.5, "v": 3.0, "complete": True, "src": "derived"}
    row.update(fields)
    return json.dumps({k: v for k, v in row.items() if v is not None})


def test_reader_sees_ssot_like_the_chart(tmp_path, caplog):
    folder = tmp_path / "XAU_USD" / "tf_3600"
    folder.mkdir(parents=True)
    (folder / "part-20260310.jsonl").write_text("\n".join([
        _row(OPEN_MS, c=9.0),  # цілий бар
        _row(OPEN_MS, c=7.0, extensions={"partial": True}),  # пізніший, але partial — програє (ADR-0094)
        _row(OPEN_MS + H1_MS, symbol="XAG/USD"),  # чужий символ
        _row(OPEN_MS + 2 * H1_MS, o=None),  # без OHLC
        _row(OPEN_MS + 3 * H1_MS, src="preview_tick"),  # нефінальний
        _row(OPEN_MS + 4 * H1_MS, l=0.25, low=None),  # легасі-поле l
    ]) + "\n", encoding="utf-8")
    reader = sp.SourceReader(str(tmp_path), "XAU/USD")
    with caplog.at_level(logging.WARNING):
        bars = reader.read(3600, OPEN_MS, OPEN_MS + 5 * H1_MS)
    assert [(bar.open_time_ms, bar.c) for bar in bars] == [(OPEN_MS, 9.0), (OPEN_MS + 4 * H1_MS, 1.5)]
    assert bars[1].low == 0.25 and bars[0].close_time_ms == OPEN_MS + H1_MS
    assert reader.rejected_rows == 1 and "SEASON_PLAN_ROW_REJECTED" in caplog.text
    assert [bar.open_time_ms for bar in reader.newest(3600, 1)] == [OPEN_MS + 4 * H1_MS]


def test_source_end_is_one_step_past_the_tail_and_never_past_the_window():
    def ctx(window, m1_tail=None, h1_tail=None):
        return sp.SymbolContext(symbol="XAU/USD", sym_dir="XAU_USD", rule=RULE_NY_CLOSE_US_DST, calendar=None,
                                window=window, m1_head_ms=None, m1_tail_ms=m1_tail, h1_tail_ms=h1_tail)

    assert ctx(sp.ALL_TIME, m1_tail=OPEN_MS).source_end_ms == OPEN_MS + 60_000
    assert ctx((0, OPEN_MS - 1), m1_tail=OPEN_MS).source_end_ms == OPEN_MS - 1
    assert ctx(sp.ALL_TIME, h1_tail=OPEN_MS).source_end_ms == OPEN_MS + H1_MS
    assert ctx((OPEN_MS, OPEN_MS + H1_MS)).source_end_ms == OPEN_MS, "без джерела не фіналізується нічого"
    assert ctx(sp.ALL_TIME).bucket_of(OPEN_MS + 5, 14400) == 1_773_136_800_000  # сітка 22/02/06/10 (18:00 NY)
