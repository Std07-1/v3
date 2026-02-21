"""Одноразова перевірка свіжості даних на диску (cold start audit)."""
import os, json, glob, datetime as dt

root = "data_v3"
symbols = sorted([d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))])
now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
print(f"Now UTC: {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M}")
print()

TF_NAMES = {60: "M1", 180: "M3", 300: "M5", 900: "M15", 1800: "M30", 3600: "H1", 14400: "H4", 86400: "D1"}

for sym in symbols:
    for tf_s in [60, 180, 300, 900, 1800, 3600, 14400, 86400]:
        tf_dir = os.path.join(root, sym, f"tf_{tf_s}")
        if not os.path.isdir(tf_dir):
            continue
        parts = sorted(glob.glob(os.path.join(tf_dir, "part-*.jsonl")))
        if not parts:
            continue
        last_file = parts[-1]
        try:
            with open(last_file, "r") as f:
                lines = f.readlines()
            if not lines:
                continue
            last_bar = json.loads(lines[-1])
            ot = last_bar.get("o", last_bar.get("open_time_ms", 0))
            last_dt = dt.datetime.fromtimestamp(ot / 1000, tz=dt.timezone.utc)
            age_min = (now_ms - ot) / 60000
            tf_name = TF_NAMES.get(tf_s, str(tf_s))
            n_bars = len(lines)
            print(f"{sym:12s} {tf_name:4s} last={last_dt:%m-%d %H:%M} age={int(age_min):>6d}m bars_in_part={n_bars:>5d} files={len(parts)}")
        except Exception as e:
            print(f"{sym:12s} tf_{tf_s}: ERR {e}")
    print()
