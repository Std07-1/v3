"""Unit tests for rotation-aware thinking-archive reader (C1).

Covers the pure helper ``runtime.ws.ws_server._read_thinking_records`` that
backs ``GET /api/archi/thinking``. The single-file reader broke on 2026-07-07
when the archive first rotated (5 MB -> ``v3_thinking_archive_*.jsonl``): the
UI showed only the 2 records in the fresh live file instead of months of
history. The helper now reads live + rotated files, newest-first, lazily.

All paths are pure (tmp-dir fixtures, no aiohttp / Redis needed).
"""
from __future__ import annotations

import json
from pathlib import Path

from runtime.ws.ws_server import _read_thinking_records


def _write_jsonl(path: Path, records: list[dict]) -> None:
    """Append-only JSONL, oldest->newest, matching the writer's format."""
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _seed_three_files(data_dir: Path) -> None:
    """Live (freshest) + 2 rotated files with known, ordered records.

    Newest-first across all files is expected to be:
        live_1, live_0, r2_1, r2_0, r1_1, r1_0
    (live leads; rotated sorted by name desc == chronological desc; within a
    file records reverse from append order.)
    """
    # Oldest rotation (earliest UTC suffix).
    _write_jsonl(
        data_dir / "v3_thinking_archive_20260707_100000.jsonl",
        [{"ts": 1, "id": "r1_0"}, {"ts": 2, "id": "r1_1"}],
    )
    # Newer rotation (later UTC suffix -> sorts after r1).
    _write_jsonl(
        data_dir / "v3_thinking_archive_20260707_144546.jsonl",
        [{"ts": 3, "id": "r2_0"}, {"ts": 4, "id": "r2_1"}],
    )
    # Live file = freshest, still being written.
    _write_jsonl(
        data_dir / "v3_thinking_archive.jsonl",
        [{"ts": 5, "id": "live_0"}, {"ts": 6, "id": "live_1"}],
    )


_EXPECTED_ORDER = ["live_1", "live_0", "r2_1", "r2_0", "r1_1", "r1_0"]


def test_newest_first_order_spans_all_files(tmp_path: Path) -> None:
    _seed_three_files(tmp_path)
    page, total = _read_thinking_records(str(tmp_path), limit=100, offset=0)
    assert [r["id"] for r in page] == _EXPECTED_ORDER
    assert total == 6


def test_limit_only_reads_live_when_page_fits_head(tmp_path: Path) -> None:
    _seed_three_files(tmp_path)
    # limit=2, offset=0 -> page satisfied by the live file alone.
    page, total = _read_thinking_records(str(tmp_path), limit=2, offset=0)
    assert [r["id"] for r in page] == ["live_1", "live_0"]
    # total still counts every file (lazy page, eager count).
    assert total == 6


def test_offset_crossing_file_boundary(tmp_path: Path) -> None:
    _seed_three_files(tmp_path)
    # offset=1, limit=2 straddles live->rotated boundary.
    page, total = _read_thinking_records(str(tmp_path), limit=2, offset=1)
    assert [r["id"] for r in page] == ["live_0", "r2_1"]
    assert total == 6
    # offset=3, limit=2 lands fully inside rotated files.
    page2, _ = _read_thinking_records(str(tmp_path), limit=2, offset=3)
    assert [r["id"] for r in page2] == ["r2_0", "r1_1"]


def test_total_counts_across_all_files(tmp_path: Path) -> None:
    _seed_three_files(tmp_path)
    _, total = _read_thinking_records(str(tmp_path), limit=1, offset=0)
    assert total == 6


def test_corrupt_line_is_skipped_not_crashed(tmp_path: Path) -> None:
    # Live file with one broken JSON line between two valid ones.
    live = tmp_path / "v3_thinking_archive.jsonl"
    with open(live, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": 1, "id": "a"}) + "\n")
        fh.write("{ this is not json\n")
        fh.write(json.dumps({"ts": 3, "id": "c"}) + "\n")
    page, total = _read_thinking_records(str(tmp_path), limit=100, offset=0)
    # Newest-first, corrupt line dropped from page.
    assert [r["id"] for r in page] == ["c", "a"]


def test_empty_dir_returns_empty(tmp_path: Path) -> None:
    page, total = _read_thinking_records(str(tmp_path), limit=50, offset=0)
    assert page == []
    assert total == 0


def test_rotated_only_no_live_file(tmp_path: Path) -> None:
    # Live file absent (edge: just rotated, nothing written yet).
    _write_jsonl(
        tmp_path / "v3_thinking_archive_20260707_144546.jsonl",
        [{"ts": 3, "id": "r2_0"}, {"ts": 4, "id": "r2_1"}],
    )
    page, total = _read_thinking_records(str(tmp_path), limit=50, offset=0)
    assert [r["id"] for r in page] == ["r2_1", "r2_0"]
    assert total == 2
