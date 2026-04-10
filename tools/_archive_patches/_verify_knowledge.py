#!/usr/bin/env python3
import json

d = json.load(open("/opt/smc-trader-v3/data/v3_knowledge.json"))
print("Topics:", len(d))
total = sum(len(v) for v in d.values() if isinstance(v, list))
print("Total entries:", total)
for k in sorted(d.keys()):
    v = d[k]
    if isinstance(v, list):
        print(f"  {k}: {len(v)}/20")
