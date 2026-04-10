#!/usr/bin/env python3
"""Check prompt version, char count, and DNA fingerprint."""

import os

KNOWLEDGE = "/opt/smc-trader-v3/knowledge/smc_trader_prompt_v3.md"
ROOT = "/opt/smc-trader-v3/smc_trader_prompt_v3.md"
BACKUP = KNOWLEDGE + ".bak"

for label, path in [("CURRENT", KNOWLEDGE), ("ROOT_COPY", ROOT), ("BACKUP", BACKUP)]:
    if not os.path.exists(path):
        print(f"{label}: NOT FOUND")
        continue
    raw = open(path, "r", encoding="utf-8").read()
    text = raw.replace("\r", "")  # normalize CRLF
    chars = len(text)
    lines = text.count("\n")
    # DNA: simple hash
    dna_sum = sum(ord(c) for c in text)
    dna = dna_sum % 100000
    # Version line
    ver = ""
    for line in text.split("\n")[:5]:
        if "v3" in line.lower() or "prompt" in line.lower():
            ver = line.strip()
            break
    print(
        f"{label}: {chars} chars, {lines} lines, DNA(mod100k)={dna}, bytes={os.path.getsize(path)}"
    )
    print(f"  version: {ver}")
    # Check if "49175" chars matches
    if chars == 49175:
        print(f"  >>> MATCHES expected 49175 chars!")
    # First 3 lines
    first3 = text.split("\n")[:3]
    for i, l in enumerate(first3):
        print(f"  L{i+1}: {l[:120]}")
    print()
