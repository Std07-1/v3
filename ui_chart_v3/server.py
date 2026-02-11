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
from core.buckets import bucket_start_ms, resolve_anchor_offset_ms
from env_profile import load_env_profile
from runtime.store.uds import ReadPolicy, UpdatesSpec, WindowSpec, build_uds_from_config


_updates_lock = threading.Lock()
_updates_seq = 0
_updates_last_digest: dict[tuple[str, int, int], str] = {}
_boot_id = uuid.uuid4().hex

_cache_lock = threading.Lock()
_bars_cache: dict[tuple[str, int], list[dict[str, Any]]] = {}
MAX_CACHE_BARS = 5000

DEFAULT_TF_ALLOWLIST = {300, 900, 1800, 3600, 14400, 86400}
DEFAULT_PREVIEW_TF_ALLOWLIST = {60, 180}
SOURCE_ALLOWLIST = {"history", "derived", "history_agg", ""}
FINAL_SOURCES = {"history", "derived", "history_agg"}
MAX_EVENTS_PER_RESPONSE = 500
DEFAULT_MIN_COLDLOAD_BARS = {300: 300, 900: 200, 1800: 150, 3600: 100}
_cfg_cache: dict[str, Any] = {"data": {}, "mtime": None, "next_check_ts": 0.0}
CFG_CACHE_CHECK_INTERVAL_S = 0.5
OVERLAY_ANCHOR_WARN_INTERVAL_S = 60
_overlay_anchor_warn_state: dict[tuple[str, int], float] = {}
OVERLAY_OBS_LOG_INTERVAL_S = 60
_overlay_obs_log_ts = 0.0
_overlay_req_total = 0
_overlay_prev_held_total = 0
_overlay_prev_wait_ms_last: int | None = None
_overlay_prev_hold_since: dict[tuple[str, int], int] = {}


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


def _preview_tf_allowlist_from_cfg(cfg: dict[str, Any]) -> set[int]:
    raw = cfg.get("tf_preview_allowlist_s")
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
    return set(DEFAULT_PREVIEW_TF_ALLOWLIST)


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


def _safe_int(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except Exception:
        return int(default)


def _parse_bool(raw: Any, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        return bool(raw)
    if isinstance(raw, str):
        val = raw.strip().lower()
        if val in ("1", "true", "yes", "y", "on"):
            return True
        if val in ("0", "false", "no", "n", "off"):
            return False
    return bool(default)


def _cache_key(symbol: str, tf_s: int) -> tuple[str, int]:
    return (symbol, tf_s)


def _cache_get(symbol: str, tf_s: int) -> list[dict[str, Any]]:
    key = _cache_key(symbol, tf_s)
    with _cache_lock:
        return list(_bars_cache.get(key, []))


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


def _overlay_anchor_warn_allowed(symbol: str, tf_s: int) -> bool:
    now = time.time()
    key = (symbol, tf_s)
    last = _overlay_anchor_warn_state.get(key, 0.0)
    if now - last < OVERLAY_ANCHOR_WARN_INTERVAL_S:
        return False
    _overlay_anchor_warn_state[key] = now
    return True


def _overlay_obs_log_allowed() -> bool:
    now = time.time()
    global _overlay_obs_log_ts
    if now - _overlay_obs_log_ts < OVERLAY_OBS_LOG_INTERVAL_S:
        return False
    _overlay_obs_log_ts = now
    return True


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


def _sample_items(items: list[Any], full_limit: int = 2000, head: int = 50, tail: int = 50) -> list[Any]:
    if len(items) <= full_limit:
        return items
    if head + tail >= len(items):
        return items
    return list(items[:head]) + list(items[-tail:])


def _is_int(value: Any) -> bool:
    return isinstance(value, int)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _guard_bar_shape(bar: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    required = {
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "open_time_ms",
        "close_time_ms",
        "tf_s",
        "src",
        "complete",
    }
    optional = {"event_ts", "last_price", "last_tick_ts"}
    allowed = required | optional
    missing = [key for key in required if key not in bar]
    if missing:
        issues.append(f"bar_missing:{','.join(sorted(missing))}")
    extra = [key for key in bar.keys() if key not in allowed]
    if extra:
        issues.append(f"bar_extra:{','.join(sorted(extra))}")
    if "time" in bar and not _is_int(bar.get("time")):
        issues.append("bar_time_not_int")
    for key in ("open", "high", "low", "close", "volume"):
        if key in bar and not _is_number(bar.get(key)):
            issues.append(f"bar_{key}_not_number")
    if "open_time_ms" in bar and not _is_int(bar.get("open_time_ms")):
        issues.append("bar_open_time_ms_not_int")
    close_ms = bar.get("close_time_ms")
    if close_ms is not None and not _is_int(close_ms):
        issues.append("bar_close_time_ms_not_int")
    if "tf_s" in bar and not _is_int(bar.get("tf_s")):
        issues.append("bar_tf_s_not_int")
    if "src" in bar and not isinstance(bar.get("src"), str):
        issues.append("bar_src_not_str")
    if "complete" in bar and not isinstance(bar.get("complete"), bool):
        issues.append("bar_complete_not_bool")
    if "event_ts" in bar and not _is_int(bar.get("event_ts")):
        issues.append("bar_event_ts_not_int")
    if "last_price" in bar and not _is_number(bar.get("last_price")):
        issues.append("bar_last_price_not_number")
    if "last_tick_ts" in bar and not _is_int(bar.get("last_tick_ts")):
        issues.append("bar_last_tick_ts_not_int")
    return issues


def _guard_event_shape(ev: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    required = {"key", "bar", "complete", "source"}
    optional = {"event_ts", "seq"}
    allowed = required | optional
    missing = [key for key in required if key not in ev]
    if missing:
        issues.append(f"event_missing:{','.join(sorted(missing))}")
    extra = [key for key in ev.keys() if key not in allowed]
    if extra:
        issues.append(f"event_extra:{','.join(sorted(extra))}")
    key = ev.get("key")
    if not isinstance(key, dict):
        issues.append("event_key_not_object")
    else:
        key_required = {"symbol", "tf_s", "open_ms"}
        key_missing = [k for k in key_required if k not in key]
        if key_missing:
            issues.append(f"event_key_missing:{','.join(sorted(key_missing))}")
        if "symbol" in key and not isinstance(key.get("symbol"), str):
            issues.append("event_key_symbol_not_str")
        if "tf_s" in key and not _is_int(key.get("tf_s")):
            issues.append("event_key_tf_s_not_int")
        if "open_ms" in key and not _is_int(key.get("open_ms")):
            issues.append("event_key_open_ms_not_int")
        key_extra = [k for k in key.keys() if k not in key_required]
        if key_extra:
            issues.append(f"event_key_extra:{','.join(sorted(key_extra))}")
    if "bar" in ev and not isinstance(ev.get("bar"), dict):
        issues.append("event_bar_not_object")
    if "complete" in ev and not isinstance(ev.get("complete"), bool):
        issues.append("event_complete_not_bool")
    if "source" in ev and not isinstance(ev.get("source"), str):
        issues.append("event_source_not_str")
    if "event_ts" in ev and ev.get("event_ts") is not None and not _is_int(ev.get("event_ts")):
        issues.append("event_event_ts_not_int")
    if "seq" in ev and not _is_int(ev.get("seq")):
        issues.append("event_seq_not_int")
    return issues


def _guard_meta_shape(meta: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    required = {"source", "redis_hit", "boot_id"}
    optional = {
        "redis_error_code",
        "redis_ttl_s_left",
        "redis_payload_ts_ms",
        "redis_seq",
        "redis_len",
        "extensions",
    }
    allowed = required | optional
    missing = [key for key in required if key not in meta]
    if missing:
        issues.append(f"meta_missing:{','.join(sorted(missing))}")
    extra = [key for key in meta.keys() if key not in allowed]
    if extra:
        issues.append(f"meta_extra:{','.join(sorted(extra))}")
    if "source" in meta and not isinstance(meta.get("source"), str):
        issues.append("meta_source_not_str")
    if "redis_hit" in meta and not isinstance(meta.get("redis_hit"), bool):
        issues.append("meta_redis_hit_not_bool")
    if "boot_id" in meta and not isinstance(meta.get("boot_id"), str):
        issues.append("meta_boot_id_not_str")
    if "redis_error_code" in meta and not isinstance(meta.get("redis_error_code"), str):
        issues.append("meta_redis_error_code_not_str")
    for key in ("redis_ttl_s_left", "redis_payload_ts_ms", "redis_seq", "redis_len"):
        if key in meta and not _is_int(meta.get(key)):
            issues.append(f"meta_{key}_not_int")
    if "extensions" in meta and not isinstance(meta.get("extensions"), dict):
        issues.append("meta_extensions_not_object")
    return issues


def _contract_guard_warn_window(
    payload: dict[str, Any],
    bars: list[dict[str, Any]],
    warnings: list[str],
    had_warnings: bool,
) -> None:
    issues: list[str] = []
    if payload.get("ok") is not True:
        issues.append("window_ok_not_true")
    if "note" in payload and payload.get("note") == "no_data":
        if "bars" in payload and isinstance(payload.get("bars"), list) and payload.get("bars"):
            issues.append("window_no_data_bars_not_empty")
    else:
        if "symbol" not in payload:
            issues.append("window_missing_symbol")
        if "tf_s" not in payload:
            issues.append("window_missing_tf_s")
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        issues.append("window_meta_not_object")
    else:
        issues.extend(_guard_meta_shape(meta))
    sample = _sample_items(bars)
    for bar in sample:
        if not isinstance(bar, dict):
            issues.append("window_bar_not_object")
            continue
        issues.extend(_guard_bar_shape(bar))
    if issues:
        if had_warnings:
            warnings.append("contract_violation")
        logging.warning("CONTRACT_VIOLATION schema=window_v1 issues=%s", ",".join(issues[:10]))


def _contract_guard_warn_updates(
    payload: dict[str, Any],
    events: list[dict[str, Any]],
    warnings: list[str],
    had_warnings: bool,
) -> None:
    issues: list[str] = []
    if payload.get("ok") is not True:
        issues.append("updates_ok_not_true")
    for key in ("symbol", "tf_s", "events", "cursor_seq", "boot_id"):
        if key not in payload:
            issues.append(f"updates_missing:{key}")
    if "cursor_seq" in payload and not _is_int(payload.get("cursor_seq")):
        issues.append("updates_cursor_seq_not_int")
    sample = _sample_items(events, full_limit=500, head=50, tail=50)
    for ev in sample:
        if not isinstance(ev, dict):
            issues.append("updates_event_not_object")
            continue
        issues.extend(_guard_event_shape(ev))
    if issues:
        if had_warnings:
            warnings.append("contract_violation")
        logging.warning("CONTRACT_VIOLATION schema=updates_v1 issues=%s", ",".join(issues[:10]))


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
        uds = getattr(self.server, "uds", None)  # type: ignore[attr-defined]
        path = parsed.path.rstrip("/") or "/"

        cfg: dict[str, Any] = {}
        if config_path:
            cfg = _load_cfg_cached(config_path)

        tf_allowlist = _tf_allowlist_from_cfg(cfg)
        preview_allowlist = _preview_tf_allowlist_from_cfg(cfg)

        if uds is None:
            self._bad("uds_not_available")
            return

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

        if path == "/api/status":
            payload = {
                "ok": True,
                "status": uds.snapshot_status(),
            }
            self._json(200, payload)
            return

        if path == "/api/updates":
            symbol = (qs.get("symbol", [""])[0] or "").strip()
            tf_s = _safe_int((qs.get("tf_s", ["300"])[0] or "300"), 300)
            limit = _safe_int((qs.get("limit", ["500"])[0] or "500"), 500)
            since_seq_raw = qs.get("since_seq", [None])[0]
            since_seq = _safe_int(since_seq_raw, 0) if since_seq_raw is not None else None
            epoch_raw = qs.get("epoch", [None])[0]
            epoch = _safe_int(epoch_raw, 0) if epoch_raw is not None else None
            include_preview_raw = qs.get("include_preview", [None])[0]
            include_preview = (
                _parse_bool(include_preview_raw, False) if include_preview_raw is not None else False
            )
            if tf_s in preview_allowlist:
                include_preview = True
            if not symbol:
                self._bad("missing_symbol")
                return
            if tf_s not in tf_allowlist and tf_s not in preview_allowlist:
                self._bad("tf_not_allowed")
                return
            spec = UpdatesSpec(
                symbol=symbol,
                tf_s=tf_s,
                since_seq=since_seq,
                limit=limit,
                include_preview=include_preview,
            )
            res = uds.read_updates(spec)
            payload: dict[str, Any] = {
                "ok": True,
                "symbol": symbol,
                "tf_s": tf_s,
                "events": res.events,
                "cursor_seq": res.cursor_seq,
                "boot_id": _boot_id,
            }
            warnings = list(res.warnings)
            had_warnings = bool(warnings)
            _contract_guard_warn_updates(payload, res.events, warnings, had_warnings)
            if warnings:
                payload["warnings"] = warnings
            payload["disk_last_open_ms"] = res.disk_last_open_ms
            payload["bar_close_ms"] = res.bar_close_ms
            payload["ssot_write_ts_ms"] = res.ssot_write_ts_ms
            payload["api_seen_ts_ms"] = res.api_seen_ts_ms
            if logging.getLogger().isEnabledFor(logging.INFO):
                window_start_ms = None
                window_end_ms = None
                if res.events:
                    first_bar = res.events[0].get("bar")
                    last_bar = res.events[-1].get("bar")
                    if isinstance(first_bar, dict):
                        window_start_ms = first_bar.get("open_time_ms")
                    if isinstance(last_bar, dict):
                        window_end_ms = last_bar.get("close_time_ms")
                now = time.time()
                events_count = len(res.events)
                key = (symbol, tf_s)
                # Лінива ініціалізація глобального стейту для логування (thread-safe)
                st = globals().get("_updates_log_state")
                if st is None:
                    st = {}
                    globals()["_updates_log_state"] = st
                lock = globals().get("_updates_log_lock")
                if lock is None:
                    lock = threading.Lock()
                    globals()["_updates_log_lock"] = lock

                with lock:
                    s = st.get(key)
                    if s is None:
                        s = {"last_ts": 0.0, "events": 0, "requests": 0}
                        st[key] = s
                    s["events"] += events_count
                    s["requests"] += 1
                    elapsed = now - s["last_ts"]
                    # Логуємо або при наявності нових подій, або не частіше ніж раз на 30s
                    if events_count > 0 or elapsed >= 30.0:
                        logged_events = s["events"]
                        logged_requests = s["requests"]
                        rate = logged_events / elapsed if elapsed > 0 else float(logged_events)
                        logging.info(
                            "UI_UPDATES symbol=%s tf_s=%s epoch=%s since_seq=%s count=%s cursor_seq=%s window_start_ms=%s window_end_ms=%s events_since_last_log=%s requests_since_last_log=%s elapsed_s=%.1f event_rate_per_s=%.2f",
                            symbol,
                            tf_s,
                            epoch,
                            since_seq,
                            len(res.events),
                            res.cursor_seq,
                            window_start_ms,
                            window_end_ms,
                            logged_events,
                            logged_requests,
                            elapsed,
                            rate,
                        )
                        s["last_ts"] = now
                        s["events"] = 0
                        s["requests"] = 0
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
            epoch_raw = qs.get("epoch", [None])[0]
            epoch = _safe_int(epoch_raw, 0) if epoch_raw is not None else None

            if not symbol:
                self._bad("missing_symbol")
                return
            if tf_s not in tf_allowlist and tf_s not in preview_allowlist:
                self._bad("tf_not_allowed")
                return

            if tf_s in preview_allowlist:
                # P2X.6: preview TF завжди через read_preview_window,
                # prefer_redis/force_disk ігноруються з loud warning
                preview_warnings: list[str] = []
                if prefer_redis:
                    preview_warnings.append("query_param_ignored:prefer_redis")
                if force_disk:
                    preview_warnings.append("query_param_ignored:force_disk")

                res = uds.read_preview_window(symbol, tf_s, limit, include_current=True)
                payload = {
                    "ok": True,
                    "symbol": symbol,
                    "tf_s": tf_s,
                    "bars": res.bars_lwc,
                    "boot_id": _boot_id,
                    "meta": res.meta,
                }
                warnings = preview_warnings + list(res.warnings)
                if warnings:
                    payload["warnings"] = warnings
                _contract_guard_warn_window(payload, res.bars_lwc, warnings, bool(warnings))
                self._json(200, payload)
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

            # P2X.6: prefer_redis/force_disk ігноруються (Правило 20.2)
            final_extra_warnings: list[str] = []
            if prefer_redis:
                final_extra_warnings.append("query_param_ignored:prefer_redis")
            if force_disk:
                final_extra_warnings.append("query_param_ignored:force_disk")
            cold_load = since_open_ms is None and to_open_ms is None
            spec = WindowSpec(
                symbol=symbol,
                tf_s=tf_s,
                limit=limit,
                since_open_ms=since_open_ms,
                to_open_ms=to_open_ms,
                cold_load=cold_load,
            )
            policy = ReadPolicy(
                force_disk=False,
                prefer_redis=False,
            )
            res = uds.read_window(spec, policy)
            if not res.bars_lwc and since_open_ms is None and to_open_ms is None:
                payload = {
                    "ok": True,
                    "bars": [],
                    "note": "no_data",
                    "boot_id": _boot_id,
                    "meta": res.meta or {"source": "disk", "redis_hit": False, "boot_id": _boot_id},
                }
                if final_extra_warnings:
                    payload["warnings"] = list(final_extra_warnings)
                _contract_guard_warn_window(payload, [], final_extra_warnings, bool(final_extra_warnings))
                self._json(200, payload)
                return
            payload = {
                "ok": True,
                "symbol": symbol,
                "tf_s": tf_s,
                "bars": res.bars_lwc,
                "boot_id": _boot_id,
                "meta": res.meta,
            }
            warnings = final_extra_warnings + list(res.warnings)
            if warnings:
                payload["warnings"] = warnings
            _contract_guard_warn_window(payload, res.bars_lwc, warnings, bool(warnings))
            if logging.getLogger().isEnabledFor(logging.INFO):
                window_start_ms = None
                window_end_ms = None
                if res.bars_lwc:
                    window_start_ms = res.bars_lwc[0].get("open_time_ms")
                    window_end_ms = res.bars_lwc[-1].get("close_time_ms")
                logging.info(
                    "UI_BARS path=%s symbol=%s tf_s=%s epoch=%s limit=%s count=%s window_start_ms=%s window_end_ms=%s source=%s redis_hit=%s redis_error=%s",
                    path,
                    symbol,
                    tf_s,
                    epoch,
                    limit,
                    len(res.bars_lwc),
                    window_start_ms,
                    window_end_ms,
                    res.meta.get("source"),
                    res.meta.get("redis_hit"),
                    res.meta.get("redis_error_code"),
                )
            self._json(200, payload)
            return

        # ----------------------------------------------------------------
        # /api/overlay — P2X.6-U1: ephemeral overlay для TF ≥ M5
        # Живиться з preview M1/M3, агрегує у один бар для target TF.
        # Read-only: жодних записів у UDS/SSOT/final.
        # ----------------------------------------------------------------
        if path == "/api/overlay":
            global _overlay_req_total
            global _overlay_prev_held_total
            global _overlay_prev_wait_ms_last
            global _overlay_prev_hold_since
            symbol = (qs.get("symbol", [""])[0] or "").strip()
            tf_s = _safe_int((qs.get("tf_s", ["300"])[0] or "300"), 300)
            base_tf_s = _safe_int((qs.get("base_tf_s", ["60"])[0] or "60"), 60)

            if not symbol:
                self._bad("missing_symbol")
                return

            overlay_warnings: list[str] = []

            # Перевірка: overlay не для preview TF (вони вже мають живі бари)
            if tf_s in preview_allowlist:
                self._json(200, {
                    "ok": True,
                    "bar": None,
                    "warnings": ["overlay_not_applicable_for_preview_tf"],
                    "meta": {"extensions": {"plane": "overlay"}, "boot_id": _boot_id},
                })
                return

            if tf_s < 300:
                overlay_warnings.append("overlay_tf_too_small")

            if tf_s not in tf_allowlist:
                self._bad("tf_not_allowed")
                return

            if base_tf_s not in preview_allowlist:
                overlay_warnings.append("base_tf_not_in_preview_allowlist")
                self._json(200, {
                    "ok": True,
                    "bar": None,
                    "warnings": overlay_warnings,
                    "meta": {"extensions": {"plane": "overlay", "base_tf_s": base_tf_s}, "boot_id": _boot_id},
                })
                return

            _overlay_req_total += 1

            # P2X.6-U3: 2-bar overlay (prev_bar + curr_bar)
            # prev_bar: попередній бакет, тримається до приходу final
            # curr_bar: поточний бакет (in-progress)
            tf_ms = tf_s * 1000
            base_tf_ms = base_tf_s * 1000
            anchor_offset_ms_cfg = resolve_anchor_offset_ms(tf_s, cfg)
            anchor_offset_ms = anchor_offset_ms_cfg
            last_final_open_ms = None
            try:
                spec_last = WindowSpec(
                    symbol=symbol,
                    tf_s=tf_s,
                    limit=1,
                    since_open_ms=None,
                    to_open_ms=None,
                    cold_load=True,
                )
                res_last = uds.read_window(spec_last, ReadPolicy(force_disk=False, prefer_redis=False))
                if res_last.bars_lwc:
                    last_final_open_ms = res_last.bars_lwc[-1].get("open_time_ms")
            except Exception:
                overlay_warnings.append("overlay_anchor_final_read_failed")

            if isinstance(last_final_open_ms, int) and last_final_open_ms > 0:
                expected = bucket_start_ms(last_final_open_ms, tf_ms, anchor_offset_ms_cfg)
                if expected != last_final_open_ms and _overlay_anchor_warn_allowed(symbol, tf_s):
                    overlay_warnings.append("overlay_anchor_mismatch")
                anchor_offset_ms = int(last_final_open_ms % tf_ms)
            bars_per_bucket = max(1, tf_ms // base_tf_ms)
            # Потрібно покрити 2 бакети + запас
            preview_bars_needed = bars_per_bucket * 2 + 4

            res = uds.read_preview_window(symbol, base_tf_s, preview_bars_needed, include_current=True)
            overlay_warnings.extend(res.warnings)

            if not res.bars_lwc:
                overlay_warnings.append("overlay_preview_unavailable")
                self._json(200, {
                    "ok": True,
                    "bar": None,
                    "bars": [],
                    "warnings": overlay_warnings,
                    "meta": {"extensions": {"plane": "overlay", "base_tf_s": base_tf_s}, "boot_id": _boot_id},
                })
                return

            # Визначаємо поточний та попередній bucket для target TF
            now_ms = int(time.time() * 1000)
            b0 = bucket_start_ms(now_ms, tf_ms, anchor_offset_ms)
            b_prev = b0 - tf_ms

            def _aggregate_bucket(bars_lwc: list[dict[str, Any]], bucket_start: int) -> dict[str, Any] | None:
                """Агрегація preview-барів у один overlay bar для бакету."""
                in_b: list[dict[str, Any]] = []
                max_ltt = 0
                for bar in bars_lwc:
                    bar_open_ms = bar.get("open_time_ms")
                    if bar_open_ms is None:
                        continue
                    if bucket_start <= bar_open_ms < bucket_start + tf_ms:
                        in_b.append(bar)
                        ltt = bar.get("last_tick_ts")
                        if isinstance(ltt, int) and ltt > max_ltt:
                            max_ltt = ltt
                if not in_b:
                    return None
                agg = {
                    "time": bucket_start // 1000,
                    "open": float(in_b[0].get("open", 0.0)),
                    "high": float(max(b.get("high", 0.0) for b in in_b)),
                    "low": float(min(b.get("low", float("inf")) for b in in_b)),
                    "close": float(in_b[-1].get("close", 0.0)),
                    "volume": 0.0,
                    "open_time_ms": bucket_start,
                    "close_time_ms": bucket_start + tf_ms,
                    "tf_s": tf_s,
                    "src": "overlay_preview",
                    "complete": False,
                }
                if max_ltt > 0:
                    agg["last_tick_ts"] = max_ltt
                # Прокидаємо last_price з останнього preview-бару (тік-ціна)
                last_close = in_b[-1].get("close")
                last_price_raw = in_b[-1].get("last_price")
                if last_price_raw is not None:
                    agg["last_price"] = float(last_price_raw)
                elif last_close is not None:
                    agg["last_price"] = float(last_close)
                return agg

            curr_bar = _aggregate_bucket(res.bars_lwc, b0)
            prev_bar_candidate = _aggregate_bucket(res.bars_lwc, b_prev)

            # P2X.6-U3: prev_bar тримається лише до приходу final
            prev_bar = None
            if prev_bar_candidate is not None:
                try:
                    since_prev = max(0, b_prev - 1)
                    spec_prev = WindowSpec(
                        symbol=symbol,
                        tf_s=tf_s,
                        limit=1,
                        since_open_ms=since_prev,
                        to_open_ms=b_prev,
                        cold_load=False,
                    )
                    res_final = uds.read_window(spec_prev, ReadPolicy(force_disk=False, prefer_redis=False))
                    has_final = any(
                        b.get("open_time_ms") == b_prev
                        for b in (res_final.bars_lwc or [])
                    )
                    if not has_final:
                        prev_bar = prev_bar_candidate
                except Exception:
                    # Якщо перевірка final упала — тримаємо prev_bar (degraded-but-visible)
                    prev_bar = prev_bar_candidate
                    overlay_warnings.append("overlay_final_check_failed")

            key = (symbol, tf_s)
            if prev_bar is not None:
                if key not in _overlay_prev_hold_since:
                    _overlay_prev_hold_since[key] = now_ms
                _overlay_prev_held_total += 1
            else:
                since_ms = _overlay_prev_hold_since.pop(key, None)
                if since_ms is not None:
                    _overlay_prev_wait_ms_last = max(0, now_ms - since_ms)

            overlay_bars = []
            if prev_bar is not None:
                overlay_bars.append(prev_bar)
            if curr_bar is not None:
                overlay_bars.append(curr_bar)

            if _overlay_obs_log_allowed():
                logging.info(
                    "UI_OVERLAY_OBS req_total=%s prev_held_total=%s prev_wait_ms_last=%s",
                    _overlay_req_total,
                    _overlay_prev_held_total,
                    _overlay_prev_wait_ms_last,
                )

            self._json(200, {
                "ok": True,
                "bar": curr_bar,  # backward compat (P2X.6-U1)
                "bars": overlay_bars,  # P2X.6-U3: 0-2 бари
                "warnings": overlay_warnings if overlay_warnings else [],
                "meta": {
                    "extensions": {
                        "plane": "overlay",
                        "base_tf_s": base_tf_s,
                        "bucket_open_ms": b0,
                        "prev_bucket_open_ms": b_prev,
                        "has_prev_bar": prev_bar is not None,
                        "has_curr_bar": curr_bar is not None,
                    },
                    "boot_id": _boot_id,
                },
            })
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
    httpd.uds = build_uds_from_config(  # type: ignore[attr-defined]
        config_path,
        data_root,
        _boot_id,
        role="reader",
    )

    logging.info("UI: http://%s:%s/", args.host, args.port)
    logging.debug("DATA_ROOT: %s", httpd.data_root)  # type: ignore[attr-defined]
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
