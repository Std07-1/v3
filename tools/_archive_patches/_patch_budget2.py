#!/usr/bin/env python3
"""Fix /budget set: use proper config path + in-memory update."""

FILE = "/opt/smc-trader-v3/bot/transport/handlers.py"
with open(FILE, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find the block to replace (line-by-line)
start_idx = None
end_idx = None
for i, line in enumerate(lines):
    if "# Persist to config.json on disk" in line and start_idx is None:
        start_idx = i
    if start_idx and "# Also reset exhausted flag" in line:
        end_idx = i
        break

if start_idx is None or end_idx is None:
    print(f"ERROR: block not found (start={start_idx}, end={end_idx})")
    exit(1)

print(f"Found block: lines {start_idx+1}-{end_idx+1}")
print("OLD block:")
for l in lines[start_idx:end_idx]:
    print(f"  |{l.rstrip()}")

# Determine indentation from first line
indent = "            "  # 12 spaces (3 levels of 4)

new_block = [
    f"{indent}# Persist to config.json on disk + update in-memory\n",
    f"{indent}import json as _json\n",
    f"{indent}from pathlib import Path as _Path\n",
    f"\n",
    f'{indent}_cfg_path = _Path(__file__).resolve().parent.parent / "config.json"\n',
    f"{indent}try:\n",
    f'{indent}    cfg_data = _json.loads(_cfg_path.read_text(encoding="utf-8"))\n',
    indent
    + '    cfg_data.setdefault("safety", {})["max_daily_budget_usd"] = new_limit\n',
    f"{indent}    _cfg_path.write_text(\n",
    f"{indent}        _json.dumps(cfg_data, ensure_ascii=False, indent=4),\n",
    f'{indent}        encoding="utf-8",\n',
    f"{indent}    )\n",
    f"{indent}except Exception as e:\n",
    f'{indent}    _log.warning("Config write failed: %s", e)\n',
    f"{indent}# Update in-memory immediately (no restart needed)\n",
    f"{indent}cfg.safety.max_daily_budget_usd = new_limit\n",
]

lines[start_idx:end_idx] = new_block

with open(FILE, "w", encoding="utf-8") as f:
    f.writelines(lines)

# Verify
with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

checks = [
    ("in-memory update", "cfg.safety.max_daily_budget_usd = new_limit" in content),
    ("Path import", "from pathlib import Path as _Path" in content),
    ("__file__ path", "_Path(__file__)" in content),
    ("no _config_path", "deps.cfg._config_path" not in content),
    ("no restart msg", "Набуде чинності після рестарту" not in content),
]
print("\n=== VERIFY ===")
all_ok = True
for name, ok in checks:
    print(f"  {'✅' if ok else '❌'} {name}")
    if not ok:
        all_ok = False

if all_ok:
    print("\n✅ ALL PATCHES APPLIED")
else:
    print("\n⚠️ SOME CHECKS FAILED")
