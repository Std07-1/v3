"""План S7.1 (ADR-0095): новий вміст part-файлів похідних TF — байтова модель, повнота і ідемпотентність.

Похідні на диску навмисно зіпсовано так, як це буває на проді: H4 на старій сітці з CRLF, рядок чужого символу й
нерозбірний рядок, застарілий M5, дублікат M15, діра H1, M30 без переводу в кінці, порожній і відсутній part-файли,
сусіди `.bak.<ts>` і `_backup_before_rebuild/`. План мусить повернути похідні рівно до f(M1), не зачепивши жодного
байта поза областю дії, а повторний план над застосованим — дати нуль змін.
"""
from __future__ import annotations

import json
import shutil

from core.session_anchor import D1_S, H4_S
from runtime.store.layers.disk_layer import DiskLayer
from season_plan_synthetic import context, ms, run_rebuild_tool, write_m1
from tools.repair import season_plan as sp
from tools.repair.partfile_io import sha256_hex

FIRST, LAST = ms(2026, 3, 5, 22), ms(2026, 3, 10, 20, 59)
TF_DIR = "XAU_USD/tf_%d"


def plan(root, changed=None):
    ctx = context(root)
    rebuild, _tail = sp.complete_rebuild_set(ctx, [sp.seed_derived_from_m1(ctx, changed)])
    planned = sp.plan_bars(ctx, sp.SourceReader(str(root), "XAU/USD"), rebuild)
    return sp.plan_symbol_files(ctx, str(root), rebuild, planned)


def apply(files):
    for file_plan in files:
        with open(file_plan.path, "wb") as fh:
            fh.write(file_plan.new_bytes)


def reader_rows(root, tf_s):
    rows, _geom = DiskLayer(str(root)).read_window_with_geom("XAU/USD", tf_s, 10 ** 9, use_tail=True, final_only=True)
    return {row["open_time_ms"]: row for row in rows}


def spoil(root):
    """Типові вади проду поверх похідних, що дорівнюють f(M1); повертає байти, які план мусить зберегти."""
    h4 = root / (TF_DIR % H4_S) / "part-20260309.jsonl"
    old_grid = [json.dumps(dict(json.loads(line), open_time_ms=json.loads(line)["open_time_ms"] + 3_600_000),
                           separators=(",", ":")) for line in h4.read_text(encoding="utf-8").splitlines()]
    h4.write_bytes("\r\n".join(old_grid).encode() + b"\r\n")  # стара сітка, CRLF
    m3 = root / (TF_DIR % 180) / "part-20260309.jsonl"
    foreign = b'{"symbol":"XAG/USD","tf_s":180,"open_time_ms":%d,"o":1,"h":1,"low":1,"c":1,"v":1,"complete":true,"src":"derived"}' % ms(2026, 3, 9, 10)
    m3_lines = m3.read_bytes().split(b"\n")
    m3_lines[7] = m3_lines[7].replace(b'"v":3.0', b'"v":2.0')  # застарілий обсяг: файл змінюється, решта — ні
    m3.write_bytes(b"\n".join(m3_lines) + foreign + b"\n" + b"garbage line\n" + b"\n")
    m5 = root / (TF_DIR % 300) / "part-20260309.jsonl"
    m5_rows = m5.read_text(encoding="utf-8").splitlines()
    m5_rows[10] = json.dumps(dict(json.loads(m5_rows[10]), c=1.0), separators=(",", ":"))  # застарілий бар
    m5.write_text("\n".join(m5_rows) + "\n", encoding="utf-8", newline="\n")
    m15 = root / (TF_DIR % 900) / "part-20260309.jsonl"
    m15.write_bytes(m15.read_bytes() + m15.read_bytes().split(b"\n")[3] + b"\n")  # дублікат ключа
    h1 = root / (TF_DIR % 3600) / "part-20260309.jsonl"
    h1_rows = h1.read_text(encoding="utf-8").splitlines()
    h1.write_text("\n".join(h1_rows[:4] + h1_rows[5:]) + "\n", encoding="utf-8", newline="\n")  # діра H1
    m30 = root / (TF_DIR % 1800) / "part-20260310.jsonl"
    m30_rows = m30.read_text(encoding="utf-8").splitlines()
    m30_rows[2] = json.dumps(dict(json.loads(m30_rows[2]), v=0.5), separators=(",", ":"))
    m30.write_bytes("\n".join(m30_rows).encode())  # без переводу в кінці
    (root / (TF_DIR % 900) / "part-20260310.jsonl").write_bytes(b"")  # порожній
    (root / (TF_DIR % D1_S) / "part-20260309.jsonl").unlink()  # відсутній
    bak = root / (TF_DIR % H4_S) / "part-20260309.jsonl.bak.1758000000"
    bak.write_bytes(b"old bak\n")
    legacy = root / (TF_DIR % H4_S) / "_backup_before_rebuild"
    legacy.mkdir()
    (legacy / "part-20260309.jsonl").write_bytes(b"legacy\n")
    return {"foreign": foreign, "bak": bak, "legacy": legacy / "part-20260309.jsonl"}


def test_plan_restores_f_of_m1_byte_exactly_and_second_plan_is_empty(tmp_path):
    root, pristine = tmp_path / "data", tmp_path / "pristine"
    write_m1(root, FIRST, LAST)
    run_rebuild_tool(root, FIRST, LAST + 60_000)
    shutil.copytree(root, pristine)
    keep = spoil(root)
    m1_before = {p.name: p.read_bytes() for p in (root / "XAU_USD" / "tf_60").iterdir()}

    files, rows = plan(root)
    by_path = {(f.tf_s, f.day): f for f in files}
    assert set(by_path) == {(H4_S, "20260309"), (180, "20260309"), (300, "20260309"), (900, "20260309"),
                            (3600, "20260309"), (1800, "20260310"), (900, "20260310"), (D1_S, "20260309")}

    h4 = by_path[(H4_S, "20260309")]
    assert h4.new_bytes.count(b"\r\n") == h4.new_bytes.count(b"\n"), "нові рядки — з EOL файла (CRLF)"
    assert rows[H4_S][sp.ROW_OFF_GRID] == len(h4.removed_keys) == 6 and rows[H4_S][sp.ROW_ADDED] == 6
    m3 = by_path[(180, "20260309")]
    assert m3.new_bytes.endswith(keep["foreign"] + b"\ngarbage line\n\n"), "чужий, нерозбірний, порожній — байт у байт"
    assert rows[300][sp.ROW_REPLACED] == 1 and rows[900][sp.ROW_DUPLICATE] == 1 and rows[3600][sp.ROW_ADDED] == 1
    m30 = by_path[(1800, "20260310")]
    assert m30.eol_added and m30.new_bytes.endswith(b"\n") and rows[1800][sp.ROW_REPLACED] == 1
    assert by_path[(900, "20260310")].src_size == 0 and not by_path[(D1_S, "20260309")].src_exists
    assert all(f.src_sha256 == (sha256_hex(open(f.path, "rb").read()) if f.src_exists else None) for f in files)
    assert rows[180][sp.ROW_REPLACED] == 1 and rows[180][sp.ROW_OLD] == rows[180][sp.ROW_UNCHANGED] + 1

    apply(files)
    for tf_s in (180, 300, 900, 1800, 3600, H4_S, D1_S):
        assert reader_rows(root, tf_s) == reader_rows(pristine, tf_s), "TF %d = f(M1)" % tf_s
    assert keep["bak"].read_bytes() == b"old bak\n" and keep["legacy"].read_bytes() == b"legacy\n"
    assert {p.name: p.read_bytes() for p in (root / "XAU_USD" / "tf_60").iterdir()} == m1_before

    again, again_rows = plan(root)
    assert again == []
    assert all(counter[sp.ROW_OLD] == counter[sp.ROW_UNCHANGED] for counter in again_rows.values())


def test_changed_m1_plans_only_files_of_buckets_with_the_changed_key(tmp_path):
    write_m1(tmp_path, FIRST, LAST)
    run_rebuild_tool(tmp_path, FIRST, LAST + 60_000)
    changed = ms(2026, 3, 10, 14, 37)
    m1 = tmp_path / "XAU_USD" / "tf_60" / "part-20260310.jsonl"
    m1.write_text("\n".join(
        json.dumps(dict(row, h=row["h"] + 100.0)) if row["open_time_ms"] == changed else json.dumps(row)
        for row in map(json.loads, m1.read_text(encoding="utf-8").splitlines())) + "\n", encoding="utf-8")

    files, rows = plan(tmp_path, changed=[changed])
    assert sorted((f.tf_s, f.day) for f in files) == sorted(
        [(tf_s, "20260310") for tf_s in (180, 300, 900, 1800, 3600, H4_S)] + [(D1_S, "20260309")])
    assert all(counter[sp.ROW_REPLACED] == 1 for counter in rows.values())
    full_files, _rows = plan(tmp_path)
    assert {(f.tf_s, f.day): f.new_bytes for f in full_files} == {(f.tf_s, f.day): f.new_bytes for f in files}

