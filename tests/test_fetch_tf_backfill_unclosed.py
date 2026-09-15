"""Засів «до зараз» не пише на диск бар, що ще формується.

Навіщо. 15.09.2026 засів GER30 без зсуву курсора записав хвилину 17:12 (v=41 проти ~130 у сусідів) як
complete=true. Наступний засів її не виправляє — бари з open, що вже є на диску, пропускаються, — тож
неповна свічка лишилася б у SSOT назавжди.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import tools.fetch_tf_backfill as backfill
from core.model.bars import CandleBar

M1_MS = 60_000
SAFETY_MS = 8_000


def _bar(open_ms: int, volume: float = 130.0) -> CandleBar:
    return CandleBar(symbol="GER30", tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + M1_MS,
                     o=1.0, h=2.0, low=0.5, c=1.5, v=volume, complete=True, src="history")


def test_split_keeps_only_bars_closed_with_the_broker_safety_margin():
    now_ms = 1_789_492_348_000  # 17:12:28 — хвилина 17:12 ще формується
    forming = _bar(1_789_492_320_000, volume=41.0)
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
    """Брокер, що повертає дві закриті хвилини і поточну, яка ще формується."""

    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def fetch_last_n_m1(self, symbol, n, date_to_utc=None):
        forming_open = int(time.time() * 1000) // M1_MS * M1_MS
        return [_bar(forming_open - 3 * M1_MS), _bar(forming_open - 2 * M1_MS), _bar(forming_open, volume=41.0)]


def test_main_does_not_write_the_forming_minute_to_ssot(tmp_path: Path, monkeypatch):
    data_root = tmp_path / "data_v3"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"data_root": str(data_root), "m1_poller": {"safety_delay_s": 8}}), encoding="utf-8")
    monkeypatch.setenv("AI_ONE_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("FXCM_USERNAME", "u")
    monkeypatch.setenv("FXCM_PASSWORD", "p")
    monkeypatch.setattr(backfill, "load_env_secrets", lambda: None)
    monkeypatch.setattr(backfill, "FxcmHistoryProvider", _FakeProvider)
    monkeypatch.setattr("sys.argv", ["fetch_tf_backfill", "--tf", "60", "--symbol", "GER30", "--n", "3"])

    assert backfill.main() == 0

    written = [json.loads(line) for part in sorted((data_root / "GER30" / "tf_60").glob("part-*.jsonl"))
               for line in part.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["v"] for row in written] == [130.0, 130.0]
    assert all(row["close_time_ms"] + SAFETY_MS <= int(time.time() * 1000) for row in written)
