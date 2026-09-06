#!/usr/bin/env python3
"""Hotfix: Fix VP description syntax error (bare newlines in string literals)."""

import sys
from pathlib import Path

DRY = "--dry-run" in sys.argv
fpath = Path("/opt/smc-trader-v3/bot/state/directives.py")
text = fpath.read_text(encoding="utf-8")

# The problem: multiline string inside ("..." ) has bare newlines
# Fix: replace the entire description block with properly formatted string
old = """                "description": ("Virtual trade position (VP). LIFECYCLE:        \n"\n                    "\xe2\x80\xa2 action='open': \xd0\x92\xd1\x96\xd0\xb4\xd0\xba\xd1\x80\xd0\xb8\xd0\xb9 \xd0\xa2\xd0\x86\xd0\x9b\xd0\xac\xd0\x9a\xd0\x98 \xd0\xbf\xd1\x96\xd1\x81\xd0\xbb\xd1\x8f Discipline Gate PASS + \xd1\x81\xd1\x86\xd0\xb5\xd0\xbd\xd0\xb0\xd1\x80\xd1\x96\xd0\xb9 active.\n"\n                    "\xe2\x80\xa2 action='close': \xd0\x97\xd0\xb0\xd0\xba\xd1\x80\xd0\xb8\xd0\xb9 \xd0\xb7 \xd0\x9e\xd0\x91\xd0\x9e\xd0\x92'\xd0\xaf\xd0\x97\xd0\x9a\xd0\x9e\xd0\x92\xd0\x98\xd0\x9c close_reason \xd1\x96 close_price.\n"\n                    "\xd0\x9f\xd0\xa0\xd0\x90\xd0\x92\xd0\x98\xd0\x9b\xd0\x9e: \xd0\x9a\xd0\xbe\xd0\xb6\xd0\xbd\xd0\xb5 \xd0\xb7\xd0\xb0\xd0\xba\xd1\x80\xd0\xb8\xd1\x82\xd1\x82\xd1\x8f = \xd0\xbe\xd0\xba\xd1\x80\xd0\xb5\xd0\xbc\xd0\xb0 \xd0\xb4\xd1\x96\xd1\x8f. close \xd0\x9f\xd0\x9e\xd0\xa2\xd0\x86\xd0\x9c open, \xd0\xbd\xd0\xb5 \xd0\xbe\xd0\xb4\xd0\xbd\xd0\xbe\xd1\x87\xd0\xb0\xd1\x81\xd0\xbd\xd0\xbe.\n"\n                    "\xd0\x9d\xd0\xb5 \xd0\xbc\xd1\x96\xd0\xbd\xd1\x8f\xd0\xb9 VP \xd0\xb1\xd0\xb5\xd0\xb7 \xd0\xbf\xd1\x80\xd0\xb8\xd1\x87\xd0\xb8\xd0\xbd\xd0\xb8 \xe2\x80\x94 \xd1\x86\xd0\xb5 \xd1\x85\xd0\xb0\xd0\xbe\xd1\x81 (P3 pitfall)."),"""

# Try a simpler approach: find the broken block by regex
import re

# Find description line with opening paren — replace until closing ),
pattern = re.compile(
    r'("description": \("Virtual trade position \(VP\)\. LIFECYCLE:.*?"Не міняй VP без причини — це хаос \(P3 pitfall\)\."\))',
    re.DOTALL,
)

m = pattern.search(text)
if not m:
    # Try finding by simpler pattern
    start_marker = '"description": ("Virtual trade position (VP). LIFECYCLE:'
    idx = text.find(start_marker)
    if idx < 0:
        print("FAIL: cannot find VP description block")
        sys.exit(1)
    end_marker = 'Не міняй VP без причини — це хаос (P3 pitfall).")'
    end_idx = text.find(end_marker, idx)
    if end_idx < 0:
        print("FAIL: cannot find end of VP description block")
        sys.exit(1)
    old_block = text[idx : end_idx + len(end_marker)]
else:
    old_block = m.group(0)

new_block = (
    '"description": '
    '"Virtual trade position (VP). LIFECYCLE: '
    "• action=open: Відкрий ТІЛЬКИ після Discipline Gate PASS + сценарій active. "
    "• action=close: Закрий з ОБОВ'ЯЗКОВИМ close_reason і close_price. "
    "ПРАВИЛО: Кожне закриття = окрема дія. close ПОТІМ open, не одночасно. "
    'Не міняй VP без причини — це хаос (P3 pitfall)."'
)

print(f"Found block ({len(old_block)} chars)")
if DRY:
    print("DRY-OK would replace VP description")
else:
    text = text.replace(old_block, new_block, 1)
    fpath.write_text(text, encoding="utf-8")
    print("OK: fixed VP description syntax")
