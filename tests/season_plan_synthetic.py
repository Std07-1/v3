"""Синтетичні дані для тестів плану S7 (ADR-0095 `tools.repair.season_plan`): M1 XAU/USD на торгових хвилинах
сезонного календаря групи `cfd_us_22_23` з config.json, контекст символу і прогін `tools.rebuild_from_m1`.

Не тест-модуль (pytest його не збирає): спільне для `test_season_plan_*`.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Dict, List

from core.config_loader import htf_anchor_rule_resolver
from core.session_anchor import D1_S, RULE_NY_CLOSE_US_DST, htf_bucket_start_ms
from runtime.ingest.tick_common import calendar_for_symbol
from runtime.store.ssot_jsonl import JsonlAppender
from tools import rebuild_from_m1
from tools.repair import season_plan as sp

UTC = dt.timezone.utc
M1_MS = 60_000
_REPO_CALENDAR_GROUP = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8"))[
    "market_calendar_by_group"]["cfd_us_22_23"]
CFG = {
    "market_calendar_symbol_groups": {"XAU/USD": "cfd_us_22_23"},
    "market_calendar_by_group": {"cfd_us_22_23": _REPO_CALENDAR_GROUP},
    "htf_anchor": {"rule_by_calendar_group": {"cfd_us_22_23": RULE_NY_CLOSE_US_DST}},
}
CAL = calendar_for_symbol(CFG, "XAU/USD")


def ms(y, mo, d, h=0, mi=0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp() * 1000)


def write_m1(root: Path, first_ms: int, last_ms: int, skip=()) -> Dict[int, float]:
    """M1 XAU/USD на кожній торговій хвилині сезонного календаря [first, last]; повертає close кожної хвилини."""
    closes: Dict[int, float] = {}
    by_day: Dict[str, List[str]] = {}
    for k, open_ms in enumerate(range(first_ms, last_ms + M1_MS, M1_MS)):
        if not CAL.is_trading_minute(open_ms) or open_ms in skip:
            continue
        price = 4000.0 + (k % 97) * 0.25
        closes[open_ms] = price + 0.1
        bar = {"symbol": "XAU/USD", "tf_s": 60, "open_time_ms": open_ms, "close_time_ms": open_ms + M1_MS,
               "o": price, "h": price + 0.5, "low": price - 0.5, "c": price + 0.1, "v": 1.0, "complete": True,
               "src": "history"}
        by_day.setdefault(dt.datetime.fromtimestamp(open_ms / 1000, UTC).strftime("%Y%m%d"), []).append(json.dumps(bar))
    tf_dir = root / "XAU_USD" / "tf_60"
    tf_dir.mkdir(parents=True, exist_ok=True)
    for day, lines in by_day.items():
        with open(tf_dir / ("part-%s.jsonl" % day), "a", encoding="utf-8") as part:
            part.write("\n".join(lines) + "\n")
    return closes


def disk_rows(root: Path, tf_s: int) -> Dict[int, dict]:
    rows: Dict[int, dict] = {}
    for path in sorted((root / "XAU_USD" / ("tf_%d" % tf_s)).glob("part-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                rows[row["open_time_ms"]] = row
    return rows


def context(root: Path, window=sp.ALL_TIME) -> sp.SymbolContext:
    m1 = sp.SourceReader(str(root), "XAU/USD").read(60, *sp.ALL_TIME)
    return sp.SymbolContext(symbol="XAU/USD", sym_dir="XAU_USD", rule=RULE_NY_CLOSE_US_DST, calendar=CAL,
                            window=window, m1_head_ms=m1[0].open_time_ms, m1_tail_ms=m1[-1].open_time_ms)


def run_rebuild_tool(root: Path, first_ms: int, end_ms: int) -> None:
    """Похідні на диску = f(M1) інструментом `rebuild_from_m1` (append через писаря SSOT)."""
    writer = JsonlAppender(root=str(root), anchor_rule_for_symbol=htf_anchor_rule_resolver(CFG))
    try:
        rebuild_from_m1.rebuild_one_symbol(
            data_root=str(root), symbol="XAU/USD", start_ms=htf_bucket_start_ms(first_ms, D1_S, RULE_NY_CLOSE_US_DST),
            end_ms=end_ms, dry_run=False, cfg=CFG, writer=writer, anchor_rule=RULE_NY_CLOSE_US_DST,
        )
    finally:
        writer.close()
