"""Dedup M1 барів на диску + перебудова M3 з очищених M1.

Використання:
  python -m tools.dedup_rebuild_m1m3 --all
  python -m tools.dedup_rebuild_m1m3 --symbols "XAU/USD,XAG/USD"
  python -m tools.dedup_rebuild_m1m3 --all --dry-run
"""
import json
import os
import sys
import logging
import argparse
from collections import OrderedDict
from typing import List,  Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_loader import load_system_config, pick_config_path

log = logging.getLogger("dedup_m1m3")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ── константи ──
TF_M1 = 60
TF_M3 = 180
M1_MS = 60_000
M3_MS = 180_000


def _sym_dir(sym: str) -> str:
    """XAU/USD -> XAU_USD"""
    return sym.replace("/", "_")


def _read_all_bars(data_root: str, sym: str, tf_s: int) -> List[dict]:
    """Зчитує всі бари з JSONL для symbol+tf."""
    d = os.path.join(data_root, _sym_dir(sym), "tf_%d" % tf_s)
    if not os.path.isdir(d):
        return []
    bars = []
    for f in sorted(os.listdir(d)):
        if not f.endswith(".jsonl"):
            continue
        with open(os.path.join(d, f), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    bars.append(json.loads(line))
    return bars


def _dedup_bars(bars: List[dict]) -> List[dict]:
    """Дедуплікація по open_time_ms. Зберігаємо 'кращий' бар (complete > not, history > derived)."""
    by_key: "OrderedDict[int, dict]" = OrderedDict()
    _src_rank = {"history": 3, "derived": 2, "tick_promoted": 1}
    for b in bars:
        ot = b["open_time_ms"]
        if ot not in by_key:
            by_key[ot] = b
        else:
            existing = by_key[ot]
            # complete переважає
            if b.get("complete") and not existing.get("complete"):
                by_key[ot] = b
            elif b.get("complete") == existing.get("complete"):
                # history > derived > tick_promoted
                if _src_rank.get(b.get("src"), 0) > _src_rank.get(existing.get("src"), 0):
                    by_key[ot] = b
    return list(by_key.values())


def _write_bars(data_root: str, sym: str, tf_s: int, bars: List[dict], dry_run: bool) -> int:
    """Перезаписує JSONL файли (по днях). Повертає кількість записаних барів."""
    import datetime
    d = os.path.join(data_root, _sym_dir(sym), "tf_%d" % tf_s)
    os.makedirs(d, exist_ok=True)

    # Групуємо по UTC-даті
    by_day: Dict[str, List[dict]] = {}
    for b in bars:
        dt = datetime.datetime.utcfromtimestamp(b["open_time_ms"] / 1000)
        day_key = dt.strftime("%Y%m%d")
        by_day.setdefault(day_key, []).append(b)

    if dry_run:
        return sum(len(v) for v in by_day.values())

    # Видаляємо старі файли
    for f in os.listdir(d):
        if f.endswith(".jsonl"):
            os.remove(os.path.join(d, f))

    total = 0
    for day_key in sorted(by_day):
        path = os.path.join(d, "part-%s.jsonl" % day_key)
        with open(path, "w", encoding="utf-8") as fh:
            for b in by_day[day_key]:
                fh.write(json.dumps(b, ensure_ascii=False) + "\n")
                total += 1
    return total


def _derive_m3_from_m1(m1_bars: List[dict], symbol: str) -> List[dict]:
    """Будує M3 бари з послідовності M1."""
    # Групуємо M1 по M3 bucket
    m3_buckets: Dict[int, List[dict]] = {}
    for b in m1_bars:
        ot = b["open_time_ms"]
        m3_open = (ot // M3_MS) * M3_MS
        m3_buckets.setdefault(m3_open, []).append(b)

    m3_bars = []
    for m3_open in sorted(m3_buckets):
        group = m3_buckets[m3_open]
        # Потрібно рівно 3 M1 бари
        expected = [m3_open + i * M1_MS for i in range(3)]
        present = sorted([b["open_time_ms"] for b in group])
        if present != expected:
            log.debug("M3 bucket %d: неповний (%d/3 M1), skip", m3_open, len(group))
            continue

        # Фільтруємо calendar-pause flat бари (O==H==L==C, v<=4)
        trading = []
        for b in group:
            is_flat = (b["o"] == b["h"] == b["low"] == b["c"]) and b.get("v", 0) <= 4
            ext = b.get("extensions", {})
            if ext.get("calendar_pause_flat"):
                continue
            if is_flat:
                continue
            trading.append(b)

        if not trading:
            continue

        extensions = {}
        if len(trading) < len(group):
            extensions["partial_calendar_pause"] = True
            extensions["calendar_pause_m1_count"] = len(group) - len(trading)

        m3_bar = {
            "symbol": symbol,
            "tf_s": TF_M3,
            "open_time_ms": m3_open,
            "close_time_ms": m3_open + M3_MS,
            "o": trading[0]["o"],
            "h": max(b["h"] for b in trading),
            "low": min(b["low"] for b in trading),
            "c": trading[-1]["c"],
            "v": sum(b.get("v", 0) for b in trading),
            "complete": True,
            "src": "derived",
        }
        if extensions:
            m3_bar["extensions"] = extensions
        m3_bars.append(m3_bar)

    return m3_bars


def process_symbol(data_root: str, symbol: str, dry_run: bool) -> dict:
    """Обробляє один символ: dedup M1 + rebuild M3."""
    stats = {"symbol": symbol, "m1_before": 0, "m1_after": 0, "m1_dups": 0,
             "m3_before": 0, "m3_after": 0}

    # ── M1 dedup ──
    m1_raw = _read_all_bars(data_root, symbol, TF_M1)
    stats["m1_before"] = len(m1_raw)
    if not m1_raw:
        log.info("%s: M1 порожній, skip", symbol)
        return stats

    m1_clean = _dedup_bars(m1_raw)
    # Сортуємо по open_time_ms
    m1_clean.sort(key=lambda b: b["open_time_ms"])
    stats["m1_after"] = len(m1_clean)
    stats["m1_dups"] = stats["m1_before"] - stats["m1_after"]

    _write_bars(data_root, symbol, TF_M1, m1_clean, dry_run)
    log.info("%s M1: %d -> %d (dups=%d)%s",
             symbol, stats["m1_before"], stats["m1_after"], stats["m1_dups"],
             " [DRY]" if dry_run else "")

    # ── M3 rebuild ──
    m3_old = _read_all_bars(data_root, symbol, TF_M3)
    stats["m3_before"] = len(m3_old)

    m3_new = _derive_m3_from_m1(m1_clean, symbol)
    m3_new.sort(key=lambda b: b["open_time_ms"])
    stats["m3_after"] = len(m3_new)

    _write_bars(data_root, symbol, TF_M3, m3_new, dry_run)
    log.info("%s M3: %d -> %d%s",
             symbol, stats["m3_before"], stats["m3_after"],
             " [DRY]" if dry_run else "")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Dedup M1 + rebuild M3")
    parser.add_argument("--config", default=None)
    parser.add_argument("--all", action="store_true", help="Всі символи з конфігу")
    parser.add_argument("--symbols", default="", help="CSV: 'XAU/USD,XAG/USD'")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg_path = args.config or pick_config_path()
    cfg = load_system_config(cfg_path)
    data_root = cfg.get("data_root", "./data_v3")

    if args.all:
        symbols = cfg.get("symbols", [])
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        symbols = [cfg.get("symbol", "XAU/USD")]

    log.info("=== Dedup M1 + Rebuild M3 ===")
    log.info("Символи: %d, dry_run=%s", len(symbols), args.dry_run)

    total_stats = {"m1_dups": 0, "m3_rebuilt": 0}
    for sym in symbols:
        s = process_symbol(data_root, sym, args.dry_run)
        total_stats["m1_dups"] += s["m1_dups"]
        total_stats["m3_rebuilt"] += s["m3_after"]

    log.info("=== DONE: M1 dups видалено=%d, M3 перебудовано=%d ===",
             total_stats["m1_dups"], total_stats["m3_rebuilt"])


if __name__ == "__main__":
    main()
