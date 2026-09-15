"""Одна сесія fetch: свіжий cwd SDK, дитина на пакет діб, розбір кожної доби, коміт у staging (ADR-0096 §3.3 B).

Батько не довіряє дитині: рядки кожної доби перевалідовуються (схема, прапорці, запит, лічильники) і лише тоді
атомарно стають добою staging. Будь-яка відмова лишає попередню валідну добу staging недоторканою. Доба, на якій
дитину вбили (дедлайн, таймаут батька, падіння), — невдала; доби, яких дитина не почала, повертаються в чергу.
Батька зупинили посеред сесії (сигнал, Ctrl+C, відмова) — доби, які дитина вже віддала, не викидаються: розбір і
коміт під знятими обробниками, сесія `stopped` — у маніфест прогону, потім виняток летить далі. Python 3.7.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import os
import shutil
import signal
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from tools.repair.first_tick_m1.common import (
    REPO_ROOT, day_key, log_event, request_window, session_timeout_s, sym_dir, utc_iso,
)
from tools.repair.first_tick_m1.fetch_child import EXIT_SDK_ERROR, SESSION_RESULT, day_files
from tools.repair.first_tick_m1.fetch_runner import ChildOutcome
from tools.repair.first_tick_m1.staging import StagingInvalid, validate_rows, write_day_atomic

CHILD_MODULE = "tools.repair.first_tick_m1.fetch_child"
# Код виходу дитини, убитої SIGALRM власного дедлайну (Popen: −signum); на Windows сигналу немає.
_DEADLINE_RETURNCODE = -int(getattr(signal, "SIGALRM", 0)) or None


@dataclasses.dataclass
class CallRecord:
    """Одна доба — один get_history у сесії `session`."""

    seq: int
    session: int
    day: str
    status: str  # ok | empty | invalid | child_error | deadline | timeout | unkillable | stopped
    duration_s: Optional[float] = None
    rows: Optional[int] = None
    raw_open_not_tick: Optional[int] = None
    sha256: Optional[str] = None
    detail: Optional[str] = None


@dataclasses.dataclass
class SessionRecord:
    """Один логін FXCM: доби пакета, чим закінчився процес дитини і які доби він не почав."""

    seq: int
    days: List[str]
    status: str  # ok | child_error | deadline | timeout | unkillable | stopped (батька зупинили посеред сесії)
    returncode: Optional[int]
    duration_s: float
    unattempted: List[str]
    detail: Optional[str] = None


@dataclasses.dataclass
class SessionOutcome:
    session: SessionRecord
    calls: List[CallRecord]
    unattempted: List[dt.date]


@dataclasses.dataclass(frozen=True)
class ParentStop:
    """Батька зупинили посеред сесії: зняти обробники (`disarm`) і записати сесію `stopped` у маніфест (`record`)."""

    disarm: Callable[[], None]
    record: Callable[[SessionOutcome], None]


def execute_session(ctx: Any, opts: Any, deps: Any, run_id: str, session_seq: int, first_call_seq: int,
                    days: List[dt.date], parent_stop: ParentStop) -> SessionOutcome:
    """Сесія пакета діб: staging змінюється лише для діб зі status ok; решта — записи для маніфесту прогону."""
    tag = "%s-s%04d" % (run_id, session_seq)
    call_dir = os.path.join(ctx.sdk_cwd, "session-" + tag)
    os.mkdir(call_dir)  # exist_ok=False: спільний кеш History/ між сесіями віддав би стару версію доби
    out_dir = os.path.join(ctx.staging_root, "_inflight", tag)
    log_dir = os.path.join(ctx.staging_root, "_runs", run_id)
    os.makedirs(out_dir)
    os.makedirs(log_dir, exist_ok=True)
    keys = [day_key(day) for day in days]
    argv = [deps.python_executable, "-u", "-m", CHILD_MODULE, "--symbol", opts.symbol, "--days", ",".join(keys),
            "--out-dir", out_dir, "--deadline-s", str(opts.call_timeout_s)]
    log_path = os.path.join(log_dir, "session-%04d-%s-%s-%s.log" % (session_seq, sym_dir(opts.symbol), keys[0],
                                                                    keys[-1]))
    outcome, started = None, time.monotonic()
    try:
        try:
            outcome = deps.run_child(argv, cwd=call_dir, env=child_env(),
                                     timeout_s=session_timeout_s(opts.call_timeout_s, len(days)), log_path=log_path)
        except BaseException as exc:
            # run_child уже вбив і дочекався дитину; отримані доби — у staging і маніфест, до прибирання out_dir.
            _salvage_stopped_session(ctx, opts, deps, run_id, session_seq, first_call_seq, days, out_dir,
                                     round(time.monotonic() - started, 3), exc, parent_stop)
            raise
        result = _resolve_session(ctx, opts, deps, run_id, session_seq, first_call_seq, days, outcome, out_dir)
    finally:
        if outcome is None or outcome.status != "unkillable":  # живому процесу теки не забираємо
            remove_path(call_dir)
            remove_path(out_dir)
    _log_session(opts, result)
    return result


def child_env() -> Dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + existing if existing else "")
    return env


def _salvage_stopped_session(ctx: Any, opts: Any, deps: Any, run_id: str, session_seq: int, first_call_seq: int,
                             days: List[dt.date], out_dir: str, duration_s: float, exc: BaseException,
                             parent_stop: ParentStop) -> None:
    """Батька зупинили посеред сесії: доби з result — тим самим розбором, що й завершена сесія, під знятими
    обробниками (другий сигнал не обриває коміт); сесія `stopped` — у маніфест. Відмова розбору не підміняє
    зупинку: вона гучна в лозі, а виняток зупинки летить далі у викликача."""
    parent_stop.disarm()
    try:
        outcome = _resolve_session(ctx, opts, deps, run_id, session_seq, first_call_seq, days,
                                   ChildOutcome("stopped", None, duration_s), out_dir)
        outcome.session.detail = "parent_stopped: %s: %s" % (type(exc).__name__, exc)
        _log_session(opts, outcome)
        parent_stop.record(outcome)
    except Exception as salvage_exc:
        log_event(logging.ERROR, "FT_FETCH_SESSION_SALVAGE_FAILED", symbol=opts.symbol, session=session_seq,
                  stop="%s: %s" % (type(exc).__name__, exc), err="%s: %s" % (type(salvage_exc).__name__, salvage_exc))


def _resolve_session(ctx: Any, opts: Any, deps: Any, run_id: str, session_seq: int, first_call_seq: int,
                     days: List[dt.date], outcome: Any, out_dir: str) -> SessionOutcome:
    session_result, problem = _read_json_object(os.path.join(out_dir, SESSION_RESULT))
    status, detail = _session_status(outcome, session_result, problem)
    calls: List[CallRecord] = []
    unattempted: List[dt.date] = []
    for index, day in enumerate(days):
        started_path, rows_path, result_path = day_files(out_dir, day_key(day))
        seq = first_call_seq + len(calls)
        if os.path.exists(result_path):
            calls.append(_resolve_day(ctx, opts, deps, run_id, session_seq, seq, day, rows_path, result_path))
            continue
        if os.path.exists(started_path):
            # Дитину вбили (чи вона впала) посеред цієї доби: доба невдала, наступна сесія почне з наступної.
            calls.append(CallRecord(seq, session_seq, day_key(day), "child_error" if status == "ok" else status,
                                    detail=detail or "result_missing"))
            unattempted = days[index + 1:]
        else:
            unattempted = days[index:]
        break
    record = SessionRecord(session_seq, [day_key(day) for day in days], status, outcome.returncode,
                           round(outcome.duration_s, 3), [day_key(day) for day in unattempted], detail)
    return SessionOutcome(record, calls, unattempted)


def _session_status(outcome: Any, session_result: Optional[Dict[str, Any]],
                    problem: Optional[str]) -> Tuple[str, Optional[str]]:
    """Чим закінчилась дитина: ok лише коли процес вийшов 0 і сам звітував ok."""
    if outcome.status == "stopped":
        return "stopped", "parent_stopped"
    if outcome.status in ("timeout", "unkillable"):
        return outcome.status, "session_timeout"
    if _DEADLINE_RETURNCODE is not None and outcome.returncode == _DEADLINE_RETURNCODE:
        return "deadline", "child_deadline_sigalrm"
    if outcome.returncode == 0 and session_result is not None and session_result.get("status") == "ok":
        return "ok", None
    if outcome.returncode == EXIT_SDK_ERROR and session_result is not None:
        return "child_error", "%s: %s" % (session_result.get("stage"), session_result.get("error"))
    return "child_error", problem or "rc=%s session=%s" % (outcome.returncode, (session_result or {}).get("status"))


def _resolve_day(ctx: Any, opts: Any, deps: Any, run_id: str, session_seq: int, seq: int, day: dt.date,
                 rows_path: str, result_path: str) -> CallRecord:
    key = day_key(day)
    record = CallRecord(seq, session_seq, key, "child_error")
    result, problem = _read_json_object(result_path)
    if result is None or result.get("day") != key:
        record.detail = problem or "result_day_mismatch"
        return record
    record.duration_s = result.get("call_duration_s")
    if result.get("status") in ("empty", "invalid"):
        record.status, record.detail = result["status"], result.get("error") or "0 рядків у добі"
        return record
    if result.get("status") != "ok":
        record.detail = result.get("error") or "status=%s" % result.get("status")
        return record
    try:
        rows = _read_inflight_rows(rows_path)
        flags = sum(1 for row in rows if isinstance(row, dict) and row.get("raw_open_not_tick") is True)
        if result.get("request") != request_window(day):
            raise StagingInvalid("request_mismatch", "result.request=%r" % (result.get("request"),))
        dropped = result.get("rows_outside_day_dropped")
        if (result.get("rows"), result.get("raw_open_not_tick")) != (len(rows), flags) or not _is_int(dropped):
            raise StagingInvalid("result_counts", "result=%r rows=%d flags=%d" % (result, len(rows), flags))
        validate_rows(opts.symbol, day, rows)
    except StagingInvalid as exc:
        record.status, record.detail = "invalid", str(exc)
        return record
    meta = {"request": result["request"], "rows_outside_day_dropped": dropped, "run_id": run_id,
            "fetched_at_utc": utc_iso(deps.now_ms()), "call_seq": seq, "call_duration_s": record.duration_s,
            "sdk": result.get("sdk")}
    manifest = write_day_atomic(ctx.staging_root, opts.symbol, day, rows, meta)
    record.status, record.rows, record.raw_open_not_tick, record.sha256 = "ok", len(rows), flags, manifest["sha256"]
    return record


def _log_session(opts: Any, outcome: SessionOutcome) -> None:
    for record in outcome.calls:
        if record.status == "ok":
            log_event(logging.INFO, "FT_FETCH_DAY_COMMITTED", symbol=opts.symbol, day=record.day, rows=record.rows,
                      open_not_tick=record.raw_open_not_tick, sha256=record.sha256, session=record.session)
        else:
            log_event(logging.WARNING, "FT_FETCH_DAY_FAILED", symbol=opts.symbol, day=record.day,
                      status=record.status, detail=record.detail, session=record.session)
    session = outcome.session
    log_event(logging.INFO if session.status == "ok" else logging.WARNING, "FT_FETCH_SESSION_DONE",
              symbol=opts.symbol, session=session.seq, status=session.status, rc=session.returncode,
              days=len(session.days), calls=len(outcome.calls), unattempted=",".join(session.unattempted) or None,
              detail=session.detail)


def _read_json_object(path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not os.path.exists(path):
        return None, "result_missing"
    try:
        with open(path, "rb") as fh:
            payload = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return None, "result_unreadable: %s" % exc
    return (payload, None) if isinstance(payload, dict) else (None, "result_not_object")


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


def remove_path(path: str) -> None:
    """Тека сесії чи залишок inflight (кеш SDK, незакомічені файли дитини): прибрати; невдача не змінює staging,
    але видима — наступний прогін прибере."""
    if not os.path.lexists(path):
        return
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    except OSError as exc:
        log_event(logging.WARNING, "FT_FETCH_SESSION_DIR_CLEANUP_FAILED", path=path, err=exc)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
