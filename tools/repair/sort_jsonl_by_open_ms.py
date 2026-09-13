"""tools/repair/sort_jsonl_by_open_ms.py — впорядкувати part-файли JSONL за open_time_ms.

Навіщо. `tools/fetch_tf_backfill` сіє історію сторінками від сьогодні назад і дописує їх
у ті самі part-файли, тому на кожному кроці ланцюжка лишається шов: свіжіші бари, за ними
старіші. Кілька читачів неявно припускали хронологічний порядок (див. changelog
`20260912-003`); найгірше з того полагоджено в коді, але вибір ВІКНА досі бере останні N
рядків замість найновіших N барів, а health через це тримає кожен засіяний символ у YELLOW.

Що робить і чого НЕ робить:
  * СТАБІЛЬНО сортує рядки за `open_time_ms`. Стабільність тут не косметика. Обидва дедупи
    (`disk_layer._dedup_open_ms`, `uds._ensure_sorted_dedup`) стабільно сортують за ключем,
    а переможця обирає єдиний `core.model.bar_choice.choose_better_bar` (ADR-0094): complete →
    final src → не-partial → ts, і лише при ПОВНІЙ нічиї — пізніший у вхідному порядку. Отже
    порядок рядків вирішує саме тоді, коли записи нерозрізненні за якістю; стабільний сорт зберігає їхній взаємний порядок, тому
    після сортування обирається ТОЙ САМИЙ запис, що й до нього. 178 файлів мають дублікати.
    Near-dedup D1 (поріг `tf_ms // 12`) залежить лише від ключів і до порядку байдужий.
  * Рядок переписується БАЙТ-У-БАЙТ: жодного re-serialize JSON (інакше змінився б порядок
    ключів і формат чисел).
  * НЕ дедуплікує, НЕ відкидає, НЕ додає. Дедуп — окреме рішення зі своєю семантикою
    (`tools/repair/dedup_jsonl_lastwins.py`).
  * Уже впорядковані файли не переписуються взагалі.

Рейка: перед записом перевіряється, що новий вміст — ТОЧНО перестановка старого
(мультимножина рядків збігається). Не збіглась — файл не чіпаємо і кажемо голосно.

Запуск (writers мусять бути зупинені — os.replace відчіпляє відкритий FD):
    python -m tools.repair.sort_jsonl_by_open_ms --dry-run
    python -m tools.repair.sort_jsonl_by_open_ms --commit --writers-stopped
    python -m tools.repair.sort_jsonl_by_open_ms --symbol US30 --tf 60 --dry-run
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import shutil
import sys
import time
from collections import Counter
from typing import List, Optional, Tuple

from core.config_loader import load_system_config, pick_config_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)


def read_lines(path: str) -> List[str]:
    """Непорожні рядки файла без завершального переводу рядка."""
    with open(path, encoding="utf-8") as fh:
        return [ln.rstrip("\n").rstrip("\r") for ln in fh if ln.strip()]


def open_ms_of(line: str) -> Optional[int]:
    try:
        value = json.loads(line)["open_time_ms"]
    except Exception:
        return None
    return value if isinstance(value, int) else None


def plan_file(path: str) -> Tuple[Optional[List[str]], int, int]:
    """(впорядковані рядки або None якщо не треба/не можна, барів, інверсій)."""
    lines = read_lines(path)
    keys = [open_ms_of(ln) for ln in lines]
    if any(k is None for k in keys):
        log.error(
            "SORT_JSONL_UNPARSABLE path=%s — рядок без цілого open_time_ms; файл пропущено",
            path,
        )
        return None, len(lines), 0
    inversions = sum(1 for a, b in zip(keys, keys[1:]) if b < a)
    if inversions == 0:
        return None, len(lines), 0
    # sorted() стабільний: записи з однаковим ключем зберігають взаємний порядок.
    ordered = [ln for _k, ln in sorted(zip(keys, lines), key=lambda pair: pair[0])]
    if Counter(ordered) != Counter(lines):
        log.error(
            "SORT_JSONL_NOT_A_PERMUTATION path=%s — вміст змінився б; файл пропущено",
            path,
        )
        return None, len(lines), inversions
    return ordered, len(lines), inversions


def rewrite_atomic(path: str, ordered: List[str]) -> str:
    """Підміна без жодної миті, коли файла за його іменем не існує.

    Порядок важливий. Наївне «спершу перейменувати оригінал у .bak, потім підставити
    .tmp» лишає вікно, у якому part-файла немає: читач у цю мить (ws_server живий і
    читає диск) отримає FileNotFoundError і МОВЧКИ пропустить файл — у графіку зʼявиться
    дірка на рівному місці. Тому: спершу пишемо .tmp і фсинкаємо, далі бекап робимо
    жорстким лінком на СТАРИЙ inode (os.link не чіпає ім'я path), і лише потім один
    атомарний os.replace. Файл існує весь час; читач бачить або старий вміст, або новий.
    """
    stamp = int(time.time())
    backup = "%s.bak.%d" % (path, stamp)
    tmp = "%s.tmp" % path
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for line in ordered:
            fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.link(path, backup)
    except OSError:
        # ФС без жорстких лінків — падаємо назад на копію (теж не чіпає ім'я path).
        shutil.copy2(path, backup)
    os.replace(tmp, path)
    return backup


def iter_part_files(root: str, symbols: Optional[List[str]], tfs: Optional[List[int]]) -> List[str]:
    out: List[str] = []
    for sym_dir in sorted(os.listdir(root)):
        base = os.path.join(root, sym_dir)
        if not os.path.isdir(base) or sym_dir.startswith("_"):
            continue
        if symbols and sym_dir not in symbols:
            continue
        for tf_dir in sorted(os.listdir(base)):
            if not tf_dir.startswith("tf_"):
                continue
            if tfs and int(tf_dir[3:]) not in tfs:
                continue
            out.extend(sorted(glob.glob(os.path.join(base, tf_dir, "part-*.jsonl"))))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Стабільно впорядкувати part-файли за open_time_ms.")
    ap.add_argument("--root", default=None, help="data_root (default: з config.json)")
    ap.add_argument("--symbol", default=None, help="Символи через кому у вигляді каталогів (XAU_USD,US30)")
    ap.add_argument("--tf", default=None, help="TF у секундах через кому")
    ap.add_argument("--commit", action="store_true", help="Насправді переписати (інакше dry-run)")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Явний dry-run (поведінка за замовчуванням; разом із --commit — помилка)",
    )
    ap.add_argument(
        "--writers-stopped",
        action="store_true",
        help="Підтверджую: smc-fxcm/smc-preview/smc-ticks зупинені (os.replace відчіпляє відкритий FD)",
    )
    args = ap.parse_args()

    if args.commit and args.dry_run:
        log.error("SORT_JSONL_REFUSED --commit і --dry-run разом — незрозуміло, чого від нас хочуть")
        return 2
    if args.commit and not args.writers_stopped:
        log.error("SORT_JSONL_REFUSED --commit потребує --writers-stopped: перепис під живим writer'ом губить бари")
        return 2

    root = args.root or str(load_system_config(pick_config_path()).get("data_root", "data_v3"))
    symbols = [s.strip() for s in args.symbol.split(",")] if args.symbol else None
    tfs = [int(t) for t in args.tf.split(",")] if args.tf else None

    files = iter_part_files(root, symbols, tfs)
    log.info("SORT_JSONL_START root=%s files=%d commit=%s", root, len(files), args.commit)

    touched = skipped = bars = inversions = 0
    for path in files:
        ordered, n_bars, n_inv = plan_file(path)
        if ordered is None:
            skipped += 1
            continue
        touched += 1
        bars += n_bars
        inversions += n_inv
        if args.commit:
            backup = rewrite_atomic(path, ordered)
            log.info(
                "SORT_JSONL_FIXED path=%s bars=%d inversions=%d backup=%s",
                path, n_bars, n_inv, os.path.basename(backup),
            )
        else:
            log.info("SORT_JSONL_WOULD_FIX path=%s bars=%d inversions=%d", path, n_bars, n_inv)

    log.info(
        "SORT_JSONL_DONE %s=%d untouched=%d bars_in_touched=%d inversions=%d",
        "fixed" if args.commit else "would_fix", touched, skipped, bars, inversions,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
