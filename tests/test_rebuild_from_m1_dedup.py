"""ADR-0054 P0.2 — dedup-on-finish по фактичному діапазону rebuild (а не по --start)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.rebuild_from_m1 import dedup_derived_in_ranges

DAY_MS = 86_400_000


def _part(root: Path, symbol: str, tf_s: int, day: str, opens: list[int]) -> Path:
    d = root / symbol.replace("/", "_") / f"tf_{tf_s}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"part-{day}.jsonl"
    p.write_text(
        "".join(json.dumps({"open_time_ms": o, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}) + "\n" for o in opens),
        encoding="utf-8",
    )
    return p


def _opens(p: Path) -> list[int]:
    return [json.loads(ln)["open_time_ms"] for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_duplicates_removed_when_start_was_not_given(tmp_path):
    """Регресія: раніше `--force` без `--start` давав dupes_removed=0 і не відкривав жодного файлу."""
    day_ms = 1_700_000_000_000 // DAY_MS * DAY_MS
    day = "19700101"
    import datetime as dt

    day = dt.datetime.fromtimestamp(day_ms / 1000, dt.timezone.utc).strftime("%Y%m%d")
    p = _part(tmp_path, "XAU/USD", 300, day, [day_ms, day_ms + 300_000, day_ms + 300_000])
    removed = dedup_derived_in_ranges(str(tmp_path), {"XAU/USD": (day_ms, day_ms + DAY_MS)})
    assert removed == 1
    assert _opens(p) == [day_ms, day_ms + 300_000]


def test_symbol_without_rebuild_range_is_untouched(tmp_path):
    """Пропущений під час rebuild символ не дедуплікується — і це видно в логах, не мовчки."""
    import datetime as dt

    day_ms = 1_700_000_000_000 // DAY_MS * DAY_MS
    day = dt.datetime.fromtimestamp(day_ms / 1000, dt.timezone.utc).strftime("%Y%m%d")
    p = _part(tmp_path, "XAG/USD", 300, day, [day_ms, day_ms])
    removed = dedup_derived_in_ranges(str(tmp_path), {})
    assert removed == 0
    assert _opens(p) == [day_ms, day_ms], "файл поза діапазоном не чіпаємо"


def test_m1_files_are_never_touched(tmp_path):
    """Дедупимо лише derived: M1 — джерело істини rebuild."""
    import datetime as dt

    day_ms = 1_700_000_000_000 // DAY_MS * DAY_MS
    day = dt.datetime.fromtimestamp(day_ms / 1000, dt.timezone.utc).strftime("%Y%m%d")
    m1 = _part(tmp_path, "XAU/USD", 60, day, [day_ms, day_ms])
    dedup_derived_in_ranges(str(tmp_path), {"XAU/USD": (day_ms, day_ms + DAY_MS)})
    assert _opens(m1) == [day_ms, day_ms]


@pytest.mark.parametrize("rng", [(0, 100), (100, 100), (200, 100)])
def test_invalid_range_fails_loud(tmp_path, rng):
    """Нульовий/інвертований діапазон = помилка виклику, не тихий no-op (I5)."""
    with pytest.raises(ValueError, match="dedup range"):
        dedup_derived_in_ranges(str(tmp_path), {"XAU/USD": rng})
