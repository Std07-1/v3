"""Tail integrity scanner: перевірка цілісності даних на диску.

Сканує ВСІ символи × ВСІ TF за останні N днів.
Перевіряє: монотонність, дублікати, геометрію (close=open+tf_ms),
bucket alignment, calendar-aware gaps.

Використання:
  python -m tools.tail_integrity_scanner --days 7
  python -m tools.tail_integrity_scanner --days 1 --symbols XAU_USD
  python -m tools.tail_integrity_scanner --days 3 --json-only

Виводить звіт у reports/tail_audit/<timestamp>.json
та reports/tail_audit/latest.json (для моніторингу).

Архітектурний шар: tools/ (one-shot утиліта, не runtime).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_loader import load_system_config
from core.buckets import bucket_start_ms
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.tick_common import calendar_from_group

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("tail_integrity")

TF_NAMES = {60: "M1", 180: "M3", 300: "M5", 900: "M15",
             1800: "M30", 3600: "H1", 14400: "H4", 86400: "D1"}
# Скануємо лише derived TFs (M1 = ingest, D1 = broker)
SCAN_TFS = [60, 180, 300, 900, 1800, 3600, 14400]


# ---------------------------------------------------------------------------
# Допоміжні функції
# ---------------------------------------------------------------------------

def _build_calendar(cfg: dict, symbol: str) -> Optional[MarketCalendar]:
    groups = cfg.get("market_calendar_by_group", {})
    sym_groups = cfg.get("market_calendar_symbol_groups", {})
    gname = sym_groups.get(symbol) or sym_groups.get(symbol.replace("_", "/"))
    if not gname:
        return None
    gcfg = groups.get(gname)
    if not isinstance(gcfg, dict):
        return None
    return calendar_from_group(gcfg)


def _symbols(cfg: dict) -> List[str]:
    raw = cfg.get("symbols", [])
    return [str(s) for s in raw if str(s).strip()] if isinstance(raw, list) else []


def _load_bars(data_root: str, symbol: str, tf_s: int, day: str) -> List[dict]:
    sym_dir = symbol.replace("/", "_")
    path = os.path.join(data_root, sym_dir, f"tf_{tf_s}", f"part-{day}.jsonl")
    if not os.path.isfile(path):
        return []
    bars = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                bars.append(json.loads(line))
            except Exception:
                continue
    return bars


def _check_geometry(
    bars: List[dict], tf_s: int, anchor_s: int,
    alt_anchors: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Перевірка геометрії: sorted, no-dup, close_ms, alignment."""
    issues = []  # type: List[str]
    tf_ms = tf_s * 1000
    all_anchors_ms = [anchor_s * 1000]
    if alt_anchors:
        all_anchors_ms.extend(a * 1000 for a in alt_anchors if a is not None)
    prev_ot = -1
    dup_count = 0
    unsorted_count = 0
    close_bad = 0
    align_bad = 0

    for b in bars:
        ot = b.get("open_time_ms", 0)
        ct = b.get("close_time_ms", 0)
        if ot <= prev_ot and prev_ot >= 0:
            if ot == prev_ot:
                dup_count += 1
            else:
                unsorted_count += 1
        prev_ot = ot
        if ct != ot + tf_ms:
            close_bad += 1
        if tf_s >= 14400:
            # H4+ — перевірити проти всіх відомих anchors
            aligned = any((ot - a) % tf_ms == 0 for a in all_anchors_ms)
            if not aligned:
                align_bad += 1
        else:
            if ot % tf_ms != 0:
                align_bad += 1

    if dup_count:
        issues.append(f"duplicates={dup_count}")
    if unsorted_count:
        issues.append(f"unsorted={unsorted_count}")
    if close_bad:
        issues.append(f"close_time_bad={close_bad}")
    if align_bad:
        issues.append(f"alignment_bad={align_bad}")
    return {
        "bar_count": len(bars),
        "duplicates": dup_count,
        "unsorted": unsorted_count,
        "close_bad": close_bad,
        "align_bad": align_bad,
        "issues": issues,
        "ok": len(issues) == 0,
    }


def _expected_buckets_in_day(
    day_str: str, tf_s: int, anchor_s: int, cal: Optional[MarketCalendar]
) -> List[int]:
    """Список очікуваних bucket open_ms за кайлендарем для UTC-дня."""
    d = dt.datetime.strptime(day_str, "%Y%m%d").replace(tzinfo=dt.timezone.utc)
    day_start_ms = int(d.timestamp() * 1000)
    day_end_ms = day_start_ms + 86400_000
    tf_ms = tf_s * 1000
    anchor_ms = anchor_s * 1000

    first = bucket_start_ms(day_start_ms, tf_ms, anchor_ms)
    if first < day_start_ms:
        first += tf_ms

    expected = []
    ot = first
    while ot < day_end_ms:
        # Перевірити чи хоч одна хвилина в bucket торгова
        if cal is not None:
            has_trading = False
            check_step = 60_000  # 1 хвилина
            t = ot
            while t < ot + tf_ms:
                if cal.is_trading_minute(t):
                    has_trading = True
                    break
                t += check_step
            if has_trading:
                expected.append(ot)
        else:
            expected.append(ot)
        ot += tf_ms
    return expected


def scan_symbol_tf(
    data_root: str,
    symbol: str,
    tf_s: int,
    day_keys: List[str],
    anchor_s: int,
    cal: Optional[MarketCalendar],
    now_ms: int = 0,
    alt_anchors: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Сканування одного символу × TF за вказані дні."""
    total_bars = 0
    total_expected = 0
    total_unexpected_gaps = 0
    all_issues = []  # type: List[str]
    gap_details = []  # type: List[Dict]
    geom_ok = True

    for day in day_keys:
        bars = _load_bars(data_root, symbol, tf_s, day)
        geom = _check_geometry(bars, tf_s, anchor_s, alt_anchors)
        total_bars += geom["bar_count"]
        if not geom["ok"]:
            geom_ok = False
            all_issues.extend(
                [f"{day}: {i}" for i in geom["issues"]]
            )

        # Gap analysis (тільки для derived TFs >= M5)
        if tf_s >= 300:
            existing = {b["open_time_ms"] for b in bars}
            expected = _expected_buckets_in_day(day, tf_s, anchor_s, cal)
            # Виключити бакети з майбутнього (not yet closed)
            tf_ms = tf_s * 1000
            cutoff = now_ms - tf_ms if now_ms > 0 else 0
            for ot in expected:
                if cutoff > 0 and ot >= cutoff:
                    continue  # бакет ще не завершений
                total_expected += 1
                if ot not in existing:
                    total_unexpected_gaps += 1
                    utc_str = dt.datetime.fromtimestamp(
                        ot / 1000, tz=dt.timezone.utc
                    ).strftime("%Y-%m-%d %H:%M")
                    gap_details.append({
                        "day": day,
                        "open_time_ms": ot,
                        "utc": utc_str,
                    })

    return {
        "symbol": symbol,
        "tf_s": tf_s,
        "tf_name": TF_NAMES.get(tf_s, str(tf_s)),
        "days": day_keys,
        "total_bars": total_bars,
        "total_expected": total_expected,
        "unexpected_gaps": total_unexpected_gaps,
        "geometry_ok": geom_ok,
        "issues": all_issues,
        "gap_details": gap_details[:50],  # обмежуємо вивід
        "status": "PASS" if geom_ok and total_unexpected_gaps == 0 else "FAIL",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Tail integrity scanner")
    ap.add_argument("--days", type=int, default=3, help="Скільки днів назад сканувати")
    ap.add_argument("--symbols", help="Символи через кому (або --all)")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--json-only", action="store_true", help="Тільки JSON, без тексту")
    args = ap.parse_args()

    cfg = load_system_config(args.config)
    data_root = cfg.get("data_root", "./data_v3")
    anchor_s = cfg.get("day_anchor_offset_s", 68400)
    alt_anchors = [
        cfg.get("day_anchor_offset_s_alt"),
        cfg.get("day_anchor_offset_s_alt2"),
    ]

    sym_list = _symbols(cfg)
    if args.symbols:
        sym_list = [s.strip() for s in args.symbols.split(",")]

    now = dt.datetime.now(dt.timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    day_keys = [
        (now - dt.timedelta(days=i)).strftime("%Y%m%d")
        for i in range(args.days - 1, -1, -1)
    ]

    t0 = time.time()
    results = []
    pass_count = 0
    fail_count = 0

    for symbol in sym_list:
        cal = _build_calendar(cfg, symbol)
        for tf_s in SCAN_TFS:
            r = scan_symbol_tf(data_root, symbol, tf_s, day_keys, anchor_s, cal, now_ms, alt_anchors)
            results.append(r)
            if r["status"] == "PASS":
                pass_count += 1
            else:
                fail_count += 1
                if not args.json_only:
                    log.warning(
                        "FAIL %s %s: gaps=%d issues=%s",
                        symbol, r["tf_name"], r["unexpected_gaps"],
                        r["issues"][:3] if r["issues"] else "gaps_only",
                    )

    elapsed = time.time() - t0

    report = {
        "ts": now.isoformat(),
        "scan_days": day_keys,
        "symbols_count": len(sym_list),
        "tfs_scanned": [TF_NAMES.get(t, str(t)) for t in SCAN_TFS],
        "total_checks": pass_count + fail_count,
        "pass": pass_count,
        "fail": fail_count,
        "elapsed_s": round(elapsed, 2),
        "overall": "PASS" if fail_count == 0 else "FAIL",
        "results": results,
    }

    # Зберегти звіт
    report_dir = os.path.join("reports", "tail_audit")
    os.makedirs(report_dir, exist_ok=True)
    ts_str = now.strftime("%Y%m%dT%H%M%SZ")
    report_path = os.path.join(report_dir, f"{ts_str}.json")
    latest_path = os.path.join(report_dir, "latest.json")

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    if not args.json_only:
        log.info(
            "Scan done: %d checks (%d PASS, %d FAIL) in %.1fs",
            pass_count + fail_count, pass_count, fail_count, elapsed,
        )
        log.info("Report: %s", report_path)

    # Summary для моніторингу (компактний файл)
    summary = {
        "ts": now.isoformat(),
        "overall": report["overall"],
        "pass": pass_count,
        "fail": fail_count,
        "elapsed_s": report["elapsed_s"],
        "failed_items": [
            {"symbol": r["symbol"], "tf": r["tf_name"],
             "gaps": r["unexpected_gaps"], "issues": r["issues"][:2]}
            for r in results if r["status"] == "FAIL"
        ][:20],
    }
    summary_path = os.path.join(report_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    return 1 if fail_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
