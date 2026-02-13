"""Тест /api/updates — як UI бачить events."""
import urllib.request, json, time
from datetime import datetime, timezone

BASE = "http://localhost:8089"

# 1. First poll with no cursor — should get latest events
url1 = f"{BASE}/api/updates?symbol=XAU/USD&tf_s=60&limit=10"
resp1 = urllib.request.urlopen(url1)
data1 = json.loads(resp1.read())
events1 = data1.get("events", [])
cursor = data1.get("cursor_seq")
boot_id = data1.get("boot_id")
print("=== FIRST POLL (no cursor) ===")
print("events:", len(events1), " cursor_seq:", cursor, " boot_id:", boot_id)
for e in events1[-5:]:
    bar = e.get("bar", {})
    om = bar.get("open_time_ms", 0)
    dt = datetime.fromtimestamp(om/1000, tz=timezone.utc).strftime("%H:%M:%S") if om else "?"
    print("  seq=%s %s o=%.2f c=%.2f compl=%s src=%s" % (
        e.get("seq"), dt, bar.get("o", bar.get("open", 0)), 
        bar.get("c", bar.get("close", 0)),
        bar.get("complete"), bar.get("src")))
if data1.get("warnings"):
    print("  WARNINGS:", data1["warnings"])

# 2. Wait 3 seconds, then poll incrementally
time.sleep(3)
url2 = f"{BASE}/api/updates?symbol=XAU/USD&tf_s=60&limit=100&since_seq={cursor}"
resp2 = urllib.request.urlopen(url2)
data2 = json.loads(resp2.read())
events2 = data2.get("events", [])
cursor2 = data2.get("cursor_seq")
print("\n=== INCREMENTAL POLL (3s later, since_seq=%s) ===" % cursor)
print("events:", len(events2), " new_cursor:", cursor2)
for e in events2[-3:]:
    bar = e.get("bar", {})
    om = bar.get("open_time_ms", 0)
    dt = datetime.fromtimestamp(om/1000, tz=timezone.utc).strftime("%H:%M:%S") if om else "?"
    print("  seq=%s %s o=%.2f c=%.2f compl=%s src=%s" % (
        e.get("seq"), dt, bar.get("o", bar.get("open", 0)),
        bar.get("c", bar.get("close", 0)),
        bar.get("complete"), bar.get("src")))

# 3. Check: are there any FINAL events mixed in?
print("\n=== EVENT TYPES IN LAST 100 ===")
url3 = f"{BASE}/api/updates?symbol=XAU/USD&tf_s=60&limit=100"
resp3 = urllib.request.urlopen(url3)
data3 = json.loads(resp3.read())
events3 = data3.get("events", [])
by_src = {}
by_complete = {}
for e in events3:
    bar = e.get("bar", {})
    src = bar.get("src", "?")
    by_src[src] = by_src.get(src, 0) + 1
    comp = bar.get("complete")
    by_complete[comp] = by_complete.get(comp, 0) + 1

print("  by source:", by_src)
print("  by complete:", by_complete)

# 4. Check field names in events
if events3:
    print("\n=== EVENT BAR FIELD NAMES ===")
    bar = events3[-1].get("bar", {})
    print("  fields:", sorted(bar.keys()))
    print("  has 'open':", "open" in bar)
    print("  has 'o':", "o" in bar)
    print("  has 'close':", "close" in bar)
    print("  has 'c':", "c" in bar)
    print("  has 'time':", "time" in bar)
    print("  has 'open_time_ms':", "open_time_ms" in bar)
