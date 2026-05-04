"""Tests for `runtime/api_v3/kill_switch.py` (ADR-0059 slice 059.4).

Covers:
* Config layer: `analysis_enabled=False` blocks /bars/* and /smc/* with 503
  `analysis_disabled_config`, regardless of Redis flag state.
* Runtime layer: Redis flag present → 503 `analysis_disabled_runtime`.
* Pass-through: signals/* / bias/* / narrative/* / macro/* are NEVER blocked.
* Fail-open (F-S1-002): Redis outage on EXISTS check → request passes
  through with warning log + counter increment.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from redis.exceptions import RedisError

from runtime.api_v3.kill_switch import (
    APP_KILL_SWITCH,
    KillSwitch,
    kill_flag_redis_key,
    register_kill_switch,
)

pytestmark = pytest.mark.asyncio


NAMESPACE = "test_ns"
SENTINEL_OK = "passthrough_ok"


# ────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────


def _build_app(*, redis_client: Any, analysis_enabled: bool) -> web.Application:
    """Minimal aiohttp app with kill switch + dummy handlers covering both
    analysis paths and an unaffected signals path."""
    app = web.Application()
    register_kill_switch(
        app,
        redis_client=redis_client,
        namespace=NAMESPACE,
        analysis_enabled=analysis_enabled,
    )

    async def _bars_handler(_req: web.Request) -> web.Response:
        return web.json_response({"sentinel": SENTINEL_OK, "kind": "bars"})

    async def _smc_handler(_req: web.Request) -> web.Response:
        return web.json_response({"sentinel": SENTINEL_OK, "kind": "smc"})

    async def _signals_handler(_req: web.Request) -> web.Response:
        return web.json_response({"sentinel": SENTINEL_OK, "kind": "signals"})

    app.router.add_get("/api/v3/bars/window", _bars_handler)
    app.router.add_get("/api/v3/smc/zones", _smc_handler)
    app.router.add_get("/api/v3/signals/latest", _signals_handler)
    return app


@pytest.fixture
def fake_redis_empty() -> MagicMock:
    """Redis stub: kill flag absent (analysis serves)."""
    client = MagicMock()
    client.exists = MagicMock(return_value=0)
    return client


@pytest.fixture
def fake_redis_killed() -> MagicMock:
    """Redis stub: kill flag present (analysis blocked)."""
    client = MagicMock()
    client.exists = MagicMock(return_value=1)
    return client


@pytest.fixture
def fake_redis_outage() -> MagicMock:
    """Redis stub: EXISTS raises RedisError (fail-open path)."""
    client = MagicMock()
    client.exists = MagicMock(side_effect=RedisError("connection refused"))
    return client


# ────────────────────────────────────────────────────────────
# Layer 1 — config flag
# ────────────────────────────────────────────────────────────


async def test_config_disabled_blocks_bars(aiohttp_client, fake_redis_empty):
    """analysis_enabled=False → /bars/* gets 503 even with Redis flag absent."""
    app = _build_app(redis_client=fake_redis_empty, analysis_enabled=False)
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/bars/window")
    assert resp.status == 503
    body = await resp.json()
    assert body["kind"] == "error"
    assert body["data"]["code"] == "analysis_disabled_config"
    # Config layer short-circuits BEFORE Redis check.
    fake_redis_empty.exists.assert_not_called()


async def test_config_disabled_blocks_smc(aiohttp_client, fake_redis_empty):
    """Both analysis prefixes are governed by the same config flag."""
    app = _build_app(redis_client=fake_redis_empty, analysis_enabled=False)
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/smc/zones")
    assert resp.status == 503
    body = await resp.json()
    assert body["data"]["code"] == "analysis_disabled_config"


async def test_config_disabled_does_not_block_signals(aiohttp_client, fake_redis_empty):
    """Signals endpoint must remain available even when analysis is killed."""
    app = _build_app(redis_client=fake_redis_empty, analysis_enabled=False)
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/signals/latest")
    assert resp.status == 200
    body = await resp.json()
    assert body["sentinel"] == SENTINEL_OK
    assert body["kind"] == "signals"


# ────────────────────────────────────────────────────────────
# Layer 2 — Redis runtime flag
# ────────────────────────────────────────────────────────────


async def test_runtime_flag_blocks_bars(aiohttp_client, fake_redis_killed):
    """Redis flag present (config enabled) → 503 analysis_disabled_runtime."""
    app = _build_app(redis_client=fake_redis_killed, analysis_enabled=True)
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/bars/window")
    assert resp.status == 503
    body = await resp.json()
    assert body["data"]["code"] == "analysis_disabled_runtime"
    fake_redis_killed.exists.assert_called_once_with(kill_flag_redis_key(NAMESPACE))


async def test_runtime_flag_blocks_smc(aiohttp_client, fake_redis_killed):
    app = _build_app(redis_client=fake_redis_killed, analysis_enabled=True)
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/smc/zones")
    assert resp.status == 503
    body = await resp.json()
    assert body["data"]["code"] == "analysis_disabled_runtime"


async def test_runtime_flag_does_not_block_signals(aiohttp_client, fake_redis_killed):
    """Signals path bypasses the kill check entirely (no Redis call)."""
    app = _build_app(redis_client=fake_redis_killed, analysis_enabled=True)
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/signals/latest")
    assert resp.status == 200
    fake_redis_killed.exists.assert_not_called()


async def test_redis_empty_serves_analysis(aiohttp_client, fake_redis_empty):
    """Happy path: config enabled + Redis flag absent → analysis serves."""
    app = _build_app(redis_client=fake_redis_empty, analysis_enabled=True)
    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/bars/window")
    assert resp.status == 200
    body = await resp.json()
    assert body["sentinel"] == SENTINEL_OK


# ────────────────────────────────────────────────────────────
# F-S1-002 — fail-open behavior
# ────────────────────────────────────────────────────────────


async def test_redis_outage_fails_open(aiohttp_client, fake_redis_outage, caplog):
    """Redis EXISTS raises → analysis served (fail-open) + warning logged."""
    import logging

    caplog.set_level(logging.WARNING, logger="api_v3.kill_switch")
    app = _build_app(redis_client=fake_redis_outage, analysis_enabled=True)
    switch: KillSwitch = app[APP_KILL_SWITCH]
    assert switch.fail_open_count == 0

    client = await aiohttp_client(app)
    resp = await client.get("/api/v3/bars/window")
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert body["sentinel"] == SENTINEL_OK

    # Counter incremented + structured warning emitted.
    assert switch.fail_open_count == 1
    assert any(
        "api_v3_kill_switch_check_failed" in rec.getMessage() for rec in caplog.records
    ), [rec.getMessage() for rec in caplog.records]


# ────────────────────────────────────────────────────────────
# Redis key SSOT
# ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ns,expected",
    [
        ("v3_local", "v3_local:api_v3:analysis_kill"),
        ("test_ns", "test_ns:api_v3:analysis_kill"),
    ],
)
async def test_redis_key_format_matches_adr(ns: str, expected: str):
    """ADR-0059 §3.4: key = `{namespace}:api_v3:analysis_kill`."""
    assert kill_flag_redis_key(ns) == expected
