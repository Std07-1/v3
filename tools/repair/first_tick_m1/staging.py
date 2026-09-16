"""Staging сирих рядків FXCM FIRST_TICK поза data_v3: формат доби, валідація, атомарний запис (ADR-0096 §3.3 B).

Доба = `day-YYYYMMDD.jsonl` (сирі рядки SDK, строго за зростанням open_time_ms) + маніфест із sha файла.
Рядок у staging — рівно те, що віддав брокер (без нормалізації): лише так план бачить «запечений» open поза
[low, high] (§1.4). Python 3.7.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import math
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from runtime.ingest.broker.fxcm.provider import (
    OPEN_PRICE_MODE_NAME,
    RAW_ROW_FIELDS,
    extract_ohlc,
    is_open_outside_range,
)
from tools.repair.first_tick_m1.common import (
    DAY_MS, MINUTE_MS, TF_S, TOOL_VERSION, day_key, day_start_ms, read_json, sha256_bytes, sha256_file, sym_dir,
    write_json_atomic,
)

# v2 — доба несе покриття: торгові хвилини календаря (`trading_minutes_expected`) і частку отриманих
# (`coverage`). Без цього обрізана відповідь SDK (`date_from` + `quotes_count=-1` віддала лише хвіст) комітилась
# як валідна доба, і `--only-missing` більше ніколи її не перезабирав.
STAGING_DAY_FORMAT = "ft_m1_staging_day_v2"
ROW_KEYS = ("open_time_ms",) + tuple(RAW_ROW_FIELDS) + ("raw_open_not_tick",)
_PRICE_FIELDS = tuple(field for field in RAW_ROW_FIELDS if field != "Volume")


class StagingInvalid(ValueError):
    """Доба staging не придатна для плану; `reason` — стабільний код, `detail` — для людини."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__("%s: %s" % (reason, detail))
        self.reason = reason
        self.detail = detail


@dataclasses.dataclass(frozen=True)
class StagedDay:
    rows: Tuple[Dict[str, Any], ...]
    manifest: Dict[str, Any]
    sha256_file: str
    sha256_manifest: str


def day_paths(staging_root: Any, symbol: str, day: dt.date) -> Tuple[str, str]:
    base = os.path.join(str(staging_root), sym_dir(symbol), "tf_60")
    key = day_key(day)
    return os.path.join(base, "day-%s.jsonl" % key), os.path.join(base, "day-%s.manifest.json" % key)


def open_not_tick(row: Dict[str, Any]) -> bool:
    """Прапорець рядка — тим самим предикатом, що й live (`provider.is_open_outside_range`), на Bid-цінах."""
    o, h, low, _close = extract_ohlc(row)
    return is_open_outside_range(o, h, low)


def rows_from_raw(raw_rows: Iterable[Dict[str, Any]], day: dt.date) -> Tuple[List[Dict[str, Any]], int]:
    """Рядки провайдера → рядки staging доби D; рядки поза [D, D+1d) відкидаються і рахуються, дублікати лишаються."""
    start = day_start_ms(day)
    rows, dropped = [], 0
    for raw in raw_rows:
        try:
            row = {key: raw[key] for key in ROW_KEYS[:-1]}
            if not _is_int(row["open_time_ms"]):
                raise TypeError("open_time_ms=%r" % (row["open_time_ms"],))
            row["raw_open_not_tick"] = open_not_tick(row)
        except (KeyError, TypeError, ValueError) as exc:
            raise StagingInvalid("schema", "сирий рядок брокера: %s: %s" % (type(exc).__name__, exc))
        if not start <= row["open_time_ms"] < start + DAY_MS:
            dropped += 1
            continue
        rows.append(row)
    rows.sort(key=lambda row: row["open_time_ms"])
    return rows, dropped


def validate_rows(symbol: str, day: dt.date, rows: List[Dict[str, Any]]) -> None:
    """Порушення — StagingInvalid з reason: empty|schema|misaligned|outside_day|flag_mismatch|duplicate|unsorted."""
    if not rows:
        raise StagingInvalid("empty", "0 рядків symbol=%s day=%s" % (symbol, day_key(day)))
    start, previous = day_start_ms(day), None
    for index, row in enumerate(rows):
        _check_schema(index, row)
        open_ms = row["open_time_ms"]
        if open_ms % MINUTE_MS:
            raise StagingInvalid("misaligned", "row=%d open_time_ms=%d" % (index, open_ms))
        if not start <= open_ms < start + DAY_MS:
            raise StagingInvalid("outside_day", "row=%d open_time_ms=%d day=%s" % (index, open_ms, day_key(day)))
        if row["raw_open_not_tick"] != open_not_tick(row):
            raise StagingInvalid("flag_mismatch", "row=%d open_time_ms=%d" % (index, open_ms))
        if previous is not None and open_ms == previous:
            raise StagingInvalid("duplicate", "row=%d open_time_ms=%d" % (index, open_ms))
        if previous is not None and open_ms < previous:
            raise StagingInvalid("unsorted", "row=%d open_time_ms=%d prev=%d" % (index, open_ms, previous))
        previous = open_ms


def rows_bytes(rows: Iterable[Dict[str, Any]]) -> bytes:
    lines = [json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) for row in rows]
    return "".join(line + "\n" for line in lines).encode("utf-8")


def write_day_atomic(staging_root: Any, symbol: str, day: dt.date, rows: List[Dict[str, Any]],
                     meta: Dict[str, Any]) -> Dict[str, Any]:
    """Файл доби (.tmp + fsync + os.replace), потім маніфест із його sha. Крах між ними — sha_mismatch при читанні."""
    validate_rows(symbol, day, rows)
    day_file, manifest_file = day_paths(staging_root, symbol, day)
    os.makedirs(os.path.dirname(day_file), exist_ok=True)
    body = rows_bytes(rows)
    tmp = day_file + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, day_file)
    manifest = dict(
        format=STAGING_DAY_FORMAT, tool_version=TOOL_VERSION, symbol=symbol, tf_s=TF_S, day=day_key(day),
        open_price_mode=OPEN_PRICE_MODE_NAME, request=meta["request"], rows=len(rows),
        rows_outside_day_dropped=meta["rows_outside_day_dropped"],
        raw_open_not_tick=sum(1 for row in rows if row["raw_open_not_tick"]),
        trading_minutes_expected=meta["trading_minutes_expected"],
        coverage=(round(len(rows) / meta["trading_minutes_expected"], 6)
                  if meta["trading_minutes_expected"] else None),
        attempts=meta["attempts"],
        first_open_ms=rows[0]["open_time_ms"], last_open_ms=rows[-1]["open_time_ms"], sha256=sha256_bytes(body),
        bytes=len(body), fetched_at_utc=meta["fetched_at_utc"], run_id=meta["run_id"], call_seq=meta["call_seq"],
        call_duration_s=meta["call_duration_s"], sdk=meta["sdk"],
    )
    write_json_atomic(manifest_file, manifest)
    return manifest


def load_day(staging_root: Any, symbol: str, day: dt.date) -> Optional[StagedDay]:
    """Валідна доба staging, None — доби немає зовсім, StagingInvalid — є, але не придатна."""
    day_file, manifest_file = day_paths(staging_root, symbol, day)
    has_file, has_manifest = os.path.exists(day_file), os.path.exists(manifest_file)
    if not has_file and not has_manifest:
        return None
    if not (has_file and has_manifest):
        raise StagingInvalid("half_missing", "file=%s manifest=%s" % (has_file, has_manifest))
    try:
        manifest = read_json(manifest_file)
    except (ValueError, UnicodeDecodeError) as exc:
        raise StagingInvalid("manifest_corrupt", str(exc))
    if not isinstance(manifest, dict) or (manifest.get("format"), manifest.get("tool_version")) != (
            STAGING_DAY_FORMAT, TOOL_VERSION):
        raise StagingInvalid("format", "manifest=%s" % manifest_file)
    if (manifest.get("symbol"), manifest.get("tf_s"), manifest.get("day")) != (symbol, TF_S, day_key(day)):
        raise StagingInvalid("identity", "manifest=%s" % manifest_file)
    if manifest.get("open_price_mode") != OPEN_PRICE_MODE_NAME:
        raise StagingInvalid("mode", "open_price_mode=%r" % (manifest.get("open_price_mode"),))
    if not _is_int(manifest.get("trading_minutes_expected")) or not _is_int(manifest.get("attempts")):
        raise StagingInvalid("coverage_fields", "manifest=%s" % manifest_file)
    with open(day_file, "rb") as fh:
        raw = fh.read()
    if sha256_bytes(raw) != manifest.get("sha256"):
        raise StagingInvalid("sha_mismatch", "file=%s" % day_file)
    problem = _canonical_problem(raw)
    if problem:
        raise StagingInvalid("not_canonical", problem)
    try:
        rows = [json.loads(line) for line in raw.decode("utf-8").split("\n")[:-1]]
    except (UnicodeDecodeError, ValueError) as exc:
        raise StagingInvalid("schema", "file=%s: %s" % (day_file, exc))
    flags = sum(1 for row in rows if isinstance(row, dict) and row.get("raw_open_not_tick") is True)
    if (manifest.get("rows"), manifest.get("raw_open_not_tick")) != (len(rows), flags):
        raise StagingInvalid("counts", "manifest rows/flags != file")
    validate_rows(symbol, day, rows)
    if rows_bytes(rows) != raw:
        raise StagingInvalid("not_canonical", "serialization")
    return StagedDay(tuple(rows), manifest, sha256_bytes(raw), sha256_file(manifest_file))


def _check_schema(index: int, row: Any) -> None:
    ok = isinstance(row, dict) and tuple(row) == ROW_KEYS and _is_int(row["open_time_ms"])
    ok = ok and all(isinstance(row[f], float) and math.isfinite(row[f]) and row[f] > 0 for f in _PRICE_FIELDS)
    ok = ok and _is_int(row["Volume"]) and row["Volume"] >= 0 and isinstance(row["raw_open_not_tick"], bool)
    if not ok:
        raise StagingInvalid("schema", "row=%d %r" % (index, row))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _canonical_problem(raw: bytes) -> Optional[str]:
    if b"\r" in raw:
        return "crlf"
    if raw and not raw.endswith(b"\n"):
        return "no_final_newline"
    if raw.startswith(b"\n") or b"\n\n" in raw:
        return "blank_line"
    return None
