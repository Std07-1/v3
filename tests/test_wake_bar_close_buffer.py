"""tests/test_wake_bar_close_buffer.py — ADR-0075 candle_close.

Mirrors test_wake_structure_buffer.py for the closed-bar path:
  1. SmcRunner buffers closed-bar events (broker finale = complete bar)
  2. get_recent_bar_closes filters by since_ts_ms + caps FIFO
  3. Symbol isolation (XAU vs BTC)
  4. WakeEngine CANDLE_CLOSE fires when a CLOSE (not touch) crosses level
  5. No false fire: empty buffer / wrong TF / old events / wrong side

Run: python -m pytest tests/test_wake_bar_close_buffer.py -v
"""

from __future__ import annotations

from typing import List

import pytest

from core.smc.config import SmcConfig
from core.smc.engine import SmcEngine
from core.smc.wake_check import check_condition
from core.smc.wake_types import WakeCondition, WakeConditionKind
from runtime.smc.smc_runner import SmcRunner


def _make_engine() -> SmcEngine:
    cfg = SmcConfig.from_dict(
        {
            "enabled": True,
            "lookback_bars": 100,
            "swing_period": 2,
            "ob": {"enabled": True, "min_impulse_atr_mult": 0.1, "atr_period": 5, "max_active_per_side": 5},
            "fvg": {"enabled": True, "min_gap_atr_mult": 0.0, "max_active": 10},
            "structure": {"enabled": True, "confirmation_bars": 1},
            "max_zones_per_tf": 30,
            "performance": {"max_compute_ms": 2000, "log_slow_threshold_ms": 500},
        }
    )
    return SmcEngine(cfg)


def _make_runner(symbols: List[str]) -> SmcRunner:
    cfg = {
        "symbols": symbols,
        "tf_allowlist_s": [60, 900],
        "smc": {
            "enabled": True,
            "lookback_bars": 100,
            "swing_period": 2,
            "compute_tfs": [900],
            "ob": {"enabled": True, "min_impulse_atr_mult": 0.1, "atr_period": 5, "max_active_per_side": 5},
            "fvg": {"enabled": True, "min_gap_atr_mult": 0.0, "max_active": 10},
            "structure": {"enabled": True, "confirmation_bars": 1},
            "max_zones_per_tf": 30,
            "performance": {"max_compute_ms": 2000, "log_slow_threshold_ms": 500},
        },
    }
    return SmcRunner(cfg, _make_engine())


def _push_close(runner: SmcRunner, symbol: str, tf_s: int, close: float, ts_ms: int) -> None:
    """Mimic the on_bar_dict complete-bar buffer block."""
    with runner._lock:
        buf = runner._recent_bar_closes.setdefault(symbol, [])
        buf.append(
            {"tf_s": tf_s, "close": close, "open_time_ms": ts_ms - tf_s * 1000, "ts_ms": ts_ms}
        )
        if len(buf) > 100:
            del buf[: len(buf) - 100]


class TestBarCloseBuffer:
    def test_empty_buffer_returns_empty(self):
        runner = _make_runner(["XAU/USD"])
        assert runner.get_recent_bar_closes("XAU/USD") == []

    def test_filter_by_since_ts_ms(self):
        runner = _make_runner(["XAU/USD"])
        _push_close(runner, "XAU/USD", 14400, 4200.0, 1000)
        _push_close(runner, "XAU/USD", 14400, 4260.0, 3000)
        evs = runner.get_recent_bar_closes("XAU/USD", since_ts_ms=1500)
        assert len(evs) == 1
        assert evs[0]["close"] == 4260.0

    def test_buffer_caps_at_100_fifo(self):
        runner = _make_runner(["XAU/USD"])
        for i in range(110):
            _push_close(runner, "XAU/USD", 900, 4200.0 + i, ts_ms=1000 + i)
        evs = runner.get_recent_bar_closes("XAU/USD")
        assert len(evs) == 100
        assert min(e["ts_ms"] for e in evs) == 1010

    def test_symbol_isolation(self):
        runner = _make_runner(["XAU/USD", "BTCUSDT"])
        _push_close(runner, "XAU/USD", 14400, 4200.0, 1000)
        _push_close(runner, "BTCUSDT", 14400, 68000.0, 1000)
        assert len(runner.get_recent_bar_closes("XAU/USD")) == 1
        assert runner.get_recent_bar_closes("XAU/USD")[0]["close"] == 4200.0
        assert runner.get_recent_bar_closes("BTCUSDT")[0]["close"] == 68000.0


class TestCandleCloseCheck:
    """check_condition CANDLE_CLOSE — close (not touch) crosses level."""

    def _cond(self, **params):
        return WakeCondition(
            kind=WakeConditionKind.CANDLE_CLOSE, params=params, reason="archi invalidation"
        )

    def test_close_above_level_fires(self):
        # H4 closed at 4270 above 4268 → invalidation confirmed.
        runner = _make_runner(["XAU/USD"])
        _push_close(runner, "XAU/USD", 14400, 4270.0, 2000)
        evs = runner.get_recent_bar_closes("XAU/USD", since_ts_ms=500)
        cond = self._cond(level=4268, direction="above", tf_s=14400)
        assert check_condition(cond, 4271.0, 5.0, {}, ts_ms=3000, bar_close_events=evs) is True

    def test_close_not_reaching_level_does_not_fire(self):
        # Closed at 4265 — below 4268, even if current price touched higher.
        runner = _make_runner(["XAU/USD"])
        _push_close(runner, "XAU/USD", 14400, 4265.0, 2000)
        evs = runner.get_recent_bar_closes("XAU/USD")
        cond = self._cond(level=4268, direction="above", tf_s=14400)
        assert check_condition(cond, 4275.0, 5.0, {}, ts_ms=3000, bar_close_events=evs) is False

    def test_close_below_level_fires(self):
        runner = _make_runner(["XAU/USD"])
        _push_close(runner, "XAU/USD", 14400, 4180.0, 2000)
        evs = runner.get_recent_bar_closes("XAU/USD")
        cond = self._cond(level=4199, direction="below", tf_s=14400)
        assert check_condition(cond, 4179.0, 5.0, {}, ts_ms=3000, bar_close_events=evs) is True

    def test_tf_filter_blocks_wrong_timeframe(self):
        # Condition wants H4 close; only an M15 close exists.
        runner = _make_runner(["XAU/USD"])
        _push_close(runner, "XAU/USD", 900, 4270.0, 2000)
        evs = runner.get_recent_bar_closes("XAU/USD")
        cond = self._cond(level=4268, direction="above", tf_s=14400)
        assert check_condition(cond, 4271.0, 5.0, {}, ts_ms=3000, bar_close_events=evs) is False

    def test_empty_events_no_fire(self):
        cond = self._cond(level=4268, direction="above", tf_s=14400)
        assert check_condition(cond, 4271.0, 5.0, {}, ts_ms=3000, bar_close_events=[]) is False

    def test_old_close_filtered_by_since(self):
        runner = _make_runner(["XAU/USD"])
        _push_close(runner, "XAU/USD", 14400, 4270.0, 1000)  # before last_wake
        evs = runner.get_recent_bar_closes("XAU/USD", since_ts_ms=5000)
        cond = self._cond(level=4268, direction="above", tf_s=14400)
        assert check_condition(cond, 4271.0, 5.0, {}, ts_ms=6000, bar_close_events=evs) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
