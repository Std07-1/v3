#!/usr/bin/env python3
"""Аналіз sweep-to-reversal move XAU/USD 2026-04-07/08.

Витягує дані з локального API і реконструює:
1. Session levels (Prev Lon Lo, Prev NY Lo)
2. Sweep event (ціна нижче рівнів)
3. Reversal confirmation (CHoCH/BOS)
4. Magnitude of move after sweep
"""

import json
import urllib.request
import sys
from datetime import datetime, timezone

BASE = "http://127.0.0.1:8000"


def fetch(endpoint):
    url = f"{BASE}{endpoint}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())


def ts_to_utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )


def main():
    # --- H1 bars for the big picture ---
    print("=" * 60)
    print("XAU/USD Sweep-to-Reversal Analysis")
    print("=" * 60)

    # SMC data across TFs
    for tf_label, tf_s in [("M15", 900), ("H1", 3600), ("H4", 14400)]:
        print(f"\n--- {tf_label} (tf={tf_s}) ---")
        try:
            d = fetch(f"/api/smc?symbol=XAU/USD&tf={tf_s}")
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        # Structure events
        events = d.get("structure_events", [])
        print(f"  Structure events (last 8):")
        for e in events[-8:]:
            bar_ms = e.get("bar_ms", 0)
            ts = ts_to_utc(bar_ms) if bar_ms else "?"
            print(f"    {e['kind']:6s} {e['direction']:8s} @ {e['price']:.2f}  [{ts}]")

        # Key levels
        levels = d.get("levels", [])
        print(f"  Key levels:")
        for lv in levels:
            print(f"    {lv.get('label', lv['kind']):20s} @ {lv['price']:.2f}")

        # Zones near 4600
        zones = d.get("zones", [])
        sweep_zones = [z for z in zones if z.get("low", 0) < 4650]
        if sweep_zones:
            print(f"  Zones near sweep area (<4650):")
            for z in sweep_zones:
                print(
                    f"    {z['kind']:6s} {z.get('direction','?'):6s} {z.get('low',0):.2f}-{z.get('high',0):.2f} grade={z.get('grade','?')}"
                )

        # Bias
        print(f"  trend_bias={d.get('trend_bias', '?')}")

    # --- Bars around sweep ---
    print(f"\n--- H1 Bars (sweep zone 4580-4650) ---")
    try:
        bars_data = fetch("/api/bars?symbol=XAU/USD&tf=3600&limit=200")
        bars = bars_data if isinstance(bars_data, list) else bars_data.get("bars", [])
        sweep_bars = [b for b in bars if b.get("l", b.get("low", 9999)) < 4650]
        for b in sweep_bars[-10:]:
            ot = b.get("open_time_ms", b.get("t", 0))
            ts = ts_to_utc(ot) if ot else "?"
            o = b.get("o", b.get("open", 0))
            h = b.get("h", b.get("high", 0))
            low = b.get("l", b.get("low", 0))
            c = b.get("c", b.get("close", 0))
            print(f"  {ts}  O={o:.2f} H={h:.2f} L={low:.2f} C={c:.2f}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # --- Price range ---
    print(f"\n--- Move magnitude ---")
    try:
        bars_data = fetch("/api/bars?symbol=XAU/USD&tf=3600&limit=200")
        bars = bars_data if isinstance(bars_data, list) else bars_data.get("bars", [])
        if bars:
            # Find lowest low in recent bars
            recent = bars[-48:]  # last 48 hours
            min_bar = min(recent, key=lambda b: b.get("l", b.get("low", 9999)))
            max_bar = max(recent, key=lambda b: b.get("h", b.get("high", 0)))
            min_low = min_bar.get("l", min_bar.get("low", 0))
            max_high = max_bar.get("h", max_bar.get("high", 0))
            min_ts = ts_to_utc(min_bar.get("open_time_ms", min_bar.get("t", 0)))
            max_ts = ts_to_utc(max_bar.get("open_time_ms", max_bar.get("t", 0)))
            print(f"  Low:  {min_low:.2f} @ {min_ts}")
            print(f"  High: {max_high:.2f} @ {max_ts}")
            print(
                f"  Move: {max_high - min_low:.2f} pips ({(max_high - min_low) / min_low * 100:.2f}%)"
            )
    except Exception as e:
        print(f"  ERROR: {e}")


if __name__ == "__main__":
    main()
