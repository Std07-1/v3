"""Фаза rollback: повернути part-файли до байтів з бекапів apply — дзеркало apply (ADR-0096 §3.3 B, ADR §6).

Ті самі рейки цілі (prod/copy, realpath шляхів запису в межах цілі і поза продом для копії, записувачі, ринок,
власник, лок plan_dir). Кожен файл, який apply міг переписати, класифікується за диском ДО будь-якого запису:
sha == до apply → уже відновлено (попередній відкат устиг або заміни не було) — пропуск у звіті; sha == після
apply → відкотити, бекап мусить мати sha до; інше — хтось писав після apply, відмова. Тому відкат, перерваний
сигналом, Ctrl+C чи kill, повторний запуск завершує. Відновлення — у зворотному порядку через rewrite_atomic:
перед кожним файлом повторні рейки і sha, намір `restoring` у звіт, після — sha == до apply. Звіт
`ft_m1_rollback_v1` поруч з маніфестом apply фіналізується на будь-якій зупинці (except BaseException).
rc: 0 відновлено (або вже було); 1 звірка після запису не зійшлась; 2 відмова до запису; 3 записувачі не доведено
зупиненими, ринок відкритий або файл змінився посеред відкату; 128+signum зупинено сигналом (фіналізація йде ще
під обробниками, тож і сигнал посеред неї).
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import posixpath
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

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
            logging.ERROR, "ROLLBACK_LOCK_HELD", holder=held.holder, reason=held.reason))
        return 2


def _run_locked(opts: RollbackOptions, deps: rails.WriteDeps, cfg: Dict[str, Any], applied: Dict[str, Any],
                target: rails.Target) -> int:
    records = list(reversed(am.touched_records(applied)))
    if not records:
        print("FT_ROLLBACK_SUMMARY status=ok rc=0 files=0 reason=nothing_rewritten")
        return 0
    for record in records:
        rails.require_contained(cfg, target, "ROLLBACK", _write_paths(target, record))
    todo, skipped = _classify(records, target.data_root)
    tf_dir = under(target.data_root, posixpath.dirname(records[0]["part"]))
    if todo and target.kind == "prod":
        rails.writers_check(deps, tf_dir, "ROLLBACK")
        rails.market_check(cfg, deps.now_ms(), opts.guard_minutes, "ROLLBACK")
        rails.owner_check(deps, [under(target.data_root, f["part"]) for f in todo], "ROLLBACK")
    report = {"format": "ft_m1_rollback_v1", "tool_version": c.TOOL_VERSION, "apply_manifest": opts.apply_manifest,
              "apply_manifest_sha256": opts.expect_manifest_sha, "target": target.kind, "status": "running", "rc": None,
              "stop_reason": None, "started_at_utc": c.utc_iso(deps.now_ms()), "finished_at_utc": None,
              "files": [_skipped_entry(record) for record in skipped]}

    out_path = _create_report(opts, deps, report)

    def persist() -> None:
        c.write_json_atomic(out_path, report)

    with c.StopSignals("ROLLBACK") as signals:
        try:
            try:
                rc, reason = _restore_files(cfg, deps, target, tf_dir, todo, report, persist), None
                status = "ok" if rc == 0 else "failed"
            except rails.TargetRefused as refused:
                status, rc, reason = "interrupted", refused.rc, refused.text
            signals.disarm()
        except BaseException as exc:
            # Сигнал, Ctrl+C, ENOSPC посеред відкату: звіт каже правду про кожен файл (вирок диска для `restoring`),
            # а повторний запуск завершить відкат за класифікацією диска. Сигнал — rc 128+signum, решта — далі.
            signals.disarm()
            stopped = isinstance(exc, (c.StopSignal, KeyboardInterrupt, SystemExit))
            code = "ROLLBACK_STOPPED_BY_SIGNAL" if isinstance(exc, c.StopSignal) else (
                "ROLLBACK_INTERRUPTED" if stopped else "ROLLBACK_UNEXPECTED_ERROR")
            text = c.log_event(logging.ERROR, code, err="%s: %s" % (type(exc).__name__, exc))
            exit_code = exc.exit_code if isinstance(exc, c.StopSignal) else 1
            _finalize(report, out_path, "interrupted" if stopped else "failed", exit_code, text, target, deps)
            _print_summary(report, out_path)
            if isinstance(exc, c.StopSignal):
                return exit_code
            raise
        # Фіналізація — ще під обробниками: сигнал у мить запису звіту відкладено й повернуто як 128+signum.
        exit_code = c.finalize_under_signals(signals, lambda signum: _finalize(
            report, out_path, status, rc, reason, target, deps, signum))
    _print_summary(report, out_path)
    return exit_code


def _classify(records: List[Dict[str, Any]], data_root: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(відкотити, уже «до») за диском; файл не «до» і не «після», бекапу немає або він не той — відмова до будь-якого
    запису (і до звіту)."""
    todo, skipped, changed = [], [], []
    for record in records:
        state = am.disk_state(record, data_root)
        if state == am.DISK_BEFORE:
            skipped.append(record)
        elif state == am.DISK_AFTER:
            todo.append(record)
        else:
            changed.append("%s:%s" % (record["part"], state))
    if changed:
        raise rails.refuse(2, "ROLLBACK_CURRENT_CHANGED", files=",".join(changed))
    missing = [str(f.get("backup")) for f in todo if not f.get("backup") or not os.path.isfile(f["backup"])]
    if missing:  # бекапи прибрано (гігієна §6) — відкат можливий лише з tar, не з цього маніфесту
        raise rails.refuse(2, "ROLLBACK_BACKUP_MISSING", files=",".join(missing))
    broken = [f["backup"] for f in todo if c.sha256_file(f["backup"]) != f["sha256_before"]]
    if broken:
        raise rails.refuse(2, "ROLLBACK_BACKUP_CHANGED", files=",".join(broken))
    for record in skipped:
        c.log_event(logging.WARNING, "ROLLBACK_FILE_ALREADY_BEFORE", part=record["part"], status=record["status"])
    return todo, skipped


def _restore_files(cfg: Dict[str, Any], deps: rails.WriteDeps, target: rails.Target, tf_dir: str,
                   todo: List[Dict[str, Any]], report: Dict[str, Any], persist: Callable[[], None]) -> int:
    """Відкат файлів по черзі; перед кожним — рейки і sha «після», намір у звіт; повертає rc (0 або 1)."""
    for record in todo:
        rails.require_contained(cfg, target, "ROLLBACK", _write_paths(target, record))
        if target.kind == "prod":
            rails.writers_check(deps, tf_dir, "ROLLBACK")
        if am.disk_state(record, target.data_root) != am.DISK_AFTER:
            raise rails.refuse(3, "ROLLBACK_CURRENT_CHANGED_DURING_ROLLBACK", part=record["part"])
        entry = {"part": record["part"], "status": "restoring", "sha256_before": record["sha256_before"],
                 "sha256_after": am.expected_after(record), "sha256_restored": None, "backup_of_patched": None}
        report["files"].append(entry)
        persist()
        path = under(target.data_root, record["part"])
        rewrite_atomic(path, read_lines(record["backup"]),
                       before_replace=_backup_recorder(cfg, target, entry, path, persist))
        entry["sha256_restored"] = c.sha256_file(path)
        entry["status"] = "restored" if entry["sha256_restored"] == record["sha256_before"] else "verify_failed"
        persist()
        if entry["status"] != "restored":
            c.log_event(logging.ERROR, "ROLLBACK_WRITE_VERIFY_FAILED", part=record["part"])
            return 1
    return 0


def _backup_recorder(cfg: Dict[str, Any], target: rails.Target, entry: Dict[str, Any], path: str,
                     persist: Callable[[], None]) -> Callable[[str], None]:
    """Хук rewrite_atomic: бекап пропатченого файла — у межах цілі і у звіті ДО os.replace; його байти — ті самі
    «після apply», інакше хтось писав між звіркою і os.link, і відкат стер би дописане — підміну скасовано."""
    def backup_ready(backup: str) -> None:
        rails.require_contained(cfg, target, "ROLLBACK", [backup])
        entry["backup_of_patched"] = os.path.abspath(backup)
        persist()
        if c.sha256_file(backup) != entry["sha256_after"] or c.sha256_file(path) != entry["sha256_after"]:
            entry.update(status="not_restored", restore_aborted=True)
            persist()
            raise rails.refuse(3, "ROLLBACK_CURRENT_CHANGED_DURING_ROLLBACK", part=entry["part"], stage="before_replace",
                               backup_of_patched=entry["backup_of_patched"])

    return backup_ready


def _finalize(report: Dict[str, Any], out_path: str, status: str, rc: int, reason: Optional[str],
              target: rails.Target, deps: rails.WriteDeps, signum: Optional[int] = None) -> int:
    """Фінальний звіт: вирок диска для кожного `restoring`, статус і rc (128+signum, якщо прийшов сигнал)."""
    for entry in report["files"]:
        if entry["status"] == "restoring":  # зупинка між наміром і звіркою: вирок диска
            digest = c.sha256_file(under(target.data_root, entry["part"]))
            verdicts = {entry["sha256_before"]: "restored", entry["sha256_after"]: "not_restored"}
            entry.update(sha256_restored=digest, status=verdicts.get(digest, "unknown"))
    if signum is not None:
        rc = 128 + signum
        reason = reason or c.log_event(logging.WARNING, "ROLLBACK_SIGNAL_DURING_FINALIZE", signal=signum)
    report.update(status=status, rc=rc, stop_reason=reason, finished_at_utc=c.utc_iso(deps.now_ms()))
    c.write_json_atomic(out_path, report)
    return rc


def _print_summary(report: Dict[str, Any], out_path: str) -> None:
    restored = sum(1 for entry in report["files"] if entry["status"] == "restored")
    already = sum(1 for entry in report["files"] if entry["status"] in ("already_restored", "not_replaced"))
    print("FT_ROLLBACK_SUMMARY status=%s rc=%d restored=%d already_before=%d manifest=%s" % (
        report["status"], report["rc"], restored, already, out_path))


def _create_report(opts: RollbackOptions, deps: rails.WriteDeps, report: Dict[str, Any]) -> str:
    """Звіт відкату — завжди новий файл: повторний запуск після перерваного відкату не затирає його звіт."""
    base = "%s.rollback-%s-%d" % (opts.apply_manifest, time.strftime(
        "%Y%m%dT%H%M%SZ", time.gmtime(deps.now_ms() // 1000)), os.getpid())
    attempt = 0
    while True:
        path = "%s%s.json" % (base, "" if attempt == 0 else ".%d" % attempt)
        try:
            c.create_json_exclusive(path, report)
            return path
        except FileExistsError:
            attempt += 1


def _skipped_entry(record: Dict[str, Any]) -> Dict[str, Any]:
    """Файл уже «до»: після `rewritten` — попередній відкат устиг; після `replacing` — заміни не було."""
    return {"part": record["part"], "status": "already_restored" if record["status"] == "rewritten" else "not_replaced",
            "sha256_before": record["sha256_before"], "sha256_after": am.expected_after(record),
            "sha256_restored": record["sha256_before"], "backup_of_patched": None}


def _write_paths(target: rails.Target, record: Dict[str, Any]) -> List[str]:
    """Шляхи відкату одного файла: каталог, part-файл, його .tmp і бекап apply, з якого читаються байти."""
    return rails.part_write_paths(target, record["part"]) + ([record["backup"]] if record.get("backup") else [])


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
