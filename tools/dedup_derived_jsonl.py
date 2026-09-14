"""Dedup derived JSONL files (M3–D1) — усі part-файли символу через SSOT-дедуп (ADR-0094).

Зʼявився як фікс для cascade_catchup reset_watermark(0), що дописував усі derived бари при
кожному рестарті. До 2026-09-14 мав власний вибирач (`_SRC_RANK`: history > derived, partial
не бачив) — тобто на диску міг лишити не той бар, який показують читачі. Тепер це лише обхід
файлів: переможця обирає `tools.repair.dedup_jsonl_lastwins` (єдиний вибирач
`core.model.bar_choice`, рядок байт-у-байт, бекап, нерозбірні рядки блокують перепис).

Використання:
  python -m tools.dedup_derived_jsonl --all
  python -m tools.dedup_derived_jsonl --symbols "XAU/USD"
  python -m tools.dedup_derived_jsonl --all --dry-run
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_loader import load_system_config, pick_config_path
from tools.repair.dedup_jsonl_lastwins import dedup_file

log = logging.getLogger("dedup_derived")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

DERIVED_TFS = [180, 300, 900, 1800, 3600, 14400, 86400]


def _sym_dir(sym: str) -> str:
    return sym.replace("/", "_")


def dedup_symbol(data_root: str, sym: str, dry_run: bool) -> int:
    """Дедуплікує всі derived TF файли для символу; повертає кількість прибраних дублікатів."""
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
            _lines_in, _lines_out, dropped = dedup_file(Path(tf_dir) / fname, dry_run=dry_run)
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
