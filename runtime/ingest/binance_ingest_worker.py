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
import threading
import time
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

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
from runtime.store.ssot_jsonl import head_first_bar_time_ms
from runtime.store.uds import build_uds_from_config, UnifiedDataStore


logger = logging.getLogger("binance_ingest_worker")


# ---------------------------------------------------------------------------
# Historical Backward Crawl (ADR-0038 S6)
# ---------------------------------------------------------------------------

def _get_oldest_m1_ms(data_root: str, symbol: str) -> Optional[int]:
    """Повертає open_time_ms найстарішого M1 бару на диску.

    Використовує head_first_bar_time_ms — O(1), читає лише перший рядок
    найстарішого part-*.jsonl файлу.
    Повертає None якщо даних немає (initial backfill ще не запускався).
    """
    return head_first_bar_time_ms(data_root, symbol, tf_s=60)


def _crawl_tick(
    symbol: str,
    provider: BinanceHistoryProvider,
    jsonl_appender: Any,
    data_root: str,
    n_bars: int,
    max_days: int,
) -> None:
    """Один тік backward crawl для одного символу.

    Алгоритм:
    1. Знаходимо horizon = найстаріший open_time_ms на диску.
    2. Якщо None — пропускаємо (initial backfill ще не запустився).
    3. Обчислюємо limit_ms = now - max_days * 86400s.
    4. Якщо horizon <= limit_ms — crawl завершений.
    5. Запитуємо [from_ms, horizon) у провайдера.
    6. Записуємо бари через JsonlAppender (disk-only, без Redis).
    """
    # Крок 1–2: горизонт
    horizon = _get_oldest_m1_ms(data_root, symbol)
    if horizon is None:
        logger.debug("CRAWL_SKIP_NO_HORIZON symbol=%s (initial backfill not done)", symbol)
        return

    # Крок 3–4: перевірка ліміту
    now_ms = int(time.time() * 1000)
    limit_ms = now_ms - max_days * 86_400_000
    if horizon <= limit_ms:
        logger.info("CRAWL_COMPLETE symbol=%s horizon_ms=%d limit_ms=%d", symbol, horizon, limit_ms)
        return

    # Крок 5: визначаємо вікно запиту
    to_ms = horizon - 1          # виключаємо сам horizon (вже є на диску)
    from_ms = max(horizon - n_bars * 60_000, limit_ms)

    if from_ms >= to_ms:
        logger.debug("CRAWL_SKIP_WINDOW_EMPTY symbol=%s from_ms=%d to_ms=%d", symbol, from_ms, to_ms)
        return

    # Крок 6: fetch (provider потребує активної сесії — _client != None)
    if getattr(provider, "_client", None) is None:
        logger.warning("CRAWL_SKIP_NO_SESSION symbol=%s (provider not connected)", symbol)
        return

    bars = provider.fetch_m1_range(symbol, from_ms, to_ms, n_bars)
    if not bars:
        logger.debug("CRAWL_EMPTY_RESPONSE symbol=%s from_ms=%d to_ms=%d", symbol, from_ms, to_ms)
        return

    # Крок 7–8: sort + write (JsonlAppender — disk-only, ADR-0021 thread-safe)
    bars_sorted = sorted(bars, key=lambda b: b.open_time_ms)
    written = 0
    for bar in bars_sorted:
        try:
            jsonl_appender.append(bar)
            written += 1
        except Exception as exc:
            logger.warning(
                "CRAWL_BAR_WRITE_FAIL symbol=%s open_ms=%d err=%s",
                symbol,
                bar.open_time_ms,
                exc,
            )

    new_horizon = bars_sorted[0].open_time_ms if bars_sorted else horizon
    logger.info(
        "CRAWL_TICK symbol=%s written=%d new_horizon=%d (was %d)",
        symbol,
        written,
        new_horizon,
        horizon,
    )


def _run_crawl_loop(
    symbols: List[str],
    provider: BinanceHistoryProvider,
    uds: UnifiedDataStore,
    crawl_cfg: Dict[str, Any],
    stop_event: threading.Event,
) -> None:
    """Daemon crawl loop: тік кожен interval_s. I5: exception → WARNING, continue."""
    n_bars = int(crawl_cfg["m1_per_run"])
    max_days = int(crawl_cfg["max_days"])
    interval_s = float(crawl_cfg["interval_s"])
    data_root = str(crawl_cfg["data_root"])
    jsonl_appender = uds._jsonl  # noqa: SLF001 — JsonlAppender, ADR-0038
    if jsonl_appender is None:
        logger.error("CRAWL_LOOP_ABORT uds._jsonl is None (writer_components=False?)")
        return
    while not stop_event.is_set():
        for symbol in symbols:
            try:
                _crawl_tick(symbol, provider, jsonl_appender, data_root, n_bars, max_days)
            except Exception as exc:
                logger.warning("CRAWL_ERROR symbol=%s err=%s", symbol, exc, exc_info=True)
        stop_event.wait(timeout=interval_s)
    logger.info("CRAWL_LOOP_STOP")


def build_binance_ingest_worker(
    config_path: str,
) -> Optional[Tuple[M1PollerRunner, Dict[str, Any]]]:
    """Будує M1PollerRunner з BinanceHistoryProvider для crypto symbols.

    Повертає (runner, crawl_context) або None якщо Binance вимкнено.
    crawl_context містить параметри Historical Backward Crawl (ADR-0038 S6).
    """
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

    # ADR-0038: initial backfill for virgin symbols
    initial_backfill_bars = 1440
    if isinstance(bootstrap_cfg, dict):
        raw_ib = bootstrap_cfg.get("initial_backfill_m1_bars")
        if raw_ib is not None:
            try:
                initial_backfill_bars = int(raw_ib)
            except (ValueError, TypeError):
                logging.debug("BINANCE_INITIAL_BACKFILL_PARSE raw=%r", raw_ib)

    # ADR-0038 S6: Historical Backward Crawl config
    def _int_cfg(key: str, default: int) -> int:
        try:
            return int(bootstrap_cfg.get(key, default))  # type: ignore[union-attr]
        except (ValueError, TypeError):
            logging.debug("BINANCE_CFG_PARSE key=%r", key)
            return default

    crawl_enabled = bool(bootstrap_cfg.get("historical_crawl_enabled", False)) if isinstance(bootstrap_cfg, dict) else False
    crawl_m1_per_run = _int_cfg("historical_crawl_m1_per_run", 1440)
    crawl_interval_s = _int_cfg("historical_crawl_interval_s", 3600)
    crawl_max_days = _int_cfg("historical_crawl_max_days", 30)

    crawl_context: Dict[str, Any] = {
        "enabled": crawl_enabled,
        "m1_per_run": crawl_m1_per_run,
        "interval_s": crawl_interval_s,
        "max_days": crawl_max_days,
        "symbols": symbols,
        "provider": provider,
        "uds": uds,
        "data_root": data_root,
    }

    logger.info(
        "BINANCE_INGEST_WORKER_BUILD symbols=%s tfs=%s crawl_enabled=%s",
        symbols,
        sorted(redis_tail_n.keys()),
        crawl_enabled,
    )

    runner = M1PollerRunner(
        pollers=pollers,
        provider=provider,
        uds=uds,
        redis_tail_n=redis_tail_n,
        safety_delay_s=safety_delay_s,
        tail_catchup_enabled=(backfill_max_bars > 0),
        derive_engine=derive_engine,
        derive_warmup_bars_by_tf=derive_warmup,
        cascade_catchup_m1_bars=cascade_catchup,
        initial_backfill_m1_bars=initial_backfill_bars,
    )
    return runner, crawl_context


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

    result = build_binance_ingest_worker(config_path)
    if result is None:
        logger.info("BINANCE_INGEST_WORKER_EXIT (disabled or no config)")
        return 0

    runner, crawl_ctx = result

    # ADR-0038 S6: Historical Backward Crawl — daemon thread
    # Guard: тільки якщо crawl_enabled=True AND provider має fetch_m1_range
    # (FXCM provider не має fetch_m1_range → crawl не запускається)
    crawl_stop = threading.Event()
    crawl_thread = None
    if crawl_ctx["enabled"] and hasattr(crawl_ctx["provider"], "fetch_m1_range"):
        crawl_thread = threading.Thread(
            target=_run_crawl_loop,
            args=(
                crawl_ctx["symbols"],
                crawl_ctx["provider"],
                crawl_ctx["uds"],
                crawl_ctx,
                crawl_stop,
            ),
            daemon=True,
            name="historical-crawl",
        )
        crawl_thread.start()
        logger.info(
            "CRAWL_STARTED symbols=%d max_days=%d interval_s=%d",
            len(crawl_ctx["symbols"]),
            crawl_ctx["max_days"],
            crawl_ctx["interval_s"],
        )

    try:
        runner.run_forever()
    except KeyboardInterrupt:
        logger.info("BINANCE_INGEST_WORKER_STOP (KeyboardInterrupt)")
    except Exception:
        logger.exception("BINANCE_INGEST_WORKER_FATAL")
        return 1
    finally:
        # Зупиняємо crawl thread перед виходом
        crawl_stop.set()
        runner.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
