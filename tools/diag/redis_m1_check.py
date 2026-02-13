"""Діагностика Redis стану для M1 XAU/USD."""
import redis, json, sys
from datetime import datetime, timezone

r = redis.Redis(host="127.0.0.1", port=6379, db=1, decode_responses=True)
ns = "v3_local"
sym = "XAU/USD"

def ts_str(ms):
    if not ms: return "?"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

# 1. All keys related to XAU/USD M1
print("=== ALL REDIS KEYS matching XAU/USD + 60 ===")
for k in sorted(r.keys(f"{ns}:*XAU*60*")):
    t = r.type(k)
    if t == "string":
        v = r.get(k)
        try:
            d = json.loads(v)
            if isinstance(d, dict) and "bars" in d:
                print(f"  {k}  type={t}  bars_count={len(d['bars'])}")
                if d["bars"]:
                    last = d["bars"][-1]
                    om = last.get("open_ms") or last.get("open_time_ms")
                    print(f"    last_bar: open={ts_str(om)}  complete={last.get('complete')}")
            elif isinstance(d, dict):
                print(f"  {k}  type={t}  keys={list(d.keys())[:10]}")
            else:
                print(f"  {k}  type={t}  val_preview={str(v)[:120]}")
        except:
            print(f"  {k}  type={t}  raw={v[:120]}")
    elif t == "list":
        ln = r.llen(k)
        print(f"  {k}  type=list  len={ln}")
        if ln > 0:
            last = r.lindex(k, -1)
            try:
                d = json.loads(last)
                om = d.get("open_ms") or d.get("open_time_ms") or d.get("bar", {}).get("open_ms")
                print(f"    last_item: open={ts_str(om)}  complete={d.get('complete', d.get('bar', {}).get('complete'))}")
            except:
                print(f"    last_item: {last[:120]}")
    else:
        print(f"  {k}  type={t}")

# 2. Preview keys
print("\n=== PREVIEW KEYS for XAU/USD ===")
for k in sorted(r.keys(f"{ns}:preview:*XAU*")):
    t = r.type(k)
    if t == "string":
        v = r.get(k)
        try:
            d = json.loads(v)
            if isinstance(d, dict):
                om = d.get("open_ms") or d.get("open_time_ms")
                print(f"  {k}  open={ts_str(om)}  complete={d.get('complete')}  keys={list(d.keys())[:8]}")
            else:
                print(f"  {k}  {str(v)[:200]}")
        except:
            print(f"  {k}  raw={v[:200]}")
    elif t == "list":
        ln = r.llen(k)
        print(f"  {k}  type=list  len={ln}")
    else:
        print(f"  {k}  type={t}")

# 3. Updates bus
print("\n=== UPDATES BUS for XAU/USD M1 ===")
upd_key = f"{ns}:updates:XAU/USD:60"
t = r.type(upd_key)
if t == "list":
    ln = r.llen(upd_key)
    print(f"  {upd_key}  len={ln}")
    if ln > 0:
        last = r.lindex(upd_key, -1)
        try:
            d = json.loads(last)
            bar = d.get("bar", {})
            om = bar.get("open_ms") or bar.get("open_time_ms")
            print(f"    last_update: open={ts_str(om)}  complete={bar.get('complete')}  source={bar.get('source')}")
        except:
            print(f"    last: {last[:200]}")
elif t == "none":
    print(f"  {upd_key}  NOT FOUND")
else:
    print(f"  {upd_key}  type={t}")

# 4. Tick PubSub channels
print("\n=== PUBSUB CHANNELS ===")
channels = r.pubsub_channels(f"{ns}:tick:*")
print(f"  tick channels: {channels}")
channels2 = r.pubsub_channels(f"{ns}:*")
print(f"  all ns channels: {channels2}")

# 5. M1 watermark
print("\n=== WATERMARKS ===")
for k in sorted(r.keys(f"{ns}:wm:*")):
    v = r.get(k)
    print(f"  {k} = {v}  ({ts_str(int(v)) if v and v.isdigit() else v})")

# 6. Status key  
print("\n=== STATUS ===")
for k in sorted(r.keys(f"{ns}:status:*")):
    v = r.get(k)
    try:
        d = json.loads(v)
        print(f"  {k}  {json.dumps(d, indent=2)[:500]}")
    except:
        print(f"  {k}  {v[:200] if v else 'null'}")

print("\n=== DONE ===")
