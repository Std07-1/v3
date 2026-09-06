"""runtime/agent_bridge/routes_ochi.py — «Очі Арчі»: /api/archi/wakes + /api/archi/now (ADR-0088).

Перенесено з runtime/ws/ws_server.py verbatim (ADR-0090 S1, move-only). Єдина змістовна
зміна: ціна для /now береться з Redis tick:last (ctx.last_price), а не з in-process SmcRunner.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

from aiohttp import web

from runtime.agent_bridge.context import BridgeContext
from runtime.agent_bridge.thinking_archive import read_thinking_records

_log = logging.getLogger("agent_bridge.ochi")


def register_ochi_routes(app: web.Application, ctx: BridgeContext) -> None:
    """Змонтувати /api/archi/wakes і /api/archi/now (лише при console.enabled)."""
    # Аліаси на closure-локали build_app (ws_server.py) — тіла handler'ів нижче перенесені
    # verbatim (ADR-0090 S1 move-only); рефакторинг імен — S5/S6.
    _archi_auth = ctx.authorize
    _agent_redis_client = ctx.redis
    _agent_ns = ctx.ns
    _console_data_dir = ctx.data_dir
    _console_thinking_max = ctx.cfg.console.thinking_max_items
    _console_feed_max = ctx.cfg.console.feed_max_items
    _read_thinking_records = read_thinking_records

    # ── /api/archi/wakes + /api/archi/now — «Очі Арчі» (ADR-0088) ──────────────
    # Серверний джойн приватних джерел трейдера у ГОТОВІ картки/знімок. UI = dumb
    # renderer (X28). Pure-логіка у runtime.agent_bridge.wake_cards; тут — лише тонкі handlers
    # (I/O: файли data_dir, Redis agent:state/thesis, ціна з Redis tick:last).
    from runtime.agent_bridge import wake_cards as _wake_cards

    # thinking-скан для джойна: wake_log cap = 200 (~10h); цей зріз архіву покриває
    # той самий діапазон часу. Старіші картки (глибша пагінація) деградують до
    # thinking:null — прийнятно (I5 graceful).
    _WAKE_THINKING_SCAN = 500

    def _read_directives_now(data_dir: str) -> Optional[Dict[str, Any]]:
        """Прочитати v3_agent_directives.json → dict; None якщо нема/битий (I5)."""
        import os as _os

        fpath = _os.path.join(data_dir, "v3_agent_directives.json")
        try:
            if not _os.path.exists(fpath):
                return None
            with open(fpath, "r", encoding="utf-8") as _fh:
                loaded = json.loads(_fh.read())
            return loaded if isinstance(loaded, dict) else None
        except Exception as _e:
            _log.warning("API_ARCHI_NOW_DIRECTIVES_FAIL: %s", _e)
            return None

    async def _api_archi_wakes(request: web.Request) -> web.Response:
        """GET /api/archi/wakes?limit=30&before_ts=… — кіноплівка пробуджень (ADR-0088).

        Keyset newest-first: ≤``limit`` карток з ``ts < before_ts``. Кожна картка =
        wake_log-запис verbatim + category/alert + trace|null + thinking|null.
        """
        if not _archi_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if not _console_data_dir:
            return web.json_response({"error": "data_dir_not_configured"}, status=503)
        try:
            limit = _wake_cards.clamp_wake_limit(request.query.get("limit"), default=30)
            before_ts: Optional[int] = None
            _bt_raw = request.query.get("before_ts")
            if _bt_raw:
                try:
                    before_ts = int(_bt_raw)
                except ValueError:
                    _log.debug("WAKES_BEFORE_TS_INVALID raw=%r", _bt_raw)
                    before_ts = None
            thinking_page, _ = _read_thinking_records(
                _console_data_dir, _WAKE_THINKING_SCAN, 0
            )
            wakes, total, oldest_ts = _wake_cards.read_wake_cards(
                _console_data_dir,
                limit,
                before_ts=before_ts,
                thinking_records=thinking_page,
            )
            return web.json_response(
                {"wakes": wakes, "total": total, "oldest_ts": oldest_ts}
            )
        except Exception as _e:
            _log.warning("API_ARCHI_WAKES_FAIL: %s", _e)
            return web.json_response({"error": "read_failed"}, status=503)

    async def _api_archi_now(request: web.Request) -> web.Response:
        """GET /api/archi/now?symbol=… — серверний знімок «стан зараз» (ADR-0088).

        Джойнить presence (agent:state), директиви (файл), тезу (Redis) та ціну
        (Redis tick:last, TTL ~30 с; поза сесією null + price_unavailable) → знімок з
        СЕРВЕРНИМИ delta/delta_pct армованих рівнів (X28).
        Недоступне джерело → поле null + запис у ``degraded[]``, HTTP 200 (I5).
        """
        if not _archi_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        now_ms = int(time.time() * 1000)
        degraded: list[str] = []

        directives: Optional[Dict[str, Any]] = None
        if _console_data_dir:
            directives = _read_directives_now(_console_data_dir)
            if directives is None:
                degraded.append("directives_unavailable")
        else:
            degraded.append("data_dir_not_configured")

        # Символ фокусу: ?symbol= (UI знає) → fallback wake_conditions.params.symbol.
        # agent:state/active_scenario символу не несуть (verified) — без нього ціна/
        # теза лишаються null (degraded), решта знімка віддається.
        symbol = request.query.get("symbol", "").strip()
        if not symbol and directives:
            for _wc in directives.get("wake_conditions") or []:
                _sym = (_wc.get("params") or {}).get("symbol")
                if _sym:
                    symbol = str(_sym)
                    break

        state: Optional[Dict[str, Any]] = None
        if _agent_redis_client is not None:
            try:
                state = _agent_redis_client.hgetall(f"{_agent_ns}:agent:state") or None
                if state is None:
                    degraded.append("state_no_data")
            except Exception as _e:
                _log.warning("API_ARCHI_NOW_STATE_FAIL: %s", _e)
                degraded.append("state_unavailable")
        else:
            degraded.append("redis_not_configured")

        thesis: Optional[Dict[str, Any]] = None
        if symbol and _agent_redis_client is not None:
            try:
                _sym_safe = symbol.replace("/", "_")
                thesis = (
                    _agent_redis_client.hgetall(f"{_agent_ns}:thesis:{_sym_safe}")
                    or None
                )
            except Exception as _e:
                _log.warning("API_ARCHI_NOW_THESIS_FAIL: %s", _e)
                degraded.append("thesis_unavailable")

        # ADR-0090 S1: bridge = окремий процес, SmcRunner недоступний. Ціна — з Redis
        # `{ns}:tick:last:{SYM}` (той самий ключ, що читає /api/context; TTL ~30 с) →
        # поза торговою сесією price=None + degraded price_unavailable (I5, не тихо).
        price: Optional[float] = None
        if symbol:
            try:
                price = ctx.last_price(symbol)
            except Exception as _e:
                _log.warning("API_ARCHI_NOW_PRICE_FAIL: %s", _e)
        if not symbol:
            degraded.append("symbol_unknown")
        elif price is None:
            degraded.append("price_unavailable")

        body = _wake_cards.build_now_view(
            symbol=symbol,
            state=state,
            directives=directives,
            thesis=thesis,
            price=price,
            now_ms=now_ms,
            degraded=degraded,
        )
        return web.json_response(body)

    if ctx.cfg.console_enabled:
        app.router.add_get("/api/archi/wakes", _api_archi_wakes)
        app.router.add_get("/api/archi/now", _api_archi_now)
