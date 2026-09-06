import json

d = json.load(open("/opt/smc-trader-v3/data/v3_agent_directives.json"))
print("stats_date present:", "stats_date" in d)
print("stats_date value:", d.get("stats_date", "MISSING"))
print("estimated_cost:", d.get("estimated_cost_usd_today"))
print("agent_calls_today:", d.get("agent_calls_today"))
