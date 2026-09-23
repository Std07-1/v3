"""tools/rebuild_from_m1 на сезонній сітці (ADR-0095 S5b): прогін через вихідні DST 01.11.2026.

Раніше інструмент брав один якір з config на весь прогін і крокував `range(b0, end, tf_ms)`: літній H4 п'ятниці
і зимовий H4 понеділка не могли вийти правильними обидва, а фіксований крок 4 год робив з обрубка доби переходу
(нд 21:00, 1 год) бар на 4 год, що вбирав години наступної доби. Тепер правило — з резолвера на символ, бакети —
ітератором сітки, вікно — до наступного бакета. Початок прогону вирівнюється на відкриття торгової доби, а джерело
вантажиться порціями по бакетах D1.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List

import pytest

from core.config_loader import htf_anchor_rule_resolver
from core.session_anchor import D1_S, H4_S, RULE_NY_CLOSE_US_DST, htf_bucket_start_ms
from runtime.store.ssot_jsonl import JsonlAppender
from tools import rebuild_from_m1

UTC = dt.timezone.utc
M1_MS = 60_000

# cfd_us_22_23 з config.json: вихідні Пт 20:45 → Нд 22:00, перерва 21:00–22:00
CFG = {
    "market_calendar_symbol_groups": {"XAU/USD": "cfd_us_22_23", "HKG33": "cfd_hk_main"},
    "market_calendar_by_group": {
        "cfd_us_22_23": {
            "market_weekend_close_dow": 4, "market_weekend_close_hm": "20:45",
            "market_weekend_open_dow": 6, "market_weekend_open_hm": "22:00",
            "market_daily_break_start_hm": "21:00", "market_daily_break_end_hm": "22:00",
        },
    },
    "htf_anchor": {"rule_by_calendar_group": {"cfd_us_22_23": RULE_NY_CLOSE_US_DST}},
}


def _ms(y, mo, d, h=0, mi=0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp() * 1000)


def _write_m1(root: Path, first_ms: int, last_ms: int) -> Dict[int, float]:
    """M1 XAU/USD кожну хвилину [first, last] (дописує в part-файли); повертає open кожної хвилини."""
    opens: Dict[int, float] = {}
    by_day: Dict[str, List[str]] = {}
    for k, open_ms in enumerate(range(first_ms, last_ms + M1_MS, M1_MS)):
        price = 4000.0 + k * 0.01
        opens[open_ms] = price
        bar = {"symbol": "XAU/USD", "tf_s": 60, "open_time_ms": open_ms, "close_time_ms": open_ms + M1_MS,
               "o": price, "h": price + 0.5, "low": price - 0.5, "c": price + 0.1, "v": 1.0, "complete": True,
               "src": "history"}
        day = dt.datetime.fromtimestamp(open_ms / 1000, UTC).strftime("%Y%m%d")
        by_day.setdefault(day, []).append(json.dumps(bar))
    tf_dir = root / "XAU_USD" / "tf_60"
    tf_dir.mkdir(parents=True, exist_ok=True)
    for day, lines in by_day.items():
        with open(tf_dir / ("part-%s.jsonl" % day), "a", encoding="utf-8") as part:
            part.write("\n".join(lines) + "\n")
    return opens


def _disk_bars(root: Path, tf_s: int) -> Dict[int, dict]:
    bars: Dict[int, dict] = {}
    for path in sorted((root / "XAU_USD" / ("tf_%d" % tf_s)).glob("part-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                bar = json.loads(line)
                bars[bar["open_time_ms"]] = bar
    return bars


def test_rebuild_across_dst_weekend_2026_11_01_keeps_h4_d1_on_season_grid(tmp_path):
    """Пт 30.10 — літня сітка (H4 17:00), нд 01.11 з 22:00 — зимова (22:00, 02:00); H4 нд 21:00 немає."""
    fri_opens = _write_m1(tmp_path, _ms(2026, 10, 30, 17), _ms(2026, 10, 30, 20, 44))
    sun_opens = _write_m1(tmp_path, _ms(2026, 11, 1, 22), _ms(2026, 11, 2, 5, 59))
    writer = JsonlAppender(root=str(tmp_path), anchor_rule_for_symbol=htf_anchor_rule_resolver(CFG))
    try:
        stats = rebuild_from_m1.rebuild_one_symbol(
            data_root=str(tmp_path), symbol="XAU/USD", start_ms=_ms(2026, 10, 30, 17), end_ms=_ms(2026, 11, 2, 6),
            dry_run=False, cfg=CFG, writer=writer, anchor_rule=RULE_NY_CLOSE_US_DST,
        )
    finally:
        writer.close()

    h4 = _disk_bars(tmp_path, H4_S)
    assert sorted(h4) == [_ms(2026, 10, 30, 17), _ms(2026, 11, 1, 22), _ms(2026, 11, 2, 2)]
    assert _ms(2026, 11, 1, 21) not in h4, "обрубок доби переходу не вбирає годин наступної доби"
    first_winter = h4[_ms(2026, 11, 1, 22)]
    assert first_winter["o"] == sun_opens[_ms(2026, 11, 1, 22)]
    assert first_winter["c"] == pytest.approx(sun_opens[_ms(2026, 11, 2, 1, 59)] + 0.1)
    assert h4[_ms(2026, 10, 30, 17)]["o"] == fri_opens[_ms(2026, 10, 30, 17)]

    d1 = _disk_bars(tmp_path, D1_S)
    for tf_s, bars in ((H4_S, h4), (D1_S, d1)):
        assert all(htf_bucket_start_ms(o, tf_s, RULE_NY_CLOSE_US_DST) == o for o in bars)
    assert _ms(2026, 11, 1, 21) not in d1 and _ms(2026, 11, 1, 22) not in d1  # доба понеділка ще не закрита
    assert stats["tf_14400_written"] == 3


def test_rebuild_main_refuses_symbol_without_measured_grid_before_any_write(tmp_path, monkeypatch):
    """Символ невиміряної групи (HKG33) відмовляє весь прогін до запису, а не посеред нього після M3..H1."""
    _write_m1(tmp_path, _ms(2026, 10, 30, 17), _ms(2026, 10, 30, 17, 59))
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(dict(CFG, data_root=str(tmp_path), symbols=["XAU/USD", "HKG33"])),
                           encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["rebuild_from_m1", "--config", str(config_path), "--writers-stopped"])
    with pytest.raises(SystemExit) as caught:
        rebuild_from_m1.main()
    assert caught.value.code == 2
    assert sorted(p.name for p in (tmp_path / "XAU_USD").iterdir()) == ["tf_60"], "XAU/USD не перебудовано"


def test_rebuild_force_from_round_date_aligns_start_to_trading_day_open(tmp_path, monkeypatch, caplog):
    """`--start 2026-05-15 --force`: торгова доба пт 15.05 відкрилась чт 14.05 21:00 UTC — прогін вирівнюється на неї.

    Раніше джерело читалося від 00:00, тож H4/D1 14.05 21:00 будувались partial (H4 з 1 H1 із 3, D1 без перших двох
    годин), а dedup `--force` не заходив у part-20260514: там лишалися старий цілий бар і новий partial.
    """
    _write_m1(tmp_path, _ms(2026, 5, 13, 22), _ms(2026, 5, 14, 20, 59))
    _write_m1(tmp_path, _ms(2026, 5, 14, 22), _ms(2026, 5, 15, 20, 44))
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(dict(CFG, data_root=str(tmp_path), symbols=["XAU/USD"])), encoding="utf-8")

    def run(*args: str) -> None:
        monkeypatch.setattr(sys, "argv", ["rebuild_from_m1", "--config", str(config_path), "--symbol", "XAU/USD",
                                          "--writers-stopped", *args])
        rebuild_from_m1.main()

    run("--start", "2026-05-13T21:00:00Z", "--end", "2026-05-16T00:00:00Z")
    with caplog.at_level(logging.INFO):
        run("--start", "2026-05-15", "--end", "2026-05-16", "--force")

    for tf_s in (H4_S, D1_S):
        part = tmp_path / "XAU_USD" / ("tf_%d" % tf_s) / "part-20260514.jsonl"
        bars = [json.loads(line) for line in part.read_text(encoding="utf-8").splitlines() if line.strip()]
        opens = [bar["open_time_ms"] for bar in bars]
        assert len(opens) == len(set(opens)), "tf_%d: дублікат ключа у part-файлі попереднього дня" % tf_s
        first = next(bar for bar in bars if bar["open_time_ms"] == _ms(2026, 5, 14, 21))
        assert not (first.get("extensions") or {}).get("partial"), "tf_%d: перший бакет прогону — з цілої доби" % tf_s
    assert ("REBUILD_RANGE_ALIGNED symbol=XAU/USD requested=2026-05-15T00:00:00+00:00 "
            "aligned=2026-05-14T21:00:00+00:00") in caplog.text


def _write_week_with_thin_tuesday_close(root: Path) -> None:
    """Сесії XAU/USD пн 11.05 – пт 15.05.2026 (літо: доба 21:00 UTC); у вівторка бракує останньої години 20:00–20:59."""
    _write_m1(root, _ms(2026, 5, 10, 22), _ms(2026, 5, 11, 20, 59))
    _write_m1(root, _ms(2026, 5, 11, 22), _ms(2026, 5, 12, 19, 59))
    for day in (12, 13):
        _write_m1(root, _ms(2026, 5, day, 22), _ms(2026, 5, day + 1, 20, 59))
    _write_m1(root, _ms(2026, 5, 14, 22), _ms(2026, 5, 15, 20, 44))


def _rebuild_week(root: Path) -> Dict[str, int]:
    writer = JsonlAppender(root=str(root), anchor_rule_for_symbol=htf_anchor_rule_resolver(CFG))
    try:
        return rebuild_from_m1.rebuild_one_symbol(
            data_root=str(root), symbol="XAU/USD", start_ms=_ms(2026, 5, 10, 21), end_ms=_ms(2026, 5, 16),
            dry_run=False, cfg=CFG, writer=writer, anchor_rule=RULE_NY_CLOSE_US_DST,
        )
    finally:
        writer.close()


def test_rebuild_by_one_day_chunks_matches_single_chunk_including_d1_frontier(tmp_path, monkeypatch):
    """Порції по одній добі дають ті самі бари всіх TF, що й одна порція на весь прогін.

    Раніше джерело всього діапазону йшло в буфер з FIFO-стелею, і на довгому прогоні найстаріші бари витіснялись
    мовчки. Тепер буфер тримає одну порцію, і результат від її розміру не залежить. D1 вівторка без останньої години
    будується лише за фронтиром ADR-0097 (джерело дійшло до кінця доби). У порції з однієї доби цей фронтир доводить
    перший бар за порцією — інакше вівторка б не було.
    """
    built: Dict[int, Dict[int, Dict[int, dict]]] = {}
    for chunk_d1_buckets in (1, 1000):
        root = tmp_path / ("chunk_%d" % chunk_d1_buckets)
        _write_week_with_thin_tuesday_close(root)
        monkeypatch.setattr(rebuild_from_m1, "REBUILD_CHUNK_D1_BUCKETS", chunk_d1_buckets)
        stats = _rebuild_week(root)
        assert stats["tf_86400_written"] == 5
        built[chunk_d1_buckets] = {tf_s: _disk_bars(root, tf_s) for tf_s in (180, 300, 900, 1800, 3600, H4_S, D1_S)}

    assert built[1] == built[1000]
    tuesday = built[1][D1_S][_ms(2026, 5, 11, 21)]
    assert "thin_session" in tuesday["extensions"]["partial_reasons"]


def test_rebuild_chunks_are_consecutive_d1_buckets_covering_the_range(monkeypatch):
    """Порції стикуються без щілин, внутрішні межі — відкриття D1 (через DST-неділю 01.11 теж), остання — до end."""
    monkeypatch.setattr(rebuild_from_m1, "REBUILD_CHUNK_D1_BUCKETS", 2)
    start_ms, end_ms = _ms(2026, 10, 28, 12), _ms(2026, 11, 4, 3)
    chunks = rebuild_from_m1._rebuild_chunks(start_ms, end_ms, RULE_NY_CLOSE_US_DST)
    assert chunks[0][0] == start_ms and chunks[-1][1] == end_ms
    assert all(prev[1] == nxt[0] for prev, nxt in zip(chunks, chunks[1:]))
    # Сітка D1 крокує й через вихідні (сб 31.10 21:00 — бакет без торгівлі), з 01.11 доба відкривається о 22:00
    assert [c[0] for c in chunks[1:]] == [_ms(2026, 10, 29, 21), _ms(2026, 10, 31, 21), _ms(2026, 11, 2, 22)]
