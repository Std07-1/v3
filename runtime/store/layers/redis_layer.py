from __future__ import annotations

import json
from typing import Any, Optional, Tuple

from runtime.store.redis_keys import (
    preview_curr_key,
    preview_tail_key,
    preview_updates_list_key,
    preview_updates_seq_key,
    symbol_key,
)


class RedisLayer:
    """Redis шар: читання tail/snap для Phase A."""

    def __init__(self, client: Any, ns: str) -> None:
        self._client = client
        self._ns = ns

    def _key(self, *parts: str) -> str:
        return ":".join([self._ns, *parts])

    def _get_json(self, key: str) -> Tuple[Optional[dict[str, Any]], Optional[int], Optional[str]]:
        try:
            raw = self._client.get(key)
            if raw is None:
                return None, None, "redis_miss"
            ttl_left = self._client.ttl(key)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            payload = json.loads(raw)
            if ttl_left is None:
                ttl_left = -1
            return payload, int(ttl_left), None
        except Exception:
            return None, None, "redis_error"

    def _write_json(self, key: str, payload: dict[str, Any], ttl_s: Optional[int]) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if ttl_s is not None and ttl_s > 0:
            self._client.set(key, raw, ex=int(ttl_s))
        else:
            self._client.set(key, raw)

    def read_tail_or_snap(
        self, symbol: str, tf_s: int
    ) -> Tuple[Optional[dict[str, Any]], Optional[int], Optional[str], Optional[str]]:
        key_symbol = symbol_key(symbol)
        tail_key = self._key("ohlcv", "tail", key_symbol, str(tf_s))
        payload, ttl_left, err = self._get_json(tail_key)
        if err == "redis_miss":
            snap_key = self._key("ohlcv", "snap", key_symbol, str(tf_s))
            payload, ttl_left, err = self._get_json(snap_key)
            if err is None:
                return payload, ttl_left, "redis_snap", None
        if err is None:
            return payload, ttl_left, "redis_tail", None
        return None, ttl_left, None, err

    def read_preview_curr(
        self, symbol: str, tf_s: int
    ) -> Tuple[Optional[dict[str, Any]], Optional[int], Optional[str]]:
        key = preview_curr_key(self._ns, symbol, tf_s)
        return self._get_json(key)

    def read_preview_tail(
        self, symbol: str, tf_s: int
    ) -> Tuple[Optional[dict[str, Any]], Optional[int], Optional[str]]:
        key = preview_tail_key(self._ns, symbol, tf_s)
        return self._get_json(key)

    def write_preview_curr(
        self, symbol: str, tf_s: int, payload: dict[str, Any], ttl_s: Optional[int]
    ) -> None:
        key = preview_curr_key(self._ns, symbol, tf_s)
        self._write_json(key, payload, ttl_s)

    def write_preview_tail(
        self, symbol: str, tf_s: int, payload: dict[str, Any], ttl_s: Optional[int] = None
    ) -> None:
        key = preview_tail_key(self._ns, symbol, tf_s)
        self._write_json(key, payload, ttl_s)

    def publish_preview_event(
        self, symbol: str, tf_s: int, event: dict[str, Any], retain: int
    ) -> Optional[int]:
        seq_key = preview_updates_seq_key(self._ns, symbol, tf_s)
        list_key = preview_updates_list_key(self._ns, symbol, tf_s)
        seq = int(self._client.incr(seq_key))
        event = dict(event)
        event["seq"] = seq
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        self._client.rpush(list_key, payload)
        self._client.ltrim(list_key, -max(1, int(retain)), -1)
        return seq

    def read_preview_updates(
        self,
        symbol: str,
        tf_s: int,
        since_seq: Optional[int],
        limit: int,
        retain: int,
    ) -> tuple[list[dict[str, Any]], int, Optional[dict[str, Any]], Optional[str]]:
        try:
            seq_key = preview_updates_seq_key(self._ns, symbol, tf_s)
            list_key = preview_updates_list_key(self._ns, symbol, tf_s)

            # P2: since_seq is None → adopt-tail (перший poll після loadBarsFull)
            # Повертаємо events=[], cursor_seq=max_seq (fast-forward)
            if since_seq is None:
                last_seq_raw = self._client.get(seq_key)
                if isinstance(last_seq_raw, bytes):
                    last_seq_raw = last_seq_raw.decode("utf-8")
                try:
                    cursor_seq = int(last_seq_raw) if last_seq_raw is not None else 0
                except Exception:
                    cursor_seq = 0
                return [], cursor_seq, None, None

            raw_list = self._client.lrange(list_key, -max(1, int(retain)), -1)
            events: list[dict[str, Any]] = []
            min_seq: Optional[int] = None
            max_seq: Optional[int] = None
            for raw in raw_list or []:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                seq = ev.get("seq")
                if not isinstance(seq, int):
                    continue
                if min_seq is None or seq < min_seq:
                    min_seq = seq
                if max_seq is None or seq > max_seq:
                    max_seq = seq
                if seq <= since_seq:
                    continue
                events.append(ev)

            # P1: cursor_gap → fast-forward, events=[] (НЕ віддаємо stale хвіст)
            gap: Optional[dict[str, Any]] = None
            if min_seq is not None and since_seq < min_seq - 1:
                gap = {
                    "first_seq_available": min_seq,
                    "last_seq_available": max_seq if max_seq is not None else min_seq,
                }
                cursor_seq = max_seq if max_seq is not None else min_seq
                return [], int(cursor_seq), gap, None

            if limit > 0 and len(events) > limit:
                events = events[-limit:]

            cursor_seq = since_seq
            if events:
                cursor_seq = max(ev.get("seq", 0) for ev in events)

            return events, int(cursor_seq), gap, None
        except Exception as exc:
            return [], since_seq if since_seq is not None else 0, None, str(exc)

    def get_prime_ready_payload(self) -> Optional[dict[str, Any]]:
        key = self._key("prime", "ready")
        payload, _, err = self._get_json(key)
        if err is not None or payload is None:
            return None
        return payload
