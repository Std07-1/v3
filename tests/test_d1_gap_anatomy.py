"""tools/diag/d1_gap_anatomy — класифікація анатомії пропусків D1 (ADR-0092 P1).

Інструмент калібрує пороги політики повноти D1, тому його класи мусять розділяти
саме ті чотири випадки, які сьогодні однаково дають «бару немає»: край сесії
(рання сесія або DST), обрив фіду всередині, розсип неліквідних хвилин і повне
закриття. Кожен тест — один клас, і до кожного є контроль.
"""
from __future__ import annotations

import datetime as dt

from runtime.ingest.tick_common import calendar_from_group
from tools.diag.d1_gap_anatomy import analyze_bucket

# Той самий календар, що в config.json для XAU/XAG/NAS100/SPX500/US30.
CFD_US_22_23 = {
    "market_weekend_open_dow": 6,
    "market_weekend_open_hm": "22:00",
    "market_weekend_close_dow": 4,
    "market_weekend_close_hm": "20:45",
    "market_daily_break_start_hm": "21:00",
    "market_daily_break_end_hm": "22:00",
}
D1_MS = 86_400_000
M1_MS = 60_000


def _bucket_slots(session_date: str) -> list:
    """Торгові хвилини D1-бакета, що закривається 21:00 UTC у вказану добу."""
    cal = calendar_from_group(CFD_US_22_23)
    end = dt.datetime.strptime(session_date, "%Y-%m-%d").replace(hour=21, tzinfo=dt.timezone.utc)
    b0 = int(end.timestamp()) * 1000 - D1_MS
    return [t for t in range(b0, b0 + D1_MS, M1_MS) if cal.is_trading_minute(t)]


def test_clean_bucket_is_clean():
    slots = _bucket_slots("2026-06-24")
    assert len(slots) == 1380, "контроль: у звичайній добі 1380 торгових хвилин"
    r = analyze_bucket(slots, set(slots))
    assert r["class"] == "clean"
    assert r["missing"] == 0 and r["coverage_pct"] == 100.0


def test_long_tail_at_session_close_is_edge_close():
    """Рання сесія свята: індекси 07.09 — хвіст ~222 хв до кінця доби."""
    slots = _bucket_slots("2026-09-07")
    present = set(slots[:-222])
    r = analyze_bucket(slots, present)
    assert r["class"] == "edge_close"
    assert r["max_run"] == 222 and r["missing_at_session_close"] is True


def test_long_run_at_session_open_is_edge_open():
    """DST-зсув на відкритті: перші 59 хвилин бакета відсутні."""
    slots = _bucket_slots("2026-06-24")
    r = analyze_bucket(slots, set(slots[59:]))
    assert r["class"] == "edge_open"
    assert r["max_run"] == 59 and r["missing_at_session_open"] is True


def test_long_run_in_mid_session_is_feed_gap():
    """Обрив фіду: 120 хв усередині, обидва краї на місці."""
    slots = _bucket_slots("2026-06-24")
    present = set(slots[:600]) | set(slots[720:])
    r = analyze_bucket(slots, present)
    assert r["class"] == "feed_gap"
    assert r["max_run"] == 120
    assert r["missing_at_session_open"] is False and r["missing_at_session_close"] is False


def test_scattered_short_runs_are_thin_scatter():
    """Метали у святковий вечір: розсип по 1-2 хвилини, довгих прогонів немає."""
    slots = _bucket_slots("2026-09-07")
    drop = {slots[i] for i in range(100, 1300, 29)} | {slots[i + 1] for i in range(100, 1300, 97)}
    r = analyze_bucket(slots, set(slots) - drop)
    assert r["class"] == "thin_scatter"
    assert r["max_run"] <= 3 and r["scatter"] == r["missing"]


def test_almost_everything_missing_is_full_closure():
    """Good Friday: торгів майже не було."""
    slots = _bucket_slots("2026-04-03")
    r = analyze_bucket(slots, set(slots[:12]))
    assert r["class"] == "full_closure"


def test_mid_session_count_excludes_free_boundary_minutes():
    """Гранична хвилина для derive безкоштовна — mid не має її рахувати."""
    slots = _bucket_slots("2026-06-24")
    r = analyze_bucket(slots, set(slots[1:]))  # відсутня лише перша хвилина
    assert r["missing"] == 1 and r["missing_at_session_open"] is True
    r2 = analyze_bucket(slots, set(slots[:-1]))  # відсутня лише остання
    assert r2["missing"] == 1 and r2["missing_at_session_close"] is True


def test_scatter_run_max_is_a_parameter_not_a_constant():
    """Той самий бакет при іншому порозі розсипу дає інший scatter — це і калібрують."""
    slots = _bucket_slots("2026-06-24")
    drop = {slots[i] for i in range(10, 400, 17)} | {slots[i] for i in range(11, 400, 17)}
    present = set(slots) - drop
    narrow = analyze_bucket(slots, present, scatter_run_max=1)
    wide = analyze_bucket(slots, present, scatter_run_max=3)
    assert wide["scatter"] > narrow["scatter"]
