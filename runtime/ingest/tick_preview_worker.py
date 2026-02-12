from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Iterable

from env_profile import load_env_secrets
from core.config_loader import pick_config_path, load_system_config, env_str
from runtime.ingest.tick_agg import TickAggregator
from runtime.store.redis_spec import resolve_redis_spec
from runtime.store.uds import build_uds_from_config

try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore


@dataclass(frozen=True)
class PreviewConfig:
    enabled: bool
    tfs: list[int]
    publish_min_interval_ms: int
    curr_ttl_s: int
    symbols: list[str]
    channel: str


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def _pick_tick_channel() -> Optional[str]:
    channel = env_str("FXCM_PRICE_TICK_CHANNEL")
    if channel:
        return channel
    legacy = env_str("FXCM_PRICE_SNAPSHOT_CHANNEL")
    if legacy:
        logging.warning(
            "TickPreview: FXCM_PRICE_TICK_CHANNEL не заданий, fallback до FXCM_PRICE_SNAPSHOT_CHANNEL"
        )
        return legacy
    return None


def _parse_preview_cfg(cfg: dict[str, Any]) -> PreviewConfig:
    enabled = bool(cfg.get("preview_tick_enabled", False))
    raw_tfs = cfg.get("preview_tick_tfs_s", [60, 180])
    tfs: list[int] = []
    if isinstance(raw_tfs, list):
        for item in raw_tfs:
            try:
                tf_s = int(item)
            except Exception:
                continue
            if tf_s > 0:
                tfs.append(tf_s)
    publish_min_interval_ms = int(cfg.get("preview_tick_publish_min_interval_ms", 250))
    curr_ttl_s = int(cfg.get("preview_curr_ttl_s", 60))
    symbols_raw = cfg.get("preview_tick_symbols")
    symbols: list[str] = []
    if isinstance(symbols_raw, list):
        symbols = [str(x) for x in symbols_raw if str(x).strip()]
    channel = _pick_tick_channel()
    return PreviewConfig(
        enabled=enabled,
        tfs=tfs,
        publish_min_interval_ms=publish_min_interval_ms,
        curr_ttl_s=curr_ttl_s,
        symbols=symbols,
        channel=channel or "",
    )


def _symbols_from_cfg(cfg: dict[str, Any]) -> list[str]:
    raw = cfg.get("symbols")
    if isinstance(raw, list) and raw:
        out = [str(x) for x in raw if str(x).strip()]
        if out:
            return out
    symbol = cfg.get("symbol")
    return [str(symbol)] if symbol else []


def _build_symbol_aliases(symbols: Iterable[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for sym in symbols:
        canon = str(sym).strip()
        if not canon:
            continue
        aliases[canon] = canon
        aliases[canon.replace("/", "")] = canon
        aliases[canon.replace("/", "_")] = canon
    return aliases


def _to_ms(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    try:
        value = float(raw)
    except Exception:
        return None
    if value <= 0:
        return None
    if value < 100_000_000_000:
        value *= 1000.0
    return int(value)


def _pick_price(payload: dict[str, Any]) -> Optional[float]:
    mid = payload.get("mid")
    if mid is not None:
        try:
            return float(mid)
        except Exception:
            return None
    bid = payload.get("bid")
    ask = payload.get("ask")
    if bid is None or ask is None:
        return None
    try:
        return (float(bid) + float(ask)) / 2.0
    except Exception:
        return None


class TickPreviewWorker:
    # tick_v1 required fields (from core/contracts/public/marketdata_v1/tick_v1.json)
    _TICK_REQUIRED = ("v", "symbol", "tick_ts_ms", "src", "seq")
    _TICK_ALLOWED = {"v", "symbol", "bid", "ask", "mid", "tick_ts_ms", "src", "seq"}
    # "0 ticks loud" — поріг тиші (секунди)
    _ZERO_TICKS_WARN_INTERVAL_S = 120

    def __init__(
        self,
        *,
        uds: Any,
        tfs: list[int],
        publish_min_interval_ms: int,
        curr_ttl_s: int,
        symbols: list[str],
        channel: str,
    ) -> None:
        self._uds = uds
        self._tfs = [int(x) for x in tfs if int(x) > 0]
        self._publish_min_interval_ms = max(0, int(publish_min_interval_ms))
        self._curr_ttl_s = max(1, int(curr_ttl_s))
        self._channel = str(channel)
        self._agg = TickAggregator(tf_allowlist=self._tfs, source="preview_tick")
        self._last_tick_ts_ms: Dict[str, int] = {}
        self._last_pub_ms: Dict[tuple[str, int], int] = {}
        self._last_open_ms: Dict[tuple[str, int], int] = {}
        base_symbols = symbols
        self._symbol_aliases = _build_symbol_aliases(base_symbols)
        self._symbol_allowlist = set(base_symbols)
        self._stats: Dict[str, int] = {}
        self._stats_last_emit_ts = 0.0
        # "0 ticks loud" state
        self._last_tick_rx_ts = time.time()
        self._zero_ticks_warned = False

    def _inc(self, key: str, val: int = 1) -> None:
        self._stats[key] = self._stats.get(key, 0) + int(val)

    def _maybe_emit_stats(self) -> None:
        now = time.time()
        if now - self._stats_last_emit_ts < 60:
            return
        self._stats_last_emit_ts = now
        # "0 ticks loud": якщо тиків не було > _ZERO_TICKS_WARN_INTERVAL_S
        silence_s = now - self._last_tick_rx_ts
        if silence_s > self._ZERO_TICKS_WARN_INTERVAL_S:
            if not self._zero_ticks_warned:
                logging.warning(
                    "TickPreview: 0 тиків вже %.0f с (канал=%s) — можливо ринок закритий або channel невірний",
                    silence_s,
                    self._channel,
                )
                self._zero_ticks_warned = True
        else:
            self._zero_ticks_warned = False
        if not self._stats:
            return
        payload = dict(self._stats)
        self._stats.clear()
        logging.info("TICK_PREVIEW_STATS %s", json.dumps(payload, ensure_ascii=False))

    def _normalize_symbol(self, raw: Any) -> Optional[str]:
        if raw is None:
            return None
        key = str(raw).strip()
        if not key:
            return None
        if self._symbol_aliases:
            canon = self._symbol_aliases.get(key)
            if canon is None:
                return None
            return canon
        return key

    def _validate_tick_schema(self, payload: dict[str, Any]) -> Optional[str]:
        """Процедурний guard за tick_v1.json (без зовнішньої залежності jsonschema)."""
        if not isinstance(payload, dict):
            return "not_dict"
        for field in self._TICK_REQUIRED:
            if field not in payload:
                return f"missing_{field}"
        if not isinstance(payload.get("v"), int):
            return "v_not_int"
        if not isinstance(payload.get("symbol"), str):
            return "symbol_not_str"
        if not isinstance(payload.get("tick_ts_ms"), (int, float)):
            return "tick_ts_ms_not_number"
        if not isinstance(payload.get("src"), str):
            return "src_not_str"
        if not isinstance(payload.get("seq"), int):
            return "seq_not_int"
        # additionalProperties=false
        extra = set(payload.keys()) - self._TICK_ALLOWED
        if extra:
            return f"extra_fields:{','.join(sorted(extra))}"
        return None

    def on_tick(self, payload: dict[str, Any]) -> None:
        self._inc("ticks_in_total")
        self._last_tick_rx_ts = time.time()
        self._zero_ticks_warned = False
        # Schema guard (tick_v1 contract)
        schema_err = self._validate_tick_schema(payload)
        if schema_err is not None:
            self._inc("ticks_dropped_schema")
            self._inc(f"ticks_schema_err:{schema_err}")
            return
        version = payload.get("v")
        if version is not None:
            try:
                version = int(version)
            except Exception:
                version = -1
            if version != 1:
                self._inc("ticks_dropped_version")
                return
        symbol = self._normalize_symbol(payload.get("symbol"))
        if symbol is None:
            self._inc("ticks_dropped_symbol")
            return
        if self._symbol_allowlist and symbol not in self._symbol_allowlist:
            self._inc("ticks_dropped_symbol")
            return
        tick_ts_ms = _to_ms(payload.get("tick_ts"))
        if tick_ts_ms is None:
            tick_ts_ms = _to_ms(payload.get("tick_ts_ms"))
        if tick_ts_ms is None:
            tick_ts_ms = _to_ms(payload.get("snap_ts"))
        if tick_ts_ms is None:
            self._inc("ticks_dropped_ts")
            return
        last_ts = self._last_tick_ts_ms.get(symbol)
        if last_ts is not None and tick_ts_ms < last_ts:
            self._inc("ticks_dropped_out_of_order")
            return
        self._last_tick_ts_ms[symbol] = tick_ts_ms
        price = _pick_price(payload)
        if price is None:
            self._inc("ticks_dropped_price")
            return

        for tf_s in self._tfs:
            bar = self._agg.update(symbol, tf_s, tick_ts_ms, price)
            if bar is None:
                continue
            key = (symbol, tf_s)
            last_open = self._last_open_ms.get(key)
            rollover = last_open is None or last_open != bar.open_time_ms
            now_ms = int(time.time() * 1000)
            last_pub = self._last_pub_ms.get(key)
            allow_publish = rollover
            if not allow_publish and last_pub is not None:
                if now_ms - last_pub >= self._publish_min_interval_ms:
                    allow_publish = True
            if not allow_publish:
                self._inc("preview_publish_throttled_total")
                continue
            try:
                self._uds.publish_preview_bar(bar, ttl_s=self._curr_ttl_s)
                self._last_pub_ms[key] = now_ms
                self._last_open_ms[key] = bar.open_time_ms
                self._inc("preview_publish_total")
            except Exception as exc:
                logging.warning(
                    "TickPreview: publish помилка symbol=%s tf_s=%s err=%s",
                    symbol,
                    tf_s,
                    exc,
                )
                self._inc("preview_publish_errors_total")
        self._maybe_emit_stats()

    def run_forever(self, redis_client: Any) -> None:
        while True:
            try:
                pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
                pubsub.subscribe(self._channel)
                logging.info("TickPreview: підписка на канал %s", self._channel)
                for msg in pubsub.listen():
                    if not isinstance(msg, dict):
                        continue
                    data = msg.get("data")
                    if data is None:
                        continue
                    if isinstance(data, bytes):
                        raw = data.decode("utf-8", errors="ignore")
                    else:
                        raw = str(data)
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        self._inc("ticks_dropped_json")
                        continue
                    if isinstance(payload, dict):
                        self.on_tick(payload)
            except Exception as exc:
                logging.warning("TickPreview: помилка pubsub err=%s", exc)
                time.sleep(1.0)


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
        logging.error("TickPreview: не вдалося прочитати config.json err=%s", exc)
        time.sleep(5.0)
        return 2

    preview_cfg = _parse_preview_cfg(cfg)
    if not preview_cfg.enabled:
        logging.warning("TickPreview: preview_tick_enabled=false, воркер у режимі очікування")
        while True:
            time.sleep(60.0)

    if not preview_cfg.channel:
        logging.error("TickPreview: tick-канал не заданий")
        while True:
            time.sleep(60.0)

    if redis_lib is None:
        logging.error("TickPreview: redis бібліотека недоступна")
        while True:
            time.sleep(60.0)

    spec = resolve_redis_spec(cfg, role="tick_preview")
    if spec is None:
        logging.error("TickPreview: Redis вимкнено у config")
        while True:
            time.sleep(60.0)

    data_root = str(cfg.get("data_root", "./data_v3"))
    boot_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    uds = build_uds_from_config(
        config_path,
        data_root,
        boot_id,
        role="writer",
        writer_components=False,
    )

    worker = TickPreviewWorker(
        uds=uds,
        tfs=preview_cfg.tfs,
        publish_min_interval_ms=preview_cfg.publish_min_interval_ms,
        curr_ttl_s=preview_cfg.curr_ttl_s,
        symbols=preview_cfg.symbols or _symbols_from_cfg(cfg),
        channel=preview_cfg.channel,
    )

    client = redis_lib.Redis(
        host=spec.host,
        port=spec.port,
        db=spec.db,
        decode_responses=False,
        socket_timeout=None,
        socket_connect_timeout=1.0,
    )
    worker.run_forever(client)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
