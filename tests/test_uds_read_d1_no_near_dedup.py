"""Читач UDS не зливає D1 з різними open_ms (ADR-0095 §3.3, рішення власника 23.09.2026).

Колишній near-dedup у `_ensure_sorted_dedup` зливав D1, ближчі за 2 год («DST-джитер 21:00/22:00»), і лишав один.
Після валідатора рівності в писарі SSOT це стала тиха маска: D1 поза сезонною сіткою (13 D1 XAU жовтня 2025 на 22:00)
зникав на читанні, а `dedup_dropped` рахував його як звичайний дублікат. Тепер читач тупий: видно обидва бари, а бар
поза сіткою ловлять писар (`bar_off_season_grid`) і health `off_season_grid`.
"""
from __future__ import annotations

import datetime as dt
import json

from runtime.store.uds import ReadPolicy, UnifiedDataStore, WindowSpec, _ensure_sorted_dedup

D1_MS = 86_400_000


def _utc_ms(*args: int) -> int:
    return int(dt.datetime(*args, tzinfo=dt.timezone.utc).timestamp() * 1000)


# Ср 15.10.2025 — літо США (до 02.11): D1 відкривається о 21:00 UTC, 22:00 — зимова сітка, тобто поза сезоном
SUMMER_D1 = _utc_ms(2025, 10, 15, 21)
OFF_GRID_D1 = _utc_ms(2025, 10, 15, 22)


def _d1(open_ms: int, src: str = "history") -> dict:
    return {"symbol": "XAU/USD", "tf_s": 86400, "open_time_ms": open_ms, "close_time_ms": open_ms + D1_MS,
            "o": 100.0, "h": 110.0, "low": 90.0, "c": 105.0, "v": 1000.0, "complete": True, "src": src}


def test_sorted_d1_pair_of_one_day_is_returned_as_is():
    result, geom = _ensure_sorted_dedup([_d1(SUMMER_D1), _d1(OFF_GRID_D1, src="derived")])
    assert [b["open_time_ms"] for b in result] == [SUMMER_D1, OFF_GRID_D1]
    assert geom is None


def test_reversed_d1_pair_is_sorted_not_merged():
    result, geom = _ensure_sorted_dedup([_d1(OFF_GRID_D1, src="derived"), _d1(SUMMER_D1)])
    assert [b["open_time_ms"] for b in result] == [SUMMER_D1, OFF_GRID_D1]
    assert geom == {"sorted": True, "dedup_dropped": 0}


def test_exact_duplicate_key_is_still_deduped():
    """Дедуп записів ОДНОГО ключа (ADR-0094) лишається: history і derived того самого open_ms — одна свічка."""
    result, geom = _ensure_sorted_dedup([_d1(SUMMER_D1), _d1(SUMMER_D1, src="derived")])
    assert len(result) == 1
    assert geom == {"sorted": True, "dedup_dropped": 1}


def test_uds_range_read_from_disk_shows_off_grid_d1_next_to_summer_d1(tmp_path):
    """Живий шлях scrollback (disk range): бар 22:00 не зливається з 21:00 і лишається видимим."""
    tf_dir = tmp_path / "XAU_USD" / "tf_86400"
    tf_dir.mkdir(parents=True)
    rows = [_d1(SUMMER_D1), _d1(OFF_GRID_D1, src="derived")]
    (tf_dir / "part-20251015.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    uds = UnifiedDataStore(data_root=str(tmp_path), boot_id="test-boot", tf_allowlist={86400},
                           min_coldload_bars={86400: 1}, role="reader")

    result = uds.read_window(WindowSpec("XAU/USD", 86400, 10, to_open_ms=OFF_GRID_D1), ReadPolicy(force_disk=True))

    assert [b["open_time_ms"] for b in result.bars_lwc] == [SUMMER_D1, OFF_GRID_D1]
    assert "geom_non_monotonic" not in result.warnings
