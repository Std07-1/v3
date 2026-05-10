"""
Діагностичний скрипт: перевірка narrative output з реальних даних.
Запуск: python tools/diag/test_narrative_live.py
"""

import json
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.engine import SmcEngine
from core.smc.narrative import narrative_to_wire, synthesize_narrative
from core.smc.swings import compute_atr
from runtime.store.layers.disk_layer import DiskLayer


def _dicts_to_bars(dicts, tf_s):
    """Convert disk layer dicts to CandleBar objects."""
    bars = []
    tf_ms = tf_s * 1000
    for d in dicts:
        bars.append(
            CandleBar(
                symbol=d.get("symbol", "XAU/USD"),
                tf_s=tf_s,
                open_time_ms=d["open_time_ms"],
                close_time_ms=d["open_time_ms"] + tf_ms,
                o=d["o"],
                h=d["h"],
                low=d["low"],
                c=d["c"],
                v=d.get("v", 0),
                complete=d.get("complete", True),
                src=d.get("src", "disk"),
            )
        )
    return bars


def main():
    with open("config.json") as f:
        cfg = json.load(f)

    smc_cfg = SmcConfig.from_dict(cfg.get("smc", {}))
    engine = SmcEngine(smc_cfg)

    dl = DiskLayer("data_v3")
    symbol = "XAU/USD"

    # Load bars for all compute TFs
    tf_bars = {}
    for tf_s in sorted(smc_cfg.compute_tfs):
        dicts, _ = dl.read_window_with_geom(symbol, tf_s, limit=200, use_tail=True)
        bars = _dicts_to_bars(dicts, tf_s)
        tf_bars[tf_s] = bars
        print(f"  {tf_s:>6}s: {len(bars)} bars")

    # Feed in TF order: D1 → H4 → H1 → M15 → M5
    for tf_s in sorted(tf_bars.keys(), reverse=True):
        bars = tf_bars[tf_s]
        if bars:
            engine.update(symbol, tf_s, bars)

    # Test multiple viewer TFs
    for viewer_tf in [900, 3600, 14400]:
        tf_label = {900: "M15", 3600: "H1", 14400: "H4"}.get(viewer_tf, str(viewer_tf))
        print(f"\n{'=' * 60}")
        print(f"=== Viewer: {tf_label} ===")
        print(f"{'=' * 60}")

        snap = engine.get_display_snapshot(symbol, viewer_tf)
        grades = engine.get_zone_grades(symbol, viewer_tf)

        print(
            f"zones: {len(snap.zones)}, swings: {len(snap.swings)}, "
            f"levels: {len(snap.levels)}, trend_bias: {snap.trend_bias}"
        )

        # Zones with grades
        print(f"\n--- Zones ---")
        for z in snap.zones:
            gi = grades.get(z.id, {})
            g = gi.get("grade", "?")
            s = gi.get("score", 0)
            factors = ", ".join(gi.get("factors", []))
            print(
                f"  {z.kind:20} {z.low:>8.1f}-{z.high:<8.1f} "
                f"st={z.status:12} {g:>2}({s:>2}) ctx={z.context_layer:14} "
                f"[{factors}]"
            )

        # Bias
        bias = {}
        for tf_s in sorted(smc_cfg.compute_tfs):
            b = engine.get_htf_bias(symbol, tf_s)
            if b:
                bias[str(tf_s)] = b
        print(f"\n--- Bias ---")
        for k, v in bias.items():
            tf_label2 = {
                "300": "M5",
                "900": "M15",
                "3600": "H1",
                "14400": "H4",
                "86400": "D1",
            }.get(k, k)
            print(f"  {tf_label2:>4}: {v}")

        # ATR
        base_bars = tf_bars.get(viewer_tf, tf_bars.get(900, []))
        if base_bars:
            last_price = base_bars[-1].c
            atr = compute_atr(list(base_bars), period=14)
        else:
            last_price = 0.0
            atr = 1.0
        # Also get engine ATR
        engine_atr = engine.get_atr(symbol, viewer_tf)
        print(f"\n--- Price & ATR ---")
        print(f"  current_price: {last_price:.1f}")
        print(f"  ATR14 (local bars): {atr:.1f}")
        print(f"  ATR14 (engine): {engine_atr:.1f}")

        # Narrative
        narr_cfg = cfg.get("smc", {}).get("narrative", {})
        narr = synthesize_narrative(
            snap,
            bias,
            grades,
            {},
            viewer_tf,
            last_price,
            engine_atr,
            narr_cfg,
        )
        wire = narrative_to_wire(narr)

        print(f"\n--- Narrative ---")
        print(f"  mode: {wire['mode']}")
        print(f"  sub_mode: {wire['sub_mode']}")
        print(f"  headline: {wire['headline']}")
        print(f"  bias_summary: {wire['bias_summary']}")
        print(f"  market_phase: {wire['market_phase']}")
        print(f"  next_area: {wire['next_area']}")
        print(f"  fvg_context: {wire['fvg_context']}")
        print(f"  warnings: {wire['warnings']}")

        if wire["scenarios"]:
            print(f"\n--- Scenarios ({len(wire['scenarios'])}) ---")
            for i, sc in enumerate(wire["scenarios"]):
                print(f"  [{i}] dir={sc['direction']} entry={sc['entry_desc']}")
                print(f"      trigger={sc['trigger']} ({sc['trigger_desc']})")
                print(f"      target={sc['target_desc']}")
                print(f"      invalidation={sc['invalidation']}")
        else:
            print(f"\n--- No scenarios (mode={wire['mode']}) ---")


if __name__ == "__main__":
    main()
