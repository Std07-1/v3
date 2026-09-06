"""runtime/agent_bridge/app.py — окремий процес адаптера для зовнішнього AI-клієнта (ADR-0090 S1).

Слухає 127.0.0.1:agent_bridge.port (default 8010); nginx приватних hostname'ів (archi/gorn)
проксює сюди /api/archi/*, /api/agent/*. Платформа (ws_server :8000) цих маршрутів не має.
Запуск: `python -m runtime.agent_bridge [--config config.json] [--host H] [--port P]`.
Вимкнений bridge (`agent_bridge.enabled=false` без env-override) НЕ виходить: він слухає порт і
віддає лише `GET /api/bridge/health` з `enabled:false`, приватні маршрути не монтуються (404).
Так supervisor бачить чесний RUNNING без BACKOFF/FATAL (exit до startsecs = «failed start»).
"""
from __future__ import annotations

import argparse
import logging
import time
from typing import Any, Mapping, Optional

from aiohttp import web

from core.config_loader import load_system_config, resolve_config_path
from runtime.agent_bridge.config import AGENT_BRIDGE_ENABLED_ENV, resolve_agent_bridge_config
from runtime.agent_bridge.context import BridgeContext, _UNSET, build_bridge_context
from runtime.agent_bridge.routes_console import register_console_routes
from runtime.agent_bridge.routes_ochi import register_ochi_routes

_log = logging.getLogger("agent_bridge")

BRIDGE_CTX = web.AppKey("bridge_ctx", object)
SERVICE_NAME = "agent_bridge"


def build_bridge_app(
    full_cfg: Mapping[str, Any],
    *,
    redis_client: Any = _UNSET,
    environ: Optional[Mapping[str, str]] = None,
) -> web.Application:
    """Зібрати aiohttp-app bridge. Health без auth; решта — Bearer (ADR-0076)."""
    ctx = build_bridge_context(full_cfg, redis_client=redis_client, environ=environ)
    app = web.Application()
    app[BRIDGE_CTX] = ctx

    async def _health(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "service": SERVICE_NAME,
                "enabled": ctx.cfg.enabled,
                "console": ctx.cfg.console_enabled,
                "redis": ctx.redis is not None,
                "server_ts_ms": int(time.time() * 1000),
            }
        )

    app.router.add_get("/api/bridge/health", _health)
    if not ctx.cfg.enabled:
        # I5: вимкнено = кажемо один раз і лишаємось живими лише з health (supervisor RUNNING)
        _log.info(
            "AGENT_BRIDGE_DISABLED: agent_bridge.enabled=false (source=%s) -> only /api/bridge/health "
            "is served; set env %s=1 to enable console/agent routes on this host",
            ctx.cfg.enabled_source,
            AGENT_BRIDGE_ENABLED_ENV,
        )
        return app
    register_console_routes(app, ctx)
    register_ochi_routes(app, ctx)
    _log.info(
        "AGENT_BRIDGE_ROUTES: console=%s redis=%s data_dir_set=%s routes=%d",
        ctx.cfg.console_enabled,
        ctx.redis is not None,
        bool(ctx.data_dir),
        len(app.router.routes()),
    )
    return app


def bridge_context(app: web.Application) -> BridgeContext:
    return app[BRIDGE_CTX]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="agent bridge (ADR-0090)")
    parser.add_argument("--config", type=str, default="config.json")
    parser.add_argument("--host", type=str, default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    full_cfg = load_system_config(resolve_config_path(args.config))
    cfg = resolve_agent_bridge_config(full_cfg)
    host = args.host or cfg.host
    port = args.port or cfg.port
    _log.info(
        "AGENT_BRIDGE starting host=%s port=%s enabled=%s enabled_source=%s",
        host,
        port,
        cfg.enabled,
        cfg.enabled_source,
    )
    web.run_app(build_bridge_app(full_cfg), host=host, port=port, access_log=None, print=None, reuse_address=True)
    return 0
