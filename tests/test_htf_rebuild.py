"""tests/test_htf_rebuild.py

Тести для tools/repair/htf_rebuild_from_fxcm.py.
Перевіряє safety rails: валідація batch, summary, rewrite-range.
"""
from __future__ import annotations

import json
import os
import tempfile

from core.model.bars import CandleBar
from tools.repair.htf_rebuild_from_fxcm import (
    _validate_batch,
    _bar_summary,
    rewrite_range,
    _read_all_bars_raw,
)


def _make_bar(
    open_ms: int,
    tf_s: int = 14400,
    complete: bool = True,
    src: str = "history",
) -> CandleBar:
    return CandleBar(
        symbol="XAU/USD",
        tf_s=tf_s,
        open_time_ms=open_ms,
        close_time_ms=open_ms + tf_s * 1000,
        o=1900.0,
        h=1910.0,
        low=1895.0,
        c=1905.0,
        v=500,
        complete=complete,
        src=src,
    )


class TestValidateBatch:
    """Тести _validate_batch."""

    def test_empty_batch(self) -> None:
        assert _validate_batch([], 14400) == []

    def test_valid_batch(self) -> None:
        tf_ms = 14400 * 1000
        bars = [
            _make_bar(100 * tf_ms),
            _make_bar(101 * tf_ms),
            _make_bar(102 * tf_ms),
        ]
        errors = _validate_batch(bars, 14400)
        assert errors == []

    def test_non_monotonic(self) -> None:
        tf_ms = 14400 * 1000
        bars = [
            _make_bar(102 * tf_ms),
            _make_bar(101 * tf_ms),  # зворотний порядок
        ]
        errors = _validate_batch(bars, 14400)
        assert len(errors) == 1
        assert "non-monotonic" in errors[0]

    def test_duplicate_open(self) -> None:
        tf_ms = 14400 * 1000
        bars = [
            _make_bar(100 * tf_ms),
            _make_bar(100 * tf_ms),  # дублікат
        ]
        errors = _validate_batch(bars, 14400)
        # non-monotonic + duplicate
        assert any("duplicate" in e for e in errors)

    def test_incomplete_bar(self) -> None:
        tf_ms = 14400 * 1000
        bars = [_make_bar(100 * tf_ms, complete=False)]
        errors = _validate_batch(bars, 14400)
        assert len(errors) == 1
        assert "complete=False" in errors[0]

    def test_wrong_src(self) -> None:
        tf_ms = 14400 * 1000
        bars = [_make_bar(100 * tf_ms, src="derived")]
        errors = _validate_batch(bars, 14400)
        assert len(errors) == 1
        assert "src='derived'" in errors[0]

    def test_d1_valid(self) -> None:
        """D1 бар (tf_s=86400) з правильними параметрами."""
        tf_s = 86400
        tf_ms = tf_s * 1000
        bars = [
            _make_bar(10 * tf_ms, tf_s=tf_s),
            _make_bar(11 * tf_ms, tf_s=tf_s),
        ]
        errors = _validate_batch(bars, tf_s)
        assert errors == []


class TestBarSummary:
    """Тести _bar_summary."""

    def test_empty(self) -> None:
        s = _bar_summary([])
        assert s == {"count": 0}

    def test_single_bar(self) -> None:
        bar = _make_bar(1738800000000)  # ~2025-02-06T02:00:00Z
        s = _bar_summary([bar])
        assert s["count"] == 1
        assert s["first_open_ms"] == 1738800000000
        assert s["last_open_ms"] == 1738800000000
        assert "first_utc" in s
        assert "last_utc" in s

    def test_multi_bars(self) -> None:
        tf_ms = 14400 * 1000
        bars = [
            _make_bar(100 * tf_ms),
            _make_bar(101 * tf_ms),
            _make_bar(102 * tf_ms),
        ]
        s = _bar_summary(bars)
        assert s["count"] == 3
        assert s["first_open_ms"] == 100 * tf_ms
        assert s["last_open_ms"] == 102 * tf_ms


# ─── Helpers для rewrite-range тестів ─────────────────────────────


def _write_jsonl_bars(tf_dir: str, bars_by_day: dict) -> None:
    """Записати бари у part-YYYYMMDD.jsonl файли для тесту."""
    os.makedirs(tf_dir, exist_ok=True)
    for day_str, bars in bars_by_day.items():
        path = os.path.join(tf_dir, "part-%s.jsonl" % day_str)
        with open(path, "w", encoding="utf-8") as f:
            for b in bars:
                f.write(json.dumps(b, ensure_ascii=False, separators=(",", ":")) + "\n")


def _raw_bar(open_ms: int, tf_s: int = 14400, o: float = 1900.0) -> dict:
    """Створити raw dict бар для запису в JSONL."""
    return {
        "symbol": "XAU/USD",
        "tf_s": tf_s,
        "open_time_ms": open_ms,
        "close_time_ms": open_ms + tf_s * 1000,
        "o": o,
        "h": o + 10,
        "low": o - 5,
        "c": o + 5,
        "v": 500,
        "complete": True,
        "src": "history",
    }


class TestRewriteRange:
    """Тести для rewrite_range()."""

    def test_rewrite_range_replaces_mixed_anchor(self) -> None:
        """Бари з remainder=0 і remainder=2h у діапазоні замінюються на FXCM бари."""
        tf_s = 14400
        tf_ms = tf_s * 1000

        with tempfile.TemporaryDirectory() as tmpdir:
            data_root = tmpdir
            tf_dir = os.path.join(tmpdir, "XAU_USD", "tf_14400")

            # "Старі" бари: мікс anchor (remainder=0 і remainder=2h offset)
            # remainder=0: open_ms кратний tf_ms
            bar_r0_a = _raw_bar(100 * tf_ms, o=1800.0)  # remainder=0 (старий)
            bar_r0_b = _raw_bar(101 * tf_ms, o=1810.0)  # remainder=0 (старий)
            # remainder=2h: відкритий з офсетом 7200_000ms
            bar_r2h = _raw_bar(100 * tf_ms + 7200000, o=1805.0)
            # Бар поза діапазоном (мусить зберегтися)
            bar_outside = _raw_bar(50 * tf_ms, o=1700.0)

            # Записуємо у JSONL (дата для всіх — один "part" файл)
            _write_jsonl_bars(tf_dir, {
                "19700101": [bar_outside],
                "19700617": [bar_r0_a, bar_r2h, bar_r0_b],
            })

            # FXCM batch — "правильні" бари (remainder=2h)
            fxcm_bars = [
                _make_bar(100 * tf_ms + 7200000, tf_s=tf_s),
                _make_bar(101 * tf_ms + 7200000, tf_s=tf_s),
            ]

            from_ms = fxcm_bars[0].open_time_ms
            to_ms = fxcm_bars[-1].open_time_ms

            result = rewrite_range(
                data_root=data_root,
                symbol="XAU/USD",
                tf_s=tf_s,
                fxcm_bars=fxcm_bars,
                from_open_ms=from_ms,
                to_open_ms=to_ms,
                dry_run=False,
            )

            assert result["status"] == "committed"
            assert result["monotonic"] is True
            assert result["fxcm_inserted"] == 2

            # Перечитати — перевірити що anchor-mix зник
            all_bars = _read_all_bars_raw(tf_dir)
            opens_in_range = [
                b["open_time_ms"] for b in all_bars
                if from_ms <= b["open_time_ms"] <= to_ms
            ]
            # У діапазоні мусять бути тільки FXCM бари
            assert len(opens_in_range) == 2
            for ot in opens_in_range:
                assert (ot - 7200000) % tf_ms == 0, "Тільки remainder=2h бари мусять залишитись"

    def test_rewrite_range_keeps_outside_window(self) -> None:
        """Бари поза діапазоном зберігаються 1:1."""
        tf_s = 14400
        tf_ms = tf_s * 1000

        with tempfile.TemporaryDirectory() as tmpdir:
            data_root = tmpdir
            tf_dir = os.path.join(tmpdir, "XAU_USD", "tf_14400")

            # Бари: 3 у діапазоні, 2 поза (до і після)
            bar_before = _raw_bar(10 * tf_ms, o=1600.0)
            bar_in_1 = _raw_bar(100 * tf_ms, o=1800.0)
            bar_in_2 = _raw_bar(101 * tf_ms, o=1810.0)
            bar_in_3 = _raw_bar(102 * tf_ms, o=1820.0)
            bar_after = _raw_bar(200 * tf_ms, o=1900.0)

            _write_jsonl_bars(tf_dir, {
                "19700101": [bar_before, bar_in_1, bar_in_2, bar_in_3, bar_after],
            })

            # FXCM — заміна для діапазону 100..102
            fxcm_bars = [
                _make_bar(100 * tf_ms, tf_s=tf_s),
                _make_bar(101 * tf_ms, tf_s=tf_s),
            ]
            from_ms = 100 * tf_ms
            to_ms = 102 * tf_ms

            result = rewrite_range(
                data_root=data_root,
                symbol="XAU/USD",
                tf_s=tf_s,
                fxcm_bars=fxcm_bars,
                from_open_ms=from_ms,
                to_open_ms=to_ms,
                dry_run=False,
            )

            assert result["status"] == "committed"
            assert result["kept_outside"] == 2  # bar_before + bar_after
            assert result["removed_in_range"] == 3  # bar_in_1..3

            all_bars = _read_all_bars_raw(tf_dir)
            outside_opens = [
                b["open_time_ms"] for b in all_bars
                if b["open_time_ms"] < from_ms or b["open_time_ms"] > to_ms
            ]
            assert 10 * tf_ms in outside_opens, "bar_before мусить зберегтися"
            assert 200 * tf_ms in outside_opens, "bar_after мусить зберегтися"

    def test_atomic_replace_creates_valid_file(self) -> None:
        """Після rewrite part-файл є валідним JSONL (кожний рядок — valid JSON)."""
        tf_s = 14400
        tf_ms = tf_s * 1000

        with tempfile.TemporaryDirectory() as tmpdir:
            data_root = tmpdir
            tf_dir = os.path.join(tmpdir, "XAU_USD", "tf_14400")

            bar1 = _raw_bar(100 * tf_ms)
            _write_jsonl_bars(tf_dir, {"19700617": [bar1]})

            fxcm_bars = [_make_bar(100 * tf_ms, tf_s=tf_s)]

            result = rewrite_range(
                data_root=data_root,
                symbol="XAU/USD",
                tf_s=tf_s,
                fxcm_bars=fxcm_bars,
                from_open_ms=100 * tf_ms,
                to_open_ms=100 * tf_ms,
                dry_run=False,
            )

            assert result["status"] == "committed"

            # Перевірити що part-файл є валідним JSONL
            import glob as globmod
            parts = sorted(globmod.glob(os.path.join(tf_dir, "part-*.jsonl")))
            assert len(parts) >= 1
            for part_path in parts:
                with open(part_path, "r", encoding="utf-8") as f:
                    for line_no, line in enumerate(f, 1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            assert "open_time_ms" in obj, (
                                "Рядок %d у %s не має open_time_ms" % (line_no, part_path)
                            )
                        except json.JSONDecodeError:
                            raise AssertionError(
                                "Невалідний JSON у рядку %d: %s" % (line_no, part_path)
                            )

    def test_rewrite_dry_run_no_changes(self) -> None:
        """dry_run=True не змінює файли."""
        tf_s = 14400
        tf_ms = tf_s * 1000

        with tempfile.TemporaryDirectory() as tmpdir:
            data_root = tmpdir
            tf_dir = os.path.join(tmpdir, "XAU_USD", "tf_14400")

            bar1 = _raw_bar(100 * tf_ms, o=1800.0)
            _write_jsonl_bars(tf_dir, {"19700617": [bar1]})

            # Зберегти оригінальний вміст
            orig_path = os.path.join(tf_dir, "part-19700617.jsonl")
            with open(orig_path, "r") as f:
                orig_content = f.read()

            fxcm_bars = [_make_bar(100 * tf_ms, tf_s=tf_s)]

            result = rewrite_range(
                data_root=data_root,
                symbol="XAU/USD",
                tf_s=tf_s,
                fxcm_bars=fxcm_bars,
                from_open_ms=100 * tf_ms,
                to_open_ms=100 * tf_ms,
                dry_run=True,
            )

            assert result["status"] == "dry_run"

            # Файл не змінився
            with open(orig_path, "r") as f:
                assert f.read() == orig_content
