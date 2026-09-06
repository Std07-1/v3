#!/usr/bin/env python3
"""Quick check of Archi's directives state."""

import json

d = json.load(open("/opt/smc-trader-v3/data/v3_agent_directives.json"))
keys = [
    "estimated_cost_usd_today",
    "budget_strategy",
    "agent_calls_today",
    "next_check_minutes",
    "model_preference",
    "budget_day",
    "last_agent_call_ts",
    "narrative_mode",
]
for k in keys:
    print(f"{k}: {d.get(k, '(missing)')}")

# Wake timers
wake_at = d.get("wake_at", [])
print(f"\nwake_at timers: {len(wake_at)}")
for wt in wake_at:
    print(
        f"  id={wt.get('id','?')} ts={wt.get('ts','?')} reason={wt.get('reason','?')}"
    )

# Price alerts
alerts = d.get("price_alerts", [])
print(f"\nprice_alerts: {len(alerts)}")
for a in alerts:
    print(f"  {a}")

# Audit inbox
inbox = d.get("audit_inbox", [])
print(f"\naudit_inbox: {len(inbox)}")
for item in inbox[:5]:
    print(f"  {item}")
