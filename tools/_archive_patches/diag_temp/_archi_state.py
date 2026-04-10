#!/usr/bin/env python3
"""Show Archi's recent notes and directives."""

import json, pathlib

base = pathlib.Path("/opt/smc-trader-v3/data")

# Notes
notes_f = base / "notes.json"
if notes_f.exists():
    raw = json.loads(notes_f.read_text())
    notes = raw if isinstance(raw, list) else raw.get("notes", [])
    print(f"=== NOTES ({len(notes)} total, last 5) ===")
    for n in notes[-5:]:
        txt = n if isinstance(n, str) else json.dumps(n, ensure_ascii=False)
        print(f"  • {txt[:300]}")
else:
    print("No notes.json found")

# Directives (watch_levels, wake_conditions, next_check)
dir_f = base / "directives.json"
if dir_f.exists():
    d = json.loads(dir_f.read_text())
    print(f"\n=== DIRECTIVES ===")
    print(f"  next_check_minutes: {d.get('next_check_minutes')}")
    wl = d.get("watch_levels", [])
    print(f"  watch_levels ({len(wl)}):")
    for w in wl[:5]:
        if isinstance(w, dict):
            print(
                f"    {w.get('label','?')} @ {w.get('price','?')} ({w.get('direction','?')})"
            )
        else:
            print(f"    {w}")
    wk = d.get("wake_conditions", [])
    print(f"  wake_conditions ({len(wk)}):")
    for c in wk[:5]:
        if isinstance(c, dict):
            print(f"    {c.get('condition','?')} → {c.get('action','?')}")
        else:
            print(f"    {c}")
    sc = d.get("scenario")
    if sc:
        print(f"  scenario: {json.dumps(sc, ensure_ascii=False)[:300]}")

# Recent lessons
journal_f = base / "journal.json"
if journal_f.exists():
    j = json.loads(journal_f.read_text())
    entries = j if isinstance(j, list) else j.get("entries", [])
    recent = [e for e in entries if isinstance(e, dict)][-3:]
    if recent:
        print(f"\n=== JOURNAL (last 3 of {len(entries)}) ===")
        for e in recent:
            print(
                f"  [{e.get('type','?')}] {str(e.get('text', e.get('lesson','')))[:250]}"
            )

# Self-model
sm_f = base / "self_model.json"
if sm_f.exists():
    sm = json.loads(sm_f.read_text())
    print(f"\n=== SELF_MODEL ===")
    for k, v in sm.items():
        print(f"  {k}: {str(v)[:200]}")

print("\n=== DONE ===")
