#!/usr/bin/env python3
"""
Patch: Add budget limit visibility to Archi's context.

Currently Archi sees: "~$5.643 spent" but doesn't know the limit is $6.00.
After patch: "💰 $5.643 / $6.00 budget (94%) — economy mode"

Changes:
1. directives.py: add budget_limit kwarg to build_directives_context, enhance stats line
2. core.py: pass budget_limit at proactive call site
3. handlers.py: pass budget_limit at all 5 reactive/other call sites
"""

import re
import sys
from pathlib import Path

BASE = Path("/opt/smc-trader-v3")

# ══════════════════════════════════════════════════════════
# 1. directives.py — function signature + stats line
# ══════════════════════════════════════════════════════════
fp_dir = BASE / "bot" / "state" / "directives.py"
text = fp_dir.read_text(encoding="utf-8")

# 1a. Add budget_limit kwarg to function signature
old_sig = 'def build_directives_context(\n    d: AgentDirectives, current_price: float = 0.0, memory_stats: str = ""\n) -> str:'
new_sig = 'def build_directives_context(\n    d: AgentDirectives, current_price: float = 0.0, memory_stats: str = "",\n    budget_limit: float = 0.0,\n) -> str:'

if old_sig not in text:
    print("ERROR: Could not find build_directives_context signature in directives.py")
    sys.exit(1)
text = text.replace(old_sig, new_sig, 1)

# 1b. Replace stats line with budget-aware version
old_stats = (
    "    # Stats\n"
    "    parts.append(\n"
    '        f"\\nСтатистика за сьогодні: {d.agent_calls_today} calls, "\n'
    '        f"{d.messages_sent_today} messages, ~${d.estimated_cost_usd_today:.3f} spent"\n'
    "    )"
)
new_stats = (
    "    # Stats + Budget\n"
    "    if budget_limit > 0:\n"
    "        _bpct = d.estimated_cost_usd_today / budget_limit * 100\n"
    '        _bmode = " — ECONOMY MODE" if _bpct >= 80 else ""\n'
    "        parts.append(\n"
    '            f"\\n💰 Бюджет: ${d.estimated_cost_usd_today:.2f} / ${budget_limit:.2f} ({_bpct:.0f}%){_bmode}"\n'
    "        )\n"
    "        parts.append(\n"
    '            f"Статистика: {d.agent_calls_today} calls, {d.messages_sent_today} msgs"\n'
    "        )\n"
    "    else:\n"
    "        parts.append(\n"
    '            f"\\nСтатистика за сьогодні: {d.agent_calls_today} calls, "\n'
    '            f"{d.messages_sent_today} messages, ~${d.estimated_cost_usd_today:.3f} spent"\n'
    "        )"
)

if old_stats not in text:
    print("ERROR: Could not find old stats block in directives.py")
    print("Looking for:")
    print(repr(old_stats))
    # Debug: find the line
    for i, line in enumerate(text.split("\n"), 1):
        if "Статистика за сьогодні" in line:
            print(f"  Found at line {i}: {line!r}")
    sys.exit(1)
text = text.replace(old_stats, new_stats, 1)

fp_dir.write_text(text, encoding="utf-8")
print(f"OK directives.py patched ({len(text)} chars)")

# ══════════════════════════════════════════════════════════
# 2. core.py — proactive call site (line ~595)
# ══════════════════════════════════════════════════════════
fp_core = BASE / "bot" / "agent" / "core.py"
text_core = fp_core.read_text(encoding="utf-8")

old_core = "    dir_context = build_directives_context(\n        directives, current_price=current_price, memory_stats=memory_stats\n    )"
# Only replace inside call_agent_proactive (which has cfg: Any = None)
new_core = "    dir_context = build_directives_context(\n        directives, current_price=current_price, memory_stats=memory_stats,\n        budget_limit=cfg.safety.max_daily_budget_usd if cfg else 0.0,\n    )"

count = text_core.count(old_core)
if count == 0:
    print("ERROR: Could not find proactive build_directives_context call in core.py")
    sys.exit(1)
if count > 1:
    print(f"WARNING: Found {count} matches in core.py — replacing FIRST only")
text_core = text_core.replace(old_core, new_core, 1)

fp_core.write_text(text_core, encoding="utf-8")
print(f"OK core.py patched ({len(text_core)} chars)")

# ══════════════════════════════════════════════════════════
# 3. handlers.py — all 5 call sites
# ══════════════════════════════════════════════════════════
fp_handlers = BASE / "bot" / "transport" / "handlers.py"
text_h = fp_handlers.read_text(encoding="utf-8")

# Pattern: all variants of build_directives_context( calls
# They all end with memory_stats=...) — we add budget_limit before closing paren

# Site 1 (line 252): uses mem_stats variable
old_h1 = "    dir_ctx = build_directives_context(\n        directives, current_price=current_price, memory_stats=mem_stats\n    )"
new_h1 = "    dir_ctx = build_directives_context(\n        directives, current_price=current_price, memory_stats=mem_stats,\n        budget_limit=cfg.safety.max_daily_budget_usd,\n    )"
if old_h1 in text_h:
    text_h = text_h.replace(old_h1, new_h1, 1)
    print("OK handlers.py site 1 (line ~252)")
else:
    print("WARN: handlers.py site 1 not found (may already be patched)")

# Sites 2-5: use deps.state.format_memory_stats() inline
old_h_inline = (
    "        dir_ctx = build_directives_context(\n"
    "            directives,\n"
    "            current_price=extract_price(ctx),\n"
    "            memory_stats=deps.state.format_memory_stats(),\n"
    "        )"
)
new_h_inline = (
    "        dir_ctx = build_directives_context(\n"
    "            directives,\n"
    "            current_price=extract_price(ctx),\n"
    "            memory_stats=deps.state.format_memory_stats(),\n"
    "            budget_limit=cfg.safety.max_daily_budget_usd,\n"
    "        )"
)
count_inline = text_h.count(old_h_inline)
if count_inline > 0:
    text_h = text_h.replace(old_h_inline, new_h_inline)  # replace ALL occurrences
    print(f"OK handlers.py sites 2-3 (extract_price pattern, {count_inline} matches)")
else:
    print("WARN: handlers.py extract_price pattern not found")

# Sites 4-5: current_price=current_price variant
old_h_cp = (
    "        dir_ctx = build_directives_context(\n"
    "            directives,\n"
    "            current_price=current_price,\n"
    "            memory_stats=deps.state.format_memory_stats(),\n"
    "        )"
)
new_h_cp = (
    "        dir_ctx = build_directives_context(\n"
    "            directives,\n"
    "            current_price=current_price,\n"
    "            memory_stats=deps.state.format_memory_stats(),\n"
    "            budget_limit=cfg.safety.max_daily_budget_usd,\n"
    "        )"
)
count_cp = text_h.count(old_h_cp)
if count_cp > 0:
    text_h = text_h.replace(old_h_cp, new_h_cp)  # replace ALL occurrences
    print(f"OK handlers.py sites 4-5 (current_price pattern, {count_cp} matches)")
else:
    print("WARN: handlers.py current_price pattern not found")

fp_handlers.write_text(text_h, encoding="utf-8")
print(f"OK handlers.py saved ({len(text_h)} chars)")

print("\n=== PATCH COMPLETE ===")
print("Restart bot to apply: sudo supervisorctl restart smc_trader_v3")
