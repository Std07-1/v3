"""Пристрої, що можуть ставити сповіщення (ADR-0114 §4.5): прив'язка через бота і cookie пристрою.

Потік «код назад» (власник 15.10, після security review: посилання саме по собі не прив'язує):
1. сторінка просить прив'язку → отримує код посилання (для `t.me/<бот>?start=<код>`) і сирий токен пристрою (cookie,
   поки нічого не відкриває);
2. бот, отримавши `/start <код>` з дозволеного чату, видає 6 цифр підтвердження для підписника `tg:<chat_id>`;
3. цифри вводять на сторінці, що почала прив'язку: прив'язується лише браузер із тим самим токеном.
Тож чуже посилання, відкрите власником, не прив'яже браузер нападника — без 6 цифр із Telegram власника нічого не буде.
На цифри — `confirm_attempts` спроб, далі прив'язка анулюється.

На диску (`devices.json`, атомарний запис) — лише sha256 токена; сирий токен знає тільки браузер. Незавершені
прив'язки живуть лише в пам'яті (`pair_code_ttl_s`): рестарт процесу = натиснути «Під'єднати» ще раз.
"""

from __future__ import annotations

import dataclasses
import enum
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
CONFIRM_CODE_DIGITS = 6  # рішення власника 15.10: «бот дає 6 цифр»; із `confirm_attempts` спроб угадати ~5·10⁻⁶


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


class ConfirmOutcome(str, enum.Enum):
    BOUND = "bound"
    WRONG_CODE = "wrong_code"  # спроби ще є
    LOCKED = "locked"  # спроби вичерпано — прив'язку анульовано
    AWAITING_BOT = "awaiting_bot"  # посилання в боті ще не відкривали — цифр нема
    NO_PAIRING = "no_pairing"  # нема або минула


@dataclasses.dataclass(frozen=True)
class ConfirmResult:
    outcome: ConfirmOutcome
    device: Optional[Device] = None
    attempts_left: int = 0


@dataclasses.dataclass(frozen=True)
class _Pairing:
    token_sha256: str
    label: str
    expires_ms: int
    subscriber_id: Optional[str] = None  # хто відкрив посилання в боті
    confirm_code: Optional[str] = None  # 6 цифр, які бот показав
    attempts_left: int = 0


def _six_digits() -> str:
    return f"{secrets.randbelow(10**CONFIRM_CODE_DIGITS):0{CONFIRM_CODE_DIGITS}d}"


class DeviceRegistry:
    def __init__(
        self,
        path: Path,
        *,
        confirm_attempts: int,
        max_pending: int,
        new_secret: Callable[[int], str] = secrets.token_urlsafe,
        new_confirm_code: Callable[[], str] = _six_digits,
    ) -> None:
        if confirm_attempts < 1 or max_pending < 1:
            raise ValueError(f"confirm_attempts={confirm_attempts}, max_pending={max_pending}: очікується ≥ 1")
        self.path = path
        self.devices: Dict[str, Device] = {}
        self._pairings: Dict[str, _Pairing] = {}  # код посилання → незавершена прив'язка
        self._confirm_attempts, self._max_pending = confirm_attempts, max_pending
        self._new_secret, self._new_confirm_code = new_secret, new_confirm_code

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
        """Сторінка: → (код посилання для t.me, сирий токен для cookie). Понад `max_pending` — найстаріша незавершена
        відкидається (гучно): потік запитів прив'язки не роздує пам'ять."""
        self._prune(now_ms)
        while len(self._pairings) >= self._max_pending:
            oldest = min(self._pairings, key=lambda c: self._pairings[c].expires_ms)
            del self._pairings[oldest]
            _log.warning("ALERTS_PAIRING_EVICTED pending=%d max=%d", len(self._pairings), self._max_pending)
        link_code, raw_token = self._new_secret(12), self._new_secret(32)
        self._pairings[link_code] = _Pairing(token_digest(raw_token), label, now_ms + ttl_ms)
        return link_code, raw_token

    def issue_confirm_code(self, link_code: str, subscriber_id: str, *, now_ms: int, ttl_ms: int) -> Optional[str]:
        """Бот: дозволений чат відкрив посилання → 6 цифр для сторінки. Повторне відкриття дає нові цифри (старі
        недійсні) і повний запас спроб. Невідомий чи прострочений код посилання — None."""
        self._prune(now_ms)
        pairing = self._pairings.get(link_code)
        if pairing is None:
            return None
        code = self._new_confirm_code()
        self._pairings[link_code] = dataclasses.replace(
            pairing,
            subscriber_id=subscriber_id,
            confirm_code=code,
            attempts_left=self._confirm_attempts,
            expires_ms=now_ms + ttl_ms,
        )
        return code

    def confirm(self, raw_token: str, code: str, *, now_ms: int) -> ConfirmResult:
        """Сторінка: 6 цифр з бота → прив'язка САМЕ цього браузера (за його токеном). Хибні цифри витрачають спробу;
        останньої — прив'язка анулюється. Успіх комітить реєстр."""
        self._prune(now_ms)
        digest = token_digest(raw_token)
        found = next((c for c, p in self._pairings.items() if secrets.compare_digest(p.token_sha256, digest)), None)
        if found is None:
            return ConfirmResult(ConfirmOutcome.NO_PAIRING)
        pairing = self._pairings[found]
        if pairing.confirm_code is None or pairing.subscriber_id is None:
            return ConfirmResult(ConfirmOutcome.AWAITING_BOT)
        well_formed = len(code) == CONFIRM_CODE_DIGITS and code.isascii() and code.isdigit()
        if not (well_formed and secrets.compare_digest(pairing.confirm_code, code)):
            left = pairing.attempts_left - 1
            if left <= 0:
                del self._pairings[found]
                _log.warning("ALERTS_PAIRING_LOCKED subscriber=%s label=%s", pairing.subscriber_id, pairing.label)
                return ConfirmResult(ConfirmOutcome.LOCKED)
            self._pairings[found] = dataclasses.replace(pairing, attempts_left=left)
            return ConfirmResult(ConfirmOutcome.WRONG_CODE, attempts_left=left)
        del self._pairings[found]
        return ConfirmResult(ConfirmOutcome.BOUND, device=self._add_device(pairing, pairing.subscriber_id, now_ms))

    def _add_device(self, pairing: _Pairing, subscriber_id: str, now_ms: int) -> Device:
        device_id = self._new_secret(4)
        while device_id in self.devices:  # збіг короткого id не має перезаписати чужий пристрій
            device_id = self._new_secret(4)
        device = Device(device_id, pairing.token_sha256, subscriber_id, pairing.label, now_ms)
        self.devices[device.device_id] = device
        self.commit()
        _log.info("ALERTS_DEVICE_BOUND device=%s subscriber=%s label=%s", device.device_id, subscriber_id, device.label)
        return device

    def _prune(self, now_ms: int) -> None:
        self._pairings = {c: p for c, p in self._pairings.items() if p.expires_ms > now_ms}

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
