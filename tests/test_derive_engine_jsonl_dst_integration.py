"""Зчеплення деривації і писаря SSOT через обидва вихідні переходу DST 2026 (ADR-0095 S4a, критика §3).

Юніти деривації і писаря перевіряють кожен свою половину. Тут — справжній шлях: `build_derive_engine` з config
репозиторію → `DeriveEngine.on_bar` + overdue → `UnifiedDataStore.commit_final_bar` → справжній `JsonlAppender` з
резолвером правила. Кожен H4/D1 на диску має стояти на сезонній сітці, писар не відкидає жодного бару, а після
01.11 немає фантомного H4 Нд 21:00 (обрубок доби на 25 год, який на старому вікні `open + tf` поглинав би H1
22:00..00:00 нової доби).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple
from unittest.mock import Mock

import pytest

from core.config_loader import htf_anchor_rule_resolver, load_system_config
from core.model.bars import CandleBar
from core.session_anchor import D1_S, H4_S, assert_on_season_grid
from runtime.ingest.derive_engine import build_derive_engine
from runtime.ingest.tick_common import calendar_from_group
from runtime.store.ssot_jsonl import JsonlAppender
from runtime.store.uds import UnifiedDataStore

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config.json"
SYM = "XAU/USD"  # група cfd_us_22_23
M1_MS = 60_000
ALL_TFS = {60, 180, 300, 900, 1800, 3600, H4_S, D1_S}
OVERDUE_EVERY_MIN = 240  # поллер кличе overdue щоцикла; тут рідше (швидкість), плюс завжди на відкритті сесії


def _ms(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp() * 1000)


class _DiskStub:
    def last_open_ms(self, symbol: str, tf_s: int):
        _ = symbol, tf_s
        return None


def _m1(open_ms: int, price: float) -> CandleBar:
    return CandleBar(symbol=SYM, tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + M1_MS, o=price,
                     h=price + 0.5, low=price - 0.5, c=price + 0.25, v=1.0, complete=True, src="history")


def _run_weekend(tmp_path: Path, start_ms: int, end_ms: int) -> Tuple[Dict[int, List[int]], object]:
    """Подати торгові M1 [start, end) у рушій зі справжнім писарем; повернути {tf: [open_ms]} з part-файлів."""
    cfg = load_system_config(str(REPO_CONFIG))
    group = cfg["market_calendar_symbol_groups"][SYM]
    calendar = calendar_from_group(cfg["market_calendar_by_group"][group])
    appender = JsonlAppender(str(tmp_path), anchor_rule_for_symbol=htf_anchor_rule_resolver(cfg))
    uds = UnifiedDataStore(
        data_root=str(tmp_path), boot_id="test-dst", tf_allowlist=set(ALL_TFS),
        min_coldload_bars={tf: 1 for tf in ALL_TFS}, role="writer", disk_layer=_DiskStub(), redis_layer=Mock(),
        jsonl_appender=appender, redis_snapshot_writer=Mock(), updates_bus=Mock(), preview_tf_allowlist=set(ALL_TFS),
    )
    engine = build_derive_engine(cfg, [SYM], {SYM: calendar})
    engine.register_symbol_uds(SYM, uds)

    prev_ms = None
    for k, open_ms in enumerate(range(start_ms, end_ms, M1_MS)):
        if not calendar.is_trading_minute(open_ms):
            continue
        engine.on_bar(_m1(open_ms, 2000.0 + k * 0.01))
        session_reopened = prev_ms is not None and open_ms - prev_ms > M1_MS
        if session_reopened or (open_ms // M1_MS) % OVERDUE_EVERY_MIN == 0:
            engine.check_overdue_buckets(open_ms + M1_MS)
        prev_ms = open_ms
    engine.check_overdue_buckets(end_ms)

    opens: Dict[int, List[int]] = {tf: [] for tf in (H4_S, D1_S)}
    for tf in opens:
        for part in sorted(tmp_path.rglob("tf_%d/part-*.jsonl" % tf)):
            for line in part.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                assert row["symbol"] == SYM
                opens[tf].append(int(row["open_time_ms"]))
    return opens, engine


@pytest.mark.parametrize("start_ms, end_ms, must_have_h4, must_have_d1, phantom_h4", [
    pytest.param(  # весна: пт 06.03 зима (23/03/…, 18:00 EST) → пн 09.03 літо (22/02/…, 18:00 EDT)
        _ms(2026, 3, 5, 22), _ms(2026, 3, 9, 10),
        [_ms(2026, 3, 6, 19), _ms(2026, 3, 8, 22), _ms(2026, 3, 9, 2), _ms(2026, 3, 9, 6)],
        [_ms(2026, 3, 5, 22)],
        [_ms(2026, 3, 8, 23), _ms(2026, 3, 9, 3)],  # зимова сітка після переходу
        id="spring-2026-03-08",
    ),
    pytest.param(  # осінь: пт 30.10 літо (22/02/…) → пн 02.11 зима (23/03/…)
        _ms(2026, 10, 29, 21), _ms(2026, 11, 2, 10),
        [_ms(2026, 10, 30, 18), _ms(2026, 11, 1, 23), _ms(2026, 11, 2, 3)],
        [_ms(2026, 10, 29, 21)],
        # літня сітка після переходу; обрубок Нд 22:00 тут не фантом: плаский літній календар живих споживачів (до S6b,
        # ADR-0095) вважає Нд 22:00–22:59 торговим і зимою, тож фікстура подає в обрубок M1
        [_ms(2026, 11, 2, 2)],
        id="fall-2026-11-01",
    ),
])
def test_derived_h4_d1_written_through_real_appender_stay_on_season_grid(
    tmp_path: Path, caplog, start_ms, end_ms, must_have_h4, must_have_d1, phantom_h4,
):
    rule = htf_anchor_rule_resolver(load_system_config(str(REPO_CONFIG)))(SYM)
    with caplog.at_level(logging.WARNING):
        opens, engine = _run_weekend(tmp_path, start_ms, end_ms)

    for tf, tf_opens in opens.items():
        assert tf_opens, "писар не отримав жодного tf=%d — зчеплення не перевірено" % tf
        assert len(tf_opens) == len(set(tf_opens)), "дубль ключа tf=%d" % tf
        for open_ms in tf_opens:
            assert_on_season_grid(open_ms, tf, rule)
    assert set(must_have_h4) <= set(opens[H4_S])
    assert set(must_have_d1) <= set(opens[D1_S])
    assert not set(phantom_h4) & set(opens[H4_S])
    assert engine.stats()["rejected"] == 0
    assert "DERIVE_REJECT" not in caplog.text and "bar_off_season_grid" not in caplog.text
