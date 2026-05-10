#!/usr/bin/env python3
"""Quick verify: bar counts per TF for today."""

import json, os, datetime as dt

for sym in ["XAU_USD", "XAG_USD"]:
    print(f"\n=== {sym} ===")
    for tf in [60, 180, 300, 900, 1800, 3600, 14400]:
        path = f"data_v3/{sym}/tf_{tf}/part-20260331.jsonl"
        if os.path.exists(path):
            with open(path) as f:
                lines = f.readlines()
            count = len(lines)
            if lines:
                first = json.loads(lines[0])["open_time_ms"]
                last = json.loads(lines[-1])["open_time_ms"]
                t1 = dt.datetime.fromtimestamp(first / 1000, dt.UTC).strftime("%H:%M")
                t2 = dt.datetime.fromtimestamp(last / 1000, dt.UTC).strftime("%H:%M")
                print(f"  tf={tf:>5}: {count:>5} bars  {t1}..{t2}")
            else:
                print(f"  tf={tf:>5}:     0 bars")
        else:
            print(f"  tf={tf:>5}: MISSING")
