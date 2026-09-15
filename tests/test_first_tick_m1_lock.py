"""Лок plan_dir/staging first_tick_m1: покинутий лок цього host знімається сам, чинний — ні (ADR-0096 §3.3 B).

Навіщо. apply, убитий SIGKILL/OOM чи перезавантаженням, лишає `.apply.lock` — і rollback, заради якого оператор
прийшов, відмовляв ROLLBACK_LOCK_HELD без пояснення. Зняти можна лише доведено покинутий лок: той самий host і
процесу holder.pid немає. Живий pid, інший host (спільний диск) чи невідома ОС — відмова, рішення за оператором.
Перевірка pid ін'єктована (`common.pid_alive`); справжня — окремим тестом на POSIX. Python 3.7.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys

import pytest

from tools.repair.first_tick_m1 import common


def _write_lock(path, pid, host=None):
    holder = {"pid": pid, "host": socket.gethostname() if host is None else host, "started_at_utc": "x"}
    path.write_bytes(common.canonical_json_bytes(holder))
    return path.read_bytes()


def test_abandoned_lock_of_this_host_removed_loudly_and_taken(tmp_path, monkeypatch, caplog):
    lock = tmp_path / ".apply.lock"
    _write_lock(lock, 4242)
    monkeypatch.setattr(common, "pid_alive", lambda pid: False if pid == 4242 else True)
    with common.exclusive_lock(str(lock)):
        assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()
    assert not lock.exists()
    assert "FT_LOCK_STALE_REMOVED" in caplog.text and '"pid":4242' in caplog.text


@pytest.mark.parametrize("alive, host, reason", [
    (True, None, "pid_alive"), (False, "other-host.example", "other_host"), (None, None, "pid_liveness_unknown"),
])
def test_live_foreign_or_unprovable_lock_held_bytes_untouched(tmp_path, monkeypatch, alive, host, reason):
    lock = tmp_path / ".apply.lock"
    before = _write_lock(lock, 4242, host)
    monkeypatch.setattr(common, "pid_alive", lambda pid: alive)
    with pytest.raises(common.LockHeld) as caught:
        with common.exclusive_lock(str(lock)):
            pytest.fail("лок чинний — тіло не виконується")
    assert caught.value.reason == reason and lock.read_bytes() == before


def test_unparsable_holder_is_held(tmp_path, monkeypatch):
    lock = tmp_path / "_fetch.lock"
    lock.write_text('{"pid": 1}', encoding="utf-8")
    monkeypatch.setattr(common, "pid_alive", lambda pid: False)
    with pytest.raises(common.LockHeld) as caught:
        with common.exclusive_lock(str(lock)):
            pytest.fail("holder без host — не довести, що лок покинутий")
    assert caught.value.reason == "holder_unparsable" and lock.read_text(encoding="utf-8") == '{"pid": 1}'


@pytest.mark.skipif(os.name != "posix", reason="перевірка pid — POSIX (на Windows os.kill(pid, 0) завершує процес)")
def test_pid_alive_real_process_and_reaped_child():
    assert common.pid_alive(os.getpid()) is True
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert common.pid_alive(child.pid) is False
