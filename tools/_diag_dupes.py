"""Порівняння duplicate M1 барів: чи відрізняються OHLCV."""
import json

with open("data_v3/XAU_USD/tf_60/part-20260219.jsonl") as f:
    lines = f.readlines()

# Групуємо по open_time_ms
from collections import defaultdict
by_ot = defaultdict(list)
for i, line in enumerate(lines):
    b = json.loads(line)
    by_ot[b["open_time_ms"]].append((i, b))

# Показуємо перші 5 дублікатів
dupes = {k: v for k, v in by_ot.items() if len(v) > 1}
import datetime as dt
print(f"Total duplicate open_ms: {len(dupes)}")
print(f"First duplicate starts at line index: {min(v[1][0] for v in dupes.values())}")
print()

for k in sorted(dupes)[:3]:
    t = dt.datetime.fromtimestamp(k / 1000, tz=dt.timezone.utc)
    print(f"  {t:%H:%M}:")
    for idx, b in dupes[k]:
        print(f"    [{idx}] o={b['o']} h={b['h']} l={b['low']} c={b['c']} v={b['v']} src={b.get('source','?')}")
    # Check if OHLCV identical
    b1, b2 = dupes[k][0][1], dupes[k][1][1]
    same = all(b1[f] == b2[f] for f in ['o', 'h', 'low', 'c', 'v'])
    print(f"    OHLCV identical: {same}")

# How many unique vs total
print(f"\nTotal lines: {len(lines)}")
print(f"Unique open_ms: {len(by_ot)}")
print(f"Duplicated entries: {len(lines) - len(by_ot)}")

# Check M5 derived for today
import os, glob
m5dir = "data_v3/XAU_USD/tf_300"
m5_parts = sorted(glob.glob(os.path.join(m5dir, "part-2026021*.jsonl")))
for p in m5_parts:
    with open(p) as f:
        m5lines = f.readlines()
    last = json.loads(m5lines[-1])
    t = dt.datetime.fromtimestamp(last["open_time_ms"]/1000, tz=dt.timezone.utc)
    print(f"\n{os.path.basename(p)}: {len(m5lines)} bars, last={t:%m-%d %H:%M} src={last.get('source','?')}")
