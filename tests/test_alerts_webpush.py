"""ADR-0114 Rev 2 S9: пуш у браузер (runtime/alerts/webpush.py).

Відправка — справжній HTTPS-запит aiohttp до локального сервера: резолвер спрямовує `fcm.googleapis.com:443` на
порт тестового сервера, тож перевіряються і SSRF-правила, і справжній POST (заголовки, підпис VAPID, тіло, яке
розшифровує ключ «браузера»). Run: python -m pytest tests/test_alerts_webpush.py -q
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import json
import os
import socket
import ssl
import time
from email.utils import format_datetime
from typing import Any, Dict, List

import aiohttp
import http_ece
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.abc import AbstractResolver
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from py_vapid import Vapid02

from runtime.alerts.webpush import (
    MAX_PLAINTEXT_BYTES,
    VAPID_EXP_S,
    PushOutcome,
    PushSubscription,
    WebPushSender,
    _b64url_decode,
    classify,
    endpoint_label,
    retry_after_s,
    validate_endpoint,
)

SUFFIXES = ("fcm.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com")
SUBJECT = "https://aione-smc.com"
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc:def"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _browser_keys() -> tuple:
    """Ключі «браузера»: приватний (щоб розшифрувати), p256dh, auth."""
    private = ec.generate_private_key(ec.SECP256R1())
    point = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return private, _b64(point), _b64(os.urandom(16))


def _vapid_key() -> str:
    return _b64(ec.generate_private_key(ec.SECP256R1()).private_numbers().private_value.to_bytes(32, "big"))


# ── SSRF: який endpoint приймаємо ────────────────────────────────────


@pytest.mark.parametrize(
    "endpoint",
    [
        ENDPOINT,
        "https://updates.push.services.mozilla.com/wpush/v2/gAAA",
        "https://web.push.apple.com/QGuQyavXutnMH",
        "https://wns2-par02p.notify.windows.com/w/?token=BQYAAA",
        "https://fcm.googleapis.com:443/fcm/send/x",
        "https://FCM.googleapis.com./fcm/send/x",
    ],
)
def test_validate_endpoint_accepts_push_services(endpoint):
    assert validate_endpoint(endpoint, SUFFIXES) == endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/fcm/send/x",  # не https
        "https://fcm.googleapis.com:8443/fcm/send/x",  # не 443
        "https://user:pw@fcm.googleapis.com/fcm/send/x",  # облікові дані
        "https://127.0.0.1/fcm/send/x",
        "https://[::1]/fcm/send/x",
        "https://169.254.169.254/latest/meta-data",
        "https://example.com/fcm/send/x",
        "https://fcm.googleapis.com.evil.com/x",  # суфікс не в кінці
        "https://evilfcm.googleapis.com/x",  # не на межі крапки
        "https://localhost/x",
        "https://fcm.googleapis.com:99999/x",  # кривий порт
        "https://fcm.googleapis.com/" + "a" * 2100,
        "",
        None,
        42,
    ],
)
def test_validate_endpoint_rejects_anything_else(endpoint):
    with pytest.raises(ValueError):
        validate_endpoint(endpoint, SUFFIXES)


@pytest.mark.parametrize("endpoint", ["https://127.0.0.1/x", "https://[::1]/x", "https://10.0.0.1/x"])
def test_validate_endpoint_names_ip_literal_even_if_allowlist_is_wide(endpoint):
    with pytest.raises(ValueError, match="IP"):
        validate_endpoint(endpoint, SUFFIXES + ("127.0.0.1", "::1", "10.0.0.1"))


def test_b64url_decode_is_strict_about_foreign_characters():
    assert _b64url_decode("QUJDREVG", "x") == b"ABCDEF"
    with pytest.raises(ValueError):
        _b64url_decode("QUJD!REVG", "x")  # нестрогий декодер мовчки викинув би «!» і віддав ABCDEF


def test_endpoint_label_has_host_and_hash_but_no_path():
    label = endpoint_label(ENDPOINT)
    assert label.startswith("fcm.googleapis.com#") and "abc" not in label
    assert label == endpoint_label(ENDPOINT) != endpoint_label(ENDPOINT + "x")


# ── Підписка з браузера ───────────────────────────────────────────────


def test_subscription_parse_accepts_browser_json():
    _, p256dh, auth = _browser_keys()
    sub = PushSubscription.parse({"endpoint": ENDPOINT, "expirationTime": None, "keys": {"p256dh": p256dh, "auth": auth}}, SUFFIXES)
    assert (sub.endpoint, sub.p256dh, sub.auth) == (ENDPOINT, p256dh, auth)


@pytest.mark.parametrize("broken", ["no_keys", "bad_b64", "short_point", "compressed_point", "short_auth", "bad_endpoint", "not_dict"])
def test_subscription_parse_rejects_broken(broken):
    _, p256dh, auth = _browser_keys()
    raw: Any = {"endpoint": ENDPOINT, "keys": {"p256dh": p256dh, "auth": auth}}
    if broken == "no_keys":
        del raw["keys"]
    elif broken == "bad_b64":
        raw["keys"]["p256dh"] = "!!!не base64!!!"
    elif broken == "short_point":
        raw["keys"]["p256dh"] = _b64(b"\x04" + os.urandom(31))
    elif broken == "compressed_point":
        raw["keys"]["p256dh"] = _b64(b"\x02" + os.urandom(64))
    elif broken == "short_auth":
        raw["keys"]["auth"] = _b64(os.urandom(8))
    elif broken == "bad_endpoint":
        raw["endpoint"] = "https://example.com/x"
    else:
        raw = [raw]
    with pytest.raises(ValueError):
        PushSubscription.parse(raw, SUFFIXES)


# ── Відповідь push-сервісу ────────────────────────────────────────────


@pytest.mark.parametrize(
    "status, outcome",
    [
        (201, PushOutcome.DELIVERED),
        (200, PushOutcome.DELIVERED),
        (202, PushOutcome.DELIVERED),
        (404, PushOutcome.GONE),
        (410, PushOutcome.GONE),
        (429, PushOutcome.RETRY),
        (500, PushOutcome.RETRY),
        (503, PushOutcome.RETRY),
        (400, PushOutcome.REJECTED),
        (401, PushOutcome.REJECTED),
        (403, PushOutcome.REJECTED),
        (413, PushOutcome.REJECTED),
        (302, PushOutcome.REJECTED),
    ],
)
def test_classify_maps_status_to_outcome(status, outcome):
    assert classify(status, None).outcome is outcome


def test_classify_keeps_retry_after_only_for_retry():
    assert classify(429, 30.0).retry_after_s == 30.0
    assert classify(410, 30.0).retry_after_s is None


def test_retry_after_parses_seconds_and_http_date():
    now = time.time()
    date = format_datetime(datetime.datetime.fromtimestamp(now + 120, tz=datetime.timezone.utc), usegmt=True)
    assert retry_after_s("30", now) == 30.0
    assert 118 <= retry_after_s(date, now) <= 121
    assert retry_after_s("колись", now) is None
    assert retry_after_s(None, now) is None


# ── Відправник: ключ ──────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["", "!!!", _b64(os.urandom(31)), _b64(os.urandom(33))])
def test_sender_rejects_key_that_is_not_32_raw_bytes(key):
    with pytest.raises(ValueError):
        WebPushSender(None, key, SUBJECT, ttl_s=60, timeout_s=1, host_suffixes=SUFFIXES)  # type: ignore[arg-type]


def test_sender_application_server_key_is_public_point_of_vapid_key():
    private_raw = _vapid_key()
    sender = WebPushSender(None, private_raw, SUBJECT, ttl_s=60, timeout_s=1, host_suffixes=SUFFIXES)  # type: ignore[arg-type]
    point = base64.urlsafe_b64decode(sender.application_server_key + "==")
    assert len(point) == 65 and point[0] == 4
    d = int.from_bytes(base64.urlsafe_b64decode(private_raw + "="), "big")
    expected = ec.derive_private_key(d, ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    assert point == expected


# ── Відправник: справжній HTTPS до локального «push-сервісу» ───────────


def _self_signed_ssl(folder) -> ssl.SSLContext:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fcm.googleapis.com")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    cert_path, key_path = folder / "cert.pem", folder / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    ctx.load_cert_chain(str(cert_path), str(key_path))
    return ctx


class _ToLocal(AbstractResolver):
    """Будь-який хост → 127.0.0.1:<порт сервера> (порт 443 з URL лишається в URL)."""

    def __init__(self, port: int) -> None:
        self.port = port

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> List[Dict[str, Any]]:
        return [{"hostname": host, "host": "127.0.0.1", "port": self.port, "family": socket.AF_INET, "proto": 0, "flags": 0}]

    async def close(self) -> None:
        return None


class _PushService:
    def __init__(self) -> None:
        self.requests: List[Dict[str, Any]] = []
        self.status, self.headers, self.text, self.delay_s = 201, {}, "", 0.0

    async def handle(self, request: web.Request) -> web.Response:
        self.requests.append({"path": request.path_qs, "headers": dict(request.headers), "body": await request.read()})
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return web.Response(status=self.status, headers=self.headers, text=self.text)


@pytest_asyncio.fixture
async def push_env(tmp_path):
    service = _PushService()
    app = web.Application()
    app.router.add_route("POST", "/{tail:.*}", service.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0, ssl_context=_self_signed_ssl(tmp_path)).start()
    port = runner.addresses[0][1]
    session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=_ToLocal(port), ssl=False))
    sender = WebPushSender(session, _vapid_key(), SUBJECT, ttl_s=21600, timeout_s=1.0, host_suffixes=SUFFIXES)
    private, p256dh, auth = _browser_keys()
    sub = PushSubscription.parse({"endpoint": ENDPOINT, "keys": {"p256dh": p256dh, "auth": auth}}, SUFFIXES)
    try:
        yield {"service": service, "sender": sender, "sub": sub, "browser_key": private, "auth": auth}
    finally:
        await session.close()
        await runner.cleanup()


PAYLOAD = {"title": "XAU/USD 4164.00 — перетнуто знизу вгору", "body": "ціна 4164.35 · 14:32", "url": "/?symbol=XAU%2FUSD&tf=M30", "tag": "a-1"}


@pytest.mark.asyncio
async def test_send_delivers_encrypted_payload_with_vapid_signature(push_env):
    started = int(time.time())
    result = await push_env["sender"].send(push_env["sub"], PAYLOAD)
    assert (result.outcome, result.status) == (PushOutcome.DELIVERED, 201)
    (req,) = push_env["service"].requests
    assert req["path"] == "/fcm/send/abc:def"
    headers = req["headers"]
    assert headers["TTL"] == "21600" and headers["Urgency"] == "high"
    assert headers["Content-Encoding"] == "aes128gcm" and headers["Content-Type"] == "application/octet-stream"
    # Підпис VAPID справжній і від нашого ключа; claims — для цього push-сервісу
    auth_header = headers["Authorization"]
    assert Vapid02.verify(auth_header)
    token, key = auth_header.split(" ", 1)[1].split(",")
    assert key == "k=" + push_env["sender"].application_server_key
    claims = json.loads(base64.urlsafe_b64decode(token[2:].split(".")[1] + "=="))
    assert claims["aud"] == "https://fcm.googleapis.com" and claims["sub"] == SUBJECT
    assert started + VAPID_EXP_S - 5 <= claims["exp"] <= int(time.time()) + VAPID_EXP_S
    # Тіло розшифровує лише браузер (його приватний ключ + auth) — і це наш payload
    plain = http_ece.decrypt(
        req["body"],
        private_key=push_env["browser_key"],
        auth_secret=base64.urlsafe_b64decode(push_env["auth"] + "=="),
        version="aes128gcm",
    )
    assert json.loads(plain.decode("utf-8")) == PAYLOAD


@pytest.mark.parametrize(
    "status, headers, outcome, retry_after",
    [
        (410, {}, PushOutcome.GONE, None),
        (404, {}, PushOutcome.GONE, None),
        (429, {"Retry-After": "30"}, PushOutcome.RETRY, 30.0),
        (503, {}, PushOutcome.RETRY, None),
        (400, {}, PushOutcome.REJECTED, None),
        (302, {"Location": "https://169.254.169.254/"}, PushOutcome.REJECTED, None),
    ],
)
@pytest.mark.asyncio
async def test_send_classifies_service_answer_and_never_follows_redirect(push_env, status, headers, outcome, retry_after):
    service = push_env["service"]
    service.status, service.headers, service.text = status, headers, "причина сервісу"
    result = await push_env["sender"].send(push_env["sub"], PAYLOAD)
    assert (result.outcome, result.status, result.retry_after_s) == (outcome, status, retry_after)
    assert len(service.requests) == 1  # перенаправлення не виконується
    if outcome is not PushOutcome.DELIVERED:
        assert "причина сервісу" in result.detail


@pytest.mark.asyncio
async def test_send_timeout_is_retry_without_status(push_env):
    push_env["service"].delay_s = 1.6  # тайм-аут відправника 1 с
    result = await push_env["sender"].send(push_env["sub"], PAYLOAD)
    assert (result.outcome, result.status) == (PushOutcome.RETRY, None)


@pytest.mark.asyncio
async def test_send_rechecks_endpoint_and_payload_size_before_any_request(push_env):
    narrowed = WebPushSender(
        push_env["sender"]._session, _vapid_key(), SUBJECT, ttl_s=60, timeout_s=1.0, host_suffixes=("push.apple.com",)
    )
    assert (await narrowed.send(push_env["sub"], PAYLOAD)).outcome is PushOutcome.REJECTED
    too_big = {"body": "я" * MAX_PLAINTEXT_BYTES}
    result = await push_env["sender"].send(push_env["sub"], too_big)
    assert result.outcome is PushOutcome.REJECTED and result.status is None
    assert push_env["service"].requests == []
