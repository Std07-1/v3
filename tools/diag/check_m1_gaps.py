"""Quick diagnostic: find gaps and anomalies in M1 JSONL for XAU/USD today."""
import json
import os
import glob
from datetime import datetime, timezone

def main():
    m1_file = "data_v3/XAU_USD/tf_60/part-20260323.jsonl"
    if not os.path.exists(m1_file):
        print(f"File not found: {m1_file}")
        return

    bars = []
    with open(m1_file, "r") as f:
        for line in f:
            bar = json.loads(line)
            bars.append(bar)

    print(f"Total M1 bars today: {len(bars)}")
    first_ts = datetime.fromtimestamp(bars[0]["open_time_ms"] / 1000, tz=timezone.utc)
    last_ts = datetime.fromtimestamp(bars[-1]["open_time_ms"] / 1000, tz=timezone.utc)
    print(f"First bar: {first_ts.strftime('%H:%M')}")
    print(f"Last bar:  {last_ts.strftime('%H:%M')}")

    # Find gaps > 1 minute
    gaps = []
    for i in range(1, len(bars)):
        prev_ot = bars[i - 1]["open_time_ms"]
        curr_ot = bars[i]["open_time_ms"]
        diff_min = (curr_ot - prev_ot) / 60000
        if diff_min > 1.5:
            prev_t = datetime.fromtimestamp(prev_ot / 1000, tz=timezone.utc).strftime("%H:%M")
            curr_t = datetime.fromtimestamp(curr_ot / 1000, tz=timezone.utc).strftime("%H:%M")
            gaps.append((prev_t, curr_t, diff_min, i))

    if gaps:
        print(f"\n=== M1 GAPS ({len(gaps)}) ===")
        for prev_t, curr_t, diff, idx in gaps:
            print(f"  Gap: {prev_t} -> {curr_t} ({diff:.0f} min, ~{int(diff)} bars missing)")
    else:
        print("\nNo gaps in M1 today")

    # Bars with volume=0
    zero_vol = [(i, b) for i, b in enumerate(bars) if b.get("v", 0) == 0]
    if zero_vol:
        print(f"\nBars with volume=0: {len(zero_vol)}")
        for idx, bar in zero_vol[:5]:
            ts = datetime.fromtimestamp(bar["open_time_ms"] / 1000, tz=timezone.utc).strftime("%H:%M")
            print(f"  i={idx} {ts} O={bar['o']:.2f} H={bar['h']:.2f} C={bar['c']:.2f}")
    else:
        print("\nNo zero-volume bars")

    # Check all derived TFs too
    for tf_s, tf_name in [(180, "M3"), (300, "M5"), (900, "M15"), (1800, "M30"), (3600, "H1"), (14400, "H4")]:
        sym_dir = f"data_v3/XAU_USD/tf_{tf_s}"
        if not os.path.isdir(sym_dir):
            print(f"\n{tf_name}: directory not found")
            continue
        files = sorted(glob.glob(os.path.join(sym_dir, "*.jsonl")))
        if not files:
            print(f"\n{tf_name}: no files")
            continue
        last_f = files[-1]
        d_bars = []
        with open(last_f, "r") as f:
            for line in f:
                d_bars.append(json.loads(line))
        # Find gaps
        d_gaps = []
        for i in range(1, len(d_bars)):
            prev_ot = d_bars[i - 1]["open_time_ms"]
            curr_ot = d_bars[i]["open_time_ms"]
            expected_diff = tf_s * 1000
            if curr_ot - prev_ot > expected_diff * 1.5:
                prev_t = datetime.fromtimestamp(prev_ot / 1000, tz=timezone.utc).strftime("%H:%M")
                curr_t = datetime.fromtimestamp(curr_ot / 1000, tz=timezone.utc).strftime("%H:%M")
                gap_count = (curr_ot - prev_ot) // expected_diff - 1
                d_gaps.append((prev_t, curr_t, gap_count))
        # Last 3 bars
        print(f"\n{tf_name} (file={os.path.basename(last_f)}, bars={len(d_bars)}, gaps={len(d_gaps)})")
        if d_gaps:
            for prev_t, curr_t, gc in d_gaps[-5:]:
                print(f"  Gap: {prev_t} -> {curr_t} (~{gc} bars missing)")
        last3 = d_bars[-3:]
        for bar in last3:
            ts = datetime.fromtimestamp(bar["open_time_ms"] / 1000, tz=timezone.utc).strftime("%H:%M")
            src = bar.get("source", "?")
            comp = bar.get("complete", "?")
            print(f"  {ts} O={bar['o']:.2f} H={bar['h']:.2f} L={bar.get('l', bar.get('low', 0)):.2f} C={bar['c']:.2f} V={bar.get('v',0):.0f} src={src} comp={comp}")

if __name__ == "__main__":
    main()
