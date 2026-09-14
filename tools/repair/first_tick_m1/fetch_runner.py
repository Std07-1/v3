"""Дочірній процес з жорстким таймаутом — єдиний спосіб перервати нативний `get_history` (ADR-0054 §3.6). Python 3.7.

Потік тут не рятує: синхронний виклик SDK не віддає GIL-точок скасування, а `ForexConnect.get_history`
ще й крутить `while not self._com.is_ready: pass` без дедлайну. Процес у власній сесії (POSIX) вбивається
разом з усіма нащадками.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import signal
import subprocess
import time
from typing import Dict, Optional, Sequence

from tools.repair.first_tick_m1.common import KILL_WAIT_S, log_event


@dataclasses.dataclass(frozen=True)
class ChildOutcome:
    status: str  # "exited" | "timeout" | "unkillable"
    returncode: Optional[int]
    duration_s: float


def run_child(argv: Sequence[str], cwd: str, env: Dict[str, str], timeout_s: float, log_path: str) -> ChildOutcome:
    """Запустити дитину (stdout+stderr → log_path), дочекатись або вбити після timeout_s."""
    started = time.monotonic()
    popen_kwargs = {"start_new_session": True} if os.name == "posix" else {}
    with open(log_path, "ab") as log_fh:
        proc = subprocess.Popen(list(argv), cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log_fh,
                                stderr=subprocess.STDOUT, **popen_kwargs)
        try:
            returncode = proc.wait(timeout=timeout_s)
            return ChildOutcome("exited", returncode, time.monotonic() - started)
        except subprocess.TimeoutExpired:
            _kill(proc)
        try:
            proc.wait(timeout=KILL_WAIT_S)
        except subprocess.TimeoutExpired:
            log_event(logging.ERROR, "FT_FETCH_CHILD_UNKILLABLE", pid=proc.pid, timeout_s=timeout_s,
                      kill_wait_s=KILL_WAIT_S)
            return ChildOutcome("unkillable", None, time.monotonic() - started)
    log_event(logging.WARNING, "FT_FETCH_CHILD_TIMEOUT", pid=proc.pid, timeout_s=timeout_s, rc=proc.returncode)
    return ChildOutcome("timeout", proc.returncode, time.monotonic() - started)


def _kill(proc: "subprocess.Popen") -> None:
    if os.name != "posix":
        proc.kill()
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # type: ignore[attr-defined]
    except ProcessLookupError:
        # Дитина завершилась між таймаутом і kill — чекати нічого, wait нижче забере код виходу.
        log_event(logging.INFO, "FT_FETCH_CHILD_EXITED_BEFORE_KILL", pid=proc.pid)
