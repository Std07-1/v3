#!/usr/bin/env python3
"""Read Archi's internal state: thoughts, lessons, mood, findings."""

import json, os

DIR = "/opt/smc-trader-v3/data"

# Directives
d = json.load(open(os.path.join(DIR, "v3_agent_directives.json")))

print("=== MOOD ===")
print(d.get("mood", "N/A"))
print()

print("=== INNER_THOUGHT ===")
print(str(d.get("inner_thought", ""))[:600])
print()

print("=== SCRATCHPAD ===")
print(str(d.get("scratchpad", ""))[:600])
print()

# Thought history
th = d.get("thought_history", [])
print(f"=== LAST 5 THOUGHTS (total: {len(th)}) ===")
for t in th[-5:]:
    text = t.get("text", "")[:350]
    ts = t.get("ts", "")
    print(f"[{ts}] {text}")
    print()

# Lessons learned
les = d.get("lessons_learned", [])
print(f"=== LAST 5 LESSONS (total: {len(les)}) ===")
for l in les[-5:]:
    if isinstance(l, dict):
        print(f"  [{l.get('tag','')}] {str(l.get('text',''))[:250]}")
    else:
        print(f"  {str(l)[:250]}")
    print()

# Internal findings
findings = d.get("internal_findings", [])
print(f"=== LAST 3 FINDINGS (total: {len(findings)}) ===")
for f in findings[-3:]:
    if isinstance(f, dict):
        print(f"  [{f.get('tag','')}] {str(f.get('text',''))[:250]}")
    else:
        print(f"  {str(f)[:250]}")
    print()

# Self model
sm = d.get("self_model", {})
if sm:
    print("=== SELF_MODEL ===")
    for k, v in sm.items():
        print(f"  {k}: {str(v)[:200]}")

# Active scenario
print()
print("=== ACTIVE SCENARIO ===")
print(str(d.get("active_scenario", ""))[:300])

# Conversation last messages
print()
print("=== LAST 3 CONVERSATION MESSAGES ===")
try:
    conv = json.load(open(os.path.join(DIR, "v3_conversation.json")))
    msgs = conv.get("messages", conv if isinstance(conv, list) else [])
    for m in msgs[-3:]:
        role = m.get("role", "?")
        text = str(m.get("text", m.get("content", "")))[:400]
        print(f"[{role}] {text}")
        print()
except Exception as e:
    print(f"  Error: {e}")
