"""Patch core.py — wire ToolRegistry into reactive + proactive tool dispatch.

4 changes:
1. Add import of get_registry
2. Replace tools_with_cache in call_reactive (line ~400)
3. Replace block.name == "emit_directives" in reactive (line ~472)
4. Replace tools_with_cache in call_agent_proactive (line ~654)
5. Replace block.name == "emit_directives" in proactive (line ~725)
"""

import shutil
from pathlib import Path

TARGET = Path("/opt/smc-trader-v3/bot/agent/core.py")
BACKUP = TARGET.with_suffix(".py.bak_executor")

src = TARGET.read_text(encoding="utf-8")
orig = src

# ── Backup ──
shutil.copy2(TARGET, BACKUP)
print(f"BACKUP: {BACKUP}")

changes = 0

# ── 1. Add import after existing bot.state.directives import ──
# Find the import block that has DIRECTIVES_TOOL
import_marker = "from bot.state.directives import ("
if import_marker in src:
    # Add our import AFTER the closing paren of that import
    # Find the closing ")" of that multiline import
    idx_start = src.index(import_marker)
    idx_paren = src.index(")", idx_start + len(import_marker))
    # Find the next newline after the closing paren
    idx_nl = src.index("\n", idx_paren)
    insert_line = "\nfrom bot.tools.executor import get_registry"
    if "from bot.tools.executor" not in src:
        src = src[:idx_nl] + insert_line + src[idx_nl:]
        changes += 1
        print("PATCH 1: Added import get_registry")
    else:
        print("PATCH 1: SKIP — import already exists")
else:
    # Fallback: add after all imports
    print("PATCH 1: WARNING — marker not found, adding at top")
    src = "from bot.tools.executor import get_registry\n" + src
    changes += 1

# ── Helper: build tools_with_cache from registry ──
# We need a helper that's lazy-initialized
helper_code = '''
# ── Tool Registry (lazy singleton) ──
def _get_tools_with_cache():
    """Tools list for Claude API with cache_control."""
    return [
        {**schema, "cache_control": {"type": "ephemeral"}}
        for schema in get_registry().get_schemas()
    ]

'''

# Add helper after the import section (before first function def)
if "_get_tools_with_cache" not in src:
    # Find a good insertion point — after imports, before first function
    # Look for the first "def " or "class " after imports
    marker_2 = "from bot.agent.prompts import ("
    if marker_2 in src:
        idx2 = src.index(marker_2)
        # Find the closing ")" and next newline
        idx2_paren = src.index(")", idx2 + len(marker_2))
        idx2_nl = src.index("\n", idx2_paren)
        # Find the next blank line after that (end of imports section)
        # Look for "\n\n" after this point
        search_from = idx2_nl
        next_blank = src.find("\n\n", search_from)
        if next_blank > 0:
            src = src[: next_blank + 1] + helper_code + src[next_blank + 1 :]
            changes += 1
            print("PATCH 1b: Added _get_tools_with_cache helper")
    else:
        print("PATCH 1b: WARNING — could not find insertion point for helper")
else:
    print("PATCH 1b: SKIP — helper already exists")

# ── 2. Replace tools_with_cache in call_reactive ──
# Old pattern (line ~400):
old_reactive_tools = """    tools_with_cache = [
        {**DIRECTIVES_TOOL, "cache_control": {"type": "ephemeral"}},
    ]

    # Extended thinking for Opus (deep analysis, chart photos)"""

new_reactive_tools = """    tools_with_cache = _get_tools_with_cache()

    # Extended thinking for Opus (deep analysis, chart photos)"""

if old_reactive_tools in src:
    src = src.replace(old_reactive_tools, new_reactive_tools, 1)
    changes += 1
    print("PATCH 2: Replaced reactive tools_with_cache")
else:
    # Try flexible match
    marker_r = "    tools_with_cache = [\n        {**DIRECTIVES_TOOL"
    if marker_r in src:
        idx_r = src.index(marker_r)
        # Find the closing "]" of the list
        idx_r_end = src.index("    ]", idx_r)
        idx_r_nl = src.index("\n", idx_r_end)
        old_block = src[idx_r : idx_r_nl + 1]
        # Count how many times this pattern appears
        count = src.count(marker_r)
        if count >= 1:
            src = src.replace(
                old_block, "    tools_with_cache = _get_tools_with_cache()\n", 1
            )
            changes += 1
            print(
                f"PATCH 2: Replaced reactive tools_with_cache (flexible, {count} total matches)"
            )
    else:
        print("PATCH 2: WARNING — reactive tools_with_cache not found")

# ── 3. Replace block.name check in reactive ──
old_reactive_check = 'block.type == "tool_use" and block.name == "emit_directives"'
new_reactive_check = 'block.type == "tool_use" and get_registry().has_tool(block.name)'

count_check = src.count(old_reactive_check)
if count_check >= 1:
    src = src.replace(old_reactive_check, new_reactive_check)
    changes += count_check
    print(f"PATCH 3+5: Replaced {count_check} block.name checks (reactive + proactive)")
else:
    print("PATCH 3: WARNING — block.name check not found")

# ── 4. Replace tools_with_cache in call_agent_proactive ──
# This is the second occurrence of the pattern
old_proactive_tools_marker = "    tools_with_cache = [\n        {**DIRECTIVES_TOOL"
if old_proactive_tools_marker in src:
    idx_p = src.index(old_proactive_tools_marker)
    idx_p_end = src.index("    ]", idx_p)
    idx_p_nl = src.index("\n", idx_p_end)
    old_block_p = src[idx_p : idx_p_nl + 1]
    src = src.replace(
        old_block_p, "    tools_with_cache = _get_tools_with_cache()\n", 1
    )
    changes += 1
    print("PATCH 4: Replaced proactive tools_with_cache")
else:
    print("PATCH 4: SKIP — proactive tools_with_cache already patched or not found")

# ── Write result ──
if changes > 0:
    TARGET.write_text(src, encoding="utf-8")
    print(f"\nPATCHED {TARGET}: {changes} changes")

    # Syntax check
    import py_compile

    try:
        py_compile.compile(str(TARGET), doraise=True)
        print("SYNTAX CHECK: OK")
    except py_compile.PyCompileError as e:
        print(f"SYNTAX CHECK: FAIL — {e}")
        # Restore backup
        shutil.copy2(BACKUP, TARGET)
        print("RESTORED from backup due to syntax error")
else:
    print("\nNO CHANGES needed")
