# tools/diag/disk_max_open_ms.py
"""
Діагностика SSOT JSONL: пошук max open_time_ms і підозрілих "майбутніх" барів.

Використання:
  python -m tools.diag.disk_max_open_ms --data-root ./data_v3 --future-skew-s 172800
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


_OPEN_KEYS = ("open_time_ms", "open_ms", "t_open_ms", "openTimeMs")


def _get_open_ms(row: Dict[str, Any]) -> Optional[int]:
    for k in _OPEN_KEYS:
        v = row.get(k)
        if v is None:
            continue
        try:
            return int(v)
        except Exception:
            return None
    return None


@dataclass(frozen=True)
class MaxHit:
    open_ms: int
    file_path: str
    line_no: int


def _iter_jsonl_lines(path: str) -> Iterable[Tuple[int, Dict[str, Any]]]:
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield i, json.loads(line)
            except Exception:
                # Не валимося — це діагностика.
                continue


def _list_part_files(tf_dir: str) -> List[str]:
    files: List[str] = []
    for name in os.listdir(tf_dir):
        if name.startswith("part-") and name.endswith(".jsonl"):
            files.append(os.path.join(tf_dir, name))
    files.sort()
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--future-skew-s", type=int, default=172800)  # 2 дні
    ap.add_argument("--tail-files", type=int, default=0, help="0 = всі part-файли, інакше лише останні N")
    args = ap.parse_args()

    now_ms = int(time.time() * 1000)
    future_cutoff_ms = now_ms + args.future_skew_s * 1000

    data_root = os.path.abspath(args.data_root)
    if not os.path.isdir(data_root):
        print(f"ERROR: data_root не є директорією: {data_root}")
        return 2

    sym_dirs = [os.path.join(data_root, d) for d in os.listdir(data_root) if os.path.isdir(os.path.join(data_root, d))]
    sym_dirs.sort()

    any_hits = False

    for sym_dir in sym_dirs:
        sym = os.path.basename(sym_dir)
        tf_dirs = [os.path.join(sym_dir, d) for d in os.listdir(sym_dir) if d.startswith("tf_") and os.path.isdir(os.path.join(sym_dir, d))]
        tf_dirs.sort()

        for tf_dir in tf_dirs:
            m = re.match(r".*tf_(\d+)$", os.path.basename(tf_dir))
            tf_s = m.group(1) if m else "?"
            part_files = _list_part_files(tf_dir)
            if args.tail_files and len(part_files) > args.tail_files:
                part_files = part_files[-args.tail_files :]

            max_hit: Optional[MaxHit] = None
            future_hits: List[MaxHit] = []

            for p in part_files:
                for line_no, row in _iter_jsonl_lines(p):
                    open_ms = _get_open_ms(row)
                    if open_ms is None:
                        continue
                    if (max_hit is None) or (open_ms > max_hit.open_ms):
                        max_hit = MaxHit(open_ms=open_ms, file_path=p, line_no=line_no)
                    if open_ms > future_cutoff_ms:
                        future_hits.append(MaxHit(open_ms=open_ms, file_path=p, line_no=line_no))

            if max_hit is None:
                continue

            any_hits = True
            print(f"SSOT_MAX symbol={sym} tf_s={tf_s} max_open_ms={max_hit.open_ms} file={os.path.basename(max_hit.file_path)} line={max_hit.line_no}")

            if future_hits:
                future_hits.sort(key=lambda h: h.open_ms, reverse=True)
                top = future_hits[0]
                print(
                    f"SSOT_FUTURE_BAR symbol={sym} tf_s={tf_s} open_ms={top.open_ms} "
                    f"file={os.path.basename(top.file_path)} line={top.line_no} cutoff_ms={future_cutoff_ms}"
                )

    if not any_hits:
        print("WARN: не знайдено жодного бару у SSOT під data_root")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
