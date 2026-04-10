#!/usr/bin/env python3
"""Read last conversation messages from Archi."""

import json, os

DIR = "/opt/smc-trader-v3/data"

# Conversation
conv = json.load(open(os.path.join(DIR, "v3_conversation.json")))

# It might be a list directly
if isinstance(conv, list):
    msgs = conv
elif isinstance(conv, dict):
    msgs = conv.get("messages", [])
else:
    msgs = []

print(f"Total messages: {len(msgs)}")
print()

# Last 8 messages
for m in msgs[-8:]:
    if isinstance(m, dict):
        role = m.get("role", "?")
        text = str(m.get("text", m.get("content", "")))[:600]
        ts = m.get("ts", m.get("timestamp", ""))
        print(f"--- [{role}] {ts} ---")
        print(text)
        print()
    elif isinstance(m, list):
        # Maybe [role, text] format
        for item in m[-2:]:
            print(str(item)[:400])
        print()
    else:
        print(str(m)[:400])
        print()

# Also check learning journal
print("=== LEARNING JOURNAL ===")
try:
    j = json.load(open(os.path.join(DIR, "v3_learning_journal.json")))
    if isinstance(j, dict):
        les = j.get("lessons", j.get("entries", []))
    elif isinstance(j, list):
        les = j
    else:
        les = []
    print(f"Total lessons: {len(les)}")
    for l in les[-3:]:
        if isinstance(l, dict):
            print(
                f"  [{l.get('tag',l.get('type',''))}] {str(l.get('text',l.get('lesson','')))[:250]}"
            )
        else:
            print(f"  {str(l)[:250]}")
except Exception as e:
    print(f"  Error: {e}")
