#!/usr/bin/env python3
"""Fix entries: rename 'content' -> 'text', remove extra fields."""

import json

path = "/opt/smc-trader-v3/data/v3_knowledge.json"
d = json.load(open(path))

fixed = 0
for topic, entries in d.items():
    if not isinstance(entries, list):
        continue
    for e in entries:
        if not isinstance(e, dict):
            continue
        # Rename content -> text
        if "content" in e and "text" not in e:
            e["text"] = e.pop("content")
            fixed += 1
        # Clean extra fields - keep only text, ts, source
        extra = [k for k in e if k not in ("text", "ts", "source")]
        for k in extra:
            del e[k]

with open(path, "w") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

print(f"Fixed {fixed} entries (content -> text)")

# Verify
d2 = json.load(open(path))
bad = 0
for topic, entries in d2.items():
    if isinstance(entries, list):
        for e in entries:
            if "text" not in e:
                bad += 1
            extra = [k for k in e if k not in ("text", "ts", "source")]
            if extra:
                bad += 1
print(f"Remaining bad: {bad}")
