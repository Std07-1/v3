"""Immutable audit stream → Redis XADD (ADR-0052 S7).

Each event is appended to a capped Redis stream (``MAXLEN ~ N`` trim). Events
carry ``ts_ms``, ``nonce`` (random), and structured fields from the caller.
Callers use this to make degraded-but-loud visible: every auth deny, every
rate-limit hit, every CSRF failure → one XADD.

Fail-open: if Redis is unreachable the call logs WARNING and returns False.
Never raises — an audit outage must never break the user-facing endpoint.
"""
from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

_log = logging.getLogger(__name__)

EventType = str  # "auth_deny" | "rate_limit_hit" | "csrf_fail" | "xss_strip" | ...


class _RedisLike(Protocol):
    def xadd(
        self,
        name: str,
        fields: Mapping[str, Any],
        id: str = "*",
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> Any: ...


@dataclass(frozen=True)
class AuditConfig:
    enabled: bool = False
    stream_key: str = "audit:chat"
    max_len: int = 10000

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> "AuditConfig":
        return cls(
            enabled=bool(m.get("enabled", False)),
            stream_key=str(m.get("stream_key", "audit:chat")),
            max_len=int(m.get("max_len", 10000)),
        )


def _coerce(value: Any) -> str:
    """Redis streams only store strings; coerce safely."""
    if value is None:
        return ""
    if isinstance(value, (str, bytes)):
        return value if isinstance(value, str) else value.decode("utf-8", "replace")
    return str(value)


def log_event(
    redis_client: _RedisLike | None,
    event_type: EventType,
    payload: Mapping[str, Any],
    cfg: AuditConfig,
) -> bool:
    """Append one event to the audit stream. Returns True on success."""
    if not cfg.enabled:
        return False
    if redis_client is None:
        _log.warning("AUDIT_DEGRADED: redis unavailable event=%s", event_type)
        return False
    fields: dict[str, str] = {
        "type": str(event_type),
        "ts_ms": str(int(time.time() * 1000)),
        "nonce": secrets.token_hex(8),
    }
    for k, v in payload.items():
        fields[str(k)] = _coerce(v)
    try:
        redis_client.xadd(
            cfg.stream_key, fields, maxlen=cfg.max_len, approximate=True
        )
        return True
    except Exception as exc:
        _log.warning("AUDIT_DEGRADED: xadd failed event=%s err=%s", event_type, exc)
        return False
