#!/usr/bin/env python3
"""Defensive patch for manager.py — fix kb_summary and kb_search."""

import re

path = "/opt/smc-trader-v3/bot/state/manager.py"
with open(path) as f:
    code = f.read()

original = code
changes = 0

# Fix 1: kb_summary sort — ensure ts is int
old1 = 'key=lambda e: (e.get("importance", 3), e.get("ts", 0)),'
new1 = 'key=lambda e: (e.get("importance", 3), int(e.get("ts", 0))),'
if old1 in code:
    code = code.replace(old1, new1, 1)
    changes += 1
    print(f"Fix 1: ts int cast in kb_summary sort")

# Fix 2: kb_summary text access — use .get() with default
old2 = 'texts = [e["text"][:250] for e in sorted_entries[:5]]'
new2 = 'texts = [e.get("text", e.get("content", ""))[:250] for e in sorted_entries[:5]]'
if old2 in code:
    code = code.replace(old2, new2, 1)
    changes += 1
    print(f"Fix 2: text .get() fallback in kb_summary")

# Fix 3: kb_search sort — ensure ts is int
old3 = 'scored.sort(key=lambda x: (x[0], x[2].get("ts", 0)), reverse=True)'
new3 = 'scored.sort(key=lambda x: (x[0], int(x[2].get("ts", 0))), reverse=True)'
if old3 in code:
    code = code.replace(old3, new3, 1)
    changes += 1
    print(f"Fix 3: ts int cast in kb_search sort")

if changes > 0:
    with open(path, "w") as f:
        f.write(code)
    print(f"\nApplied {changes} fixes to {path}")
else:
    print("No changes needed (already patched?)")
