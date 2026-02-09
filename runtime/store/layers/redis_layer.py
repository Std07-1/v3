from __future__ import annotations

import json
from typing import Any, Optional, Tuple

from runtime.store.redis_keys import symbol_key


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
