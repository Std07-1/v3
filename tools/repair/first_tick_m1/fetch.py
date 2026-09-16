"""Фаза fetch: перезабір M1 з FXCM FIRST_TICK у staging поза data_v3 під рейками (ADR-0096 §3.3 B). Python 3.7.

Батько не логіниться в FXCM. Одна дитина (`fetch_call`) = одна сесія FXCM на пакет до --days-per-session діб
(свіжий cwd, дедлайн кожного get_history всередині дитини, таймаут батька на всю сесію). Перед кожною сесією —
ліміт викликів get_history, ліміт логінів (сесія без жодної доби теж логін), ліміт відмов поспіль, пауза між
логінами і рейка закритого ринку на всю сесію (`fetch_rails`). Дитину вбили посеред доби — доба невдала, наступна
сесія починає з наступної доби; доби, яких дитина не почала, лишаються в черзі. Після кожної сесії — маніфест
прогону `_runs/<run_id>.json`. SIGTERM/SIGHUP/Ctrl+C
посеред сесії вбивають дитину; доби, які вона встигла віддати, комітяться, сесія `stopped` з ними — у маніфест;
далі маніфест фіналізовано. Фінальний запис маніфесту — ще під обробниками.
rc: 0 усе закомічено або законно пропущено; 1 є невдалі доби або сесії (зокрема логаут, що відмовив після
останньої доби); 2 відмова до виклику; 3 зупинка рейкою;
128+signum зупинено сигналом (і коли сигнал прийшов посеред фіналізації).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from core.config_loader import env_str, load_system_config, pick_config_path
from env_profile import load_env_secrets
from tools.repair.first_tick_m1 import common as c
from tools.repair.first_tick_m1.fetch_call import ParentStop, SessionOutcome, execute_session, remove_path
from tools.repair.first_tick_m1.fetch_child import CREDENTIAL_ENV_KEYS, SESSION_RESULT
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
        c.log_event(logging.ERROR, "FT_FETCH_LOCK_HELD", path=held.path, holder=held.holder, reason=held.reason)
        return 2


def _locked_run(opts: FetchOptions, deps: FetchDeps, ctx: FetchContext) -> int:
    now_ms = deps.now_ms()
    run_id = "%s-%d" % (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now_ms // 1000)), os.getpid())
    _clean_inflight(ctx.staging_root)
    run = _run_manifest(opts, ctx, run_id, now_ms)
    queue = _day_queue(opts, ctx, run["skipped"])
    with c.StopSignals("FT_FETCH") as signals:
        try:
            stop = _run_sessions(opts, deps, ctx, run, run_id, queue, signals)
            signals.disarm()
        except BaseException as exc:
            # Сигнал, Ctrl+C чи відмова самого батька посеред прогону: дитину вже вбив run_child, отримані доби
            # закомічено й записано сесією `stopped` (fetch_call); маніфест прогону фіксує, на якій добі зупинились.
            # Сигнал — rc 128+signum, решта летить далі.
            signals.disarm()
            stopped_by_signal = isinstance(exc, c.StopSignal)
            run.update(stop_reason=c.log_event(
                logging.ERROR, "FT_FETCH_STOPPED_BY_SIGNAL" if stopped_by_signal else "FT_FETCH_CRASHED",
                in_flight=_in_flight_text(run), err="%s: %s" % (type(exc).__name__, exc)),
                finished_at_utc=c.utc_iso(deps.now_ms()), rc=exc.exit_code if stopped_by_signal else None)
            c.write_json_atomic(_run_path(ctx, run_id), run)
            if not stopped_by_signal:
                raise
            _print_summary(opts, ctx, run, run_id)
            return exc.exit_code
        # Фіналізація — ще під обробниками: сигнал у мить запису маніфесту відкладено й чесно повернуто як 128+signum.
        rc = c.finalize_under_signals(signals, lambda signum: _finalize_run(ctx, deps, run, run_id, stop, signum))
    _print_summary(opts, ctx, run, run_id)
    return rc


def _finalize_run(ctx: FetchContext, deps: FetchDeps, run: Dict[str, Any], run_id: str, stop: Optional[str],
                  signum: Optional[int]) -> int:
    """Фінальний маніфест завершеного прогону: rc за добами, сесіями і рейками, або 128+signum, якщо прийшов сигнал."""
    failed = sum(1 for call in run["calls"] if call["status"] != "ok")
    sessions_failed = sum(1 for session in run["sessions"] if session["status"] != "ok")
    rc = 3 if stop else (1 if failed or sessions_failed else 0)
    if signum is not None:
        rc = 128 + signum
        stop = stop or c.log_event(logging.WARNING, "FT_FETCH_SIGNAL_DURING_FINALIZE", signal=signum)
    run.update(finished_at_utc=c.utc_iso(deps.now_ms()), stop_reason=stop, rc=rc)
    c.write_json_atomic(_run_path(ctx, run_id), run)
    return rc


def _run_sessions(opts: FetchOptions, deps: FetchDeps, ctx: FetchContext, run: Dict[str, Any], run_id: str,
                  queue: List[dt.date], signals: c.StopSignals) -> Optional[str]:
    """Сесії по черзі під рейками; маніфест — після кожної; повертає причину зупинки або None.

    Кожна сесія — логін: їх не більше --max-logins, тож цикл скінченний, навіть коли логін відмовляє щоразу і
    жодна доба не витрачає --max-calls.
    """
    def record_stopped(stopped: SessionOutcome) -> None:
        _record_session(run, stopped)
        c.write_json_atomic(_run_path(ctx, run_id), run)

    parent_stop = ParentStop(disarm=signals.disarm, record=record_stopped)
    pending = list(queue)
    stop = market_open_reason(ctx, opts, deps.now_ms(), _batch_size(opts, run, pending))
    while pending and not stop:
        batch_size, stop = _stop_before_session(opts, deps, ctx, run, pending)
        if stop:
            break
        batch, pending = pending[:batch_size], pending[batch_size:]
        seq = len(run["sessions"]) + 1
        run["in_flight"] = {"session": seq, "days": [c.day_key(day) for day in batch]}
        outcome = execute_session(ctx, opts, deps, run_id, seq, len(run["calls"]) + 1, batch, parent_stop)
        run["in_flight"] = None
        _record_session(run, outcome)
        pending = list(outcome.unattempted) + pending
        if outcome.session.status == "unkillable":
            stop = c.log_event(logging.ERROR, "FT_FETCH_STOPPED_UNKILLABLE_CHILD", session=seq)
        c.write_json_atomic(_run_path(ctx, run_id), run)
    return stop


def _record_session(run: Dict[str, Any], outcome: SessionOutcome) -> None:
    run["sessions"].append(dataclasses.asdict(outcome.session))
    run["calls"].extend(dataclasses.asdict(record) for record in outcome.calls)


def _stop_before_session(opts: FetchOptions, deps: FetchDeps, ctx: FetchContext, run: Dict[str, Any],
                         pending: List[dt.date]) -> Tuple[int, Optional[str]]:
    """Рейки перед сесією: ліміт викликів → ліміт логінів → відмови поспіль (діб, потім сесій) → пауза → ринок
    закритий усю сесію. Відмови поспіль рахуються з хвоста маніфесту прогону: доби — `calls`, сесії — `sessions`
    (сесія не ok — логін, дедлайн, SDK, логаут після останньої доби: доби в ній ok, а сесію не закрито штатно)."""
    next_day = c.day_key(pending[0])
    if len(run["calls"]) >= opts.max_calls:
        return 0, c.log_event(logging.ERROR, "FT_FETCH_MAX_CALLS_REACHED", days_left=len(pending), next_day=next_day)
    if len(run["sessions"]) >= ctx.max_logins:
        return 0, c.log_event(logging.ERROR, "FT_FETCH_MAX_LOGINS_REACHED", logins=len(run["sessions"]),
                              days_left=len(pending), next_day=next_day)
    failures_in_row, session_failures_in_row = _trailing_failures(run["calls"]), _trailing_failures(run["sessions"])
    if failures_in_row >= opts.max_consecutive_failures:
        return 0, c.log_event(logging.ERROR, "FT_FETCH_TOO_MANY_FAILURES", in_row=failures_in_row, next_day=next_day)
    if session_failures_in_row >= opts.max_consecutive_failures:
        return 0, c.log_event(logging.ERROR, "FT_FETCH_TOO_MANY_SESSION_FAILURES", in_row=session_failures_in_row,
                              last_status=run["sessions"][-1]["status"], next_day=next_day)
    if run["sessions"]:
        deps.sleep(opts.call_interval_s)
    batch_size = _batch_size(opts, run, pending)
    return batch_size, market_open_reason(ctx, opts, deps.now_ms(), batch_size)


def _trailing_failures(records: List[Dict[str, Any]]) -> int:
    """Скільки останніх записів маніфесту поспіль — не ok."""
    count = 0
    for record in reversed(records):
        if record["status"] == "ok":
            break
        count += 1
    return count


def _batch_size(opts: FetchOptions, run: Dict[str, Any], pending: List[dt.date]) -> int:
    """Діб у наступній сесії: не більше --days-per-session, черги і залишку --max-calls (мінімум 1 для рейки ринку)."""
    return max(1, min(opts.days_per_session, len(pending), opts.max_calls - len(run["calls"])))


def _in_flight_text(run: Dict[str, Any]) -> Optional[str]:
    in_flight = run["in_flight"]
    return None if in_flight is None else "session:%d:%s" % (in_flight["session"], ",".join(in_flight["days"]))


def _print_summary(opts: FetchOptions, ctx: FetchContext, run: Dict[str, Any], run_id: str) -> None:
    calls = run["calls"]
    failed = sum(1 for call in calls if call["status"] != "ok")
    sessions_failed = sum(1 for session in run["sessions"] if session["status"] != "ok")
    print("FT_FETCH_SUMMARY symbol=%s sessions=%d sessions_failed=%d calls=%d committed=%d failed=%d skipped=%d "
          "rc=%s run=%s" % (opts.symbol, len(run["sessions"]), sessions_failed, len(calls), len(calls) - failed, failed,
                            len(run["skipped"]), run["rc"], _run_path(ctx, run_id)))


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
    """Чи можна пропустити добу в --only-missing: вона є, валідна і не обрізана (або спроби вичерпані)."""
    try:
        staged = load_day(ctx.staging_root, opts.symbol, day)
    except StagingInvalid as exc:
        c.log_event(logging.WARNING, "FT_FETCH_DAY_STAGING_INVALID_REFETCH", day=c.day_key(day), reason=exc.reason)
        return False
    if staged is None:
        return False
    coverage, attempts = staged.manifest.get("coverage"), int(staged.manifest.get("attempts", 1))
    if coverage is not None and coverage < opts.day_coverage_min and attempts < opts.max_day_attempts:
        # Обрізана доба (SDK віддав лише хвіст) інакше лишалась би «валідною» назавжди саме в тому режимі, який
        # ранбук радить для продовження. Тонкі й святкові доби приймаються після max_day_attempts спроб.
        c.log_event(logging.WARNING, "FT_FETCH_DAY_LOW_COVERAGE_REFETCH", day=c.day_key(day), coverage=coverage,
                    expected=staged.manifest.get("trading_minutes_expected"), rows=staged.manifest.get("rows"),
                    attempts=attempts)
        return False
    return True


def _dry_run(opts: FetchOptions, deps: FetchDeps, ctx: FetchContext) -> int:
    queue = _day_queue(opts, ctx, [])[:min(opts.max_calls, ctx.max_logins * opts.days_per_session)]
    for index, day in enumerate(queue):
        print("FT_FETCH_DRY_RUN day=%s session=%d decision=fetch" % (c.day_key(day), index // opts.days_per_session + 1))
    sessions = -(-len(queue) // opts.days_per_session)
    first_batch = max(1, min(opts.days_per_session, len(queue)))
    rc = 3 if market_open_reason(ctx, opts, deps.now_ms(), first_batch) else 0
    print("FT_FETCH_SUMMARY symbol=%s calls=0 planned=%d sessions=%d max_logins=%d dry_run=1 rc=%d" % (
        opts.symbol, len(queue), sessions, ctx.max_logins, rc))
    return rc


def _run_manifest(opts: FetchOptions, ctx: FetchContext, run_id: str, now_ms: int) -> Dict[str, Any]:
    args = {key: (value.isoformat() if isinstance(value, dt.date) else value)
            for key, value in dataclasses.asdict(opts).items()}
    return {"format": "ft_m1_fetch_run_v1", "run_id": run_id, "tool_version": c.TOOL_VERSION, "symbol": opts.symbol,
            "args": args, "data_root": ctx.data_root, "staging_root": ctx.staging_root, "sdk_cwd": ctx.sdk_cwd,
            "started_at_utc": c.utc_iso(now_ms), "finished_at_utc": None,
            "rails": {"calendar_group": ctx.calendar_group, "guard_minutes": opts.guard_minutes,
                      "call_timeout_s": opts.call_timeout_s, "max_calls": opts.max_calls,
                      "max_logins": ctx.max_logins, "max_consecutive_failures": opts.max_consecutive_failures,
                      "days_per_session": opts.days_per_session, "min_age_days": opts.min_age_days},
            "sessions": [], "calls": [], "skipped": [], "in_flight": None, "stop_reason": None, "rc": None}


def _run_path(ctx: FetchContext, run_id: str) -> str:
    runs = os.path.join(ctx.staging_root, "_runs")
    os.makedirs(runs, exist_ok=True)
    return os.path.join(runs, run_id + ".json")


def _clean_inflight(staging_root: str) -> None:
    """Теки сесій, які лишив батько, убитий без фіналізації (SIGKILL, OOM): прибрати; доби з result у них — у лог.

    Такі доби не комітяться: маніфест їхнього прогону не фіналізовано, а дитина могла загинути посеред запису. Вони
    видимі (FT_FETCH_INFLIGHT_STALE_DAYS) і перезабираються прогоном з --only-missing.
    """
    inflight = os.path.join(staging_root, "_inflight")
    stale = sorted(os.listdir(inflight)) if os.path.isdir(inflight) else []
    for name in stale:
        path = os.path.join(inflight, name)
        days = _inflight_result_days(path)
        if days:
            c.log_event(logging.WARNING, "FT_FETCH_INFLIGHT_STALE_DAYS", session=name, days=",".join(days),
                        action="not_committed_refetch_with_only_missing")
        remove_path(path)
    if stale:
        c.log_event(logging.WARNING, "FT_FETCH_INFLIGHT_STALE_REMOVED", n=len(stale))


def _inflight_result_days(session_dir: str) -> List[str]:
    if not os.path.isdir(session_dir):
        return []
    suffix = ".result.json"
    return sorted(name[:-len(suffix)] for name in os.listdir(session_dir)
                  if name.endswith(suffix) and name != SESSION_RESULT)


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
                          ("--max-consecutive-failures", c.MAX_CONSECUTIVE_FAILURES_DEFAULT),
                          ("--days-per-session", c.DAYS_PER_SESSION_DEFAULT),
                          ("--max-day-attempts", c.MAX_DAY_ATTEMPTS_DEFAULT)):
        parser.add_argument(flag, type=int, default=default)
    parser.add_argument("--day-coverage-min", type=float, default=c.DAY_COVERAGE_MIN_DEFAULT,
                        help=("частка торгових хвилин доби, нижче якої доба вважається обрізаною і --only-missing "
                              "забирає її ще раз (дефолт %.2f, до --max-day-attempts спроб)"
                              % c.DAY_COVERAGE_MIN_DEFAULT))
    parser.add_argument("--max-logins", type=int, default=None,
                        help="логінів FXCM за прогін (1..%d); дефолт ceil(max-calls / days-per-session)"
                        % c.MAX_LOGINS_CEILING)
    parser.add_argument("--only-missing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    opts = FetchOptions(**vars(parser.parse_args(argv)))
    deps = FetchDeps(now_ms=lambda: int(time.time() * 1000), sleep=time.sleep, run_child=run_child,
                     load_cfg=lambda: load_system_config(pick_config_path()), getcwd=os.getcwd,
                     env_has_credentials=_env_has_credentials)
    return run_fetch(opts, deps)
