from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple

from core.model.bars import CandleBar

try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore


class RedisSnapshotWriter:
    def __init__(
        self,
        client: Any,
        ns: str,
        ttl_by_tf_s: Dict[int, int],
        tail_n_by_tf_s: Dict[int, int],
        boot_id: str,
    ) -> None:
        self._client = client
        self._ns = ns
        self._ttl_by_tf_s = ttl_by_tf_s
        self._tail_n_by_tf_s = tail_n_by_tf_s
        self._boot_id = boot_id
        self._seq = 0
        self._tails: Dict[Tuple[str, int], Deque[Dict[str, Any]]] = {}
        self._last_final_close_ms: Optional[int] = None
        self._redis_ok = True
        self._last_err_ts = 0.0
        self._suppressed_err = 0
        self._last_err_msg = ""
        self._cache_primed = False
        self._cache_prime_partial = False
        self._cache_primed_counts: Dict[str, int] = {}
        self._cache_priming_ts_ms: Optional[int] = None
        self._cache_degraded: list[str] = []
        self._cache_errors: list[str] = []
        self._gap_state: Dict[str, Any] = {}

    def close(self) -> None:
        try:
            close_fn = getattr(self._client, "close", None)
            if callable(close_fn):
                close_fn()
        except Exception:
            pass

    def ping(self) -> Tuple[bool, Optional[str]]:
        try:
            pong = self._client.ping()
            self._redis_ok = True
            return bool(pong), None
        except Exception as exc:
            self._redis_ok = False
            return False, str(exc)

    def _key(self, *parts: str) -> str:
        return ":".join([self._ns, *parts])

    def _ttl(self, tf_s: int) -> Optional[int]:
        ttl = int(self._ttl_by_tf_s.get(tf_s, 0))
        return ttl if ttl > 0 else None

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _log_error_throttled(self, message: str, every_s: int = 60) -> None:
        now = time.time()
        if now - self._last_err_ts < every_s:
            self._suppressed_err += 1
            self._last_err_msg = message
            return
        if self._suppressed_err > 0:
            logging.warning(
                "REDIS_SNAP_SUPPRESSED count=%d window_s=%d last=%s",
                self._suppressed_err,
                int(now - self._last_err_ts),
                self._last_err_msg,
            )
        logging.warning("%s", message)
        self._last_err_ts = now
        self._suppressed_err = 0
        self._last_err_msg = message

    def _write_json(self, key: str, payload: Dict[str, Any], ttl_s: Optional[int]) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            if ttl_s is not None:
                self._client.set(key, raw, ex=ttl_s)
            else:
                self._client.set(key, raw)
            self._redis_ok = True
        except Exception as exc:
            self._redis_ok = False
            self._log_error_throttled(f"REDIS_SNAP_WRITE_FAILED key={key} err={exc}")

    def _bar_to_cache_bar(self, bar: CandleBar) -> Optional[Dict[str, Any]]:
        open_ms = bar.open_time_ms
        close_ms_excl = bar.close_time_ms
        tf_ms = int(bar.tf_s) * 1000
        if close_ms_excl <= open_ms:
            return None
        if bar.complete and close_ms_excl != open_ms + tf_ms:
            return None
        close_ms_incl = close_ms_excl - 1
        return {
            "open_ms": open_ms,
            "close_ms": close_ms_incl,
            "o": bar.o,
            "h": bar.h,
            "l": bar.low,
            "c": bar.c,
            "v": bar.v,
        }

    def set_cache_state(
        self,
        *,
        primed: bool,
        prime_partial: bool,
        priming_ts_ms: int,
        primed_counts: Dict[str, int],
        degraded: list[str],
        errors: list[str],
    ) -> None:
        self._cache_primed = bool(primed)
        self._cache_prime_partial = bool(prime_partial)
        self._cache_priming_ts_ms = int(priming_ts_ms)
        self._cache_primed_counts = dict(primed_counts)
        self._cache_degraded = list(degraded)
        self._cache_errors = list(errors)
        self._write_status(int(priming_ts_ms))

    def set_gap_state(
        self,
        *,
        backlog_bars: int,
        gap_from_ms: Optional[int],
        gap_to_ms: Optional[int],
        policy: Optional[str],
    ) -> None:
        state: Dict[str, Any] = {}
        if int(backlog_bars) > 0:
            state["m5_backlog_bars"] = int(backlog_bars)
            if gap_from_ms is not None:
                state["m5_gap_from_ms"] = int(gap_from_ms)
            if gap_to_ms is not None:
                state["m5_gap_to_ms"] = int(gap_to_ms)
            if policy:
                state["policy"] = str(policy)
        self._gap_state = state
        self._write_status(int(time.time() * 1000))

    def prime_from_bars(self, symbol: str, tf_s: int, bars: list[CandleBar]) -> int:
        if not bars:
            return 0
        tail: list[Dict[str, Any]] = []
        last_complete = True
        last_src = ""
        last_close_ms_excl: Optional[int] = None
        for b in bars:
            if b.symbol != symbol or b.tf_s != tf_s:
                continue
            cache_bar = self._bar_to_cache_bar(b)
            if cache_bar is None:
                continue
            tail.append(cache_bar)
            last_complete = bool(b.complete)
            last_src = str(b.src)
            last_close_ms_excl = b.close_time_ms
        if not tail:
            return 0

        payload_ts_ms = int(time.time() * 1000)
        seq = self._next_seq()
        snap_bar = tail[-1]
        snap = {
            "v": 1,
            "symbol": symbol,
            "tf_s": tf_s,
            "bar": snap_bar,
            "complete": bool(last_complete),
            "source": last_src,
            "event_ts_ms": snap_bar["close_ms"] if last_complete else None,
            "seq": seq,
            "payload_ts_ms": payload_ts_ms,
        }
        key = self._key("ohlcv", "snap", symbol, str(tf_s))
        self._write_json(key, snap, self._ttl(tf_s))

        n = int(self._tail_n_by_tf_s.get(tf_s, 0))
        if n > 0:
            if len(tail) > n:
                tail = tail[-n:]
            k = (symbol, tf_s)
            self._tails[k] = deque(tail, maxlen=n)
            tail_payload = {
                "v": 1,
                "symbol": symbol,
                "tf_s": tf_s,
                "bars": list(self._tails[k]),
                "complete": bool(last_complete),
                "source": last_src,
                "last_seq": seq,
                "payload_ts_ms": payload_ts_ms,
            }
            tail_key = self._key("ohlcv", "tail", symbol, str(tf_s))
            self._write_json(tail_key, tail_payload, self._ttl(tf_s))

        if last_complete and last_close_ms_excl is not None:
            if self._last_final_close_ms is None or last_close_ms_excl > self._last_final_close_ms:
                self._last_final_close_ms = last_close_ms_excl
        self._write_status(payload_ts_ms)
        return len(tail)

    def put_bar(self, bar: CandleBar) -> None:
        payload_ts_ms = int(time.time() * 1000)
        open_ms = bar.open_time_ms
        close_ms_excl = bar.close_time_ms
        tf_ms = int(bar.tf_s) * 1000
        if close_ms_excl <= open_ms:
            logging.warning(
                "REDIS_SNAP_SKIP_INVALID_BAR symbol=%s tf_s=%s open_ms=%s close_ms=%s reason=close_le_open",
                bar.symbol,
                bar.tf_s,
                open_ms,
                close_ms_excl,
            )
            return
        if bar.complete and close_ms_excl != open_ms + tf_ms:
            logging.warning(
                "REDIS_SNAP_SKIP_INVALID_BAR symbol=%s tf_s=%s open_ms=%s close_ms=%s reason=close_mismatch",
                bar.symbol,
                bar.tf_s,
                open_ms,
                close_ms_excl,
            )
            return
        close_ms_incl = close_ms_excl - 1
        seq = self._next_seq()
        snap = {
            "v": 1,
            "symbol": bar.symbol,
            "tf_s": bar.tf_s,
            "bar": {
                "open_ms": open_ms,
                "close_ms": close_ms_incl,
                "o": bar.o,
                "h": bar.h,
                "l": bar.low,
                "c": bar.c,
                "v": bar.v,
            },
            "complete": bool(bar.complete),
            "source": str(bar.src),
            "event_ts_ms": close_ms_incl if bar.complete else None,
            "seq": seq,
            "payload_ts_ms": payload_ts_ms,
        }
        key = self._key("ohlcv", "snap", bar.symbol, str(bar.tf_s))
        self._write_json(key, snap, self._ttl(bar.tf_s))

        n = int(self._tail_n_by_tf_s.get(bar.tf_s, 0))
        if n > 0:
            k = (bar.symbol, bar.tf_s)
            if k not in self._tails:
                self._tails[k] = deque(maxlen=n)
            self._tails[k].append(snap["bar"])
            tail = {
                "v": 1,
                "symbol": bar.symbol,
                "tf_s": bar.tf_s,
                "bars": list(self._tails[k]),
                "complete": bool(bar.complete),
                "source": str(bar.src),
                "last_seq": seq,
                "payload_ts_ms": payload_ts_ms,
            }
            tail_key = self._key("ohlcv", "tail", bar.symbol, str(bar.tf_s))
            self._write_json(tail_key, tail, self._ttl(bar.tf_s))

        if bar.complete:
            if self._last_final_close_ms is None or bar.close_time_ms > self._last_final_close_ms:
                self._last_final_close_ms = bar.close_time_ms
        self._write_status(payload_ts_ms)

    def _write_status(self, now_ms: int) -> None:
        status = {
            "v": 1,
            "boot_id": self._boot_id,
            "now_ms": now_ms,
            "redis": {"ok": bool(self._redis_ok)},
            "bars": {"last_final_close_ms": self._last_final_close_ms},
            "gaps": dict(self._gap_state),
            "cache": {
                "primed": bool(self._cache_primed),
                "prime_partial": bool(self._cache_prime_partial),
                "priming_ts_ms": self._cache_priming_ts_ms,
                "primed_counts": dict(self._cache_primed_counts),
            },
            "degraded": list(self._cache_degraded),
            "errors": list(self._cache_errors),
            "warnings": [],
            "last_error": None,
        }
        key = self._key("status", "snapshot")
        self._write_json(key, status, None)


_WRITER_CACHE: Dict[str, RedisSnapshotWriter] = {}


def _parse_int_map(raw: Any) -> Dict[int, int]:
    out: Dict[int, int] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        try:
            key = int(k)
            val = int(v)
        except Exception:
            continue
        if key > 0 and val > 0:
            out[key] = val
    return out


def build_redis_snapshot_writer(config_path: str) -> Optional[RedisSnapshotWriter]:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return None

    raw = cfg.get("redis")
    if not isinstance(raw, dict):
        return None
    if not bool(raw.get("enabled", False)):
        return None
    if redis_lib is None:
        logging.warning("Redis: пакет redis не встановлено, snapshots вимкнено")
        return None

    host = str(raw.get("host", "127.0.0.1"))
    port = int(raw.get("port", 6379))
    db = int(raw.get("db", 0))
    ns = str(raw.get("ns", "v3"))
    ttl_by_tf_s = _parse_int_map(raw.get("ttl_by_tf_s"))
    tail_n_by_tf_s = _parse_int_map(raw.get("tail_n_by_tf_s"))

    cache_key = f"{host}:{port}:{db}:{ns}"
    cached = _WRITER_CACHE.get(cache_key)
    if cached is not None:
        return cached

    client = redis_lib.Redis(host=host, port=port, db=db, decode_responses=True)
    boot_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    writer = RedisSnapshotWriter(
        client=client,
        ns=ns,
        ttl_by_tf_s=ttl_by_tf_s,
        tail_n_by_tf_s=tail_n_by_tf_s,
        boot_id=boot_id,
    )
    _WRITER_CACHE[cache_key] = writer
    return writer


def init_redis_snapshot(config_path: str, log_detail: bool = True) -> bool:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as exc:
        logging.warning("REDIS_INIT_SKIP reason=config_read_failed err=%s", exc)
        return False

    raw = cfg.get("redis")
    if not isinstance(raw, dict):
        logging.info("REDIS_INIT_SKIP reason=redis_not_configured")
        return False
    if not bool(raw.get("enabled", False)):
        logging.info("REDIS_INIT_SKIP reason=redis_disabled")
        return False
    if redis_lib is None:
        logging.warning("REDIS_INIT_SKIP reason=redis_package_missing")
        return False

    host = str(raw.get("host", "127.0.0.1"))
    port = int(raw.get("port", 6379))
    db = int(raw.get("db", 0))
    ns = str(raw.get("ns", "v3"))
    ttl_by_tf_s = _parse_int_map(raw.get("ttl_by_tf_s"))
    tail_n_by_tf_s = _parse_int_map(raw.get("tail_n_by_tf_s"))

    if log_detail:
        logging.info(
            "REDIS_INIT_START host=%s port=%d db=%d ns=%s",
            host,
            port,
            db,
            ns,
        )

    writer = build_redis_snapshot_writer(config_path)
    if writer is None:
        logging.warning(
            "REDIS_INIT_FAIL reason=writer_unavailable host=%s port=%d db=%d ns=%s",
            host,
            port,
            db,
            ns,
        )
        return False

    ok, err = writer.ping()
    if ok:
        logging.info(
            "REDIS_INIT_OK host=%s port=%d db=%d ns=%s ttl_tfs=%d tail_tfs=%d",
            host,
            port,
            db,
            ns,
            len(ttl_by_tf_s),
            len(tail_n_by_tf_s),
        )
        return True

    logging.warning(
        "REDIS_INIT_FAIL host=%s port=%d db=%d ns=%s err=%s",
        host,
        port,
        db,
        ns,
        err,
    )
    return False
