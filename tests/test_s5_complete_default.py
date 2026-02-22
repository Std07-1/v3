"""S5: bar без complete → default False + warning (не True)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_raw_bar(include_complete=True, complete_value=True):
    """Мінімальний raw bar для _normalize_bar_window_v1."""
    bar = {
        "open_time_ms": 1700000000000,
        "close_time_ms": 1700000060000,
        "tf_s": 60,
        "open": 2000.0, "high": 2001.0, "low": 1999.0,
        "close": 2000.5, "volume": 100.0,
        "src": "history",
    }
    if include_complete:
        bar["complete"] = complete_value
    return bar


def test_bar_with_complete_true():
    from ui_chart_v3.server import _normalize_bar_window_v1
    raw = _make_raw_bar(include_complete=True, complete_value=True)
    result = _normalize_bar_window_v1(raw, symbol="XAU_USD", tf_s=60)
    assert result is not None
    assert result["complete"] is True
    assert "_warnings" not in result


def test_bar_with_complete_false():
    from ui_chart_v3.server import _normalize_bar_window_v1
    raw = _make_raw_bar(include_complete=True, complete_value=False)
    result = _normalize_bar_window_v1(raw, symbol="XAU_USD", tf_s=60)
    assert result is not None
    assert result["complete"] is False
    assert "_warnings" not in result


def test_bar_without_complete_defaults_false():
    """S5 core: бар без поля complete → complete=False (safe side)."""
    from ui_chart_v3.server import _normalize_bar_window_v1
    raw = _make_raw_bar(include_complete=False)
    result = _normalize_bar_window_v1(raw, symbol="XAU_USD", tf_s=60)
    assert result is not None
    assert result["complete"] is False, (
        "Expected complete=False for bar without 'complete' field, got %s" % result["complete"]
    )


def test_bar_without_complete_has_warning():
    """S5 loud: бар без complete → _warnings містить MISSING_COMPLETE_FIELD."""
    from ui_chart_v3.server import _normalize_bar_window_v1
    raw = _make_raw_bar(include_complete=False)
    result = _normalize_bar_window_v1(raw, symbol="XAU_USD", tf_s=60)
    assert result is not None
    warnings = result.get("_warnings", [])
    assert any("MISSING_COMPLETE" in w for w in warnings), (
        "Expected MISSING_COMPLETE warning, got %s" % warnings
    )


def test_pop_bar_warnings_removes_transient_field():
    """S5: _pop_bar_warnings видаляє _warnings з барів і агрегує."""
    from ui_chart_v3.server import _pop_bar_warnings
    bars = [
        {"time": 1, "complete": False, "_warnings": ["MISSING_COMPLETE_FIELD_DEFAULTED_FALSE"]},
        {"time": 2, "complete": True},
        {"time": 3, "complete": False, "_warnings": ["MISSING_COMPLETE_FIELD_DEFAULTED_FALSE"]},
    ]
    result = _pop_bar_warnings(bars)
    # _warnings мають бути видалені з барів
    for b in bars:
        assert "_warnings" not in b, "_warnings field should be popped"
    # Мають бути агреговані з count
    assert len(result) == 1
    assert "MISSING_COMPLETE" in result[0]
    assert "count=2" in result[0]


def test_pop_bar_warnings_empty_when_no_warnings():
    """S5: _pop_bar_warnings повертає [] якщо жоден бар не має _warnings."""
    from ui_chart_v3.server import _pop_bar_warnings
    bars = [
        {"time": 1, "complete": True},
        {"time": 2, "complete": True},
    ]
    result = _pop_bar_warnings(bars)
    assert result == []
