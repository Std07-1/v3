import json

c = json.load(open("config.json"))
smc = c.get("smc", {})
print("=== SMC CONFIG ===")
print(json.dumps(smc, indent=2)[:3000])
print("\n=== display_filter ===")
df = c.get("display_filter", {})
print(json.dumps(df, indent=2)[:1000])
