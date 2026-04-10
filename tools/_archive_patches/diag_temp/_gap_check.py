"""Quick diagnostic: check last bar timestamps across symbols/TFs."""

import json
import os
import time
from datetime import datetime, timezone

BASE = "data_v3"
SYMBOLS = ["XAU_USD", "XAG_USD", "BTCUSDT", "ETHUSDT"]
TFS = [60, 180, 300, 900, 1800, 3600, 14400, 86400]

now_ms = int(time.time() * 1000)
now_utc = datetime.now(timezone.utc)
print(f"Now: {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC  ({now_ms})")
print()

for sym in SYMBOLS:
    print(f"=== {sym} ===")
    for tf in TFS:
        d = os.path.join(BASE, sym, f"tf_{tf}")
        if not os.path.isdir(d):
            continue
        files = sorted([f for f in os.listdir(d) if f.endswith(".jsonl")])
        if not files:
            print(f"  tf={tf:>5}s: NO FILES")
            continue
        last_file = os.path.join(d, files[-1])
        lines = open(last_file, encoding="utf-8").readlines()
        if not lines:
            print(f"  tf={tf:>5}s: EMPTY FILE {files[-1]}")
            continue

        # Dedup + sort by open_time_ms (JSONL append creates out-of-order entries after repair)
        seen = {}
        for line in lines:
            try:
                bar = json.loads(line)
                ot = bar.get("open_time_ms", bar.get("t", 0))
                seen[ot] = bar
            except Exception:
                pass
        bars_sorted = sorted(
            seen.values(), key=lambda b: b.get("open_time_ms", b.get("t", 0))
        )
        n_unique = len(bars_sorted)

        last = bars_sorted[-1]
        ot = last.get("open_time_ms", last.get("t", 0))
        dt = datetime.fromtimestamp(ot / 1000, timezone.utc)
        gap_min = (now_ms - ot) / 60000

        # Find gap: scan all unique bars for time jumps > 2*tf
        gap_info = ""
        if len(bars_sorted) > 2:
            prev_ot = None
            biggest_gap = 0
            gap_at = ""
            for bar in bars_sorted:
                cur_ot = bar.get("open_time_ms", bar.get("t", 0))
                if prev_ot is not None:
                    delta = cur_ot - prev_ot
                    expected = tf * 1000
                    if delta > expected * 2 and delta > biggest_gap:
                        biggest_gap = delta
                        gap_start = datetime.fromtimestamp(prev_ot / 1000, timezone.utc)
                        gap_end = datetime.fromtimestamp(cur_ot / 1000, timezone.utc)
                        gap_at = f" GAP: {gap_start.strftime('%H:%M')}-{gap_end.strftime('%H:%M')} ({biggest_gap // 60000}min)"
                prev_ot = cur_ot
            gap_info = gap_at

        flag = " <<<" if gap_min > tf / 60 * 3 else ""
        print(
            f"  tf={tf:>5}s: last={dt.strftime('%H:%M')}UTC"
            f"  age={gap_min:.0f}min"
            f"  bars={n_unique}"
            f"  file={files[-1]}"
            f"{gap_info}{flag}"
        )
    print()
