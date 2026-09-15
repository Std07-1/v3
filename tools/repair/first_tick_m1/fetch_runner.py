"""Дочірній процес з жорстким таймаутом — єдиний спосіб перервати нативний `get_history` (ADR-0054 §3.6). Python 3.7.

Потік тут не рятує: синхронний виклик SDK не віддає GIL-точок скасування, а `ForexConnect.get_history`
ще й крутить `while not self._com.is_ready: pass` без дедлайну. Процес у власній сесії (POSIX) вбивається
разом з усіма нащадками.

Дитина не має права пережити батька з відкритою сесією FXCM. Три рубежі: дедлайн усередині самої дитини
(`fetch_child.ProcessDeadline`, SIGALRM), на Linux — `PR_SET_PDEATHSIG=SIGKILL` (ядро вбиває дитину, щойно помер
батько, навіть від SIGKILL), і в батька — будь-яка зупинка посеред очікування (Ctrl+C, SIGTERM/SIGHUP як
StopSignal, баг) спершу вбиває дитину, потім летить далі.
"""

from __future__ import annotations

import ctypes
import dataclasses
import functools
import logging
import os
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Dict, Optional, Sequence

from tools.repair.first_tick_m1.common import KILL_WAIT_S, log_event

PR_SET_PDEATHSIG = 1  # <linux/prctl.h>


@dataclasses.dataclass(frozen=True)
class ChildOutcome:
    status: str  # "exited" | "timeout" | "unkillable"; "stopped" — лише fetch_call: батька зупинили посеред очікування
    returncode: Optional[int]
    duration_s: float


def run_child(argv: Sequence[str], cwd: str, env: Dict[str, str], timeout_s: float, log_path: str) -> ChildOutcome:
    """Запустити дитину (stdout+stderr → log_path), дочекатись або вбити після timeout_s."""
    started = time.monotonic()
    popen_kwargs: Dict[str, Any] = {"start_new_session": True} if os.name == "posix" else {}
    preexec = parent_death_preexec()
    if preexec is not None:
        popen_kwargs["preexec_fn"] = preexec
    with open(log_path, "ab") as log_fh:
        proc = subprocess.Popen(list(argv), cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log_fh,
                                stderr=subprocess.STDOUT, **popen_kwargs)
        try:
            returncode = proc.wait(timeout=timeout_s)
            return ChildOutcome("exited", returncode, time.monotonic() - started)
        except subprocess.TimeoutExpired:
            _kill(proc)
        except BaseException:
            # Батька зупиняють посеред очікування: дитина з живою сесією FXCM не лишається сиротою в get_history.
            log_event(logging.ERROR, "FT_FETCH_CHILD_KILLED_PARENT_STOPPING", pid=proc.pid)
            _kill(proc)
            _reap(proc, timeout_s)
            raise
        if not _reap(proc, timeout_s):
            return ChildOutcome("unkillable", None, time.monotonic() - started)
    log_event(logging.WARNING, "FT_FETCH_CHILD_TIMEOUT", pid=proc.pid, timeout_s=timeout_s, rc=proc.returncode)
    return ChildOutcome("timeout", proc.returncode, time.monotonic() - started)


def parent_death_preexec() -> Optional[Callable[[], None]]:
    """`preexec_fn` для Popen: на Linux дитина отримає SIGKILL, щойно помре батько; поза Linux — None (видно в лозі)."""
    prctl = _load_prctl()
    if prctl is None:
        return None
    parent_pid = os.getpid()

    def set_parent_death_signal() -> None:
        if prctl(PR_SET_PDEATHSIG, int(signal.SIGKILL), 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG) відмовив")
        if os.getppid() != parent_pid:  # батько помер між fork і prctl — сигналу смерті вже не буде
            os._exit(1)

    return set_parent_death_signal


@functools.lru_cache(maxsize=None)
def _load_prctl() -> Optional[Callable[..., int]]:
    """prctl з libc (один раз на процес); недоступний — лише дедлайн дитини і kill батька, це видно в лозі."""
    if not sys.platform.startswith("linux"):
        log_event(logging.WARNING, "FT_FETCH_PDEATHSIG_UNAVAILABLE", platform=sys.platform)
        return None
    try:
        return ctypes.CDLL(None, use_errno=True).prctl
    except (OSError, AttributeError) as exc:
        log_event(logging.WARNING, "FT_FETCH_PDEATHSIG_UNAVAILABLE", err="%s: %s" % (type(exc).__name__, exc))
        return None


def _reap(proc: "subprocess.Popen", timeout_s: float) -> bool:
    try:
        proc.wait(timeout=KILL_WAIT_S)
        return True
    except subprocess.TimeoutExpired:
        log_event(logging.ERROR, "FT_FETCH_CHILD_UNKILLABLE", pid=proc.pid, timeout_s=timeout_s,
                  kill_wait_s=KILL_WAIT_S)
        return False


def _kill(proc: "subprocess.Popen") -> None:
    if os.name != "posix":
        proc.kill()
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # type: ignore[attr-defined]
    except ProcessLookupError:
        # Дитина завершилась між таймаутом і kill — чекати нічого, wait забере код виходу.
        log_event(logging.INFO, "FT_FETCH_CHILD_EXITED_BEFORE_KILL", pid=proc.pid)
