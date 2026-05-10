#!/usr/bin/env python3
"""Quick diagnostic: check M5 dupes + last_price freshness."""

import json, urllib.request, time

BASE = "http://127.0.0.1:8000"


def fetch(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=5) as r:
        return json.loads(r.read())


# 1. M5 context — check for duplicate candle timestamps
d = fetch("/api/context?symbol=XAU_USD&tf=M5&candles=20")
candles = d.get("candles", [])
ts_list = [c["t"] for c in candles]
ts_set = set(ts_list)
dupes = len(ts_list) - len(ts_set)
lp = d.get("last_price", "MISSING")
print(f"M5: candles={len(candles)} duplicate_timestamps={dupes}")
if candles:
    last = candles[-1]
    age_s = (time.time() * 1000 - last["t"]) / 1000
    print(f"  last_candle: t={last['t']} c={last.get('c')} age={age_s:.0f}s")

# 2. H4 context
d4 = fetch("/api/context?symbol=XAU_USD&tf=H4&candles=10")
candles4 = d4.get("candles", [])
ts4 = [c["t"] for c in candles4]
dupes4 = len(ts4) - len(set(ts4))
print(f"H4: candles={len(candles4)} duplicate_timestamps={dupes4}")
if candles4:
    last4 = candles4[-1]
    age4 = (time.time() * 1000 - last4["t"]) / 1000
    print(f"  last_candle: t={last4['t']} c={last4.get('c')} age={age4:.0f}s")

# 3. last_price
print(f"last_price={lp}")

# 4. Compare
print("---")
if candles and lp != "MISSING":
    diff = abs(float(lp) - float(candles[-1].get("c", 0)))
    print(
        f"price_discrepancy: last_price={lp} vs M5_last_close={candles[-1].get('c')} diff={diff:.2f}"
    )
