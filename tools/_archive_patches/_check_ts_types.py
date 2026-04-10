#!/usr/bin/env python3
"""Check ts types in knowledge file."""

import json

path = "/opt/smc-trader-v3/data/v3_knowledge.json"
d = json.load(open(path))
bad = []
for k, es in d.items():
    if isinstance(es, list):
        for e in es:
            ts = e.get("ts")
            if not isinstance(ts, int):
                bad.append((k, type(ts).__name__, ts))
print(f"Bad ts: {len(bad)}")
for b in bad[:10]:
    print(b)

# Also check directives kill switch state
dp = "/opt/smc-trader-v3/data/v3_agent_directives.json"
dd = json.load(open(dp))
print(f"\nkill_switch_active: {dd.get('kill_switch_active')}")
print(f"consecutive_errors: {dd.get('consecutive_errors')}")
print(f"circuit_breaker: {dd.get('circuit_breaker', {})}")
