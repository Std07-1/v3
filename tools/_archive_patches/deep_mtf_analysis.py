#!/usr/bin/env python3
"""Deep multi-TF SMC analysis of XAU/USD.

Not just "what happened" but WHY it happened and WHAT'S NEXT.
Reconstructs the full institutional narrative across D1→H4→H1→M15.
"""

import json
import os
import glob
from datetime import datetime, timezone

DATA_DIR = "/opt/smc-v3/data_v3/XAU_USD"


def ts(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")


def ts_day(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d (%a)")


def read_all(tf_dir, n=500):
    files = sorted(glob.glob(os.path.join(tf_dir, "*.jsonl")))
    all_bars = []
    for f in files[-5:]:
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


def bar_type(b):
    body = b["c"] - b["o"]
    rng = b["h"] - b["low"]
    if rng == 0:
        return "DOJI"
    body_pct = abs(body) / rng * 100
    direction = "BULL" if body > 0 else "BEAR"
    if body_pct > 70:
        return f"{direction}_MOMENTUM"
    elif body_pct > 40:
        return f"{direction}"
    else:
        if body > 0:
            return "BULL_INDECISION"
        else:
            return "BEAR_INDECISION"


def main():
    # ============================================================
    # D1 — MACRO CONTEXT (last 20 days)
    # ============================================================
    d1 = read_all(os.path.join(DATA_DIR, "tf_86400"), 30)
    print("=" * 70)
    print("LAYER 1: D1 — MACRO NARRATIVE (Where are we in the big picture?)")
    print("=" * 70)

    if d1:
        # Find swing structure
        print("\nD1 bars (last 15):")
        for b in d1[-15:]:
            body = b["c"] - b["o"]
            rng = b["h"] - b["low"]
            bt = bar_type(b)
            print(
                f"  {ts_day(b['open_time_ms'])}  O={b['o']:.2f} H={b['h']:.2f} L={b['low']:.2f} C={b['c']:.2f}  {bt:20s}  range={rng:.1f}  body={abs(body):.1f}"
            )

        # Key D1 levels
        last_20 = d1[-20:]
        overall_high = max(b["h"] for b in last_20)
        overall_low = min(b["low"] for b in last_20)
        eq = (overall_high + overall_low) / 2
        print(f"\n  D1 Range (20d): {overall_low:.2f} — {overall_high:.2f}")
        print(f"  D1 EQ (50%):    {eq:.2f}")
        print(f"  Premium zone:   > {eq:.2f}")
        print(f"  Discount zone:  < {eq:.2f}")

        # Recent D1 structure
        last_5 = d1[-5:]
        if last_5:
            recent_low = min(b["low"] for b in last_5)
            recent_high = max(b["h"] for b in last_5)
            last_close = last_5[-1]["c"]
            pct_of_range = (
                (last_close - overall_low) / (overall_high - overall_low) * 100
                if overall_high > overall_low
                else 50
            )
            print(
                f"  Last close:     {last_close:.2f} ({pct_of_range:.0f}% of D1 range)"
            )
            print(f"  5d low:         {recent_low:.2f}")
            print(f"  5d high:        {recent_high:.2f}")

    # ============================================================
    # H4 — STRUCTURAL CONTEXT
    # ============================================================
    h4 = read_all(os.path.join(DATA_DIR, "tf_14400"), 80)
    print("\n" + "=" * 70)
    print("LAYER 2: H4 — STRUCTURAL NARRATIVE")
    print("=" * 70)

    if h4:
        # Last 20 H4 bars = ~3.3 days
        recent_h4 = h4[-30:]
        print(f"\nH4 bars (last 20):")
        for b in recent_h4[-20:]:
            bt = bar_type(b)
            body = b["c"] - b["o"]
            rng = b["h"] - b["low"]
            lo_wick = min(b["o"], b["c"]) - b["low"]
            hi_wick = b["h"] - max(b["o"], b["c"])
            print(
                f"  {ts(b['open_time_ms'])}  O={b['o']:.2f} H={b['h']:.2f} L={b['low']:.2f} C={b['c']:.2f}  {bt:20s}  lo_w={lo_wick:.1f}  hi_w={hi_wick:.1f}"
            )

        # Identify swing lows and highs on H4
        print(f"\n  H4 Swing Analysis:")
        swing_lows = []
        swing_highs = []
        for i in range(2, len(recent_h4) - 2):
            b = recent_h4[i]
            if (
                b["low"] < recent_h4[i - 1]["low"]
                and b["low"] < recent_h4[i - 2]["low"]
                and b["low"] < recent_h4[i + 1]["low"]
                and b["low"] < recent_h4[i + 2]["low"]
            ):
                swing_lows.append((b["open_time_ms"], b["low"]))
            if (
                b["h"] > recent_h4[i - 1]["h"]
                and b["h"] > recent_h4[i - 2]["h"]
                and b["h"] > recent_h4[i + 1]["h"]
                and b["h"] > recent_h4[i + 2]["h"]
            ):
                swing_highs.append((b["open_time_ms"], b["h"]))

        for t, p in swing_lows[-5:]:
            print(f"    Swing Low:  {p:.2f} @ {ts(t)}")
        for t, p in swing_highs[-5:]:
            print(f"    Swing High: {p:.2f} @ {ts(t)}")

    # ============================================================
    # H1 — EXECUTION CONTEXT
    # ============================================================
    h1 = read_all(os.path.join(DATA_DIR, "tf_3600"), 200)
    print("\n" + "=" * 70)
    print("LAYER 3: H1 — EXECUTION NARRATIVE")
    print("=" * 70)

    if h1:
        # Find the sweep and reversal zone
        recent_h1 = h1[-72:]  # last 3 days

        # Find the low of the move
        min_bar_h1 = min(recent_h1, key=lambda b: b["low"])
        sweep_low = min_bar_h1["low"]
        sweep_ts = min_bar_h1["open_time_ms"]

        # Find the high after sweep
        after_sweep = [b for b in recent_h1 if b["open_time_ms"] >= sweep_ts]
        if after_sweep:
            max_after = max(after_sweep, key=lambda b: b["h"])
            pump_high = max_after["h"]
            pump_ts = max_after["open_time_ms"]
        else:
            pump_high = 0
            pump_ts = 0

        print(f"\n  SWEEP: {sweep_low:.2f} @ {ts(sweep_ts)}")
        print(f"  PUMP:  {pump_high:.2f} @ {ts(pump_ts)}")
        print(f"  MOVE:  +{pump_high - sweep_low:.2f} pips")

        # Classify the move phases
        print(f"\n  Phase Analysis:")
        phase = "pre_sweep"
        for b in recent_h1:
            ot = b["open_time_ms"]
            body = b["c"] - b["o"]
            bt = bar_type(b)
            lo_wick = min(b["o"], b["c"]) - b["low"]
            rng = b["h"] - b["low"]

            if b["low"] <= sweep_low + 5 and phase == "pre_sweep":
                phase = "sweep"
                print(f"  >>> SWEEP PHASE <<<")
            elif (
                phase == "sweep" and body > 0 and abs(body) / rng > 0.5
                if rng > 0
                else False
            ):
                phase = "reversal"
                print(f"  >>> REVERSAL PHASE <<<")
            elif phase == "reversal" and b["c"] > sweep_low + 100:
                phase = "markup"
                print(f"  >>> MARKUP PHASE <<<")

            # Only print key bars
            if b["low"] < sweep_low + 70 or b["h"] > pump_high - 50 or rng > 40:
                print(
                    f"    {ts(ot)}  O={b['o']:.2f} H={b['h']:.2f} L={b['low']:.2f} C={b['c']:.2f}  {bt:20s}  lo_wick={lo_wick:.1f}"
                )

    # ============================================================
    # M15 — PRECISION (around sweep area)
    # ============================================================
    m15 = read_all(os.path.join(DATA_DIR, "tf_900"), 500)
    print("\n" + "=" * 70)
    print("LAYER 4: M15 — PRECISION (sweep zone)")
    print("=" * 70)

    if m15 and h1:
        # M15 bars around the sweep (sweep_low ± 50)
        sweep_zone = [
            b
            for b in m15
            if b["low"] < sweep_low + 50
            or (
                b["open_time_ms"] >= sweep_ts - 3600000
                and b["open_time_ms"] <= sweep_ts + 7200000
            )
        ]
        print(f"\n  M15 bars in sweep zone ({ts(sweep_ts)} ± 2h):")
        sweep_m15 = [
            b
            for b in m15
            if b["open_time_ms"] >= sweep_ts - 3600000
            and b["open_time_ms"] <= sweep_ts + 14400000
        ]
        for b in sweep_m15:
            body = b["c"] - b["o"]
            bt = bar_type(b)
            print(
                f"    {ts(b['open_time_ms'])}  O={b['o']:.2f} H={b['h']:.2f} L={b['low']:.2f} C={b['c']:.2f}  {bt}"
            )

        # Find M15 CHoCH / BOS after sweep
        print(f"\n  M15 Structure After Sweep:")
        post_sweep = [b for b in m15 if b["open_time_ms"] > sweep_ts]
        if len(post_sweep) > 5:
            highs = []
            lows = []
            for i, b in enumerate(post_sweep[:40]):
                if i >= 2 and i < len(post_sweep) - 2:
                    if (
                        b["h"] >= post_sweep[i - 1]["h"]
                        and b["h"] >= post_sweep[i + 1]["h"]
                    ):
                        highs.append((b["open_time_ms"], b["h"]))
                    if (
                        b["low"] <= post_sweep[i - 1]["low"]
                        and b["low"] <= post_sweep[i + 1]["low"]
                    ):
                        lows.append((b["open_time_ms"], b["low"]))

            print(f"    M15 Swing Highs after sweep:")
            for t, p in highs[:6]:
                print(f"      {ts(t)}: {p:.2f}")
            print(f"    M15 Swing Lows after sweep:")
            for t, p in lows[:6]:
                print(f"      {ts(t)}: {p:.2f}")

    # ============================================================
    # CURRENT STATE — Where are we NOW?
    # ============================================================
    print("\n" + "=" * 70)
    print("LAYER 5: CURRENT STATE — What happens next?")
    print("=" * 70)

    if h1 and d1:
        last_h1 = h1[-1]
        last_d1 = d1[-1]
        current_price = last_h1["c"]

        # D1 range context
        d1_20_high = max(b["h"] for b in d1[-20:])
        d1_20_low = min(b["low"] for b in d1[-20:])
        d1_eq = (d1_20_high + d1_20_low) / 2
        pct = (current_price - d1_20_low) / (d1_20_high - d1_20_low) * 100

        print(f"\n  Current price: {current_price:.2f}")
        print(f"  D1 Range:      {d1_20_low:.2f} — {d1_20_high:.2f}")
        print(f"  D1 EQ:         {d1_eq:.2f}")
        print(f"  Position:      {pct:.0f}% of range")

        if pct > 70:
            print(f"  Zone:          PREMIUM (>70%)")
        elif pct > 50:
            print(f"  Zone:          ABOVE EQ")
        elif pct > 30:
            print(f"  Zone:          BELOW EQ")
        else:
            print(f"  Zone:          DISCOUNT (<30%)")

        # H4 last 5 bars momentum
        if h4:
            last_5_h4 = h4[-5:]
            bull_count = sum(1 for b in last_5_h4 if b["c"] > b["o"])
            avg_body = sum(abs(b["c"] - b["o"]) for b in last_5_h4) / len(last_5_h4)
            print(
                f"\n  H4 Momentum (last 5): {bull_count}/5 bullish, avg body={avg_body:.1f}"
            )

        # Distance from sweep
        if h1:
            print(f"  Distance from sweep low: +{current_price - sweep_low:.2f}")
            print(f"  Distance to D1 high:     {d1_20_high - current_price:.2f}")

        # Recent OB/FVG zones from API
        print(f"\n  Key levels to watch:")
        print(f"    Sweep low (support):     {sweep_low:.2f}")
        print(f"    D1 EQ:                   {d1_eq:.2f}")
        print(f"    D1 20d high:             {d1_20_high:.2f}")

        # Nearest resistance analysis
        print(f"\n  Supply zones (potential targets/resistance):")
        for b in d1[-10:]:
            if b["h"] > current_price and bar_type(b).startswith("BEAR"):
                print(
                    f"    D1 bear bar high: {b['h']:.2f} @ {ts_day(b['open_time_ms'])}"
                )


if __name__ == "__main__":
    main()
