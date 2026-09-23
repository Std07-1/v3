"""Читач UDS не зливає D1 з різними open_ms (ADR-0095 §3.3, рішення власника 23.09.2026).

Колишній near-dedup у `_ensure_sorted_dedup` зливав D1, ближчі за 2 год («DST-джитер 21:00/22:00»), і лишав один.
Після валідатора рівності в писарі SSOT це стала тиха маска: D1 поза сезонною сіткою (13 D1 XAU жовтня 2025 на 22:00)
зникав на читанні, а `dedup_dropped` рахував його як звичайний дублікат. Тепер читач тупий: видно обидва бари, а бар
поза сіткою ловлять писар (`bar_off_season_grid`) і health `off_season_grid`.

Тести шляхів ідуть через `UnifiedDataStore.read_window` тим викликом, яким читають UI і SMC (ws_server
`_uds_read_window`, smc_runner, /api/bars: `cold_load=True`, `ReadPolicy(disk_policy="explicit", prefer_redis=True)`):
Redis-хвіст, праймлений з диска і доповнений живим commit; хвіст диска, коли Redis-хвоста нема; RAM-вікно, злите з
шиною оновлень. Кожен із них падає на коді до S3b (near-dedup зливав пару на всіх цих шляхах, W1fix-доказ).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Optional

import pytest
from preview_ring_fake import PreviewRingFakeRedis

from core.config_loader import htf_anchor_rule_resolver
from core.model.bars import CandleBar
from runtime.store.layers.redis_layer import RedisLayer
from runtime.store.redis_snapshot import RedisSnapshotWriter
from runtime.store.ssot_jsonl import JsonlAppender
from runtime.store.uds import (
    ReadPolicy,
    UnifiedDataStore,
    UpdatesSpec,
    WindowSpec,
    _RedisUpdatesBus,
    _ensure_sorted_dedup,
)

SYM = "XAU/USD"
D1_S = 86_400
D1_MS = D1_S * 1000
NS = "t_w1fix"
# Політика читання UI/SMC: ws_server._uds_read_window, smc_runner._read_bars, api_v3._read_bars_window_atomic
UI_POLICY = ReadPolicy(disk_policy="explicit", prefer_redis=True)
_CFG = {
    "htf_anchor": {"rule_by_calendar_group": {"cfd_us_22_23": "ny_close_us_dst"}},
    "market_calendar_symbol_groups": {SYM: "cfd_us_22_23"},
}


def _utc_ms(*args: int) -> int:
    return int(dt.datetime(*args, tzinfo=dt.timezone.utc).timestamp() * 1000)


# Ср 15.10.2025 — літо США (до 02.11): D1 відкривається о 21:00 UTC, 22:00 — зимова сітка, тобто поза сезоном
PREV_D1 = _utc_ms(2025, 10, 14, 21)
SUMMER_D1 = _utc_ms(2025, 10, 15, 21)
OFF_GRID_D1 = _utc_ms(2025, 10, 15, 22)
NEXT_D1 = _utc_ms(2025, 10, 16, 21)


def _d1(open_ms: int, src: str = "history", **extra) -> dict:
    row = {"symbol": SYM, "tf_s": D1_S, "open_time_ms": open_ms, "close_time_ms": open_ms + D1_MS,
           "o": 100.0, "h": 110.0, "low": 90.0, "c": 105.0, "v": 1000.0, "complete": True, "src": src}
    row.update(extra)
    return row


def _write_disk(root: Path, rows: list[dict]) -> None:
    """Рядки в part-файли за UTC-добою open (інваріант імені part-файла)."""
    tf_dir = root / "XAU_USD" / "tf_86400"
    tf_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        day = dt.datetime.fromtimestamp(row["open_time_ms"] / 1000, tz=dt.timezone.utc).strftime("%Y%m%d")
        with open(tf_dir / ("part-%s.jsonl" % day), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")


class _TtlFakeRedis(PreviewRingFakeRedis):
    """PreviewRingFakeRedis + TTL ключа: читач UDS відкидає Redis-хвіст із ttl ≤ 0 (redis_ttl_invalid)."""

    def __init__(self) -> None:
        super().__init__()
        self.ex: dict[str, int] = {}

    def set(self, key: str, value, ex: Optional[int] = None) -> bool:
        if ex:
            self.ex[key] = int(ex)
        return super().set(key, value, ex=ex)

    def ttl(self, key: str) -> int:
        if key not in self.kv:
            return -2
        return self.ex.get(key, -1)


def _writer(root: Path, redis: _TtlFakeRedis) -> UnifiedDataStore:
    """Писар як у проді: справжній JsonlAppender із сезонною сіткою, Redis-снапшот і шина оновлень на тому ж Redis."""
    return UnifiedDataStore(
        data_root=str(root), boot_id="writer", tf_allowlist={D1_S}, min_coldload_bars={D1_S: 1}, role="writer",
        jsonl_appender=JsonlAppender(str(root), anchor_rule_for_symbol=htf_anchor_rule_resolver(_CFG)),
        redis_snapshot_writer=RedisSnapshotWriter(redis, NS, {D1_S: 3600}, {D1_S: 100}, "writer"),
        updates_bus=_RedisUpdatesBus(redis, NS, 100),
    )


def _reader(root: Path, redis: Optional[_TtlFakeRedis]) -> UnifiedDataStore:
    """Читач UI/SMC (ws_server, smc_runner): Redis-шар і шина над тим самим Redis, диск, RAM."""
    return UnifiedDataStore(
        data_root=str(root), boot_id="reader", tf_allowlist={D1_S}, min_coldload_bars={D1_S: 1}, role="reader",
        redis_layer=RedisLayer(redis, NS) if redis is not None else None,
        updates_bus=_RedisUpdatesBus(redis, NS, 100) if redis is not None else None,
    )


def _candle(open_ms: int) -> CandleBar:
    return CandleBar(symbol=SYM, tf_s=D1_S, open_time_ms=open_ms, close_time_ms=open_ms + D1_MS,
                     o=100.0, h=110.0, low=90.0, c=105.0, v=1000.0, complete=True, src="derived")


def _opens(result) -> list[int]:
    return [b["open_time_ms"] for b in result.bars_lwc]


# ── Шляхи читання UI/SMC ─────────────────────────────────────────────────────


def test_ui_cold_load_from_redis_tail_primed_from_disk_and_live_commit_shows_both_d1(tmp_path: Path):
    """Головний шлях UI/SMC: Redis-хвіст = праймінг з диска (там легасі-пара 21:00/22:00) + живий commit наступного D1."""
    _write_disk(tmp_path, [_d1(PREV_D1), _d1(SUMMER_D1), _d1(OFF_GRID_D1, src="derived")])
    redis = _TtlFakeRedis()
    writer = _writer(tmp_path, redis)
    assert writer.bootstrap_prime_from_disk(SYM, D1_S, 100) == 3
    assert writer.commit_final_bar(_candle(NEXT_D1)).ok

    result = _reader(tmp_path, redis).read_window(WindowSpec(SYM, D1_S, 10, cold_load=True), UI_POLICY)

    assert result.meta["source"] == "redis_tail"
    assert _opens(result) == [PREV_D1, SUMMER_D1, OFF_GRID_D1, NEXT_D1]
    assert "geom_non_monotonic" not in result.warnings


def test_ui_cold_load_without_redis_tail_then_ram_merged_with_updates_bus_shows_both_d1(tmp_path: Path):
    """Redis-хвоста нема (TTL/flush): cold-load UI падає на хвіст диска і заповнює RAM; далі дельта-цикл ws зливає в RAM
    фінал із шини, а читання без cold_load (D1_FORMING_SEED_UDS у ws_server, /api) бере RAM-вікно."""
    _write_disk(tmp_path, [_d1(PREV_D1), _d1(SUMMER_D1), _d1(OFF_GRID_D1, src="derived")])
    redis = _TtlFakeRedis()
    reader = _reader(tmp_path, redis)

    cold = reader.read_window(WindowSpec(SYM, D1_S, 10, cold_load=True), UI_POLICY)
    assert cold.meta["source"] == "disk_tail"
    assert _opens(cold) == [PREV_D1, SUMMER_D1, OFF_GRID_D1]

    assert _writer(tmp_path, redis).commit_final_bar(_candle(NEXT_D1)).ok
    updates = reader.read_updates(UpdatesSpec(SYM, D1_S, since_seq=None, limit=500))
    assert [ev["key"]["open_ms"] for ev in updates.events] == [NEXT_D1]

    ram = reader.read_window(WindowSpec(SYM, D1_S, 3), UI_POLICY)

    assert ram.meta["source"] == "ram"
    assert _opens(ram) == [SUMMER_D1, OFF_GRID_D1, NEXT_D1]
    assert "geom_non_monotonic" not in ram.warnings


# Пара D1 одного дня у різних станах запису (колишні кейси вибирача near-dedup, ADR-0094 P1)
D1_PAIRS = {
    "повна нічия": ({}, {}),
    "ранній partial на диску": ({"extensions": {"partial": True}}, {}),
    "пізній partial на диску": ({}, {"extensions": {"partial": True}}),
    "пізній не final": ({}, {"src": "preview"}),
    "ранній не final": ({"src": "preview"}, {}),
}


@pytest.mark.parametrize("case", sorted(D1_PAIRS))
def test_d1_pair_of_one_day_is_not_merged_on_disk_tail_and_ram_reads(tmp_path: Path, case):
    """Без Redis cold-load UI іде на хвіст диска, наступне читання — з RAM; обидва шляхи показують обидва бари."""
    earlier, later = D1_PAIRS[case]
    _write_disk(tmp_path, [_d1(SUMMER_D1, **earlier), _d1(OFF_GRID_D1, **later)])
    reader = _reader(tmp_path, None)

    cold = reader.read_window(WindowSpec(SYM, D1_S, 10, cold_load=True), UI_POLICY)
    ram = reader.read_window(WindowSpec(SYM, D1_S, 2), UI_POLICY)

    assert _opens(cold) == [SUMMER_D1, OFF_GRID_D1]
    assert _opens(ram) == [SUMMER_D1, OFF_GRID_D1]
    assert (cold.meta["source"], ram.meta["source"]) == ("disk_tail", "ram")


def test_uds_range_read_from_disk_shows_off_grid_d1_next_to_summer_d1(tmp_path: Path):
    """Scrollback (disk range, to_open_ms): бар 22:00 не зливається з 21:00 і лишається видимим."""
    _write_disk(tmp_path, [_d1(SUMMER_D1), _d1(OFF_GRID_D1, src="derived")])
    uds = UnifiedDataStore(data_root=str(tmp_path), boot_id="test-boot", tf_allowlist={D1_S},
                           min_coldload_bars={D1_S: 1}, role="reader")

    result = uds.read_window(WindowSpec(SYM, D1_S, 10, to_open_ms=OFF_GRID_D1), ReadPolicy(force_disk=True))

    assert result.meta["source"] == "disk_range"
    assert _opens(result) == [SUMMER_D1, OFF_GRID_D1]
    assert "geom_non_monotonic" not in result.warnings


# ── Специфікація _ensure_sorted_dedup (спільний крок усіх шляхів) ────────────


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
