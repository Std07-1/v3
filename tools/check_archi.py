#!/usr/bin/env python3
"""Quick remote state check for Archi bot - run locally, reads from stdin piped via ssh."""

import json, sys

d = json.load(sys.stdin)

# Lessons
lessons = d.get("lessons", d.get("agent_lessons", []))
print(f"=== LESSONS ({len(lessons)} total) ===")
for l in lessons[-8:]:
    print(json.dumps(l, ensure_ascii=False)[:250])

# Findings
findings = d.get("internal_findings", [])
print(f"\n=== FINDINGS ({len(findings)} total) ===")
for f in findings[-5:]:
    print(json.dumps(f, ensure_ascii=False)[:250])

# Stats
print(f"\n=== STATS ===")
print(f"agent_calls_today: {d.get('agent_calls_today')}")
print(f"messages_sent_today: {d.get('messages_sent_today')}")
print(f"estimated_cost_usd_today: {d.get('estimated_cost_usd_today')}")
print(f"consecutive_errors: {d.get('consecutive_errors')}")
print(f"kill_switch_active: {d.get('kill_switch_active')}")
print(f"budget_exhausted_notified: {d.get('budget_exhausted_notified')}")
print(f"model_preference: {d.get('model_preference')}")
print(f"stats_date: {d.get('stats_date')}")

# Wake timers
wakes = d.get("wake_at", [])
print(f"\n=== WAKE TIMERS ({len(wakes)}) ===")
for w in wakes:
    print(json.dumps(w, ensure_ascii=False)[:250])

# Scenario
sc = d.get("active_scenario")
if sc:
    print(f"\n=== ACTIVE SCENARIO ===")
    print(json.dumps(sc, ensure_ascii=False)[:500])

# Op rules
rules = d.get("operational_rules", [])
print(f"\n=== OPERATIONAL RULES ({len(rules)}) ===")
for r in rules:
    print(r[:200])
