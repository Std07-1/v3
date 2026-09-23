"""tools/rebuild_from_m1.py — Перебудова derived TFs з M1 даних на диску.

Заповнює гапи у M3→M5→M15→M30→H1→H4, використовуючи core/derive.py
(GenericBuffer + derive_bar) і calendar-aware boundary tolerance.

H4/D1 — на сезонній сітці символу (ADR-0095): правило з htf_anchor_rule_resolver, бакети крокують
ітератором сітки (htf_bucket_start_ms / htf_next_bucket_start_ms), тож прогін через вихідні DST
міняє сітку сам і не будує H4 обрубка доби переходу з годин наступної доби.

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
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from core.config_loader import htf_anchor_rule_resolver, load_system_config as load_config, pick_config_path
from core.derive import (
    DERIVE_ORDER,
    GenericBuffer,
    derive_bar,
)
from core.model.bars import CandleBar
from core.model.candle_chain import is_display_hidden
from core.session_anchor import D1_S, htf_bucket_start_ms, htf_next_bucket_start_ms
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
    day = dt.datetime.fromtimestamp(open_time_ms / 1000, dt.timezone.utc).strftime(
        "%Y%m%d"
    )
    idx = _load_day_index(cache, data_root, symbol, tf_s, day)
    return open_time_ms in idx


def _mark_on_disk(
    cache: Dict[str, set],
    data_root: str,
    symbol: str,
    tf_s: int,
    open_time_ms: int,
) -> None:
    day = dt.datetime.fromtimestamp(open_time_ms / 1000, dt.timezone.utc).strftime(
        "%Y%m%d"
    )
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


def _grid_bucket_opens(start_ms: int, end_ms: int, tf_s: int, anchor_rule: str) -> Iterator[int]:
    """Відкриття бакетів TF від бакета, що містить `start_ms`, до `end_ms` (виключно) — кроком сезонної сітки.

    H4/D1 крокують `htf_next_bucket_start_ms`, а не `range(b0, end, tf_ms)`: на вихідних DST сітка зсувається на
    годину, доба переходу триває 23 або 25 год, а її останній H4 — обрубок (осінь: нд 21:00, 1 год). M1..H1 —
    рівний крок від епохи.
    """
    bucket_open = htf_bucket_start_ms(start_ms, tf_s, anchor_rule)
    while bucket_open < end_ms:
        yield bucket_open
        bucket_open = htf_next_bucket_start_ms(bucket_open, tf_s, anchor_rule)


def rebuild_one_symbol(
    data_root: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
    dry_run: bool,
    cfg: dict,
    writer: JsonlAppender,
    anchor_rule: str,
    force: bool = False,
) -> Dict[str, int]:
    """Rebuild derived TFs для одного символу з M1.

    Staged cascade:
      Stage 1: M1 → M3, M5, D1 (прямо з M1 барів на диску)
      Stage 2: M5 (all disk) → M15
      Stage 3: M15 (all disk) → M30
      Stage 4: M30 (all disk) → H1
      Stage 5: H1 (all disk) → H4

    Calendar-aware (boundary-tolerant). Бакети H4/D1 — на сезонній сітці `anchor_rule` (ADR-0095): якір і
    вікно агрегації свої в кожного бакета, а не один якір на весь прогін.

    Returns: stats dict {tf_s: written_count, ...}
    """
    calendar = _build_calendar(cfg, symbol)
    is_trading_fn = calendar.is_trading_minute if calendar else None

    disk_cache: Dict[str, set] = {}
    stats: Dict[str, int] = {"m1_loaded": 0, "m1_flat_skipped": 0}
    for tf_s in DERIVE_ORDER:
        stats[f"tf_{tf_s}_written"] = 0
        stats[f"tf_{tf_s}_existed"] = 0

    def derive_stage(target_tf_s: int, source_buf: GenericBuffer) -> None:
        """Бакети target TF у [start, end) з source_buf; наявні на диску ключі пропускаються (без --force)."""
        for bucket_open in _grid_bucket_opens(start_ms, end_ms, target_tf_s, anchor_rule):
            if not force and _has_on_disk(disk_cache, data_root, symbol, target_tf_s, bucket_open):
                stats[f"tf_{target_tf_s}_existed"] += 1
                continue
            result = derive_bar(
                symbol=symbol,
                target_tf_s=target_tf_s,
                source_buffer=source_buf,
                bucket_open_ms=bucket_open,
                is_trading_fn=is_trading_fn,
                filter_calendar_pause=True,
                anchor_rule=anchor_rule,
            )
            if result is None:
                continue
            if not dry_run:
                writer.append(result)
                _mark_on_disk(disk_cache, data_root, symbol, target_tf_s, bucket_open)
            stats[f"tf_{target_tf_s}_written"] += 1

    t0 = time.time()

    # ── Stage 1: M1 → M3, M5, D1 ─────────────────────────
    # Спочатку завантажуємо ВСІ M1 бари, потім деривуємо по бакетах.
    # Bug-fix: попередня версія деривувала після кожного M1 upsert,
    # що призводило до запису partial бару (source_count=1) з подальшим
    # пропуском повних даних через _has_on_disk cache hit.
    logging.info("  Stage 1: M1 → M3, M5, D1")
    m1_buf = GenericBuffer(60, max_keep=100000)  # 100K = ~69 days for D1 (1440/day)
    for bar in iter_m1_bars(data_root, symbol, start_ms, end_ms):
        stats["m1_loaded"] += 1
        if is_display_hidden(bar.extensions):
            stats["m1_flat_skipped"] += 1
            continue
        m1_buf.upsert(bar)

    # M3, M5 і D1 (ADR-0023: D1 = 1440 × M1) — з повного M1 буфера, аналогічно Stages 2-5
    for target_tf_s in (180, 300, 86400):
        derive_stage(target_tf_s, m1_buf)

    elapsed_s1 = time.time() - t0
    logging.info(
        "  Stage 1 done: m1=%d, M3 written=%d existed=%d, M5 written=%d existed=%d, D1 written=%d existed=%d (%.1fs)",
        stats["m1_loaded"],
        stats["tf_180_written"],
        stats["tf_180_existed"],
        stats["tf_300_written"],
        stats["tf_300_existed"],
        stats["tf_86400_written"],
        stats["tf_86400_existed"],
        elapsed_s1,
    )

    # ── Stages 2..5: каскад M5→M15→M30→H1→H4 ─────────────
    # Кожен stage читає source TF з диску (включаючи щойно записані бари)
    # і деривує наступний TF.
    cascade_steps = [
        (300, 900),  # M5 → M15
        (900, 1800),  # M15 → M30
        (1800, 3600),  # M30 → H1
        (3600, 14400),  # H1 → H4
    ]
    for source_tf_s, target_tf_s in cascade_steps:
        stage_label = f"tf_{source_tf_s}→tf_{target_tf_s}"
        logging.info("  Stage %s", stage_label)

        # Читаємо source TF з диску
        source_buf = GenericBuffer(source_tf_s, max_keep=50000)
        loaded = 0
        for bar in _iter_bars_from_disk(
            data_root, symbol, source_tf_s, start_ms, end_ms
        ):
            source_buf.upsert(bar)
            loaded += 1

        logging.info("    Loaded %d %ss bars from disk", loaded, _tf_label(source_tf_s))

        derive_stage(target_tf_s, source_buf)
        logging.info(
            "    %s: written=%d existed=%d",
            stage_label,
            stats[f"tf_{target_tf_s}_written"],
            stats[f"tf_{target_tf_s}_existed"],
        )

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
    labels = {
        60: "M1",
        180: "M3",
        300: "M5",
        900: "M15",
        1800: "M30",
        3600: "H1",
        14400: "H4",
        86400: "D1",
    }
    return labels.get(tf_s, f"{tf_s}s")


# ─── CLI entrypoint ───────────────────────────────────────────────


class DedupRefused(RuntimeError):
    """Dedup-on-finish відмовив хоч одному файлу (DEDUP_UNPARSABLE): дублікати `--force` там лишились."""

    def __init__(self, paths: List[str], dupes_removed: int) -> None:
        super().__init__(
            "DEDUP_REFUSED files=%d dupes_removed_elsewhere=%d first=%s" % (len(paths), dupes_removed, paths[0])
        )
        self.paths = paths
        self.dupes_removed = dupes_removed


def dedup_derived_in_ranges(
    data_root: str,
    symbol_ranges: Dict[str, Tuple[int, int]],
) -> int:
    """Прибрати дублікати open_time_ms у derived part-файлах єдиним вибирачем (ADR-0094).

    Діапазон береться з ФАКТИЧНО перебудованого вікна на символ, а не з `--start`:
    без цього `--force` без `--start` мовчки не дедуплікував нічого (ADR-0054 §3.1 P0.2).
    Символ без запису в ``symbol_ranges`` = пропущений під час rebuild, тут не чіпаємо.

    Returns:
        Скільки дублікатів видалено сумарно.

    Raises:
        DedupRefused: після обходу ВСІХ файлів, якщо хоч один не переписано (нерозбірний рядок).
    """
    from pathlib import Path

    from tools.repair.dedup_jsonl_lastwins import run_dedup

    derived_tfs = [tf for tf in DERIVE_ORDER if tf != TF_M1_S]
    dedup_total = 0
    refused: List[str] = []
    for symbol, (start_ms, end_ms) in sorted(symbol_ranges.items()):
        if start_ms <= 0 or end_ms <= start_ms:
            raise ValueError(
                f"dedup range невалідний symbol={symbol} start={start_ms} end={end_ms}"
            )
        sym_dir = symbol.replace("/", "_")
        for tf_s in derived_tfs:
            for day in iter_day_keys_utc(start_ms, end_ms):
                p = Path(data_root) / sym_dir / f"tf_{tf_s}" / f"part-{day}.jsonl"
                if not p.exists():
                    continue
                plan = run_dedup(p, dry_run=False)
                if plan is None:
                    continue
                dedup_total += plan.lines_in - plan.lines_out
                if plan.refused:
                    refused.append(str(p))
    if refused:
        # Решту файлів уже дедупнуто; відмовлені лишили дублікати перебудови — це не «0 прибрано».
        raise DedupRefused(refused, dedup_total)
    return dedup_total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild derived TFs (M3→H4) з M1 даних на диску.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Тільки підрахунок, без запису."
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Один символ (наприклад XAU/USD). За замовчуванням — усі з config.",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help=(
            "Початок діапазону (ISO UTC, наприклад 2025-01-01). Вирівнюється назад на відкриття торгової доби "
            "символу (бакет D1 сезонної сітки)."
        ),
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="Кінець діапазону (ISO UTC, наприклад 2026-03-01).",
    )
    parser.add_argument("--config", type=str, default=None, help="Шлях до config.json.")
    parser.add_argument(
        "--writers-stopped",
        action="store_true",
        help=(
            "Підтверджую: smc-fxcm/smc-ticks/smc-preview зупинені. Потрібно для символів "
            "з config.json:symbols — rebuild append-ить у ті самі part-файли, що й live writer "
            "(ADR-0054 §3.1 P0.2)."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Перезаписати вже існуючі derived бари (після ремонту M1 gaps).",
    )
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

    # Правило якоря H4/D1 кожного символу — до першого запису (ADR-0095 §3.4): символ невиміряної групи
    # календаря відмовляє весь прогін гучно, а не падає посеред нього після частково записаних TF.
    try:
        anchor_rule_for_symbol = htf_anchor_rule_resolver(cfg)
        anchor_rules = {symbol: anchor_rule_for_symbol(symbol) for symbol in symbols}
    except ValueError as exc:
        logging.error("REBUILD_REFUSED правило якоря H4/D1 не визначене: %s", exc)
        raise SystemExit(2)

    # Writer
    writer = JsonlAppender(root=data_root, anchor_rule_for_symbol=anchor_rule_for_symbol)

    # ADR-0054 §3.1 P0.2: rebuild пише append-only у ті самі part-файли, що й live
    # writer, без lock. Для символу з config.json:symbols це тихе джерело дублікатів,
    # тому вимагаємо явного підтвердження, що ingest зупинено.
    _live = [s for s in symbols if s in set(cfg.get("symbols", []))]
    if _live and not args.writers_stopped and not args.dry_run:
        logging.error(
            "REBUILD_REFUSED symbols=%s у config.json:symbols — зупиніть smc-fxcm/smc-ticks/"
            "smc-preview і додайте --writers-stopped (або запускайте з --dry-run)",
            ",".join(_live),
        )
        raise SystemExit(2)

    total_stats: Dict[str, Dict[str, int]] = {}
    # Фактичні вікна rebuild на символ — джерело діапазону для dedup-on-finish
    symbol_ranges: Dict[str, Tuple[int, int]] = {}
    try:
        for symbol in symbols:
            logging.info("═══ REBUILD START symbol=%s ═══", symbol)

            # Визначення діапазону
            if args.start:
                start_ms = int(parse_iso_utc(args.start).timestamp() * 1000)
            else:
                _head = head_first_bar_time_ms(data_root, symbol, tf_s=TF_M1_S)
                if _head is None:
                    logging.warning("SKIP symbol=%s — M1 дані відсутні.", symbol)
                    continue
                start_ms = _head

            if args.end:
                end_ms = int(parse_iso_utc(args.end).timestamp() * 1000)
            else:
                tail_ms = tail_last_bar_time_ms(data_root, symbol, tf_s=TF_M1_S)
                if tail_ms is None:
                    logging.warning("SKIP symbol=%s — M1 tail відсутній.", symbol)
                    continue
                end_ms = tail_ms + TF_M1_MS

            # Бакет H4/D1, що містить `start`, на сезонній сітці відкривається раніше за нього: торгова доба
            # 17:00 NY починається попереднього вечора UTC. Без вирівнювання джерело читалося від `start`, тому
            # перший H4/D1 будувався partial з обрізаних годин. Dedup `--force` при цьому не заходив у part-файл
            # попереднього дня, і там лишалися обидва бари: старий цілий і новий partial. Тому вирівнюємо на
            # відкриття торгової доби — найширшого похідного бакета.
            aligned_start_ms = htf_bucket_start_ms(start_ms, D1_S, anchor_rules[symbol])
            if aligned_start_ms != start_ms:
                logging.info(
                    "REBUILD_RANGE_ALIGNED symbol=%s requested=%s aligned=%s",
                    symbol,
                    dt.datetime.fromtimestamp(start_ms / 1000, dt.timezone.utc).isoformat(),
                    dt.datetime.fromtimestamp(aligned_start_ms / 1000, dt.timezone.utc).isoformat(),
                )
                start_ms = aligned_start_ms

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
                anchor_rule=anchor_rules[symbol],
                force=args.force,
            )
            total_stats[symbol] = stats
            symbol_ranges[symbol] = (start_ms, end_ms)
    finally:
        writer.close()

    # Dedup-on-finish: коли --force активний, append-only JSONL writer лишає stale records.
    # Переможця обирає core.model.bar_choice (ADR-0094), а НЕ last-wins: цілий старий бар
    # перемагає щойно перебудований partial. Такі ключі dedup_file друкує як
    # DEDUP_KEPT_NOT_LAST — перебудову там не застосовано, і старший TF, зібраний у цьому ж
    # прогоні з перебудованого дочірнього бару, може дати cascade_mismatch у health.
    if args.force and not args.dry_run:
        logging.info("═══ DEDUP-ON-FINISH (force=True) ═══")
        skipped = [s for s in symbols if s not in symbol_ranges]
        if skipped:
            # I5: не мовчазний пропуск — символи без rebuild не дедуплікуються свідомо
            logging.warning("DEDUP_SKIP symbols=%s (rebuild не виконувався)", ",".join(skipped))
        try:
            dedup_total = dedup_derived_in_ranges(data_root, symbol_ranges)
        except DedupRefused as refusal:
            logging.error("%s — перебудову записано, дублікати у цих файлах лишились", refusal)
            raise SystemExit(1)
        logging.info(
            "DEDUP_TOTAL dupes_removed=%d symbols=%d", dedup_total, len(symbol_ranges)
        )

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
