"""Пуш у браузер (ADR-0114 Rev 2 §2a): шифрування RFC 8291 (aes128gcm) і підпис VAPID RFC 8292 → POST на endpoint
підписки браузера.

- Кодування й підпис — публічні частини `pywebpush` (`WebPusher.encode`) і `py-vapid` (`Vapid.sign`): мілісекунди
  процесора. POST — aiohttp-сесія процесу, без перенаправлень і з тайм-аутом; статус — у `PushResult`, не винятком.
- **SSRF:** endpoint задає браузер, а сервер на нього шле POST — лише `https://`, порт 443, хост зі списку
  push-сервісів (`push_host_suffixes`), без IP-літералів і облікових даних. Перевірка — при підписці
  (`PushSubscription.parse`) і перед кожною відправкою (список міг змінитись).
- Відповідь → `PushOutcome`: DELIVERED (2xx — прийнято сервісом, не «показано на екрані»), RETRY (429, 5xx, мережа;
  `Retry-After`), GONE (404/410 — підписки нема, треба ввімкнути знову), REJECTED (решта: формат, підпис, перенаправлення
  — гучно в лог, без повтору).
- Endpoint — ідентифікатор браузера: у логах лише `endpoint_label` (хост і хеш).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import dataclasses
import email.utils
import enum
import hashlib
import json
import logging
import time
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlsplit

import aiohttp
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPusher

_log = logging.getLogger(__name__)

MAX_ENDPOINT_CHARS = 2048
# Тіло ≤ 4096 байт (обмеження push-сервісів): заголовок aes128gcm 86 + розділювач 1 + тег 16 → відкритого тексту ≤ 3993
MAX_PLAINTEXT_BYTES = 3993
VAPID_EXP_S = 12 * 3600  # RFC 8292: не довше 24 год; 12 год — як типово в pywebpush
_P256_POINT_BYTES, _AUTH_SECRET_BYTES = 65, 16


class PushOutcome(enum.Enum):
    DELIVERED = "delivered"
    RETRY = "retry"
    GONE = "gone"
    REJECTED = "rejected"


@dataclasses.dataclass(frozen=True)
class PushResult:
    outcome: PushOutcome
    status: Optional[int]  # None — відповіді не було (мережа, тайм-аут, відмова до запиту)
    retry_after_s: Optional[float] = None
    detail: str = ""


def _b64url_decode(value: Any, field: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field}: не рядок")
    try:  # строго: чужі символи — помилка, а не мовчки відкинуті
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(f"{field}: не base64url") from None


def validate_endpoint(endpoint: Any, host_suffixes: Sequence[str]) -> str:
    """Endpoint підписки, на який серверу дозволено слати POST; інакше `ValueError` з причиною."""
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > MAX_ENDPOINT_CHARS:
        raise ValueError("endpoint: порожній або задовгий")
    parts = urlsplit(endpoint)
    if parts.scheme != "https":
        raise ValueError("endpoint: не https")
    if parts.username is not None or parts.password is not None:
        raise ValueError("endpoint: облікові дані в URL")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("endpoint: кривий порт") from None
    if port not in (None, 443):
        raise ValueError("endpoint: порт не 443")
    host = (parts.hostname or "").rstrip(".").lower()
    if ":" in host or host.replace(".", "").isdigit():  # IPv6 / IPv4-літерал (перевірка суфікса їх і так не пропустить)
        raise ValueError("endpoint: IP замість хоста")
    if not any(host == s or host.endswith("." + s) for s in host_suffixes):
        raise ValueError(f"endpoint: хост {host!r} не push-сервіс")
    return endpoint


@dataclasses.dataclass(frozen=True)
class PushSubscription:
    endpoint: str
    p256dh: str  # base64url, точка P-256 без стиснення (65 байт)
    auth: str  # base64url, секрет 16 байт

    @classmethod
    def parse(cls, raw: Any, host_suffixes: Sequence[str]) -> "PushSubscription":
        """Підписка з браузера (`PushSubscription.toJSON()`): `{endpoint, keys: {p256dh, auth}}`; інакше `ValueError`."""
        if not isinstance(raw, Mapping) or not isinstance(raw.get("keys"), Mapping):
            raise ValueError("підписка: нема keys")
        endpoint = validate_endpoint(raw.get("endpoint"), host_suffixes)
        keys = raw["keys"]
        point = _b64url_decode(keys.get("p256dh"), "p256dh")
        if len(point) != _P256_POINT_BYTES or point[0] != 0x04:
            raise ValueError("p256dh: не точка P-256 без стиснення")
        if len(_b64url_decode(keys.get("auth"), "auth")) != _AUTH_SECRET_BYTES:
            raise ValueError("auth: не 16 байт")
        return cls(endpoint=endpoint, p256dh=keys["p256dh"], auth=keys["auth"])


def endpoint_label(endpoint: str) -> str:
    """Для логів: хост і короткий хеш — без шляху (це ідентифікатор браузера)."""
    host = urlsplit(endpoint).hostname or "?"
    return f"{host}#{hashlib.sha256(endpoint.encode()).hexdigest()[:10]}"


def retry_after_s(header: Optional[str], now_s: float) -> Optional[float]:
    """`Retry-After`: секунди або HTTP-дата; кривий — None (пауза за власним правилом черги)."""
    if not header:
        return None
    value = header.strip()
    if value.isdigit():
        return float(value)
    parsed = email.utils.parsedate_tz(value)  # None на кривому значенні, без винятку
    return None if parsed is None else max(0.0, email.utils.mktime_tz(parsed) - now_s)


def classify(status: int, retry_after: Optional[float], detail: str = "") -> PushResult:
    if 200 <= status < 300:
        return PushResult(PushOutcome.DELIVERED, status)
    if status in (404, 410):
        return PushResult(PushOutcome.GONE, status, detail=detail)
    if status == 429 or status >= 500:
        return PushResult(PushOutcome.RETRY, status, retry_after, detail)
    return PushResult(PushOutcome.REJECTED, status, detail=detail)


def load_vapid(private_key: str) -> Vapid:
    """Ключ VAPID — base64url сирих 32 байт (генерує `runtime.alerts.vapid_keys`); інакше `ValueError`."""
    if len(_b64url_decode(private_key, "ключ VAPID")) != 32:
        raise ValueError("ключ VAPID: не 32 байти")
    return Vapid.from_raw(private_key.encode())


def application_server_key(vapid: Vapid) -> str:
    """Публічний ключ для `pushManager.subscribe({applicationServerKey})`: base64url точки P-256 без стиснення."""
    point = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(point).rstrip(b"=").decode()


class WebPushSender:
    """Відправка одного пушу на endpoint підписки браузера."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        vapid_private_key: str,
        subject: str,
        *,
        ttl_s: int,
        timeout_s: float,
        host_suffixes: Sequence[str],
    ) -> None:
        self._vapid = load_vapid(vapid_private_key)
        self.application_server_key = application_server_key(self._vapid)
        self._session, self._subject = session, subject
        self._ttl_s, self._timeout_s, self._host_suffixes = ttl_s, timeout_s, tuple(host_suffixes)

    async def send(self, sub: PushSubscription, payload: Mapping[str, Any]) -> PushResult:
        try:
            validate_endpoint(sub.endpoint, self._host_suffixes)
        except ValueError as exc:  # список хостів звузили після підписки, або підписку підробили
            _log.warning("WEBPUSH_ENDPOINT_REFUSED %s %s", endpoint_label(sub.endpoint), exc)
            return PushResult(PushOutcome.REJECTED, None, detail=str(exc))
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_PLAINTEXT_BYTES:
            return PushResult(PushOutcome.REJECTED, None, detail=f"тіло {len(body)} байт > {MAX_PLAINTEXT_BYTES}")
        info = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
        encrypted = WebPusher(info).encode(body, "aes128gcm")["body"]
        parts = urlsplit(sub.endpoint)
        now = time.time()
        claims = {"sub": self._subject, "aud": f"{parts.scheme}://{parts.netloc}", "exp": int(now) + VAPID_EXP_S}
        headers = {
            **self._vapid.sign(claims),
            "TTL": str(self._ttl_s),
            "Urgency": "high",
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
        }
        try:
            async with self._session.post(
                sub.endpoint,
                data=encrypted,
                headers=headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=self._timeout_s),
            ) as resp:
                detail = (await resp.text(errors="replace"))[:200]
                return classify(resp.status, retry_after_s(resp.headers.get("Retry-After"), time.time()), detail)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            _log.debug("WEBPUSH_NETWORK %s %s", endpoint_label(sub.endpoint), type(exc).__name__)
            return PushResult(PushOutcome.RETRY, None, detail=f"{type(exc).__name__}: {exc}"[:200])
