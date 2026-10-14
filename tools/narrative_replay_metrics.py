"""tools/narrative_replay_metrics.py — метрики прогону системного наративу (ADR-0113 Ф1): baseline для порівняння змін.

Вхід — JSONL `tools.narrative_replay` і M1 того самого символу з диску (шлях ціни після появи сценарію). Рахується
окремо на кожен TF глядача, лише на відкритому ринку (рядки «Ринок закрито» — окремою часткою):

- `empty_wait_share` — «чекаємо без змісту»: wait, без сценаріїв і без next_area;
- `headlines` — розподіл заголовків;
- `changes_per_day` — змін стану (mode, заголовок, зона основного сценарію, тригер) на торгову добу; `flip_backs` —
  повернень до попереднього стану, коли проміжний протримався ≤ 2 кроки (A→B→A);
- `scenario_share` — частка кроків зі сценарієм; епізод — поспіль та сама зона основного сценарію;
- `confirm_min_median` — хвилин від появи епізоду до першого triggered/ready (і кількість підтверджених);
- `outcomes` / `outcomes_after_confirm` — що ціна M1 зачепила раніше в межах горизонту від появи (від підтвердження):
  `target`, `invalidated`, обидва в одному барі (`ambiguous`), нічого (`open`), ціль не названа (`no_target`).

Приклад:

    python -m tools.narrative_replay_metrics --replay replay.jsonl --json metrics.json
"""
from __future__ import annotations

import argparse
import bisect
import collections
import datetime as dt
import json
import statistics
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.config_loader import load_system_config, resolve_config_path
from runtime.smc.signal_journal import parse_price_from_desc
from runtime.store.layers.disk_layer import DiskLayer

Row = Dict[str, Any]
CONFIRMED_TRIGGERS = frozenset({"triggered", "ready"})
FLIP_BACK_MAX_STEPS = 2
DEFAULT_HORIZON_H = 72


def narrative_state(narrative: Row) -> Tuple[str, str, str, str]:
    primary = narrative["scenarios"][0] if narrative["scenarios"] else {}
    return (narrative["mode"], narrative["headline"], primary.get("zone_id", ""), primary.get("trigger", ""))


def _runs(items: Sequence[Any]) -> List[Tuple[Any, int]]:
    runs: List[Tuple[Any, int]] = []
    for item in items:
        if runs and runs[-1][0] == item:
            runs[-1] = (item, runs[-1][1] + 1)
        else:
            runs.append((item, 1))
    return runs


def _primary_zone_id(row: Row) -> str:
    scenarios = row["narrative"]["scenarios"]
    return scenarios[0]["zone_id"] if scenarios else ""


def episodes(rows: Sequence[Row]) -> List[List[Row]]:
    """Поспіль та сама зона основного сценарію. rows — усі кроки одного TF за часом (прогін пише рядок на кожен крок,
    «Ринок закрито» теж), тож рядок без сценарію — межа епізоду."""
    found: List[List[Row]] = []
    previous_zone_id = ""
    for row in rows:
        zone_id = _primary_zone_id(row)
        if zone_id and zone_id == previous_zone_id:
            found[-1].append(row)
        elif zone_id:
            found.append([row])
        previous_zone_id = zone_id
    return found


def first_touch(m1: Sequence[Row], m1_opens: Sequence[int], start_ms: int, horizon_ms: int, direction: str,
                target: float, invalidation: float) -> str:
    """Що ціна зачепила раніше в барах M1, що відкрились у [start_ms, start_ms + horizon_ms)."""
    if target <= 0:
        return "no_target"
    for bar in m1[bisect.bisect_left(m1_opens, start_ms):]:
        if bar["open_time_ms"] >= start_ms + horizon_ms:
            break
        if direction == "long":
            hit_target, hit_invalidation = bar["h"] >= target, bar["low"] <= invalidation
        else:
            hit_target, hit_invalidation = bar["low"] <= target, bar["h"] >= invalidation
        if hit_target and hit_invalidation:
            return "ambiguous"
        if hit_target:
            return "target"
        if hit_invalidation:
            return "invalidated"
    return "open"


def _outcome(m1: Sequence[Row], m1_opens: Sequence[int], row: Row, horizon_ms: int) -> str:
    scenario = row["narrative"]["scenarios"][0]
    return first_touch(m1, m1_opens, row["t"], horizon_ms, scenario["direction"],
                       parse_price_from_desc(scenario.get("target_desc") or ""),
                       parse_price_from_desc(scenario.get("invalidation") or ""))


def _share(rows: Sequence[Row], predicate: Callable[[Row], bool]) -> float:
    return round(sum(1 for r in rows if predicate(r["narrative"])) / max(len(rows), 1), 4)


def _is_empty_wait(narrative: Row) -> bool:
    return narrative["mode"] == "wait" and not narrative["scenarios"] and not narrative["next_area"]


def tf_metrics(rows: Sequence[Row], m1: Sequence[Row], horizon_ms: int) -> Row:
    """Метрики одного TF глядача; rows — усі кроки за часом, m1 — бари M1 за зростанням open_time_ms."""
    open_rows = [r for r in rows if r["narrative"]["market_phase"] != "closed"]
    runs = _runs([narrative_state(r["narrative"]) for r in open_rows])
    flip_backs = sum(1 for k in range(1, len(runs) - 1)
                     if runs[k][1] <= FLIP_BACK_MAX_STEPS and runs[k - 1][0] == runs[k + 1][0])
    days = len({dt.datetime.fromtimestamp(r["t"] / 1000, dt.timezone.utc).date() for r in open_rows}) or 1
    m1_opens = [bar["open_time_ms"] for bar in m1]
    found = episodes(rows)
    confirm_min: List[float] = []
    outcomes: collections.Counter = collections.Counter()
    after_confirm: collections.Counter = collections.Counter()
    for episode in found:
        outcomes[_outcome(m1, m1_opens, episode[0], horizon_ms)] += 1
        confirmed = next((r for r in episode if r["narrative"]["scenarios"][0]["trigger"] in CONFIRMED_TRIGGERS), None)
        if confirmed is not None:
            confirm_min.append((confirmed["t"] - episode[0]["t"]) / 60000)
            after_confirm[_outcome(m1, m1_opens, confirmed, horizon_ms)] += 1
    return {
        "steps": len(rows), "open_steps": len(open_rows), "closed_share": round(1 - len(open_rows) / max(len(rows), 1), 4),
        "empty_wait_share": _share(open_rows, _is_empty_wait),
        "scenario_share": _share(open_rows, lambda narrative: bool(narrative["scenarios"])),
        "headlines": collections.Counter(r["narrative"]["headline"] for r in open_rows).most_common(8),
        "changes_per_day": round((len(runs) - 1) / days, 2), "flip_backs": flip_backs, "trading_days": days,
        "episodes": len(found), "confirmed": len(confirm_min),
        "confirm_min_median": statistics.median(confirm_min) if confirm_min else None,
        "outcomes": dict(outcomes), "outcomes_after_confirm": dict(after_confirm),
    }


def compute_metrics(replay_rows: Sequence[Row], m1: Sequence[Row], horizon_h: int = DEFAULT_HORIZON_H) -> Row:
    by_tf: Dict[int, List[Row]] = collections.defaultdict(list)
    for row in sorted(replay_rows, key=lambda r: (r["tf_s"], r["t"])):
        by_tf[row["tf_s"]].append(row)
    return {str(tf_s): tf_metrics(rows, m1, horizon_h * 3600 * 1000) for tf_s, rows in sorted(by_tf.items())}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Метрики прогону системного наративу (ADR-0113 Ф1)")
    parser.add_argument("--replay", required=True, help="JSONL tools.narrative_replay")
    parser.add_argument("--horizon-h", type=int, default=DEFAULT_HORIZON_H, help="горизонт результату сценарію, год")
    parser.add_argument("--json", default=None, help="куди записати метрики")
    parser.add_argument("--config", default=None)
    parser.add_argument("--data-root", default=None)
    args = parser.parse_args(argv)
    rows = [json.loads(line) for line in open(args.replay, encoding="utf-8") if line.strip()]
    if not rows:
        print("NARRATIVE_METRICS empty replay=%s" % args.replay)
        return 2
    cfg = load_system_config(resolve_config_path(args.config))
    first_ms, last_ms = min(r["t"] for r in rows), max(r["t"] for r in rows)
    horizon_ms = args.horizon_h * 3600 * 1000
    m1, _geom = DiskLayer(args.data_root or cfg.get("data_root", "./data_v3")).read_window_with_geom(
        rows[0]["symbol"], 60, (last_ms + horizon_ms - first_ms) // 60000 + 1, since_open_ms=first_ms - 1,
        to_open_ms=last_ms + horizon_ms, use_tail=True, skip_preview=True)
    metrics = {"symbol": rows[0]["symbol"], "horizon_h": args.horizon_h, "by_tf": compute_metrics(rows, m1, args.horizon_h)}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as out:
            json.dump(metrics, out, ensure_ascii=False, indent=1)
    sys.stdout.reconfigure(encoding="utf-8")  # заголовки з емодзі; консоль Windows за замовчуванням cp1251
    print(json.dumps(metrics, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
