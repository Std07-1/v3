import json

kb = json.load(open("/opt/smc-trader-v3/data/v3_knowledge.json"))
for k in sorted(kb.keys()):
    print(f"  {k}: {len(kb[k])} entries")
