"""Tests for `tools.cowork.event_watcher` (slice cowork.004).

Covers:
    * TDA signal journal scan (symbol filter, event filter, multi-day)
    * Bias hash stability + change detection
    * Cold-start seeding (no firing on first tick)
    * tda_signal precedence over bias_flip
    * State persistence + restart resilience
    * Atomic flag write
    * Tick precedence: tda_signal wins
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.cowork.event_watcher import (
    DEFAULT_TRIGGER_EVENTS,
    EVENT_FLAG_FILENAME,
    STATE_FILENAME,
    WatcherConfig,
    WatcherState,
    evaluate_tick,
    hash_bias_map,
    load_config_from_env,
    load_state,
    save_state,
    scan_latest_signal,
    write_event_flag,
)


def _utc(y: int, mo: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, mo, d, h, mi, 0, tzinfo=timezone.utc)


def _make_config(tmp_path: Path, **overrides) -> WatcherConfig:
    defaults: dict = dict(
        triggers_dir=tmp_path / "triggers",
        signals_dir=tmp_path / "signals",
        api_base="http://127.0.0.1:8000",
        api_token="t-test",
        interval_s=30,
        symbol="XAU/USD",
        trigger_events=frozenset(DEFAULT_TRIGGER_EVENTS),
    )
    defaults.update(overrides)
    return WatcherConfig(**defaults)


def _write_journal(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_load_config_defaults_when_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_config_from_env(env={})
    assert cfg.symbol == "XAU/USD"
    assert cfg.interval_s == 30
    assert "signal_emitted" in cfg.trigger_events
    assert cfg.api_token is None


def test_load_config_overrides_from_env() -> None:
    cfg = load_config_from_env(
        env={
            "COWORK_TRIGGERS_DIR": "/tmp/trig",
            "COWORK_EVENT_WATCHER_TOKEN": "abc",
            "COWORK_EVENT_WATCHER_INTERVAL_S": "60",
            "COWORK_EVENT_WATCHER_SYMBOL": "BTCUSDT",
            "COWORK_EVENT_WATCHER_TRIGGER_EVENTS": "trade_entered, trade_exited",
        }
    )
    assert cfg.api_token == "abc"
    assert cfg.interval_s == 60
    assert cfg.symbol == "BTCUSDT"
    assert cfg.trigger_events == frozenset({"trade_entered", "trade_exited"})


# ---------------------------------------------------------------------------
# Signal scanning
# ---------------------------------------------------------------------------


def test_scan_latest_signal_filters_symbol_and_event(tmp_path: Path) -> None:
    journal = tmp_path / "journal-2026-05-07.jsonl"
    _write_journal(
        journal,
        [
            {"symbol": "XAU/USD", "event": "signal_emitted", "wall_ms": 1000},
            {"symbol": "XAU/USD", "event": "log_only", "wall_ms": 2000},  # filtered
            {
                "symbol": "BTCUSDT",
                "event": "trade_entered",
                "wall_ms": 3000,
            },  # filtered
            {"symbol": "XAU/USD", "event": "trade_entered", "wall_ms": 1500},
        ],
    )
    latest = scan_latest_signal(
        signals_dir=tmp_path,
        symbol="XAU/USD",
        trigger_events=frozenset(DEFAULT_TRIGGER_EVENTS),
        now_utc=_utc(2026, 5, 7, 12, 0),
    )
    assert latest == 1500


def test_scan_latest_signal_returns_none_on_empty_dir(tmp_path: Path) -> None:
    latest = scan_latest_signal(
        signals_dir=tmp_path / "nope",
        symbol="XAU/USD",
        trigger_events=frozenset(DEFAULT_TRIGGER_EVENTS),
        now_utc=_utc(2026, 5, 7, 12, 0),
    )
    assert latest is None


def test_scan_latest_signal_skips_garbled_lines(tmp_path: Path) -> None:
    journal = tmp_path / "journal-2026-05-07.jsonl"
    journal.write_text(
        '{"symbol":"XAU/USD","event":"signal_emitted","wall_ms":42}\n'
        "{not json\n"
        "\n"
        '{"symbol":"XAU/USD","event":"trade_exited","wall_ms":100}\n',
        encoding="utf-8",
    )
    latest = scan_latest_signal(
        signals_dir=tmp_path,
        symbol="XAU/USD",
        trigger_events=frozenset(DEFAULT_TRIGGER_EVENTS),
        now_utc=_utc(2026, 5, 7, 12, 0),
    )
    assert latest == 100


def test_scan_latest_signal_includes_yesterday(tmp_path: Path) -> None:
    yday = tmp_path / "journal-2026-05-06.jsonl"
    today = tmp_path / "journal-2026-05-07.jsonl"
    _write_journal(
        yday,
        [
            {"symbol": "XAU/USD", "event": "signal_emitted", "wall_ms": 99999},
        ],
    )
    _write_journal(
        today,
        [
            {"symbol": "XAU/USD", "event": "signal_emitted", "wall_ms": 50000},
        ],
    )
    latest = scan_latest_signal(
        signals_dir=tmp_path,
        symbol="XAU/USD",
        trigger_events=frozenset(DEFAULT_TRIGGER_EVENTS),
        now_utc=_utc(2026, 5, 7, 0, 30),
    )
    # Yesterday's wall_ms is the larger value, so it wins.
    assert latest == 99999


# ---------------------------------------------------------------------------
# Bias hash
# ---------------------------------------------------------------------------


def test_hash_bias_map_stable_across_key_order() -> None:
    a = {"300": "bullish", "900": "bearish", "3600": "neutral"}
    b = {"3600": "neutral", "900": "bearish", "300": "bullish"}
    assert hash_bias_map(a) == hash_bias_map(b)


def test_hash_bias_map_changes_on_value_flip() -> None:
    before = {"900": "bullish"}
    after = {"900": "bearish"}
    assert hash_bias_map(before) != hash_bias_map(after)


# ---------------------------------------------------------------------------
# evaluate_tick — cold start + transitions
# ---------------------------------------------------------------------------


def test_cold_start_seeds_without_firing(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    state = WatcherState(symbol=cfg.symbol)
    trigger = evaluate_tick(
        cfg,
        state,
        _utc(2026, 5, 7, 9, 0),
        bias_map={"900": "bullish"},
        latest_signal_ms=12345,
    )
    assert trigger is None
    assert state.seeded is True
    assert state.last_signal_wall_ms == 12345
    assert state.last_bias_hash != ""


def test_new_signal_after_seed_fires_tda_signal(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    state = WatcherState(
        symbol=cfg.symbol,
        last_signal_wall_ms=100,
        last_bias_hash=hash_bias_map({"900": "bullish"}),
        seeded=True,
    )
    trigger = evaluate_tick(
        cfg,
        state,
        _utc(2026, 5, 7, 9, 0),
        bias_map={"900": "bullish"},
        latest_signal_ms=200,
    )
    assert trigger == "tda_signal"
    assert state.last_signal_wall_ms == 200


def test_bias_flip_alone_fires_bias_flip(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    state = WatcherState(
        symbol=cfg.symbol,
        last_signal_wall_ms=100,
        last_bias_hash=hash_bias_map({"900": "bullish"}),
        seeded=True,
    )
    trigger = evaluate_tick(
        cfg,
        state,
        _utc(2026, 5, 7, 9, 0),
        bias_map={"900": "bearish"},
        latest_signal_ms=100,  # no new signal
    )
    assert trigger == "bias_flip"


def test_signal_wins_over_bias_when_both_change(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    state = WatcherState(
        symbol=cfg.symbol,
        last_signal_wall_ms=100,
        last_bias_hash=hash_bias_map({"900": "bullish"}),
        seeded=True,
    )
    trigger = evaluate_tick(
        cfg,
        state,
        _utc(2026, 5, 7, 9, 0),
        bias_map={"900": "bearish"},
        latest_signal_ms=200,
    )
    assert trigger == "tda_signal"
    # Bias hash also updated to avoid double-firing on next tick.
    assert state.last_bias_hash == hash_bias_map({"900": "bearish"})


def test_no_change_returns_none(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    bias = {"900": "bullish"}
    state = WatcherState(
        symbol=cfg.symbol,
        last_signal_wall_ms=100,
        last_bias_hash=hash_bias_map(bias),
        seeded=True,
    )
    assert (
        evaluate_tick(
            cfg,
            state,
            _utc(2026, 5, 7, 9, 0),
            bias_map=bias,
            latest_signal_ms=100,
        )
        is None
    )


def test_missing_observations_do_not_crash(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    state = WatcherState(
        symbol=cfg.symbol, last_signal_wall_ms=100, last_bias_hash="abc", seeded=True
    )
    assert (
        evaluate_tick(
            cfg,
            state,
            _utc(2026, 5, 7, 9, 0),
            bias_map=None,
            latest_signal_ms=None,
        )
        is None
    )


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def test_save_then_load_state_round_trip(tmp_path: Path) -> None:
    state_path = tmp_path / STATE_FILENAME
    original = WatcherState(
        symbol="XAU/USD", last_signal_wall_ms=42, last_bias_hash="deadbeef", seeded=True
    )
    save_state(state_path, original)
    loaded = load_state(state_path, "XAU/USD")
    assert loaded.last_signal_wall_ms == 42
    assert loaded.last_bias_hash == "deadbeef"
    assert loaded.seeded is True


def test_load_state_resets_when_symbol_changes(tmp_path: Path) -> None:
    state_path = tmp_path / STATE_FILENAME
    save_state(
        state_path, WatcherState(symbol="BTCUSDT", last_signal_wall_ms=999, seeded=True)
    )
    loaded = load_state(state_path, "XAU/USD")
    assert loaded.seeded is False
    assert loaded.last_signal_wall_ms == 0


def test_load_state_returns_default_when_file_missing(tmp_path: Path) -> None:
    loaded = load_state(tmp_path / "missing.json", "XAU/USD")
    assert loaded.symbol == "XAU/USD"
    assert loaded.seeded is False


def test_load_state_handles_corrupt_file(tmp_path: Path) -> None:
    state_path = tmp_path / STATE_FILENAME
    state_path.write_text("{not json", encoding="utf-8")
    loaded = load_state(state_path, "XAU/USD")
    assert loaded.seeded is False


# ---------------------------------------------------------------------------
# Atomic flag write
# ---------------------------------------------------------------------------


def test_write_event_flag_creates_dir_and_file(tmp_path: Path) -> None:
    triggers_dir = tmp_path / "deeply" / "nested" / "triggers"
    flag = write_event_flag(triggers_dir, "tda_signal", _utc(2026, 5, 7, 9, 30))
    assert flag.name == EVENT_FLAG_FILENAME
    payload = json.loads(flag.read_text(encoding="utf-8"))
    assert payload["trigger"] == "tda_signal"
    assert payload["ts"] == "2026-05-07T09:30:00Z"


def test_write_event_flag_overwrites_atomically(tmp_path: Path) -> None:
    triggers_dir = tmp_path / "triggers"
    write_event_flag(triggers_dir, "tda_signal", _utc(2026, 5, 7, 9, 30))
    write_event_flag(triggers_dir, "bias_flip", _utc(2026, 5, 7, 9, 31))
    payload = json.loads((triggers_dir / EVENT_FLAG_FILENAME).read_text())
    assert payload["trigger"] == "bias_flip"
    # No tmp leftover.
    assert not (triggers_dir / f"{EVENT_FLAG_FILENAME}.tmp").exists()
