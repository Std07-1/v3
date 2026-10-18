"""Пристрої, що можуть ставити сповіщення: браузер із пуш-підпискою (ADR-0114 Rev 2 §2a) або прив'язка ботом (§4.5).

**Браузер (Rev 2, кожен відвідувач):** сторінка дала підписку пушу → пристрій `web:<device_id>` і сирий токен для
cookie; та сама сторінка (її cookie) з новою підпискою — оновлення, не новий пристрій. Сайт відкритий, тож нових
пристроїв з однієї IP — не більше `per_ip_per_hour`, усього — не більше `max_devices` (чужих не витісняємо). `last_seen_ms`
— з точністю до години (для прибирання неактивних, запис на диск — разом із наступною зміною).

Потік «код назад» для Telegram (власник 15.10, після security review: посилання саме по собі не прив'язує):
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
from typing import Any, Callable, Collection, Dict, List, Optional, Tuple

from core.alerts.types import validate_epoch_ms
from runtime.alerts.store import write_json_atomic
from runtime.alerts.webpush import PushSubscription, endpoint_label

_log = logging.getLogger(__name__)

DEVICES_FILE = "devices.json"
DEVICES_SCHEMA = 2  # 1 — Rev 1 (лише Telegram, без last_seen/push): читається, пишеться вже 2
SUBSCRIBER_PREFIX = "tg:"
WEB_PREFIX = "web:"
CONFIRM_CODE_DIGITS = 6  # рішення власника 15.10: «бот дає 6 цифр»; із `confirm_attempts` спроб угадати ~5·10⁻⁶
TOUCH_RESOLUTION_MS = 3_600_000  # прибирання неактивних — за днями; частіше оновлювати last_seen = зайві записи
_IP_WINDOW_MS = 3_600_000  # ліміт нових пристроїв — «за годину» (WebLimits.per_ip_per_hour)
_SCHEMA1_FIELDS = frozenset({"device_id", "token_sha256", "subscriber_id", "label", "created_ms"})


def subscriber_for_chat(chat_id: int) -> str:
    return f"{SUBSCRIBER_PREFIX}{chat_id}"


def chat_of_subscriber(subscriber_id: str) -> Optional[int]:
    """`tg:<chat_id>` → chat_id; інший формат — None (викликач робить це гучним)."""
    head, _, tail = subscriber_id.partition(SUBSCRIBER_PREFIX)
    if head or not tail.lstrip("-").isdigit():
        return None
    return int(tail)


def subscriber_for_web(device_id: str) -> str:
    return f"{WEB_PREFIX}{device_id}"


def is_web_subscriber(subscriber_id: str) -> bool:
    return subscriber_id.startswith(WEB_PREFIX)


def token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class Device:
    device_id: str  # коротке, показується в /devices і в /revoke
    token_sha256: str
    subscriber_id: str
    label: str  # клас пристрою з User-Agent («Android · Chrome»)
    created_ms: int
    last_seen_ms: int
    push: Optional[PushSubscription] = None  # лише web:; Telegram-пристрій — None
    push_gone: bool = False  # push-сервіс сказав 404/410 — до нової підписки не доставляємо

    def __post_init__(self) -> None:
        for name in ("device_id", "token_sha256", "subscriber_id", "label"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"device.{name}: порожній або не рядок")
        if not isinstance(self.push_gone, bool):
            raise ValueError("device.push_gone: не bool")
        if is_web_subscriber(self.subscriber_id):
            if self.subscriber_id != subscriber_for_web(self.device_id) or not isinstance(self.push, PushSubscription):
                raise ValueError(f"device {self.device_id}: web-пристрій — це web:<device_id> з підпискою пушу")
        elif chat_of_subscriber(self.subscriber_id) is None or self.push is not None or self.push_gone:
            raise ValueError(f"device.subscriber_id: очікується tg:<chat_id> або web:<id>, отримано {self.subscriber_id!r}")
        validate_epoch_ms("device.created_ms", self.created_ms)
        validate_epoch_ms("device.last_seen_ms", self.last_seen_ms)


@dataclasses.dataclass(frozen=True)
class WebLimits:
    max_devices: int  # усього пристроїв; переповнення — відмова новим, чужих не витісняємо
    per_ip_per_hour: int  # нових пристроїв з однієї IP за годину


class DeviceLimitError(ValueError):
    """Новий пристрій не створено: `code` — devices_full / devices_per_ip / web_disabled (для відповіді API)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
        web_limits: Optional[WebLimits] = None,  # None — підписка браузера вимкнена (Rev 1, лише Telegram)
    ) -> None:
        if confirm_attempts < 1 or max_pending < 1:
            raise ValueError(f"confirm_attempts={confirm_attempts}, max_pending={max_pending}: очікується ≥ 1")
        self.path = path
        self.devices: Dict[str, Device] = {}
        self._by_digest: Dict[str, str] = {}  # sha256 токена → device_id (authenticate за O(1))
        self._ip_log: Dict[str, List[int]] = {}  # IP → час створення пристроїв за останню годину (лише в пам'яті)
        self._web_limits = web_limits
        self._dirty = False  # last_seen змінено лише в пам'яті
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
        schema = doc.get("schema") if isinstance(doc, dict) else None
        if schema not in (1, DEVICES_SCHEMA) or not isinstance(doc.get("devices"), list):
            raise ValueError(f"{path}: очікується schema 1 або {DEVICES_SCHEMA} і devices[]")
        fields = _SCHEMA1_FIELDS if schema == 1 else frozenset(f.name for f in dataclasses.fields(Device))
        for raw in doc["devices"]:
            if not isinstance(raw, dict) or raw.keys() != fields:
                raise ValueError(f"{path}: запис пристрою має містити рівно {sorted(fields)}")
            registry._put(_device_from_dict(raw))
        return registry

    def commit(self) -> None:
        devices = [dataclasses.asdict(d) for d in self.devices.values()]
        write_json_atomic(self.path, {"schema": DEVICES_SCHEMA, "devices": devices})
        self._dirty = False

    def commit_if_dirty(self) -> None:
        """Періодично (цикл прибирання): лише last_seen змінено — записати."""
        if self._dirty:
            self.commit()

    def _put(self, device: Device) -> None:
        old = self.devices.get(device.device_id)
        if old is not None:
            self._by_digest.pop(old.token_sha256, None)
        self.devices[device.device_id] = device
        self._by_digest[device.token_sha256] = device.device_id

    def _new_device_id(self) -> str:
        device_id = self._new_secret(4)
        while device_id in self.devices:  # збіг короткого id не має перезаписати чужий пристрій
            device_id = self._new_secret(4)
        return device_id

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
        device = Device(self._new_device_id(), pairing.token_sha256, subscriber_id, pairing.label, now_ms, now_ms)
        self._put(device)
        self.commit()
        _log.info("ALERTS_DEVICE_BOUND device=%s subscriber=%s label=%s", device.device_id, subscriber_id, device.label)
        return device

    def _prune(self, now_ms: int) -> None:
        self._pairings = {c: p for c, p in self._pairings.items() if p.expires_ms > now_ms}

    # ── доступ ───────────────────────────────────────────────────────────

    def authenticate(self, raw_token: str) -> Optional[Device]:
        """Пошук за sha256 токена (словник): хеш сирого токена нападнику нічого не дає, а 5000 пристроїв — O(1)."""
        device_id = self._by_digest.get(token_digest(raw_token))
        return None if device_id is None else self.devices[device_id]

    def devices_of(self, subscriber_id: str) -> List[Device]:
        own = (d for d in self.devices.values() if d.subscriber_id == subscriber_id)
        return sorted(own, key=lambda d: d.created_ms)

    def revoke(self, device_id: str, subscriber_id: str) -> bool:
        """Лише свій пристрій. Комітить реєстр."""
        device = self.devices.get(device_id)
        if device is None or device.subscriber_id != subscriber_id:
            return False
        del self.devices[device_id]
        self._by_digest.pop(device.token_sha256, None)
        self.commit()
        _log.info("ALERTS_DEVICE_REVOKED device=%s subscriber=%s", device_id, subscriber_id)
        return True

    # ── браузер з пуш-підпискою (Rev 2) ──────────────────────────────────

    def subscribe_web(
        self, raw_token: Optional[str], push: PushSubscription, label: str, *, ip: str, now_ms: int
    ) -> Tuple[Device, Optional[str]]:
        """Підписка від сторінки. Її cookie веде на web-пристрій — оновлення підписки (і зняття push_gone), токен не
        змінюється (→ None). Інакше — новий пристрій і сирий токен для cookie; ліміти → `DeviceLimitError`. Комітить."""
        if self._web_limits is None:
            raise DeviceLimitError("web_disabled", "підписка браузера вимкнена")
        current = self.authenticate(raw_token) if raw_token else None
        if current is not None and is_web_subscriber(current.subscriber_id):
            updated = dataclasses.replace(current, push=push, push_gone=False, label=label, last_seen_ms=now_ms)
            self._put(updated)
            self.commit()
            _log.info("ALERTS_PUSH_RESUBSCRIBED device=%s push=%s", updated.device_id, endpoint_label(push.endpoint))
            return updated, None
        self._check_new_device(self._web_limits, ip, now_ms)
        raw = self._new_secret(32)
        device_id = self._new_device_id()
        device = Device(device_id, token_digest(raw), subscriber_for_web(device_id), label, now_ms, now_ms, push)
        self._put(device)
        self.commit()
        self._ip_log.setdefault(ip, []).append(now_ms)
        _log.info("ALERTS_DEVICE_SUBSCRIBED device=%s label=%s push=%s", device_id, label, endpoint_label(push.endpoint))
        return device, raw

    def _check_new_device(self, limits: WebLimits, ip: str, now_ms: int) -> None:
        cutoff = now_ms - _IP_WINDOW_MS
        self._ip_log = {k: kept for k, v in self._ip_log.items() if (kept := [t for t in v if t > cutoff])}
        if len(self._ip_log.get(ip, ())) >= limits.per_ip_per_hour:
            raise DeviceLimitError("devices_per_ip", f"з цієї IP уже {limits.per_ip_per_hour} нових пристроїв за годину")
        if len(self.devices) >= limits.max_devices:
            _log.warning("ALERTS_DEVICES_FULL devices=%d max=%d", len(self.devices), limits.max_devices)
            raise DeviceLimitError("devices_full", f"досягнуто {limits.max_devices} пристроїв")

    def mark_push_gone(self, device_id: str) -> bool:
        """Push-сервіс: підписки нема (404/410). До нової підписки цьому пристрою не доставляємо. Комітить."""
        device = self.devices.get(device_id)
        if device is None or device.push is None or device.push_gone:
            return False
        self._put(dataclasses.replace(device, push_gone=True))
        self.commit()
        _log.warning("ALERTS_PUSH_GONE device=%s push=%s", device_id, endpoint_label(device.push.endpoint))
        return True

    def touch(self, device: Device, *, now_ms: int) -> Device:
        """Запит від пристрою: last_seen з точністю до години, лише в пам'яті (`commit_if_dirty`)."""
        if now_ms - device.last_seen_ms < TOUCH_RESOLUTION_MS:
            return device
        touched = dataclasses.replace(device, last_seen_ms=now_ms)
        self._put(touched)
        self._dirty = True
        return touched

    def idle_web_devices(self, *, now_ms: int, idle_ms: int, busy: Collection[str]) -> List[Device]:
        """Кандидати на прибирання: web-пристрої без запитів `idle_ms` і без взведених сповіщень (не в `busy`)."""
        return [
            d
            for d in self.devices.values()
            if is_web_subscriber(d.subscriber_id) and d.subscriber_id not in busy and now_ms - d.last_seen_ms >= idle_ms
        ]


def _device_from_dict(raw: Dict[str, Any]) -> Device:
    """Запис схеми 1 (Telegram) отримує last_seen = created; схеми 2 — як є, підписка — з полів."""
    raw = dict(raw)
    raw.setdefault("last_seen_ms", raw.get("created_ms"))
    push = raw.get("push")
    if push is not None:
        if not isinstance(push, dict) or push.keys() != {"endpoint", "p256dh", "auth"}:
            raise ValueError(f"device {raw.get('device_id')}: push має містити рівно endpoint, p256dh, auth")
        raw["push"] = PushSubscription(**push)
    return Device(**raw)
