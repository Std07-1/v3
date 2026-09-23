"""S7.3 (ADR-0095): застосування плану — заміна part-файлів з бекапом і журналом, повтор = 0, відкат байт у байт."""
from __future__ import annotations

import os

from core.config_loader import load_system_config, pick_config_path
from season_plan_synthetic import ms, run_rebuild_tool, write_m1
from core.session_anchor import D1_S, H4_S
from test_season_plan_files import FIRST, LAST, reader_rows, spoil
from tools.repair import season_apply
from tools.repair.season_plan import SCOPE_DERIVED_FROM_M1, build_plan


def _snapshot(root):
    out = {}
    for base, _dirs, names in os.walk(root):
        for name in names:
            path = os.path.join(base, name)
            out[os.path.relpath(path, root)] = open(path, "rb").read()
    return out


def _tree(tmp_path):
    root = tmp_path / "data"
    write_m1(root, FIRST, LAST)
    run_rebuild_tool(root, FIRST, LAST + 60_000)
    pristine = _snapshot(root)
    spoil(root)
    return root, pristine


def _derived(snapshot):
    return {k: v for k, v in snapshot.items()
            if "tf_60" not in k and "_backup" not in k and ".bak." not in k and k.endswith(".jsonl")}


def test_apply_restores_derived_then_replan_is_empty_and_rollback_is_byte_exact(tmp_path, capsys):
    root, pristine = _tree(tmp_path)
    spoiled = _snapshot(root)
    backup_dir, journal = tmp_path / "bak", tmp_path / "bak" / "j.jsonl"
    rc = season_apply.main(["--scope", SCOPE_DERIVED_FROM_M1, "--symbols", "XAU/USD", "--data-root", str(root),
                            "--backup-dir", str(backup_dir), "--journal", str(journal), "--apply"])
    out = capsys.readouterr().out

    assert rc == 0 and "VERIFY_REPLAN files=0" in out
    pristine_root = tmp_path / "pristine"
    for rel, data in pristine.items():
        os.makedirs(os.path.dirname(pristine_root / rel), exist_ok=True)
        (pristine_root / rel).write_bytes(data)
    for tf_s in (180, 300, 900, 1800, 3600, H4_S, D1_S):  # читач бачить рівно f(M1), як до псування
        assert reader_rows(root, tf_s) == reader_rows(pristine_root, tf_s), tf_s
    assert os.path.exists(journal) and any(name.endswith(".tgz") for name in os.listdir(backup_dir))

    assert season_apply.main(["--rollback", str(journal)]) == 0
    restored = _snapshot(root)
    assert _derived(restored) == _derived(spoiled)


def test_stale_source_is_detected_before_any_write(tmp_path):
    root, _pristine = _tree(tmp_path)
    cfg = load_system_config(pick_config_path())
    plan = build_plan(cfg, str(root), ["XAU/USD"], [SCOPE_DERIVED_FROM_M1])
    target = plan.files[0].path
    with open(target, "ab") as fh:
        fh.write(b"\n")  # живий запис між планом і застосуванням
    assert season_apply.stale_sources(plan) and target in season_apply.stale_sources(plan)[0]


def test_apply_refuses_backup_inside_data_root(tmp_path):
    root, _pristine = _tree(tmp_path)
    rc = season_apply.main(["--scope", SCOPE_DERIVED_FROM_M1, "--symbols", "XAU/USD", "--data-root", str(root),
                            "--backup-dir", str(root / "bak"), "--apply"])
    assert rc == 2
