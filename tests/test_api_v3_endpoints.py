"""Tests for `runtime/api_v3/endpoints.py` (ADR-0058 slice 058.2).

Covers:
* Envelope shape (schema_version + kind + server_ts + data/items)
* Auth flow: missing key (401), invalid key (401), redis error (503), valid (200)
* Query validation: limit cap (400), date in past >90d (400), invalid date (400)
* Endpoint routing for all 5 endpoints + structured 404 catch-all
* Journal source filtering (tda_cascade vs smc_narrative)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from aiohttp import web
from redis.exceptions import RedisError

from runtime.api_v3 import endpoints as ep

pytestmark = pytest.mark.asyncio


# ────────────────────────────────────────────────────────────
# Stubs
# ────────────────────────────────────────────────────────────


class _StubTokenStore:
    """Drop-in replacement for runtime.api_v3.token_store.TokenStore."""

    def __init__(self, valid: Dict[str, ep.TokenStore] | None = None) -> None:  # type: ignore[name-defined]
        self._valid = valid or {}
        self.raise_on_lookup: Optional[Exception] = None

    def lookup(self, token: Optional[str]):  # type: ignore[override]
        if self.raise_on_lookup is not None:
            raise self.raise_on_lookup
        return self._valid.get(token or "")


class _StubRecord:
    def __init__(self, consumer: str = "old_news_bot", scope: str = "read") -> None:
        self.consumer = consumer
        self.scope = scope
        self.created = "2026-04-01T00:00:00Z"
        self.expires = "2026-07-01T00:00:00Z"


class _StubSmcRunner:
    """Implements only the methods touched by api_v3 endpoints."""

    def __init__(self) -> None:
        self.last_price = 4500.0
        self.bias = {"900": "bullish", "3600": "bearish"}
        self._block: Optional[Any] = None

    def get_last_price(self, symbol: str) -> float:
        return self.last_price

    def get_bias_map(self, symbol: str) -> Dict[str, str]:
        return dict(self.bias)

    def get_narrative(self, symbol: str, tf_s: int, price: float, atr: float):
        return self._block


class _StubNarrativeBlock:
    def to_wire(self) -> Dict[str, Any]:
        return {
            "mode": "wait",
            "headline": "Test headline",
            "market_phase": "ranging",
        }


# ────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────

VALID_TOKEN = "tk_" + ("a" * 64)


@pytest.fixture
def signals_dir(tmp_path: Path) -> Path:
    d = tmp_path / "_signals"
    d.mkdir()
    return d


@pytest.fixture
def token_store() -> _StubTokenStore:
    return _StubTokenStore({VALID_TOKEN: _StubRecord()})


@pytest.fixture
def smc_runner() -> _StubSmcRunner:
    return _StubSmcRunner()


def _build_app(
    *,
    token_store: _StubTokenStore,
    signals_dir: Path,
    smc_runner: _StubSmcRunner,
    config_symbols: Optional[List[str]] = None,
) -> web.Application:
    """Build a minimal aiohttp app with api_v3 routes mounted."""
    from runtime.ws.ws_server import APP_FULL_CONFIG, APP_SMC_RUNNER

    app = web.Application()
    app[APP_SMC_RUNNER] = smc_runner  # type: ignore[assignment]
    app[APP_FULL_CONFIG] = {"symbols": config_symbols or ["XAU/USD", "BTCUSDT"]}
    ep.register_routes(
        app,
        token_store=token_store,  # type: ignore[arg-type]
        signals_dir=str(signals_dir),
    )
    return app


@pytest.fixture
def app(token_store, signals_dir, smc_runner):
    return _build_app(
        token_store=token_store,
        signals_dir=signals_dir,
        smc_runner=smc_runner,
    )


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _write_journal(
    signals_dir: Path, date_str: str, records: List[Dict[str, Any]]
) -> None:
    path = signals_dir / f"journal-{date_str}.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _auth() -> Dict[str, str]:
    return {"X-API-Key": VALID_TOKEN}


# ────────────────────────────────────────────────────────────
# Envelope shape
# ────────────────────────────────────────────────────────────


def _assert_envelope(body: Dict[str, Any], expected_kind: str) -> None:
    assert body["schema_version"] == "v3.0", body
    assert body["kind"] == expected_kind, body
    assert "server_ts" in body and body["server_ts"].endswith("Z")
    # F-S2-003 (slice 058.5): disclaimer must be present in every envelope
    # so consumers cannot strip it without violating the contract.
    assert "disclaimer" in body, body
    assert "not financial advice" in body["disclaimer"].lower()


# ────────────────────────────────────────────────────────────
# Auth tests
# ────────────────────────────────────────────────────────────


async def test_missing_api_key_returns_401(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/macro/context")
    assert resp.status == 401
    body = await resp.json()
    _assert_envelope(body, "error")
    assert body["data"]["code"] == "missing_api_key"


async def test_invalid_api_key_returns_401(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get(
        "/api/v3/macro/context", headers={"X-API-Key": "tk_" + ("0" * 64)}
    )
    assert resp.status == 401
    body = await resp.json()
    assert body["data"]["code"] == "invalid_api_key"


async def test_redis_error_returns_503(
    token_store, signals_dir, smc_runner, aiohttp_client
):
    token_store.raise_on_lookup = RedisError("boom")
    app = _build_app(
        token_store=token_store, signals_dir=signals_dir, smc_runner=smc_runner
    )
    cl = await aiohttp_client(app)
    resp = await cl.get("/api/v3/macro/context", headers=_auth())
    assert resp.status == 503
    body = await resp.json()
    assert body["data"]["code"] == "auth_backend_unavailable"


# ────────────────────────────────────────────────────────────
# /api/v3/signals/latest
# ────────────────────────────────────────────────────────────


async def test_signals_latest_returns_envelope(aiohttp_client, app, signals_dir):
    client = await aiohttp_client(app)
    today = _today_str()
    _write_journal(
        signals_dir,
        today,
        [
            {
                "ts": "2026-04-26T10:00:00Z",
                "wall_ms": 1,
                "source": "tda_cascade",
                "event": "signal_emitted",
                "symbol": "XAU/USD",
                "signal_id": "s1",
                "direction": "long",
            },
            {
                "ts": "2026-04-26T11:00:00Z",
                "wall_ms": 2,
                "symbol": "XAU/USD",
                "tf_s": 900,
                "event": "narrative_emit",
                "mode": "trade",
                "headline": "London sweep",
            },
        ],
    )
    resp = await client.get(
        "/api/v3/signals/latest?limit=10&source=all", headers=_auth()
    )
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    _assert_envelope(body, "signal_list")
    assert body["total"] == 2
    kinds = {item["kind"] for item in body["items"]}
    assert kinds == {"tda_cascade", "smc_narrative"}


async def test_signals_latest_filter_by_source(aiohttp_client, app, signals_dir):
    client = await aiohttp_client(app)
    today = _today_str()
    _write_journal(
        signals_dir,
        today,
        [
            {"source": "tda_cascade", "symbol": "XAU/USD", "ts": "x", "wall_ms": 1},
            {
                "symbol": "BTCUSDT",
                "tf_s": 900,
                "event": "narrative_emit",
                "ts": "y",
                "wall_ms": 2,
            },
        ],
    )
    resp = await client.get(
        "/api/v3/signals/latest?source=tda_cascade", headers=_auth()
    )
    body = await resp.json()
    assert body["total"] == 1
    assert body["items"][0]["kind"] == "tda_cascade"


async def test_signals_latest_limit_cap(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/signals/latest?limit=999", headers=_auth())
    assert resp.status == 400
    body = await resp.json()
    assert body["data"]["code"] == "limit_exceeded"
    assert body["data"]["max"] == 100


async def test_signals_latest_invalid_limit(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/signals/latest?limit=abc", headers=_auth())
    assert resp.status == 400
    assert (await resp.json())["data"]["code"] == "limit_invalid"


async def test_signals_latest_invalid_source(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/signals/latest?source=hacker", headers=_auth())
    assert resp.status == 400
    assert (await resp.json())["data"]["code"] == "source_invalid"


# ────────────────────────────────────────────────────────────
# /api/v3/signals/journal
# ────────────────────────────────────────────────────────────


async def test_signals_journal_requires_date(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/signals/journal", headers=_auth())
    assert resp.status == 400
    assert (await resp.json())["data"]["code"] == "missing_date"


async def test_signals_journal_invalid_date_format(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/signals/journal?date=2026/04/26", headers=_auth())
    assert resp.status == 400
    assert (await resp.json())["data"]["code"] == "date_invalid"


async def test_signals_journal_too_old(aiohttp_client, app):
    client = await aiohttp_client(app)
    old = (datetime.now(timezone.utc) - timedelta(days=200)).strftime("%Y-%m-%d")
    resp = await client.get(f"/api/v3/signals/journal?date={old}", headers=_auth())
    assert resp.status == 400
    body = await resp.json()
    assert body["data"]["code"] == "date_too_old"
    assert body["data"]["max_back_days"] == 90


async def test_signals_journal_in_future(aiohttp_client, app):
    client = await aiohttp_client(app)
    fut = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")
    resp = await client.get(f"/api/v3/signals/journal?date={fut}", headers=_auth())
    assert resp.status == 400
    assert (await resp.json())["data"]["code"] == "date_in_future"


async def test_signals_journal_filter_by_symbol(aiohttp_client, app, signals_dir):
    client = await aiohttp_client(app)
    today = _today_str()
    _write_journal(
        signals_dir,
        today,
        [
            {"source": "tda_cascade", "symbol": "XAU/USD", "ts": "x", "wall_ms": 1},
            {"source": "tda_cascade", "symbol": "BTCUSDT", "ts": "y", "wall_ms": 2},
        ],
    )
    resp = await client.get(
        f"/api/v3/signals/journal?date={today}&symbol=XAU/USD", headers=_auth()
    )
    assert resp.status == 200
    body = await resp.json()
    _assert_envelope(body, "journal")
    assert body["total"] == 1
    assert body["items"][0]["data"]["symbol"] == "XAU/USD"


async def test_signals_journal_missing_file_returns_empty(aiohttp_client, app):
    client = await aiohttp_client(app)
    today = _today_str()
    resp = await client.get(f"/api/v3/signals/journal?date={today}", headers=_auth())
    assert resp.status == 200
    body = await resp.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_signals_journal_skips_malformed_lines(aiohttp_client, app, signals_dir):
    client = await aiohttp_client(app)
    today = _today_str()
    path = signals_dir / f"journal-{today}.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"source": "tda_cascade", "symbol": "XAU/USD", "ts": "ok"}\n')
        fh.write("not-json garbage line\n")
        fh.write('{"source": "tda_cascade", "symbol": "BTCUSDT", "ts": "ok"}\n')
    resp = await client.get(f"/api/v3/signals/journal?date={today}", headers=_auth())
    assert resp.status == 200
    body = await resp.json()
    assert body["total"] == 2  # malformed line skipped, others kept


# ────────────────────────────────────────────────────────────
# /api/v3/bias/latest
# ────────────────────────────────────────────────────────────


async def test_bias_latest_uses_default_symbol(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/bias/latest", headers=_auth())
    assert resp.status == 200
    body = await resp.json()
    _assert_envelope(body, "bias_map")
    assert body["data"]["symbol"] == "XAU/USD"
    assert body["data"]["bias"] == {"900": "bullish", "3600": "bearish"}


async def test_bias_latest_explicit_symbol(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/bias/latest?symbol=BTCUSDT", headers=_auth())
    assert resp.status == 200
    body = await resp.json()
    assert body["data"]["symbol"] == "BTCUSDT"


# ────────────────────────────────────────────────────────────
# /api/v3/narrative/snapshot
# ────────────────────────────────────────────────────────────


async def test_narrative_snapshot_success(aiohttp_client, app, smc_runner):
    client = await aiohttp_client(app)
    smc_runner._block = _StubNarrativeBlock()
    resp = await client.get(
        "/api/v3/narrative/snapshot?symbol=XAU/USD&tf=900", headers=_auth()
    )
    assert resp.status == 200
    body = await resp.json()
    _assert_envelope(body, "narrative_block")
    assert body["data"]["tf_s"] == 900
    assert body["data"]["block"]["headline"] == "Test headline"


async def test_narrative_snapshot_invalid_tf(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/narrative/snapshot?tf=foo", headers=_auth())
    assert resp.status == 400
    assert (await resp.json())["data"]["code"] == "tf_invalid"


async def test_narrative_snapshot_no_price_503(aiohttp_client, app, smc_runner):
    client = await aiohttp_client(app)
    smc_runner.last_price = 0.0
    resp = await client.get("/api/v3/narrative/snapshot", headers=_auth())
    assert resp.status == 503
    assert (await resp.json())["data"]["code"] == "price_unavailable"


async def test_narrative_snapshot_no_block_503(aiohttp_client, app, smc_runner):
    client = await aiohttp_client(app)
    smc_runner._block = None
    resp = await client.get("/api/v3/narrative/snapshot", headers=_auth())
    assert resp.status == 503
    assert (await resp.json())["data"]["code"] == "narrative_unavailable"


# ────────────────────────────────────────────────────────────
# /api/v3/macro/context
# ────────────────────────────────────────────────────────────


async def test_macro_context_returns_state(aiohttp_client, app, signals_dir):
    client = await aiohttp_client(app)
    state = {"XAU/USD": {"signal_id": "s1", "direction": "long"}}
    with open(signals_dir / "tda_state.json", "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    resp = await client.get("/api/v3/macro/context", headers=_auth())
    assert resp.status == 200
    body = await resp.json()
    _assert_envelope(body, "tda_state")
    assert body["data"]["symbols"] == state


async def test_macro_context_missing_file_returns_empty(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/macro/context", headers=_auth())
    assert resp.status == 200
    body = await resp.json()
    assert body["data"]["symbols"] == {}


# ────────────────────────────────────────────────────────────
# Catch-all 404
# ────────────────────────────────────────────────────────────


async def test_unknown_v3_path_returns_structured_404(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/does/not/exist", headers=_auth())
    assert resp.status == 404
    body = await resp.json()
    _assert_envelope(body, "error")
    assert body["data"]["code"] == "not_found"
    assert body["data"]["path"] == "/api/v3/does/not/exist"
