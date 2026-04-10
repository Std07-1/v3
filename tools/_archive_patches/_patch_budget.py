#!/usr/bin/env python3
"""Patch /budget set to: 1) write to disk via known path, 2) update in-memory immediately."""

import re

FILE = "/opt/smc-trader-v3/bot/transport/handlers.py"
with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# ── Patch 1: Replace the broken config_path logic with hardcoded path ──
old_block = """            # Persist to config.json on disk
            import json as _json

            cfg_path = (
                deps.cfg._config_path if hasattr(deps.cfg, "_config_path") else None
            )
            if cfg_path:
                try:
                    cfg_data = _json.loads(cfg_path.read_text(encoding="utf-8"))
                    cfg_data.setdefault("safety", {})[
                        "max_daily_budget_usd"
                    ] = new_limit
                    cfg_path.write_text(
                        _json.dumps(cfg_data, ensure_ascii=False, indent=4),    
                        encoding="utf-8",
                    )
                except Exception as e:
                    _log.warning("Config write failed: %s", e)"""

new_block = """            # Persist to config.json on disk + update in-memory
            import json as _json
            from pathlib import Path as _Path

            _cfg_path = _Path(__file__).resolve().parent.parent / "config.json"
            try:
                cfg_data = _json.loads(_cfg_path.read_text(encoding="utf-8"))
                cfg_data.setdefault("safety", {})["max_daily_budget_usd"] = new_limit
                _cfg_path.write_text(
                    _json.dumps(cfg_data, ensure_ascii=False, indent=4),
                    encoding="utf-8",
                )
            except Exception as e:
                _log.warning("Config write failed: %s", e)
            # Update in-memory immediately (no restart needed)
            cfg.safety.max_daily_budget_usd = new_limit"""

if old_block in content:
    content = content.replace(old_block, new_block)
    print("PATCH 1 OK: config_path fix + in-memory update")
else:
    print("PATCH 1 SKIP: old block not found")
    # Try a more flexible match
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if "deps.cfg._config_path" in line:
            print(f"  Found _config_path ref at line {i+1}: {line.strip()}")

# ── Patch 2: Remove "після рестарту" message ──
old_msg = '''                f"✅ Ліміт: ${new_limit:.2f}\\n"
                f"⚠️ Набуде чинності після рестарту бота\\n"
                f"Або /budget reset щоб скинути лічильники зараз"'''

new_msg = '''                f"✅ Ліміт оновлено: ${new_limit:.2f}\\n"
                f"Діє негайно (без рестарту)\\n"
                f"/budget reset — скинути лічильники"'''

if old_msg in content:
    content = content.replace(old_msg, new_msg)
    print("PATCH 2 OK: message updated")
else:
    print("PATCH 2 SKIP: old message not found (trying flexible)")
    # Try flexible match
    if "Набуде чинності після рестарту бота" in content:
        content = content.replace(
            "Набуде чинності після рестарту бота", "Діє негайно (без рестарту)"
        )
        print("PATCH 2 OK (flexible)")

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)

# ── Verify ──
with open(FILE, "r", encoding="utf-8") as f:
    result = f.read()

checks = [
    ("in-memory update", "cfg.safety.max_daily_budget_usd = new_limit" in result),
    ("Path import", "from pathlib import Path as _Path" in result),
    ("__file__ path", "__file__" in result),
    ("no restart msg", "Набуде чинності після рестарту" not in result),
]
print("\n=== VERIFY ===")
for name, ok in checks:
    print(f"  {'✅' if ok else '❌'} {name}")

print("\nDONE")
