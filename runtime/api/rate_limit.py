"""Redis-backed per-identity rate limiter (ADR-0052 S7).

Fixed-window counter (INCR + EXPIRE). Default 10 requests / 60 s per identity
(token or IP). **Fail-open with a loud warning** — if Redis is unreachable the
limiter returns ``(True, -1)`` and emits a warning log, so a Redis outage
never silently blocks legitimate traffic (I7 degraded-but-loud).

Returns ``(allowed, retry_after_s)``:
    allowed=True  → caller proceeds; ``retry_after_s=-1`` signals degraded path
    allowed=False → HTTP 429; ``retry_after_s`` = seconds until the next window
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

_log = logging.getLogger(__name__)


class _RedisLike(Protocol):
    def incr(self, name: str) -> int: ...
    def expire(self, name: str, time: int) -> bool: ...


@dataclass(frozen=True)
class RateLimitConfig:
    enabled: bool = False
    requests_per_minute: int = 10
    window_seconds: int = 60
    key_prefix: str = "ratelimit:"

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> "RateLimitConfig":
        return cls(
            enabled=bool(m.get("enabled", False)),
            requests_per_minute=int(m.get("requests_per_minute", 10)),
            window_seconds=int(m.get("window_seconds", 60)),
            key_prefix=str(m.get("key_prefix", "ratelimit:")),
        )


def check_and_consume(
    redis_client: _RedisLike | None,
    identity: str,
    cfg: RateLimitConfig,
    now_s: float | None = None,
) -> tuple[bool, int]:
    """Consume one token for ``identity``. Returns ``(allowed, retry_after_s)``.

    Fail-open contract: if ``cfg.enabled`` is False or the Redis call raises,
    we return ``(True, -1)`` and log WARNING. The negative ``retry_after_s``
    signals "limiter degraded" so the caller can record an audit entry.
    """
    if not cfg.enabled:
        return (True, -1)
    if redis_client is None:
        _log.warning("RATE_LIMIT_DEGRADED: redis unavailable identity=%s", identity)
        return (True, -1)
    ts = now_s if now_s is not None else time.time()
    window = int(ts // cfg.window_seconds)
    key = f"{cfg.key_prefix}{identity}:{window}"
    try:
        count = int(redis_client.incr(key))
        if count == 1:
            redis_client.expire(key, cfg.window_seconds)
    except Exception as exc:
        _log.warning(
            "RATE_LIMIT_DEGRADED: redis error identity=%s err=%s", identity, exc
        )
        return (True, -1)
    if count > cfg.requests_per_minute:
        next_window_start = (window + 1) * cfg.window_seconds
        retry_after = max(1, int(next_window_start - ts))
        return (False, retry_after)
    return (True, 0)
