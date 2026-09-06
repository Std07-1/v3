#!/usr/bin/env python3
import json

d = json.load(open("/opt/smc-trader-v3/data/v3_agent_directives.json"))
print("next_check_minutes:", d.get("next_check_minutes"))
print("budget_strategy:", d.get("budget_strategy"))
print("agent_calls_today:", d.get("agent_calls_today"))
print("estimated_cost:", d.get("estimated_cost_usd_today"))
wa = d.get("wake_at", [])
print("\nwake_at timers:", len(wa))
for t in wa:
    print("  ", t)
pa = d.get("price_alerts", [])
print("\nprice_alerts:", len(pa))
for a in pa:
    print("  ", a)
ai = d.get("audit_inbox", [])
print("\naudit_inbox:", len(ai))
for i in ai[:5]:
    print("  ", i)
