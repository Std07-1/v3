"""runtime/agent_bridge/routes_console.py — консоль зовнішнього AI-клієнта (ADR-025 у trader-v3).

Перенесено з runtime/ws/ws_server.py verbatim (ADR-0090 S1, move-only): /api/agent/state|feed
(ADR-012 observability) + 13 маршрутів /api/archi/* (thinking, directives, feed, SSE stream,
relationship, chat, logs, owner-note, proposals). Writes у файли клієнта (owner-note,
proposals/review) і в Redis (chat → archi:web_inbox) лишаються як є до S6 (→ проксі до бота).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from aiohttp import web

from runtime.agent_bridge.context import BridgeContext
from runtime.agent_bridge.thinking_archive import read_thinking_records

_log = logging.getLogger("agent_bridge.console")


def register_console_routes(app: web.Application, ctx: BridgeContext) -> None:
    """Змонтувати agent-observability + консольні маршрути. /api/agent/* — завжди (auth fail-closed
    при вимкненій консолі, як у ws_server); /api/archi/* — лише при console.enabled."""
    # Аліаси на closure-локали build_app (ws_server.py) — тіла handler'ів нижче перенесені
    # verbatim (ADR-0090 S1 move-only); рефакторинг імен — S5/S6.
    _archi_auth = ctx.authorize
    _agent_redis_client = ctx.redis
    _agent_ns = ctx.ns
    _console_data_dir = ctx.data_dir
    _console_thinking_max = ctx.cfg.console.thinking_max_items
    _console_feed_max = ctx.cfg.console.feed_max_items
    _read_thinking_records = read_thinking_records

    async def _api_agent_state(request: web.Request) -> web.Response:
        """GET /api/agent/state вЂ” latest agent state snapshot."""
        if not _archi_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if _agent_redis_client is None:
            return web.json_response(
                {"error": "agent_redis_not_configured"}, status=503
            )
        try:
            raw = _agent_redis_client.hgetall(f"{_agent_ns}:agent:state")
            if not raw:
                return web.json_response({"status": "no_data"}, status=204)
            return web.json_response(raw)
        except Exception as e:
            _log.warning("API_AGENT_STATE_FAIL: %s", e)
            return web.json_response({"error": "redis_read_failed"}, status=503)

    async def _api_agent_feed(request: web.Request) -> web.Response:
        """GET /api/agent/feed?limit=50 вЂ” chronological event log."""
        if not _archi_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if _agent_redis_client is None:
            return web.json_response(
                {"error": "agent_redis_not_configured"}, status=503
            )
        try:
            limit = min(int(request.query.get("limit", "50")), 500)
            raw_items = _agent_redis_client.lrange(
                f"{_agent_ns}:agent:feed", 0, limit - 1
            )
            events = []
            for item in list(raw_items):  # type: ignore[arg-type]
                try:
                    events.append(json.loads(item))
                except (json.JSONDecodeError, TypeError):
                    continue
            return web.json_response({"events": events, "total": len(events)})
        except Exception as e:
            _log.warning("API_AGENT_FEED_FAIL: %s", e)
            return web.json_response({"error": "redis_read_failed"}, status=503)

    async def _api_archi_thinking(request: web.Request) -> web.Response:
        """GET /api/archi/thinking?limit=50&offset=0 вЂ” Thinking Archive.

        Reads across the live archive + all rotated ``v3_thinking_archive_*``
        files (newest-first) so history survives rotation (ADR-018). Thin
        wrapper over the pure ``_read_thinking_records`` helper.
        """
        if not _archi_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if not _console_data_dir:
            return web.json_response({"error": "data_dir_not_configured"}, status=503)
        try:
            limit = min(int(request.query.get("limit", "50")), _console_thinking_max)
            offset = max(0, int(request.query.get("offset", "0")))
            page, total = _read_thinking_records(_console_data_dir, limit, offset)
            return web.json_response(
                {"entries": page, "total": total, "offset": offset, "limit": limit}
            )
        except Exception as _e:
            _log.warning("API_ARCHI_THINKING_FAIL: %s", _e)
            return web.json_response({"error": "read_failed"}, status=503)

    async def _api_archi_directives(request: web.Request) -> web.Response:
        """GET /api/archi/directives вЂ” agent directives snapshot."""
        if not _archi_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if not _console_data_dir:
            return web.json_response({"error": "data_dir_not_configured"}, status=503)
        import os as _os

        fpath = _os.path.join(_console_data_dir, "v3_agent_directives.json")
        try:
            if not _os.path.exists(fpath):
                return web.json_response({"error": "no_data"}, status=204)
            with open(fpath, "r", encoding="utf-8") as _fh:
                data = json.loads(_fh.read())
            # Return only safe/display fields вЂ” strip inner_thought if requested
            brief = request.query.get("brief", "0") == "1"
            if brief:
                safe_keys = [
                    "mode",
                    "focus_symbol",
                    "active_scenario",
                    "mood",
                    "inner_thought",
                    "bias_map",
                    "market_mental_model",
                    "token_usage_today",
                    "kill_switch_active",
                    "economy_mode_active",
                ]
                data = {k: data[k] for k in safe_keys if k in data}
            return web.json_response(data)
        except Exception as _e:
            _log.warning("API_ARCHI_DIRECTIVES_FAIL: %s", _e)
            return web.json_response({"error": "read_failed"}, status=503)

    async def _api_archi_feed(request: web.Request) -> web.Response:
        """GET /api/archi/feed?limit=50 вЂ” event feed with auth."""
        if not _archi_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if _agent_redis_client is None:
            return web.json_response({"error": "redis_not_available"}, status=503)
        try:
            limit = min(int(request.query.get("limit", "50")), _console_feed_max)
            raw_items = _agent_redis_client.lrange(
                f"{_agent_ns}:agent:feed", 0, limit - 1
            )
            events = []
            for item in list(raw_items):  # type: ignore[arg-type]
                try:
                    events.append(json.loads(item))
                except (json.JSONDecodeError, TypeError):
                    continue
            return web.json_response({"events": events, "total": len(events)})
        except Exception as _e:
            _log.warning("API_ARCHI_FEED_FAIL: %s", _e)
            return web.json_response({"error": "redis_read_failed"}, status=503)

    async def _api_archi_stream(request: web.Request) -> web.StreamResponse:
        """GET /api/archi/stream вЂ” SSE stream: feed events + directives changes."""
        if not _archi_auth(request):
            return web.Response(status=401)  # type: ignore[return-value]
        import os as _os
        import asyncio as _asyncio

        resp = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Access-Control-Allow-Origin": "*",
            }
        )
        await resp.prepare(request)

        loop = _asyncio.get_event_loop()
        ns = _agent_ns
        redis_cl = _agent_redis_client
        last_len: int = 0
        last_dir_mtime: float = 0.0

        # send initial keep-alive
        await resp.write(b": connected\n\n")

        try:
            while True:
                # в”Ђв”Ђ Check feed (Redis LIST) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
                if redis_cl is not None:
                    try:
                        curr_len: int = await loop.run_in_executor(
                            None, lambda: redis_cl.llen(f"{ns}:agent:feed")  # type: ignore[union-attr]
                        )
                        if curr_len > last_len:
                            n_new = curr_len - last_len
                            # Get only the new items (index 0 = newest in LPUSH list)
                            _n = n_new
                            new_raw = await loop.run_in_executor(
                                None, lambda: redis_cl.lrange(f"{ns}:agent:feed", 0, _n - 1)  # type: ignore[union-attr]
                            )
                            for raw_item in reversed(list(new_raw)):  # oldest first
                                try:
                                    ev = json.loads(raw_item)
                                    payload = json.dumps({"type": "feed", "data": ev})
                                    await resp.write(f"data: {payload}\n\n".encode())
                                except (json.JSONDecodeError, TypeError):
                                    pass
                            last_len = curr_len
                    except Exception as _e:
                        _log.debug("ARCHI_STREAM_REDIS_ERR: %s", _e)

                # в”Ђв”Ђ Check directives file mtime в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
                if _console_data_dir:
                    dir_path = _os.path.join(
                        _console_data_dir, "v3_agent_directives.json"
                    )
                    try:
                        mtime = (
                            _os.path.getmtime(dir_path)
                            if _os.path.exists(dir_path)
                            else 0.0
                        )
                        if mtime > last_dir_mtime:
                            last_dir_mtime = mtime
                            with open(dir_path, "r", encoding="utf-8") as _fh:
                                raw_dir = json.loads(_fh.read())
                            safe_keys = [
                                "mode",
                                "focus_symbol",
                                "active_scenario",
                                "mood",
                                "inner_thought",
                                "token_usage_today",
                                "kill_switch_active",
                                "economy_mode_active",
                            ]
                            brief_dir = {
                                k: raw_dir[k] for k in safe_keys if k in raw_dir
                            }
                            await resp.write(
                                f"data: {json.dumps({'type': 'directives', 'data': brief_dir})}\n\n".encode()
                            )
                    except Exception as _e:
                        _log.debug("ARCHI_STREAM_DIR_ERR: %s", _e)

                # в”Ђв”Ђ Keep-alive comment every 2s в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
                await resp.write(b": ping\n\n")
                await _asyncio.sleep(2)

        except (ConnectionResetError, _asyncio.CancelledError, Exception):
            pass

        return resp

    async def _api_archi_relationship(request: web.Request) -> web.Response:
        """GET /api/archi/relationship вЂ” relationship memo snapshot."""
        if not _archi_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if not _console_data_dir:
            return web.json_response({"error": "data_dir_not_configured"}, status=503)
        import os as _os

        fpath = _os.path.join(_console_data_dir, "v3_relationship_memo.json")
        try:
            if not _os.path.exists(fpath):
                return web.json_response({"error": "no_data"}, status=204)
            with open(fpath, "r", encoding="utf-8") as _fh:
                data = json.loads(_fh.read())
            return web.json_response(data)
        except Exception as _e:
            _log.warning("API_ARCHI_RELATIONSHIP_FAIL: %s", _e)
            return web.json_response({"error": "read_failed"}, status=503)

    # в”Ђв”Ђ /api/archi/chat вЂ” unified chat (proxy to bot via Redis IPC) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    _ARCHI_CHAT_KEY = f"{_agent_ns}:archi:chat"
    _ARCHI_CHAT_MAX = 500
    _ARCHI_WEB_INBOX_KEY = f"{_agent_ns}:archi:web_inbox"

    async def _api_archi_chat_post(request: web.Request) -> web.Response:
        """POST /api/archi/chat вЂ” saves user message and pushes to bot inbox.

        Bot process picks up from web_inbox, calls Claude with full personality,
        and writes reply to the same chat key. Frontend polls for reply.
        """
        if not _archi_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if _agent_redis_client is None:
            return web.json_response({"error": "redis_not_available"}, status=503)
        try:
            import time as _time

            body = await request.json()
            msg_text = str(body.get("message", "")).strip()
            if not msg_text:
                return web.json_response({"error": "empty_message"}, status=400)
            now_ms = int(_time.time() * 1000)
            user_msg = {
                "id": f"u_{now_ms}",
                "role": "user",
                "text": msg_text,
                "ts_ms": now_ms,
                "source": "web",
            }
            # Save to chat history (visible immediately in GET)
            _agent_redis_client.lpush(
                _ARCHI_CHAT_KEY, json.dumps(user_msg, ensure_ascii=False)
            )
            _agent_redis_client.ltrim(_ARCHI_CHAT_KEY, 0, _ARCHI_CHAT_MAX - 1)

            # Push to bot inbox for processing (bot will call Claude)
            inbox_msg = {
                "req_id": user_msg["id"],
                "text": msg_text,
                "ts_ms": now_ms,
                "source": "web",
            }
            _agent_redis_client.rpush(
                _ARCHI_WEB_INBOX_KEY, json.dumps(inbox_msg, ensure_ascii=False)
            )

            return web.json_response({"ok": True, "message": user_msg, "pending": True})
        except Exception as _e:
            _log.warning("API_ARCHI_CHAT_POST_FAIL: %s", _e)
            return web.json_response({"error": "write_failed"}, status=503)

    async def _api_archi_chat_get(request: web.Request) -> web.Response:
        """GET /api/archi/chat?limit=50 вЂ” chat history (oldest first)."""
        if not _archi_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if _agent_redis_client is None:
            return web.json_response({"error": "redis_not_available"}, status=503)
        try:
            limit = min(int(request.query.get("limit", "50")), 200)
            raw_items = _agent_redis_client.lrange(_ARCHI_CHAT_KEY, 0, limit - 1)
            messages: list = []
            for item in list(raw_items):
                try:
                    messages.append(json.loads(item))
                except (json.JSONDecodeError, TypeError):
                    continue
            messages.reverse()  # chronological order (oldest first)
            return web.json_response({"messages": messages, "total": len(messages)})
        except Exception as _e:
            _log.warning("API_ARCHI_CHAT_GET_FAIL: %s", _e)
            return web.json_response({"error": "redis_read_failed"}, status=503)

    # ── ADR-0053 S3: /api/archi/chat/stream — fake-stream final reply over SSE ──
    #
    # Option A per product discussion 2026-04-20: the bot still produces a final
    # reply via non-streaming Claude call (see trader-v3/bot/agent/core.py); this
    # endpoint waits for that reply in Redis, then re-emits it to the browser as
    # a pacing SSE so the user sees a real typing effect without any changes in
    # the bot's hot path.
    #
    # Contract:
    #   GET /api/archi/chat/stream?after_id=<user_msg_id>&token=<tok>&timeout=120
    #   Events:
    #     start   — metadata {id, ts_ms}
    #     delta   — {"text": "..."} chunks with 25–45 ms pacing
    #     done    — final marker (full text already accumulated)
    #     timeout — no archi reply within window
    #     error   — unrecoverable (closes stream)
    #
    # Degraded-but-loud (I7): the UI MUST keep its existing fast-poll as the
    # source of truth for message history. This endpoint is a UX overlay, not
    # the authoritative message channel — if it drops, fast-poll picks up the
    # reply on the next tick.
    async def _api_archi_chat_stream(
        request: web.Request,
    ) -> web.StreamResponse:
        if not _archi_auth(request):
            return web.Response(status=401)  # type: ignore[return-value]
        if _agent_redis_client is None:
            return web.Response(status=503)  # type: ignore[return-value]

        import asyncio as _asyncio
        import time as _time

        after_id = str(request.query.get("after_id", "")).strip()
        try:
            timeout_s = max(5, min(240, int(request.query.get("timeout", "120"))))
        except (TypeError, ValueError):
            timeout_s = 120

        resp = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Access-Control-Allow-Origin": "*",
            }
        )
        await resp.prepare(request)
        await resp.write(b": connected\n\n")

        async def _send(event: str, payload: dict) -> None:
            line = (
                f"event: {event}\n"
                f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            )
            await resp.write(line.encode("utf-8"))

        loop = _asyncio.get_event_loop()
        redis_cl = _agent_redis_client
        deadline = _time.time() + timeout_s

        # Anchor: read the user msg's ts_ms so "reply comes after user" has a clean
        # ordering even if the bot is fast enough to reply before the stream opens.
        after_ts_ms: int = 0
        try:
            raw_hist = await loop.run_in_executor(
                None, lambda: redis_cl.lrange(_ARCHI_CHAT_KEY, 0, 50)  # type: ignore[union-attr]
            )
            for item in list(raw_hist):
                try:
                    m = json.loads(item)
                    if m.get("id") == after_id:
                        after_ts_ms = int(m.get("ts_ms", 0))
                        break
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
        except Exception as _e:
            _log.debug("ARCHI_CHAT_STREAM_ANCHOR_ERR: %s", _e)

        if after_ts_ms == 0:
            # Unknown user message — fall back to "any archi msg newer than now"
            after_ts_ms = int(_time.time() * 1000) - 1

        reply: dict | None = None

        try:
            while _time.time() < deadline:
                # Poll the newest ~40 list entries for an archi reply newer than anchor.
                try:
                    raw = await loop.run_in_executor(
                        None, lambda: redis_cl.lrange(_ARCHI_CHAT_KEY, 0, 40)  # type: ignore[union-attr]
                    )
                except Exception as _e:
                    _log.debug("ARCHI_CHAT_STREAM_POLL_ERR: %s", _e)
                    raw = []

                for item in list(raw):
                    try:
                        m = json.loads(item)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if m.get("role") != "archi":
                        continue
                    try:
                        ts = int(m.get("ts_ms", 0))
                    except (TypeError, ValueError):
                        ts = 0
                    if ts > after_ts_ms:
                        reply = m
                        break

                if reply is not None:
                    break

                # Keep-alive so proxies don't close the idle connection.
                await resp.write(b": ping\n\n")
                await _asyncio.sleep(0.4)

            if reply is None:
                await _send("timeout", {"after_id": after_id})
                return resp

            text = str(reply.get("text", ""))
            await _send(
                "start",
                {
                    "id": reply.get("id"),
                    "ts_ms": reply.get("ts_ms"),
                    "role": "archi",
                    "length": len(text),
                },
            )

            # Split into pacing chunks: whitespace-preserving groups of ~8–16 chars.
            chunks: list[str] = []
            buf = ""
            for ch in text:
                buf += ch
                if len(buf) >= 10 and ch in (" ", "\n", "\t", ",", ".", "!", "?", ";"):
                    chunks.append(buf)
                    buf = ""
            if buf:
                chunks.append(buf)
            if not chunks:
                chunks = [text]

            # Target total animation: ~1.8s typical, cap at 3.5s, floor at 0.5s.
            total_target = min(3.5, max(0.5, 0.02 * len(chunks)))
            per_chunk = max(0.015, min(0.06, total_target / max(1, len(chunks))))

            for chunk in chunks:
                await _send("delta", {"text": chunk})
                await _asyncio.sleep(per_chunk)

            await _send("done", {"id": reply.get("id")})
        except (ConnectionResetError, _asyncio.CancelledError):
            # Client gone — that's fine, fast-poll on the UI side handles completion.
            pass
        except Exception as _e:
            _log.warning("ARCHI_CHAT_STREAM_FAIL: %s", _e)
            try:
                await _send("error", {"reason": "internal"})
            except Exception:
                pass

        return resp

    # ── ADR-0053 S4: /api/archi/chat/react — hover-reactions → feedback stream ──
    #
    # UX layer reactions (👍/📌/⭐) тепер публікуються у Redis XADD stream
    # `{ns}:feedback:chat`. Бот може консумити цей stream як training signal:
    # які репліки резонують з юзером, які — ні. Shape:
    #   XADD {ns}:feedback:chat MAXLEN ~ 5000 *
    #     msg_id <id> type <like|pin|star> action <add|remove>
    #     ts_ms <ms> source web user <token_hint>
    #
    # Degraded-but-loud: якщо Redis падає — 503 у відповіді, клієнт НЕ
    # rollback-ить оптимістичний toggle (localStorage лишається SSOT UX).
    # Тобто користувач не бачить стрибків іконки, але бот міг не отримати сигнал.
    _ARCHI_FEEDBACK_KEY = f"{_agent_ns}:feedback:chat"
    _ARCHI_FEEDBACK_MAXLEN = 5000
    _ALLOWED_REACTIONS = ("like", "pin", "star")
    _ALLOWED_ACTIONS = ("add", "remove")

    async def _api_archi_chat_react(request: web.Request) -> web.Response:
        """POST /api/archi/chat/react — publish a reaction to feedback:chat."""
        if not _archi_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if _agent_redis_client is None:
            return web.json_response({"error": "redis_not_available"}, status=503)
        try:
            import time as _time

            body = await request.json()
            msg_id = str(body.get("msg_id", "")).strip()
            rtype = str(body.get("type", "")).strip().lower()
            action = str(body.get("action", "add")).strip().lower()
            if not msg_id or len(msg_id) > 64:
                return web.json_response({"error": "bad_msg_id"}, status=400)
            if rtype not in _ALLOWED_REACTIONS:
                return web.json_response({"error": "bad_type"}, status=400)
            if action not in _ALLOWED_ACTIONS:
                return web.json_response({"error": "bad_action"}, status=400)

            fields = {
                "msg_id": msg_id,
                "type": rtype,
                "action": action,
                "ts_ms": str(int(_time.time() * 1000)),
                "source": "web",
            }
            entry_id = _agent_redis_client.xadd(
                _ARCHI_FEEDBACK_KEY,
                fields,
                maxlen=_ARCHI_FEEDBACK_MAXLEN,
                approximate=True,
            )
            return web.json_response({"ok": True, "entry_id": str(entry_id)})
        except json.JSONDecodeError:
            return web.json_response({"error": "bad_json"}, status=400)
        except Exception as _e:
            _log.warning("API_ARCHI_CHAT_REACT_FAIL: %s", _e)
            return web.json_response({"error": "write_failed"}, status=503)

    # в”Ђв”Ђ /api/archi/logs вЂ” read bot supervisor log from data_dir в”Ђв”Ђ
    async def _api_archi_logs(request: web.Request) -> web.Response:
        """GET /api/archi/logs?lines=50&level=all вЂ” read recent bot log lines."""
        if not _archi_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        lines_limit = min(int(request.query.get("lines", "80")), 500)
        level_filter = request.query.get("level", "all").upper()
        # Try multiple log locations
        _log_candidates = [
            os.path.join(_console_data_dir, "..", "logs", "supervisor.log"),
            "/var/log/smc-v3/smc_trader_v3.stderr.log",
            os.path.join(_console_data_dir, "..", "logs", "bot.log"),
        ]
        log_path = None
        for _c in _log_candidates:
            _norm = os.path.normpath(_c)
            if os.path.isfile(_norm):
                log_path = _norm
                break
        if not log_path:
            return web.json_response(
                {"lines": [], "source": "none", "error": "no_log_file_found"},
            )
        try:
            # Read last N lines efficiently (tail)
            import collections

            result_lines: list[dict[str, str]] = []
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                tail = collections.deque(
                    f, maxlen=lines_limit * 3
                )  # over-read for filter
            for raw_line in tail:
                raw_line = raw_line.rstrip("\n")
                if not raw_line:
                    continue
                # Detect level
                line_level = "INFO"
                for _lv in ("ERROR", "WARN", "WARNING", "DEBUG", "CRITICAL"):
                    if _lv in raw_line:
                        line_level = (
                            "ERROR"
                            if _lv in ("ERROR", "CRITICAL")
                            else ("WARN" if _lv in ("WARN", "WARNING") else _lv)
                        )
                        break
                if level_filter != "ALL" and line_level != level_filter:
                    continue
                result_lines.append({"text": raw_line, "level": line_level})
            # Keep only last N after filtering
            result_lines = result_lines[-lines_limit:]
            return web.json_response(
                {
                    "lines": result_lines,
                    "source": os.path.basename(log_path),
                    "total": len(result_lines),
                },
            )
        except Exception as e:
            _log.warning("API_ARCHI_LOGS_FAIL: %s", e)
            return web.json_response({"error": str(e), "lines": []}, status=500)

    # в”Ђв”Ђ /api/archi/owner-note вЂ” user status note Archi can read в”Ђв”Ђ
    async def _api_archi_owner_note_get(request: web.Request) -> web.Response:
        """GET /api/archi/owner-note вЂ” read owner's note for Archi."""
        if not _archi_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        note_path = os.path.join(_console_data_dir, "owner_note.json")
        try:
            if os.path.isfile(note_path):
                with open(note_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return web.json_response(data)
            return web.json_response(
                {"text": "", "mood": "", "status": "", "updated_at": ""}
            )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _api_archi_owner_note_post(request: web.Request) -> web.Response:
        """POST /api/archi/owner-note вЂ” save owner's note."""
        if not _archi_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            body = await request.json()
            note_data = {
                "text": str(body.get("text", ""))[:500],
                "mood": str(body.get("mood", ""))[:50],
                "status": str(body.get("status", ""))[:100],
                "updated_at": int(time.time() * 1000),
            }
            note_path = os.path.join(_console_data_dir, "owner_note.json")
            import tempfile

            _tmp_fd, _tmp_path = tempfile.mkstemp(
                dir=os.path.dirname(note_path), suffix=".tmp"
            )
            try:
                with os.fdopen(_tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(note_data, f, ensure_ascii=False)
                os.replace(_tmp_path, note_path)
            except BaseException:
                try:
                    os.unlink(_tmp_path)
                except OSError:
                    pass
                raise
            return web.json_response({"ok": True, **note_data})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # в”Ђв”Ђ /api/archi/proposals/review вЂ” ADR-028 P3 J5 approval в”Ђв”Ђ
    async def _api_archi_proposals_review(request: web.Request) -> web.Response:
        """POST /api/archi/proposals/review вЂ” approve or reject a pending proposal.

        Body: {"id": "p<ts>", "approved": true|false}
        Reads v3_agent_directives.json, applies the decision, saves.
        """
        if not _archi_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if not _console_data_dir:
            return web.json_response({"error": "data_dir_not_configured"}, status=503)
        import os as _os

        directives_path = _os.path.join(_console_data_dir, "v3_agent_directives.json")
        try:
            body = await request.json()
            proposal_id = str(body.get("id", "")).strip()
            approved = bool(body.get("approved", False))
            if not proposal_id:
                return web.json_response({"error": "missing_id"}, status=400)
            if not _os.path.exists(directives_path):
                return web.json_response({"error": "no_directives"}, status=404)

            # Load в†’ patch в†’ save (atomic replace)
            with open(directives_path, "r", encoding="utf-8") as _fh:
                raw = json.loads(_fh.read())

            proposals = raw.get("improvement_proposals", [])
            found = False
            for p in proposals:
                if p.get("id") == proposal_id and p.get("status") == "pending":
                    p["status"] = "approved" if approved else "rejected"
                    p["resolved_at"] = time.time()
                    if approved and p.get("type") == "add_rule":
                        rule = str(p.get("proposed_rule", "")).strip()
                        current_rules: list = raw.get("operational_rules", [])
                        if rule and rule not in current_rules:
                            current_rules.append(rule)
                            raw["operational_rules"] = current_rules[-20:]
                    found = True
                    break

            if not found:
                return web.json_response(
                    {"error": "proposal_not_found_or_already_resolved"}, status=404
                )

            raw["improvement_proposals"] = proposals
            import tempfile

            _tmp_fd, _tmp_path = tempfile.mkstemp(
                dir=_os.path.dirname(directives_path), suffix=".tmp"
            )
            try:
                with _os.fdopen(_tmp_fd, "w", encoding="utf-8") as _wf:
                    json.dump(raw, _wf, ensure_ascii=False)
                _os.replace(_tmp_path, directives_path)
            except BaseException:
                try:
                    _os.unlink(_tmp_path)
                except OSError:
                    pass
                raise

            _log.info(
                "PROPOSALS_REVIEW: %s id=%s",
                "APPROVED" if approved else "REJECTED",
                proposal_id,
            )
            return web.json_response(
                {"ok": True, "id": proposal_id, "approved": approved}
            )
        except Exception as _e:
            _log.warning("API_ARCHI_PROPOSALS_REVIEW_FAIL: %s", _e)
            return web.json_response({"error": "failed"}, status=500)

    app.router.add_get("/api/agent/state", _api_agent_state)
    app.router.add_get("/api/agent/feed", _api_agent_feed)
    if ctx.cfg.console_enabled:
        app.router.add_get("/api/archi/thinking", _api_archi_thinking)
        app.router.add_get("/api/archi/directives", _api_archi_directives)
        app.router.add_get("/api/archi/feed", _api_archi_feed)
        app.router.add_get("/api/archi/stream", _api_archi_stream)
        app.router.add_get("/api/archi/relationship", _api_archi_relationship)
        app.router.add_post("/api/archi/chat", _api_archi_chat_post)
        app.router.add_get("/api/archi/chat", _api_archi_chat_get)
        app.router.add_get("/api/archi/chat/stream", _api_archi_chat_stream)
        app.router.add_post("/api/archi/chat/react", _api_archi_chat_react)
        app.router.add_get("/api/archi/logs", _api_archi_logs)
        app.router.add_get("/api/archi/owner-note", _api_archi_owner_note_get)
        app.router.add_post("/api/archi/owner-note", _api_archi_owner_note_post)
        app.router.add_post("/api/archi/proposals/review", _api_archi_proposals_review)
