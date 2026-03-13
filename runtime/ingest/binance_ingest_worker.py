"""Binance Ingest Worker — M1 polling + derive cascade (ADR-0037).

Прямий аналог m1_ingestion_worker.py для Binance Futures:
- BinanceHistoryProvider замість BrokerRedisProxy (запускається у .venv, не .venv37)
- Символи з config.json:binance.symbols (BTCUSDT, ETHUSDT)
- anchor_offset_s=0 (crypto: H4 midnight-aligned, D1 midnight-aligned)
- Calendar: crypto_24x7 → is_trading_minute() = True завжди

Реіспользує M1SymbolPoller / M1PollerRunner з m1_poller.py.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from core.config_loader import pick_config_path, load_system_config
from env_profile import load_env_secrets
from runtime.ingest.broker.binance.provider import BinanceHistoryProvider
from runtime.ingest.derive_engine import DeriveEngine
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.polling.m1_poller import (
    M1SymbolPoller,
    M1PollerRunner,
)
from runtime.ingest.tick_common import calendar_from_group
from runtime.store.uds import build_uds_from_config


logger = logging.getLogger("binance_ingest_worker")


def build_binance_ingest_worker(config_path: str) -> Optional[M1PollerRunner]:
    """Будує M1PollerRunner з BinanceHistoryProvider для crypto symbols."""
    cfg = load_system_config(config_path)
    bn_cfg = cfg.get("binance", {})
    if not isinstance(bn_cfg, dict):
        bn_cfg = {}

    if not bn_cfg.get("enabled", False):
        logger.info("BINANCE_INGEST_DISABLED (binance.enabled=false)")
        return None

    symbols: List[str] = bn_cfg.get("symbols", [])
    if not symbols:
        logger.warning("BINANCE_INGEST_NO_SYMBOLS")
        return None

    # Credentials від env (не з config — правило D1)
    api_key = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_API_SECRET", "")

    provider = BinanceHistoryProvider(api_key=api_key, api_secret=api_secret)

    # Config values
    tail_fetch_n = int(bn_cfg.get("tail_fetch_n", 5))
    safety_delay_s = int(bn_cfg.get("safety_delay_s", 10))
    backfill_max_bars = int(bn_cfg.get("backfill_max_bars", 1440))

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

    # Calendars (crypto_24x7 → cal=None → always trading)
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
                m3_derive=True,
                tail_catchup_max_bars=backfill_max_bars,
            )
        )

    # DeriveEngine — anchors=0 для crypto (ADR-0037)
    anchor_offset_s = int(bn_cfg.get("day_anchor_offset_s", 0))
    d1_anchor_offset_s = int(bn_cfg.get("d1_anchor_offset_s", 0))

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

    logger.info(
        "BINANCE_DERIVE_ENGINE_WIRED symbols=%d anchor=%d d1_anchor=%d",
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

    # Derive warmup
    bootstrap_cfg = cfg.get("bootstrap", {})
    derive_warmup: Optional[Dict[int, int]] = None
    if isinstance(bootstrap_cfg, dict):
        raw_warmup = bootstrap_cfg.get("derive_warmup_bars_by_tf")
        if isinstance(raw_warmup, dict):
            derive_warmup = {}
            for k, v in raw_warmup.items():
                try:
                    derive_warmup[int(k)] = int(v)
                except (ValueError, TypeError):
                    logging.debug("BINANCE_WARMUP_PARSE key=%r val=%r", k, v)

    cascade_catchup = 1440
    if isinstance(bootstrap_cfg, dict):
        raw_cc = bootstrap_cfg.get("cascade_catchup_m1_bars")
        if raw_cc is not None:
            try:
                cascade_catchup = int(raw_cc)
            except (ValueError, TypeError):
                logging.debug("BINANCE_CASCADE_CATCHUP_PARSE raw=%r", raw_cc)

    logger.info(
        "BINANCE_INGEST_WORKER_BUILD symbols=%s tfs=%s",
        symbols,
        sorted(redis_tail_n.keys()),
    )

    return M1PollerRunner(
        pollers=pollers,
        provider=provider,
        uds=uds,
        redis_tail_n=redis_tail_n,
        safety_delay_s=safety_delay_s,
        tail_catchup_enabled=(backfill_max_bars > 0),
        derive_engine=derive_engine,
        derive_warmup_bars_by_tf=derive_warmup,
        cascade_catchup_m1_bars=cascade_catchup,
    )


# ---------------------------------------------------------------------------
# Entrypoint  (python -m runtime.ingest.binance_ingest_worker)
# ---------------------------------------------------------------------------
def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    report = load_env_secrets()
    if report.loaded:
        logger.info(
            "ENV: secrets_loaded path=%s keys=%d", report.path, report.keys_count
        )

    config_path = pick_config_path()
    logger.info("BINANCE_INGEST_WORKER config=%s", config_path)

    runner = build_binance_ingest_worker(config_path)
    if runner is None:
        logger.info("BINANCE_INGEST_WORKER_EXIT (disabled or no config)")
        return 0

    try:
        runner.run_forever()
    except KeyboardInterrupt:
        logger.info("BINANCE_INGEST_WORKER_STOP (KeyboardInterrupt)")
    except Exception:
        logger.exception("BINANCE_INGEST_WORKER_FATAL")
        return 1
    finally:
        runner.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
