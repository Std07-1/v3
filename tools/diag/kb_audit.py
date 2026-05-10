#!/usr/bin/env python3
"""Quick audit of trader bot knowledge base files."""

import json, collections, sys
from datetime import datetime

DATA = "/opt/smc-trader-v3/data"


def audit_file(path, label):
    try:
        data = json.load(open(path))
    except Exception as e:
        print(f"\n=== {label} === ERROR: {e}")
        return

    if isinstance(data, list):
        print(
            f"\n=== {label} === ({len(data)} entries, {len(json.dumps(data))//1024}KB)"
        )
        sources = collections.Counter(e.get("source", "?") for e in data)
        cats = collections.Counter(e.get("category", "?") for e in data)
        print(f"  Sources: {dict(sources)}")
        print(f"  Categories: {dict(cats)}")
        tss = [e["ts"] for e in data if "ts" in e]
        if tss:
            print(
                f"  TS range: {datetime.utcfromtimestamp(min(tss))} — {datetime.utcfromtimestamp(max(tss))}"
            )
        # Detect duplicates
        texts = [e.get("text", "") for e in data]
        dupes = len(texts) - len(set(texts))
        if dupes:
            print(f"  ⚠ DUPLICATES: {dupes} duplicate text entries!")
        # Last 10
        print(f"  --- Last 10 entries ---")
        for e in data[-10:]:
            ts_str = (
                datetime.utcfromtimestamp(e["ts"]).strftime("%Y-%m-%d %H:%M")
                if "ts" in e
                else "?"
            )
            cat = e.get("category", e.get("type", "?"))
            src = e.get("source", "?")
            txt = e.get("text", str(e))[:160].replace("\n", " ")
            print(f"    [{cat}] {ts_str} src={src} | {txt}")
    elif isinstance(data, dict):
        print(f"\n=== {label} === (dict, {len(json.dumps(data))//1024}KB)")
        for k, v in data.items():
            if isinstance(v, list):
                print(f"  {k}: {len(v)} items")
                # Check timestamps
                tss = [e.get("ts") for e in v if isinstance(e, dict) and "ts" in e]
                if tss:
                    print(
                        f"    TS range: {datetime.utcfromtimestamp(min(tss))} — {datetime.utcfromtimestamp(max(tss))}"
                    )
                # Duplicates
                texts = [e.get("text", "") for e in v if isinstance(e, dict)]
                dupes = len(texts) - len(set(texts))
                if dupes:
                    print(f"    ⚠ DUPLICATES: {dupes}")
            elif isinstance(v, dict):
                print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:200]}")
            else:
                print(f"  {k}: {str(v)[:200]}")
    else:
        print(f"\n=== {label} === type={type(data).__name__}")
        print(f"  {str(data)[:500]}")


files = [
    (f"{DATA}/v3_knowledge.json", "v3_knowledge"),
    (f"{DATA}/v3_learning_journal.json", "v3_learning_journal"),
    (f"{DATA}/v3_symbol_profile.json", "v3_symbol_profile"),
    (f"{DATA}/v3_bot_config.json", "v3_bot_config"),
    (f"{DATA}/v3_agent_directives.json", "v3_agent_directives"),
    (f"{DATA}/v3_channel_briefings.json", "v3_channel_briefings"),
    (f"{DATA}/v3_market_state.json", "v3_market_state"),
    (f"{DATA}/knowledge.json", "OLD knowledge"),
    (f"{DATA}/market_state.json", "OLD market_state"),
]

for path, label in files:
    audit_file(path, label)

# Check lessons dir
import os

lessons_dir = f"{DATA}/lessons"
if os.path.isdir(lessons_dir):
    files_list = os.listdir(lessons_dir)
    print(f"\n=== lessons/ dir === ({len(files_list)} files)")
    for f in sorted(files_list):
        fp = os.path.join(lessons_dir, f)
        sz = os.path.getsize(fp)
        print(f"  {f} ({sz} bytes)")

# Duplicate detail in learning journal
print("\n=== DUPLICATE ANALYSIS (v3_learning_journal) ===")
journal = json.load(open(f"{DATA}/v3_learning_journal.json"))
seen = {}
for i, e in enumerate(journal):
    t = e.get("text", "")
    if t in seen:
        print(f"  DUPE idx {i} = idx {seen[t]}: {t[:100]}")
    else:
        seen[t] = i

# Cross-check: KB vs Journal overlap
print("\n=== KB vs JOURNAL OVERLAP ===")
kb = json.load(open(f"{DATA}/v3_knowledge.json"))
kb_texts = set()
for cat in kb.values():
    if isinstance(cat, list):
        for e in cat:
            if isinstance(e, dict):
                kb_texts.add(e.get("text", ""))
journal_texts = set(e.get("text", "") for e in journal)
overlap = kb_texts & journal_texts
print(f"  KB unique texts: {len(kb_texts)}")
print(f"  Journal unique texts: {len(journal_texts)}")
print(f"  Overlap (same text in both): {len(overlap)}")
if len(overlap) == len(kb_texts):
    print("  => ALL KB entries also in Journal (Journal is superset)")

# Check auto-learning entries (entries added by bot, not migrated)
print("\n=== AUTO-LEARNED ENTRIES (source=auto) ===")
auto = [e for e in journal if e.get("source") == "auto"]
print(f"  Total auto-learned: {len(auto)}")
if auto:
    cats = collections.Counter(e.get("category", "?") for e in auto)
    print(f"  Categories: {dict(cats)}")
    tss = [e["ts"] for e in auto if "ts" in e]
    if tss:
        print(
            f"  TS range: {datetime.utcfromtimestamp(min(tss))} — {datetime.utcfromtimestamp(max(tss))}"
        )
    # Sample last 3 auto-learned
    print("  --- Last 3 auto-learned ---")
    for e in auto[-3:]:
        ts_str = datetime.utcfromtimestamp(e["ts"]).strftime("%Y-%m-%d %H:%M")
        print(f"    [{e.get('category','?')}] {ts_str}: {e['text'][:180]}")

# Check knowledge structure health
print("\n=== KNOWLEDGE STRUCTURE HEALTH ===")
# Is there new knowledge being added after migration?
migrated_ts = 1774933070  # latest migrated_* timestamp
new_kb = [
    e
    for e in kb.get("правила", []) + kb.get("сетапи", []) + kb.get("нотатки", [])
    if e.get("ts", 0) > migrated_ts
]
print(f"  KB entries added AFTER migration: {len(new_kb)}")
if new_kb:
    for e in new_kb:
        print(f"    {datetime.utcfromtimestamp(e['ts'])}: {e['text'][:120]}")
else:
    print("  ⚠ KB has NOT grown since initial migration! Only journal grows.")
