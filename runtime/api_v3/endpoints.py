"""ADR-0058 — Public read-only API endpoints (slice 058.2).

Five GET endpoints exposed under `/api/v3/*` from inside the existing
`runtime/ws/ws_server.py` aiohttp Application:

    GET /api/v3/signals/latest      ?limit=10&source=tda_cascade|smc_narrative|all
    GET /api/v3/signals/journal     ?date=YYYY-MM-DD&symbol=...&source=...
    GET /api/v3/bias/latest         ?symbol=XAU/USD
    GET /api/v3/narrative/snapshot  ?symbol=XAU/USD&tf=900
    GET /api/v3/macro/context       (no params — symbol map)

Architectural choices:

* Embedded in `ws_server` (aiohttp), not the FastAPI sidecar from slice 058.1.
  The sidecar (`auth_validator.py`) remains the SSOT for token validation
  semantics; we reuse `runtime/api_v3/token_store.TokenStore` so both
  surfaces enforce the exact same shape/scope/expiry rules.
* Auth is per-handler (cheap Redis GET) rather than nginx `auth_request` so
  this module is self-contained and ws_server can be reverse-proxied behind
  any front door.
* Every response is wrapped in the ADR-0058 §3.2.1 envelope so external
  consumers can disambiguate `kind` and version-bump on `schema_version`.

Invariants:
  I1 — read-only. Only Redis GET on token store + filesystem reads.
  I5 — degraded-but-loud. Auth/Redis errors → structured 503 envelope, never
       silent allow.
  X28 — emit canonical backend shapes verbatim (no re-derivation here either).
  F-S1-003 — `?limit` hard cap = 100.
  F-S2-002 — `?date` rejected if older than 90 days from today (UTC).
  F-S2-004 — typo paths under `/api/v3/` get a structured 404 envelope.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from aiohttp import web
from redis.exceptions import RedisError

from runtime.api_v3.token_store import TokenStore

log = logging.getLogger("api_v3.endpoints")

SCHEMA_VERSION = "v3.0"
DEFAULT_LIMIT = 10
MAX_LIMIT = 100
MAX_DATE_BACK_DAYS = 90
DEFAULT_TF_S = 900  # M15 — matches ADR-0040 execution TF
KNOWN_SOURCES = frozenset({"tda_cascade", "smc_narrative"})
# F-S2-003 — disclaimer in every envelope so consumers cannot strip it
# without violating the contract; see SECURITY.md §Public API Disclaimer.
DISCLAIMER = (
    "Educational/research data only. Not financial advice. "
    "No recommendation to buy or sell any instrument."
)

# Public AppKey so ws_server.create_app can stash these dependencies once.
APP_TOKEN_STORE = web.AppKey("api_v3_token_store", TokenStore)
APP_SIGNALS_DIR = web.AppKey("api_v3_signals_dir", str)


# ────────────────────────────────────────────────────────────
# Envelope helpers (ADR-0058 §3.2.1)
# ────────────────────────────────────────────────────────────


def _server_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _envelope_data(kind: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "server_ts": _server_ts(),
        "disclaimer": DISCLAIMER,
        "data": data,
    }


def _envelope_items(
    kind: str, items: List[Dict[str, Any]], total: Optional[int] = None
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "server_ts": _server_ts(),
        "disclaimer": DISCLAIMER,
        "items": items,
    }
    if total is not None:
        body["total"] = total
    return body


def _error_response(
    code: str,
    message: str,
    *,
    status: int,
    **extra: Any,
) -> web.Response:
    payload: Dict[str, Any] = {"code": code, "message": message}
    payload.update(extra)
    return web.json_response(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "error",
            "server_ts": _server_ts(),
            "disclaimer": DISCLAIMER,
            "data": payload,
        },
        status=status,
    )


# ────────────────────────────────────────────────────────────
# Auth dependency
# ────────────────────────────────────────────────────────────


def _validate_token(request: web.Request) -> Optional[web.Response]:
    """Return None if request is authorised, else an error response.

    Resolves the consumer name and stashes it on the request so handlers /
    access logs can pick it up via `request["api_consumer"]`.
    """
    store: Optional[TokenStore] = request.app.get(APP_TOKEN_STORE)
    if store is None:
        return _error_response(
            "auth_unconfigured",
            "API token store is not configured on this server",
            status=503,
        )
    token = request.headers.get("X-API-Key", "").strip()
    if not token:
        return _error_response(
            "missing_api_key",
            "X-API-Key header is required",
            status=401,
        )
    try:
        record = store.lookup(token)
    except RedisError as exc:
        log.warning("api_v3_auth_redis_fail err=%s", exc)
        return _error_response(
            "auth_backend_unavailable",
            "Token store unreachable",
            status=503,
        )
    if record is None:
        log.info("api_v3_auth_reject token_prefix=%s", token[:8])
        return _error_response(
            "invalid_api_key",
            "Token unknown, expired, or scope not honored",
            status=401,
        )
    request["api_consumer"] = record.consumer
    request["api_scope"] = record.scope
    return None


# ────────────────────────────────────────────────────────────
# Query parsing helpers
# ────────────────────────────────────────────────────────────


def _parse_limit(request: web.Request) -> tuple[Optional[int], Optional[web.Response]]:
    raw = request.query.get("limit")
    if raw is None:
        return DEFAULT_LIMIT, None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None, _error_response(
            "limit_invalid",
            "?limit must be a positive integer",
            status=400,
        )
    if n < 1:
        return None, _error_response(
            "limit_invalid",
            "?limit must be ≥ 1",
            status=400,
        )
    if n > MAX_LIMIT:
        return None, _error_response(
            "limit_exceeded",
            f"?limit capped at {MAX_LIMIT}",
            status=400,
            max=MAX_LIMIT,
            requested=n,
        )
    return n, None


def _parse_source(request: web.Request) -> tuple[Optional[str], Optional[web.Response]]:
    """Return ("all"|<source>, error). 'all' means no filtering."""
    raw = (request.query.get("source") or "all").strip()
    if raw == "all":
        return "all", None
    if raw in KNOWN_SOURCES:
        return raw, None
    return None, _error_response(
        "source_invalid",
        f"?source must be one of: all, {', '.join(sorted(KNOWN_SOURCES))}",
        status=400,
    )


def _parse_date(request: web.Request) -> tuple[Optional[str], Optional[web.Response]]:
    raw = (request.query.get("date") or "").strip()
    if not raw:
        return None, _error_response(
            "missing_date",
            "?date=YYYY-MM-DD is required",
            status=400,
        )
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None, _error_response(
            "date_invalid",
            "?date must be YYYY-MM-DD",
            status=400,
        )
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if dt > today:
        return None, _error_response(
            "date_in_future",
            "?date cannot be in the future",
            status=400,
        )
    if dt < today - timedelta(days=MAX_DATE_BACK_DAYS):
        return None, _error_response(
            "date_too_old",
            f"?date older than {MAX_DATE_BACK_DAYS} days back is not served",
            status=400,
            max_back_days=MAX_DATE_BACK_DAYS,
        )
    return raw, None


def _resolve_symbol(request: web.Request, default_symbol: str) -> str:
    raw = (request.query.get("symbol") or "").strip()
    return raw or default_symbol


# ────────────────────────────────────────────────────────────
# Journal readers (sync — caller wraps in to_thread)
# ────────────────────────────────────────────────────────────


def _journal_path(base_dir: str, date_str: str) -> str:
    return os.path.join(base_dir, f"journal-{date_str}.jsonl")


def _read_journal_file(path: str) -> List[Dict[str, Any]]:
    """Load all valid JSON records from one journal file (chronological)."""
    if not os.path.isfile(path):
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    # I5: surface in logs but skip the malformed row so a
                    # single bad write does not poison the whole response.
                    log.warning("api_v3_journal_skip_malformed path=%s", path)
                    continue
    except OSError as exc:
        log.warning("api_v3_journal_read_err path=%s err=%s", path, exc)
    return rows


def _filter_records(
    rows: Iterable[Dict[str, Any]],
    *,
    source: str,
    symbol: Optional[str] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rec in rows:
        if source != "all" and rec.get("source", "smc_narrative") != source:
            # Records without an explicit `source` field come from the
            # narrative pipeline (pre-TDA), so default them to smc_narrative.
            continue
        if symbol and rec.get("symbol") != symbol:
            continue
        out.append(rec)
    return out


def _classify_record_kind(rec: Dict[str, Any]) -> str:
    src = rec.get("source", "smc_narrative")
    return "tda_cascade" if src == "tda_cascade" else "smc_narrative"


# ────────────────────────────────────────────────────────────
# Route handlers
# ────────────────────────────────────────────────────────────


async def _handle_signals_latest(request: web.Request) -> web.Response:
    err = _validate_token(request)
    if err is not None:
        return err
    limit, err = _parse_limit(request)
    if err is not None:
        return err
    source, err = _parse_source(request)
    if err is not None:
        return err
    base_dir = request.app[APP_SIGNALS_DIR]
    assert limit is not None and source is not None

    def _collect() -> List[Dict[str, Any]]:
        # Walk back day-by-day until we have `limit` matching records or
        # exhaust the 90-day horizon. New entries are rare (~2/day) so
        # typically only today's file is opened.
        today = datetime.now(timezone.utc)
        gathered: List[Dict[str, Any]] = []
        for offset in range(MAX_DATE_BACK_DAYS):
            dt = today - timedelta(days=offset)
            rows = _read_journal_file(_journal_path(base_dir, dt.strftime("%Y-%m-%d")))
            if not rows:
                continue
            filtered = _filter_records(rows, source=source)
            # Newest first within the file.
            gathered.extend(reversed(filtered))
            if len(gathered) >= limit:
                break
        return gathered[:limit]

    records = await asyncio.to_thread(_collect)
    items = [{"kind": _classify_record_kind(r), "data": r} for r in records]
    return web.json_response(_envelope_items("signal_list", items, total=len(items)))


async def _handle_signals_journal(request: web.Request) -> web.Response:
    err = _validate_token(request)
    if err is not None:
        return err
    date_str, err = _parse_date(request)
    if err is not None:
        return err
    source, err = _parse_source(request)
    if err is not None:
        return err
    base_dir = request.app[APP_SIGNALS_DIR]
    assert date_str is not None and source is not None
    symbol_filter = (request.query.get("symbol") or "").strip() or None

    def _read() -> List[Dict[str, Any]]:
        rows = _read_journal_file(_journal_path(base_dir, date_str))
        return _filter_records(rows, source=source, symbol=symbol_filter)

    records = await asyncio.to_thread(_read)
    items = [{"kind": _classify_record_kind(r), "data": r} for r in records]
    return web.json_response(_envelope_items("journal", items, total=len(items)))


async def _handle_bias_latest(request: web.Request) -> web.Response:
    err = _validate_token(request)
    if err is not None:
        return err
    runner = _resolve_smc_runner(request)
    if isinstance(runner, web.Response):
        return runner
    symbol = _resolve_symbol(request, _default_symbol(request))
    bias_map = runner.get_bias_map(symbol)  # {"900": "bullish", ...}
    return web.json_response(
        _envelope_data(
            "bias_map",
            {"symbol": symbol, "bias": bias_map},
        )
    )


async def _handle_narrative_snapshot(request: web.Request) -> web.Response:
    err = _validate_token(request)
    if err is not None:
        return err
    runner = _resolve_smc_runner(request)
    if isinstance(runner, web.Response):
        return runner
    symbol = _resolve_symbol(request, _default_symbol(request))
    tf_raw = request.query.get("tf") or str(DEFAULT_TF_S)
    try:
        tf_s = int(tf_raw)
    except (TypeError, ValueError):
        return _error_response(
            "tf_invalid", "?tf must be an integer (seconds)", status=400
        )

    price = runner.get_last_price(symbol)
    if price <= 0.0:
        return _error_response(
            "price_unavailable",
            "No price data for this symbol yet (warmup or market closed)",
            status=503,
        )
    block = runner.get_narrative(symbol, tf_s, price, 0.0)
    if block is None:
        return _error_response(
            "narrative_unavailable",
            "Narrative engine disabled or warmup not finished",
            status=503,
        )
    return web.json_response(
        _envelope_data(
            "narrative_block",
            {
                "symbol": symbol,
                "tf_s": tf_s,
                "current_price": price,
                "block": block.to_wire(),
            },
        )
    )


async def _handle_macro_context(request: web.Request) -> web.Response:
    err = _validate_token(request)
    if err is not None:
        return err
    base_dir = request.app[APP_SIGNALS_DIR]
    state_path = os.path.join(base_dir, "tda_state.json")

    def _read_state() -> Dict[str, Any]:
        if not os.path.isfile(state_path):
            return {}
        try:
            with open(state_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            return raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("api_v3_macro_state_err path=%s err=%s", state_path, exc)
            return {}

    state = await asyncio.to_thread(_read_state)
    return web.json_response(
        _envelope_data(
            "tda_state",
            {"symbols": state, "source_file": "tda_state.json"},
        )
    )


# ────────────────────────────────────────────────────────────
# 404 catch-all for /api/v3/* (F-S2-004)
# ────────────────────────────────────────────────────────────


async def _handle_v3_not_found(request: web.Request) -> web.Response:
    return _error_response(
        "not_found",
        "Endpoint not found under /api/v3/",
        status=404,
        path=request.path,
    )


# ────────────────────────────────────────────────────────────
# Wiring helpers (called from ws_server.create_app)
# ────────────────────────────────────────────────────────────


# Late import keeps endpoints.py importable in tests that don't pull ws_server.
def _resolve_smc_runner(request: web.Request) -> Any:
    from runtime.ws.ws_server import APP_SMC_RUNNER  # local import — avoid cycle

    runner = request.app.get(APP_SMC_RUNNER)
    if runner is None:
        return _error_response(
            "smc_runner_unavailable",
            "SMC runner not initialised — server is still warming up",
            status=503,
        )
    return runner


def _default_symbol(request: web.Request) -> str:
    from runtime.ws.ws_server import APP_FULL_CONFIG  # local import — avoid cycle

    cfg = request.app.get(APP_FULL_CONFIG) or {}
    symbols = cfg.get("symbols") or []
    return symbols[0] if symbols else "XAU/USD"


def register_routes(
    app: web.Application,
    *,
    token_store: TokenStore,
    signals_dir: str = "data_v3/_signals",
) -> None:
    """Mount the five `/api/v3/*` endpoints on `app`.

    Idempotent at module level: a second call would shadow the AppKeys, so the
    caller (ws_server.create_app) MUST only invoke it once during startup.
    """
    app[APP_TOKEN_STORE] = token_store
    app[APP_SIGNALS_DIR] = signals_dir
    app.router.add_get("/api/v3/signals/latest", _handle_signals_latest)
    app.router.add_get("/api/v3/signals/journal", _handle_signals_journal)
    app.router.add_get("/api/v3/bias/latest", _handle_bias_latest)
    app.router.add_get("/api/v3/narrative/snapshot", _handle_narrative_snapshot)
    app.router.add_get("/api/v3/macro/context", _handle_macro_context)
    # Catch-all so typos under /api/v3/ return a structured 404 envelope
    # (F-S2-004) instead of aiohttp's plain text default.
    app.router.add_route("*", "/api/v3/{tail:.*}", _handle_v3_not_found)
    log.info(
        "api_v3_routes_registered signals_dir=%s endpoints=5",
        signals_dir,
    )
