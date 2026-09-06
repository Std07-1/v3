"""
runtime/ws/ws_server.py вЂ” aiohttp WebSocket СЃРµСЂРІРµСЂ РґР»СЏ ui_v4.

P1: skeleton + heartbeat.
P2: UDS reader integration (full frame, switch, delta, scrollback).

Р†РЅРІР°СЂС–Р°РЅС‚Рё:
  W0: WS-СЃРµСЂРІРµСЂ = UDS reader only (role="reader")
  W1: schema_v = "ui_v4_v2" РЅР° РєРѕР¶РЅРѕРјСѓ frame
  W2: meta.seq СЃС‚СЂРѕРіРѕ Р·СЂРѕСЃС‚Р°С” per-connection (heartbeat/full/delta/scrollback)
  W7: heartbeat РєРѕР¶РЅС– в‰¤30s

Р—Р°РїСѓСЃРє: python -m runtime.ws.ws_server --port 8000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
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
# ADR-0085 archi_chart: one-shot WARN per symbol (I5 без спаму — delta_loop кличе кожні 2s).
_ARCHI_CHART_THESIS_WARNED: set[str] = set()

# в”Ђв”Ђ Constants в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
SCHEMA_V = "ui_v4_v2"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_HEARTBEAT_S = 30
DEFAULT_DELTA_POLL_S = 2.0
DEFAULT_BG_SMC_POLL_S = 10.0
DEFAULT_COLD_START_BARS = 300


class RedisLike(Protocol):
    def get(self, name: str) -> Any: ...


class UdsLike(Protocol):
    def read_window(self, spec: Any, policy: Any) -> Any: ...

    def read_updates(self, spec: Any) -> Any: ...


class SmcRunnerLike(Protocol):
    _engine: Any  # SmcEngine вЂ” accessed by diagnostics endpoints

    def get_snapshot(self, symbol: str, tf_s: int) -> Any: ...

    def get_last_price(self, symbol: str) -> float: ...

    def get_zone_grades(self, symbol: str, tf_s: int) -> Any: ...

    def get_session_levels_wire(self, symbol: str) -> Any: ...

    def get_bias_map(self, symbol: str) -> Any: ...

    def get_momentum_map(self, symbol: str) -> Any: ...

    def get_pd_state(self, symbol: str, viewer_tf_s: int) -> Any: ...

    def get_narrative(self, symbol: str, tf_s: int, *args: Any) -> Any: ...

    def feed_m1_bar_dict(self, symbol: str, bar: Dict[str, Any]) -> None: ...

    def on_bar_dict(self, symbol: str, tf_s: int, bar: Dict[str, Any]) -> None: ...

    def last_delta(self, symbol: str, tf_s: int) -> Any: ...

    def clear_delta(self, symbol: str, tf_s: int) -> None: ...

    def get_shell_payload(
        self, symbol: str, tf_s: int, narrative: Any, signal: Any = None
    ) -> Any: ...

    def get_signals(
        self, symbol: str, tf_s: int, narrative: Any, price: float, atr: float
    ) -> Any: ...

    def warmup(self, uds: UdsLike) -> None: ...


# AppKey-визначення винесені в runtime.ws.app_keys (SSOT, fix для __main__/module
# double-import bug — див. docstring у app_keys.py та changelog 2026-05-06).
from runtime.ws.app_keys import (  # noqa: E402
    APP_BG_SMC_TASK,
    APP_BOOT_ID,
    APP_CONFIG_PATH,
    APP_CORS_ORIGINS,
    APP_D1_TICK_RELAY_TFS,
    APP_DELTA_POLL_S,
    APP_FULL_CONFIG,
    APP_GLOBAL_DELTA_TASK,
    APP_HEARTBEAT_S,
    APP_PREVIEW_TF_SET,
    APP_SMC_RUNNER,
    APP_SYMBOLS_SET,
    APP_TF_ALLOWLIST,
    APP_TICK_REDIS_CLIENT,
    APP_TICK_REDIS_NS,
    APP_UDS,
    APP_UDS_EXECUTOR,
    APP_WAKE_ENGINE,
    APP_WS_LIMITS,
    APP_AGENT_BRIDGE_CFG,
    APP_WS_SESSIONS,
)
from runtime.agent_bridge.config import (  # noqa: E402
    AGENT_BRIDGE_ENABLED_ENV,
    resolve_agent_bridge_config,
)

# CORS: РґРѕР·РІРѕР»РµРЅС– origins РґР»СЏ cross-origin (Vercel / Cloudflare Pages)
# РљРѕРЅС„С–Рі: ws_server.cors_allowed_origins РІ config.json
# РЇРєС‰Рѕ СЃРїРёСЃРѕРє РїРѕСЂРѕР¶РЅС–Р№ вЂ” CORS headers РЅРµ РґРѕРґР°СЋС‚СЊСЃСЏ (same-origin СЂРµР¶РёРј)
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
SCROLLBACK_MAX_STEPS = 12  # РјР°РєСЃ С‡Р°РЅРєС–РІ scrollback per session per symbol+tf
SCROLLBACK_COOLDOWN_S = 0.5  # РјС–РЅС–РјР°Р»СЊРЅРёР№ С–РЅС‚РµСЂРІР°Р» РјС–Р¶ scrollback РІС–Рґ РѕРґРЅРѕРіРѕ РєР»С–С”РЅС‚Р°

# SEC-06: WS connection/action rails — SSOT-дефолти; override через config.json:ws_server.*
WS_MAX_CLIENTS_DEFAULT = 200  # одночасних WS-сесій на процес (0 = без ліміту)
WS_MAX_CLIENTS_PER_IP_DEFAULT = 8  # сесій з однієї адреси (X-Real-IP від nginx після realip)
WS_ACTIONS_BURST_DEFAULT = 10  # token bucket: burst дій на сесію (0 = без ліміту)
WS_ACTIONS_PER_S_DEFAULT = 2.0  # token bucket: refill дій/с
WS_SWITCH_COOLDOWN_S_DEFAULT = 1.0  # мінімум між switch: кожен = cold-start read + SMC snapshot


@dataclass(frozen=True)
class WsLimits:
    """SEC-06: rails WS-сервера (нуль = відповідний ліміт вимкнено)."""

    max_clients: int = WS_MAX_CLIENTS_DEFAULT
    max_clients_per_ip: int = WS_MAX_CLIENTS_PER_IP_DEFAULT
    actions_burst: int = WS_ACTIONS_BURST_DEFAULT
    actions_per_s: float = WS_ACTIONS_PER_S_DEFAULT
    switch_cooldown_s: float = WS_SWITCH_COOLDOWN_S_DEFAULT


def _ws_limits_from_cfg(ws_cfg: Dict[str, Any]) -> WsLimits:
    """config.json:ws_server.* → WsLimits. Відсутній ключ = дефолт; сміття = дефолт + WARN (I5)."""

    def _num(key: str, default: Any, cast: Any) -> Any:
        raw = ws_cfg.get(key, default)
        try:
            return cast(raw)
        except (TypeError, ValueError):
            _log.warning("WS_LIMITS_CONFIG_INVALID key=%s value=%r default=%r", key, raw, default)
            return default

    return WsLimits(
        max_clients=_num("max_clients", WS_MAX_CLIENTS_DEFAULT, int),
        max_clients_per_ip=_num("max_clients_per_ip", WS_MAX_CLIENTS_PER_IP_DEFAULT, int),
        actions_burst=_num("actions_burst", WS_ACTIONS_BURST_DEFAULT, int),
        actions_per_s=_num("actions_per_s", WS_ACTIONS_PER_S_DEFAULT, float),
        switch_cooldown_s=_num("switch_cooldown_s", WS_SWITCH_COOLDOWN_S_DEFAULT, float),
    )


def _client_ip(request: web.Request) -> str:
    """Реальна адреса клієнта: X-Real-IP ставить nginx (після realip_cloudflare), інакше peer."""
    return (request.headers.get("X-Real-IP") or request.remote or "?").strip()

# TF label в†” seconds mapping (types.ts WsAction.switch.tf)
# Canonical labels: uppercase M1, M5, H1 etc. (СЏРє Сѓ С„СЂРѕРЅС‚РµРЅРґС– SymbolTfPicker)
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

# в”Ђв”Ђ Helpers в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ


def _load_full_config(config_path: str) -> Dict[str, Any]:
    """Р—Р°РІР°РЅС‚Р°Р¶СѓС” РїРѕРІРЅРёР№ config.json С‡РµСЂРµР· core.config_loader (T10/S26 SSOT)."""
    try:
        resolved = resolve_config_path(config_path)
        return load_system_config(resolved)
    except Exception as exc:
        _log.warning("WS_CONFIG load_error=%s path=%s", exc, config_path)
        return {}


def _canonicalize_symbol(raw: str, symbols: set) -> str:
    """РќРѕСЂРјР°Р»С–Р·СѓС” СЃРёРјРІРѕР»: EUR_USD в†’ EUR/USD."""
    if raw in symbols:
        return raw
    if "_" in raw:
        canon = raw.replace("_", "/")
        if canon in symbols:
            return canon
    return raw


def _cold_start_limit(tf_s: int, cfg: Dict[str, Any]) -> int:
    """РџРѕРІРµСЂС‚Р°С” РєС–Р»СЊРєС–СЃС‚СЊ Р±Р°СЂС–РІ РґР»СЏ cold start РїРѕ TF."""
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


# в”Ђв”Ђ Output Guard (T6/S19: WS candle shape + monotonicity) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ


def _guard_candle_shape(candle: dict) -> Optional[str]:
    """Р’Р°Р»С–РґСѓС” РѕРґРёРЅ v4 Candle dict. РџРѕРІРµСЂС‚Р°С” issue string Р°Р±Рѕ None СЏРєС‰Рѕ OK.

    РљРѕРЅС‚СЂР°РєС‚: types.ts Candle {t_ms: int, o: float, h: float, l: float, c: float, v: float}
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
    """Output guard РґР»СЏ РјР°СЃРёРІСѓ candles РїРµСЂРµРґ РІС–РґРїСЂР°РІРєРѕСЋ РєР»С–С”РЅС‚Сѓ (T6/S19).

    - Р”СЂРѕРїР°С” candles Р· РїРѕРіР°РЅРѕСЋ С„РѕСЂРјРѕСЋ (degraded-but-loud).
    - РџРµСЂРµРІС–СЂСЏС” РјРѕРЅРѕС‚РѕРЅРЅС–СЃС‚СЊ t_ms (no duplicates, sorted asc).
    - РџРѕРІРµСЂС‚Р°С” СЃРїРёСЃРѕРє warnings.

    РњСѓС‚СѓС” candles in-place (РІРёРґР°Р»СЏС” bad).
    """
    warnings: list = []
    if not candles:
        return warnings

    # в”Ђв”Ђ Pass 1: shape guard вЂ” РґСЂРѕРїР°С”РјРѕ РїРѕРіР°РЅС– в”Ђв”Ђ
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

    # в”Ђв”Ђ Pass 2: РјРѕРЅРѕС‚РѕРЅРЅС–СЃС‚СЊ t_ms (sorted asc, no dup) в”Ђв”Ђ
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


# -- Session ----------------------------------------------------------------


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
        "client_ip",
        "_action_tokens",
        "_action_refill_ts",
        "_switch_last_ts",
    )

    def __init__(self, ws: web.WebSocketResponse) -> None:
        self.client_id: str = uuid.uuid4().hex[:12]
        self.seq: int = 0
        self.symbol: Optional[str] = None
        self.tf_s: Optional[int] = None
        self.last_update_seq: Optional[int] = 0
        self.ws: web.WebSocketResponse = ws
        self._scrollback_count: int = (
            0  # P11: РєС–Р»СЊРєС–СЃС‚СЊ scrollback РґР»СЏ РїРѕС‚РѕС‡РЅРѕРіРѕ symbol+tf
        )
        self._scrollback_last_ts: float = (
            0  # P11: timestamp РѕСЃС‚Р°РЅРЅСЊРѕРіРѕ scrollback
        )
        self.client_ip: str = "?"  # SEC-06: для per-IP ліміту (X-Real-IP)
        self._action_tokens: float = float(WS_ACTIONS_BURST_DEFAULT)  # SEC-06 token bucket
        self._action_refill_ts: float = 0.0
        self._switch_last_ts: float = 0.0  # SEC-06: cooldown між switch

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq


# в”Ђв”Ђ Frame builders в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ


def _archi_chart_wire(app: Any, symbol: str) -> Optional[Dict[str, Any]]:
    """ADR-0085 P1: зібрати `frame.archi_chart` — числові будильники Арчі.

    Джерело: WakeEngine._bot_conditions кеш (30с TTL) через get_bot_conditions
    — НУЛЬ нових Redis I/O. Лише source=="bot" + чартові kind-и (фільтр у
    getter-і). Сортування: найсвіжіші перші; cap = config max_lines.
    Повертає None ЛИШЕ коли вимкнено/нема двигуна (поле відсутнє у frame).
    Умов нема → {"conditions": []} — порожній маркер їде у wire, щоб зняття
    будильника Арчі пропагувалось НЕГАЙНО (UI чистить шар), не чекаючи full.
    Corrupt-параметри умови → skip item (деградація per-item, лог debug — I5).
    """
    try:
        _bridge = app.get(APP_AGENT_BRIDGE_CFG)
        if _bridge is None:
            return None
        cfg = _bridge.wake_engine.get("archi_chart", {})
        if not cfg.get("enabled", True):
            return None
        we = app.get(APP_WAKE_ENGINE)
        if we is None:
            return None

        # ADR-0085 P4: числові рівні тези (optional, з enricher-кешу).
        thesis_levels: Optional[Dict[str, Any]] = None
        _enr = app.get("_narrative_enricher")
        if _enr is not None:
            try:
                thesis_levels = _enr.get_thesis_chart_levels(symbol)
            except Exception as exc:
                thesis_levels = None
                if symbol not in _ARCHI_CHART_THESIS_WARNED:
                    _ARCHI_CHART_THESIS_WARNED.add(symbol)
                    _log.warning(
                        "ARCHI_CHART_THESIS_LEVELS_FAIL sym=%s err=%s", symbol, exc
                    )

        conds = sorted(
            we.get_bot_conditions(symbol),
            key=lambda c: c.created_at_ms,
            reverse=True,
        )
        max_lines = int(cfg.get("max_lines", 6))
        out: list[Dict[str, Any]] = []
        for c in conds[:max_lines]:
            p = c.params or {}
            item: Dict[str, Any] = {
                "kind": c.kind.value,
                "reason": (c.reason or "")[:140],
                "created_at_ms": int(c.created_at_ms or 0),
            }
            try:
                if c.kind.value == "price_cross":
                    item["level"] = float(p["level"])
                    item["direction"] = str(p.get("direction", ""))
                elif c.kind.value == "price_zone_touch":
                    item["zone_high"] = float(p["zone_high"])
                    item["zone_low"] = float(p["zone_low"])
                elif c.kind.value == "candle_close":
                    item["level"] = float(p["level"])
                    item["direction"] = str(p.get("direction", ""))
                    item["tf_s"] = int(p.get("tf_s", 0))
            except (KeyError, TypeError, ValueError) as bad:
                _log.debug("ARCHI_CHART_BAD_COND sym=%s kind=%s: %s", symbol, c.kind.value, bad)
                continue
            out.append(item)
        # Поле присутнє, якщо є будильники АБО числові рівні тези.
        if not out and thesis_levels is None:
            return {"conditions": []}  # порожній маркер — чистить шар (D1)
        result: Dict[str, Any] = {"conditions": out}
        if thesis_levels is not None:
            result.update(thesis_levels)
        return result
    except Exception:
        _log.debug("ARCHI_CHART_WIRE_ERR sym=%s", symbol, exc_info=True)
        return None


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
    """S20/S25: error response frame вЂ” degraded-but-loud РґР»СЏ РєР»С–С”РЅС‚Р°."""
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
    pd_state: Optional[Dict] = None,
    atr: Optional[float] = None,
    rv: Optional[float] = None,
) -> Dict[str, Any]:
    # T6/S19: output guard вЂ” validate candle shapes before send
    guard_warns = _guard_candles_output(candles, symbol, tf_label, "full")
    meta = _build_meta(session, app=app)
    all_warnings = list(warnings or []) + guard_warns
    if all_warnings:
        meta["warnings"] = all_warnings
    # P1в†’P2: config payload вЂ” UI С‡РёС‚Р°С” symbols/tfs Р· СЃРµСЂРІРµСЂР° (SSOT)
    # tfs = canonical labels (["M1","M3",...]) вЂ” UI switch РЅР°РґСЃРёР»Р°С” СЃР°РјРµ labels
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
    # ADR-0053: range_exhaustion (ATR travel gauge) — symbol-scoped, surfaces in full frame
    if smc_wire and smc_wire.get("range_exhaustion"):
        frame["range_exhaustion"] = smc_wire["range_exhaustion"]
    # ADR-0031: multi-TF bias map (only in full frame)
    if bias_map:
        frame["bias_map"] = bias_map
    if momentum_map:
        frame["momentum_map"] = momentum_map
    if pd_state:
        frame["pd_state"] = pd_state
    # X28-fix: surface backend ATR (engine.get_atr) so UI consumes SSOT
    # instead of recomputing from raw candles. Same source as REST
    # /api/context.atr endpoint (runtime/api_v3/endpoints.py:1335).
    if atr is not None:
        frame["atr"] = atr
    # ADR-0070 amendment: backend SSOT for relative volume (X28).
    # Frontend MUST consume frame["rv"] as-is — no re-derivation from raw
    # candle volumes. 1.0 = neutral / no signal (RV convention).
    if rv is not None:
        frame["rv"] = rv
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
    """T8/S24: dedicated config frame вЂ” policy bridge for UI.

    Р’С–РґРїСЂР°РІР»СЏС”С‚СЊСЃСЏ РѕРґСЂР°Р·Сѓ РїСЂРё connect, РґРѕ full frame.
    UI РѕС‚СЂРёРјСѓС” symbols/tfs/defaults РЅР°РІС–С‚СЊ СЏРєС‰Рѕ UDS РЅРµРґРѕСЃС‚СѓРїРЅРёР№.
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


# в”Ђв”Ђ UDS async wrappers (blocking I/O в†’ executor) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ


async def _uds_read_window(
    app: web.Application,
    symbol: str,
    tf_s: int,
    limit: int,
    to_open_ms: Optional[int] = None,
) -> Any:
    """Async wrapper РґР»СЏ UDS read_window (blocking Redis/Disk I/O)."""
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
    # P11: scrollback (to_open_ms) = "explicit" (disk РґРѕР·РІРѕР»РµРЅРѕ Р·Р°РІР¶РґРё);
    #      cold-start/switch = "bootstrap" (disk С‚С–Р»СЊРєРё РІ bootstrap РІС–РєРЅС–).
    # P2: disk_policy="explicit" РґР»СЏ РІСЃС–С… reads вЂ” ws_server РѕРєСЂРµРјРёР№ РїСЂРѕС†РµСЃ,
    # Р№РѕРіРѕ RAM/Redis РјРѕР¶СѓС‚СЊ Р±СѓС‚Рё stale. Disk Р·Р°РІР¶РґРё Р°РєС‚СѓР°Р»СЊРЅРёР№.
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
    """Async wrapper РґР»СЏ UDS read_updates (blocking)."""
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
    """Р§РёС‚Р°С” UDS read_window в†’ map в†’ full frame в†’ send."""
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
        # SMC: inject snapshot into full frame (ADR-0024 В§6.1)
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
        pd_state = None  # ADR-0041: P/D state for badge + EQ line
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
            # ADR-0041: P/D state for badge + EQ line
            try:
                pd_state = _smc_runner.get_pd_state(session.symbol, session.tf_s)
            except Exception as _pd_exc:
                _log.warning("WS_PD_STATE_ERR sym=%s err=%s", session.symbol, _pd_exc)
        # X28-fix: surface backend ATR (peripheral chrome context).
        # Engine returns 1.0 fallback when state has no bars; we pass through
        # so UI shows what backend canonically sees. Cross-checks REST
        # /api/context.atr (same source: runner._engine.get_atr).
        atr_val: Optional[float] = None
        rv_val: Optional[float] = None
        if _smc_runner is not None:
            try:
                atr_val = float(
                    _smc_runner._engine.get_atr(session.symbol, session.tf_s)
                )
            except Exception as _atr_exc:
                _log.debug(
                    "WS_ATR_ERR sym=%s tf=%s err=%s",
                    session.symbol,
                    session.tf_s,
                    _atr_exc,
                )
            # ADR-0070 amendment: backend SSOT for relative volume (X28).
            # 1.0 fallback (neutral) when no data or null/zero last-bar volume.
            try:
                rv_val = float(
                    _smc_runner._engine.get_rv(session.symbol, session.tf_s)
                )
            except Exception as _rv_exc:
                _log.debug(
                    "WS_RV_ERR sym=%s tf=%s err=%s",
                    session.symbol,
                    session.tf_s,
                    _rv_exc,
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
            pd_state=pd_state,
            atr=atr_val,
            rv=rv_val,
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
                    # ADR-0049: enrich narrative with thesis + presence
                    _wake_eng_enr = app.get(APP_WAKE_ENGINE)
                    if _wake_eng_enr is not None and isinstance(
                        frame.get("narrative"), dict
                    ):
                        try:
                            from runtime.smc.narrative_enricher import NarrativeEnricher

                            _enricher = app.get("_narrative_enricher")
                            if _enricher is not None:
                                _pres = _wake_eng_enr.get_presence(session.symbol)
                                frame["narrative"] = _enricher.enrich_narrative(
                                    frame["narrative"],
                                    session.symbol,
                                    presence=_pres,
                                    tier="free",
                                )
                        except Exception as _enr_exc:
                            _log.debug("NARRATIVE_ENRICH_ERR: %s", _enr_exc)
                    # ADR-0085 P1: числові будильники Арчі на чарт
                    _ac = _archi_chart_wire(app, session.symbol)
                    if _ac is not None:
                        frame["archi_chart"] = _ac
                    # ADR-0039: signal engine wiring (before shell, so signal feeds shell)
                    _primary_sig = None
                    try:
                        _sigs, _sig_alerts = _smc_runner.get_signals(
                            session.symbol,
                            session.tf_s,
                            _narr,
                            float(_last_c),
                            float(_atr_est),
                        )
                        if _sigs:
                            frame["signals"] = [s.to_wire() for s in _sigs]
                            _primary_sig = _sigs[0]
                        if _sig_alerts:
                            frame["signal_alerts"] = [a.to_wire() for a in _sig_alerts]
                    except Exception as _sig_exc:
                        _log.warning(
                            "WS_SIGNAL_ERR sym=%s err=%s", session.symbol, _sig_exc
                        )
                    # ADR-0036: shell payload (post-processing narrative)
                    try:
                        _shell = _smc_runner.get_shell_payload(
                            session.symbol,
                            session.tf_s,
                            _narr,
                            signal=_primary_sig,
                        )
                        if _shell is not None:
                            frame["shell"] = _shell.to_wire()
                    except Exception as _shell_exc:
                        _log.warning(
                            "WS_SHELL_ERR sym=%s err=%s", session.symbol, _shell_exc
                        )
            except Exception as _narr_exc:
                _log.warning(
                    "WS_NARRATIVE_ERR sym=%s err=%s", session.symbol, _narr_exc
                )
        await session.ws.send_json(frame)
        _log.debug(
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
    """ADR-0011 BC5+BC6: broadcast Р· per-client seq + timeout + degraded-but-loud.

    Р”Р»СЏ РєРѕР¶РЅРѕРіРѕ РєР»С–С”РЅС‚Р°: С–РЅР¶РµРєС‚РёС‚СЊ session.next_seq() РІ meta.seq,
    СЃРµСЂС–Р°Р»С–Р·СѓС”, РІС–РґРїСЂР°РІР»СЏС” Р· timeout. РџРѕРІРµСЂС‚Р°С” t_send_ms.
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
                _log.debug(
                    "WS_CLOSE_CLEANUP_FAIL client_id=%s", s.client_id, exc_info=True
                )
    return t_send_ms


def _seed_forming_from_uds(
    app: web.Application,
    symbol: str,
    tf_s: int,
    bucket_open_ms: int,
    fallback_price: float,
) -> Dict[str, Any]:
    """Seed forming candle Р· UDS (РїРѕС‚РѕС‡РЅРёР№ Р±Р°СЂ РґР»СЏ bucket_open_ms).

    РџС–СЃР»СЏ СЂРµСЃС‚Р°СЂС‚Сѓ forming_by_target = {}. Р‘РµР· seed open = РїРµСЂС€РёР№ С‚С–Рє
    (С…РёР±РЅРёР№ D1 open). РЇРєС‰Рѕ UDS РјР°С” Р±Р°СЂ РґР»СЏ С†СЊРѕРіРѕ bucket вЂ” Р±РµСЂРµС‚СЊСЃСЏ O/H/L.
    РЇРєС‰Рѕ UDS С‰Рµ РїРѕСЂРѕР¶РЅС–Р№ вЂ” fallback РЅР° tick_price (degraded-but-loud).
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
    # Fallback: РЅРµРјР°С” UDS РґР°РЅРёС… в†’ С‡РёСЃС‚РёР№ С‚С–Рє (degraded-but-loud)
    _log.warning(
        "D1_FORMING_NO_SEED sym=%s open_ms=%d price=%.2f "
        "вЂ” open Р±СѓРґРµ РїРµСЂС€РёРј С‚С–РєРѕРј РїС–СЃР»СЏ СЂРµСЃС‚Р°СЂС‚Сѓ",
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
    """ADR-0011: Global Background task: poll UDS read_updates в†’ serialize once в†’ fanout."""
    poll_s = app.get(APP_DELTA_POLL_S, DEFAULT_DELTA_POLL_S)
    preview_tfs: set = app.get(APP_PREVIEW_TF_SET, set())
    forming_by_target: Dict[tuple[str, int], Dict[str, Any]] = (
        {}
    )  # ADR-0012 P3 global forming tracking
    # ADR-0035: M1 cursor per symbol for session H/L live feed
    _m1_cursor_by_sym: Dict[str, Optional[int]] = {}

    # O3-sleep: dedicated Redis client for viewer signal (lightweight, best-effort)
    _viewer_redis = None
    _viewer_ns = "v3_local"
    try:
        from runtime.store.redis_spec import resolve_redis_spec
        import redis as _redis_mod

        _v_spec = resolve_redis_spec(
            app.get(APP_FULL_CONFIG, {}), role="viewer_signal", log=False
        )
        if _v_spec is not None:
            _viewer_redis = _redis_mod.Redis(
                host=_v_spec.host,
                port=_v_spec.port,
                db=_v_spec.db,
                **_v_spec.auth_kwargs(),
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
                decode_responses=False,
            )
            _viewer_ns = _v_spec.namespace
    except Exception:
        _log.warning(
            "VIEWER_REDIS_INIT_FAIL: viewer Redis init failed, tick_preview falls back to normal throttle",
            exc_info=True,
        )

    try:
        while True:
            await asyncio.sleep(poll_s)
            sessions: Dict[str, WsSession] = app.get(APP_WS_SESSIONS, {})

            # O3-sleep: publish active viewer count to Redis for tick_preview_worker
            active_count = sum(
                1
                for s in (sessions or {}).values()
                if not s.ws.closed and s.symbol is not None
            )
            if _viewer_redis is not None:
                try:
                    _vk = f"{_viewer_ns}:ws:viewer_count"
                    _viewer_redis.set(_vk, str(active_count), ex=30)
                except Exception:
                    _log.warning(
                        "VIEWER_REDIS_SET_FAIL: cannot publish viewer_count to Redis",
                        exc_info=True,
                    )

            subs_by_target: Dict[tuple[str, int], list[WsSession]] = {}
            for sess in (sessions or {}).values():
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
                                            # в”Ђв”Ђ ADR: seed forming Р· UDS РїСЂРё СЂРµСЃС‚Р°СЂС‚С– в”Ђв”Ђ
                                            # Р‘РµР· seed open = РїРµСЂС€РёР№ С‚С–Рє РїС–СЃР»СЏ СЂРµСЃС‚Р°СЂС‚Сѓ (С…РёР±РЅРёР№).
                                            # Р§РёС‚Р°С”РјРѕ РїРѕС‚РѕС‡РЅРёР№ Р±Р°СЂ Р· UDS С‰РѕР± СѓСЃРїР°РґРєСѓРІР°С‚Рё O/H/L.
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
                                _log.debug(
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

                        # SMC: notify runner on complete bars в†’ inject delta if has_changes
                        _smc_runner = (
                            app[APP_SMC_RUNNER] if APP_SMC_RUNNER in app else None
                        )
                        # X28-fix: surface backend ATR in delta too — UI buffer
                        # would otherwise lose ATR between full frames.
                        if _smc_runner is not None:
                            try:
                                frame["atr"] = float(
                                    _smc_runner._engine.get_atr(symbol, tf_s)
                                )
                            except Exception:
                                _log.debug(
                                    "WS_DELTA_ATR_ERR sym=%s tf=%s",
                                    symbol,
                                    tf_s,
                                    exc_info=True,
                                )
                            # ADR-0070 amendment: backend SSOT for relative
                            # volume (X28). Mirrors ATR rationale — keep UI in
                            # sync between full frames.
                            try:
                                frame["rv"] = float(
                                    _smc_runner._engine.get_rv(symbol, tf_s)
                                )
                            except Exception:
                                _log.debug(
                                    "WS_DELTA_RV_ERR sym=%s tf=%s",
                                    symbol,
                                    tf_s,
                                    exc_info=True,
                                )
                        if _smc_runner is not None:
                            for _ev in seen_events.values():
                                if _ev.get("complete"):
                                    _smc_runner.on_bar_dict(symbol, tf_s, _ev["bar"])
                            _smc_d = _smc_runner.last_delta(symbol, tf_s)
                            if _smc_d is not None and _smc_d.has_changes:
                                frame["smc_delta"] = _smc_d.to_wire()
                                _smc_runner.clear_delta(symbol, tf_s)
                            # ADR-0053: range_exhaustion — emit every delta tick so
                            # UI pill reflects live traveled_mult; runner caches 2s to
                            # amortize across per-TF frame fan-out. Symbol-scope, gated
                            # by runner config.range_exhaustion.enabled.
                            try:
                                _re = _smc_runner.get_range_exhaustion(symbol)
                                if _re is not None:
                                    frame["range_exhaustion"] = _re.to_wire()
                            except Exception:
                                _log.debug(
                                    "WS_DELTA_RE_ERR sym=%s", symbol, exc_info=True
                                )
                            # ADR-0035: narrative refresh in delta (was full-frame only)
                            _any_complete = any(
                                _ev.get("complete") for _ev in seen_events.values()
                            )
                            if _any_complete and candles:
                                # ADR-0042 P2: metadata in delta (DF-2)
                                try:
                                    _zg = _smc_runner.get_zone_grades(symbol, tf_s)
                                    if _zg:
                                        frame["zone_grades"] = _zg
                                except Exception:
                                    _log.debug(
                                        "WS_DELTA_ZG_ERR sym=%s", symbol, exc_info=True
                                    )
                                try:
                                    _bm = _smc_runner.get_bias_map(symbol)
                                    if _bm:
                                        frame["bias_map"] = _bm
                                except Exception:
                                    _log.debug(
                                        "WS_DELTA_BM_ERR sym=%s", symbol, exc_info=True
                                    )
                                try:
                                    _mm = _smc_runner.get_momentum_map(symbol)
                                    if _mm:
                                        frame["momentum_map"] = _mm
                                except Exception:
                                    _log.debug(
                                        "WS_DELTA_MM_ERR sym=%s", symbol, exc_info=True
                                    )
                                try:
                                    _pds = _smc_runner.get_pd_state(symbol, tf_s)
                                    if _pds is not None:
                                        frame["pd_state"] = _pds
                                except Exception:
                                    _log.debug(
                                        "WS_DELTA_PD_ERR sym=%s", symbol, exc_info=True
                                    )
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
                                        # ADR-0036: shell payload
                                        try:
                                            _shell = _smc_runner.get_shell_payload(
                                                symbol, tf_s, _narr
                                            )
                                            if _shell is not None:
                                                frame["shell"] = _shell.to_wire()
                                        except Exception as _shell_exc:
                                            _log.warning(
                                                "WS_SHELL_ERR sym=%s err=%s",
                                                symbol,
                                                _shell_exc,
                                            )
                                    # ADR-0042 P2: signals in delta (DF-2)
                                    try:
                                        _sigs, _sig_alerts = _smc_runner.get_signals(
                                            symbol,
                                            tf_s,
                                            _narr,
                                            float(_last_c),
                                            float(_atr_est),
                                        )
                                        if _sigs:
                                            frame["signals"] = [
                                                s.to_wire() for s in _sigs
                                            ]
                                        if _sig_alerts:
                                            frame["signal_alerts"] = [
                                                a.to_wire() for a in _sig_alerts
                                            ]
                                    except Exception:
                                        _log.debug(
                                            "WS_DELTA_SIG_ERR sym=%s",
                                            symbol,
                                            exc_info=True,
                                        )
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
                                _log.debug(
                                    "WS_SESSION_LEVELS_DELTA_FAIL sym=%s",
                                    symbol,
                                    exc_info=True,
                                )

                        d1_relay_tfs_2: set = app.get(APP_D1_TICK_RELAY_TFS, set())
                        if tf_s in d1_relay_tfs_2:
                            for c in candles:
                                if c.get("complete", False) or c.get("src") not in (
                                    "tick_relay",
                                    None,
                                ):
                                    forming_by_target.pop((symbol, tf_s), None)

                    # ADR-0085 P1: будильники Арчі в КОЖНОМУ delta (не лише
                    # complete-bar) — зняття/поява пропагується ≤2с+30с кеш,
                    # порожній маркер {"conditions":[]} чистить шар негайно.
                    if frame is not None and "archi_chart" not in frame:
                        _ac_delta = _archi_chart_wire(app, symbol)
                        if _ac_delta is not None:
                            frame["archi_chart"] = _ac_delta

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
                        _log.debug(
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

            # в”Ђв”Ђ ADR-0035: M1 feed for session H/L live tracking в”Ђв”Ђ
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

            # в”Ђв”Ђ ADR-0049: WakeEngine tick ($0, in-process) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
            _wake_eng = app.get(APP_WAKE_ENGINE)
            if _wake_eng is not None:
                try:
                    _wake_ts_ms = int(time.time() * 1000)
                    await _wake_eng.tick(_wake_ts_ms)
                except Exception as _wake_exc:
                    _log.debug("WS_WAKE_ENGINE_TICK_ERR: %s", _wake_exc)

            # в”Ђв”Ђ ADR-0049: NarrativeEnricher thesis refresh (via executor) в”Ђв”Ђ
            _enricher = app.get("_narrative_enricher")
            if _enricher is not None:
                try:
                    _enr_loop = asyncio.get_event_loop()
                    for _enr_sym in set(sym for sym, _ in subs_by_target.keys()):
                        if _enricher.needs_refresh(_enr_sym):
                            await _enr_loop.run_in_executor(
                                app[APP_UDS_EXECUTOR],
                                _enricher.refresh_thesis_sync,
                                _enr_sym,
                            )
                except Exception as _enr_exc:
                    _log.debug("WS_THESIS_REFRESH_ERR: %s", _enr_exc)

    except asyncio.CancelledError:
        _log.debug("WS_GLOBAL_DELTA_CANCELLED")
        pass


async def _bg_smc_feed_loop(app: web.Application) -> None:
    """ADR-0040: С„РѕРЅРѕРІРёР№ feed ALL symbols Г— compute_tfs РґР»СЏ SMC/TDA cascade.

    РћРєСЂРµРјР° coroutine Р· РїРѕРІС–Р»СЊРЅРёРј С–РЅС‚РµСЂРІР°Р»РѕРј (bg_smc_poll_interval_s, default 10s).
    HTF Р±Р°СЂРё (D1/H4/H1/M15) Р·РјС–РЅСЋСЋС‚СЊСЃСЏ СЂС–РґРєРѕ вЂ” polling РєРѕР¶РЅСѓ 1s РЅР°РґР»РёС€РєРѕРІРёР№.
    Р—Р°РїСѓСЃРєР°С”С‚СЊСЃСЏ РІ _start_bg_tasks РїС–СЃР»СЏ warmup SMC runner.
    """
    ws_cfg = app.get(APP_FULL_CONFIG, {}).get("ws_server", {})
    poll_s = float(ws_cfg.get("bg_smc_poll_interval_s", DEFAULT_BG_SMC_POLL_S))
    _log.info("WS_BG_SMC_FEED_START poll_s=%.1f", poll_s)

    _bg_cursor: Dict[tuple[str, int], Optional[int]] = {}

    try:
        while True:
            await asyncio.sleep(poll_s)
            _smc_runner = app[APP_SMC_RUNNER] if APP_SMC_RUNNER in app else None
            if _smc_runner is None:
                continue
            _all_smc_syms: list = getattr(_smc_runner, "_symbols", [])
            _smc_compute_tfs: set = getattr(_smc_runner, "_compute_tfs", set())
            for sym in _all_smc_syms:
                for bg_tf in _smc_compute_tfs:
                    try:
                        bg_seq = _bg_cursor.get((sym, bg_tf))
                        bg_result = await _uds_read_updates(
                            app, sym, bg_tf, bg_seq, False
                        )
                        if bg_result is None:
                            continue
                        bg_events = getattr(bg_result, "events", [])
                        bg_cur = getattr(bg_result, "cursor_seq", 0)
                        _bg_cursor[(sym, bg_tf)] = bg_cur
                        for ev in bg_events:
                            if isinstance(ev, dict) and ev.get("complete"):
                                bar = ev.get("bar")
                                if isinstance(bar, dict):
                                    cast(Any, _smc_runner).on_bar_dict(sym, bg_tf, bar)
                    except Exception as bg_exc:
                        _log.debug(
                            "WS_BG_SMC_FEED_ERR sym=%s tf=%s err=%s",
                            sym,
                            bg_tf,
                            bg_exc,
                        )
                # в”Ђв”Ђ M1 feed for _last_prices + session H/L (ADR-0035 bg path) в”Ђв”Ђ
                # When ws_clients=0 the delta_loop doesn't feed M1 bars,
                # so _last_prices freezes. Poll M1 here to keep price fresh.
                try:
                    m1_seq = _bg_cursor.get((sym, 60))
                    m1_res = await _uds_read_updates(app, sym, 60, m1_seq, False)
                    if m1_res is not None:
                        m1_events = getattr(m1_res, "events", [])
                        m1_cur = getattr(m1_res, "cursor_seq", 0)
                        _bg_cursor[(sym, 60)] = m1_cur
                        for ev in m1_events:
                            if isinstance(ev, dict) and ev.get("complete"):
                                bar = ev.get("bar")
                                if isinstance(bar, dict):
                                    cast(Any, _smc_runner).feed_m1_bar_dict(sym, bar)
                except Exception as m1_exc:
                    _log.debug("WS_BG_M1_FEED_ERR sym=%s err=%s", sym, m1_exc)
    except asyncio.CancelledError:
        _log.debug("WS_BG_SMC_FEED_CANCELLED")
        pass


# в”Ђв”Ђ WS Handler в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ


async def ws_handler(request: web.Request) -> web.StreamResponse:
    app = request.app
    sessions: Dict[str, WsSession] = app[APP_WS_SESSIONS]
    limits: WsLimits = app.get(APP_WS_LIMITS) or WsLimits()
    client_ip = _client_ip(request)
    # SEC-06: capacity rails ДО upgrade — відмова коштує HTTP 503, не сесію в пам'яті
    if limits.max_clients > 0 and len(sessions) >= limits.max_clients:
        _log.warning("WS_REJECT reason=max_clients(%d) ip=%s", limits.max_clients, client_ip)
        return web.Response(status=503, text="ws_capacity")
    if limits.max_clients_per_ip > 0:
        per_ip = sum(1 for s in sessions.values() if s.client_ip == client_ip)
        if per_ip >= limits.max_clients_per_ip:
            _log.warning(
                "WS_REJECT reason=max_clients_per_ip(%d) ip=%s", limits.max_clients_per_ip, client_ip
            )
            return web.Response(status=503, text="ws_capacity_ip")

    hb_interval = app.get(APP_HEARTBEAT_S, DEFAULT_HEARTBEAT_S)
    # SEC-06: aiohttp ping/pong закриває мертвих peer-ів; max_msg_size ріже фрейм ДО буферизації
    ws = web.WebSocketResponse(heartbeat=float(hb_interval), max_msg_size=_MAX_WS_MSG_BYTES)
    await ws.prepare(request)

    session = WsSession(ws)
    session.client_ip = client_ip
    session._action_tokens = float(limits.actions_burst)
    sessions[session.client_id] = session

    _log.info("WS_CONNECT client_id=%s remote=%s", session.client_id, request.remote)

    # P2: set default symbol/tf from config, send full frame
    cfg = app.get(APP_FULL_CONFIG, {})
    symbols = cfg.get("symbols", [])
    default_symbol = symbols[0] if symbols else "XAU/USD"
    session.symbol = default_symbol
    session.tf_s = 1800  # M30 default вЂ” sync Р· SymbolTfPicker default

    # T8/S24: config frame (policy bridge) вЂ” Р·Р°РІР¶РґРё, РЅРµР·Р°Р»РµР¶РЅРѕ РІС–Рґ UDS
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

    # Heartbeat task (hb_interval визначено вище, до upgrade)

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


_MAX_WS_MSG_BYTES = 65536  # SEC-05: 64 KB limit РґР»СЏ WS РїРѕРІС–РґРѕРјР»РµРЅСЊ


def _sanitize_log(value: str, max_len: int = 120) -> str:
    """SEC-03: РІРёРґР°Р»СЏС” control characters РґР»СЏ Р±РµР·РїРµС‡РЅРѕРіРѕ Р»РѕРіСѓРІР°РЅРЅСЏ."""
    import re as _re

    if not isinstance(value, str):
        return str(value)[:max_len]
    return _re.sub(r"[\x00-\x1f\x7f]", "", value)[:max_len]


def _consume_action_token(session: WsSession, limits: WsLimits, now: float) -> bool:
    """SEC-06 token bucket per session: burst=actions_burst, refill=actions_per_s/с; burst 0 = вимкнено."""
    if limits.actions_burst <= 0:
        return True
    elapsed = max(0.0, now - session._action_refill_ts)
    session._action_refill_ts = now
    session._action_tokens = min(
        float(limits.actions_burst), session._action_tokens + elapsed * limits.actions_per_s
    )
    if session._action_tokens < 1.0:
        return False
    session._action_tokens -= 1.0
    return True


async def _handle_action(session: WsSession, raw: str, app: web.Application) -> None:
    """Р РѕР·Р±РёСЂР°С” РІС…С–РґРЅРµ РїРѕРІС–РґРѕРјР»РµРЅРЅСЏ РІС–Рґ РєР»С–С”РЅС‚Р°. P2: switch + scrollback.

    S20: JSON/schema errors в†’ error frame РєР»С–С”РЅС‚Сѓ (degraded-but-loud).
    S25: Unknown action в†’ error frame + Р»РѕРі (РЅРµ silent ignore).
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
    # SEC-06: token bucket — flood дій не доходить до parse/switch
    limits: WsLimits = app.get(APP_WS_LIMITS) or WsLimits()
    if not _consume_action_token(session, limits, time.time()):
        _log.warning(
            "WS_ACTION_RATE_LIMITED client=%s ip=%s", session.client_id, session.client_ip
        )
        err = _build_error_frame(
            session, "action_rate_limited", "Too many actions, slow down", app=app
        )
        await session.ws.send_json(err)
        return
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        _log.warning(
            "WS_ACTION_INVALID client=%s reason=json_error raw=%.200s",
            session.client_id,
            _sanitize_log(raw, 200),  # SEC-03: без log injection
        )
        err = _build_error_frame(session, "json_parse_error", "Invalid JSON", app=app)
        await session.ws.send_json(err)
        return
    if not isinstance(data, dict) or "action" not in data:
        _log.warning(
            "WS_ACTION_INVALID client=%s reason=missing_action raw=%.200s",
            session.client_id,
            _sanitize_log(raw, 200),  # SEC-03: без log injection
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
        # S25: unknown action в†’ error frame (degraded-but-loud, РЅРµ silent ignore)
        _log.warning("WS_ACTION_UNKNOWN client=%s action=%s", session.client_id, action)
        err = _build_error_frame(
            session, "unknown_action", "Unknown action: %s" % action, app=app
        )
        await session.ws.send_json(err)


async def _handle_switch(
    session: WsSession, data: Dict[str, Any], app: web.Application
) -> None:
    """РћР±СЂРѕР±РєР° switch action: Р·РјС–РЅРёС‚Рё symbol/tf в†’ РЅРѕРІРёР№ full frame."""
    # SEC-06: cooldown між switch (кожен switch = cold-start read + SMC snapshot + серіалізація)
    limits: WsLimits = app.get(APP_WS_LIMITS) or WsLimits()
    now_s = time.time()
    if limits.switch_cooldown_s > 0 and now_s - session._switch_last_ts < limits.switch_cooldown_s:
        _log.warning(
            "WS_SWITCH_REJECT client=%s reason=cooldown ip=%s", session.client_id, session.client_ip
        )
        err = _build_error_frame(session, "switch_throttled", "switch cooldown active", app=app)
        await session.ws.send_json(err)
        return
    session._switch_last_ts = now_s
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
    """РћР±СЂРѕР±РєР° scrollback action: UDS read_window(to_open_ms) в†’ scrollback frame.

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
    # Scrollback chunk: РјРµРЅС€Рµ РЅС–Р¶ cold start
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
        _log.debug(
            "WS_SCROLLBACK_PUSH client=%s symbol=%s bars=%d to_ms=%d",
            session.client_id,
            session.symbol,
            len(candles),
            to_ms,
        )
    except Exception as exc:
        _log.warning("WS_SCROLLBACK_ERROR client=%s err=%s", session.client_id, exc)
        # Р—Р°РІР¶РґРё РІС–РґРїРѕРІС–РґР°С”РјРѕ РїСѓСЃС‚РёРј frame вЂ” С–РЅР°РєС€Рµ РєР»С–С”РЅС‚ Р·Р°СЃС‚СЂСЏРіРЅРµ
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


# в”Ђв”Ђ UDS init в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ


def _init_uds(app: web.Application, config_path: str, cfg: Dict[str, Any]) -> None:
    """Р†РЅС–С†С–Р°Р»С–Р·СѓС” UDS reader. W0: role='reader', writer_components=False."""
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


# в”Ђв”Ђ App factory в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ


def build_app(
    *,
    config_path: str = "config.json",
    uds: Any = None,
) -> web.Application:
    """РЎС‚РІРѕСЂСЋС” aiohttp Application Р· WS endpoint.

    Args:
        config_path: С€Р»СЏС… РґРѕ config.json
        uds: Р·РѕРІРЅС–С€РЅС–Р№ UDS instance (РґР»СЏ С‚РµСЃС‚С–РІ). РЇРєС‰Рѕ None вЂ” P2 auto-init.
    """
    full_cfg = _load_full_config(config_path)
    ws_cfg = full_cfg.get("ws_server", {}) if isinstance(full_cfg, dict) else {}
    app = web.Application()
    app[APP_HEARTBEAT_S] = int(ws_cfg.get("heartbeat_interval_s", DEFAULT_HEARTBEAT_S))
    app[APP_WS_LIMITS] = _ws_limits_from_cfg(ws_cfg)  # SEC-06
    app[APP_DELTA_POLL_S] = float(
        ws_cfg.get("delta_poll_interval_s", DEFAULT_DELTA_POLL_S)
    )
    app[APP_WS_SESSIONS] = {}
    app[APP_CONFIG_PATH] = config_path
    app[APP_BOOT_ID] = uuid.uuid4().hex[:16]
    app[APP_FULL_CONFIG] = full_cfg
    # ADR-0090 S4: єдина секція для всього агентського; env-override логуємо гучно
    _bridge_cfg = resolve_agent_bridge_config(full_cfg)
    app[APP_AGENT_BRIDGE_CFG] = _bridge_cfg
    if _bridge_cfg.enabled_source == "env":
        _log.warning(
            "AGENT_BRIDGE_ENABLED_BY_ENV: %s overrides config.json agent_bridge.enabled -> %s",
            AGENT_BRIDGE_ENABLED_ENV,
            _bridge_cfg.enabled,
        )
    elif not _bridge_cfg.enabled:
        # I5: master switch off = wake_engine/archi_chart НЕ стартують навіть при
        # їхніх власних enabled=true — кажемо це один раз на старті, а не мовчимо
        _log.info(
            "AGENT_BRIDGE_DISABLED: agent_bridge.enabled=false (source=config) -> "
            "WakeEngine/NarrativeEnricher/archi_chart not started "
            "(wake_engine.enabled=%s); console = окремий процес smc-agent-bridge; "
            "set env %s=1 to enable on this host",
            bool(_bridge_cfg.wake_engine.get("enabled", False)),
            AGENT_BRIDGE_ENABLED_ENV,
        )

    # Symbol/TF sets from config (T10: imports at top-level)
    symbols_list = full_cfg.get("symbols", [])
    app[APP_SYMBOLS_SET] = set(str(s) for s in symbols_list)
    app[APP_TF_ALLOWLIST] = tf_allowlist_from_cfg(full_cfg)
    preview_set, _ = preview_tf_allowlist_from_cfg(full_cfg)
    app[APP_PREVIEW_TF_SET] = preview_set

    # ADR-0012 P3: D1 live tick relay вЂ” Redis client + config flags
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
                    **spec.auth_kwargs(),
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
    # min(4, cpu_count) вЂ” 2 Р±СѓР»Рѕ РЅРµРґРѕСЃС‚Р°С‚РЅСЊРѕ РґР»СЏ РїР°СЂР°Р»РµР»СЊРЅРёС… /api/bars + /api/updates
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

    # SMC Runner init (ADR-0024 В§6.1) вЂ” in-process, same event loop as ws_server
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
                "WS_SMC_RUNNER_INIT_FAILED err=%s вЂ” SMC disabled", _smc_init_exc
            )

    # в”Ђв”Ђ CORS middleware (cross-origin: Vercel / Cloudflare Pages) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    _cors_origins_raw = ws_cfg.get("cors_allowed_origins", [])
    _cors_origins = set(str(o).rstrip("/") for o in _cors_origins_raw if o)
    app[APP_CORS_ORIGINS] = _cors_origins
    if _cors_origins:
        _log.info("WS_CORS_ENABLED origins=%s", _cors_origins)

    @web.middleware
    async def cors_middleware(request, handler):
        origin = request.headers.get("Origin", "")
        allowed = app.get(APP_CORS_ORIGINS, set())
        # РЇРєС‰Рѕ CORS РЅРµ РЅР°Р»Р°С€С‚РѕРІР°РЅРёР№ Р°Р±Рѕ origin РЅРµ РІ СЃРїРёСЃРєСѓ вЂ” РїСЂРѕРїСѓСЃРєР°С”РјРѕ
        if not allowed or origin not in allowed:
            return await handler(request)
        # Preflight OPTIONS
        if request.method == "OPTIONS":
            resp = web.Response(status=204)
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers.update(_CORS_HEADERS_COMMON)
            return resp
        # Р—РІРёС‡Р°Р№РЅРёР№ Р·Р°РїРёС‚ вЂ” РґРѕРґР°С”РјРѕ CORS headers
        resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = origin
        return resp

    app.middlewares.append(cors_middleware)

    # в”Ђв”Ђ /api/status вЂ” edge probe endpoint (РґР»СЏ Vercel cross-origin) в”Ђв”Ђв”Ђв”Ђ
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

    # ── Redis-клієнт клієнтських ключів (role agent_observability): api_v3 TokenStore/kill_switch ──
    # та ADR-0086 snapshot (WIP). Agent-observability маршрути → окремий процес bridge (ADR-0090 S1).
    _agent_redis_client = None
    try:
        from runtime.store.redis_spec import resolve_redis_spec
        import redis as _aredis_mod

        _agent_spec = resolve_redis_spec(
            load_system_config(resolve_config_path()),
            role="agent_observability",
        )
        if _agent_spec is None:
            raise ValueError("resolve_redis_spec returned None")
        _agent_redis_client = _aredis_mod.Redis(
            host=_agent_spec.host,
            port=_agent_spec.port,
            db=_agent_spec.db,
            **_agent_spec.auth_kwargs(),
            socket_connect_timeout=2,
            decode_responses=True,
        )
        _agent_ns = _agent_spec.namespace
        _log.info("AGENT_OBSERVABILITY: Redis wired ns=%s", _agent_ns)
    except Exception as _are:
        _agent_ns = "v3_local"
        _log.info(
            "AGENT_OBSERVABILITY: Redis not available (%s) -- /api/v3 TokenStore/kill_switch cannot mount",
            _are,
        )

    # ── /api/v3/* — Public read-only API (ADR-0058 slice 058.2) ─────────────
    # Token auth via X-API-Key header against Redis-backed TokenStore.
    # Off by default — enable via config.json:api_v3.enabled.
    _api_v3_cfg = (full_cfg or {}).get("api_v3") or {}
    if bool(_api_v3_cfg.get("enabled", False)):
        try:
            from runtime.api_v3.endpoints import register_routes as _register_api_v3
            from runtime.api_v3.token_store import TokenStore as _TokenStore

            if _agent_redis_client is None:
                raise RuntimeError("redis_client_unavailable")
            _signals_dir = str(_api_v3_cfg.get("signals_dir", "data_v3/_signals"))
            _audit_dir_cfg = _api_v3_cfg.get("audit_dir", "data_v3/_audit")
            _audit_dir = str(_audit_dir_cfg) if _audit_dir_cfg else None
            _token_store = _TokenStore(_agent_redis_client, namespace=_agent_ns)
            _register_api_v3(
                app,
                token_store=_token_store,
                signals_dir=_signals_dir,
                audit_dir=_audit_dir,
            )
            # ADR-0059 §3.4 (slice 059.4) — analysis kill switch.
            # Mounted unconditionally when api_v3 is enabled so the
            # middleware is in place BEFORE analysis endpoints land in
            # slices 059.1-059.3. Config flag `analysis_enabled` (default
            # true) gates the boot layer; Redis flag toggles runtime.
            from runtime.api_v3.kill_switch import (
                register_kill_switch as _register_kill_switch,
            )

            _analysis_enabled = bool(_api_v3_cfg.get("analysis_enabled", True))
            _register_kill_switch(
                app,
                redis_client=_agent_redis_client,
                namespace=_agent_ns,
                analysis_enabled=_analysis_enabled,
            )
            _log.info(
                "API_V3_ENABLED: ns=%s signals_dir=%s audit_dir=%s analysis_enabled=%s",
                _agent_ns,
                _signals_dir,
                _audit_dir or "DISABLED",
                _analysis_enabled,
            )
        except Exception as _av3_exc:  # pragma: no cover — surfaced loud
            _log.warning("API_V3_INIT_FAIL: %s", _av3_exc)
    else:
        _log.info("API_V3_DISABLED: set api_v3.enabled=true to mount /api/v3/*")

    # ADR-0090 S1: консоль клієнта та agent-observability маршрути живуть в окремому
    # процесі runtime/agent_bridge (smc-agent-bridge :8010); тут їх немає.
    # в”Ђв”Ђ /api/context вЂ” SMC context for external consumers (bot, TUI) в”Ђв”Ђв”Ђ
    async def _api_context(request: web.Request) -> web.Response:
        """РџРѕРІРµСЂС‚Р°С” РїРѕС‚РѕС‡РЅРёР№ SMC РєРѕРЅС‚РµРєСЃС‚ РґР»СЏ symbol+tf.

        GET /api/context?symbol=BTCUSDT&tf=M15
        Р’РёРєРѕСЂРёСЃС‚РѕРІСѓС”С‚СЊСЃСЏ Telegram-Р±РѕС‚РѕРј РґР»СЏ Р·Р±Р°РіР°С‡РµРЅРЅСЏ Р°РЅР°Р»С–Р·Сѓ.
        """
        symbol_raw = request.query.get("symbol", "")
        tf_raw = request.query.get("tf", "M15")
        if not symbol_raw:
            return web.json_response(
                {"error": "missing 'symbol' query param"}, status=400
            )
        symbol = _canonicalize_symbol(symbol_raw, app[APP_SYMBOLS_SET])
        tf_s = _TF_LABEL_TO_S.get(tf_raw) or _TF_LABEL_TO_S.get(tf_raw.upper())
        if tf_s is None:
            return web.json_response({"error": f"unknown tf '{tf_raw}'"}, status=400)

        ctx: Dict[str, Any] = {
            "symbol": symbol,
            "tf": _TF_S_TO_LABEL.get(tf_s, tf_raw),
            "tf_s": tf_s,
            "server_ts_ms": int(time.time() * 1000),
        }

        _smc_runner = app.get(APP_SMC_RUNNER)
        if _smc_runner is None:
            ctx["error"] = "smc_runner not available"
            return web.json_response(ctx)

        # last_price вЂ” РІС–Рґ SmcRunner (РѕРЅРѕРІР»СЋС”С‚СЊСЃСЏ РїСЂРё РєРѕР¶РЅРѕРјСѓ M1 bar)
        _last_price = _smc_runner.get_last_price(symbol)
        if _last_price > 0:
            ctx["last_price"] = _last_price

        # bias_map вЂ” HTF alignment
        try:
            bm = _smc_runner.get_bias_map(symbol)
            if bm:
                # РџРµСЂРµС‚РІРѕСЂСЋС”РјРѕ РєР»СЋС‡С– tf_s в†’ label РґР»СЏ С‡РёС‚Р°Р±РµР»СЊРЅРѕСЃС‚С–
                ctx["bias_map"] = {
                    _TF_S_TO_LABEL.get(int(k), k): v for k, v in bm.items()
                }
        except Exception as exc:
            ctx.setdefault("warnings", []).append(f"bias_map: {exc}")

        # pd_state вЂ” premium/discount
        try:
            pd = _smc_runner.get_pd_state(symbol, tf_s)
            if pd:
                ctx["pd_state"] = pd
        except Exception as exc:
            ctx.setdefault("warnings", []).append(f"pd_state: {exc}")

        # zones + zone_grades вЂ” active SMC zones
        try:
            snap = _smc_runner.get_snapshot(symbol, tf_s)
            if snap is not None:
                wire = snap.to_wire()
                # РўС–Р»СЊРєРё Р°РєС‚РёРІРЅС– Р·РѕРЅРё (РЅРµ mitigated)
                ctx["zones"] = wire.get("zones", [])
                ctx["levels"] = wire.get("levels", [])
                ctx["trend_bias"] = wire.get("trend_bias")
                # swings вЂ” filtered to structure events only (BOS/CHoCH/displacement)
                _STRUCTURE_KINDS = {
                    "bos_bull",
                    "bos_bear",
                    "choch_bull",
                    "choch_bear",
                    "displacement_bull",
                    "displacement_bear",
                }
                all_swings = wire.get("swings", [])
                ctx["swings"] = [
                    s
                    for s in all_swings
                    if isinstance(s, dict) and s.get("kind", "") in _STRUCTURE_KINDS
                ]
                # zone_grades
                zg = _smc_runner.get_zone_grades(symbol, tf_s)
                if zg:
                    ctx["zone_grades"] = zg
        except Exception as exc:
            ctx.setdefault("warnings", []).append(f"snapshot: {exc}")

        # session levels
        try:
            sl = _smc_runner.get_session_levels_wire(symbol)
            if sl:
                ctx["session_levels"] = sl
        except Exception as exc:
            ctx.setdefault("warnings", []).append(f"session_levels: {exc}")

        # momentum_map вЂ” displacement intensity per TF
        try:
            mm = _smc_runner.get_momentum_map(symbol)
            if mm:
                ctx["momentum_map"] = {
                    _TF_S_TO_LABEL.get(int(k), k): v for k, v in mm.items()
                }
        except Exception as exc:
            ctx.setdefault("warnings", []).append(f"momentum_map: {exc}")

        # ATR(14) for requested TF + D1/H4
        try:
            atr_map = {}
            for _atf in sorted({tf_s, 14400, 86400}):
                _atr_val = _smc_runner._engine.get_atr(symbol, _atf)
                if _atr_val > 1.0:
                    atr_map[_TF_S_TO_LABEL.get(_atf, str(_atf))] = round(_atr_val, 2)
            if atr_map:
                ctx["atr"] = atr_map
        except Exception as exc:
            ctx.setdefault("warnings", []).append(f"atr: {exc}")

        # recent OHLC candles (last N bars for the TF)
        try:
            _limit = int(request.query.get("candles", "0"))
            if _limit > 0:
                _limit = min(_limit, 20)
                state = _smc_runner._engine._states.get((symbol, tf_s))
                if state is not None:
                    _raw = state.bars_list()[-_limit:]
                    ctx["candles"] = [
                        {
                            "t": int(b.open_time_ms),
                            "o": round(b.o, 5),
                            "h": round(b.h, 5),
                            "l": round(b.low, 5),
                            "c": round(b.c, 5),
                        }
                        for b in _raw
                    ]
        except Exception as exc:
            ctx.setdefault("warnings", []).append(f"candles: {exc}")

        # narrative вЂ” market phase, scenario, mode
        narr = None
        try:
            _atr_est = 1.0
            narr = _smc_runner.get_narrative(symbol, tf_s, _last_price, _atr_est)
            if narr is not None:
                from core.smc.narrative import narrative_to_wire

                ctx["narrative"] = narrative_to_wire(narr)
        except Exception as exc:
            ctx.setdefault("warnings", []).append(f"narrative: {exc}")

        # signals (ADR-0039)
        try:
            _atr_for_sig = ctx.get("atr", {}).get(_TF_S_TO_LABEL.get(tf_s, ""), 1.0)
            _sigs, _ = _smc_runner.get_signals(
                symbol,
                tf_s,
                narr,
                _last_price,
                _atr_for_sig if isinstance(_atr_for_sig, (int, float)) else 1.0,
            )
            if _sigs:
                ctx["signals"] = [s.to_wire() for s in _sigs[:5]]
        except Exception as exc:
            ctx.setdefault("warnings", []).append(f"signals: {exc}")

        # в”Ђв”Ђ tick_price: real-time tick from Redis (sub-second freshness) в”Ђв”Ђ
        try:
            tick_redis = (
                app[APP_TICK_REDIS_CLIENT] if APP_TICK_REDIS_CLIENT in app else None
            )
            if tick_redis is not None:
                tick_ns = app[APP_TICK_REDIS_NS]
                tick_key = f"{tick_ns}:tick:last:{symbol.replace('/', '_')}"
                tick_raw = await asyncio.get_event_loop().run_in_executor(
                    app[APP_UDS_EXECUTOR], tick_redis.get, tick_key
                )
                if tick_raw:
                    tick_data = json.loads(tick_raw)
                    _tp = float(tick_data.get("mid", 0))
                    _tts = int(tick_data.get("tick_ts_ms", 0))
                    if _tp > 0:
                        ctx["tick_price"] = round(_tp, 5)
                        ctx["tick_ts_ms"] = _tts
                        # Override last_price with tick if tick is fresher
                        if _last_price <= 0 or (_tts > 0 and _tp > 0):
                            ctx["last_price"] = round(_tp, 5)
        except Exception as exc:
            ctx.setdefault("warnings", []).append(f"tick_price: {exc}")

        # в”Ђв”Ђ data_quality: auto-computed freshness metadata в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
        try:
            now_ms = int(time.time() * 1000)
            dq: Dict[str, Any] = {"server_ts_ms": now_ms, "tf_freshness": {}}
            _compute_tfs_set: set = (
                getattr(_smc_runner, "_compute_tfs", set()) if _smc_runner else set()
            )
            for _dq_tf in sorted(_compute_tfs_set):
                _dq_label = _TF_S_TO_LABEL.get(_dq_tf, str(_dq_tf))
                _dq_state = (
                    _smc_runner._engine._states.get((symbol, _dq_tf))
                    if _smc_runner
                    else None
                )
                if _dq_state is not None:
                    _dq_bars = _dq_state.bars_list()
                    if _dq_bars:
                        _dq_last_ms = _dq_bars[-1].open_time_ms
                        _dq_age_s = (now_ms - _dq_last_ms) / 1000
                        _dq_expected_s = (
                            _dq_tf * 2.5
                        )  # bar should arrive within ~2.5Г— TF
                        dq["tf_freshness"][_dq_label] = {
                            "last_bar_ms": _dq_last_ms,
                            "age_s": round(_dq_age_s),
                            "bars_count": len(_dq_bars),
                            "stale": _dq_age_s > _dq_expected_s,
                        }
            # M1 freshness from session_m1_bars (separate storage)
            if _smc_runner:
                _m1_deque = _smc_runner._engine._session_m1_bars.get(symbol)
                if _m1_deque and len(_m1_deque) > 0:
                    _m1_last = _m1_deque[-1]
                    _m1_age_s = (now_ms - _m1_last.open_time_ms) / 1000
                    dq["tf_freshness"]["M1"] = {
                        "last_bar_ms": _m1_last.open_time_ms,
                        "age_s": round(_m1_age_s),
                        "bars_count": len(_m1_deque),
                        "stale": _m1_age_s > 150,  # M1 should arrive within 2.5min
                    }
            # price_frozen detection: compare tick vs last M1 bar close
            _tick_p = ctx.get("tick_price", 0)
            if _tick_p > 0 and _last_price > 0:
                dq["price_spread"] = round(abs(_tick_p - _last_price), 5)
            dq["ws_clients"] = len(app.get(APP_WS_SESSIONS, {}))  # type: ignore[arg-type]
            ctx["data_quality"] = dq
        except Exception as exc:
            ctx.setdefault("warnings", []).append(f"data_quality: {exc}")

        # в”Ђв”Ђ h4_forming: synthesized forming H4 candle from M1 bars в”Ђв”Ђ
        try:
            if tf_s == 14400 or int(request.query.get("include_h4_forming", "0")):
                from core.buckets import bucket_start_ms, resolve_anchor_offset_ms

                _anchor = resolve_anchor_offset_ms(14400, app.get(APP_FULL_CONFIG, {}))
                _now_ms = int(time.time() * 1000)
                _h4_open_ms = bucket_start_ms(_now_ms, 14400 * 1000, _anchor)
                # Get M1 bars from SmcEngine session storage (not _states)
                _m1_deque = (
                    _smc_runner._engine._session_m1_bars.get(symbol)
                    if _smc_runner
                    else None
                )
                if _m1_deque is not None and len(_m1_deque) > 0:
                    _m1_bars = list(_m1_deque)
                    _forming_bars = [
                        b for b in _m1_bars if b.open_time_ms >= _h4_open_ms
                    ]
                    if _forming_bars:
                        _fo = _forming_bars[0].o
                        _fh = max(b.h for b in _forming_bars)
                        _fl = min(b.low for b in _forming_bars)
                        _fc = _forming_bars[-1].c
                        # Use tick_price as latest close if available
                        _tp_forming = ctx.get("tick_price")
                        if _tp_forming and _tp_forming > 0:
                            _fc = _tp_forming
                            _fh = max(_fh, _tp_forming)
                            _fl = min(_fl, _tp_forming)
                        ctx["h4_forming"] = {
                            "open_ms": _h4_open_ms,
                            "o": round(_fo, 5),
                            "h": round(_fh, 5),
                            "l": round(_fl, 5),
                            "c": round(_fc, 5),
                            "m1_count": len(_forming_bars),
                            "age_s": round((_now_ms - _h4_open_ms) / 1000),
                        }
        except Exception as exc:
            ctx.setdefault("warnings", []).append(f"h4_forming: {exc}")

        return web.json_response(ctx)

    app.router.add_get("/api/context", _api_context)
    app.router.add_get("/ws", ws_handler)

    # в”Ђв”Ђ Same-origin SPA serving (РџСЂР°РІРёР»Рѕ В§11: UI + API = РѕРґРёРЅ РїСЂРѕС†РµСЃ) в”Ђв”Ђ
    # Р РѕР·РґР°С‡Р° ui_v4/dist/ СЏРєС‰Рѕ dist С–СЃРЅСѓС” (РїС–СЃР»СЏ npm run build)
    _ws_dir = os.path.dirname(os.path.abspath(__file__))
    _ui_dist = os.path.normpath(os.path.join(_ws_dir, "..", "..", "ui_v4", "dist"))
    _ui_src = os.path.normpath(os.path.join(_ws_dir, "..", "..", "ui_v4", "src"))
    _ui_index = os.path.join(_ui_dist, "index.html")
    # D7: stale dist/ detection вЂ” РїРѕСЂС–РІРЅСЏС‚Рё mtime dist/index.html vs max(src/**)
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
                    "UI_V4_DIST_STALE dist/index.html older than src/ by %.0fs вЂ” "
                    "run 'cd ui_v4 && npm run build' to rebuild",
                    _src_mtime - _dist_mtime,
                )
        except Exception:
            _log.debug("UI_V4_DIST_STALE_CHECK_FAILED", exc_info=True)
            pass  # best-effort, РЅРµ Р±Р»РѕРєСѓС”РјРѕ СЃС‚Р°СЂС‚
    if os.path.isfile(_ui_index):

        async def _spa_index(request: web.Request) -> web.FileResponse:
            # ADR-0082 hotfix: index.html БЕЗ Cache-Control потрапляв у
            # евристичний HTTP-кеш браузера (10% віку файлу) → застарілий HTML
            # посилався на СТАРИЙ hashed-bundle → різні вкладки/навігації
            # виконували РІЗНІ builds (спіймано live: DMvQM1ig і RxIMeZdv у
            # сусідніх переходах). no-cache = завжди ревалідація; ETag робить
            # її дешевим 304. Hashed /assets лишаються cache-first — їм safe.
            return web.FileResponse(
                _ui_index, headers={"Cache-Control": "no-cache"}
            )

        # SPA fallback: index.html РґР»СЏ РєРѕСЂРµРЅСЏ
        app.router.add_get("/", _spa_index)
        # РЎС‚Р°С‚РёС‡РЅС– Р°СЃСЃРµС‚Рё (JS/CSS/images)
        app.router.add_static("/assets", os.path.join(_ui_dist, "assets"))
        # ADR-0066 PATCH 03: brand assets (favicon SVG, wordmark, mark variants)
        _brand_dir = os.path.join(_ui_dist, "brand")
        if os.path.isdir(_brand_dir):
            app.router.add_static("/brand", _brand_dir)
        # ADR-0071 — PWA assets: icons dir + manifest + service worker + offline page.
        # Each registered explicitly (aiohttp routes need exact paths or static dirs).
        # /icons/* — PNG raster icons generated via gen-icons.mjs.
        _icons_dir = os.path.join(_ui_dist, "icons")
        if os.path.isdir(_icons_dir):
            app.router.add_static("/icons", _icons_dir)

        # /manifest.json — PWA manifest (Chrome/Edge install prompt prereq).
        _manifest = os.path.join(_ui_dist, "manifest.json")
        if os.path.isfile(_manifest):
            async def _serve_manifest(request: web.Request) -> web.FileResponse:
                return web.FileResponse(_manifest)
            app.router.add_get("/manifest.json", _serve_manifest)

        # /sw.js — Service Worker. CRITICAL: must be served at SCOPE root (/)
        # for SW.scope='/' registration. Cache-Control:no-cache ОБОВ'ЯЗКОВЕ
        # (ADR-0082 hotfix): без нього update detection SW гальмує на
        # евристичному HTTP-кеші так само, як index.html вище.
        _sw = os.path.join(_ui_dist, "sw.js")
        if os.path.isfile(_sw):
            async def _serve_sw(request: web.Request) -> web.FileResponse:
                return web.FileResponse(
                    _sw, headers={"Cache-Control": "no-cache"}
                )
            app.router.add_get("/sw.js", _serve_sw)

        # /offline.html — branded fallback served by SW при network failure.
        _offline = os.path.join(_ui_dist, "offline.html")
        if os.path.isfile(_offline):
            async def _serve_offline(request: web.Request) -> web.FileResponse:
                return web.FileResponse(_offline)
            app.router.add_get("/offline.html", _serve_offline)

        # /robots.txt — SEO standard.
        _robots = os.path.join(_ui_dist, "robots.txt")
        if os.path.isfile(_robots):
            async def _serve_robots(request: web.Request) -> web.FileResponse:
                return web.FileResponse(_robots)
            app.router.add_get("/robots.txt", _serve_robots)

        _log.info("UI_V4_STATIC registered dist=%s (PWA assets included)", _ui_dist)
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
        # SMC warmup in executor (blocking UDS reads, РЅРµ Р±Р»РѕРєСѓС” event loop)
        _smc_r = app_ctx[APP_SMC_RUNNER] if APP_SMC_RUNNER in app_ctx else None
        _uds_r = app_ctx[APP_UDS] if APP_UDS in app_ctx else None
        _exec = app_ctx[APP_UDS_EXECUTOR]
        if _smc_r is not None and _uds_r is not None:
            asyncio.ensure_future(
                asyncio.get_event_loop().run_in_executor(_exec, _smc_r.warmup, _uds_r)
            )
            _log.info("WS_SMC_WARMUP_SCHEDULED")
        # ADR-0040: BG SMC feed loop вЂ” РѕРєСЂРµРјР° coroutine Р· РїРѕРІС–Р»СЊРЅРёРј poll (default 10s)
        if APP_UDS in app_ctx and _smc_r is not None:
            app_ctx[APP_BG_SMC_TASK] = asyncio.ensure_future(_bg_smc_feed_loop(app_ctx))

        # ADR-0049: WakeEngine вЂ” $0 wake condition checker in delta_loop
        _full_cfg = app_ctx.get(APP_FULL_CONFIG, {})
        _bridge_rt = app_ctx.get(APP_AGENT_BRIDGE_CFG)
        if _bridge_rt is not None and _bridge_rt.wake_engine_enabled and _smc_r is not None:
            try:
                _wake_redis = app_ctx.get(APP_TICK_REDIS_CLIENT)
                _wake_ns = app_ctx.get(APP_TICK_REDIS_NS, "v3_local")
                _wake_symbols = list(_full_cfg.get("symbols", []))
                if _wake_redis is not None and _wake_symbols:
                    from runtime.smc.wake_engine import WakeEngine

                    _we = WakeEngine(
                        redis_client=_wake_redis,
                        namespace=_wake_ns,
                        executor=_exec,
                        smc_runner=_smc_r,
                        symbols=_wake_symbols,
                        config=_bridge_rt.wake_engine,
                    )
                    app_ctx[APP_WAKE_ENGINE] = _we
                    _log.info(
                        "WAKE_ENGINE_INIT: symbols=%s ns=%s",
                        _wake_symbols,
                        _wake_ns,
                    )
                    # NarrativeEnricher вЂ” thesis injection, same Redis
                    from runtime.smc.narrative_enricher import NarrativeEnricher

                    _ne = NarrativeEnricher(
                        redis_client=_wake_redis,
                        namespace=_wake_ns,
                        executor=_exec,
                    )
                    app_ctx["_narrative_enricher"] = _ne
                    _log.info("NARRATIVE_ENRICHER_INIT: ns=%s", _wake_ns)
                else:
                    _log.warning("WAKE_ENGINE_SKIP: no redis or no symbols")
            except Exception as _we_exc:
                _log.warning("WAKE_ENGINE_INIT_FAIL: %s", _we_exc)

    async def _cleanup_bg_tasks(app_ctx: web.Application) -> None:
        for task_key, label in (
            (APP_GLOBAL_DELTA_TASK, "WS_GLOBAL_DELTA_TASK_CANCELLED"),
            (APP_BG_SMC_TASK, "WS_BG_SMC_TASK_CANCELLED"),
        ):
            task = app_ctx.get(task_key)
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    _log.debug(label)
                    pass

    app.on_startup.append(_start_bg_tasks)
    app.on_cleanup.append(_cleanup_bg_tasks)

    return app


# в”Ђв”Ђ Port bind with retry (Windows TIME_WAIT resilience) в”Ђв”Ђ

_BIND_MAX_RETRIES = 5
_BIND_RETRY_DELAY_S = 3.0


def _ws_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Suppress noisy ProactorBasePipeTransport errors on Windows."""
    msg = context.get("message", "")
    if "_ProactorBasePipeTransport" in msg or "_call_connection_lost" in msg:
        _log.debug("WS_ASYNCIO_TRANSPORT_NOISE msg=%s", msg)
        return
    loop.default_exception_handler(context)


def _run_with_retry(
    app: web.Application,
    host: str,
    port: int,
    max_retries: int = _BIND_MAX_RETRIES,
    retry_delay: float = _BIND_RETRY_DELAY_S,
) -> None:
    """Р—Р°РїСѓСЃРє aiohttp Р· retry РґР»СЏ port bind (Windows TIME_WAIT)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(_ws_exception_handler)
    runner = web.AppRunner(
        app, access_log=None
    )  # РІРёРјРєРЅРµРЅРѕ access log (P4: ~227k СЂСЏРґРєС–РІ/РґРµРЅСЊ С€СѓРјСѓ)
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


# в”Ђв”Ђ CLI entrypoint в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ


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
