import json, redis

r = redis.Redis(host="127.0.0.1", port=6379, db=1, decode_responses=True)

# Find all smc-related keys
all_keys = r.keys("*smc*")
print("All smc keys:", all_keys[:20])

# Also check v3_local keys related to zones
v3_keys = r.keys("v3_local:*")
smc_related = [
    k
    for k in v3_keys
    if "smc" in k.lower() or "zone" in k.lower() or "snapshot" in k.lower()
]
print("SMC-related v3_local keys:", smc_related[:20])

# Check ws_server processes — is SmcRunner storing data somewhere else?
print("\n=== Total v3_local keys ===")
print(f"Total: {len(v3_keys)}")
sample = sorted(v3_keys)[:30]
for k in sample:
    t = r.type(k)
    print(f"  {k} [{t}]")
