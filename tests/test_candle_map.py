"""
tests/test_candle_map.py — 7 тестів для runtime/ws/candle_map.py.

P0 exit-gate: pytest tests/test_candle_map.py -v → 7 passed.
"""

from __future__ import annotations

import logging
import pytest

from runtime.ws.candle_map import map_bar_to_candle_v4, map_bars_to_candles_v4


# ────────────────────────────────────────────────────────
# Fixtures: зразки барів у двох форматах
# ────────────────────────────────────────────────────────

LWC_BAR = {
    "time": 1740000000,          # unix seconds
    "open": 2650.50,
    "high": 2660.00,
    "low": 2648.25,
    "close": 2655.75,
    "volume": 1234.0,
    "open_time_ms": 1740000000000,
    "close_time_ms": 1740000300000,
    "tf_s": 300,
    "src": "broker_history",
    "complete": True,
}

SHORT_BAR_LOW = {
    "o": 2650.50,
    "h": 2660.00,
    "low": 2648.25,      # CandleBar uses "low" (not "l")
    "c": 2655.75,
    "v": 1234.0,
    "open_time_ms": 1740000000000,
    "close_time_ms": 1740000300000,
    "tf_s": 300,
    "src": "broker_history",
    "complete": True,
}

SHORT_BAR_L = {
    "o": 2650.50,
    "h": 2660.00,
    "l": 2648.25,         # preview може мати "l"
    "c": 2655.75,
    "v": 1234.0,
    "open_time_ms": 1740000000000,
    "close_time_ms": 1740000300000,
    "tf_s": 300,
    "src": "preview_tick",
    "complete": False,
}


# ────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────

def test_map_lwc_bar():
    """LWC bar → Candle з правильними полями, keys == {t_ms,o,h,l,c,v}."""
    result = map_bar_to_candle_v4(LWC_BAR)
    assert result is not None
    assert set(result.keys()) == {"t_ms", "o", "h", "l", "c", "v"}
    assert result["t_ms"] == 1740000000000
    assert result["o"] == 2650.50
    assert result["h"] == 2660.00
    assert result["l"] == 2648.25
    assert result["c"] == 2655.75
    assert result["v"] == 1234.0


def test_map_short_bar_low_vs_l():
    """bar з 'low' і bar з 'l' → обидва l==2648.25."""
    r1 = map_bar_to_candle_v4(SHORT_BAR_LOW)
    r2 = map_bar_to_candle_v4(SHORT_BAR_L)
    assert r1 is not None and r2 is not None
    assert r1["l"] == 2648.25
    assert r2["l"] == 2648.25
    # Решта полів теж однакові
    assert r1["o"] == r2["o"]
    assert r1["h"] == r2["h"]
    assert r1["c"] == r2["c"]


def test_map_missing_volume():
    """bar без volume → v==0.0 (не None, не reject)."""
    bar = {
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "open_time_ms": 1740000000000,
    }
    result = map_bar_to_candle_v4(bar)
    assert result is not None
    assert result["v"] == 0.0


def test_map_missing_ohlc_reject(caplog):
    """bar без open → None (rejected, loud log)."""
    bar = {
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "open_time_ms": 1740000000000,
    }
    with caplog.at_level(logging.WARNING):
        result = map_bar_to_candle_v4(bar)
    assert result is None
    assert "CANDLE_MAP_REJECT" in caplog.text
    assert "missing_ohlc" in caplog.text


def test_map_batch_with_dropped():
    """2 valid + 1 invalid → (2 candles, 1 dropped)."""
    bars = [
        LWC_BAR,
        {"garbage": True},
        SHORT_BAR_LOW,
    ]
    candles, dropped = map_bars_to_candles_v4(bars)
    assert len(candles) == 2
    assert dropped == 1
    assert candles[0]["t_ms"] == 1740000000000
    assert candles[1]["t_ms"] == 1740000000000


def test_map_fallback_open_ms():
    """bar з open_ms (замість open_time_ms) → t_ms correct."""
    bar = {
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "volume": 50.0,
        "open_ms": 1740000060000,
    }
    result = map_bar_to_candle_v4(bar)
    assert result is not None
    assert result["t_ms"] == 1740000060000


def test_map_fallback_time_sec():
    """bar з time (sec) → t_ms = time*1000."""
    bar = {
        "o": 100.0,
        "h": 110.0,
        "l": 90.0,
        "c": 105.0,
        "v": 50.0,
        "time": 1740000000,  # unix seconds
    }
    result = map_bar_to_candle_v4(bar)
    assert result is not None
    assert result["t_ms"] == 1740000000000
