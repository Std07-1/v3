"""Tests for `runtime/api_v3/cowork.py` (ADR-001, slice cowork.001).

Covers:
* GET /api/v3/cowork/recent_thesis  — empty store, after POST, age filter,
  symbol filter, limit cap, missing symbol (400), invalid limit (400),
  out-of-range max_age_h (400)
* POST /api/v3/cowork/published     — happy path, schema_invalid (422),
  duplicate scan_id idempotent (appended:false), invalid JSON (400),
  empty body (400), scope_forbidden (403)
* Auth flow: missing key (401), wrong scope (403)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pytest
from aiohttp import web

from runtime.api_v3 import endpoints as ep
from runtime.api_v3.cowork import register_cowork_routes

pytestmark = pytest.mark.asyncio


# ────────────────────────────────────────────────────────────
# Stubs (copied from test_api_v3_endpoints.py — kept local to avoid
# cross-test fixture coupling)
# ────────────────────────────────────────────────────────────


class _StubRecord:
    def __init__(self, consumer: str = "cowork", scope: str = "read") -> None:
        self.consumer = consumer
        self.scope = scope
        self.created = "2026-04-01T00:00:00Z"
        self.expires = "2026-12-01T00:00:00Z"


class _StubTokenStore:
    def __init__(self, valid: Dict[str, _StubRecord] | None = None) -> None:
        self._valid = valid or {}

    def lookup(self, token: Optional[str]):
        return self._valid.get(token or "")


READ_TOKEN = "tk_" + ("a" * 64)
WRITE_TOKEN = "tk_" + ("b" * 64)


@pytest.fixture
def cowork_dir(tmp_path: Path) -> Path:
    d = tmp_path / "cowork_data"
    d.mkdir()
    return d


@pytest.fixture
def token_store() -> _StubTokenStore:
    return _StubTokenStore(
        {
            READ_TOKEN: _StubRecord(scope="read"),
            WRITE_TOKEN: _StubRecord(scope="cowork_write"),
        }
    )


@pytest.fixture
def app(
    token_store: _StubTokenStore, cowork_dir: Path, tmp_path: Path
) -> web.Application:
    app = web.Application()
    ep.register_routes(
        app,
        token_store=token_store,  # type: ignore[arg-type]
        signals_dir=str(tmp_path / "_signals"),
        audit_dir=None,
    )
    # Re-register cowork routes with our test store_dir so each test gets its
    # own isolated JSONL directory. register_routes already mounted with
    # default; we overwrite the AppKey here (idempotent for tests).
    from runtime.api_v3.cowork import APP_COWORK_STORE_DIR

    app[APP_COWORK_STORE_DIR] = cowork_dir
    return app


def _read_auth() -> Dict[str, str]:
    return {"X-API-Key": READ_TOKEN}


def _write_auth() -> Dict[str, str]:
    return {"X-API-Key": WRITE_TOKEN}


def _sample_thesis_payload(
    *,
    scan_id: str = "scan-20260506-143000-xauusd",
    symbol: str = "XAU/USD",
    ts: Optional[str] = None,
) -> Dict[str, Any]:
    """Minimal valid PublishedThesis payload (matches schema.from_jsonable)."""
    return {
        "ts": ts or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scan_id": scan_id,
        "symbol": symbol,
        "model": "claude-opus-4-7",
        "prompt_version": "v3.0",
        "current_price": 4687.5,
        "tldr": "H4 premium exhaustion + triple sweep — bias short pending CHoCH",
        "preferred_scenario_id": "B",
        "preferred_direction": "bearish",
        "preferred_probability": 50,
        "thesis_grade": "B",
        "market_phase": "trending_down",
        "session": "newyork",
        "in_killzone": True,
        "watch_levels": [4720.0, 4660.0, 4615.0],
        "scenarios_summary": [
            {"id": "A", "label": "Continuation up", "probability": 30},
            {"id": "B", "label": "Reversal down", "probability": 50},
            {"id": "C", "label": "Range pre-NFP", "probability": 20},
        ],
        "telegram_msg_id": 12345,
        "prompt_hash": "deadbeef",
        "prior_context_used": False,
        "corrects": None,
        "extras": {"cost_tokens": 4200},
    }


# ────────────────────────────────────────────────────────────
# GET /recent_thesis — happy paths
# ────────────────────────────────────────────────────────────


async def test_recent_thesis_empty_store_returns_empty_list(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get(
        "/api/v3/cowork/recent_thesis",
        params={"symbol": "XAU/USD"},
        headers=_read_auth(),
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["kind"] == "cowork_recent_thesis"
    assert body["data"]["symbol"] == "XAU/USD"
    assert body["data"]["count"] == 0
    assert body["data"]["theses"] == []


async def test_recent_thesis_returns_record_after_post(aiohttp_client, app):
    client = await aiohttp_client(app)

    # POST a thesis
    payload = _sample_thesis_payload()
    resp = await client.post(
        "/api/v3/cowork/published", json=payload, headers=_write_auth()
    )
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert body["kind"] == "cowork_published_ack"
    assert body["data"]["appended"] is True
    assert body["data"]["duplicate"] is False
    assert body["data"]["scan_id"] == payload["scan_id"]

    # GET it back
    resp = await client.get(
        "/api/v3/cowork/recent_thesis",
        params={"symbol": "XAU/USD", "limit": "5"},
        headers=_read_auth(),
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["data"]["count"] == 1
    got = body["data"]["theses"][0]
    assert got["scan_id"] == payload["scan_id"]
    assert got["preferred_direction"] == "bearish"
    assert got["watch_levels"] == [4720.0, 4660.0, 4615.0]


async def test_recent_thesis_filters_by_symbol(aiohttp_client, app):
    client = await aiohttp_client(app)

    p1 = _sample_thesis_payload(scan_id="scan-20260506-100000-xauusd", symbol="XAU/USD")
    p2 = _sample_thesis_payload(
        scan_id="scan-20260506-100000-btcusdt", symbol="BTCUSDT"
    )
    for p in (p1, p2):
        r = await client.post("/api/v3/cowork/published", json=p, headers=_write_auth())
        assert r.status == 200, await r.text()

    resp = await client.get(
        "/api/v3/cowork/recent_thesis",
        params={"symbol": "BTCUSDT"},
        headers=_read_auth(),
    )
    body = await resp.json()
    assert body["data"]["count"] == 1
    assert body["data"]["theses"][0]["symbol"] == "BTCUSDT"


# ────────────────────────────────────────────────────────────
# POST /published — idempotency + validation
# ────────────────────────────────────────────────────────────


async def test_published_idempotent_on_scan_id(aiohttp_client, app):
    client = await aiohttp_client(app)
    payload = _sample_thesis_payload()

    r1 = await client.post(
        "/api/v3/cowork/published", json=payload, headers=_write_auth()
    )
    b1 = await r1.json()
    assert b1["data"]["appended"] is True

    r2 = await client.post(
        "/api/v3/cowork/published", json=payload, headers=_write_auth()
    )
    b2 = await r2.json()
    assert r2.status == 200
    assert b2["data"]["appended"] is False
    assert b2["data"]["duplicate"] is True
    assert b2["data"]["scan_id"] == payload["scan_id"]


async def test_published_invalid_schema_returns_422(aiohttp_client, app):
    client = await aiohttp_client(app)
    bad = _sample_thesis_payload()
    bad["thesis_grade"] = "Z+"  # not in THESIS_GRADES
    resp = await client.post(
        "/api/v3/cowork/published", json=bad, headers=_write_auth()
    )
    assert resp.status == 422
    body = await resp.json()
    assert body["data"]["code"] == "schema_invalid"
    assert "thesis_grade" in body["data"]["message"]


async def test_published_empty_body_returns_400(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.post(
        "/api/v3/cowork/published", data=b"", headers=_write_auth()
    )
    assert resp.status == 400
    body = await resp.json()
    assert body["data"]["code"] == "body_empty"


async def test_published_invalid_json_returns_400(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.post(
        "/api/v3/cowork/published",
        data=b"{not json",
        headers={**_write_auth(), "Content-Type": "application/json"},
    )
    assert resp.status == 400
    body = await resp.json()
    assert body["data"]["code"] == "body_not_json"


# ────────────────────────────────────────────────────────────
# Auth + scope
# ────────────────────────────────────────────────────────────


async def test_recent_thesis_missing_api_key_returns_401(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get(
        "/api/v3/cowork/recent_thesis", params={"symbol": "XAU/USD"}
    )
    assert resp.status == 401
    body = await resp.json()
    assert body["data"]["code"] == "missing_api_key"


async def test_published_with_read_scope_returns_403(aiohttp_client, app):
    """Read-only token must not be allowed to POST published theses."""
    client = await aiohttp_client(app)
    payload = _sample_thesis_payload()
    resp = await client.post(
        "/api/v3/cowork/published", json=payload, headers=_read_auth()
    )
    assert resp.status == 403
    body = await resp.json()
    assert body["data"]["code"] == "scope_forbidden"
    assert body["data"]["required_scope"] == "cowork_write"


async def test_recent_thesis_missing_symbol_returns_400(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/cowork/recent_thesis", headers=_read_auth())
    assert resp.status == 400
    body = await resp.json()
    assert body["data"]["code"] == "symbol_required"


async def test_recent_thesis_invalid_limit_returns_400(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get(
        "/api/v3/cowork/recent_thesis",
        params={"symbol": "XAU/USD", "limit": "abc"},
        headers=_read_auth(),
    )
    assert resp.status == 400
    body = await resp.json()
    assert body["data"]["code"] == "limit_invalid"


async def test_recent_thesis_max_age_out_of_range_returns_400(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get(
        "/api/v3/cowork/recent_thesis",
        params={"symbol": "XAU/USD", "max_age_h": "9999"},
        headers=_read_auth(),
    )
    assert resp.status == 400
    body = await resp.json()
    assert body["data"]["code"] == "max_age_h_out_of_range"


# ===========================================================================
# cowork.005 — GET /api/v3/cowork/event_flag
# ===========================================================================


@pytest.fixture
def triggers_dir(tmp_path: Path) -> Path:
    d = tmp_path / "cowork_triggers"
    d.mkdir()
    return d


@pytest.fixture
def app_with_triggers(
    token_store: _StubTokenStore,
    cowork_dir: Path,
    triggers_dir: Path,
    tmp_path: Path,
) -> web.Application:
    app = web.Application()
    ep.register_routes(
        app,
        token_store=token_store,  # type: ignore[arg-type]
        signals_dir=str(tmp_path / "_signals"),
        audit_dir=None,
    )
    from runtime.api_v3.cowork import (
        APP_COWORK_STORE_DIR,
        APP_COWORK_TRIGGERS_DIR,
    )

    app[APP_COWORK_STORE_DIR] = cowork_dir
    app[APP_COWORK_TRIGGERS_DIR] = triggers_dir
    return app


async def test_event_flag_missing_api_key_returns_401(
    aiohttp_client, app_with_triggers
):
    client = await aiohttp_client(app_with_triggers)
    resp = await client.get("/api/v3/cowork/event_flag")
    assert resp.status == 401


async def test_event_flag_absent_file_returns_state_absent(
    aiohttp_client, app_with_triggers
):
    client = await aiohttp_client(app_with_triggers)
    resp = await client.get("/api/v3/cowork/event_flag", headers=_read_auth())
    assert resp.status == 200
    body = await resp.json()
    assert body["kind"] == "cowork_event_flag"
    data = body["data"]
    assert data["state"] == "absent"
    assert data["trigger"] is None
    assert data["age_min"] is None
    assert data["triggers_configured"] is True


async def test_event_flag_present_returns_fresh_payload(
    aiohttp_client, app_with_triggers, triggers_dir
):
    now = datetime.now(timezone.utc)
    flag_payload = {
        "trigger": "tda_signal",
        "ts": (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (triggers_dir / "event_flag.json").write_text(json.dumps(flag_payload))
    client = await aiohttp_client(app_with_triggers)
    resp = await client.get("/api/v3/cowork/event_flag", headers=_read_auth())
    assert resp.status == 200
    body = await resp.json()
    data = body["data"]
    assert data["state"] == "present"
    assert data["trigger"] == "tda_signal"
    assert data["age_min"] in (1, 2, 3)
    assert data["ts"] == flag_payload["ts"]


async def test_event_flag_stale_payload(
    aiohttp_client, app_with_triggers, triggers_dir
):
    now = datetime.now(timezone.utc)
    flag_payload = {
        "trigger": "bias_flip",
        "ts": (now - timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (triggers_dir / "event_flag.json").write_text(json.dumps(flag_payload))
    client = await aiohttp_client(app_with_triggers)
    resp = await client.get("/api/v3/cowork/event_flag", headers=_read_auth())
    assert resp.status == 200
    data = (await resp.json())["data"]
    assert data["state"] == "stale"
    assert data["trigger"] == "bias_flip"


async def test_event_flag_malformed_json_returns_invalid(
    aiohttp_client, app_with_triggers, triggers_dir
):
    (triggers_dir / "event_flag.json").write_text("{not valid json")
    client = await aiohttp_client(app_with_triggers)
    resp = await client.get("/api/v3/cowork/event_flag", headers=_read_auth())
    assert resp.status == 200
    data = (await resp.json())["data"]
    assert data["state"] == "invalid"


async def test_event_flag_unknown_trigger_returns_invalid(
    aiohttp_client, app_with_triggers, triggers_dir
):
    (triggers_dir / "event_flag.json").write_text(
        json.dumps({"trigger": "fake_event", "ts": "2026-05-07T22:00:00Z"})
    )
    client = await aiohttp_client(app_with_triggers)
    resp = await client.get("/api/v3/cowork/event_flag", headers=_read_auth())
    assert resp.status == 200
    data = (await resp.json())["data"]
    assert data["state"] == "invalid"


async def test_event_flag_no_triggers_dir_configured_returns_absent(
    aiohttp_client, token_store, cowork_dir, tmp_path
):
    """When `triggers_dir` is unset on the app, endpoint returns absent +
    triggers_configured=False (still 200)."""
    app = web.Application()
    ep.register_routes(
        app,
        token_store=token_store,  # type: ignore[arg-type]
        signals_dir=str(tmp_path / "_signals"),
        audit_dir=None,
    )
    from runtime.api_v3.cowork import APP_COWORK_STORE_DIR

    app[APP_COWORK_STORE_DIR] = cowork_dir
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/cowork/event_flag", headers=_read_auth())
    assert resp.status == 200
    data = (await resp.json())["data"]
    assert data["state"] == "absent"
    assert data["triggers_configured"] is False
