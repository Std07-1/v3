from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from core.model.bars import CandleBar, assert_invariants, ms_to_utc_dt


def _d1_anchor_offsets(
    day_anchor_offset_s: int,
    day_anchor_offset_s_d1: Optional[int],
    day_anchor_offset_s_d1_alt: Optional[int],
) -> Tuple[int, Optional[int]]:
    primary = day_anchor_offset_s_d1 if day_anchor_offset_s_d1 is not None else day_anchor_offset_s
    alt = day_anchor_offset_s_d1_alt
    if alt is not None and alt == primary:
        alt = None
    return primary, alt


def _h4_anchor_offsets(
    day_anchor_offset_s: int,
    day_anchor_offset_s_alt: Optional[int],
    day_anchor_offset_s_alt2: Optional[int],
) -> Tuple[int, Optional[int], Optional[int]]:
    primary = day_anchor_offset_s
    alt = day_anchor_offset_s_alt
    if alt is not None and alt == primary:
        alt = None
    alt2 = day_anchor_offset_s_alt2
    if alt2 is not None and alt2 in (primary, alt):
        alt2 = None
    return primary, alt, alt2


def select_anchor_offset_for_open_ms(
    tf_s: int,
    open_time_ms: int,
    day_anchor_offset_s: int,
    day_anchor_offset_s_alt: Optional[int],
    day_anchor_offset_s_alt2: Optional[int],
    day_anchor_offset_s_d1: Optional[int],
    day_anchor_offset_s_d1_alt: Optional[int],
) -> int:
    if tf_s != 86400:
        tf_ms = tf_s * 1000
        primary, alt, alt2 = _h4_anchor_offsets(
            day_anchor_offset_s,
            day_anchor_offset_s_alt,
            day_anchor_offset_s_alt2,
        )
        for off in (primary, alt, alt2):
            if off is None:
                continue
            if (open_time_ms - off * 1000) % tf_ms == 0:
                return off
        return primary
    tf_ms = tf_s * 1000
    primary, alt = _d1_anchor_offsets(
        day_anchor_offset_s,
        day_anchor_offset_s_d1,
        day_anchor_offset_s_d1_alt,
    )
    for off in (primary, alt):
        if off is None:
            continue
        if (open_time_ms - off * 1000) % tf_ms == 0:
            return off
    return primary


class JsonlAppender:
    """Append-only JSONL writer із ротацією по даті open_time_utc (YYYYMMDD)."""

    def __init__(
        self,
        root: str,
        day_anchor_offset_s: int = 0,
        day_anchor_offset_s_d1: Optional[int] = None,
        day_anchor_offset_s_d1_alt: Optional[int] = None,
        day_anchor_offset_s_alt: Optional[int] = None,
        day_anchor_offset_s_alt2: Optional[int] = None,
    ) -> None:
        self._root = root
        self._open_files: Dict[str, Any] = {}
        self._day_anchor_offset_s = day_anchor_offset_s
        self._day_anchor_offset_s_d1 = day_anchor_offset_s_d1
        self._day_anchor_offset_s_d1_alt = day_anchor_offset_s_d1_alt
        self._day_anchor_offset_s_alt = day_anchor_offset_s_alt
        self._day_anchor_offset_s_alt2 = day_anchor_offset_s_alt2

    def _path_for(self, symbol: str, tf_s: int, open_time_ms: int) -> str:
        day = ms_to_utc_dt(open_time_ms).strftime("%Y%m%d")
        sym_dir = symbol.replace("/", "_")
        tf_dir = f"tf_{tf_s}"
        out_dir = os.path.join(self._root, sym_dir, tf_dir)
        os.makedirs(out_dir, exist_ok=True)
        return os.path.join(out_dir, f"part-{day}.jsonl")

    def append(self, bar: CandleBar) -> None:
        anchor_offset_s = select_anchor_offset_for_open_ms(
            bar.tf_s,
            bar.open_time_ms,
            self._day_anchor_offset_s,
            self._day_anchor_offset_s_alt,
            self._day_anchor_offset_s_alt2,
            self._day_anchor_offset_s_d1,
            self._day_anchor_offset_s_d1_alt,
        )
        assert_invariants(bar, anchor_offset_s=anchor_offset_s)
        path = self._path_for(bar.symbol, bar.tf_s, bar.open_time_ms)
        fh = self._open_files.get(path)
        if fh is None:
            fh = open(path, "a", encoding="utf-8")
            self._open_files[path] = fh
        line = json.dumps(bar.to_dict(), ensure_ascii=False, separators=(",", ":"))
        fh.write(line + "\n")
        fh.flush()

    def close(self) -> None:
        for fh in self._open_files.values():
            try:
                fh.close()
            except Exception:
                pass
        self._open_files.clear()


def tail_last_bar_time_ms(data_root: str, symbol: str, tf_s: int) -> Optional[int]:
    """Знаходить останній open_time_ms для (symbol,tf_s) через tail JSONL файлів.

    Мінімізує читання: беремо найновіший part-*.jsonl і читаємо його з кінця.
    """
    sym_dir = symbol.replace("/", "_")
    tf_dir = f"tf_{tf_s}"
    dir_path = os.path.join(data_root, sym_dir, tf_dir)
    if not os.path.isdir(dir_path):
        return None

    parts = [
        p
        for p in os.listdir(dir_path)
        if p.startswith("part-") and p.endswith(".jsonl")
    ]
    if not parts:
        return None
    parts.sort()  # YYYYMMDD => лексикографічно ок
    latest = os.path.join(dir_path, parts[-1])

    # Читаємо "хвіст" (останній валідний JSON рядок).
    try:
        with open(latest, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = 8192
            buf = b""
            pos = size
            while pos > 0:
                step = min(chunk, pos)
                pos -= step
                f.seek(pos)
                buf = f.read(step) + buf
                if b"\n" in buf:
                    break
            lines = buf.splitlines()
            for raw in reversed(lines):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw.decode("utf-8"))
                    return int(obj["open_time_ms"])
                except Exception:
                    continue
    except Exception:
        return None

    return None


def head_first_bar_time_ms(data_root: str, symbol: str, tf_s: int) -> Optional[int]:
    """Знаходить найперший open_time_ms для (symbol,tf_s) через head JSONL файлів."""
    sym_dir = symbol.replace("/", "_")
    tf_dir = f"tf_{tf_s}"
    dir_path = os.path.join(data_root, sym_dir, tf_dir)
    if not os.path.isdir(dir_path):
        return None

    parts = [
        p
        for p in os.listdir(dir_path)
        if p.startswith("part-") and p.endswith(".jsonl")
    ]
    if not parts:
        return None
    parts.sort()
    earliest = os.path.join(dir_path, parts[0])

    try:
        with open(earliest, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    return int(obj["open_time_ms"])
                except Exception:
                    continue
    except Exception:
        return None

    return None


def iter_day_keys_utc(start_ms: int, end_ms: int) -> List[str]:
    """Повертає список YYYYMMDD між start_ms та end_ms (UTC, включно)."""
    if end_ms < start_ms:
        return []
    start_day = ms_to_utc_dt(start_ms).date()
    end_day = ms_to_utc_dt(end_ms).date()
    out: List[str] = []
    cur = start_day
    while cur <= end_day:
        out.append(cur.strftime("%Y%m%d"))
        cur += dt.timedelta(days=1)
    return out


def load_day_open_times(
    data_root: str, symbol: str, tf_s: int, day: str
) -> set[int]:
    """Завантажує open_time_ms з part-YYYYMMDD.jsonl для (symbol, tf_s)."""
    sym_dir = symbol.replace("/", "_")
    tf_dir = f"tf_{tf_s}"
    path = os.path.join(data_root, sym_dir, tf_dir, f"part-{day}.jsonl")
    out: set[int] = set()
    if not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    out.add(int(obj["open_time_ms"]))
                except Exception:
                    continue
    except Exception:
        return out
    return out
