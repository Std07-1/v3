#!/usr/bin/env python3
"""Set budget to $6 in config.json."""

import json

path = "/opt/smc-trader-v3/config.json"
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

old = data.get("safety", {}).get("max_daily_budget_usd")
data.setdefault("safety", {})["max_daily_budget_usd"] = 6.0

with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=4)

print(f"Budget: ${old} → $6.0")

# Verify
with open(path, "r", encoding="utf-8") as f:
    check = json.load(f)
print(f"Verified: ${check['safety']['max_daily_budget_usd']}")
