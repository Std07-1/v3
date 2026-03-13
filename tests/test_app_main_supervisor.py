from __future__ import annotations

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


def test_kill_tree_uses_killpg_on_posix(monkeypatch):
    calls = []

    monkeypatch.setattr(supervisor_main.os, "name", "posix")
    monkeypatch.setattr(
        supervisor_main.os, "getpgid", lambda pid: pid + 10, raising=False
    )
    monkeypatch.setattr(
        supervisor_main.os,
        "killpg",
        lambda pgid, sig: calls.append((pgid, sig)),
        raising=False,
    )

    supervisor_main._kill_tree(100)

    assert len(calls) == 1
    assert calls[0][0] == 110


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
