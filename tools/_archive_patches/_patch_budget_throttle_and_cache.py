"""
Patch 1: BUDGET_WARN throttle (monitor.py)
  - Replace modulo-based guard with time-based throttle (once per 5 min)
  - Bug: agent_calls_today stays constant between API calls, so modulo check
    is True every 30s loop iteration when calls_today is a multiple of 5

Patch 2: Prompt cache fix (core.py)
  - Split system prompt in proactive call into 2 blocks:
    Block 1: personality prompt (cached, shared with reactive calls)
    Block 2: PROACTIVE_OPS_OVERLAY (not cached, proactive-specific)
  - This allows reactive and proactive calls to share the same cache entry
    (both use loaded_prompt/full_prompt from the same source)
  - Expected saving: ~$0.07 per cache-hit call (27K tokens at $3/MTok -> $0.30/MTok)
"""

import re
import ast

# ═══════════════════════════════════════════════════════════
# PATCH 1: BUDGET_WARN throttle
# ═══════════════════════════════════════════════════════════

MONITOR = "/opt/smc-trader-v3/bot/scheduling/monitor.py"

with open(MONITOR, "r", encoding="utf-8") as f:
    code = f.read()

# 1a. Add _last_budget_warn_ts = 0.0 near the top-level constants
if "_last_budget_warn_ts" not in code:
    code = code.replace(
        "BASE_CYCLE = 30",
        "BASE_CYCLE = 30\n_BUDGET_WARN_INTERVAL = 300  # log budget warning at most once per 5 min\n_last_budget_warn_ts: float = 0.0",
    )
    print("OK: Added _last_budget_warn_ts and _BUDGET_WARN_INTERVAL constants")
else:
    print("SKIP: _last_budget_warn_ts already exists")

# 1b. Replace the modulo-based guard with time-based throttle
# Use targeted line-by-line replacement to avoid whitespace issues
old_marker = "d.agent_calls_today % 5 == 0"
if old_marker in code:
    # Replace the entire budget warning block
    lines = code.split("\n")
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Find the start of the budget warning block
        if "# Budget warning at N% (once)" in line:
            indent = line[: len(line) - len(line.lstrip())]
            new_lines.append(
                f"{indent}# Budget warning at N% -- throttled to once per 5 min"
            )
            new_lines.append(f"{indent}global _last_budget_warn_ts")
            new_lines.append(
                f"{indent}warn_threshold = budget_limit * cfg.safety.budget_warn_pct / 100"
            )
            new_lines.append(f"{indent}_now_bw = time.time()")
            new_lines.append(f"{indent}if (")
            new_lines.append(
                f"{indent}    d.estimated_cost_usd_today >= warn_threshold"
            )
            new_lines.append(f"{indent}    and not d.budget_exhausted_notified")
            new_lines.append(
                f"{indent}    and (_now_bw - _last_budget_warn_ts) >= _BUDGET_WARN_INTERVAL"
            )
            new_lines.append(f"{indent}):")
            new_lines.append(f"{indent}    _last_budget_warn_ts = _now_bw")
            new_lines.append(f"{indent}    _log.info(")
            new_lines.append(
                f'{indent}        "BUDGET_WARN: $%.3f / $%.2f (%.0f%%) [next warn in %ds]",'
            )
            new_lines.append(f"{indent}        d.estimated_cost_usd_today,")
            new_lines.append(f"{indent}        budget_limit,")
            new_lines.append(
                f"{indent}        d.estimated_cost_usd_today / budget_limit * 100,"
            )
            new_lines.append(f"{indent}        _BUDGET_WARN_INTERVAL,")
            new_lines.append(f"{indent}    )")
            # Skip old block lines until we find the closing paren of _log.info
            i += 1
            # Skip until we pass the old _log.info(...) closing
            depth = 0
            found_log = False
            while i < len(lines):
                l = lines[i]
                if "_log.info(" in l:
                    found_log = True
                if found_log:
                    depth += l.count("(") - l.count(")")
                    if depth <= 0:
                        i += 1
                        break
                elif "# " in l.lstrip()[:2] and i > 0:
                    # Next section comment — we've gone too far
                    break
                elif l.strip() == "" and found_log:
                    break
                i += 1
            continue
        new_lines.append(line)
        i += 1
    code = "\n".join(new_lines)
    print("OK: Replaced BUDGET_WARN with time-based throttle (5 min)")
else:
    print("SKIP: modulo guard not found (already patched?)")

with open(MONITOR, "w", encoding="utf-8") as f:
    f.write(code)

try:
    ast.parse(code)
    print("SYNTAX OK: monitor.py")
except SyntaxError as e:
    print(f"SYNTAX ERROR: monitor.py: {e}")


# ═══════════════════════════════════════════════════════════
# PATCH 2: Prompt cache sharing (core.py)
# ═══════════════════════════════════════════════════════════

CORE = "/opt/smc-trader-v3/bot/agent/core.py"

with open(CORE, "r", encoding="utf-8") as f:
    core_code = f.read()

# Target: replace the concatenation approach with split blocks
concat_marker = "system = system_prompt + PROACTIVE_OPS_OVERLAY"
single_block = '"text": system,'

if concat_marker in core_code and single_block in core_code:
    lines = core_code.split("\n")
    new_lines = []
    i = 0
    state = (
        "scanning"  # scanning -> found_if -> found_else -> found_cache_block -> done
    )

    while i < len(lines):
        line = lines[i]

        # Find the "Build system prompt" comment
        if (
            state == "scanning"
            and "Build system prompt: full prompt + ops overlay" in line
        ):
            indent = "    "  # function-level indent
            new_lines.append(
                f"{indent}# Build system prompt: full prompt + ops overlay > personality_dna + legacy"
            )
            new_lines.append(
                f"{indent}# Cache strategy: personality prompt shared between reactive & proactive."
            )
            new_lines.append(
                f"{indent}# Split into separate blocks so Anthropic prefix-caches the shared part."
            )
            new_lines.append(f"{indent}if system_prompt:")
            new_lines.append(
                f"{indent}    from bot.state.directives import PROACTIVE_OPS_OVERLAY"
            )
            new_lines.append(f"")
            new_lines.append(f"{indent}    system_with_cache = [")
            new_lines.append(f"{indent}        {{")
            new_lines.append(f'{indent}            "type": "text",')
            new_lines.append(f'{indent}            "text": system_prompt,')
            new_lines.append(
                f'{indent}            "cache_control": {{"type": "ephemeral"}},'
            )
            new_lines.append(f"{indent}        }},")
            new_lines.append(f"{indent}        {{")
            new_lines.append(f'{indent}            "type": "text",')
            new_lines.append(f'{indent}            "text": PROACTIVE_OPS_OVERLAY,')
            new_lines.append(f"{indent}        }},")
            new_lines.append(f"{indent}    ]")
            new_lines.append(f"{indent}else:")
            new_lines.append(f"{indent}    base_system = SYSTEM_PROACTIVE_V2")
            new_lines.append(f"{indent}    system = (")
            new_lines.append(
                f'{indent}        (personality_dna + "\\n\\n" + base_system) if personality_dna else base_system'
            )
            new_lines.append(f"{indent}    )")
            new_lines.append(f"{indent}    system_with_cache = [")
            new_lines.append(f"{indent}        {{")
            new_lines.append(f'{indent}            "type": "text",')
            new_lines.append(f'{indent}            "text": system,')
            new_lines.append(
                f'{indent}            "cache_control": {{"type": "ephemeral"}},'
            )
            new_lines.append(f"{indent}        }}")
            new_lines.append(f"{indent}    ]")

            # Skip old block: from current line until after system_with_cache closing ']'
            i += 1
            found_swc_open = False
            while i < len(lines):
                l = lines[i]
                if "system_with_cache = [" in l:
                    found_swc_open = True
                if found_swc_open and l.strip() == "]":
                    i += 1  # skip the closing ]
                    break
                i += 1
            state = "done"
            continue

        new_lines.append(line)
        i += 1

    if state == "done":
        core_code = "\n".join(new_lines)
        print("OK: Split proactive system prompt into 2 blocks for cache sharing")
    else:
        print("FAIL: Could not find system prompt block structure")
else:
    if concat_marker not in core_code:
        print("SKIP: concat marker not found (already patched?)")
    if single_block not in core_code:
        print("SKIP: single block marker not found")

with open(CORE, "w", encoding="utf-8") as f:
    f.write(core_code)

try:
    ast.parse(core_code)
    print("SYNTAX OK: core.py")
except SyntaxError as e:
    print(f"SYNTAX ERROR: core.py: {e}")

print("\n=== DONE ===")
print("Restart smc_trader_v3 to apply changes")
