"""Purge legacy broker bars + rebuild H4 з H1 (4×H1, правильний anchor).

Використання:
  python -m tools.purge_rebuild_h4 --symbol XAU/USD
  python -m tools.purge_rebuild_h4 --symbol XAU/USD --dry-run
  python -m tools.purge_rebuild_h4 --all

Per ADR-0002: M3→H4 only derived. Broker дозволений лише для M1 + D1.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import logging
import os
import shutil
from typing import Dict, List, Optional, Tuple

from core.model.bars import CandleBar, assert_invariants
from core.buckets import bucket_start_ms

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("purge_rebuild_h4")

TF_H1 = 3600
TF_H4 = 14400
TF_M5 = 300
H4_MS = TF_H4 * 1000
H1_MS = TF_H1 * 1000


def _read_bars(tf_dir: str) -> List[dict]:
    """Читає всі бари з part-файлів, сортує по open_time_ms."""
    bars = []
    for path in sorted(glob.glob(os.path.join(tf_dir, "part-*.jsonl"))):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                bars.append(json.loads(line))
    bars.sort(key=lambda b: b["open_time_ms"])
    return bars


def _write_bars(tf_dir: str, bars: List[dict]) -> int:
    """Записує бари в part-файли (по даті), повертає к-сть записаних."""
    by_day: Dict[str, List[dict]] = {}
    for b in bars:
        day = dt.datetime.utcfromtimestamp(b["open_time_ms"] / 1000).strftime("%Y%m%d")
        by_day.setdefault(day, []).append(b)
    written = 0
    for day, day_bars in sorted(by_day.items()):
        path = os.path.join(tf_dir, "part-%s.jsonl" % day)
        with open(path, "w", encoding="utf-8") as f:
            for b in sorted(day_bars, key=lambda x: x["open_time_ms"]):
                f.write(json.dumps(b, ensure_ascii=False, separators=(",", ":")) + "\n")
                written += 1
    return written


def _delete_all_parts(tf_dir: str) -> int:
    """Видаляє всі part-файли, повертає к-сть видалених."""
    count = 0
    for path in glob.glob(os.path.join(tf_dir, "part-*.jsonl")):
        os.remove(path)
        count += 1
    return count


def purge_m5_history(data_root: str, symbol: str, dry_run: bool) -> Tuple[int, int]:
    """Видаляє src=history з M5. Повертає (removed, kept)."""
    sym_dir = symbol.replace("/", "_")
    m5_dir = os.path.join(data_root, sym_dir, "tf_%d" % TF_M5)
    if not os.path.isdir(m5_dir):
        return 0, 0
    removed = 0
    kept = 0
    for path in sorted(glob.glob(os.path.join(m5_dir, "part-*.jsonl"))):
        lines_keep = []
        lines_remove = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                bar = json.loads(line)
                if bar.get("src") == "history":
                    lines_remove += 1
                else:
                    lines_keep.append(line)
        removed += lines_remove
        kept += len(lines_keep)
        if lines_remove > 0 and not dry_run:
            with open(path, "w", encoding="utf-8") as f:
                for line in lines_keep:
                    f.write(line + "\n")
            if not lines_keep:
                os.remove(path)
    return removed, kept


def purge_h4(data_root: str, symbol: str, dry_run: bool) -> int:
    """Видаляє ВСЕ з tf_14400. Повертає к-сть видалених part-файлів."""
    sym_dir = symbol.replace("/", "_")
    h4_dir = os.path.join(data_root, sym_dir, "tf_%d" % TF_H4)
    if not os.path.isdir(h4_dir):
        return 0
    if dry_run:
        return len(glob.glob(os.path.join(h4_dir, "part-*.jsonl")))
    return _delete_all_parts(h4_dir)


def rebuild_h4_from_h1(
    data_root: str, symbol: str, anchor_offset_s: int, dry_run: bool
) -> Tuple[int, int]:
    """Ребілдить H4 з H1 (4×H1). Повертає (written, skipped_incomplete)."""
    sym_dir = symbol.replace("/", "_")
    h1_dir = os.path.join(data_root, sym_dir, "tf_%d" % TF_H1)
    h4_dir = os.path.join(data_root, sym_dir, "tf_%d" % TF_H4)
    if not os.path.isdir(h1_dir):
        log.warning("%s: tf_3600 відсутній, пропуск rebuild", symbol)
        return 0, 0

    h1_bars = _read_bars(h1_dir)
    if not h1_bars:
        log.warning("%s: 0 H1 барів, пропуск rebuild", symbol)
        return 0, 0

    h1_by_open: Dict[int, dict] = {}
    for b in h1_bars:
        h1_by_open[b["open_time_ms"]] = b

    anchor_ms = anchor_offset_s * 1000
    first_h1 = min(h1_by_open.keys())
    last_h1 = max(h1_by_open.keys())

    first_h4 = bucket_start_ms(first_h1, H4_MS, anchor_ms)
    last_h4 = bucket_start_ms(last_h1, H4_MS, anchor_ms)

    written = 0
    skipped = 0
    h4_bars: List[dict] = []

    for h4_open in range(first_h4, last_h4 + 1, H4_MS):
        parts = []
        for i in range(4):
            h1_open = h4_open + i * H1_MS
            if h1_open in h1_by_open:
                parts.append(h1_by_open[h1_open])
        if not parts:
            skipped += 1
            continue

        h4_bar = {
            "symbol": symbol,
            "tf_s": TF_H4,
            "open_time_ms": h4_open,
            "close_time_ms": h4_open + H4_MS,
            "o": parts[0]["o"],
            "h": max(p["h"] for p in parts),
            "low": min(p.get("low", p.get("l", 0)) for p in parts),
            "c": parts[-1]["c"],
            "v": sum(p.get("v", 0) for p in parts),
            "complete": True,
            "src": "derived",
        }

        # Перевірка інваріанту anchor alignment
        if (h4_open - anchor_ms) % H4_MS != 0:
            log.error("ANCHOR_MISALIGN h4_open=%d anchor=%d", h4_open, anchor_ms)
            continue

        h4_bars.append(h4_bar)
        written += 1

    if not dry_run and h4_bars:
        os.makedirs(h4_dir, exist_ok=True)
        _write_bars(h4_dir, h4_bars)

    return written, skipped


def verify_symbol(data_root: str, symbol: str, anchor_offset_s: int) -> List[str]:
    """Верифікація: H4 anchor, M5 no history, сортування."""
    errors = []
    sym_dir = symbol.replace("/", "_")
    anchor_ms = anchor_offset_s * 1000

    # H4 check
    h4_dir = os.path.join(data_root, sym_dir, "tf_%d" % TF_H4)
    if os.path.isdir(h4_dir):
        h4_bars = _read_bars(h4_dir)
        for b in h4_bars:
            ot = b["open_time_ms"]
            if (ot - anchor_ms) % H4_MS != 0:
                hr = (ot // 1000 % 86400) // 3600
                errors.append("H4 bad_anchor open_ms=%d hour=%d" % (ot, hr))
            if b.get("src") == "history":
                errors.append("H4 src=history still present open_ms=%d" % ot)
        # Перевірка сортування
        for i in range(1, len(h4_bars)):
            if h4_bars[i]["open_time_ms"] <= h4_bars[i - 1]["open_time_ms"]:
                errors.append("H4 UNSORTED at %d" % i)
        if not errors:
            hours = sorted(set((b["open_time_ms"] // 1000 % 86400) // 3600 for b in h4_bars))
            log.info("%s H4: %d bars, hours=%s", symbol, len(h4_bars), hours)
    else:
        errors.append("H4 dir not found after rebuild")

    # M5 check
    m5_dir = os.path.join(data_root, sym_dir, "tf_%d" % TF_M5)
    if os.path.isdir(m5_dir):
        m5_hist = 0
        m5_total = 0
        for path in glob.glob(os.path.join(m5_dir, "part-*.jsonl")):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    b = json.loads(line)
                    m5_total += 1
                    if b.get("src") == "history":
                        m5_hist += 1
        if m5_hist > 0:
            errors.append("M5 still has %d src=history bars" % m5_hist)
        log.info("%s M5: %d bars (0 history)", symbol, m5_total)

    return errors


def process_symbol(
    data_root: str, symbol: str, anchor_offset_s: int, dry_run: bool
) -> bool:
    """Повний цикл для одного символу. Повертає True при успіху."""
    log.info("=" * 60)
    log.info("SYMBOL: %s  anchor=%ds  dry_run=%s", symbol, anchor_offset_s, dry_run)
    log.info("=" * 60)

    # 1. Purge M5 history
    m5_removed, m5_kept = purge_m5_history(data_root, symbol, dry_run)
    log.info("M5 purge: removed=%d kept=%d", m5_removed, m5_kept)

    # 2. Purge H4
    h4_files = purge_h4(data_root, symbol, dry_run)
    log.info("H4 purge: %d part-files deleted", h4_files)

    # 3. Rebuild H4 from H1
    h4_written, h4_skipped = rebuild_h4_from_h1(data_root, symbol, anchor_offset_s, dry_run)
    log.info("H4 rebuild: written=%d skipped=%d", h4_written, h4_skipped)

    # 4. Verify
    if not dry_run:
        errors = verify_symbol(data_root, symbol, anchor_offset_s)
        if errors:
            for e in errors[:10]:
                log.error("VERIFY FAIL: %s", e)
            return False
        log.info("VERIFY OK: %s", symbol)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Purge legacy H4/M5 + rebuild H4 з H1")
    ap.add_argument("--symbol", default=None, help="Один символ (напр. XAU/USD)")
    ap.add_argument("--all", action="store_true", help="Всі символи з config.json")
    ap.add_argument("--dry-run", action="store_true", help="Тільки показати що буде зроблено")
    ap.add_argument("--data-root", default="./data_v3", help="Кореневий каталог даних")
    ap.add_argument("--anchor", type=int, default=79200, help="day_anchor_offset_s (default 79200=22h winter)")
    args = ap.parse_args()

    if args.all:
        from core.config_loader import pick_config_path, load_system_config
        cfg = load_system_config(pick_config_path())
        symbols = [str(s) for s in cfg.get("symbols", [])]
        args.data_root = str(cfg.get("data_root", args.data_root))
        args.anchor = int(cfg.get("day_anchor_offset_s", args.anchor))
    elif args.symbol:
        symbols = [args.symbol]
    else:
        log.error("Потрібно --symbol або --all")
        return 2

    ok = 0
    fail = 0
    for sym in symbols:
        success = process_symbol(args.data_root, sym, args.anchor, args.dry_run)
        if success:
            ok += 1
        else:
            fail += 1

    log.info("DONE: ok=%d fail=%d", ok, fail)
    return 1 if fail > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
