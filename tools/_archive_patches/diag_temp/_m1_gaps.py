"""Check M1 data for gaps (with dedup + sort)."""

import json
from datetime import datetime, timezone

for sym in ["XAU_USD", "XAG_USD"]:
    f = f"data_v3/{sym}/tf_60/part-20260331.jsonl"
    lines = open(f).readlines()
    # Dedup by open_time_ms, sort
    seen = {}
    for line in lines:
        bar = json.loads(line)
        ot = bar["open_time_ms"]
        seen[ot] = bar  # last wins (dedup)
    bars_sorted = sorted(seen.values(), key=lambda b: b["open_time_ms"])
    print(f"=== {sym} M1: {len(lines)} lines, {len(bars_sorted)} unique bars ===")

    if not bars_sorted:
        print("  EMPTY")
        continue

    ft = datetime.fromtimestamp(bars_sorted[0]["open_time_ms"] / 1000, timezone.utc)
    lt = datetime.fromtimestamp(bars_sorted[-1]["open_time_ms"] / 1000, timezone.utc)
    print(f"  First: {ft.strftime('%H:%M')} UTC")
    print(f"  Last:  {lt.strftime('%H:%M')} UTC")

    prev_ot = None
    gaps = []
    for bar in bars_sorted:
        ot = bar["open_time_ms"]
        if prev_ot is not None:
            delta_min = (ot - prev_ot) / 60000
            if delta_min > 2:
                gs = datetime.fromtimestamp(prev_ot / 1000, timezone.utc).strftime(
                    "%H:%M"
                )
                ge = datetime.fromtimestamp(ot / 1000, timezone.utc).strftime("%H:%M")
                gaps.append(f"    {gs}-{ge} ({delta_min:.0f}min)")
        prev_ot = ot

    if gaps:
        print(f"  M1 gaps ({len(gaps)}):")
        for g in gaps[:15]:
            print(g)
    else:
        print("  M1 gaps: NONE ✓")
    print()
