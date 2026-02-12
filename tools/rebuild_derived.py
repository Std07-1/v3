from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import time
from typing import Dict, Iterable, List, Optional, Tuple

from app.composition import load_config
from core.model.bars import CandleBar, assert_invariants
from runtime.ingest.market_calendar import MarketCalendar, parse_hm
from runtime.ingest.polling.time_buckets import floor_bucket_start_ms
from runtime.store.ssot_jsonl import (
    JsonlAppender,
    head_first_bar_time_ms,
    iter_day_keys_utc,
    load_day_open_times,
    tail_last_bar_time_ms,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


TF_M5_S = 300
TF_M5_MS = 300_000


def parse_iso_utc(s: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def parse_tf_list(s: str) -> List[int]:
    out: List[int] = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part))
    return out


def iter_m5_bars(
    data_root: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> Iterable[CandleBar]:
    sym_dir = symbol.replace("/", "_")
    tf_dir = f"tf_{TF_M5_S}"
    for day in iter_day_keys_utc(start_ms, end_ms):
        path = os.path.join(data_root, sym_dir, tf_dir, f"part-{day}.jsonl")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue

                    open_ms = obj.get("open_time_ms")
                    if not isinstance(open_ms, int):
                        continue
                    if open_ms < start_ms or open_ms > end_ms:
                        continue

                    o = float(obj.get("o"))
                    h = float(obj.get("h"))
                    low_val = obj.get("low", obj.get("l"))
                    low = float(low_val)
                    c = float(obj.get("c"))
                    v = float(obj.get("v", 0.0))
                    yield CandleBar(
                        symbol=symbol,
                        tf_s=TF_M5_S,
                        open_time_ms=open_ms,
                        close_time_ms=open_ms + TF_M5_MS,
                        o=o,
                        h=h,
                        low=low,
                        c=c,
                        v=v,
                        complete=True,
                        src="history",
                    )
        except Exception:
            logging.exception("Rebuild: помилка читання %s", path)


def _load_day_index(
    cache: Dict[str, set[int]],
    data_root: str,
    symbol: str,
    tf_s: int,
    day: str,
) -> set[int]:
    key = f"{tf_s}:{day}"
    idx = cache.get(key)
    if idx is not None:
        return idx
    idx = load_day_open_times(data_root, symbol, tf_s, day)
    cache[key] = idx
    return idx


def _has_on_disk(
    cache: Dict[str, set[int]],
    data_root: str,
    symbol: str,
    tf_s: int,
    open_time_ms: int,
) -> bool:
    day = dt.datetime.fromtimestamp(open_time_ms / 1000, dt.timezone.utc).strftime("%Y%m%d")
    idx = _load_day_index(cache, data_root, symbol, tf_s, day)
    return open_time_ms in idx


def _mark_on_disk(
    cache: Dict[str, set[int]],
    data_root: str,
    symbol: str,
    tf_s: int,
    open_time_ms: int,
) -> None:
    day = dt.datetime.fromtimestamp(open_time_ms / 1000, dt.timezone.utc).strftime("%Y%m%d")
    idx = _load_day_index(cache, data_root, symbol, tf_s, day)
    idx.add(open_time_ms)


def _calendar_from_group(group_cfg):
    # type: (dict) -> Optional[MarketCalendar]
    """Побудувати MarketCalendar з конфігу calendar-групи."""
    try:
        daily_breaks_raw = group_cfg.get("market_daily_breaks", [])
        daily_breaks = tuple(
            (str(pair[0]), str(pair[1]))
            for pair in daily_breaks_raw
            if isinstance(pair, (list, tuple)) and len(pair) >= 2
        )
        return MarketCalendar(
            enabled=True,
            weekend_close_dow=int(group_cfg["market_weekend_close_dow"]),
            weekend_close_hm=str(group_cfg["market_weekend_close_hm"]),
            weekend_open_dow=int(group_cfg["market_weekend_open_dow"]),
            weekend_open_hm=str(group_cfg["market_weekend_open_hm"]),
            daily_break_start_hm=str(group_cfg["market_daily_break_start_hm"]),
            daily_break_end_hm=str(group_cfg["market_daily_break_end_hm"]),
            daily_break_enabled=True,
            daily_breaks=daily_breaks,
        )
    except Exception:
        return None


def _build_calendar(cfg, symbol):
    # type: (dict, str) -> Optional[MarketCalendar]
    groups = cfg.get("market_calendar_by_group", {})
    sym_groups = cfg.get("market_calendar_symbol_groups", {})
    group_name = sym_groups.get(symbol)
    if not group_name:
        return None
    group_cfg = groups.get(group_name)
    if not isinstance(group_cfg, dict):
        return None
    return _calendar_from_group(group_cfg)


def _load_known_outages(cfg, symbol):
    # type: (dict, str) -> List[Tuple[int, int]]
    raw = cfg.get("known_broker_outages", [])
    intervals = []  # type: List[Tuple[int, int]]
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("symbol", "")) != symbol:
            continue
        try:
            f_str = str(entry["from_utc"]).replace("Z", "+00:00")
            t_str = str(entry["to_utc"]).replace("Z", "+00:00")
            f = dt.datetime.fromisoformat(f_str)
            t = dt.datetime.fromisoformat(t_str)
            intervals.append((int(f.timestamp() * 1000), int(t.timestamp() * 1000)))
        except Exception:
            continue
    return intervals


def rebuild_from_m5(
    data_root: str,
    symbol: str,
    tf_list: List[int],
    start_ms: int,
    end_ms: int,
    dry_run: bool,
    writer: JsonlAppender,
    calendar: Optional[MarketCalendar] = None,
    known_outages: Optional[List[Tuple[int, int]]] = None,
) -> None:
    m5_by_open: Dict[int, CandleBar] = {}
    for b in iter_m5_bars(data_root, symbol, start_ms, end_ms):
        m5_by_open[b.open_time_ms] = b

    cache: Dict[str, set[int]] = {}
    _outages = known_outages or []

    for tf_s in tf_list:
        if tf_s <= TF_M5_S:
            logging.warning("Rebuild: TF=%ds пропущено (має бути > 300).", tf_s)
            continue
        if tf_s % TF_M5_S != 0:
            logging.warning("Rebuild: TF=%ds не кратний 300, пропуск.", tf_s)
            continue

        tf_ms = tf_s * 1000
        n_m5 = tf_ms // TF_M5_MS
        b0 = floor_bucket_start_ms(start_ms, tf_s, anchor_offset_s=0)
        if b0 < start_ms:
            b0 += tf_ms

        written = 0
        partial_written = 0
        skipped = 0
        for open_ms in range(b0, end_ms + 1, tf_ms):
            parts: List[CandleBar] = []
            missing_closed = 0
            missing_outage = 0
            missing_unexpected = 0

            for t in range(open_ms, open_ms + tf_ms, TF_M5_MS):
                b = m5_by_open.get(t)
                if b is not None:
                    parts.append(b)
                elif calendar is not None:
                    is_trading = calendar.is_trading_minute(t)
                    in_outage = any(f <= t < to for f, to in _outages)
                    if not is_trading:
                        missing_closed += 1
                    elif in_outage:
                        missing_outage += 1
                    else:
                        missing_unexpected += 1
                else:
                    # Без календаря — старша поведінка: вимагаємо всі M5
                    missing_unexpected += 1

            # Пропускаємо bucket якщо: немає жодного M5, або є unexpected gap
            if not parts or missing_unexpected > 0:
                skipped += 1
                continue

            if _has_on_disk(cache, data_root, symbol, tf_s, open_ms):
                continue

            o = parts[0].o
            c = parts[-1].c
            h = max(x.h for x in parts)
            low = min(x.low for x in parts)
            v = sum(x.v for x in parts)

            is_partial = (missing_closed + missing_outage) > 0
            extensions = {}  # type: Dict[str, Any]
            if is_partial:
                extensions = {
                    "partial": True,
                    "partial_reason": "calendar_break",
                    "missing_expected_closed": missing_closed,
                    "missing_known_outage": missing_outage,
                    "missing_unexpected": 0,
                    "present_m5": len(parts),
                    "total_m5": n_m5,
                }

            out = CandleBar(
                symbol=symbol,
                tf_s=tf_s,
                open_time_ms=open_ms,
                close_time_ms=open_ms + tf_ms,
                o=o,
                h=h,
                low=low,
                c=c,
                v=v,
                complete=True,
                src="derived",
                extensions=extensions,
            )
            assert_invariants(out, anchor_offset_s=0)
            if not dry_run:
                writer.append(out)
                _mark_on_disk(cache, data_root, symbol, tf_s, open_ms)
            written += 1
            if is_partial:
                partial_written += 1

        msg = "Rebuild TF=%ds: written=%d skipped=%d"
        args_log = [tf_s, written, skipped]
        if partial_written:
            msg += " (partial=%d)"
            args_log.append(partial_written)
        logging.info(msg, *args_log)


def _update_derived_tail_state(
    data_root,   # type: str
    symbol,      # type: str
    tf_list,     # type: List[int]
    start_ms,    # type: int
    end_ms,      # type: int
):
    # type: (...) -> None
    """Записати стан derived rebuild у _derived_tail_state.json.

    Формат сумісний з engine_b._store_derived_tail_state().
    """
    path = os.path.join(data_root, "_derived_tail_state.json")
    data = {"symbols": {}}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and isinstance(raw.get("symbols"), dict):
                data = raw
        except Exception:
            data = {"symbols": {}}

    symbols = data.get("symbols")
    if not isinstance(symbols, dict):
        symbols = {}
        data["symbols"] = symbols

    window_bars = max(1, int((end_ms - start_ms) / TF_M5_MS))
    entry = {
        "m5_tail_ok_end_ms": int(end_ms),
        "m5_tail_ok_window_bars": window_bars,
        "m5_tail_missing_count": 0,
        "m5_tail_missing_samples": [],
        "m5_tail_missing_end_ms": int(end_ms),
        "m5_tail_missing_window_bars": window_bars,
        "derived_coverage_from_ms": {str(tf): int(start_ms) for tf in tf_list},
        "derived_gaps_detected": False,
        "ts_ms": int(time.time() * 1000),
        "source": "tools.rebuild_derived",
    }
    symbols[symbol] = entry

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        logging.exception("Rebuild: не вдалось записати стан у %s", path)


def _symbols_from_config(cfg: dict) -> List[str]:
    """Канонічний список символів з config.json (symbols[] → fallback symbol)."""
    raw = cfg.get("symbols", [])
    if isinstance(raw, list) and raw:
        return [str(s) for s in raw if str(s).strip()]
    sym = cfg.get("symbol", "")
    return [str(sym)] if sym else []


def _rebuild_one_symbol(
    data_root,       # type: str
    symbol,          # type: str
    tf_list,         # type: List[int]
    start_override,  # type: int
    end_override,    # type: int
    dry_run,         # type: bool
    calendar=None,   # type: Optional[MarketCalendar]
    known_outages=None,  # type: Optional[List[Tuple[int, int]]]
):
    # type: (...) -> str
    """Rebuild derived TFs для одного символу.

    Повертає None при успіху, рядок з описом помилки при невдачі.
    """
    if start_override is not None:
        start_ms = start_override
    else:
        start_ms = head_first_bar_time_ms(data_root, symbol, tf_s=TF_M5_S)

    if end_override is not None:
        end_ms = end_override
    else:
        end_open = tail_last_bar_time_ms(data_root, symbol, tf_s=TF_M5_S)
        end_ms = None if end_open is None else end_open + TF_M5_MS

    if start_ms is None or end_ms is None:
        return "%s: немає M5 на диску" % symbol

    writer = JsonlAppender(root=data_root)
    try:
        rebuild_from_m5(
            data_root=data_root,
            symbol=symbol,
            tf_list=tf_list,
            start_ms=start_ms,
            end_ms=end_ms,
            dry_run=dry_run,
            writer=writer,
            calendar=calendar,
            known_outages=known_outages,
        )
    except Exception as exc:
        return "%s: %s" % (symbol, exc)
    finally:
        writer.close()

    # Оновити _derived_tail_state.json (сумісно з engine_b)
    _update_derived_tail_state(
        data_root=data_root,
        symbol=symbol,
        tf_list=tf_list,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Rebuild derived TFs (M15/M30/H1) з M5 даних на диску.",
    )
    ap.add_argument("--config", default="config.json")
    ap.add_argument(
        "--all", action="store_true",
        help="Ітерувати по всіх symbols[] з config.json (послідовно).",
    )
    ap.add_argument(
        "--symbols", default=None,
        help="CSV override символів, напр. 'XAU/USD,XAG/USD'.",
    )
    ap.add_argument("--symbol", default=None, help="Один символ (legacy)")
    ap.add_argument("--tf", default=None, help="TF у секундах, напр. 900,1800,3600")
    ap.add_argument("--start-utc", default=None)
    ap.add_argument("--end-utc", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except Exception:
        logging.exception("Rebuild: не вдалось завантажити config.json")
        return 2

    data_root = str(cfg.get("data_root", "./data_v3"))

    # --- Визначення списку символів ---
    if getattr(args, "all", False):
        sym_list = _symbols_from_config(cfg)
        if not sym_list:
            logging.error("Rebuild: --all задано, але symbols[] порожній у %s", args.config)
            return 2
        logging.info("Rebuild --all: %d символів: %s", len(sym_list), ", ".join(sym_list))
    elif args.symbols:
        sym_list = [s.strip() for s in args.symbols.split(",") if s.strip()]
        if not sym_list:
            logging.error("Rebuild: --symbols порожній")
            return 2
        logging.info("Rebuild --symbols: %d символів: %s", len(sym_list), ", ".join(sym_list))
    else:
        sym_list = [args.symbol or str(cfg.get("symbol", "XAU/USD"))]

    # --- TF ---
    if args.tf:
        tf_list = parse_tf_list(args.tf)
    else:
        tf_list = [int(x) for x in cfg.get("derived_tfs_s", [])]

    if not tf_list:
        logging.error("Rebuild: порожній список TF")
        return 2

    # --- start/end overrides ---
    start_override = None
    end_override = None
    if args.start_utc:
        start_override = int(parse_iso_utc(args.start_utc).timestamp() * 1000)
    if args.end_utc:
        end_override = int(parse_iso_utc(args.end_utc).timestamp() * 1000)

    # --- Послідовний rebuild (NoMix для _derived_tail_state.json) ---
    errors = []   # type: List[str]
    ok_count = 0
    for sym in sym_list:
        logging.info("=== Rebuild START: %s ===", sym)
        cal = _build_calendar(cfg, sym)
        outages = _load_known_outages(cfg, sym)
        err = _rebuild_one_symbol(
            data_root=data_root,
            symbol=sym,
            tf_list=tf_list,
            start_override=start_override,
            end_override=end_override,
            dry_run=bool(args.dry_run),
            calendar=cal,
            known_outages=outages,
        )
        if err:
            logging.error("Rebuild FAIL: %s", err)
            errors.append(err)
        else:
            logging.info("Rebuild OK: %s", sym)
            ok_count += 1

    # --- Підсумок ---
    total = len(sym_list)
    fail_count = len(errors)
    logging.info(
        "=== ПІДСУМОК: %d/%d OK, %d FAIL ===",
        ok_count, total, fail_count,
    )
    if errors:
        for e in errors:
            logging.error("  FAIL: %s", e)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
