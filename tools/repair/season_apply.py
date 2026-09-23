"""tools/repair/season_apply.py — застосування плану S7 (ADR-0095 S7.3): заміна похідних part-файлів і відкат.

    python -m tools.repair.season_apply --scope derived_from_m1,h4_from_h1,d1_rekey,holes [--symbols ...]
        [--from ISO] [--to ISO] [--changed-m1 changed.json] [--data-root data_v3] --backup-dir <поза data_root>
        [--apply] [--journal <поза data_root>]
    python -m tools.repair.season_apply --rollback <journal.jsonl>

Без `--apply` — план і звіт (як `season_plan_report`), без жодного запису. З `--apply`:
1. записувачі SSOT доведено зупинені (`writers_guard`, скан /proc на прод-шляху);
2. STALE_SOURCE: кожен part-файл плану на диску досі той, з якого план складено (sha/відсутність), інакше нічого;
3. tgz-бекап усіх зачеплених файлів із sha-маніфестом (перевірений перечитуванням), поза data_root;
4. заміна кожного файла з байтами плану (sha staging), старий inode — у `_backup_adr0095_<stamp>`, рядок журналу з
   fsync на кожен файл (path, old_sha, new_sha, backup);
5. V1: жодного H4/D1 поза сезонною сіткою в замінених файлах; повторний план тих самих областей = 0 файлів.
Будь-яка відмова після кроку 4 друкує команду відкату. `--rollback` іде журналом у зворотному порядку: файл, чий sha
досі new_sha, повертається зі старого inode і звіряється з old_sha; інший sha — ROLLBACK_CONFLICT (живий запис уже
щось дописав), стоп — запасний шлях: tgz за маніфестом. Файл, створений планом (old_sha null), видаляється.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

from core.config_loader import load_system_config, pick_config_path
from core.session_anchor import D1_S, H4_S, assert_on_season_grid
from core.config_loader import htf_anchor_rule_resolver
from tools.repair.jsonl_rewrite import replace_bytes_atomic
from tools.repair.partfile_io import (
    backup_files, load_part, replace_part, sha256_hex, split_lines, parse_body, utc_stamp, writers_guard,
)
from tools.repair.season_plan import ALL_TIME, SeasonPlan, build_plan
from tools.repair.season_plan_report import (
    _iso_ms, _load_changed_m1, _resolve_symbols, format_report,
)

log = logging.getLogger("season_apply")


def _inside(path: str, root: str) -> bool:
    return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)


def stale_sources(plan: SeasonPlan) -> List[str]:
    """Файли плану, що змінились на диску після планування (STALE_SOURCE)."""
    stale = []
    for fp in plan.files:
        if not os.path.exists(fp.path):
            if fp.src_exists:
                stale.append("%s: зник" % fp.path)
            continue
        with open(fp.path, "rb") as fh:
            sha = sha256_hex(fh.read())
        if not fp.src_exists or sha != fp.src_sha256:
            stale.append("%s: sha %s != план %s" % (fp.path, sha[:12], (fp.src_sha256 or "none")[:12]))
    return stale


def grid_violations(plan: SeasonPlan, rule_for_symbol) -> List[str]:
    """V1: свої рядки H4/D1 у нових байтах, чий ключ не на сезонній сітці символу."""
    bad = []
    for sp in plan.symbols:
        rule = rule_for_symbol(sp.context.symbol)
        for fp in sp.files:
            if fp.tf_s not in (H4_S, D1_S):
                continue
            for line in split_lines(fp.new_bytes):
                obj = parse_body(line.body)
                if obj is None or str(obj.get("symbol", sp.context.symbol)) != sp.context.symbol:
                    continue
                try:
                    assert_on_season_grid(int(obj["open_time_ms"]), fp.tf_s, rule)
                except ValueError as exc:
                    bad.append(str(exc))
    return bad


def apply_plan(plan: SeasonPlan, data_root: str, backup_dir: str, journal_path: str) -> Dict[str, Any]:
    stamp = utc_stamp()
    paths = [fp.path for fp in plan.files]
    tgz, manifest = backup_files(paths, backup_dir, data_root=data_root, tag="season_apply", stamp=stamp)
    print("BACKUP %s files=%d manifest=%s" % (tgz, len(paths), manifest))
    written = 0
    with open(journal_path, "a", encoding="utf-8") as journal:
        for fp in plan.files:
            backup = replace_part(fp.path, fp.new_bytes, stage_sha256=fp.new_sha256, stamp=stamp)
            journal.write(json.dumps({"path": fp.path, "old_sha256": fp.src_sha256 if fp.src_exists else None,
                                      "new_sha256": fp.new_sha256, "backup": backup, "tgz": tgz}) + "\n")
            journal.flush()
            os.fsync(journal.fileno())
            written += 1
    return {"tgz": tgz, "manifest": manifest, "written": written, "stamp": stamp}


def rollback(journal_path: str) -> int:
    entries = [json.loads(line) for line in open(journal_path, encoding="utf-8") if line.strip()]
    stamp = utc_stamp()
    restored = 0
    for entry in reversed(entries):
        path = entry["path"]
        current = sha256_hex(open(path, "rb").read()) if os.path.exists(path) else None
        if current != entry["new_sha256"]:
            print("ROLLBACK_CONFLICT %s sha=%s очікувався %s — стоп; запасний шлях: tar xzf %s -C <батько data_root>"
                  % (path, (current or "none")[:12], entry["new_sha256"][:12], entry["tgz"]), file=sys.stderr)
            return 1
        if entry["old_sha256"] is None:
            os.remove(path)
        else:
            old = open(entry["backup"], "rb").read()
            if sha256_hex(old) != entry["old_sha256"]:
                print("ROLLBACK_BACKUP_MISMATCH %s — стоп; tar xzf %s" % (entry["backup"], entry["tgz"]), file=sys.stderr)
                return 1
            rollback_dir = os.path.join(os.path.dirname(path), "_backup_adr0095_rollback_%s" % stamp)
            os.makedirs(rollback_dir, exist_ok=True)
            replace_bytes_atomic(path, old, backup_dir=rollback_dir)
            if sha256_hex(open(path, "rb").read()) != entry["old_sha256"]:
                print("ROLLBACK_VERIFY_FAILED %s" % path, file=sys.stderr)
                return 1
        restored += 1
    print("ROLLBACK_OK files=%d" % restored)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scope")
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--to", dest="date_to", default=None)
    ap.add_argument("--changed-m1", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--backup-dir", default=None)
    ap.add_argument("--journal", default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.rollback:
        return rollback(args.rollback)
    if not args.scope:
        print("SEASON_APPLY_REFUSED --scope обов'язковий", file=sys.stderr)
        return 2
    cfg = load_system_config(args.config or pick_config_path())
    data_root = os.path.abspath(args.data_root or str(cfg.get("data_root", "data_v3")))
    symbols = _resolve_symbols(cfg, args.symbols)
    scopes = [s.strip() for s in args.scope.split(",") if s.strip()]
    window = (_iso_ms(args.date_from) if args.date_from else ALL_TIME[0],
              _iso_ms(args.date_to) if args.date_to else ALL_TIME[1])
    changed = _load_changed_m1(args.changed_m1) if args.changed_m1 else None
    if args.apply:
        if not args.backup_dir or _inside(args.backup_dir, data_root):
            print("SEASON_APPLY_REFUSED --apply потребує --backup-dir поза data_root", file=sys.stderr)
            return 2
        writers_guard(data_root)
    plan = build_plan(cfg, data_root, symbols, scopes, window, changed)
    print(format_report(plan))
    rule_for_symbol = htf_anchor_rule_resolver(dict(cfg))
    violations = grid_violations(plan, rule_for_symbol)
    if violations:
        print("SEASON_APPLY_V1_FAILED n=%d first=%s — план лишає H4/D1 поза сіткою" % (len(violations), violations[:3]),
              file=sys.stderr)
        return 2
    if not args.apply:
        return 0
    stale = stale_sources(plan)
    if stale:
        print("STALE_SOURCE n=%d first=%s — нічого не записано" % (len(stale), stale[:3]), file=sys.stderr)
        return 2
    if not plan.files:
        print("SEASON_APPLY_NOTHING_TO_WRITE")
        return 0
    journal = args.journal or os.path.join(args.backup_dir, "season_apply_%s.journal.jsonl" % utc_stamp())
    result = apply_plan(plan, data_root, args.backup_dir, journal)
    print("WRITTEN files=%d journal=%s" % (result["written"], journal))
    again = build_plan(cfg, data_root, symbols, scopes, window, changed)
    print("VERIFY_REPLAN files=%d" % len(again.files))
    if again.files:
        print("SEASON_APPLY_VERIFY_FAILED повторний план не порожній — відкат: python -m tools.repair.season_apply "
              "--rollback %s" % journal, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
