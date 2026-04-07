"""Broker Sidecar — FXCM M1 fetcher + tick relay V2 (Python 3.7, ADR-0016).

Працює у .venv37/. Виконує ДВІ функції через ОДНУ FXCM сесію:

1. **M1 Fetch**: отримує команди fetch через Redis list,
   повертає бари через Redis list. Не має стану scheduling/watermark —
   вся бізнес-логіка в m1_ingestion_worker (3.12+).

2. **Tick Relay V2**: підписується на FXCM OFFERS table (bid/ask),
   публікує тіки у Redis PubSub (`{ns}:price_tick`).
   Замінює окремий tick_publisher_fxcm (зупинений назавжди —
   FXCM SDK не підтримує дві одночасні сесії з одного акаунту).
   Деталі: docs/audit/vps_production_incidents_2026_04_06.md §1.

⚠️  НЕ ЗАПУСКАЙТЕ tick_publisher_fxcm паралельно з broker_sidecar!
    FXCM SDK дозволяє лише одну сесію на акаунт. Два процеси →
    конфлікт → «QuotesManager: Quotes storage taking too long».

Redis queues (namespace з config.json):
  M1 fetch:
    {ns}:broker:m1:cmd   — BLPOP (commands)
    {ns}:broker:m1:bars  — legacy shared RPUSH (responses)
    {ns}:broker:m1:bars:{req_id} — canonical per-request reply queue
  Tick relay:
    {ns}:price_tick — Redis PubSub channel (ticks)
    {ns}:tick:last:{sym} — last tick cache (TTL configurable)

Command contract v1:
  {"v": 1, "cmd": "fetch_m1", "symbol": "XAU/USD", "n_bars": 5, "date_to_ms": 1741392060000}

Response contract v1:
    {"v": 1, "req_id": "...", "symbol": "XAU/USD", "bars": [{...}, ...], "error": null}
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import time

from core.config_loader import pick_config_path, load_system_config, env_str
from core.model.bars import ms_to_utc_dt
from env_profile import load_env_secrets
from runtime.store.redis_spec import resolve_redis_spec
from runtime.ingest.tick_common import (
    pick_tick_channel,
    symbols_from_cfg,
    build_symbol_aliases,
    to_ms,
)
from runtime.store.redis_keys import symbol_key

try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore

try:
    from forexconnect import fxcorepy  # type: ignore
except Exception:
    fxcorepy = None  # type: ignore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CMD_QUEUE_SUFFIX = "broker:m1:cmd"
_BARS_QUEUE_SUFFIX = "broker:m1:bars"
_MAX_LIST_LEN = 10000  # LTRIM safety rail (ADR-0016 §F4)
_BLPOP_TIMEOUT_S = 5  # seconds
_DEFAULT_IPC_REPLY_TTL_S = 120  # config.json:broker_ipc_reply_ttl_s

_ipc_reply_ttl_s = _DEFAULT_IPC_REPLY_TTL_S  # overridden in main() from config
_RECONNECT_COOLDOWN_S = 30
_MAX_BARS_PER_CMD = 200  # guard against huge requests
_CONTRACT_VERSION = 1

_running = True


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def _signal_handler(sig, _frame):
    global _running
    _running = False
    logging.info("BROKER_SIDECAR_SIGNAL sig=%s", sig)


def _build_provider(cfg):
    """Створити FxcmHistoryProvider з config + env."""
    from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider

    user_id = env_str("FXCM_USERNAME")
    password = env_str("FXCM_PASSWORD")
    url = env_str("FXCM_HOST_URL")
    connection = env_str("FXCM_CONNECTION") or "Demo"

    if not user_id or not password or not url:
        raise RuntimeError("FXCM credentials missing (FXCM_USERNAME/PASSWORD/HOST_URL)")

    anchor_s = int(cfg.get("day_anchor_offset_s", 0))
    d1_anchor_s = int(cfg.get("day_anchor_offset_s_d1", 0))

    return FxcmHistoryProvider(
        user_id=user_id,
        password=password,
        url=url,
        connection=connection,
        day_anchor_offset_s=anchor_s,
        day_anchor_offset_s_d1=d1_anchor_s,
    )


def _handle_command(provider, cmd_raw, redis_cli, bars_key):
    """Обробити одну команду fetch і записати результат у Redis.

    Returns True if FXCM session needs reconnection.
    """
    try:
        cmd = json.loads(cmd_raw)
    except (json.JSONDecodeError, TypeError) as exc:
        logging.warning(
            "BROKER_SIDECAR_CMD_PARSE_ERROR raw=%r err=%s", cmd_raw[:200], exc
        )
        return False

    v = cmd.get("v", 0)
    if v != _CONTRACT_VERSION:
        logging.warning(
            "BROKER_SIDECAR_CMD_VERSION_MISMATCH v=%s expected=%d", v, _CONTRACT_VERSION
        )
        return False

    action = cmd.get("cmd", "")
    req_id = str(cmd.get("req_id", "") or "")
    reply_to = str(cmd.get("reply_to", "") or "")
    symbol = cmd.get("symbol", "")
    n_bars = min(int(cmd.get("n_bars", 5)), _MAX_BARS_PER_CMD)
    date_to_ms = cmd.get("date_to_ms")
    target_key = reply_to or bars_key

    if action != "fetch_m1" or not symbol:
        logging.warning("BROKER_SIDECAR_CMD_UNKNOWN cmd=%s symbol=%s", action, symbol)
        return False

    # Convert date_to_ms → datetime (provider expects tz-aware UTC)
    date_to_utc = None
    if date_to_ms is not None:
        date_to_utc = ms_to_utc_dt(int(date_to_ms))

    try:
        bars = provider.fetch_last_n_m1(symbol, n=n_bars, date_to_utc=date_to_utc)
    except Exception as exc:
        # Повертаємо error response, worker вирішить що робити
        error_resp = json.dumps(
            {
                "v": _CONTRACT_VERSION,
                "req_id": req_id,
                "symbol": symbol,
                "bars": [],
                "error": str(exc),
            }
        )
        redis_cli.rpush(target_key, error_resp)
        if reply_to:
            redis_cli.expire(target_key, _ipc_reply_ttl_s)
        else:
            redis_cli.ltrim(target_key, -_MAX_LIST_LEN, -1)
        logging.warning("BROKER_SIDECAR_FETCH_ERROR symbol=%s err=%s", symbol, exc)
        return True  # needs reconnect

    # Check for silent provider errors (SDK exceptions swallowed internally)
    last_err = provider.consume_last_error()
    needs_reconnect = False
    if last_err:
        logging.warning(
            "BROKER_SIDECAR_PROVIDER_ERROR symbol=%s context=%s err=%s",
            symbol,
            last_err[0],
            last_err[1],
        )
        needs_reconnect = True

    # Серіалізація: batch response
    bar_dicts = [b.to_dict() for b in bars] if bars else []
    resp = json.dumps(
        {
            "v": _CONTRACT_VERSION,
            "req_id": req_id,
            "symbol": symbol,
            "bars": bar_dicts,
            "error": None,
        }
    )
    redis_cli.rpush(target_key, resp)
    if reply_to:
        redis_cli.expire(target_key, _ipc_reply_ttl_s)
    else:
        redis_cli.ltrim(target_key, -_MAX_LIST_LEN, -1)

    if bars:
        logging.info(
            "BROKER_SIDECAR_FETCHED symbol=%s n=%d returned=%d",
            symbol,
            n_bars,
            len(bars),
        )
    else:
        logging.warning(
            "BROKER_SIDECAR_EMPTY symbol=%s n=%d date_to_ms=%s",
            symbol,
            n_bars,
            date_to_ms,
        )

    return needs_reconnect


# ---------------------------------------------------------------------------
# Tick relay: FXCM OFFERS → Redis PubSub (V2, ADR-0016 companion)
# ---------------------------------------------------------------------------


class _TickRelay:
    """Relay FXCM OFFERS updates → Redis PubSub. Called from SDK thread."""

    def __init__(
        self, redis_cli, channel, namespace, aliases, min_interval_ms, ttl_s, price_mode
    ):
        self._cli = redis_cli
        self._channel = channel
        self._ns = namespace
        self._aliases = aliases
        self._min_ms = max(0, int(min_interval_ms))
        self._ttl = max(0, int(ttl_s))
        self._mode = str(price_mode)
        self._last_pub_ms = {}
        self._seq = 0
        self._count = 0
        self._errors = 0
        self._last_stats_ts = time.time()

    def handle_row(self, row):
        """Called from ForexConnect SDK callback thread."""
        if row is None:
            return
        # Extract symbol
        sym_raw = None
        for k in ("Instrument", "instrument"):
            try:
                sym_raw = getattr(row, k, None)
            except Exception:
                pass
            if sym_raw is not None:
                break
        if sym_raw is None:
            return
        sym = self._aliases.get(str(sym_raw).strip())
        if not sym:
            return
        # Extract bid/ask
        bid = None
        ask = None
        for k in ("Bid", "bid"):
            try:
                v = getattr(row, k, None)
                if v is not None:
                    bid = float(v)
                    break
            except Exception:
                pass
        for k in ("Ask", "ask"):
            try:
                v = getattr(row, k, None)
                if v is not None:
                    ask = float(v)
                    break
            except Exception:
                pass
        if bid is None and ask is None:
            return
        # Price mode
        if self._mode == "bid" and bid is not None:
            mid = bid
        elif self._mode == "ask" and ask is not None:
            mid = ask
        elif bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
        else:
            mid = bid if bid is not None else ask
        # Throttle
        now_ms = int(time.time() * 1000)
        last = self._last_pub_ms.get(sym)
        if last is not None and now_ms - last < self._min_ms:
            return
        # Timestamp
        tick_ts_ms = None
        src = "fxcm"
        for k in ("Time", "time"):
            try:
                v = getattr(row, k, None)
                if v is not None:
                    tick_ts_ms = to_ms(v)
                    break
            except Exception:
                pass
        if tick_ts_ms is None:
            tick_ts_ms = now_ms
            src = "fxcm_wallclock"
        # Publish
        payload = json.dumps(
            {
                "v": 1,
                "symbol": sym,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "tick_ts_ms": int(tick_ts_ms),
                "src": src,
                "seq": self._seq,
            }
        )
        self._seq += 1
        try:
            self._cli.publish(self._channel, payload.encode("utf-8"))
            if self._ttl > 0:
                lk = "%s:tick:last:%s" % (self._ns, symbol_key(sym))
                self._cli.setex(lk, self._ttl, payload.encode("utf-8"))
            self._last_pub_ms[sym] = now_ms
            self._count += 1
        except Exception as exc:
            self._errors += 1
            if self._errors <= 5 or self._errors % 100 == 0:
                logging.warning("TICK_RELAY_PUB_ERR err=%s n=%d", exc, self._errors)
        # Periodic stats
        now = time.time()
        if now - self._last_stats_ts >= 60:
            logging.info(
                "TICK_RELAY_STATS published=%d errors=%d seq=%d",
                self._count,
                self._errors,
                self._seq,
            )
            self._count = 0
            self._errors = 0
            self._last_stats_ts = now


def _setup_tick_sub(fx, relay, timeout_s=30):
    """Subscribe to FXCM OFFERS table. Returns listener ref or None."""
    if fxcorepy is None:
        logging.warning("TICK_RELAY_SKIP fxcorepy not available")
        return None

    tm = getattr(fx, "table_manager", None)
    if tm is None:
        logging.warning("TICK_RELAY_NO_TABLE_MANAGER")
        return None

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if tm.status == fxcorepy.O2GTableManagerStatus.TABLES_LOADED:
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        logging.warning("TICK_RELAY_TABLES_TIMEOUT timeout=%ds", timeout_s)
        return None

    offers = tm.get_table(fxcorepy.O2GTableType.OFFERS)
    if offers is None:
        logging.warning("TICK_RELAY_NO_OFFERS_TABLE")
        return None

    class _L(fxcorepy.AO2GTableListener):
        def __init__(self, r):
            super(_L, self).__init__()
            self._r = r

        def on_added(self, rid, row):
            self._r.handle_row(row)

        def on_changed(self, rid, row):
            self._r.handle_row(row)

        def on_deleted(self, rid, row):
            pass

    listener = _L(relay)
    offers.subscribe_update(fxcorepy.O2GTableUpdateType.UPDATE, listener)
    offers.subscribe_update(fxcorepy.O2GTableUpdateType.INSERT, listener)
    logging.info("TICK_RELAY_SUBSCRIBED ch=%s", relay._channel)
    return listener


def main():
    # type: () -> int
    _setup_logging()
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    report = load_env_secrets()
    if report.loaded:
        logging.info(
            "ENV: secrets_loaded path=%s keys=%d", report.path, report.keys_count
        )

    config_path = pick_config_path()
    cfg = load_system_config(config_path)
    logging.info("BROKER_SIDECAR_CONFIG path=%s", config_path)

    # Redis
    if redis_lib is None:
        logging.error("BROKER_SIDECAR_NO_REDIS (pip install redis)")
        return 1

    spec = resolve_redis_spec(cfg, role="broker_sidecar", log=True)
    if spec is None:
        logging.error("BROKER_SIDECAR_REDIS_DISABLED")
        return 1

    global _ipc_reply_ttl_s
    _ipc_reply_ttl_s = int(cfg.get("broker_ipc_reply_ttl_s", _DEFAULT_IPC_REPLY_TTL_S))
    cmd_key = "%s:%s" % (spec.namespace, _CMD_QUEUE_SUFFIX)
    bars_key = "%s:%s" % (spec.namespace, _BARS_QUEUE_SUFFIX)

    redis_cli = redis_lib.Redis(
        host=spec.host,
        port=spec.port,
        db=spec.db,
        decode_responses=True,
        socket_timeout=10,
        socket_connect_timeout=5,
    )

    # ---- Tick relay setup (V2: ticks via same FXCM session) ----
    tick_relay = None
    tick_listener = None
    tick_enabled = bool(cfg.get("tick_stream_enabled", False))
    if tick_enabled:
        channel = pick_tick_channel(cfg)
        if channel:
            raw_syms = cfg.get("tick_stream_symbols")
            syms = (
                [str(x) for x in raw_syms if str(x).strip()]
                if isinstance(raw_syms, list) and raw_syms
                else symbols_from_cfg(cfg)
            )
            # Exclude binance symbols
            bn_cfg = cfg.get("binance", {})
            if isinstance(bn_cfg, dict) and bn_cfg.get("enabled", False):
                bn_s = set(bn_cfg.get("symbols", []))
                syms = [s for s in syms if s not in bn_s]
            aliases = build_symbol_aliases(syms)
            tick_redis = redis_lib.Redis(
                host=spec.host,
                port=spec.port,
                db=spec.db,
                decode_responses=False,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
            )
            tick_relay = _TickRelay(
                tick_redis,
                channel,
                spec.namespace,
                aliases,
                int(cfg.get("tick_stream_min_interval_ms", 200)),
                int(cfg.get("tick_stream_last_tick_ttl_s", 30)),
                str(cfg.get("tick_stream_price_mode", "bid")),
            )
            logging.info(
                "TICK_RELAY_READY ch=%s symbols=%s",
                channel,
                ",".join(syms),
            )
        else:
            logging.warning(
                "TICK_RELAY_NO_CHANNEL tick_stream_enabled=true but no channel"
            )
    else:
        logging.info("TICK_RELAY_DISABLED tick_stream_enabled=false")

    # FXCM provider
    provider = _build_provider(cfg)
    connected = False

    logging.info(
        "BROKER_SIDECAR_START cmd_key=%s bars_key=%s",
        cmd_key,
        bars_key,
    )

    _consecutive_failures = 0
    while _running:
        # Ensure FXCM session
        if not connected:
            try:
                provider.__enter__()
                connected = True
                _consecutive_failures = 0
                # Subscribe to OFFERS for tick relay (V2)
                tick_listener = None
                if tick_relay is not None:
                    tick_listener = _setup_tick_sub(provider._fx, tick_relay)
                logging.info("BROKER_SIDECAR_FXCM_CONNECTED")
            except Exception as exc:
                _consecutive_failures += 1
                if _consecutive_failures == 10:
                    logging.error(
                        "BROKER_SIDECAR_ESCALATION послідовних_збоїв=%d — FXCM стабільно не підключається (~5хв)",
                        _consecutive_failures,
                    )
                elif _consecutive_failures == 60:
                    logging.critical(
                        "BROKER_SIDECAR_CRITICAL_FAILURE послідовних_збоїв=%d — немає з'єднання ~30хв",
                        _consecutive_failures,
                    )
                logging.warning(
                    "BROKER_SIDECAR_FXCM_CONNECT_FAIL err=%s cooldown=%ds",
                    exc,
                    _RECONNECT_COOLDOWN_S,
                )
                time.sleep(_RECONNECT_COOLDOWN_S)
                continue

        # Wait for command
        try:
            result = redis_cli.blpop([cmd_key], timeout=_BLPOP_TIMEOUT_S)
        except Exception as exc:
            logging.warning("BROKER_SIDECAR_REDIS_ERROR err=%s", exc)
            time.sleep(2)
            continue

        if result is None:
            continue  # timeout, loop back

        _key, cmd_raw = result  # type: ignore[misc]
        try:
            needs_reconnect = _handle_command(provider, cmd_raw, redis_cli, bars_key)
        except Exception as exc:
            logging.warning("BROKER_SIDECAR_HANDLE_ERROR err=%s", exc)
            needs_reconnect = True

        if needs_reconnect:
            logging.warning("BROKER_SIDECAR_RECONNECTING reason=provider_error")
            connected = False
            tick_listener = None  # release OFFERS subscription
            try:
                provider.__exit__(None, None, None)
            except Exception:
                logging.debug("BROKER_SIDECAR_EXIT_CLEANUP_FAIL", exc_info=True)

    # Cleanup
    logging.info("BROKER_SIDECAR_SHUTDOWN")
    tick_listener = None  # release OFFERS subscription
    if connected:
        try:
            provider.__exit__(None, None, None)
        except Exception:
            logging.debug("BROKER_SIDECAR_SHUTDOWN_CLEANUP_FAIL", exc_info=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
