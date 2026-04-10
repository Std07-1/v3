#!/usr/bin/env python3
import json

d = json.load(open("/opt/smc-trader-v3/data/v3_knowledge.json"))
print("Type:", type(d).__name__)
print("Keys:", list(d.keys())[:15])
# Show structure of first few items
for k in list(d.keys())[:3]:
    v = d[k]
    if isinstance(v, list):
        print(f"\n  '{k}': list of {len(v)}")
        if v:
            print(
                f"    first item keys: {list(v[0].keys()) if isinstance(v[0], dict) else type(v[0]).__name__}"
            )
    elif isinstance(v, dict):
        print(f"\n  '{k}': dict with keys {list(v.keys())[:5]}")
    else:
        print(f"\n  '{k}': {type(v).__name__} = {str(v)[:100]}")
