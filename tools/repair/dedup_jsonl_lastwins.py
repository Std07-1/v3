"""Dedup JSONL bar files by open_time_ms — переможець обирається ЄДИНИМ вибирачем читачів (ADR-0094).

Імʼя модуля історичне: колись це був чистий last-wins. Тепер переможця групи обирає
`core.model.bar_choice.choose_better_bar` — той самий, що й у TAIL/RANGE-читачів
(complete → final src → не-partial → ts → нічия: пізніший запис). Інакше ремонт міг би лишити
на диску partial-бар, який читач відкидає, тобто сам ремонт міняв би свічку на графіку.

Use case: after rebuild_from_m1.py --force appends new bars without removing
stale ones, leaving (open_time_ms duplicate, different h/l/c) pairs in JSONL.

Що робить:
  1. Групує рядки за open_time_ms, лишає переможця `core.model.bar_choice` (нічия — пізніший рядок).
  2. Записує переможців за зростанням open_time_ms БАЙТ-У-БАЙТ: рядок не пересеріалізовується,
     порядок ключів і формат чисел лишаються такими, якими їх записав writer.
  3. Підміняє файл `tools.repair.jsonl_rewrite.rewrite_atomic`: бекап `.bak.<unix_ts>`, режим
     доступу оригіналу, файл за своїм іменем існує весь час.

Чого НЕ робить: не переписує файл, у якому є рядок без цілого open_time_ms (`DEDUP_UNPARSABLE`).
Такий рядок читачі пропускають, але це все одно байти SSOT, яких інструмент не розуміє: до
2026-09-14 вони мовчки зникали при перепису. Файли без дублікатів не чіпаються і не друкуються.

Usage:
  python -m tools.repair.dedup_jsonl_lastwins --file <path> --dry-run
  python -m tools.repair.dedup_jsonl_lastwins --glob "data_v3/XAU_USD/tf_*/part-20260505.jsonl" --writers-stopped
Exit: 0 — ok; 1 — хоч один файл не переписано через нерозбірні рядки; 2 — жодного файла або запис
без --writers-stopped.
"""

from __future__ import annotations

import argparse
import dataclasses
import glob
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.model.bar_choice import choose_better_bar
from tools.repair.jsonl_rewrite import open_ms_of, read_lines, rewrite_atomic


@dataclasses.dataclass(frozen=True)
class DedupPlan:
    """Що дедуп зробив би з файлом. Лише читання — нічого не пише."""

    lines_in: int
    unparsable: int
    kept: Tuple[str, ...]  # рядки-переможці як на диску, за зростанням open_time_ms
    kept_not_last: Tuple[int, ...]  # ключі, де переміг НЕ останній рядок групи

    @property
    def duplicates(self) -> int:
        return self.lines_in - self.unparsable - len(self.kept)

    @property
    def refused(self) -> bool:
        return self.unparsable > 0

    @property
    def lines_out(self) -> int:
        """Скільки рядків лишиться у файлі після запуску."""
        return self.lines_in if self.refused else len(self.kept)


def plan_dedup(path: Path) -> DedupPlan:
    lines = read_lines(str(path))
    winners: Dict[int, Tuple[int, dict]] = {}
    last_index: Dict[int, int] = {}
    unparsable = 0
    for index, line in enumerate(lines):
        key = open_ms_of(line)
        if key is None:
            unparsable += 1
            continue
        bar = json.loads(line)
        last_index[key] = index
        current = winners.get(key)
        # Рядки йдуть у порядку файла — нічия вибирача дістається пізнішому запису.
        if current is None or choose_better_bar(current[1], bar) is bar:
            winners[key] = (index, bar)
    keys = sorted(winners)
    return DedupPlan(
        lines_in=len(lines),
        unparsable=unparsable,
        kept=tuple(lines[winners[key][0]] for key in keys),
        kept_not_last=tuple(key for key in keys if winners[key][0] != last_index[key]),
    )


def run_dedup(path: Path, dry_run: bool) -> Optional[DedupPlan]:
    """Дедуп одного файла з гучним звітом; None — файла немає. `plan.refused` — файл НЕ переписано.

    Викликач, що рахує підсумок, мусить рахувати й відмови: відмовлений файл лишає дублікати на
    диску, і підсумок «0 прибрано» без лічильника відмов збрехав би.
    """
    if not path.exists():
        print(f"SKIP {path} (not found)")
        return None
    plan = plan_dedup(path)
    if plan.duplicates == 0 and not plan.refused:
        return plan

    print(
        f"{path.name}: in={plan.lines_in} out={len(plan.kept)} dupes={plan.duplicates} "
        f"parse_err={plan.unparsable} kept_not_last={len(plan.kept_not_last)}"
    )
    # До ADR-0094 переможцем завжди був останній рядок (чистий last-wins); тепер цілий старий бар
    # перемагає свіжий partial — зокрема після `rebuild_from_m1 --force`, і оператор мусить це
    # бачити, а не думати, що перебудову застосовано.
    if plan.kept_not_last:
        print(
            f"  DEDUP_KEPT_NOT_LAST file={path.name} keys={len(plan.kept_not_last)} — переміг не останній "
            f"рядок (complete / final / не-partial / ts, ADR-0094); open_ms={list(plan.kept_not_last[:3])}"
        )
    if plan.refused:
        print(
            f"  DEDUP_UNPARSABLE file={path.name} lines={plan.unparsable} - рядок без цілого open_time_ms; "
            f"файл НЕ переписано, дублікати лишились (читачі обирають з них тим самим вибирачем)"
        )
        return plan
    if dry_run:
        return plan

    backup = rewrite_atomic(str(path), list(plan.kept))
    print(f"  -> rewritten ({plan.lines_out} lines), backup: {Path(backup).name}")
    return plan


def dedup_file(path: Path, dry_run: bool = False) -> Tuple[int, int, int]:
    """Returns (lines_in, lines_out, dupes_removed); у dry-run — що було б після запуску."""
    plan = run_dedup(path, dry_run)
    if plan is None:
        return (0, 0, 0)
    return (plan.lines_in, plan.lines_out, plan.lines_in - plan.lines_out)


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", help="Single JSONL file to dedup")
    g.add_argument("--glob", help="Glob pattern for multiple files")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--writers-stopped",
        action="store_true",
        help="Підтверджую: writer'и зупинені (os.replace відчіпляє відкритий FD — дописи підуть у .bak)",
    )
    args = ap.parse_args()
    if not args.dry_run and not args.writers_stopped:
        print("DEDUP_REFUSED запис потребує --writers-stopped (або --dry-run)", file=sys.stderr)
        return 2

    targets: List[Path] = [Path(args.file)] if args.file else [Path(p) for p in glob.glob(args.glob)]
    if not targets:
        print("no files matched", file=sys.stderr)
        return 2

    print(f"=== {'DRY-RUN' if args.dry_run else 'COMMIT'} mode, {len(targets)} files ===")
    total_in = total_out = refused = 0
    for p in sorted(targets):
        plan = run_dedup(p, dry_run=args.dry_run)
        if plan is None:
            continue
        total_in += plan.lines_in
        total_out += plan.lines_out
        refused += int(plan.refused)

    print(
        f"=== TOTAL: in={total_in} out={total_out} dupes_removed={total_in - total_out} "
        f"refused_unparsable={refused} ==="
    )
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
