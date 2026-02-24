"""D1 глибокий аудит — інвентаризація даних по всіх символах.

Перевіряємо:
1. Disk: скільки D1 барів, останній бар (open_time_ms, OHLCV, complete, src)
2. Redis snap: останній бар (OHLCV) — чи flat (O==H==L==C)?
3. Redis tail: довжина
4. Redis updates bus: останні events для D1
5. UDS read_window: скільки барів повертає, останній
6. /api/bars (HTTP v3): останній бар
"""
import json, os, sys, glob, redis
from datetime import datetime, timezone

DATA_ROOT = "data_v3"
REDIS_DB = 1
REDIS_NS = "v3_local"
TF_S = 86400

def ts_fmt(ms):
    if ms is None:
        return "None"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

def check_disk(symbol):
    """Читаємо останні бари з JSONL."""
    sym_dir = symbol.replace("/", "_")
    tf_dir = os.path.join(DATA_ROOT, sym_dir, f"tf_{TF_S}")
    if not os.path.isdir(tf_dir):
        return {"disk_exists": False}
    
    files = sorted(glob.glob(os.path.join(tf_dir, "*.jsonl")))
    if not files:
        return {"disk_exists": True, "files": 0, "bars": 0}
    
    # Читаємо останній файл, останні 5 барів
    last_file = files[-1]
    bars = []
    with open(last_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                bars.append(json.loads(line))
            except:
                pass
    
    total_bars = 0
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            total_bars += sum(1 for l in f if l.strip())
    
    last5 = bars[-5:] if bars else []
    result = {
        "disk_exists": True,
        "files": len(files),
        "total_bars": total_bars,
        "last_bars": []
    }
    for b in last5:
        ot = b.get("open_time_ms", b.get("open_ms"))
        o = b.get("open", b.get("o"))
        h = b.get("high", b.get("h"))
        l = b.get("low", b.get("l"))
        c = b.get("close", b.get("c"))
        v = b.get("volume", b.get("v"))
        comp = b.get("complete")
        src = b.get("src", b.get("source"))
        is_flat = (o == h == l == c) if None not in (o,h,l,c) else None
        result["last_bars"].append({
            "t": ts_fmt(ot),
            "open_ms": ot,
            "O": o, "H": h, "L": l, "C": c, "V": v,
            "complete": comp, "src": src,
            "FLAT": is_flat
        })
    return result

def check_redis(r, symbol):
    """Перевіряємо Redis snap/tail/updates."""
    sym_key = symbol.replace("/", "_")
    
    # snap
    snap_key = f"{REDIS_NS}:snap:{sym_key}:{TF_S}"
    snap_raw = r.get(snap_key)
    snap_info = None
    if snap_raw:
        try:
            snap = json.loads(snap_raw)
            o, h, l, c, v = snap.get("o"), snap.get("h"), snap.get("l"), snap.get("c"), snap.get("v")
            snap_info = {
                "O": o, "H": h, "L": l, "C": c, "V": v,
                "FLAT": (o == h == l == c) if None not in (o,h,l,c) else None,
                "open_ms": snap.get("open_ms", snap.get("open_time_ms")),
                "t": ts_fmt(snap.get("open_ms", snap.get("open_time_ms"))),
                "ttl": r.ttl(snap_key)
            }
        except:
            snap_info = {"raw_len": len(snap_raw)}
    
    # tail
    tail_key = f"{REDIS_NS}:tail:{sym_key}:{TF_S}"
    tail_raw = r.get(tail_key)
    tail_info = None
    if tail_raw:
        try:
            tail_data = json.loads(tail_raw)
            if isinstance(tail_data, list):
                tail_bars = tail_data
            elif isinstance(tail_data, dict) and "bars" in tail_data:
                tail_bars = tail_data["bars"]
            else:
                tail_bars = []
            
            last3 = tail_bars[-3:] if tail_bars else []
            tail_info = {
                "count": len(tail_bars),
                "raw_bytes": len(tail_raw),
                "ttl": r.ttl(tail_key),
                "last_bars": []
            }
            for b in last3:
                ot = b.get("open_time_ms", b.get("open_ms"))
                o = b.get("open", b.get("o"))
                h = b.get("high", b.get("h"))
                l = b.get("low", b.get("l"))
                c = b.get("close", b.get("c"))
                v = b.get("volume", b.get("v"))
                tail_info["last_bars"].append({
                    "t": ts_fmt(ot),
                    "O": o, "H": h, "L": l, "C": c, "V": v,
                    "FLAT": (o == h == l == c) if None not in (o,h,l,c) else None
                })
        except Exception as e:
            tail_info = {"error": str(e), "raw_bytes": len(tail_raw)}
    
    # updates bus
    upd_key = f"{REDIS_NS}:updates:{sym_key}:{TF_S}"
    upd_raw = r.get(upd_key)
    upd_info = None
    if upd_raw:
        try:
            upd = json.loads(upd_raw)
            events = upd.get("events", []) if isinstance(upd, dict) else upd
            upd_info = {
                "count": len(events),
                "raw_bytes": len(upd_raw),
                "last_events": []
            }
            for ev in events[-3:]:
                bar = ev.get("bar", {})
                ot = bar.get("open_time_ms", bar.get("open_ms"))
                o = bar.get("open", bar.get("o"))
                h = bar.get("high", bar.get("h"))
                l = bar.get("low", bar.get("l"))
                c = bar.get("close", bar.get("c"))
                v = bar.get("volume", bar.get("v"))
                upd_info["last_events"].append({
                    "t": ts_fmt(ot),
                    "seq": ev.get("seq"),
                    "complete": ev.get("complete"),
                    "src": ev.get("source", ev.get("src")),
                    "O": o, "C": c, "V": v,
                    "FLAT": (o == h == l == c) if None not in (o,h,l,c) else None
                })
        except Exception as e:
            upd_info = {"error": str(e)}
    
    return {"snap": snap_info, "tail": tail_info, "updates": upd_info}

def check_http_api(symbol):
    """HTTP /api/bars через urllib."""
    import urllib.request
    try:
        url = f"http://localhost:8089/api/bars?symbol={symbol.replace('/', '%2F')}&tf_s={TF_S}&limit=3"
        with urllib.request.urlopen(url, timeout=5) as resp:
            d = json.loads(resp.read())
        bars = d.get("bars", [])
        meta = d.get("meta", {})
        warnings = d.get("warnings", [])
        result = {"count": len(bars), "meta": meta, "warnings": warnings, "last_bars": []}
        for b in bars[-3:]:
            ot = b.get("open_time_ms", b.get("open_ms"))
            o = b.get("open", b.get("o"))
            h = b.get("high", b.get("h"))
            l = b.get("low", b.get("l"))
            c = b.get("close", b.get("c"))
            v = b.get("volume", b.get("v"))
            result["last_bars"].append({
                "t": ts_fmt(ot),
                "O": o, "H": h, "L": l, "C": c, "V": v,
                "FLAT": (o == h == l == c) if None not in (o,h,l,c) else None,
                "complete": b.get("complete"),
                "src": b.get("src", b.get("source"))
            })
        return result
    except Exception as e:
        return {"error": str(e)}


def main():
    # Символи з config
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        symbols = cfg.get("symbols", [])
    except:
        symbols = []
    
    if not symbols:
        # fallback: scan data_v3
        symbols = []
        for d in os.listdir(DATA_ROOT):
            if os.path.isdir(os.path.join(DATA_ROOT, d)):
                symbols.append(d.replace("_", "/"))
    
    r = redis.Redis(host="localhost", port=6379, db=REDIS_DB, decode_responses=True)
    
    print("=" * 80)
    print("D1 AUDIT -- %d symbols -- %s" % (len(symbols), datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')))
    print("=" * 80)
    
    flat_issues = []
    missing_data = []
    
    for sym in sorted(symbols):
        print("\n" + "-" * 60)
        print("  %s" % sym)
        print("-" * 60)
        
        try:
            # Disk
            disk = check_disk(sym)
            if not disk.get("disk_exists"):
                print(f"  DISK: NOT FOUND")
                missing_data.append((sym, "no disk"))
            else:
                print(f"  DISK: {disk['total_bars']} bars in {disk['files']} files")
                for b in disk.get("last_bars", [])[-3:]:
                    flag = " !! FLAT" if b.get("FLAT") else ""
                    print(f"    {b['t']}  O={b['O']} C={b['C']} V={b['V']} complete={b['complete']} src={b['src']}{flag}")
                    if b.get("FLAT"):
                        flat_issues.append((sym, "disk", b))
            
            # Redis
            rd = check_redis(r, sym)
            
            # Snap
            if rd["snap"]:
                s = rd["snap"]
                flag = " !! FLAT" if s.get("FLAT") else ""
                print(f"  REDIS SNAP: {s.get('t')} O={s.get('O')} C={s.get('C')} V={s.get('V')} TTL={s.get('ttl')}{flag}")
                if s.get("FLAT"):
                    flat_issues.append((sym, "redis_snap", s))
            else:
                print(f"  REDIS SNAP: NONE")
                missing_data.append((sym, "no redis snap"))
            
            # Tail
            if rd["tail"]:
                t = rd["tail"]
                print(f"  REDIS TAIL: {t.get('count')} bars, {t.get('raw_bytes')} bytes, TTL={t.get('ttl')}")
                for b in t.get("last_bars", [])[-2:]:
                    flag = " !! FLAT" if b.get("FLAT") else ""
                    print(f"    {b['t']}  O={b['O']} C={b['C']} V={b['V']}{flag}")
            else:
                print(f"  REDIS TAIL: NONE")
                missing_data.append((sym, "no redis tail"))
            
            # Updates
            if rd["updates"]:
                u = rd["updates"]
                print(f"  REDIS UPDATES: {u.get('count')} events, {u.get('raw_bytes')} bytes")
                for ev in u.get("last_events", []):
                    flag = " !! FLAT" if ev.get("FLAT") else ""
                    print(f"    seq={ev.get('seq')} {ev['t']} O={ev.get('O')} C={ev.get('C')} V={ev.get('V')} complete={ev.get('complete')} src={ev.get('src')}{flag}")
            else:
                print(f"  REDIS UPDATES: NONE")
            
            # HTTP API
            http = check_http_api(sym)
            if "error" in http:
                print(f"  HTTP API: ERROR - {http['error']}")
            else:
                print(f"  HTTP API: {http['count']} bars, warnings={http.get('warnings', [])}")
                for b in http.get("last_bars", [])[-2:]:
                    flag = " !! FLAT" if b.get("FLAT") else ""
                    print(f"    {b['t']}  O={b['O']} C={b['C']} V={b['V']} complete={b['complete']} src={b['src']}{flag}")
        except Exception as e:
            print(f"  ERROR processing {sym}: {e}")
            import traceback; traceback.print_exc()
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    if flat_issues:
        print("\n!! FLAT BARS (O==H==L==C) found in %d locations:" % len(flat_issues))
        for sym, where, info in flat_issues:
            print("  %s [%s] t=%s V=%s" % (sym, where, info.get('t'), info.get('V')))
    else:
        print("\nOK: No flat bars found")
    
    if missing_data:
        print("\n!! MISSING DATA:")
        for sym, what in missing_data:
            print("  %s: %s" % (sym, what))

if __name__ == "__main__":
    main()
