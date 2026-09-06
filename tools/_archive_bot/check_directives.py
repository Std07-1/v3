#!/usr/bin/env python3
"""Quick check of directives state."""

import json, time

with open("/opt/smc-trader-v3/data/v3_agent_directives.json") as f:
    d = json.load(f)

ts = d.get("last_agent_call_ts", 0)
ago = int(time.time() - ts) if ts else -1
print(f"last_agent_call_ts: {ts}")
print(f"seconds_ago: {ago}")
print(f"next_check_minutes: {d.get('next_check_minutes', '?')}")
print(f"estimated_cost_usd_today: {d.get('estimated_cost_usd_today', '?')}")
print(f"consecutive_errors: {d.get('consecutive_errors', '?')}")
print(f"budget_exhausted_notified: {d.get('budget_exhausted_notified', '?')}")
print(f"last_market_status: {d.get('last_market_status', '?')}")
print(f"agent_calls_today: {d.get('agent_calls_today', '?')}")
print(f"messages_sent_today: {d.get('messages_sent_today', '?')}")
