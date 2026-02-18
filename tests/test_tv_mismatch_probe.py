"""
Тести для Slice 1-3: instrument extensions, sample_last_bar, price sanity.

Scope: діагностичні rails для виявлення TV mismatch.
"""

from __future__ import annotations

import pytest


# ── Тести для server.py helpers ──────────────────────────────


def test_build_instrument_info_fields() -> None:
    """_build_instrument_info повертає всі обов'язкові поля."""
    from ui_chart_v3.server import _build_instrument_info

    cfg = {"tick_stream_price_mode": "bid"}
    info = _build_instrument_info("XAU/USD", cfg)

    assert info["requested_symbol"] == "XAU/USD"
    assert info["canonical_symbol_key"] == "XAU_USD"
    assert info["provider"] == "fxcm"
    assert info["fxcm_instrument"] == "XAU/USD"
    assert info["price_stream_mode"] == "bid"


def test_build_instrument_info_no_price_mode() -> None:
    """price_stream_mode за замовчуванням = 'bid'."""
    from ui_chart_v3.server import _build_instrument_info

    info = _build_instrument_info("NAS100", {})
    assert info["price_stream_mode"] == "bid"
    assert info["canonical_symbol_key"] == "NAS100"


def test_build_sample_last_bar_basic() -> None:
    """sample_last_bar коректно формує останній бар."""
    from ui_chart_v3.server import _build_sample_last_bar

    bars = [
        {"open": 5000.0, "high": 5050.0, "low": 4950.0, "close": 5030.0,
         "open_time_ms": 1739577600000, "volume": 100},
    ]
    sample = _build_sample_last_bar(bars, tf_s=14400)
    assert sample is not None
    assert sample["open"] == 5000.0
    assert sample["close"] == 5030.0
    assert sample["high"] == 5050.0
    assert sample["low"] == 4950.0
    assert sample["bullish"] is True
    assert sample["tf_s"] == 14400
    assert "UTC" in sample["open_utc"]


def test_build_sample_last_bar_bearish() -> None:
    """bearish бар має bullish=False."""
    from ui_chart_v3.server import _build_sample_last_bar

    bars = [
        {"open": 5030.0, "high": 5050.0, "low": 4950.0, "close": 4970.0,
         "open_time_ms": 1739577600000, "volume": 100},
    ]
    sample = _build_sample_last_bar(bars, tf_s=14400)
    assert sample is not None
    assert sample["bullish"] is False


def test_build_sample_last_bar_empty() -> None:
    """Порожній список барів повертає None."""
    from ui_chart_v3.server import _build_sample_last_bar

    assert _build_sample_last_bar([], tf_s=14400) is None


def test_inject_instrument_extensions() -> None:
    """_inject_instrument_extensions додає instrument і sample_last_bar."""
    from ui_chart_v3.server import _inject_instrument_extensions

    meta = {}
    bars = [
        {"open": 5000.0, "high": 5050.0, "low": 4950.0, "close": 5030.0,
         "open_time_ms": 1739577600000, "volume": 100},
    ]
    cfg = {"tick_stream_price_mode": "bid"}
    _inject_instrument_extensions(meta, "XAU/USD", 14400, bars, cfg)

    assert "extensions" in meta
    ext = meta["extensions"]
    assert "instrument" in ext
    assert ext["instrument"]["requested_symbol"] == "XAU/USD"
    assert "sample_last_bar" in ext
    assert ext["sample_last_bar"]["open"] == 5000.0


# ── Тести для UI mapping identity (Python еквівалент normalizeBar) ──


def _normalize_bar_py(bar: dict) -> dict | None:
    """Python еквівалент JS normalizeBar для тестування mapping identity.

    Ціль: довести що mapping не swap-нув O/C/H/L.
    """
    if not bar:
        return None
    time_v = bar.get("time")
    if time_v is None:
        open_ms = bar.get("open_time_ms")
        if open_ms is not None:
            time_v = int(open_ms) // 1000
    if time_v is None:
        return None
    time_v = int(time_v)
    open_v = bar.get("open", bar.get("o"))
    high_v = bar.get("high", bar.get("h"))
    low_v = bar.get("low", bar.get("l"))
    close_v = bar.get("close", bar.get("c"))
    if None in (open_v, high_v, low_v, close_v):
        return None
    open_v = float(open_v)
    high_v = float(high_v)
    low_v = float(low_v)
    close_v = float(close_v)
    # lastPrice override (для preview барів)
    lp = bar.get("last_price")
    if lp is not None and bar.get("complete") is not True:
        close_v = float(lp)
    # clamp high/low
    if close_v > high_v:
        high_v = close_v
    if close_v < low_v:
        low_v = close_v
    volume_v = float(bar.get("volume", bar.get("v", 0)))
    return {
        "time": time_v,
        "open": open_v,
        "high": high_v,
        "low": low_v,
        "close": close_v,
        "volume": volume_v,
    }


def test_ui_mapping_identity_basic() -> None:
    """Mapping має зберегти OHLC identity для complete=True бару."""
    bar = {
        "time": 1739577600,
        "open": 5000.0,
        "high": 5050.0,
        "low": 4950.0,
        "close": 5030.0,
        "volume": 100,
        "complete": True,
    }
    mapped = _normalize_bar_py(bar)
    assert mapped is not None
    assert mapped["open"] == bar["open"], "open swap!"
    assert mapped["close"] == bar["close"], "close swap!"
    assert mapped["high"] == bar["high"], "high swap!"
    assert mapped["low"] == bar["low"], "low swap!"
    assert mapped["time"] == bar["time"], "time mismatch!"


def test_ui_mapping_identity_window_v1_format() -> None:
    """Mapping зберігає identity для window_v1 формату (open_time_ms замість time)."""
    bar = {
        "open_time_ms": 1739577600000,
        "open": 4980.0,
        "high": 5010.0,
        "low": 4960.0,
        "close": 4990.0,
        "volume": 200,
        "complete": True,
    }
    mapped = _normalize_bar_py(bar)
    assert mapped is not None
    assert mapped["time"] == 1739577600
    assert mapped["open"] == 4980.0
    assert mapped["close"] == 4990.0
    assert mapped["high"] == 5010.0
    assert mapped["low"] == 4960.0


def test_ui_mapping_bullish_direction() -> None:
    """Перевірка що bullish direction зберігається після mapping."""
    bullish_bar = {"time": 100, "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 1, "complete": True}
    bearish_bar = {"time": 200, "open": 105.0, "high": 110.0, "low": 95.0, "close": 100.0, "volume": 1, "complete": True}
    m_bull = _normalize_bar_py(bullish_bar)
    m_bear = _normalize_bar_py(bearish_bar)
    assert m_bull["close"] >= m_bull["open"], "bullish бар став bearish після mapping!"
    assert m_bear["close"] < m_bear["open"], "bearish бар став bullish після mapping!"


# ── Тест price sanity ──


def test_price_sanity_check() -> None:
    """Price sanity check для XAU/USD."""
    from tools.audit.tv_mismatch_probe import _price_sanity_check

    # Адекватна ціна
    bars_ok = [{"close": 5038.0}]
    assert _price_sanity_check("XAU/USD", bars_ok) == []

    # Неадекватна ціна (наприклад замість XAU показує EUR/USD)
    bars_bad = [{"close": 1.08}]
    issues = _price_sanity_check("XAU/USD", bars_bad)
    assert len(issues) > 0
    assert "PRICE_OUT_OF_RANGE" in issues[0]
