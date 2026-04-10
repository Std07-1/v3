#!/usr/bin/env python3
"""Remove mangled sed lines from platform.py."""

import pathlib

p = pathlib.Path("/opt/smc-trader-v3/bot/transport/platform.py")
lines = p.read_text().splitlines(keepends=True)

# Remove lines that contain mangled sed output (no proper quotes for keys)
bad_patterns = [
    "ick_ts_ms: data.get(",
    "data_quality: data.get(data_quality)",
    "h4_forming: data.get(h4_forming)",
    "tick_price: data.get( ick_price)",
    "tick_price: data.get(\tick_price)",
]

cleaned = []
removed = 0
for line in lines:
    stripped = line.strip()
    if any(pat in stripped for pat in bad_patterns):
        removed += 1
        continue
    cleaned.append(line)

if removed > 0:
    p.write_text("".join(cleaned))
    print(f"CLEANED: removed {removed} mangled lines")
else:
    print("CLEAN: no mangled lines found")

# Verify
t = p.read_text()
assert '"tick_price": data.get("tick_price")' in t, "tick_price field missing!"
print("VERIFIED: tick_price properly present")
