"""Один виклик fetch: свіжий cwd SDK, дитина з таймаутом, розбір результату, коміт доби в staging. Python 3.7.

Батько не довіряє дитині: рядки inflight перевалідовуються (схема, прапорці, запит, лічильники) і лише тоді
атомарно стають добою staging. Будь-яка відмова лишає попередню валідну добу staging недоторканою.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import os
import shutil
from typing import Any, Dict, List, Optional, Tuple

from tools.repair.first_tick_m1.common import (
    REPO_ROOT, day_key, log_event, request_window, session_timeout_s, sym_dir, utc_iso,
)
from tools.repair.first_tick_m1.fetch_child import EXIT_EMPTY, EXIT_STAGING_INVALID
from tools.repair.first_tick_m1.staging import StagingInvalid, validate_rows, write_day_atomic

CHILD_MODULE = "tools.repair.first_tick_m1.fetch_child"


@dataclasses.dataclass
class CallRecord:
    seq: int
    day: str
    status: str  # ok | timeout | child_error | invalid | empty | unkillable
    returncode: Optional[int]
    duration_s: float
    rows: Optional[int] = None
    raw_open_not_tick: Optional[int] = None
    sha256: Optional[str] = None
    detail: Optional[str] = None


def execute_call(ctx: Any, opts: Any, deps: Any, run_id: str, seq: int, day: dt.date) -> CallRecord:
    """Виклик однієї доби: результат — запис для маніфесту прогону; staging змінюється лише при status ok."""
    tag = "%s-%04d" % (run_id, seq)
    call_dir = os.path.join(ctx.sdk_cwd, "call-" + tag)
    os.mkdir(call_dir)  # exist_ok=False: спільний кеш History/ між викликами віддав би стару версію доби
    inflight = os.path.join(ctx.staging_root, "_inflight")
    log_dir = os.path.join(ctx.staging_root, "_runs", run_id)
    for directory in (inflight, log_dir):
        os.makedirs(directory, exist_ok=True)
    out_path, result_path = os.path.join(inflight, tag + ".jsonl"), os.path.join(inflight, tag + ".result.json")
    argv = [deps.python_executable, "-u", "-m", CHILD_MODULE, "--symbol", opts.symbol, "--day", day_key(day),
            "--out", out_path, "--result", result_path, "--deadline-s", str(opts.call_timeout_s)]
    log_path = os.path.join(log_dir, "call-%04d-%s-%s.log" % (seq, sym_dir(opts.symbol), day_key(day)))
    outcome = None
    try:
        outcome = deps.run_child(argv, cwd=call_dir, env=child_env(), timeout_s=session_timeout_s(opts.call_timeout_s, 1),
                                 log_path=log_path)
        record = CallRecord(seq, day_key(day), outcome.status, outcome.returncode, round(outcome.duration_s, 3))
        if outcome.status == "exited":
            _resolve_exited(record, ctx, opts, deps, run_id, day, out_path, result_path)
    finally:
        if outcome is None or outcome.status != "unkillable":  # живому процесу теку не забираємо
            _remove_tree(call_dir)
        for path in (out_path, result_path):
            remove_inflight(path)
    if record.status == "ok":
        log_event(logging.INFO, "FT_FETCH_DAY_COMMITTED", symbol=opts.symbol, day=record.day, rows=record.rows,
                  open_not_tick=record.raw_open_not_tick, sha256=record.sha256)
    else:
        log_event(logging.WARNING, "FT_FETCH_DAY_FAILED", symbol=opts.symbol, day=record.day, status=record.status,
                  rc=record.returncode, detail=record.detail)
    return record


def child_env() -> Dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + existing if existing else "")
    return env


def _resolve_exited(record: CallRecord, ctx: Any, opts: Any, deps: Any, run_id: str, day: dt.date, out_path: str,
                    result_path: str) -> None:
    result, problem = _read_result(result_path)
    if record.returncode == EXIT_EMPTY:
        record.status, record.detail = "empty", "0 рядків у добі"
        return
    if record.returncode == EXIT_STAGING_INVALID:
        record.status, record.detail = "invalid", (result or {}).get("error") or problem
        return
    if record.returncode != 0 or result is None or result.get("status") != "ok":
        record.status = "child_error"
        record.detail = (result or {}).get("error") or problem or "rc=%s status=%s" % (
            record.returncode, (result or {}).get("status"))
        return
    try:
        rows = _read_inflight_rows(out_path)
        flags = sum(1 for row in rows if isinstance(row, dict) and row.get("raw_open_not_tick") is True)
        if result.get("request") != request_window(day):
            raise StagingInvalid("request_mismatch", "result.request=%r" % (result.get("request"),))
        dropped = result.get("rows_outside_day_dropped")
        if (result.get("rows"), result.get("raw_open_not_tick")) != (len(rows), flags) or not _is_int(dropped):
            raise StagingInvalid("result_counts", "result=%r rows=%d flags=%d" % (result, len(rows), flags))
        validate_rows(opts.symbol, day, rows)
    except StagingInvalid as exc:
        record.status, record.detail = "invalid", str(exc)
        return
    meta = {"request": result["request"], "rows_outside_day_dropped": dropped, "run_id": run_id,
            "fetched_at_utc": utc_iso(deps.now_ms()), "call_seq": record.seq, "call_duration_s": record.duration_s,
            "sdk": result.get("sdk")}
    manifest = write_day_atomic(ctx.staging_root, opts.symbol, day, rows, meta)
    record.status, record.rows, record.raw_open_not_tick, record.sha256 = "ok", len(rows), flags, manifest["sha256"]


def _read_result(path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not os.path.exists(path):
        return None, "result_missing"
    try:
        with open(path, "rb") as fh:
            result = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return None, "result_unreadable: %s" % exc
    return (result, None) if isinstance(result, dict) else (None, "result_not_object")


def _read_inflight_rows(path: str) -> List[Any]:
    if not os.path.exists(path):
        raise StagingInvalid("inflight_missing", path)
    with open(path, "rb") as fh:
        raw = fh.read()
    if not raw.endswith(b"\n") or b"\r" in raw:
        raise StagingInvalid("not_canonical", "inflight %s" % path)
    try:
        return [json.loads(text) for text in raw.decode("utf-8").split("\n")[:-1]]
    except (UnicodeDecodeError, ValueError) as exc:
        raise StagingInvalid("schema", "inflight %s: %s" % (path, exc))


def remove_inflight(path: str) -> None:
    """Незакомічений файл дитини: прибрати; невдача не змінює staging, але видима (наступний прогін прибере)."""
    if not os.path.exists(path):
        return
    try:
        os.remove(path)
    except OSError as exc:
        log_event(logging.WARNING, "FT_FETCH_INFLIGHT_CLEANUP_FAILED", path=path, err=exc)


def _remove_tree(path: str) -> None:
    try:
        shutil.rmtree(path)
    except OSError as exc:
        log_event(logging.WARNING, "FT_FETCH_CALL_DIR_CLEANUP_FAILED", path=path, err=exc)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
