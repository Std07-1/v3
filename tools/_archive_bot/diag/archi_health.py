#!/usr/bin/env python3
"""Archi bot health diagnostic — full state dump."""

import json, os, time
from datetime import datetime, timezone

BASE = "/opt/smc-trader-v3/data"


def main():
    # 1. Directives
    dp = os.path.join(BASE, "v3_agent_directives.json")
    if os.path.exists(dp):
        d = json.load(open(dp))
        print("=== DIRECTIVES ===")
        print(f"TSM: {d.get('trade_state_machine')}")
        print(f"next_check_min: {d.get('next_check_minutes')}")
        print(f"user_signal: {d.get('user_signal')}")
        print(f"analyst_model: {d.get('analyst_model')}")
        wl = d.get("watch_levels", [])
        print(f"watch_levels: {len(wl)}")
        for w in wl[:5]:
            print(f"  {w.get('label','')} @ {w.get('price','')} → {w.get('action','')}")
        sc = d.get("scenarios", [])
        print(f"scenarios: {len(sc)}")
        for s in sc:
            print(
                f"  {s.get('id','')} dir={s.get('direction','')} status={s.get('status','')}"
            )
            for cp in s.get("checkpoints", [])[-2:]:
                print(f"    cp: {cp.get('note','')[:80]}")
        wk = d.get("wake_conditions", [])
        print(f"wake_conditions: {len(wk)}")
        for w in wk:
            print(f"  {w.get('condition','')[:100]}")
        mm = d.get("market_mental_model", {})
        print(
            f"mental_model: macro={mm.get('macro_direction','')} struct={mm.get('structure_direction','')}"
        )
        kls = mm.get("key_levels", [])
        if kls:
            print(
                f"  key_levels: {kls[:6] if isinstance(kls[0], str) else [(l.get('label',''), l.get('price','')) for l in kls[:6]]}"
            )
    else:
        print("NO DIRECTIVES FILE")

    # 2. Conversation
    cp = os.path.join(BASE, "v3_conversation.json")
    if os.path.exists(cp):
        c = json.load(open(cp))
        msgs = c.get("messages", []) if isinstance(c, dict) else c
        print(f"\n=== CONVERSATION ===")
        print(f"Total messages: {len(msgs)}")
        if msgs:
            last = msgs[-1]
            print(
                f"Last: role={last.get('role','?')} ts={last.get('timestamp','?')[:25]}"
            )
            print(f"  content: {str(last.get('content',''))[:200]}")
    else:
        print("NO CONVERSATION FILE")

    # 3. Knowledge
    kp = os.path.join(BASE, "v3_knowledge.json")
    if os.path.exists(kp):
        k = json.load(open(kp))
        entries = k if isinstance(k, list) else k.get("entries", [])
        print(f"\n=== KNOWLEDGE ===")
        print(f"Entries: {len(entries)}")
        for e in entries[-3:]:
            print(f"  [{e.get('category','')}] {str(e.get('content',''))[:100]}")

    # 4. Timers
    tp = os.path.join(BASE, "v3_agent_directives.json")
    if os.path.exists(tp):
        d = json.load(open(tp))
        timers = d.get("timers", [])
        print(f"\n=== TIMERS ===")
        print(f"Count: {len(timers)}")
        for t in timers[:5]:
            print(
                f"  {t.get('id','')} @ {t.get('fire_at','')} active={t.get('active',True)}"
            )

    # Journal
    jp = os.path.join(BASE, "v3_event_journal.jsonl")
    if os.path.exists(jp):
        lines = open(jp).readlines()
        print(f"\n=== EVENT JOURNAL ===")
        print(f"Total events: {len(lines)}")
        for line in lines[-5:]:
            try:
                ev = json.loads(line)
                print(
                    f"  {ev.get('ts','')[:19]} {ev.get('type','')} {ev.get('src','')} → {str(ev.get('summary',''))[:80]}"
                )
            except:
                pass


if __name__ == "__main__":
    main()
