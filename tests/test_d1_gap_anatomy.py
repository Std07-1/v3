"""tools/diag/d1_gap_anatomy — класифікація анатомії пропусків D1 (ADR-0092 P1).

Інструмент калібрує пороги політики повноти D1, тому його класи мусять розділяти саме
ті випадки, які сьогодні однаково дають «бару немає»: край сесії (рання сесія або DST),
обрив фіду всередині, розсип неліквідних хвилин, повне закриття — і окремо безкоштовні
граничні хвилини, яких у проді найбільше (одна хвилина 22:00 майже щодня). Кожен клас
має тест і контроль.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from runtime.ingest.tick_common import calendar_from_group
from tools.diag.d1_gap_anatomy import analyze_bucket, main

# Той самий календар, що в config.json для XAU/XAG/NAS100/SPX500/US30.
CFD_US_22_23 = {
    "market_weekend_open_dow": 6,
    "market_weekend_open_hm": "22:00",
    "market_weekend_close_dow": 4,
    "market_weekend_close_hm": "20:45",
    "market_daily_break_start_hm": "21:00",
    "market_daily_break_end_hm": "22:00",
}
# Група з двома обідніми перервами: внутрішні межі сесії теж безкоштовні для derive.
CFD_HK_MAIN = {
    "market_weekend_open_dow": 6,
    "market_weekend_open_hm": "01:15",
    "market_weekend_close_dow": 4,
    "market_weekend_close_hm": "19:00",
    "market_daily_break_start_hm": "19:00",
    "market_daily_break_end_hm": "01:15",
    "market_daily_breaks": [["04:00", "05:00"], ["08:30", "09:15"]],
}
D1_MS = 86_400_000
M1_MS = 60_000


def _slots(group: dict, session_date: str, anchor_hm: int = 21) -> list:
    """Торгові хвилини D1-бакета, що закривається anchor_hm:00 UTC у вказану добу."""
    cal = calendar_from_group(group)
    end = dt.datetime.strptime(session_date, "%Y-%m-%d").replace(
        hour=anchor_hm, tzinfo=dt.timezone.utc
    )
    b0 = int(end.timestamp()) * 1000 - D1_MS
    return [t for t in range(b0, b0 + D1_MS, M1_MS) if cal.is_trading_minute(t)]


def _cal(group: dict):
    return calendar_from_group(group).is_trading_minute


def test_clean_bucket_is_clean():
    slots = _slots(CFD_US_22_23, "2026-06-24")
    assert len(slots) == 1380, "контроль: у звичайній добі 1380 торгових хвилин"
    r = analyze_bucket(slots, set(slots), is_trading_fn=_cal(CFD_US_22_23))
    assert r["class"] == "clean"
    assert r["missing"] == 0 and r["coverage_pct"] == 100.0


def test_only_session_open_minute_missing_is_boundary_only():
    """Найчастіший випадок у проді: бракує однієї хвилини 22:00. Для derive безкоштовно."""
    slots = _slots(CFD_US_22_23, "2026-06-24")
    r = analyze_bucket(slots, set(slots[1:]), is_trading_fn=_cal(CFD_US_22_23))
    assert r["class"] == "boundary_only"
    assert r["missing"] == 1 and r["mid_session_missing"] == 0


def test_only_session_close_minute_missing_is_boundary_only():
    slots = _slots(CFD_US_22_23, "2026-06-24")
    r = analyze_bucket(slots, set(slots[:-1]), is_trading_fn=_cal(CFD_US_22_23))
    assert r["class"] == "boundary_only" and r["mid_session_missing"] == 0


def test_internal_lunch_break_edges_are_boundary_not_mid():
    """HKG33: хвилини біля обідніх перерв — теж межі; за індексом їх видно не було."""
    slots = _slots(CFD_HK_MAIN, "2026-06-24", anchor_hm=19)
    fn = _cal(CFD_HK_MAIN)
    edges = [i for i, t in enumerate(slots) if not fn(t - M1_MS) or not fn(t + M1_MS)]
    assert len(edges) > 2, "контроль: у HK більше двох граничних хвилин (обіди)"
    r = analyze_bucket(slots, set(slots) - {slots[i] for i in edges}, is_trading_fn=fn)
    assert r["class"] == "boundary_only" and r["mid_session_missing"] == 0


def test_long_tail_at_session_close_is_edge_close():
    """Рання сесія свята: індекси 07.09 — хвіст ~222 хв до кінця доби."""
    slots = _slots(CFD_US_22_23, "2026-09-07")
    r = analyze_bucket(slots, set(slots[:-222]), is_trading_fn=_cal(CFD_US_22_23))
    assert r["class"] == "edge_close"
    assert r["max_run"] == 222 and r["missing_at_session_close"] is True


def test_long_run_at_session_open_is_edge_open():
    """DST-зсув на відкритті: перші 59 хвилин бакета відсутні."""
    slots = _slots(CFD_US_22_23, "2026-06-24")
    r = analyze_bucket(slots, set(slots[59:]), is_trading_fn=_cal(CFD_US_22_23))
    assert r["class"] == "edge_open"
    assert r["mid_max_run"] >= 45 and r["missing_at_session_open"] is True


def test_long_run_in_mid_session_is_feed_gap():
    """Обрив фіду: 120 хв усередині, обидва краї на місці."""
    slots = _slots(CFD_US_22_23, "2026-06-24")
    present = set(slots[:600]) | set(slots[720:])
    r = analyze_bucket(slots, present, is_trading_fn=_cal(CFD_US_22_23))
    assert r["class"] == "feed_gap"
    assert r["mid_max_run"] == 120
    assert r["missing_at_session_open"] is False and r["missing_at_session_close"] is False


def test_scattered_short_runs_are_thin_scatter():
    """Метали у святковий вечір: розсип по 1-2 хвилини, довгих прогонів немає."""
    slots = _slots(CFD_US_22_23, "2026-09-07")
    drop = {slots[i] for i in range(100, 1300, 29)} | {slots[i + 1] for i in range(100, 1300, 97)}
    r = analyze_bucket(slots, set(slots) - drop, is_trading_fn=_cal(CFD_US_22_23))
    assert r["class"] == "thin_scatter"
    assert r["mid_max_run"] <= 3 and r["scatter"] == r["mid_session_missing"]


def test_almost_everything_missing_is_full_closure():
    """Good Friday: торгів майже не було."""
    slots = _slots(CFD_US_22_23, "2026-04-03")
    r = analyze_bucket(slots, set(slots[:12]), is_trading_fn=_cal(CFD_US_22_23))
    assert r["class"] == "full_closure"


def test_scatter_by_run_max_is_the_calibration_output():
    """Той самий бакет при різних порогах дає різний розсип — це і калібрують."""
    slots = _slots(CFD_US_22_23, "2026-06-24")
    drop = {slots[i] for i in range(10, 400, 17)} | {slots[i + 1] for i in range(10, 400, 17)}
    r = analyze_bucket(slots, set(slots) - drop, is_trading_fn=_cal(CFD_US_22_23))
    sb = r["scatter_by_run_max"]
    assert sb["1"] == 0, "прогони по 2 хвилини не потрапляють у поріг 1"
    assert sb["2"] == r["mid_session_missing"] and sb["10"] == r["mid_session_missing"]
    assert analyze_bucket(slots, set(slots) - drop, scatter_run_max=1,
                          is_trading_fn=_cal(CFD_US_22_23))["scatter"] == 0


def _utc_ms(y, mo, d, h=0, mi=0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp()) * 1000


def _write_opens(root: Path, tf_s: int, opens) -> None:
    by_day: dict = {}
    for open_ms in opens:
        day = dt.datetime.fromtimestamp(open_ms / 1000, dt.timezone.utc).strftime("%Y%m%d")
        by_day.setdefault(day, []).append(json.dumps({"open_time_ms": open_ms, "tf_s": tf_s}))
    tf_dir = root / "XAU_USD" / ("tf_%d" % tf_s)
    tf_dir.mkdir(parents=True, exist_ok=True)
    for day, lines in by_day.items():
        (tf_dir / ("part-%s.jsonl" % day)).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_d1_gap_anatomy_main_seasonal_across_2026_11_01(tmp_path, monkeypatch):
    """--date — торгова доба на сезонній сітці (ADR-0095 S5b): пт 30.10 — бакет чт 21:00, пн 02.11 — нд 22:00.

    Легасі-ключ `day_anchor_offset_s_d1` = 75600 (літо) у config лишається до S5c, але інструмент його не
    читає: з ним бакет понеділка став би нд 21:00, D1 на диску (нд 22:00) — «НЕМА», хибна cascade_hole.
    """
    mon_open, mon_close = _utc_ms(2026, 11, 1, 22), _utc_ms(2026, 11, 2, 21)
    feed_gap = range(_utc_ms(2026, 11, 2, 10), _utc_ms(2026, 11, 2, 11), M1_MS)
    _write_opens(tmp_path, 60, [t for t in range(mon_open, mon_close, M1_MS) if t not in feed_gap])
    _write_opens(tmp_path, 86400, [_utc_ms(2026, 10, 29, 21), mon_open])
    cfg = {
        "data_root": str(tmp_path),
        "day_anchor_offset_s_d1": 75600,
        "market_calendar_symbol_groups": {"XAU/USD": "cfd_us_22_23"},
        "market_calendar_by_group": {"cfd_us_22_23": CFD_US_22_23},
        "htf_anchor": {"rule_by_calendar_group": {"cfd_us_22_23": "ny_close_us_dst"}},
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    report = tmp_path / "anatomy.json"
    monkeypatch.setattr(sys, "argv", [
        "d1_gap_anatomy", "--config", str(tmp_path / "config.json"), "--symbol", "XAU/USD",
        "--date", "2026-10-30", "--date", "2026-11-02", "--json", str(report), "--show-boundary",
    ])
    main()
    rows = {r["bucket_open_ms"]: r for r in json.loads(report.read_text(encoding="utf-8"))["rows"]}
    assert sorted(rows) == [_utc_ms(2026, 10, 29, 21), mon_open]
    friday, monday = rows[_utc_ms(2026, 10, 29, 21)], rows[mon_open]
    assert (friday["session_date"], friday["d1_present"]) == ("2026-10-30 Fri", True)
    assert (monday["session_date"], monday["d1_present"], monday["class"]) == ("2026-11-02 Mon", True, "feed_gap")
    assert (monday["expected"], monday["missing"]) == (1380, 60)
