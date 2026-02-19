#!/usr/bin/env python3
"""
Тести для tools.audit.tv_csv_compare — парсинг CSV, нормалізація часу, verdict.
"""

from __future__ import annotations

import datetime as _dt
import os
import tempfile
import textwrap

import pytest

from tools.audit.tv_csv_compare import (
    parse_tv_csv,
    tv_bar_to_key_ms,
    align_and_compare,
    _canonical_header,
)
from tools.audit import tv_tooltip_compare as tooltip_compare
from ui_chart_v3 import server as ui_server


# ═══════ test_parse_tv_csv_basic ═══════

def test_parse_tv_csv_basic():
    """Парсинг TradingView CSV: дата/час + OHLCV."""
    csv_text = textwrap.dedent("""\
        time,open,high,low,close,Volume
        2026-02-12T10:00:00Z,5072.71,5083.41,5048.42,5075.00,1234
        2026-02-12T14:00:00Z,5075.00,5077.38,4877.68,4940.89,5678
        2026-02-12 18:00,4940.89,4958.21,4906.26,4920.81,910
    """)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(csv_text)
        path = f.name
    try:
        bars = parse_tv_csv(path)
        assert len(bars) == 3, "Очікуємо 3 бари"

        # Перший бар
        b0 = bars[0]
        assert b0["open"] == pytest.approx(5072.71)
        assert b0["high"] == pytest.approx(5083.41)
        assert b0["low"] == pytest.approx(5048.42)
        assert b0["close"] == pytest.approx(5075.00)
        assert b0["volume"] == pytest.approx(1234.0)
        assert b0["time_dt"] == _dt.datetime(2026, 2, 12, 10, 0, 0)

        # Різні формати дати
        assert bars[1]["time_dt"] == _dt.datetime(2026, 2, 12, 14, 0, 0)
        assert bars[2]["time_dt"] == _dt.datetime(2026, 2, 12, 18, 0, 0)
    finally:
        os.unlink(path)


def test_parse_tv_csv_daily():
    """CSV з daily даними (тільки дата, без часу)."""
    csv_text = textwrap.dedent("""\
        time,open,high,low,close
        2026-02-10,5000.00,5100.00,4900.00,5050.00
        2026-02-11,5050.00,5200.00,4950.00,5150.00
    """)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(csv_text)
        path = f.name
    try:
        bars = parse_tv_csv(path)
        assert len(bars) == 2
        assert bars[0]["time_dt"] == _dt.datetime(2026, 2, 10, 0, 0, 0)
        assert bars[0]["volume"] == 0.0  # немає volume колонки
    finally:
        os.unlink(path)


def test_canonical_header():
    """Маппінг різних заголовків CSV до канонічних."""
    assert _canonical_header("Time") == "time"
    assert _canonical_header("Date") == "time"
    assert _canonical_header("Date/Time") == "time"
    assert _canonical_header('"Open"') == "open"
    assert _canonical_header("Volume MA") == "volume"
    assert _canonical_header("\ufefftime") == "time"  # BOM


# ═══════ test_align_open_time_ms ═══════

def test_align_open_time_ms_open_field():
    """Правильне зведення часу до open_time_ms (time_field=open)."""
    bar = {
        "time_dt": _dt.datetime(2026, 2, 12, 10, 0, 0),
        "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0,
    }
    oms = tv_bar_to_key_ms(bar, tz_mode="utc", time_field="open")
    expected = int((_dt.datetime(2026, 2, 12, 10, 0, 0) - _dt.datetime(1970, 1, 1)).total_seconds()) * 1000
    assert oms == expected  # 1770890400000


def test_align_open_time_ms_close_field():
    """time_field=close: CSV час = close → open_ms = time - tf_s."""
    bar = {
        "time_dt": _dt.datetime(2026, 2, 12, 14, 0, 0),  # close time
        "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0,
    }
    oms = tv_bar_to_key_ms(bar, tz_mode="utc", time_field="close")
    # close=14:00
    expected = int((_dt.datetime(2026, 2, 12, 10, 0, 0) - _dt.datetime(1970, 1, 1)).total_seconds()) * 1000
    assert oms == expected + (14400 * 1000)


# ═══════ test_verdict_rules ═══════

def test_verdict_pass():
    """PASS: mismatch=0, tv_only=0, api_only=0, common>0."""
    tv_bars = [
        {"time_dt": _dt.datetime(2026, 2, 12, 10, 0), "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0},
        {"time_dt": _dt.datetime(2026, 2, 12, 14, 0), "open": 105.0, "high": 115.0, "low": 95.0, "close": 108.0},
    ]
    api_bars = [
        {"open_time_ms": 1770890400000, "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0},
        {"open_time_ms": 1770904800000, "open": 105.0, "high": 115.0, "low": 95.0, "close": 108.0},
    ]
    result = align_and_compare(tv_bars, api_bars, tf_s=14400, tz_mode="utc", time_field="open", expected_limit=2)
    assert result["verdict"] == "PASS"
    assert result["common_count"] == 2
    assert result["mismatch_count"] == 0
    assert result["tv_only_count"] == 0
    assert result["api_only_count"] == 0


def test_verdict_fail_mismatch():
    """FAIL: OHLC mismatch."""
    tv_bars = [
        {"time_dt": _dt.datetime(2026, 2, 12, 10, 0), "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0},
    ]
    api_bars = [
        {"open_time_ms": 1770890400000, "open": 100.5, "high": 110.0, "low": 90.0, "close": 105.0},
    ]
    result = align_and_compare(tv_bars, api_bars, tf_s=14400, tz_mode="utc", time_field="open", expected_limit=1)
    assert result["verdict"] == "FAIL"
    assert result["mismatch_count"] == 1
    assert result["first_mismatch"] is not None
    assert result["first_mismatch"]["delta"]["o"] == pytest.approx(-0.5)


def test_verdict_fail_tv_only():
    """FAIL: є бари тільки в TV."""
    tv_bars = [
        {"time_dt": _dt.datetime(2026, 2, 12, 10, 0), "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0},
        {"time_dt": _dt.datetime(2026, 2, 12, 14, 0), "open": 105.0, "high": 115.0, "low": 95.0, "close": 108.0},
    ]
    api_bars = [
        {"open_time_ms": 1770890400000, "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0},
    ]
    result = align_and_compare(tv_bars, api_bars, tf_s=14400, tz_mode="utc", time_field="open", expected_limit=2)
    assert result["verdict"] == "FAIL"
    assert result["tv_only_count"] == 1


def test_verdict_fail_empty():
    """FAIL: common=0."""
    result = align_and_compare([], [], tf_s=14400, tz_mode="utc", time_field="open", expected_limit=0)
    assert result["verdict"] == "FAIL"
    assert result["common_count"] == 0


def test_float_tolerance():
    """PASS при різниці менше float_tol."""
    tv_bars = [
        {"time_dt": _dt.datetime(2026, 2, 12, 10, 0), "open": 100.001, "high": 110.0, "low": 90.0, "close": 105.0},
    ]
    api_bars = [
        {"open_time_ms": 1770890400000, "open": 100.005, "high": 110.0, "low": 90.0, "close": 105.0},
    ]
    result = align_and_compare(tv_bars, api_bars, tf_s=14400, tz_mode="utc", time_field="open", expected_limit=1, float_tol=0.01)
    assert result["verdict"] == "PASS"
    assert result["mismatch_count"] == 0


def test_tv_tooltip_compare_finds_bar_in_large_limit_window() -> None:
    """Пошук open_time_ms має знаходити бар у вікні 600."""
    bars = []
    base = 1_700_000_000_000
    target_open_ms = base + 123 * 1000
    for i in range(600):
        open_ms = base + i * 1000
        bars.append({
            "open_time_ms": open_ms,
            "open": 100.0,
            "high": 110.0,
            "low": 90.0,
            "close": 105.0,
        })

    payload = {"ok": True, "bars": bars}
    result = tooltip_compare.compare_tooltip_payload(
        payload,
        symbol="XAU/USD",
        tf_s=14400,
        align="tv",
        open_time_ms=target_open_ms,
        tv_open=100.0,
        tv_high=110.0,
        tv_low=90.0,
        tv_close=105.0,
        tol=0.02,
        searched_limit=600,
        dump_window_on_miss=True,
    )
    assert result["verdict_reason"] != "bar_not_found"
    assert result["bar_found"] is True


def test_tv_tooltip_compare_miss_includes_window_proof() -> None:
    """Miss має включати window proof: first/last open_utc і searched_limit."""
    bars = [
        {"open_time_ms": 1000, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0},
        {"open_time_ms": 2000, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0},
        {"open_time_ms": 3000, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0},
    ]
    payload = {"ok": True, "bars": bars}
    result = tooltip_compare.compare_tooltip_payload(
        payload,
        symbol="XAU/USD",
        tf_s=14400,
        align="tv",
        open_time_ms=9999,
        tv_open=0.0,
        tv_high=0.0,
        tv_low=0.0,
        tv_close=0.0,
        tol=0.02,
        searched_limit=600,
        dump_window_on_miss=True,
    )
    assert result["verdict_reason"] == "bar_not_found"
    assert result["searched_limit"] == 600
    assert result["window_first_open_utc"] is not None
    assert result["window_last_open_utc"] is not None


def test_tv_tooltip_compare_tol_accepts_small_delta() -> None:
    """Tol=0.02 приймає різницю low на 0.02."""
    bars = [
        {
            "open_time_ms": 1000,
            "open": 100.0,
            "high": 110.0,
            "low": 100.0,
            "close": 105.0,
        }
    ]
    payload = {"ok": True, "bars": bars}
    result = tooltip_compare.compare_tooltip_payload(
        payload,
        symbol="XAU/USD",
        tf_s=14400,
        align="tv",
        open_time_ms=1000,
        tv_open=100.0,
        tv_high=110.0,
        tv_low=100.02,
        tv_close=105.0,
        tol=0.02,
        searched_limit=600,
        dump_window_on_miss=False,
    )
    assert result["verdict"] == "PASS"


# ADR-0002 Phase 3: тести _derive_h4_tv_from_h1 / _derive_h4_tv_with_budget /
# _make_derived_meta видалені — ця логіка більше не в server.py.
# Деривація H4 тепер у core/derive.py + runtime/ingest/derive_engine.py.

