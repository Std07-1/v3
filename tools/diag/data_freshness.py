#!/usr/bin/env python3
"""Data pipeline freshness check."""

import json, sys, time, subprocess


def redis_get(key):
    r = subprocess.run(
        ["redis-cli", "-n", "1", "GET", key], capture_output=True, text=True
    )
    return r.stdout.strip() if r.returncode == 0 else None


def check_snap(sym, tf):
    raw = redis_get(f"v3_local:ohlcv:snap:{sym}:{tf}")
    if not raw:
        print(f"  {sym} M{tf//60}: NO DATA")
        return
    try:
        d = json.loads(raw)
        bars = d.get("bars", [])
        if not bars:
            print(f"  {sym} TF{tf}: 0 bars")
            return
        b = bars[-1]
        age_min = (time.time() * 1000 - b.get("t", 0)) / 60000
        print(
            f"  {sym} TF{tf}: {len(bars)} bars, last c={b.get('c',0)}, age={age_min:.1f}min"
        )
    except Exception as e:
        print(f"  {sym} TF{tf}: PARSE ERROR {e}")


print("=== DATA FRESHNESS ===")
for sym in ["XAU_USD", "XAG_USD", "BTCUSDT", "ETHUSDT"]:
    check_snap(sym, 60)
check_snap("XAU_USD", 3600)
check_snap("XAU_USD", 14400)
check_snap("XAU_USD", 86400)

# Preview
print("\n=== PREVIEW FRESHNESS ===")
raw = redis_get("v3_local:preview:curr:XAU_USD:60")
if raw:
    try:
        d = json.loads(raw)
        age = (time.time() * 1000 - d.get("t", 0)) / 1000
        print(f"  XAU_USD preview: c={d.get('c',0)} age={age:.0f}s")
    except:
        print(f"  XAU_USD preview: raw={raw[:100]}")
else:
    print("  XAU_USD preview: NONE")

raw = redis_get("v3_local:preview:curr:BTCUSDT:60")
if raw:
    try:
        d = json.loads(raw)
        age = (time.time() * 1000 - d.get("t", 0)) / 1000
        print(f"  BTCUSDT preview: c={d.get('c',0)} age={age:.0f}s")
    except:
        print(f"  BTCUSDT preview: raw={raw[:100]}")
else:
    print("  BTCUSDT preview: NONE")

# Nginx recent
print("\n=== NGINX RECENT ===")
import subprocess as sp

r = sp.run(
    ["sudo", "tail", "-5", "/var/log/nginx/access.log"], capture_output=True, text=True
)
for line in r.stdout.strip().split("\n")[-3:]:
    print(f"  {line[:120]}")
