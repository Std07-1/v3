"""Settle M1 з архіву брокера (ADR-0098, ADR-0101, ADR-0103 S2): SSOT := бари FXCM PREVIOUS_CLOSE (= TV `FX:`) у вікні.

Вхід — архів `tools/fetch_m1_prev` (`<SYM_DIR>_m1.json` = [[open_ms, o, h, low, c, v], ...] + `meta.json` з режимом,
часом забору і чанками), вікно ключів [--from, --to) UTC. Гейт архіву (`settle_gate`) відмовляє до будь-якого запису;
план (`settle_plan`): правила ключів → вкладення застарілого краю → суцільний ланцюг. Дефолт — dry-run (план і звіт).
`--apply`: доказ зупинених записувачів (`writers_guard`) → tgz-бекап із sha-маніфестом поза data_root → заміна
part-файлів (`replace_part`: власник і режим як були, старий inode у `_backup_adr0095_<stamp>`) → verify: байти =
очікувані, скасованих маркерів 0, повторний план = 0 дій. Файл вікна без \\n у кінці отримує EOL гучно (NEWLINE_ADDED):
живий писар дописує «рядок\\n» наосліп. Код виходу: 0 ok, 1 verify, 2 аргументи/вхід/гейт.

    python -m tools.repair.settle_m1 --data-root data_v3 --archive-dir <dir> --from 2025-10-15T00:00 --to 2026-09-24T12:00 \
        [--symbols XAU/USD ...] [--baseline-ours | --baseline-archive <dir>] [--apply --backup-dir <dir>] [--report out.json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from typing import Any, Dict, List

from core.config_loader import load_system_config, pick_config_path, session_open_grace_resolver
from runtime.ingest.m1_session_filter import classify_m1_by_calendar, resolve_flat_max_volume, resolve_pause_policy
from runtime.ingest.tick_common import calendar_for_symbol
from tools.repair.partfile_io import (
    Line, backup_files, is_prod_data_root, load_part, replace_part, sha256_hex, utc_stamp, writers_guard,
)
from tools.repair.settle_gate import archive_gate, last_week_open_ms, utc_label
from tools.repair.settle_plan import STRIPPED_MARKERS, SymbolPlan, canonical_row, plan_symbol
from tools.repair.settle_rules import is_visible, to_bar

log = logging.getLogger("settle_m1")
TOOL = "settle_m1/1"
# Маркери, яких після settle не лишається в жодному своєму рядку зачепленого файла (перераховані ADR-0101 — лишаються)
_MUST_BE_GONE = tuple(m for m in STRIPPED_MARKERS if m not in ("open_chained_from", "late_ticks_folded"))


def parse_iso(text: str) -> int:
    """ISO UTC → мс (2026-09-07T00:00, 2026-09-07T21:59:59Z, +00:00)."""
    moment = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return int((moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)).timestamp() * 1000)


def load_archive(archive_dir: str, sym_dir: str, lo_ms: int, hi_ms: int) -> Dict[int, List[float]]:
    with open(os.path.join(archive_dir, "%s_m1.json" % sym_dir), encoding="utf-8") as fh:
        rows = json.load(fh)
    return {int(r[0]): [float(x) for x in r[1:6]] for r in rows if lo_ms <= int(r[0]) < hi_ms}


def provenance_of(kind: str, fetched_at: str) -> Any:
    """extensions.settled (ADR-0098 §3.2): compact — `prev/<забір>`, full — словник, none — без мітки."""
    if kind == "none":
        return None
    if kind == "full":
        return {"mode": "PREVIOUS_CLOSE", "fetched_at": fetched_at, "tool": TOOL}
    return "prev/%sZ" % fetched_at[:16].replace("-", "").replace(":", "")


def ours_visible_keys(plan_files, lo_ms: int, hi_ms: int) -> set:
    return {line.own_key for part in plan_files.values() for line in part.lines
            if line.own_key is not None and lo_ms <= line.own_key < hi_ms and is_visible(line.obj.get("extensions"))}


def expected_bytes(plan: SymbolPlan, path: str, sym_dir: str) -> bytes:
    """Файл після дій плану: нецільові рядки байт у байт зі своїм EOL, вставки — у кінець з EOL файла."""
    part = plan.files[path] if path in plan.files else load_part(path, sym_dir)
    eol = part.eol_style()
    replaced = {i: canonical_row(obj) for p, i, _k, obj in plan.replace if p == path}
    deleted = {i for p, i in plan.delete if p == path}
    lines = [Line(body=replaced.get(i, line.body), eol=line.eol or eol)
             for i, line in enumerate(part.lines) if i not in deleted]
    lines += [Line(body=body, eol=eol) for _k, body in plan.insert.get(path, [])]
    if part.lines and part.lines[-1].eol == b"":
        log.warning("NEWLINE_ADDED %s — живий писар дописує рядок наосліп", path)
    return b"".join(line.body + line.eol for line in lines)


def apply_plan(plan: SymbolPlan, sym_dir: str, stamp: str) -> List[str]:
    """Пише зачеплені файли плану і перечитує їх; повертає проблеми verify."""
    problems = []
    for path in plan.touched():
        data = expected_bytes(plan, path, sym_dir)
        replace_part(path, data, stage_sha256=sha256_hex(data), stamp=stamp)
        after = load_part(path, sym_dir)
        if after.to_bytes() != data:
            problems.append("%s: bytes ≠ expected" % path)
        elif after.lines and after.lines[-1].eol == b"":
            problems.append("%s: no trailing newline after write" % path)
        problems += ["%s: marker remains key=%s" % (path, line.own_key) for line in after.lines
                     if line.own_key is not None and any(m in (line.obj.get("extensions") or {}) for m in _MUST_BE_GONE)]
    return problems


def _args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--archive-dir", required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--from", dest="t_from", required=True)
    ap.add_argument("--to", dest="t_to", required=True)
    ap.add_argument("--symbols", nargs="*", help="XAU/USD NAS100 …; дефолт — config.symbols")
    ap.add_argument("--baseline-archive", default=None, help="попередній успішний забір: покриття доби не менше")
    ap.add_argument("--baseline-ours", action="store_true", help="покриття доби — проти видимих торгових барів SSOT")
    ap.add_argument("--ours-tolerance", type=int, default=3, help="наші торгові бари доби без пари в архіві (заглушки)")
    ap.add_argument("--min-coverage", type=float, default=0.90, help="got/calendar доби без бази")
    ap.add_argument("--provenance", choices=("compact", "full", "none"), default="compact")
    ap.add_argument("--drop-only-ours-by-classifier", action="store_true")
    ap.add_argument("--drop-only-ours-flat", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backup-dir", default=None)
    ap.add_argument("--report", default=None)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _args(argv)
    data_root = os.path.abspath(args.data_root)
    lo_ms, hi_ms = parse_iso(args.t_from), parse_iso(args.t_to)
    if hi_ms <= lo_ms or (args.apply and not args.backup_dir):
        log.error("SETTLE_ARGS --to пізніше за --from; --apply потребує --backup-dir")
        return 2
    if args.provenance == "none" and is_prod_data_root(data_root):
        log.error("PROVENANCE_NONE_REFUSED на прод-шляху рядок без сліду походження заборонений (ADR-0098 §3.2)")
        return 2
    with open(os.path.join(args.archive_dir, "meta.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    if meta.get("mode") != "PREVIOUS_CLOSE":
        log.error("ARCHIVE_MODE_NOT_PREVIOUS_CLOSE mode=%r", meta.get("mode"))
        return 2
    if args.apply:
        writers_guard(data_root)
    cfg = load_system_config(args.config or pick_config_path())
    grace_for, flat_max = session_open_grace_resolver(cfg), resolve_flat_max_volume(cfg)
    fetched_ms = int(meta.get("fetched_at_ms") or parse_iso(meta["fetched_at"]))
    provenance = provenance_of(args.provenance, meta["fetched_at"])
    report: Dict[str, Any] = {"tool": TOOL, "data_root": data_root, "window": [utc_label(lo_ms), utc_label(hi_ms)],
                              "mode": "apply" if args.apply else "dry-run", "provenance": provenance, "symbols": {}}
    planners, refused = {}, []
    for symbol in args.symbols or list(cfg.get("symbols") or []):
        sym_dir = symbol.replace("/", "_")
        is_trading = calendar_for_symbol(cfg, symbol).is_trading_minute
        policy = resolve_pause_policy(cfg, symbol)

        def classify(key, vals, _symbol=symbol, _is_trading=is_trading, _policy=policy):
            return classify_m1_by_calendar(to_bar(_symbol, key, vals), _is_trading, flat_max, _policy)

        archive = load_archive(args.archive_dir, sym_dir, lo_ms, hi_ms)
        unarchived_from = last_week_open_ms(is_trading, fetched_ms)

        def planner(_sd=sym_dir, _symbol=symbol, _archive=archive, _it=is_trading, _cl=classify, _un=unarchived_from):
            return plan_symbol(data_root, _sd, _symbol, _archive, lo_ms, hi_ms, is_trading=_it, classify=_cl,
                               provenance=provenance, grace_min=grace_for(_symbol), flat_max=flat_max,
                               drop_by_classifier=args.drop_only_ours_by_classifier,
                               drop_flat=args.drop_only_ours_flat, unarchived_from_ms=_un)

        plan = planner()
        baseline = (set(load_archive(args.baseline_archive, sym_dir, lo_ms, hi_ms))
                    if args.baseline_archive else None)
        problems, gate = archive_gate(
            ((meta.get("symbols") or {}).get(sym_dir) or {}).get("chunks"), set(archive), is_trading, lo_ms, hi_ms,
            min_coverage=args.min_coverage, baseline_keys=baseline, ours_tolerance=args.ours_tolerance,
            ours_keys=ours_visible_keys(plan.files, lo_ms, hi_ms) if args.baseline_ours else None,
            parse_iso=parse_iso)
        refused += ["%s: %s" % (sym_dir, p) for p in problems]
        planners[sym_dir] = (planner, plan)
        stats = dict(plan.trace.stats)
        report["symbols"][sym_dir] = {"stats": stats, "gate": {"problems": problems, **gate},
                                      "unarchived_from": utc_label(unarchived_from), "changed_m1_keys": plan.changed_keys(),
                                      "log": {k: v[:200] for k, v in plan.trace.log.items()}}
        log.info("%s gate_problems=%d %s unarchived_from=%s", sym_dir, len(problems), stats, utc_label(unarchived_from))
    report["archive_gate_verdict"] = "REFUSED" if refused else "OK"
    for problem in refused[:20]:
        log.error("ARCHIVE_GATE_PROBLEM %s", problem)
    if args.apply and not refused:
        report.update(_apply(planners, data_root, args.backup_dir))
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1, default=str)
    if refused:
        return 2
    return 1 if report.get("verify_problems") else 0


def _apply(planners, data_root: str, backup_dir: str) -> Dict[str, Any]:
    touched = sorted({path for _planner, plan in planners.values() for path in plan.touched()})
    if not touched:
        log.info("SETTLE_NOTHING_TO_WRITE")
        return {"written_files": 0, "verify_problems": []}
    stamp = utc_stamp()
    tgz, manifest = backup_files(touched, backup_dir, data_root=data_root, tag="settle_m1", stamp=stamp)
    log.info("BACKUP %s files=%d", tgz, len(touched))
    problems: List[str] = []
    for sym_dir, (planner, plan) in planners.items():
        problems += apply_plan(plan, sym_dir, stamp)
        again = planner().actions  # ідемпотентність — частина verify: повторний план по записаному = 0 дій
        log.info("VERIFY_REPLAN %s actions=%d", sym_dir, again)
        if again:
            problems.append("%s: replan actions=%d" % (sym_dir, again))
    if problems:
        log.error("SETTLE_VERIFY_FAILED n=%d first=%s — відкат: tar xzf %s -C %s", len(problems), problems[:5], tgz,
                  os.path.dirname(data_root))
    return {"written_files": len(touched), "backup": {"tgz": tgz, "manifest": manifest}, "verify_problems": problems}


if __name__ == "__main__":
    sys.exit(main())
