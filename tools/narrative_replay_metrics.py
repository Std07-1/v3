"""tools/narrative_replay_metrics.py — метрики прогону системного наративу (ADR-0113 Ф1): baseline для порівняння змін.

Вхід — JSONL `tools.narrative_replay` і M1 того самого символу з диску (шлях ціни після появи сценарію). Рахується
окремо на кожен TF глядача, лише на відкритому ринку й без деградованих блоків:

- `closed_steps` — кроків «Ринок закрито»: момент розбору — закриття бару, тож крок на початку паузи чи вихідних завжди
  такий (лічильник меж пауз, не частка закритого часу); `degraded_share` — запасні блоки (`computation_error`,
  `no_snapshot`) серед відкритих кроків — у решту метрик не йдуть;
- `empty_wait_share` — «чекаємо без змісту»: wait, без сценаріїв і без next_area; `headlines` — розподіл заголовків;
- `changes_per_day` — змін стану (mode, заголовок, зона основного сценарію, тригер) на торгову добу (від відкриття
  сесії, правило якоря символу), `flip_backs` — повернень до попереднього стану, коли проміжний протримався ≤ 2 кроки;
- `scenario_share` — частка кроків зі сценарієм; епізод — поспіль та сама зона основного сценарію; сетап — зона:
  мигання рве сетап на кілька епізодів, тож результат рахується раз на сетап, від його першої появи;
- `outcomes` — результат угоди від краю зони (`trade_outcome`): до входу ціна дійшла до цілі (`missed`) чи горизонт
  минув без входу (`no_fill`); після входу — `target`, `invalidated`, обидва в одному барі M1 (`ambiguous`), нічого
  (`open`); M1 на диску закінчились раніше горизонту — `censored`; `no_target` — ціль не названа.
  `outcomes_after_confirm` — те саме від першого triggered/ready сетапу;
- `rr_at_entry_median` — плановий R:R від краю зони: |ціль − край| / |інвалідація − край|.

Ціна цілі-зони («HTF OB a–b») — ближній край за напрямком: short — верхній, long — нижній.

Приклад (база й порівняння зміни з нею — `--compare`, рядок на TF: база → зміна):

    python -m tools.narrative_replay_metrics --replay replay.jsonl --json metrics.json
    python -m tools.narrative_replay_metrics --replay candidate.jsonl --compare metrics.json
"""
from __future__ import annotations

import argparse
import bisect
import collections
import json
import re
import statistics
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.config_loader import htf_anchor_rule_resolver, load_system_config, resolve_config_path
from core.session_anchor import trading_day_open_ms
from runtime.smc.signal_journal import parse_price_from_desc
from runtime.store.layers.disk_layer import DiskLayer

Row = Dict[str, Any]
CONFIRMED_TRIGGERS = frozenset({"triggered", "ready"})
DEGRADED_WARNINGS = frozenset({"computation_error", "no_snapshot"})  # _fallback_narrative_block
FLIP_BACK_MAX_STEPS = 2
DEFAULT_HORIZON_H = 72
M1_MS = 60_000
_DAY_MS = 86_400_000
_ZONE_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)–(\d+(?:\.\d+)?)")  # «OB▲ A(6) 4090.00–4095.00» (_format_entry)


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


def setups(found_episodes: Sequence[List[Row]]) -> List[List[Row]]:
    """Сетап — усі кроки однієї зони основного сценарію за порядком першої появи (епізоди однієї зони — разом)."""
    by_zone: Dict[str, List[Row]] = {}
    for episode in found_episodes:
        by_zone.setdefault(_primary_zone_id(episode[0]), []).extend(episode)
    return list(by_zone.values())


def parse_zone_range(desc: str) -> Optional[Tuple[float, float]]:
    match = _ZONE_RANGE_RE.search(desc or "")
    if not match:
        return None
    low, high = float(match.group(1)), float(match.group(2))
    return (min(low, high), max(low, high))


def target_price(target_desc: str, direction: str) -> float:
    """Ціна цілі; для цілі-зони — ближній край за напрямком (short іде вниз — спершу верхній край)."""
    zone = parse_zone_range(target_desc)
    if zone is not None:
        return zone[1] if direction == "short" else zone[0]
    return parse_price_from_desc(target_desc or "")


def trade_outcome(m1: Sequence[Row], m1_opens: Sequence[int], start_ms: int, horizon_ms: int, direction: str,
                  zone: Tuple[float, float], target: float, invalidation: float) -> str:
    """Угода від краю зони в барах M1, що відкрились у [start_ms, start_ms + horizon_ms).

    Вхід — дотик краю зони з боку ціни (long — верхнього, short — нижнього); до входу ціль = `missed` (угоди не було).
    Після входу — що раніше: ціль чи інвалідація; бар, у якому вхід і інвалідація разом, — `ambiguous`. Нерозв'язаний
    результат, коли M1 закінчились раніше горизонту, — `censored`, а не «нічого не сталося».
    """
    if target <= 0:
        return "no_target"
    long = direction == "long"
    entry = zone[1] if long else zone[0]
    filled = False
    for bar in m1[bisect.bisect_left(m1_opens, start_ms):]:
        if bar["open_time_ms"] >= start_ms + horizon_ms:
            break
        hit_target = bar["h"] >= target if long else bar["low"] <= target
        hit_invalidation = bar["low"] <= invalidation if long else bar["h"] >= invalidation
        if not filled:
            touches_entry = bar["low"] <= entry if long else bar["h"] >= entry
            if not touches_entry:
                if hit_target:
                    return "missed"
                continue
            filled = True
            if hit_invalidation:
                return "ambiguous"  # вхід і стоп в одному барі — порядок усередині хвилини невідомий
            continue
        if hit_target and hit_invalidation:
            return "ambiguous"
        if hit_target:
            return "target"
        if hit_invalidation:
            return "invalidated"
    if not m1_opens or m1_opens[-1] + M1_MS < start_ms + horizon_ms:
        return "censored"
    return "open" if filled else "no_fill"


def _scenario_levels(row: Row) -> Tuple[str, Optional[Tuple[float, float]], float, float]:
    scenario = row["narrative"]["scenarios"][0]
    direction = scenario["direction"]
    return (direction, parse_zone_range(scenario.get("entry_desc", "")),
            target_price(scenario.get("target_desc") or "", direction),
            parse_price_from_desc(scenario.get("invalidation") or ""))


def _outcome(m1: Sequence[Row], m1_opens: Sequence[int], row: Row, horizon_ms: int) -> str:
    direction, zone, target, invalidation = _scenario_levels(row)
    if zone is None:
        return "no_zone"
    return trade_outcome(m1, m1_opens, row["t"], horizon_ms, direction, zone, target, invalidation)


def _rr_at_entry(row: Row) -> Optional[float]:
    direction, zone, target, invalidation = _scenario_levels(row)
    if zone is None or target <= 0 or invalidation <= 0:
        return None
    entry = zone[1] if direction == "long" else zone[0]
    risk = abs(entry - invalidation)
    return round(abs(target - entry) / risk, 2) if risk > 0 else None


def _share(rows: Sequence[Row], predicate: Callable[[Row], bool]) -> float:
    return round(sum(1 for r in rows if predicate(r["narrative"])) / max(len(rows), 1), 4)


def _is_closed(narrative: Row) -> bool:
    return narrative["market_phase"] == "closed"


def _is_degraded(narrative: Row) -> bool:
    return bool(DEGRADED_WARNINGS.intersection(narrative.get("warnings") or ()))


def _is_empty_wait(narrative: Row) -> bool:
    return narrative["mode"] == "wait" and not narrative["scenarios"] and not narrative["next_area"]


def tf_metrics(rows: Sequence[Row], m1: Sequence[Row], horizon_ms: int, trading_day: Callable[[int], int]) -> Row:
    """Метрики одного TF глядача; rows — усі кроки за часом, m1 — бари M1 за зростанням open_time_ms."""
    market_open = [r for r in rows if not _is_closed(r["narrative"])]
    open_rows = [r for r in market_open if not _is_degraded(r["narrative"])]
    runs = _runs([narrative_state(r["narrative"]) for r in open_rows])
    flip_backs = sum(1 for k in range(1, len(runs) - 1)
                     if runs[k][1] <= FLIP_BACK_MAX_STEPS and runs[k - 1][0] == runs[k + 1][0])
    days = len({trading_day(r["t"]) for r in open_rows}) or 1
    m1_opens = [bar["open_time_ms"] for bar in m1]
    found_episodes = episodes(rows)
    found_setups = setups(found_episodes)
    confirm_min: List[float] = []
    outcomes: collections.Counter = collections.Counter()
    after_confirm: collections.Counter = collections.Counter()
    rr = [value for value in (_rr_at_entry(setup[0]) for setup in found_setups) if value is not None]
    for setup in found_setups:
        outcomes[_outcome(m1, m1_opens, setup[0], horizon_ms)] += 1
        confirmed = next((r for r in setup if r["narrative"]["scenarios"][0]["trigger"] in CONFIRMED_TRIGGERS), None)
        if confirmed is not None:
            confirm_min.append((confirmed["t"] - setup[0]["t"]) / 60000)
            after_confirm[_outcome(m1, m1_opens, confirmed, horizon_ms)] += 1
    return {
        "steps": len(rows), "open_steps": len(open_rows), "closed_steps": len(rows) - len(market_open),
        "degraded_share": _share(market_open, _is_degraded),
        "empty_wait_share": _share(open_rows, _is_empty_wait),
        "scenario_share": _share(open_rows, lambda narrative: bool(narrative["scenarios"])),
        "headlines": collections.Counter(r["narrative"]["headline"] for r in open_rows).most_common(8),
        "changes_per_day": round(max(len(runs) - 1, 0) / days, 2), "flip_backs": flip_backs, "trading_days": days,
        "episodes": len(found_episodes), "setups": len(found_setups), "confirmed": len(confirm_min),
        "confirm_min_median": statistics.median(confirm_min) if confirm_min else None,
        "rr_at_entry_median": statistics.median(rr) if rr else None,
        "outcomes": dict(outcomes), "outcomes_after_confirm": dict(after_confirm),
    }


def compute_metrics(replay_rows: Sequence[Row], m1: Sequence[Row], horizon_h: int = DEFAULT_HORIZON_H,
                    trading_day: Optional[Callable[[int], int]] = None) -> Row:
    """trading_day — ключ торгової доби для моменту (main: відкриття сесії за правилом якоря символу); None — UTC-дата."""
    day_key = trading_day or (lambda t_ms: t_ms // _DAY_MS)
    by_tf: Dict[int, List[Row]] = collections.defaultdict(list)
    for row in sorted(replay_rows, key=lambda r: (r["tf_s"], r["t"])):
        by_tf[row["tf_s"]].append(row)
    return {str(tf_s): tf_metrics(rows, m1, horizon_h * 3600 * 1000, day_key) for tf_s, rows in sorted(by_tf.items())}


COMPARED_FIELDS = ("scenario_share", "changes_per_day", "flip_backs", "setups", "confirmed", "rr_at_entry_median")
COMPARED_OUTCOMES = ("target", "invalidated", "ambiguous", "open", "missed", "no_fill", "censored")


def _outcome_cell(tf_metrics_row: Row) -> str:
    return "/".join(str(tf_metrics_row.get("outcomes", {}).get(key, 0)) for key in COMPARED_OUTCOMES)


def compare_table(base: Row, candidate: Row) -> str:
    """Markdown: на кожен TF — поле «база → зміна» і результати сетапів (ADR-0113 §6: кожна зміна проти бази)."""
    lines = ["| TF | " + " | ".join(COMPARED_FIELDS) + " | outcomes (" + "/".join(COMPARED_OUTCOMES) + ") |",
             "|---" * (len(COMPARED_FIELDS) + 2) + "|"]
    for tf_s in sorted(set(base["by_tf"]) | set(candidate["by_tf"]), key=int):
        old, new = base["by_tf"].get(tf_s, {}), candidate["by_tf"].get(tf_s, {})
        cells = ["%s → %s" % (old.get(field), new.get(field)) for field in COMPARED_FIELDS]
        lines.append("| %s | %s | %s → %s |" % (tf_s, " | ".join(cells), _outcome_cell(old), _outcome_cell(new)))
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Метрики прогону системного наративу (ADR-0113 Ф1)")
    parser.add_argument("--replay", required=True, help="JSONL tools.narrative_replay")
    parser.add_argument("--horizon-h", type=int, default=DEFAULT_HORIZON_H, help="горизонт результату сценарію, год")
    parser.add_argument("--json", default=None, help="куди записати метрики")
    parser.add_argument("--compare", default=None, help="метрики бази (JSON): надрукувати таблицю база → зміна")
    parser.add_argument("--config", default=None)
    parser.add_argument("--data-root", default=None)
    args = parser.parse_args(argv)
    rows = [json.loads(line) for line in open(args.replay, encoding="utf-8") if line.strip()]
    if not rows:
        print("NARRATIVE_METRICS empty replay=%s" % args.replay)
        return 2
    cfg = load_system_config(resolve_config_path(args.config))
    symbol = rows[0]["symbol"]
    rule = htf_anchor_rule_resolver(cfg)(symbol)
    first_ms, last_ms = min(r["t"] for r in rows), max(r["t"] for r in rows)
    horizon_ms = args.horizon_h * 3600 * 1000
    m1, _geom = DiskLayer(args.data_root or cfg.get("data_root", "./data_v3")).read_window_with_geom(
        symbol, 60, (last_ms + horizon_ms - first_ms) // M1_MS + 1, since_open_ms=first_ms - 1,
        to_open_ms=last_ms + horizon_ms, use_tail=True, skip_preview=True)
    by_tf = compute_metrics(rows, m1, args.horizon_h, trading_day=lambda t_ms: trading_day_open_ms(t_ms, rule))
    metrics = {"symbol": symbol, "horizon_h": args.horizon_h, "by_tf": by_tf}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as out:
            json.dump(metrics, out, ensure_ascii=False, indent=1)
    sys.stdout.reconfigure(encoding="utf-8")  # заголовки з емодзі; консоль Windows за замовчуванням cp1251
    if args.compare:
        print(compare_table(json.load(open(args.compare, encoding="utf-8")), metrics))
        return 0
    print(json.dumps(metrics, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
