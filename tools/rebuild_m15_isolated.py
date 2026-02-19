from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Dict

from app.composition import load_config
from core.model.bars import CandleBar, assert_invariants
from core.buckets import bucket_start_ms  # floor_bucket_start_ms removed (ADR-0002)
from runtime.store.ssot_jsonl import JsonlAppender

DAY_YYYYMMDD = "20260205"
TF_M5_MS = 300_000
TF_M15_S = 900


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def _pick_symbol(cfg: dict) -> str:
    if str(cfg.get("symbol", "")).strip() == "XAU/USD":
        return "XAU/USD"
    symbols = cfg.get("symbols")
    if isinstance(symbols, list) and "XAU/USD" in symbols:
        return "XAU/USD"
    symbol = str(cfg.get("symbol", "")).strip()
    if symbol:
        return symbol
    if isinstance(symbols, list) and symbols:
        return str(symbols[0])
    return "XAU/USD"


def _load_m5_day(path: str) -> Dict[int, CandleBar]:
    out: Dict[int, CandleBar] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            bar = CandleBar(**__import__("json").loads(line))
            out[bar.open_time_ms] = bar
    return out


def main() -> int:
    _setup_logging()
    cfg = load_config("config.json")
    symbol = _pick_symbol(cfg)
    data_root = str(cfg.get("data_root", "./data_v3"))

    in_root = os.path.join(data_root, "isolated_m5_warmup")
    out_root = os.path.join(data_root, "isolated_m5_derived")

    sym_dir = symbol.replace("/", "_")
    in_path = os.path.join(in_root, sym_dir, "tf_300", f"part-{DAY_YYYYMMDD}.jsonl")
    if not os.path.exists(in_path):
        logging.error("M5 day file missing: %s", in_path)
        return 2

    logging.info("Rebuild M15 isolated: symbol=%s day=%s", symbol, DAY_YYYYMMDD)

    m5_by_open = _load_m5_day(in_path)
    if not m5_by_open:
        logging.warning("M5 day empty: %s", in_path)
        return 3

    day_dt = dt.datetime.strptime(DAY_YYYYMMDD, "%Y%m%d").replace(tzinfo=dt.timezone.utc)
    day_start_ms = int(day_dt.timestamp() * 1000)
    day_end_ms = day_start_ms + 24 * 60 * 60 * 1000

    writer = JsonlAppender(root=out_root)
    written = 0
    skipped = 0

    try:
        for b0 in range(day_start_ms, day_end_ms, TF_M15_S * 1000):
            a0 = b0
            a1 = b0 + TF_M5_MS
            a2 = b0 + 2 * TF_M5_MS
            b_m5 = [m5_by_open.get(a0), m5_by_open.get(a1), m5_by_open.get(a2)]
            if any(x is None for x in b_m5):
                skipped += 1
                continue

            o = b_m5[0].o
            c = b_m5[-1].c
            h = max(x.h for x in b_m5)
            low = min(x.low for x in b_m5)
            v = sum(x.v for x in b_m5)

            out = CandleBar(
                symbol=symbol,
                tf_s=TF_M15_S,
                open_time_ms=b0,
                close_time_ms=b0 + TF_M15_S * 1000,
                o=o,
                h=h,
                low=low,
                c=c,
                v=v,
                complete=True,
                src="derived",
            )
            assert_invariants(out, anchor_offset_s=0)
            writer.append(out)
            written += 1
    finally:
        writer.close()

    logging.info("Rebuild M15 isolated: written=%d skipped=%d", written, skipped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
