"""Бекап і заміна part-файла (ADR-0095 S7.0, `tools.repair.partfile_io`).

Заміна part-файлів — після tgz із sha-маніфестом поза data_root, зі старим inode в `<tf>/_backup_adr0095_<ts>/` і
байтами рівно зі staging.
"""
from __future__ import annotations

import json
import tarfile

import pytest

from tools.repair import partfile_io as pio

STAMP = "20260926T210500Z"


def _data_root(tmp_path):
    folder = tmp_path / "data_v3" / "XAU_USD" / "tf_14400"
    folder.mkdir(parents=True)
    (folder / "part-20260308.jsonl").write_bytes(b'{"open_time_ms":1}\r\n{"open_time_ms":2}')
    (folder / "part-20260309.jsonl").write_bytes(b'{"open_time_ms":3}\n')
    return tmp_path / "data_v3", folder


def test_backup_is_tgz_with_verified_sha_manifest_outside_data_root(tmp_path):
    root, folder = _data_root(tmp_path)
    paths = [str(folder / "part-20260308.jsonl"), str(folder / "part-20260309.jsonl"), str(folder / "part-20260310.jsonl")]
    tgz, manifest = pio.backup_files(paths, str(tmp_path / "backups"), data_root=str(root), tag="s7", stamp=STAMP)
    files = json.load(open(manifest, encoding="utf-8"))["files"]
    assert files["data_v3/XAU_USD/tf_14400/part-20260310.jsonl"] is None  # створить заміна — відкат видаляє
    with tarfile.open(tgz) as tar:
        names = sorted(tar.getnames())
        assert names == ["data_v3/XAU_USD/tf_14400/part-20260308.jsonl", "data_v3/XAU_USD/tf_14400/part-20260309.jsonl"]
        first = tar.extractfile(names[0]).read()
    assert first == b'{"open_time_ms":1}\r\n{"open_time_ms":2}'
    assert files[names[0]]["sha256"] == pio.sha256_hex(first)


def test_backup_inside_data_root_is_refused(tmp_path):
    root, folder = _data_root(tmp_path)
    with pytest.raises(ValueError, match="BACKUP_INSIDE_DATA_ROOT"):
        pio.backup_files([str(folder / "part-20260308.jsonl")], str(root / "_bk"), data_root=str(root), tag="s7",
                         stamp=STAMP)


def test_replace_part_keeps_old_inode_in_adr0095_backup_dir(tmp_path):
    _root, folder = _data_root(tmp_path)
    path = folder / "part-20260308.jsonl"
    new = b'{"open_time_ms":1}\r\n{"open_time_ms":5}\n'
    backup = pio.replace_part(str(path), new, stage_sha256=pio.sha256_hex(new), stamp=STAMP)
    assert path.read_bytes() == new
    assert backup == str(folder / ("_backup_adr0095_" + STAMP) / path.name)
    assert open(backup, "rb").read() == b'{"open_time_ms":1}\r\n{"open_time_ms":2}'


def test_replace_part_with_bytes_other_than_staged_writes_nothing(tmp_path):
    _root, folder = _data_root(tmp_path)
    path = folder / "part-20260308.jsonl"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="PARTFILE_STAGE_SHA_MISMATCH"):
        pio.replace_part(str(path), b"x\n", stage_sha256=pio.sha256_hex(b"y\n"), stamp=STAMP)
    assert path.read_bytes() == before and not (folder / ("_backup_adr0095_" + STAMP)).exists()


def test_replace_part_creates_new_file_like_a_sibling(tmp_path):
    _root, folder = _data_root(tmp_path)
    new = b'{"open_time_ms":9}\n'
    assert pio.replace_part(str(folder / "part-20260310.jsonl"), new, stage_sha256=pio.sha256_hex(new), stamp=STAMP) is None
    assert (folder / "part-20260310.jsonl").read_bytes() == new
    empty_tf = folder.parent / "tf_86400"
    empty_tf.mkdir()  # каталог TF є, part-файлів у ньому ще нема — зразок з іншого TF того самого символу
    assert pio.replace_part(str(empty_tf / "part-20260310.jsonl"), new, stage_sha256=pio.sha256_hex(new), stamp=STAMP) is None
    assert (empty_tf / "part-20260310.jsonl").read_bytes() == new


def test_replace_part_creates_missing_tf_dir_like_another_tf_of_the_symbol(tmp_path):
    """GER30 26.09: символ мав лише M1, натив D1 створює tf_86400 — каталог з режимом каталогу M1, не падіння."""
    _root, folder = _data_root(tmp_path)
    folder.chmod(0o775)
    missing_tf = folder.parent / "tf_86400"
    new = b'{"open_time_ms":9}\n'
    assert pio.replace_part(str(missing_tf / "part-19900924.jsonl"), new, stage_sha256=pio.sha256_hex(new), stamp=STAMP) is None
    assert (missing_tf / "part-19900924.jsonl").read_bytes() == new
    assert (missing_tf.stat().st_mode & 0o777) == (folder.stat().st_mode & 0o777)


def test_replace_part_refuses_symbol_without_any_part_file(tmp_path):
    _root, folder = _data_root(tmp_path)
    lonely = folder.parent.parent / "NEWSYM" / "tf_86400"
    new = b'{"open_time_ms":9}\n'
    with pytest.raises(FileNotFoundError, match="PARTFILE_NO_SIBLING"):
        pio.replace_part(str(lonely / "part-19900924.jsonl"), new, stage_sha256=pio.sha256_hex(new), stamp=STAMP)
    assert not lonely.exists()
