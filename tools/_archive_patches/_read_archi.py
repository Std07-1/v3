#!/usr/bin/env python3
"""Read Archi's state files and print a summary."""

import json, os, sys

DATA = "/opt/smc-trader-v3/data"


def load(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


# === DIRECTIVES ===
d = load("v3_agent_directives.json")
if d:
    print("=== DIRECTIVES KEYS ===")
    print(list(d.keys()))

    notes = d.get("notes", [])
    print(f"\n=== NOTES ({len(notes)} total, showing last 5) ===")
    for i, n in enumerate(notes[-5:]):
        text = n if isinstance(n, str) else json.dumps(n, ensure_ascii=False)
        idx = len(notes) - 5 + i
        print(f"\n--- Note #{idx} [{len(text)} chars] ---")
        print(text[:500])

    print(f"\n=== WATCH LEVELS ===")
    wl = d.get("watch_levels", [])
    print(json.dumps(wl, indent=2, ensure_ascii=False)[:800])

    print(f"\n=== SCENARIOS ===")
    sc = d.get("scenarios", d.get("scenario", "none"))
    print(json.dumps(sc, indent=2, ensure_ascii=False)[:800])

    print(f"\n=== WAKE CONDITIONS ===")
    wc = d.get("wake_conditions", [])
    print(json.dumps(wc, indent=2, ensure_ascii=False)[:600])

    print(f"\n=== NEXT CHECK MINUTES ===")
    print(d.get("next_check_minutes", "?"))

    print(f"\n=== BUDGET ===")
    print(json.dumps(d.get("budget", {}), indent=2, ensure_ascii=False)[:400])
else:
    print("No directives file found")

# === LEARNING JOURNAL (last 3 entries) ===
j = load("v3_learning_journal.json")
if j:
    entries = j if isinstance(j, list) else j.get("entries", j.get("lessons", []))
    print(f"\n\n=== LEARNING JOURNAL ({len(entries)} entries, last 3) ===")
    for e in entries[-3:]:
        text = (
            json.dumps(e, indent=2, ensure_ascii=False)
            if isinstance(e, dict)
            else str(e)
        )
        print(f"\n{text[:400]}")
else:
    print("\nNo learning journal found")

# === SELF SCORES (last entry) ===
ss = load("v3_self_scores.json")
if ss:
    entries = ss if isinstance(ss, list) else ss.get("scores", ss.get("entries", []))
    print(f"\n\n=== SELF SCORES ({len(entries)} entries, latest) ===")
    if entries:
        latest = entries[-1]
        print(json.dumps(latest, indent=2, ensure_ascii=False)[:600])
else:
    print("\nNo self scores found")

# === CONDENSED CONTEXT ===
cc = load("v3_condensed_context.json")
if cc:
    print(f"\n\n=== CONDENSED CONTEXT ===")
    print(json.dumps(cc, indent=2, ensure_ascii=False)[:800])

print("\n\n=== DONE ===")
