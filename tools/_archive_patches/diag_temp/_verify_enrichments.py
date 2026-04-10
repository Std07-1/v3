#!/usr/bin/env python3
"""Verify /api/context enrichments."""

import json, urllib.request

url = "http://127.0.0.1:8000/api/context?symbol=XAU/USD&tf=H4&candles=3"
resp = urllib.request.urlopen(url, timeout=10)
d = json.loads(resp.read())

print("=== ENRICHMENT VERIFICATION ===")
print(f"tick_price:  {d.get('tick_price')}")
print(f"tick_ts_ms:  {d.get('tick_ts_ms')}")
print(f"last_price:  {d.get('last_price')}")

dq = d.get("data_quality") or {}
print(f"\ndata_quality keys: {list(dq.keys())}")
tf_fresh = dq.get("tf_freshness", {})
for tf, info in tf_fresh.items():
    stale = info.get("stale", False)
    marker = " << STALE" if stale else ""
    print(f"  {tf}: age={info.get('age_s')}s bars={info.get('bars_count')}{marker}")
print(f"  ws_clients: {dq.get('ws_clients')}")
print(f"  price_spread: {dq.get('price_spread')}")

h4f = d.get("h4_forming")
if h4f:
    print(
        f"\nh4_forming: O={h4f['o']} H={h4f['h']} L={h4f['l']} C={h4f['c']} m1={h4f['m1_count']} age={h4f['age_s']}s"
    )
else:
    print("\nh4_forming: None")

print("\n=== OK ===")
