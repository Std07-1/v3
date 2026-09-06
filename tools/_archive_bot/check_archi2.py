#!/usr/bin/env python3
"""Read Archi findings and journal from stdin JSON."""

import json, sys

d = json.load(sys.stdin)

# Findings
findings = d.get("internal_findings", [])
print(f"=== FINDINGS ({len(findings)}) ===")
for f in findings:
    cat = f.get("category", "?")
    text = f.get("text", "")[:250]
    print(f"  [{cat}] {text}")
    print()

# Fired events
events = d.get("fired_events", [])
print(f"=== FIRED EVENTS ({len(events)}) ===")
for e in events:
    print(f"  {e}")

# Reflection log
refl = d.get("reflection_log", [])
print(f"\n=== REFLECTION LOG ({len(refl)}) ===")
for r in refl[-5:]:
    print(f"  {json.dumps(r, ensure_ascii=False)[:200]}")

# Scratchpad
sp = d.get("scratchpad", "")
if sp:
    print(f"\n=== SCRATCHPAD ===")
    print(f"  {sp[:500]}")

# Op rules
rules = d.get("operational_rules", [])
print(f"\n=== OPERATIONAL RULES ({len(rules)}) ===")
for r in rules:
    print(f"  {r[:200]}")
