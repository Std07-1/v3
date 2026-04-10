#!/usr/bin/env python3
"""One-shot patch: fix mangled platform.py and add new fields."""

import pathlib

p = pathlib.Path("/opt/smc-trader-v3/bot/transport/platform.py")
lines = p.read_text().splitlines(keepends=True)

# Find the line with server_ts_ms in the return dict
target_idx = None
for i, line in enumerate(lines):
    if '"server_ts_ms": data.get("server_ts_ms", 0),' in line:
        target_idx = i
        break

if target_idx is None:
    print("ERROR: cannot find server_ts_ms line")
    raise SystemExit(1)

# Check if already patched
for line in lines[target_idx + 1 : target_idx + 6]:
    if '"tick_price"' in line:
        print("ALREADY PATCHED: tick_price already present")
        raise SystemExit(0)

# Remove any mangled lines between server_ts_ms and the closing }
end_idx = target_idx + 1
while end_idx < len(lines):
    stripped = lines[end_idx].strip()
    if stripped == "}":
        break
    # Skip mangled content (lines with tick_price/data_quality/h4_forming without proper quotes)
    if any(
        k in stripped
        for k in ["tick_price", "data_quality", "h4_forming", "tick_ts_ms"]
    ):
        end_idx += 1
        continue
    break

# Build new lines
new_fields = [
    '        "tick_price": data.get("tick_price"),\n',
    '        "tick_ts_ms": data.get("tick_ts_ms"),\n',
    '        "data_quality": data.get("data_quality") or {},\n',
    '        "h4_forming": data.get("h4_forming"),\n',
]

# Replace: keep everything up to and including server_ts_ms line, add new fields, then closing }
result = lines[: target_idx + 1] + new_fields + lines[end_idx:]
p.write_text("".join(result))
print(
    f"PATCHED OK: removed {end_idx - target_idx - 1} mangled lines, added 4 new fields"
)
