#!/usr/bin/env python3
"""Dump Archi bot state for diagnostics."""

import json, os, glob

BASE = "/opt/smc-trader-v3/data"

# 1. Conversation summary
conv_path = os.path.join(BASE, "v3_conversation.json")
if os.path.exists(conv_path):
    d = json.load(open(conv_path))
    if isinstance(d, list):
        msgs = d
        sums = []
    else:
        msgs = d.get("messages", [])
        sums = d.get("summaries", [])
    print(f"=== CONVERSATION: {len(msgs)} messages, {len(sums)} summaries ===")
    for m in msgs[-6:]:
        role = m.get("role", "?")
        content = str(m.get("content", ""))[:400]
        print(f"\n--- {role} ---")
        print(content)

# 2. Directives key fields
dir_path = os.path.join(BASE, "v3_agent_directives.json")
if os.path.exists(dir_path):
    dd = json.load(open(dir_path))
    print("\n=== DIRECTIVES ===")
    print(
        f"next_check: {dd.get('next_check_minutes')} min — {dd.get('next_check_reason','?')}"
    )
    wl = dd.get("watch_levels", [])
    print(f"watch_levels: {len(wl)}")
    for w in wl:
        print(f"  {w['id']}: {w['direction']} {w['price']} prio={w.get('priority',0)}")
    mm = dd.get("market_mental_model", {})
    print(
        f"mental_model: macro={mm.get('macro_bias','?')} structure={mm.get('structure_bias','?')}"
    )
    scens = mm.get("scenarios", [])
    for s in scens:
        print(
            f"  scenario {s['id']}: dir={s['direction']} conf={s.get('confidence',0)} role={s.get('role','?')}"
        )
        print(f"    thesis: {str(s.get('thesis',''))[:200]}")
    mc = dd.get("metacognition", {})
    print(
        f"metacognition: accuracy={mc.get('scenario_accuracy',0)} eval={mc.get('scenarios_evaluated',0)}"
    )
    budget = dd.get("budget_strategy", "?")
    print(f"budget_strategy: {budget}")

# 3. Thinking archive recent
ta_dir = os.path.join(BASE, "thinking_archive")
if os.path.isdir(ta_dir):
    files = sorted(glob.glob(os.path.join(ta_dir, "*.json")))
    print(f"\n=== THINKING ARCHIVE: {len(files)} files ===")
    for f in files[-3:]:
        entry = json.load(open(f))
        ts = entry.get("timestamp", "?")
        trigger = entry.get("trigger", "?")
        thinking = str(entry.get("thinking", ""))[:300]
        print(f"\n--- {ts} trigger={trigger} ---")
        print(thinking)

# 4. Knowledge recent entries
kb_path = os.path.join(BASE, "v3_knowledge.json")
if os.path.exists(kb_path):
    kb = json.load(open(kb_path))
    entries = kb.get("entries", [])
    print(f"\n=== KNOWLEDGE: {len(entries)} entries ===")
    for e in entries[-5:]:
        print(f"  [{e.get('category','?')}] {str(e.get('content',''))[:150]}")

# 5. Last log lines check
import subprocess

r = subprocess.run(
    ["tail", "-5", "/opt/smc-trader-v3/logs/supervisor.log"],
    capture_output=True,
    text=True,
)
print(f"\n=== LAST 5 LOG LINES ===")
print(r.stdout)
