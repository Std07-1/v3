#!/usr/bin/env python3
"""Quick check of /api/context zones on VPS. Run via: ssh aione-vps 'curl -s ... | python3 -'"""

import sys, json

d = json.load(sys.stdin)
zones = d.get("zones", [])
print(f"Total zones: {len(zones)}")
for z in zones:
    print(
        f"  {z.get('kind'):20s} grade={z.get('grade','-'):3s} {z.get('high')}-{z.get('low')} status={z.get('status')} tf={z.get('tf_s')}"
    )
top = d.get("top_zones", [])
print(f"Top zones: {len(top)}")
for z in top:
    print(
        f"  {z.get('kind'):20s} grade={z.get('grade','-'):3s} {z.get('high')}-{z.get('low')} status={z.get('status')} tf={z.get('tf_s')}"
    )
sigs = d.get("signals", [])
print(f"Signals: {len(sigs)}")
narr = d.get("narrative", {})
print(
    f"Narrative: mode={narr.get('mode','?')} headline={str(narr.get('headline',''))[:60]}"
)
print(f"Bias: {d.get('bias_map', {})}")
print(f"P/D: {d.get('pd_state', {})}")
