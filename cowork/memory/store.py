"""JSONL persistence for cowork published theses.

Append-only store (CW4). One file per month, monthly rotation:
  cowork/data/published_thesis-YYYYMM.jsonl

Ніяких HTTP / network / external SDK залежностей (CW2). Pure stdlib.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from cowork.memory.schema import PublishedThesis

log = logging.getLogger(__name__)

# Storage root — overridable via env for tests / VPS deploy.
DEFAULT_STORE_DIR: Path = Path(
    os.environ.get("COWORK_STORE_DIR", "cowork/data")
).resolve()
DEFAULT_STORE_PATH: Path = DEFAULT_STORE_DIR / "published_thesis.jsonl"

# Single-process append lock. Cowork is currently single-writer (one Claude
# Desktop task instance), but the lock guards against test parallelism and
# any future fan-in scenario.
_APPEND_LOCK = threading.Lock()


# ── Path helpers ──────────────────────────────────────────────────────────────


def _monthly_path(base_dir: Path, ts_iso: str) -> Path:
    """Resolve `published_thesis-YYYYMM.jsonl` for a given ISO timestamp."""
    # Tolerate trailing 'Z' which fromisoformat doesn't accept on <3.11
    ts_clean = ts_iso.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts_clean)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    yyyymm = dt.strftime("%Y%m")
    return base_dir / f"published_thesis-{yyyymm}.jsonl"


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


# ── Append / read API ─────────────────────────────────────────────────────────


def append_thesis(
    thesis: PublishedThesis,
    *,
    store_dir: Path | None = None,
) -> tuple[Path, bool]:
    """Append `thesis` as one JSONL line. Returns (path, was_duplicate).

    Idempotent on `scan_id` (CW6): if a record with same `scan_id` already exists
    in the current month's file, returns (path, True) without writing.

    Concurrent appends from same process are serialized via `_APPEND_LOCK`.
    Cross-process writes assume single-writer model (current cowork deployment).
    """
    base_dir = store_dir or DEFAULT_STORE_DIR
    target = _monthly_path(base_dir, thesis.ts)
    _ensure_dir(target)

    payload = thesis.to_jsonable()
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    with _APPEND_LOCK:
        if _scan_id_exists(target, thesis.scan_id):
            log.info(
                "cowork_thesis_duplicate scan_id=%s symbol=%s",
                thesis.scan_id,
                thesis.symbol,
            )
            return target, True

        with target.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")

    log.info(
        "cowork_thesis_appended scan_id=%s symbol=%s grade=%s direction=%s file=%s",
        thesis.scan_id,
        thesis.symbol,
        thesis.thesis_grade,
        thesis.preferred_direction,
        target.name,
    )
    return target, False


def _scan_id_exists(path: Path, scan_id: str) -> bool:
    """Linear scan of monthly file for duplicate scan_id (CW6).

    OK at current scale (~10-20 records/month per symbol). If volume grows,
    introduce per-month index sidecar (`published_thesis-YYYYMM.idx`).
    """
    if not path.exists():
        return False
    needle = f'"scan_id":"{scan_id}"'
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if needle in line:
                    return True
    except OSError as exc:
        log.warning("cowork_dedup_scan_err path=%s err=%s", path, exc)
    return False


def read_recent(
    symbol: str,
    *,
    limit: int = 3,
    max_age_h: int = 12,
    store_dir: Path | None = None,
    now: datetime | None = None,
) -> list[PublishedThesis]:
    """Return up to `limit` most recent theses for `symbol` within `max_age_h`.

    Reads current month + previous month (handles month boundary). Sorts by `ts`
    desc, filters by symbol + age, returns newest first.

    Returns [] if no matches — never raises on missing files (graceful for first run).
    """
    if limit <= 0:
        return []
    base_dir = store_dir or DEFAULT_STORE_DIR
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - (max_age_h * 3600)

    candidates = list(_iter_recent_files(base_dir, now))
    raw_records: list[tuple[float, dict]] = []
    for path in candidates:
        for record in _iter_jsonl(path):
            if record.get("symbol") != symbol:
                continue
            ts_str = record.get("ts")
            if not ts_str:
                continue
            try:
                rec_ts = datetime.fromisoformat(
                    ts_str.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                log.warning("cowork_read_bad_ts path=%s ts=%r", path.name, ts_str)
                continue
            if rec_ts < cutoff:
                continue
            raw_records.append((rec_ts, record))

    raw_records.sort(key=lambda x: x[0], reverse=True)
    out: list[PublishedThesis] = []
    for _, record in raw_records[:limit]:
        try:
            out.append(PublishedThesis.from_jsonable(record))
        except (KeyError, ValueError) as exc:
            log.warning(
                "cowork_read_skip_invalid scan_id=%r err=%s",
                record.get("scan_id"),
                exc,
            )
    return out


def read_by_scan_id(
    scan_id: str,
    *,
    store_dir: Path | None = None,
) -> PublishedThesis | None:
    """Locate a single thesis by `scan_id`. Scans last 3 months back."""
    base_dir = store_dir or DEFAULT_STORE_DIR
    now = datetime.now(timezone.utc)
    needle = f'"scan_id":"{scan_id}"'
    for path in _iter_recent_files(base_dir, now, months_back=3):
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    if needle not in line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("scan_id") == scan_id:
                        return PublishedThesis.from_jsonable(record)
        except OSError as exc:
            log.warning("cowork_lookup_err path=%s err=%s", path, exc)
    return None


# ── Internals ─────────────────────────────────────────────────────────────────


def _iter_recent_files(
    base_dir: Path,
    now: datetime,
    *,
    months_back: int = 2,
) -> Iterable[Path]:
    """Yield paths for current + N previous monthly files (newest first)."""
    year, month = now.year, now.month
    for _ in range(months_back):
        yield base_dir / f"published_thesis-{year:04d}{month:02d}.jsonl"
        month -= 1
        if month == 0:
            month = 12
            year -= 1


def _iter_jsonl(path: Path) -> Iterable[dict]:
    """Yield parsed records from a JSONL file. Skip malformed lines (loud)."""
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8") as f:
            for lineno, raw in enumerate(f, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError as exc:
                    log.warning(
                        "cowork_jsonl_parse_err path=%s line=%d err=%s",
                        path.name,
                        lineno,
                        exc,
                    )
    except OSError as exc:
        log.warning("cowork_jsonl_open_err path=%s err=%s", path, exc)
