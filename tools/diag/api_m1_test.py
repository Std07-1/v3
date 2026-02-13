"""Quick M1 API test."""
import urllib.request, json, time
from datetime import datetime, timezone

url = "http://localhost:8089/api/bars?symbol=XAU/USD&tf_s=60&limit=200"
resp = urllib.request.urlopen(url)
data = json.loads(resp.read())
bars = data.get("bars", [])
print("TOTAL bars:", len(bars))

# Last 5 bars
print("\n=== LAST 5 BARS ===")
for b in bars[-5:]:
    dt_s = datetime.fromtimestamp(b["time"], tz=timezone.utc).strftime("%H:%M")
    print("  %s  O=%.2f H=%.2f L=%.2f C=%.2f  V=%.0f  compl=%s  src=%s" % (
        dt_s, b["open"], b["high"], b["low"], b["close"],
        b.get("volume", 0), b.get("complete"), b.get("src")
    ))

# Freshness
now = time.time()
now_utc = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%H:%M:%S")
print("\nNOW UTC:", now_utc)
if bars:
    last_t = bars[-1]["time"]
    last_dt = datetime.fromtimestamp(last_t, tz=timezone.utc).strftime("%H:%M:%S")
    diff_s = int(now - last_t)
    print("Last bar time:", last_dt, " age_s:", diff_s)
    # Is last bar preview?
    print("Last bar complete:", bars[-1].get("complete"))
    print("Last bar src:", bars[-1].get("src"))

# Check gaps in last 20 bars 
print("\n=== GAP ANALYSIS (last 20) ===")
gap_count = 0
for i in range(max(0, len(bars)-20), len(bars)-1):
    t1 = bars[i]["time"]
    t2 = bars[i+1]["time"]
    dt1 = datetime.fromtimestamp(t1, tz=timezone.utc).strftime("%H:%M")
    dt2 = datetime.fromtimestamp(t2, tz=timezone.utc).strftime("%H:%M")
    diff = t2 - t1
    if diff != 60:  # M1 = 60s gap
        gap_count += 1
        print("  GAP: %s -> %s  diff=%ds (expected 60s)" % (dt1, dt2, diff))

if gap_count == 0:
    print("  No gaps in last 20 bars")

# Meta  
meta = data.get("meta", {})
print("\n=== META ===")
for k, v in meta.items():
    print("  %s: %s" % (k, v))

# Check updates endpoint
print("\n=== UPDATES ENDPOINT ===")
upd_url = "http://localhost:8089/api/updates?symbol=XAU/USD&tf_s=60&limit=5"
try:
    resp2 = urllib.request.urlopen(upd_url)
    upd_data = json.loads(resp2.read())
    events = upd_data.get("events", [])
    print("events count:", len(events))
    for e in events[-3:]:
        bar = e.get("bar", {})
        om = bar.get("open_time_ms") or bar.get("open_ms")
        dt_e = datetime.fromtimestamp(om/1000, tz=timezone.utc).strftime("%H:%M") if om else "?"
        print("  %s  compl=%s  src=%s" % (dt_e, bar.get("complete"), bar.get("src")))
except Exception as exc:
    print("  ERROR:", exc)
