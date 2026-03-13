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
from typing import Any, Dict, Optional, Protocol, cast

from aiohttp import web, WSMsgType

from runtime.ws.candle_map import map_bars_to_candles_v4
from core.config_loader import (
    load_system_config,
    resolve_config_path,
    tf_allowlist_from_cfg,
    preview_tf_allowlist_from_cfg,
)

_log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────
SCHEMA_V = "ui_v4_v2"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_HEARTBEAT_S = 30
DEFAULT_DELTA_POLL_S = 2.0
DEFAULT_COLD_START_BARS = 300


class RedisLike(Protocol):
    def get(self, name: str) -> Any: ...


class UdsLike(Protocol):
    def read_window(self, spec: Any, policy: Any) -> Any: ...

    def read_updates(self, spec: Any) -> Any: ...


class SmcRunnerLike(Protocol):
    def get_snapshot(self, symbol: str, tf_s: int) -> Any: ...

    def get_zone_grades(self, symbol: str, tf_s: int) -> Any: ...

    def get_session_levels_wire(self, symbol: str) -> Any: ...

    def get_bias_map(self, symbol: str) -> Any: ...

    def get_momentum_map(self, symbol: str) -> Any: ...

    def get_narrative(self, symbol: str, tf_s: int, *args: Any) -> Any: ...

    def feed_m1_bar_dict(self, symbol: str, bar: Dict[str, Any]) -> None: ...

    def on_bar_dict(self, symbol: str, tf_s: int, bar: Dict[str, Any]) -> None: ...

    def last_delta(self, symbol: str, tf_s: int) -> Any: ...

    def clear_delta(self, symbol: str, tf_s: int) -> None: ...

    def warmup(self, uds: UdsLike) -> None: ...


APP_HEARTBEAT_S = web.AppKey("heartbeat_s", int)
APP_DELTA_POLL_S = web.AppKey("delta_poll_s", float)
APP_WS_SESSIONS = web.AppKey("ws_sessions", dict)
APP_CONFIG_PATH = web.AppKey("config_path", str)
APP_BOOT_ID = web.AppKey("boot_id", str)
APP_FULL_CONFIG = web.AppKey("full_config", dict)
APP_SYMBOLS_SET = web.AppKey("symbols_set", set)
APP_TF_ALLOWLIST = web.AppKey("tf_allowlist", set)
APP_PREVIEW_TF_SET = web.AppKey("preview_tf_set", set)
APP_D1_TICK_RELAY_TFS = web.AppKey("d1_tick_relay_tfs", set)
APP_TICK_REDIS_CLIENT = web.AppKey("tick_redis_client", RedisLike)
APP_TICK_REDIS_NS = web.AppKey("tick_redis_ns", str)
APP_UDS_EXECUTOR = web.AppKey("uds_executor", ThreadPoolExecutor)
APP_UDS = web.AppKey("uds", UdsLike)
APP_SMC_RUNNER = web.AppKey("smc_runner", SmcRunnerLike)
APP_CORS_ORIGINS = web.AppKey("cors_origins", set)
APP_GLOBAL_DELTA_TASK = web.AppKey("global_delta_task", asyncio.Task)

# CORS: дозволені origins для cross-origin (Vercel / Cloudflare Pages)
# Конфіг: ws_server.cors_allowed_origins в config.json
# Якщо список порожній — CORS headers не додаються (same-origin режим)
_CORS_HEADERS_COMMON = {
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
}

# ADR-0011: broadcast send timeout per client (slow-client rail)
BROADCAST_SEND_TIMEOUT_S = 1.0

# ADR-0012 P3: D1 live tick relay defaults
_D1_TICK_RELAY_ENABLED_DEFAULT = False
_D1_TICK_RELAY_TFS_DEFAULT: set = set()

# P11: scrollback disk rails
SCROLLBACK_MAX_STEPS = 12  # макс чанків scrollback per session per symbol+tf
SCROLLBACK_COOLDOWN_S = 0.5  # мінімальний інтервал між scrollback від одного клієнта

# TF label ↔ seconds mapping (types.ts WsAction.switch.tf)
# Canonical labels: uppercase M1, M5, H1 etc. (як у фронтенді SymbolTfPicker)
_TF_CANONICAL_LABELS: Dict[str, int] = {
    "M1": 60,
    "M3": 180,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}
# Case-insensitive lookup: accept both "M15" and "15m" and "m15"
_TF_LABEL_TO_S: Dict[str, int] = {}
for _lbl, _sec in _TF_CANONICAL_LABELS.items():
    _TF_LABEL_TO_S[_lbl] = _sec  # M15
    _TF_LABEL_TO_S[_lbl.lower()] = _sec  # m15
# Also support legacy lowercase like "15m", "1h", "4h", "1d"
_TF_LABEL_TO_S.update(
    {
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }
)
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
            _log.debug(
                "WS_COLD_START_LIMIT_PARSE_FAIL tf_s=%s raw=%r",
                tf_s,
                raw,
                exc_info=True,
            )
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
            warnings.append(
                "WS_GUARD_%s idx=%d sym=%s tf=%s ft=%s"
                % (
                    issue.upper(),
                    i,
                    symbol,
                    tf_label,
                    frame_type,
                )
            )
        else:
            valid.append(c)
    dropped = len(candles) - len(valid)
    if dropped > 0:
        candles[:] = valid
        _log.warning(
            "WS_OUTPUT_GUARD_DROP frame_type=%s sym=%s tf=%s dropped=%d/%d",
            frame_type,
            symbol,
            tf_label,
            dropped,
            dropped + len(valid),
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
            warnings.append(
                "WS_GUARD_DUP_T_MS count=%d sym=%s tf=%s ft=%s"
                % (
                    dup_count,
                    symbol,
                    tf_label,
                    frame_type,
                )
            )
        if unsorted_count > 0:
            warnings.append(
                "WS_GUARD_UNSORTED_T_MS count=%d sym=%s tf=%s ft=%s"
                % (
                    unsorted_count,
                    symbol,
                    tf_label,
                    frame_type,
                )
            )

    return warnings


# ── Session ────────────────────────────────────────────


class WsSession:
    """Per-connection session state."""

    __slots__ = (
        "client_id",
        "seq",
        "symbol",
        "tf_s",
        "last_update_seq",
        "ws",
        "_scrollback_count",
        "_scrollback_last_ts",
    )

    def __init__(self, ws: web.WebSocketResponse) -> None:
        self.client_id: str = uuid.uuid4().hex[:12]
        self.seq: int = 0
        self.symbol: Optional[str] = None
        self.tf_s: Optional[int] = None
        self.last_update_seq: Optional[int] = 0
        self.ws: web.WebSocketResponse = ws
        self._scrollback_count: int = (
            0  # P11: кількість scrollback для поточного symbol+tf
        )
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
        "boot_id": app[APP_BOOT_ID] if app is not None else None,
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


def _build_error_frame(
    session: WsSession,
    code: str,
    message: str,
    app: Any = None,
) -> Dict[str, Any]:
    """S20/S25: error response frame — degraded-but-loud для клієнта."""
    return {
        "type": "render_frame",
        "frame_type": "error",
        "error": {"code": code, "message": message},
        "meta": _build_meta(session, app=app),
    }


def _build_full_frame(
    session: WsSession,
    candles: list,
    symbol: str,
    tf_label: str,
    warnings: Optional[list] = None,
    app: Any = None,
    smc_wire: Optional[Dict[str, Any]] = None,
    bias_map: Optional[Dict[str, str]] = None,
    momentum_map: Optional[Dict] = None,
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
        cfg = app.get(APP_FULL_CONFIG, {})
        allowlist_s = sorted(app.get(APP_TF_ALLOWLIST, set()))
        tf_labels = [_TF_S_TO_LABEL[s] for s in allowlist_s if s in _TF_S_TO_LABEL]
        meta["config"] = {
            "symbols": cfg.get("symbols", []),
            "tfs": tf_labels,
        }
    frame = {
        "type": "render_frame",
        "frame_type": "full",
        "symbol": symbol,
        "tf": tf_label,
        "candles": candles,
        "zones": smc_wire.get("zones", []) if smc_wire else [],
        "swings": smc_wire.get("swings", []) if smc_wire else [],
        "levels": smc_wire.get("levels", []) if smc_wire else [],
        "trend_bias": smc_wire.get("trend_bias") if smc_wire else None,
        "drawings": [],
        "meta": meta,
    }  # type: Dict[str, Any]
    # ADR-0029: zone_grades (only in full frame)
    if smc_wire and smc_wire.get("zone_grades"):
        frame["zone_grades"] = smc_wire["zone_grades"]
    # ADR-0031: multi-TF bias map (only in full frame)
    if bias_map:
        frame["bias_map"] = bias_map
    if momentum_map:
        frame["momentum_map"] = momentum_map
    return frame


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
    cfg = app.get(APP_FULL_CONFIG, {})
    allowlist_s = sorted(app.get(APP_TF_ALLOWLIST, set()))
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


async def _uds_read_window(
    app: web.Application,
    symbol: str,
    tf_s: int,
    limit: int,
    to_open_ms: Optional[int] = None,
) -> Any:
    """Async wrapper для UDS read_window (blocking Redis/Disk I/O)."""
    from runtime.store.uds import WindowSpec, ReadPolicy

    uds = app[APP_UDS] if APP_UDS in app else None
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
    executor = app[APP_UDS_EXECUTOR]
    return await loop.run_in_executor(executor, uds.read_window, spec, policy)


async def _uds_read_updates(
    app: web.Application,
    symbol: str,
    tf_s: int,
    since_seq: Optional[int],
    include_preview: bool,
) -> Any:
    """Async wrapper для UDS read_updates (blocking)."""
    from runtime.store.uds import UpdatesSpec

    uds = app[APP_UDS] if APP_UDS in app else None
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
    executor = app[APP_UDS_EXECUTOR]
    return await loop.run_in_executor(executor, uds.read_updates, spec)


async def _send_full_frame(session: WsSession, app: web.Application) -> None:
    """Читає UDS read_window → map → full frame → send."""
    if session.symbol is None or session.tf_s is None:
        return
    cfg = app.get(APP_FULL_CONFIG, {})
    limit = _cold_start_limit(session.tf_s, cfg)
    tf_label = _TF_S_TO_LABEL.get(session.tf_s, f"{session.tf_s}s")
    warnings: list = []
    try:
        result = await _uds_read_window(app, session.symbol, session.tf_s, limit)
        if result is None:
            warnings.append("uds_unavailable")
            frame = _build_full_frame(
                session, [], session.symbol, tf_label, warnings, app=app
            )
            await session.ws.send_json(frame)
            return
        bars = getattr(result, "bars_lwc", [])
        candles, dropped = map_bars_to_candles_v4(bars, tf_s=session.tf_s or 0)
        if dropped > 0:
            warnings.append("candle_map_dropped:%d" % dropped)
            _log.warning(
                "WS_CANDLE_MAP_DROPPED client=%s dropped=%d", session.client_id, dropped
            )
        warnings.extend(getattr(result, "warnings", []))
        # SMC: inject snapshot into full frame (ADR-0024 §6.1)
        smc_wire: Optional[Dict[str, Any]] = None
        _smc_runner = app[APP_SMC_RUNNER] if APP_SMC_RUNNER in app else None
        if _smc_runner is not None:
            try:
                _snap = _smc_runner.get_snapshot(session.symbol, session.tf_s)
                if _snap is not None:
                    smc_wire = _snap.to_wire()
                    # ADR-0029: zone_grades (computed during get_snapshot)
                    _zg = _smc_runner.get_zone_grades(session.symbol, session.tf_s)
                    if _zg and smc_wire is not None:
                        smc_wire["zone_grades"] = _zg
            except Exception as _smc_exc:
                _log.warning(
                    "WS_SMC_SNAP_ERR sym=%s tf=%s err=%s",
                    session.symbol,
                    session.tf_s,
                    _smc_exc,
                )
        # ADR-0031: collect bias_map for all compute TFs
        bias_map = None
        momentum_map = None
        if _smc_runner is not None:
            try:
                bias_map = _smc_runner.get_bias_map(session.symbol)
            except Exception as _bm_exc:
                _log.warning("WS_BIAS_MAP_ERR sym=%s err=%s", session.symbol, _bm_exc)
            try:
                momentum_map = _smc_runner.get_momentum_map(session.symbol)
            except Exception as _mm_exc:
                _log.warning(
                    "WS_MOMENTUM_MAP_ERR sym=%s err=%s", session.symbol, _mm_exc
                )
        frame = _build_full_frame(
            session,
            candles,
            session.symbol,
            tf_label,
            warnings or None,
            app=app,
            smc_wire=smc_wire,
            bias_map=bias_map,
            momentum_map=momentum_map,
        )
        # ADR-0033 N4: narrative in full frame + delta on complete bars. current_price from last candle.
        if _smc_runner is not None and candles:
            try:
                _last_c = (
                    candles[-1].get("c", 0) if isinstance(candles[-1], dict) else 0
                )
                _atr_est = (
                    abs(candles[-1].get("h", 0) - candles[-1].get("l", 0))
                    if isinstance(candles[-1], dict) and len(candles) > 0
                    else 1.0
                )
                _narr = _smc_runner.get_narrative(
                    session.symbol, session.tf_s, float(_last_c), float(_atr_est)
                )
                if _narr is not None:
                    from core.smc.narrative import narrative_to_wire

                    frame["narrative"] = narrative_to_wire(_narr)
            except Exception as _narr_exc:
                _log.warning(
                    "WS_NARRATIVE_ERR sym=%s err=%s", session.symbol, _narr_exc
                )
        await session.ws.send_json(frame)
        _log.info(
            "WS_FULL_PUSH client=%s symbol=%s tf=%s candles=%d seq=%d",
            session.client_id,
            session.symbol,
            tf_label,
            len(candles),
            session.seq,
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
            reason = (
                "timeout"
                if isinstance(res, asyncio.TimeoutError)
                else type(res).__name__
            )
            _log.warning(
                "WS_BROADCAST_ERR client_id=%s reason=%s err=%s",
                s.client_id,
                reason,
                res,
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
                symbol=symbol,
                tf_s=tf_s,
                limit=2,
                to_open_ms=None,
                cold_load=False,
            )
            policy = ReadPolicy(disk_policy="explicit", prefer_redis=True)
            result = uds.read_window(spec, policy)
            bars_lwc = getattr(result, "bars_lwc", [])
            for b in reversed(bars_lwc):
                b_open = b.get("open_time_ms") or b.get("open_ms", 0)
                if b_open == bucket_open_ms:
                    _log.info(
                        "D1_FORMING_SEED_UDS sym=%s open_ms=%d o=%.2f h=%.2f l=%.2f",
                        symbol,
                        bucket_open_ms,
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
        symbol,
        bucket_open_ms,
        fallback_price,
    )
    return {
        "symbol": symbol,
        "o": fallback_price,
        "h": fallback_price,
        "l": fallback_price,
        "c": fallback_price,
        "open_ms": bucket_open_ms,
    }


async def _global_delta_loop(app: web.Application) -> None:
    """ADR-0011: Global Background task: poll UDS read_updates → serialize once → fanout."""
    poll_s = app.get(APP_DELTA_POLL_S, DEFAULT_DELTA_POLL_S)
    preview_tfs: set = app.get(APP_PREVIEW_TF_SET, set())
    forming_by_target: Dict[tuple[str, int], Dict[str, Any]] = (
        {}
    )  # ADR-0012 P3 global forming tracking
    # ADR-0035: M1 cursor per symbol for session H/L live feed
    _m1_cursor_by_sym: Dict[str, Optional[int]] = {}

    try:
        while True:
            await asyncio.sleep(poll_s)
            sessions: Dict[str, WsSession] = app.get(APP_WS_SESSIONS, {})
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
                    result = await _uds_read_updates(
                        app, symbol, tf_s, min_seq, include_preview
                    )
                    if result is None:
                        continue

                    events = getattr(result, "events", [])
                    cursor = getattr(result, "cursor_seq", 0)
                    tf_label = _TF_S_TO_LABEL.get(tf_s, f"{tf_s}s")

                    if not events:
                        d1_relay_tfs: set = app[APP_D1_TICK_RELAY_TFS]
                        tick_redis = (
                            app[APP_TICK_REDIS_CLIENT]
                            if APP_TICK_REDIS_CLIENT in app
                            else None
                        )
                        tick_ns = app[APP_TICK_REDIS_NS]
                        if tf_s in d1_relay_tfs and tick_redis is not None:
                            try:
                                tick_key = (
                                    f"{tick_ns}:tick:last:{symbol.replace('/', '_')}"
                                )
                                tick_raw = (
                                    await asyncio.get_event_loop().run_in_executor(
                                        app[APP_UDS_EXECUTOR],
                                        tick_redis.get,
                                        tick_key,
                                    )
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
                                            from core.buckets import (
                                                bucket_start_ms,
                                                resolve_anchor_offset_ms,
                                            )

                                            cfg = app.get(APP_FULL_CONFIG, {})
                                            anchor_ms = resolve_anchor_offset_ms(
                                                tf_s, cfg
                                            )
                                            seed_open_ms = bucket_start_ms(
                                                tick_ts_ms, tf_s * 1000, anchor_ms
                                            )
                                            forming = _seed_forming_from_uds(
                                                app,
                                                symbol,
                                                tf_s,
                                                seed_open_ms,
                                                tick_price,
                                            )
                                        forming["h"] = max(forming["h"], tick_price)
                                        forming["l"] = min(forming["l"], tick_price)
                                        forming["c"] = tick_price
                                        open_ms = forming.get("open_ms", 0)
                                        if open_ms <= 0:
                                            from core.buckets import (
                                                bucket_start_ms,
                                                resolve_anchor_offset_ms,
                                            )

                                            cfg = app.get(APP_FULL_CONFIG, {})
                                            anchor_ms = resolve_anchor_offset_ms(
                                                tf_s, cfg
                                            )
                                            open_ms = bucket_start_ms(
                                                tick_ts_ms, tf_s * 1000, anchor_ms
                                            )
                                            forming["open_ms"] = open_ms
                                        forming_by_target[(symbol, tf_s)] = forming

                                        forming_candle = {
                                            "t_ms": open_ms,
                                            "o": forming["o"],
                                            "h": forming["h"],
                                            "l": forming["l"],
                                            "c": forming["c"],
                                            "v": 0,
                                            "complete": False,
                                            "src": "tick_relay",
                                        }
                                        meta = {
                                            "schema_v": SCHEMA_V,
                                            "seq": 0,
                                            "server_ts_ms": int(time.time() * 1000),
                                            "boot_id": app[APP_BOOT_ID],
                                        }
                                        frame = {
                                            "type": "render_frame",
                                            "frame_type": "delta",
                                            "symbol": symbol,
                                            "tf": tf_label,
                                            "candles": [forming_candle],
                                            "meta": meta,
                                        }
                            except Exception as tick_exc:
                                _log.debug(
                                    "WS_TICK_RELAY_ERR target=%s:%s err=%s",
                                    symbol,
                                    tf_s,
                                    tick_exc,
                                )

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
                                    frame,
                                    tuple(active_recipients),
                                    sessions,
                                )
                                _log.info(
                                    "WS_BROADCAST_METRICS sym=%s tf=%s subs=%d t_ser_ms=%.2f t_send_ms=%.2f",
                                    symbol,
                                    tf_label,
                                    len(active_recipients),
                                    t_ser_ms,
                                    t_send_ms,
                                )

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
                        guard_warns = _guard_candles_output(
                            candles, symbol, tf_label, "delta"
                        )
                        all_warnings = list(warnings) + guard_warns
                        meta = {
                            "schema_v": SCHEMA_V,
                            "seq": 0,
                            "server_ts_ms": int(time.time() * 1000),
                            "boot_id": app[APP_BOOT_ID],
                        }
                        if all_warnings:
                            meta["warnings"] = all_warnings
                        frame = {
                            "type": "render_frame",
                            "frame_type": "delta",
                            "symbol": symbol,
                            "tf": tf_label,
                            "candles": candles,
                            "meta": meta,
                        }

                        # SMC: notify runner on complete bars → inject delta if has_changes
                        _smc_runner = (
                            app[APP_SMC_RUNNER] if APP_SMC_RUNNER in app else None
                        )
                        if _smc_runner is not None:
                            for _ev in seen_events.values():
                                if _ev.get("complete"):
                                    _smc_runner.on_bar_dict(symbol, tf_s, _ev["bar"])
                            _smc_d = _smc_runner.last_delta(symbol, tf_s)
                            if _smc_d is not None and _smc_d.has_changes:
                                frame["smc_delta"] = _smc_d.to_wire()
                                _smc_runner.clear_delta(symbol, tf_s)
                            # ADR-0035: narrative refresh in delta (was full-frame only)
                            _any_complete = any(
                                _ev.get("complete") for _ev in seen_events.values()
                            )
                            if _any_complete and candles:
                                try:
                                    _last_c = (
                                        candles[-1].get("c", 0)
                                        if isinstance(candles[-1], dict)
                                        else 0
                                    )
                                    _atr_est = (
                                        abs(
                                            candles[-1].get("h", 0)
                                            - candles[-1].get("l", 0)
                                        )
                                        if isinstance(candles[-1], dict)
                                        else 1.0
                                    )
                                    _narr = _smc_runner.get_narrative(
                                        symbol,
                                        tf_s,
                                        float(_last_c),
                                        float(_atr_est),
                                    )
                                    if _narr is not None:
                                        from core.smc.narrative import (
                                            narrative_to_wire,
                                        )

                                        frame["narrative"] = narrative_to_wire(_narr)
                                except Exception as _narr_exc:
                                    _log.debug(
                                        "WS_DELTA_NARRATIVE_ERR sym=%s err=%s",
                                        symbol,
                                        _narr_exc,
                                    )
                            # ADR-0035: inject fresh session levels in delta
                            try:
                                _sess_lvls = cast(
                                    Any, _smc_runner
                                ).get_session_levels_wire(symbol)
                                if _sess_lvls:
                                    frame["session_levels"] = _sess_lvls
                            except Exception:
                                pass  # session levels are best-effort in delta

                        d1_relay_tfs_2: set = app.get(APP_D1_TICK_RELAY_TFS, set())
                        if tf_s in d1_relay_tfs_2:
                            for c in candles:
                                if c.get("complete", False) or c.get("src") not in (
                                    "tick_relay",
                                    None,
                                ):
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
                            frame,
                            tuple(active_recipients),
                            sessions,
                        )
                        _log.info(
                            "WS_BROADCAST_METRICS sym=%s tf=%s subs=%d t_ser_ms=%.2f t_send_ms=%.2f",
                            symbol,
                            tf_label,
                            len(active_recipients),
                            t_ser_ms,
                            t_send_ms,
                        )

                except Exception as loop_exc:
                    _log.warning(
                        "WS_GLOBAL_DELTA_ERR target=%s:%s err=%s",
                        symbol,
                        tf_s,
                        loop_exc,
                    )

            # Purge forming_by_target for keys with no subscribers (memory hygiene)
            stale_keys = [k for k in forming_by_target if k not in subs_by_target]
            for k in stale_keys:
                forming_by_target.pop(k, None)

            # ── ADR-0035: M1 feed for session H/L live tracking ──
            # Poll M1 updates for each active symbol and feed to SmcRunner.
            # This ensures session levels update as new M1 bars complete,
            # even when M1 is not a subscribed display TF.
            _smc_runner_m1 = app[APP_SMC_RUNNER] if APP_SMC_RUNNER in app else None
            if _smc_runner_m1 is not None:
                active_symbols = set(sym for sym, _ in subs_by_target.keys())
                for sym in active_symbols:
                    try:
                        m1_seq = _m1_cursor_by_sym.get(sym)
                        m1_result = await _uds_read_updates(app, sym, 60, m1_seq, False)
                        if m1_result is None:
                            continue
                        m1_events = getattr(m1_result, "events", [])
                        m1_cursor = getattr(m1_result, "cursor_seq", 0)
                        _m1_cursor_by_sym[sym] = m1_cursor
                        for ev in m1_events:
                            if isinstance(ev, dict) and ev.get("complete"):
                                bar = ev.get("bar")
                                if isinstance(bar, dict):
                                    cast(Any, _smc_runner_m1).feed_m1_bar_dict(sym, bar)
                    except Exception as m1_exc:
                        _log.debug("WS_M1_SESSION_FEED_ERR sym=%s err=%s", sym, m1_exc)

    except asyncio.CancelledError:
        _log.debug("WS_GLOBAL_DELTA_CANCELLED")
        pass


# ── WS Handler ─────────────────────────────────────────


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=None)
    await ws.prepare(request)

    session = WsSession(ws)
    app = request.app
    sessions: Dict[str, WsSession] = app[APP_WS_SESSIONS]
    sessions[session.client_id] = session

    _log.info("WS_CONNECT client_id=%s remote=%s", session.client_id, request.remote)

    # P2: set default symbol/tf from config, send full frame
    cfg = app.get(APP_FULL_CONFIG, {})
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

    uds = app[APP_UDS] if APP_UDS in app else None
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
    hb_interval = app.get(APP_HEARTBEAT_S, DEFAULT_HEARTBEAT_S)

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
                    _log.debug(
                        "WS_HEARTBEAT_SEND_FAIL client_id=%s",
                        session.client_id,
                        exc_info=True,
                    )
                    break
        except asyncio.CancelledError:
            _log.debug("WS_HEARTBEAT_CANCELLED client_id=%s", session.client_id)
            pass

    hb_task = asyncio.ensure_future(_heartbeat_loop())

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await _handle_action(session, msg.data, app)
            elif msg.type == WSMsgType.ERROR:
                _log.warning(
                    "WS_ERROR client_id=%s err=%s",
                    session.client_id,
                    ws.exception(),
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


_MAX_WS_MSG_BYTES = 65536  # SEC-05: 64 KB limit для WS повідомлень


def _sanitize_log(value: str, max_len: int = 120) -> str:
    """SEC-03: видаляє control characters для безпечного логування."""
    import re as _re

    if not isinstance(value, str):
        return str(value)[:max_len]
    return _re.sub(r"[\x00-\x1f\x7f]", "", value)[:max_len]


async def _handle_action(session: WsSession, raw: str, app: web.Application) -> None:
    """Розбирає вхідне повідомлення від клієнта. P2: switch + scrollback.

    S20: JSON/schema errors → error frame клієнту (degraded-but-loud).
    S25: Unknown action → error frame + лог (не silent ignore).
    """
    # SEC-05: message size guard
    if len(raw) > _MAX_WS_MSG_BYTES:
        _log.warning(
            "WS_ACTION_OVERSIZED client=%s size=%d", session.client_id, len(raw)
        )
        err = _build_error_frame(
            session, "message_too_large", "Message exceeds 64 KB limit", app=app
        )
        await session.ws.send_json(err)
        return
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        _log.warning(
            "WS_ACTION_INVALID client=%s reason=json_error raw=%.200s",
            session.client_id,
            raw,
        )
        err = _build_error_frame(session, "json_parse_error", "Invalid JSON", app=app)
        await session.ws.send_json(err)
        return
    if not isinstance(data, dict) or "action" not in data:
        _log.warning(
            "WS_ACTION_INVALID client=%s reason=missing_action raw=%.200s",
            session.client_id,
            raw,
        )
        err = _build_error_frame(
            session, "missing_action", "Message must have 'action' field", app=app
        )
        await session.ws.send_json(err)
        return
    action = data.get("action")
    _log.info("WS_ACTION client=%s action=%s", session.client_id, action)

    if action == "switch":
        await _handle_switch(session, data, app)
    elif action == "scrollback":
        await _handle_scrollback(session, data, app)
    else:
        # S25: unknown action → error frame (degraded-but-loud, не silent ignore)
        _log.warning("WS_ACTION_UNKNOWN client=%s action=%s", session.client_id, action)
        err = _build_error_frame(
            session, "unknown_action", "Unknown action: %s" % action, app=app
        )
        await session.ws.send_json(err)


async def _handle_switch(
    session: WsSession, data: Dict[str, Any], app: web.Application
) -> None:
    """Обробка switch action: змінити symbol/tf → новий full frame."""
    symbols_set: set = app.get(APP_SYMBOLS_SET, set())
    tf_allowlist: set = app.get(APP_TF_ALLOWLIST, set())

    raw_symbol = str(data.get("symbol", session.symbol or ""))
    raw_tf = str(data.get("tf", data.get("tf_s", "")))

    # Validate symbol
    symbol = _canonicalize_symbol(raw_symbol, symbols_set)
    if symbol not in symbols_set and symbols_set:
        _log.warning(
            "WS_SWITCH_REJECT client=%s reason=unknown_symbol raw=%s",
            session.client_id,
            _sanitize_log(raw_symbol),
        )
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
            _log.debug("WS_SWITCH_TF_PARSE_FAIL raw_tf=%r", raw_tf, exc_info=True)
            tf_s = None
    if tf_s is None or (tf_allowlist and tf_s not in tf_allowlist):
        _log.warning(
            "WS_SWITCH_REJECT client=%s reason=tf_not_allowed raw=%s",
            session.client_id,
            _sanitize_log(raw_tf),
        )
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
        old_sym,
        _TF_S_TO_LABEL.get(old_tf or 0, "?"),
        symbol,
        _TF_S_TO_LABEL.get(tf_s, "?"),
    )

    await _send_full_frame(session, app)


async def _handle_scrollback(
    session: WsSession, data: Dict[str, Any], app: web.Application
) -> None:
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
            session.client_id,
            SCROLLBACK_MAX_STEPS,
        )
        frame = _build_scrollback_frame(
            session,
            [],
            session.symbol,
            tf_label,
            ["scrollback_max_steps_reached"],
            app=app,
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
            session,
            [],
            session.symbol,
            tf_label,
            ["scrollback_throttled"],
            app=app,
        )
        await session.ws.send_json(frame)
        return

    to_ms = data.get("to_ms")
    if not isinstance(to_ms, (int, float)) or to_ms <= 0:
        _log.warning(
            "WS_SCROLLBACK_REJECT client=%s reason=bad_to_ms", session.client_id
        )
        frame = _build_scrollback_frame(
            session, [], session.symbol, tf_label, ["bad_to_ms"], app=app
        )
        await session.ws.send_json(frame)
        return
    to_ms = int(to_ms)
    session._scrollback_count += 1
    session._scrollback_last_ts = now
    cfg = app.get(APP_FULL_CONFIG, {})
    # Scrollback chunk: менше ніж cold start
    limit = min(_cold_start_limit(session.tf_s, cfg), 500)
    warnings: list = []
    try:
        result = await _uds_read_window(
            app, session.symbol, session.tf_s, limit, to_open_ms=to_ms
        )
        if result is None:
            warnings.append("uds_unavailable")
            frame = _build_scrollback_frame(
                session, [], session.symbol, tf_label, warnings, app=app
            )
            await session.ws.send_json(frame)
            return
        bars = getattr(result, "bars_lwc", [])
        candles, dropped = map_bars_to_candles_v4(bars, tf_s=session.tf_s or 0)
        if dropped > 0:
            warnings.append("candle_map_dropped:%d" % dropped)
        warnings.extend(getattr(result, "warnings", []))
        frame = _build_scrollback_frame(
            session, candles, session.symbol, tf_label, warnings or None, app=app
        )
        await session.ws.send_json(frame)
        _log.info(
            "WS_SCROLLBACK_PUSH client=%s symbol=%s bars=%d to_ms=%d",
            session.client_id,
            session.symbol,
            len(candles),
            to_ms,
        )
    except Exception as exc:
        _log.warning("WS_SCROLLBACK_ERROR client=%s err=%s", session.client_id, exc)
        # Завжди відповідаємо пустим frame — інакше клієнт застрягне
        try:
            tf_label = _TF_S_TO_LABEL.get(session.tf_s, f"{session.tf_s}s")
            frame = _build_scrollback_frame(
                session, [], session.symbol, tf_label, ["scrollback_error"], app=app
            )
            await session.ws.send_json(frame)
        except Exception:
            _log.debug(
                "WS_SCROLLBACK_FALLBACK_SEND_FAIL client=%s",
                session.client_id,
                exc_info=True,
            )
            pass


# ── UDS init ───────────────────────────────────────────


def _init_uds(app: web.Application, config_path: str, cfg: Dict[str, Any]) -> None:
    """Ініціалізує UDS reader. W0: role='reader', writer_components=False."""
    try:
        from runtime.store.uds import build_uds_from_config
        import os

        data_root = cfg.get("data_root", "./data_v3")
        boot_id = app[APP_BOOT_ID]
        uds = build_uds_from_config(
            os.path.abspath(config_path),
            data_root,
            boot_id,
            role="reader",
            writer_components=False,
        )
        app[APP_UDS] = uds
        _log.info("WS_UDS_INIT role=reader boot_id=%s", boot_id)
    except Exception as exc:
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
    app[APP_HEARTBEAT_S] = int(ws_cfg.get("heartbeat_interval_s", DEFAULT_HEARTBEAT_S))
    app[APP_DELTA_POLL_S] = float(
        ws_cfg.get("delta_poll_interval_s", DEFAULT_DELTA_POLL_S)
    )
    app[APP_WS_SESSIONS] = {}
    app[APP_CONFIG_PATH] = config_path
    app[APP_BOOT_ID] = uuid.uuid4().hex[:16]
    app[APP_FULL_CONFIG] = full_cfg

    # Symbol/TF sets from config (T10: imports at top-level)
    symbols_list = full_cfg.get("symbols", [])
    app[APP_SYMBOLS_SET] = set(str(s) for s in symbols_list)
    app[APP_TF_ALLOWLIST] = tf_allowlist_from_cfg(full_cfg)
    preview_set, _ = preview_tf_allowlist_from_cfg(full_cfg)
    app[APP_PREVIEW_TF_SET] = preview_set

    # ADR-0012 P3: D1 live tick relay — Redis client + config flags
    _d1_relay_enabled = bool(
        full_cfg.get("d1_live_tick_relay_enabled", _D1_TICK_RELAY_ENABLED_DEFAULT)
    )
    _d1_relay_tfs_raw = full_cfg.get("d1_live_tick_relay_tfs_s", [])
    app[APP_D1_TICK_RELAY_TFS] = (
        set(int(x) for x in _d1_relay_tfs_raw) if _d1_relay_enabled else set()
    )
    app[APP_TICK_REDIS_NS] = ""
    _d1_relay_tfs = app[APP_D1_TICK_RELAY_TFS]
    if _d1_relay_enabled and _d1_relay_tfs:
        try:
            from runtime.store.redis_spec import resolve_redis_spec
            import redis as _redis_lib

            spec = resolve_redis_spec(full_cfg, role="tick_relay", log=False)
            if spec is not None:
                app[APP_TICK_REDIS_CLIENT] = _redis_lib.Redis(
                    host=spec.host,
                    port=spec.port,
                    db=spec.db,
                    socket_timeout=2.0,
                    socket_connect_timeout=2.0,
                    decode_responses=False,
                )
                app[APP_TICK_REDIS_NS] = spec.namespace
                _log.info(
                    "D1_TICK_RELAY_INIT enabled=1 tfs=%s ns=%s",
                    app[APP_D1_TICK_RELAY_TFS],
                    spec.namespace,
                )
        except Exception as relay_exc:
            _log.warning("D1_TICK_RELAY_INIT_FAILED err=%s (disabled)", relay_exc)

    # Dedicated thread pool for UDS blocking I/O (limit thread explosion)
    # min(4, cpu_count) — 2 було недостатньо для паралельних /api/bars + /api/updates
    import os as _os

    _uds_workers = min(4, _os.cpu_count() or 4)
    app[APP_UDS_EXECUTOR] = ThreadPoolExecutor(
        max_workers=_uds_workers, thread_name_prefix="uds"
    )

    # P2: UDS reader (W0: role="reader")
    if uds is not None:
        app[APP_UDS] = uds
    else:
        _init_uds(app, config_path, full_cfg)

    # SMC Runner init (ADR-0024 §6.1) — in-process, same event loop as ws_server
    _smc_section = full_cfg.get("smc", {}) if isinstance(full_cfg, dict) else {}
    if _smc_section.get("enabled", False):
        try:
            from core.smc.config import SmcConfig
            from core.smc.engine import SmcEngine
            from runtime.smc.smc_runner import SmcRunner

            _smc_cfg = SmcConfig.from_dict(_smc_section)
            _smc_engine = SmcEngine(_smc_cfg)
            app[APP_SMC_RUNNER] = cast(Any, SmcRunner(full_cfg, _smc_engine))
            _log.info(
                "WS_SMC_RUNNER_INIT lookback=%d swing_period=%d",
                _smc_cfg.lookback_bars,
                _smc_cfg.swing_period,
            )
        except Exception as _smc_init_exc:
            _log.warning(
                "WS_SMC_RUNNER_INIT_FAILED err=%s — SMC disabled", _smc_init_exc
            )

    # ── CORS middleware (cross-origin: Vercel / Cloudflare Pages) ──────
    _cors_origins_raw = ws_cfg.get("cors_allowed_origins", [])
    _cors_origins = set(str(o).rstrip("/") for o in _cors_origins_raw if o)
    app[APP_CORS_ORIGINS] = _cors_origins
    if _cors_origins:
        _log.info("WS_CORS_ENABLED origins=%s", _cors_origins)

    @web.middleware
    async def cors_middleware(request, handler):
        origin = request.headers.get("Origin", "")
        allowed = app.get(APP_CORS_ORIGINS, set())
        # Якщо CORS не налаштований або origin не в списку — пропускаємо
        if not allowed or origin not in allowed:
            return await handler(request)
        # Preflight OPTIONS
        if request.method == "OPTIONS":
            resp = web.Response(status=204)
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers.update(_CORS_HEADERS_COMMON)
            return resp
        # Звичайний запит — додаємо CORS headers
        resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = origin
        return resp

    app.middlewares.append(cors_middleware)

    # ── /api/status — edge probe endpoint (для Vercel cross-origin) ────
    async def _api_status(request: web.Request) -> web.Response:
        uds_ok = APP_UDS in app
        sessions_count = len(app[APP_WS_SESSIONS])
        return web.json_response(
            {
                "status": "ok" if uds_ok else "no_uds",
                "boot_id": app[APP_BOOT_ID],
                "ws_clients": sessions_count,
                "server_ts_ms": int(time.time() * 1000),
            }
        )

    app.router.add_get("/api/status", _api_status)
    app.router.add_get("/ws", ws_handler)

    # ── Same-origin SPA serving (Правило §11: UI + API = один процес) ──
    # Роздача ui_v4/dist/ якщо dist існує (після npm run build)
    _ws_dir = os.path.dirname(os.path.abspath(__file__))
    _ui_dist = os.path.normpath(os.path.join(_ws_dir, "..", "..", "ui_v4", "dist"))
    _ui_src = os.path.normpath(os.path.join(_ws_dir, "..", "..", "ui_v4", "src"))
    _ui_index = os.path.join(_ui_dist, "index.html")
    # D7: stale dist/ detection — порівняти mtime dist/index.html vs max(src/**)
    if os.path.isfile(_ui_index) and os.path.isdir(_ui_src):
        try:
            _dist_mtime = os.path.getmtime(_ui_index)
            _src_mtime = (
                max(
                    os.path.getmtime(os.path.join(dp, f))
                    for dp, _, fns in os.walk(_ui_src)
                    for f in fns
                )
                if os.path.isdir(_ui_src)
                else 0
            )
            if _src_mtime > _dist_mtime:
                _log.warning(
                    "UI_V4_DIST_STALE dist/index.html older than src/ by %.0fs — "
                    "run 'cd ui_v4 && npm run build' to rebuild",
                    _src_mtime - _dist_mtime,
                )
        except Exception:
            _log.debug("UI_V4_DIST_STALE_CHECK_FAILED", exc_info=True)
            pass  # best-effort, не блокуємо старт
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
        app[APP_HEARTBEAT_S],
        app[APP_BOOT_ID],
        "ready" if APP_UDS in app else "none",
    )

    # Register global delta task lifecycle (ADR-0011)
    async def _start_bg_tasks(app_ctx: web.Application) -> None:
        if APP_UDS in app_ctx:
            app_ctx[APP_GLOBAL_DELTA_TASK] = asyncio.ensure_future(
                _global_delta_loop(app_ctx)
            )
        # SMC warmup in executor (blocking UDS reads, не блокує event loop)
        _smc_r = app_ctx[APP_SMC_RUNNER] if APP_SMC_RUNNER in app_ctx else None
        _uds_r = app_ctx[APP_UDS] if APP_UDS in app_ctx else None
        _exec = app_ctx[APP_UDS_EXECUTOR]
        if _smc_r is not None and _uds_r is not None:
            asyncio.ensure_future(
                asyncio.get_event_loop().run_in_executor(_exec, _smc_r.warmup, _uds_r)
            )
            _log.info("WS_SMC_WARMUP_SCHEDULED")

    async def _cleanup_bg_tasks(app_ctx: web.Application) -> None:
        task = app_ctx.get(APP_GLOBAL_DELTA_TASK)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                _log.debug("WS_GLOBAL_DELTA_TASK_CANCELLED")
                pass

    app.on_startup.append(_start_bg_tasks)
    app.on_cleanup.append(_cleanup_bg_tasks)

    return app


# ── Port bind with retry (Windows TIME_WAIT resilience) ──

_BIND_MAX_RETRIES = 5
_BIND_RETRY_DELAY_S = 3.0


def _run_with_retry(
    app: web.Application,
    host: str,
    port: int,
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
                host,
                port,
                attempt,
            )
            break
        except OSError as exc:
            _log.warning(
                "WS_SERVER_BIND_RETRY port=%s attempt=%d/%d err=%s",
                port,
                attempt,
                max_retries,
                exc,
            )
            if attempt == max_retries:
                _log.error(
                    "WS_SERVER_BIND_FAILED port=%s after %d attempts",
                    port,
                    max_retries,
                )
                loop.run_until_complete(runner.cleanup())
                raise SystemExit(1)
            time.sleep(retry_delay)

    try:
        _log.info("WS_SERVER_READY ws://%s:%s/ws", host, port)
        loop.run_forever()
    except KeyboardInterrupt:
        _log.info("WS_SERVER_STOP keyboard_interrupt=1")
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
