"""Тест near-dedup guard у _ensure_sorted_dedup.

Перевіряє що D1 бари з різними anchor (21:00 vs 22:00)
коректно дедуплікуються як "той самий торговий день".
"""
import pytest
from runtime.store.uds import _ensure_sorted_dedup, _get_open_ms


def _make_bar(open_ms, src="history", complete=True):
    return {
        "open_time_ms": open_ms,
        "close_time_ms": open_ms + 86400000,
        "o": 100.0,
        "h": 110.0,
        "low": 90.0,
        "c": 105.0,
        "v": 1000.0,
        "complete": complete,
        "src": src,
        "symbol": "XAU/USD",
        "tf_s": 86400,
    }


class TestNearDedupD1:
    """Near-dedup: anchor jitter для D1 (DST 21:00 vs 22:00)."""

    def test_no_near_dupes_returns_none_geom(self):
        """Бари з нормальним інтервалом (24h) — geom=None."""
        bars = [
            _make_bar(1000000 * 86400),
            _make_bar(1000000 * 86400 + 86400000),
        ]
        result, geom = _ensure_sorted_dedup(bars, tf_ms=86400000)
        assert geom is None
        assert len(result) == 2

    def test_near_dupes_merged_keep_first(self):
        """history@21:00 + derived@22:00 → merged to 1 bar.

        Both are final sources + complete, so _choose_better_bar keeps
        existing (first by sort order = earlier open_ms).
        """
        ot_21 = 1729112400000  # Oct 16 21:00 UTC
        ot_22 = 1729116000000  # Oct 16 22:00 UTC (1h later)
        bars = [
            _make_bar(ot_21, src="history"),
            _make_bar(ot_22, src="derived"),
        ]
        result, geom = _ensure_sorted_dedup(bars, tf_ms=86400000)
        assert geom is not None
        assert geom["dedup_dropped"] == 1
        assert len(result) == 1
        # Both complete+final → existing (earlier) wins
        assert _get_open_ms(result[0]) == ot_21

    def test_near_dupes_reversed_order(self):
        """derived@22:00 + history@21:00 (reversed) → still merged."""
        ot_21 = 1729112400000
        ot_22 = 1729116000000
        bars = [
            _make_bar(ot_22, src="derived"),
            _make_bar(ot_21, src="history"),  # out of order
        ]
        result, geom = _ensure_sorted_dedup(bars, tf_ms=86400000)
        assert geom is not None
        assert len(result) == 1

    def test_multiple_near_dupes_in_sequence(self):
        """Кілька пар near-dupes поспіль."""
        base = 1729000000000
        bars = []
        for i in range(5):
            day_offset = i * 86400000
            bars.append(_make_bar(base + day_offset, src="history"))         # @21:00
            bars.append(_make_bar(base + day_offset + 3600000, src="derived"))  # @22:00
        result, geom = _ensure_sorted_dedup(bars, tf_ms=86400000)
        assert len(result) == 5
        assert geom["dedup_dropped"] == 5

    def test_no_near_dedup_for_small_tf(self):
        """Для M1/M5 near-dedup не активний (tf_ms < 86400000)."""
        bars = [
            {"open_time_ms": 1000, "src": "history", "complete": True},
            {"open_time_ms": 1060, "src": "derived", "complete": True},
        ]
        result, geom = _ensure_sorted_dedup(bars, tf_ms=60000)
        # Ці бари out of order — geom буде, але near_threshold=0
        # Вони sorted, різні open_ms → обидва залишаться
        assert len(result) == 2

    def test_no_near_dedup_without_tf_ms(self):
        """Без tf_ms — legacy поведінка, near-dedup вимкнений."""
        ot_21 = 1729112400000
        ot_22 = 1729116000000
        bars = [
            _make_bar(ot_21, src="history"),
            _make_bar(ot_22, src="derived"),
        ]
        result, geom = _ensure_sorted_dedup(bars)
        assert len(result) == 2  # Not merged without tf_ms
        assert geom is None  # Already sorted, no exact dupes

    def test_exact_dedup_still_works(self):
        """Exact dedup (same open_time_ms) все ще працює."""
        ot = 1729116000000
        bars = [
            _make_bar(ot, src="history"),
            _make_bar(ot, src="derived"),
        ]
        result, geom = _ensure_sorted_dedup(bars, tf_ms=86400000)
        assert len(result) == 1
        assert geom["dedup_dropped"] >= 1
