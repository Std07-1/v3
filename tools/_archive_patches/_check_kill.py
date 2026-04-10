#!/usr/bin/env python3
import json

d = json.load(open("/opt/smc-trader-v3/data/v3_agent_directives.json"))
print("kill_switch:", d.get("kill_switch", "NOT_SET"))
print("paused:", d.get("paused", "NOT_SET"))
# Check for any key with 'kill' or 'pause'
for k, v in d.items():
    if "kill" in k.lower() or "pause" in k.lower() or "circuit" in k.lower():
        print(f"  {k}: {v}")
