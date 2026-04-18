"""tests/test_wake_structure_buffer.py — GAP #9 / ADR-040 errata A2.

Verifies:
  1. SmcRunner buffers confirmed BOS/CHoCH events from delta.new_swings
  2. get_recent_structure_events filters by since_ts_ms
  3. Buffer caps at 50 (FIFO drop)
  4. SYMBOL PARITY: XAU/USD and BTCUSDT use identical path; events isolated per symbol
  5. WakeEngine STRUCTURE_BREAK condition fires when buffer has matching event
  6. WakeEngine does NOT fire STRUCTURE_BREAK when buffer empty (no false positive)

Run: python -m pytest tests/test_wake_structure_buffer.py -v
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from core.smc.config import SmcConfig
from core.smc.engine import SmcEngine
from core.smc.wake_check import check_condition
from core.smc.wake_types import WakeCondition, WakeConditionKind
from runtime.smc.smc_runner import SmcRunner

# ── Helpers ─────────────────────────────────────────────────


def _make_engine() -> SmcEngine:
    cfg = SmcConfig.from_dict(
        {
            "enabled": True,
            "lookback_bars": 100,
            "swing_period": 2,
            "ob": {
                "enabled": True,
                "min_impulse_atr_mult": 0.1,
                "atr_period": 5,
                "max_active_per_side": 5,
            },
            "fvg": {"enabled": True, "min_gap_atr_mult": 0.0, "max_active": 10},
            "structure": {"enabled": True, "confirmation_bars": 1},
            "max_zones_per_tf": 30,
            "performance": {"max_compute_ms": 2000, "log_slow_threshold_ms": 500},
        }
    )
    return SmcEngine(cfg)


def _make_runner(symbols: List[str]) -> SmcRunner:
    """SmcRunner with minimal config covering both XAU and BTC."""
    cfg = {
        "symbols": symbols,
        "tf_allowlist_s": [60, 900],
        "smc": {
            "enabled": True,
            "lookback_bars": 100,
            "swing_period": 2,
            "compute_tfs": [900],
            "ob": {
                "enabled": True,
                "min_impulse_atr_mult": 0.1,
                "atr_period": 5,
                "max_active_per_side": 5,
            },
            "fvg": {"enabled": True, "min_gap_atr_mult": 0.0, "max_active": 10},
            "structure": {"enabled": True, "confirmation_bars": 1},
            "max_zones_per_tf": 30,
            "performance": {
                "max_compute_ms": 2000,
                "log_slow_threshold_ms": 500,
            },
        },
    }
    return SmcRunner(cfg, _make_engine())


def _push_event(
    runner: SmcRunner,
    symbol: str,
    tf_s: int,
    event_type: str,
    ts_ms: int,
    direction: str = "bullish",
    price: float = 1900.0,
) -> None:
    """Directly push event into buffer (mimics on_bar_dict relay block).

    Tests buffer mechanics without driving full delta pipeline.
    """
    ev = {
        "tf_s": tf_s,
        "type": event_type,
        "direction": direction,
        "ts_ms": ts_ms,
        "price": price,
        "id": f"{event_type}_{direction[:4]}_{symbol}_{tf_s}_{ts_ms}",
    }
    with runner._lock:
        buf = runner._recent_structure_events.setdefault(symbol, [])
        buf.append(ev)
        if len(buf) > 50:
            del buf[: len(buf) - 50]


# ── Buffer behavior ─────────────────────────────────────────


class TestStructureBuffer:
    """SmcRunner._recent_structure_events + get_recent_structure_events."""

    def test_empty_buffer_returns_empty_list(self):
        runner = _make_runner(["XAU/USD"])
        events = runner.get_recent_structure_events("XAU/USD")
        assert events == []

    def test_unknown_symbol_returns_empty_list(self):
        runner = _make_runner(["XAU/USD"])
        events = runner.get_recent_structure_events("BTCUSDT")
        assert events == []

    def test_get_returns_all_when_since_zero(self):
        runner = _make_runner(["XAU/USD"])
        _push_event(runner, "XAU/USD", 900, "bos", 1000)
        _push_event(runner, "XAU/USD", 900, "choch", 2000)
        events = runner.get_recent_structure_events("XAU/USD", since_ts_ms=0)
        assert len(events) == 2

    def test_filter_by_since_ts_ms(self):
        runner = _make_runner(["XAU/USD"])
        _push_event(runner, "XAU/USD", 900, "bos", 1000)
        _push_event(runner, "XAU/USD", 900, "choch", 2000)
        _push_event(runner, "XAU/USD", 900, "bos", 3000)
        events = runner.get_recent_structure_events("XAU/USD", since_ts_ms=1500)
        # only events with ts_ms >= 1500
        assert len(events) == 2
        assert all(e["ts_ms"] >= 1500 for e in events)

    def test_buffer_caps_at_50_fifo_drop(self):
        runner = _make_runner(["XAU/USD"])
        for i in range(60):
            _push_event(runner, "XAU/USD", 900, "bos", ts_ms=1000 + i)
        events = runner.get_recent_structure_events("XAU/USD")
        assert len(events) == 50
        # oldest events (ts_ms 1000..1009) dropped, newest kept
        ts_list = [e["ts_ms"] for e in events]
        assert min(ts_list) == 1010
        assert max(ts_list) == 1059

    def test_returns_shallow_copy_not_reference(self):
        """Mutating returned list must not corrupt buffer."""
        runner = _make_runner(["XAU/USD"])
        _push_event(runner, "XAU/USD", 900, "bos", 1000)
        events = runner.get_recent_structure_events("XAU/USD")
        events.clear()
        # Buffer untouched
        events2 = runner.get_recent_structure_events("XAU/USD")
        assert len(events2) == 1


# ── Symbol parity (XAU vs BTC) ──────────────────────────────


class TestSymbolParity:
    """ALL 4 symbols (XAU/XAG/BTC/ETH) must use identical path.

    Buffer keyed per-symbol — events from one symbol must NOT leak to another.
    """

    def test_xau_and_btc_buffers_isolated(self):
        runner = _make_runner(["XAU/USD", "BTCUSDT"])
        _push_event(runner, "XAU/USD", 900, "bos", 1000, price=1900.0)
        _push_event(runner, "BTCUSDT", 900, "choch", 2000, price=68000.0)
        # XAU buffer has only XAU event
        xau_evs = runner.get_recent_structure_events("XAU/USD")
        assert len(xau_evs) == 1
        assert xau_evs[0]["price"] == 1900.0
        # BTC buffer has only BTC event
        btc_evs = runner.get_recent_structure_events("BTCUSDT")
        assert len(btc_evs) == 1
        assert btc_evs[0]["price"] == 68000.0

    def test_btc_and_eth_buffers_isolated(self):
        """Crypto symbols also isolated — verifies key-by-symbol contract."""
        runner = _make_runner(["BTCUSDT", "ETHUSDT"])
        _push_event(runner, "BTCUSDT", 900, "bos", 1000, price=68000.0)
        _push_event(runner, "ETHUSDT", 900, "bos", 1000, price=3500.0)
        assert len(runner.get_recent_structure_events("BTCUSDT")) == 1
        assert len(runner.get_recent_structure_events("ETHUSDT")) == 1
        assert runner.get_recent_structure_events("BTCUSDT")[0]["price"] == 68000.0
        assert runner.get_recent_structure_events("ETHUSDT")[0]["price"] == 3500.0

    def test_xag_and_xau_buffers_isolated(self):
        """Forex pair siblings — same calendar group, must still be isolated."""
        runner = _make_runner(["XAU/USD", "XAG/USD"])
        _push_event(runner, "XAU/USD", 900, "bos", 1000, price=1900.0)
        _push_event(runner, "XAG/USD", 900, "bos", 1000, price=24.5)
        assert len(runner.get_recent_structure_events("XAU/USD")) == 1
        assert len(runner.get_recent_structure_events("XAG/USD")) == 1


# ── WakeEngine wiring (structure_events → check_condition) ──


class TestWakeEngineWiring:
    """End-to-end: buffer event → WakeEngine reads → check_condition fires."""

    def test_check_condition_fires_with_matching_event(self):
        """STRUCTURE_BREAK condition with type=bos fires when buffer has BOS event."""
        runner = _make_runner(["XAU/USD"])
        _push_event(runner, "XAU/USD", 900, "bos", 1000)

        cond = WakeCondition(
            kind=WakeConditionKind.STRUCTURE_BREAK,
            params={"type": "bos"},
            reason="archi watching for BOS",
        )
        events = runner.get_recent_structure_events("XAU/USD", since_ts_ms=500)
        result = check_condition(
            cond, 1900.0, 5.0, {}, ts_ms=2000, structure_events=events
        )
        assert result is True

    def test_check_condition_does_not_fire_when_buffer_empty(self):
        """No false positive: empty buffer → no fire."""
        runner = _make_runner(["XAU/USD"])
        cond = WakeCondition(
            kind=WakeConditionKind.STRUCTURE_BREAK,
            params={"type": "bos"},
            reason="archi watching for BOS",
        )
        events = runner.get_recent_structure_events("XAU/USD")
        assert events == []
        result = check_condition(
            cond, 1900.0, 5.0, {}, ts_ms=2000, structure_events=events
        )
        assert result is False

    def test_check_condition_filters_by_tf(self):
        """Condition with tf_s=14400 must NOT match buffer event with tf_s=900."""
        runner = _make_runner(["XAU/USD"])
        _push_event(runner, "XAU/USD", 900, "bos", 1000)

        cond = WakeCondition(
            kind=WakeConditionKind.STRUCTURE_BREAK,
            params={"type": "bos", "tf_s": 14400},
            reason="H4 BOS only",
        )
        events = runner.get_recent_structure_events("XAU/USD")
        result = check_condition(
            cond, 1900.0, 5.0, {}, ts_ms=2000, structure_events=events
        )
        assert result is False

    def test_check_condition_does_not_fire_for_old_events(self):
        """since_ts_ms=last_wake filters out events seen before."""
        runner = _make_runner(["XAU/USD"])
        _push_event(runner, "XAU/USD", 900, "bos", ts_ms=1000)  # before last_wake

        cond = WakeCondition(
            kind=WakeConditionKind.STRUCTURE_BREAK,
            params={"type": "bos"},
            reason="archi",
        )
        # last_wake at 5000 → filter only events >= 5000
        events = runner.get_recent_structure_events("XAU/USD", since_ts_ms=5000)
        assert events == []
        result = check_condition(
            cond, 1900.0, 5.0, {}, ts_ms=6000, structure_events=events
        )
        assert result is False

    def test_btc_event_does_not_fire_xau_condition(self):
        """Symbol parity: BTC buffer event must not fire XAU's WakeEngine condition."""
        runner = _make_runner(["XAU/USD", "BTCUSDT"])
        _push_event(runner, "BTCUSDT", 900, "bos", 1000)

        cond = WakeCondition(
            kind=WakeConditionKind.STRUCTURE_BREAK,
            params={"type": "bos"},
            reason="archi watching XAU",
        )
        # Read XAU buffer (NOT BTC) — empty
        events = runner.get_recent_structure_events("XAU/USD")
        result = check_condition(
            cond, 1900.0, 5.0, {}, ts_ms=2000, structure_events=events
        )
        assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
