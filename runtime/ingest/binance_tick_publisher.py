"""Binance Tick Publisher — Futures kline_1m WS → Redis pub/sub (ADR-0037).

Аналог tick_publisher_fxcm.py для Binance Futures:
- WebSocket wss://fstream.binance.com/stream?streams={sym}@kline_1m
- Публікує tick payload у Redis pub/sub (той самий канал що FXCM)
- tick_preview_worker (існуючий, symbol-agnostic) будує preview bars

Tick payload формат (v1, сумісний з FXCM):
  {"v":1, "symbol":"BTCUSDT", "bid":price, "ask":price, "mid":price,
   "tick_ts_ms":..., "src":"binance", "seq":N}
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

from core.config_loader import pick_config_path, load_system_config
from env_profile import load_env_secrets
from runtime.ingest.tick_common import pick_tick_channel
from runtime.store.redis_keys import symbol_key
from runtime.store.redis_spec import resolve_redis_spec

try:
    import redis as redis_lib  # type: ignore
except ImportError:
    redis_lib = None  # type: ignore

try:
    import websockets  # type: ignore
except ImportError:
    websockets = None  # type: ignore


logger = logging.getLogger("binance_tick_publisher")

_BACKOFF_BASE_S = 3
_BACKOFF_MAX_S = 30
_WS_PING_INTERVAL_S = 20


class BinanceTickPublisher:
    """WebSocket kline_1m stream → Redis pub/sub tick payloads."""

    def __init__(
        self,
        *,
        symbols: list[str],
        channel: str,
        redis_client: Any,
        namespace: str,
        min_interval_ms: int = 250,
        last_tick_ttl_s: int = 30,
    ) -> None:
        self._symbols = [s.lower() for s in symbols]
        self._channel = str(channel)
        self._redis = redis_client
        self._namespace = str(namespace)
        self._min_interval_ms = max(0, int(min_interval_ms))
        self._last_tick_ttl_s = max(0, int(last_tick_ttl_s))
        self._last_pub_ms: Dict[str, int] = {}
        self._seq = 0
        self._stats: Dict[str, int] = {}
        self._stats_last_ts = 0.0
        self._stop = False

    def _build_ws_url(self) -> str:
        streams = "/".join(f"{s}@kline_1m" for s in sorted(self._symbols))
        return f"wss://fstream.binance.com/stream?streams={streams}"

    def _inc(self, key: str) -> None:
        self._stats[key] = self._stats.get(key, 0) + 1

    def _maybe_emit_stats(self) -> None:
        now = time.time()
        if now - self._stats_last_ts < 60:
            return
        self._stats_last_ts = now
        if self._stats:
            logger.info("BINANCE_TICK_STATS %s", json.dumps(self._stats))
            self._stats.clear()

    def _handle_kline(self, k: Dict[str, Any]) -> None:
        """Обробка одного kline update з WebSocket."""
        self._inc("klines_in")

        sym = str(k.get("s", "")).upper()
        if not sym:
            self._inc("klines_dropped_no_symbol")
            return

        try:
            price = float(k["c"])  # close = current price
        except (KeyError, ValueError, TypeError):
            self._inc("klines_dropped_no_price")
            return

        tick_ts_ms = int(k.get("T", 0)) or int(time.time() * 1000)

        # Throttle
        now_ms = int(time.time() * 1000)
        last_pub = self._last_pub_ms.get(sym)
        if last_pub is not None and now_ms - last_pub < self._min_interval_ms:
            self._inc("klines_throttled")
            return

        payload = {
            "v": 1,
            "symbol": sym,
            "bid": price,
            "ask": price,
            "mid": price,
            "tick_ts_ms": tick_ts_ms,
            "src": "binance",
            "seq": self._seq,
        }
        self._seq += 1

        raw = json.dumps(payload, ensure_ascii=False)
        try:
            self._redis.publish(self._channel, raw.encode("utf-8"))
            if self._last_tick_ttl_s > 0:
                key = f"{self._namespace}:tick:last:{symbol_key(sym)}"
                self._redis.setex(key, self._last_tick_ttl_s, raw.encode("utf-8"))
            self._last_pub_ms[sym] = now_ms
            self._inc("ticks_published")
        except Exception as exc:
            logger.warning("BINANCE_TICK_PUBLISH_ERROR err=%s", exc)
            self._inc("publish_errors")

        self._maybe_emit_stats()

    async def _consume(self) -> None:
        """Async WebSocket loop з reconnect/backoff."""
        if websockets is None:
            logger.error("BINANCE_TICK_NO_WEBSOCKETS (pip install websockets)")
            return

        url = self._build_ws_url()
        backoff = _BACKOFF_BASE_S

        while not self._stop:
            try:
                async with websockets.connect(
                    url, ping_interval=_WS_PING_INTERVAL_S
                ) as ws:
                    logger.info("BINANCE_WS_CONNECTED symbols=%s", self._symbols)
                    backoff = _BACKOFF_BASE_S

                    async for raw_msg in ws:
                        if self._stop:
                            break
                        try:
                            msg = json.loads(raw_msg)
                            k = msg.get("data", {}).get("k")
                            if k:
                                self._handle_kline(k)
                        except (json.JSONDecodeError, TypeError) as exc:
                            logger.debug(
                                "BINANCE_WS_PARSE_ERROR err=%s msg=%s",
                                exc,
                                str(raw_msg)[:120],
                            )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(
                    "BINANCE_WS_ERROR err=%s reconnect_in=%ds",
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX_S)

    def run_forever(self) -> None:
        """Synchronous entrypoint — запускає async WebSocket loop."""
        self._stop = False
        try:
            asyncio.run(self._consume())
        except KeyboardInterrupt:
            logger.info("BINANCE_TICK_PUBLISHER_STOP (KeyboardInterrupt)")
        finally:
            self._stop = True


# ---------------------------------------------------------------------------
# Entrypoint  (python -m runtime.ingest.binance_tick_publisher)
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
    cfg = load_system_config(config_path)
    bn_cfg = cfg.get("binance", {})

    if not bn_cfg.get("enabled", False):
        logger.info("BINANCE_TICK_PUBLISHER_DISABLED (binance.enabled=false)")
        return 0

    if not bn_cfg.get("tick_ws_enabled", True):
        logger.info("BINANCE_TICK_PUBLISHER_DISABLED (binance.tick_ws_enabled=false)")
        return 0

    symbols = bn_cfg.get("symbols", [])
    if not symbols:
        logger.warning("BINANCE_TICK_PUBLISHER_NO_SYMBOLS")
        return 0

    channel = pick_tick_channel(cfg)
    if not channel:
        logger.error("BINANCE_TICK_PUBLISHER_NO_CHANNEL")
        return 1

    if redis_lib is None:
        logger.error("BINANCE_TICK_PUBLISHER_NO_REDIS (pip install redis)")
        return 1

    spec = resolve_redis_spec(cfg, role="binance_tick_publisher")
    if spec is None:
        logger.error("BINANCE_TICK_PUBLISHER_REDIS_DISABLED")
        return 1

    redis_cli = redis_lib.Redis(
        host=spec.host,
        port=spec.port,
        db=spec.db,
        socket_timeout=30,
        socket_connect_timeout=5,
    )

    min_interval_ms = int(cfg.get("preview_tick_publish_min_interval_ms", 250))
    last_tick_ttl_s = int(cfg.get("tick_stream_last_tick_ttl_s", 30))

    publisher = BinanceTickPublisher(
        symbols=symbols,
        channel=channel,
        redis_client=redis_cli,
        namespace=spec.namespace,
        min_interval_ms=min_interval_ms,
        last_tick_ttl_s=last_tick_ttl_s,
    )

    logger.info(
        "BINANCE_TICK_PUBLISHER_START symbols=%s channel=%s",
        symbols,
        channel,
    )

    publisher.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
