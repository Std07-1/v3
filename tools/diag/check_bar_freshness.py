"""Check derived bar freshness on VPS."""

import os, json, time

now = time.time()
symbol = "XAU_USD"
for tf_s in [60, 300, 900, 3600, 14400, 86400]:
    tf_dir = f"data_v3/{symbol}/tf_{tf_s}"
    if not os.path.isdir(tf_dir):
        print(f"tf={tf_s}: DIR MISSING")
        continue
    files = sorted([f for f in os.listdir(tf_dir) if f.endswith(".jsonl")])
    if not files:
        print(f"tf={tf_s}: NO FILES")
        continue
    last_file = os.path.join(tf_dir, files[-1])
    mtime = os.path.getmtime(last_file)
    age_h = (now - mtime) / 3600
    with open(last_file, "rb") as fh:
        fh.seek(max(0, os.path.getsize(last_file) - 500))
        lines = fh.read().decode().strip().split("\n")
        last_line = json.loads(lines[-1])
        bar_ts = last_line.get("open_time_ms", 0) / 1000
        bar_age_h = (now - bar_ts) / 3600
    print(
        f"tf={tf_s:>5}: file_age={age_h:.1f}h  bar_age={bar_age_h:.1f}h  last_file={files[-1]}"
    )
