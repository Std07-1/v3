"""tools/rebuild_from_m1 на сезонній сітці (ADR-0095 S5b): прогін через вихідні DST 01.11.2026.

Раніше інструмент брав один якір з config на весь прогін і крокував `range(b0, end, tf_ms)`: літній H4 п'ятниці
і зимовий H4 понеділка не могли вийти правильними обидва, а фіксований крок 4 год робив з обрубка доби переходу
(нд 21:00, 1 год) бар на 4 год, що вбирав години наступної доби. Тепер правило — з резолвера на символ, бакети —
ітератором сітки, вікно — до наступного бакета. Початок прогону вирівнюється на відкриття торгової доби, а джерело
вантажиться порціями по бакетах D1. Бакет, до останньої торгової хвилини якого M1 не дійшло, — формуючий хвіст:
інструмент його не фіналізує.
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

# Сезонна група cfd_us_22_23 з config.json (ADR-0095 §3.5): улітку вихідні Пт 20:45 → Нд 22:00, перерва 21:00–22:00;
# узимку Пт 21:45 → Нд 23:00, перерва 22:00–23:00
_REPO_CALENDAR_GROUP = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8"))[
    "market_calendar_by_group"]["cfd_us_22_23"]
CFG = {
    "market_calendar_symbol_groups": {"XAU/USD": "cfd_us_22_23", "HKG33": "cfd_hk_main"},
    "market_calendar_by_group": {"cfd_us_22_23": _REPO_CALENDAR_GROUP},
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
    """Пт 30.10 — літня сітка (H4 18:00, від відкриття сесії 18:00 NY), нд 01.11 з 23:00 — зимова (23:00, 03:00);
    обрубок сесійної доби H4 нд 22:00–23:00 порожній і не вбирає годин наступної доби.

    Зимою ринок відкривається в неділю о 23:00 — рівно на відкритті першого зимового H4."""
    fri_opens = _write_m1(tmp_path, _ms(2026, 10, 30, 18), _ms(2026, 10, 30, 20, 44))
    sun_opens = _write_m1(tmp_path, _ms(2026, 11, 1, 23), _ms(2026, 11, 2, 6, 59))
    writer = JsonlAppender(root=str(tmp_path), anchor_rule_for_symbol=htf_anchor_rule_resolver(CFG))
    try:
        stats = rebuild_from_m1.rebuild_one_symbol(
            data_root=str(tmp_path), symbol="XAU/USD", start_ms=_ms(2026, 10, 30, 18), end_ms=_ms(2026, 11, 2, 7),
            dry_run=False, cfg=CFG, writer=writer, anchor_rule=RULE_NY_CLOSE_US_DST,
        )
    finally:
        writer.close()

    h4 = _disk_bars(tmp_path, H4_S)
    assert sorted(h4) == [_ms(2026, 10, 30, 18), _ms(2026, 11, 1, 23), _ms(2026, 11, 2, 3)]
    assert _ms(2026, 11, 1, 22) not in h4, "обрубок доби переходу не вбирає годин наступної доби"
    first_winter = h4[_ms(2026, 11, 1, 23)]
    assert first_winter["o"] == sun_opens[_ms(2026, 11, 1, 23)]
    assert not (first_winter.get("extensions") or {}).get("partial"), "перший зимовий H4 — з відкриття ринку 23:00"
    assert first_winter["c"] == pytest.approx(sun_opens[_ms(2026, 11, 2, 2, 59)] + 0.1)
    assert h4[_ms(2026, 10, 30, 18)]["o"] == fri_opens[_ms(2026, 10, 30, 18)]

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
        # D1 — від 17:00 NY (21:00), H4 — від відкриття сесії 18:00 NY: бакет H4 18:00–22:00 має всі торгові хвилини до
        # 21:00 (21:00–22:00 — перерва), тож прогін від 21:00 його не чіпає, а перший H4 прогону — 22:00
        for first_open in ((_ms(2026, 5, 14, 21),) if tf_s == D1_S else (_ms(2026, 5, 14, 18), _ms(2026, 5, 14, 22))):
            first = next(bar for bar in bars if bar["open_time_ms"] == first_open)
            assert not (first.get("extensions") or {}).get("partial"), "tf_%d: бакет з цілої доби" % tf_s
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


def test_rebuild_winter_h4_1800_takes_h1_2100_by_seasonal_calendar(tmp_path):
    """Пн 03.11.2025 (зима): H4 19:00–23:00 (сітка 18:00 EST) має три торгові H1, остання — 21:00 (перерва 22:00–23:00).

    Статичний літній календар інструмента вважав 21:00–21:59 перервою і губив H1 21:00: H4 закривався close 20:59
    без жодного маркера (ADR-0095 S6a, «333 H4» зони B). Тепер розклад сезону хвилини.
    """
    opens = _write_m1(tmp_path, _ms(2025, 11, 3, 19), _ms(2025, 11, 3, 21, 59))
    writer = JsonlAppender(root=str(tmp_path), anchor_rule_for_symbol=htf_anchor_rule_resolver(CFG))
    try:
        rebuild_from_m1.rebuild_one_symbol(
            data_root=str(tmp_path), symbol="XAU/USD", start_ms=_ms(2025, 11, 3, 19), end_ms=_ms(2025, 11, 3, 23),
            dry_run=False, cfg=CFG, writer=writer, anchor_rule=RULE_NY_CLOSE_US_DST,
        )
    finally:
        writer.close()

    h1 = _disk_bars(tmp_path, 3600)
    assert sorted(h1) == [_ms(2025, 11, 3, hour) for hour in (19, 20, 21)]
    h4 = _disk_bars(tmp_path, H4_S)[_ms(2025, 11, 3, 19)]
    assert h4["c"] == pytest.approx(opens[_ms(2025, 11, 3, 21, 59)] + 0.1)
    assert h4["h"] == pytest.approx(opens[_ms(2025, 11, 3, 21, 59)] + 0.5)
    assert not (h4.get("extensions") or {}).get("partial")


def test_rebuild_main_refuses_symbol_without_season_blocks_before_any_write(tmp_path, monkeypatch):
    """Група без `season_rule`/блоків summer і winter — REBUILD_REFUSED rc=2, а не тихий літній розклад узимку."""
    _write_m1(tmp_path, _ms(2026, 10, 30, 17), _ms(2026, 10, 30, 17, 59))
    flat_group = {key: value for key, value in _REPO_CALENDAR_GROUP.items()
                  if key not in ("season_rule", "summer", "winter")}
    cfg = dict(CFG, market_calendar_by_group={"cfd_us_22_23": flat_group}, data_root=str(tmp_path),
               symbols=["XAU/USD"])
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["rebuild_from_m1", "--config", str(config_path), "--writers-stopped"])
    with pytest.raises(SystemExit) as caught:
        rebuild_from_m1.main()
    assert caught.value.code == 2
    assert sorted(p.name for p in (tmp_path / "XAU_USD").iterdir()) == ["tf_60"], "нічого не перебудовано"


_DERIVED_TFS = (180, 300, 900, 1800, 3600, H4_S, D1_S)


def _partial_opens(root: Path) -> Dict[int, List[int]]:
    """Відкриття partial-барів на диску по кожному похідному TF (порожні списки прибрано)."""
    found = {tf_s: sorted(o for o, bar in _disk_bars(root, tf_s).items() if (bar.get("extensions") or {}).get("partial"))
             for tf_s in _DERIVED_TFS}
    return {tf_s: opens for tf_s, opens in found.items() if opens}


@pytest.mark.parametrize(
    "end_args, end_iso, expected_skipped",
    [
        ([], "2026-05-14T10:38:00+00:00", '{"M3": 1, "M5": 1, "M15": 1, "M30": 1, "H1": 1, "H4": 1, "D1": 1}'),
        # `--end` за хвостом M1: бакети між хвостом і кінцем теж не фіналізуються — джерело до них не дійшло
        (["--end", "2026-05-14T12:00:00Z"], "2026-05-14T12:00:00+00:00",
         '{"M3": 28, "M5": 17, "M15": 6, "M30": 3, "H1": 2, "H4": 1, "D1": 1}'),
    ],
    ids=["default_end", "end_past_m1_tail"],
)
def test_rebuild_tail_mid_bucket_leaves_forming_buckets_to_live_derive(
    tmp_path, monkeypatch, caplog, end_args, end_iso, expected_skipped
):
    """Хвіст M1 чт 14.05 10:37 (типовий `--end` = хвіст + 1 хв або `--end` пізніше): бакет кожного TF, що його
    містить, не фіналізується.

    Раніше останній бакет кожного TF ставав partial final (M3 2/3, M5 3/5, M15 2/3, M30 1/2, H4 1/4), а H1 10:00
    збирався з partial M30 10:30 уже без жодного маркера. Append-only: наступний прогін без `--force` бачив ключ як
    наявний і урізаний бар лишався назавжди. Формуючий хвіст — справа живого DeriveEngine.
    """
    _write_m1(tmp_path, _ms(2026, 5, 13, 22), _ms(2026, 5, 14, 10, 37))
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(dict(CFG, data_root=str(tmp_path), symbols=["XAU/USD"])), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["rebuild_from_m1", "--config", str(config_path), "--symbol", "XAU/USD",
                                      "--writers-stopped", "--start", "2026-05-13T21:00:00Z", *end_args])
    with caplog.at_level(logging.INFO):
        rebuild_from_m1.main()

    tail_opens = {180: _ms(2026, 5, 14, 10, 36), 300: _ms(2026, 5, 14, 10, 35), 900: _ms(2026, 5, 14, 10, 30),
                  1800: _ms(2026, 5, 14, 10, 30), 3600: _ms(2026, 5, 14, 10), H4_S: _ms(2026, 5, 14, 10),
                  D1_S: _ms(2026, 5, 13, 21)}
    for tf_s, tail_open in tail_opens.items():
        assert tail_open not in _disk_bars(tmp_path, tf_s), "tf_%d: формуючий бакет записано фіналом" % tf_s
    assert _partial_opens(tmp_path) == {}
    h1_opens = [_ms(2026, 5, 13, 22), _ms(2026, 5, 13, 23)] + [_ms(2026, 5, 14, hour) for hour in range(10)]
    assert sorted(_disk_bars(tmp_path, 3600)) == h1_opens, "H1 10:00 не збирається з partial M30 10:30"
    assert sorted(_disk_bars(tmp_path, H4_S)) == [_ms(2026, 5, 13, 22), _ms(2026, 5, 14, 2), _ms(2026, 5, 14, 6)]
    assert ('REBUILD_TAIL_BUCKETS_SKIPPED symbol=XAU/USD m1_tail=2026-05-14T10:37:00+00:00 '
            'end=%s skipped=%s ' % (end_iso, expected_skipped) +
            'first={"M3": "2026-05-14T10:36:00+00:00", "M5": "2026-05-14T10:35:00+00:00", '
            '"M15": "2026-05-14T10:30:00+00:00", "M30": "2026-05-14T10:30:00+00:00", "H1": "2026-05-14T10:00:00+00:00", '
            '"H4": "2026-05-14T10:00:00+00:00", "D1": "2026-05-13T21:00:00+00:00"}') in caplog.text


def test_rebuild_end_on_closed_d1_boundary_skips_nothing(tmp_path, caplog):
    """`--end` чт 14.05 21:00 = межа D1 сезонної сітки, M1 є й за нею: кожен бакет прогону закритий, хвоста немає."""
    _write_m1(tmp_path, _ms(2026, 5, 13, 22), _ms(2026, 5, 14, 20, 59))
    _write_m1(tmp_path, _ms(2026, 5, 14, 22), _ms(2026, 5, 14, 22, 30))
    writer = JsonlAppender(root=str(tmp_path), anchor_rule_for_symbol=htf_anchor_rule_resolver(CFG))
    try:
        with caplog.at_level(logging.INFO):
            stats = rebuild_from_m1.rebuild_one_symbol(
                data_root=str(tmp_path), symbol="XAU/USD", start_ms=_ms(2026, 5, 13, 21), end_ms=_ms(2026, 5, 14, 21),
                dry_run=False, cfg=CFG, writer=writer, anchor_rule=RULE_NY_CLOSE_US_DST,
            )
    finally:
        writer.close()

    assert {tf_s: stats["tf_%d_tail_skipped" % tf_s] for tf_s in _DERIVED_TFS} == dict.fromkeys(_DERIVED_TFS, 0)
    assert "REBUILD_TAIL_BUCKETS_SKIPPED" not in caplog.text
    assert sorted(_disk_bars(tmp_path, D1_S)) == [_ms(2026, 5, 13, 21)]
    assert _ms(2026, 5, 14, 18) in _disk_bars(tmp_path, H4_S)
    assert _partial_opens(tmp_path) == {}


@pytest.mark.parametrize(
    "sunday_m1, end_ms, expected_first_tail",
    [
        # Прогін у вихідні з типовим `--end` = хвіст пт 20:44 + 1 хв: H4 18:00 і D1 закриті хвилиною 20:44, хоч їхні
        # вікна тривають до 22:00 і 21:00
        (False, _ms(2026, 10, 30, 20, 45), {}),
        # Кінець посеред обрубка H4 нд 22:00 (1 год) 25-годинної сесійної доби: торгових хвилин в обрубку немає, але
        # кінець уже за межею зимової D1 22:00 — її торгові хвилини з 23:00 попереду, тож D1 — хвіст
        (True, _ms(2026, 11, 1, 22, 30), {D1_S: _ms(2026, 11, 1, 22)}),
        (True, _ms(2026, 11, 1, 22), {}),  # межа D1 зимової сітки
        # Хвіст M1 нд 23:29 після зимового відкриття 23:00: формуються H1 і H4 23:00, D1 22:00 зимової сітки
        (True, _ms(2026, 11, 1, 23, 30), {3600: _ms(2026, 11, 1, 23), H4_S: _ms(2026, 11, 1, 23),
                                          D1_S: _ms(2026, 11, 1, 22)}),
    ],
    ids=["weekend_default_end", "end_in_stub", "end_on_winter_d1", "tail_after_winter_open"],
)
def test_rebuild_tail_across_dst_sunday_2026_11_01_stub_bucket_is_not_forming(
    tmp_path, sunday_m1, end_ms, expected_first_tail
):
    """Хвіст M1 на закритті пт 30.10 20:44 або після зимового відкриття нд 01.11 23:29. Обрубок H4 нд 22:00–23:00 —
    вихідні: за годинником його вікно триває після кінця джерела, та торгової хвилини там немає, тож це не хвіст.
    Хвостом стає лише бакет зимової сітки, у якому ринок уже торгує."""
    _write_m1(tmp_path, _ms(2026, 10, 29, 22), _ms(2026, 10, 30, 20, 44))
    if sunday_m1:
        _write_m1(tmp_path, _ms(2026, 11, 1, 23), _ms(2026, 11, 1, 23, 29))
    writer = JsonlAppender(root=str(tmp_path), anchor_rule_for_symbol=htf_anchor_rule_resolver(CFG))
    try:
        stats = rebuild_from_m1.rebuild_one_symbol(
            data_root=str(tmp_path), symbol="XAU/USD", start_ms=_ms(2026, 10, 29, 21), end_ms=end_ms,
            dry_run=False, cfg=CFG, writer=writer, anchor_rule=RULE_NY_CLOSE_US_DST,
        )
    finally:
        writer.close()

    skipped = {tf_s: stats["tf_%d_tail_skipped" % tf_s] for tf_s in _DERIVED_TFS if stats["tf_%d_tail_skipped" % tf_s]}
    assert skipped == dict.fromkeys(expected_first_tail, 1)
    h4 = _disk_bars(tmp_path, H4_S)
    assert _ms(2026, 10, 30, 18) in h4 and _ms(2026, 11, 1, 22) not in h4
    for tf_s, tail_open in expected_first_tail.items():
        assert tail_open not in _disk_bars(tmp_path, tf_s)
    assert _partial_opens(tmp_path) == {}
    assert sorted(_disk_bars(tmp_path, D1_S)) == [_ms(2026, 10, 29, 21)]
