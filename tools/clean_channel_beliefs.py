#!/usr/bin/env python3
"""Clean stale wrong lessons about channel reading from Archi's state files."""

import json

# 1. Clean learning journal
with open("/opt/smc-trader-v3/data/v3_learning_journal.json") as f:
    journal = json.load(f)

wrong_phrases = [
    "не можу читати",
    "публікую наосліп",
    "не маю доступу до читання",
    "НЕ можу читати",
]
before = len(journal)
journal = [e for e in journal if not any(p in e.get("text", "") for p in wrong_phrases)]
after = len(journal)
print(f"Journal: {before} -> {after} (removed {before - after})")

with open("/opt/smc-trader-v3/data/v3_learning_journal.json", "w") as f:
    json.dump(journal, f, ensure_ascii=False, indent=2)

# 2. Clean directives findings
with open("/opt/smc-trader-v3/data/v3_agent_directives.json") as f:
    d = json.load(f)

findings = d.get("internal_findings", [])
before_f = len(findings)
findings = [
    f for f in findings if not any(p in f.get("text", "") for p in wrong_phrases)
]
d["internal_findings"] = findings
print(f"Findings: {before_f} -> {len(findings)} (removed {before_f - len(findings)})")

with open("/opt/smc-trader-v3/data/v3_agent_directives.json", "w") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

print("Done. Stale 'can't read channel' beliefs removed.")
