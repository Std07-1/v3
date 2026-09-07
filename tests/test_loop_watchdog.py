"""LoopWatchdog / arm_exit_timer — ADR-0054 §3.6 п.3 (інцидент 06.09.2026: sidecar завис у get_history)."""
from __future__ import annotations

import logging
import threading

import pytest

from runtime.ingest.loop_watchdog import (
    EXIT_CODE_SIGTERM_FORCED,
    EXIT_CODE_WATCHDOG_HANG,
    LoopWatchdog,
    arm_exit_timer,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_watchdog_idle_цикл_не_застряглий():
    clock = _Clock()
    wd = LoopWatchdog(timeout_s=10, clock=clock)
    clock.now += 1_000
    assert wd.in_flight() is False
    assert wd.stuck_for() is None


def test_watchdog_виклик_у_межах_таймауту_не_застряглий():
    clock = _Clock()
    wd = LoopWatchdog(timeout_s=10, clock=clock)
    wd.enter("fetch_m1")
    clock.now += 9.9
    assert wd.in_flight() is True
    assert wd.stuck_for() is None


def test_watchdog_виклик_довше_таймауту_застряглий_з_міткою():
    clock = _Clock()
    wd = LoopWatchdog(timeout_s=10, clock=clock)
    wd.enter("cmd:fetch_m1 XAG/USD")
    clock.now += 10.5
    stuck = wd.stuck_for()
    assert stuck is not None
    label, elapsed = stuck
    assert label == "cmd:fetch_m1 XAG/USD"
    assert elapsed == pytest.approx(10.5)


def test_watchdog_leave_знімає_стан_навіть_після_довгого_виклику():
    clock = _Clock()
    wd = LoopWatchdog(timeout_s=10, clock=clock)
    wd.enter("login")
    clock.now += 100
    wd.leave()
    assert wd.in_flight() is False
    assert wd.stuck_for() is None


def test_watchdog_повторний_enter_перезапускає_відлік():
    clock = _Clock()
    wd = LoopWatchdog(timeout_s=10, clock=clock)
    wd.enter("a")
    clock.now += 8
    wd.enter("b")
    clock.now += 8
    assert wd.stuck_for() is None  # 8 с від другого enter, не 16 від першого


def test_watchdog_потік_викликає_exit_75_при_зависанні():
    clock = _Clock()
    wd = LoopWatchdog(timeout_s=1, clock=clock)
    fired = threading.Event()
    codes = []

    def _exit(code: int) -> None:
        codes.append(code)
        fired.set()

    wd.enter("get_history")
    clock.now += 5  # уже застрягло до старту потоку
    wd.start_thread(exit_fn=_exit, poll_s=0.01, log=logging.getLogger("test"))
    assert fired.wait(2.0), "watchdog-потік не викликав exit_fn"
    assert codes == [EXIT_CODE_WATCHDOG_HANG]


def test_watchdog_потік_мовчить_поки_цикл_живий():
    clock = _Clock()
    wd = LoopWatchdog(timeout_s=1, clock=clock)
    codes = []
    wd.start_thread(exit_fn=codes.append, poll_s=0.01, log=logging.getLogger("test"))
    threading.Event().wait(0.1)
    assert codes == []


def test_watchdog_відхиляє_нульовий_таймаут():
    with pytest.raises(ValueError):
        LoopWatchdog(timeout_s=0)


def test_arm_exit_timer_спрацьовує_після_grace_з_кодом_143():
    fired = threading.Event()
    codes = []

    def _exit(code: int) -> None:
        codes.append(code)
        fired.set()

    timer = arm_exit_timer(0.05, reason="sig=15", exit_fn=_exit, log=logging.getLogger("test"))
    assert timer.daemon is True
    assert fired.wait(2.0), "grace-таймер не спрацював"
    assert codes == [EXIT_CODE_SIGTERM_FORCED]


def test_arm_exit_timer_можна_скасувати_якщо_цикл_вийшов_сам():
    codes = []
    timer = arm_exit_timer(0.2, reason="sig=15", exit_fn=codes.append, log=logging.getLogger("test"))
    timer.cancel()
    threading.Event().wait(0.3)
    assert codes == []
