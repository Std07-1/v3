"""ADR-0058 — Token store (Redis-backed lookup for public API tokens).

Token format (F-S1-006):
    "tk_" + secrets.token_bytes(32).hex()  → 67 chars total

Redis schema:
    Key:   {namespace}:tokens:{token}
    Value: JSON {"consumer": str, "scope": str, "created": ISO8601, "expires": ISO8601}
    TTL:   set via SETEX at issuance (default 90 days, see slice 058.4 tooling)

Scope semantics (F-S1-007):
    Only "read" is honored in slice 058.1. Future scopes ("read:XAU/USD",
    "read:no-narrative") are reserved — `lookup()` returns None for them
    (fail-closed). Extension requires ADR amendment.

This module is layer-runtime (allowed Redis I/O) but contains no FastAPI
imports — kept thin for unit testing without an HTTP harness.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional, cast

import redis as redis_lib

log = logging.getLogger("api_v3.token_store")

TOKEN_PREFIX = "tk_"
TOKEN_HEX_LEN = 64  # 32 bytes * 2 chars/byte (secrets.token_bytes(32).hex())
TOKEN_FULL_LEN = len(TOKEN_PREFIX) + TOKEN_HEX_LEN  # 67

# F-S1-007: only "read" scope implemented. Unknown scopes fail-closed via lookup() returning None.
VALID_SCOPES = frozenset({"read"})


@dataclass(frozen=True)
class TokenRecord:
    """Validated token snapshot returned to the auth endpoint."""

    consumer: str
    scope: str
    created: str
    expires: str


def token_redis_key(namespace: str, token: str) -> str:
    """Compose the Redis key for a token. SSOT for the key shape."""
    return f"{namespace}:tokens:{token}"


def is_well_formed(token: Optional[str]) -> bool:
    """Cheap shape check before hitting Redis.

    Rejects: None, empty, wrong length, missing prefix, non-hex tail.
    """
    if not token or len(token) != TOKEN_FULL_LEN:
        return False
    if not token.startswith(TOKEN_PREFIX):
        return False
    hex_tail = token[len(TOKEN_PREFIX) :]
    try:
        int(hex_tail, 16)
    except ValueError:
        return False
    return True


class TokenStore:
    """Redis-backed token validator. Caller MUST handle RedisError fail-closed.

    No in-memory cache: revocation (`DEL` in Redis) takes effect on the next
    request. This trades a per-request Redis GET (~0.1ms local) for instant
    revocation — acceptable since /api/v3/* is rate-limited at 60 req/min.
    """

    def __init__(self, client: redis_lib.Redis, namespace: str) -> None:
        self._redis = client
        self._namespace = namespace

    def lookup(self, token: Optional[str]) -> Optional[TokenRecord]:
        """Return TokenRecord if token is valid+active+known-scope, else None.

        Returns None for: malformed shape, missing in Redis, malformed JSON,
        unknown scope (F-S1-007), missing consumer field.

        Raises:
            redis.exceptions.RedisError: on connection/timeout. Caller MUST
                fail-closed (HTTP 503). Never let a Redis outage silently
                allow requests through.
        """
        if not is_well_formed(token):
            return None
        # `token` is non-None after is_well_formed passes (asserted by shape check)
        assert token is not None
        raw = self._redis.get(token_redis_key(self._namespace, token))
        if raw is None:
            return None
        # decode_responses=True on the client guarantees str, but redis-py's
        # type stubs are unioned with the async API → narrow explicitly.
        if not isinstance(raw, (str, bytes, bytearray)):
            log.warning("token_store unexpected_redis_type type=%s", type(raw).__name__)
            return None
        try:
            payload = json.loads(cast("str | bytes | bytearray", raw))
        except (json.JSONDecodeError, TypeError):
            log.warning("token_store malformed_json key_prefix=%s", token[:8])
            return None
        scope = payload.get("scope")
        if scope not in VALID_SCOPES:
            # F-S1-007: forbidden_scope — reserved future vocabulary.
            log.info(
                "token_store unknown_scope scope=%r consumer=%r",
                scope,
                payload.get("consumer"),
            )
            return None
        consumer = payload.get("consumer")
        if not consumer or not isinstance(consumer, str):
            log.warning("token_store missing_consumer key_prefix=%s", token[:8])
            return None
        return TokenRecord(
            consumer=consumer,
            scope=scope,
            created=str(payload.get("created", "")),
            expires=str(payload.get("expires", "")),
        )
