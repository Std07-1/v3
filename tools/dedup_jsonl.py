"""Dedup JSONL: видалити дублікати по open_time_ms (зберегти останній).

Аналізує всі part-*.jsonl для всіх символів і TF.
Режими:
    --dry-run   (default): лише звіт
    --fix       : перезаписати файли без дублікатів
"""
import os, json, glob, sys, datetime as dt
from collections import OrderedDict

DRY = "--fix" not in sys.argv

root = "data_v3"
symbols = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
TF_NAMES = {60: "M1", 180: "M3", 300: "M5", 900: "M15", 1800: "M30",
             3600: "H1", 14400: "H4", 86400: "D1"}

total_dupes = 0
total_files_fixed = 0

for sym in symbols:
    sym_dir = os.path.join(root, sym)
    for tf_dir_name in sorted(os.listdir(sym_dir)):
        tf_path = os.path.join(sym_dir, tf_dir_name)
        if not os.path.isdir(tf_path):
            continue
        try:
            tf_s = int(tf_dir_name.split("_")[1])
        except (IndexError, ValueError):
            continue

        for part_file in sorted(glob.glob(os.path.join(tf_path, "part-*.jsonl"))):
            with open(part_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            if not lines:
                continue

            # Dedup: keep LAST occurrence per open_time_ms
            seen = OrderedDict()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    bar = json.loads(line)
                    key = bar.get("open_time_ms", bar.get("o"))
                    seen[key] = line
                except json.JSONDecodeError:
                    continue

            n_dupes = len(lines) - len(seen)
            if n_dupes <= 0:
                continue

            total_dupes += n_dupes
            tf_name = TF_NAMES.get(tf_s, str(tf_s))
            rel = os.path.relpath(part_file, root)
            print(f"  {sym:12s} {tf_name:4s} {os.path.basename(part_file):30s} "
                  f"lines={len(lines):>5d} unique={len(seen):>5d} dupes={n_dupes:>4d}")

            if not DRY:
                # Записуємо dedup
                with open(part_file, "w", encoding="utf-8", newline="\n") as f:
                    for v in seen.values():
                        f.write(v + "\n")
                total_files_fixed += 1

mode_str = "DRY-RUN" if DRY else "FIXED"
print(f"\n{mode_str}: total_dupes={total_dupes} files_fixed={total_files_fixed}")
if DRY and total_dupes > 0:
    print("Запустіть з --fix для видалення дублікатів.")
