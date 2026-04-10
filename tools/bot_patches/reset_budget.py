#!/usr/bin/env python3
"""Budget reset: reset daily counters so bot resumes agent calls."""

import json
from pathlib import Path

p = Path("/opt/smc-trader-v3/data/v3_agent_directives.json")
d = json.loads(p.read_text())

print(
    f"BEFORE: cost=${d.get('estimated_cost_usd_today', 0):.3f}, "
    f"calls={d.get('agent_calls_today', 0)}, "
    f"budget_notified={d.get('budget_exhausted_notified', False)}"
)

d["estimated_cost_usd_today"] = 0.0
d["agent_calls_today"] = 0
d["messages_sent_today"] = 0
d["budget_exhausted_notified"] = False
d["consecutive_errors"] = 0
d["hourly_call_timestamps"] = []

# Reset token usage
d["token_usage_today"] = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_create_tokens": 0,
}

p.write_text(json.dumps(d, ensure_ascii=False, indent=2))
print(f"AFTER:  cost=$0.000, calls=0, budget_notified=False, consecutive_errors=0")
print("Budget reset complete.")
