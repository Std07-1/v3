import json, sys

path = "/opt/smc-trader-v3/data/v3_agent_directives.json"
d = json.load(open(path))

# Find circuit breaker related fields
for k in sorted(d.keys()):
    kl = k.lower()
    if any(w in kl for w in ["error", "consec", "circuit", "breaker"]):
        print(f"{k} = {d[k]}")

# Reset consecutive_errors to 0
if "consecutive_errors" in d:
    print(f"\nResetting consecutive_errors from {d['consecutive_errors']} to 0")
    d["consecutive_errors"] = 0
    with open(path, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print("DONE: saved")
else:
    # Maybe it's a nested key — look deeper
    print("\nAll top-level keys:", list(d.keys())[:30])
    for k, v in d.items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                if "error" in k2.lower() or "consec" in k2.lower():
                    print(f"  {k}.{k2} = {v2}")
