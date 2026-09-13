"""Dedup JSONL bar files by open_time_ms — переможець обирається ЄДИНИМ вибирачем читачів (ADR-0094).

Імʼя модуля історичне: колись це був чистий last-wins. Тепер переможця групи обирає
`core.model.bar_choice.choose_better_bar` — той самий, що й у TAIL/RANGE-читачів
(complete → final src → не-partial → ts → нічия: пізніший запис). Інакше ремонт міг би лишити
на диску partial-бар, який читач відкидає, тобто сам ремонт міняв би свічку на графіку.

Use case: after rebuild_from_m1.py --force appends new bars without removing
stale ones, leaving (open_time_ms duplicate, different h/l/c) pairs in JSONL.
External readers without UDS dedup logic read top-down and pick stale first record.

This tool:
  1. Reads file as list[dict].
  2. Groups by open_time_ms, keeps the winner of core.model.bar_choice (ties: later line).
  3. Sorts by open_time_ms ASC.
  4. Atomically rewrites: write to .tmp, fsync, rename.
  5. Creates .bak.<unix_ts> backup before rewrite.

Usage:
  python -m tools.repair.dedup_jsonl_lastwins --file <path> [--dry-run]
  python -m tools.repair.dedup_jsonl_lastwins --glob "data_v3/XAU_USD/tf_*/part-20260505.jsonl"
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

from core.model.bar_choice import choose_better_bar


def dedup_file(path: Path, dry_run: bool = False) -> tuple[int, int, int]:
    """Returns (lines_in, lines_out, dupes_removed)."""
    if not path.exists():
        print(f"SKIP {path} (not found)")
        return (0, 0, 0)

    raw_lines = path.read_text(encoding="utf-8").splitlines()
    lines_in = len(raw_lines)

    by_open: dict[int, dict] = {}
    last_line_by_open: dict[int, dict] = {}
    order: list[int] = []
    parse_errors = 0
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        ot = obj.get("open_time_ms")
        if ot is None:
            parse_errors += 1
            continue
        if ot not in by_open:
            order.append(ot)
        last_line_by_open[ot] = obj
        existing = by_open.get(ot)
        # Рядки йдуть у порядку файла — нічия вибирача дістається пізнішому запису.
        by_open[ot] = obj if existing is None else choose_better_bar(existing, obj)

    # sort ascending by open_time_ms (canonical order)
    sorted_keys = sorted(by_open.keys())
    lines_out = len(sorted_keys)
    dupes = lines_in - lines_out - parse_errors

    # Скільки ключів виграв НЕ останній рядок. До ADR-0094 тут завжди було 0 (чистий last-wins);
    # тепер цілий старий бар перемагає свіжий partial — зокрема після `rebuild_from_m1 --force`,
    # і оператор мусить це бачити, а не думати, що перебудову застосовано.
    kept_not_last = [ot for ot in sorted_keys if by_open[ot] is not last_line_by_open[ot]]
    print(
        f"{path.name}: in={lines_in} out={lines_out} dupes={dupes} parse_err={parse_errors} "
        f"kept_not_last={len(kept_not_last)}"
    )
    if kept_not_last:
        print(
            f"  DEDUP_KEPT_NOT_LAST file={path.name} keys={len(kept_not_last)} — переміг не останній "
            f"рядок (complete / final / не-partial / ts, ADR-0094); open_ms={kept_not_last[:3]}"
        )

    if dry_run:
        return (lines_in, lines_out, dupes)

    if dupes == 0 and parse_errors == 0:
        print(f"  -> no changes needed, skip rewrite")
        return (lines_in, lines_out, 0)

    # backup
    ts = int(time.time())
    backup = path.with_suffix(path.suffix + f".bak.{ts}")
    backup.write_bytes(path.read_bytes())
    print(f"  backup: {backup.name}")

    # atomic write
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for k in sorted_keys:
            f.write(json.dumps(by_open[k], separators=(",", ":"), ensure_ascii=False))
            f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    print(f"  -> rewritten ({lines_out} lines)")

    return (lines_in, lines_out, dupes)


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", help="Single JSONL file to dedup")
    g.add_argument("--glob", help="Glob pattern for multiple files")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    targets: list[Path] = []
    if args.file:
        targets = [Path(args.file)]
    else:
        targets = [Path(p) for p in glob.glob(args.glob)]

    if not targets:
        print(f"no files matched", file=sys.stderr)
        return 2

    print(
        f"=== {'DRY-RUN' if args.dry_run else 'COMMIT'} mode, {len(targets)} files ==="
    )
    total_in = total_out = total_dupes = 0
    for p in sorted(targets):
        i, o, d = dedup_file(p, dry_run=args.dry_run)
        total_in += i
        total_out += o
        total_dupes += d

    print(f"=== TOTAL: in={total_in} out={total_out} dupes_removed={total_dupes} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
