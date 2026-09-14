"""Фаза fetch: перезабір M1 з FXCM FIRST_TICK у staging поза data_v3 під рейками (ADR-0096 §3.3 B). Python 3.7.

Батько не логіниться в FXCM; кожна доба — окремий дочірній процес (`fetch_call`) зі свіжим cwd і жорстким
таймаутом. Перед кожним викликом — ліміт викликів, ліміт відмов поспіль, пауза між логінами і рейка
закритого ринку (`fetch_rails`). Після кожного виклику — маніфест прогону `_runs/<run_id>.json`.
rc: 0 усе закомічено або законно пропущено; 1 є невдалі доби; 2 відмова до виклику; 3 зупинка рейкою.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import logging
import os
import time
from typing import Any, Dict, List, Optional

from core.config_loader import env_str, load_system_config, pick_config_path
from env_profile import load_env_secrets
from tools.repair.first_tick_m1 import common as c
from tools.repair.first_tick_m1.fetch_call import execute_call, remove_inflight
from tools.repair.first_tick_m1.fetch_child import CREDENTIAL_ENV_KEYS
from tools.repair.first_tick_m1.fetch_rails import (
    FetchContext, FetchDeps, FetchOptions, FetchRefused, check_rails, market_open_reason,
)
from tools.repair.first_tick_m1.fetch_runner import run_child
from tools.repair.first_tick_m1.staging import StagingInvalid, load_day

__all__ = ["FetchDeps", "FetchOptions", "main", "run_fetch"]


def run_fetch(opts: FetchOptions, deps: FetchDeps) -> int:
    try:
        ctx = check_rails(opts, deps)
    except FetchRefused as refused:
        print("FT_FETCH_SUMMARY symbol=%s calls=0 rc=2 refused=%s" % (opts.symbol, refused.text.split(" ")[0]))
        return 2
    if opts.dry_run:
        return _dry_run(opts, deps, ctx)
    try:
        with c.exclusive_lock(os.path.join(ctx.staging_root, "_fetch.lock")):
            return _locked_run(opts, deps, ctx)
    except c.LockHeld as held:
        c.log_event(logging.ERROR, "FT_FETCH_LOCK_HELD", path=held.path, holder=held.holder)
        return 2


def _locked_run(opts: FetchOptions, deps: FetchDeps, ctx: FetchContext) -> int:
    now_ms = deps.now_ms()
    run_id = "%s-%d" % (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now_ms // 1000)), os.getpid())
    _clean_inflight(ctx.staging_root)
    run = _run_manifest(opts, ctx, run_id, now_ms)
    queue = _day_queue(opts, ctx, run["skipped"])
    calls: List[Dict[str, Any]] = run["calls"]
    stop, failures_in_row = market_open_reason(ctx, opts, now_ms), 0
    for index, day in enumerate(queue):
        stop = stop or _stop_before_call(opts, deps, ctx, calls, failures_in_row, len(queue) - index, day)
        if stop:
            break
        record = execute_call(ctx, opts, deps, run_id, len(calls) + 1, day)
        calls.append(dataclasses.asdict(record))
        failures_in_row = 0 if record.status == "ok" else failures_in_row + 1
        if record.status == "unkillable":
            stop = c.log_event(logging.ERROR, "FT_FETCH_STOPPED_UNKILLABLE_CHILD", day=record.day)
        c.write_json_atomic(_run_path(ctx, run_id), run)
    failed = sum(1 for call in calls if call["status"] != "ok")
    rc = 3 if stop else (1 if failed else 0)
    run.update(finished_at_utc=c.utc_iso(deps.now_ms()), stop_reason=stop, rc=rc)
    c.write_json_atomic(_run_path(ctx, run_id), run)
    print("FT_FETCH_SUMMARY symbol=%s calls=%d committed=%d failed=%d skipped=%d rc=%d run=%s" % (
        opts.symbol, len(calls), len(calls) - failed, failed, len(run["skipped"]), rc, _run_path(ctx, run_id)))
    return rc


def _stop_before_call(opts: FetchOptions, deps: FetchDeps, ctx: FetchContext, calls: List[Dict[str, Any]],
                      failures_in_row: int, days_left: int, day: dt.date) -> Optional[str]:
    """Рейки перед викликом: ліміт викликів → ліміт відмов поспіль → пауза між логінами → закритий ринок."""
    if len(calls) == opts.max_calls:
        return c.log_event(logging.ERROR, "FT_FETCH_MAX_CALLS_REACHED", days_left=days_left, next_day=c.day_key(day))
    if failures_in_row == opts.max_consecutive_failures:
        return c.log_event(logging.ERROR, "FT_FETCH_TOO_MANY_FAILURES", in_row=failures_in_row,
                           next_day=c.day_key(day))
    if calls:
        deps.sleep(opts.call_interval_s)
    return market_open_reason(ctx, opts, deps.now_ms())


def _day_queue(opts: FetchOptions, ctx: FetchContext, skipped: List[Dict[str, str]]) -> List[dt.date]:
    """Доби до виклику: без part-файла — не виклик; з --only-missing валідна доба staging — пропуск."""
    queue = []
    for day in ctx.days:
        key = c.day_key(day)
        if not os.path.exists(os.path.join(ctx.data_root, c.sym_dir(opts.symbol), "tf_60", "part-%s.jsonl" % key)):
            skipped.append({"day": key, "reason": "no_part"})
            c.log_event(logging.INFO, "FT_FETCH_DAY_SKIPPED_NO_PART", day=key)
            continue
        if opts.only_missing and _staged_valid(ctx, opts, day):
            skipped.append({"day": key, "reason": "staged"})
            c.log_event(logging.INFO, "FT_FETCH_DAY_SKIPPED_STAGED", day=key)
            continue
        queue.append(day)
    return queue


def _staged_valid(ctx: FetchContext, opts: FetchOptions, day: dt.date) -> bool:
    try:
        return load_day(ctx.staging_root, opts.symbol, day) is not None
    except StagingInvalid as exc:
        c.log_event(logging.WARNING, "FT_FETCH_DAY_STAGING_INVALID_REFETCH", day=c.day_key(day), reason=exc.reason)
        return False


def _dry_run(opts: FetchOptions, deps: FetchDeps, ctx: FetchContext) -> int:
    queue = _day_queue(opts, ctx, [])
    for day in queue:
        print("FT_FETCH_DRY_RUN day=%s decision=fetch" % c.day_key(day))
    rc = 3 if market_open_reason(ctx, opts, deps.now_ms()) else 0
    print("FT_FETCH_SUMMARY symbol=%s calls=0 planned=%d dry_run=1 rc=%d" % (opts.symbol, len(queue), rc))
    return rc


def _run_manifest(opts: FetchOptions, ctx: FetchContext, run_id: str, now_ms: int) -> Dict[str, Any]:
    args = {key: (value.isoformat() if isinstance(value, dt.date) else value)
            for key, value in dataclasses.asdict(opts).items()}
    return {"format": "ft_m1_fetch_run_v1", "run_id": run_id, "tool_version": c.TOOL_VERSION, "symbol": opts.symbol,
            "args": args, "data_root": ctx.data_root, "staging_root": ctx.staging_root, "sdk_cwd": ctx.sdk_cwd,
            "started_at_utc": c.utc_iso(now_ms), "finished_at_utc": None,
            "rails": {"calendar_group": ctx.calendar_group, "guard_minutes": opts.guard_minutes,
                      "call_timeout_s": opts.call_timeout_s, "max_calls": opts.max_calls,
                      "min_age_days": opts.min_age_days},
            "calls": [], "skipped": [], "stop_reason": None, "rc": None}


def _run_path(ctx: FetchContext, run_id: str) -> str:
    runs = os.path.join(ctx.staging_root, "_runs")
    os.makedirs(runs, exist_ok=True)
    return os.path.join(runs, run_id + ".json")


def _clean_inflight(staging_root: str) -> None:
    inflight = os.path.join(staging_root, "_inflight")
    stale = sorted(os.listdir(inflight)) if os.path.isdir(inflight) else []
    for name in stale:
        remove_inflight(os.path.join(inflight, name))
    if stale:
        c.log_event(logging.WARNING, "FT_FETCH_INFLIGHT_STALE_REMOVED", n=len(stale))


def _env_has_credentials() -> bool:
    load_env_secrets(env_path=c.REPO_ROOT / ".env")
    return all(env_str(key) for key in CREDENTIAL_ENV_KEYS)


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    parser = argparse.ArgumentParser(prog="python -m tools.repair.first_tick_m1 fetch")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--from", dest="day_from", required=True, type=c.parse_day)
    parser.add_argument("--to", dest="day_to", required=True, type=c.parse_day)
    parser.add_argument("--staging-root", required=True)
    parser.add_argument("--sdk-cwd", required=True)
    parser.add_argument("--data-root")
    for flag, default in (("--max-calls", c.MAX_CALLS_DEFAULT), ("--call-timeout-s", c.CALL_TIMEOUT_DEFAULT_S),
                          ("--call-interval-s", c.CALL_INTERVAL_DEFAULT_S), ("--guard-minutes", c.GUARD_MINUTES_DEFAULT),
                          ("--min-age-days", c.MIN_AGE_DAYS_DEFAULT),
                          ("--max-consecutive-failures", c.MAX_CONSECUTIVE_FAILURES_DEFAULT)):
        parser.add_argument(flag, type=int, default=default)
    parser.add_argument("--only-missing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    opts = FetchOptions(**vars(parser.parse_args(argv)))
    deps = FetchDeps(now_ms=lambda: int(time.time() * 1000), sleep=time.sleep, run_child=run_child,
                     load_cfg=lambda: load_system_config(pick_config_path()), getcwd=os.getcwd,
                     env_has_credentials=_env_has_credentials)
    return run_fetch(opts, deps)
