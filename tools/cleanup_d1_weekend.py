"""
tools/cleanup_d1_weekend.py — Видалення weekend artifact D1 барів з disk SSOT.

Видаляє part-файли для D1 (tf_86400) де:
1. Бар flat (O==H==L==C, V<=4) AND open_time на п'ятницю (wd=4) або суботу (wd=5) UTC.
2. Бар flat AND unclosed (close_time > now_ms).

Не видаляє real бари (навіть на вихідних) або бари з volume > 4.
Запуск: python -m tools.cleanup_d1_weekend [--dry-run]
"""
import json
import os
import sys
import datetime
import time

DATA_ROOT = os.path.join(os.path.dirname(__file__), "..", "data_v3")
TF_DIR = "tf_86400"
TF_S = 86400
FLAT_MAX_VOLUME = 4.0


def _is_flat(data: dict) -> bool:
    o = float(data.get("open", data.get("o", -1)))
    h = float(data.get("high", data.get("h", -2)))
    lo = float(data.get("low", data.get("l", -3)))
    c = float(data.get("close", data.get("c", -4)))
    v = float(data.get("volume", data.get("v", 999)))
    return o == h == lo == c and v <= FLAT_MAX_VOLUME


def main():
    dry_run = "--dry-run" in sys.argv
    now_ms = int(time.time() * 1000)
    total_removed = 0
    total_scanned = 0
    symbols_cleaned = {}

    for sym in sorted(os.listdir(DATA_ROOT)):
        tf_path = os.path.join(DATA_ROOT, sym, TF_DIR)
        if not os.path.isdir(tf_path):
            continue
        removed = 0
        for fname in sorted(os.listdir(tf_path)):
            if not fname.endswith(".jsonl"):
                continue
            fpath = os.path.join(tf_path, fname)
            total_scanned += 1
            try:
                with open(fpath) as fh:
                    data = json.loads(fh.readline())
            except Exception:
                continue

            if not _is_flat(data):
                continue

            ot = data.get("open_time_ms") or data.get("t")
            if not ot:
                continue

            bar_dt = datetime.datetime.utcfromtimestamp(int(ot) / 1000)
            wd = bar_dt.weekday()
            bar_close_ms = int(ot) + TF_S * 1000

            should_remove = False
            reason = ""
            # Weekend artifact: Fri(4)/Sat(5) open
            if wd in (4, 5):
                should_remove = True
                reason = f"weekend_flat wd={wd}"
            # Unclosed bar
            elif bar_close_ms > now_ms:
                should_remove = True
                reason = f"unclosed close_ms={bar_close_ms}>now={now_ms}"

            if should_remove:
                if dry_run:
                    print(f"DRY-RUN REMOVE {sym}/{fname} {bar_dt.strftime('%Y-%m-%d %H:%M')} wd={wd} {reason}")
                else:
                    os.remove(fpath)
                removed += 1

        if removed:
            symbols_cleaned[sym] = removed
            total_removed += removed

    mode = "DRY-RUN" if dry_run else "REMOVED"
    print(f"\n=== {mode}: scanned={total_scanned} removed={total_removed} ===")
    for sym, cnt in sorted(symbols_cleaned.items()):
        print(f"  {sym}: {cnt}")


if __name__ == "__main__":
    main()
