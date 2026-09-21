"""Спільний in-memory фейк Redis для тестів preview-кільця (RedisLayer / UDS.read_updates).

Реалізує лише команди, які використовує RedisLayer: get/set/ttl/incr/rpush/ltrim/lrange.
Семантика індексів lrange/ltrim — як у Redis (включний кінець, від'ємні індекси).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from runtime.store.redis_keys import preview_updates_list_key, preview_updates_seq_key


class PreviewRingFakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, bytes] = {}
        self.lists: dict[str, list[bytes]] = {}

    def get(self, key: str) -> Optional[bytes]:
        return self.kv.get(key)

    def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        self.kv[key] = value.encode("utf-8") if isinstance(value, str) else value
        return True

    def ttl(self, key: str) -> int:
        return -1 if key in self.kv else -2

    def incr(self, key: str) -> int:
        value = int(self.kv.get(key, b"0")) + 1
        self.kv[key] = str(value).encode("utf-8")
        return value

    def rpush(self, key: str, value: Any) -> int:
        payload = value.encode("utf-8") if isinstance(value, str) else value
        self.lists.setdefault(key, []).append(payload)
        return len(self.lists[key])

    def ltrim(self, key: str, start: int, end: int) -> bool:
        self.lists[key] = self._slice(self.lists.get(key, []), start, end)
        return True

    def lrange(self, key: str, start: int, end: int) -> list[bytes]:
        return self._slice(self.lists.get(key, []), start, end)

    @staticmethod
    def _slice(items: list[bytes], start: int, end: int) -> list[bytes]:
        size = len(items)
        lo = max(0, size + start if start < 0 else start)
        hi = size + end if end < 0 else min(end, size - 1)
        return list(items[lo : hi + 1]) if hi >= lo else []

    # ── Хелпери сценаріїв (не команди Redis) ─────────────────────────────

    def push_ring_event(
        self,
        ns: str,
        symbol: str,
        tf_s: int,
        open_ms: int,
        *,
        retain: int,
        complete: bool = False,
    ) -> int:
        """Публікує подію так само, як RedisLayer.publish_preview_event: INCR → RPUSH → LTRIM."""
        seq = self.incr(preview_updates_seq_key(ns, symbol, tf_s))
        event = {
            "seq": seq,
            "complete": complete,
            "source": "history" if complete else "preview_tick",
            "bar": {
                "open_time_ms": open_ms,
                "close_time_ms": open_ms + tf_s * 1000,
                "o": 1.0,
                "h": 2.0,
                "low": 0.5,
                "c": 1.5,
                "v": 1.0,
                "complete": complete,
            },
        }
        list_key = preview_updates_list_key(ns, symbol, tf_s)
        self.rpush(list_key, json.dumps(event))
        self.ltrim(list_key, -retain, -1)
        return seq

    def seed_ring(
        self, ns: str, symbol: str, tf_s: int, last_seq: int, count: int, *, retain: int
    ) -> None:
        """Заповнює кільце подіями (last_seq-count, last_seq] — порядок величин як на проді."""
        self.kv[preview_updates_seq_key(ns, symbol, tf_s)] = str(last_seq - count).encode()
        for _ in range(count):
            self.push_ring_event(ns, symbol, tf_s, 1_790_000_000_000, retain=retain)
