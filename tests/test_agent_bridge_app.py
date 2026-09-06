"""ADR-0090 S1 — bridge як окремий процес: auth, health, ціна з Redis, gating консолі."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.agent_bridge.app import build_bridge_app

pytestmark = pytest.mark.asyncio

TOKEN_ENV = "TEST_BRIDGE_TOKEN"
NS = "t_ns"


class FakeRedis:
    """Мінімальний sync-Redis: рівно ті команди, що використовують handler'и."""

    def __init__(self):
        self.hashes: dict[str, dict] = {}
        self.lists: dict[str, list] = {}
        self.strings: dict[str, str] = {}
        self.streams: dict[str, list] = {}

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        return items[start : (end + 1) if end >= 0 else None]

    def get(self, key):
        return self.strings.get(key)

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def ltrim(self, key, start, end):
        self.lists[key] = self.lists.get(key, [])[start : end + 1]

    def xadd(self, key, fields, maxlen=None, approximate=True):
        self.streams.setdefault(key, []).append(fields)
        return f"{len(self.streams[key])}-0"


def _cfg(data_dir: Path, *, console_enabled: bool = True, enabled: bool = True) -> dict:
    return {
        "redis": {"namespace": NS},
        "agent_bridge": {
            "enabled": enabled,
            "data_dir": str(data_dir),
            "console": {"enabled": console_enabled, "auth_token_env": TOKEN_ENV},
        },
    }


def _app(tmp_path: Path, fake: FakeRedis, **kw):
    return build_bridge_app(_cfg(tmp_path, **kw), redis_client=fake, environ={TOKEN_ENV: "tok"})


AUTH = {"Authorization": "Bearer tok"}


async def test_disabled_bridge_serves_only_health_and_stays_alive(aiohttp_client, tmp_path):
    """S1 рев'ю: вимкнений bridge = живий процес з health enabled:false; жодного приватного маршруту."""
    client = await aiohttp_client(_app(tmp_path, FakeRedis(), enabled=False))
    body = await (await client.get("/api/bridge/health")).json()
    assert body["enabled"] is False and body["console"] is False
    for path in ("/api/agent/state", "/api/archi/now", "/api/archi/thinking"):
        assert (await client.get(path, headers=AUTH)).status == 404, path


async def test_health_is_public_and_names_the_service(aiohttp_client, tmp_path):
    client = await aiohttp_client(_app(tmp_path, FakeRedis()))
    resp = await client.get("/api/bridge/health")
    body = await resp.json()
    assert resp.status == 200
    assert body["service"] == "agent_bridge" and body["console"] is True and body["redis"] is True


async def test_archi_now_requires_bearer(aiohttp_client, tmp_path):
    client = await aiohttp_client(_app(tmp_path, FakeRedis()))
    assert (await client.get("/api/archi/now")).status == 401
    assert (await client.get("/api/archi/now", headers={"Authorization": "Bearer wrong"})).status == 401
    assert (await client.get("/api/archi/now", headers=AUTH)).status == 200


async def test_archi_now_price_comes_from_redis_tick_last(aiohttp_client, tmp_path):
    fake = FakeRedis()
    client = await aiohttp_client(_app(tmp_path, fake))
    body = await (await client.get("/api/archi/now?symbol=XAU/USD", headers=AUTH)).json()
    assert body["price"] is None and "price_unavailable" in body["degraded"]
    fake.strings[f"{NS}:tick:last:XAU_USD"] = json.dumps({"mid": 4200.5, "tick_ts_ms": 1})
    body = await (await client.get("/api/archi/now?symbol=XAU/USD", headers=AUTH)).json()
    assert body["price"] == 4200.5 and "price_unavailable" not in body["degraded"]


async def test_agent_state_204_without_data_then_hash(aiohttp_client, tmp_path):
    fake = FakeRedis()
    client = await aiohttp_client(_app(tmp_path, fake))
    assert (await client.get("/api/agent/state", headers=AUTH)).status == 204
    fake.hashes[f"{NS}:agent:state"] = {"mood": "calm", "ts_ms": "1"}
    body = await (await client.get("/api/agent/state", headers=AUTH)).json()
    assert body["mood"] == "calm"


async def test_thinking_pagination_over_http(aiohttp_client, tmp_path):
    (tmp_path / "v3_thinking_archive.jsonl").write_text(
        "\n".join(json.dumps({"i": i}) for i in range(3)) + "\n", encoding="utf-8"
    )
    client = await aiohttp_client(_app(tmp_path, FakeRedis()))
    body = await (await client.get("/api/archi/thinking?limit=2", headers=AUTH)).json()
    assert body["total"] == 3 and [e["i"] for e in body["entries"]] == [2, 1]


async def test_console_disabled_hides_archi_routes_but_agent_routes_stay_fail_closed(aiohttp_client, tmp_path):
    client = await aiohttp_client(_app(tmp_path, FakeRedis(), console_enabled=False))
    assert (await client.get("/api/archi/now", headers=AUTH)).status == 404
    assert (await client.get("/api/archi/thinking", headers=AUTH)).status == 404
    assert (await client.get("/api/agent/state", headers=AUTH)).status == 401


@pytest.mark.parametrize("path", ["/api/archi/wakes", "/api/archi/logs", "/api/agent/feed"])
async def test_every_private_route_is_fail_closed_without_token(aiohttp_client, tmp_path, path):
    client = await aiohttp_client(_app(tmp_path, FakeRedis()))
    assert (await client.get(path)).status == 401
