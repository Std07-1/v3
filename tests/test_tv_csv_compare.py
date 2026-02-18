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


def _make_h1_bar(open_ms: int, o: float, h: float, lo: float, c: float, v: float) -> dict:
    return {
        "open_time_ms": int(open_ms),
        "close_time_ms": int(open_ms + 3600 * 1000),
        "open": float(o),
        "high": float(h),
        "low": float(lo),
        "close": float(c),
        "volume": float(v),
        "tf_s": 3600,
        "complete": True,
        "src": "history",
    }


def _make_m5_bar(open_ms: int, o: float, h: float, lo: float, c: float, v: float) -> dict:
    return {
        "open_time_ms": int(open_ms),
        "close_time_ms": int(open_ms + 300 * 1000),
        "open": float(o),
        "high": float(h),
        "low": float(lo),
        "close": float(c),
        "volume": float(v),
        "tf_s": 300,
        "complete": True,
        "src": "history",
    }


def _make_m5_bar(open_ms: int, o: float, h: float, lo: float, c: float, v: float) -> dict:
    return {
        "open_time_ms": int(open_ms),
        "close_time_ms": int(open_ms + 300 * 1000),
        "open": float(o),
        "high": float(h),
        "low": float(lo),
        "close": float(c),
        "volume": float(v),
        "tf_s": 300,
        "complete": True,
        "src": "history",
    }


def test_derive_h4_tv_anchor_times():
    """H4 TV anchor: відкриття на 23/03/07/... UTC."""
    base = _dt.datetime(2026, 2, 12, 23, 0)
    epoch = _dt.datetime(1970, 1, 1)
    bars = []
    # 8 H1 барів: 23,00,01,02,03,04,05,06
    for i in range(8):
        open_ms = int((base + _dt.timedelta(hours=i) - epoch).total_seconds()) * 1000
        bars.append(_make_h1_bar(open_ms, 100 + i, 110 + i, 90 + i, 105 + i, 1))

    derived, warnings, ext = ui_server._derive_h4_tv_from_h1(
        bars,
        anchor_remainder_ms=ui_server._TV_H4_ANCHOR_REMAINDER_MS,
        base_tf_s=3600,
        target_tf_s=14400,
    )
    assert not warnings
    opens = [b["open_time_ms"] for b in derived]
    expected_0 = int((base - epoch).total_seconds()) * 1000
    expected_1 = int((base + _dt.timedelta(hours=4) - epoch).total_seconds()) * 1000
    assert opens == [expected_0, expected_1]


def test_no_partial_bucket():
    """Неповний бакет не потрапляє в результат, warnings виставлені."""
    base = _dt.datetime(2026, 2, 12, 23, 0)
    epoch = _dt.datetime(1970, 1, 1)
    bars = []
    for i in range(3):  # тільки 3/4 H1
        open_ms = int((base + _dt.timedelta(hours=i) - epoch).total_seconds()) * 1000
        bars.append(_make_h1_bar(open_ms, 100 + i, 110 + i, 90 + i, 105 + i, 1))

    derived, warnings, ext = ui_server._derive_h4_tv_from_h1(
        bars,
        anchor_remainder_ms=ui_server._TV_H4_ANCHOR_REMAINDER_MS,
        base_tf_s=3600,
        target_tf_s=14400,
    )
    assert derived == []
    assert "derived_incomplete_bucket" in warnings
    assert ext.get("derived_incomplete_count") == 1


def test_cache_key_separates_align():
    """Ключ кешу має розрізняти align та anchor."""
    k1 = ui_server._derived_cache_key("XAU/USD", 14400, "fxcm", 7200000)
    k2 = ui_server._derived_cache_key("XAU/USD", 14400, "tv", 10800000)
    assert k1 != k2


def test_derive_fill_to_limit():
    """Derived H4 має дотягнутись до target_h4 через budget steps."""
    base = _dt.datetime(2026, 2, 12, 23, 0)
    epoch = _dt.datetime(1970, 1, 1)
    h1_all = []
    for i in range(8):
        open_ms = int((base + _dt.timedelta(hours=i) - epoch).total_seconds()) * 1000
        h1_all.append(_make_h1_bar(open_ms, 100 + i, 110 + i, 90 + i, 105 + i, 1))

    calls: list[int] = []

    def _fetch_h1(limit: int):
        calls.append(limit)
        return h1_all[-limit:], []

    derived, warnings, ext, _ = ui_server._derive_h4_tv_with_budget(
        _fetch_h1,
        target_h4=2,
        anchor_remainder_ms=ui_server._TV_H4_ANCHOR_REMAINDER_MS,
        base_tf_s=3600,
        target_tf_s=14400,
        max_steps=3,
        initial_h1_limit=4,
        step_h1_limit=4,
    )
    assert len(derived) == 2
    assert ext.get("derived_target_h4") == 2
    assert ext.get("derived_got_h4") == 2
    assert ext.get("derived_fill_steps") == 2
    assert "derived_insufficient_h1" not in warnings


def test_derive_budget_exhausted():
    """При нестачі H1: warning + degraded_budget_exhausted."""
    base = _dt.datetime(2026, 2, 12, 23, 0)
    epoch = _dt.datetime(1970, 1, 1)
    h1_all = []
    for i in range(3):
        open_ms = int((base + _dt.timedelta(hours=i) - epoch).total_seconds()) * 1000
        h1_all.append(_make_h1_bar(open_ms, 100 + i, 110 + i, 90 + i, 105 + i, 1))

    def _fetch_h1(limit: int):
        return h1_all[-limit:], []

    derived, warnings, ext, _ = ui_server._derive_h4_tv_with_budget(
        _fetch_h1,
        target_h4=1,
        anchor_remainder_ms=ui_server._TV_H4_ANCHOR_REMAINDER_MS,
        base_tf_s=3600,
        target_tf_s=14400,
        max_steps=1,
        initial_h1_limit=3,
        step_h1_limit=4,
    )
    assert derived == []
    assert "derived_insufficient_h1" in warnings
    assert "derived_budget_exhausted" in (ext.get("degraded") or [])


def test_align_tv_meta_source_override():
    """meta.source має бути derived_h1_final і без redis_* полів у top-level."""
    base_meta = {
        "source": "redis_tail",
        "redis_hit": True,
        "redis_len": 123,
        "partial": True,
    }
    warnings: list[str] = []
    derived_ext = {
        "derived_target_h4": 2,
        "derived_got_h4": 2,
    }
    meta = ui_server._make_derived_meta(
        base_meta,
        derived_ext,
        warnings,
        align="tv",
        anchor_remainder_ms=10800000,
        derived_from_tf_s=3600,
    )
    assert meta.get("source") == "derived_h1_final"
    assert "redis_hit" not in meta
    assert "redis_len" not in meta
    ext = meta.get("extensions") or {}
    assert ext.get("base_source") == "redis_tail"
    assert ext.get("base_partial") is True
    assert isinstance(ext.get("base_redis"), dict)


def test_align_tv_has_derived_extensions_on_cache_hit():
    """derived_* мають бути присутні навіть при cache hit (через _make_derived_meta)."""
    warnings: list[str] = []
    derived_ext = {
        "derived_target_h4": 20,
        "derived_got_h4": 19,
        "derived_h1_used_count": 200,
        "derived_fill_steps": 2,
    }
    meta = ui_server._make_derived_meta(
        {},
        derived_ext,
        warnings,
        align="tv",
        anchor_remainder_ms=10800000,
        derived_from_tf_s=3600,
    )
    ext = meta.get("extensions") or {}
    assert ext.get("derived_target_h4") == 20
    assert ext.get("derived_got_h4") == 19
    assert ext.get("derived_h1_used_count") == 200
    assert ext.get("derived_fill_steps") == 2


def test_align_tv_budget_exhausted_sets_degraded_when_got_lt_target():
    """Якщо got<target, має бути degraded_budget_exhausted + warning."""
    warnings: list[str] = []
    derived_ext = {
        "derived_target_h4": 20,
        "derived_got_h4": 18,
    }
    meta = ui_server._make_derived_meta(
        {},
        derived_ext,
        warnings,
        align="tv",
        anchor_remainder_ms=10800000,
        derived_from_tf_s=3600,
    )
    ext = meta.get("extensions") or {}
    assert "derived_budget_exhausted" in (ext.get("degraded") or [])
    assert "derived_insufficient_h1" in warnings


def test_partial_before_calendar_break_emitted():
    """Partial H4 дозволений перед calendar break (gap > tf_ms)."""
    epoch = _dt.datetime(1970, 1, 1)
    # bucket open 2026-02-13 19:00
    b0 = _dt.datetime(2026, 2, 13, 19, 0)
    h1 = []
    for i in range(3):  # 19,20,21 (missing 22)
        open_ms = int((b0 + _dt.timedelta(hours=i) - epoch).total_seconds()) * 1000
        h1.append(_make_h1_bar(open_ms, 100 + i, 110 + i, 90 + i, 105 + i, 1))
    # next bucket far away (calendar break)
    b_next = _dt.datetime(2026, 2, 15, 23, 0)
    open_ms_next = int((b_next - epoch).total_seconds()) * 1000
    h1.append(_make_h1_bar(open_ms_next, 200, 210, 190, 205, 1))

    derived, warnings, ext = ui_server._derive_h4_tv_from_h1(
        h1,
        anchor_remainder_ms=ui_server._TV_H4_ANCHOR_REMAINDER_MS,
        base_tf_s=3600,
        target_tf_s=14400,
    )
    assert derived
    assert derived[0].get("open_time_ms") == int((b0 - epoch).total_seconds()) * 1000
    assert derived[0].get("complete") is False
    assert "derived_partial_bucket" in warnings
    assert ext.get("partial_reason") in ("calendar_break", "calendar_break_no_m5")


def test_no_partial_midweek_incomplete_dropped():
    """Неповний бакет midweek дропаємо (без partial)."""
    epoch = _dt.datetime(1970, 1, 1)
    b0 = _dt.datetime(2026, 2, 13, 19, 0)
    h1 = []
    for i in range(3):  # 19,20,21 (missing 22)
        open_ms = int((b0 + _dt.timedelta(hours=i) - epoch).total_seconds()) * 1000
        h1.append(_make_h1_bar(open_ms, 100 + i, 110 + i, 90 + i, 105 + i, 1))
    # next bucket normal (no gap > tf_ms)
    b_next = _dt.datetime(2026, 2, 13, 23, 0)
    open_ms_next = int((b_next - epoch).total_seconds()) * 1000
    h1.append(_make_h1_bar(open_ms_next, 200, 210, 190, 205, 1))

    derived, warnings, ext = ui_server._derive_h4_tv_from_h1(
        h1,
        anchor_remainder_ms=ui_server._TV_H4_ANCHOR_REMAINDER_MS,
        base_tf_s=3600,
        target_tf_s=14400,
    )
    # partial не має бути, bucket має бути dropped
    opens = [b.get("open_time_ms") for b in derived]
    assert int((b0 - epoch).total_seconds()) * 1000 not in opens
    assert "derived_incomplete_bucket" in warnings
    assert ext.get("derived_partial") is False


def test_refine_uses_m5_when_h1_missing():
    """Якщо H1 неповний, але M5 є — partial має бути з M5."""
    epoch = _dt.datetime(1970, 1, 1)
    b0 = _dt.datetime(2026, 2, 13, 19, 0)
    h1 = []
    for i in range(3):  # 19,20,21
        open_ms = int((b0 + _dt.timedelta(hours=i) - epoch).total_seconds()) * 1000
        h1.append(_make_h1_bar(open_ms, 100 + i, 110 + i, 90 + i, 105 + i, 1))
    b_next = _dt.datetime(2026, 2, 15, 23, 0)
    open_ms_next = int((b_next - epoch).total_seconds()) * 1000
    h1.append(_make_h1_bar(open_ms_next, 200, 210, 190, 205, 1))

    # M5 bars from 19:00 to 21:45 (partial)
    m5 = []
    for i in range(0, 33):
        open_ms = int((b0 + _dt.timedelta(minutes=5 * i) - epoch).total_seconds()) * 1000
        m5.append(_make_m5_bar(open_ms, 200 + i, 205 + i, 195 + i, 202 + i, 1))

    def _fetch_m5_range(start_ms: int, end_ms: int):
        bars = [b for b in m5 if start_ms <= b["open_time_ms"] < end_ms]
        return bars, []

    derived, warnings, ext = ui_server._derive_h4_tv_from_h1(
        h1,
        anchor_remainder_ms=ui_server._TV_H4_ANCHOR_REMAINDER_MS,
        base_tf_s=3600,
        target_tf_s=14400,
        fetch_m5_range=_fetch_m5_range,
    )
    assert derived
    assert derived[0].get("complete") is False
    assert ext.get("derived_refine_base_tf_s") == 300
    assert ext.get("derived_partial") is True
    assert ext.get("partial_reason") == "calendar_break"
