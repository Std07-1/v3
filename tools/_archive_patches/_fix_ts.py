#!/usr/bin/env python3
"""Fix ts field type mismatch in knowledge entries.
All ts values must be int (epoch), not ISO strings."""

import json, os
from datetime import datetime, timezone

KNOWLEDGE_FILE = "/opt/smc-trader-v3/data/v3_knowledge.json"


def fix_ts(data):
    fixed = 0
    for topic, entries in data.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            ts_val = entry.get("ts")
            if ts_val is None:
                # No ts — add current epoch
                entry["ts"] = int(datetime.now(timezone.utc).timestamp())
                fixed += 1
            elif isinstance(ts_val, str):
                # ISO string → epoch int
                try:
                    dt = datetime.fromisoformat(ts_val)
                    entry["ts"] = int(dt.timestamp())
                except ValueError:
                    entry["ts"] = int(datetime.now(timezone.utc).timestamp())
                fixed += 1
            elif isinstance(ts_val, float):
                entry["ts"] = int(ts_val)
                fixed += 1
            # int is already correct
    return fixed


data = json.load(open(KNOWLEDGE_FILE))
fixed = fix_ts(data)
with open(KNOWLEDGE_FILE, "w") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Fixed {fixed} ts values")

# Verify: check all ts are int now
bad = 0
for topic, entries in data.items():
    if not isinstance(entries, list):
        continue
    for entry in entries:
        ts = entry.get("ts")
        if not isinstance(ts, int):
            print(f"  STILL BAD: {topic} ts={ts} type={type(ts).__name__}")
            bad += 1
print(f"Verification: {bad} bad ts values remaining")
