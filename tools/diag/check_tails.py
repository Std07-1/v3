"""Quick tail audit: last bar per symbol/TF."""

import json, os, glob
from datetime import datetime, timezone

BASE = "data_v3"
SYMS = ["XAU_USD", "XAG_USD", "BTCUSDT", "ETHUSDT"]

for sym_dir in SYMS:
    sym_path = os.path.join(BASE, sym_dir)
    if not os.path.isdir(sym_path):
        continue
    print(f"\n=== {sym_dir} ===")
    for tf_dir in sorted(os.listdir(sym_path)):
        tf_path = os.path.join(sym_path, tf_dir)
        if not os.path.isdir(tf_path):
            continue
        files = sorted(glob.glob(os.path.join(tf_path, "*.jsonl")))
        if not files:
            print(f"  {tf_dir}: EMPTY")
            continue
        last_file = files[-1]
        with open(last_file, "rb") as f:
            f.seek(0, 2)
            pos = f.tell()
            if pos == 0:
                print(f"  {tf_dir}: empty file")
                continue
            f.seek(max(0, pos - 512))
            lines = f.read().decode().strip().split("\n")
            last = json.loads(lines[-1])
            ts = last.get("open_time_ms", last.get("open_ms", 0))
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            print(
                f"  {tf_dir}: last_open={dt:%Y-%m-%d %H:%M} UTC  ({len(files)} files)"
            )
