"""Audit Archi's data scale for memory evolution planning."""

import json, os

DATA = "/opt/smc-trader-v3/data"

# Knowledge base
try:
    kb = json.load(open(f"{DATA}/v3_knowledge.json"))
    if isinstance(kb, dict):
        total = 0
        for k, v in kb.items():
            n = len(v) if isinstance(v, list) else 1
            total += n
            print(f"  KB topic '{k}': {n} entries")
        print(f"  KB TOTAL: {total} entries")
except Exception as e:
    print(f"  KB error: {e}")

# Journal
print()
try:
    j = json.load(open(f"{DATA}/v3_learning_journal.json"))
    if isinstance(j, dict):
        for k, v in j.items():
            if isinstance(v, list):
                print(f"  Journal '{k}': {len(v)} entries")
    elif isinstance(j, list):
        print(f"  Journal: {len(j)} entries")
except Exception as e:
    print(f"  Journal error: {e}")

# Directives
print()
try:
    d = json.load(open(f"{DATA}/v3_agent_directives.json"))
    for k in sorted(d.keys()):
        v = d[k]
        if isinstance(v, list):
            print(f"  Dir '{k}': {len(v)} items")
        elif isinstance(v, dict):
            print(f"  Dir '{k}': {len(v)} keys")
        elif isinstance(v, str) and len(v) > 50:
            print(f"  Dir '{k}': str({len(v)} chars)")
        else:
            print(f"  Dir '{k}': {repr(v)[:80]}")
except Exception as e:
    print(f"  Dir error: {e}")

# Self scores
print()
try:
    s = json.load(open(f"{DATA}/v3_self_scores.json"))
    if isinstance(s, dict):
        for k, v in s.items():
            if isinstance(v, list):
                print(f"  Scores '{k}': {len(v)} entries")
    elif isinstance(s, list):
        print(f"  Scores: {len(s)} entries")
except Exception as e:
    print(f"  Scores error: {e}")

# Symbol profile
print()
try:
    sp = json.load(open(f"{DATA}/v3_symbol_profile.json"))
    if isinstance(sp, dict):
        for k, v in sp.items():
            n = len(v) if isinstance(v, list) else 1
            print(f"  Profile '{k}': {n} entries")
except Exception as e:
    print(f"  Profile error: {e}")

print("\n=== SUMMARY ===")
total_bytes = sum(
    os.path.getsize(f"{DATA}/{f}") for f in os.listdir(DATA) if f.endswith(".json")
)
print(
    f"Total data: {total_bytes/1024:.1f} KB across {len([f for f in os.listdir(DATA) if f.endswith('.json')])} files"
)
