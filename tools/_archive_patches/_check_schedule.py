"""Check scheduling state of Archi bot."""

import json, time

d = json.load(open("/opt/smc-trader-v3/data/v3_agent_directives.json"))
print("next_check_min:", d.get("next_check_minutes"))

wakes = d.get("wake_at", [])
print(f"wake_at: {len(wakes)} timers")
for w in wakes:
    print(f"  {w.get('id')}: {w.get('time_iso')} — {w.get('reason', '')[:60]}")

watches = d.get("watch_levels", [])
print(f"watch_levels: {len(watches)}")
for w in watches:
    print(f"  {w.get('id')}: price={w.get('price')} dir={w.get('direction')}")

last = d.get("last_agent_call_ts", 0)
diff = int(time.time() - last)
print(f"last_call: {diff}s ago")
print(f"uptime_since_restart: ~{int(time.time() - 1744233185)}s")
