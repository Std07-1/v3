"""tools/rebuild_from_m1.py — Перебудова derived TFs з M1 даних на диску.

Заповнює гапи у M3→M5→M15→M30→H1→H4, використовуючи core/derive.py
(GenericBuffer + derive_bar) і calendar-aware boundary tolerance.

Не змінює M1 (source). D1 тепер derived (ADR-0023).
Не змінює SSOT формат — append-only через JsonlAppender.

Запуск:
    python -m tools.rebuild_from_m1 [--dry-run] [--symbol XAU/USD] [--start 2025-01-01] [--end 2026-01-01]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import time
from typing import Dict, Iterable, List, Optional

from core.buckets import bucket_start_ms
from core.config_loader import load_system_config as load_config, pick_config_path
from core.derive import (
    DERIVE_ORDER,
    GenericBuffer,
    derive_bar,
)
from core.model.bars import CandleBar
from runtime.ingest.market_calendar import MarketCalendar
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

TF_M1_S = 60
TF_M1_MS = 60_000


# ─── Допоміжні функції ────────────────────────────────────────────

def parse_iso_utc(s: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def iter_m1_bars(
    data_root: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> Iterable[CandleBar]:
    """Читає M1 бари з JSONL файлів (tf_60/) у хронологічному порядку."""
    sym_dir = symbol.replace("/", "_")
    tf_dir = "tf_60"
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

                    try:
                        o = float(obj.get("o"))
                        h = float(obj.get("h"))
                        low_val = obj.get("low", obj.get("l"))
                        low = float(low_val)
                        c = float(obj.get("c"))
                        v = float(obj.get("v", 0.0))
                    except Exception:
                        continue

                    ext = obj.get("extensions", {})
                    if not isinstance(ext, dict):
                        ext = {}

                    yield CandleBar(
                        symbol=symbol,
                        tf_s=TF_M1_S,
                        open_time_ms=open_ms,
                        close_time_ms=open_ms + TF_M1_MS,
                        o=o,
                        h=h,
                        low=low,
                        c=c,
                        v=v,
                        complete=True,
                        src=str(obj.get("src", "history")),
                        extensions=ext,
                    )
        except Exception:
            logging.exception("rebuild_from_m1: помилка читання %s", path)


def _load_day_index(
    cache: Dict[str, set],
    data_root: str,
    symbol: str,
    tf_s: int,
    day: str,
) -> set:
    key = f"{tf_s}:{day}"
    idx = cache.get(key)
    if idx is not None:
        return idx
    idx = load_day_open_times(data_root, symbol, tf_s, day)
    cache[key] = idx
    return idx


def _has_on_disk(
    cache: Dict[str, set],
    data_root: str,
    symbol: str,
    tf_s: int,
    open_time_ms: int,
) -> bool:
    day = dt.datetime.fromtimestamp(
        open_time_ms / 1000, dt.timezone.utc
    ).strftime("%Y%m%d")
    idx = _load_day_index(cache, data_root, symbol, tf_s, day)
    return open_time_ms in idx


def _mark_on_disk(
    cache: Dict[str, set],
    data_root: str,
    symbol: str,
    tf_s: int,
    open_time_ms: int,
) -> None:
    day = dt.datetime.fromtimestamp(
        open_time_ms / 1000, dt.timezone.utc
    ).strftime("%Y%m%d")
    idx = _load_day_index(cache, data_root, symbol, tf_s, day)
    idx.add(open_time_ms)


# ─── Calendar ─────────────────────────────────────────────────────

def _calendar_from_group(group_cfg: dict) -> Optional[MarketCalendar]:
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


def _build_calendar(cfg: dict, symbol: str) -> Optional[MarketCalendar]:
    groups = cfg.get("market_calendar_by_group", {})
    sym_groups = cfg.get("market_calendar_symbol_groups", {})
    group_name = sym_groups.get(symbol)
    if not group_name:
        return None
    group_cfg = groups.get(group_name)
    if not isinstance(group_cfg, dict):
        return None
    return _calendar_from_group(group_cfg)


# ─── Символи з конфігу ────────────────────────────────────────────

def _symbols_from_config(cfg: dict) -> List[str]:
    raw = cfg.get("symbols", [])
    if isinstance(raw, list) and raw:
        return [str(s) for s in raw if str(s).strip()]
    sym = cfg.get("symbol", "")
    return [str(sym)] if sym else []


# ─── Основна логіка rebuild ───────────────────────────────────────

def rebuild_one_symbol(
    data_root: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
    dry_run: bool,
    cfg: dict,
    writer: JsonlAppender,
) -> Dict[str, int]:
    """Rebuild derived TFs для одного символу з M1.

    Staged cascade:
      Stage 1: M1 → M3, M5 (прямо з M1 барів на диску)
      Stage 2: M5 (all disk) → M15
      Stage 3: M15 (all disk) → M30
      Stage 4: M30 (all disk) → H1
      Stage 5: H1 (all disk) → H4

    Calendar-aware (boundary-tolerant).

    Returns: stats dict {tf_s: written_count, ...}
    """
    calendar = _build_calendar(cfg, symbol)
    is_trading_fn = calendar.is_trading_minute if calendar else None
    anchor_offset_s = int(cfg.get("day_anchor_offset_s", 0))

    disk_cache: Dict[str, set] = {}
    stats: Dict[str, int] = {"m1_loaded": 0, "m1_flat_skipped": 0}
    for tf_s in DERIVE_ORDER:
        stats[f"tf_{tf_s}_written"] = 0
        stats[f"tf_{tf_s}_existed"] = 0

    t0 = time.time()

    # ── Stage 1: M1 → M3, M5 ──────────────────────────────
    logging.info("  Stage 1: M1 → M3, M5")
    m1_buf = GenericBuffer(60, max_keep=10000)
    for bar in iter_m1_bars(data_root, symbol, start_ms, end_ms):
        stats["m1_loaded"] += 1
        ext = bar.extensions or {}
        if ext.get("calendar_pause_flat"):
            stats["m1_flat_skipped"] += 1
            continue
        m1_buf.upsert(bar)

        # Derive M3 and M5 from M1
        for target_tf_s in [180, 300]:
            ao_s = 0  # M3/M5 не мають anchor
            target_tf_ms = target_tf_s * 1000
            bucket_open = bucket_start_ms(bar.open_time_ms, target_tf_ms, 0)

            result = derive_bar(
                symbol=symbol,
                target_tf_s=target_tf_s,
                source_buffer=m1_buf,
                bucket_open_ms=bucket_open,
                anchor_offset_s=ao_s,
                is_trading_fn=is_trading_fn,
                filter_calendar_pause=True,
            )
            if result is None:
                continue
            if _has_on_disk(disk_cache, data_root, symbol, target_tf_s, bucket_open):
                stats[f"tf_{target_tf_s}_existed"] += 1
                continue
            if not dry_run:
                writer.append(result)
                _mark_on_disk(disk_cache, data_root, symbol, target_tf_s, bucket_open)
            stats[f"tf_{target_tf_s}_written"] += 1

    elapsed_s1 = time.time() - t0
    logging.info(
        "  Stage 1 done: m1=%d, M3 written=%d existed=%d, M5 written=%d existed=%d (%.1fs)",
        stats["m1_loaded"],
        stats["tf_180_written"], stats["tf_180_existed"],
        stats["tf_300_written"], stats["tf_300_existed"],
        elapsed_s1,
    )

    # ── Stages 2..5: каскад M5→M15→M30→H1→H4 ─────────────
    # Кожен stage читає source TF з диску (включаючи щойно записані бари)
    # і деривує наступний TF.
    cascade_steps = [
        (300, 900, 3),       # M5 → M15
        (900, 1800, 2),      # M15 → M30
        (1800, 3600, 2),     # M30 → H1
        (3600, 14400, 4),    # H1 → H4
    ]
    for source_tf_s, target_tf_s, n_bars in cascade_steps:
        stage_label = f"tf_{source_tf_s}→tf_{target_tf_s}"
        logging.info("  Stage %s", stage_label)

        ao_s = anchor_offset_s if target_tf_s >= 14400 else 0
        ao_ms = ao_s * 1000
        target_tf_ms = target_tf_s * 1000

        # Читаємо source TF з диску
        source_buf = GenericBuffer(source_tf_s, max_keep=50000)
        loaded = 0
        for bar in _iter_bars_from_disk(data_root, symbol, source_tf_s, start_ms, end_ms):
            source_buf.upsert(bar)
            loaded += 1

        logging.info("    Loaded %d %ss bars from disk", loaded, _tf_label(source_tf_s))

        # Derive target TF
        # Ітеруємо по всіх можливих target buckets
        b0 = bucket_start_ms(start_ms, target_tf_ms, ao_ms)
        written = 0
        existed = 0
        for bucket_open in range(b0, end_ms, target_tf_ms):
            if _has_on_disk(disk_cache, data_root, symbol, target_tf_s, bucket_open):
                existed += 1
                continue

            result = derive_bar(
                symbol=symbol,
                target_tf_s=target_tf_s,
                source_buffer=source_buf,
                bucket_open_ms=bucket_open,
                anchor_offset_s=ao_s,
                is_trading_fn=is_trading_fn,
                filter_calendar_pause=True,
            )
            if result is None:
                continue
            if not dry_run:
                writer.append(result)
                _mark_on_disk(disk_cache, data_root, symbol, target_tf_s, bucket_open)
            written += 1

        stats[f"tf_{target_tf_s}_written"] = written
        stats[f"tf_{target_tf_s}_existed"] = existed
        logging.info("    %s: written=%d existed=%d", stage_label, written, existed)

    elapsed = time.time() - t0
    logging.info(
        "REBUILD_DONE symbol=%s elapsed=%.1fs stats=%s",
        symbol,
        elapsed,
        json.dumps(stats, ensure_ascii=False),
    )
    return stats


def _iter_bars_from_disk(
    data_root: str,
    symbol: str,
    tf_s: int,
    start_ms: int,
    end_ms: int,
) -> Iterable[CandleBar]:
    """Читає бари будь-якого TF з JSONL файлів у хронологічному порядку."""
    sym_dir = symbol.replace("/", "_")
    tf_dir = f"tf_{tf_s}"
    tf_ms = tf_s * 1000
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

                    try:
                        o = float(obj.get("o"))
                        h = float(obj.get("h"))
                        low_val = obj.get("low", obj.get("l"))
                        low = float(low_val)
                        c = float(obj.get("c"))
                        v = float(obj.get("v", 0.0))
                    except Exception:
                        continue

                    ext = obj.get("extensions", {})
                    if not isinstance(ext, dict):
                        ext = {}

                    yield CandleBar(
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
                        src=str(obj.get("src", "derived")),
                        extensions=ext,
                    )
        except Exception:
            logging.exception("rebuild_from_m1: помилка читання %s", path)


def _tf_label(tf_s: int) -> str:
    labels = {60: "M1", 180: "M3", 300: "M5", 900: "M15", 1800: "M30", 3600: "H1", 14400: "H4", 86400: "D1"}
    return labels.get(tf_s, f"{tf_s}s")


# ─── CLI entrypoint ───────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild derived TFs (M3→H4) з M1 даних на диску.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Тільки підрахунок, без запису.")
    parser.add_argument("--symbol", type=str, default=None, help="Один символ (наприклад XAU/USD). За замовчуванням — усі з config.")
    parser.add_argument("--start", type=str, default=None, help="Початок діапазону (ISO UTC, наприклад 2025-01-01).")
    parser.add_argument("--end", type=str, default=None, help="Кінець діапазону (ISO UTC, наприклад 2026-03-01).")
    parser.add_argument("--config", type=str, default=None, help="Шлях до config.json.")
    args = parser.parse_args()

    cfg = load_config(args.config or pick_config_path())
    data_root = str(cfg.get("data_root", "data_v3"))

    # Symbols
    if args.symbol:
        symbols = [args.symbol]
    else:
        symbols = _symbols_from_config(cfg)
    if not symbols:
        logging.error("Немає символів. Вкажіть --symbol або перевірте config.json.")
        return

    # Writer
    writer = JsonlAppender(
        root=data_root,
        day_anchor_offset_s=int(cfg.get("day_anchor_offset_s", 0)),
        day_anchor_offset_s_d1=cfg.get("day_anchor_offset_s_d1"),
        day_anchor_offset_s_d1_alt=cfg.get("day_anchor_offset_s_d1_alt"),
        day_anchor_offset_s_alt=cfg.get("day_anchor_offset_s_alt"),
        day_anchor_offset_s_alt2=cfg.get("day_anchor_offset_s_alt2"),
    )

    total_stats: Dict[str, Dict[str, int]] = {}
    try:
        for symbol in symbols:
            logging.info("═══ REBUILD START symbol=%s ═══", symbol)

            # Визначення діапазону
            if args.start:
                start_ms = int(parse_iso_utc(args.start).timestamp() * 1000)
            else:
                start_ms = head_first_bar_time_ms(data_root, symbol, tf_s=TF_M1_S)
                if start_ms is None:
                    logging.warning("SKIP symbol=%s — M1 дані відсутні.", symbol)
                    continue

            if args.end:
                end_ms = int(parse_iso_utc(args.end).timestamp() * 1000)
            else:
                tail_ms = tail_last_bar_time_ms(data_root, symbol, tf_s=TF_M1_S)
                if tail_ms is None:
                    logging.warning("SKIP symbol=%s — M1 tail відсутній.", symbol)
                    continue
                end_ms = tail_ms + TF_M1_MS

            logging.info(
                "REBUILD_RANGE symbol=%s start=%s end=%s",
                symbol,
                dt.datetime.fromtimestamp(start_ms / 1000, dt.timezone.utc).isoformat(),
                dt.datetime.fromtimestamp(end_ms / 1000, dt.timezone.utc).isoformat(),
            )

            stats = rebuild_one_symbol(
                data_root=data_root,
                symbol=symbol,
                start_ms=start_ms,
                end_ms=end_ms,
                dry_run=args.dry_run,
                cfg=cfg,
                writer=writer,
            )
            total_stats[symbol] = stats
    finally:
        writer.close()

    # Підсумок
    logging.info("═══ REBUILD SUMMARY ═══")
    for sym, stats in total_stats.items():
        written_total = sum(v for k, v in stats.items() if k.endswith("_written"))
        existed_total = sum(v for k, v in stats.items() if k.endswith("_existed"))
        logging.info(
            "  %s: m1=%d written=%d existed=%d%s",
            sym,
            stats.get("m1_loaded", 0),
            written_total,
            existed_total,
            " [DRY-RUN]" if args.dry_run else "",
        )


if __name__ == "__main__":
    main()
