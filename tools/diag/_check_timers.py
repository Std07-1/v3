#!/usr/bin/env python3
"""Quick check of wake_at timers in directives."""

import json, time
from datetime import datetime, timezone

d = json.load(open("/opt/smc-trader-v3/data/v3_agent_directives.json"))
wt = d.get("wake_at", [])
print(f"wake_at count: {len(wt)}")
now = time.time()
for w in wt:
    ep = w.get("time_epoch", 0)
    dt = datetime.fromtimestamp(ep, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    delta = (ep - now) / 3600
    print(f"  {w.get('id')}: {dt} ({delta:+.1f}h from now)")

print(f"\nnext_check_minutes: {d.get('next_check_minutes')}")
print(f"observation_interval_minutes: {d.get('observation_interval_minutes')}")
print(f"thesis_sm_state keys: {list((d.get('thesis_sm_state') or {}).keys())}")
tsm = d.get("thesis_sm_state", {})
if tsm:
    print(f"  TSM state: {tsm.get('state')}, direction: {tsm.get('direction')}")
