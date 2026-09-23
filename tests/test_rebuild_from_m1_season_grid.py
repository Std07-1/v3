"""tools/rebuild_from_m1 на сезонній сітці (ADR-0095 S5b): прогін через вихідні DST 01.11.2026.

Раніше інструмент брав один якір з config на весь прогін і крокував `range(b0, end, tf_ms)`: літній H4 п'ятниці
і зимовий H4 понеділка не могли вийти правильними обидва, а фіксований крок 4 год робив з обрубка доби переходу
(нд 21:00, 1 год) бар на 4 год, що вбирав години наступної доби. Тепер правило — з резолвера на символ, бакети —
ітератором сітки, вікно — до наступного бакета.
"""
from __future__ import annotations

import datetime as dt
import json
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
    """M1 XAU/USD кожну хвилину [first, last]; повертає open кожної хвилини."""
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
        (tf_dir / ("part-%s.jsonl" % day)).write_text("\n".join(lines) + "\n", encoding="utf-8")
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
