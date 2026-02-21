"""Діагностика M1 дублікатів і continuity навколо 13:30."""
import json, datetime as dt
from collections import Counter

with open("data_v3/XAU_USD/tf_60/part-20260219.jsonl") as f:
    lines = f.readlines()

print(f"Total M1 bars today: {len(lines)}")
print("\nLast 15 M1:")
for line in lines[-15:]:
    b = json.loads(line)
    t = dt.datetime.fromtimestamp(b["open_time_ms"] / 1000, tz=dt.timezone.utc)
    print(f"  open_ms={b['open_time_ms']} open={t:%H:%M}")

opens = [json.loads(l)["open_time_ms"] for l in lines]
dupes = {k: v for k, v in Counter(opens).items() if v > 1}
print(f"\nDuplicate open_time_ms: {len(dupes)} (showing first 10)")
for k in sorted(dupes)[:10]:
    t = dt.datetime.fromtimestamp(k / 1000, tz=dt.timezone.utc)
    print(f"  {t:%H:%M} x{dupes[k]}")

# M1 around 13:30 where M5 stopped
print("\nM1 around 13:28-13:42 (M5 stall zone):")
for line in lines:
    b = json.loads(line)
    ot = b["open_time_ms"]
    t = dt.datetime.fromtimestamp(ot / 1000, tz=dt.timezone.utc)
    hm = t.strftime("%H:%M")
    if "13:28" <= hm <= "13:42":
        print(f"  open={hm} ot={ot}")

# Check M1 continuity — consecutive gaps
print("\nM1 gaps >1 minute:")
unique_sorted = sorted(set(opens))
for i in range(1, len(unique_sorted)):
    gap_min = (unique_sorted[i] - unique_sorted[i-1]) / 60000
    if gap_min > 1.5:
        t1 = dt.datetime.fromtimestamp(unique_sorted[i-1]/1000, tz=dt.timezone.utc)
        t2 = dt.datetime.fromtimestamp(unique_sorted[i]/1000, tz=dt.timezone.utc)
        print(f"  {t1:%H:%M} -> {t2:%H:%M}  gap={gap_min:.0f}m")
