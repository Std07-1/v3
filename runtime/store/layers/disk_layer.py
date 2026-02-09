from __future__ import annotations

import json
import os
from collections import deque
from typing import Any, Iterable, Optional


FINAL_SOURCES = {"history", "derived", "history_agg"}


def _iter_lines_reverse(path: str) -> Iterable[bytes]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            buf = b""
            chunk = 8192
            while pos > 0:
                step = min(chunk, pos)
                pos -= step
                f.seek(pos)
                buf = f.read(step) + buf
                while b"\n" in buf:
                    idx = buf.rfind(b"\n")
                    line = buf[idx + 1 :]
                    buf = buf[:idx]
                    yield line
            if buf:
                yield buf
    except Exception:
        return


def _read_jsonl_filtered(
    paths: list[str],
    since_open_ms: Optional[int],
    to_open_ms: Optional[int],
    limit: int,
) -> list[dict[str, Any]]:
    buf: deque[dict[str, Any]] = deque(maxlen=max(1, limit))
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
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

                    if since_open_ms is not None and open_ms <= since_open_ms:
                        continue
                    if to_open_ms is not None and open_ms > to_open_ms:
                        continue

                    buf.append(obj)
        except FileNotFoundError:
            continue

    out = list(buf)
    out.sort(key=lambda x: x.get("open_time_ms", 0))
    return out


def _needs_sort_by_open_ms(bars: list[dict[str, Any]]) -> bool:
    prev_open: Optional[int] = None
    for bar in bars:
        open_ms = bar.get("open_time_ms")
        if not isinstance(open_ms, int):
            continue
        if prev_open is not None and open_ms < prev_open:
            return True
        prev_open = open_ms
    return False


def _needs_dedup_by_open_ms(bars: list[dict[str, Any]]) -> bool:
    seen: set[int] = set()
    for bar in bars:
        open_ms = bar.get("open_time_ms")
        if not isinstance(open_ms, int):
            continue
        if open_ms in seen:
            return True
        seen.add(open_ms)
    return False


def _bar_is_complete(bar: dict[str, Any]) -> bool:
    val = bar.get("complete")
    return bool(val) if isinstance(val, bool) else bool(val)


def _bar_is_final_source(bar: dict[str, Any]) -> bool:
    src = bar.get("src")
    return isinstance(src, str) and src in FINAL_SOURCES


def _bar_ts_priority(bar: dict[str, Any]) -> Optional[int]:
    event_ts = bar.get("event_ts")
    if isinstance(event_ts, int):
        return event_ts
    ssot_write_ts_ms = bar.get("ssot_write_ts_ms")
    if isinstance(ssot_write_ts_ms, int):
        return ssot_write_ts_ms
    return None


def _choose_better_bar(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    existing_complete = _bar_is_complete(existing)
    incoming_complete = _bar_is_complete(incoming)
    if incoming_complete and not existing_complete:
        return incoming
    if existing_complete and not incoming_complete:
        return existing
    existing_final = _bar_is_final_source(existing)
    incoming_final = _bar_is_final_source(incoming)
    if incoming_final and not existing_final:
        return incoming
    if existing_final and not incoming_final:
        return existing
    existing_ts = _bar_ts_priority(existing)
    incoming_ts = _bar_ts_priority(incoming)
    if incoming_ts is not None and existing_ts is not None:
        if incoming_ts > existing_ts:
            return incoming
        if incoming_ts < existing_ts:
            return existing
    elif incoming_ts is not None and existing_ts is None:
        return incoming
    elif existing_ts is not None and incoming_ts is None:
        return existing
    return incoming


def _dedup_open_ms(bars: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    deduped: dict[int, dict[str, Any]] = {}
    dropped = 0
    for bar in bars:
        open_ms = bar.get("open_time_ms")
        if not isinstance(open_ms, int):
            continue
        existing = deduped.get(open_ms)
        if existing is None:
            deduped[open_ms] = bar
            continue
        deduped[open_ms] = _choose_better_bar(existing, bar)
        dropped += 1
    result = [deduped[k] for k in sorted(deduped.keys())]
    return result, dropped


def _read_jsonl_tail_filtered_with_geom(
    paths: list[str],
    since_open_ms: Optional[int],
    to_open_ms: Optional[int],
    limit: int,
) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
    if limit <= 0:
        return [], None
    out: list[dict[str, Any]] = []
    for p in reversed(paths):
        for raw in _iter_lines_reverse(p):
            if len(out) >= limit:
                break
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            open_ms = obj.get("open_time_ms")
            if not isinstance(open_ms, int):
                continue
            if to_open_ms is not None and open_ms > to_open_ms:
                continue
            if since_open_ms is not None and open_ms <= since_open_ms:
                out.reverse()
                return _finalize_tail_with_geom(out)
            out.append(obj)
        if len(out) >= limit:
            break
    out.reverse()
    return _finalize_tail_with_geom(out)


def _finalize_tail_with_geom(
    out: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
    needs_sort = _needs_sort_by_open_ms(out)
    needs_dedup = _needs_dedup_by_open_ms(out)
    if not needs_sort and not needs_dedup:
        return out, None
    out.sort(key=lambda x: x.get("open_time_ms", 0))
    deduped, dropped = _dedup_open_ms(out)
    geom = {"sorted": True, "dedup_dropped": dropped}
    return deduped, geom


def _read_jsonl_tail_filtered(
    paths: list[str],
    since_open_ms: Optional[int],
    to_open_ms: Optional[int],
    limit: int,
) -> list[dict[str, Any]]:
    out, _geom = _read_jsonl_tail_filtered_with_geom(
        paths,
        since_open_ms,
        to_open_ms,
        limit,
    )
    return out


def _read_last_jsonl(path: str) -> Optional[dict[str, Any]]:
    last_obj: Optional[dict[str, Any]] = None
    try:
        with open(path, encoding="utf-8") as f:
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
                last_obj = obj
    except FileNotFoundError:
        return None
    return last_obj


class DiskLayer:
    """Дисковий шар: читання JSONL SSOT."""

    def __init__(self, data_root: str) -> None:
        self._data_root = data_root

    def list_parts(self, symbol: str, tf_s: int) -> list[str]:
        d = os.path.join(self._data_root, symbol.replace("/", "_"), f"tf_{tf_s}")
        if not os.path.isdir(d):
            return []
        parts = [
            os.path.join(d, x)
            for x in os.listdir(d)
            if x.startswith("part-") and x.endswith(".jsonl")
        ]
        parts.sort()
        return parts

    def read_window(
        self,
        symbol: str,
        tf_s: int,
        limit: int,
        *,
        since_open_ms: Optional[int] = None,
        to_open_ms: Optional[int] = None,
        use_tail: bool = False,
    ) -> list[dict[str, Any]]:
        bars, _geom = self.read_window_with_geom(
            symbol,
            tf_s,
            limit,
            since_open_ms=since_open_ms,
            to_open_ms=to_open_ms,
            use_tail=use_tail,
        )
        return bars

    def read_window_with_geom(
        self,
        symbol: str,
        tf_s: int,
        limit: int,
        *,
        since_open_ms: Optional[int] = None,
        to_open_ms: Optional[int] = None,
        use_tail: bool = False,
    ) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
        parts = self.list_parts(symbol, tf_s)
        if not parts:
            return [], None
        if use_tail:
            return _read_jsonl_tail_filtered_with_geom(
                parts,
                since_open_ms,
                to_open_ms,
                limit,
            )
        return _read_jsonl_filtered(parts, since_open_ms, to_open_ms, limit), None

    def last_open_ms(self, symbol: str, tf_s: int) -> Optional[int]:
        parts = self.list_parts(symbol, tf_s)
        if not parts:
            return None
        last_obj = _read_last_jsonl(parts[-1])
        if not last_obj:
            return None
        open_ms = last_obj.get("open_time_ms")
        return int(open_ms) if isinstance(open_ms, int) else None

    def last_mtime_ms(self, symbol: str, tf_s: int) -> Optional[int]:
        parts = self.list_parts(symbol, tf_s)
        if not parts:
            return None
        try:
            ts = os.path.getmtime(parts[-1])
        except Exception:
            return None
        return int(ts * 1000)
