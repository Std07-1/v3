from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from env_profile import load_env_secrets
from core.config_loader import pick_config_path, load_system_config, env_str
from runtime.store.redis_keys import symbol_key
from runtime.store.redis_spec import resolve_redis_spec
from runtime.ingest.tick_common import (
    pick_tick_channel,
    symbols_from_cfg,
    build_symbol_aliases,
    to_ms,
)

try:
    from forexconnect import ForexConnect, fxcorepy  # type: ignore
except Exception:  # noqa: BLE001
    ForexConnect = None  # type: ignore
    fxcorepy = None  # type: ignore

try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore


@dataclass(frozen=True)
class TickStreamConfig:
    enabled: bool
    symbols: list[str]
    min_interval_ms: int
    last_tick_ttl_s: int
    price_mode: str
    channel: str


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )





def _parse_stream_cfg(cfg: dict[str, Any]) -> TickStreamConfig:
    enabled = bool(cfg.get("tick_stream_enabled", False))
    raw_symbols = cfg.get("tick_stream_symbols")
    symbols: list[str] = []
    if isinstance(raw_symbols, list):
        symbols = [str(x) for x in raw_symbols if str(x).strip()]
    min_interval_ms = int(cfg.get("tick_stream_min_interval_ms", 200))
    last_tick_ttl_s = int(cfg.get("tick_stream_last_tick_ttl_s", 30))
    price_mode = str(cfg.get("tick_stream_price_mode", "mid")).lower().strip()
    if price_mode not in ("mid", "bid", "ask"):
        price_mode = "mid"
    channel = pick_tick_channel(cfg)
    return TickStreamConfig(
        enabled=enabled,
        symbols=symbols,
        min_interval_ms=min_interval_ms,
        last_tick_ttl_s=last_tick_ttl_s,
        price_mode=price_mode,
        channel=channel or "",
    )





def _extract_row(update: Any) -> Optional[Any]:
    if update is None:
        return None
    getter = getattr(update, "get_row", None)
    if callable(getter):
        try:
            row = getter()
            if row is not None:
                return row
        except Exception:
            pass
    row = getattr(update, "row", None)
    if row is not None:
        return row
    return update


def _row_value(row: Any, keys: list[str]) -> Optional[Any]:
    if row is None:
        return None
    if isinstance(row, dict):
        for key in keys:
            if key in row:
                return row.get(key)
    for key in keys:
        if hasattr(row, key):
            try:
                return getattr(row, key)
            except Exception:
                continue
    getter = getattr(row, "get", None)
    if callable(getter):
        for key in keys:
            try:
                value = getter(key)
            except Exception:
                value = None
            if value is not None:
                return value
    return None


def _pick_price(mode: str, bid: Optional[float], ask: Optional[float], mid: Optional[float]) -> Optional[float]:
    if mode == "bid":
        return bid
    if mode == "ask":
        return ask
    if mid is not None:
        return mid
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


class FxcmTickPublisher:
    def __init__(
        self,
        *,
        fxcm_user: str,
        fxcm_password: str,
        fxcm_url: str,
        fxcm_connection: str,
        symbols: list[str],
        channel: str,
        redis_client: Any,
        namespace: str,
        min_interval_ms: int,
        last_tick_ttl_s: int,
        price_mode: str,
    ) -> None:
        self._user = fxcm_user
        self._password = fxcm_password
        self._url = fxcm_url
        self._connection = fxcm_connection
        self._channel = str(channel)
        self._client = redis_client
        self._namespace = str(namespace)
        self._symbols = symbols
        self._aliases = build_symbol_aliases(symbols)
        self._min_interval_ms = max(0, int(min_interval_ms))
        self._last_tick_ttl_s = max(0, int(last_tick_ttl_s))
        self._price_mode = str(price_mode)
        self._last_pub_ms: Dict[str, int] = {}
        self._last_tick_ts_ms: Dict[str, int] = {}
        self._stats: Dict[str, int] = {}
        self._stats_last_emit_ts = 0.0
        self._seq = 0
        self._fx: Optional[Any] = None
        self._offers_listener: Optional[Any] = None

    def _inc(self, key: str, val: int = 1) -> None:
        self._stats[key] = self._stats.get(key, 0) + int(val)

    def _maybe_emit_stats(self) -> None:
        now = time.time()
        if now - self._stats_last_emit_ts < 60:
            return
        self._stats_last_emit_ts = now
        if not self._stats:
            return
        payload = dict(self._stats)
        # Wallclock fallback ratio warning (раз/60с)
        wc_count = payload.get("ticks_ts_fallback_wallclock", 0)
        pub_count = payload.get("ticks_published_total", 0)
        if wc_count > 0 and pub_count > 0:
            ratio = wc_count / max(1, pub_count)
            payload["wallclock_ratio"] = round(ratio, 3)
            if ratio > 0.1:
                logging.warning(
                    "TickPublisher: wallclock fallback ratio=%.1f%% (%d/%d) — FXCM не надає tick_ts",
                    ratio * 100,
                    wc_count,
                    pub_count,
                )
        self._stats.clear()
        logging.info("TICK_PUBLISHER_STATS %s", json.dumps(payload, ensure_ascii=False))

    def _normalize_symbol(self, raw: Any) -> Optional[str]:
        if raw is None:
            return None
        key = str(raw).strip()
        if not key:
            return None
        if self._aliases:
            return self._aliases.get(key)
        return key

    def _handle_row(self, row: Any) -> None:
        self._inc("ticks_in_total")
        symbol_raw = _row_value(row, ["Instrument", "instrument", "symbol", "Symbol"])
        symbol = self._normalize_symbol(symbol_raw)
        if symbol is None:
            self._inc("ticks_dropped_symbol")
            return

        bid_raw = _row_value(row, ["Bid", "bid", "BidPrice", "bid_price"])
        ask_raw = _row_value(row, ["Ask", "ask", "AskPrice", "ask_price"])
        mid_raw = _row_value(row, ["Mid", "mid"])
        bid = float(bid_raw) if bid_raw is not None else None
        ask = float(ask_raw) if ask_raw is not None else None
        mid = float(mid_raw) if mid_raw is not None else None
        price = _pick_price(self._price_mode, bid, ask, mid)
        if price is None:
            self._inc("ticks_dropped_price")
            return

        ts_raw = _row_value(row, ["Time", "time", "timestamp", "tick_ts", "tick_ts_ms"])
        tick_ts_ms = to_ms(ts_raw)
        ts_wallclock = False
        if tick_ts_ms is None:
            tick_ts_ms = int(time.time() * 1000)
            ts_wallclock = True
            self._inc("ticks_ts_fallback_wallclock")
        last_ts = self._last_tick_ts_ms.get(symbol)
        if last_ts is not None and tick_ts_ms < last_ts:
            self._inc("ticks_dropped_out_of_order")
            return
        self._last_tick_ts_ms[symbol] = tick_ts_ms

        now_ms = int(time.time() * 1000)
        last_pub = self._last_pub_ms.get(symbol)
        if last_pub is not None and now_ms - last_pub < self._min_interval_ms:
            self._inc("ticks_throttled_total")
            return

        tick_src = "fxcm_wallclock" if ts_wallclock else "fxcm"
        payload = {
            "v": 1,
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "mid": price,  # selected price (bid/ask/mid) — preview worker reads this
            "tick_ts_ms": int(tick_ts_ms),
            "src": tick_src,
            "seq": self._seq,
        }
        self._seq += 1
        raw = json.dumps(payload, ensure_ascii=False)
        try:
            self._client.publish(self._channel, raw.encode("utf-8"))
            if self._last_tick_ttl_s > 0:
                key = f"{self._namespace}:tick:last:{symbol_key(symbol)}"
                self._client.setex(key, self._last_tick_ttl_s, raw.encode("utf-8"))
            self._last_pub_ms[symbol] = now_ms
            self._inc("ticks_published_total")
        except Exception as exc:
            logging.warning("TickPublisher: publish err=%s", exc)
            self._inc("publish_errors_total")
        self._maybe_emit_stats()

    def _on_offer_update(self, *args: Any) -> None:
        if not args:
            return
        row = None
        for item in reversed(args):
            row = _extract_row(item)
            if row is not None:
                break
        if row is None:
            return
        self._handle_row(row)

    def _wait_tables(self, table_manager: Any, timeout_s: int = 30) -> bool:
        if fxcorepy is None:
            return False
        deadline = time.time() + max(1, int(timeout_s))
        while time.time() < deadline:
            try:
                status = table_manager.status
            except Exception:
                status = None
            if status == fxcorepy.O2GTableManagerStatus.TABLES_LOADED:
                return True
            time.sleep(0.5)
        return False

    def run_forever(self) -> None:
        self._stop_requested = False
        while not self._stop_requested:
            try:
                self._run_once()
            except KeyboardInterrupt:
                logging.info("TickPublisher: отримано сигнал зупинки CTRL+C")
                break
            except Exception as exc:
                logging.warning("TickPublisher: помилка err=%s", exc)
                time.sleep(5.0)
        self._cleanup()

    def _cleanup(self) -> None:
        """Коректне завершення: відписка від OFFERS, logout."""
        try:
            if getattr(self, '_offers_listener', None) is not None:
                logging.debug("TickPublisher: відписка від OFFERS listener")
                self._offers_listener = None
            if getattr(self, '_fx', None) is not None:
                try:
                    self._fx.logout()
                except Exception:
                    pass
                self._fx = None
        except Exception as exc:
            logging.debug("TickPublisher: cleanup err=%s", exc)
        logging.info("TickPublisher: завершено коректно")

    def _run_once(self) -> None:
        if ForexConnect is None or fxcorepy is None:
            logging.error("TickPublisher: forexconnect недоступний")
            time.sleep(10.0)
            return
        class _OffersListener(fxcorepy.AO2GTableListener):
            def __init__(self, owner: "FxcmTickPublisher") -> None:
                super().__init__()
                self._owner = owner

            def on_added(self, row_id: str, row: Any) -> None:
                self._owner._handle_row(row)

            def on_changed(self, row_id: str, row: Any) -> None:
                self._owner._handle_row(row)

            def on_deleted(self, row_id: str, row: Any) -> None:
                return

        self._fx = ForexConnect()
        self._fx.login(self._user, self._password, self._url, self._connection)
        table_manager = getattr(self._fx, "table_manager", None)
        if table_manager is None:
            logging.error("TickPublisher: table_manager недоступний")
            return
        if not self._wait_tables(table_manager):
            logging.error("TickPublisher: tables не завантажені")
            return
        offers = table_manager.get_table(fxcorepy.O2GTableType.OFFERS)
        if offers is None:
            logging.error("TickPublisher: OFFERS table недоступна")
            return
        self._offers_listener = _OffersListener(self)
        offers.subscribe_update(fxcorepy.O2GTableUpdateType.UPDATE, self._offers_listener)
        offers.subscribe_update(fxcorepy.O2GTableUpdateType.INSERT, self._offers_listener)
        logging.info("TickPublisher: підписка на OFFERS (%s)", ",".join(self._symbols))
        try:
            while not self._stop_requested:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass


def main() -> int:
    _setup_logging()
    report = load_env_secrets()
    if report.loaded:
        logging.info("ENV: secrets_loaded path=%s keys=%d", report.path, report.keys_count)
    else:
        logging.info("ENV: .env не завантажено")

    config_path = pick_config_path()
    try:
        cfg = load_system_config(config_path)
    except Exception as exc:
        logging.error("TickPublisher: не вдалося прочитати config.json err=%s", exc)
        time.sleep(5.0)
        return 2

    stream_cfg = _parse_stream_cfg(cfg)
    if not stream_cfg.enabled:
        logging.warning("TickPublisher: tick_stream_enabled=false, воркер у режимі очікування")
        while True:
            time.sleep(60.0)

    if not stream_cfg.channel:
        logging.error("TickPublisher: tick-канал не заданий")
        while True:
            time.sleep(60.0)

    if redis_lib is None:
        logging.error("TickPublisher: redis бібліотека недоступна")
        while True:
            time.sleep(60.0)

    spec = resolve_redis_spec(cfg, role="tick_publisher")
    if spec is None:
        logging.error("TickPublisher: Redis вимкнено у config")
        while True:
            time.sleep(60.0)

    fxcm_user = env_str("FXCM_USERNAME") or str(cfg.get("user_id") or "")
    fxcm_password = env_str("FXCM_PASSWORD") or str(cfg.get("password") or "")
    fxcm_url = env_str("FXCM_HOST_URL") or str(cfg.get("url", "http://www.fxcorporate.com/Hosts.jsp"))
    fxcm_connection = env_str("FXCM_CONNECTION") or str(cfg.get("connection", "Demo"))
    if not fxcm_user or not fxcm_password:
        logging.error("TickPublisher: FXCM credentials відсутні")
        while True:
            time.sleep(60.0)

    symbols = stream_cfg.symbols or symbols_from_cfg(cfg)
    client = redis_lib.Redis(
        host=spec.host,
        port=spec.port,
        db=spec.db,
        decode_responses=False,
        socket_timeout=1.0,
        socket_connect_timeout=1.0,
    )

    publisher = FxcmTickPublisher(
        fxcm_user=fxcm_user,
        fxcm_password=fxcm_password,
        fxcm_url=fxcm_url,
        fxcm_connection=fxcm_connection,
        symbols=symbols,
        channel=stream_cfg.channel,
        redis_client=client,
        namespace=spec.namespace,
        min_interval_ms=stream_cfg.min_interval_ms,
        last_tick_ttl_s=stream_cfg.last_tick_ttl_s,
        price_mode=stream_cfg.price_mode,
    )
    publisher.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
