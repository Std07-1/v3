"""Дочірній процес fetch: один логін FXCM, один get_history доби, inflight-рядки + результат (ADR-0096 §3.3 B).

Запускається батьком `fetch` у свіжому cwd (власний кеш SDK `./History`) з жорстким таймаутом. Пише лише
`--out` (рядки staging) і `--result` (JSON); у staging-доби переносить батько після повторної валідації. Логін,
get_history і логаут — кожен під власним дедлайном усередині самої дитини (`--deadline-s`, ProcessDeadline):
дитина не переживає зависання SDK, навіть коли батька вже вбили.
Коди виходу: 0 ok; 11 будь-яка відмова до/під час логіну чи get_history; 12 рядки не пройшли валідацію;
13 у добі 0 рядків. Тексти помилок — без кредів. Python 3.7.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import platform
import signal
import sys
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from core.config_loader import load_system_config, pick_config_path
from env_profile import load_env_secrets
from tools.repair.first_tick_m1.common import (
    DAY_MS, REPO_ROOT, REQUEST_MARGIN_BEFORE_S, day_start_ms, log_event, parse_day_key, request_window,
    write_json_atomic,
)
from tools.repair.first_tick_m1.staging import StagingInvalid, rows_bytes, rows_from_raw, validate_rows

EXIT_OK = 0
EXIT_SDK_ERROR = 11
EXIT_STAGING_INVALID = 12
EXIT_EMPTY = 13
CREDENTIAL_ENV_KEYS = ("FXCM_USERNAME", "FXCM_PASSWORD", "FXCM_HOST_URL")


class ProcessDeadline:
    """Дедлайн кожного кроку сесії FXCM усередині самої дитини (ADR-0054 §3.6, ADR-0096 §3.3 B).

    `signal.alarm` при SIGALRM = SIG_DFL: ядро завершує процес, навіть коли той застряг у нативному логіні чи
    get_history, — без участі інтерпретатора і без батька (його можуть убити Ctrl+C чи SIGHUP раніше за його
    таймаут). Кожен крок перевзводить будильник на повний дедлайн. Без `signal.alarm` (Windows) — видно в лозі,
    лишається таймаут батька.
    """

    def __init__(self, seconds: int, alarm: Optional[Callable[[int], Any]] = None) -> None:
        if seconds <= 0:
            raise ValueError("FT_FETCH_CHILD_DEADLINE_INVALID seconds=%r" % (seconds,))
        self.seconds = seconds
        self._alarm = alarm if alarm is not None else getattr(signal, "alarm", None)
        if alarm is None and self._alarm is not None:
            signal.signal(signal.SIGALRM, signal.SIG_DFL)  # type: ignore[attr-defined]
        if self._alarm is None:
            log_event(logging.WARNING, "FT_FETCH_CHILD_DEADLINE_UNAVAILABLE", platform=sys.platform)

    def arm(self, stage: str) -> None:
        """Перевзвести будильник перед кроком; останній ARMED у лозі дитини — крок, на якому її вбили."""
        log_event(logging.INFO, "FT_FETCH_CHILD_DEADLINE_ARMED", stage=stage, seconds=self.seconds)
        if self._alarm is not None:
            self._alarm(self.seconds)

    def cancel(self) -> None:
        if self._alarm is not None:
            self._alarm(0)


def main(argv: Optional[List[str]] = None, provider_factory: Optional[Callable[[Dict[str, Any]], Any]] = None,
         deadline: Optional[ProcessDeadline] = None) -> int:
    args = _parse_args(argv)
    day = parse_day_key(args.day)
    started = time.monotonic()
    result = {"status": "error", "rows": 0, "rows_outside_day_dropped": 0, "raw_open_not_tick": 0,
              "request": request_window(day), "call_duration_s": None, "sdk": sdk_versions(), "error": None}
    guard = deadline if deadline is not None else ProcessDeadline(args.deadline_s)
    try:
        raw_rows = _fetch_raw(args.symbol, day, provider_factory, guard)
    except Exception as exc:  # будь-яка відмова логіну/SDK/конфігу — один код 11, текст без кредів
        guard.cancel()
        return _fail(result, args.result, started, EXIT_SDK_ERROR, "FT_FETCH_CHILD_SDK_ERROR", exc)
    guard.cancel()
    try:
        rows, dropped = rows_from_raw(raw_rows, day)
        result.update(rows=len(rows), rows_outside_day_dropped=dropped,
                      raw_open_not_tick=sum(1 for row in rows if row["raw_open_not_tick"]))
        if rows:
            validate_rows(args.symbol, day, rows)
    except StagingInvalid as exc:
        return _fail(result, args.result, started, EXIT_STAGING_INVALID, "FT_FETCH_CHILD_STAGING_INVALID", exc)
    if not rows:
        result["status"] = "empty"
        log_event(logging.WARNING, "FT_FETCH_CHILD_EMPTY", symbol=args.symbol, day=args.day, dropped=dropped)
        _write_result(args.result, result, started)
        return EXIT_EMPTY
    with open(args.out, "wb") as fh:
        fh.write(rows_bytes(rows))
        fh.flush()
        os.fsync(fh.fileno())
    result["status"] = "ok"
    _write_result(args.result, result, started)
    log_event(logging.INFO, "FT_FETCH_CHILD_OK", symbol=args.symbol, day=args.day, rows=len(rows),
              open_not_tick=result["raw_open_not_tick"], dropped=dropped)
    return EXIT_OK


def sdk_versions() -> Dict[str, str]:
    return {"python": platform.python_version(), "forexconnect": _forexconnect_version()}


def scrub_credentials(text: str) -> str:
    """Прибрати значення кредів FXCM з тексту помилки чи traceback перед записом у лог і результат."""
    for key in CREDENTIAL_ENV_KEYS:
        value = (os.environ.get(key) or "").strip()
        if value:
            text = text.replace(value, "***")
    return text


def _fetch_raw(symbol: str, day: dt.date, provider_factory: Optional[Callable[[Dict[str, Any]], Any]],
               guard: ProcessDeadline) -> list:
    load_env_secrets(env_path=REPO_ROOT / ".env")
    provider = (provider_factory or _sidecar_provider)(load_system_config(pick_config_path()))
    start = day_start_ms(day)
    date_from = dt.datetime.fromtimestamp((start - REQUEST_MARGIN_BEFORE_S * 1000) / 1000, tz=dt.timezone.utc)
    date_to = dt.datetime.fromtimestamp((start + DAY_MS) / 1000, tz=dt.timezone.utc)
    guard.arm("login")
    with provider:
        try:
            guard.arm("get_history")
            return provider.fetch_m1_raw_range(symbol, date_from, date_to)
        finally:
            guard.arm("logout")  # логаут — і після відмови get_history — під власним дедлайном


def _sidecar_provider(cfg: Dict[str, Any]) -> Any:
    """Фабрика live-сайдкара — SSOT логіну (креди лише з ENV)."""
    from runtime.ingest.broker_sidecar import _build_provider

    return _build_provider(cfg)


def _fail(result: Dict[str, Any], result_path: str, started: float, code: int, event: str, exc: Exception) -> int:
    result["status"] = "error"
    result["error"] = scrub_credentials("%s: %s" % (type(exc).__name__, exc))
    log_event(logging.ERROR, event, exit=code, error=result["error"])
    logging.getLogger("first_tick_m1").error("%s", scrub_credentials(traceback.format_exc()))
    _write_result(result_path, result, started)
    return code


def _write_result(path: str, result: Dict[str, Any], started: float) -> None:
    result["call_duration_s"] = round(time.monotonic() - started, 3)
    write_json_atomic(path, result)


def _forexconnect_version() -> str:
    try:
        return _distribution_version("forexconnect")
    except Exception as exc:  # версія лише довідкова в маніфесті — відсутність не зупиняє виклик, але видима
        log_event(logging.WARNING, "FT_FETCH_CHILD_SDK_VERSION_UNKNOWN", err="%s: %s" % (type(exc).__name__, exc))
        return "unknown"


def _distribution_version(name: str) -> str:
    try:
        from importlib import metadata
    except ImportError:  # Python 3.7: importlib.metadata лише з 3.8
        import pkg_resources

        return str(pkg_resources.get_distribution(name).version)
    return str(metadata.version(name))


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.repair.first_tick_m1.fetch_child")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--day", required=True, help="YYYYMMDD")
    parser.add_argument("--out", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--deadline-s", type=int, required=True, help="дедлайн кожного кроку сесії, с")
    return parser.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    sys.exit(main())
