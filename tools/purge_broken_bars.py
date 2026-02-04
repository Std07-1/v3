from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from typing import Tuple


def parse_iso_utc(s: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def daterange_key(d: dt.datetime) -> str:
    return d.strftime("%Y%m%d")


def clamp_range(start: dt.datetime, end: dt.datetime) -> Tuple[dt.datetime, dt.datetime]:
    if end <= start:
        raise ValueError("end_before_start")
    return start, end


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="./data_v3")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tf", type=int, required=True)
    ap.add_argument("--start-utc", required=True)
    ap.add_argument("--end-utc", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    start_dt = parse_iso_utc(args.start_utc)
    end_dt = parse_iso_utc(args.end_utc)
    start_dt, end_dt = clamp_range(start_dt, end_dt)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    sym_dir = args.symbol.replace("/", "_")
    tf_dir = f"tf_{args.tf}"
    base_dir = os.path.join(args.root, sym_dir, tf_dir)
    if not os.path.isdir(base_dir):
        print(f"missing_dir={base_dir}")
        return 2

    start_key = daterange_key(start_dt)
    end_key = daterange_key(end_dt - dt.timedelta(milliseconds=1))

    total_removed = 0
    total_kept = 0
    total_files = 0

    for name in sorted(os.listdir(base_dir)):
        if not (name.startswith("part-") and name.endswith(".jsonl")):
            continue
        day_key = name[5:13]
        if day_key < start_key or day_key > end_key:
            continue

        path = os.path.join(base_dir, name)
        total_files += 1
        removed = 0
        kept_lines = []

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    kept_lines.append(line)
                    continue
                open_ms = obj.get("open_time_ms")
                if isinstance(open_ms, int) and start_ms <= open_ms < end_ms:
                    removed += 1
                    continue
                kept_lines.append(line)

        total_removed += removed
        total_kept += len(kept_lines)

        if args.dry_run:
            print(f"DRY_RUN file={name} removed={removed} kept={len(kept_lines)}")
            continue

        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            for line in kept_lines:
                f.write(line + "\n")
        os.replace(tmp_path, path)
        print(f"file={name} removed={removed} kept={len(kept_lines)}")

    print(
        f"done files={total_files} removed={total_removed} kept={total_kept} range={start_dt.isoformat()}..{end_dt.isoformat()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
