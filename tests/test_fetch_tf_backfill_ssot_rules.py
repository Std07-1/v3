"""Засів M1 (`tools.fetch_tf_backfill`) пише в SSOT лише те, що записав би живий M1-полер.

Навіщо.
- Бар, що ще формується. 15.09.2026 засів GER30 без зсуву курсора записав хвилину 17:12 (v=41 проти ~130 у
  сусідів) як complete=true. Наступний засів її не виправляє — бари з open, що вже є на диску, пропускаються.
- Пласкі бари поза сесією. Брокер віддає O=H=L=C і після закриття; полер їх відкидає, а засів писав усе — NAS100 і
  US30 мають пласкі хвилини Сб 22:00 саме із засіву. Власник: TradingView пласких барів не показує, вони ламають графік.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

import tools.fetch_tf_backfill as backfill
from core.model.bars import CandleBar

M1_MS = 60_000
SAFETY_MS = 8_000
# Середа 2026-09-09 12:00:30 UTC — усередині сесії календаря нижче.
NOW_MS = int(dt.datetime(2026, 9, 9, 12, 0, 30, tzinfo=dt.timezone.utc).timestamp() * 1000)
SATURDAY_22 = int(dt.datetime(2026, 9, 5, 22, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)  # до NOW_MS, бар закритий
CALENDAR_GROUP = {
    "market_weekend_open_dow": 6, "market_weekend_open_hm": "22:00",
    "market_weekend_close_dow": 4, "market_weekend_close_hm": "20:45",
    "market_daily_break_start_hm": "21:00", "market_daily_break_end_hm": "22:00",
}


def _bar(open_ms: int, *, o=1.0, h=2.0, low=0.5, c=1.5, v=130.0) -> CandleBar:
    return CandleBar(symbol="GER30", tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + M1_MS,
                     o=o, h=h, low=low, c=c, v=v, complete=True, src="history")


def _flat(open_ms: int, v: float = 1.0) -> CandleBar:
    return _bar(open_ms, o=5.0, h=5.0, low=5.0, c=5.0, v=v)


def test_split_keeps_only_bars_closed_with_the_broker_safety_margin():
    now_ms = 1_789_492_348_000  # 17:12:28 — хвилина 17:12 ще формується
    forming = _bar(1_789_492_320_000, v=41.0)
    just_closed = _bar(forming.open_time_ms - M1_MS)  # закрилась о 17:12:00, запас 8 с минув
    closed, unclosed = backfill._split_unclosed([just_closed, forming], now_ms, SAFETY_MS)
    assert closed == [just_closed]
    assert unclosed == [forming]


def test_split_holds_back_a_minute_closed_inside_the_safety_window():
    close_ms = 1_789_492_320_000
    bar = _bar(close_ms - M1_MS)
    assert backfill._split_unclosed([bar], close_ms + SAFETY_MS - 1, SAFETY_MS) == ([], [bar])
    assert backfill._split_unclosed([bar], close_ms + SAFETY_MS, SAFETY_MS) == ([bar], [])


@pytest.mark.parametrize("cfg, expected_ms", [
    ({"m1_poller": {"safety_delay_s": 10}}, 10_000),
    ({}, 8_000),
])
def test_safety_margin_comes_from_the_m1_poller_config(cfg, expected_ms):
    assert backfill._close_safety_ms(cfg) == expected_ms


class _FakeProvider:
    bars = []

    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def fetch_last_n_m1(self, symbol, n, date_to_utc=None):
        return list(type(self).bars)


def _run_main(tmp_path: Path, monkeypatch, bars, *, with_calendar=True):
    data_root = tmp_path / "data_v3"
    cfg = {"data_root": str(data_root), "m1_poller": {"safety_delay_s": 8}}
    if with_calendar:
        cfg["market_calendar_by_group"] = {"test_group": CALENDAR_GROUP}
        cfg["market_calendar_symbol_groups"] = {"GER30": "test_group"}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("AI_ONE_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("FXCM_USERNAME", "u")
    monkeypatch.setenv("FXCM_PASSWORD", "p")
    monkeypatch.setattr(backfill, "load_env_secrets", lambda: None)
    monkeypatch.setattr(_FakeProvider, "bars", bars)
    monkeypatch.setattr(backfill, "FxcmHistoryProvider", _FakeProvider)
    monkeypatch.setattr(backfill.time, "time", lambda: NOW_MS / 1000)
    monkeypatch.setattr("sys.argv", ["fetch_tf_backfill", "--tf", "60", "--symbol", "GER30", "--n", str(len(bars))])
    rc = backfill.main()
    written = [json.loads(line) for part in sorted((data_root / "GER30" / "tf_60").glob("part-*.jsonl"))
               for line in part.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rc, written


def test_main_does_not_write_the_forming_minute_to_ssot(tmp_path: Path, monkeypatch):
    forming_open = NOW_MS // M1_MS * M1_MS
    bars = [_bar(forming_open - 3 * M1_MS), _bar(forming_open - 2 * M1_MS), _bar(forming_open, v=41.0)]
    rc, written = _run_main(tmp_path, monkeypatch, bars)
    assert rc == 0
    assert [row["v"] for row in written] == [130.0, 130.0]


def test_main_drops_flat_bars_outside_session_and_marks_the_rest_like_the_poller(tmp_path: Path, monkeypatch):
    in_session = NOW_MS // M1_MS * M1_MS - 10 * M1_MS
    bars = [
        _bar(in_session),
        _flat(in_session + M1_MS),            # однотікова хвилина сесії — лишається з маркером
        _flat(SATURDAY_22 - 60 * M1_MS),      # Сб 21:00: вихідні, плаский шум брокера — не пишеться
        _bar(SATURDAY_22 - 30 * M1_MS),       # Сб 21:30: неплаский поза сесією — аномалія, пишеться з маркером
    ]
    rc, written = _run_main(tmp_path, monkeypatch, bars)
    assert rc == 0
    by_open = {row["open_time_ms"]: row for row in written}
    assert set(by_open) == {in_session, in_session + M1_MS, SATURDAY_22 - 30 * M1_MS}
    assert by_open[in_session + M1_MS]["extensions"] == {"trading_flat": True}
    assert by_open[SATURDAY_22 - 30 * M1_MS]["extensions"] == {"calendar_pause_nonflat_anomaly": True}


def test_main_refuses_a_symbol_without_session_calendar(tmp_path: Path, monkeypatch):
    rc, written = _run_main(tmp_path, monkeypatch, [_bar(NOW_MS // M1_MS * M1_MS - 5 * M1_MS)], with_calendar=False)
    assert rc == 2
    assert written == []
