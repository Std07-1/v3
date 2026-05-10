"""Quick diagnostic: test broker_sidecar fetch with different n values."""
import redis
import json
import uuid
import time

r = redis.Redis(host="127.0.0.1", port=6379, db=1, decode_responses=True)
ns = "v3_local"

for n in [5, 10, 20, 50, 100, 150, 200]:
    rid = uuid.uuid4().hex
    rk = f"{ns}:broker:m1:bars:{rid}"
    cmd = json.dumps({
        "v": 1, "cmd": "fetch_m1", "req_id": rid,
        "reply_to": rk, "symbol": "XAU/USD",
        "n_bars": n, "date_to_ms": None,
    })
    r.rpush(f"{ns}:broker:m1:cmd", cmd)
    result = r.blpop(rk, timeout=60)
    r.delete(rk)
    if result:
        resp = json.loads(result[1])
        bars = resp.get("bars", [])
        err = resp.get("error")
        print(f"n={n:>3}  bars={len(bars)}  err={err}")
    else:
        print(f"n={n:>3}  TIMEOUT (60s)")
    time.sleep(2)  # space out requests
