"""Атомарна заміна part-файла байтами (ADR-0095 S7.0, `tools.repair.jsonl_rewrite.replace_bytes_atomic`).

Заміна S7 переписує part-файли SSOT, де EOL рядків змішані (CRLF у більшості H4 XAU/XAG) і останній рядок буває
без переводу. Тому вміст задається байтами, а не рядками: інакше заміна нормалізувала б EOL рядків, яких не
чіпала. Каталог бекапу `_backup_adr0095_<ts>` лежить поруч із part-файлами, тож читач його не бачить.
"""
from __future__ import annotations

import os
import types

import pytest

from runtime.store.layers.disk_layer import DiskLayer
from tools.repair import jsonl_rewrite
from tools.repair.jsonl_rewrite import replace_bytes_atomic

OLD = b'{"open_time_ms":1}\r\n{"open_time_ms":2}\r\n'
NEW = b'{"open_time_ms":1}\r\n{"open_time_ms":3}\n{"open_time_ms":4}'


def _part(tmp_path, data: bytes = OLD):
    folder = tmp_path / "XAU_USD" / "tf_14400"
    folder.mkdir(parents=True)
    path = folder / "part-20260308.jsonl"
    path.write_bytes(data)
    return path


def test_replace_writes_exact_bytes_mixed_eol_and_no_trailing_newline(tmp_path):
    path = _part(tmp_path)
    replace_bytes_atomic(str(path), NEW)
    assert path.read_bytes() == NEW
    assert not os.path.exists(str(path) + ".tmp")


def test_backup_dir_holds_old_bytes_and_readers_do_not_see_it(tmp_path):
    path = _part(tmp_path)
    backup_dir = path.parent / "_backup_adr0095_20260926T210000Z"
    backup = replace_bytes_atomic(str(path), NEW, backup_dir=str(backup_dir))
    assert backup == str(backup_dir / path.name)
    assert (backup_dir / path.name).read_bytes() == OLD
    assert DiskLayer(str(tmp_path)).list_parts("XAU/USD", 14400) == [str(path)]


def test_existing_backup_name_refuses_before_any_write(tmp_path):
    path = _part(tmp_path)
    backup_dir = path.parent / "_backup_adr0095_x"
    backup_dir.mkdir()
    (backup_dir / path.name).write_bytes(b"earlier backup")
    with pytest.raises(FileExistsError, match="REWRITE_BACKUP_EXISTS"):
        replace_bytes_atomic(str(path), NEW, backup_dir=str(backup_dir))
    assert path.read_bytes() == OLD
    assert (backup_dir / path.name).read_bytes() == b"earlier backup"
    assert not os.path.exists(str(path) + ".tmp")


def test_owner_that_cannot_be_preserved_refuses_before_replace(tmp_path, monkeypatch):
    """Файл, чужий для живого писаря, гірший за відмову: заміна не відбувається, tmp прибрано."""
    path = _part(tmp_path)
    real_stat = os.stat

    def stat_with_foreign_owner(target, *args, **kwargs):
        st = real_stat(target, *args, **kwargs)
        if os.path.abspath(target) == os.path.abspath(str(path)):
            return types.SimpleNamespace(st_mode=st.st_mode, st_uid=st.st_uid + 1, st_gid=st.st_gid)
        return st

    def chown_denied(*_args):
        raise PermissionError("not root")

    monkeypatch.setattr(jsonl_rewrite.os, "stat", stat_with_foreign_owner)
    monkeypatch.setattr(jsonl_rewrite.os, "chown", chown_denied, raising=False)
    with pytest.raises(PermissionError, match="REWRITE_OWNER_NOT_PRESERVED"):
        replace_bytes_atomic(str(path), NEW)
    monkeypatch.undo()
    assert path.read_bytes() == OLD
    assert not os.path.exists(str(path) + ".tmp")
    assert [name for name in os.listdir(path.parent) if ".bak." in name] == []


def test_content_that_differs_after_replace_is_loud(tmp_path, monkeypatch):
    path = _part(tmp_path)
    real_replace = os.replace

    def replace_then_corrupt(src, dst):
        real_replace(src, dst)
        with open(dst, "ab") as fh:
            fh.write(b"x")

    monkeypatch.setattr(jsonl_rewrite.os, "replace", replace_then_corrupt)
    with pytest.raises(RuntimeError, match="REWRITE_VERIFY_FAILED"):
        replace_bytes_atomic(str(path), NEW)


def test_rewrite_atomic_lines_still_lf_and_accepts_backup_dir(tmp_path):
    path = _part(tmp_path)
    backup_dir = path.parent / "_backup_adr0095_y"
    jsonl_rewrite.rewrite_atomic(str(path), ['{"open_time_ms":5}'], backup_dir=str(backup_dir))
    assert path.read_bytes() == b'{"open_time_ms":5}\n'
    assert (backup_dir / path.name).read_bytes() == OLD
