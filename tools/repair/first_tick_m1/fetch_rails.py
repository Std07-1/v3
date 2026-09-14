"""Рейки фази fetch до першого виклику і рейка закритого ринку перед кожним (ADR-0096 §3.3 B, §4.2/§4.4). Python 3.7.

Порядок перевірок — як у специфікації: аргументи, FXCM-символ з part-каталогом, cwd не корінь репо (там кеш
SDK live-сайдкара), корені поза data_root і репо, календар, креди, доби завершені й не молодші за
MIN_AGE_DAYS. Перша порушена — FetchRefused (rc=2), жодного виклику брокера.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import os
import sys
from typing import Any, Callable, Dict, List, Optional

from runtime.ingest.tick_common import resolve_symbol_calendars
from tools.repair.first_tick_m1 import common as c
from tools.repair.first_tick_m1.fetch_child import CREDENTIAL_ENV_KEYS


@dataclasses.dataclass(frozen=True)
class FetchOptions:
    symbol: str
    day_from: dt.date
    day_to: dt.date
    staging_root: str
    sdk_cwd: str
    data_root: Optional[str] = None
    max_calls: int = c.MAX_CALLS_DEFAULT
    call_timeout_s: int = c.CALL_TIMEOUT_DEFAULT_S
    call_interval_s: int = c.CALL_INTERVAL_DEFAULT_S
    guard_minutes: int = c.GUARD_MINUTES_DEFAULT
    min_age_days: int = c.MIN_AGE_DAYS_DEFAULT
    max_consecutive_failures: int = c.MAX_CONSECUTIVE_FAILURES_DEFAULT
    days_per_session: int = c.DAYS_PER_SESSION_DEFAULT
    only_missing: bool = False
    dry_run: bool = False


@dataclasses.dataclass(frozen=True)
class FetchDeps:
    """Зовнішній світ фази — ін'єкції для тестів; `fetch.main` збирає справжні."""

    now_ms: Callable[[], int]
    sleep: Callable[[float], None]
    run_child: Callable[..., Any]
    load_cfg: Callable[[], Dict[str, Any]]
    getcwd: Callable[[], str]
    env_has_credentials: Callable[[], bool]
    python_executable: str = sys.executable


@dataclasses.dataclass(frozen=True)
class FetchContext:
    data_root: str
    staging_root: str
    sdk_cwd: str
    calendar: Any
    calendar_group: str
    days: List[dt.date]


class FetchRefused(Exception):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


def check_rails(opts: FetchOptions, deps: FetchDeps) -> FetchContext:
    days = _check_arguments(opts)
    cfg = deps.load_cfg()
    problem = c.fxcm_symbol_problem(cfg, opts.symbol)
    if problem:
        raise _refuse("FT_FETCH_SYMBOL_NOT_FXCM", symbol=opts.symbol, reason=problem)
    data_root = c.resolve_data_root(cfg, opts.data_root)
    if not os.path.isdir(os.path.join(data_root, c.sym_dir(opts.symbol), "tf_60")):
        raise _refuse("FT_FETCH_SYMBOL_NO_SSOT", symbol=opts.symbol, data_root=data_root)
    if c.norm_path(deps.getcwd()) == c.norm_path(c.REPO_ROOT):
        raise _refuse("FT_FETCH_CWD_IS_REPO", cwd=deps.getcwd())
    staging_root, sdk_cwd = os.path.realpath(opts.staging_root), os.path.realpath(opts.sdk_cwd)
    _check_roots(staging_root, sdk_cwd, data_root, create=not opts.dry_run)
    calendars, _rejected = resolve_symbol_calendars(cfg, [opts.symbol], where="ft_m1_fetch")
    if opts.symbol not in calendars:
        raise _refuse("FT_FETCH_NO_CALENDAR", symbol=opts.symbol)
    if not deps.env_has_credentials():
        raise _refuse("FT_FETCH_NO_CREDENTIALS", keys=",".join(CREDENTIAL_ENV_KEYS))
    now_ms = deps.now_ms()
    for day in days:
        day_end_ms = c.day_start_ms(day) + c.DAY_MS
        if day_end_ms > now_ms:
            raise _refuse("FT_FETCH_DAY_NOT_COMPLETE", day=c.day_key(day), now_utc=c.utc_iso(now_ms))
        if day_end_ms + opts.min_age_days * c.DAY_MS > now_ms:
            raise _refuse("FT_FETCH_DAY_TOO_RECENT", day=c.day_key(day), min_age_days=opts.min_age_days)
    group = cfg["market_calendar_symbol_groups"][opts.symbol]
    return FetchContext(data_root, staging_root, sdk_cwd, calendars[opts.symbol], group, days)


def market_open_reason(ctx: FetchContext, opts: FetchOptions, now_ms: int, days: int) -> Optional[str]:
    """Торгова хвилина у [now − guard, now + найдовша сесія з `days` діб + guard] — сесію заборонено; текст або None."""
    guard_ms = opts.guard_minutes * c.MINUTE_MS
    horizon_ms = c.session_timeout_s(opts.call_timeout_s, days) * 1000
    minute = c.first_trading_minute(ctx.calendar, now_ms - guard_ms, now_ms + horizon_ms + guard_ms)
    if minute is None:
        return None
    return c.log_event(logging.ERROR, "FT_FETCH_REFUSED_MARKET_OPEN", symbol=opts.symbol,
                       first_trading_minute_utc=c.utc_iso(minute), now_utc=c.utc_iso(now_ms))


def _check_arguments(opts: FetchOptions) -> List[dt.date]:
    if opts.day_from > opts.day_to:
        raise _refuse("FT_FETCH_BAD_RANGE", day_from=opts.day_from, day_to=opts.day_to)
    days = c.days_between(opts.day_from, opts.day_to)
    checks = [
        (len(days) <= c.PLAN_MAX_DAYS, "FT_FETCH_TOO_MANY_DAYS", len(days)),
        (1 <= opts.max_calls <= c.MAX_CALLS_CEILING, "FT_FETCH_MAX_CALLS_OVER_CEILING", opts.max_calls),
        (_within(opts.call_timeout_s, c.CALL_TIMEOUT_RANGE_S), "FT_FETCH_CALL_TIMEOUT_OUT_OF_RANGE", opts.call_timeout_s),
        (_within(opts.call_interval_s, c.CALL_INTERVAL_RANGE_S), "FT_FETCH_CALL_INTERVAL_OUT_OF_RANGE",
         opts.call_interval_s),
        (_within(opts.guard_minutes, c.GUARD_MINUTES_RANGE), "FT_FETCH_GUARD_OUT_OF_RANGE", opts.guard_minutes),
        (_within(opts.min_age_days, c.MIN_AGE_DAYS_RANGE), "FT_FETCH_MIN_AGE_OUT_OF_RANGE", opts.min_age_days),
        (opts.max_consecutive_failures >= 1, "FT_FETCH_BAD_FAILURE_LIMIT", opts.max_consecutive_failures),
        (_within(opts.days_per_session, c.DAYS_PER_SESSION_RANGE), "FT_FETCH_DAYS_PER_SESSION_OUT_OF_RANGE",
         opts.days_per_session),
    ]
    for ok, code, value in checks:
        if not ok:
            raise _refuse(code, value=value)
    return days


def _check_roots(staging_root: str, sdk_cwd: str, data_root: str, create: bool) -> None:
    """Корені staging і кешу SDK — поза data_root, поза репо і не один в одному; перетин перевіряється ДО mkdir."""
    pairs = [("staging_root", staging_root, "data_root", data_root), ("staging_root", staging_root, "repo", c.REPO_ROOT),
             ("sdk_cwd", sdk_cwd, "data_root", data_root), ("sdk_cwd", sdk_cwd, "repo", c.REPO_ROOT),
             ("staging_root", staging_root, "sdk_cwd", sdk_cwd)]
    for name, path, other_name, other in pairs:
        if c.paths_overlap(path, other):
            raise _refuse("FT_FETCH_ROOTS_OVERLAP", root=name, path=path, other=other_name, other_path=other)
    for path in (staging_root, sdk_cwd) if create else ():
        os.makedirs(path, exist_ok=True)
        if not os.access(path, os.W_OK):
            raise _refuse("FT_FETCH_ROOT_NOT_WRITABLE", path=path)


def _refuse(code: str, **fields: Any) -> FetchRefused:
    return FetchRefused(c.log_event(logging.ERROR, code, **fields))


def _within(value: int, bounds: Any) -> bool:
    return bounds[0] <= value <= bounds[1]
