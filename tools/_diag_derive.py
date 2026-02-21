"""Діагностика M1/M3/M5 derive stall."""
import json, datetime as dt, os

sym_dir = "data_v3/XAU_USD"

for tf_s, tf_name in [(60, "M1"), (180, "M3"), (300, "M5"), (14400, "H4")]:
    fpath = os.path.join(sym_dir, f"tf_{tf_s}")
    if not os.path.isdir(fpath):
        print(f"{tf_name}: no dir")
        continue
    import glob
    parts = sorted(glob.glob(os.path.join(fpath, "part-*.jsonl")))
    if not parts:
        print(f"{tf_name}: no parts")
        continue
    with open(parts[-1]) as f:
        lines = f.readlines()
    print(f"\n{tf_name} ({parts[-1].split(os.sep)[-1]}): {len(lines)} bars")
    for line in lines[-5:]:
        b = json.loads(line)
        t = dt.datetime.fromtimestamp(b["open_time_ms"]/1000, tz=dt.timezone.utc)
        src = b.get("source", "?")
        compl = b.get("complete", "?")
        print(f"  open={t:%H:%M} complete={compl} source={src}")
