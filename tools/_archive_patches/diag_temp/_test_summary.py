#!/usr/bin/env python3
"""Test context_summary with enriched data."""

import sys, json, urllib.request

# Simulate what the bot does: fetch context for H4 TF
url = "http://127.0.0.1:8000/api/context?symbol=XAU/USD&tf=H4&candles=3"
resp = urllib.request.urlopen(url, timeout=10)
d = json.loads(resp.read())

# Show enrichment fields the bot now receives
print("=== Fields bot receives ===")
print(f"tick_price: {d.get('tick_price')}")
print(f"tick_ts_ms: {d.get('tick_ts_ms')}")
print(f"h4_forming: {json.dumps(d.get('h4_forming'), indent=2)}")
dq = d.get("data_quality", {})
stale = [k for k, v in dq.get("tf_freshness", {}).items() if v.get("stale")]
print(f"stale_tfs: {stale}")
print(f"ws_clients: {dq.get('ws_clients')}")

# Test that context_summary can parse these
sys.path.insert(0, "/opt/smc-trader-v3")
try:
    from bot.config import BotConfig

    cfg = BotConfig.from_file("/opt/smc-trader-v3/config.yaml")
    from bot.agent.prompts import context_summary

    # Build mock ctx like bot does
    ctx = {"H4": d}
    summary = context_summary(cfg, ctx, session="london", symbol="XAU/USD")
    print("\n=== context_summary output (last 15 lines) ===")
    lines = summary.strip().split("\n")
    for line in lines[-15:]:
        print(line)
    # Check enrichment lines are present
    if "tick" in summary.lower() or "LIVE" in summary:
        print("\n[OK] tick_price in summary")
    else:
        print("\n[WARN] tick_price NOT in summary")
    if "H4 Forming" in summary:
        print("[OK] h4_forming in summary")
    else:
        print("[WARN] h4_forming NOT in summary")
except Exception as exc:
    print(f"\n[ERROR] {exc}")
