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
import base64
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

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
APP_AUDIT_DIR = web.AppKey("api_v3_audit_dir", str)

# F-S3-002 (slice 058.5): per-day rotating audit JSONL with hashed IP.
# 90d retention enforced by a startup cleanup pass in register_routes().
AUDIT_FILE_PREFIX = "api_v3_access-"
AUDIT_FILE_SUFFIX = ".jsonl"
AUDIT_RETENTION_DAYS = 90
# Daily salt rotation: same IP yields different hashes on different days,
# so long-term cross-day tracking is impossible while same-day grouping
# (for rate-limit forensics) is preserved. Operator can override with the
# API_V3_AUDIT_SALT env var; otherwise a deterministic per-host fallback.
_AUDIT_SALT_CACHE: Dict[str, str] = {}


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
# ADR-0059 §3.1.1 — GET /api/v3/bars/window (slice 059.1)
# ────────────────────────────────────────────────────────────

# Schema version is bumped to v3.1 for analysis endpoints (ADR-0059) to let
# external consumers branch on response shape without ambiguity. ADR-0058
# endpoints stay at v3.0.
SCHEMA_VERSION_V31 = "v3.1"

# TF whitelist for /api/v3/bars/* — explicitly excludes D1 (intraday focus).
BARS_WINDOW_TF_LABEL_TO_S: Dict[str, int] = {"M15": 900, "H1": 3600, "H4": 14400}
BARS_WINDOW_DEFAULT_TFS: tuple[str, ...] = ("M15", "H1", "H4")
BARS_WINDOW_DEFAULT_COUNT = 200
BARS_WINDOW_MIN_COUNT = 50
BARS_WINDOW_MAX_COUNT = 200
# 100 KB hard cap (ADR-0059 §3.1.1 payload budget). Above this we serve a 503
# rather than return a clipped response that would silently violate the
# atomic-snapshot invariant.
BARS_WINDOW_PAYLOAD_HARD_CAP_BYTES = 102400
# Treat `since_ms` older than 1 year as "stale cursor" → return full window
# plus a meta.warnings flag instead of a tiny incremental delta computed off
# data the consumer almost certainly never cached.
BARS_WINDOW_SINCE_MS_MAX_AGE_MS = 365 * 24 * 3600 * 1000


def _envelope_data_v31(kind: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Same envelope as v3.0 but with schema_version=v3.1 (ADR-0059)."""
    return {
        "schema_version": SCHEMA_VERSION_V31,
        "kind": kind,
        "server_ts": _server_ts(),
        "disclaimer": DISCLAIMER,
        "data": data,
    }


def _enforce_payload_cap(
    body: Dict[str, Any],
    *,
    cap_bytes: int,
    error_code: str,
    log_label: str,
    log_ctx: Dict[str, Any],
) -> tuple[Optional[bytes], Optional[web.Response]]:
    """Serialize `body` and enforce hard size cap (ADR-0059 §3.1).

    Returns `(raw_bytes, None)` when within budget, `(None, 503_response)` when
    above cap. Caller MUST emit the bytes via `web.Response(body=raw_bytes)` —
    we serialize once here so the cap check matches the wire payload exactly.
    """
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(raw) > cap_bytes:
        log.warning(
            "api_v3_payload_too_large label=%s bytes=%d cap=%d ctx=%s",
            log_label,
            len(raw),
            cap_bytes,
            log_ctx,
        )
        return None, _error_response(
            error_code,
            f"Response would exceed {cap_bytes} bytes",
            status=503,
            bytes=len(raw),
            cap=cap_bytes,
        )
    return raw, None


def _bars_window_resolve_uds(request: web.Request) -> Any:
    """Return UDS instance from app or a 503 envelope response if missing."""
    from runtime.ws.app_keys import APP_UDS  # SSOT, avoids __main__ import drift

    uds = request.app.get(APP_UDS)
    if uds is None:
        return _error_response(
            "uds_unavailable",
            "Data store not initialised — server is still warming up",
            status=503,
        )
    return uds


def _parse_bars_window_tfs(
    request: web.Request,
) -> tuple[Optional[List[str]], Optional[web.Response]]:
    raw = (request.query.get("tfs") or "").strip()
    labels = (
        list(BARS_WINDOW_DEFAULT_TFS)
        if not raw
        else [part.strip().upper() for part in raw.split(",") if part.strip()]
    )
    if not labels:
        return None, _error_response("tfs_invalid", "?tfs cannot be empty", status=400)
    seen: set[str] = set()
    deduped: List[str] = []
    for lbl in labels:
        if lbl in seen:
            continue
        if lbl not in BARS_WINDOW_TF_LABEL_TO_S:
            return None, _error_response(
                "tf_invalid",
                f"?tfs entry '{lbl}' not allowed (M15, H1, H4 only)",
                status=400,
                allowed=list(BARS_WINDOW_TF_LABEL_TO_S.keys()),
            )
        seen.add(lbl)
        deduped.append(lbl)
    return deduped, None


def _parse_bars_window_count(
    request: web.Request,
) -> tuple[Optional[int], Optional[web.Response]]:
    raw = request.query.get("count")
    if raw is None:
        return BARS_WINDOW_DEFAULT_COUNT, None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None, _error_response(
            "count_invalid", "?count must be an integer", status=400
        )
    if n < BARS_WINDOW_MIN_COUNT:
        return None, _error_response(
            "count_below_min",
            f"?count must be ≥ {BARS_WINDOW_MIN_COUNT}",
            status=400,
            min=BARS_WINDOW_MIN_COUNT,
            requested=n,
        )
    if n > BARS_WINDOW_MAX_COUNT:
        return None, _error_response(
            "count_above_max",
            f"?count must be ≤ {BARS_WINDOW_MAX_COUNT}",
            status=400,
            max=BARS_WINDOW_MAX_COUNT,
            requested=n,
        )
    return n, None


def _parse_bars_window_since_ms(
    request: web.Request,
) -> tuple[Optional[int], Optional[web.Response]]:
    raw = request.query.get("since_ms")
    if raw is None:
        return None, None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None, _error_response(
            "since_ms_invalid", "?since_ms must be an integer (epoch ms)", status=400
        )
    if n < 0:
        return None, _error_response(
            "since_ms_invalid", "?since_ms must be ≥ 0", status=400
        )
    return n, None


def _validate_bars_window_symbol(
    request: web.Request,
) -> tuple[Optional[str], Optional[web.Response]]:
    from runtime.ws.app_keys import APP_FULL_CONFIG  # SSOT, avoids __main__ drift

    raw = (request.query.get("symbol") or "").strip()
    if not raw:
        return None, _error_response(
            "missing_symbol", "?symbol is required", status=400
        )
    cfg = request.app.get(APP_FULL_CONFIG) or {}
    allowed = list(cfg.get("symbols") or [])
    if raw not in allowed:
        return None, _error_response(
            "symbol_invalid",
            f"?symbol '{raw}' not in configured symbols",
            status=400,
            allowed=list(allowed),
        )
    return raw, None


def _lwc_to_wire_bar(b: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert UDS `bars_lwc` row → ADR-0059 wire shape {open_ms,o,h,l,c,v}.

    Returns None for rows that fail the basic shape guard so the caller can
    skip them (instead of poisoning the response with NaN/None values).
    Wire key is `"l"` per ADR-0059 §3.1.1 (NOT `"low"` — V3 dict-vs-dataclass
    convention, see X13).
    """
    open_ms = b.get("open_time_ms")
    o = b.get("open")
    h = b.get("high")
    low = b.get("low")
    c = b.get("close")
    v = b.get("volume", 0.0)
    if not isinstance(open_ms, int):
        return None
    if o is None or h is None or low is None or c is None:
        return None
    return {
        "open_ms": int(open_ms),
        "o": float(o),
        "h": float(h),
        "l": float(low),
        "c": float(c),
        "v": float(v),
    }


def _read_bars_window_atomic(
    uds: Any,
    symbol: str,
    tf_s_list: List[int],
    count: int,
) -> Dict[int, List[Dict[str, Any]]]:
    """Synchronously read N TFs in one batch (atomic snapshot — no yield).

    All `read_window` calls happen inside one `to_thread` invocation by the
    caller, so no other coroutine can interleave preview/final state between
    the per-TF reads. Satisfies ADR-0059 §3.1.1 atomic invariant.
    """
    from runtime.store.uds import ReadPolicy, WindowSpec

    policy = ReadPolicy(disk_policy="explicit", prefer_redis=True)
    out: Dict[int, List[Dict[str, Any]]] = {}
    for tf_s in tf_s_list:
        spec = WindowSpec(symbol=symbol, tf_s=tf_s, limit=count, cold_load=True)
        result = uds.read_window(spec, policy)
        bars_lwc = getattr(result, "bars_lwc", None) or []
        out[tf_s] = list(bars_lwc)
    return out


async def _handle_bars_window(request: web.Request) -> web.Response:
    err = _validate_token(request)
    if err is not None:
        return err
    symbol, err = _validate_bars_window_symbol(request)
    if err is not None:
        return err
    tf_labels, err = _parse_bars_window_tfs(request)
    if err is not None:
        return err
    count, err = _parse_bars_window_count(request)
    if err is not None:
        return err
    since_ms, err = _parse_bars_window_since_ms(request)
    if err is not None:
        return err
    uds_or_err = _bars_window_resolve_uds(request)
    if isinstance(uds_or_err, web.Response):
        return uds_or_err
    uds = uds_or_err
    assert symbol is not None and tf_labels is not None and count is not None

    # ADR-0059 §3.1 cross-endpoint rule: current_price = M15.close. We
    # always include M15 in the read set even if the consumer didn't ask
    # for it, so the price is atomic with the rest of the snapshot.
    requested_tf_s = [BARS_WINDOW_TF_LABEL_TO_S[lbl] for lbl in tf_labels]
    m15_tf_s = BARS_WINDOW_TF_LABEL_TO_S["M15"]
    read_tf_s_list = list(requested_tf_s)
    if m15_tf_s not in read_tf_s_list:
        read_tf_s_list.append(m15_tf_s)
    # Read M15 with at least 1 bar even if not in requested set.
    # We piggy-back on the per-TF count when M15 is in the request, else
    # use a tiny limit just to fetch the last complete bar for price.
    m15_only_for_price = m15_tf_s not in requested_tf_s

    warnings: List[str] = []

    # since_ms staleness check (ADR-0059 §3.1.1 incremental fetch semantics).
    effective_since_ms = since_ms
    if since_ms is not None:
        now_ms = int(time.time() * 1000)
        if (now_ms - since_ms) > BARS_WINDOW_SINCE_MS_MAX_AGE_MS:
            warnings.append("since_ms_too_old_full_window_returned")
            effective_since_ms = None

    def _read_all() -> Dict[int, List[Dict[str, Any]]]:
        # M15 piggy-back uses count=1 only when the consumer didn't ask for it.
        if not m15_only_for_price:
            return _read_bars_window_atomic(uds, symbol, read_tf_s_list, count)
        # Mixed limits — read requested TFs at `count`, M15 at min size.
        from runtime.store.uds import ReadPolicy, WindowSpec

        policy = ReadPolicy(disk_policy="explicit", prefer_redis=True)
        out: Dict[int, List[Dict[str, Any]]] = {}
        for tf_s in requested_tf_s:
            spec = WindowSpec(symbol=symbol, tf_s=tf_s, limit=count, cold_load=True)
            res = uds.read_window(spec, policy)
            out[tf_s] = list(getattr(res, "bars_lwc", None) or [])
        m15_spec = WindowSpec(symbol=symbol, tf_s=m15_tf_s, limit=1, cold_load=True)
        m15_res = uds.read_window(m15_spec, policy)
        out[m15_tf_s] = list(getattr(m15_res, "bars_lwc", None) or [])
        return out

    raw_by_tf = await asyncio.to_thread(_read_all)

    # Compute current_price from M15 last complete bar (always available
    # in raw_by_tf because we forced M15 above).
    m15_rows = raw_by_tf.get(m15_tf_s, [])
    m15_complete = [r for r in m15_rows if bool(r.get("complete", True))]
    if not m15_complete:
        return _error_response(
            "price_unavailable",
            "No complete M15 bar yet (warmup or market closed)",
            status=503,
        )
    current_price = float(m15_complete[-1].get("close") or 0.0)

    # Build per-TF wire payloads for the requested TFs only.
    bars_payload: Dict[str, List[Dict[str, Any]]] = {}
    latest_open_ms: Dict[str, int] = {}
    for lbl in tf_labels:
        tf_s = BARS_WINDOW_TF_LABEL_TO_S[lbl]
        rows = raw_by_tf.get(tf_s, [])
        wire_rows: List[Dict[str, Any]] = []
        for r in rows:
            if not bool(r.get("complete", True)):
                continue  # I3 — final-only on the public surface
            wire = _lwc_to_wire_bar(r)
            if wire is None:
                continue
            if effective_since_ms is not None and wire["open_ms"] <= effective_since_ms:
                continue
            wire_rows.append(wire)
        # Deduplicate by open_ms while preserving order (defensive — UDS
        # already dedupes, but this makes the contract self-evident).
        seen_open: set[int] = set()
        deduped: List[Dict[str, Any]] = []
        for w in wire_rows:
            if w["open_ms"] in seen_open:
                continue
            seen_open.add(w["open_ms"])
            deduped.append(w)
        # Monotonic ASC by open_ms.
        deduped.sort(key=lambda r: r["open_ms"])
        bars_payload[lbl] = deduped
        if deduped:
            latest_open_ms[lbl] = deduped[-1]["open_ms"]

    incremental = since_ms is not None
    data: Dict[str, Any] = {
        "symbol": symbol,
        "current_price": current_price,
        "incremental": incremental,
        "since_ms": since_ms,
        "bars": bars_payload,
        "meta": {
            "latest_open_ms": latest_open_ms,
            "warnings": warnings,
        },
    }
    body = _envelope_data_v31("bars_window", data)
    raw_bytes, cap_err = _enforce_payload_cap(
        body,
        cap_bytes=BARS_WINDOW_PAYLOAD_HARD_CAP_BYTES,
        error_code="payload_too_large",
        log_label="bars_window",
        log_ctx={"symbol": symbol, "tfs": ",".join(tf_labels), "count": count},
    )
    if cap_err is not None:
        return cap_err
    assert raw_bytes is not None
    return web.Response(
        body=raw_bytes,
        status=200,
        content_type="application/json",
    )


# ────────────────────────────────────────────────────────────
# GET /api/v3/smc/zones (ADR-0059 §3.1.2 — slice 059.2)
# ────────────────────────────────────────────────────────────

# Reuse bars/window TF mapping (M15/H1/H4) — same allowlist per ADR §3.1.2.
SMC_ZONES_TF_LABEL_TO_S = BARS_WINDOW_TF_LABEL_TO_S
SMC_ZONES_DEFAULT_LIMIT = 50
SMC_ZONES_MIN_LIMIT = 1
SMC_ZONES_MAX_LIMIT = 200
SMC_ZONES_PAYLOAD_HARD_CAP_BYTES = 102400  # 100 KB — same envelope as bars_window
SMC_ZONES_KIND_FILTERS_ALLOWED = ("all", "ob", "fvg", "liquidity")
SMC_ZONES_STATUS_FILTERS_ALLOWED = ("all", "active", "mitigated")

# ADR-0059 §3.1.2 + core/smc/types.py:ZONE_KINDS canonical mapping.
# Kind filter "ob" / "fvg" / "liquidity" (where liquidity = premium/discount
# zones, NOT levels — those go through /smc/levels in slice 059.3).
SMC_ZONES_FILTER_TO_INTERNAL: Dict[str, frozenset] = {
    "ob": frozenset({"ob_bull", "ob_bear"}),
    "fvg": frozenset({"fvg_bull", "fvg_bear", "ifvg_bull", "ifvg_bear"}),
    "liquidity": frozenset({"premium", "discount"}),
}

SMC_ZONES_INTERNAL_TO_EXTERNAL_KIND: Dict[str, str] = {
    "ob_bull": "order_block",
    "ob_bear": "order_block",
    "fvg_bull": "fvg",
    "fvg_bear": "fvg",
    "ifvg_bull": "ifvg",
    "ifvg_bear": "ifvg",
    "premium": "premium_discount",
    "discount": "premium_discount",
}

SMC_ZONES_INTERNAL_TO_DIRECTION: Dict[str, str] = {
    "ob_bull": "bullish",
    "ob_bear": "bearish",
    "fvg_bull": "bullish",
    "fvg_bear": "bearish",
    "ifvg_bull": "bullish",
    "ifvg_bear": "bearish",
    "premium": "bearish",  # P/D zones: premium = sell zone
    "discount": "bullish",  # discount = buy zone
}

# Status filter semantics (ADR-0059 §3.1.2):
#   "active"    → status == "active"
#   "mitigated" → status ∈ {mitigated, filled, partially_filled, breaker, expired, fading}
#   "all"       → no filter
SMC_ZONES_MITIGATED_STATUSES: frozenset = frozenset(
    {"mitigated", "filled", "partially_filled", "breaker", "expired", "fading"}
)


def _validate_smc_zones_symbol(
    request: web.Request,
) -> tuple[Optional[str], Optional[web.Response]]:
    # Same validation rules as bars/window — delegate.
    return _validate_bars_window_symbol(request)


def _parse_smc_zones_tf(
    request: web.Request,
) -> tuple[Optional[str], Optional[web.Response]]:
    """Accept tf in two formats — alpha label OR seconds (ADR-0059 §3.1.2 contract harmonization).

    External callers may send `tf=900` (seconds) or `tf=M15` (alpha, the documented contract).
    Both resolve to the same internal label so /smc/zones works for either.

    Mapping seconds → label:
      900   → M15
      3600  → H1
      14400 → H4

    Anything else (M5/300, M30/1800, D1/86400 — all not in zones whitelist) →
    400 tf_invalid with explicit allowed list (both formats listed).
    """
    raw = (request.query.get("tf") or "").strip()
    if not raw:
        return None, _error_response(
            "missing_tf", "?tf is required (M15, H1 or H4)", status=400
        )

    # Path 1: alpha label (M15 / H1 / H4 — case-insensitive)
    raw_alpha = raw.upper()
    if raw_alpha in SMC_ZONES_TF_LABEL_TO_S:
        return raw_alpha, None

    # Path 2: seconds (900 / 3600 / 14400)
    try:
        tf_s = int(raw)
        for label, seconds in SMC_ZONES_TF_LABEL_TO_S.items():
            if seconds == tf_s:
                return label, None
    except (TypeError, ValueError):
        pass

    # Both paths exhausted — explicit 400 with both formats in allowed list
    allowed_alpha = list(SMC_ZONES_TF_LABEL_TO_S.keys())
    allowed_seconds = [str(s) for s in SMC_ZONES_TF_LABEL_TO_S.values()]
    return None, _error_response(
        "tf_invalid",
        f"?tf '{raw}' not allowed (use M15/H1/H4 or seconds 900/3600/14400)",
        status=400,
        allowed=allowed_alpha + allowed_seconds,
    )


def _parse_smc_zones_limit(
    request: web.Request,
) -> tuple[Optional[int], Optional[web.Response]]:
    raw = request.query.get("limit")
    if raw is None:
        return SMC_ZONES_DEFAULT_LIMIT, None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None, _error_response(
            "limit_invalid", "?limit must be an integer", status=400
        )
    if n < SMC_ZONES_MIN_LIMIT:
        return None, _error_response(
            "limit_below_min",
            f"?limit must be ≥ {SMC_ZONES_MIN_LIMIT}",
            status=400,
            min=SMC_ZONES_MIN_LIMIT,
            requested=n,
        )
    if n > SMC_ZONES_MAX_LIMIT:
        return None, _error_response(
            "limit_above_max",
            f"?limit must be ≤ {SMC_ZONES_MAX_LIMIT}",
            status=400,
            max=SMC_ZONES_MAX_LIMIT,
            requested=n,
        )
    return n, None


def _parse_smc_zones_offset(
    request: web.Request,
) -> tuple[Optional[int], Optional[web.Response]]:
    raw = request.query.get("offset")
    if raw is None:
        return 0, None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None, _error_response(
            "offset_invalid", "?offset must be an integer", status=400
        )
    if n < 0:
        return None, _error_response(
            "offset_invalid", "?offset must be ≥ 0", status=400, requested=n
        )
    return n, None


def _parse_smc_zones_kind(
    request: web.Request,
) -> tuple[Optional[str], Optional[web.Response]]:
    raw = (request.query.get("kind") or "all").strip().lower()
    if raw not in SMC_ZONES_KIND_FILTERS_ALLOWED:
        return None, _error_response(
            "kind_invalid",
            f"?kind '{raw}' not allowed",
            status=400,
            allowed=list(SMC_ZONES_KIND_FILTERS_ALLOWED),
        )
    return raw, None


def _parse_smc_zones_status(
    request: web.Request,
) -> tuple[Optional[str], Optional[web.Response]]:
    raw = (request.query.get("status") or "active").strip().lower()
    if raw not in SMC_ZONES_STATUS_FILTERS_ALLOWED:
        return None, _error_response(
            "status_invalid",
            f"?status '{raw}' not allowed",
            status=400,
            allowed=list(SMC_ZONES_STATUS_FILTERS_ALLOWED),
        )
    return raw, None


def _parse_smc_zones_include_internal(request: web.Request) -> bool:
    raw = (request.query.get("include_internal") or "").strip().lower()
    return raw in ("1", "true", "yes")


def _zone_passes_kind_filter(zone_internal_kind: str, kind_filter: str) -> bool:
    if kind_filter == "all":
        return zone_internal_kind in SMC_ZONES_INTERNAL_TO_EXTERNAL_KIND
    allowed = SMC_ZONES_FILTER_TO_INTERNAL.get(kind_filter)
    return bool(allowed and zone_internal_kind in allowed)


def _zone_passes_status_filter(zone_status: str, status_filter: str) -> bool:
    if status_filter == "all":
        return True
    if status_filter == "active":
        return zone_status == "active"
    # status_filter == "mitigated"
    return zone_status in SMC_ZONES_MITIGATED_STATUSES


def _compute_distance_pts(current_price: float, top: float, bottom: float) -> float:
    """Distance from `current_price` to nearest zone edge (0 if price inside).

    Pure: same inputs → same output. Used for proximity_atr ranking.
    """
    if bottom <= current_price <= top:
        return 0.0
    if current_price > top:
        return current_price - top
    return bottom - current_price


def _zone_to_wire(
    zone: Any,
    *,
    tf_label: str,
    current_price: float,
    atr: float,
    grade_payload: Optional[Dict[str, Any]],
    include_internal: bool,
) -> Optional[Dict[str, Any]]:
    """Convert SmcZone → ADR-0059 §3.1.2 wire dict.

    Returns None for zones whose internal kind has no public mapping (defensive
    — should never happen for ZONE_KINDS, but keeps payload sealed if engine
    introduces a new kind without ADR update).
    """
    internal_kind = getattr(zone, "kind", None)
    if internal_kind not in SMC_ZONES_INTERNAL_TO_EXTERNAL_KIND:
        return None
    top = float(getattr(zone, "high", 0.0))
    bottom = float(getattr(zone, "low", 0.0))
    distance_pts = _compute_distance_pts(current_price, top, bottom)
    proximity_atr_val = (distance_pts / atr) if atr > 0 else None
    grade = "C"
    factors: List[str] = []
    if grade_payload:
        grade = str(grade_payload.get("grade") or "C")
        raw_factors = grade_payload.get("factors") or []
        factors = [str(f) for f in raw_factors]
    end_ms = getattr(zone, "end_ms", None)
    anchor_ms = int(getattr(zone, "anchor_bar_ms", 0))
    wire: Dict[str, Any] = {
        "id": str(getattr(zone, "id", "")),
        "kind": SMC_ZONES_INTERNAL_TO_EXTERNAL_KIND[internal_kind],
        "direction": SMC_ZONES_INTERNAL_TO_DIRECTION[internal_kind],
        "tf": tf_label,
        "top": top,
        "bottom": bottom,
        "anchor_ms": anchor_ms,
        "last_touch_ms": int(end_ms) if end_ms is not None else anchor_ms,
        "status": str(getattr(zone, "status", "active")),
        "grade": grade,
        "confluence_factors": factors,
        "distance_pts": round(distance_pts, 4),
        "proximity_atr": (
            round(proximity_atr_val, 4) if proximity_atr_val is not None else None
        ),
    }
    if include_internal and grade_payload:
        # F-S1-003 Opus compromise: numeric grade_score gated behind explicit
        # debug flag. NEVER expose other internal fields (X28).
        score = grade_payload.get("score")
        if score is not None:
            wire["grade_score"] = int(score)
    return wire


# ---- ADR-0059 §5.5 / 059.7 — cursor pagination (F-S2-001) -----------------


def _smc_zones_sort_key(w: Dict[str, Any]) -> tuple[int, float, str]:
    """Stable sort: proximity ASC, zone id lex ASC.

    None proximity (atr unavailable) buckets to the back so they never
    displace good zones. Tie-break by id is mandatory for cursor stability —
    two zones with identical proximity must always land in the same order.
    """
    prox = w.get("proximity_atr")
    zid = str(w.get("id") or "")
    if prox is None:
        return (1, 0.0, zid)
    return (0, float(prox), zid)


def _smc_zones_compute_snapshot_id(sorted_wire_zones: List[Dict[str, Any]]) -> str:
    """Deterministic snapshot fingerprint from zone IDs (post-filter, sorted).

    SHA-1 of comma-joined ids → first 16 hex chars. Mutates whenever the
    underlying zone set changes (add / remove / re-grade reorders proximity).
    """
    raw = ",".join(str(z.get("id") or "") for z in sorted_wire_zones)
    # usedforsecurity=False — це fingerprint для cache-key/ETag (CWE-327
    # не застосовується); пояснюємо bandit через kwarg, а не nosec.
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


# RFC 4648 §5 urlsafe base64 alphabet + padding char.
_CURSOR_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_="
)


def _cursor_encode(
    snapshot_id: str, last_proximity: Optional[float], last_id: str
) -> str:
    payload = {
        "snapshot_id": snapshot_id,
        "last_proximity": last_proximity,
        "last_id": last_id,
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _cursor_decode(
    raw: str,
) -> tuple[Optional[Dict[str, Any]], Optional[web.Response]]:
    """Parse opaque cursor → dict or 400 error envelope.

    Errors split: `cursor_invalid` for base64 problems, `cursor_corrupt` for
    JSON / shape problems — gives callers a clear hint.
    """
    if not raw:
        return None, _error_response(
            "cursor_invalid", "?cursor must be a non-empty token", status=400
        )
    # Strict urlsafe alphabet check — base64.urlsafe_b64decode is lenient by
    # default and silently drops invalid bytes, which would let malformed
    # cursors slip through and be reported as JSON corruption instead.
    if any(c not in _CURSOR_ALPHABET for c in raw):
        return None, _error_response(
            "cursor_invalid",
            "?cursor contains characters outside base64url alphabet",
            status=400,
        )
    try:
        padding = "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode((raw + padding).encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        return None, _error_response(
            "cursor_invalid",
            f"?cursor is not valid base64url: {exc}",
            status=400,
        )
    try:
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, _error_response(
            "cursor_corrupt",
            f"?cursor payload is not valid JSON: {exc}",
            status=400,
        )
    if (
        not isinstance(payload, dict)
        or "snapshot_id" not in payload
        or "last_id" not in payload
        or "last_proximity" not in payload
    ):
        return None, _error_response(
            "cursor_corrupt",
            "?cursor missing required fields {snapshot_id, last_id, last_proximity}",
            status=400,
        )
    return payload, None


def _smc_zones_apply_cursor(
    sorted_zones: List[Dict[str, Any]],
    cursor_payload: Dict[str, Any],
    snapshot_id: str,
) -> tuple[List[Dict[str, Any]], List[str]]:
    """Slice `sorted_zones` to entries strictly after the cursor position.

    Returns `(remaining_zones, warnings)`. When the cursor's snapshot_id no
    longer matches the current snapshot, we still serve the next page on a
    best-effort basis but emit `cursor_stale` so the caller can decide whether to
    restart pagination.
    """
    warnings: List[str] = []
    if cursor_payload.get("snapshot_id") != snapshot_id:
        warnings.append("cursor_stale")
    last_prox_raw = cursor_payload.get("last_proximity")
    last_prox = float(last_prox_raw) if last_prox_raw is not None else None
    last_id = str(cursor_payload.get("last_id") or "")
    # last_proximity=None means cursor was emitted from the no-proximity
    # bucket — every bucket-0 entry sorts before, so we resume inside bucket 1.
    if last_prox is None:
        cursor_key: tuple[int, float, str] = (1, 0.0, last_id)
    else:
        cursor_key = (0, last_prox, last_id)
    remaining: List[Dict[str, Any]] = []
    for z in sorted_zones:
        if _smc_zones_sort_key(z) > cursor_key:
            remaining.append(z)
    return remaining, warnings


async def _handle_smc_zones(request: web.Request) -> web.Response:
    """ADR-0059 §3.1.2 + §5.5 — paginated zones with proximity ranking.

    Phases (D6.1): validate → resolve_runner → fetch_snapshot → filter →
    sort → paginate (cursor or offset) → assemble → cap_check → respond.

    Pagination modes (ADR-0059 §5.5 / 059.7):
      * `?cursor=<opaque>` — opaque token issued by previous page response.
        Stable across snapshot mutation: emits `data.warnings: [cursor_stale]`
        + best-effort fallback when SmcRunner mutated zones between pages.
      * `?offset=<int>` — legacy positional pagination (kept for backward
        compatibility, ADR-0059 §3.1.2). Ignored when `?cursor` present.
    Response always carries `next_cursor` (null on last page).
    """
    err = _validate_token(request)
    if err is not None:
        return err

    symbol, err = _validate_smc_zones_symbol(request)
    if err is not None:
        return err
    tf_label, err = _parse_smc_zones_tf(request)
    if err is not None:
        return err
    limit, err = _parse_smc_zones_limit(request)
    if err is not None:
        return err
    offset, err = _parse_smc_zones_offset(request)
    if err is not None:
        return err
    kind_filter, err = _parse_smc_zones_kind(request)
    if err is not None:
        return err
    status_filter, err = _parse_smc_zones_status(request)
    if err is not None:
        return err
    include_internal = _parse_smc_zones_include_internal(request)
    cursor_raw = (request.query.get("cursor") or "").strip()
    cursor_payload: Optional[Dict[str, Any]] = None
    if cursor_raw:
        cursor_payload, err = _cursor_decode(cursor_raw)
        if err is not None:
            return err
    assert (
        symbol is not None
        and tf_label is not None
        and limit is not None
        and offset is not None
        and kind_filter is not None
        and status_filter is not None
    )

    runner_or_err = _resolve_smc_runner(request)
    if isinstance(runner_or_err, web.Response):
        return runner_or_err
    runner = runner_or_err

    uds_or_err = _bars_window_resolve_uds(request)
    if isinstance(uds_or_err, web.Response):
        return uds_or_err
    uds = uds_or_err

    tf_s = SMC_ZONES_TF_LABEL_TO_S[tf_label]
    m15_tf_s = SMC_ZONES_TF_LABEL_TO_S["M15"]

    def _read_all() -> Dict[str, Any]:
        # Atomic snapshot — all SMC reads + UDS price read in one to_thread.
        # ADR-0059 §3.1 cross-endpoint rule: current_price = M15.close from
        # UDS (NOT runner.get_last_price which is a live tick aggregate).
        from runtime.store.uds import ReadPolicy, WindowSpec

        policy = ReadPolicy(disk_policy="explicit", prefer_redis=True)
        m15_spec = WindowSpec(symbol=symbol, tf_s=m15_tf_s, limit=1, cold_load=True)
        m15_res = uds.read_window(m15_spec, policy)
        m15_rows = list(getattr(m15_res, "bars_lwc", None) or [])
        snap = runner.get_snapshot(symbol, tf_s)
        grades = runner.get_zone_grades(symbol, tf_s) or {}
        atr = float(runner.get_atr(symbol, tf_s) or 0.0)
        return {
            "m15_rows": m15_rows,
            "snapshot": snap,
            "grades": grades,
            "atr": atr,
        }

    bundle = await asyncio.to_thread(_read_all)

    # current_price from last complete M15 bar (cross-endpoint atomic rule).
    m15_complete = [r for r in bundle["m15_rows"] if bool(r.get("complete", True))]
    if not m15_complete:
        return _error_response(
            "price_unavailable",
            "No complete M15 bar yet (warmup or market closed)",
            status=503,
        )
    current_price = float(m15_complete[-1].get("close") or 0.0)

    snap = bundle["snapshot"]
    raw_zones: List[Any] = list(getattr(snap, "zones", []) or []) if snap else []
    grades: Dict[str, Any] = bundle["grades"] or {}
    atr = float(bundle["atr"])

    # Filter → score → sort → paginate.
    filtered: List[Dict[str, Any]] = []
    for z in raw_zones:
        if not _zone_passes_kind_filter(getattr(z, "kind", ""), kind_filter):
            continue
        if not _zone_passes_status_filter(getattr(z, "status", ""), status_filter):
            continue
        wire = _zone_to_wire(
            z,
            tf_label=tf_label,
            current_price=current_price,
            atr=atr,
            grade_payload=grades.get(getattr(z, "id", "")),
            include_internal=include_internal,
        )
        if wire is not None:
            filtered.append(wire)

    # Sort by proximity_atr ASC (closest to current price first).
    # Tie-break on zone id for cursor stability — same key, same order.
    filtered.sort(key=_smc_zones_sort_key)

    total = len(filtered)
    snapshot_id = _smc_zones_compute_snapshot_id(filtered)
    warnings: List[str] = []
    cursor_used = cursor_payload is not None
    if cursor_used:
        assert cursor_payload is not None  # mypy
        remaining, warnings = _smc_zones_apply_cursor(
            filtered, cursor_payload, snapshot_id
        )
        page = remaining[:limit]
    else:
        page = filtered[offset : offset + limit]

    # next_cursor — null on last page, opaque token otherwise.
    if page and len(page) == limit:
        # Only emit if there's a chance more pages exist.
        if cursor_used:
            more = len(remaining) > limit
        else:
            more = (offset + limit) < total
        if more:
            tail = page[-1]
            tail_prox = tail.get("proximity_atr")
            tail_prox_f = float(tail_prox) if tail_prox is not None else None
            next_cursor: Optional[str] = _cursor_encode(
                snapshot_id, tail_prox_f, str(tail.get("id") or "")
            )
        else:
            next_cursor = None
    else:
        next_cursor = None

    data: Dict[str, Any] = {
        "symbol": symbol,
        "tf": tf_label,
        "current_price": current_price,
        "total": total,
        "limit": limit,
        "offset": offset,
        "snapshot_id": snapshot_id,
        "next_cursor": next_cursor,
        "zones": page,
    }
    if warnings:
        data["warnings"] = warnings
    body = _envelope_data_v31("smc_zones", data)
    raw_bytes, cap_err = _enforce_payload_cap(
        body,
        cap_bytes=SMC_ZONES_PAYLOAD_HARD_CAP_BYTES,
        error_code="payload_too_large",
        log_label="smc_zones",
        log_ctx={
            "symbol": symbol,
            "tf": tf_label,
            "total": total,
            "limit": limit,
            "offset": offset,
            "cursor_used": cursor_used,
        },
    )
    if cap_err is not None:
        return cap_err
    assert raw_bytes is not None
    return web.Response(
        body=raw_bytes,
        status=200,
        content_type="application/json",
    )


# ────────────────────────────────────────────────────────────
# ADR-0059 §3.1.3 — GET /api/v3/smc/levels (slice 059.3)
# ────────────────────────────────────────────────────────────

SMC_LEVELS_PAYLOAD_HARD_CAP_BYTES = 5 * 1024  # 5 KB per ADR §3.1.3
SMC_LEVELS_PREV_DAY_LOOKBACK = 8  # D1 bars to read (≥1 prev day + 5 prev week)
SMC_LEVELS_PREV_WEEK_DAYS = 5  # trading days = "previous week" approximation


def _smc_levels_session_complete(
    state: Any, current_time_ms: int, sessions_def: List[Any]
) -> bool:
    """A session is 'complete' for today when it has data and is no longer active.

    Active = `current_time_ms` falls inside its UTC window. If no current data
    yet (session hasn't opened today) → not complete.
    """
    if state.current_high is None or state.current_low is None:
        return False
    return not bool(state.active)


def _smc_levels_session_to_wire(
    state: Any, current_time_ms: int, sessions_def: List[Any]
) -> Dict[str, Any]:
    """SessionState → wire dict per ADR §3.1.3.

    Fields: high, low, complete, swept_high, swept_low.

    swept_high/low semantics: did THIS session's running H/L exceed its own
    PRIOR (yesterday's) H/L. Booleans only — frontend renders as-is (X28).
    """
    cur_h = state.current_high
    cur_l = state.current_low
    prev_h = state.previous_high
    prev_l = state.previous_low
    swept_high = (
        cur_h is not None and prev_h is not None and float(cur_h) > float(prev_h)
    )
    swept_low = (
        cur_l is not None and prev_l is not None and float(cur_l) < float(prev_l)
    )
    return {
        "high": float(cur_h) if cur_h is not None else None,
        "low": float(cur_l) if cur_l is not None else None,
        "complete": _smc_levels_session_complete(state, current_time_ms, sessions_def),
        "swept_high": bool(swept_high),
        "swept_low": bool(swept_low),
    }


# Internal session name → external wire key (per ADR §3.1.3 sessions block).
_SMC_LEVELS_SESSION_NAME_MAP: Dict[str, str] = {
    "asia": "asia",
    "london": "london",
    "newyork": "ny",
}


def _build_previous_day(d1_complete: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Last completed D1 bar → {high, low, close, ts_ms}. None if no D1 data."""
    if not d1_complete:
        return None
    last = d1_complete[-1]
    open_ms = last.get("open_time_ms")
    high = last.get("high")
    low = last.get("low")
    close = last.get("close")
    if not isinstance(open_ms, int) or high is None or low is None or close is None:
        return None
    return {
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "ts_ms": int(open_ms),
    }


def _build_previous_week(d1_complete: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Aggregate {high, low} from the 5 D1 bars preceding 'previous_day'.

    Per ADR §3.1.3: previous_week intentionally has ONLY {high, low} — no close,
    no ts_ms. Do not add fields 'for symmetry'.
    Returns None if fewer than 1 prior bar available (warmup).
    """
    if len(d1_complete) < 2:
        return None
    prior = d1_complete[:-1][-SMC_LEVELS_PREV_WEEK_DAYS:]
    if not prior:
        return None
    highs = [float(b["high"]) for b in prior if b.get("high") is not None]
    lows = [float(b["low"]) for b in prior if b.get("low") is not None]
    if not highs or not lows:
        return None
    return {"high": max(highs), "low": min(lows)}


async def _handle_smc_levels(request: web.Request) -> web.Response:
    """ADR-0059 §3.1.3 — compact key levels + sessions snapshot.

    Phases (D6.1): validate_symbol → resolve_runner → atomic_read
    (M15 1 bar + D1 N bars + session_states) → assemble → cap_check → respond.
    """
    import time as _time
    from runtime.ws.app_keys import (
        APP_FULL_CONFIG,
        APP_UDS,
    )  # SSOT, avoids __main__ drift

    auth_err = _validate_token(request)
    if auth_err is not None:
        return auth_err

    symbol, sym_err = _validate_bars_window_symbol(request)
    if sym_err is not None:
        return sym_err
    assert symbol is not None

    runner_or_err = _resolve_smc_runner(request)
    if isinstance(runner_or_err, web.Response):
        return runner_or_err
    runner = runner_or_err

    uds = request.app.get(APP_UDS)
    if uds is None:
        return _error_response(
            "uds_unavailable",
            "UDS not configured",
            status=503,
        )

    cfg = request.app.get(APP_FULL_CONFIG) or {}
    sessions_cfg = cfg.get("smc", {}).get("sessions", {}).get("definitions", {})
    # SSOT for session label/window definitions; only needed for completeness check
    from core.smc.sessions import load_session_windows

    sessions_def = load_session_windows(sessions_cfg) if sessions_cfg else []
    current_time_ms = int(_time.time() * 1000)

    # ── Atomic snapshot: M15 1 bar + D1 N bars + session_states in one to_thread
    def _read_all() -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Any]]:
        from runtime.store.uds import ReadPolicy, WindowSpec

        # Cross-endpoint atomic price (ADR §3.1)
        m15_spec = WindowSpec(
            symbol=symbol,
            tf_s=900,
            limit=1,
        )
        m15_res = uds.read_window(m15_spec, ReadPolicy())
        m15_bars = list(getattr(m15_res, "bars_lwc", []) or [])

        # D1 lookback for previous_day + previous_week
        d1_spec = WindowSpec(
            symbol=symbol,
            tf_s=86400,
            limit=SMC_LEVELS_PREV_DAY_LOOKBACK + 1,
        )
        d1_res = uds.read_window(d1_spec, ReadPolicy())
        d1_bars = list(getattr(d1_res, "bars_lwc", []) or [])

        states = runner.get_session_states(symbol, current_time_ms)
        return m15_bars, d1_bars, states

    try:
        m15_bars, d1_bars, session_states = await asyncio.to_thread(_read_all)
    except Exception as exc:
        log.exception("smc_levels_read_failed symbol=%s err=%s", symbol, exc)
        return _error_response(
            "read_failed",
            "UDS/SMC snapshot read failed",
            status=503,
        )

    # ── current_price from M15 (I3: complete-only)
    m15_complete = [b for b in m15_bars if bool(b.get("complete"))]
    if not m15_complete:
        return _error_response(
            "price_unavailable",
            "M15 has no complete bars yet (warmup)",
            status=503,
        )
    current_price = float(m15_complete[-1].get("close") or 0.0)

    # ── previous_day / previous_week from D1
    d1_complete = [b for b in d1_bars if bool(b.get("complete"))]
    previous_day = _build_previous_day(d1_complete)
    previous_week = _build_previous_week(d1_complete)

    # ── sessions: map internal name → external key, default null when missing
    sessions_wire: Dict[str, Dict[str, Any]] = {
        ext_key: {
            "high": None,
            "low": None,
            "complete": False,
            "swept_high": False,
            "swept_low": False,
        }
        for ext_key in _SMC_LEVELS_SESSION_NAME_MAP.values()
    }
    for st in session_states or []:
        ext_key = _SMC_LEVELS_SESSION_NAME_MAP.get(getattr(st, "name", ""))
        if ext_key is None:
            continue
        sessions_wire[ext_key] = _smc_levels_session_to_wire(
            st, current_time_ms, sessions_def
        )

    data = {
        "symbol": symbol,
        "current_price": current_price,
        "previous_day": previous_day,
        "previous_week": previous_week,
        "sessions": sessions_wire,
    }
    body = _envelope_data_v31("smc_levels", data)
    raw_bytes, cap_err = _enforce_payload_cap(
        body,
        cap_bytes=SMC_LEVELS_PAYLOAD_HARD_CAP_BYTES,
        error_code="payload_too_large",
        log_label="smc_levels",
        log_ctx={"symbol": symbol},
    )
    if cap_err is not None:
        return cap_err
    assert raw_bytes is not None
    return web.Response(
        body=raw_bytes,
        status=200,
        content_type="application/json",
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
# Audit log (F-S3-002 — append-only JSONL with hashed IP, 90d retention)
# ────────────────────────────────────────────────────────────


def _audit_salt_for(date_str: str) -> str:
    """Return the per-day salt used when hashing client IPs.

    Cached per-process so the cost is amortised. Daily rotation prevents
    cross-day correlation of the same IP while keeping intra-day grouping
    intact for rate-limit / abuse forensics.
    """
    cached = _AUDIT_SALT_CACHE.get(date_str)
    if cached is not None:
        return cached
    seed = os.environ.get("API_V3_AUDIT_SALT", "v3-audit-default-salt")
    salt = hashlib.sha256(f"{seed}:{date_str}".encode("utf-8")).hexdigest()[:32]
    _AUDIT_SALT_CACHE[date_str] = salt
    return salt


def _ip_hash(ip: str, date_str: str) -> str:
    """SHA-256(salt || ip), truncated to 16 hex chars (~64-bit collision space)."""
    salt = _audit_salt_for(date_str)
    return hashlib.sha256(f"{salt}:{ip}".encode("utf-8")).hexdigest()[:16]


def _client_ip(request: web.Request) -> str:
    """Resolve consumer IP through the nginx → loopback hop.

    nginx sets `X-Forwarded-For` (CF-edge IP, possibly comma-list); we take
    the first hop which is what Cloudflare exposes. Falls back to the raw
    socket peer when the header is missing (e.g. local pytest client).
    """
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.remote or "unknown"


def _audit_path(base_dir: str, date_str: str) -> str:
    return os.path.join(base_dir, f"{AUDIT_FILE_PREFIX}{date_str}{AUDIT_FILE_SUFFIX}")


def _audit_write(base_dir: str, record: Dict[str, Any]) -> None:
    """Append one JSON record. Fail-soft (logs only) so audit never breaks API."""
    date_str = record["ts"][:10]  # "YYYY-MM-DDT…" → "YYYY-MM-DD"
    path = _audit_path(base_dir, date_str)
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
    try:
        os.makedirs(base_dir, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:
        log.warning("api_v3_audit_write_err path=%s err=%s", path, exc)


def _cleanup_old_audit_files(
    base_dir: str, keep_days: int = AUDIT_RETENTION_DAYS
) -> int:
    """Delete audit files older than `keep_days`. Returns count purged."""
    if not os.path.isdir(base_dir):
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).strftime(
        "%Y-%m-%d"
    )
    purged = 0
    for name in os.listdir(base_dir):
        if not (
            name.startswith(AUDIT_FILE_PREFIX) and name.endswith(AUDIT_FILE_SUFFIX)
        ):
            continue
        date_part = name[len(AUDIT_FILE_PREFIX) : -len(AUDIT_FILE_SUFFIX)]
        if date_part < cutoff:
            try:
                os.remove(os.path.join(base_dir, name))
                purged += 1
            except OSError as exc:
                log.warning("api_v3_audit_purge_err name=%s err=%s", name, exc)
    if purged:
        log.info("api_v3_audit_purged count=%d cutoff=%s", purged, cutoff)
    return purged


@web.middleware
async def audit_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """Append one JSONL line per `/api/v3/*` request after the handler runs.

    Captured fields: ts, consumer (resolved by handler before us), ip_hash,
    method, path, query (sanitized), status, latency_ms. The X-API-Key value
    itself is NEVER persisted — only the consumer name from the lookup.
    """
    if not request.path.startswith("/api/v3/"):
        return await handler(request)
    base_dir: Optional[str] = request.app.get(APP_AUDIT_DIR)
    t0 = time.monotonic()
    response = await handler(request)
    if not base_dir:
        return response
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    record = {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "consumer": request.get("api_consumer") or "anonymous",
        "ip_hash": _ip_hash(_client_ip(request), date_str),
        "method": request.method,
        "path": request.path,
        "query": dict(request.query),
        "status": response.status,
        "latency_ms": int((time.monotonic() - t0) * 1000),
    }
    _audit_write(base_dir, record)
    return response


# ────────────────────────────────────────────────────────────
# Wiring helpers (called from ws_server.create_app)
# ────────────────────────────────────────────────────────────


# Late import keeps endpoints.py importable in tests that don't pull ws_server.
def _resolve_smc_runner(request: web.Request) -> Any:
    from runtime.ws.app_keys import APP_SMC_RUNNER  # SSOT, avoids __main__ drift

    runner = request.app.get(APP_SMC_RUNNER)
    if runner is None:
        return _error_response(
            "smc_runner_unavailable",
            "SMC runner not initialised — server is still warming up",
            status=503,
        )
    return runner


def _default_symbol(request: web.Request) -> str:
    from runtime.ws.app_keys import APP_FULL_CONFIG  # SSOT, avoids __main__ drift

    cfg = request.app.get(APP_FULL_CONFIG) or {}
    symbols = cfg.get("symbols") or []
    return symbols[0] if symbols else "XAU/USD"


def register_routes(
    app: web.Application,
    *,
    token_store: TokenStore,
    signals_dir: str = "data_v3/_signals",
    audit_dir: Optional[str] = "data_v3/_audit",
) -> None:
    """Mount the five `/api/v3/*` endpoints on `app`.

    Idempotent at module level: a second call would shadow the AppKeys, so the
    caller (ws_server.create_app) MUST only invoke it once during startup.

    `audit_dir` controls F-S3-002 audit JSONL output; pass `None` to disable
    (e.g. for unit tests that don't care about the audit trail).
    """
    app[APP_TOKEN_STORE] = token_store
    app[APP_SIGNALS_DIR] = signals_dir
    if audit_dir:
        app[APP_AUDIT_DIR] = audit_dir
        # Best-effort retention sweep at startup. Failures are logged inside.
        try:
            _cleanup_old_audit_files(audit_dir)
        except OSError as exc:
            log.warning("api_v3_audit_cleanup_err dir=%s err=%s", audit_dir, exc)
        # Middleware fires only for /api/v3/* — see audit_middleware body.
        app.middlewares.append(audit_middleware)
    app.router.add_get("/api/v3/signals/latest", _handle_signals_latest)
    app.router.add_get("/api/v3/signals/journal", _handle_signals_journal)
    app.router.add_get("/api/v3/bias/latest", _handle_bias_latest)
    app.router.add_get("/api/v3/narrative/snapshot", _handle_narrative_snapshot)
    app.router.add_get("/api/v3/macro/context", _handle_macro_context)
    # ADR-0059 §3.1.1 — analysis endpoint (slice 059.1).
    app.router.add_get("/api/v3/bars/window", _handle_bars_window)
    # ADR-0059 §3.1.2 — paginated zones endpoint (slice 059.2).
    app.router.add_get("/api/v3/smc/zones", _handle_smc_zones)
    app.router.add_get("/api/v3/smc/levels", _handle_smc_levels)
    # Catch-all so typos under /api/v3/ return a structured 404 envelope
    # (F-S2-004) instead of aiohttp's plain text default.
    app.router.add_route("*", "/api/v3/{tail:.*}", _handle_v3_not_found)
    log.info(
        "api_v3_routes_registered signals_dir=%s audit_dir=%s endpoints=8",
        signals_dir,
        audit_dir or "DISABLED",
    )
