#!/usr/bin/env python3
"""Reset Archi's budget_strategy to normal."""

import json

p = "/opt/smc-trader-v3/data/v3_agent_directives.json"
with open(p) as f:
    d = json.load(f)

print(f"budget_strategy: {d.get('budget_strategy', '?')}")
print(f"next_check_minutes: {d.get('next_check_minutes', '?')}")
print(f"model_preference: {d.get('model_preference', '?')}")
print(f"estimated_cost: {round(d.get('estimated_cost_usd_today', 0), 3)}")

d["budget_strategy"] = "normal"
d["next_check_minutes"] = 30  # Reset to default 30 min heartbeat
d["model_preference"] = ""  # Reset to auto (not forced analyst)
with open(p, "w") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

print("=> Reset: budget=normal, heartbeat=30min, model=auto")
