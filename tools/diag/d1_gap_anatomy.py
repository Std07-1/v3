"""Аудит: анатомія пропусків у D1-бакетах (ADR-0092 P1, калібрування порогів).

Навіщо: політика повноти D1 (ADR-0092 шар 2) має відрізняти речі, які сьогодні
однаково дають «бару немає»:

  - `boundary_only` — бракує лише граничних хвилин сесії; для derive вони безкоштовні,
    дефекту немає (найчастіший випадок у проді: перша хвилина сесії 22:00);
  - `edge_close` / `edge_open` — довгий суцільний пропуск на краю сесії: рання сесія
    свята АБО невідповідний DST-якір (саме це ловить нинішній бюджет 15);
  - `feed_gap` — довгий пропуск усередині сесії: реальний обрив фіду;
  - `thin_scatter` — розсип коротких пропусків: неліквідні хвилини (метали ввечері);
  - `full_closure` — торгівлі не було майже весь бакет (повне свято);
  - `cascade_hole` — бар мав збудуватись (бюджет не перевищено), а його немає: це не
    політика, а незаписаний бар.

Без цих чисел пороги `scatter_run_max`, `scatter_ratio` і `max_run` — гіпотеза, тому
ADR-0092 не може стати Accepted до прогону цього інструмента.

НІЧОГО НЕ ПИШЕ (крім --json звіту): лише читає SSOT-JSONL і рахує. Календар — через
канонічну фабрику (`resolve_symbol_calendars`), без ще однієї копії будівника (ADR-0092 шар 0).

Використання:
    python -m tools.diag.d1_gap_anatomy --all --days 180
    python -m tools.diag.d1_gap_anatomy --symbol XAU/USD --date 2026-09-07 --date 2026-05-25
    python -m tools.diag.d1_gap_anatomy --all --days 180 --json /tmp/anatomy.json --show-boundary
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from core.config_loader import htf_anchor_rule_resolver, load_system_config, pick_config_path
from core.derive import MAX_MID_SESSION_GAPS_BY_TF
from core.health.measures import expected_bucket_opens
from runtime.ingest.tick_common import resolve_symbol_calendars

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

TF_M1_S = 60
TF_M1_MS = 60_000
TF_D1_S = 86400
TF_D1_MS = 86_400_000

# Прогін mid-session пропусків довший за це — «довгий»; саме довгі прогони ловить
# нинішній бюджет D1 (ADR-0005), і саме вони відрізняють DST/обрив від неліквідності.
LONG_RUN_MIN = 45
# Пропуск у прогоні не довшому за це вважаємо «розсипом» (кандидат на окремий бюджет).
DEFAULT_SCATTER_RUN_MAX = 3
# Пороги, за якими рахуємо розсип для калібрування (ADR-0092 Open Question 3).
CALIBRATION_RUN_MAXES = (1, 2, 3, 5, 10)
# Скільки торгових хвилин має лишитись, щоб бакет не вважався повним закриттям.
FULL_CLOSURE_TOLERANCE = 20


def _runs(indices: Sequence[int]) -> List[Tuple[int, int]]:
    """Злиті прогони послідовних індексів."""
    out: List[Tuple[int, int]] = []
    for i in indices:
        if out and i == out[-1][1] + 1:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def analyze_bucket(
    slots: Sequence[int],
    present: Set[int],
    *,
    scatter_run_max: int = DEFAULT_SCATTER_RUN_MAX,
    is_trading_fn: Optional[Callable[[int], bool]] = None,
) -> Dict[str, Any]:
    """Анатомія одного D1-бакета. Чиста функція: ніякого I/O.

    ``slots`` — торгові хвилини бакета за календарем (зростаюче), ``present`` — ті, для
    яких є M1-бар. Прогони рахуються по ПОСЛІДОВНОСТІ СЛОТІВ, а не по астрономічному
    часу: derive рахує пропуски саме так (перерви не розривають прогін).

    Гранична хвилина (торгова хвилина біля неторгового сусіда) для derive безкоштовна
    (``_collect_boundary_tolerant``), тому класифікація і розсип рахуються по MID-SESSION
    пропусках. Межу визначає календар, якщо він переданий: інакше внутрішні межі
    (обідні перерви HKG33) хибно рахувалися б як mid.
    """
    total = len(slots)
    if total == 0:
        return {"expected": 0, "class": "no_trading_minutes"}

    if is_trading_fn is None:
        def _boundary(i: int) -> bool:
            return i == 0 or i == total - 1
    else:
        def _boundary(i: int) -> bool:
            t = slots[i]
            return not is_trading_fn(t - TF_M1_MS) or not is_trading_fn(t + TF_M1_MS)

    missing_idx = [i for i, ms in enumerate(slots) if ms not in present]
    mid_idx = [i for i in missing_idx if not _boundary(i)]
    all_runs = _runs(missing_idx)
    mid_runs = _runs(mid_idx)
    missing = len(missing_idx)
    mid_missing = len(mid_idx)
    max_run = max((b - a + 1 for a, b in all_runs), default=0)
    mid_max_run = max((b - a + 1 for a, b in mid_runs), default=0)
    scatter = sum(b - a + 1 for a, b in mid_runs if b - a + 1 <= scatter_run_max)
    longest = max(all_runs, key=lambda r: r[1] - r[0], default=None) if all_runs else None
    at_open = any(a == 0 for a, _b in all_runs)
    at_close = any(b == total - 1 for _a, b in all_runs)

    if missing == 0:
        klass = "clean"
    elif mid_missing == 0:
        klass = "boundary_only"
    elif missing >= total - FULL_CLOSURE_TOLERANCE:
        klass = "full_closure"
    elif mid_max_run >= LONG_RUN_MIN and longest is not None and longest[1] == total - 1:
        klass = "edge_close"
    elif mid_max_run >= LONG_RUN_MIN and longest is not None and longest[0] == 0:
        klass = "edge_open"
    elif mid_max_run >= LONG_RUN_MIN:
        klass = "feed_gap"
    elif scatter == mid_missing:
        klass = "thin_scatter"
    else:
        klass = "mixed"

    return {
        "expected": total,
        "present": total - missing,
        "missing": missing,
        "mid_session_missing": mid_missing,
        "coverage_pct": round(100.0 * (total - missing) / total, 3),
        "max_run": max_run,
        "mid_max_run": mid_max_run,
        "runs": len(all_runs),
        "mid_runs": len(mid_runs),
        "scatter": scatter,
        "mid_ratio_pct": round(100.0 * mid_missing / total, 3),
        "scatter_by_run_max": {
            str(k): sum(b - a + 1 for a, b in mid_runs if b - a + 1 <= k)
            for k in CALIBRATION_RUN_MAXES
        },
        "missing_at_session_open": at_open,
        "missing_at_session_close": at_close,
        "class": klass,
    }


def _load_opens(data_root: str, symbol: str, tf_s: int, lo_ms: int, hi_ms: int) -> Set[int]:
    """open_time_ms з part-файлів у [lo, hi). Читає лише потрібні дні."""
    sym_dir = symbol.replace("/", "_")
    days = set()
    cur = lo_ms - TF_D1_MS
    while cur < hi_ms + TF_D1_MS:
        days.add(dt.datetime.fromtimestamp(max(0, cur) / 1000, dt.timezone.utc).strftime("%Y%m%d"))
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
    return int(
        dt.datetime.strptime(days[0], "%Y%m%d").replace(tzinfo=dt.timezone.utc).timestamp()
    ) * 1000


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
    """Анатомія D1-бакетів символу. Повертає рядки з дефектом або без бару."""
    calendars, rejected = resolve_symbol_calendars(cfg, [symbol], where="d1_gap_anatomy")
    if rejected or symbol not in calendars:
        raise SystemExit("ANATOMY_CALENDAR_MISSING symbol=%s" % symbol)
    is_trading = calendars[symbol].is_trading_minute
    anchor_ms = int(cfg.get("day_anchor_offset_s_d1", 0)) * 1000

    if dates:
        buckets = [
            int(
                dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
            ) * 1000
            + anchor_ms
            - TF_D1_MS
            for d in dates
        ]
    else:
        first_m1 = _first_part_day_ms(data_root, symbol, TF_M1_S)
        start = max(now_ms - days * TF_D1_MS, first_m1 if first_m1 is not None else now_ms)
        # Очікувані бакети — сезонна сітка символу (ADR-0095 S5a, API health); вікна й --date — S5b.
        rule = htf_anchor_rule_resolver(cfg)(symbol)
        buckets = expected_bucket_opens(start, now_ms, tf_s=TF_D1_S, rule=rule, is_trading_fn=is_trading)

    d1_opens = _all_opens_by_glob(data_root, symbol, TF_D1_S)
    budget = MAX_MID_SESSION_GAPS_BY_TF.get(TF_D1_S, 3)
    rows: List[Dict[str, Any]] = []
    for b0 in buckets:
        slots = [t for t in range(b0, b0 + TF_D1_MS, TF_M1_MS) if is_trading(t)]
        present = _load_opens(data_root, symbol, TF_M1_S, b0, b0 + TF_D1_MS)
        row = analyze_bucket(
            slots, present, scatter_run_max=scatter_run_max, is_trading_fn=is_trading
        )
        row["symbol"] = symbol
        row["bucket_open_ms"] = b0
        row["session_date"] = dt.datetime.fromtimestamp(
            (b0 + TF_D1_MS) / 1000, dt.timezone.utc
        ).strftime("%Y-%m-%d %a")
        row["d1_present"] = b0 in d1_opens
        row["legacy_budget"] = budget
        row["legacy_would_refuse"] = row.get("mid_session_missing", 0) > budget
        if not row["d1_present"] and not row["legacy_would_refuse"]:
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
    ap.add_argument("--date", action="append", default=None, help="Торгова доба YYYY-MM-DD")
    ap.add_argument("--scatter-run-max", type=int, default=DEFAULT_SCATTER_RUN_MAX)
    ap.add_argument("--show-boundary", action="store_true", help="Не ховати boundary_only")
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

    shown = [r for r in rows if args.show_boundary or r["class"] != "boundary_only"]
    log.info(
        "%-9s %-14s %-13s %6s %6s %6s %7s %7s %5s %s",
        "symbol", "доба", "клас", "покр%", "проп", "mid", "max_run", "розсип", "D1", "бюджет",
    )
    for r in sorted(shown, key=lambda x: (x["symbol"], x["bucket_open_ms"])):
        log.info(
            "%-9s %-14s %-13s %6.2f %6d %6d %7d %7d %5s %s",
            r["symbol"], r["session_date"], r["class"], r.get("coverage_pct", 0.0),
            r.get("missing", 0), r.get("mid_session_missing", 0), r.get("mid_max_run", 0),
            r.get("scatter", 0), "є" if r["d1_present"] else "НЕМА",
            "відмова" if r.get("legacy_would_refuse") else "проходить",
        )

    by_class: Dict[str, int] = {}
    for r in rows:
        by_class[r["class"]] = by_class.get(r["class"], 0) + 1
    log.info("РАЗОМ %d бакетів (показано %d); класи: %s", len(rows), len(shown), by_class)

    absent = [r for r in rows if not r["d1_present"]]
    log.info(
        "D1 ВІДСУТНІЙ: %d | бюджет %d: поза ним %d, у межах (cascade_hole) %d",
        len(absent), rows[0]["legacy_budget"] if rows else 0,
        sum(1 for r in absent if r.get("legacy_would_refuse")),
        sum(1 for r in absent if not r.get("legacy_would_refuse")),
    )
    if absent:
        log.info("--- КАЛІБРУВАННЯ: розсип у відсутніх D1 при різних порогах прогону ---")
        log.info(
            "%-9s %-14s %-13s %6s %7s %s",
            "symbol", "доба", "клас", "mid", "max_run",
            " ".join("<=%d" % k for k in CALIBRATION_RUN_MAXES),
        )
        for r in sorted(absent, key=lambda x: (x["symbol"], x["bucket_open_ms"])):
            sb = r.get("scatter_by_run_max", {})
            log.info(
                "%-9s %-14s %-13s %6d %7d %s",
                r["symbol"], r["session_date"], r["class"], r.get("mid_session_missing", 0),
                r.get("mid_max_run", 0),
                " ".join("%4d" % sb.get(str(k), 0) for k in CALIBRATION_RUN_MAXES),
            )
        scatterish = [r for r in absent if r["class"] in ("thin_scatter", "mixed")]
        if scatterish:
            log.info(
                "Розсипні відмови: max(mid)=%d, max(mid_max_run)=%d, max(mid_ratio)=%.2f%% "
                "— звідси нижні межі scatter_ratio і scatter_run_max",
                max(r["mid_session_missing"] for r in scatterish),
                max(r["mid_max_run"] for r in scatterish),
                max(r["mid_ratio_pct"] for r in scatterish),
            )
        edgeish = [r for r in absent if r["class"] in ("edge_close", "edge_open", "feed_gap")]
        if edgeish:
            log.info(
                "Довгі прогони (край/обрив): min(mid_max_run)=%d — верхня межа max_run мусить "
                "лишитись НИЖЧЕ за це, інакше DST і обриви перестануть ловитись",
                min(r["mid_max_run"] for r in edgeish),
            )
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"rows": rows, "by_class": by_class}, fh, ensure_ascii=False, indent=1)
        log.info("JSON: %s", args.json)


if __name__ == "__main__":
    main()
