#!/usr/bin/env python3
"""Reset kill_switch_active and clear circuit breaker state."""

import json

DIRECTIVES = "/opt/smc-trader-v3/data/v3_agent_directives.json"

d = json.load(open(DIRECTIVES))
changes = []

# Reset kill switch
if d.get("kill_switch_active"):
    d["kill_switch_active"] = False
    changes.append("kill_switch_active: True -> False")

# Clear any circuit breaker counters
for key in list(d.keys()):
    if "consecutive_error" in key.lower() or "circuit" in key.lower():
        old = d[key]
        d[key] = 0
        changes.append(f"{key}: {old} -> 0")

with open(DIRECTIVES, "w") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

if changes:
    print("Fixed:")
    for c in changes:
        print(f"  {c}")
else:
    print("Nothing to fix")
print(f"\nkill_switch_active now: {d.get('kill_switch_active')}")
