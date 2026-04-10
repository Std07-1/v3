#!/usr/bin/env python3
"""Read Archi's thinking state (inner thoughts, scratchpad, reflection)."""

import json, os

DATA = "/opt/smc-trader-v3/data"

with open(os.path.join(DATA, "v3_agent_directives.json")) as f:
    d = json.load(f)

print("=== INNER THOUGHT ===")
it = d.get("inner_thought", "empty")
if isinstance(it, str):
    print(it[:1000])
else:
    print(json.dumps(it, indent=2, ensure_ascii=False)[:1000])

print("\n=== THOUGHT HISTORY (last 5) ===")
th = d.get("thought_history", [])
print(f"Total thoughts: {len(th)}")
for i, t in enumerate(th[-5:]):
    idx = len(th) - 5 + i
    text = t if isinstance(t, str) else json.dumps(t, ensure_ascii=False)
    print(f"\n--- Thought #{idx} [{len(text)}ch] ---")
    print(text[:400])

print("\n\n=== SCRATCHPAD ===")
sp = d.get("scratchpad", "empty")
if isinstance(sp, str):
    print(sp[:1500])
elif isinstance(sp, list):
    for s in sp[-5:]:
        print(json.dumps(s, ensure_ascii=False)[:300])
else:
    print(json.dumps(sp, indent=2, ensure_ascii=False)[:1500])

print("\n\n=== REFLECTION LOG (last 3) ===")
rl = d.get("reflection_log", [])
print(f"Total reflections: {len(rl)}")
for r in rl[-3:]:
    text = (
        json.dumps(r, indent=2, ensure_ascii=False) if isinstance(r, dict) else str(r)
    )
    print(f"\n{text[:500]}")

print("\n\n=== MOOD ===")
print(json.dumps(d.get("mood", "unknown"), indent=2, ensure_ascii=False)[:300])

print("\n\n=== SELF MODEL ===")
sm = d.get("self_model", {})
print(json.dumps(sm, indent=2, ensure_ascii=False)[:800])

print("\n\n=== TRADER PROFILE ===")
tp = d.get("trader_profile", {})
print(json.dumps(tp, indent=2, ensure_ascii=False)[:800])

print("\n\n=== INTERNAL FINDINGS (last 3) ===")
fi = d.get("internal_findings", [])
print(f"Total: {len(fi)}")
for f_ in fi[-3:]:
    text = json.dumps(f_, ensure_ascii=False) if isinstance(f_, dict) else str(f_)
    print(f"\n{text[:400]}")

print("\n\n=== FIRED EVENTS (last 3) ===")
fe = d.get("fired_events", [])
print(f"Total: {len(fe)}")
for e in fe[-3:]:
    text = json.dumps(e, ensure_ascii=False) if isinstance(e, dict) else str(e)
    print(f"\n{text[:300]}")

print("\n\n=== WEEKLY PLAN ===")
wp = d.get("weekly_plan", "none")
print(
    json.dumps(wp, indent=2, ensure_ascii=False)[:800]
    if not isinstance(wp, str)
    else wp
)

print("\n\n=== DONE ===")
