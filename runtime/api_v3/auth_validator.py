"""ADR-0058 — FastAPI auth validator sidecar (slice 058.1).

Purpose:
    nginx `auth_request /_auth` calls this service to validate `X-API-Key`
    on every incoming `/api/v3/*` request. This is the only authority for
    token validity — public endpoints in slice 058.2 trust nginx's verdict.

Endpoints:
    GET /health  → liveness probe (no auth, no Redis touch).
    GET /_auth   → validates X-API-Key header.
        200 + X-Consumer / X-Scope response headers — token valid.
        401 — missing header or unknown/invalid token.
        503 — Redis unavailable (fail-closed; nginx surfaces 502/503 to client).

Run (development):
    uvicorn runtime.api_v3.auth_validator:app --host 127.0.0.1 --port 8001

Production wiring (slice 058.3):
    supervisor mode `api_v3_auth` runs uvicorn; nginx auth_request points at
    http://127.0.0.1:8001/_auth; X-API-Key forwarded via `proxy_set_header`.

Invariants:
    I1 — read-only. No POST/PUT/DELETE handlers. No Redis writes.
    I5 — degraded-but-loud. Redis errors → 503 + log; never silent allow.
    F-S1-007 — only "read" scope honored (token_store enforces fail-closed).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import redis as redis_lib
from fastapi import FastAPI, Header, HTTPException, Response
from redis.exceptions import RedisError

from runtime.api_v3.token_store import TokenRecord, TokenStore

log = logging.getLogger("api_v3.auth")

# Configuration via environment (Redis target). Defaults match config.json
# `redis` section so local dev "just works" without env setup.
REDIS_HOST = os.environ.get("API_V3_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("API_V3_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("API_V3_REDIS_DB", "1"))
REDIS_NAMESPACE = os.environ.get("API_V3_REDIS_NS", "v3_local")
REDIS_SOCKET_TIMEOUT_S = float(os.environ.get("API_V3_REDIS_TIMEOUT_S", "2.0"))

# Lazy singletons — created on first request, allow tests to monkeypatch.
_redis_client: Optional[redis_lib.Redis] = None
_token_store: Optional[TokenStore] = None


def _get_store() -> TokenStore:
    """Lazy singleton. Built on first call so import doesn't require Redis."""
    global _redis_client, _token_store
    if _token_store is None:
        _redis_client = redis_lib.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            decode_responses=True,
            socket_timeout=REDIS_SOCKET_TIMEOUT_S,
            socket_connect_timeout=REDIS_SOCKET_TIMEOUT_S,
        )
        _token_store = TokenStore(_redis_client, REDIS_NAMESPACE)
    return _token_store


# OpenAPI/docs disabled: this service is server-internal (nginx-only client),
# advertising schema would only help attackers enumerate endpoints.
app = FastAPI(
    title="api_v3 auth validator",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health")
def health() -> dict:
    """Liveness probe. Does NOT touch Redis — for that, exercise /_auth."""
    return {"status": "ok", "service": "api_v3_auth"}


@app.get("/_auth")
def auth(
    response: Response,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> dict:
    """nginx auth_request handler. See module docstring for response codes."""
    if not x_api_key:
        # Header literally absent — never log token material on this branch.
        raise HTTPException(status_code=401, detail="missing_api_key")
    try:
        record: Optional[TokenRecord] = _get_store().lookup(x_api_key)
    except RedisError as err:
        # I5 / F4 — degraded-but-loud. Fail-closed: 503 forces nginx to deny.
        log.error("redis_unavailable err=%s", err)
        raise HTTPException(status_code=503, detail="auth_backend_unavailable")
    if record is None:
        # Invalid keys are normal background noise (port scans, expired tokens) —
        # log at INFO with prefix only. Never log full token (X1 — secrets).
        log.info("auth_denied key_prefix=%s", x_api_key[:8])
        raise HTTPException(status_code=401, detail="invalid_token")
    response.headers["X-Consumer"] = record.consumer
    response.headers["X-Scope"] = record.scope
    return {"status": "ok", "consumer": record.consumer, "scope": record.scope}
