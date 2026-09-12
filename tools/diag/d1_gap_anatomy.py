"""Аудит: анатомія пропусків у D1-бакетах (ADR-0092 P1, калібрування порогів).

Навіщо: політика повноти D1 (ADR-0092 шар 2) має відрізняти чотири різні речі, які
сьогодні однаково дають «бару немає»:

  - `edge_close` / `edge_open` — довгий суцільний пропуск на краю сесії: рання сесія
    свята АБО невідповідний DST-якір (саме це ловить нинішній бюджет 15);
  - `feed_gap` — довгий пропуск усередині сесії: реальний обрив фіду;
  - `thin_scatter` — розсип коротких пропусків: неліквідні хвилини (метали ввечері);
  - `full_closure` — торгівлі не було майже весь бакет (повне свято).

Без цих чисел пороги `scatter_run_max`, `scatter_ratio` і `max_run` — гіпотеза,
тому ADR-0092 не може стати Accepted до прогону цього інструмента.

НІЧОГО НЕ ПИШЕ: лише читає SSOT-JSONL і рахує. Календар — через канонічну фабрику
(`resolve_symbol_calendars`), щоб не додавати ще одну копію будівника (ADR-0092 шар 0).

Використання:
    python -m tools.diag.d1_gap_anatomy --all --days 180
    python -m tools.diag.d1_gap_anatomy --symbol XAU/USD --date 2026-09-06 --date 2026-06-21
    python -m tools.diag.d1_gap_anatomy --all --days 180 --json /tmp/anatomy.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from core.config_loader import load_system_config, pick_config_path
from core.derive import MAX_MID_SESSION_GAPS_BY_TF
from core.health.measures import expected_bucket_opens
from runtime.ingest.tick_common import resolve_symbol_calendars

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

TF_M1_S = 60
TF_M1_MS = 60_000
TF_D1_S = 86400
TF_D1_MS = 86_400_000

# Прогін пропусків довший за це — «довгий»; саме довгі прогони ловить нинішній
# бюджет D1 (ADR-0005), і саме вони відрізняють DST/обрив від неліквідності.
LONG_RUN_MIN = 45
# Пропуск у прогоні не довшому за це вважаємо «розсипом» (кандидат на окремий бюджет).
DEFAULT_SCATTER_RUN_MAX = 3
# Скільки торгових хвилин має лишитись, щоб бакет не вважався повним закриттям.
FULL_CLOSURE_TOLERANCE = 20


def analyze_bucket(
    slots: Sequence[int],
    present: Set[int],
    *,
    scatter_run_max: int = DEFAULT_SCATTER_RUN_MAX,
) -> Dict[str, Any]:
    """Анатомія одного D1-бакета. Чиста функція: ніякого I/O.

    ``slots`` — торгові хвилини бакета за календарем (зростаюче), ``present`` — ті,
    для яких є M1-бар. Прогони рахуються по ПОСЛІДОВНОСТІ СЛОТІВ, а не по
    астрономічному часу: derive рахує пропуски саме так (перерви не розривають прогін).
    Прогони на першому і останньому слоті — «граничні»: для derive вони безкоштовні
    (``_collect_boundary_tolerant``), і саме вони відповідають ранній сесії та DST-зсуву.
    """
    total = len(slots)
    if total == 0:
        return {"expected": 0, "class": "no_trading_minutes"}
    missing_idx = [i for i, ms in enumerate(slots) if ms not in present]
    runs: List[Tuple[int, int]] = []
    for i in missing_idx:
        if runs and i == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], i)
        else:
            runs.append((i, i))
    missing = len(missing_idx)
    max_run = max((b - a + 1 for a, b in runs), default=0)
    scatter = sum(b - a + 1 for a, b in runs if b - a + 1 <= scatter_run_max)
    at_open = any(a == 0 for a, _b in runs)
    at_close = any(b == total - 1 for _a, b in runs)
    longest = max(runs, key=lambda r: r[1] - r[0], default=None) if runs else None

    if missing == 0:
        klass = "clean"
    elif missing >= total - FULL_CLOSURE_TOLERANCE:
        klass = "full_closure"
    elif max_run >= LONG_RUN_MIN and longest is not None and longest[1] == total - 1:
        klass = "edge_close"
    elif max_run >= LONG_RUN_MIN and longest is not None and longest[0] == 0:
        klass = "edge_open"
    elif max_run >= LONG_RUN_MIN:
        klass = "feed_gap"
    elif scatter == missing:
        klass = "thin_scatter"
    else:
        klass = "mixed"

    return {
        "expected": total,
        "present": total - missing,
        "missing": missing,
        "coverage_pct": round(100.0 * (total - missing) / total, 3),
        "max_run": max_run,
        "runs": len(runs),
        "scatter": scatter,
        "scatter_ratio_pct": round(100.0 * scatter / total, 3),
        "missing_at_session_open": at_open,
        "missing_at_session_close": at_close,
        "class": klass,
    }


def _load_opens(data_root: str, symbol: str, tf_s: int, lo_ms: int, hi_ms: int) -> Set[int]:
    """open_time_ms з part-файлів у [lo, hi). Читає лише потрібні дні."""
    sym_dir = symbol.replace("/", "_")
    days = {
        dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y%m%d")
        for t in (lo_ms, hi_ms - 1)
    }
    cur = lo_ms
    while cur < hi_ms:
        days.add(dt.datetime.fromtimestamp(cur / 1000, dt.timezone.utc).strftime("%Y%m%d"))
        cur += TF_D1_MS
    out: Set[int] = set()
    for day in sorted(days):
        path = os.path.join(data_root, sym_dir, "tf_%d" % tf_s, "part-%s.jsonl" % day)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = int(json.loads(line)["open_time_ms"])
                except (ValueError, KeyError, TypeError):
                    continue
                if lo_ms <= o < hi_ms:
                    out.add(o)
    return out


def _all_opens_by_glob(data_root: str, symbol: str, tf_s: int) -> Set[int]:
    """Усі open_time_ms цього TF (для D1 — дешево). Перебір part-файлів, не днів."""
    pattern = os.path.join(data_root, symbol.replace("/", "_"), "tf_%d" % tf_s, "part-*.jsonl")
    out: Set[int] = set()
    for path in sorted(glob.glob(pattern)):
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.add(int(json.loads(line)["open_time_ms"]))
                except (ValueError, KeyError, TypeError):
                    continue
    return out


def _first_part_day_ms(data_root: str, symbol: str, tf_s: int) -> Optional[int]:
    """Початок найранішого part-файлу (UTC-доба з імені) — без читання вмісту."""
    pattern = os.path.join(data_root, symbol.replace("/", "_"), "tf_%d" % tf_s, "part-*.jsonl")
    days = sorted(os.path.basename(p)[5:-6] for p in glob.glob(pattern))
    if not days:
        return None
    return int(dt.datetime.strptime(days[0], "%Y%m%d").replace(tzinfo=dt.timezone.utc).timestamp()) * 1000


def analyze_symbol(
    cfg: Dict[str, Any],
    data_root: str,
    symbol: str,
    *,
    days: int,
    dates: Sequence[str],
    scatter_run_max: int,
    now_ms: int,
) -> List[Dict[str, Any]]:
    """Анатомія всіх цікавих D1-бакетів символу (пропуск D1 або пропуски M1)."""
    calendars, rejected = resolve_symbol_calendars(cfg, [symbol], where="d1_gap_anatomy")
    if rejected or symbol not in calendars:
        raise SystemExit("ANATOMY_CALENDAR_MISSING symbol=%s" % symbol)
    is_trading = calendars[symbol].is_trading_minute
    anchor_ms = int(cfg.get("day_anchor_offset_s_d1", 0)) * 1000

    if dates:
        buckets = [
            int(dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()) * 1000
            + anchor_ms
            for d in dates
        ]
    else:
        first_m1 = _first_part_day_ms(data_root, symbol, TF_M1_S)
        start = max(now_ms - days * TF_D1_MS, first_m1 if first_m1 is not None else now_ms)
        buckets = expected_bucket_opens(start, now_ms, TF_D1_MS, anchor_ms, is_trading)

    d1_opens = _all_opens_by_glob(data_root, symbol, TF_D1_S)
    budget = MAX_MID_SESSION_GAPS_BY_TF.get(TF_D1_S, 3)
    rows: List[Dict[str, Any]] = []
    for b0 in buckets:
        slots = [t for t in range(b0, b0 + TF_D1_MS, TF_M1_MS) if is_trading(t)]
        present = _load_opens(data_root, symbol, TF_M1_S, b0, b0 + TF_D1_MS)
        row = analyze_bucket(slots, present, scatter_run_max=scatter_run_max)
        row["symbol"] = symbol
        row["bucket_open_ms"] = b0
        row["session_date"] = dt.datetime.fromtimestamp(
            (b0 + TF_D1_MS) / 1000, dt.timezone.utc
        ).strftime("%Y-%m-%d %a")
        row["d1_present"] = b0 in d1_opens
        # Чи відмовив би нинішній бюджет (ADR-0005): рахує mid-session прогони.
        mid = row.get("missing", 0)
        if row.get("missing_at_session_open"):
            mid -= 1
        if row.get("missing_at_session_close"):
            mid -= 1
        row["mid_session_missing"] = max(0, mid)
        row["legacy_budget"] = budget
        row["legacy_would_refuse"] = row["mid_session_missing"] > budget
        if not row["d1_present"] and not row["legacy_would_refuse"]:
            # Бар мав бути: бюджет не перевищено, а бару немає — не політика, а запис.
            row["class"] = "cascade_hole"
        if row["class"] != "clean" or not row["d1_present"]:
            rows.append(row)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Анатомія пропусків D1 (ADR-0092 P1)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--symbol", action="append", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--date", action="append", default=None, help="Конкретна торгова доба YYYY-MM-DD")
    ap.add_argument("--scatter-run-max", type=int, default=DEFAULT_SCATTER_RUN_MAX)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    cfg = load_system_config(args.config or pick_config_path())
    data_root = cfg.get("data_root", "data_v3")
    symbols = list(cfg.get("symbols", [])) if args.all else (args.symbol or [])
    if not symbols:
        raise SystemExit("Вкажи --symbol або --all")

    now_ms = int(time.time() * 1000)
    rows: List[Dict[str, Any]] = []
    for sym in symbols:
        rows.extend(
            analyze_symbol(
                cfg, data_root, sym,
                days=args.days, dates=args.date or [],
                scatter_run_max=args.scatter_run_max, now_ms=now_ms,
            )
        )

    log.info(
        "%-9s %-14s %-14s %6s %7s %7s %7s %7s %6s %s",
        "symbol", "доба", "клас", "покр%", "пропущ", "max_run", "розсип", "mid", "D1", "бюджет",
    )
    for r in sorted(rows, key=lambda x: (x["symbol"], x["bucket_open_ms"])):
        log.info(
            "%-9s %-14s %-14s %6.2f %7d %7d %7d %7d %6s %s",
            r["symbol"], r["session_date"], r["class"], r.get("coverage_pct", 0.0),
            r.get("missing", 0), r.get("max_run", 0), r.get("scatter", 0),
            r.get("mid_session_missing", 0), "є" if r["d1_present"] else "НЕМА",
            "відмова" if r.get("legacy_would_refuse") else "проходить",
        )
    by_class: Dict[str, int] = {}
    for r in rows:
        by_class[r["class"]] = by_class.get(r["class"], 0) + 1
    log.info("РАЗОМ: %d бакетів з дефектом; за класами: %s", len(rows), by_class)
    missing_d1 = [r for r in rows if not r["d1_present"]]
    log.info(
        "D1 відсутній: %d; з них поза бюджетом: %d, у межах бюджету (cascade_hole): %d",
        len(missing_d1),
        sum(1 for r in missing_d1 if r.get("legacy_would_refuse")),
        sum(1 for r in missing_d1 if not r.get("legacy_would_refuse")),
    )
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"rows": rows, "by_class": by_class}, fh, ensure_ascii=False, indent=1)
        log.info("JSON: %s", args.json)


if __name__ == "__main__":
    main()
