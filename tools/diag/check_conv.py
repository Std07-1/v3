#!/usr/bin/env python3
"""Quick diagnostic: dump conversation.json structure."""

import json, sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/smc-trader-v3/data/conversation.json"
with open(path) as f:
    d = json.load(f)

print(f"Top keys: {list(d.keys())}")
msgs = d.get("messages", [])
print(f"Messages: {len(msgs)}")
for i, m in enumerate(msgs):
    role = m.get("role", "?")
    content = str(m.get("content", ""))
    print(f"  [{i}] {role} ({len(content)} chars): {content[:200]}")
