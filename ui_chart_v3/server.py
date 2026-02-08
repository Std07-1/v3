#!/usr/bin/env python3
"""
Мінімальний read-only UI чарт для v3 (режим B): читає JSONL з диска і віддає OHLCV через HTTP.

Цілі:
- Без SQL, без метрик, без WebSocket.
- Same-origin: HTML/JS + API в одному процесі.
- Підтримка TF з директорій tf_{tf_s}/part-YYYYMMDD.jsonl.
- Інкрементальні оновлення через простий polling /api/updates.

Запуск:
  python server.py --data-root ./data_v3 --host 0.0.0.0 --port 8089

Використання:
  Відкрити у браузері: http://HOST:PORT/
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import logging
import os
import threading
import time
import urllib.parse
import uuid
from collections import deque
from typing import Any

from core.time_geom import normalize_bar
from env_profile import load_env_profile

try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore


_updates_lock = threading.Lock()
_updates_seq = 0
_updates_last_digest: dict[tuple[str, int, int], str] = {}
_boot_id = uuid.uuid4().hex

_cache_lock = threading.Lock()
_bars_cache: dict[tuple[str, int], list[dict[str, Any]]] = {}
MAX_CACHE_BARS = 5000

DEFAULT_TF_ALLOWLIST = {300, 900, 1800, 3600, 14400, 86400}
SOURCE_ALLOWLIST = {"history", "derived", "history_agg", ""}
FINAL_SOURCES = {"history", "derived", "history_agg"}
MAX_EVENTS_PER_RESPONSE = 500
REDIS_SOCKET_TIMEOUT_S = 0.4
DEFAULT_MIN_COLDLOAD_BARS = {300: 300, 900: 200, 1800: 150, 3600: 100}

_redis_log_throttle: dict[str, float] = {}
_redis_client_cache: dict[str, Any] = {}
_cfg_cache: dict[str, Any] = {"data": {}, "mtime": None, "next_check_ts": 0.0}
CFG_CACHE_CHECK_INTERVAL_S = 0.5


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
    return dict(DEFAULT_MIN_COLDLOAD_BARS)


def _cache_key(symbol: str, tf_s: int) -> tuple[str, int]:
    return (symbol, tf_s)


def _cache_get(symbol: str, tf_s: int) -> list[dict[str, Any]]:
    key = _cache_key(symbol, tf_s)
    with _cache_lock:
        return list(_bars_cache.get(key, []))


def _cache_seed(symbol: str, tf_s: int, bars: list[dict[str, Any]]) -> None:
    key = _cache_key(symbol, tf_s)
    tail = bars[-MAX_CACHE_BARS:] if len(bars) > MAX_CACHE_BARS else list(bars)
    with _cache_lock:
        _bars_cache[key] = tail


def _cache_upsert(symbol: str, tf_s: int, bar: dict[str, Any]) -> None:
    key = _cache_key(symbol, tf_s)
    open_ms = bar.get("open_time_ms")
    if not isinstance(open_ms, int):
        return
    incoming_complete = bool(bar.get("complete", False))
    incoming_src = bar.get("src")
    with _cache_lock:
        arr = _bars_cache.get(key)
        if arr is None:
            _bars_cache[key] = [bar]
            return
        replaced = False
        for i, existing in enumerate(arr):
            if existing.get("open_time_ms") == open_ms:
                existing_complete = bool(existing.get("complete", False))
                existing_src = existing.get("src")
                if existing_complete and not incoming_complete:
                    return
                if existing_complete and incoming_complete and existing_src and incoming_src and existing_src != incoming_src:
                    return
                arr[i] = bar
                replaced = True
                break
        if not replaced:
            arr.append(bar)
            if len(arr) >= 2 and arr[-2].get("open_time_ms") is not None:
                prev_ms = arr[-2].get("open_time_ms")
                if isinstance(prev_ms, int) and open_ms < prev_ms:
                    arr.sort(key=lambda x: x.get("open_time_ms", 0))
        if len(arr) > MAX_CACHE_BARS:
            del arr[: len(arr) - MAX_CACHE_BARS]


def _safe_int(v: str | None, default: int) -> int:
    try:
        return int(v) if v is not None else default
    except Exception:
        return default


def _parse_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "y", "on"):
            return True
        if v in ("0", "false", "no", "n", "off", ""):
            return False
    return default


def _log_throttled(level: str, key: str, message: str, every_s: int = 60) -> None:
    now = time.time()
    last = _redis_log_throttle.get(key, 0.0)
    if now - last < every_s:
        return
    _redis_log_throttle[key] = now
    if level == "warning":
        logging.warning("%s", message)
    else:
        logging.info("%s", message)


def _redis_config_from_cfg(cfg: dict[str, Any]) -> dict[str, Any] | None:
    raw = cfg.get("redis")
    if not isinstance(raw, dict):
        return None
    if not bool(raw.get("enabled", False)):
        return None
    env_host = (os.environ.get("FXCM_REDIS_HOST") or "").strip() or None
    env_port = (os.environ.get("FXCM_REDIS_PORT") or "").strip() or None
    env_db = (os.environ.get("FXCM_REDIS_DB") or "").strip() or None
    env_ns = (os.environ.get("FXCM_REDIS_NS") or "").strip() or None

    merged = dict(raw)
    if env_host:
        merged["host"] = env_host
    if env_port:
        try:
            merged["port"] = int(env_port)
        except Exception:
            pass
    if env_db:
        try:
            merged["db"] = int(env_db)
        except Exception:
            pass
    if env_ns:
        merged["ns"] = env_ns
    return merged


def _redis_client_from_cfg(cfg: dict[str, Any]) -> tuple[Any, str] | None:
    raw = _redis_config_from_cfg(cfg)
    if raw is None:
        return None
    if redis_lib is None:
        return None
    host = str(raw.get("host", "127.0.0.1"))
    port = int(raw.get("port", 6379))
    db = int(raw.get("db", 0))
    ns = str(raw.get("ns", "v3"))
    cache_key = f"{host}:{port}:{db}:{ns}"
    cached = _redis_client_cache.get(cache_key)
    if cached is not None:
        return cached, ns
    client = redis_lib.Redis(
        host=host,
        port=port,
        db=db,
        decode_responses=True,
        socket_timeout=REDIS_SOCKET_TIMEOUT_S,
        socket_connect_timeout=REDIS_SOCKET_TIMEOUT_S,
    )
    _redis_client_cache[cache_key] = client
    return client, ns


def _redis_key(ns: str, *parts: str) -> str:
    return ":".join([ns, *parts])


def _redis_get_json(client: Any, key: str) -> tuple[dict[str, Any] | None, int | None, str | None]:
    try:
        raw = client.get(key)
    except Exception as exc:
        return None, None, f"redis_get_failed:{type(exc).__name__}"
    if raw is None:
        return None, None, "redis_miss"
    try:
        payload = json.loads(raw)
    except Exception:
        return None, None, "redis_json_invalid"
    ttl_left: int | None = None
    try:
        ttl = client.ttl(key)
        if isinstance(ttl, int) and ttl >= 0:
            ttl_left = ttl
    except Exception:
        ttl_left = None
    return payload, ttl_left, None


def _load_cfg_cached(config_path: str | None) -> dict[str, Any]:
    if not config_path:
        return {}
    now = time.time()
    if now < float(_cfg_cache.get("next_check_ts", 0.0)):
        cached = _cfg_cache.get("data")
        if isinstance(cached, dict):
            return cached
        return {}
    _cfg_cache["next_check_ts"] = now + CFG_CACHE_CHECK_INTERVAL_S
    try:
        if not os.path.isfile(config_path):
            _cfg_cache["data"] = {}
            _cfg_cache["mtime"] = None
            return {}
        mtime = os.path.getmtime(config_path)
        if _cfg_cache.get("mtime") == mtime:
            cached = _cfg_cache.get("data")
            if isinstance(cached, dict):
                return cached
            return {}
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
        _cfg_cache["data"] = data
        _cfg_cache["mtime"] = mtime
        return data
    except Exception:
        return {}


def _redis_payload_to_bars(
    payload: dict[str, Any],
    symbol: str,
    tf_s: int,
) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    complete = bool(payload.get("complete", True))
    source = str(payload.get("source", ""))
    raw_bars = payload.get("bars")
    if isinstance(raw_bars, list):
        for item in raw_bars:
            if not isinstance(item, dict):
                continue
            bar = _redis_payload_bar_to_canonical(item, symbol, tf_s, complete, source)
            if bar is not None:
                bars.append(bar)
        return bars
    raw_bar = payload.get("bar")
    if isinstance(raw_bar, dict):
        bar = _redis_payload_bar_to_canonical(raw_bar, symbol, tf_s, complete, source)
        if bar is not None:
            bars.append(bar)
    return bars


def _redis_payload_bar_to_canonical(
    bar: dict[str, Any],
    symbol: str,
    tf_s: int,
    complete: bool,
    source: str,
) -> dict[str, Any] | None:
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


def _resolve_cfg_path(path: str | None, config_path: str | None) -> str | None:
    if not path or not isinstance(path, str):
        return None
    if os.path.isabs(path):
        return path
    if config_path:
        base = os.path.dirname(os.path.abspath(config_path))
        return os.path.join(base, path)
    return os.path.abspath(path)


def _resolve_profile_config_path(base_dir: str) -> str | None:
    env_path = (os.environ.get("AI_ONE_CONFIG_PATH") or "").strip()
    if not env_path:
        return None
    if os.path.isabs(env_path):
        return os.path.abspath(env_path)
    return os.path.abspath(os.path.join(base_dir, "..", env_path))


def _sym_dir(symbol: str) -> str:
    return symbol.replace("/", "_")


def _list_symbols(data_root: str) -> list[str]:
    if not os.path.isdir(data_root):
        return []
    out: list[str] = []
    for name in sorted(os.listdir(data_root)):
        p = os.path.join(data_root, name)
        if os.path.isdir(p):
            # Повертаємо у форматі з "/": найпростіша евристика
            # (для XAU_USD -> XAU/USD). Якщо ви маєте інші символи, адаптуйте тут.
            sym = name.replace("_", "/")
            out.append(sym)
    return out


def _list_parts(data_root: str, symbol: str, tf_s: int) -> list[str]:
    d = os.path.join(data_root, _sym_dir(symbol), f"tf_{tf_s}")
    if not os.path.isdir(d):
        return []
    parts = [
        os.path.join(d, x)
        for x in os.listdir(d)
        if x.startswith("part-") and x.endswith(".jsonl")
    ]
    parts.sort()
    return parts


def _read_jsonl_filtered(
    paths: list[str],
    since_open_ms: int | None,
    to_open_ms: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Читає JSONL у хронологічному порядку, повертає останні limit елементів після фільтрації."""
    buf: deque[dict[str, Any]] = deque(maxlen=max(1, limit))
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue

                    open_ms = obj.get("open_time_ms")
                    if not isinstance(open_ms, int):
                        continue

                    if since_open_ms is not None and open_ms <= since_open_ms:
                        continue
                    if to_open_ms is not None and open_ms > to_open_ms:
                        continue

                    buf.append(obj)
        except FileNotFoundError:
            continue

    out = list(buf)
    out.sort(key=lambda x: x.get("open_time_ms", 0))
    return out


def _iter_lines_reverse(path: str):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            buf = b""
            chunk = 8192
            while pos > 0:
                step = min(chunk, pos)
                pos -= step
                f.seek(pos)
                buf = f.read(step) + buf
                while b"\n" in buf:
                    idx = buf.rfind(b"\n")
                    line = buf[idx + 1 :]
                    buf = buf[:idx]
                    yield line
            if buf:
                yield buf
    except Exception:
        return


def _read_jsonl_tail_filtered(
    paths: list[str],
    since_open_ms: int | None,
    to_open_ms: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    out: list[dict[str, Any]] = []
    for p in reversed(paths):
        for raw in _iter_lines_reverse(p):
            if len(out) >= limit:
                break
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            open_ms = obj.get("open_time_ms")
            if not isinstance(open_ms, int):
                continue
            if to_open_ms is not None and open_ms > to_open_ms:
                continue
            if since_open_ms is not None and open_ms <= since_open_ms:
                out.reverse()
                return out
            out.append(obj)
        if len(out) >= limit:
            break
    out.reverse()
    return out


def _read_last_jsonl(path: str) -> dict[str, Any] | None:
    last_obj: dict[str, Any] | None = None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                open_ms = obj.get("open_time_ms")
                if not isinstance(open_ms, int):
                    continue
                last_obj = obj
    except FileNotFoundError:
        return None
    return last_obj


def _bars_to_lwc(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Перетворює канонічний формат у формат Lightweight Charts."""
    out: list[dict[str, Any]] = []
    for raw in bars:
        b = normalize_bar(raw, mode="incl")
        t = b["open_time_ms"] // 1000
        low_val = b.get("low", b.get("l"))
        complete = bool(b.get("complete", True))
        item = {
            "time": t,
            "open": float(b["o"]),
            "high": float(b["h"]),
            "low": float(low_val),
            "close": float(b["c"]),
            "volume": float(b.get("v", 0.0)),
            "open_time_ms": int(b["open_time_ms"]),
            "close_time_ms": int(b.get("close_time_ms", b["open_time_ms"]))
            if "close_time_ms" in b
            else None,
            "tf_s": int(b["tf_s"]),
            "src": str(b.get("src", "")),
            "complete": complete,
        }
        if complete and "close_time_ms" in b:
            item["event_ts"] = int(b["close_time_ms"])
        if "last_price" in b:
            try:
                item["last_price"] = float(b["last_price"])
            except Exception:
                pass
        if "last_tick_ts" in b:
            try:
                item["last_tick_ts"] = int(b["last_tick_ts"])
            except Exception:
                pass
        out.append(item)
    return out


def _bar_to_update_event(symbol: str, bar: dict[str, Any]) -> dict[str, Any]:
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


def _digest_bar(bar: dict[str, Any]) -> str:
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
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _next_seq_for_event(key: tuple[str, int, int], digest: str) -> int | None:
    global _updates_seq
    with _updates_lock:
        prev = _updates_last_digest.get(key)
        if prev == digest:
            return None
        _updates_last_digest[key] = digest
        _updates_seq += 1
        return _updates_seq


def _current_seq() -> int:
    with _updates_lock:
        return _updates_seq


def _disk_last_open_ms(data_root: str, symbol: str, tf_s: int) -> int | None:
    parts = _list_parts(data_root, symbol, tf_s)
    if not parts:
        return None
    last_obj = _read_last_jsonl(parts[-1])
    if not last_obj:
        return None
    open_ms = last_obj.get("open_time_ms")
    return int(open_ms) if isinstance(open_ms, int) else None


def _disk_last_mtime_ms(data_root: str, symbol: str, tf_s: int) -> int | None:
    parts = _list_parts(data_root, symbol, tf_s)
    if not parts:
        return None
    try:
        ts = os.path.getmtime(parts[-1])
    except Exception:
        return None
    return int(ts * 1000)


def _validate_event(ev: dict[str, Any], tf_allowlist: set[int]) -> list[str]:
    warnings: list[str] = []
    key = ev.get("key") or {}
    bar = ev.get("bar") or {}
    symbol = key.get("symbol") or bar.get("symbol")
    if not symbol:
        warnings.append("missing_symbol")
    tf_s = key.get("tf_s") if key.get("tf_s") is not None else bar.get("tf_s")
    if not isinstance(tf_s, int) or tf_s not in tf_allowlist:
        warnings.append("tf_not_allowed")
    open_ms = key.get("open_ms") if key.get("open_ms") is not None else bar.get("open_time_ms")
    close_ms = bar.get("close_time_ms")
    if not isinstance(open_ms, int) or not isinstance(close_ms, int):
        warnings.append("time_not_int")
    if isinstance(open_ms, int) and isinstance(tf_s, int) and isinstance(close_ms, int):
        if close_ms != open_ms + tf_s * 1000 - 1:
            warnings.append("close_time_invalid")
    src = ev.get("source") if ev.get("source") is not None else bar.get("src")
    if not isinstance(src, str) or src not in SOURCE_ALLOWLIST:
        warnings.append("source_not_allowed")
    complete = ev.get("complete") if ev.get("complete") is not None else bar.get("complete")
    if not isinstance(complete, bool):
        warnings.append("complete_not_bool")
    if complete is True:
        event_ts = ev.get("event_ts") if ev.get("event_ts") is not None else bar.get("event_ts")
        if not isinstance(event_ts, int) or event_ts != close_ms:
            warnings.append("event_ts_invalid")
        if isinstance(src, str) and src not in FINAL_SOURCES:
            warnings.append("final_source_not_allowed")
    return warnings


def _validate_bar_lwc(bar: dict[str, Any], tf_allowlist: set[int]) -> list[str]:
    warnings: list[str] = []
    tf_s = bar.get("tf_s")
    if not isinstance(tf_s, int) or tf_s not in tf_allowlist:
        warnings.append("tf_not_allowed")
    open_ms = bar.get("open_time_ms")
    close_ms = bar.get("close_time_ms")
    if not isinstance(open_ms, int) or not isinstance(close_ms, int):
        warnings.append("time_not_int")
    if isinstance(open_ms, int) and isinstance(tf_s, int) and isinstance(close_ms, int):
        if close_ms != open_ms + tf_s * 1000 - 1:
            warnings.append("close_time_invalid")
    src = bar.get("src")
    if not isinstance(src, str) or src not in SOURCE_ALLOWLIST:
        warnings.append("source_not_allowed")
    complete = bar.get("complete")
    if not isinstance(complete, bool):
        warnings.append("complete_not_bool")
    if complete is True:
        event_ts = bar.get("event_ts")
        if not isinstance(event_ts, int) or event_ts != close_ms:
            warnings.append("event_ts_invalid")
        if isinstance(src, str) and src not in FINAL_SOURCES:
            warnings.append("final_source_not_allowed")
    return warnings


class Handler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler: / (статичні файли), /api/* (JSON)."""

    server_version = "AiOne_v3_UI/0.1"
    _client_id: str | None = None
    _client_new: bool = False

    def _get_or_create_client_id(self) -> str:
        cookie_header = self.headers.get("Cookie", "")
        client_id = ""
        if cookie_header:
            parts = [p.strip() for p in cookie_header.split(";") if p.strip()]
            for part in parts:
                if part.startswith("aione_client_id="):
                    client_id = part.split("=", 1)[1].strip()
                    break
        if client_id:
            self._client_new = False
            return client_id
        self._client_new = True
        return uuid.uuid4().hex

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        self._client_id = self._get_or_create_client_id()

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        if path.startswith("/api/"):
            self._handle_api(parsed)
            return

        if path in ("/", "/index.html"):
            try:
                ui_debug = True
                config_path: str | None = getattr(self.server, "config_path", None)  # type: ignore[attr-defined]
                if config_path:
                    cfg = _load_cfg_cached(config_path)
                    ui_debug = _parse_bool(cfg.get("ui_debug", True), True)
                host, port = self.client_address
                user_agent = self.headers.get("User-Agent", "")
                referer = self.headers.get("Referer", "")
                accept_lang = self.headers.get("Accept-Language", "")
                forwarded_for = self.headers.get("X-Forwarded-For", "")
                if ui_debug:
                    logging.debug(
                        "UI клієнт: id=%s new=%s addr=%s:%s xff=%s | UA=%s | Ref=%s | Lang=%s",
                        self._client_id,
                        self._client_new,
                        host,
                        port,
                        forwarded_for,
                        user_agent,
                        referer,
                        accept_lang,
                    )
            except Exception:
                logging.debug("UI клієнт підключився")

        # Статичні файли
        super().do_GET()

    def _json(self, code: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except ConnectionAbortedError:
            logging.debug("UI: клієнт розірвав з'єднання під час відповіді")

    def _bad(self, msg: str) -> None:
        logging.error("UI API помилка: %s", msg)
        self._json(400, {"ok": False, "error": msg})

    def _handle_api(self, parsed: urllib.parse.ParseResult) -> None:
        qs = urllib.parse.parse_qs(parsed.query)
        data_root: str = self.server.data_root  # type: ignore[attr-defined]
        config_path: str | None = getattr(self.server, "config_path", None)  # type: ignore[attr-defined]
        path = parsed.path.rstrip("/") or "/"

        cfg: dict[str, Any] = {}
        if config_path:
            cfg = _load_cfg_cached(config_path)

        tf_allowlist = _tf_allowlist_from_cfg(cfg)
        min_coldload_bars = _min_coldload_bars_from_cfg(cfg)

        if path == "/api/config":
            ui_debug = _parse_bool(cfg.get("ui_debug", True), True)
            self._json(
                200,
                {
                    "ok": True,
                    "ui_debug": ui_debug,
                },
            )
            return

        if path == "/api/updates":
            symbol = (qs.get("symbol", [""])[0] or "").strip()
            tf_s = _safe_int((qs.get("tf_s", ["300"])[0] or "300"), 300)
            limit = _safe_int((qs.get("limit", ["500"])[0] or "500"), 500)
            since_seq_raw = qs.get("since_seq", [None])[0]
            since_seq = _safe_int(since_seq_raw, 0) if since_seq_raw is not None else None
            if not symbol:
                self._bad("missing_symbol")
                return
            if tf_s not in tf_allowlist:
                self._bad("tf_not_allowed")
                return

            events: list[dict[str, Any]] = []
            parts = _list_parts(data_root, symbol, tf_s)
            if parts:
                bars = _read_jsonl_tail_filtered(parts, None, None, limit)
                lwc = _bars_to_lwc(bars)
                for b in lwc:
                    key = (symbol, int(b.get("tf_s", 0)), int(b.get("open_time_ms", 0)))
                    digest = _digest_bar(b)
                    seq = _next_seq_for_event(key, digest)
                    if seq is None:
                        continue
                    ev = _bar_to_update_event(symbol, b)
                    ev["seq"] = seq
                    events.append(ev)

            if since_seq is not None:
                events = [ev for ev in events if ev.get("seq") and ev["seq"] > since_seq]

            warnings: list[str] = []
            filtered_events: list[dict[str, Any]] = []
            for ev in events:
                issues = _validate_event(ev, tf_allowlist)
                if issues:
                    warnings.extend(issues)
                    continue
                filtered_events.append(ev)
            if len(filtered_events) > MAX_EVENTS_PER_RESPONSE:
                filtered_events = filtered_events[-MAX_EVENTS_PER_RESPONSE:]
                warnings.append("max_events_trimmed")

            events = filtered_events
            for ev in events:
                if ev.get("complete") is True and ev.get("source") in FINAL_SOURCES:
                    bar = ev.get("bar")
                    if isinstance(bar, dict):
                        _cache_upsert(symbol, tf_s, bar)

            cursor_seq = None
            for ev in events:
                seq = ev.get("seq")
                if isinstance(seq, int) and (cursor_seq is None or seq > cursor_seq):
                    cursor_seq = seq
            payload: dict[str, Any] = {
                "ok": True,
                "symbol": symbol,
                "tf_s": tf_s,
                "events": events,
                "cursor_seq": cursor_seq,
                "boot_id": _boot_id,
            }
            if warnings:
                payload["warnings"] = warnings
            payload["cursor_seq"] = _current_seq()
            disk_last_open_ms = _disk_last_open_ms(data_root, symbol, tf_s)
            payload["disk_last_open_ms"] = disk_last_open_ms
            if disk_last_open_ms is not None:
                payload["bar_close_ms"] = disk_last_open_ms + tf_s * 1000 - 1
            payload["ssot_write_ts_ms"] = _disk_last_mtime_ms(data_root, symbol, tf_s)
            payload["api_seen_ts_ms"] = int(time.time() * 1000)
            self._json(200, payload)
            return

        if path == "/api/symbols":
            self._json(200, {"ok": True, "symbols": _list_symbols(data_root)})
            return

        if path in ("/api/bars", "/api/latest"):
            symbol = (qs.get("symbol", [""])[0] or "").strip()
            tf_s = _safe_int((qs.get("tf_s", ["60"])[0] or "60"), 60)
            limit = _safe_int((qs.get("limit", ["2000"])[0] or "2000"), 2000)
            force_disk = _safe_int((qs.get("force_disk", ["0"])[0] or "0"), 0) == 1
            prefer_redis = _safe_int((qs.get("prefer_redis", ["0"])[0] or "0"), 0) == 1

            if not symbol:
                self._bad("missing_symbol")
                return
            if tf_s not in tf_allowlist:
                self._bad("tf_not_allowed")
                return

            parts = _list_parts(data_root, symbol, tf_s)
            if not parts:
                self._json(
                    200,
                    {
                        "ok": True,
                        "bars": [],
                        "note": "no_data",
                        "boot_id": _boot_id,
                        "meta": {"source": "disk", "redis_hit": False, "boot_id": _boot_id},
                    },
                )
                return

            since_open_ms = None
            to_open_ms = None
            if path == "/api/latest":
                a = qs.get("after_open_ms", [None])[0]
                since_open_ms = _safe_int(a, 0) if a is not None else None
            else:
                s = qs.get("since_open_ms", [None])[0]
                t = qs.get("to_open_ms", [None])[0]
                since_open_ms = _safe_int(s, 0) if s is not None else None
                to_open_ms = _safe_int(t, 0) if t is not None else None

            meta: dict[str, Any] = {"source": "disk", "redis_hit": False, "boot_id": _boot_id}
            warnings: list[str] = []
            filtered: list[dict[str, Any]] = []
            cold_load = since_open_ms is None and to_open_ms is None and not force_disk

            if path == "/api/bars" and prefer_redis and cold_load:
                redis_error_code: str | None = None
                redis_client = _redis_client_from_cfg(cfg)
                if redis_client is None:
                    redis_error_code = "redis_disabled"
                else:
                    client, ns = redis_client
                    tail_key = _redis_key(ns, "ohlcv", "tail", symbol, str(tf_s))
                    snap_key = _redis_key(ns, "ohlcv", "snap", symbol, str(tf_s))
                    payload, ttl_left, err = _redis_get_json(client, tail_key)
                    if err == "redis_miss":
                        payload, ttl_left, err = _redis_get_json(client, snap_key)
                    if err is not None:
                        redis_error_code = err
                    elif payload is None:
                        redis_error_code = "redis_empty"
                    elif ttl_left is None or ttl_left <= 0:
                        redis_error_code = "redis_ttl_invalid"
                    else:
                        bars = _redis_payload_to_bars(payload, symbol, tf_s)
                        if not bars:
                            redis_error_code = "redis_empty"
                        else:
                            lwc = _bars_to_lwc(bars)
                            for b in lwc:
                                issues = _validate_bar_lwc(b, tf_allowlist)
                                if issues:
                                    warnings.extend(issues)
                                    continue
                                filtered.append(b)
                            if limit > 0:
                                filtered = filtered[-limit:]
                            min_required = int(min_coldload_bars.get(tf_s, 0))
                            if min_required > 0 and len(filtered) < min_required:
                                redis_error_code = "redis_tail_small"
                                meta = {
                                    "source": "disk_fallback_small_tail",
                                    "redis_hit": True,
                                    "redis_len": len(filtered),
                                    "redis_ttl_s_left": ttl_left,
                                    "redis_payload_ts_ms": payload.get("payload_ts_ms"),
                                    "redis_seq": payload.get("last_seq")
                                    if isinstance(payload.get("last_seq"), int)
                                    else payload.get("seq"),
                                    "boot_id": _boot_id,
                                }
                                log_key = f"redis_small_tail:{symbol}:{tf_s}"
                                _log_throttled(
                                    "warning",
                                    log_key,
                                    (
                                        "UI_BARS_REDIS_SMALL_TAIL_FALLBACK symbol=%s tf=%s len=%s min=%s"
                                        % (symbol, tf_s, len(filtered), min_required)
                                    ),
                                )
                                filtered = []
                            else:
                                meta = {
                                    "source": "redis",
                                    "redis_hit": True,
                                    "redis_ttl_s_left": ttl_left,
                                    "redis_payload_ts_ms": payload.get("payload_ts_ms"),
                                    "redis_seq": payload.get("last_seq")
                                    if isinstance(payload.get("last_seq"), int)
                                    else payload.get("seq"),
                                    "boot_id": _boot_id,
                                }
                                _cache_seed(symbol, tf_s, filtered)
                                log_key = f"redis_hit:{symbol}:{tf_s}"
                                _log_throttled(
                                    "info",
                                    log_key,
                                    (
                                        "UI_BARS_REDIS_HIT symbol=%s tf=%s ttl_left_s=%s seq=%s"
                                        % (symbol, tf_s, ttl_left, meta.get("redis_seq"))
                                    ),
                                )

                if filtered:
                    payload = {
                        "ok": True,
                        "symbol": symbol,
                        "tf_s": tf_s,
                        "bars": filtered,
                        "boot_id": _boot_id,
                        "meta": meta,
                    }
                    if warnings:
                        payload["warnings"] = warnings
                    self._json(200, payload)
                    return
                if redis_error_code:
                    meta = {
                        "source": "disk",
                        "redis_hit": False,
                        "redis_error_code": redis_error_code,
                        "boot_id": _boot_id,
                    }
                    log_key = f"redis_fallback:{symbol}:{tf_s}"
                    if redis_error_code == "redis_miss":
                        _log_throttled(
                            "warning",
                            log_key,
                            (
                                "UI_BARS_REDIS_MISS_FALLBACK_DISK symbol=%s tf=%s"
                                % (symbol, tf_s)
                            ),
                        )
                    elif redis_error_code == "redis_tail_small":
                        _log_throttled(
                            "warning",
                            log_key,
                            (
                                "UI_BARS_REDIS_SMALL_TAIL_FALLBACK symbol=%s tf=%s"
                                % (symbol, tf_s)
                            ),
                        )
                    else:
                        _log_throttled(
                            "warning",
                            log_key,
                            (
                                "UI_BARS_REDIS_READ_FAILED symbol=%s tf=%s code=%s fallback=disk"
                                % (symbol, tf_s, redis_error_code)
                            ),
                        )

            use_tail = (
                path == "/api/latest"
                or cold_load
                or since_open_ms is not None
                or (to_open_ms is not None and force_disk)
            )
            if use_tail:
                bars = _read_jsonl_tail_filtered(parts, since_open_ms, to_open_ms, limit)
            else:
                bars = _read_jsonl_filtered(parts, since_open_ms, to_open_ms, limit)
            lwc = _bars_to_lwc(bars)
            for b in lwc:
                issues = _validate_bar_lwc(b, tf_allowlist)
                if issues:
                    warnings.extend(issues)
                    continue
                filtered.append(b)
            if cold_load and not force_disk:
                cached = _cache_get(symbol, tf_s)
                if cached:
                    filtered = cached[-limit:] if limit > 0 else cached
                else:
                    _cache_seed(symbol, tf_s, filtered)
            payload = {
                "ok": True,
                "symbol": symbol,
                "tf_s": tf_s,
                "bars": filtered,
                "boot_id": _boot_id,
                "meta": meta,
            }
            if warnings:
                payload["warnings"] = warnings
            self._json(200, payload)
            return

        self._bad("unknown_endpoint")

    def end_headers(self) -> None:
        path = self.path or ""
        if not path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
            if self._client_id and self._client_new:
                self.send_header(
                    "Set-Cookie",
                    f"aione_client_id={self._client_id}; Path=/; HttpOnly; SameSite=Lax",
                )
        super().end_headers()

    # Вимикаємо логування кожного статичного файлу (за замовчуванням надто шумно)
    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> int:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    report = load_env_profile()
    if report.dispatcher_loaded or report.profile_loaded:
        logging.info("ENV: dispatcher=%s profile=%s", report.dispatcher_path, report.profile_path)
    else:
        logging.info("ENV: профіль не завантажено")
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--data-root",
        default=None,
        help="Корінь data_v3 (де лежать SYMBOL/tf_*/part-*.jsonl)",
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8089)
    ap.add_argument(
        "--static-root",
        default="static",
        help="Каталог зі статикою (index.html, app.js)",
    )
    ap.add_argument(
        "--config",
        default=None,
        help="Шлях до config.json (для ui_debug)",
    )
    args = ap.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    static_root_value = args.static_root
    if static_root_value == "static":
        static_root_value = os.path.join(base_dir, "static")
    static_root = os.path.abspath(static_root_value)
    env_config = _resolve_profile_config_path(base_dir)
    default_config = env_config or os.path.abspath(os.path.join(base_dir, "..", "config.json"))
    config_path = os.path.abspath(args.config) if args.config else default_config
    data_root_value = args.data_root
    if not data_root_value:
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            data_root_value = cfg.get("data_root")
        except Exception:
            data_root_value = None
    if not data_root_value:
        logging.error("data_root не задано (ні --data-root, ні data_root у config.json)")
        return 2
    data_root = os.path.abspath(data_root_value)
    os.chdir(static_root)

    httpd = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.data_root = data_root  # type: ignore[attr-defined]
    httpd.config_path = config_path  # type: ignore[attr-defined]

    logging.info("UI: http://%s:%s/", args.host, args.port)
    logging.info("DATA_ROOT: %s", httpd.data_root)  # type: ignore[attr-defined]
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
