import json

c = json.load(open("/opt/smc-v3/config.json", encoding="utf-8"))
smc = c.get("smc", {})
print("compute_tfs:", smc.get("compute_tfs"))
print("enabled:", smc.get("enabled"))
print("hide_mitigated:", smc.get("hide_mitigated"))
print("display_budget:", smc.get("display_budget"))
print("warmup_bars:", smc.get("warmup_bars"))
