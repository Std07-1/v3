"""ADR-0114 S5: HTTP процесу smc-alerts (runtime/alerts/app.py, sse.py) — справжній aiohttp-сервер у тесті.

Run: python -m pytest tests/test_alerts_app.py -q
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from core.alerts.types import AlertSide, AlertState
from runtime.alerts.app import COOKIE_NAME, ApiSettings, build_app
from runtime.alerts.devices import DEVICES_FILE, DeviceRegistry, subscriber_for_chat
from runtime.alerts.engine import AlertEngine
from runtime.alerts.feeds import M1ReadError
from runtime.alerts.sse import QUEUE_MAX
from runtime.alerts.store import STATE_FILE, AlertStore
from runtime.alerts.telegram import TelegramError

pytestmark = pytest.mark.asyncio

T0 = 1_792_086_371_000
OWNER, STRANGER = 1001, 2002
SITE = "https://aione-smc.com"
CSRF = {"X-Alerts": "1", "Origin": SITE}
PAIR_TTL = 600_000
UA_ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36"


class World:
    """Двигун, реєстр і сервер з керованим годинником і ціною."""

    def __init__(self, tmp_path, **overrides) -> None:
        self.now = [T0]
        self.prices = {"XAU/USD": 4370.0}
        self.bot_ok = True
        self.store = AlertStore(tmp_path / STATE_FILE)
        self.engine = AlertEngine(self.store, gap_after_ms=900_000, clock_ms=lambda: self.now[0])
        codes = (f"{n:06d}" for n in itertools.count(111111, 111111))
        self.registry = DeviceRegistry(
            tmp_path / DEVICES_FILE, confirm_attempts=3, max_pending=10, new_confirm_code=lambda: next(codes)
        )
        settings = dict(
            symbols=frozenset({"XAU/USD", "XAG/USD"}),
            site_origin=SITE,
            pair_ttl_ms=PAIR_TTL,
            max_active_per_subscriber=2,
            requests_per_min=1000,
            sse_heartbeat_s=30.0,
        )
        settings.update(overrides)
        ids = (f"alert{n}" for n in itertools.count(1))
        self.app = build_app(
            self.engine,
            self.registry,
            ApiSettings(**settings),
            clock_ms=lambda: self.now[0],
            bot_username=self._bot_username,
            reference_price=self._reference_price,
            new_alert_id=lambda: next(ids),
        )

    async def _bot_username(self) -> str:
        if not self.bot_ok:
            raise TelegramError("getMe: HTTP 502")
        return "v3_signals_bot"

    async def _reference_price(self, symbol):
        price = self.prices.get(symbol)
        if isinstance(price, Exception):
            raise price
        return price

    async def pair(self, client: TestClient, *, chat: int = OWNER) -> str:
        """Повна прив'язка «код назад»; → значення cookie пристрою."""
        resp = await client.post("/api/alerts/pair", headers={**CSRF, "User-Agent": UA_ANDROID})
        assert resp.status == 200, await resp.text()
        token = resp.cookies[COOKIE_NAME].value
        link_code = (await resp.json())["link"].split("start=")[1]
        digits = self.registry.issue_confirm_code(
            link_code, subscriber_for_chat(chat), now_ms=self.now[0], ttl_ms=PAIR_TTL
        )
        confirm = await client.post(
            "/api/alerts/pair/confirm", json={"code": digits}, headers={**CSRF, "Cookie": f"{COOKIE_NAME}={token}"}
        )
        assert confirm.status == 200, await confirm.text()
        return token


def _auth(token: str, csrf: bool = True) -> dict:
    return {**(CSRF if csrf else {}), "Cookie": f"{COOKIE_NAME}={token}"}


def _line(level=4380.0, **kw) -> dict:
    return {"symbol": "XAU/USD", "level": level, "tf_label": "M15", **kw}


@pytest_asyncio.fixture
async def world(tmp_path):
    world = World(tmp_path)
    async with TestClient(TestServer(world.app)) as client:
        world.client = client
        yield world


# ── здоров'я, CSRF, прив'язка ────────────────────────────────────────────


async def test_health_reports_degraded_flags_only(world):
    world.engine.set_degraded("telegram_unreachable", "HTTP 502 token-free detail")
    body = await (await world.client.get("/api/alerts/health")).json()
    assert body == {"enabled": True, "degraded": ["telegram_unreachable"]}


@pytest.mark.parametrize(
    "headers, code", [({}, "csrf_header"), ({"X-Alerts": "1", "Origin": "https://evil.example"}, "origin")]
)
async def test_state_changes_require_csrf_header_and_site_origin(world, headers, code):
    resp = await world.client.post("/api/alerts/pair", headers=headers)
    assert resp.status == 403 and (await resp.json())["error"] == code


async def test_pair_sets_strict_cookie_that_opens_nothing_until_digits_confirmed(world):
    resp = await world.client.post("/api/alerts/pair", headers={**CSRF, "User-Agent": UA_ANDROID})
    body, morsel = await resp.json(), resp.cookies[COOKIE_NAME]
    assert body["link"].startswith("https://t.me/v3_signals_bot?start=") and body["label"] == "Android · Chrome"
    assert (morsel["path"], morsel["samesite"], bool(morsel["httponly"]), bool(morsel["secure"])) == (
        "/api/alerts",
        "Strict",
        True,
        True,
    )
    token = morsel.value
    assert (await world.client.get("/api/alerts", headers=_auth(token))).status == 401
    confirm = await world.client.post("/api/alerts/pair/confirm", json={"code": "111111"}, headers=_auth(token))
    assert confirm.status == 409 and (await confirm.json())["status"] == "awaiting_bot"


async def test_confirm_digits_wrong_then_spaced_right_binds_this_browser(world):
    resp = await world.client.post("/api/alerts/pair", headers=CSRF)
    token, link_code = resp.cookies[COOKIE_NAME].value, (await resp.json())["link"].split("start=")[1]
    digits = world.registry.issue_confirm_code(link_code, subscriber_for_chat(OWNER), now_ms=T0, ttl_ms=PAIR_TTL)
    wrong = await world.client.post("/api/alerts/pair/confirm", json={"code": "000000"}, headers=_auth(token))
    assert wrong.status == 400 and (await wrong.json()) == {"status": "wrong_code", "attempts_left": 2}
    spaced = f"{digits[:3]} {digits[3:]}"
    right = await world.client.post("/api/alerts/pair/confirm", json={"code": spaced}, headers=_auth(token))
    assert right.status == 200 and (await right.json())["status"] == "bound"
    assert (await world.client.get("/api/alerts", headers=_auth(token))).status == 200
    again = await world.client.post("/api/alerts/pair", headers=_auth(token))
    assert again.status == 409 and (await again.json())["error"] == "already_paired"


async def test_pair_without_bot_is_loud_503(world):
    world.bot_ok = False
    resp = await world.client.post("/api/alerts/pair", headers=CSRF)
    assert resp.status == 503 and (await resp.json())["error"] == "bot_unavailable"


# ── сповіщення ───────────────────────────────────────────────────────────


async def test_put_creates_alert_with_side_from_reference_price_and_persists(world):
    token = await world.pair(world.client)
    resp = await world.client.put("/api/alerts/d-1", json=_line(), headers=_auth(token))
    alert = (await resp.json())["alert"]
    assert resp.status == 200 and "subscriber_id" not in alert
    assert (alert["side"], alert["state"], alert["delivery"], alert["active_from_ms"]) == ("below", "armed", None, T0)
    assert AlertStore.load(world.store.path, now_ms=T0).alerts["alert1"].drawing_id == "d-1"


async def test_put_same_level_after_fire_does_not_rearm_but_moved_line_or_rearm_does(world):
    token = await world.pair(world.client)
    await world.client.put("/api/alerts/d-1", json=_line(), headers=_auth(token))
    world.engine.apply_tick("XAU/USD", 4381.0, T0 + 1_000)
    same = (await (await world.client.put("/api/alerts/d-1", json=_line(), headers=_auth(token))).json())["alert"]
    assert (same["state"], same["delivery"], same["version"]) == ("fired", "pending", 1)
    moved = (await (await world.client.put("/api/alerts/d-1", json=_line(4390.0), headers=_auth(token))).json())
    assert (moved["alert"]["state"], moved["alert"]["version"], moved["alert"]["level"]) == ("armed", 2, 4390.0)
    world.engine.apply_tick("XAU/USD", 4391.0, T0 + 2_000)
    rearmed = await world.client.put("/api/alerts/d-1", json=_line(4390.0, rearm=True), headers=_auth(token))
    assert (await rearmed.json())["alert"]["version"] == 3


async def test_put_ray_anchor_in_future_activates_from_anchor(world):
    token = await world.pair(world.client)
    body = _line(active_from_ms=T0 + 3_600_000)
    alert = (await (await world.client.put("/api/alerts/d-ray", json=body, headers=_auth(token))).json())["alert"]
    assert alert["active_from_ms"] == T0 + 3_600_000
    past = _line(active_from_ms=T0 - 3_600_000)
    alert = (await (await world.client.put("/api/alerts/d-old", json=past, headers=_auth(token))).json())["alert"]
    assert alert["active_from_ms"] == T0  # перетин у минулому не рахується


@pytest.mark.parametrize(
    "path, body, code",
    [
        ("/api/alerts/d-1", {"symbol": "GER30", "level": 1.0, "tf_label": "M15"}, "symbol"),
        ("/api/alerts/d-1", {"symbol": ["XAU/USD"], "level": 1.0, "tf_label": "M15"}, "symbol"),
        ("/api/alerts/d-1", _line(level="4380"), "level"),
        ("/api/alerts/d-1", _line(level=-1), "level"),
        ("/api/alerts/d-1", _line(tf_label="m15<script>"), "tf_label"),
        ("/api/alerts/d-1", _line(rearm="yes"), "rearm"),
        ("/api/alerts/d-1", _line(active_from_ms=True), "active_from_ms"),
        ("/api/alerts/bad.id", _line(), "drawing_id"),
        ("/api/alerts/d-1", [1, 2], "bad_json"),
    ],
)
async def test_put_validation_is_loud_with_reason(world, path, body, code):
    token = await world.pair(world.client)
    resp = await world.client.put(path, json=body, headers=_auth(token))
    assert resp.status == 400 and (await resp.json())["error"] == code


async def test_put_conflicts_limit_and_price_problems(world):
    token = await world.pair(world.client)
    await world.client.put("/api/alerts/d-1", json=_line(), headers=_auth(token))
    xag_line = {**_line(), "symbol": "XAG/USD"}
    other_symbol = await world.client.put("/api/alerts/d-1", json=xag_line, headers=_auth(token))
    assert other_symbol.status == 409 and (await other_symbol.json())["error"] == "symbol_mismatch"
    await world.client.put("/api/alerts/d-2", json=_line(4400.0), headers=_auth(token))
    over = await world.client.put("/api/alerts/d-3", json=_line(4410.0), headers=_auth(token))
    assert over.status == 409 and (await over.json()) == {"error": "limit", "max_active": 2}
    await world.client.delete("/api/alerts/d-2", headers=_auth(token))
    world.prices["XAU/USD"] = None
    no_price = await world.client.put("/api/alerts/d-3", json=_line(4410.0), headers=_auth(token))
    assert no_price.status == 409 and (await no_price.json())["error"] == "no_price"
    world.prices["XAU/USD"] = M1ReadError("disk_disabled_range_query")
    broken = await world.client.put("/api/alerts/d-3", json=_line(4410.0), headers=_auth(token))
    assert broken.status == 503 and (await broken.json())["error"] == "price_unavailable"


async def test_subscribers_see_and_delete_only_their_own(world):
    owner, stranger = await world.pair(world.client), await world.pair(world.client, chat=STRANGER)
    await world.client.put("/api/alerts/d-1", json=_line(), headers=_auth(owner))
    theirs = await (await world.client.get("/api/alerts", headers=_auth(stranger))).json()
    assert theirs["alerts"] == []
    gone = await (await world.client.delete("/api/alerts/d-1", headers=_auth(stranger))).json()
    assert gone["removed"] is False and "alert1" in world.store.alerts
    mine = await (await world.client.delete("/api/alerts/d-1", headers=_auth(owner))).json()
    assert mine["removed"] is True and world.store.alerts == {}


async def test_delete_without_csrf_header_is_refused(world):
    token = await world.pair(world.client)
    await world.client.put("/api/alerts/d-1", json=_line(), headers=_auth(token))
    resp = await world.client.delete("/api/alerts/d-1", headers=_auth(token, csrf=False))
    assert resp.status == 403 and "alert1" in world.store.alerts


async def test_oversized_body_rejected(world):
    token = await world.pair(world.client)
    resp = await world.client.put("/api/alerts/d-1", data="x" * 5000, headers=_auth(token))
    assert resp.status == 413


# ── ліміт запитів ────────────────────────────────────────────────────────


async def test_rate_limit_per_ip_per_minute(tmp_path):
    world = World(tmp_path, requests_per_min=3)
    async with TestClient(TestServer(world.app)) as client:
        statuses = [(await client.get("/api/alerts/health", headers={"X-Real-IP": "1.2.3.4"})).status for _ in range(4)]
        other_ip = (await client.get("/api/alerts/health", headers={"X-Real-IP": "5.6.7.8"})).status
        world.now[0] += 60_000
        next_minute = (await client.get("/api/alerts/health", headers={"X-Real-IP": "1.2.3.4"})).status
    assert statuses == [200, 200, 200, 429] and other_ip == 200 and next_minute == 200


# ── SSE ──────────────────────────────────────────────────────────────────


async def test_events_stream_fired_and_delivered_with_heartbeat(tmp_path):
    world = World(tmp_path, sse_heartbeat_s=0.05)
    async with TestClient(TestServer(world.app)) as client:
        token = await world.pair(client)
        await client.put("/api/alerts/d-1", json=_line(), headers=_auth(token))
        resp = await client.get("/api/alerts/events", headers=_auth(token, csrf=False))
        assert resp.status == 200 and resp.headers["Content-Type"].startswith("text/event-stream")
        assert await asyncio.wait_for(resp.content.readuntil(b"\n\n"), 2) == b": ping\n\n"
        world.engine.apply_tick("XAU/USD", 4380.5, T0 + 1_000)
        fired = await _next_event(resp)
        assert fired[0] == "fired" and fired[1]["delivery"] == "pending" and fired[1]["state"] == "fired"
        world.engine.ack_delivered(("alert1", 1))
        delivered = await _next_event(resp)
        assert delivered[0] == "delivered" and delivered[1]["delivery"] == "delivered"
        resp.close()
    unpaired = World(tmp_path / "other")
    async with TestClient(TestServer(unpaired.app)) as client:
        assert (await client.get("/api/alerts/events")).status == 401


async def test_slow_stream_overflow_closes_cleanly_instead_of_growing(tmp_path, caplog):
    world = World(tmp_path)
    async with TestClient(TestServer(world.app)) as client:
        token = await world.pair(client)
        await client.put("/api/alerts/d-1", json=_line(), headers=_auth(token))
        resp = await client.get("/api/alerts/events", headers=_auth(token, csrf=False))
        alert = world.store.alerts["alert1"]
        with caplog.at_level(logging.WARNING):
            for _ in range(QUEUE_MAX + 1):  # сторінка не встигає читати
                world.engine.listeners[0]("fired", alert)
            rest = await asyncio.wait_for(resp.content.read(), 2)  # сервер закрив потік
    assert b"event:" not in rest  # черга скинута, не дописана
    messages = [r.getMessage() for r in caplog.records]
    assert any("ALERTS_SSE_OVERFLOW" in m for m in messages)
    assert not any("Error handling request" in m for m in messages)


async def _next_event(resp):
    while True:
        chunk = (await asyncio.wait_for(resp.content.readuntil(b"\n\n"), 2)).decode()
        if chunk.startswith(":"):
            continue
        name_line, data_line = chunk.strip().split("\n")
        return name_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: "))
