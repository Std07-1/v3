#!/usr/bin/env python3
"""Check VPS config for personality_sections and economy mode."""

import json, sys

cfg = json.load(open("/opt/smc-trader-v3/config.json"))

agent = cfg.get("agent", {})
print("=== personality_sections ===")
ps = agent.get("personality_sections", "NOT_SET (using code defaults)")
print(json.dumps(ps, indent=2, ensure_ascii=False))

print("\n=== safety ===")
safety = cfg.get("safety", {})
print(f"max_daily_budget_usd: {safety.get('max_daily_budget_usd', 'NOT_SET')}")
print(f"budget_warn_pct: {safety.get('budget_warn_pct', 'NOT_SET')}")

print("\n=== agent models ===")
print(f"model_strategist: {agent.get('model_strategist', 'NOT_SET')}")
print(f"model_analyst: {agent.get('model_analyst', 'NOT_SET')}")
print(f"model_sentinel: {agent.get('model_sentinel', 'NOT_SET')}")

print("\n=== max_tokens ===")
mt = agent.get("max_tokens", {})
print(json.dumps(mt, indent=2))

print("\n=== thinking ===")
th = agent.get("thinking", {})
print(json.dumps(th, indent=2))
