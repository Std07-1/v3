"""Quick check: current M1 gap state + derived bar coverage."""

import json
from datetime import datetime, timezone


def ts_fmt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%M")


def main():
    m1_file = "data_v3/XAU_USD/tf_60/part-20260323.jsonl"
    bars = []
    with open(m1_file) as f:
        for line in f:
            bars.append(json.loads(line))

    last_ts = datetime.fromtimestamp(bars[-1]["open_time_ms"] / 1000, tz=timezone.utc)
    now_ts = datetime.now(timezone.utc)
    age_min = (now_ts - last_ts).total_seconds() / 60

    print(f"M1 bars today: {len(bars)}")
    print(f"Last bar: {ts_fmt(bars[-1]['open_time_ms'])} UTC (age: {age_min:.0f}m)")
    print(f"Now: {now_ts.strftime('%H:%M')} UTC")

    # Count all gaps
    gaps = []
    for i in range(1, len(bars)):
        diff = bars[i]["open_time_ms"] - bars[i - 1]["open_time_ms"]
        if diff > 90000:
            missing = int(diff / 60000) - 1
            gaps.append((bars[i - 1]["open_time_ms"], bars[i]["open_time_ms"], missing))

    total_missing = sum(g[2] for g in gaps)
    print(f"\nTotal M1 gaps: {len(gaps)}, total missing bars: {total_missing}")
    for g_start, g_end, missing in gaps:
        print(f"  {ts_fmt(g_start)} -> {ts_fmt(g_end)} ({missing} bars missing)")

    # Check M5 bars in gap range
    print("\n--- M5 bars in gap range (11:30 - 12:10) ---")
    m5_file = "data_v3/XAU_USD/tf_300/part-20260323.jsonl"
    with open(m5_file) as f:
        for line in f:
            bar = json.loads(line)
            ot = bar["open_time_ms"]
            ts = datetime.fromtimestamp(ot / 1000, tz=timezone.utc)
            h, m = ts.hour, ts.minute
            if (h == 11 and m >= 30) or (h == 12 and m <= 10):
                # Count how many M1 bars exist for this M5 bucket
                m5_start = ot
                m5_end = ot + 300000
                m1_in_bucket = sum(
                    1 for b in bars if m5_start <= b["open_time_ms"] < m5_end
                )
                print(
                    f"  {ts_fmt(ot)} O={bar['o']:.2f} H={bar['h']:.2f} L={bar.get('l', bar.get('low', 0)):.2f} C={bar['c']:.2f} V={bar.get('v',0):.0f} (M1 inside: {m1_in_bucket}/5)"
                )

    # Also check the gap AFTER last bar — is broker still failing?
    if age_min > 5:
        print(
            f"\n!! WARNING: Last M1 bar is {age_min:.0f}m old — broker may still be down"
        )
    else:
        print(f"\n OK: Last M1 bar is {age_min:.0f}m old — broker seems active")


if __name__ == "__main__":
    main()
