"""Staging сирих рядків FIRST_TICK поза data_v3 — формат доби і відмови (ADR-0096 §3.3 B, слайс B2).

Навіщо. План класифікує ключі SSOT за рядками staging; «доба» staging мусить бути або цілком тією, що
віддав брокер (sha маніфесту == байти файла, прапорці «не перший тік» перераховуються), або гучно
непридатною. Тихий мікс двох перезаборів чи CRLF-файл, що вийшов з-під Windows, дали б плану чужі рядки.
"""
from __future__ import annotations

import ast
import hashlib
import os
import sys
from pathlib import Path

import pytest

from ft_m1_support import DAY, SYMBOL, at, fetch_meta, raw_row, staged_row
from tools.repair.first_tick_m1 import staging
from tools.repair.first_tick_m1.common import write_json_atomic

PACKAGE = Path(staging.__file__).resolve().parent


def _rows():
    return [
        staged_row(at(DAY, 22, 0), 4089.00, 4090.10, 4088.50, 4089.40),
        staged_row(at(DAY, 22, 1), 4089.98, 4093.19, 4086.33, 4092.36, volume=31),
        staged_row(at(DAY, 22, 2), 4092.30, 4094.00, 4091.00, 4093.50),
    ]


def test_day_roundtrip_manifest_sha_equals_file_bytes(tmp_path):
    manifest = staging.write_day_atomic(tmp_path, SYMBOL, DAY, _rows(), fetch_meta())
    day_file, manifest_file = staging.day_paths(tmp_path, SYMBOL, DAY)
    assert manifest["sha256"] == hashlib.sha256(Path(day_file).read_bytes()).hexdigest()
    assert (manifest["rows"], manifest["raw_open_not_tick"], manifest["open_price_mode"]) == (3, 0, "FIRST_TICK")
    loaded = staging.load_day(tmp_path, SYMBOL, DAY)
    assert list(loaded.rows) == _rows()
    assert loaded.manifest == manifest
    assert loaded.sha256_manifest == hashlib.sha256(Path(manifest_file).read_bytes()).hexdigest()


def test_refetch_replaces_day_and_leaves_no_tmp(tmp_path):
    staging.write_day_atomic(tmp_path, SYMBOL, DAY, _rows(), fetch_meta())
    second = _rows()[:2]
    staging.write_day_atomic(tmp_path, SYMBOL, DAY, second, fetch_meta(call_seq=2))
    loaded = staging.load_day(tmp_path, SYMBOL, DAY)
    assert list(loaded.rows) == second and loaded.manifest["call_seq"] == 2
    day_dir = Path(staging.day_paths(tmp_path, SYMBOL, DAY)[0]).parent
    assert [p.name for p in day_dir.iterdir() if p.name.endswith(".tmp")] == []


def test_crash_between_file_and_manifest_is_refused_not_mixed(tmp_path, monkeypatch):
    """Ловить тихий мікс: новий файл доби зі старим маніфестом мусить читатись як sha_mismatch, не як доба."""
    staging.write_day_atomic(tmp_path, SYMBOL, DAY, _rows(), fetch_meta())
    real_replace = os.replace

    def replace_crashing_on_manifest(src, dst):
        if str(dst).endswith(".manifest.json"):
            raise OSError("disk full")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace_crashing_on_manifest)
    with pytest.raises(OSError):
        staging.write_day_atomic(tmp_path, SYMBOL, DAY, _rows()[:1], fetch_meta(call_seq=2))
    monkeypatch.setattr(os, "replace", real_replace)
    with pytest.raises(staging.StagingInvalid) as caught:
        staging.load_day(tmp_path, SYMBOL, DAY)
    assert caught.value.reason == "sha_mismatch"


def test_load_refuses_mode_other_than_first_tick(tmp_path):
    staging.write_day_atomic(tmp_path, SYMBOL, DAY, _rows(), fetch_meta())
    _day_file, manifest_file = staging.day_paths(tmp_path, SYMBOL, DAY)
    manifest = staging.read_json(manifest_file)
    manifest["open_price_mode"] = "PREVIOUS_CLOSE"
    write_json_atomic(manifest_file, manifest)
    with pytest.raises(staging.StagingInvalid) as caught:
        staging.load_day(tmp_path, SYMBOL, DAY)
    assert caught.value.reason == "mode"


def _mutated(kind):
    rows = _rows()
    if kind == "outside_day":
        rows[2] = staged_row(at(DAY, 22, 0) + 86_400_000 * 2, 4092.3, 4094.0, 4091.0, 4093.5)
    elif kind == "duplicate":
        rows[2] = dict(rows[1])
    elif kind == "unsorted":
        rows[0], rows[1] = rows[1], rows[0]
    elif kind == "misaligned":
        rows[1] = staged_row(at(DAY, 22, 1) + 1_000, 4089.98, 4093.19, 4086.33, 4092.36)
    elif kind == "schema_bool_as_int":
        rows[1] = dict(rows[1], Volume=True)
    elif kind == "schema_nan_price":
        rows[1] = dict(rows[1], BidClose=float("nan"))
    elif kind == "schema_extra_key":
        rows[1] = dict(rows[1], Extra=1)
    elif kind == "empty":
        rows = []
    return rows


@pytest.mark.parametrize("kind, reason", [
    ("outside_day", "outside_day"), ("duplicate", "duplicate"), ("unsorted", "unsorted"),
    ("misaligned", "misaligned"), ("schema_bool_as_int", "schema"), ("schema_nan_price", "schema"),
    ("schema_extra_key", "schema"), ("empty", "empty"),
])
def test_validate_refuses_bad_rows(tmp_path, kind, reason):
    with pytest.raises(staging.StagingInvalid) as caught:
        staging.validate_rows(SYMBOL, DAY, _mutated(kind))
    assert caught.value.reason == reason
    with pytest.raises(staging.StagingInvalid):
        staging.write_day_atomic(tmp_path, SYMBOL, DAY, _mutated(kind), fetch_meta())
    assert staging.load_day(tmp_path, SYMBOL, DAY) is None


def test_flag_must_match_recomputed_predicate():
    """Ловить довіру прапорцю з файла: «запечений» рядок (o 4346.23 > h 4337.69) з false — flag_mismatch."""
    baked = staged_row(at(DAY, 22, 1), 4346.23, 4337.69, 4330.62, 4331.55)
    assert baked["raw_open_not_tick"] is True
    staging.validate_rows(SYMBOL, DAY, [baked])
    with pytest.raises(staging.StagingInvalid) as caught:
        staging.validate_rows(SYMBOL, DAY, [dict(baked, raw_open_not_tick=False)])
    assert caught.value.reason == "flag_mismatch"


def test_rows_from_raw_drops_rows_outside_day_and_counts_them():
    before = raw_row(at(DAY, 0, 0) - 60_000, 1.0, 2.0, 0.5, 1.5)
    after = raw_row(at(DAY, 0, 0) + 86_400_000, 1.0, 2.0, 0.5, 1.5)
    inside_late = raw_row(at(DAY, 23, 59), 4346.23, 4337.69, 4330.62, 4331.55)
    inside_early = raw_row(at(DAY, 0, 0), 4089.98, 4093.19, 4086.33, 4092.36)
    rows, dropped = staging.rows_from_raw([before, inside_late, after, inside_early], DAY)
    assert dropped == 2
    assert [r["open_time_ms"] for r in rows] == [at(DAY, 0, 0), at(DAY, 23, 59)]
    assert [r["raw_open_not_tick"] for r in rows] == [False, True]
    assert list(rows[0]) == list(staging.ROW_KEYS)


def test_load_refuses_crlf_staging_file(tmp_path):
    staging.write_day_atomic(tmp_path, SYMBOL, DAY, _rows(), fetch_meta())
    day_file, manifest_file = staging.day_paths(tmp_path, SYMBOL, DAY)
    crlf = Path(day_file).read_bytes().replace(b"\n", b"\r\n")
    Path(day_file).write_bytes(crlf)
    manifest = staging.read_json(manifest_file)
    manifest["sha256"] = hashlib.sha256(crlf).hexdigest()
    write_json_atomic(manifest_file, manifest)
    with pytest.raises(staging.StagingInvalid) as caught:
        staging.load_day(tmp_path, SYMBOL, DAY)
    assert caught.value.reason == "not_canonical"


def test_load_refuses_half_missing_day(tmp_path):
    staging.write_day_atomic(tmp_path, SYMBOL, DAY, _rows(), fetch_meta())
    os.remove(staging.day_paths(tmp_path, SYMBOL, DAY)[1])
    with pytest.raises(staging.StagingInvalid) as caught:
        staging.load_day(tmp_path, SYMBOL, DAY)
    assert caught.value.reason == "half_missing"


FETCH_SIDE = ("__init__", "__main__", "common", "staging", "fetch", "fetch_rails", "fetch_call", "fetch_runner",
              "fetch_child")
PLATFORM_ONLY = ("runtime.store", "redis", "aiohttp") + tuple(
    "tools.repair.first_tick_m1." + name
    for name in ("plan", "plan_io", "apply", "verify", "rollback", "classify", "ssot_part", "writers_guard",
                 "target_rails")
)


# Модулі tools/repair поза пакетом, які імпортує fetch-сторона.
FETCH_SIDE_SHARED = ("durable_fs",)


@pytest.mark.parametrize("path", [PACKAGE / (name + ".py") for name in FETCH_SIDE]
                         + [PACKAGE.parent / (name + ".py") for name in FETCH_SIDE_SHARED], ids=lambda p: p.stem)
def test_fetch_side_modules_parse_as_python37_and_import_no_platform(path):
    """Fetch іде в .venv37 (Python 3.7): синтаксис 3.8+ або модульний імпорт платформних залежностей зламав би
    його лише на VPS. Імпорт фаз plan/apply/verify у __main__ — лише всередині функцій."""
    # У самому Python 3.7 (.venv37) парсер і є 3.7 — feature_version з'явився лише в 3.8.
    grammar = {"feature_version": (3, 7)} if sys.version_info >= (3, 8) else {}
    tree = ast.parse(path.read_text(encoding="utf-8"), **grammar)
    module_level = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_level += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module_level.append(node.module or "")
            module_level += ["%s.%s" % (node.module, alias.name) for alias in node.names]
    offenders = [m for m in module_level if any(m == p or m.startswith(p + ".") for p in PLATFORM_ONLY)]
    assert offenders == [], offenders
