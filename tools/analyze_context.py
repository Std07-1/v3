#!/usr/bin/env python3
"""Quick analysis of the sweep-to-reversal XAU/USD move via /api/context."""

import json
import urllib.request
from datetime import datetime, timezone

BASE = "http://127.0.0.1:8000"


def fetch(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def ts(ms):
    if not ms:
        return "?"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")


def main():
    d = fetch(f"{BASE}/api/context?symbol=XAU/USD")
    print("=== Keys ===")
    for k, v in d.items():
        if isinstance(v, list):
            print(f"  {k}: list[{len(v)}]")
        elif isinstance(v, dict):
            print(f"  {k}: dict keys={list(v.keys())[:8]}")
        else:
            print(f"  {k}: {repr(v)[:80]}")

    # SMC per TF
    smc = d.get("smc", {})
    print("\n=== SMC per TF ===")
    for tf_key, tf_data in smc.items():
        if not isinstance(tf_data, dict):
            continue
        events = tf_data.get("structure_events", [])
        bias = tf_data.get("trend_bias", "?")
        zones = tf_data.get("zones", [])
        levels = tf_data.get("levels", [])
        print(
            f"\n  TF={tf_key}  bias={bias}  events={len(events)}  zones={len(zones)}  levels={len(levels)}"
        )

        # Last 6 structure events
        for e in events[-6:]:
            bar_ms = e.get("bar_ms", 0)
            print(
                f"    {e.get('kind','?'):6s} {e.get('direction','?'):8s} @ {e.get('price',0):.2f}  [{ts(bar_ms)}]"
            )

        # Key levels
        for lv in levels[:8]:
            label = lv.get("label", lv.get("kind", "?"))
            print(f"    LVL {label:25s} @ {lv.get('price',0):.2f}")

    # Sessions
    sessions = d.get("sessions", {})
    if sessions:
        print("\n=== Sessions ===")
        for sk, sv in sessions.items():
            if isinstance(sv, dict):
                for field in ["high", "low", "sweep_high", "sweep_low"]:
                    if field in sv:
                        print(f"  {sk}.{field} = {sv[field]}")

    # Narrative
    narrative = d.get("narrative", {})
    if narrative:
        print("\n=== Narrative ===")
        for nk, nv in narrative.items():
            if isinstance(nv, dict):
                print(f"  {nk}: {json.dumps(nv, default=str)[:120]}")
            else:
                print(f"  {nk}: {repr(nv)[:100]}")

    # Bias map
    bias_map = d.get("bias_map", {})
    if bias_map:
        print("\n=== Bias Map ===")
        for bk, bv in bias_map.items():
            print(f"  {bk}: {bv}")


if __name__ == "__main__":
    main()
