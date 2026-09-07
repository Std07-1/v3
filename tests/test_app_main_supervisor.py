from __future__ import annotations

import pytest
from pathlib import Path
from types import SimpleNamespace

from app import main as supervisor_main


class _DummyProc:
    def __init__(self, pid: int, alive: bool = True):
        self.pid = pid
        self._alive = alive
        self.wait_timeout = None
        self.kill_called = False

    def poll(self):
        return None if self._alive else 0

    def wait(self, timeout=None):
        self.wait_timeout = timeout
        self._alive = False
        return 0

    def kill(self):
        self.kill_called = True
        self._alive = False


class _DummyHandle:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_kill_tree_uses_taskkill_on_windows(monkeypatch):
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(supervisor_main.os, "name", "nt")
    monkeypatch.setattr(supervisor_main.subprocess, "run", _fake_run)

    supervisor_main._kill_tree(1234)

    assert calls == [
        (
            ["taskkill", "/F", "/T", "/PID", "1234"],
            {
                "stdout": supervisor_main.subprocess.DEVNULL,
                "stderr": supervisor_main.subprocess.DEVNULL,
                "timeout": 10,
            },
        )
    ]


def test_kill_tree_терм_лише_прямій_дитині_на_posix(monkeypatch):
    """ADR-0054 §3.6 п.3: не killpg по власній групі (app.main гинув першим), а os.kill(pid)."""
    import signal

    kills = []
    killpg_calls = []

    monkeypatch.setattr(supervisor_main.os, "name", "posix")
    monkeypatch.setattr(
        supervisor_main.os, "kill", lambda pid, sig: kills.append((pid, sig)), raising=False
    )
    monkeypatch.setattr(
        supervisor_main.os, "killpg", lambda pgid, sig: killpg_calls.append(pgid), raising=False
    )

    supervisor_main._kill_tree(100)

    assert kills == [(100, signal.SIGTERM)]
    assert killpg_calls == []


def test_sigterm_handler_піднімає_keyboardinterrupt_і_ігнорує_повтор(monkeypatch):
    """SIGTERM має пройти тим самим шляхом, що Ctrl+C → finally → _terminate() дітей."""
    import signal

    installed = {}

    def _fake_signal(signum, handler):
        installed[signum] = handler

    monkeypatch.setattr(supervisor_main.os, "name", "posix")
    monkeypatch.setattr(signal, "signal", _fake_signal)

    supervisor_main._install_sigterm_handler()
    handler = installed[signal.SIGTERM]
    assert callable(handler)

    with pytest.raises(KeyboardInterrupt):
        handler(signal.SIGTERM, None)
    # повторний TERM під час cleanup — ігнорується, а не перериває _terminate()
    assert installed[signal.SIGTERM] is signal.SIG_IGN


def test_sigterm_handler_не_втручається_на_windows(monkeypatch):
    import signal

    called = []
    monkeypatch.setattr(supervisor_main.os, "name", "nt")
    monkeypatch.setattr(signal, "signal", lambda *a: called.append(a))

    supervisor_main._install_sigterm_handler()

    assert called == []


def test_terminate_kills_tree_and_closes_handles(monkeypatch):
    killed = []
    monkeypatch.setattr(supervisor_main, "_kill_tree", lambda pid: killed.append(pid))

    proc = _DummyProc(pid=4321, alive=True)
    stdout = _DummyHandle()
    stderr = _DummyHandle()
    item = supervisor_main.ChildProcess(
        label="worker",
        module="runtime.fake",
        proc=proc,
        stdout_handle=stdout,
        stderr_handle=stderr,
    )

    supervisor_main._terminate(item, timeout_s=7)

    assert killed == [4321]
    assert proc.wait_timeout == 7
    assert stdout.closed is True
    assert stderr.closed is True


def test_acquire_pid_lock_rewrites_stale_file(monkeypatch, tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    pid_path = log_dir / "supervisor.pid"
    pid_path.write_text("99999")

    def _fake_run(cmd, **kwargs):
        _ = (cmd, kwargs)
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(supervisor_main.os, "name", "nt")
    monkeypatch.setattr(supervisor_main.subprocess, "run", _fake_run)
    monkeypatch.setattr(supervisor_main.os, "getpid", lambda: 12345)

    ok = supervisor_main._acquire_pid_lock(log_dir)

    assert ok is True
    assert pid_path.read_text() == "12345"


def test_acquire_pid_lock_rejects_live_supervisor(monkeypatch, tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    pid_path = log_dir / "supervisor.pid"
    pid_path.write_text("54321")

    def _fake_run(cmd, **kwargs):
        _ = (cmd, kwargs)
        return SimpleNamespace(stdout="python.exe 54321 Console 1 10,000 K")

    monkeypatch.setattr(supervisor_main.os, "name", "nt")
    monkeypatch.setattr(supervisor_main.subprocess, "run", _fake_run)

    ok = supervisor_main._acquire_pid_lock(log_dir)

    assert ok is False
    assert pid_path.read_text() == "54321"
