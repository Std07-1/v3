"""Cowork persistent memory endpoints (ADR-001, slice cowork.001).

Two endpoints under `/api/v3/cowork/*`:

    GET  /api/v3/cowork/recent_thesis  ?symbol=XAU/USD&limit=3&max_age_h=12
    POST /api/v3/cowork/published                          (body: PublishedThesis JSON)

Architectural choices:
* Reuses the same `_validate_token` / envelope helpers as the rest of api_v3
  (kept module-local imports → no circular risk).
* GET requires `read` scope (read-only consumption of prior thesis tail).
* POST requires `cowork_write` scope (added to TokenStore.VALID_SCOPES) so a
  leaked read-only token cannot pollute the cowork journal.
* Storage path is overridable via `APP_COWORK_STORE_DIR` AppKey (set by
  `register_cowork_routes`) — default falls back to `cowork.memory.store`'s
  `COWORK_STORE_DIR` env / `cowork/data/` resolution.
* Append is idempotent on `scan_id` (CW6) — duplicate POST returns
  `appended:false` with the original path, never errors.

Invariants:
  I1 — read-only api_v3 surface boundary: writes go to a dedicated cowork
       JSONL store, not to UDS / OHLCV / signal journals.
  I5 — degraded-but-loud: schema validation failure → 422; storage failure
       → 503; never silent allow.
  CW2 — no HTTP / external SDK leaks into `cowork.memory.*`; this module is
        the adapter layer.
  CW6 — idempotent on scan_id (handled by `append_thesis`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from aiohttp import web

from cowork.memory import (
    PublishedThesis,
    append_thesis,
    read_recent,
)
from cowork.runner import evaluate_event_flag_payload
from runtime.api_v3.endpoints import (
    APP_TOKEN_STORE,
    _envelope_data,
    _error_response,
    _validate_token,
)

log = logging.getLogger("api_v3.cowork")

# Optional override for tests / VPS deploy. None → use cowork.memory.store
# default (COWORK_STORE_DIR env or `cowork/data/`).
APP_COWORK_STORE_DIR = web.AppKey("api_v3_cowork_store_dir", Path)

# Directory containing `event_flag.json` written by the cowork event watcher
# daemon (slice cowork.004). When unset, the GET /api/v3/cowork/event_flag
# endpoint returns state='absent' (still 200 — absence is normal).
APP_COWORK_TRIGGERS_DIR = web.AppKey("api_v3_cowork_triggers_dir", Path)

EVENT_FLAG_FILENAME = "event_flag.json"

# Bounds for GET parameters.
DEFAULT_LIMIT = 3
MAX_LIMIT = 20
DEFAULT_MAX_AGE_H = 12
MAX_MAX_AGE_H = 168  # 7 days — anything older is not "recent context"


# ────────────────────────────────────────────────────────────
# Scope gate
# ────────────────────────────────────────────────────────────


def _require_scope(request: web.Request, scope: str) -> Optional[web.Response]:
    """Return 403 envelope if the validated token lacks `scope`, else None.

    `_validate_token` already populated `request["api_scope"]`. Cowork POST
    needs `cowork_write`; GET tolerates the standard `read` scope.
    """
    have = request.get("api_scope")
    if have == scope:
        return None
    log.info(
        "cowork_scope_reject required=%s have=%r consumer=%r path=%s",
        scope,
        have,
        request.get("api_consumer"),
        request.path,
    )
    return _error_response(
        "scope_forbidden",
        f"This endpoint requires scope={scope!r}",
        status=403,
        required_scope=scope,
    )


def _store_dir(request: web.Request) -> Optional[Path]:
    return request.app.get(APP_COWORK_STORE_DIR)


# ────────────────────────────────────────────────────────────
# GET /api/v3/cowork/recent_thesis
# ────────────────────────────────────────────────────────────


def _parse_int(
    request: web.Request,
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> tuple[Optional[int], Optional[web.Response]]:
    raw = request.query.get(name)
    if raw is None:
        return default, None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None, _error_response(
            f"{name}_invalid",
            f"?{name} must be an integer",
            status=400,
        )
    if n < minimum or n > maximum:
        return None, _error_response(
            f"{name}_out_of_range",
            f"?{name} must be in [{minimum}, {maximum}]",
            status=400,
            value=n,
        )
    return n, None


async def _handle_recent_thesis(request: web.Request) -> web.Response:
    err = _validate_token(request)
    if err is not None:
        return err
    # Read scope is sufficient for GET (mirrors other api_v3 GET endpoints).
    scope_err = _require_scope(request, "read")
    if scope_err is not None:
        return scope_err

    symbol = (request.query.get("symbol") or "").strip()
    if not symbol:
        return _error_response(
            "symbol_required",
            "?symbol is required",
            status=400,
        )

    limit, lim_err = _parse_int(
        request, "limit", DEFAULT_LIMIT, minimum=1, maximum=MAX_LIMIT
    )
    if lim_err is not None:
        return lim_err
    max_age_h, age_err = _parse_int(
        request, "max_age_h", DEFAULT_MAX_AGE_H, minimum=1, maximum=MAX_MAX_AGE_H
    )
    if age_err is not None:
        return age_err

    store_dir = _store_dir(request)

    def _read() -> list[PublishedThesis]:
        return read_recent(
            symbol,
            limit=int(limit),  # type: ignore[arg-type]
            max_age_h=int(max_age_h),  # type: ignore[arg-type]
            store_dir=store_dir,
        )

    try:
        records = await asyncio.to_thread(_read)
    except OSError as exc:
        log.warning("cowork_recent_read_err symbol=%s err=%s", symbol, exc)
        return _error_response(
            "cowork_store_unavailable",
            "Cowork store unreachable",
            status=503,
        )

    return web.json_response(
        _envelope_data(
            "cowork_recent_thesis",
            {
                "symbol": symbol,
                "limit": int(limit),  # type: ignore[arg-type]
                "max_age_h": int(max_age_h),  # type: ignore[arg-type]
                "count": len(records),
                "theses": [r.to_jsonable() for r in records],
            },
        )
    )


# ────────────────────────────────────────────────────────────
# POST /api/v3/cowork/published
# ────────────────────────────────────────────────────────────


async def _handle_published(request: web.Request) -> web.Response:
    err = _validate_token(request)
    if err is not None:
        return err
    scope_err = _require_scope(request, "cowork_write")
    if scope_err is not None:
        return scope_err

    try:
        raw = await request.read()
    except (OSError, asyncio.CancelledError) as exc:
        log.warning("cowork_post_read_err err=%s", exc)
        return _error_response(
            "body_read_failed",
            "Failed to read request body",
            status=400,
        )
    if not raw:
        return _error_response(
            "body_empty",
            "Request body must be non-empty JSON",
            status=400,
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _error_response(
            "body_not_json",
            f"Request body is not valid JSON: {exc.msg}",
            status=400,
        )
    if not isinstance(payload, dict):
        return _error_response(
            "body_not_object",
            "Request body must be a JSON object",
            status=400,
        )

    try:
        thesis = PublishedThesis.from_jsonable(payload)
    except (KeyError, ValueError, TypeError) as exc:
        return _error_response(
            "schema_invalid",
            f"PublishedThesis schema validation failed: {exc}",
            status=422,
        )

    store_dir = _store_dir(request)

    def _append() -> tuple[Path, bool]:
        return append_thesis(thesis, store_dir=store_dir)

    try:
        path, was_dup = await asyncio.to_thread(_append)
    except OSError as exc:
        log.warning(
            "cowork_append_err scan_id=%s symbol=%s err=%s",
            thesis.scan_id,
            thesis.symbol,
            exc,
        )
        return _error_response(
            "cowork_store_unavailable",
            "Cowork store unreachable",
            status=503,
        )

    return web.json_response(
        _envelope_data(
            "cowork_published_ack",
            {
                "appended": not was_dup,
                "duplicate": was_dup,
                "scan_id": thesis.scan_id,
                "symbol": thesis.symbol,
                "path": str(path),
            },
        )
    )


# ────────────────────────────────────────────────────────────
# GET /api/v3/cowork/event_flag (slice cowork.005)
# ────────────────────────────────────────────────────────────


def _read_event_flag_file(triggers_dir: Path) -> tuple[Optional[dict], Optional[str]]:
    """Read & parse `event_flag.json`. Returns `(payload, read_error)`.

    `read_error` is `None` on success or absent file; populated only for
    real I/O / parse failures so the caller can map to a 503 envelope.
    Absent file is normal → returns `(None, None)`.
    """
    flag_path = triggers_dir / EVENT_FLAG_FILENAME
    if not flag_path.exists():
        return None, None
    try:
        raw = flag_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return None, f"flag_file_read_failed: {exc}"
    if not raw:
        # Treat empty file as malformed but non-fatal → caller emits 'invalid'.
        return {"__empty__": True}, None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"__malformed__": True}, None
    return payload, None


async def _handle_event_flag(request: web.Request) -> web.Response:
    err = _validate_token(request)
    if err is not None:
        return err
    scope_err = _require_scope(request, "read")
    if scope_err is not None:
        return scope_err

    triggers_dir: Optional[Path] = request.app.get(APP_COWORK_TRIGGERS_DIR)
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    if triggers_dir is None:
        return web.json_response(
            _envelope_data(
                "cowork_event_flag",
                {
                    "state": "absent",
                    "trigger": None,
                    "age_min": None,
                    "ts": None,
                    "now_utc": now_iso,
                    "triggers_configured": False,
                },
            )
        )

    def _read() -> tuple[Optional[dict], Optional[str]]:
        return _read_event_flag_file(triggers_dir)

    try:
        payload, read_err = await asyncio.to_thread(_read)
    except OSError as exc:
        log.warning(
            "cowork_event_flag_read_err triggers_dir=%s err=%s", triggers_dir, exc
        )
        return _error_response(
            "cowork_triggers_unavailable",
            "Cowork triggers directory unreachable",
            status=503,
        )
    if read_err is not None:
        log.warning(
            "cowork_event_flag_read_err triggers_dir=%s err=%s",
            triggers_dir,
            read_err,
        )
        return _error_response(
            "cowork_triggers_unavailable",
            "Cowork triggers directory unreachable",
            status=503,
        )

    state, trigger, age_min = evaluate_event_flag_payload(payload, now_utc)
    ts_value = payload.get("ts") if isinstance(payload, dict) else None
    return web.json_response(
        _envelope_data(
            "cowork_event_flag",
            {
                "state": state,
                "trigger": trigger,
                "age_min": age_min,
                "ts": ts_value if isinstance(ts_value, str) else None,
                "now_utc": now_iso,
                "triggers_configured": True,
            },
        )
    )


# ────────────────────────────────────────────────────────────
# Registration
# ────────────────────────────────────────────────────────────


def register_cowork_routes(
    app: web.Application,
    *,
    store_dir: Optional[Path] = None,
    triggers_dir: Optional[Path] = None,
) -> None:
    """Mount cowork endpoints. Token store must already be installed via
    `endpoints.register_routes(app, token_store=...)`.

    `store_dir` overrides `cowork.memory.store.DEFAULT_STORE_DIR` for this
    Application instance — primarily for tests. Production passes None and
    relies on the `COWORK_STORE_DIR` env / `cowork/data/` default.

    `triggers_dir` (slice cowork.005) is the directory containing
    `event_flag.json` written by the cowork event watcher daemon. When
    `None`, `/api/v3/cowork/event_flag` always returns state='absent' with
    `triggers_configured=false`.
    """
    if APP_TOKEN_STORE not in app:
        raise RuntimeError(
            "register_cowork_routes called before endpoints.register_routes"
        )
    if store_dir is not None:
        app[APP_COWORK_STORE_DIR] = store_dir
    if triggers_dir is not None:
        app[APP_COWORK_TRIGGERS_DIR] = triggers_dir
    app.router.add_get("/api/v3/cowork/recent_thesis", _handle_recent_thesis)
    app.router.add_post("/api/v3/cowork/published", _handle_published)
    app.router.add_get("/api/v3/cowork/event_flag", _handle_event_flag)
    log.info(
        "api_v3_cowork_routes_registered store_dir=%s triggers_dir=%s endpoints=3",
        store_dir or "DEFAULT",
        triggers_dir or "UNSET",
    )
