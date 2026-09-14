"""Фаза plan: класифікація кожного ключа M1 діапазону проти staging FIRST_TICK — лише читання (ADR-0096 §3.3 B).

Входи: part-файли діб [from, to] і доби staging [from−1, to+1] (контекстні доби — лише для ланцюжка «запечених»
рядків через північ). Вихід: plan_dir з PLAN.json і entries по добі; sha кожного входу, sha файла після заміни.
rc: 0 план записано; 1 план записано, але є відмовлені part-файли (їхні ключі не ремонтуються); 2 плану немає.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from core.config_loader import load_system_config, pick_config_path
from runtime.ingest.broker.fxcm.provider import OPEN_PRICE_MODE_NAME
from runtime.ingest.polling import m1_poller
from runtime.ingest.tick_common import resolve_symbol_calendars
from tools.repair.first_tick_m1 import common as c
from tools.repair.first_tick_m1 import plan_io
from tools.repair.first_tick_m1.classify import (
    CATEGORIES, ClassifyContext, PlanInvariantBroken, baked_scan, classify_key, extra_entry,
)
from tools.repair.first_tick_m1.ssot_part import Patch, lines_bytes, render_patched_lines, scan_part
from tools.repair.first_tick_m1.staging import StagingInvalid, load_day
from tools.repair.jsonl_rewrite import key_groups


@dataclasses.dataclass(frozen=True)
class PlanOptions:
    symbol: str
    day_from: dt.date
    day_to: dt.date
    staging_root: str
    plan_dir: str
    data_root: Optional[str] = None
    close_eps: float = c.CLOSE_EPS_DEFAULT


class PlanRefused(Exception):
    pass


def run_plan(opts: PlanOptions, cfg: Dict[str, Any]) -> int:
    try:
        plan, entries = build_plan(opts, cfg)
    except PlanRefused as refused:
        print("FT_PLAN_SUMMARY symbol=%s rc=2 refused=%s" % (opts.symbol, str(refused).split(" ")[0]))
        return 2
    plan_id = plan_io.write_plan(opts.plan_dir, plan, entries)
    totals = plan["totals"]
    print("FT_PLAN_SUMMARY symbol=%s days=%s..%s %s" % (
        opts.symbol, plan["day_from"], plan["day_to"], " ".join("%s=%s" % kv for kv in sorted(totals.items()))))
    print("FT_PLAN_ID sha256=%s plan_dir=%s" % (plan_id, opts.plan_dir))
    return 1 if totals["refused_files"] else 0


def build_plan(opts: PlanOptions, cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, bytes]]:
    data_root, calendar, group = _check_rails(opts, cfg)
    flat_max = _apply_flat_threshold(cfg)
    days = c.days_between(opts.day_from, opts.day_to)
    context = c.days_between(opts.day_from - dt.timedelta(days=1), opts.day_to + dt.timedelta(days=1))
    staged = {day: _load_staged(opts, day) for day in context}
    part_path = {day: plan_io.under(data_root, plan_io.rel_part(opts.symbol, c.day_key(day))) for day in context}
    chain = [(c.day_key(d), staged[d].rows if staged[d] else None, os.path.exists(part_path[d])) for d in context]
    scan = baked_scan(chain, opts.close_eps)
    ctx = ClassifyContext(calendar, opts.close_eps, scan.baked_keys, scan.suspect_days)
    files, warnings, entries, parts = [], [], {}, []
    for day in days:
        item, records, digest = _plan_day(opts, day, part_path[day], staged[day], ctx, warnings)
        files.append(item)
        parts.append(dict(day=item["day"], path=item["part"], sha256=digest[0], bytes=digest[1]))
        if item["entries"]:
            entries[item["entries"]] = plan_io.entries_bytes(records)
            item["entries_sha256"] = c.sha256_bytes(entries[item["entries"]])
    planned_keys = {c.day_key(day) for day in days}
    for day in days:
        if c.day_key(day) in scan.suspect_days:
            warnings.append(_warning("PLAN_DAY_SUSPECT_BAKED", day, "eq_prev_share=%s" % scan.eq_prev_share[c.day_key(day)]))
    for run in scan.edge_runs:
        warnings.append(_warning("PLAN_BAKED_RUN_AT_CONTEXT_EDGE", c.day_of_ms(run["first_open_ms"]), str(run)))
    plan = {
        "format": plan_io.PLAN_FORMAT, "tool_version": c.TOOL_VERSION, "symbol": opts.symbol, "tf_s": c.TF_S,
        "day_from": c.day_key(opts.day_from), "day_to": c.day_key(opts.day_to),
        "params": {"close_eps": opts.close_eps, "flat_bar_max_volume": flat_max, "open_price_mode": OPEN_PRICE_MODE_NAME,
                   "calendar_group": group, "calendar": dataclasses.asdict(calendar),
                   "suspect_eq_prev_share": c.SUSPECT_EQ_PREV_SHARE, "suspect_min_rows": c.SUSPECT_MIN_ROWS},
        "inputs": {"parts": parts, "staging": [_staging_input(opts, day, staged[day]) for day in context]},
        "files": files, "totals": _totals(files), "baked_runs": list(scan.runs),
        "day_eq_prev_share": {k: v for k, v in scan.eq_prev_share.items() if k in planned_keys},
        "warnings": warnings,
    }
    return plan, entries


def _plan_day(opts: PlanOptions, day: dt.date, path: str, staged: Any, ctx: ClassifyContext,
              warnings: List[Dict[str, str]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Tuple[Any, Any]]:
    key = c.day_key(day)
    staged_rows = {row["open_time_ms"]: row for row in staged.rows} if staged else {}
    item = {"day": key, "part": plan_io.rel_part(opts.symbol, key), "status": "absent", "refuse_reason": None,
            "rewrite": False, "lines": None, "sha256_before": None, "sha256_after": None, "entries": None,
            "entries_sha256": None, "counts": {cat: 0 for cat in CATEGORIES}, "trading_flat_add": 0,
            "unparsable_lines": 0, "keys_refused": 0, "v_differs": 0, "same_suspect": 0}
    records: List[Dict[str, Any]] = []
    has_part = os.path.exists(path)
    if not has_part:
        if staged:
            warnings.append(_warning("PLAN_PART_ABSENT", day, "staging rows=%d" % len(staged_rows)))
            records = [extra_entry(row) for row in staged.rows]
        digest = (None, None)
    else:
        part = scan_part(path, opts.symbol, day)
        digest = (part.sha256, part.size)
        item.update(status=part.status, refuse_reason=part.refuse_reason, lines=len(part.lines),
                    sha256_before=part.sha256, unparsable_lines=part.unparsable_lines)
        if staged is None:
            warnings.append(_warning("PLAN_STAGING_DAY_ABSENT", day, "part keys=%d" % len(part.winners)))
        if part.status == "refused":
            item["keys_refused"] = len(key_groups(list(part.lines)))
            warnings.append(_warning("PLAN_PART_REFUSED", day, part.refuse_reason))
        else:
            records = _classify_part(part, staged_rows, ctx, item, warnings, day)
    for record in records:
        item["counts"][record["cat"]] += 1
        item["trading_flat_add"] += int(bool(record.get("trading_flat_add")))
        item["v_differs"] += int(bool(record.get("v_differs")))
        item["same_suspect"] += int(bool(record.get("suspect")))
    if has_part or staged:
        item["entries"] = plan_io.rel_entries(opts.symbol, key)
    return item, records, digest


def _classify_part(part: Any, staged_rows: Dict[int, Dict[str, Any]], ctx: ClassifyContext, item: Dict[str, Any],
                   warnings: List[Dict[str, str]], day: dt.date) -> List[Dict[str, Any]]:
    try:
        records = [classify_key(winner, staged_rows.get(k), ctx) for k, winner in part.winners.items()]
    except PlanInvariantBroken as exc:
        raise PlanRefused(c.log_event(logging.ERROR, "PLAN_INVARIANT_BROKEN", day=c.day_key(day), detail=exc))
    records += [extra_entry(row) for k, row in staged_rows.items() if k not in part.winners]
    records.sort(key=lambda record: record["k"])
    patches = {r["line"]: Patch(r["new"]["o"], r["new"]["h"], r["new"]["low"], r["trading_flat_add"])
               for r in records if r["cat"] == "REPLACE"}
    if patches:
        item.update(rewrite=True, sha256_after=c.sha256_bytes(lines_bytes(render_patched_lines(part.lines, patches))))
    if part.unparsable_lines:
        warnings.append(_warning("PLAN_UNPARSABLE_LINES", day, "lines=%d" % part.unparsable_lines))
    v_differs = sum(1 for r in records if r.get("v_differs"))
    if v_differs:
        warnings.append(_warning("PLAN_V_DIFFERS", day, "keys=%d" % v_differs))
    return records


def _totals(files: List[Dict[str, Any]]) -> Dict[str, int]:
    totals = {cat: sum(f["counts"][cat] for f in files) for cat in CATEGORIES}
    totals["keys"] = sum(totals[cat] for cat in CATEGORIES if cat != "EXTRA_IN_STAGING")
    for field in ("trading_flat_add", "unparsable_lines", "v_differs", "same_suspect"):
        totals[field] = sum(f[field] for f in files)
    totals["keys_in_refused_files"] = sum(f["keys_refused"] for f in files)
    totals["refused_files"] = sum(1 for f in files if f["status"] == "refused")
    totals["rewrite_files"] = sum(1 for f in files if f["rewrite"])
    return totals


def _check_rails(opts: PlanOptions, cfg: Dict[str, Any]) -> Tuple[str, Any, str]:
    if opts.day_from > opts.day_to or len(c.days_between(opts.day_from, opts.day_to)) > c.PLAN_MAX_DAYS:
        raise _refuse("FT_PLAN_BAD_RANGE", day_from=opts.day_from, day_to=opts.day_to, max_days=c.PLAN_MAX_DAYS)
    if not 0 < opts.close_eps <= c.CLOSE_EPS_MAX:
        raise _refuse("FT_PLAN_CLOSE_EPS_OUT_OF_RANGE", close_eps=opts.close_eps, max=c.CLOSE_EPS_MAX)
    if os.path.isfile(opts.plan_dir) or (os.path.isdir(opts.plan_dir) and os.listdir(opts.plan_dir)):
        raise _refuse("FT_PLAN_DIR_NOT_EMPTY", plan_dir=opts.plan_dir)
    data_root = c.resolve_data_root(cfg, opts.data_root)
    for name, path, other in (("plan_dir", opts.plan_dir, data_root), ("staging_root", opts.staging_root, data_root),
                              ("plan_dir", opts.plan_dir, opts.staging_root)):
        if c.paths_overlap(path, other):
            raise _refuse("FT_PLAN_ROOTS_OVERLAP", root=name, path=path, other=other)
    problem = c.fxcm_symbol_problem(cfg, opts.symbol)
    if problem:
        raise _refuse("FT_PLAN_SYMBOL_NOT_FXCM", symbol=opts.symbol, reason=problem)
    calendars, _rejected = resolve_symbol_calendars(cfg, [opts.symbol], where="ft_m1_plan")
    if opts.symbol not in calendars:
        raise _refuse("FT_PLAN_NO_CALENDAR", symbol=opts.symbol)
    return data_root, calendars[opts.symbol], cfg["market_calendar_symbol_groups"][opts.symbol]


def _apply_flat_threshold(cfg: Dict[str, Any]) -> int:
    """Поріг пласкості — як у live-інжесту (m1_ingestion_worker): з config або дефолт m1_poller, записується в план."""
    raw = cfg.get("flat_bar_max_volume")
    m1_poller.set_flat_bar_max_volume(int(raw) if raw is not None else m1_poller._FLAT_BAR_MAX_VOLUME_DEFAULT)
    return int(m1_poller._flat_bar_max_volume)


def _load_staged(opts: PlanOptions, day: dt.date) -> Any:
    try:
        return load_day(opts.staging_root, opts.symbol, day)
    except StagingInvalid as exc:
        raise _refuse("FT_PLAN_STAGING_INVALID", day=c.day_key(day), reason=exc.reason, detail=exc.detail)


def _staging_input(opts: PlanOptions, day: dt.date, staged: Any) -> Dict[str, Any]:
    planned = opts.day_from <= day <= opts.day_to
    return {"day": c.day_key(day), "role": "planned" if planned else "context",
            "path": plan_io.rel_staging(opts.symbol, c.day_key(day)),
            "sha256": staged.sha256_file if staged else None,
            "manifest_sha256": staged.sha256_manifest if staged else None}


def _warning(code: str, day: dt.date, detail: Optional[str]) -> Dict[str, str]:
    c.log_event(logging.WARNING, code, day=c.day_key(day), detail=detail)
    return {"code": code, "day": c.day_key(day), "detail": detail or ""}


def _refuse(code: str, **fields: Any) -> PlanRefused:
    return PlanRefused(c.log_event(logging.ERROR, code, **fields))


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    parser = argparse.ArgumentParser(prog="python -m tools.repair.first_tick_m1 plan")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--from", dest="day_from", required=True, type=c.parse_day)
    parser.add_argument("--to", dest="day_to", required=True, type=c.parse_day)
    parser.add_argument("--staging-root", required=True)
    parser.add_argument("--plan-dir", required=True)
    parser.add_argument("--data-root")
    parser.add_argument("--close-eps", type=float, default=c.CLOSE_EPS_DEFAULT)
    return run_plan(PlanOptions(**vars(parser.parse_args(argv))), load_system_config(pick_config_path()))
