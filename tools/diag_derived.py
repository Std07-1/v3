from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
from typing import List, Optional, Set, Tuple

from v3_polling_b import (
    anchor_offset_for_tf,
    floor_bucket_start_ms,
    head_first_bar_time_ms,
    iter_day_keys_utc,
    load_config,
    load_day_open_times,
    ms_to_utc_dt,
    tail_last_bar_time_ms,
)


def parse_hhmm_to_minutes(s: str) -> int:
    """Парсить HH:MM у хвилини від початку доби."""
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


def is_trading_minute_utc(
    ts: dt.datetime,
    weekend_close_dow: int,
    weekend_close_min: int,
    weekend_open_dow: int,
    weekend_open_min: int,
    daily_break_start_min: Optional[int],
    daily_break_end_min: Optional[int],
) -> bool:
    """Перевіряє, чи хвилина є торговою (UTC) за простими правилами FX."""
    dow = ts.weekday()  # Mon=0..Sun=6
    minutes = ts.hour * 60 + ts.minute

    # weekend close/open: закритий інтервал [close .. open)
    week_minutes = 7 * 1440
    close_idx = weekend_close_dow * 1440 + weekend_close_min
    open_idx = weekend_open_dow * 1440 + weekend_open_min
    if open_idx <= close_idx:
        open_idx += week_minutes
    ts_idx = dow * 1440 + minutes
    closed_weekend = (close_idx <= ts_idx < open_idx) or (
        close_idx <= (ts_idx + week_minutes) < open_idx
    )
    if closed_weekend:
        return False

    # daily break (якщо задано)
    if daily_break_start_min is not None and daily_break_end_min is not None:
        if daily_break_start_min < daily_break_end_min:
            if daily_break_start_min <= minutes < daily_break_end_min:
                return False
        elif daily_break_start_min > daily_break_end_min:
            if minutes >= daily_break_start_min or minutes < daily_break_end_min:
                return False

    return True


def collapse_minute_ranges(times_ms: List[int]) -> List[Tuple[int, int, int]]:
    """Групує хвилини у діапазони. Повертає (start_ms, end_ms, len_minutes)."""
    if not times_ms:
        return []
    times_ms.sort()
    out: List[Tuple[int, int, int]] = []
    start = times_ms[0]
    prev = start
    length = 1
    for t in times_ms[1:]:
        if t == prev + 60_000:
            length += 1
        else:
            out.append((start, prev, length))
            start = t
            length = 1
        prev = t
    out.append((start, prev, length))
    return out


def diagnose_derived_on_disk(
    data_root: str,
    symbol: str,
    derived_tfs_s: List[int],
    day_anchor_offset_s: int,
    day_anchor_offset_s_d1: Optional[int],
    day_anchor_offset_s_d1_alt: Optional[int],
    lookback_bars: int,
    weekend_close_dow: int,
    weekend_close_hm: str,
    weekend_open_dow: int,
    weekend_open_hm: str,
    daily_break_start_hm: Optional[str],
    daily_break_end_hm: Optional[str],
    boundary_slip_minutes_per_day: int,
    missing_trading_output: str,
    missing_trading_max_ranges: int,
    missing_trading_dump_path: Optional[str],
    ignore_minutes_ms: Optional[Set[int]],
) -> None:
    """Діагностика покриття M1 і можливості побудови derived на диску."""
    weekend_close_min = parse_hhmm_to_minutes(weekend_close_hm)
    weekend_open_min = parse_hhmm_to_minutes(weekend_open_hm)
    daily_break_start_min = (
        parse_hhmm_to_minutes(daily_break_start_hm)
        if daily_break_start_hm
        else None
    )
    daily_break_end_min = (
        parse_hhmm_to_minutes(daily_break_end_hm) if daily_break_end_hm else None
    )

    logging.info("Diag derived: data_root=%s symbol=%s", data_root, symbol)
    logging.info(
        "Diag market: close=%s dow=%d; open=%s dow=%d; break=%s..%s",
        weekend_close_hm,
        weekend_close_dow,
        weekend_open_hm,
        weekend_open_dow,
        daily_break_start_hm or "-",
        daily_break_end_hm or "-",
    )
    last = tail_last_bar_time_ms(data_root, symbol, tf_s=60)
    first = head_first_bar_time_ms(data_root, symbol, tf_s=60)
    if last is None or first is None:
        logging.warning("Diag derived: немає M1 даних на диску для %s", symbol)
        return

    lookback = max(1, int(lookback_bars))
    start_ms = max(first, last - lookback * 60_000)

    day_keys = iter_day_keys_utc(start_ms, last)
    m1_set: set[int] = set()
    missing_days: List[str] = []
    total_days = 0
    for day in day_keys:
        total_days += 1
        day_set = load_day_open_times(data_root, symbol, 60, day)
        if not day_set:
            missing_days.append(day)
        for t in day_set:
            if start_ms <= t <= last:
                m1_set.add(t)

    if missing_days:
        head = ", ".join(missing_days[:5])
        tail = ", ".join(missing_days[-5:])
        if len(missing_days) > 10:
            logging.info(
                "Diag M1: дні без файлів=%d/%d (перші=%s ... останні=%s)",
                len(missing_days),
                total_days,
                head,
                tail,
            )
        else:
            logging.info(
                "Diag M1: дні без файлів=%d/%d (%s)",
                len(missing_days),
                total_days,
                ", ".join(missing_days),
            )

    logging.info(
        "Diag M1: on_disk=%d (унікальні open_time_ms у вікні)",
        len(m1_set),
    )

    expected = ((last - start_ms) // 60_000) + 1
    missing_list: List[int] = []
    missing_trading_list: List[int] = []
    expected_trading = 0
    missing_trading = 0
    max_gap_trading = 0
    cur_gap_trading = 0
    prev_trading_missing: Optional[int] = None
    first_trading_missing: Optional[int] = None
    last_trading_missing: Optional[int] = None
    for t in range(start_ms, last + 1, 60_000):
        if ignore_minutes_ms and t in ignore_minutes_ms:
            continue
        ts = ms_to_utc_dt(t)
        is_trading = is_trading_minute_utc(
            ts,
            weekend_close_dow,
            weekend_close_min,
            weekend_open_dow,
            weekend_open_min,
            daily_break_start_min,
            daily_break_end_min,
        )
        if is_trading:
            expected_trading += 1
        if t not in m1_set:
            missing_list.append(t)
            if is_trading:
                missing_trading += 1
                missing_trading_list.append(t)
                if prev_trading_missing is not None and t == prev_trading_missing + 60_000:
                    cur_gap_trading += 1
                else:
                    cur_gap_trading = 1
                if cur_gap_trading > max_gap_trading:
                    max_gap_trading = cur_gap_trading
                prev_trading_missing = t
                if first_trading_missing is None:
                    first_trading_missing = t
                last_trading_missing = t
            else:
                prev_trading_missing = None
                cur_gap_trading = 0
        else:
            prev_trading_missing = None
            cur_gap_trading = 0

    missing = len(missing_list)
    actual = expected - missing
    miss_pct = (missing / expected * 100.0) if expected else 0.0

    logging.debug(
        "Diag M1: вікно [%s .. %s] очікувано_24x7=%d фактично=%d пропущено=%d (%.2f%%)",
        ms_to_utc_dt(start_ms).isoformat(),
        ms_to_utc_dt(last).isoformat(),
        expected,
        actual,
        missing,
        miss_pct,
    )

    if expected_trading:
        trading_actual = expected_trading - missing_trading
        trading_pct = missing_trading / expected_trading * 100.0
        logging.info(
            "Diag M1: очікувано_торгових=%d фактично=%d пропущено=%d (%.2f%%)",
            expected_trading,
            trading_actual,
            missing_trading,
            trading_pct,
        )
        if boundary_slip_minutes_per_day > 0:
            slip = min(expected_trading, boundary_slip_minutes_per_day * total_days)
            expected_trading_adj = max(0, expected_trading - slip)
            missing_trading_adj = max(0, missing_trading - slip)
            trading_actual_adj = expected_trading_adj - missing_trading_adj
            trading_pct_adj = (
                missing_trading_adj / expected_trading_adj * 100.0
                if expected_trading_adj
                else 0.0
            )
            logging.info(
                "Diag M1: очікувано_торгових_adj=%d фактично=%d пропущено=%d (%.2f%%) slip=%d/день",
                expected_trading_adj,
                trading_actual_adj,
                missing_trading_adj,
                trading_pct_adj,
                boundary_slip_minutes_per_day,
            )
        if missing_trading:
            logging.info(
                "Diag M1: пропуски_торгові найбільший=%d хв; перший=%s; останній=%s",
                max_gap_trading,
                ms_to_utc_dt(first_trading_missing).isoformat(),
                ms_to_utc_dt(last_trading_missing).isoformat(),
            )

        if missing_trading_output != "none" and missing_trading_list:
            ranges = collapse_minute_ranges(missing_trading_list)
            if missing_trading_output == "ranges":
                limit = max(1, missing_trading_max_ranges)
                logging.info(
                    "Diag M1: пропуски_торгові діапазони=%d (показуємо до %d)",
                    len(ranges),
                    limit,
                )
                for start_ms, end_ms, length in ranges[:limit]:
                    logging.info(
                        "Diag M1: пропуск [%s .. %s] len=%d хв",
                        ms_to_utc_dt(start_ms).isoformat(),
                        ms_to_utc_dt(end_ms).isoformat(),
                        length,
                    )
            elif missing_trading_output == "full" and missing_trading_dump_path:
                out_path = missing_trading_dump_path
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                try:
                    with open(out_path, "w", encoding="utf-8") as f:
                        for t in missing_trading_list:
                            f.write(
                                json.dumps(
                                    {
                                        "open_time_ms": t,
                                        "open_time_utc": ms_to_utc_dt(t).isoformat(),
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                    logging.info(
                        "Diag M1: пропуски_торгові записано у %s (рядків=%d)",
                        out_path,
                        len(missing_trading_list),
                    )
                except Exception as e:
                    logging.warning(
                        "Diag M1: не вдалось записати пропуски у %s: %s",
                        out_path,
                        str(e),
                    )

    max_gap = 0
    cur = 0
    prev = None
    for t in missing_list:
        if prev is not None and t == prev + 60_000:
            cur += 1
        else:
            cur = 1
        if cur > max_gap:
            max_gap = cur
        prev = t

    if missing_list:
        logging.info(
            "Diag M1: найбільший пропуск=%d хв; перший=%s; останній=%s",
            max_gap,
            ms_to_utc_dt(missing_list[0]).isoformat(),
            ms_to_utc_dt(missing_list[-1]).isoformat(),
        )

    for tf_s in derived_tfs_s:
        anchor_offset_s = anchor_offset_for_tf(
            tf_s,
            day_anchor_offset_s,
            day_anchor_offset_s_d1,
        )
        tf_ms = tf_s * 1000
        b0 = floor_bucket_start_ms(start_ms, tf_s, anchor_offset_s=anchor_offset_s)
        total = 0
        complete = 0
        while b0 + tf_ms - 60_000 <= last:
            if b0 >= start_ms:
                total += 1
                ok = True
                for t in range(b0, b0 + tf_ms, 60_000):
                    if t not in m1_set:
                        ok = False
                        break
                if ok:
                    complete += 1
            b0 += tf_ms

        missing_buckets = total - complete
        logging.info(
            "Diag derived tf=%ds: бакетів=%d повних=%d пропущених=%d",
            tf_s,
            total,
            complete,
            missing_buckets,
        )
        if tf_s == 86400:
            logging.info(
                "Diag D1: anchor_offset_s=%d (UTC-доба). Для FX може знадобитись офсет.",
                day_anchor_offset_s,
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    try:
        cfg = load_config(args.config)
    except Exception:
        logging.exception("Не вдалось завантажити config.json")
        return 2

    try:
        symbol = str(cfg.get("symbol", "XAU/USD"))
        data_root = str(cfg.get("data_root", "./data_v3"))

        derived = cfg.get(
            "derived_tfs_s", [180, 300, 900, 1800, 3600, 14400, 86400]
        )
        derived_tfs_s = [int(x) for x in derived]

        day_anchor_offset_s = int(cfg.get("day_anchor_offset_s", 0))
        day_anchor_offset_s_d1_raw = cfg.get("day_anchor_offset_s_d1", None)
        day_anchor_offset_s_d1 = (
            None if day_anchor_offset_s_d1_raw is None else int(day_anchor_offset_s_d1_raw)
        )
        day_anchor_offset_s_d1_alt_raw = cfg.get("day_anchor_offset_s_d1_alt", None)
        day_anchor_offset_s_d1_alt = (
            None if day_anchor_offset_s_d1_alt_raw is None else int(day_anchor_offset_s_d1_alt_raw)
        )
        lookback_bars = int(cfg.get("diagnose_lookback_bars", 60000))

        weekend_close_dow = int(cfg.get("market_weekend_close_dow", 4))
        weekend_close_hm = str(cfg.get("market_weekend_close_hm", "21:45"))
        weekend_open_dow = int(cfg.get("market_weekend_open_dow", 6))
        weekend_open_hm = str(cfg.get("market_weekend_open_hm", "22:00"))
        daily_break_start_hm = cfg.get("market_daily_break_start_hm", "22:00")
        daily_break_end_hm = cfg.get("market_daily_break_end_hm", "23:00")
        if daily_break_start_hm == "":
            daily_break_start_hm = None
        if daily_break_end_hm == "":
            daily_break_end_hm = None
        boundary_slip_minutes_per_day = int(
            cfg.get("market_boundary_slip_minutes_per_day", 0)
        )
        ignore_minutes_ms: Set[int] = set()
        ignore_minutes_cfg = cfg.get("market_ignore_minutes_utc", [])
        if isinstance(ignore_minutes_cfg, list):
            for item in ignore_minutes_cfg:
                try:
                    ts = parse_iso_utc(str(item))
                    ignore_minutes_ms.add(int(ts.timestamp() * 1000))
                except Exception:
                    logging.warning(
                        "Diag: ігнор хвилини має невірний формат: %s",
                        item,
                    )
        missing_trading_output = str(
            cfg.get("diagnose_missing_trading_output", "ranges")
        ).lower()
        missing_trading_max_ranges = int(
            cfg.get("diagnose_missing_trading_max_ranges", 100)
        )
        missing_trading_dump_path = cfg.get("diagnose_missing_trading_dump_path")
        if missing_trading_dump_path:
            missing_trading_dump_path = os.path.abspath(
                str(missing_trading_dump_path)
            )
    except Exception:
        logging.exception("Невірна конфігурація")
        return 2

    logging.info("Diag derived: запуск діагностики на диску (без FXCM).")
    diagnose_derived_on_disk(
        data_root=data_root,
        symbol=symbol,
        derived_tfs_s=derived_tfs_s,
        day_anchor_offset_s=day_anchor_offset_s,
        day_anchor_offset_s_d1=day_anchor_offset_s_d1,
        day_anchor_offset_s_d1_alt=day_anchor_offset_s_d1_alt,
        lookback_bars=lookback_bars,
        weekend_close_dow=weekend_close_dow,
        weekend_close_hm=weekend_close_hm,
        weekend_open_dow=weekend_open_dow,
        weekend_open_hm=weekend_open_hm,
        daily_break_start_hm=daily_break_start_hm,
        daily_break_end_hm=daily_break_end_hm,
        boundary_slip_minutes_per_day=boundary_slip_minutes_per_day,
        missing_trading_output=missing_trading_output,
        missing_trading_max_ranges=missing_trading_max_ranges,
        missing_trading_dump_path=missing_trading_dump_path,
        ignore_minutes_ms=ignore_minutes_ms or None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
