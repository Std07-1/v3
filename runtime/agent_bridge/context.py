"""runtime/agent_bridge/context.py — спільний контекст handler'ів bridge (ADR-0090 S1).

Замінює closure-локали build_app у ws_server.py: Redis-клієнт клієнтських ключів,
namespace, data_dir, auth (ADR-0076 check_bearer) і ціна з Redis tick:last.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from aiohttp import web

from runtime.agent_bridge.config import AgentBridgeConfig, resolve_agent_bridge_config
from runtime.api.auth import AuthConfig, check_bearer
from runtime.store.redis_keys import symbol_key

_log = logging.getLogger("agent_bridge.context")

REDIS_ROLE = "agent_bridge"
REDIS_CONNECT_TIMEOUT_S = 2
_UNSET = object()


@dataclass(frozen=True)
class BridgeContext:
    """Усе, що потрібно handler'ам; `redis=None` → handlers відповідають 503 (як у ws_server)."""

    cfg: AgentBridgeConfig
    redis: Optional[Any]
    ns: str
    auth: AuthConfig

    @property
    def data_dir(self) -> str:
        return self.cfg.data_dir

    def authorize(self, request: web.Request) -> bool:
        """True iff Bearer-заголовок або ?token= (лише для EventSource SSE) валідний (ADR-0076)."""
        allowed, _reason = check_bearer(
            request.headers.get("Authorization", ""),
            request.query.get("token", ""),
            self.auth,
        )
        return allowed

    def last_price(self, symbol: str) -> Optional[float]:
        """Ціна з Redis `{ns}:tick:last:{SYM}` (пише tick_publisher, TTL ~30 с); None = немає/протухла."""
        if self.redis is None:
            return None
        raw = self.redis.get(f"{self.ns}:tick:last:{symbol_key(symbol)}")
        if not raw:
            return None
        mid = float(json.loads(raw).get("mid", 0) or 0)
        return mid if mid > 0 else None


def build_bridge_context(
    full_cfg: Mapping[str, Any],
    *,
    redis_client: Any = _UNSET,
    environ: Optional[Mapping[str, str]] = None,
) -> BridgeContext:
    """Зібрати контекст з повного config.json. `redis_client` — інʼєкція для тестів."""
    cfg = resolve_agent_bridge_config(full_cfg, environ=environ)
    ns = str((full_cfg.get("redis") or {}).get("namespace") or "")
    redis = redis_client if redis_client is not _UNSET else _connect_redis(full_cfg)
    if redis is not None and redis_client is _UNSET:
        ns = _resolved_namespace(full_cfg, ns)
    token = cfg.console.resolve_token(environ)
    auth = AuthConfig(
        enabled=cfg.console_enabled,
        token=token,
        allow_no_token_dev_mode=cfg.console.allow_no_token_dev_mode,
    )
    if cfg.console_enabled and not token and not auth.allow_no_token_dev_mode:
        # I5: консоль увімкнена без токена = усі запити DENIED; кажемо це на старті, не мовчимо
        _log.error(
            "ARCHI_AUTH_MISCONFIG: agent_bridge.console enabled but no token configured "
            "(set env %s) -- ALL /api/agent/* and /api/archi/* requests will be DENIED",
            cfg.console.auth_token_env,
        )
    return BridgeContext(cfg=cfg, redis=redis, ns=ns, auth=auth)


def _connect_redis(full_cfg: Mapping[str, Any]) -> Optional[Any]:
    """Той самий рецепт, що AGENT_OBSERVABILITY у ws_server: resolve_redis_spec + sync redis.Redis."""
    try:
        import redis as _redis_mod
        from runtime.store.redis_spec import resolve_redis_spec

        spec = resolve_redis_spec(dict(full_cfg), role=REDIS_ROLE)
        if spec is None:
            raise ValueError("resolve_redis_spec returned None")
        client = _redis_mod.Redis(
            host=spec.host,
            port=spec.port,
            db=spec.db,
            socket_connect_timeout=REDIS_CONNECT_TIMEOUT_S,
            decode_responses=True,
        )
        _log.info("AGENT_BRIDGE_REDIS: wired ns=%s", spec.namespace)
        return client
    except Exception as exc:  # noqa: BLE001 — degraded-but-loud: 503 у handler'ах, не crash
        _log.warning("AGENT_BRIDGE_REDIS_UNAVAILABLE: %s -- endpoints will return 503", exc)
        return None


def _resolved_namespace(full_cfg: Mapping[str, Any], fallback: str) -> str:
    try:
        from runtime.store.redis_spec import resolve_redis_spec

        spec = resolve_redis_spec(dict(full_cfg), role=REDIS_ROLE)
        return str(spec.namespace) if spec is not None else fallback
    except Exception as exc:  # noqa: BLE001 — namespace з config як fallback, з логом
        _log.warning("AGENT_BRIDGE_NS_FALLBACK: %s -> ns=%r", exc, fallback)
        return fallback
