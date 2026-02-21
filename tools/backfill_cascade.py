"""Backfill: повна каскадна деривація M1->M3->M5->M15->M30->H1->H4 з диску.

Waterfall підхід: читає M1 + існуючі бари з диску -> заповнює буфери ->
ітерує bucket'и для кожного TF -> деривує відсутні -> записує на диск.

Використання:
  python -m tools.backfill_cascade --all --date 20260219
  python -m tools.backfill_cascade --symbols XAU_USD --date-range 20260218 20260220
  python -m tools.backfill_cascade --all --date 20260219 --dry-run

Архітектурний шар: tools/ (one-shot утиліта, не runtime).
Залежності: core/ + runtime/store/ssot_jsonl + runtime/ingest/market_calendar.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import time
from typing import Callable, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.derive import (
    DERIVE_ORDER,
    DERIVE_SOURCE,
    GenericBuffer,
    derive_bar,
)
from core.model.bars import CandleBar
from core.buckets import bucket_start_ms
from runtime.store.ssot_jsonl import (
    JsonlAppender,
)
from runtime.ingest.tick_common import calendar_from_group

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("backfill_cascade")

# ---------------------------------------------------------------------------
ANCHOR_OFFSET_S = 68400  # 19h TV anchor for H4

_BUF_MAX = {
    60: 3000, 180: 1000, 300: 700, 900: 300, 1800: 150, 3600: 60,
}


# ---------------------------------------------------------------------------
# Disk I/O
# ---------------------------------------------------------------------------
def load_bars_from_disk(
    data_root: str,
    symbol: str,
    tf_s: int,
    day_keys: List[str],
) -> List[CandleBar]:
    sym_dir = symbol.replace("/", "_")
    bars = []  # type: List[CandleBar]
    for day in day_keys:
        path = os.path.join(data_root, sym_dir, "tf_%d" % tf_s, "part-%s.jsonl" % day)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                try:
                    bar = CandleBar(
                        symbol=symbol,
                        tf_s=tf_s,
                        open_time_ms=int(obj["open_time_ms"]),
                        close_time_ms=int(obj["close_time_ms"]),
                        o=float(obj["o"]),
                        h=float(obj["h"]),
                        low=float(obj.get("low", obj.get("l", 0))),
                        c=float(obj["c"]),
                        v=float(obj.get("v", 0)),
                        complete=True,
                        src=obj.get("src", "history"),
                    )
                    bars.append(bar)
                except (KeyError, ValueError):
                    continue
    bars.sort(key=lambda b: b.open_time_ms)
    return bars


# ---------------------------------------------------------------------------
# Waterfall cascade
# ---------------------------------------------------------------------------
def run_waterfall(
    data_root: str,
    symbol: str,
    day_keys: List[str],
    check_day_keys: List[str],
    anchor_offset_s: int,
    is_trading_fn: Optional[Callable[[int], bool]],
    writer: Optional[JsonlAppender],
    dry_run: bool,
) -> Dict[int, int]:
    """Waterfall: M1->M3->M5->M15->M30->H1->H4.

    Для кожного рівня каскаду:
    1. Завантажити source бари (disk + derived з попередніх кроків) в буфер
    2. Ітерувати всі можливі target bucket'и в date range
    3. Якщо bucket відсутній на диску -> derive_bar з буфера
    4. Записати -> додати у буфер для наступного рівня
    """
    buffers = {}  # type: Dict[int, GenericBuffer]
    new_counts = {}  # type: Dict[int, int]

    def get_buf(tf_s):
        # type: (int) -> GenericBuffer
        buf = buffers.get(tf_s)
        if buf is None:
            buf = GenericBuffer(tf_s, max_keep=_BUF_MAX.get(tf_s, 100))
            buffers[tf_s] = buf
        return buf

    # Time range (ms) for bucket iteration
    d_from = dt.datetime.strptime(day_keys[0], "%Y%m%d").replace(
        tzinfo=dt.timezone.utc
    )
    d_to = dt.datetime.strptime(day_keys[-1], "%Y%m%d").replace(
        tzinfo=dt.timezone.utc
    ) + dt.timedelta(days=1)
    range_start_ms = int(d_from.timestamp() * 1000)
    range_end_ms = int(d_to.timestamp() * 1000)

    # Step 0: Load M1 from disk
    m1_bars = load_bars_from_disk(data_root, symbol, 60, check_day_keys)
    if not m1_bars:
        log.warning("  No M1 bars")
        return new_counts
    log.info("  M1 loaded: %d bars", len(m1_bars))
    m1_buf = get_buf(60)
    for b in m1_bars:
        m1_buf.upsert(b)

    # Cascade steps: source_tf -> target_tf
    cascade_steps = []  # type: List[Tuple[int, int]]
    for target_tf_s in DERIVE_ORDER:
        source_info = DERIVE_SOURCE.get(target_tf_s)
        if source_info is None:
            continue
        source_tf_s, _ = source_info
        cascade_steps.append((source_tf_s, target_tf_s))

    for source_tf_s, target_tf_s in cascade_steps:
        target_tf_ms = target_tf_s * 1000
        is_htf = target_tf_s >= 14400
        anchor = anchor_offset_s if is_htf else 0
        anchor_ms = anchor * 1000

        # Load existing source bars from disk into buffer (if not yet loaded)
        if source_tf_s not in buffers:
            src_bars = load_bars_from_disk(data_root, symbol, source_tf_s, check_day_keys)
            src_buf = get_buf(source_tf_s)
            for b in src_bars:
                src_buf.upsert(b)
            log.info("  %s loaded from disk: %d bars", _tf_label(source_tf_s), len(src_bars))

        # Load existing target bars from disk into buffer (needed as source for next levels)
        tgt_disk_bars = load_bars_from_disk(data_root, symbol, target_tf_s, check_day_keys)
        tgt_existing = set(b.open_time_ms for b in tgt_disk_bars)
        tgt_buf = get_buf(target_tf_s)
        for b in tgt_disk_bars:
            tgt_buf.upsert(b)

        source_buf = buffers.get(source_tf_s)
        if source_buf is None:
            continue

        # Iterate all possible buckets in date range
        scan_start = range_start_ms - target_tf_ms if is_htf else range_start_ms
        first_bucket = bucket_start_ms(scan_start, target_tf_ms, anchor_ms)
        if first_bucket < scan_start:
            first_bucket += target_tf_ms

        count = 0
        for bkt in range(first_bucket, range_end_ms, target_tf_ms):
            if bkt in tgt_existing:
                continue

            derived = derive_bar(
                symbol=symbol,
                target_tf_s=target_tf_s,
                source_buffer=source_buf,
                bucket_open_ms=bkt,
                anchor_offset_s=anchor,
                is_trading_fn=is_trading_fn,
                filter_calendar_pause=True,
            )
            if derived is None:
                continue

            if not dry_run and writer is not None:
                writer.append(derived)

            tgt_buf.upsert(derived)
            tgt_existing.add(bkt)
            count += 1

            t = dt.datetime.utcfromtimestamp(bkt / 1000)
            log.info(
                "NEW %s %s open=%s%s",
                _tf_label(target_tf_s), symbol, t.strftime("%Y-%m-%d %H:%M"),
                " (dry)" if dry_run else "",
            )

        if count:
            new_counts[target_tf_s] = count

    return new_counts


def _tf_label(tf_s):
    # type: (int) -> str
    return {60: "M1", 180: "M3", 300: "M5", 900: "M15",
            1800: "M30", 3600: "H1", 14400: "H4"}.get(tf_s, "TF%d" % tf_s)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def build_calendar(cfg, symbol):
    # type: (dict, str) -> Optional[MarketCalendar]
    groups = cfg.get("market_calendar_by_group", {})
    sym_groups = cfg.get("market_calendar_symbol_groups", {})
    # Підтримка обох форматів: XAU_USD та XAU/USD
    group_name = (
        sym_groups.get(symbol)
        or sym_groups.get(symbol.replace("_", "/"))
        or sym_groups.get(symbol.replace("/", "_"))
    )
    if not group_name:
        return None
    group_cfg = groups.get(group_name)
    if not isinstance(group_cfg, dict):
        return None
    return calendar_from_group(group_cfg)


def symbols_from_config(cfg):
    # type: (dict) -> List[str]
    raw = cfg.get("symbols", [])
    if isinstance(raw, list) and raw:
        return [str(s) for s in raw if str(s).strip()]
    sym = cfg.get("symbol", "")
    return [str(sym)] if sym else []


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    # type: () -> int
    ap = argparse.ArgumentParser(
        description="Backfill: cascade M1->H4 from disk (one-shot).",
    )
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--all", action="store_true", help="All symbols from config.json")
    ap.add_argument("--symbols", default=None, help="CSV, e.g. XAU_USD,GER30")
    ap.add_argument("--date", default=None, help="YYYYMMDD (single day)")
    ap.add_argument(
        "--date-range", nargs=2, metavar=("FROM", "TO"),
        help="YYYYMMDD YYYYMMDD (inclusive)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        with open(args.config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        log.exception("Cannot load %s", args.config)
        return 2

    data_root = str(cfg.get("data_root", "./data_v3"))

    if getattr(args, "all", False):
        sym_list = symbols_from_config(cfg)
    elif args.symbols:
        sym_list = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        log.error("Specify --all or --symbols")
        return 2

    if not sym_list:
        log.error("Empty symbol list")
        return 2

    if args.date:
        day_keys = [args.date]
    elif args.date_range:
        d_from = dt.datetime.strptime(args.date_range[0], "%Y%m%d")
        d_to = dt.datetime.strptime(args.date_range[1], "%Y%m%d")
        day_keys = []  # type: List[str]
        cur = d_from
        while cur <= d_to:
            day_keys.append(cur.strftime("%Y%m%d"))
            cur += dt.timedelta(days=1)
    else:
        log.error("Specify --date or --date-range")
        return 2

    first_day = dt.datetime.strptime(day_keys[0], "%Y%m%d")
    prev_day = (first_day - dt.timedelta(days=1)).strftime("%Y%m%d")
    last_day = dt.datetime.strptime(day_keys[-1], "%Y%m%d")
    next_day = (last_day + dt.timedelta(days=1)).strftime("%Y%m%d")
    check_day_keys = [prev_day] + day_keys + [next_day]

    log.info(
        "Backfill: %d symbols, days=%s, dry=%s",
        len(sym_list), day_keys, args.dry_run,
    )

    writer = None  # type: Optional[JsonlAppender]
    if not args.dry_run:
        writer = JsonlAppender(
            root=data_root,
            day_anchor_offset_s=cfg.get("day_anchor_offset_s", 68400),
            day_anchor_offset_s_alt=cfg.get("day_anchor_offset_s_alt"),
            day_anchor_offset_s_alt2=cfg.get("day_anchor_offset_s_alt2"),
            day_anchor_offset_s_d1=cfg.get("day_anchor_offset_s_d1"),
            day_anchor_offset_s_d1_alt=cfg.get("day_anchor_offset_s_d1_alt"),
        )

    total_new = 0
    errors = []  # type: List[str]

    for symbol in sym_list:
        log.info("=== %s ===", symbol)
        t0 = time.time()

        cal = build_calendar(cfg, symbol)
        is_trading_fn = cal.is_trading_minute if cal else None

        try:
            new_counts = run_waterfall(
                data_root=data_root,
                symbol=symbol,
                day_keys=day_keys,
                check_day_keys=check_day_keys,
                anchor_offset_s=ANCHOR_OFFSET_S,
                is_trading_fn=is_trading_fn,
                writer=writer,
                dry_run=args.dry_run,
            )
        except Exception as exc:
            log.exception("  FAIL: %s", exc)
            errors.append("%s: %s" % (symbol, exc))
            continue

        elapsed = time.time() - t0
        sym_total = sum(new_counts.values())
        total_new += sym_total

        if new_counts:
            parts = []
            for tf_s in DERIVE_ORDER:
                n = new_counts.get(tf_s, 0)
                if n > 0:
                    parts.append("%s=%d" % (_tf_label(tf_s), n))
            log.info("  RESULT: %s (%.1fs)", " ".join(parts), elapsed)
        else:
            log.info("  All bars already on disk (%.1fs)", elapsed)

    if writer is not None:
        writer.close()

    log.info("=== TOTAL: %d new bars, %d errors ===", total_new, len(errors))
    for e in errors:
        log.error("  FAIL: %s", e)
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
