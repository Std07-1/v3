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
from runtime.ingest.m1_session_filter import resolve_close_safety_ms, split_closed_bars

M1_MS = 60_000
SAFETY_MS = 8_000
# Середа 2026-09-09 12:00:30 UTC — усередині сесії календаря нижче.
NOW_MS = int(dt.datetime(2026, 9, 9, 12, 0, 30, tzinfo=dt.timezone.utc).timestamp() * 1000)
SATURDAY_22 = int(dt.datetime(2026, 9, 5, 22, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)  # до NOW_MS, бар закритий
# Нд 21:30 — пауза, але за 30 хв до відкриття 22:00: «біля краю сесії», де лишається маркер anomaly.
SUNDAY_2130 = int(dt.datetime(2026, 9, 6, 21, 30, tzinfo=dt.timezone.utc).timestamp() * 1000)
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
    closed, unclosed = split_closed_bars([just_closed, forming], now_ms, SAFETY_MS)
    assert closed == [just_closed]
    assert unclosed == [forming]


def test_split_holds_back_a_minute_closed_inside_the_safety_window():
    close_ms = 1_789_492_320_000
    bar = _bar(close_ms - M1_MS)
    assert split_closed_bars([bar], close_ms + SAFETY_MS - 1, SAFETY_MS) == ([], [bar])
    assert split_closed_bars([bar], close_ms + SAFETY_MS, SAFETY_MS) == ([bar], [])


@pytest.mark.parametrize("cfg, expected_ms", [
    ({"m1_poller": {"safety_delay_s": 10}}, 10_000),
    ({}, 8_000),
    ({"m1_poller": {"safety_delay_s": "хибне"}}, 8_000),
])
def test_safety_margin_comes_from_the_m1_poller_config(cfg, expected_ms):
    assert resolve_close_safety_ms(cfg) == expected_ms


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


def _run_main(tmp_path: Path, monkeypatch, bars, *, with_calendar=True, extra_argv=(), extra_cfg=None):
    data_root = tmp_path / "data_v3"
    cfg = {"data_root": str(data_root), "m1_poller": {"safety_delay_s": 8}}
    cfg.update(extra_cfg or {})
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
    monkeypatch.setattr("sys.argv", ["fetch_tf_backfill", "--tf", "60", "--symbol", "GER30", "--n", str(len(bars))]
                        + list(extra_argv))
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
        _flat(SATURDAY_22 - 60 * M1_MS),      # Сб 21:00: глибоко у вихідних, плаский шум брокера — не пишеться
        _bar(SATURDAY_22 - 30 * M1_MS),       # Сб 21:30: глибоко у вихідних, неплаский — теж шум, не пишеться
        _bar(SUNDAY_2130),                    # Нд 21:30: пауза біля краю сесії — аномалія, пишеться з маркером
    ]
    rc, written = _run_main(tmp_path, monkeypatch, bars)
    assert rc == 0
    by_open = {row["open_time_ms"]: row for row in written}
    assert set(by_open) == {in_session, in_session + M1_MS, SUNDAY_2130}
    assert by_open[in_session + M1_MS]["extensions"] == {"trading_flat": True}
    assert by_open[SUNDAY_2130]["extensions"] == {"calendar_pause_nonflat_anomaly": True}


def test_main_pause_noise_margin_comes_from_config(tmp_path: Path, monkeypatch):
    """Засів бере запас із config (m1_session_filter.pause_noise_margin_min): при запасі на всю добу Сб 21:30 уже
    «біля краю» (до Нд 22:00 — 1470 хв) і пишеться як аномалія, а не відкидається як шум."""
    in_session = NOW_MS // M1_MS * M1_MS - 10 * M1_MS
    saturday_2130 = SATURDAY_22 - 30 * M1_MS
    rc, written = _run_main(tmp_path, monkeypatch, [_bar(in_session), _bar(saturday_2130)],
                            extra_cfg={"m1_session_filter": {"pause_noise_margin_min": 1470}})
    assert rc == 0
    by_open = {row["open_time_ms"]: row for row in written}
    assert by_open[saturday_2130]["extensions"] == {"calendar_pause_nonflat_anomaly": True}


def test_main_refuses_the_batch_when_many_minutes_fall_outside_the_calendar(tmp_path: Path, monkeypatch):
    """Хибний календар не має тихо їсти справжні хвилини: понад допуск — відмова rc 1, нічого не записано."""
    off = [_flat(SATURDAY_22 - (i + 1) * M1_MS) for i in range(6)]
    in_session = _bar(NOW_MS // M1_MS * M1_MS - 10 * M1_MS)
    rc, written = _run_main(tmp_path, monkeypatch, [in_session] + off)
    assert rc == 1
    assert written == []


def test_main_writes_the_batch_off_calendar_when_operator_allows_it(tmp_path: Path, monkeypatch):
    """--allow-off-calendar — свідоме рішення оператора: пласкі поза сесією все одно не пишуться."""
    off = [_flat(SATURDAY_22 - (i + 1) * M1_MS) for i in range(6)]
    in_session = _bar(NOW_MS // M1_MS * M1_MS - 10 * M1_MS)
    rc, written = _run_main(tmp_path, monkeypatch, [in_session] + off, extra_argv=["--allow-off-calendar"])
    assert rc == 0
    assert [row["open_time_ms"] for row in written] == [in_session.open_time_ms]


def test_main_tolerates_a_couple_of_minutes_around_the_session_edge(tmp_path: Path, monkeypatch):
    """Дві-три хвилини навколо межі сесії — не ознака хибного календаря, партія пишеться без відмови."""
    edge = [_flat(SATURDAY_22 - M1_MS), _flat(SATURDAY_22 - 2 * M1_MS)]
    in_session = _bar(NOW_MS // M1_MS * M1_MS - 10 * M1_MS)
    rc, written = _run_main(tmp_path, monkeypatch, [in_session] + edge)
    assert rc == 0
    assert [row["open_time_ms"] for row in written] == [in_session.open_time_ms]


def test_main_refuses_a_symbol_without_session_calendar(tmp_path: Path, monkeypatch):
    rc, written = _run_main(tmp_path, monkeypatch, [_bar(NOW_MS // M1_MS * M1_MS - 5 * M1_MS)], with_calendar=False)
    assert rc == 2
    assert written == []
