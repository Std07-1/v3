"""tests/test_htf_tail_sync.py

Тести для tools/repair/htf_tail_sync_from_fxcm.py.
Фокус: validate_batch, merge_dedup_last_wins, validate_monotonic,
       bar_summary, sync_one (dry-run), report content.
"""
from __future__ import annotations

import dataclasses
import json
import os
import tempfile

import pytest

from core.model.bars import CandleBar
from tools.repair.htf_tail_sync_from_fxcm import (
    validate_batch,
    merge_dedup_last_wins,
    validate_monotonic,
    bar_summary,
    _write_json_report,
)


# ─── Helpers ─────────────────────────────────────────────────────

def _make_bar(open_ms: int, tf_s: int = 14400, complete: bool = True,
              src: str = "history", symbol: str = "XAU/USD") -> CandleBar:
    return CandleBar(
        symbol=symbol,
        tf_s=tf_s,
        open_time_ms=open_ms,
        close_time_ms=open_ms + tf_s * 1000,
        o=1.0,
        h=2.0,
        low=0.5,
        c=1.5,
        v=100.0,
        complete=complete,
        src=src,
    )


# ─── Tests ───────────────────────────────────────────────────────


class TestValidateBatch:
    """validate_batch: перевірка batch CandleBar-ів."""

    def test_valid_batch_no_errors(self):
        bars = [_make_bar(1000), _make_bar(2000), _make_bar(3000)]
        errs = validate_batch(bars, 14400)
        assert errs == []

    def test_empty_batch(self):
        errs = validate_batch([], 14400)
        assert errs == []

    def test_reject_non_monotonic(self):
        bars = [_make_bar(3000), _make_bar(2000), _make_bar(1000)]
        errs = validate_batch(bars, 14400)
        assert len(errs) >= 1
        assert any("non-monotonic" in e for e in errs)

    def test_reject_duplicate_open(self):
        bars = [_make_bar(1000), _make_bar(1000)]
        errs = validate_batch(bars, 14400)
        assert any("duplicate" in e or "non-monotonic" in e for e in errs)

    def test_reject_incomplete(self):
        bars = [_make_bar(1000, complete=False)]
        errs = validate_batch(bars, 14400)
        assert any("complete=False" in e for e in errs)

    def test_reject_non_history_src(self):
        bars = [_make_bar(1000, src="derived")]
        errs = validate_batch(bars, 14400)
        assert any("src=" in e for e in errs)


class TestMergeDedup:
    """merge_dedup_last_wins: existing + incoming → merged sorted deduped."""

    def test_no_overlap(self):
        existing = [
            {"open_time_ms": 1000, "c": 1.1},
            {"open_time_ms": 2000, "c": 1.2},
        ]
        incoming = [
            {"open_time_ms": 3000, "c": 1.3},
        ]
        result = merge_dedup_last_wins(existing, incoming, 3000, 3000)
        assert len(result) == 3
        assert result[0]["open_time_ms"] == 1000
        assert result[2]["open_time_ms"] == 3000

    def test_overlap_last_wins(self):
        existing = [
            {"open_time_ms": 1000, "c": 1.1},
            {"open_time_ms": 2000, "c": 1.2},
        ]
        incoming = [
            {"open_time_ms": 2000, "c": 9.9},
        ]
        result = merge_dedup_last_wins(existing, incoming, 2000, 2000)
        assert len(result) == 2
        # incoming wins for dup key
        bar_2000 = [b for b in result if b["open_time_ms"] == 2000][0]
        assert bar_2000["c"] == 9.9

    def test_range_replace(self):
        existing = [
            {"open_time_ms": 1000, "c": 1.0},
            {"open_time_ms": 2000, "c": 2.0},
            {"open_time_ms": 3000, "c": 3.0},
            {"open_time_ms": 4000, "c": 4.0},
        ]
        incoming = [
            {"open_time_ms": 2000, "c": 22.0},
            {"open_time_ms": 3000, "c": 33.0},
        ]
        result = merge_dedup_last_wins(existing, incoming, 2000, 3000)
        assert len(result) == 4
        assert result[1]["c"] == 22.0
        assert result[2]["c"] == 33.0
        assert result[0]["c"] == 1.0
        assert result[3]["c"] == 4.0


class TestValidateMonotonic:
    """validate_monotonic: open_time_ms строго зростає."""

    def test_sorted(self):
        bars = [{"open_time_ms": 100}, {"open_time_ms": 200}, {"open_time_ms": 300}]
        assert validate_monotonic(bars) is True

    def test_not_sorted(self):
        bars = [{"open_time_ms": 300}, {"open_time_ms": 100}]
        assert validate_monotonic(bars) is False

    def test_empty(self):
        assert validate_monotonic([]) is True


class TestBarSummary:
    """bar_summary: count + first/last UTC strings."""

    def test_candlebar_objects(self):
        bars = [_make_bar(1000000000), _make_bar(2000000000)]
        s = bar_summary(bars)
        assert s["count"] == 2
        assert s["first_open_ms"] == 1000000000
        assert s["last_open_ms"] == 2000000000
        assert "first_utc" in s
        assert "last_utc" in s

    def test_empty(self):
        s = bar_summary([])
        assert s["count"] == 0

    def test_dict_bars(self):
        bars = [{"open_time_ms": 5000000000}, {"open_time_ms": 6000000000}]
        s = bar_summary(bars)
        assert s["count"] == 2
        assert s["first_open_ms"] == 5000000000


class TestReportOutput:
    """_write_json_report: звіт містить per-tf summary."""

    def test_report_contains_per_tf_summary(self):
        report = {
            "tool": "htf_tail_sync_from_fxcm",
            "mode": "DRY-RUN",
            "ts_utc": "2025-01-01T00:00:00Z",
            "symbols": ["XAU/USD"],
            "tfs": [14400, 86400],
            "results": [
                {"symbol": "XAU/USD", "tf_s": 14400, "status": "dry_run", "fetched": 8},
                {"symbol": "XAU/USD", "tf_s": 86400, "status": "dry_run", "fetched": 5},
            ],
            "totals": {
                "fetched": 13, "committed": 0,
                "validation_errors": 0, "fetch_errors": 0,
                "rewrite_errors": 0, "unexpected_errors": 0,
            },
        }
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "report.json")
            _write_json_report(report, path)
            with open(path, "r") as f:
                loaded = json.load(f)
            assert loaded["tool"] == "htf_tail_sync_from_fxcm"
            assert len(loaded["results"]) == 2
            assert loaded["results"][0]["tf_s"] == 14400
            assert loaded["results"][1]["tf_s"] == 86400
            assert loaded["totals"]["fetched"] == 13
            # Нові поля тоталів
            assert "rewrite_errors" in loaded["totals"]
            assert "unexpected_errors" in loaded["totals"]
