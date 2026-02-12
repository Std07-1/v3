from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from env_profile import load_env_secrets
from core.config_loader import pick_config_path
from app.composition import ConfigError, build_connector
from app.lifecycle import run_with_shutdown
from runtime.ingest.market_calendar import MarketCalendar
from runtime.store.redis_snapshot import init_redis_snapshot


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def _load_retry_config(config_path: str) -> tuple[int, int, int]:
    base_delay_s = 10
    max_delay_s = 3600
    wake_ahead_s = 900
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        base_delay_s = int(cfg.get("connector_retry_base_s", base_delay_s))
        max_delay_s = int(cfg.get("connector_retry_max_s", max_delay_s))
        wake_ahead_s = int(cfg.get("connector_wake_ahead_s", wake_ahead_s))
    except Exception:
        logging.warning("Retry: не вдалося прочитати config.json, використовую дефолти")

    if base_delay_s < 1:
        base_delay_s = 1
    if max_delay_s < base_delay_s:
        max_delay_s = base_delay_s

    if wake_ahead_s < 0:
        wake_ahead_s = 0

    return base_delay_s, max_delay_s, wake_ahead_s


def _calendar_from_group(group_cfg: dict) -> Optional[MarketCalendar]:
    try:
        daily_breaks_raw = group_cfg.get("market_daily_breaks", [])
        daily_breaks = tuple(
            (str(pair[0]), str(pair[1]))
            for pair in daily_breaks_raw
            if isinstance(pair, (list, tuple)) and len(pair) >= 2
        )
        return MarketCalendar(
            enabled=True,
            weekend_close_dow=int(group_cfg["market_weekend_close_dow"]),
            weekend_close_hm=str(group_cfg["market_weekend_close_hm"]),
            weekend_open_dow=int(group_cfg["market_weekend_open_dow"]),
            weekend_open_hm=str(group_cfg["market_weekend_open_hm"]),
            daily_break_start_hm=str(group_cfg["market_daily_break_start_hm"]),
            daily_break_end_hm=str(group_cfg["market_daily_break_end_hm"]),
            daily_break_enabled=True,
            daily_breaks=daily_breaks,
        )
    except Exception:
        return None


def _next_open_ms(calendar: MarketCalendar, now_ms: int) -> Optional[int]:
    step_ms = 60_000
    for _ in range(8 * 24 * 60):
        now_ms += step_ms
        if calendar.is_trading_minute(now_ms):
            return now_ms
    return None


def _calendar_sleep_s(config_path: str, now_ms: int, wake_ahead_s: int) -> Optional[int]:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return None

    if not bool(cfg.get("calendar_gate_enabled", False)):
        return None

    calendar_by_group = cfg.get("market_calendar_by_group")
    calendar_symbol_groups = cfg.get("market_calendar_symbol_groups")
    if not isinstance(calendar_by_group, dict) or not isinstance(calendar_symbol_groups, dict):
        return None

    symbols_raw = cfg.get("symbols")
    if isinstance(symbols_raw, list) and symbols_raw:
        symbols = [str(x) for x in symbols_raw if str(x).strip()]
    else:
        symbols = [str(cfg.get("symbol", ""))]

    calendars: list[MarketCalendar] = []
    for symbol in symbols:
        group = calendar_symbol_groups.get(symbol)
        if group is None:
            continue
        group_cfg = calendar_by_group.get(group)
        if not isinstance(group_cfg, dict):
            continue
        cal = _calendar_from_group(group_cfg)
        if cal is not None:
            calendars.append(cal)

    if not calendars:
        return None

    if any(cal.is_trading_minute(now_ms) for cal in calendars):
        return None

    next_open = min(
        (ts for cal in calendars for ts in [ _next_open_ms(cal, now_ms) ] if ts is not None),
        default=None,
    )
    if next_open is None:
        return None

    target_ms = next_open - max(0, wake_ahead_s) * 1000
    sleep_s = int((target_ms - now_ms) / 1000)
    if sleep_s < 1:
        return 1

    logging.info(
        "Календар: ринок закритий, next_open=%s, wake_ahead_s=%d",
        dt.datetime.fromtimestamp(next_open / 1000, dt.timezone.utc).isoformat(),
        wake_ahead_s,
    )
    return sleep_s


def _build_with_retry(config_path: str) -> tuple[object, callable | None]:
    base_delay_s, max_delay_s, wake_ahead_s = _load_retry_config(config_path)
    attempt = 0
    while True:
        try:
            return build_connector(config_path)
        except KeyboardInterrupt:
            logging.info("Зупинено користувачем у retry‑циклі")
            raise SystemExit(0)
        except ConfigError as exc:
            if exc.stage == "load":
                logging.exception("Не вдалось завантажити config.json")
            else:
                logging.exception("Невірна конфігурація")
            raise
        except Exception as exc:
            attempt += 1
            backoff = min(max_delay_s, base_delay_s * (2 ** min(attempt - 1, 4)))
            msg = str(exc)
            msg_l = msg.lower()
            if "ORA-499" in msg:
                sleep_s = _calendar_sleep_s(config_path, int(time.time() * 1000), wake_ahead_s)
                if sleep_s is not None:
                    backoff = min(max_delay_s, max(base_delay_s, sleep_s))
                logging.info(
                    "FXCM login: ORA-499 (Hosts.jsp недоступний). Ринок спить; retry через %ds",
                    backoff,
                )
            elif "user or connection doesn't exist" in msg_l or "loginfailederror" in msg_l:
                logging.warning(
                    "FXCM login: невірні user_id/connection або недійсний акаунт; retry через %ds",
                    backoff,
                )
            elif "wait timeout exceeded" in msg_l:
                logging.warning(
                    "FXCM login: wait timeout exceeded; retry через %ds",
                    backoff,
                )
            else:
                logging.exception(
                    "Не вдалось ініціалізувати FxcmHistoryProvider або запустити engine; retry через %ds",
                    backoff,
                )
            try:
                time.sleep(backoff)
            except KeyboardInterrupt:
                logging.info("Зупинено користувачем під час backoff")
                raise SystemExit(0)


def main() -> int:
    setup_logging(verbose=False)
    report = load_env_secrets()
    if report.loaded:
        logging.info("ENV: secrets_loaded path=%s keys=%d", report.path, report.keys_count)
    else:
        logging.info("ENV: .env не завантажено")
    config_path = pick_config_path()
    logging.info("Config: %s", config_path)
    logging.info("Запуск PollingConnectorB")
    init_redis_snapshot(config_path, log_detail=True)
    try:
        runner, cleanup_fn = _build_with_retry(config_path)
    except ConfigError:
        return 2
    except KeyboardInterrupt:
        logging.info("Зупинено користувачем до старту engine")
        return 0

    try:
        result = run_with_shutdown(getattr(runner, "run_forever"), cleanup_fn)
        if result is not None:
            return result
    except KeyboardInterrupt:
        logging.info("Зупинено користувачем у роботі engine")
        return 0
    except Exception:
        logging.exception("Помилка в роботі engine")
        return 1

    logging.info("Завершення роботи (main)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
