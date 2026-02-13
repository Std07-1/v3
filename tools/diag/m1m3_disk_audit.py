"""Діагностика: аудит M1/M3 даних на диску."""
import json, os, datetime

DATA_ROOT = "data_v3"
SYMS = ["XAU_USD","XAG_USD","NAS100","SPX500","GER30","EUSTX50",
        "US30","NGAS","GBP_CAD","NZD_CAD","USD_CAD","USD_JPY","HKG33"]
TFS = ["tf_60", "tf_180"]

for sd in SYMS:
    for td in TFS:
        base = os.path.join(DATA_ROOT, sd, td)
        if not os.path.isdir(base):
            print(f"{base}: NOT FOUND")
            continue
        bars = []
        for f in sorted(os.listdir(base)):
            if f.endswith(".jsonl"):
                with open(os.path.join(base, f)) as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            bars.append(json.loads(line))
        if not bars:
            print(f"{base}: EMPTY")
            continue
        ots = [b["open_time_ms"] for b in bars]
        tf_ms = bars[0]["tf_s"] * 1000
        dups = len(ots) - len(set(ots))
        srcs = set(b.get("src", "?") for b in bars)
        t0 = datetime.datetime.utcfromtimestamp(ots[0]/1000).strftime("%m-%d %H:%M")
        t1 = datetime.datetime.utcfromtimestamp(ots[-1]/1000).strftime("%m-%d %H:%M")
        print(f"{sd:12s} {td:6s}: {len(bars):5d} bars, {t0} -> {t1}, dups={dups}, srcs={srcs}")
