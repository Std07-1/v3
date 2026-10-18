"""ADR-0114 Rev 2 S11: HTTP каналу «пуш» (runtime/alerts/app.py) — ключ, підписка, вимкнення пристрою, межі каналу.

Справжній aiohttp-сервер у тесті. Cookie — заголовком (CookieJar тестового клієнта не шле Secure-cookie по http).
Run: python -m pytest tests/test_alerts_app_push.py -q
"""

from __future__ import annotations

import base64
import itertools
import os
from http.cookies import SimpleCookie

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from runtime.alerts.app import COOKIE_NAME, ApiSettings, build_app
from runtime.alerts.devices import DEVICES_FILE, TOUCH_RESOLUTION_MS, DeviceRegistry, WebLimits
from runtime.alerts.engine import AlertEngine
from runtime.alerts.store import STATE_FILE, AlertStore

pytestmark = pytest.mark.asyncio

T0 = 1_792_086_371_000
SITE = "https://aione-smc.com"
CSRF = {"X-Alerts": "1", "Origin": SITE}
SUFFIXES = ("fcm.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com")
PUBLIC_KEY = "BPublicKeyForTests"
UA_ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36"


def _subscription(n: int = 1, endpoint: str = "") -> dict:
    point = base64.urlsafe_b64encode(b"\x04" + os.urandom(64)).rstrip(b"=").decode()
    auth = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
    return {
        "endpoint": endpoint or f"https://fcm.googleapis.com/fcm/send/dev{n}",
        "expirationTime": None,
        "keys": {"p256dh": point, "auth": auth},
    }


class World:
    def __init__(self, tmp_path, *, channel: str = "push", max_devices: int = 100, per_ip: int = 100) -> None:
        self.now = [T0]
        self.engine = AlertEngine(AlertStore(tmp_path / STATE_FILE), gap_after_ms=900_000, clock_ms=lambda: self.now[0])
        counter = itertools.count(1)
        self.registry = DeviceRegistry(
            tmp_path / DEVICES_FILE,
            confirm_attempts=3,
            max_pending=10,
            new_secret=lambda n: f"s{next(counter)}x{n}",
            web_limits=WebLimits(max_devices, per_ip),
        )
        settings = ApiSettings(
            symbols=frozenset({"XAU/USD"}),
            site_origin=SITE,
            pair_ttl_ms=600_000,
            max_active_per_subscriber=5,
            requests_per_min=1000,
            sse_heartbeat_s=30.0,
            channel=channel,
            push_host_suffixes=SUFFIXES,
            push_public_key=PUBLIC_KEY if channel == "push" else None,
        )
        ids = (f"alert{n}" for n in itertools.count(1))

        async def bot_username() -> str:
            return "v3_signals_bot"

        async def reference_price(symbol):
            return 4370.0

        self.app = build_app(
            self.engine,
            self.registry,
            settings,
            clock_ms=lambda: self.now[0],
            bot_username=bot_username,
            reference_price=reference_price,
            new_alert_id=lambda: next(ids),
        )

    async def subscribe(self, client: TestClient, body: dict, *, cookie: str = "", ip: str = "1.1.1.1"):
        headers = {**CSRF, "User-Agent": UA_ANDROID, "X-Real-IP": ip}
        if cookie:
            headers["Cookie"] = f"{COOKIE_NAME}={cookie}"
        return await client.post("/api/alerts/push/subscribe", json=body, headers=headers)


def _cookie_of(resp) -> SimpleCookie:
    jar = SimpleCookie()
    for header in resp.headers.getall("Set-Cookie", []):
        jar.load(header)
    return jar


def _auth(token: str, csrf: bool = True) -> dict:
    return {**(CSRF if csrf else {}), "Cookie": f"{COOKIE_NAME}={token}"}


@pytest_asyncio.fixture
async def world(tmp_path):
    w = World(tmp_path)
    async with TestClient(TestServer(w.app)) as client:
        w.client = client
        yield w


async def test_health_names_push_channel_and_key_is_served(world):
    assert (await (await world.client.get("/api/alerts/health")).json())["channel"] == "push"
    assert await (await world.client.get("/api/alerts/push/key")).json() == {"public_key": PUBLIC_KEY}


async def test_telegram_routes_are_off_in_push_channel(world):
    for path in ("/api/alerts/pair", "/api/alerts/pair/confirm"):
        resp = await world.client.post(path, json={"code": "111111"}, headers=CSRF)
        assert resp.status == 404 and (await resp.json())["error"] == "telegram_disabled"


async def test_push_routes_are_off_in_telegram_channel(tmp_path):
    w = World(tmp_path, channel="telegram")
    async with TestClient(TestServer(w.app)) as client:
        key = await client.get("/api/alerts/push/key")
        sub = await w.subscribe(client, _subscription())
        assert (key.status, sub.status) == (404, 404)
        assert (await sub.json())["error"] == "push_disabled" and w.registry.devices == {}


async def test_subscribe_creates_device_with_strict_cookie_that_opens_alerts(world):
    resp = await world.subscribe(world.client, _subscription())
    assert resp.status == 200
    device = (await resp.json())["device"]
    assert device["label"] == "Android · Chrome"
    morsel = _cookie_of(resp)[COOKIE_NAME]
    assert morsel["httponly"] and morsel["secure"] and morsel["samesite"] == "Strict" and morsel["path"] == "/api/alerts"
    token = morsel.value
    assert (await (await world.client.get("/api/alerts", headers=_auth(token, csrf=False))).json())["alerts"] == []
    line = {"symbol": "XAU/USD", "level": 4380.0, "tf_label": "M15"}
    put = await world.client.put("/api/alerts/d-1", json=line, headers=_auth(token))
    assert put.status == 200
    assert world.engine.store.alerts["alert1"].subscriber_id == f"web:{device['device_id']}"


async def test_resubscribe_with_own_cookie_keeps_device_and_sets_no_cookie(world):
    first = await world.subscribe(world.client, _subscription(1))
    token, device_id = _cookie_of(first)[COOKIE_NAME].value, (await first.json())["device"]["device_id"]
    again = await world.subscribe(world.client, _subscription(2), cookie=token)
    assert again.status == 200 and (await again.json())["device"]["device_id"] == device_id
    assert COOKIE_NAME not in _cookie_of(again) and len(world.registry.devices) == 1


@pytest.mark.parametrize(
    "body",
    [
        _subscription(endpoint="http://fcm.googleapis.com/fcm/send/x"),
        _subscription(endpoint="https://169.254.169.254/latest"),
        _subscription(endpoint="https://evil.example/push"),
        {"endpoint": "https://fcm.googleapis.com/fcm/send/x"},
        [],
    ],
)
async def test_bad_subscription_is_refused_without_device(world, body):
    resp = await world.subscribe(world.client, body)
    assert resp.status == 400 and (await resp.json())["error"] in ("bad_subscription", "bad_json")
    assert world.registry.devices == {}


async def test_subscribe_requires_csrf_header_and_site_origin(world):
    for headers in ({}, {"X-Alerts": "1", "Origin": "https://evil.example"}):
        resp = await world.client.post("/api/alerts/push/subscribe", json=_subscription(), headers=headers)
        assert resp.status == 403
    assert world.registry.devices == {}


async def test_device_limits_answer_429_per_ip_and_503_when_full(tmp_path):
    w = World(tmp_path, max_devices=2, per_ip=1)
    async with TestClient(TestServer(w.app)) as client:
        assert (await w.subscribe(client, _subscription(1), ip="9.9.9.9")).status == 200
        per_ip = await w.subscribe(client, _subscription(2), ip="9.9.9.9")
        assert per_ip.status == 429 and (await per_ip.json())["error"] == "devices_per_ip"
        assert (await w.subscribe(client, _subscription(3), ip="8.8.8.8")).status == 200
        full = await w.subscribe(client, _subscription(4), ip="7.7.7.7")
        assert full.status == 503 and (await full.json())["error"] == "devices_full"


async def test_delete_device_forgets_alerts_device_and_cookie(world):
    resp = await world.subscribe(world.client, _subscription())
    token = _cookie_of(resp)[COOKIE_NAME].value
    line = {"symbol": "XAU/USD", "level": 4380.0, "tf_label": "M15"}
    await world.client.put("/api/alerts/d-1", json=line, headers=_auth(token))
    gone = await world.client.delete("/api/alerts/device", headers=_auth(token))
    assert gone.status == 200 and await gone.json() == {"removed_alerts": 1}  # не «лінія з id device»
    cleared = _cookie_of(gone)[COOKIE_NAME]
    assert cleared.value == "" and cleared["path"] == "/api/alerts"
    assert world.engine.store.alerts == {} and world.registry.devices == {}
    assert (await world.client.get("/api/alerts", headers=_auth(token, csrf=False))).status == 401


async def test_list_tells_page_when_its_push_subscription_is_gone(world):
    resp = await world.subscribe(world.client, _subscription())
    token, device_id = _cookie_of(resp)[COOKIE_NAME].value, (await resp.json())["device"]["device_id"]
    listed = await (await world.client.get("/api/alerts", headers=_auth(token, csrf=False))).json()
    assert listed["device"] == {"device_id": device_id, "label": "Android · Chrome", "push_gone": False}
    world.registry.mark_push_gone(device_id)
    listed = await (await world.client.get("/api/alerts", headers=_auth(token, csrf=False))).json()
    assert listed["device"]["push_gone"] is True


async def test_requests_refresh_last_seen_hourly(world):
    resp = await world.subscribe(world.client, _subscription())
    token, device_id = _cookie_of(resp)[COOKIE_NAME].value, (await resp.json())["device"]["device_id"]
    world.now[0] = T0 + TOUCH_RESOLUTION_MS
    await world.client.get("/api/alerts", headers=_auth(token, csrf=False))
    assert world.registry.devices[device_id].last_seen_ms == T0 + TOUCH_RESOLUTION_MS
