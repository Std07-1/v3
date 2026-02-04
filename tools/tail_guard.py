from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

from v3_polling_b import (
    anchor_offset_for_tf,
    FxcmHistoryProvider,
    floor_bucket_start_ms,
    load_config,
    load_day_open_times,
    ms_to_utc_dt,
    tail_last_bar_time_ms,
)

logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

def parse_hhmm_to_minutes(s: str) -> int:
    parts = s.strip().split(":")
    if len(parts) != 2:
        raise ValueError("Невірний формат часу, очікується HH:MM")
    h = int(parts[0])
    m = int(parts[1])
    if h < 0 or h > 23 or m < 0 or m > 59:
        raise ValueError("Невірні значення часу HH:MM")
    return h * 60 + m


def parse_iso_utc(s: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def in_window_utc(now: dt.datetime, start_hm: str, end_hm: str) -> bool:
    start_min = parse_hhmm_to_minutes(start_hm)
    end_min = parse_hhmm_to_minutes(end_hm)
    cur_min = now.hour * 60 + now.minute
    if start_min <= end_min:
        return start_min <= cur_min <= end_min
    return cur_min >= start_min or cur_min <= end_min


def seconds_until_window_start(now: dt.datetime, start_hm: str, end_hm: str) -> int:
    if in_window_utc(now, start_hm, end_hm):
        return 0
    target = snap_to_window_start(now, start_hm, end_hm)
    delta = target - now
    return max(0, int(delta.total_seconds()))


def snap_to_window_start(
    ts: dt.datetime, start_hm: str, end_hm: str
) -> dt.datetime:
    start_min = parse_hhmm_to_minutes(start_hm)
    end_min = parse_hhmm_to_minutes(end_hm)
    cur_min = ts.hour * 60 + ts.minute
    if start_min <= end_min:
        if cur_min < start_min:
            return ts.replace(hour=start_min // 60, minute=start_min % 60, second=0, microsecond=0)
        if cur_min > end_min:
            next_day = (ts + dt.timedelta(days=1)).replace(
                hour=start_min // 60,
                minute=start_min % 60,
                second=0,
                microsecond=0,
            )
            return next_day
        return ts
    # window across midnight
    if cur_min >= start_min or cur_min <= end_min:
        return ts
    return ts.replace(hour=start_min // 60, minute=start_min % 60, second=0, microsecond=0)


def has_on_disk(
    cache: Dict[str, set[int]],
    data_root: str,
    symbol: str,
    tf_s: int,
    open_time_ms: int,
) -> bool:
    day = ms_to_utc_dt(open_time_ms).strftime("%Y%m%d")
    key = f"{tf_s}:{day}"
    idx = cache.get(key)
    if idx is None:
        idx = load_day_open_times(data_root, symbol, tf_s, day)
        cache[key] = idx
    return open_time_ms in idx


def mark_on_disk(
    cache: Dict[str, set[int]],
    data_root: str,
    symbol: str,
    tf_s: int,
    open_time_ms: int,
) -> None:
    day = ms_to_utc_dt(open_time_ms).strftime("%Y%m%d")
    key = f"{tf_s}:{day}"
    idx = cache.get(key)
    if idx is None:
        idx = load_day_open_times(data_root, symbol, tf_s, day)
        cache[key] = idx
    idx.add(open_time_ms)


def compute_last_closed_open_ms(
    latest_m1_open_ms: int,
    tf_s: int,
    anchor_offset_s: int,
) -> int:
    tf_ms = tf_s * 1000
    b0 = floor_bucket_start_ms(latest_m1_open_ms, tf_s, anchor_offset_s=anchor_offset_s)
    b1 = b0 + tf_ms
    if latest_m1_open_ms == (b1 - 60_000):
        return b0
    return b0 - tf_ms


def scan_and_backfill(
    provider: FxcmHistoryProvider,
    data_root: str,
    symbol: str,
    derived_tfs_s: List[int],
    day_anchor_offset_s: int,
    lookback_bars: int,
    max_fetch_per_cycle: int,
    window_start_hm: str,
    window_end_hm: str,
    min_open_ms: Optional[int],
    cache: Dict[str, set[int]],
) -> Tuple[int, int]:
    latest_m1 = tail_last_bar_time_ms(data_root, symbol, tf_s=60)
    if latest_m1 is None:
        logging.warning("Tail-guard: немає M1 на диску для %s", symbol)
        return (0, 0)

    fetch_count = 0
    checked = 0
    for tf_s in derived_tfs_s:
        anchor_offset_s = anchor_offset_for_tf(tf_s, day_anchor_offset_s)
        last_open = compute_last_closed_open_ms(latest_m1, tf_s, anchor_offset_s)
        tf_ms = tf_s * 1000
        start_open = last_open - (max(1, lookback_bars) - 1) * tf_ms
        if min_open_ms is not None and start_open < min_open_ms:
            start_open = min_open_ms
        cursor = start_open
        while cursor <= last_open:
            cursor_dt = ms_to_utc_dt(cursor)
            if not in_window_utc(cursor_dt, window_start_hm, window_end_hm):
                cursor_dt = snap_to_window_start(cursor_dt, window_start_hm, window_end_hm)
                cursor = int(cursor_dt.timestamp() * 1000)
                if cursor > last_open:
                    break
                continue
            checked += 1
            if not has_on_disk(cache, data_root, symbol, tf_s, cursor):
                date_to = ms_to_utc_dt(cursor + tf_ms)
                bars = provider.fetch_last_n_tf(
                    symbol, tf_s=tf_s, n=1, date_to_utc=date_to
                )
                if bars:
                    b = bars[-1]
                    if b.open_time_ms == cursor:
                        path = os.path.join(
                            data_root,
                            symbol.replace("/", "_"),
                            f"tf_{tf_s}",
                        )
                        os.makedirs(path, exist_ok=True)
                        file_day = ms_to_utc_dt(b.open_time_ms).strftime("%Y%m%d")
                        out_path = os.path.join(path, f"part-{file_day}.jsonl")
                        with open(out_path, "a", encoding="utf-8") as f:
                            f.write(
                                json.dumps(b.to_dict(), ensure_ascii=False, separators=(",", ":"))
                                + "\n"
                            )
                        mark_on_disk(cache, data_root, symbol, tf_s, cursor)
                        fetch_count += 1
                        logging.info(
                            "Tail-guard: дописано TF=%ds open=%s",
                            tf_s,
                            ms_to_utc_dt(cursor).isoformat(),
                        )
                if fetch_count >= max_fetch_per_cycle:
                    logging.info(
                        "Tail-guard: ліміт циклу досягнуто (max_fetch_per_cycle=%d)",
                        max_fetch_per_cycle,
                    )
                    return (checked, fetch_count)
            cursor += tf_ms
    return (checked, fetch_count)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except Exception:
        logging.exception("Tail-guard: не вдалось завантажити config.json")
        return 2

    try:
        user_id = str(cfg["user_id"])
        password = str(cfg["password"])
        url = str(cfg.get("url", "http://www.fxcorporate.com/Hosts.jsp"))
        connection = str(cfg.get("connection", "Demo"))

        symbol = str(cfg.get("symbol", "XAU/USD"))
        data_root = str(cfg.get("data_root", "./data_v3"))

        derived = cfg.get(
            "derived_tfs_s", [180, 300, 900, 1800, 3600, 14400, 86400]
        )
        derived_tfs_s = [int(x) for x in derived]

        day_anchor_offset_s = int(cfg.get("day_anchor_offset_s", 0))
        full_scan_lookback_bars = int(cfg.get("tail_guard_full_scan_lookback_bars", 60000))
        full_scan_max_days = int(cfg.get("tail_guard_full_scan_max_days", 30))
        start_from_utc = cfg.get("tail_guard_start_from_utc")
        short_scan_lookback_bars = int(cfg.get("tail_guard_short_scan_lookback_bars", 30))
        interval_min = int(cfg.get("tail_guard_interval_min", 5))
        max_fetch_per_cycle = int(cfg.get("tail_guard_max_fetch_per_cycle", 50))
        window_start_hm = str(cfg.get("tail_guard_window_start_hm", "21:30"))
        window_end_hm = str(cfg.get("tail_guard_window_end_hm", "23:30"))
    except Exception:
        logging.exception("Tail-guard: невірна конфігурація")
        return 2

    logging.info(
        "Tail-guard: start symbol=%s interval=%d window=%s..%s",
        symbol,
        interval_min,
        window_start_hm,
        window_end_hm,
    )

    cache: Dict[str, set[int]] = {}

    try:
        with FxcmHistoryProvider(
            user_id=user_id,
            password=password,
            url=url,
            connection=connection,
            day_anchor_offset_s=day_anchor_offset_s,
        ) as prov:
            logging.info(
                "Tail-guard: full-scan (lookback_bars=%d max_days=%d start_from=%s window=%s..%s)",
                full_scan_lookback_bars,
                full_scan_max_days,
                start_from_utc or "-",
                window_start_hm,
                window_end_hm,
            )
            latest_m1 = tail_last_bar_time_ms(data_root, symbol, tf_s=60)
            min_open_ms = None
            if start_from_utc:
                min_open_ms = int(parse_iso_utc(str(start_from_utc)).timestamp() * 1000)
            elif latest_m1 is not None and full_scan_max_days > 0:
                min_open_ms = latest_m1 - full_scan_max_days * 24 * 60 * 60_000
            checked, written = scan_and_backfill(
                provider=prov,
                data_root=data_root,
                symbol=symbol,
                derived_tfs_s=derived_tfs_s,
                day_anchor_offset_s=day_anchor_offset_s,
                lookback_bars=full_scan_lookback_bars,
                max_fetch_per_cycle=max_fetch_per_cycle,
                window_start_hm=window_start_hm,
                window_end_hm=window_end_hm,
                min_open_ms=min_open_ms,
                cache=cache,
            )
            logging.info(
                "Tail-guard: full-scan done, checked=%d дописано=%d",
                checked,
                written,
            )

            while True:
                now = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
                if in_window_utc(now, window_start_hm, window_end_hm):
                    checked, written = scan_and_backfill(
                        provider=prov,
                        data_root=data_root,
                        symbol=symbol,
                        derived_tfs_s=derived_tfs_s,
                        day_anchor_offset_s=day_anchor_offset_s,
                        lookback_bars=short_scan_lookback_bars,
                        max_fetch_per_cycle=max_fetch_per_cycle,
                        window_start_hm=window_start_hm,
                        window_end_hm=window_end_hm,
                        min_open_ms=None,
                        cache=cache,
                    )
                    logging.info(
                        "Tail-guard: цикл ok (window), checked=%d дописано=%d",
                        checked,
                        written,
                    )
                    time.sleep(max(1, interval_min) * 60)
                else:
                    sleep_s = seconds_until_window_start(now, window_start_hm, window_end_hm)
                    logging.info(
                        "Tail-guard: поза вікном, сон %ds до старту",
                        sleep_s,
                    )
                    time.sleep(max(60, sleep_s))
    except KeyboardInterrupt:
        logging.info("Tail-guard: зупинено користувачем")
        return 0
    except Exception:
        logging.exception("Tail-guard: помилка під час роботи")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
