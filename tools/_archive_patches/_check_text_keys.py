#!/usr/bin/env python3
"""Find entries missing 'text' key in knowledge file."""

import json

path = "/opt/smc-trader-v3/data/v3_knowledge.json"
d = json.load(open(path))

bad = []
for topic, entries in d.items():
    if not isinstance(entries, list):
        bad.append((topic, "NOT_LIST", type(entries).__name__))
        continue
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            bad.append((topic, i, "NOT_DICT", type(e).__name__))
            continue
        if "text" not in e:
            bad.append((topic, i, "MISSING_TEXT", list(e.keys())))

print(f"Bad entries: {len(bad)}")
for b in bad:
    print(b)
