"""Dedup derived JSONL files (M3–D1) — усі part-файли символу через SSOT-дедуп (ADR-0094).

Зʼявився як фікс для cascade_catchup reset_watermark(0), що дописував усі derived бари при
кожному рестарті. До 2026-09-14 мав власний вибирач (`_SRC_RANK`: history > derived, partial
не бачив) — тобто на диску міг лишити не той бар, який показують читачі. Тепер це лише обхід
файлів: переможця обирає `tools.repair.dedup_jsonl_lastwins` (єдиний вибирач
`core.model.bar_choice`, рядок байт-у-байт, бекап, нерозбірні рядки блокують перепис).

Використання:
  python -m tools.dedup_derived_jsonl --all --dry-run
  python -m tools.dedup_derived_jsonl --symbols "XAU/USD" --writers-stopped
Exit: 0 — ok; 1 — хоч один файл відмовлено (DEDUP_UNPARSABLE); 2 — запис без --writers-stopped.
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_loader import load_system_config, pick_config_path
from tools.repair.dedup_jsonl_lastwins import run_dedup

log = logging.getLogger("dedup_derived")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

DERIVED_TFS = [180, 300, 900, 1800, 3600, 14400, 86400]


def _sym_dir(sym: str) -> str:
    return sym.replace("/", "_")


def dedup_symbol(data_root: str, sym: str, dry_run: bool) -> Tuple[int, int]:
    """Дедуплікує всі derived TF файли символу; (прибрано дублікатів, файлів-відмов DEDUP_UNPARSABLE).

    Відмовлений файл лишає дублікати на диску, тому його не можна загубити в «0 прибрано»: гейт
    ADR-0054 «dry-run = 0 дублікатів» на такому файлі інакше пройшов би хибно.
    """
    total_dropped = refused = 0
    sym_dir = os.path.join(data_root, _sym_dir(sym))
    if not os.path.isdir(sym_dir):
        log.warning("SKIP %s — dir not found: %s", sym, sym_dir)
        return 0, 0

    for tf_s in DERIVED_TFS:
        tf_dir = os.path.join(sym_dir, "tf_%d" % tf_s)
        if not os.path.isdir(tf_dir):
            continue
        for fname in sorted(os.listdir(tf_dir)):
            if not fname.endswith(".jsonl"):
                continue
            plan = run_dedup(Path(tf_dir) / fname, dry_run=dry_run)
            if plan is None:
                continue
            total_dropped += plan.lines_in - plan.lines_out
            refused += int(plan.refused)

    return total_dropped, refused


def main() -> int:
    parser = argparse.ArgumentParser(description="Dedup derived JSONL files")
    parser.add_argument("--all", action="store_true", help="Всі символи з config.json")
    parser.add_argument(
        "--symbols", type=str, help="Comma-separated symbols (XAU/USD,NAS100)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Тільки показати що б змінилось"
    )
    parser.add_argument("--data-root", type=str, default=None, help="data_v3 root")
    parser.add_argument(
        "--writers-stopped",
        action="store_true",
        help="Підтверджую: writer'и зупинені (os.replace відчіпляє відкритий FD — дописи підуть у .bak)",
    )
    args = parser.parse_args()
    if not args.dry_run and not args.writers_stopped:
        log.error("DEDUP_REFUSED запис потребує --writers-stopped (або --dry-run)")
        return 2

    cfg_path = pick_config_path()
    cfg = load_system_config(cfg_path)
    data_root = args.data_root or str(cfg.get("data_root", "./data_v3"))

    if args.all:
        symbols: List[str] = cfg.get("symbols", [])
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        parser.error("Вкажіть --all або --symbols")
        return 2

    total = refused = 0
    for sym in symbols:
        dropped, sym_refused = dedup_symbol(data_root, sym, args.dry_run)
        total += dropped
        refused += sym_refused

    action = "would remove" if args.dry_run else "removed"
    log.info(
        "DONE: %s %d duplicate entries across %d symbols; refused_unparsable_files=%d",
        action, total, len(symbols), refused,
    )
    if refused:
        log.error("DEDUP_REFUSED files=%d - дублікати в них лишились (див. DEDUP_UNPARSABLE вище)", refused)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
