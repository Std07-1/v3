"""M1 Ingestion Worker — platform-side M1 ingestion (Python 3.12+, ADR-0016).

Замінює m1_poller для dual-venv режиму:
- Scheduling, watermark, gap detection, calendar, flat bar — ВСЕ тут
- Fetch делегується broker_sidecar через Redis queues
- UDS commit + DeriveEngine cascade — тут (I1: UDS = вузька талія)

Реіспользує M1SymbolPoller / M1PollerRunner з m1_poller.py,
замінюючи FxcmHistoryProvider на BrokerRedisProxy.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from core.config_loader import pick_config_path, load_system_config
from core.model.bars import CandleBar
from env_profile import load_env_secrets
from runtime.ingest.derive_engine import DeriveEngine
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.polling.m1_poller import (
    M1SymbolPoller,
    M1PollerRunner,
    set_flat_bar_max_volume,
)
from runtime.ingest.tick_common import symbols_from_cfg, calendar_from_group
from runtime.store.redis_spec import resolve_redis_spec
from runtime.store.uds import build_uds_from_config

try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore


# ---------------------------------------------------------------------------
# BrokerRedisProxy — drop-in replacement for FxcmHistoryProvider
# ---------------------------------------------------------------------------
_CMD_QUEUE_SUFFIX = "broker:m1:cmd"
_BARS_QUEUE_SUFFIX = "broker:m1:bars"
_CONTRACT_VERSION = 1
_BLPOP_TIMEOUT_S = 15  # timeout for response from sidecar


class BrokerRedisProxy:
    """Виконує fetch_last_n_m1 через Redis queue (замість прямого FXCM API).

    Надсилає команду fetch в broker_sidecar (Py 3.7),
    отримує серіалізовані бари у відповідь.
    """

    def __init__(self, redis_cli: Any, namespace: str) -> None:
        self._redis = redis_cli
        self._cmd_key = f"{namespace}:{_CMD_QUEUE_SUFFIX}"
        self._bars_key = f"{namespace}:{_BARS_QUEUE_SUFFIX}"
        self._fx: Optional[str] = None  # compat з M1PollerRunner._try_connect()

    def __enter__(self) -> BrokerRedisProxy:
        self._fx = "proxy"
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._fx = None

    def fetch_last_n_m1(
        self,
        symbol: str,
        n: int,
        date_to_utc: Any = None,
    ) -> List[CandleBar]:
        """Fetch M1 bars через broker_sidecar Redis queue."""
        req_id = uuid.uuid4().hex
        reply_key = "%s:%s" % (self._bars_key, req_id)
        date_to_ms = None
        if date_to_utc is not None:
            if hasattr(date_to_utc, "timestamp"):
                date_to_ms = int(date_to_utc.timestamp() * 1000)
            else:
                date_to_ms = int(date_to_utc)

        cmd = json.dumps(
            {
                "v": _CONTRACT_VERSION,
                "cmd": "fetch_m1",
                "req_id": req_id,
                "reply_to": reply_key,
                "symbol": symbol,
                "n_bars": n,
                "date_to_ms": date_to_ms,
            }
        )
        self._redis.rpush(self._cmd_key, cmd)

        # Wait for response
        result = self._redis.blpop(reply_key, timeout=_BLPOP_TIMEOUT_S)
        if result is None:
            logging.warning(
                "BROKER_PROXY_TIMEOUT symbol=%s n=%d timeout=%ds req_id=%s",
                symbol,
                n,
                _BLPOP_TIMEOUT_S,
                req_id,
            )
            self._redis.delete(reply_key)
            return []

        _key, raw = result
        try:
            resp = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            logging.warning("BROKER_PROXY_PARSE_ERROR err=%s", exc)
            self._redis.delete(reply_key)
            return []

        self._redis.delete(reply_key)

        if resp.get("req_id") != req_id:
            logging.warning(
                "BROKER_PROXY_REQ_MISMATCH symbol=%s expected_req=%s got_req=%s",
                symbol,
                req_id,
                resp.get("req_id"),
            )
            return []

        if resp.get("symbol") != symbol:
            logging.warning(
                "BROKER_PROXY_SYMBOL_MISMATCH expected=%s got=%s req_id=%s",
                symbol,
                resp.get("symbol"),
                req_id,
            )
            return []

        if resp.get("error"):
            logging.warning(
                "BROKER_PROXY_FETCH_ERROR symbol=%s err=%s",
                symbol,
                resp["error"],
            )
            return []

        bars: List[CandleBar] = []
        for d in resp.get("bars", []):
            try:
                bars.append(
                    CandleBar(
                        symbol=d["symbol"],
                        tf_s=d["tf_s"],
                        open_time_ms=d["open_time_ms"],
                        close_time_ms=d["close_time_ms"],
                        o=d["o"],
                        h=d["h"],
                        low=d["low"],
                        c=d["c"],
                        v=d["v"],
                        complete=d.get("complete", True),
                        src=d.get("src", "history"),
                        extensions=d.get("extensions", {}),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                logging.warning("BROKER_PROXY_BAR_PARSE err=%s bar=%s", exc, d)
        return bars


# ---------------------------------------------------------------------------
# Factory: build ingestion worker (like build_m1_poller but with Redis proxy)
# ---------------------------------------------------------------------------
def build_ingestion_worker(config_path: str) -> Optional[M1PollerRunner]:
    """Будує M1PollerRunner з BrokerRedisProxy замість FxcmHistoryProvider."""
    cfg = load_system_config(config_path)
    m1_cfg = cfg.get("m1_poller", {})
    if not isinstance(m1_cfg, dict):
        m1_cfg = {}

    if not m1_cfg.get("enabled", False):
        logging.info("M1_INGESTION_WORKER_DISABLED (m1_poller.enabled=false)")
        return None

    symbols = symbols_from_cfg(cfg)
    if not symbols:
        logging.warning("M1_INGESTION_WORKER_NO_SYMBOLS")
        return None

    # Exclude symbols owned by binance worker (ADR-0037)
    bn_cfg = cfg.get("binance", {})
    if isinstance(bn_cfg, dict) and bn_cfg.get("enabled", False):
        bn_symbols = set(bn_cfg.get("symbols", []))
        if bn_symbols:
            symbols = [s for s in symbols if s not in bn_symbols]
            if not symbols:
                logging.warning(
                    "M1_INGESTION_WORKER_NO_SYMBOLS (all claimed by binance)"
                )
                return None
            logging.info(
                "M1_INGESTION_WORKER_SYMBOLS excluded_binance=%s remaining=%s",
                sorted(bn_symbols),
                symbols,
            )

    # Redis connection
    if redis_lib is None:
        logging.error("M1_INGESTION_WORKER_NO_REDIS (pip install redis)")
        return None

    spec = resolve_redis_spec(cfg, role="m1_ingestion_worker", log=True)
    if spec is None:
        logging.error("M1_INGESTION_WORKER_REDIS_DISABLED")
        return None

    redis_cli = redis_lib.Redis(
        host=spec.host,
        port=spec.port,
        db=spec.db,
        decode_responses=True,
        socket_timeout=30,
        socket_connect_timeout=5,
    )

    # BrokerRedisProxy (replaces FxcmHistoryProvider)
    provider = BrokerRedisProxy(redis_cli, spec.namespace)

    # Config values (same as build_m1_poller)
    tail_fetch_n = int(m1_cfg.get("tail_fetch_n", 5))
    safety_delay_s = int(m1_cfg.get("safety_delay_s", 8))
    m3_derive = bool(m1_cfg.get("m3_derive_enabled", True))
    tail_catchup_max_bars = int(m1_cfg.get("tail_catchup_max_bars", 5000))
    lr_threshold = int(m1_cfg.get("live_recover_threshold_bars", 3))
    lr_max_cycle = int(m1_cfg.get("live_recover_max_bars_per_cycle", 120))
    lr_cooldown = int(m1_cfg.get("live_recover_cooldown_s", 5))
    lr_max_total = int(m1_cfg.get("live_recover_max_total_bars", 5000))
    lr_log_interval = int(m1_cfg.get("live_recover_log_interval_s", 60))
    stale_s = int(m1_cfg.get("stale_s", 720))

    # Flat bar max volume (SSOT)
    flat_vol_raw = cfg.get("flat_bar_max_volume")
    if flat_vol_raw is not None:
        set_flat_bar_max_volume(int(flat_vol_raw))

    # UDS
    data_root = str(cfg.get("data_root", "./data_v3"))
    boot_id = uuid.uuid4().hex
    uds = build_uds_from_config(
        config_path=config_path,
        data_root=data_root,
        boot_id=boot_id,
        role="writer",
        writer_components=True,
    )

    # Calendars
    cal_by_group = cfg.get("market_calendar_by_group", {})
    cal_sym_groups = cfg.get("market_calendar_symbol_groups", {})

    pollers: list[M1SymbolPoller] = []
    for sym in symbols:
        group = cal_sym_groups.get(sym)
        cal: Optional[MarketCalendar] = None
        if group and isinstance(cal_by_group.get(group), dict):
            cal = calendar_from_group(cal_by_group[group])

        pollers.append(
            M1SymbolPoller(
                symbol=sym,
                provider=provider,
                uds=uds,
                calendar=cal,
                tail_fetch_n=tail_fetch_n,
                m3_derive=m3_derive,
                tail_catchup_max_bars=tail_catchup_max_bars,
                live_recover_threshold_bars=lr_threshold,
                live_recover_max_bars_per_cycle=lr_max_cycle,
                live_recover_cooldown_s=lr_cooldown,
                live_recover_max_total_bars=lr_max_total,
                live_recover_log_interval_s=lr_log_interval,
                stale_s=stale_s,
            )
        )

    # DeriveEngine (ADR-0002 + ADR-0023)
    derive_engine: Optional[DeriveEngine] = None
    derive_enabled = bool(m1_cfg.get("derive_engine_enabled", True))
    if derive_enabled:
        anchor_offset_s = int(cfg.get("day_anchor_offset_s", 0))
        d1_anchor_offset_s = int(cfg.get("day_anchor_offset_s_d1", 0))
        calendars_for_engine: Dict[str, MarketCalendar] = {}
        for sym in symbols:
            group = cal_sym_groups.get(sym)
            if group and isinstance(cal_by_group.get(group), dict):
                cal_obj = calendar_from_group(cal_by_group[group])
                if cal_obj is not None:
                    calendars_for_engine[sym] = cal_obj

        derive_engine = DeriveEngine(
            symbols=symbols,
            anchor_offset_s=anchor_offset_s,
            d1_anchor_offset_s=d1_anchor_offset_s,
            calendars=calendars_for_engine,
        )
        for sym in symbols:
            derive_engine.register_symbol_uds(sym, uds)
        for p in pollers:
            p._derive_engine = derive_engine  # noqa: SLF001
        logging.info(
            "DERIVE_ENGINE_WIRED symbols=%d anchor_offset_s=%d d1_anchor=%d",
            len(symbols),
            anchor_offset_s,
            d1_anchor_offset_s,
        )

    # Redis tail_n для priming
    redis_cfg = cfg.get("redis", {})
    tail_n_raw = redis_cfg.get("tail_n_by_tf_s", {})
    redis_tail_n: Dict[int, int] = {}
    _PRIME_TFS = (60, 180, 300, 900, 1800, 3600, 14400, 86400)
    for tf_s in _PRIME_TFS:
        val = tail_n_raw.get(str(tf_s), 0)
        if int(val) > 0:
            redis_tail_n[tf_s] = int(val)

    # Derive warmup (bootstrap)
    bootstrap_cfg = cfg.get("bootstrap", {})
    _derive_warmup_cfg: Optional[Dict[int, int]] = None
    if isinstance(bootstrap_cfg, dict):
        raw_warmup = bootstrap_cfg.get("derive_warmup_bars_by_tf")
        if isinstance(raw_warmup, dict):
            _derive_warmup_cfg = {}
            for k, v in raw_warmup.items():
                try:
                    _derive_warmup_cfg[int(k)] = int(v)
                except (ValueError, TypeError):
                    logging.debug(
                        "M1_INGESTION_WARMUP_PARSE_FAILED key=%r value=%r",
                        k,
                        v,
                        exc_info=True,
                    )
                    pass

    _cascade_catchup_m1_n = 1440
    if isinstance(bootstrap_cfg, dict):
        raw_catchup = bootstrap_cfg.get("cascade_catchup_m1_bars")
        if raw_catchup is not None:
            try:
                _cascade_catchup_m1_n = int(raw_catchup)
            except (ValueError, TypeError):
                logging.debug(
                    "M1_INGESTION_CASCADE_CATCHUP_PARSE_FAILED raw=%r",
                    raw_catchup,
                    exc_info=True,
                )
                pass

    logging.info(
        "M1_INGESTION_WORKER_BUILD symbols=%d tfs=%s mode=broker_proxy",
        len(symbols),
        sorted(redis_tail_n.keys()),
    )

    return M1PollerRunner(
        pollers=pollers,
        provider=provider,
        uds=uds,
        redis_tail_n=redis_tail_n,
        safety_delay_s=safety_delay_s,
        tail_catchup_enabled=(tail_catchup_max_bars > 0),
        derive_engine=derive_engine,
        derive_warmup_bars_by_tf=_derive_warmup_cfg,
        cascade_catchup_m1_bars=_cascade_catchup_m1_n,
    )


# ---------------------------------------------------------------------------
# Entrypoint  (python -m runtime.ingest.m1_ingestion_worker)
# ---------------------------------------------------------------------------
def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    report = load_env_secrets()
    if report.loaded:
        logging.info(
            "ENV: secrets_loaded path=%s keys=%d", report.path, report.keys_count
        )

    config_path = pick_config_path()
    logging.info("M1_INGESTION_WORKER config=%s", config_path)

    runner = build_ingestion_worker(config_path)
    if runner is None:
        logging.info("M1_INGESTION_WORKER_EXIT (disabled or no config)")
        return 0

    try:
        runner.run_forever()
    except KeyboardInterrupt:
        logging.info("M1_INGESTION_WORKER_STOP (KeyboardInterrupt)")
    except Exception:
        logging.exception("M1_INGESTION_WORKER_FATAL")
        return 1
    finally:
        runner.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
