"""fsync каталогу після os.replace / створення файла: part-файл, маніфести, доба staging (ADR-0096 §3.3 B).

Навіщо. fsync файла не закріплює запис у каталозі: після os.replace і збою живлення на POSIX-ФС part-файл може
повернутись до допатчевої версії при маніфесті `rewritten`, а маніфест — до стану без останнього кроку. Виклик
fsync каталогу ін'єктовано (`durable_fs.dir_fsync`), щоб перевіряти порядок на будь-якій ОС: каталог фсинкається
ПІСЛЯ підміни, коли ім'я вже веде на новий вміст. Справжній fsync каталогу — окремо на POSIX. Python 3.7.
"""
from __future__ import annotations

import datetime as dt
import errno
import os
from pathlib import Path

import pytest

from ft_m1_support import fetch_meta, staged_row
from tools.repair import durable_fs
from tools.repair.first_tick_m1 import common
from tools.repair.first_tick_m1.staging import day_paths, rows_bytes, write_day_atomic
from tools.repair.jsonl_rewrite import rewrite_atomic


@pytest.fixture()
def dir_fsyncs(monkeypatch):
    """[(каталог, {ім'я: байти в цю мить})] кожного fsync каталогу."""
    seen = []

    def record(directory):
        seen.append((directory, {p.name: p.read_bytes() for p in Path(directory).iterdir() if p.is_file()}))

    monkeypatch.setattr(durable_fs, "dir_fsync", record)
    return seen


def test_write_json_atomic_and_create_exclusive_fsync_dir_after_name_points_to_new_bytes(tmp_path, dir_fsyncs):
    manifest = tmp_path / "apply.json"
    manifest.write_bytes(b"old\n")
    common.write_json_atomic(manifest, {"status": "rewritten"})
    common.create_json_exclusive(tmp_path / "rollback.json", {"status": "running"})
    assert len(dir_fsyncs) == 2
    assert [(d, files.get(name)) for (d, files), name in zip(dir_fsyncs, ("apply.json", "rollback.json"))] == [
        (str(tmp_path), common.canonical_json_bytes({"status": "rewritten"})),
        (str(tmp_path), common.canonical_json_bytes({"status": "running"}))]


def test_rewrite_atomic_fsyncs_part_dir_after_replace(tmp_path, dir_fsyncs):
    part = tmp_path / "part-20260727.jsonl"
    part.write_bytes(b'{"a":1}\n')
    rewrite_atomic(str(part), ['{"a":2}'])
    ((directory, files),) = dir_fsyncs
    assert directory == str(tmp_path) and files[part.name] == b'{"a":2}\n'


def test_staging_day_dir_fsynced_after_day_file_and_manifest_replaced(tmp_path, dir_fsyncs):
    """Доба і її маніфест — в одному каталозі: fsync каталогу після підміни маніфесту закріплює обидва імені."""
    day = dt.date(2026, 7, 26)
    rows = [staged_row(common.day_start_ms(day) + 22 * 3_600_000, 4089.98, 4093.19, 4086.33, 4092.36)]
    write_day_atomic(tmp_path, "XAU/USD", day, rows, fetch_meta(request=common.request_window(day)))
    day_file, manifest = day_paths(tmp_path, "XAU/USD", day)
    last_dir, files = dir_fsyncs[-1]
    assert last_dir == os.path.dirname(day_file) and files[os.path.basename(day_file)] == rows_bytes(rows)
    assert files[os.path.basename(manifest)] == Path(manifest).read_bytes()


def test_dir_fsync_unsupported_by_fs_is_loud_other_errors_raise(tmp_path, monkeypatch, caplog):
    def einval(directory):
        raise OSError(errno.EINVAL, "Invalid argument")

    monkeypatch.setattr(durable_fs, "dir_fsync", einval)
    assert durable_fs.fsync_parent_dir(str(tmp_path / "x.json")) is False
    assert "FS_DIR_FSYNC_UNSUPPORTED" in caplog.text

    def eio(directory):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(durable_fs, "dir_fsync", eio)
    with pytest.raises(OSError):
        durable_fs.fsync_parent_dir(str(tmp_path / "x.json"))


@pytest.mark.skipif(os.name != "posix", reason="fsync каталогу — POSIX")
def test_real_posix_dir_fsync(tmp_path):
    (tmp_path / "x.json").write_bytes(b"{}\n")
    assert durable_fs.fsync_parent_dir(str(tmp_path / "x.json")) is True
