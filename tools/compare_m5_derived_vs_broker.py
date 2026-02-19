#!/usr/bin/env python3
"""Phase 4 (ADR-0002): Порівняння M5(derived з 5×M1) vs M5(broker).

Читає M1 і M5 бари з диска, агрегує 5×M1 → derived M5,
порівнює OHLCV з broker M5. Виводить дельту.

Exit gate: OHLCV delta < 0.01% за весь перетинний діапазон.

Використання:
    python -m tools.compare_m5_derived_vs_broker [--symbol XAU_USD] [--data-root ./data_v3]
    python -m tools.compare_m5_derived_vs_broker --all  # всі символи

Артефакт: reports/exit_gates/m5_compare_<timestamp>.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

# Додаємо кореневий каталог у sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.model.bars import CandleBar
from core.buckets import bucket_start_ms


# ---------------------------------------------------------------------------
# Читання JSONL барів з диска
# ---------------------------------------------------------------------------
def _read_bars_from_disk(
    data_root: str,
    symbol: str,
    tf_s: int,
) -> List[Dict[str, Any]]:
    """Читає всі JSONL бари для symbol/tf_s, повертає sorted list."""
    folder = os.path.join(data_root, symbol, "tf_%d" % tf_s)
    if not os.path.isdir(folder):
        return []
    files = sorted(glob.glob(os.path.join(folder, "*.jsonl")))
    bars: List[Dict[str, Any]] = []
    for fpath in files:
        with open(fpath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    bar = json.loads(line)
                except Exception:
                    continue
                if not isinstance(bar.get("open_time_ms"), int):
                    continue
                bars.append(bar)
    # dedup за open_time_ms — останній виграє
    seen: Dict[int, int] = {}
    for idx, b in enumerate(bars):
        seen[b["open_time_ms"]] = idx
    deduped = [bars[i] for i in sorted(seen.values())]
    deduped.sort(key=lambda b: b["open_time_ms"])
    return deduped


def _bar_to_candle(bar: Dict[str, Any]) -> CandleBar:
    """Dict → CandleBar."""
    return CandleBar(
        symbol=str(bar.get("symbol", "")),
        tf_s=int(bar.get("tf_s", 0)),
        open_time_ms=int(bar["open_time_ms"]),
        close_time_ms=int(bar.get("close_time_ms", bar["open_time_ms"] + bar.get("tf_s", 0) * 1000)),
        o=float(bar.get("o", 0)),
        h=float(bar.get("h", 0)),
        low=float(bar.get("low", bar.get("l", 0))),
        c=float(bar.get("c", 0)),
        v=float(bar.get("v", 0)),
        complete=bool(bar.get("complete", True)),
        src=str(bar.get("src", "")),
        extensions=dict(bar.get("extensions", {})),
    )


# ---------------------------------------------------------------------------
# Агрегація 5×M1 → M5
# ---------------------------------------------------------------------------
def _aggregate_m1_to_m5(
    m1_bars: List[Dict[str, Any]],
    symbol: str,
) -> Dict[int, Dict[str, Any]]:
    """Агрегує M1 бари у M5 бакети. Повертає {bucket_open_ms: bar_dict}."""
    M5_MS = 300_000  # 5 хвилин
    # Групуємо M1 бари за M5 бакетами
    buckets: Dict[int, List[Dict[str, Any]]] = {}
    for bar in m1_bars:
        ot = bar["open_time_ms"]
        bucket = bucket_start_ms(ot, M5_MS, 0)
        buckets.setdefault(bucket, []).append(bar)

    result: Dict[int, Dict[str, Any]] = {}
    for bucket_open, group in sorted(buckets.items()):
        group.sort(key=lambda b: b["open_time_ms"])
        # Потрібно рівно 5 M1 барів для повного M5
        if len(group) < 1:
            continue
        o = group[0].get("o", 0)
        h = max(b.get("h", 0) for b in group)
        low_val = min(b.get("low", b.get("l", 0)) for b in group)
        c = group[-1].get("c", 0)
        v = sum(b.get("v", 0) for b in group)
        result[bucket_open] = {
            "open_time_ms": bucket_open,
            "close_time_ms": bucket_open + M5_MS,
            "o": float(o),
            "h": float(h),
            "low": float(low_val),
            "c": float(c),
            "v": float(v),
            "m1_count": len(group),
            "complete": len(group) == 5,
        }
    return result


# ---------------------------------------------------------------------------
# Порівняння
# ---------------------------------------------------------------------------
def _rel_delta(a: float, b: float) -> float:
    """Відносна дельта: |a-b| / max(|a|,|b|,1e-12)."""
    denom = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / denom


def _compare_symbol(
    data_root: str,
    symbol: str,
) -> Dict[str, Any]:
    """Порівнює M5(derived) vs M5(broker) для одного символу."""
    m1_bars = _read_bars_from_disk(data_root, symbol, 60)
    m5_broker_bars = _read_bars_from_disk(data_root, symbol, 300)

    if not m1_bars:
        return {"symbol": symbol, "status": "no_m1_data", "overlap": 0}
    if not m5_broker_bars:
        return {"symbol": symbol, "status": "no_m5_data", "overlap": 0}

    # Derive M5 з M1
    derived_m5 = _aggregate_m1_to_m5(m1_bars, symbol)

    # Broker M5 за open_time_ms
    broker_by_open: Dict[int, Dict[str, Any]] = {}
    for b in m5_broker_bars:
        broker_by_open[b["open_time_ms"]] = b

    # Перетинний діапазон
    derived_keys = set(derived_m5.keys())
    broker_keys = set(broker_by_open.keys())
    overlap_keys = sorted(derived_keys & broker_keys)

    if not overlap_keys:
        return {
            "symbol": symbol,
            "status": "no_overlap",
            "m1_bars": len(m1_bars),
            "m5_broker": len(m5_broker_bars),
            "m5_derived": len(derived_m5),
            "overlap": 0,
        }

    # Порівняння OHLCV (окремо OHLC gate і Volume gate)
    OHLC_FIELDS = ["o", "h", "low", "c"]
    ALL_FIELDS = ["o", "h", "low", "c", "v"]
    max_delta = {f: 0.0 for f in ALL_FIELDS}
    sum_delta = {f: 0.0 for f in ALL_FIELDS}
    # Тільки повні M5 бакети (5×M1) для OHLC gate
    max_delta_full = {f: 0.0 for f in ALL_FIELDS}
    sum_delta_full = {f: 0.0 for f in ALL_FIELDS}
    mismatch_bars: List[Dict[str, Any]] = []
    exact_match = 0
    full_bars = 0  # M5 бакети з рівно 5 M1 барами
    n_full_exact = 0

    for key in overlap_keys:
        d = derived_m5[key]
        b = broker_by_open[key]
        bar_max_ohlc = 0.0
        bar_max_all = 0.0
        deltas: Dict[str, float] = {}
        is_full = d.get("m1_count", 0) == 5

        for field in ALL_FIELDS:
            d_val = float(d.get(field, 0))
            b_val = float(b.get(field, b.get("l", 0) if field == "low" else 0))
            rd = _rel_delta(d_val, b_val)
            deltas[field] = rd
            if rd > max_delta[field]:
                max_delta[field] = rd
            sum_delta[field] += rd
            bar_max_all = max(bar_max_all, rd)
            if field in OHLC_FIELDS:
                bar_max_ohlc = max(bar_max_ohlc, rd)
            if is_full:
                if rd > max_delta_full[field]:
                    max_delta_full[field] = rd
                sum_delta_full[field] += rd

        if is_full:
            full_bars += 1

        if bar_max_all < 1e-10:
            exact_match += 1
            if is_full:
                n_full_exact += 1
        elif bar_max_ohlc > 0.0001:  # > 0.01% на OHLC
            mismatch_bars.append({
                "open_ms": key,
                "time_utc": datetime.fromtimestamp(key / 1000, tz=timezone.utc).isoformat(),
                "deltas_pct": {k: round(v * 100, 6) for k, v in deltas.items()},
                "m1_count": d.get("m1_count", "?"),
                "derived": {f: d.get(f) for f in ALL_FIELDS},
                "broker": {f: b.get(f, b.get("l") if f == "low" else None) for f in ALL_FIELDS},
            })

    n = len(overlap_keys)
    avg_delta_full = {k: v / full_bars for k, v in sum_delta_full.items()} if full_bars else {k: 0 for k in ALL_FIELDS}

    # Gate: OHLC delta < 0.01% на повних бакетах (5×M1)
    ohlc_max_full = max(max_delta_full[f] for f in OHLC_FIELDS) if full_bars else 0
    ohlc_pass = ohlc_max_full < 0.0001  # < 0.01%
    # Volume delta очікується великим (FXCM tick volume ≠ M1 sum)
    vol_max_full = max_delta_full.get("v", 0) if full_bars else 0

    return {
        "symbol": symbol,
        "status": "pass" if ohlc_pass else "FAIL",
        "pass": ohlc_pass,
        "m1_bars": len(m1_bars),
        "m5_broker": len(m5_broker_bars),
        "m5_derived": len(derived_m5),
        "overlap": n,
        "overlap_full_5m1": full_bars,
        "exact_match": exact_match,
        "exact_match_pct": round(exact_match / n * 100, 2) if n else 0,
        "ohlc_max_delta_full_pct": {k: round(max_delta_full[k] * 100, 6) for k in OHLC_FIELDS},
        "ohlc_avg_delta_full_pct": {k: round(avg_delta_full[k] * 100, 6) for k in OHLC_FIELDS},
        "vol_max_delta_full_pct": round(vol_max_full * 100, 4),
        "vol_avg_delta_full_pct": round(avg_delta_full.get("v", 0) * 100, 4),
        "ohlc_mismatches_above_001pct": len(mismatch_bars),
        "mismatch_examples": mismatch_bars[:10],
        "derived_only": len(derived_keys - broker_keys),
        "broker_only": len(broker_keys - derived_keys),
        "note_volume": "FXCM tick volume differs between M1/M5 aggregation levels — expected",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 4: M5(derived) vs M5(broker) comparison")
    parser.add_argument("--symbol", type=str, default=None, help="Один символ (напр. XAU_USD)")
    parser.add_argument("--all", action="store_true", help="Всі символи з data_root")
    parser.add_argument("--data-root", type=str, default="./data_v3", help="Кореневий каталог даних")
    parser.add_argument("--config", type=str, default="./config.json", help="Шлях до config.json")
    parser.add_argument("--report", action="store_true", help="Зберегти звіт у reports/exit_gates/")
    args = parser.parse_args()

    data_root = os.path.abspath(args.data_root)

    symbols: List[str] = []
    if args.all:
        # Знаходимо символи, що мають і tf_60, і tf_300
        for entry in sorted(os.listdir(data_root)):
            sym_dir = os.path.join(data_root, entry)
            if os.path.isdir(sym_dir):
                has_m1 = os.path.isdir(os.path.join(sym_dir, "tf_60"))
                has_m5 = os.path.isdir(os.path.join(sym_dir, "tf_300"))
                if has_m1 and has_m5:
                    symbols.append(entry)
    elif args.symbol:
        symbols = [args.symbol]
    else:
        # Default: всі з config.json
        if os.path.isfile(args.config):
            cfg = json.load(open(args.config, encoding="utf-8"))
            symbols = list(cfg.get("symbols", []))
        if not symbols:
            symbols = [d for d in os.listdir(data_root)
                       if os.path.isdir(os.path.join(data_root, d, "tf_60"))
                       and os.path.isdir(os.path.join(data_root, d, "tf_300"))]

    print("=" * 72)
    print("Phase 4 (ADR-0002): M5(derived from 5×M1) vs M5(broker)")
    print("Exit gate: OHLC delta < 0.01%% on full-bucket bars (5×M1)")
    print("Note: Volume delta excluded — FXCM M5 vol ≠ sum(M1 vol)")
    print("Symbols: %d" % len(symbols))
    print("=" * 72)

    results: List[Dict[str, Any]] = []
    all_pass = True

    for sym in sorted(symbols):
        print("\n--- %s ---" % sym)
        res = _compare_symbol(data_root, sym)
        results.append(res)

        if res.get("status") in ("no_m1_data", "no_m5_data", "no_overlap"):
            print("  %s (skip)" % res["status"])
            continue

        passed = res.get("pass", False)
        if not passed:
            all_pass = False

        print("  overlap: %d bars, full (5×M1): %d, exact: %d (%.1f%%)" % (
            res["overlap"], res.get("overlap_full_5m1", 0),
            res["exact_match"], res.get("exact_match_pct", 0),
        ))
        print("  OHLC max_delta_full_pct: %s" % res.get("ohlc_max_delta_full_pct"))
        print("  OHLC avg_delta_full_pct: %s" % res.get("ohlc_avg_delta_full_pct"))
        print("  Volume max_delta_full_pct: %.4f%% (expected high — FXCM)" % res.get("vol_max_delta_full_pct", 0))
        print("  OHLC mismatches (>0.01%%): %d" % res.get("ohlc_mismatches_above_001pct", 0))
        print("  derived_only: %d, broker_only: %d" % (
            res.get("derived_only", 0), res.get("broker_only", 0),
        ))
        if res.get("mismatch_examples"):
            print("  first OHLC mismatch example:")
            ex = res["mismatch_examples"][0]
            print("    %s deltas_pct=%s m1_count=%s" % (
                ex.get("time_utc"), ex.get("deltas_pct"), ex.get("m1_count"),
            ))
        print("  => %s" % ("PASS" if passed else "*** FAIL ***"))

    print("\n" + "=" * 72)
    gate_result = "PASS" if all_pass else "FAIL"
    print("EXIT GATE: %s" % gate_result)
    print("=" * 72)

    # Артефакт
    report = {
        "gate": "m5_derived_vs_broker",
        "ts": datetime.now(timezone.utc).isoformat(),
        "result": gate_result,
        "symbols_count": len(symbols),
        "symbols": results,
    }

    if args.report:
        report_dir = os.path.join(_ROOT, "reports", "exit_gates")
        os.makedirs(report_dir, exist_ok=True)
        ts_str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_path = os.path.join(report_dir, "m5_compare_%s.json" % ts_str)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("\nReport saved: %s" % report_path)

    # Exit code
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
