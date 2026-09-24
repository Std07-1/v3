"""Доказ зупинених записувачів SSOT (ADR-0095 S7.0 / ADR-0098 §3.6, `tools.repair.partfile_io.writers_guard`).

На прод-каталозі заміна part-файлів можлива лише тоді, коли /proc доводить: немає процесу-записувача за argv і
немає FD part-каталогу TF, відкритого на запис. Копія даних (репетиція) — гучний пропуск.
"""
from __future__ import annotations

import logging
import os

import pytest

from tools.repair import partfile_io as pio


def _data_root(tmp_path):
    folder = tmp_path / "data_v3" / "XAU_USD" / "tf_14400"
    folder.mkdir(parents=True)
    (folder / "part-20260308.jsonl").write_bytes(b'{"open_time_ms":1}\n')
    return tmp_path / "data_v3", folder


def _proc(tmp_path, pid: int, argv: str, fds=()):
    pid_dir = tmp_path / "proc" / str(pid)
    (pid_dir / "fd").mkdir(parents=True)
    (pid_dir / "fdinfo").mkdir()
    (pid_dir / "cmdline").write_bytes(argv.replace(" ", "\0").encode())
    for fd, _target, flags in fds:
        (pid_dir / "fd" / str(fd)).write_bytes(b"")
        (pid_dir / "fdinfo" / str(fd)).write_text("pos:\t0\nflags:\t%s\nmnt_id:\t25\n" % flags)
    return pid_dir


def _readlink_from(table):
    def readlink(path):
        key = path.replace("\\", "/")
        if table.get(key) is PermissionError:
            raise PermissionError(key)
        return table[key]
    return readlink


def test_live_writers_by_argv_and_by_write_fd_only_under_tf_dirs(tmp_path):
    root = "/opt/smc-v3/data_v3"
    _proc(tmp_path, 101, "/opt/smc-v3/.venv/bin/python -u -m app.main --mode m1_poller")
    _proc(tmp_path, 102, "/usr/bin/python3 some_tool.py", [(3, None, "0102001"), (4, None, "0100000"), (5, None, "0100002")])
    _proc(tmp_path, 103, "/opt/smc-v3/.venv37/bin/python -u -m runtime.ingest.tick_publisher_fxcm")
    _proc(tmp_path, os.getpid(), "python -m tools.repair.purge_derived_window apply")  # сам інструмент
    proc = str(tmp_path / "proc")
    table = {
        proc.replace("\\", "/") + "/102/fd/3": root + "/XAU_USD/tf_14400/part-20260308.jsonl",  # O_WRONLY|O_APPEND
        proc.replace("\\", "/") + "/102/fd/4": root + "/XAU_USD/tf_60/part-20260308.jsonl",  # лише читання
        proc.replace("\\", "/") + "/102/fd/5": root + "/_signals/log.jsonl",  # не part-каталог TF
    }
    found, uninspectable = pio.find_live_writers(root, proc_root=proc, readlink=_readlink_from(table))
    assert uninspectable == []
    assert [(pid, reason) for pid, reason, _detail in found] == [(101, "argv"), (102, "write_fd")]
    assert found[1][2].endswith("tf_14400/part-20260308.jsonl")


def test_guard_skips_copy_loudly_and_refuses_prod_without_proof(tmp_path, caplog):
    root, _folder = _data_root(tmp_path)
    with caplog.at_level(logging.WARNING, logger="partfile_io"):
        pio.writers_guard(str(root), proc_root=str(tmp_path / "no_proc"))
    assert "WRITERS_GUARD_SKIPPED" in caplog.text
    prod = [pio._posix_abspath(str(root))]
    with pytest.raises(pio.WritersGuardRefused, match="reason=no_proc"):
        pio.writers_guard(str(root), proc_root=str(tmp_path / "no_proc"), prod_roots=prod)
    _proc(tmp_path, 201, "python -m runtime.ws.ws_server")
    with pytest.raises(pio.WritersGuardRefused, match="live=1"):
        pio.writers_guard(str(root), proc_root=str(tmp_path / "proc"), prod_roots=prod)


def test_guard_refuses_when_fds_of_a_process_cannot_be_read(tmp_path, monkeypatch):
    root, _folder = _data_root(tmp_path)
    _proc(tmp_path, 301, "/usr/sbin/sshd", [(7, None, "0100001")])
    proc = str(tmp_path / "proc")
    monkeypatch.setattr(pio.os, "readlink", _readlink_from({proc.replace("\\", "/") + "/301/fd/7": PermissionError}))
    with pytest.raises(pio.WritersGuardRefused, match=r"fd_uninspectable_pids=\[301\]"):
        pio.writers_guard(str(root), proc_root=proc, prod_roots=[pio._posix_abspath(str(root))])


def test_is_prod_data_root_matches_prod_dir_and_its_subdirs_only():
    prod = ("/opt/smc-v3/data_v3",)
    assert pio.is_prod_data_root("/opt/smc-v3/data_v3", prod_roots=prod)
    assert pio.is_prod_data_root("/opt/smc-v3/data_v3/XAU_USD", prod_roots=prod)
    assert not pio.is_prod_data_root("/opt/smc-v3/data_v3_copy", prod_roots=prod)
    assert not pio.is_prod_data_root("/tmp/p5/w26/rh_final_0924/data_v3", prod_roots=prod)


def test_guard_on_prod_refuses_while_another_repair_tool_runs(tmp_path):
    """Вікно 24.09: паралельні потоки ремонту на проді неможливі — settle_prev іншого потоку (і його sudo-обгортка) для
    рейки є живим записувачем. Тому window_data.sh іде строго послідовно; на копії рейка пропускається і цього не видно."""
    root, _folder = _data_root(tmp_path)
    _proc(tmp_path, 401, "sudo -n -u smc /opt/smc-v3/.venv/bin/python /tmp/p5/w26/tools/settle_prev.py --apply")
    _proc(tmp_path, 402, "/opt/smc-v3/.venv/bin/python /tmp/p5/w26/tools/settle_prev.py --apply")
    with pytest.raises(pio.WritersGuardRefused, match="live=2"):
        pio.writers_guard(str(root), proc_root=str(tmp_path / "proc"), prod_roots=[pio._posix_abspath(str(root))])
