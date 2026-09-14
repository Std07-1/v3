"""Фаза rollback: повернути part-файли до байтів з бекапів apply — дзеркало apply (ADR-0096 §3.3 B, ADR §6).

Ті самі рейки цілі (prod/copy, realpath шляхів запису в межах цілі і поза продом для копії, записувачі, ринок,
власник, лок plan_dir). До запису доводиться для ВСІХ файлів:
поточний sha == sha після apply (ніхто не писав після ремонту) і sha бекапу == sha до apply. Відновлення — у
зворотному порядку через rewrite_atomic; після кожного файла sha == до apply. Маніфест `ft_m1_rollback_v1`
поруч з маніфестом apply. rc: 0 відновлено; 1 звірка після запису не зійшлась; 2 відмова до запису;
3 записувачі не доведено зупиненими або ринок відкритий.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import posixpath
import time
from typing import Any, Dict, List, Optional

from core.config_loader import load_system_config, pick_config_path
from tools.repair.first_tick_m1 import apply_manifest as am
from tools.repair.first_tick_m1 import common as c
from tools.repair.first_tick_m1 import target_rails as rails
from tools.repair.first_tick_m1.plan_io import under
from tools.repair.first_tick_m1.writers_guard import scan_writers
from tools.repair.jsonl_rewrite import read_lines, rewrite_atomic


@dataclasses.dataclass(frozen=True)
class RollbackOptions:
    apply_manifest: str
    expect_manifest_sha: str
    copy: bool = False
    guard_minutes: int = c.APPLY_GUARD_MINUTES_DEFAULT


def run_rollback(opts: RollbackOptions, deps: rails.WriteDeps) -> int:
    cfg = deps.load_cfg()
    try:
        if c.sha256_file(opts.apply_manifest) != opts.expect_manifest_sha:
            raise rails.refuse(2, "ROLLBACK_MANIFEST_SHA_MISMATCH", manifest=opts.apply_manifest)
        applied = c.read_json(opts.apply_manifest)
        if applied.get("format") != am.APPLY_FORMAT:
            raise rails.refuse(2, "ROLLBACK_MANIFEST_FORMAT", manifest=opts.apply_manifest)
        target = rails.resolve_target(cfg, applied["data_root"], opts.copy, "ROLLBACK")
        with c.exclusive_lock(os.path.join(applied["plan_dir"], ".apply.lock")):
            return _run_locked(opts, deps, cfg, applied, target)
    except rails.TargetRefused as refused:
        print("FT_ROLLBACK_SUMMARY status=refused rc=%d reason=%s" % (refused.rc, refused.text))
        return refused.rc
    except c.LockHeld as held:
        print("FT_ROLLBACK_SUMMARY status=refused rc=2 reason=%s" % c.log_event(
            logging.ERROR, "ROLLBACK_LOCK_HELD", holder=held.holder))
        return 2


def _run_locked(opts: RollbackOptions, deps: rails.WriteDeps, cfg: Dict[str, Any], applied: Dict[str, Any],
                target: rails.Target) -> int:
    todo = list(reversed(_rewritten_on_disk(applied, target.data_root)))
    if not todo:
        print("FT_ROLLBACK_SUMMARY status=ok rc=0 files=0 reason=nothing_rewritten")
        return 0
    paths = [under(target.data_root, f["part"]) for f in todo]
    tf_dir = under(target.data_root, posixpath.dirname(todo[0]["part"]))
    for record in todo:
        rails.require_contained(cfg, target, "ROLLBACK", _write_paths(target, record))
    if target.kind == "prod":
        rails.writers_check(deps, tf_dir, "ROLLBACK")
        rails.market_check(cfg, deps.now_ms(), opts.guard_minutes, "ROLLBACK")
        rails.owner_check(deps, paths, "ROLLBACK")
    changed = [path for f, path in zip(todo, paths) if am.disk_state(f, target.data_root) != am.DISK_AFTER]
    if changed:
        raise rails.refuse(2, "ROLLBACK_CURRENT_CHANGED", files=",".join(changed))
    broken = [f["backup"] for f in todo if not f.get("backup") or c.sha256_file(f["backup"]) != f["sha256_before"]]
    if broken:
        raise rails.refuse(2, "ROLLBACK_BACKUP_CHANGED", files=",".join(broken))
    out_path = "%s.rollback-%s-%d.json" % (opts.apply_manifest, time.strftime(
        "%Y%m%dT%H%M%SZ", time.gmtime(deps.now_ms() // 1000)), os.getpid())
    report = {"format": "ft_m1_rollback_v1", "tool_version": c.TOOL_VERSION, "apply_manifest": opts.apply_manifest,
              "apply_manifest_sha256": opts.expect_manifest_sha, "target": target.kind, "status": "running", "rc": None,
              "started_at_utc": c.utc_iso(deps.now_ms()), "finished_at_utc": None, "files": []}
    c.write_json_atomic(out_path, report)
    rc = 0
    for record, path in zip(todo, paths):
        try:
            rails.require_contained(cfg, target, "ROLLBACK", _write_paths(target, record))
            if target.kind == "prod":
                rails.writers_check(deps, tf_dir, "ROLLBACK")
        except rails.TargetRefused as refused:
            report.update(status="interrupted", rc=refused.rc, stop_reason=refused.text,
                          finished_at_utc=c.utc_iso(deps.now_ms()))
            c.write_json_atomic(out_path, report)
            raise
        backup_of_patched = rewrite_atomic(path, read_lines(record["backup"]), before_replace=lambda backup: (
            rails.require_contained(cfg, target, "ROLLBACK", [backup])))
        restored = c.sha256_file(path)
        status = "restored" if restored == record["sha256_before"] else "verify_failed"
        report["files"].append({"part": record["part"], "sha256_restored": restored, "status": status,
                                "backup_of_patched": os.path.abspath(backup_of_patched)})
        c.write_json_atomic(out_path, report)
        if status != "restored":
            rc = 1
            c.log_event(logging.ERROR, "ROLLBACK_WRITE_VERIFY_FAILED", part=record["part"])
            break
    report.update(status="ok" if rc == 0 else "failed", rc=rc, finished_at_utc=c.utc_iso(deps.now_ms()))
    c.write_json_atomic(out_path, report)
    print("FT_ROLLBACK_SUMMARY status=%s rc=%d files=%d manifest=%s" % (report["status"], rc, len(report["files"]),
                                                                        out_path))
    return rc


def _write_paths(target: rails.Target, record: Dict[str, Any]) -> List[str]:
    """Шляхи відкату одного файла: каталог, part-файл, його .tmp і бекап apply, з якого читаються байти."""
    return rails.part_write_paths(target, record["part"]) + ([record["backup"]] if record.get("backup") else [])


def _rewritten_on_disk(applied: Dict[str, Any], data_root: str) -> List[Dict[str, Any]]:
    """Файли, які apply переписав, за вироком диска: `rewritten` — завжди; `replacing`/`unknown` (процес загинув
    до фіналізації) — якщо файл не «до» (after → відкотити, other → відмова нижче)."""
    out = []
    for record in am.touched_records(applied):
        if record["status"] != "rewritten" and am.disk_state(record, data_root) == am.DISK_BEFORE:
            c.log_event(logging.WARNING, "ROLLBACK_FILE_NOT_REPLACED", part=record["part"], status=record["status"])
            continue
        out.append(record)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    parser = argparse.ArgumentParser(prog="python -m tools.repair.first_tick_m1 rollback")
    parser.add_argument("--apply-manifest", required=True)
    parser.add_argument("--expect-manifest-sha", required=True)
    parser.add_argument("--copy", action="store_true")
    parser.add_argument("--guard-minutes", type=int, default=c.APPLY_GUARD_MINUTES_DEFAULT)
    parser.add_argument("--proc-root", default="/proc", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    proc_root = args.proc_root
    deps = rails.WriteDeps(now_ms=lambda: int(time.time() * 1000),
                           scan_writers=lambda dirs: scan_writers(proc_root, target_dirs=dirs),
                           geteuid=getattr(os, "geteuid", None), load_cfg=lambda: load_system_config(pick_config_path()))
    fields = {k: v for k, v in vars(args).items() if k != "proc_root"}
    return run_rollback(RollbackOptions(**fields), deps)
