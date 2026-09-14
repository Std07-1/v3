"""Дочірній процес fetch: одна сесія FXCM на пакет діб одного символу (ADR-0096 §3.3 B).

Власник не приймає ~1000 логінів за вихідні: дитина логіниться один раз і забирає кожну добу пакета окремим
get_history у тій самій сесії. Запускається батьком `fetch` у свіжому cwd (власний кеш SDK `./History`).
Логін, кожен get_history і логаут — під власним дедлайном усередині самої дитини (`--deadline-s`,
ProcessDeadline): зависання SDK вбиває процес ядром, навіть коли батька вже вбили.

Протокол у `--out-dir` (батько не довіряє дитині і перевалідовує все): перед викликом доби — `<day>.started`;
після — `<day>.result.json` (status ok|empty|invalid|error) і для ok `<day>.jsonl`; у кінці — `session.result.json`.
Доба без result — та, на якій дитину вбили; доби без started — не почато, їх забере наступна сесія.
Коди виходу: 0 сесію завершено (статус кожної доби — у її result); 11 логін, get_history чи логаут відмовили —
сесію перервано на цій добі. Тексти помилок — без кредів. Python 3.7.
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
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.config_loader import load_system_config, pick_config_path
from env_profile import load_env_secrets
from tools.repair.first_tick_m1.common import (
    DAY_MS, REPO_ROOT, REQUEST_MARGIN_BEFORE_S, day_key, day_start_ms, log_event, parse_day_key, request_window,
    write_json_atomic,
)
from tools.repair.first_tick_m1.staging import StagingInvalid, rows_bytes, rows_from_raw, validate_rows

EXIT_OK = 0
EXIT_SDK_ERROR = 11
CREDENTIAL_ENV_KEYS = ("FXCM_USERNAME", "FXCM_PASSWORD", "FXCM_HOST_URL")
SESSION_RESULT = "session.result.json"


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


def day_files(out_dir: str, key: str) -> Tuple[str, str, str]:
    """(started, рядки, result) доби в теці сесії — спільний контракт дитини і батька."""
    base = os.path.join(out_dir, key)
    return base + ".started", base + ".jsonl", base + ".result.json"


def parse_days(text: str) -> List[dt.date]:
    """`YYYYMMDD,YYYYMMDD,...` → доби пакета в порядку виклику."""
    return [parse_day_key(key) for key in text.split(",") if key]


def main(argv: Optional[List[str]] = None, provider_factory: Optional[Callable[[Dict[str, Any]], Any]] = None,
         deadline: Optional[ProcessDeadline] = None) -> int:
    args = _parse_args(argv)
    days = parse_days(args.days)
    started = time.monotonic()
    session = {"status": "error", "stage": "config", "days": [day_key(day) for day in days], "completed": 0,
               "sdk": sdk_versions(), "error": None, "duration_s": None}
    guard = deadline if deadline is not None else ProcessDeadline(args.deadline_s)
    try:
        load_env_secrets(env_path=REPO_ROOT / ".env")
        provider = (provider_factory or _sidecar_provider)(load_system_config(pick_config_path()))
        session["stage"] = "login"
        guard.arm("login")
        with provider:
            try:
                for day in days:
                    session["stage"] = "get_history:" + day_key(day)
                    _fetch_day(provider, args, day, guard, session["sdk"])
                    session["completed"] += 1
                session["stage"] = "logout"
            finally:
                guard.arm("logout")  # логаут — і після відмови get_history — під власним дедлайном
    except Exception as exc:  # логін/SDK/конфіг: сесію перервано, решта діб не почата; текст без кредів
        guard.cancel()
        session["error"] = scrub_credentials("%s: %s" % (type(exc).__name__, exc))
        log_event(logging.ERROR, "FT_FETCH_CHILD_SESSION_ERROR", stage=session["stage"], error=session["error"])
        logging.getLogger("first_tick_m1").error("%s", scrub_credentials(traceback.format_exc()))
        _write_timed(os.path.join(args.out_dir, SESSION_RESULT), session, started, "duration_s")
        return EXIT_SDK_ERROR
    guard.cancel()
    session["status"] = "ok"
    _write_timed(os.path.join(args.out_dir, SESSION_RESULT), session, started, "duration_s")
    log_event(logging.INFO, "FT_FETCH_CHILD_SESSION_OK", symbol=args.symbol, days=len(days))
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


def _fetch_day(provider: Any, args: argparse.Namespace, day: dt.date, guard: ProcessDeadline,
               sdk: Dict[str, str]) -> None:
    """Одна доба пакета: started → get_history під дедлайном → result (+ рядки для ok). Відмова SDK — result error
    і виняток далі (сесію перервано); невалідні або порожні рядки — лише статус доби, сесія триває."""
    key = day_key(day)
    started_path, rows_path, result_path = day_files(args.out_dir, key)
    with open(started_path, "wb"):
        pass
    result = {"status": "error", "day": key, "rows": 0, "rows_outside_day_dropped": 0, "raw_open_not_tick": 0,
              "request": request_window(day), "call_duration_s": None, "sdk": sdk, "error": None}
    call_started = time.monotonic()
    start = day_start_ms(day)
    date_from = dt.datetime.fromtimestamp((start - REQUEST_MARGIN_BEFORE_S * 1000) / 1000, tz=dt.timezone.utc)
    date_to = dt.datetime.fromtimestamp((start + DAY_MS) / 1000, tz=dt.timezone.utc)
    guard.arm("get_history:" + key)
    try:
        raw_rows = provider.fetch_m1_raw_range(args.symbol, date_from, date_to)
    except Exception as exc:
        result["error"] = scrub_credentials("%s: %s" % (type(exc).__name__, exc))
        _write_timed(result_path, result, call_started, "call_duration_s")
        raise
    try:
        rows, dropped = rows_from_raw(raw_rows, day)
        result.update(rows=len(rows), rows_outside_day_dropped=dropped,
                      raw_open_not_tick=sum(1 for row in rows if row["raw_open_not_tick"]))
        if rows:
            validate_rows(args.symbol, day, rows)
    except StagingInvalid as exc:
        result.update(status="invalid", error=str(exc))
        log_event(logging.ERROR, "FT_FETCH_CHILD_STAGING_INVALID", symbol=args.symbol, day=key, error=result["error"])
        _write_timed(result_path, result, call_started, "call_duration_s")
        return
    if not rows:
        result["status"] = "empty"
        log_event(logging.WARNING, "FT_FETCH_CHILD_EMPTY", symbol=args.symbol, day=key, dropped=dropped)
        _write_timed(result_path, result, call_started, "call_duration_s")
        return
    with open(rows_path, "wb") as fh:
        fh.write(rows_bytes(rows))
        fh.flush()
        os.fsync(fh.fileno())
    result["status"] = "ok"
    _write_timed(result_path, result, call_started, "call_duration_s")
    log_event(logging.INFO, "FT_FETCH_CHILD_DAY_OK", symbol=args.symbol, day=key, rows=len(rows),
              open_not_tick=result["raw_open_not_tick"], dropped=dropped)


def _sidecar_provider(cfg: Dict[str, Any]) -> Any:
    """Фабрика live-сайдкара — SSOT логіну (креди лише з ENV)."""
    from runtime.ingest.broker_sidecar import _build_provider

    return _build_provider(cfg)


def _write_timed(path: str, payload: Dict[str, Any], started: float, field: str) -> None:
    payload[field] = round(time.monotonic() - started, 3)
    write_json_atomic(path, payload)


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
    parser.add_argument("--days", required=True, help="YYYYMMDD,YYYYMMDD,... — доби однієї сесії")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--deadline-s", type=int, required=True, help="дедлайн кожного кроку сесії, с")
    return parser.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    sys.exit(main())
