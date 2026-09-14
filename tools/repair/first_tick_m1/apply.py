"""Фаза apply: заміна o/h/low рядків-переможців за планом — байт-у-байт решта файла (ADR-0096 §3.3 B).

Порядок рейок: sha плану (оператор назвав саме цей план) → ціль prod/copy → лок → (prod) записувачі, ринок,
власник → кожен вхід плану ті самі байти → для кожного файла: повторні записувачі й ринок, sha до, кожен запис
плану збігається з рядком-переможцем, sha після рендеру == план → намір `replacing` у маніфест (fsync) →
rewrite_atomic (шлях бекапу — у маніфест до os.replace) → sha на диску == план.
Маніфест `ft_m1_apply_v1` оновлюється до і після кожного файла; сигнал, Ctrl+C чи будь-яка відмова фіналізують
його вироком диска для кожного файла (`apply_manifest`).
rc: 0 усе переписано і звірено; 1 зупинка на розбіжності/звірці (частково, маніфест точний); 2 відмова до
запису; 3 записувачі не доведено зупиненими або ринок відкритий (до запису — нічого; посеред — interrupted);
128+signum зупинено сигналом (маніфест фіналізовано).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.config_loader import load_system_config, pick_config_path
from tools.repair.first_tick_m1 import apply_manifest as am
from tools.repair.first_tick_m1 import common as c
from tools.repair.first_tick_m1 import target_rails as rails
from tools.repair.first_tick_m1.classify import OHLCV
from tools.repair.first_tick_m1.plan_io import LoadedPlan, PlanCorrupt, input_mismatches, load_plan, under
from tools.repair.first_tick_m1.ssot_part import Patch, canonical_problem, lines_bytes, render_patched_lines
from tools.repair.first_tick_m1.writers_guard import scan_writers
from tools.repair.jsonl_rewrite import key_groups, open_ms_of, read_lines, rewrite_atomic

APPLY_FORMAT = am.APPLY_FORMAT


@dataclasses.dataclass(frozen=True)
class ApplyOptions:
    plan_dir: str
    expect_plan_sha: str
    staging_root: str
    data_root: Optional[str] = None
    copy: bool = False
    guard_minutes: int = c.APPLY_GUARD_MINUTES_DEFAULT
    manifest_out: Optional[str] = None


class _Stop(Exception):
    def __init__(self, status: str, rc: int, text: str) -> None:
        super().__init__(text)
        self.status, self.rc, self.text = status, rc, text


def run_apply(opts: ApplyOptions, deps: rails.WriteDeps) -> int:
    cfg = deps.load_cfg()
    try:
        loaded, target = _preflight(opts, cfg)
    except PlanCorrupt as exc:
        return _print_refusal(c.log_event(logging.ERROR, exc.code, detail=exc.detail), 2)
    except rails.TargetRefused as refused:
        return _print_refusal(refused.text, refused.rc)
    try:
        with c.exclusive_lock(os.path.join(opts.plan_dir, ".apply.lock")):
            return _run_locked(opts, deps, cfg, loaded, target)
    except c.LockHeld as held:
        return _print_refusal(c.log_event(logging.ERROR, "APPLY_LOCK_HELD", holder=held.holder), 2)


def _preflight(opts: ApplyOptions, cfg: Dict[str, Any]) -> Tuple[LoadedPlan, rails.Target]:
    """Кроки 1–2: план той, що назвав оператор; ціль prod/copy; plan_dir/staging/маніфест поза data_root."""
    if not c.GUARD_MINUTES_RANGE[0] <= opts.guard_minutes <= c.GUARD_MINUTES_RANGE[1]:
        raise rails.refuse(2, "APPLY_GUARD_OUT_OF_RANGE", guard_minutes=opts.guard_minutes)
    loaded = load_plan(opts.plan_dir)
    if loaded.plan_id != opts.expect_plan_sha:
        raise rails.refuse(2, "APPLY_PLAN_SHA_MISMATCH", expected=opts.expect_plan_sha, actual=loaded.plan_id)
    if c.fxcm_symbol_problem(cfg, loaded.plan["symbol"]):
        raise rails.refuse(2, "APPLY_SYMBOL_NOT_FXCM", symbol=loaded.plan["symbol"])
    target = rails.resolve_target(cfg, opts.data_root, opts.copy, "APPLY")
    rails.require_outside(target, "APPLY", plan_dir=opts.plan_dir, staging_root=opts.staging_root,
                          manifest_out=opts.manifest_out)
    return loaded, target


def _run_locked(opts: ApplyOptions, deps: rails.WriteDeps, cfg: Dict[str, Any], loaded: LoadedPlan,
                target: rails.Target) -> int:
    plan = loaded.plan
    tf_dir = os.path.join(target.data_root, c.sym_dir(plan["symbol"]), "tf_60")
    todo = sorted((item for item in plan["files"] if item["rewrite"]), key=lambda item: item["day"])
    manifest_path = opts.manifest_out or os.path.join(opts.plan_dir, "applies", "%s-%d.json" % (
        time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(deps.now_ms() // 1000)), os.getpid()))
    os.makedirs(os.path.dirname(os.path.abspath(manifest_path)), exist_ok=True)
    manifest = {"format": APPLY_FORMAT, "tool_version": c.TOOL_VERSION, "plan_id": loaded.plan_id,
                "plan_dir": os.path.abspath(opts.plan_dir), "data_root": target.data_root,
                "staging_root": os.path.abspath(opts.staging_root), "target": target.kind,
                "started_at_utc": c.utc_iso(deps.now_ms()), "finished_at_utc": None, "status": "running",
                "stop_reason": None, "rc": None, "guard": {"skipped": "copy"}, "market": {"checked": False},
                "files": [_file_record(item, loaded) for item in todo]}
    try:
        # Лише новий файл: маніфест частково застосованого прогону — єдиний опис того, що вже переписано.
        c.create_json_exclusive(manifest_path, manifest)
    except FileExistsError:
        return _print_refusal(c.log_event(logging.ERROR, "APPLY_MANIFEST_EXISTS", manifest=manifest_path), 2)

    def persist() -> None:
        c.write_json_atomic(manifest_path, manifest)

    with c.StopSignals("APPLY") as signals:
        try:
            try:
                status, rc, reason = "ok", 0, None
                _apply_files(opts, deps, cfg, loaded, target, manifest, tf_dir, todo, persist)
            except _Stop as stop:
                status, rc, reason = stop.status, stop.rc, stop.text
            except rails.TargetRefused as refused:
                status, rc, reason = "refused", refused.rc, refused.text
            signals.disarm()
        except BaseException as exc:
            # Будь-що інше посеред запису — сигнал, Ctrl+C, ENOSPC, баг: маніфест мусить сказати правду про кожен
            # файл (вирок диска для `replacing`), а не лишитись «running». Сигнал — rc 128+signum, решта — далі.
            signals.disarm()
            stopped = isinstance(exc, (c.StopSignal, KeyboardInterrupt, SystemExit))
            code = "APPLY_STOPPED_BY_SIGNAL" if isinstance(exc, c.StopSignal) else (
                "APPLY_INTERRUPTED" if stopped else "APPLY_UNEXPECTED_ERROR")
            text = c.log_event(logging.ERROR, code, err="%s: %s" % (type(exc).__name__, exc))
            exit_code = exc.exit_code if isinstance(exc, c.StopSignal) else 1
            _finish(manifest, manifest_path, "interrupted" if stopped else "failed", exit_code, text, target, deps)
            if isinstance(exc, c.StopSignal):
                return exit_code
            raise
    return _finish(manifest, manifest_path, status, rc, reason, target, deps)


def _apply_files(opts: ApplyOptions, deps: rails.WriteDeps, cfg: Dict[str, Any], loaded: LoadedPlan,
                 target: rails.Target, manifest: Dict[str, Any], tf_dir: str, todo: List[Dict[str, Any]],
                 persist: Callable[[], None]) -> None:
    """Рейки і перепис файлів по черзі; зупинка — _Stop/TargetRefused, маніфест фіналізує викликач."""
    if target.kind == "prod":
        _prod_rails(opts, deps, cfg, manifest, tf_dir, [under(target.data_root, item["part"]) for item in todo])
    mismatches = input_mismatches(loaded.plan, target.data_root, opts.staging_root)
    if mismatches:
        for path, expected, actual in mismatches:
            print("APPLY_PLAN_INPUT_CHANGED path=%s expected=%s actual=%s" % (path, expected, actual))
        raise _Stop("refused", 2, c.log_event(logging.ERROR, "APPLY_PLAN_INPUT_CHANGED", n=len(mismatches)))
    for index, item in enumerate(todo):
        if target.kind == "prod":
            _prod_rails(opts, deps, cfg, manifest, tf_dir, (), during=True)
        _rewrite_file(item, loaded, target, manifest["files"][index], deps, persist)
        persist()


def _prod_rails(opts: ApplyOptions, deps: rails.WriteDeps, cfg: Dict[str, Any], manifest: Dict[str, Any], tf_dir: str,
                owned: Any, during: bool = False) -> None:
    try:
        report = rails.writers_check(deps, tf_dir, "APPLY")
    except rails.TargetRefused as refused:
        manifest["guard"] = refused.report
        raise
    if not during:
        manifest["guard"] = report
    try:
        manifest["market"] = rails.market_check(cfg, deps.now_ms(), opts.guard_minutes, "APPLY")
    except rails.TargetRefused as refused:
        manifest["market"] = {"checked": True, "refused": refused.text}
        raise
    if not during:
        rails.owner_check(deps, list(owned), "APPLY")


def _rewrite_file(item: Dict[str, Any], loaded: LoadedPlan, target: rails.Target, record: Dict[str, Any],
                  deps: rails.WriteDeps, persist: Callable[[], None]) -> None:
    path = under(target.data_root, item["part"])
    with open(path, "rb") as fh:
        raw = fh.read()
    if c.sha256_bytes(raw) != item["sha256_before"]:
        raise _Stop("interrupted", 3, c.log_event(logging.ERROR, "APPLY_INPUT_CHANGED_DURING_APPLY", path=path))
    lines = read_lines(path)
    replaces = [e for e in loaded.entries[item["day"]] if e["cat"] == "REPLACE"]
    problem = canonical_problem(raw, lines) or _entries_problem(lines, replaces)
    if problem:
        raise _Stop("failed", 1, c.log_event(logging.ERROR, "APPLY_PLAN_DIVERGED", path=path, detail=problem))
    patches = {e["line"]: Patch(e["new"]["o"], e["new"]["h"], e["new"]["low"], e["trading_flat_add"])
               for e in replaces}
    new_lines = render_patched_lines(lines, patches)
    if c.sha256_bytes(lines_bytes(new_lines)) != item["sha256_after"]:
        raise _Stop("failed", 1, c.log_event(logging.ERROR, "APPLY_PLAN_DIVERGED", path=path, detail="sha_after"))
    # Намір — у маніфест з fsync ДО перепису, шлях бекапу — до os.replace: процес, убитий будь-де між ними,
    # лишає `replacing`, і вирок виносить диск (apply_manifest.disk_state), а не памʼять цього процесу.
    record.update(status="replacing", intent_at_utc=c.utc_iso(deps.now_ms()))
    persist()

    def backup_ready(backup: str) -> None:
        record["backup"] = os.path.abspath(backup)
        persist()

    rewrite_atomic(path, new_lines, before_replace=backup_ready)
    record.update(status="rewritten", at_utc=c.utc_iso(deps.now_ms()))
    record["sha256_after_actual"] = c.sha256_file(path)
    if record["sha256_after_actual"] != item["sha256_after"]:
        raise _Stop("failed", 1, c.log_event(logging.ERROR, "APPLY_WRITE_VERIFY_FAILED", path=path,
                                             backup=record["backup"]))


def _entries_problem(lines: List[str], replaces: List[Dict[str, Any]]) -> Optional[str]:
    """Кожен REPLACE плану — той самий рядок-переможець з тими самими старими значеннями, інакше розбіжність."""
    groups = key_groups(lines)
    for entry in replaces:
        index, key = entry["line"], entry["k"]
        if not 0 <= index < len(lines) or c.sha256_bytes(lines[index].encode("utf-8")) != entry["line_sha256"]:
            return "line k=%d index=%d" % (key, index)
        if open_ms_of(lines[index]) != key or key not in groups or groups[key].winner != index:
            return "winner k=%d index=%d" % (key, index)
        bar = json.loads(lines[index])
        if any(bar.get(field) != entry["old"][field] for field in OHLCV):
            return "old_values k=%d" % key
    return None


def _file_record(item: Dict[str, Any], loaded: LoadedPlan) -> Dict[str, Any]:
    replaces = [e for e in loaded.entries[item["day"]] if e["cat"] == "REPLACE"]
    return {"day": item["day"], "part": item["part"], "sha256_before": item["sha256_before"],
            "sha256_after_planned": item["sha256_after"], "sha256_after_actual": None, "backup": None,
            "replaced": len(replaces), "trading_flat_added": sum(1 for e in replaces if e["trading_flat_add"]),
            "status": "not_started", "intent_at_utc": None, "at_utc": None}


def _finish(manifest: Dict[str, Any], path: str, status: str, rc: int, reason: Optional[str], target: rails.Target,
            deps: rails.WriteDeps) -> int:
    am.reconcile_replacing(manifest, target.data_root)
    if status == "refused" and am.touched_records(manifest):
        status = "interrupted"  # відмова рейки посеред прогону: частина файлів уже переписана
    manifest.update(status=status, rc=rc, stop_reason=reason, finished_at_utc=c.utc_iso(deps.now_ms()))
    c.write_json_atomic(path, manifest)
    done = [f for f in manifest["files"] if f["status"] == "rewritten"]
    print("FT_APPLY_SUMMARY status=%s rc=%d files=%d replaced=%d trading_flat_added=%d target=%s manifest=%s" % (
        status, rc, len(done), sum(f["replaced"] for f in done), sum(f["trading_flat_added"] for f in done),
        manifest["target"], path))
    if done:
        print("FT_APPLY_NEXT verify: python -m tools.repair.first_tick_m1 verify --apply-manifest %s" % path)
    if done and rc:
        print("FT_APPLY_NEXT rollback: python -m tools.repair.first_tick_m1 rollback --apply-manifest %s "
              "--expect-manifest-sha %s" % (path, c.sha256_file(path)))
    return rc


def _print_refusal(text: str, rc: int) -> int:
    print("FT_APPLY_SUMMARY status=refused rc=%d reason=%s" % (rc, text))
    return rc


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    parser = argparse.ArgumentParser(prog="python -m tools.repair.first_tick_m1 apply")
    parser.add_argument("--plan-dir", required=True)
    parser.add_argument("--expect-plan-sha", required=True)
    parser.add_argument("--staging-root", required=True)
    parser.add_argument("--data-root")
    parser.add_argument("--copy", action="store_true")
    parser.add_argument("--guard-minutes", type=int, default=c.APPLY_GUARD_MINUTES_DEFAULT)
    parser.add_argument("--manifest-out")
    parser.add_argument("--proc-root", default="/proc", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    proc_root = args.proc_root
    deps = rails.WriteDeps(now_ms=lambda: int(time.time() * 1000),
                           scan_writers=lambda dirs: scan_writers(proc_root, target_dirs=dirs),
                           geteuid=getattr(os, "geteuid", None), load_cfg=lambda: load_system_config(pick_config_path()))
    fields = {k: v for k, v in vars(args).items() if k != "proc_root"}
    return run_apply(ApplyOptions(**fields), deps)
