"""Пристрої, що можуть ставити сповіщення (ADR-0114 §4.5): прив'язка через бота і cookie пристрою.

Потік: сторінка просить прив'язку → отримує одноразовий код (для `t.me/<бот>?start=<код>`) і сирий токен пристрою
(cookie). Бот, отримавши `/start <код>` з дозволеного чату, прив'язує пристрій до підписника `tg:<chat_id>`; відтоді
cookie відкриває API. На диску (`devices.json`, атомарний запис) — лише sha256 токена; сирий токен знає тільки браузер.

Незавершені прив'язки живуть лише в пам'яті (10 хв): рестарт процесу = натиснути «Під'єднати» ще раз.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import secrets
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.alerts.types import validate_epoch_ms
from runtime.alerts.store import write_json_atomic

_log = logging.getLogger(__name__)

DEVICES_FILE = "devices.json"
DEVICES_SCHEMA = 1
SUBSCRIBER_PREFIX = "tg:"


def subscriber_for_chat(chat_id: int) -> str:
    return f"{SUBSCRIBER_PREFIX}{chat_id}"


def chat_of_subscriber(subscriber_id: str) -> Optional[int]:
    """`tg:<chat_id>` → chat_id; інший формат — None (викликач робить це гучним)."""
    head, _, tail = subscriber_id.partition(SUBSCRIBER_PREFIX)
    if head or not tail.lstrip("-").isdigit():
        return None
    return int(tail)


def token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class Device:
    device_id: str  # коротке, показується в /devices і в /revoke
    token_sha256: str
    subscriber_id: str
    label: str  # клас пристрою з User-Agent («Android · Chrome»)
    created_ms: int

    def __post_init__(self) -> None:
        for name in ("device_id", "token_sha256", "subscriber_id", "label"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"device.{name}: порожній або не рядок")
        if chat_of_subscriber(self.subscriber_id) is None:
            raise ValueError(f"device.subscriber_id: очікується tg:<chat_id>, отримано {self.subscriber_id!r}")
        validate_epoch_ms("device.created_ms", self.created_ms)


@dataclasses.dataclass(frozen=True)
class _Pairing:
    token_sha256: str
    label: str
    expires_ms: int


class DeviceRegistry:
    def __init__(self, path: Path, *, new_secret: Callable[[int], str] = secrets.token_urlsafe) -> None:
        self.path = path
        self.devices: Dict[str, Device] = {}
        self._pairings: Dict[str, _Pairing] = {}
        self._new_secret = new_secret

    @classmethod
    def load(cls, path: Path, **kw: Any) -> "DeviceRegistry":
        """Битий чи чужої схеми файл — виняток: процес не стартує (тихий порожній реєстр = відкликати всі пристрої)."""
        registry = cls(path, **kw)
        if not path.exists():
            return registry
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("schema") != DEVICES_SCHEMA or not isinstance(doc.get("devices"), list):
            raise ValueError(f"{path}: очікується schema={DEVICES_SCHEMA} і devices[]")
        fields = {f.name for f in dataclasses.fields(Device)}
        for raw in doc["devices"]:
            if not isinstance(raw, dict) or raw.keys() != fields:
                raise ValueError(f"{path}: запис пристрою має містити рівно {sorted(fields)}")
            device = Device(**raw)
            registry.devices[device.device_id] = device
        return registry

    def commit(self) -> None:
        devices = [dataclasses.asdict(d) for d in self.devices.values()]
        write_json_atomic(self.path, {"schema": DEVICES_SCHEMA, "devices": devices})

    # ── прив'язка ────────────────────────────────────────────────────────

    def start_pairing(self, label: str, *, now_ms: int, ttl_ms: int) -> Tuple[str, str]:
        """→ (код для t.me, сирий токен для cookie). Прострочені незавершені прибираються тут же."""
        self._pairings = {c: p for c, p in self._pairings.items() if p.expires_ms > now_ms}
        code, raw_token = self._new_secret(12), self._new_secret(32)
        self._pairings[code] = _Pairing(token_digest(raw_token), label, now_ms + ttl_ms)
        return code, raw_token

    def bind(self, code: str, subscriber_id: str, *, now_ms: int) -> Optional[Device]:
        """Одноразово: код зникає за першої спроби; прострочений чи невідомий — None. Комітить реєстр."""
        pairing = self._pairings.pop(code, None)
        if pairing is None or pairing.expires_ms <= now_ms:
            return None
        device_id = self._new_secret(4)
        while device_id in self.devices:  # збіг короткого id не має перезаписати чужий пристрій
            device_id = self._new_secret(4)
        device = Device(device_id, pairing.token_sha256, subscriber_id, pairing.label, now_ms)
        self.devices[device.device_id] = device
        self.commit()
        _log.info("ALERTS_DEVICE_BOUND device=%s subscriber=%s label=%s", device.device_id, subscriber_id, device.label)
        return device

    # ── доступ ───────────────────────────────────────────────────────────

    def authenticate(self, raw_token: str) -> Optional[Device]:
        digest = token_digest(raw_token)
        return next((d for d in self.devices.values() if secrets.compare_digest(d.token_sha256, digest)), None)

    def devices_of(self, subscriber_id: str) -> List[Device]:
        own = (d for d in self.devices.values() if d.subscriber_id == subscriber_id)
        return sorted(own, key=lambda d: d.created_ms)

    def revoke(self, device_id: str, subscriber_id: str) -> bool:
        """Лише свій пристрій. Комітить реєстр."""
        device = self.devices.get(device_id)
        if device is None or device.subscriber_id != subscriber_id:
            return False
        del self.devices[device_id]
        self.commit()
        _log.info("ALERTS_DEVICE_REVOKED device=%s subscriber=%s", device_id, subscriber_id)
        return True
