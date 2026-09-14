"""Фаза verify: погляди читачів до і після apply — змінились рівно заплановані o/h/low (ADR-0096 §3.3 B).

Лише читання. Бекап (до) і поточний файл (після) копіюються у work_dir поза data_root і читаються справжнім
кодом читачів: TAIL (cold-load), RANGE (scrollback + `_ensure_sorted_dedup`) і PRIME (final_only +
skip_preview, як bootstrap Redis). Порівнюється кожен ключ доби і кожен рядок файла.
rc: 0 порушень немає; 1 є порушення (друкуються всі); 2 вхідна помилка.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Set

from core.model.bars import FINAL_SOURCES
from runtime.store.layers.disk_layer import DiskLayer
from runtime.store.uds import _ensure_sorted_dedup
from tools.repair.first_tick_m1 import common as c
from tools.repair.first_tick_m1.plan_io import PlanCorrupt, load_plan, under
from tools.repair.jsonl_rewrite import read_lines

VIEWS = ("TAIL", "RANGE", "PRIME")
_PATCHED_FIELDS = ("o", "h", "low", "extensions")


@dataclasses.dataclass(frozen=True)
class VerifyOptions:
    apply_manifest: str
    work_dir: Optional[str] = None


class VerifyInputError(Exception):
    pass


def run_verify(opts: VerifyOptions) -> int:
    try:
        manifest, loaded, rewritten = _load_inputs(opts)
        work = _work_dir(opts, manifest["data_root"])
    except (VerifyInputError, PlanCorrupt, OSError, ValueError) as exc:
        print("FT_VERIFY_SUMMARY rc=2 error=%s" % exc)
        return 2
    symbol, violations, warnings = loaded.plan["symbol"], [], []
    for record in rewritten:
        rel = record["part"]
        for side, source in (("before", record["backup"]), ("after", under(manifest["data_root"], rel))):
            os.makedirs(os.path.dirname(under(os.path.join(work, side), rel)), exist_ok=True)
            shutil.copyfile(source, under(os.path.join(work, side), rel))
        day = c.parse_day_key(record["day"])
        planned = {e["k"]: e for e in loaded.entries[record["day"]] if e["cat"] == "REPLACE"}
        before, after = read_views(os.path.join(work, "before"), symbol, day), read_views(os.path.join(work, "after"),
                                                                                           symbol, day)
        if any(len(views[name]) >= c.VERIFY_WINDOW_LIMIT for views in (before, after) for name in VIEWS):
            print("FT_VERIFY_SUMMARY rc=2 error=VERIFY_WINDOW_TRUNCATED day=%s" % record["day"])
            return 2
        for name in VIEWS:
            violations += ["%s day=%s" % (v, record["day"]) for v in diff_views(before[name], after[name], planned, name)]
        violations += ["%s day=%s" % (v, record["day"]) for v in diff_lines(
            read_lines(under(os.path.join(work, "before"), rel)), read_lines(under(os.path.join(work, "after"), rel)),
            {e["line"] for e in planned.values()})]
        if before["TAIL"] == before["RANGE"] and after["TAIL"] != after["RANGE"]:
            violations.append("VERIFY_TAIL_RANGE_DIVERGED day=%s" % record["day"])
        elif before["TAIL"] != before["RANGE"]:
            warnings.append("VERIFY_TAIL_RANGE_DIFFERED_BEFORE day=%s" % record["day"])
    for text in warnings:
        c.log_event(logging.WARNING, text)
    for text in violations:
        print(text)
    print("FT_VERIFY_SUMMARY files=%d violations=%d warnings=%d work_dir=%s rc=%d" % (
        len(rewritten), len(violations), len(warnings), work, 1 if violations else 0))
    return 1 if violations else 0


def read_views(root: str, symbol: str, day: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Бари доби так, як їх віддають читачі UDS з диска: TAIL, RANGE і PRIME."""
    disk = DiskLayer(root)
    window = dict(since_open_ms=c.day_start_ms(day) - 1, to_open_ms=c.day_start_ms(day) + c.DAY_MS - 1)
    tail = disk.read_window_with_geom(symbol, c.TF_S, c.VERIFY_WINDOW_LIMIT, use_tail=True, **window)[0]
    raw_range = disk.read_window_with_geom(symbol, c.TF_S, c.VERIFY_WINDOW_LIMIT, use_tail=False, **window)[0]
    prime = disk.read_window_with_geom(symbol, c.TF_S, c.VERIFY_WINDOW_LIMIT, use_tail=True, final_only=True,
                                       skip_preview=True, final_sources=FINAL_SOURCES, **window)[0]
    return {"TAIL": tail, "RANGE": _ensure_sorted_dedup(raw_range, tf_ms=c.TF_S * 1000)[0], "PRIME": prime}


def diff_views(before: Sequence[Dict[str, Any]], after: Sequence[Dict[str, Any]], planned: Dict[int, Dict[str, Any]],
               view: str = "view") -> List[str]:
    """Порушення одного погляду: набір ключів, непланові зміни, заплановане не видно, trading_flat, геометрія."""
    old_by_key = {bar["open_time_ms"]: bar for bar in before}
    new_by_key = {bar["open_time_ms"]: bar for bar in after}
    out = []
    if set(old_by_key) != set(new_by_key):
        out.append("VERIFY_KEYSET_CHANGED view=%s missing=%s added=%s" % (
            view, sorted(set(old_by_key) - set(new_by_key))[:5], sorted(set(new_by_key) - set(old_by_key))[:5]))
    for key in sorted(set(old_by_key) & set(new_by_key)):
        old, new, entry = old_by_key[key], new_by_key[key], planned.get(key)
        if entry is None:
            if old != new:
                out.append("VERIFY_UNPLANNED_CHANGE view=%s k=%d fields=%s" % (view, key, _changed_fields(old, new)))
            continue
        if (new.get("o"), new.get("h"), new.get("low")) != (entry["new"]["o"], entry["new"]["h"], entry["new"]["low"]):
            out.append("VERIFY_PLANNED_VALUE_MISMATCH view=%s k=%d" % (view, key))
        rest_old = {f: v for f, v in old.items() if f not in _PATCHED_FIELDS}
        rest_new = {f: v for f, v in new.items() if f not in _PATCHED_FIELDS}
        if rest_old != rest_new:
            out.append("VERIFY_UNPLANNED_CHANGE view=%s k=%d fields=%s" % (view, key, _changed_fields(rest_old, rest_new)))
        expected_ext = dict(old.get("extensions") or {}, trading_flat=True) if entry["trading_flat_add"] else old.get(
            "extensions")
        if new.get("extensions") != expected_ext:
            out.append("VERIFY_TRADING_FLAT_MISMATCH view=%s k=%d" % (view, key))
        o, h, low, close = (new.get(f) for f in ("o", "h", "low", "c"))
        if not all(isinstance(x, (int, float)) for x in (o, h, low, close)) or not (
                low <= min(o, close) and max(o, close) <= h):
            out.append("VERIFY_OHLC_BROKEN view=%s k=%d" % (view, key))
    return out


def diff_lines(before_lines: Sequence[str], after_lines: Sequence[str], planned_indices: Set[int]) -> List[str]:
    if len(before_lines) != len(after_lines):
        return ["VERIFY_LINE_COUNT_CHANGED before=%d after=%d" % (len(before_lines), len(after_lines))]
    return ["VERIFY_UNPLANNED_LINE_CHANGED line=%d" % index
            for index, (old, new) in enumerate(zip(before_lines, after_lines)) if index not in planned_indices and old != new]


def _load_inputs(opts: VerifyOptions) -> Any:
    manifest = c.read_json(opts.apply_manifest)
    if not isinstance(manifest, dict) or manifest.get("format") != "ft_m1_apply_v1":
        raise VerifyInputError("VERIFY_MANIFEST_FORMAT")
    if manifest.get("status") not in ("ok", "interrupted", "failed"):
        raise VerifyInputError("VERIFY_MANIFEST_STATUS status=%s" % manifest.get("status"))
    loaded = load_plan(manifest["plan_dir"])
    if loaded.plan_id != manifest["plan_id"]:
        raise VerifyInputError("VERIFY_PLAN_SHA_MISMATCH")
    rewritten = [f for f in manifest["files"] if f["status"] == "rewritten"]
    for record in rewritten:
        if c.sha256_file(record["backup"]) != record["sha256_before"]:
            raise VerifyInputError("VERIFY_BACKUP_SHA_MISMATCH backup=%s" % record["backup"])
        if c.sha256_file(under(manifest["data_root"], record["part"])) != record["sha256_after_actual"]:
            raise VerifyInputError("VERIFY_CURRENT_SHA_MISMATCH part=%s" % record["part"])
    return manifest, loaded, rewritten


def _work_dir(opts: VerifyOptions, data_root: str) -> str:
    work = opts.work_dir or tempfile.mkdtemp(prefix="ft_m1_verify_")
    if c.paths_overlap(work, data_root):
        raise VerifyInputError("VERIFY_WORK_DIR_INSIDE_DATA_ROOT work_dir=%s" % work)
    if os.path.isdir(work) and os.listdir(work):
        raise VerifyInputError("VERIFY_WORK_DIR_NOT_EMPTY work_dir=%s" % work)
    return work


def _changed_fields(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    return sorted(field for field in set(old) | set(new) if old.get(field) != new.get(field))


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    parser = argparse.ArgumentParser(prog="python -m tools.repair.first_tick_m1 verify")
    parser.add_argument("--apply-manifest", required=True)
    parser.add_argument("--work-dir")
    return run_verify(VerifyOptions(**vars(parser.parse_args(argv))))
