import json

d = json.load(open("/opt/smc-trader-v3/data/v3_agent_directives.json"))

print("=== LAST THOUGHT ===")
th = d.get("thought_history", [])
if th:
    print(th[-1].get("text", "")[:800])

print("\n=== SELF_MODEL ===")
sm = d.get("self_model", {})
for k, v in sm.items():
    val = str(v)[:200]
    print(f"  {k}: {val}")

print("\n=== INNER THOUGHT ===")
print(d.get("inner_thought", "")[:500])

print("\n=== LAST LESSON ===")
j = json.load(open("/opt/smc-trader-v3/data/v3_learning_journal.json"))
entries = j.get("entries", [])
if entries:
    e = entries[-1]
    print(f"  [{e.get('category')}] {e.get('text','')[:300]}")
