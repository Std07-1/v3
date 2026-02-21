"""Діагностика: симуляція DeriveEngine cascade для пошуку причини M5 stall.

Читає M1 бари з диску, симулює derive_triggers і derive_bar,
перевіряє чому M3 деривується а M5 — ні.
"""
import json
import datetime as dt
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.derive import (
    DERIVE_CHAIN,
    DERIVE_SOURCE,
    GenericBuffer,
    derive_triggers,
    derive_bar,
)
from core.model.bars import CandleBar
from core.buckets import bucket_start_ms


def load_m1_bars(symbol: str, count: int = 500) -> list:
    """Завантажує останні count M1 барів з диску."""
    canon = symbol.replace("/", "_")
    pattern = f"data_v3/{canon}/tf_60/part-*.jsonl"
    parts = sorted(glob.glob(pattern))
    bars = []
    for p in reversed(parts):
        with open(p) as f:
            lines = f.readlines()
        for line in reversed(lines):
            d = json.loads(line)
            bars.append(d)
            if len(bars) >= count:
                break
        if len(bars) >= count:
            break
    bars.reverse()  # oldest first
    return bars


def dict_to_candle(d: dict) -> CandleBar:
    return CandleBar(
        symbol=d["symbol"],
        tf_s=d["tf_s"],
        open_time_ms=d["open_time_ms"],
        close_time_ms=d["close_time_ms"],
        o=d["o"],
        h=d["h"],
        low=d.get("low", d.get("l", 0)),
        c=d["c"],
        v=d.get("v", 0),
        complete=d.get("complete", True),
        src=d.get("src", d.get("source", "history")),
        extensions=d.get("extensions", {}),
    )


def main():
    symbol = "XAU/USD"
    m1_dicts = load_m1_bars(symbol, 500)
    print(f"Loaded {len(m1_dicts)} M1 bars for {symbol}")
    if not m1_dicts:
        return

    first_t = dt.datetime.utcfromtimestamp(m1_dicts[0]["open_time_ms"] / 1000)
    last_t = dt.datetime.utcfromtimestamp(m1_dicts[-1]["open_time_ms"] / 1000)
    print(f"  range: {first_t:%H:%M} - {last_t:%H:%M} UTC")

    # Create M1 buffer
    buf_m1 = GenericBuffer(60, max_keep=2000)
    m3_derived = 0
    m5_derived = 0
    m5_trigger_count = 0
    m5_derive_fail_count = 0
    m5_last_derived_open = None
    m3_last_derived_open = None

    for d in m1_dicts:
        bar = dict_to_candle(d)
        if not bar.complete:
            continue
        buf_m1.upsert(bar)

        # Check triggers
        triggers = derive_triggers(bar, anchor_offset_s=0)
        for target_tf_s, bucket_open_ms in triggers:
            source_info = DERIVE_SOURCE.get(target_tf_s)
            if source_info is None:
                continue

            derived = derive_bar(
                symbol=symbol,
                target_tf_s=target_tf_s,
                source_buffer=buf_m1,
                bucket_open_ms=bucket_open_ms,
                anchor_offset_s=0,
                is_trading_fn=None,
                filter_calendar_pause=True,
            )

            if target_tf_s == 180:
                if derived:
                    m3_derived += 1
                    m3_last_derived_open = derived.open_time_ms

            if target_tf_s == 300:
                m5_trigger_count += 1
                t_trigger = dt.datetime.utcfromtimestamp(bar.open_time_ms / 1000)
                t_bucket = dt.datetime.utcfromtimestamp(bucket_open_ms / 1000)
                if derived:
                    m5_derived += 1
                    m5_last_derived_open = derived.open_time_ms
                else:
                    m5_derive_fail_count += 1
                    # Diagnose why
                    target_tf_ms = 300 * 1000
                    bucket_end = bucket_open_ms + target_tf_ms
                    missing = buf_m1.missing_count(bucket_open_ms, bucket_end)
                    buf_size = len(buf_m1)
                    print(
                        f"  M5 FAIL: trigger at M1={t_trigger:%H:%M}, "
                        f"bucket={t_bucket:%H:%M}, missing={missing}, "
                        f"buf_size={buf_size}"
                    )
                    # Show which bars are present/absent
                    for slot_ms in range(bucket_open_ms, bucket_end, 60000):
                        present = slot_ms in buf_m1
                        slot_t = dt.datetime.utcfromtimestamp(slot_ms / 1000)
                        if not present:
                            print(f"    MISSING: {slot_t:%H:%M} ({slot_ms})")

    print(f"\nResults (no calendar):")
    print(f"  M3: {m3_derived} derived", end="")
    if m3_last_derived_open:
        t = dt.datetime.utcfromtimestamp(m3_last_derived_open / 1000)
        print(f", last={t:%H:%M}", end="")
    print()
    print(f"  M5: {m5_derived} derived, {m5_trigger_count} triggers, {m5_derive_fail_count} fails", end="")
    if m5_last_derived_open:
        t = dt.datetime.utcfromtimestamp(m5_last_derived_open / 1000)
        print(f", last={t:%H:%M}", end="")
    print()

    # --- Now test WITH calendar for XAU_USD ---
    print("\n--- Re-run WITH calendar ---")
    try:
        from core.config_loader import load_config
        from runtime.store.uds import MarketCalendar
        cfg = load_config()
        cal_groups = cfg.get("market_calendar_by_group", {})
        sym_groups = cfg.get("market_calendar_symbol_groups", {})
        group = sym_groups.get(symbol.replace("/", "_"), sym_groups.get(symbol))
        if group:
            cal_cfg = cal_groups.get(group, {})
            cal = MarketCalendar(cal_cfg)
            is_trading_fn = cal.is_trading_minute
            print(f"  calendar group: {group}, cfg keys: {list(cal_cfg.keys())}")
        else:
            is_trading_fn = None
            print(f"  no calendar for {symbol}")
    except Exception as e:
        print(f"  calendar load error: {e}")
        is_trading_fn = None

    buf_m1_cal = GenericBuffer(60, max_keep=2000)
    m3_cal = 0
    m5_cal = 0
    m5_cal_triggers = 0
    m5_cal_fails = 0

    for d in m1_dicts:
        bar = dict_to_candle(d)
        if not bar.complete:
            continue
        buf_m1_cal.upsert(bar)

        triggers = derive_triggers(bar, anchor_offset_s=0)
        for target_tf_s, bucket_open_ms in triggers:
            source_info = DERIVE_SOURCE.get(target_tf_s)
            if source_info is None:
                continue

            derived = derive_bar(
                symbol=symbol,
                target_tf_s=target_tf_s,
                source_buffer=buf_m1_cal,
                bucket_open_ms=bucket_open_ms,
                anchor_offset_s=0,
                is_trading_fn=is_trading_fn,
                filter_calendar_pause=True,
            )

            if target_tf_s == 180:
                if derived:
                    m3_cal += 1

            if target_tf_s == 300:
                m5_cal_triggers += 1
                t_trigger = dt.datetime.utcfromtimestamp(bar.open_time_ms / 1000)
                t_bucket = dt.datetime.utcfromtimestamp(bucket_open_ms / 1000)
                if derived:
                    m5_cal += 1
                else:
                    m5_cal_fails += 1
                    target_tf_ms = 300 * 1000
                    bucket_end = bucket_open_ms + target_tf_ms
                    missing = buf_m1_cal.missing_count(
                        bucket_open_ms, bucket_end, is_trading_fn=is_trading_fn
                    )
                    print(
                        f"  M5+CAL FAIL: trigger at M1={t_trigger:%H:%M}, "
                        f"bucket={t_bucket:%H:%M}, missing={missing}"
                    )
                    for slot_ms in range(bucket_open_ms, bucket_end, 60000):
                        is_trading = is_trading_fn(slot_ms) if is_trading_fn else True
                        present = slot_ms in buf_m1_cal
                        slot_t = dt.datetime.utcfromtimestamp(slot_ms / 1000)
                        if is_trading and not present:
                            print(f"    TRADING but MISSING: {slot_t:%H:%M}")

    print(f"\nResults (with calendar):")
    print(f"  M3: {m3_cal} derived")
    print(f"  M5: {m5_cal} derived, {m5_cal_triggers} triggers, {m5_cal_fails} fails")


if __name__ == "__main__":
    main()
