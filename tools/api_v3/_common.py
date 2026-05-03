"""Shared Redis client + env config for tools/api_v3 CLI scripts.

Env vars (same defaults as runtime/api_v3/auth_validator.py — SSOT alignment):
    API_V3_REDIS_HOST    (default 127.0.0.1)
    API_V3_REDIS_PORT    (default 6379)
    API_V3_REDIS_DB      (default 1)
    API_V3_REDIS_NS      (default v3_local)
    API_V3_REDIS_TIMEOUT_S (default 1.5)
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional, Tuple, cast

import redis as redis_lib

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6379
DEFAULT_DB = 1
DEFAULT_NAMESPACE = "v3_local"
DEFAULT_TIMEOUT_S = 1.5


def env_config() -> Tuple[str, int, int, str, float]:
    """Return (host, port, db, namespace, timeout_s) from env."""
    host = os.environ.get("API_V3_REDIS_HOST", DEFAULT_HOST)
    port = int(os.environ.get("API_V3_REDIS_PORT", DEFAULT_PORT))
    db = int(os.environ.get("API_V3_REDIS_DB", DEFAULT_DB))
    namespace = os.environ.get("API_V3_REDIS_NS", DEFAULT_NAMESPACE)
    timeout_s = float(os.environ.get("API_V3_REDIS_TIMEOUT_S", DEFAULT_TIMEOUT_S))
    return host, port, db, namespace, timeout_s


def get_redis() -> Tuple[redis_lib.Redis, str]:
    """Build a sync Redis client with decode_responses=True. Returns (client, namespace)."""
    host, port, db, namespace, timeout_s = env_config()
    client = redis_lib.Redis(
        host=host,
        port=port,
        db=db,
        socket_timeout=timeout_s,
        socket_connect_timeout=timeout_s,
        decode_responses=True,
    )
    return client, namespace


def parse_redis_json(raw: Any) -> Optional[dict]:
    """Narrow + parse a redis-py GET result into a JSON dict.

    redis-py 5.x sync+async type stubs union to Awaitable[Any] | Any, so
    sync-mode callers need an explicit isinstance guard before json.loads.
    Returns None on missing key, wrong type, or malformed JSON.
    """
    if raw is None:
        return None
    if not isinstance(raw, (str, bytes, bytearray)):
        return None
    try:
        payload = json.loads(cast("str | bytes | bytearray", raw))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload
