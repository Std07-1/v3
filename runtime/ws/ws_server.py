"""
runtime/ws/ws_server.py — aiohttp WebSocket сервер для ui_v4.

P1: skeleton + heartbeat.
P2: UDS reader integration (full frame, switch, delta, scrollback).

Інваріанти:
  W0: WS-сервер = UDS reader only (role="reader")
  W1: schema_v = "ui_v4_v2" на кожному frame
  W2: meta.seq строго зростає per-connection (heartbeat/full/delta/scrollback)
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
from concurrent.futures import ThreadPoolExecutor
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
DEFAULT_DELTA_POLL_S = 2.0
DEFAULT_COLD_START_BARS = 300

# ADR-0011: broadcast send timeout per client (slow-client rail)
BROADCAST_SEND_TIMEOUT_S = 1.0

# ADR-0012 P3: D1 live tick relay defaults
_D1_TICK_RELAY_ENABLED_DEFAULT = False
_D1_TICK_RELAY_TFS_DEFAULT: set = set()

# P11: scrollback disk rails
SCROLLBACK_MAX_STEPS = 12         # макс чанків scrollback per session per symbol+tf
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
        "last_update_seq", "ws",
        "_scrollback_count", "_scrollback_last_ts",
    )

    def __init__(self, ws: web.WebSocketResponse) -> None:
        self.client_id: str = uuid.uuid4().hex[:12]
        self.seq: int = 0
        self.symbol: Optional[str] = None
        self.tf_s: Optional[int] = None
        self.last_update_seq: Optional[int] = None
        self.ws: web.WebSocketResponse = ws
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
    # P2: disk_policy="explicit" для всіх reads — ws_server окремий процес,
    # його RAM/Redis можуть бути stale. Disk завжди актуальний.
    policy = ReadPolicy(disk_policy="explicit", prefer_redis=True)
    loop = asyncio.get_event_loop()
    executor = app.get("_uds_executor")
    return await loop.run_in_executor(executor, uds.read_window, spec, policy)


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
    executor = app.get("_uds_executor")
    return await loop.run_in_executor(executor, uds.read_updates, spec)


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
        candles, dropped = map_bars_to_candles_v4(bars, tf_s=session.tf_s or 0)
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


async def _safe_broadcast(
    frame: Dict[str, Any],
    recipients: tuple,
    sessions: Dict[str, "WsSession"],
    timeout_s: float = BROADCAST_SEND_TIMEOUT_S,
) -> float:
    """ADR-0011 BC5+BC6: broadcast з per-client seq + timeout + degraded-but-loud.

    Для кожного клієнта: інжектить session.next_seq() в meta.seq,
    серіалізує, відправляє з timeout. Повертає t_send_ms.
    """
    if not recipients:
        return 0.0

    async def _guarded_send(s: "WsSession") -> None:
        frame["meta"]["seq"] = s.next_seq()
        payload = json.dumps(frame)
        await asyncio.wait_for(s.ws.send_str(payload), timeout=timeout_s)

    t1 = time.perf_counter()
    results = await asyncio.gather(
        *(_guarded_send(s) for s in recipients),
        return_exceptions=True,
    )
    t_send_ms = (time.perf_counter() - t1) * 1000.0

    for s, res in zip(recipients, results):
        if isinstance(res, Exception):
            reason = "timeout" if isinstance(res, asyncio.TimeoutError) else type(res).__name__
            _log.warning(
                "WS_BROADCAST_ERR client_id=%s reason=%s err=%s",
                s.client_id, reason, res,
            )
            sessions.pop(s.client_id, None)
            try:
                await s.ws.close()
            except Exception:
                pass
    return t_send_ms


def _seed_forming_from_uds(
    app: web.Application,
    symbol: str,
    tf_s: int,
    bucket_open_ms: int,
    fallback_price: float,
) -> Dict[str, Any]:
    """Seed forming candle з UDS (поточний бар для bucket_open_ms).

    Після рестарту forming_by_target = {}. Без seed open = перший тік
    (хибний D1 open). Якщо UDS має бар для цього bucket — береться O/H/L.
    Якщо UDS ще порожній — fallback на tick_price (degraded-but-loud).
    """
    uds = app.get("_uds")
    if uds is not None:
        try:
            from runtime.store.uds import WindowSpec, ReadPolicy
            spec = WindowSpec(
                symbol=symbol, tf_s=tf_s, limit=2,
                to_open_ms=None, cold_load=False,
            )
            policy = ReadPolicy(disk_policy="explicit", prefer_redis=True)
            result = uds.read_window(spec, policy)
            bars_lwc = getattr(result, "bars_lwc", [])
            for b in reversed(bars_lwc):
                b_open = b.get("open_time_ms") or b.get("open_ms", 0)
                if b_open == bucket_open_ms:
                    _log.info(
                        "D1_FORMING_SEED_UDS sym=%s open_ms=%d o=%.2f h=%.2f l=%.2f",
                        symbol, bucket_open_ms,
                        b.get("open", b.get("o", 0)),
                        b.get("high", b.get("h", 0)),
                        b.get("low", b.get("l", 0)),
                    )
                    return {
                        "symbol": symbol,
                        "o": float(b.get("open", b.get("o", fallback_price))),
                        "h": float(b.get("high", b.get("h", fallback_price))),
                        "l": float(b.get("low", b.get("l", fallback_price))),
                        "c": fallback_price,
                        "open_ms": bucket_open_ms,
                    }
        except Exception as exc:
            _log.warning("D1_FORMING_SEED_ERR sym=%s err=%s", symbol, exc)
    # Fallback: немає UDS даних → чистий тік (degraded-but-loud)
    _log.warning(
        "D1_FORMING_NO_SEED sym=%s open_ms=%d price=%.2f "
        "— open буде першим тіком після рестарту",
        symbol, bucket_open_ms, fallback_price,
    )
    return {
        "symbol": symbol, "o": fallback_price, "h": fallback_price,
        "l": fallback_price, "c": fallback_price, "open_ms": bucket_open_ms,
    }


async def _global_delta_loop(app: web.Application) -> None:
    """ADR-0011: Global Background task: poll UDS read_updates → serialize once → fanout."""
    poll_s = app.get("_delta_poll_s", DEFAULT_DELTA_POLL_S)
    preview_tfs: set = app.get("_preview_tf_set", set())
    forming_by_target: Dict[tuple[str, int], Dict[str, Any]] = {}  # ADR-0012 P3 global forming tracking

    try:
        while True:
            await asyncio.sleep(poll_s)
            sessions: Dict[str, WsSession] = app.get("_ws_sessions", {})
            if not sessions:
                continue
            
            subs_by_target: Dict[tuple[str, int], list[WsSession]] = {}
            for sess in sessions.values():
                if sess.ws.closed or sess.symbol is None or sess.tf_s is None:
                    continue
                target = (sess.symbol, sess.tf_s)
                if target not in subs_by_target:
                    subs_by_target[target] = []
                subs_by_target[target].append(sess)
            
            for (symbol, tf_s), group_sessions in subs_by_target.items():
                include_preview = tf_s in preview_tfs
                subscribers = tuple(group_sessions)
                
                min_seq = None
                for s in subscribers:
                    if s.last_update_seq is not None:
                        if min_seq is None or s.last_update_seq < min_seq:
                            min_seq = s.last_update_seq
                
                t0 = time.perf_counter()
                frame = None
                try:
                    result = await _uds_read_updates(app, symbol, tf_s, min_seq, include_preview)
                    if result is None:
                        continue
                        
                    events = getattr(result, "events", [])
                    cursor = getattr(result, "cursor_seq", 0)
                    tf_label = _TF_S_TO_LABEL.get(tf_s, f"{tf_s}s")
                    
                    if not events:
                        d1_relay_tfs: set = app.get("_d1_tick_relay_tfs", set())
                        tick_redis = app.get("_tick_redis_client")
                        tick_ns = app.get("_tick_redis_ns", "")
                        if tf_s in d1_relay_tfs and tick_redis is not None:
                            try:
                                tick_key = f"{tick_ns}:tick:last:{symbol.replace('/', '_')}"
                                tick_raw = await asyncio.get_event_loop().run_in_executor(
                                    app.get("_uds_executor"), tick_redis.get, tick_key
                                )
                                if tick_raw:
                                    tick_data = json.loads(tick_raw)
                                    tick_price = float(tick_data.get("mid", 0))
                                    tick_ts_ms = int(tick_data.get("tick_ts_ms", 0))
                                    if tick_price > 0 and tick_ts_ms > 0:
                                        forming = forming_by_target.get((symbol, tf_s))
                                        if forming is None:
                                            # ── ADR: seed forming з UDS при рестарті ──
                                            # Без seed open = перший тік після рестарту (хибний).
                                            # Читаємо поточний бар з UDS щоб успадкувати O/H/L.
                                            from core.buckets import bucket_start_ms, resolve_anchor_offset_ms
                                            cfg = app.get("_full_config", {})
                                            anchor_ms = resolve_anchor_offset_ms(tf_s, cfg)
                                            seed_open_ms = bucket_start_ms(tick_ts_ms, tf_s * 1000, anchor_ms)
                                            forming = _seed_forming_from_uds(
                                                app, symbol, tf_s, seed_open_ms, tick_price,
                                            )
                                        forming["h"] = max(forming["h"], tick_price)
                                        forming["l"] = min(forming["l"], tick_price)
                                        forming["c"] = tick_price
                                        open_ms = forming.get("open_ms", 0)
                                        if open_ms <= 0:
                                            from core.buckets import bucket_start_ms, resolve_anchor_offset_ms
                                            cfg = app.get("_full_config", {})
                                            anchor_ms = resolve_anchor_offset_ms(tf_s, cfg)
                                            open_ms = bucket_start_ms(tick_ts_ms, tf_s * 1000, anchor_ms)
                                            forming["open_ms"] = open_ms
                                        forming_by_target[(symbol, tf_s)] = forming
                                        
                                        forming_candle = {
                                            "t_ms": open_ms, "o": forming["o"], "h": forming["h"],
                                            "l": forming["l"], "c": forming["c"], "v": 0,
                                            "complete": False, "src": "tick_relay",
                                        }
                                        meta = {
                                            "schema_v": SCHEMA_V, "seq": 0,
                                            "server_ts_ms": int(time.time() * 1000), "boot_id": app.get("_boot_id"),
                                        }
                                        frame = {
                                            "type": "render_frame", "frame_type": "delta",
                                            "symbol": symbol, "tf": tf_label, "candles": [forming_candle],
                                            "meta": meta,
                                        }
                            except Exception as tick_exc:
                                _log.debug("WS_TICK_RELAY_ERR target=%s:%s err=%s", symbol, tf_s, tick_exc)
                        
                        if frame is not None:
                            t1 = time.perf_counter()
                            t_ser_ms = (t1 - t0) * 1000.0
                            active_recipients = []
                            for s in subscribers:
                                if s.last_update_seq is None:
                                    s.last_update_seq = cursor
                                else:
                                    active_recipients.append(s)
                            if active_recipients:
                                t_send_ms = await _safe_broadcast(
                                    frame, tuple(active_recipients), sessions,
                                )
                                _log.info("WS_BROADCAST_METRICS sym=%s tf=%s subs=%d t_ser_ms=%.2f t_send_ms=%.2f", symbol, tf_label, len(active_recipients), t_ser_ms, t_send_ms)
                        
                        for s in subscribers:
                            if s.last_update_seq is None: 
                                s.last_update_seq = cursor
                        continue
                        
                    seen_events: Dict[int, dict] = {}
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
                            seen_events[open_ms] = ev
                        elif ev.get("complete") == prev.get("complete"):
                            seen_events[open_ms] = ev
                    
                    bars = [ev["bar"] for ev in seen_events.values()]
                    candles, dropped = map_bars_to_candles_v4(bars, tf_s=tf_s)
                    candles.sort(key=lambda c: c.get("t_ms", 0))
                    
                    if candles:
                        warnings = getattr(result, "warnings", [])
                        if dropped > 0: 
                            warnings.append("candle_map_dropped:%d" % dropped)
                        guard_warns = _guard_candles_output(candles, symbol, tf_label, "delta")
                        all_warnings = list(warnings) + guard_warns
                        meta = {
                            "schema_v": SCHEMA_V, "seq": 0,
                            "server_ts_ms": int(time.time() * 1000), "boot_id": app.get("_boot_id"),
                        }
                        if all_warnings: 
                            meta["warnings"] = all_warnings
                        frame = {
                            "type": "render_frame", "frame_type": "delta",
                            "symbol": symbol, "tf": tf_label, "candles": candles, "meta": meta,
                        }
                        
                        d1_relay_tfs_2: set = app.get("_d1_tick_relay_tfs", set())
                        if tf_s in d1_relay_tfs_2:
                            for c in candles:
                                if c.get("complete", False) or c.get("src") not in ("tick_relay", None):
                                    forming_by_target.pop((symbol, tf_s), None)
                                    
                    t1 = time.perf_counter()
                    t_ser_ms = (t1 - t0) * 1000.0
                    
                    active_recipients = []
                    for s in subscribers:
                        if s.last_update_seq is None:
                            s.last_update_seq = cursor
                        else:
                            active_recipients.append(s)
                            s.last_update_seq = cursor
                    
                    if active_recipients and frame is not None:
                        t_send_ms = await _safe_broadcast(
                            frame, tuple(active_recipients), sessions,
                        )
                        _log.info("WS_BROADCAST_METRICS sym=%s tf=%s subs=%d t_ser_ms=%.2f t_send_ms=%.2f", symbol, tf_label, len(active_recipients), t_ser_ms, t_send_ms)
                        
                except Exception as loop_exc:
                    _log.warning("WS_GLOBAL_DELTA_ERR target=%s:%s err=%s", symbol, tf_s, loop_exc)

            # Purge forming_by_target for keys with no subscribers (memory hygiene)
            stale_keys = [k for k in forming_by_target if k not in subs_by_target]
            for k in stale_keys:
                forming_by_target.pop(k, None)

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

    await _send_full_frame(session, app)


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
        candles, dropped = map_bars_to_candles_v4(bars, tf_s=session.tf_s or 0)
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

    # ADR-0012 P3: D1 live tick relay — Redis client + config flags
    _d1_relay_enabled = bool(full_cfg.get("d1_live_tick_relay_enabled", _D1_TICK_RELAY_ENABLED_DEFAULT))
    _d1_relay_tfs_raw = full_cfg.get("d1_live_tick_relay_tfs_s", [])
    app["_d1_tick_relay_tfs"] = set(int(x) for x in _d1_relay_tfs_raw) if _d1_relay_enabled else set()
    app["_tick_redis_client"] = None
    app["_tick_redis_ns"] = ""
    if _d1_relay_enabled and app["_d1_tick_relay_tfs"]:
        try:
            from runtime.store.redis_spec import resolve_redis_spec
            import redis as _redis_lib
            spec = resolve_redis_spec(full_cfg, role="tick_relay", log=False)
            if spec is not None:
                app["_tick_redis_client"] = _redis_lib.Redis(
                    host=spec.host, port=spec.port, db=spec.db,
                    socket_timeout=2.0, socket_connect_timeout=2.0,
                    decode_responses=False,
                )
                app["_tick_redis_ns"] = spec.namespace
                _log.info(
                    "D1_TICK_RELAY_INIT enabled=1 tfs=%s ns=%s",
                    app["_d1_tick_relay_tfs"], spec.namespace,
                )
        except Exception as relay_exc:
            _log.warning("D1_TICK_RELAY_INIT_FAILED err=%s (disabled)", relay_exc)

    # Dedicated thread pool for UDS blocking I/O (limit thread explosion)
    # min(4, cpu_count) — 2 було недостатньо для паралельних /api/bars + /api/updates
    import os as _os
    _uds_workers = min(4, _os.cpu_count() or 4)
    app["_uds_executor"] = ThreadPoolExecutor(max_workers=_uds_workers, thread_name_prefix="uds")

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
    
    # Register global delta task lifecycle (ADR-0011)
    async def _start_bg_tasks(app_ctx: web.Application) -> None:
        if app_ctx.get("_uds") is not None:
            app_ctx["_global_delta_task"] = asyncio.ensure_future(_global_delta_loop(app_ctx))

    async def _cleanup_bg_tasks(app_ctx: web.Application) -> None:
        task = app_ctx.get("_global_delta_task")
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app.on_startup.append(_start_bg_tasks)
    app.on_cleanup.append(_cleanup_bg_tasks)
    
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
