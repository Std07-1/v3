from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from core.time_geom import normalize_bar

from runtime.obs_60s import Obs60s
from runtime.store.layers.disk_layer import DiskLayer
from runtime.store.layers.ram_layer import RamLayer
from runtime.store.layers.redis_layer import RedisLayer

Logging = logging.getLogger("uds")
Logging.setLevel(logging.INFO)

_OBS = Obs60s("uds")

try:
    import redis as redis_lib  # type: ignore
    Logging.debug("UDS: redis бібліотека завантажена")
except Exception:
    redis_lib = None  # type: ignore
    Logging.warning("UDS: redis бібліотека недоступна, RedisLayer вимкнено")


DEFAULT_TF_ALLOWLIST = {300, 900, 1800, 3600, 14400, 86400}
SOURCE_ALLOWLIST = {"history", "derived", "history_agg", ""}
FINAL_SOURCES = {"history", "derived", "history_agg"}
MAX_EVENTS_PER_RESPONSE = 500
REDIS_SOCKET_TIMEOUT_S = 0.4


@dataclass(frozen=True)
class WindowSpec:
    symbol: str
    tf_s: int
    limit: int
    since_open_ms: Optional[int] = None
    to_open_ms: Optional[int] = None
    cold_load: bool = False


@dataclass(frozen=True)
class ReadPolicy:
    force_disk: bool = False
    prefer_redis: bool = False


@dataclass
class WindowResult:
    bars_lwc: list[dict[str, Any]]
    meta: dict[str, Any]
    warnings: list[str]


@dataclass(frozen=True)
class UpdatesSpec:
    symbol: str
    tf_s: int
    since_seq: Optional[int]
    limit: int


@dataclass
class UpdatesResult:
    events: list[dict[str, Any]]
    cursor_seq: int
    disk_last_open_ms: Optional[int]
    bar_close_ms: Optional[int]
    ssot_write_ts_ms: Optional[int]
    api_seen_ts_ms: int
    meta: dict[str, Any]
    warnings: list[str]


class UnifiedDataStore:
    """UnifiedDataStore: оркестрація RAM↔Redis↔Disk."""

    def __init__(
        self,
        *,
        data_root: str,
        boot_id: str,
        tf_allowlist: set[int],
        min_coldload_bars: dict[int, int],
        role: str = "writer",
        ram_layer: Optional[RamLayer] = None,
        redis_layer: Optional[RedisLayer] = None,
        disk_layer: Optional[DiskLayer] = None,
    ) -> None:
        if role not in ("reader", "writer"):
            raise ValueError(f"UDS: невідома роль {role}")
        self._data_root = data_root
        self._boot_id = boot_id
        self._tf_allowlist = tf_allowlist
        self._min_coldload_bars = min_coldload_bars
        self._role = role
        self._ram = ram_layer or RamLayer()
        self._redis = redis_layer
        self._disk = disk_layer or DiskLayer(data_root)
        self._updates_lock = threading.Lock()
        self._updates_seq = 0
        self._updates_last_digest: dict[tuple[str, int, int], str] = {}

    def read_window(self, spec: WindowSpec, policy: ReadPolicy) -> WindowResult:
        warnings: list[str] = []
        meta: dict[str, Any] = {"boot_id": self._boot_id}
        symbol = spec.symbol
        tf_s = spec.tf_s
        if Logging.isEnabledFor(logging.INFO):
            Logging.info(
                "UDS: читання вікна symbol=%s tf_s=%s limit=%s cold_load=%s force_disk=%s prefer_redis=%s",
                symbol,
                tf_s,
                spec.limit,
                spec.cold_load,
                policy.force_disk,
                policy.prefer_redis,
            )
        if tf_s not in self._tf_allowlist:
            warnings.append("tf_not_allowed")

        if policy.force_disk:
            result = self._read_window_disk(spec, meta, warnings)
            _log_window_result(result, warnings, tf_s)
            return result

        if spec.cold_load and policy.prefer_redis and self._redis is not None:
            if tf_s not in self._min_coldload_bars:
                warnings.append("redis_min_missing")
                Logging.warning(
                    "UDS: відсутній min_coldload_bars для tf_s=%s, Redis cold-load вимкнено",
                    tf_s,
                )
            else:
                redis_result = self._read_window_redis(spec, meta, warnings)
                if redis_result is not None:
                    _log_window_result(redis_result, warnings, tf_s)
                    return redis_result

        if spec.since_open_ms is None and spec.to_open_ms is None:
            ram_bars = self._ram.get_window(symbol, tf_s, spec.limit)
            if ram_bars is not None:
                ram_bars, geom = _ensure_sorted_dedup(ram_bars)
                if geom is not None:
                    _mark_geom_fix(meta, warnings, geom, source="ram", tf_s=tf_s)
                meta.update({"source": "ram", "redis_hit": False})
                result = WindowResult(ram_bars, meta, warnings)
                _log_window_result(result, warnings, tf_s)
                return result

            result = self._read_window_disk(spec, meta, warnings)
            _log_window_result(result, warnings, tf_s)
            return result

        result = self._read_window_disk(spec, meta, warnings)
        _log_window_result(result, warnings, tf_s)
        return result

    def read_updates(self, spec: UpdatesSpec) -> UpdatesResult:
        symbol = spec.symbol
        tf_s = spec.tf_s
        limit = spec.limit
        if Logging.isEnabledFor(logging.INFO):
            Logging.info(
                "UDS: читання updates symbol=%s tf_s=%s limit=%s since_seq=%s",
                symbol,
                tf_s,
                limit,
                spec.since_seq,
            )
        parts = self._disk.list_parts(symbol, tf_s)
        events: list[dict[str, Any]] = []
        warnings: list[str] = []
        geom_fix: Optional[dict[str, Any]] = None
        if parts:
            bars, geom = self._disk.read_window_with_geom(
                symbol,
                tf_s,
                limit,
                since_open_ms=None,
                to_open_ms=None,
                use_tail=True,
            )
            if geom is None:
                bars, geom = _ensure_sorted_dedup(bars)
            if geom is not None:
                geom_fix = geom
                _log_geom_fix("disk_tail", tf_s, geom)
            lwc = self._bars_to_lwc(bars)
            for b in lwc:
                key = (symbol, int(b.get("tf_s", 0)), int(b.get("open_time_ms", 0)))
                digest = self._digest_bar(b)
                seq = self._next_seq_for_event(key, digest)
                if seq is None:
                    continue
                ev = self._bar_to_update_event(symbol, b)
                ev["seq"] = seq
                events.append(ev)

        if spec.since_seq is not None:
            events = [ev for ev in events if ev.get("seq") and ev["seq"] > spec.since_seq]

        if len(events) > MAX_EVENTS_PER_RESPONSE:
            events = events[-MAX_EVENTS_PER_RESPONSE:]
            warnings.append("max_events_trimmed")

        if geom_fix is not None and events:
            warnings.append("geom_non_monotonic")

        for ev in events:
            if ev.get("complete") is True and ev.get("source") in FINAL_SOURCES:
                bar = ev.get("bar")
                if isinstance(bar, dict):
                    self._ram.upsert_bar(symbol, tf_s, bar)

        cursor_seq: Optional[int] = None
        for ev in events:
            seq = ev.get("seq")
            if isinstance(seq, int) and (cursor_seq is None or seq > cursor_seq):
                cursor_seq = seq
        if cursor_seq is None and spec.since_seq is not None:
            cursor_seq = spec.since_seq
        if cursor_seq is None:
            cursor_seq = 0

        disk_last_open_ms = self._disk.last_open_ms(symbol, tf_s)
        bar_close_ms = None
        if disk_last_open_ms is not None:
            bar_close_ms = disk_last_open_ms + tf_s * 1000 - 1

        ssot_write_ts_ms = self._disk.last_mtime_ms(symbol, tf_s)
        api_seen_ts_ms = int(time.time() * 1000)

        result = UpdatesResult(
            events=events,
            cursor_seq=cursor_seq,
            disk_last_open_ms=disk_last_open_ms,
            bar_close_ms=bar_close_ms,
            ssot_write_ts_ms=ssot_write_ts_ms,
            api_seen_ts_ms=api_seen_ts_ms,
            meta={"boot_id": self._boot_id},
            warnings=warnings,
        )
        _log_updates_result(result)
        return result

    def upsert_bar(self, symbol: str, tf_s: int, bar_canon: dict[str, Any]) -> None:
        if self._role != "writer":
            Logging.warning(
                "UDS: запис заборонено (UDS_WRITE_FORBIDDEN) role=%s",
                self._role,
            )
            raise RuntimeError(f"UDS_WRITE_FORBIDDEN role={self._role}")
        self._ram.upsert_bar(symbol, tf_s, bar_canon)

    def snapshot_status(self) -> dict[str, Any]:
        status = {"boot_id": self._boot_id}
        status.update(self._ram.stats())
        status["redis_enabled"] = self._redis is not None
        status["ts_ms"] = int(time.time() * 1000)
        return status

    def _read_window_disk(
        self,
        spec: WindowSpec,
        meta: dict[str, Any],
        warnings: list[str],
    ) -> WindowResult:
        use_tail = spec.cold_load or spec.since_open_ms is not None or (
            spec.to_open_ms is not None and True
        )
        bars, geom = self._disk.read_window_with_geom(
            spec.symbol,
            spec.tf_s,
            spec.limit,
            since_open_ms=spec.since_open_ms,
            to_open_ms=spec.to_open_ms,
            use_tail=use_tail,
        )
        if geom is None:
            bars, geom = _ensure_sorted_dedup(bars)
        if geom is not None:
            _mark_geom_fix(
                meta,
                warnings,
                geom,
                source="disk_tail" if use_tail else "disk_range",
                tf_s=spec.tf_s,
            )
        lwc = self._bars_to_lwc(bars)
        meta.update(
            {
                "source": "disk_tail" if use_tail else "disk_range",
                "redis_hit": False,
            }
        )
        if spec.since_open_ms is None and spec.to_open_ms is None:
            self._ram.set_window(spec.symbol, spec.tf_s, lwc)
        return WindowResult(lwc, meta, warnings)

    def _read_window_redis(
        self,
        spec: WindowSpec,
        meta: dict[str, Any],
        warnings: list[str],
    ) -> Optional[WindowResult]:
        if self._redis is None:
            return None
        payload, ttl_left, source, err = self._redis.read_tail_or_snap(
            spec.symbol, spec.tf_s
        )
        if err is not None:
            Logging.warning(
                "UDS: Redis помилка читання source=%s tf_s=%s error=%s",
                spec.symbol,
                spec.tf_s,
                err,
            )
            if err == "redis_error":
                warnings.append("redis_down")
                ext = meta.setdefault("extensions", {})
                if isinstance(ext, dict):
                    degraded = ext.get("degraded")
                    if isinstance(degraded, list):
                        if "redis_down" not in degraded:
                            degraded.append("redis_down")
                    else:
                        ext["degraded"] = ["redis_down"]
            meta.update(
                {
                    "source": "disk",
                    "redis_hit": False,
                    "redis_error_code": err,
                }
            )
            return None
        if payload is None:
            Logging.info(
                "UDS: Redis порожній payload symbol=%s tf_s=%s",
                spec.symbol,
                spec.tf_s,
            )
            meta.update(
                {
                    "source": "disk",
                    "redis_hit": False,
                    "redis_error_code": "redis_empty",
                }
            )
            return None
        if ttl_left is None or ttl_left <= 0:
            Logging.warning(
                "UDS: Redis TTL недійсний symbol=%s tf_s=%s ttl_left=%s",
                spec.symbol,
                spec.tf_s,
                ttl_left,
            )
            meta.update(
                {
                    "source": "disk",
                    "redis_hit": False,
                    "redis_error_code": "redis_ttl_invalid",
                }
            )
            return None

        bars = self._redis_payload_to_bars(payload, spec.symbol, spec.tf_s)
        if not bars:
            meta.update(
                {
                    "source": "disk",
                    "redis_hit": False,
                    "redis_error_code": "redis_empty",
                }
            )
            return None

        bars, geom = _ensure_sorted_dedup(bars)
        if geom is not None:
            _mark_geom_fix(meta, warnings, geom, source=source or "redis_tail", tf_s=spec.tf_s)

        lwc = self._bars_to_lwc(bars)
        if spec.limit > 0:
            lwc = lwc[-spec.limit :]

        min_required = int(self._min_coldload_bars.get(spec.tf_s, 0))
        if min_required > 0 and len(lwc) < min_required:
            meta.update(
                {
                    "source": "disk_fallback_small_tail",
                    "redis_hit": True,
                    "redis_len": len(lwc),
                    "redis_ttl_s_left": ttl_left,
                    "redis_payload_ts_ms": payload.get("payload_ts_ms"),
                    "redis_seq": payload.get("last_seq")
                    if isinstance(payload.get("last_seq"), int)
                    else payload.get("seq"),
                }
            )
            return None

        meta.update(
            {
                "source": source or "redis_tail",
                "redis_hit": True,
                "redis_ttl_s_left": ttl_left,
                "redis_payload_ts_ms": payload.get("payload_ts_ms"),
                "redis_seq": payload.get("last_seq")
                if isinstance(payload.get("last_seq"), int)
                else payload.get("seq"),
            }
        )
        self._ram.set_window(spec.symbol, spec.tf_s, lwc)
        return WindowResult(lwc, meta, warnings)

    def _bars_to_lwc(self, bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for raw in bars:
            b = normalize_bar(raw, mode="incl")
            t = b.get("open_time_ms")
            if not isinstance(t, int):
                continue
            low_val = b.get("low", b.get("l"))
            complete = bool(b.get("complete", True))
            item = {
                "time": t // 1000,
                "open": float(b.get("o")),
                "high": float(b.get("h")),
                "low": float(low_val),
                "close": float(b.get("c")),
                "volume": float(b.get("v", 0.0)),
                "open_time_ms": int(b.get("open_time_ms")),
                "close_time_ms": int(b.get("close_time_ms"))
                if "close_time_ms" in b
                else None,
                "tf_s": int(b.get("tf_s")),
                "src": str(b.get("src", "")),
                "complete": complete,
            }
            if complete and "close_time_ms" in b:
                item["event_ts"] = int(b.get("close_time_ms"))
            if "last_price" in b:
                try:
                    item["last_price"] = float(b.get("last_price"))
                except Exception:
                    pass
            if "last_tick_ts" in b:
                try:
                    item["last_tick_ts"] = int(b.get("last_tick_ts"))
                except Exception:
                    pass
            out.append(item)
        return out

    def _redis_payload_to_bars(
        self, payload: dict[str, Any], symbol: str, tf_s: int
    ) -> list[dict[str, Any]]:
        bars: list[dict[str, Any]] = []
        complete = bool(payload.get("complete", True))
        source = str(payload.get("source", ""))
        raw_bars = payload.get("bars")
        if isinstance(raw_bars, list):
            for item in raw_bars:
                if not isinstance(item, dict):
                    continue
                bar = self._redis_payload_bar_to_canonical(item, symbol, tf_s, complete, source)
                if bar is not None:
                    bars.append(bar)
            return bars
        raw_bar = payload.get("bar")
        if isinstance(raw_bar, dict):
            bar = self._redis_payload_bar_to_canonical(raw_bar, symbol, tf_s, complete, source)
            if bar is not None:
                bars.append(bar)
        return bars

    def _redis_payload_bar_to_canonical(
        self,
        bar: dict[str, Any],
        symbol: str,
        tf_s: int,
        complete: bool,
        source: str,
    ) -> Optional[dict[str, Any]]:
        open_ms = bar.get("open_ms")
        close_ms = bar.get("close_ms")
        if not isinstance(open_ms, int) or not isinstance(close_ms, int):
            return None
        return {
            "symbol": symbol,
            "tf_s": int(tf_s),
            "open_time_ms": int(open_ms),
            "close_time_ms": int(close_ms),
            "o": bar.get("o"),
            "h": bar.get("h"),
            "low": bar.get("l"),
            "c": bar.get("c"),
            "v": bar.get("v"),
            "complete": bool(complete),
            "src": str(source),
            "event_ts": int(close_ms) if complete else None,
        }

    def _bar_to_update_event(self, symbol: str, bar: dict[str, Any]) -> dict[str, Any]:
        complete = bool(bar.get("complete", True))
        return {
            "key": {
                "symbol": symbol,
                "tf_s": int(bar.get("tf_s", 0)),
                "open_ms": int(bar.get("open_time_ms", 0)),
            },
            "bar": bar,
            "complete": complete,
            "source": str(bar.get("src", "")),
            "event_ts": bar.get("event_ts") if complete else None,
        }

    def _digest_bar(self, bar: dict[str, Any]) -> str:
        payload = {
            "open_time_ms": bar.get("open_time_ms"),
            "close_time_ms": bar.get("close_time_ms"),
            "o": bar.get("open"),
            "h": bar.get("high"),
            "low": bar.get("low"),
            "c": bar.get("close"),
            "v": bar.get("volume"),
            "complete": bar.get("complete"),
            "src": bar.get("src"),
            "event_ts": bar.get("event_ts"),
            "last_price": bar.get("last_price"),
            "last_tick_ts": bar.get("last_tick_ts"),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return _sha256(raw)

    def _next_seq_for_event(self, key: tuple[str, int, int], digest: str) -> Optional[int]:
        with self._updates_lock:
            prev = self._updates_last_digest.get(key)
            if prev == digest:
                return None
            self._updates_last_digest[key] = digest
            self._updates_seq += 1
            return self._updates_seq


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_cfg(config_path: str) -> dict[str, Any]:
    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def _tf_allowlist_from_cfg(cfg: dict[str, Any]) -> set[int]:
    raw = cfg.get("tf_allowlist_s")
    out: list[int] = []
    if isinstance(raw, list):
        for item in raw:
            try:
                tf_s = int(item)
            except Exception:
                continue
            if tf_s > 0:
                out.append(tf_s)
    if out:
        return set(out)

    derived = cfg.get("derived_tfs_s")
    if isinstance(derived, list):
        for item in derived:
            try:
                tf_s = int(item)
            except Exception:
                continue
            if tf_s > 0:
                out.append(tf_s)

    broker_base = cfg.get("broker_base_tfs_s")
    if isinstance(broker_base, list):
        for item in broker_base:
            try:
                tf_s = int(item)
            except Exception:
                continue
            if tf_s > 0:
                out.append(tf_s)

    if 300 not in out:
        out.append(300)

    if out:
        return set(out)

    return set(DEFAULT_TF_ALLOWLIST)


def _min_coldload_bars_from_cfg(cfg: dict[str, Any]) -> dict[int, int]:
    raw = cfg.get("min_coldload_bars_by_tf_s")
    out: dict[int, int] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                tf_s = int(k)
                min_n = int(v)
            except Exception:
                continue
            if tf_s > 0 and min_n > 0:
                out[tf_s] = min_n
    if out:
        return out
    return {}


def _bar_is_complete(bar: dict[str, Any]) -> bool:
    val = bar.get("complete")
    return bool(val) if isinstance(val, bool) else bool(val)


def _bar_is_final_source(bar: dict[str, Any]) -> bool:
    src = bar.get("src")
    return isinstance(src, str) and src in FINAL_SOURCES


def _choose_better_bar(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    existing_complete = _bar_is_complete(existing)
    incoming_complete = _bar_is_complete(incoming)
    if incoming_complete and not existing_complete:
        return incoming
    if existing_complete and not incoming_complete:
        return existing
    existing_final = _bar_is_final_source(existing)
    incoming_final = _bar_is_final_source(incoming)
    if incoming_final and not existing_final:
        return incoming
    if existing_final and not incoming_final:
        return existing
    return existing


def _get_open_ms(bar: dict[str, Any]) -> Optional[int]:
    value = bar.get("open_time_ms")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except Exception:
            return None
    return None


def _ensure_sorted_dedup(
    bars: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
    if len(bars) <= 1:
        return bars, None

    prev_open: Optional[int] = None
    for bar in bars:
        open_ms = _get_open_ms(bar)
        if open_ms is None:
            continue
        if prev_open is not None and open_ms <= prev_open:
            break
        prev_open = open_ms
    else:
        return bars, None

    sorted_bars = sorted(bars, key=lambda x: _get_open_ms(x) or 0)
    deduped: dict[int, dict[str, Any]] = {}
    dropped = 0
    for bar in sorted_bars:
        open_ms = _get_open_ms(bar)
        if open_ms is None:
            continue
        existing = deduped.get(open_ms)
        if existing is None:
            deduped[open_ms] = bar
            continue
        deduped[open_ms] = _choose_better_bar(existing, bar)
        dropped += 1

    result = [deduped[k] for k in sorted(deduped.keys())]
    geom = {"sorted": True, "dedup_dropped": dropped}
    return result, geom


def _mark_geom_fix(
    meta: dict[str, Any],
    warnings: list[str],
    geom: dict[str, Any],
    *,
    source: str,
    tf_s: int,
) -> None:
    if "geom_non_monotonic" not in warnings:
        warnings.append("geom_non_monotonic")
    extensions = meta.get("extensions")
    if not isinstance(extensions, dict):
        extensions = {}
        meta["extensions"] = extensions
    extensions["geom_fix"] = geom
    extensions["degraded"] = ["geom_non_monotonic"]
    _log_geom_fix(source, tf_s, geom)


def _log_geom_fix(source: str, tf_s: int, geom: dict[str, Any]) -> None:
    Logging.warning(
        "UDS: виправлено геометрію source=%s tf_s=%s sorted=%s dedup_dropped=%s",
        source,
        tf_s,
        geom.get("sorted"),
        geom.get("dedup_dropped"),
    )
    _OBS.inc_uds_geom_fix(source, tf_s)


def _log_window_result(result: WindowResult, warnings: list[str], tf_s: int) -> None:
    if not Logging.isEnabledFor(logging.INFO):
        return
    meta = result.meta or {}
    source = meta.get("source") if isinstance(meta, dict) else None
    if isinstance(meta, dict) and isinstance(meta.get("redis_hit"), bool):
        _OBS.observe_redis_hit(tf_s, bool(meta.get("redis_hit")))
    count = len(result.bars_lwc)
    Logging.info(
        "UDS: вікно готове source=%s count=%s warnings=%s",
        source,
        count,
        "|".join(warnings) if warnings else "-",
    )


def _log_updates_result(result: UpdatesResult) -> None:
    if not Logging.isEnabledFor(logging.INFO):
        return
    Logging.info(
        "UDS: updates готові events=%s cursor_seq=%s warnings=%s",
        len(result.events),
        result.cursor_seq,
        "|".join(result.warnings) if result.warnings else "-",
    )


def _redis_layer_from_cfg(cfg: dict[str, Any]) -> Optional[RedisLayer]:
    raw = cfg.get("redis")
    if not isinstance(raw, dict):
        return None
    if not bool(raw.get("enabled", False)):
        return None
    if redis_lib is None:
        return None
    host = raw.get("host") or "127.0.0.1"
    port = int(raw.get("port", 6379))
    db = int(raw.get("db", 0))
    ns = str(raw.get("ns", "v3"))
    client = redis_lib.Redis(
        host=host,
        port=port,
        db=db,
        socket_timeout=REDIS_SOCKET_TIMEOUT_S,
        socket_connect_timeout=REDIS_SOCKET_TIMEOUT_S,
        decode_responses=False,
    )
    return RedisLayer(client, ns)


def build_uds_from_config(
    config_path: str,
    data_root: str,
    boot_id: str,
    *,
    role: str = "writer",
) -> UnifiedDataStore:
    cfg = _load_cfg(config_path)
    tf_allowlist = _tf_allowlist_from_cfg(cfg)
    min_coldload_bars = _min_coldload_bars_from_cfg(cfg)
    redis_layer = _redis_layer_from_cfg(cfg)
    return UnifiedDataStore(
        data_root=data_root,
        boot_id=boot_id,
        tf_allowlist=tf_allowlist,
        min_coldload_bars=min_coldload_bars,
        role=role,
        redis_layer=redis_layer,
    )
