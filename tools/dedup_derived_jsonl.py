"""Dedup derived JSONL files (M3–D1) — видаляє дублікати по open_time_ms.

Фікс для проблеми cascade_catchup reset_watermark(0) яка дописувала
усі derived бари при кожному рестарті.

Використання:
  python -m tools.dedup_derived_jsonl --all
  python -m tools.dedup_derived_jsonl --symbols "XAU/USD"
  python -m tools.dedup_derived_jsonl --all --dry-run
"""

import json
import os
import sys
import logging
import argparse
from collections import OrderedDict
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_loader import load_system_config, pick_config_path

log = logging.getLogger("dedup_derived")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

DERIVED_TFS = [180, 300, 900, 1800, 3600, 14400, 86400]
_SRC_RANK = {"history": 3, "derived": 2, "tick_promoted": 1}


def _sym_dir(sym: str) -> str:
    return sym.replace("/", "_")


def _dedup_file(path: str, dry_run: bool) -> int:
    """Дедуплікує один JSONL файл in-place. Повертає кількість видалених дублів."""
    with open(path, encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]

    if not lines:
        return 0

    by_key: "OrderedDict[int, str]" = OrderedDict()
    bars_parsed: "OrderedDict[int, dict]" = OrderedDict()

    for raw_line in lines:
        bar = json.loads(raw_line)
        ot = bar["open_time_ms"]
        existing = bars_parsed.get(ot)
        if existing is None:
            by_key[ot] = raw_line
            bars_parsed[ot] = bar
        else:
            # Зберігаємо кращий бар
            old_rank = _SRC_RANK.get(existing.get("src", ""), 0)
            new_rank = _SRC_RANK.get(bar.get("src", ""), 0)
            if new_rank > old_rank or (
                new_rank == old_rank and bar.get("complete", False)
            ):
                by_key[ot] = raw_line
                bars_parsed[ot] = bar

    dropped = len(lines) - len(by_key)
    if dropped == 0:
        return 0

    if dry_run:
        log.info(
            "DRY_RUN %s: %d lines → %d unique, %d dupes",
            path,
            len(lines),
            len(by_key),
            dropped,
        )
        return dropped

    # Перезаписування: write tmp → replace (або fallback на Windows якщо файл locked)
    tmp_path = path + ".dedup.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        for line in by_key.values():
            fh.write(line + "\n")
    try:
        os.replace(tmp_path, path)
    except PermissionError:
        # Windows: файл може бути locked іншим процесом
        bak_path = path + ".bak"
        try:
            if os.path.exists(bak_path):
                os.remove(bak_path)
            os.rename(path, bak_path)
            os.rename(tmp_path, path)
            os.remove(bak_path)
        except PermissionError:
            log.warning("SKIP_LOCKED %s (file locked by another process)", path)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return 0
    log.info("DEDUPED %s: %d → %d (-%d dupes)", path, len(lines), len(by_key), dropped)
    return dropped


def dedup_symbol(data_root: str, sym: str, dry_run: bool) -> int:
    """Дедуплікує всі derived TF файли для символу."""
    total_dropped = 0
    sym_dir = os.path.join(data_root, _sym_dir(sym))
    if not os.path.isdir(sym_dir):
        log.warning("SKIP %s — dir not found: %s", sym, sym_dir)
        return 0

    for tf_s in DERIVED_TFS:
        tf_dir = os.path.join(sym_dir, "tf_%d" % tf_s)
        if not os.path.isdir(tf_dir):
            continue
        for fname in sorted(os.listdir(tf_dir)):
            if not fname.endswith(".jsonl"):
                continue
            fpath = os.path.join(tf_dir, fname)
            dropped = _dedup_file(fpath, dry_run)
            total_dropped += dropped

    return total_dropped


def main() -> None:
    parser = argparse.ArgumentParser(description="Dedup derived JSONL files")
    parser.add_argument("--all", action="store_true", help="Всі символи з config.json")
    parser.add_argument(
        "--symbols", type=str, help="Comma-separated symbols (XAU/USD,NAS100)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Тільки показати що б змінилось"
    )
    parser.add_argument("--data-root", type=str, default=None, help="data_v3 root")
    args = parser.parse_args()

    cfg_path = pick_config_path()
    cfg = load_system_config(cfg_path)
    data_root = args.data_root or str(cfg.get("data_root", "./data_v3"))

    if args.all:
        symbols: List[str] = cfg.get("symbols", [])
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        parser.error("Вкажіть --all або --symbols")
        return

    total = 0
    for sym in symbols:
        dropped = dedup_symbol(data_root, sym, args.dry_run)
        total += dropped

    action = "would remove" if args.dry_run else "removed"
    log.info(
        "DONE: %s %d duplicate entries across %d symbols", action, total, len(symbols)
    )


if __name__ == "__main__":
    main()
