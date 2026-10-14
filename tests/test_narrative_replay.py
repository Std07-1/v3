"""Прогін наративу по історії (ADR-0113 Ф1): детермінований, без побічних ефектів, на кроці, з прогрівом до старту."""
from __future__ import annotations

import datetime as dt
import json
import os
from types import SimpleNamespace

import pytest

import core.smc.engine as engine_mod
import runtime.smc.smc_runner as smc_runner_mod
from tools.narrative_replay import HistoryReader, replay_config, replay_narrative

XAU = "XAU/USD"
_REPO_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
START_MS = int(dt.datetime(2026, 10, 14, 8, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)  # середа, Лондон
END_MS = START_MS + 2 * 3600 * 1000
SAT_NOON_S = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.timezone.utc).timestamp()  # ринок закрито


def _rows(tf_s, first_open_ms, last_close_ms):
    step = tf_s * 1000
    rows, open_ms, i = [], first_open_ms // step * step, 0
    while open_ms + step <= last_close_ms:
        price = 4100.0 + (i % 11) * 2.5 - (i % 5) * 1.5
        rows.append({"symbol": XAU, "tf_s": tf_s, "open_time_ms": open_ms, "o": price, "h": price + 3.0,
                     "low": price - 3.0, "c": price + 0.5, "v": 100.0, "complete": True, "src": "history"})
        open_ms, i = open_ms + step, i + 1
    return rows


@pytest.fixture(scope="module")
def cfg():
    return json.load(open(_REPO_CONFIG, encoding="utf-8"))


@pytest.fixture(scope="module")
def rows_by_tf(cfg):
    rows = {}
    for tf_s in [60] + [int(tf) for tf in cfg["smc"]["compute_tfs"]]:
        warmup_bars = 3000 if tf_s == 60 else 600  # більше за прогрів SmcRunner: 2880 M1, lookback 500
        rows[tf_s] = _rows(tf_s, START_MS - warmup_bars * tf_s * 1000, END_MS)
    return rows


def _replay(cfg, rows_by_tf):
    return list(replay_narrative(cfg, rows_by_tf, XAU, [300, 900, 3600], 300, START_MS, END_MS))


def test_replay_is_deterministic_and_ignores_wall_clock(cfg, rows_by_tf, monkeypatch):
    first = _replay(cfg, rows_by_tf)
    saturday = SimpleNamespace(time=lambda: SAT_NOON_S)  # якби годинник щось вирішував — усе було б «Ринок закрито»
    monkeypatch.setattr(smc_runner_mod, "time", saturday)
    monkeypatch.setattr(engine_mod, "time", saturday)
    second = _replay(cfg, rows_by_tf)

    assert first == second
    assert all(row["narrative"]["market_phase"] != "closed" for row in first)


def test_replay_steps_on_step_closes_for_every_viewer(cfg, rows_by_tf):
    rows = _replay(cfg, rows_by_tf)

    steps = sorted({row["t"] for row in rows})
    assert steps == list(range(START_MS + 300_000, END_MS + 1, 300_000))  # кожне закриття M5 у (start, end]
    assert all(sorted(r["tf_s"] for r in rows if r["t"] == t) == [300, 900, 3600] for t in steps)
    assert all(row["narrative"]["current_session"] == "london" for row in rows)


def test_replay_has_no_side_effects(cfg, rows_by_tf, monkeypatch, tmp_path):
    def _forbidden(*args, **kwargs):
        raise AssertionError("прогін не має слати relay")

    monkeypatch.setattr(smc_runner_mod, "fire_signal", _forbidden)
    monkeypatch.setattr(smc_runner_mod, "fire_bias", _forbidden)
    monkeypatch.chdir(tmp_path)
    _replay(cfg, rows_by_tf)

    assert list(tmp_path.iterdir()) == []  # ні журналу сигналів, ні стану TDA
    replay_cfg = replay_config(cfg, XAU)
    assert replay_cfg["smc"]["signal_journal"]["enabled"] is False
    assert replay_cfg["smc"]["tda_cascade"]["enabled"] is False
    assert cfg["smc"]["signal_journal"]["enabled"] is True  # SSOT не змінено — лише копія


def test_history_reader_serves_only_bars_closed_by_start():
    rows = _rows(900, START_MS - 10 * 900_000, START_MS + 3 * 900_000)
    reader = HistoryReader({900: rows}, START_MS)

    window = reader.read_window(SimpleNamespace(tf_s=900, limit=4), None).bars_lwc

    assert [r["open_time_ms"] for r in window] == [START_MS - k * 900_000 for k in (4, 3, 2, 1)]
    assert reader.read_window(SimpleNamespace(tf_s=60, limit=10), None).bars_lwc == []
