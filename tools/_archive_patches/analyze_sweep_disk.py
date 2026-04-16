#!/usr/bin/env python3
"""Reconstruct the XAU/USD sweep from JSONL data on disk."""

import json
import os
import glob
from datetime import datetime, timezone

DATA_DIR = "/opt/smc-v3/data_v3/XAU_USD"


def ts(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")


def read_all_recent(tf_dir, n=200):
    files = sorted(glob.glob(os.path.join(tf_dir, "*.jsonl")))
    all_bars = []
    for f in files[-3:]:
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    all_bars.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    seen = set()
    deduped = []
    for b in all_bars:
        key = b.get("open_time_ms", 0)
        if key not in seen:
            seen.add(key)
            deduped.append(b)
    deduped.sort(key=lambda b: b.get("open_time_ms", 0))
    return deduped[-n:]


def main():
    h1_bars = read_all_recent(os.path.join(DATA_DIR, "tf_3600"), 100)
    if not h1_bars:
        print("No H1 bars")
        return

    recent = h1_bars[-48:]
    min_bar = min(recent, key=lambda b: b.get("low", 99999))
    max_bar = max(recent, key=lambda b: b.get("h", 0))
    low_price = min_bar["low"]
    high_price = max_bar["h"]

    print("=" * 60)
    print("XAU/USD Sweep-to-Reversal Reconstruction")
    print("=" * 60)
    print(f"  Sweep Low:  {low_price:.2f} @ {ts(min_bar['open_time_ms'])}")
    print(f"  Pump High:  {high_price:.2f} @ {ts(max_bar['open_time_ms'])}")
    print(f"  Move Size:  {high_price - low_price:.2f} pips")

    print(f"\n=== H1 bars near sweep (low < {low_price + 60:.0f}) ===")
    for b in recent:
        if b["low"] < low_price + 60:
            body = b["c"] - b["o"]
            d = "BULL" if body > 0 else "BEAR"
            wick = min(b["o"], b["c"]) - b["low"]
            print(
                f"  {ts(b['open_time_ms'])}  O={b['o']:.2f} H={b['h']:.2f} L={b['low']:.2f} C={b['c']:.2f}  {d:4s}  body={abs(body):.1f}  lo_wick={wick:.1f}"
            )

    low_idx = None
    for i, b in enumerate(recent):
        if b["open_time_ms"] == min_bar["open_time_ms"]:
            low_idx = i
            break
    if low_idx is not None:
        print(f"\n=== Reversal from sweep (H1, 12 bars after low) ===")
        for b in recent[low_idx : low_idx + 12]:
            body = b["c"] - b["o"]
            d = "BULL" if body > 0 else "BEAR"
            gain = b["c"] - low_price
            print(
                f"  {ts(b['open_time_ms'])}  C={b['c']:.2f}  {d:4s}  body={abs(body):.1f}  from_low=+{gain:.1f}"
            )

    m15_bars = read_all_recent(os.path.join(DATA_DIR, "tf_900"), 400)
    if m15_bars:
        print(f"\n=== M15 bars near sweep (low < {low_price + 30:.0f}) ===")
        for b in m15_bars:
            if b["low"] < low_price + 30:
                body = b["c"] - b["o"]
                d = "BULL" if body > 0 else "BEAR"
                print(
                    f"  {ts(b['open_time_ms'])}  O={b['o']:.2f} H={b['h']:.2f} L={b['low']:.2f} C={b['c']:.2f}  {d}"
                )

    d1_bars = read_all_recent(os.path.join(DATA_DIR, "tf_86400"), 10)
    if d1_bars:
        print(f"\n=== D1 last 5 (macro) ===")
        for b in d1_bars[-5:]:
            body = b["c"] - b["o"]
            d = "BULL" if body > 0 else "BEAR"
            rng = b["h"] - b["low"]
            print(
                f"  {ts(b['open_time_ms'])}  O={b['o']:.2f} H={b['h']:.2f} L={b['low']:.2f} C={b['c']:.2f}  {d:4s}  range={rng:.1f}"
            )


if __name__ == "__main__":
    main()
