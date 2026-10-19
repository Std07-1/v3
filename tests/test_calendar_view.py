"""ADR-0115 S2: проєкція W1/MN з D1 через UDS — вікно, scrollback, формуюча, оновлення, таймер, config."""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

import pytest

from core.calendar_tf import MN_S, W1_S
from runtime.store.uds import UpdatesResult, WindowResult
from runtime.ws.calendar_view import CalendarTfView, CalendarViewConfig, calendar_view_config_from_cfg

SYM = "XAU/USD"


def _ms(y: int, m: int, d: int, h: int = 0) -> int:
    return int(dt.datetime(y, m, d, h, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _weekdays(start: dt.date, end: dt.date) -> List[dt.date]:
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def _d1_rows(start: dt.date, end: dt.date) -> List[Dict[str, Any]]:
    """D1 торгових дат пн–пт (серпень–жовтень 2026, літній час: відкриття напередодні 21:00 UTC)."""
    rows, prev_c = [], 100.0
    for i, day in enumerate(_weekdays(start, end)):
        c = 100.0 + (i % 7) * 1.5 + i * 0.1
        prev_day = day - dt.timedelta(days=1)
        rows.append({
            "open_time_ms": _ms(prev_day.year, prev_day.month, prev_day.day, 21), "trading_date": day,
            "open": prev_c, "high": max(prev_c, c) + 2.0, "low": min(prev_c, c) - 2.0, "close": c,
            "volume": 10.0 + i, "complete": True, "tf_s": 86400,
        })
        prev_c = c
    return rows


def _expected(rows: List[Dict[str, Any]], dates: List[dt.date]) -> tuple:
    sel = [r for r in rows if r["trading_date"] in dates]
    return (sel[0]["open"], max(r["high"] for r in sel), min(r["low"] for r in sel), sel[-1]["close"],
            sum(r["volume"] for r in sel))


class _FakeUds:
    def __init__(self, rows, preview=None, events=None, cursor=0):
        self.rows = sorted(rows, key=lambda r: r["open_time_ms"])
        self.preview = preview or []
        self.events = events or []
        self.cursor = cursor
        self.window_specs: List[Any] = []

    def read_window(self, spec, policy):
        assert spec.tf_s == 86400 and policy.force_disk
        self.window_specs.append(spec)
        sel = [r for r in self.rows
               if (spec.since_open_ms is None or r["open_time_ms"] > spec.since_open_ms)
               and (spec.to_open_ms is None or r["open_time_ms"] <= spec.to_open_ms)]
        return WindowResult([dict(r) for r in sel[-spec.limit:]], {"source": "disk_range"}, [])

    def read_preview_window(self, symbol, tf_s, limit):
        assert tf_s == 86400
        return WindowResult([dict(b) for b in self.preview[-limit:]], {}, [])

    def read_updates(self, spec):
        assert spec.tf_s == 86400 and spec.include_preview
        return UpdatesResult(list(self.events), self.cursor, None, None, None, 0, {}, [])


def _cfg(enabled: bool = True) -> CalendarViewConfig:
    return CalendarViewConfig(enabled, frozenset({W1_S, MN_S}), {W1_S: 520, MN_S: 240}, {W1_S: 260, MN_S: 120})


def _view(uds, now_ms: int, enabled: bool = True) -> CalendarTfView:
    return CalendarTfView(uds, lambda _s: "ny_close_us_dst", _cfg(enabled), clock_ms=lambda: now_ms)


def _ohlcv(bar: Dict[str, Any]) -> tuple:
    return (bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"])


ROWS = _d1_rows(dt.date(2026, 8, 3), dt.date(2026, 10, 16))
WEEK_OCT12 = [dt.date(2026, 10, d) for d in range(12, 17)]
WEEK_OCT5 = [dt.date(2026, 10, d) for d in range(5, 10)]


def test_cold_window_w1_finals_match_hand_aggregation():
    view = _view(_FakeUds(ROWS), now_ms=_ms(2026, 10, 19, 12))
    result = view.read_window(SYM, W1_S, 2)
    assert [b["open_time_ms"] for b in result.bars_lwc] == [_ms(2026, 10, 4, 21), _ms(2026, 10, 11, 21)]
    assert all(b["complete"] for b in result.bars_lwc)
    assert _ohlcv(result.bars_lwc[0]) == pytest.approx(_expected(ROWS, WEEK_OCT5))
    assert _ohlcv(result.bars_lwc[1]) == pytest.approx(_expected(ROWS, WEEK_OCT12))
    assert result.bars_lwc[1]["close_time_ms"] == _ms(2026, 10, 18, 21)


def test_current_week_is_not_final_and_comes_as_forming_with_preview_day():
    rows = [r for r in ROWS if r["trading_date"] <= dt.date(2026, 10, 13)]  # пн, вт фінальні
    wed_preview = {"open_time_ms": _ms(2026, 10, 13, 21), "open": rows[-1]["close"], "high": 999.0,
                   "low": 50.0, "close": 120.0, "volume": 3.0, "complete": False}
    uds = _FakeUds(rows, preview=[wed_preview])
    view = _view(uds, now_ms=_ms(2026, 10, 14, 15))
    finals = view.read_window(SYM, W1_S, 3).bars_lwc
    assert finals[-1]["open_time_ms"] == _ms(2026, 10, 4, 21)
    forming = view.read_forming(SYM, W1_S, _ms(2026, 10, 14, 15))
    assert forming is not None and forming["complete"] is False
    assert forming["open_time_ms"] == _ms(2026, 10, 11, 21)
    assert (forming["high"], forming["low"], forming["close"]) == (999.0, 50.0, 120.0)


def test_scrollback_is_inclusive_and_reads_d1_before_next_bucket():
    uds = _FakeUds(ROWS)
    view = _view(uds, now_ms=_ms(2026, 10, 19, 12))
    to_key = _ms(2026, 9, 13, 21)  # тиждень 14.09
    result = view.read_window(SYM, W1_S, 2, to_open_ms=to_key)
    assert [b["open_time_ms"] for b in result.bars_lwc] == [_ms(2026, 9, 6, 21), to_key]
    assert uds.window_specs[-1].to_open_ms == _ms(2026, 9, 20, 21) - 1


def test_first_bucket_cut_by_d1_window_is_dropped():
    view = _view(_FakeUds(ROWS), now_ms=_ms(2026, 10, 19, 12))
    result = view.read_window(SYM, W1_S, 1)  # вікно D1 = 12 барів: пт 02.10 + 2 повні тижні — обрізаний відкидається
    assert [b["open_time_ms"] for b in result.bars_lwc] == [_ms(2026, 10, 11, 21)]


def test_history_start_keeps_first_bucket():
    view = _view(_FakeUds(ROWS), now_ms=_ms(2026, 10, 19, 12))
    result = view.read_window(SYM, MN_S, 10)  # історія коротша за вікно — перший місяць (серпень) лишається
    assert [b["open_time_ms"] for b in result.bars_lwc] == [_ms(2026, 7, 31, 21), _ms(2026, 8, 31, 21)]
    august = _weekdays(dt.date(2026, 8, 3), dt.date(2026, 8, 31))
    assert _ohlcv(result.bars_lwc[0]) == pytest.approx(_expected(ROWS, august))


def test_month_with_unfinished_last_day_is_forming_not_final():
    rows = [r for r in ROWS if r["trading_date"] <= dt.date(2026, 9, 29)]
    sep30_preview = {"open_time_ms": _ms(2026, 9, 29, 21), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
                     "volume": 1.0, "complete": False}
    view = _view(_FakeUds(rows, preview=[sep30_preview]), now_ms=_ms(2026, 9, 30, 21) + 60_000)
    finals = view.read_window(SYM, MN_S, 5).bars_lwc
    assert finals[-1]["open_time_ms"] == _ms(2026, 7, 31, 21)  # вересень — ще не фінал
    forming = view.read_forming(SYM, MN_S, _ms(2026, 9, 30, 21) + 60_000)
    assert forming is not None and forming["open_time_ms"] == _ms(2026, 8, 31, 21) and forming["close"] == 1.5


def test_updates_recompute_touched_bucket_and_keep_d1_cursor():
    rows = [r for r in ROWS if r["trading_date"] <= dt.date(2026, 10, 13)]
    preview_event = {"bar": {"open_time_ms": _ms(2026, 10, 13, 21), "o": rows[-1]["close"], "h": 500.0,
                             "low": 90.0, "c": 130.0, "v": 2.0, "complete": False}, "complete": False}
    view = _view(_FakeUds(rows, events=[preview_event], cursor=42), now_ms=_ms(2026, 10, 14, 15))
    result = view.read_updates(SYM, W1_S, since_seq=41)
    assert result.cursor_seq == 42
    (event,) = result.events
    assert event["complete"] is False and event["bar"]["open_time_ms"] == _ms(2026, 10, 11, 21)
    assert (event["bar"]["high"], event["bar"]["close"]) == (500.0, 130.0)


def test_updates_without_events_pass_through():
    view = _view(_FakeUds(ROWS, events=[], cursor=9), now_ms=_ms(2026, 10, 19, 12))
    result = view.read_updates(SYM, W1_S, since_seq=None)
    assert result.events == [] and result.cursor_seq == 9


def test_hidden_d1_bar_is_not_aggregated():
    rows = [dict(r) for r in ROWS]
    hidden = dict(rows[-1], high=10_000.0, extensions={"calendar_pause_flat": True})
    rows[-1] = hidden
    view = _view(_FakeUds(rows), now_ms=_ms(2026, 10, 19, 12))
    week = view.read_window(SYM, W1_S, 1).bars_lwc[-1]
    assert week["high"] < 10_000.0


def test_bar_close_ms_counts_to_friday_close_and_none_after():
    view = _view(_FakeUds(ROWS), now_ms=0)
    assert view.bar_close_ms(SYM, W1_S, _ms(2026, 10, 14, 15)) == _ms(2026, 10, 16, 21)
    assert view.bar_close_ms(SYM, W1_S, _ms(2026, 10, 17, 10)) is None  # субота: тиждень уже закрився
    assert view.bar_close_ms(SYM, MN_S, _ms(2026, 10, 14, 15)) == _ms(2026, 10, 30, 21)


def test_disabled_view_exposes_no_tfs():
    view = _view(_FakeUds(ROWS), now_ms=0, enabled=False)
    assert view.tfs == frozenset() and not view.is_view_tf(W1_S)


def test_config_parse_valid_missing_and_invalid():
    cfg = {"calendar_tfs": {"enabled": True, "source_tf_s": 86400, "tfs_s": [604800, 2592000],
                            "cold_start_bars_by_tf": {"604800": 520, "2592000": 240},
                            "scrollback_chunk_bars_by_tf": {"604800": 260, "2592000": 120}}}
    parsed = calendar_view_config_from_cfg(cfg)
    assert parsed.enabled and parsed.tfs == {W1_S, MN_S} and parsed.cold_start_bars[MN_S] == 240
    assert calendar_view_config_from_cfg({}).enabled is False
    with pytest.raises(ValueError, match="невідомі TF"):
        calendar_view_config_from_cfg({"calendar_tfs": dict(cfg["calendar_tfs"], tfs_s=[3600])})
    with pytest.raises(ValueError, match="cold_start_bars_by_tf"):
        calendar_view_config_from_cfg({"calendar_tfs": dict(cfg["calendar_tfs"], cold_start_bars_by_tf={})})
    with pytest.raises(ValueError, match="source_tf_s"):
        calendar_view_config_from_cfg({"calendar_tfs": dict(cfg["calendar_tfs"], source_tf_s=14400)})
