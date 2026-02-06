from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.polling.engine_b import MultiSymbolRunner, PollingConnectorB


class ConfigError(Exception):
    def __init__(self, stage: str) -> None:
        super().__init__(stage)
        self.stage = stage


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_tf_counts_cfg(raw: Any) -> Dict[int, int]:
    out: Dict[int, int] = {}
    if raw is None:
        return out
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                tf_s = int(k)
                cnt = int(v)
            except Exception:
                continue
            if tf_s > 0 and cnt > 0:
                out[tf_s] = cnt
        return out
    if isinstance(raw, list):
        for item in raw:
            try:
                s = str(item)
                if ":" in s:
                    tf_s_str, n_str = s.split(":", 1)
                elif "=" in s:
                    tf_s_str, n_str = s.split("=", 1)
                else:
                    continue
                tf_s = int(tf_s_str.strip())
                cnt = int(n_str.strip())
                if tf_s > 0 and cnt > 0:
                    out[tf_s] = cnt
            except Exception:
                continue
        return out
    return out


def _calendar_value(overrides: Dict[str, Any], key: str, default: Any) -> Any:
    if key in overrides:
        return overrides[key]
    return default


def _calendar_str_value(overrides: Dict[str, Any], key: str, default: Any) -> str:
    if key in overrides:
        return str(overrides[key])
    return str(default)


def _merge_calendar_overrides(
    group_overrides: Optional[Dict[str, Any]],
    symbol_overrides: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(group_overrides, dict):
        out.update(group_overrides)
    if isinstance(symbol_overrides, dict):
        out.update(symbol_overrides)
    return out


def build_connector(cfg_path: str) -> Tuple[object, Optional[callable]]:
    try:
        cfg = load_config(cfg_path)
    except Exception as exc:
        raise ConfigError("load") from exc

    # Не логувати пароль
    masked = dict(cfg)
    if "password" in masked:
        masked["password"] = "***"
    logging.debug("Конфіг завантажено: %s", json.dumps(masked, ensure_ascii=False))

    try:
        user_id = str(cfg["user_id"])
        password = str(cfg["password"])
        url = str(cfg.get("url", "http://www.fxcorporate.com/Hosts.jsp"))
        connection = str(cfg.get("connection", "Demo"))

        symbols_raw = cfg.get("symbols", None)
        if isinstance(symbols_raw, list) and symbols_raw:
            symbols = [str(x) for x in symbols_raw if str(x).strip()]
        else:
            symbols = [str(cfg.get("symbol", "XAU/USD"))]
        data_root = str(cfg.get("data_root", "./data_v3"))
        warmup_bars = int(cfg.get("warmup_bars", 3000))
        safety_delay_s = int(cfg.get("safety_delay_s", 2))

        derived = cfg.get(
            "derived_tfs_s", [180, 300, 900, 1800, 3600]
        )
        derived_tfs_s = [int(x) for x in derived]

        broker_base_raw = cfg.get("broker_base_tfs_s", [14400, 86400])
        broker_base_tfs_s = [int(x) for x in broker_base_raw]

        broker_base_fetch_on_close = bool(cfg.get("broker_base_fetch_on_close", True))
        broker_base_max_tf_per_poll = int(cfg.get("broker_base_max_tf_per_poll", 0))
        broker_base_cold_start_enabled = bool(
            cfg.get("broker_base_cold_start_enabled", True)
        )
        broker_base_cold_start_counts = _parse_tf_counts_cfg(
            cfg.get("broker_base_cold_start_counts", {"14400": 1080, "86400": 180})
        )

        day_anchor_offset_s = int(cfg.get("day_anchor_offset_s", 0))
        day_anchor_offset_s_alt_raw = cfg.get("day_anchor_offset_s_alt", None)
        day_anchor_offset_s_alt = (
            None if day_anchor_offset_s_alt_raw is None else int(day_anchor_offset_s_alt_raw)
        )
        day_anchor_offset_s_alt2_raw = cfg.get("day_anchor_offset_s_alt2", None)
        day_anchor_offset_s_alt2 = (
            None if day_anchor_offset_s_alt2_raw is None else int(day_anchor_offset_s_alt2_raw)
        )
        day_anchor_offset_s_d1_raw = cfg.get("day_anchor_offset_s_d1", None)
        day_anchor_offset_s_d1 = (
            None if day_anchor_offset_s_d1_raw is None else int(day_anchor_offset_s_d1_raw)
        )
        day_anchor_offset_s_d1_alt_raw = cfg.get("day_anchor_offset_s_d1_alt", None)
        day_anchor_offset_s_d1_alt = (
            None if day_anchor_offset_s_d1_alt_raw is None else int(day_anchor_offset_s_d1_alt_raw)
        )
        backfill_step_bars = int(cfg.get("history_backfill_step_bars", 300))
        backfill_every_n_polls = int(cfg.get("history_backfill_every_n_polls", 5))
        derived_rebuild_lookback_bars = int(cfg.get("derived_rebuild_lookback_bars", 60000))
        derived_rebuild_use_tool = bool(cfg.get("derived_rebuild_use_tool", False))
        derived_rebuild_tool_dry_run = bool(cfg.get("derived_rebuild_tool_dry_run", False))
        derived_tolerate_missing_minutes = int(
            cfg.get("derived_tolerate_missing_minutes", 0)
        )
        derived_backfill_from_broker = bool(
            cfg.get("derived_backfill_from_broker", True)
        )
        derived_force_close_from_broker = bool(
            cfg.get("derived_force_close_from_broker", False)
        )
        derived_force_close_max_tf_per_poll = int(
            cfg.get("derived_force_close_max_tf_per_poll", 0)
        )
        live_candle_enabled = bool(cfg.get("live_candle_enabled", False))
        live_candle_autostart = bool(cfg.get("live_candle_autostart", True))
        calendar_gate_enabled = bool(cfg.get("calendar_gate_enabled", False))
        poll_diag_enabled = bool(cfg.get("poll_diag_enabled", False))
        market_weekend_close_dow = int(cfg.get("market_weekend_close_dow", 4))
        market_weekend_close_hm = str(cfg.get("market_weekend_close_hm", "21:44"))
        market_weekend_open_dow = int(cfg.get("market_weekend_open_dow", 6))
        market_weekend_open_hm = str(cfg.get("market_weekend_open_hm", "22:00"))
        market_daily_break_start_hm = str(cfg.get("market_daily_break_start_hm", "21:59"))
        market_daily_break_end_hm = str(cfg.get("market_daily_break_end_hm", "23:01"))
        market_daily_break_enabled = bool(cfg.get("market_daily_break_enabled", True))
        heavy_budget_s = int(cfg.get("heavy_budget_s", 25))
        calendar_by_symbol_raw = cfg.get("market_calendar_by_symbol", None)
        calendar_by_group_raw = cfg.get("market_calendar_by_group", None)
        calendar_symbol_groups_raw = cfg.get("market_calendar_symbol_groups", None)
    except Exception as exc:
        raise ConfigError("parse") from exc

    calendar_by_symbol: Dict[str, Any] = {}
    if calendar_by_symbol_raw is None:
        calendar_by_symbol = {}
    elif isinstance(calendar_by_symbol_raw, dict):
        calendar_by_symbol = calendar_by_symbol_raw
    else:
        logging.warning(
            "Config: market_calendar_by_symbol має бути dict, ігнорую значення типу %s",
            type(calendar_by_symbol_raw).__name__,
        )

    calendar_by_group: Dict[str, Any] = {}
    if calendar_by_group_raw is None:
        calendar_by_group = {}
    elif isinstance(calendar_by_group_raw, dict):
        calendar_by_group = calendar_by_group_raw
    else:
        logging.warning(
            "Config: market_calendar_by_group має бути dict, ігнорую значення типу %s",
            type(calendar_by_group_raw).__name__,
        )

    calendar_symbol_groups: Dict[str, Any] = {}
    if calendar_symbol_groups_raw is None:
        calendar_symbol_groups = {}
    elif isinstance(calendar_symbol_groups_raw, dict):
        calendar_symbol_groups = calendar_symbol_groups_raw
    else:
        logging.warning(
            "Config: market_calendar_symbol_groups має бути dict, ігнорую значення типу %s",
            type(calendar_symbol_groups_raw).__name__,
        )

    logging.debug(
        "Параметри: user=%s url=%s connection=%s symbols=%s data_root=%s warmup_bars=%d safety_delay_s=%d derived=%s broker_base=%s broker_base_fetch_on_close=%s broker_base_max_tf_per_poll=%d broker_base_cold_start_enabled=%s broker_base_cold_start_counts=%s day_anchor_offset_s=%d day_anchor_offset_s_alt=%s day_anchor_offset_s_alt2=%s day_anchor_offset_s_d1=%s day_anchor_offset_s_d1_alt=%s",
        user_id,
        url,
        connection,
        symbols,
        data_root,
        warmup_bars,
        safety_delay_s,
        derived_tfs_s,
        broker_base_tfs_s,
        str(broker_base_fetch_on_close),
        broker_base_max_tf_per_poll,
        str(broker_base_cold_start_enabled),
        broker_base_cold_start_counts,
        day_anchor_offset_s,
        str(day_anchor_offset_s_alt),
        str(day_anchor_offset_s_alt2),
        str(day_anchor_offset_s_d1),
        str(day_anchor_offset_s_d1_alt),
    )

    if derived_backfill_from_broker:
        logging.warning(
            "Config: derived_backfill_from_broker=true ігнорується (похідні тільки з M1)."
        )
    if derived_force_close_from_broker:
        logging.warning(
            "Config: derived_force_close_from_broker=true ігнорується (base TF беруться окремо)."
        )

    try:
        os.makedirs(data_root, exist_ok=True)
        logging.debug("Каталог даних готовий: %s", data_root)
    except Exception as exc:
        raise ConfigError("data_root") from exc

    live_proc: Optional[subprocess.Popen] = None
    if live_candle_enabled and live_candle_autostart:
        try:
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            env = dict(os.environ)
            prev = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = project_root + (os.pathsep + prev if prev else "")
            live_proc = subprocess.Popen(
                [sys.executable, "-m", "tools.live_candle"],
                cwd=project_root,
                env=env,
            )
            logging.info("LIVE_BAR: автозапуск tools.live_candle pid=%s", live_proc.pid)
        except Exception:
            logging.exception("LIVE_BAR: не вдалося запустити tools.live_candle")

    provider = FxcmHistoryProvider(
        user_id=user_id,
        password=password,
        url=url,
        connection=connection,
        day_anchor_offset_s=day_anchor_offset_s,
        day_anchor_offset_s_d1=day_anchor_offset_s_d1,
        day_anchor_offset_s_d1_alt=day_anchor_offset_s_d1_alt,
        day_anchor_offset_s_alt=day_anchor_offset_s_alt,
        day_anchor_offset_s_alt2=day_anchor_offset_s_alt2,
    )
    prov = provider.__enter__()

    engines: List[PollingConnectorB] = []
    for symbol in symbols:
        group_raw = calendar_symbol_groups.get(symbol)
        group: Optional[str] = None
        if group_raw is None:
            group = None
        else:
            group = str(group_raw)

        group_overrides_raw = calendar_by_group.get(group) if group else None
        if group and group_overrides_raw is None:
            logging.warning(
                "Config: market_calendar_by_group не має запису для group=%s (symbol=%s), використано default",
                group,
                symbol,
            )

        if group_overrides_raw is not None and not isinstance(group_overrides_raw, dict):
            logging.warning(
                "Config: market_calendar_by_group[%s] має бути dict, використано default",
                group,
            )
            group_overrides_raw = None

        symbol_overrides_raw = calendar_by_symbol.get(symbol)
        if symbol_overrides_raw is None:
            if calendar_by_symbol:
                logging.warning(
                    "Config: market_calendar_by_symbol не має запису для symbol=%s, використано default",
                    symbol,
                )
        elif not isinstance(symbol_overrides_raw, dict):
            logging.warning(
                "Config: market_calendar_by_symbol[%s] має бути dict, використано default",
                symbol,
            )
            symbol_overrides_raw = None

        overrides = _merge_calendar_overrides(group_overrides_raw, symbol_overrides_raw)

        market_calendar = MarketCalendar(
            enabled=bool(_calendar_value(overrides, "calendar_gate_enabled", calendar_gate_enabled)),
            weekend_close_dow=int(
                _calendar_value(overrides, "market_weekend_close_dow", market_weekend_close_dow)
            ),
            weekend_close_hm=_calendar_str_value(
                overrides, "market_weekend_close_hm", market_weekend_close_hm
            ),
            weekend_open_dow=int(
                _calendar_value(overrides, "market_weekend_open_dow", market_weekend_open_dow)
            ),
            weekend_open_hm=_calendar_str_value(
                overrides, "market_weekend_open_hm", market_weekend_open_hm
            ),
            daily_break_start_hm=_calendar_str_value(
                overrides, "market_daily_break_start_hm", market_daily_break_start_hm
            ),
            daily_break_end_hm=_calendar_str_value(
                overrides, "market_daily_break_end_hm", market_daily_break_end_hm
            ),
            daily_break_enabled=bool(
                _calendar_value(overrides, "market_daily_break_enabled", market_daily_break_enabled)
            ),
        )
        engines.append(
            PollingConnectorB(
                provider=prov,
                data_root=data_root,
                symbol=symbol,
                config_path=cfg_path,
                warmup_bars=warmup_bars,
                safety_delay_s=safety_delay_s,
                derived_tfs_s=derived_tfs_s,
                broker_base_tfs_s=broker_base_tfs_s,
                broker_base_fetch_on_close=broker_base_fetch_on_close,
                broker_base_max_tf_per_poll=broker_base_max_tf_per_poll,
                broker_base_cold_start_counts=broker_base_cold_start_counts,
                broker_base_cold_start_enabled=broker_base_cold_start_enabled,
                day_anchor_offset_s=day_anchor_offset_s,
                day_anchor_offset_s_d1=day_anchor_offset_s_d1,
                day_anchor_offset_s_d1_alt=day_anchor_offset_s_d1_alt,
                day_anchor_offset_s_alt=day_anchor_offset_s_alt,
                day_anchor_offset_s_alt2=day_anchor_offset_s_alt2,
                backfill_step_bars=backfill_step_bars,
                backfill_every_n_polls=backfill_every_n_polls,
                derived_rebuild_lookback_bars=derived_rebuild_lookback_bars,
                derived_tolerate_missing_minutes=derived_tolerate_missing_minutes,
                derived_backfill_from_broker=derived_backfill_from_broker,
                derived_force_close_from_broker=derived_force_close_from_broker,
                derived_force_close_max_tf_per_poll=derived_force_close_max_tf_per_poll,
                derived_rebuild_use_tool=derived_rebuild_use_tool,
                derived_rebuild_tool_dry_run=derived_rebuild_tool_dry_run,
                poll_diag_enabled=poll_diag_enabled,
                market_calendar=market_calendar,
                heavy_budget_s=heavy_budget_s,
            )
        )

    if len(engines) == 1:
        logging.debug("Engine створено, стартую run_forever()")
        runner: object = engines[0]
    else:
        logging.debug("Engines створено: %d, стартую MultiSymbolRunner", len(engines))
        runner = MultiSymbolRunner(engines)

    def cleanup() -> None:
        try:
            provider.__exit__(None, None, None)
        except Exception:
            pass
        if live_proc is not None and live_proc.poll() is None:
            logging.info("LIVE_BAR: зупиняю tools.live_candle pid=%s", live_proc.pid)
            try:
                live_proc.terminate()
                live_proc.wait(timeout=5)
            except Exception:
                try:
                    live_proc.kill()
                except Exception:
                    pass

    return runner, cleanup
