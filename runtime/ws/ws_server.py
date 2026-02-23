"""
runtime/ws/ws_server.py — aiohttp WebSocket сервер для ui_v4.

P1: skeleton + heartbeat.
P2: UDS reader integration (full frame, switch, delta, scrollback).

Інваріанти:
  W0: WS-сервер = UDS reader only (role="reader")
  W1: schema_v = "ui_v4_v2" на кожному frame
  W2: meta.seq строго зростає per-connection
  W7: heartbeat кожні ≤30s

Запуск: python -m runtime.ws.ws_server --port 8000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, Optional

from aiohttp import web, WSMsgType

from runtime.ws.candle_map import map_bars_to_candles_v4
from core.config_loader import (
    load_system_config, resolve_config_path,
    tf_allowlist_from_cfg, preview_tf_allowlist_from_cfg,
)

_log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────
SCHEMA_V = "ui_v4_v2"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_HEARTBEAT_S = 30
DEFAULT_DELTA_POLL_S = 1.0
DEFAULT_COLD_START_BARS = 300

# P11: scrollback disk rails
SCROLLBACK_MAX_STEPS = 6          # макс чанків scrollback per session per symbol+tf
SCROLLBACK_COOLDOWN_S = 0.5       # мінімальний інтервал між scrollback від одного клієнта

# TF label ↔ seconds mapping (types.ts WsAction.switch.tf)
# Canonical labels: uppercase M1, M5, H1 etc. (як у фронтенді SymbolTfPicker)
_TF_CANONICAL_LABELS: Dict[str, int] = {
    "M1": 60, "M3": 180, "M5": 300, "M15": 900,
    "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400,
}
# Case-insensitive lookup: accept both "M15" and "15m" and "m15"
_TF_LABEL_TO_S: Dict[str, int] = {}
for _lbl, _sec in _TF_CANONICAL_LABELS.items():
    _TF_LABEL_TO_S[_lbl] = _sec         # M15
    _TF_LABEL_TO_S[_lbl.lower()] = _sec  # m15
# Also support legacy lowercase like "15m", "1h", "4h", "1d"
_TF_LABEL_TO_S.update({
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400,
})
_TF_S_TO_LABEL: Dict[int, str] = {v: k for k, v in _TF_CANONICAL_LABELS.items()}

# ── Helpers ────────────────────────────────────────────

def _load_full_config(config_path: str) -> Dict[str, Any]:
    """Завантажує повний config.json через core.config_loader (T10/S26 SSOT)."""
    try:
        resolved = resolve_config_path(config_path)
        return load_system_config(resolved)
    except Exception as exc:
        _log.warning("WS_CONFIG load_error=%s path=%s", exc, config_path)
        return {}


def _canonicalize_symbol(raw: str, symbols: set) -> str:
    """Нормалізує символ: EUR_USD → EUR/USD."""
    if raw in symbols:
        return raw
    if "_" in raw:
        canon = raw.replace("_", "/")
        if canon in symbols:
            return canon
    return raw


def _cold_start_limit(tf_s: int, cfg: Dict[str, Any]) -> int:
    """Повертає кількість барів для cold start по TF."""
    bootstrap = cfg.get("bootstrap", {})
    cold_map = bootstrap.get("ui_cold_start_bars_by_tf", {})
    raw = cold_map.get(str(tf_s))
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return DEFAULT_COLD_START_BARS


# ── Output Guard (T6/S19: WS candle shape + monotonicity) ──────

def _guard_candle_shape(candle: dict) -> Optional[str]:
    """Валідує один v4 Candle dict. Повертає issue string або None якщо OK.

    Контракт: types.ts Candle {t_ms: int, o: float, h: float, l: float, c: float, v: float}
    """
    if not isinstance(candle, dict):
        return "candle_not_dict"
    t = candle.get("t_ms")
    if not isinstance(t, (int, float)) or t <= 0:
        return "candle_bad_t_ms"
    for field in ("o", "h", "l", "c"):
        val = candle.get(field)
        if not isinstance(val, (int, float)):
            return "candle_%s_not_number" % field
        if val != val:  # NaN check (x != x iff NaN)
            return "candle_%s_nan" % field
    v = candle.get("v")
    if v is not None and not isinstance(v, (int, float)):
        return "candle_v_not_number"
    # OHLC sanity: high >= low
    h_val, l_val = candle.get("h"), candle.get("l")
    if isinstance(h_val, (int, float)) and isinstance(l_val, (int, float)):
        if h_val < l_val:
            return "candle_h_lt_l"
    return None


def _guard_candles_output(
    candles: list,
    symbol: str,
    tf_label: str,
    frame_type: str,
) -> list:
    """Output guard для масиву candles перед відправкою клієнту (T6/S19).

    - Дропає candles з поганою формою (degraded-but-loud).
    - Перевіряє монотонність t_ms (no duplicates, sorted asc).
    - Повертає список warnings.

    Мутує candles in-place (видаляє bad).
    """
    warnings: list = []
    if not candles:
        return warnings

    # ── Pass 1: shape guard — дропаємо погані ──
    valid = []
    for i, c in enumerate(candles):
        issue = _guard_candle_shape(c)
        if issue:
            warnings.append("WS_GUARD_%s idx=%d sym=%s tf=%s ft=%s" % (
                issue.upper(), i, symbol, tf_label, frame_type,
            ))
        else:
            valid.append(c)
    dropped = len(candles) - len(valid)
    if dropped > 0:
        candles[:] = valid
        _log.warning(
            "WS_OUTPUT_GUARD_DROP frame_type=%s sym=%s tf=%s dropped=%d/%d",
            frame_type, symbol, tf_label, dropped, dropped + len(valid),
        )

    # ── Pass 2: монотонність t_ms (sorted asc, no dup) ──
    if len(candles) >= 2:
        dup_count = 0
        unsorted_count = 0
        prev_t = candles[0].get("t_ms", 0)
        for j in range(1, len(candles)):
            cur_t = candles[j].get("t_ms", 0)
            if cur_t == prev_t:
                dup_count += 1
            elif cur_t < prev_t:
                unsorted_count += 1
            prev_t = cur_t
        if dup_count > 0:
            warnings.append("WS_GUARD_DUP_T_MS count=%d sym=%s tf=%s ft=%s" % (
                dup_count, symbol, tf_label, frame_type,
            ))
        if unsorted_count > 0:
            warnings.append("WS_GUARD_UNSORTED_T_MS count=%d sym=%s tf=%s ft=%s" % (
                unsorted_count, symbol, tf_label, frame_type,
            ))

    return warnings


# ── Session ────────────────────────────────────────────

class WsSession:
    """Per-connection session state."""

    __slots__ = (
        "client_id", "seq", "symbol", "tf_s",
        "last_update_seq", "ws", "delta_task",
        "_scrollback_count", "_scrollback_last_ts",
    )

    def __init__(self, ws: web.WebSocketResponse) -> None:
        self.client_id: str = uuid.uuid4().hex[:12]
        self.seq: int = 0
        self.symbol: Optional[str] = None
        self.tf_s: Optional[int] = None
        self.last_update_seq: Optional[int] = None
        self.ws: web.WebSocketResponse = ws
        self.delta_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._scrollback_count: int = 0      # P11: кількість scrollback для поточного symbol+tf
        self._scrollback_last_ts: float = 0  # P11: timestamp останнього scrollback

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq


# ── Frame builders ─────────────────────────────────────

def _build_meta(session: WsSession, app: Any = None, **extra: Any) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "schema_v": SCHEMA_V,
        "seq": session.next_seq(),
        "server_ts_ms": int(time.time() * 1000),
        "boot_id": app["_boot_id"] if app is not None else None,
    }
    if extra:
        meta.update(extra)
    return meta


def build_heartbeat_frame(session: WsSession, app: Any = None) -> Dict[str, Any]:
    return {
        "type": "render_frame",
        "frame_type": "heartbeat",
        "meta": _build_meta(session, app=app),
    }


def _build_full_frame(
    session: WsSession,
    candles: list,
    symbol: str,
    tf_label: str,
    warnings: Optional[list] = None,
    app: Any = None,
) -> Dict[str, Any]:
    # T6/S19: output guard — validate candle shapes before send
    guard_warns = _guard_candles_output(candles, symbol, tf_label, "full")
    meta = _build_meta(session, app=app)
    all_warnings = list(warnings or []) + guard_warns
    if all_warnings:
        meta["warnings"] = all_warnings
    # P1→P2: config payload — UI читає symbols/tfs з сервера (SSOT)
    # tfs = canonical labels (["M1","M3",...]) — UI switch надсилає саме labels
    if app is not None:
        cfg = app.get("_full_config", {})
        allowlist_s = sorted(app.get("_tf_allowlist", set()))
        tf_labels = [_TF_S_TO_LABEL[s] for s in allowlist_s if s in _TF_S_TO_LABEL]
        meta["config"] = {
            "symbols": cfg.get("symbols", []),
            "tfs": tf_labels,
        }
    return {
        "type": "render_frame",
        "frame_type": "full",
        "symbol": symbol,
        "tf": tf_label,
        "candles": candles,
        "zones": [],
        "swings": [],
        "levels": [],
        "drawings": [],
        "meta": meta,
    }


def _build_delta_frame(
    session: WsSession,
    candles: list,
    symbol: str,
    tf_label: str,
    warnings: Optional[list] = None,
    app: Any = None,
) -> Dict[str, Any]:
    # T6/S19: output guard
    guard_warns = _guard_candles_output(candles, symbol, tf_label, "delta")
    meta = _build_meta(session, app=app)
    all_warnings = list(warnings or []) + guard_warns
    if all_warnings:
        meta["warnings"] = all_warnings
    return {
        "type": "render_frame",
        "frame_type": "delta",
        "symbol": symbol,
        "tf": tf_label,
        "candles": candles,
        "meta": meta,
    }


def _build_scrollback_frame(
    session: WsSession,
    candles: list,
    symbol: str,
    tf_label: str,
    warnings: Optional[list] = None,
    app: Any = None,
) -> Dict[str, Any]:
    # T6/S19: output guard
    guard_warns = _guard_candles_output(candles, symbol, tf_label, "scrollback")
    meta = _build_meta(session, app=app)
    all_warnings = list(warnings or []) + guard_warns
    if all_warnings:
        meta["warnings"] = all_warnings
    return {
        "type": "render_frame",
        "frame_type": "scrollback",
        "symbol": symbol,
        "tf": tf_label,
        "candles": candles,
        "meta": meta,
    }


def _build_config_frame(
    session: WsSession,
    app: Any,
) -> Dict[str, Any]:
    """T8/S24: dedicated config frame — policy bridge for UI.

    Відправляється одразу при connect, до full frame.
    UI отримує symbols/tfs/defaults навіть якщо UDS недоступний.
    """
    cfg = app.get("_full_config", {})
    allowlist_s = sorted(app.get("_tf_allowlist", set()))
    tf_labels = [_TF_S_TO_LABEL[s] for s in allowlist_s if s in _TF_S_TO_LABEL]
    symbols = cfg.get("symbols", [])
    default_symbol = session.symbol or (symbols[0] if symbols else "XAU/USD")
    default_tf = _TF_S_TO_LABEL.get(session.tf_s or 1800, "M30")
    return {
        "type": "render_frame",
        "frame_type": "config",
        "config": {
            "symbols": symbols,
            "tfs": tf_labels,
            "default_symbol": default_symbol,
            "default_tf": default_tf,
        },
        "meta": _build_meta(session, app=app),
    }


# ── UDS async wrappers (blocking I/O → executor) ──────

async def _uds_read_window(app: web.Application, symbol: str, tf_s: int, limit: int,
                           to_open_ms: Optional[int] = None) -> Any:
    """Async wrapper для UDS read_window (blocking Redis/Disk I/O)."""
    from runtime.store.uds import WindowSpec, ReadPolicy
    uds = app.get("_uds")
    if uds is None:
        return None
    spec = WindowSpec(
        symbol=symbol,
        tf_s=tf_s,
        limit=limit,
        to_open_ms=to_open_ms,
        cold_load=to_open_ms is None,
    )
    # P11: scrollback (to_open_ms) = "explicit" (disk дозволено завжди);
    #      cold-start/switch = "bootstrap" (disk тільки в bootstrap вікні).
    dp = "explicit" if to_open_ms is not None else "bootstrap"
    policy = ReadPolicy(disk_policy=dp, prefer_redis=True)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, uds.read_window, spec, policy)


async def _uds_read_updates(app: web.Application, symbol: str, tf_s: int,
                            since_seq: Optional[int], include_preview: bool) -> Any:
    """Async wrapper для UDS read_updates (blocking)."""
    from runtime.store.uds import UpdatesSpec
    uds = app.get("_uds")
    if uds is None:
        return None
    spec = UpdatesSpec(
        symbol=symbol,
        tf_s=tf_s,
        since_seq=since_seq,
        limit=500,
        include_preview=include_preview,
    )
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, uds.read_updates, spec)


async def _send_full_frame(session: WsSession, app: web.Application) -> None:
    """Читає UDS read_window → map → full frame → send."""
    if session.symbol is None or session.tf_s is None:
        return
    cfg = app.get("_full_config", {})
    limit = _cold_start_limit(session.tf_s, cfg)
    tf_label = _TF_S_TO_LABEL.get(session.tf_s, f"{session.tf_s}s")
    warnings: list = []
    try:
        result = await _uds_read_window(app, session.symbol, session.tf_s, limit)
        if result is None:
            warnings.append("uds_unavailable")
            frame = _build_full_frame(session, [], session.symbol, tf_label, warnings, app=app)
            await session.ws.send_json(frame)
            return
        bars = getattr(result, "bars_lwc", [])
        candles, dropped = map_bars_to_candles_v4(bars)
        if dropped > 0:
            warnings.append("candle_map_dropped:%d" % dropped)
            _log.warning("WS_CANDLE_MAP_DROPPED client=%s dropped=%d", session.client_id, dropped)
        warnings.extend(getattr(result, "warnings", []))
        frame = _build_full_frame(session, candles, session.symbol, tf_label, warnings or None, app=app)
        await session.ws.send_json(frame)
        _log.info(
            "WS_FULL_PUSH client=%s symbol=%s tf=%s candles=%d seq=%d",
            session.client_id, session.symbol, tf_label, len(candles), session.seq,
        )
        # Adopt cursor for delta after full frame
        session.last_update_seq = None  # will adopt-tail on first poll
    except Exception as exc:
        _log.warning("WS_FULL_FRAME_ERROR client=%s err=%s", session.client_id, exc)


async def _delta_loop(session: WsSession, app: web.Application) -> None:
    """Background task: poll UDS read_updates → delta frames."""
    poll_s = app.get("_delta_poll_s", DEFAULT_DELTA_POLL_S)
    preview_tfs: set = app.get("_preview_tf_set", set())
    try:
        while not session.ws.closed:
            await asyncio.sleep(poll_s)
            if session.ws.closed or session.symbol is None or session.tf_s is None:
                break
            include_preview = session.tf_s in preview_tfs
            try:
                result = await _uds_read_updates(
                    app, session.symbol, session.tf_s,
                    session.last_update_seq, include_preview,
                )
                if result is None:
                    continue
                events = getattr(result, "events", [])
                cursor = getattr(result, "cursor_seq", 0)
                if session.last_update_seq is None:
                    # Adopt-tail: skip events, just store cursor
                    session.last_update_seq = cursor
                    continue
                if not events:
                    session.last_update_seq = cursor
                    continue
                # Map event bars → candles
                # Дедуплікація events по open_ms: complete=true (final) перемагає preview.
                # Це усуває DUP_T_MS spam і гарантує final>preview.
                seen_events: Dict[int, dict] = {}  # open_ms → event
                for ev in events:
                    if not isinstance(ev, dict):
                        continue
                    bar = ev.get("bar")
                    if not isinstance(bar, dict):
                        continue
                    open_ms = bar.get("open_time_ms") or bar.get("open_ms") or 0
                    prev = seen_events.get(open_ms)
                    if prev is None:
                        seen_events[open_ms] = ev
                    elif ev.get("complete") and not prev.get("complete"):
                        # final перемагає preview
                        seen_events[open_ms] = ev
                    elif ev.get("complete") == prev.get("complete"):
                        # однаковий тип — пізніший (newer seq) перемагає
                        seen_events[open_ms] = ev
                    # else: prev is final, ev is preview → keep prev
                bars = [ev["bar"] for ev in seen_events.values()]
                candles, dropped = map_bars_to_candles_v4(bars)
                # Сортуємо по t_ms для монотонної доставки (defense-in-depth)
                candles.sort(key=lambda c: c.get("t_ms", 0))
                warnings: list = []
                if dropped > 0:
                    warnings.append("candle_map_dropped:%d" % dropped)
                warnings.extend(getattr(result, "warnings", []))
                if candles:
                    tf_label = _TF_S_TO_LABEL.get(session.tf_s, f"{session.tf_s}s")
                    frame = _build_delta_frame(
                        session, candles, session.symbol, tf_label,
                        warnings or None, app=app,
                    )
                    await session.ws.send_json(frame)
                    _log.info(
                        "WS_DELTA_PUSH client=%s symbol=%s tf=%s candles=%d seq=%d",
                        session.client_id, session.symbol, tf_label, len(candles), session.seq,
                    )
                session.last_update_seq = cursor
            except Exception as exc:
                _log.warning("WS_DELTA_ERROR client=%s err=%s", session.client_id, exc)
    except asyncio.CancelledError:
        pass


# ── WS Handler ─────────────────────────────────────────

async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=None)
    await ws.prepare(request)

    session = WsSession(ws)
    app = request.app
    sessions: Dict[str, WsSession] = app.get("_ws_sessions", {})
    sessions[session.client_id] = session
    app["_ws_sessions"] = sessions

    _log.info("WS_CONNECT client_id=%s remote=%s", session.client_id, request.remote)

    # P2: set default symbol/tf from config, send full frame
    cfg = app.get("_full_config", {})
    symbols = cfg.get("symbols", [])
    default_symbol = symbols[0] if symbols else "XAU/USD"
    session.symbol = default_symbol
    session.tf_s = 1800  # M30 default — sync з SymbolTfPicker default

    # T8/S24: config frame (policy bridge) — завжди, незалежно від UDS
    try:
        config_frame = _build_config_frame(session, app)
        await ws.send_json(config_frame)
    except Exception as exc:
        _log.warning("WS_CONFIG_FRAME_FAIL client_id=%s err=%s", session.client_id, exc)

    uds = app.get("_uds")
    if uds is not None:
        # W3: Full frame auto-push on connect
        await _send_full_frame(session, app)
    else:
        # P1 fallback: heartbeat hello (no UDS)
        try:
            hello = build_heartbeat_frame(session, app=app)
            await ws.send_json(hello)
        except Exception as exc:
            _log.warning("WS_HELLO_FAIL client_id=%s err=%s", session.client_id, exc)

    # Heartbeat task
    hb_interval = app.get("_heartbeat_s", DEFAULT_HEARTBEAT_S)

    async def _heartbeat_loop() -> None:
        try:
            while not ws.closed:
                await asyncio.sleep(hb_interval)
                if ws.closed:
                    break
                try:
                    frame = build_heartbeat_frame(session, app=app)
                    await ws.send_json(frame)
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    hb_task = asyncio.ensure_future(_heartbeat_loop())

    # P2: start delta polling task (if UDS available)
    if uds is not None:
        session.delta_task = asyncio.ensure_future(_delta_loop(session, app))

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await _handle_action(session, msg.data, app)
            elif msg.type == WSMsgType.ERROR:
                _log.warning(
                    "WS_ERROR client_id=%s err=%s",
                    session.client_id, ws.exception(),
                )
    finally:
        hb_task.cancel()
        if session.delta_task is not None:
            session.delta_task.cancel()
        sessions.pop(session.client_id, None)
        _log.info(
            "WS_DISCONNECT client_id=%s code=%s",
            session.client_id,
            ws.close_code,
        )

    return ws


async def _handle_action(session: WsSession, raw: str, app: web.Application) -> None:
    """Розбирає вхідне повідомлення від клієнта. P2: switch + scrollback."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        _log.warning("WS_ACTION_INVALID client=%s reason=json_error raw=%.200s", session.client_id, raw)
        return
    if not isinstance(data, dict) or "action" not in data:
        _log.warning("WS_ACTION_INVALID client=%s reason=missing_action raw=%.200s", session.client_id, raw)
        return
    action = data.get("action")
    _log.info("WS_ACTION client=%s action=%s", session.client_id, action)

    if action == "switch":
        await _handle_switch(session, data, app)
    elif action == "scrollback":
        await _handle_scrollback(session, data, app)
    # інші action-и (drawing_*, overlay_toggle, replay_*) — Phase 2+


async def _handle_switch(session: WsSession, data: Dict[str, Any], app: web.Application) -> None:
    """Обробка switch action: змінити symbol/tf → новий full frame."""
    symbols_set: set = app.get("_symbols_set", set())
    tf_allowlist: set = app.get("_tf_allowlist", set())

    raw_symbol = str(data.get("symbol", session.symbol or ""))
    raw_tf = str(data.get("tf", data.get("tf_s", "")))

    # Validate symbol
    symbol = _canonicalize_symbol(raw_symbol, symbols_set)
    if symbol not in symbols_set and symbols_set:
        _log.warning("WS_SWITCH_REJECT client=%s reason=unknown_symbol raw=%s", session.client_id, raw_symbol)
        # Send empty full frame with warning
        warnings = ["unknown_symbol"]
        tf_label = _TF_S_TO_LABEL.get(session.tf_s or 300, "M5")
        frame = _build_full_frame(session, [], symbol, tf_label, warnings, app=app)
        await session.ws.send_json(frame)
        return

    # Parse tf
    tf_s = _TF_LABEL_TO_S.get(raw_tf)
    if tf_s is None:
        try:
            tf_s = int(raw_tf)
        except (TypeError, ValueError):
            tf_s = None
    if tf_s is None or (tf_allowlist and tf_s not in tf_allowlist):
        _log.warning("WS_SWITCH_REJECT client=%s reason=tf_not_allowed raw=%s", session.client_id, raw_tf)
        warnings = ["tf_not_allowed"]
        tf_label = raw_tf or "?"
        frame = _build_full_frame(session, [], symbol, tf_label, warnings, app=app)
        await session.ws.send_json(frame)
        return

    old_sym, old_tf = session.symbol, session.tf_s
    session.symbol = symbol
    session.tf_s = tf_s
    session.last_update_seq = None  # reset cursor for new pair
    session._scrollback_count = 0  # P11: reset scrollback budget on switch

    _log.info(
        "WS_SWITCH client=%s from=%s:%s to=%s:%s",
        session.client_id,
        old_sym, _TF_S_TO_LABEL.get(old_tf or 0, "?"),
        symbol, _TF_S_TO_LABEL.get(tf_s, "?"),
    )

    # Cancel old delta task, start new one
    if session.delta_task is not None:
        session.delta_task.cancel()
        session.delta_task = None

    await _send_full_frame(session, app)

    if app.get("_uds") is not None:
        session.delta_task = asyncio.ensure_future(_delta_loop(session, app))


async def _handle_scrollback(session: WsSession, data: Dict[str, Any], app: web.Application) -> None:
    """Обробка scrollback action: UDS read_window(to_open_ms) → scrollback frame.

    P11 rails: max_steps + cooldown per session/symbol+tf.
    """
    if session.symbol is None or session.tf_s is None:
        return
    tf_label = _TF_S_TO_LABEL.get(session.tf_s, f"{session.tf_s}s")

    # P11 guard: max_steps
    if session._scrollback_count >= SCROLLBACK_MAX_STEPS:
        _log.warning(
            "WS_SCROLLBACK_REJECT client=%s reason=max_steps(%d)",
            session.client_id, SCROLLBACK_MAX_STEPS,
        )
        frame = _build_scrollback_frame(
            session, [], session.symbol, tf_label,
            ["scrollback_max_steps_reached"], app=app,
        )
        await session.ws.send_json(frame)
        return

    # P11 guard: cooldown
    now = time.time()
    if now - session._scrollback_last_ts < SCROLLBACK_COOLDOWN_S:
        _log.warning(
            "WS_SCROLLBACK_REJECT client=%s reason=cooldown",
            session.client_id,
        )
        frame = _build_scrollback_frame(
            session, [], session.symbol, tf_label,
            ["scrollback_throttled"], app=app,
        )
        await session.ws.send_json(frame)
        return

    to_ms = data.get("to_ms")
    if not isinstance(to_ms, (int, float)) or to_ms <= 0:
        _log.warning("WS_SCROLLBACK_REJECT client=%s reason=bad_to_ms", session.client_id)
        frame = _build_scrollback_frame(session, [], session.symbol, tf_label, ["bad_to_ms"], app=app)
        await session.ws.send_json(frame)
        return
    to_ms = int(to_ms)
    session._scrollback_count += 1
    session._scrollback_last_ts = now
    cfg = app.get("_full_config", {})
    # Scrollback chunk: менше ніж cold start
    limit = min(_cold_start_limit(session.tf_s, cfg), 500)
    warnings: list = []
    try:
        result = await _uds_read_window(app, session.symbol, session.tf_s, limit, to_open_ms=to_ms)
        if result is None:
            warnings.append("uds_unavailable")
            frame = _build_scrollback_frame(session, [], session.symbol, tf_label, warnings, app=app)
            await session.ws.send_json(frame)
            return
        bars = getattr(result, "bars_lwc", [])
        candles, dropped = map_bars_to_candles_v4(bars)
        if dropped > 0:
            warnings.append("candle_map_dropped:%d" % dropped)
        warnings.extend(getattr(result, "warnings", []))
        frame = _build_scrollback_frame(session, candles, session.symbol, tf_label, warnings or None, app=app)
        await session.ws.send_json(frame)
        _log.info(
            "WS_SCROLLBACK_PUSH client=%s symbol=%s bars=%d to_ms=%d",
            session.client_id, session.symbol, len(candles), to_ms,
        )
    except Exception as exc:
        _log.warning("WS_SCROLLBACK_ERROR client=%s err=%s", session.client_id, exc)
        # Завжди відповідаємо пустим frame — інакше клієнт застрягне
        try:
            tf_label = _TF_S_TO_LABEL.get(session.tf_s, f"{session.tf_s}s")
            frame = _build_scrollback_frame(session, [], session.symbol, tf_label, ["scrollback_error"], app=app)
            await session.ws.send_json(frame)
        except Exception:
            pass


# ── UDS init ───────────────────────────────────────────

def _init_uds(app: web.Application, config_path: str, cfg: Dict[str, Any]) -> None:
    """Ініціалізує UDS reader. W0: role='reader', writer_components=False."""
    try:
        from runtime.store.uds import build_uds_from_config
        import os
        data_root = cfg.get("data_root", "./data_v3")
        boot_id = app["_boot_id"]
        uds = build_uds_from_config(
            os.path.abspath(config_path),
            data_root,
            boot_id,
            role="reader",
            writer_components=False,
        )
        app["_uds"] = uds
        _log.info("WS_UDS_INIT role=reader boot_id=%s", boot_id)
    except Exception as exc:
        app["_uds"] = None
        _log.warning("WS_UDS_INIT_FAILED err=%s (running without UDS)", exc)


# ── App factory ────────────────────────────────────────

def build_app(
    *,
    config_path: str = "config.json",
    uds: Any = None,
) -> web.Application:
    """Створює aiohttp Application з WS endpoint.

    Args:
        config_path: шлях до config.json
        uds: зовнішній UDS instance (для тестів). Якщо None — P2 auto-init.
    """
    full_cfg = _load_full_config(config_path)
    ws_cfg = full_cfg.get("ws_server", {}) if isinstance(full_cfg, dict) else {}
    app = web.Application()
    app["_heartbeat_s"] = int(ws_cfg.get("heartbeat_interval_s", DEFAULT_HEARTBEAT_S))
    app["_delta_poll_s"] = float(ws_cfg.get("delta_poll_interval_s", DEFAULT_DELTA_POLL_S))
    app["_ws_sessions"] = {}
    app["_config_path"] = config_path
    app["_boot_id"] = uuid.uuid4().hex[:16]
    app["_full_config"] = full_cfg

    # Symbol/TF sets from config (T10: imports at top-level)
    symbols_list = full_cfg.get("symbols", [])
    app["_symbols_set"] = set(str(s) for s in symbols_list)
    app["_tf_allowlist"] = tf_allowlist_from_cfg(full_cfg)
    preview_set, _ = preview_tf_allowlist_from_cfg(full_cfg)
    app["_preview_tf_set"] = preview_set

    # P2: UDS reader (W0: role="reader")
    if uds is not None:
        app["_uds"] = uds
    else:
        _init_uds(app, config_path, full_cfg)

    app.router.add_get("/ws", ws_handler)

    # ── Same-origin SPA serving (Правило §11: UI + API = один процес) ──
    # Роздача ui_v4/dist/ якщо dist існує (після npm run build)
    _ws_dir = os.path.dirname(os.path.abspath(__file__))
    _ui_dist = os.path.normpath(os.path.join(_ws_dir, "..", "..", "ui_v4", "dist"))
    _ui_index = os.path.join(_ui_dist, "index.html")
    if os.path.isfile(_ui_index):
        async def _spa_index(request: web.Request) -> web.FileResponse:
            return web.FileResponse(_ui_index)
        # SPA fallback: index.html для кореня
        app.router.add_get("/", _spa_index)
        # Статичні ассети (JS/CSS/images)
        app.router.add_static("/assets", os.path.join(_ui_dist, "assets"))
        _log.info("UI_V4_STATIC registered dist=%s", _ui_dist)
    else:
        _log.info(
            "UI_V4_STATIC_SKIP dist not found at %s "
            "(run 'cd ui_v4 && npm run build' for prod mode)",
            _ui_dist,
        )

    _log.info(
        "WS_APP_BUILT heartbeat_s=%s boot_id=%s uds=%s",
        app["_heartbeat_s"], app["_boot_id"],
        "ready" if app.get("_uds") is not None else "none",
    )
    return app


# ── Port bind with retry (Windows TIME_WAIT resilience) ──

_BIND_MAX_RETRIES = 5
_BIND_RETRY_DELAY_S = 3.0


def _run_with_retry(
    app: web.Application, host: str, port: int,
    max_retries: int = _BIND_MAX_RETRIES,
    retry_delay: float = _BIND_RETRY_DELAY_S,
) -> None:
    """Запуск aiohttp з retry для port bind (Windows TIME_WAIT)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())

    for attempt in range(1, max_retries + 1):
        try:
            site = web.TCPSite(runner, host, port, reuse_address=True)
            loop.run_until_complete(site.start())
            _log.info(
                "WS_SERVER_BOUND host=%s port=%s attempt=%d",
                host, port, attempt,
            )
            break
        except OSError as exc:
            _log.warning(
                "WS_SERVER_BIND_RETRY port=%s attempt=%d/%d err=%s",
                port, attempt, max_retries, exc,
            )
            if attempt == max_retries:
                _log.error(
                    "WS_SERVER_BIND_FAILED port=%s after %d attempts",
                    port, max_retries,
                )
                loop.run_until_complete(runner.cleanup())
                raise SystemExit(1)
            time.sleep(retry_delay)

    try:
        _log.info("WS_SERVER_READY ws://%s:%s/ws", host, port)
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(runner.cleanup())
        loop.close()


# ── CLI entrypoint ─────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="ui_v4 WebSocket server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", type=str, default=DEFAULT_HOST)
    parser.add_argument("--config", type=str, default="config.json")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _log.info("WS_SERVER starting host=%s port=%s", args.host, args.port)
    app = build_app(config_path=args.config)
    _run_with_retry(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
