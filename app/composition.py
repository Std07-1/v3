from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Set, Tuple

from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.polling.engine_b import MultiSymbolRunner, PollingConnectorB
from core.config_loader import env_str


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


def _validate_config(cfg: Dict[str, Any]) -> None:
    issues: List[str] = []

    def require_int(key: str, min_val: Optional[int] = None) -> None:
        if key not in cfg:
            issues.append(f"missing:{key}")
            return
        try:
            val = int(cfg[key])
        except Exception:
            issues.append(f"invalid_int:{key}")
            return
        if min_val is not None and val < min_val:
            issues.append(f"invalid_range:{key}")

    def require_int_list(key: str, allow_empty: bool = False) -> None:
        raw = cfg.get(key)
        if not isinstance(raw, list):
            issues.append(f"invalid_list:{key}")
            return
        out: List[int] = []
        for item in raw:
            try:
                val = int(item)
            except Exception:
                continue
            if val > 0:
                out.append(val)
        if not allow_empty and not out:
            issues.append(f"empty:{key}")

    def require_unique_int_list(key: str) -> None:
        raw = cfg.get(key)
        if not isinstance(raw, list):
            return
        values: List[int] = []
        for item in raw:
            try:
                val = int(item)
            except Exception:
                continue
            if val > 0:
                values.append(val)
        if not values:
            return
        seen: Set[int] = set()
        dups: Set[int] = set()
        for val in values:
            if val in seen:
                dups.add(val)
            else:
                seen.add(val)
        if dups:
            issues.append(f"duplicates:{key}")

    def require_unique_str_list(key: str) -> None:
        raw = cfg.get(key)
        if not isinstance(raw, list):
            return
        values = [str(x).strip() for x in raw if str(x).strip()]
        if not values:
            return
        seen: Set[str] = set()
        dups: Set[str] = set()
        for val in values:
            if val in seen:
                dups.add(val)
            else:
                seen.add(val)
        if dups:
            issues.append(f"duplicates:{key}")

    symbols_raw = cfg.get("symbols")
    if isinstance(symbols_raw, list) and any(str(x).strip() for x in symbols_raw):
        pass
    else:
        symbol = cfg.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            issues.append("missing:symbols_or_symbol")

    if "group_logs_enabled" in cfg and not isinstance(cfg.get("group_logs_enabled"), bool):
        issues.append("invalid_bool:group_logs_enabled")

    require_int("connector_retry_base_s", min_val=1)
    require_int("connector_retry_max_s", min_val=1)
    require_int("connector_wake_ahead_s", min_val=0)
    require_int("history_summary_interval_s", min_val=60)
    require_int("history_still_failing_interval_s", min_val=60)
    require_int("history_circuit_fail_streak", min_val=1)
    require_int("history_circuit_base_s", min_val=60)
    require_int("history_circuit_max_s", min_val=60)
    require_int("history_circuit_log_interval_s", min_val=60)
    require_int("history_symbols_sample_n", min_val=1)
    require_int("history_network_error_escalate_s", min_val=60)
    require_int_list("tf_allowlist_s")
    require_int_list("broker_base_tfs_s", allow_empty=True)
    require_unique_int_list("tf_allowlist_s")
    require_unique_int_list("broker_base_tfs_s")
    require_unique_int_list("redis_priming_tfs_s")
    require_unique_str_list("symbols")

    calendar_gate_enabled = bool(cfg.get("calendar_gate_enabled", False))
    if calendar_gate_enabled:
        if not isinstance(cfg.get("market_calendar_by_group"), dict):
            issues.append("invalid_dict:market_calendar_by_group")
        if not isinstance(cfg.get("market_calendar_symbol_groups"), dict):
            issues.append("invalid_dict:market_calendar_symbol_groups")

    redis_cfg = cfg.get("redis")
    if isinstance(redis_cfg, dict) and bool(redis_cfg.get("enabled", False)):
        if not isinstance(redis_cfg.get("host"), str):
            issues.append("invalid_str:redis.host")
        try:
            int(redis_cfg.get("port", 0))
            int(redis_cfg.get("db", 0))
        except Exception:
            issues.append("invalid_int:redis.port_db")
        if "ttl_by_tf_s" in redis_cfg and not isinstance(redis_cfg.get("ttl_by_tf_s"), dict):
            issues.append("invalid_dict:redis.ttl_by_tf_s")
        if "tail_n_by_tf_s" in redis_cfg and not isinstance(redis_cfg.get("tail_n_by_tf_s"), dict):
            issues.append("invalid_dict:redis.tail_n_by_tf_s")

    try:
        base_retry = int(cfg.get("connector_retry_base_s", 0))
        max_retry = int(cfg.get("connector_retry_max_s", 0))
        if base_retry > 0 and max_retry > 0 and max_retry < base_retry:
            issues.append("invalid_range:connector_retry_max_s")
    except Exception:
        issues.append("invalid_range:connector_retry_max_s")

    if issues:
        raise ValueError("Config validation failed: " + ", ".join(issues))


def build_connector(cfg_path: str) -> Tuple[object, Optional[callable]]:
    try:
        cfg = load_config(cfg_path)
    except Exception as exc:
        raise ConfigError("load") from exc

    try:
        _validate_config(cfg)
    except Exception as exc:
        raise ConfigError("validate") from exc

    # Не логувати пароль
    masked = dict(cfg)
    if "password" in masked:
        masked["password"] = "***"
    logging.debug("Конфіг завантажено: %s", json.dumps(masked, ensure_ascii=False))

    try:
        env_user_id = env_str("FXCM_USERNAME")
        env_password = env_str("FXCM_PASSWORD")
        env_connection = env_str("FXCM_CONNECTION")
        env_url = env_str("FXCM_HOST_URL")

        cfg_user_id = cfg.get("user_id")
        cfg_password = cfg.get("password")
        cfg_connection = cfg.get("connection")
        cfg_url = cfg.get("url")

        if env_user_id and cfg_user_id and str(cfg_user_id) != env_user_id:
            logging.warning("Config: FXCM_USERNAME відрізняється від user_id у config.json; беру ENV")
        if env_password and cfg_password and str(cfg_password) != env_password:
            logging.warning("Config: FXCM_PASSWORD відрізняється від password у config.json; беру ENV")
        if env_connection and cfg_connection and str(cfg_connection) != env_connection:
            logging.warning("Config: FXCM_CONNECTION відрізняється від connection у config.json; беру ENV")
        if env_url and cfg_url and str(cfg_url) != env_url:
            logging.warning("Config: FXCM_HOST_URL відрізняється від url у config.json; беру ENV")

        user_id = env_user_id or (str(cfg_user_id) if cfg_user_id is not None else None)
        password = env_password or (str(cfg_password) if cfg_password is not None else None)
        if not user_id or not password:
            raise ConfigError("validate")

        url = env_url or str(cfg.get("url", "http://www.fxcorporate.com/Hosts.jsp"))
        connection = env_connection or str(cfg.get("connection", "Demo"))

        symbols_raw = cfg.get("symbols", None)
        if isinstance(symbols_raw, list) and symbols_raw:
            symbols = [str(x) for x in symbols_raw if str(x).strip()]
        else:
            symbols = [str(cfg.get("symbol", "XAU/USD"))]
        if isinstance(symbols_raw, list) and symbols and isinstance(cfg.get("symbol"), str):
            logging.debug("Config: задано symbol і symbols; використовую symbols")
        data_root = str(cfg.get("data_root", "./data_v3"))
        safety_delay_s = int(cfg.get("safety_delay_s", 2))
        # M5 polling params removed (ADR-0002): M5+ derive via m1_poller/DeriveEngine
        history_summary_interval_s = int(cfg.get("history_summary_interval_s", 600))
        history_still_failing_interval_s = int(cfg.get("history_still_failing_interval_s", 600))
        history_circuit_fail_streak = int(cfg.get("history_circuit_fail_streak", 3))
        history_circuit_base_s = int(cfg.get("history_circuit_base_s", 300))
        history_circuit_max_s = int(cfg.get("history_circuit_max_s", 900))
        history_circuit_log_interval_s = int(cfg.get("history_circuit_log_interval_s", 300))
        history_symbols_sample_n = int(cfg.get("history_symbols_sample_n", 3))
        history_network_error_escalate_s = int(cfg.get("history_network_error_escalate_s", 600))
        if "group_logs_enabled" not in cfg:
            group_logs_enabled: Optional[bool] = None
        else:
            group_logs_enabled = bool(cfg.get("group_logs_enabled"))

        # derived_tfs_s / m5_polling_enabled — видалено (ADR-0002, m1_poller+DeriveEngine)

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

        redis_cfg = cfg.get("redis", {})
        redis_tail_n_by_tf_s = _parse_tf_counts_cfg(
            redis_cfg.get("tail_n_by_tf_s", {}) if isinstance(redis_cfg, dict) else {}
        )
        redis_priming_enabled = bool(cfg.get("redis_priming_enabled", True))
        redis_priming_budget_s = float(cfg.get("redis_priming_budget_s", 2))
        redis_priming_tfs_raw = cfg.get("redis_priming_tfs_s", [])
        redis_priming_tfs_s = [int(x) for x in redis_priming_tfs_raw if int(x) > 0]
        redis_priming_symbols_raw = cfg.get("redis_priming_symbols", [])
        redis_priming_symbols = [str(x) for x in redis_priming_symbols_raw if str(x).strip()]

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
        market_weekend_close_dow = int(cfg.get("market_weekend_close_dow", 4))
        market_weekend_close_hm = str(cfg.get("market_weekend_close_hm", "21:44"))
        market_weekend_open_dow = int(cfg.get("market_weekend_open_dow", 6))
        market_weekend_open_hm = str(cfg.get("market_weekend_open_hm", "22:00"))
        market_daily_break_start_hm = str(cfg.get("market_daily_break_start_hm", "21:59"))
        market_daily_break_end_hm = str(cfg.get("market_daily_break_end_hm", "23:01"))
        market_daily_break_enabled = bool(cfg.get("market_daily_break_enabled", True))
        calendar_gate_enabled = bool(cfg.get("calendar_gate_enabled", False))
        calendar_by_symbol_raw = cfg.get("market_calendar_by_symbol", None)
        calendar_by_group_raw = cfg.get("market_calendar_by_group", None)
        calendar_symbol_groups_raw = cfg.get("market_calendar_symbol_groups", None)
    except ConfigError:
        raise
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
        "Параметри: user=%s url=%s connection=%s symbols=%s data_root=%s"
        " safety_delay_s=%d broker_base=%s broker_base_fetch_on_close=%s"
        " broker_base_max_tf_per_poll=%d broker_base_cold_start_enabled=%s"
        " broker_base_cold_start_counts=%s day_anchor_offset_s=%d"
        " day_anchor_offset_s_alt=%s day_anchor_offset_s_alt2=%s"
        " day_anchor_offset_s_d1=%s day_anchor_offset_s_d1_alt=%s",
        user_id,
        url,
        connection,
        symbols,
        data_root,
        safety_delay_s,
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

    try:
        os.makedirs(data_root, exist_ok=True)
        logging.debug("Каталог даних готовий: %s", data_root)
    except Exception as exc:
        raise ConfigError("data_root") from exc

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

        # daily_breaks: список додаткових інтервалів із group config
        daily_breaks_raw = []
        if isinstance(group_overrides_raw, dict):
            daily_breaks_raw = group_overrides_raw.get("market_daily_breaks", [])
        daily_breaks = tuple(
            (str(pair[0]), str(pair[1]))
            for pair in daily_breaks_raw
            if isinstance(pair, (list, tuple)) and len(pair) >= 2
        )

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
            daily_breaks=daily_breaks,
        )
        engines.append(
            PollingConnectorB(
                provider=prov,
                data_root=data_root,
                symbol=symbol,
                config_path=cfg_path,
                safety_delay_s=safety_delay_s,
                broker_base_tfs_s=broker_base_tfs_s,
                broker_base_fetch_on_close=broker_base_fetch_on_close,
                broker_base_max_tf_per_poll=broker_base_max_tf_per_poll,
                broker_base_cold_start_counts=broker_base_cold_start_counts,
                broker_base_cold_start_enabled=broker_base_cold_start_enabled,
                redis_priming_enabled=redis_priming_enabled,
                redis_priming_budget_s=redis_priming_budget_s,
                redis_priming_tfs_s=redis_priming_tfs_s,
                redis_priming_symbols=redis_priming_symbols,
                redis_tail_n_by_tf_s=redis_tail_n_by_tf_s,
                day_anchor_offset_s=day_anchor_offset_s,
                day_anchor_offset_s_d1=day_anchor_offset_s_d1,
                day_anchor_offset_s_d1_alt=day_anchor_offset_s_d1_alt,
                day_anchor_offset_s_alt=day_anchor_offset_s_alt,
                day_anchor_offset_s_alt2=day_anchor_offset_s_alt2,
                market_calendar=market_calendar,
            )
        )

    if len(engines) == 1:
        logging.debug("Engine створено, стартую run_forever()")
        if group_logs_enabled:
            engines[0].enable_group_logging()
        runner: object = engines[0]
    else:
        logging.debug("Engines створено: %d, стартую MultiSymbolRunner", len(engines))
        effective_group_logs = True if group_logs_enabled is None else bool(group_logs_enabled)
        runner = MultiSymbolRunner(
            engines,
            group_logs_enabled=effective_group_logs,
            history_summary_interval_s=history_summary_interval_s,
            history_still_failing_interval_s=history_still_failing_interval_s,
            history_circuit_fail_streak=history_circuit_fail_streak,
            history_circuit_base_s=history_circuit_base_s,
            history_circuit_max_s=history_circuit_max_s,
            history_circuit_log_interval_s=history_circuit_log_interval_s,
            history_symbols_sample_n=history_symbols_sample_n,
            history_network_error_escalate_s=history_network_error_escalate_s,
        )

    def cleanup() -> None:
        try:
            provider.__exit__(None, None, None)
        except Exception:
            pass

    return runner, cleanup
