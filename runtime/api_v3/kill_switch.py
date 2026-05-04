"""ADR-0059 §3.4 — Analysis API kill switch (slice 059.4).

Purpose
-------
Two independent layers protect the analysis surface (`/api/v3/bars/*` and
`/api/v3/smc/*`) from being served when the operator wants it dark. The
signals/bias/narrative/macro endpoints from ADR-0058 are NOT subject to
this switch — they remain available even when analysis is killed.

Layer 1 — Config (`api_v3.analysis_enabled`)
    Boot-time toggle in `config.json`. When `false`, every analysis request
    returns a structured 503 `analysis_disabled_config`. Requires restart
    to flip. Used for full deployment-level disable (e.g. before slice
    059.5 prompt-template review gate passes).

Layer 2 — Redis runtime flag (`{ns}:api_v3:analysis_kill`)
    Toggleable without restart via `python -m tools.api_v3.toggle_analysis`.
    When the key is present (any value), every analysis request returns
    503 `analysis_disabled_runtime`. CLI may set a TTL so the kill auto-
    expires; otherwise the flag is sticky until `--on`.

Fail-open by design (F-S1-002 — Opus audit revision A)
------------------------------------------------------
If Redis is unreachable while checking the runtime flag, the middleware
serves the request normally. Rationale: the kill switch is an operator
policy gate, NOT a security gate. The auth path (ADR-0058 §3.3) already
fails-closed when Redis is down (auth lookup raises → 503), so a Redis
outage cannot accidentally bypass authentication. Silently dropping
legitimate analysis traffic during a transient Redis blip would be worse
than serving a stale-but-valid response.

The fail-open path is loud:
    * `log.warning("api_v3_kill_switch_check_failed ...")` per call
    * Counter `api_v3_kill_switch_check_failed_total` (placeholder log
      until a Prometheus client is wired; alert on >10/min in slice 059.7)

Path matching
-------------
The middleware fires only for requests whose path starts with one of
`ANALYSIS_PATH_PREFIXES`. Everything else is passed straight through —
this keeps signals/* latency unchanged and lets us register the
middleware globally on the aiohttp Application.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Optional

from aiohttp import web
from redis.exceptions import RedisError

log = logging.getLogger("api_v3.kill_switch")

# Path prefixes that the kill switch governs. Signals/bias/narrative/macro
# from ADR-0058 stay outside this list intentionally — see module docstring.
ANALYSIS_PATH_PREFIXES: tuple[str, ...] = (
    "/api/v3/bars/",
    "/api/v3/smc/",
)


# Redis key shape (SSOT — toggle CLI MUST use the same builder).
def kill_flag_redis_key(namespace: str) -> str:
    """Compose the runtime kill-flag key for a given namespace."""
    return f"{namespace}:api_v3:analysis_kill"


# AppKey is bound after KillSwitch class definition (forward-ref avoidance).
# See bottom of file for `APP_KILL_SWITCH = web.AppKey(...)`.


# ────────────────────────────────────────────────────────────
# Envelope helper (mirrors endpoints._error_response shape)
# ────────────────────────────────────────────────────────────


# Imported lazily to avoid pulling endpoints.py at module import time
# (keeps unit tests fast and isolation tight).
def _build_kill_response(code: str, message: str) -> web.Response:
    from runtime.api_v3.endpoints import (  # local import — break cycle
        DISCLAIMER,
        SCHEMA_VERSION,
        _server_ts,
    )

    return web.json_response(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "error",
            "server_ts": _server_ts(),
            "disclaimer": DISCLAIMER,
            "data": {
                "code": code,
                "message": message,
            },
        },
        status=503,
    )


# ────────────────────────────────────────────────────────────
# KillSwitch service
# ────────────────────────────────────────────────────────────


class KillSwitch:
    """Owns the config flag + Redis runtime flag check.

    Stateless wrt requests — all state lives in config (immutable after
    boot) and Redis (shared with the toggle CLI). Thread-safe: each
    `is_killed()` call performs a single Redis EXISTS, no internal cache.
    """

    def __init__(
        self,
        *,
        redis_client: Any,
        namespace: str,
        analysis_enabled: bool,
    ) -> None:
        self._redis = redis_client
        self._namespace = namespace
        self._analysis_enabled = bool(analysis_enabled)
        # Counter (placeholder for Prometheus — wired in slice 059.7).
        self.fail_open_count = 0

    @property
    def analysis_enabled(self) -> bool:
        return self._analysis_enabled

    @property
    def redis_key(self) -> str:
        return kill_flag_redis_key(self._namespace)

    def is_killed(self) -> tuple[bool, Optional[str]]:
        """Return (killed?, reason_code). reason_code is the error envelope code.

        - (True, "analysis_disabled_config")  → config layer says off
        - (True, "analysis_disabled_runtime") → redis flag present
        - (False, None)                       → serve normally
        - Redis outage                        → (False, None) + warning + counter
        """
        if not self._analysis_enabled:
            return True, "analysis_disabled_config"
        try:
            present = bool(self._redis.exists(self.redis_key))
        except RedisError as exc:
            self.fail_open_count += 1
            log.warning(
                "api_v3_kill_switch_check_failed err=%s key=%s "
                "fail_open_count=%d (serving request — F-S1-002 fail-open by design)",
                exc,
                self.redis_key,
                self.fail_open_count,
            )
            return False, None
        if present:
            return True, "analysis_disabled_runtime"
        return False, None


# ────────────────────────────────────────────────────────────
# Middleware factory + wiring
# ────────────────────────────────────────────────────────────


@web.middleware
async def _analysis_kill_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """Block analysis paths when the switch is engaged; pass through otherwise.

    Order: this middleware MUST fire BEFORE auth (so we don't waste a Redis
    GET on the token store) but AFTER the audit middleware (so kill events
    are still recorded for forensics). Wiring in ws_server places it
    accordingly via `app.middlewares.append()` ordering.
    """
    path = request.path
    is_analysis = any(path.startswith(p) for p in ANALYSIS_PATH_PREFIXES)
    if not is_analysis:
        return await handler(request)
    switch: Optional[KillSwitch] = request.app.get(APP_KILL_SWITCH)
    if switch is None:
        # Safety net — if wiring is broken, log loudly and fail-open
        # (consistent with F-S1-002). This branch should never trigger
        # in production once register_kill_switch() runs at startup.
        log.warning(
            "api_v3_kill_switch_unconfigured path=%s — passing through (fail-open)",
            path,
        )
        return await handler(request)
    killed, reason = switch.is_killed()
    if killed and reason is not None:
        log.info("api_v3_analysis_blocked path=%s reason=%s", path, reason)
        return _build_kill_response(
            reason,
            "Analysis API is currently disabled by operator. "
            "Try again later or contact the platform owner.",
        )
    return await handler(request)


def register_kill_switch(
    app: web.Application,
    *,
    redis_client: Any,
    namespace: str,
    analysis_enabled: bool,
) -> KillSwitch:
    """Mount the kill switch on `app` and return the live KillSwitch instance.

    Idempotent at module level — second call would shadow the AppKey, so
    callers MUST invoke this exactly once during ws_server.create_app.

    Args:
        app: aiohttp Application to attach to.
        redis_client: redis-py sync client (decode_responses=True).
        namespace: Redis namespace prefix (e.g. "v3_local").
        analysis_enabled: Boot-time config layer (config.json:api_v3
            .analysis_enabled). False → analysis blocked until restart.

    Returns:
        The KillSwitch instance (also stashed at app[APP_KILL_SWITCH]).
    """
    switch = KillSwitch(
        redis_client=redis_client,
        namespace=namespace,
        analysis_enabled=analysis_enabled,
    )
    app[APP_KILL_SWITCH] = switch
    app.middlewares.append(_analysis_kill_middleware)
    log.info(
        "api_v3_kill_switch_registered ns=%s analysis_enabled=%s redis_key=%s",
        namespace,
        analysis_enabled,
        switch.redis_key,
    )
    return switch


# AppKey for stashing the live KillSwitch on the aiohttp Application.
# Defined after the class so we can pass the real type (mirrors the pattern
# in runtime/api_v3/endpoints.py for APP_TOKEN_STORE).
APP_KILL_SWITCH = web.AppKey("api_v3_kill_switch", KillSwitch)
