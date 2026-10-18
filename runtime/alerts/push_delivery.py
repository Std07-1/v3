"""Доставка черги пушем у браузер (ADR-0114 Rev 2 §2a): «щонайменше раз», порядок — у межах пристрою.

Запис черги належить підписнику `web:<device_id>`; знімається лише після DELIVERED (прийнято push-сервісом).
- RETRY (429, 5xx, мережа): пауза лише цьому пристрою (`Retry-After`, інакше `retry_first_s` → ×2 до `retry_max_s`);
  його молодші записи чекають, інші пристрої йдуть далі. Прапорець `push_unreachable`, поки хоч один пристрій на паузі.
- GONE (404/410): пристрій позначається `push_gone`; записи лишаються «чекає» — після нової підписки підуть.
- REJECTED (формат, підпис, перенаправлення, endpoint поза списком, кривий ключ): запис — «не доставлено» з причиною;
  повтор нічого не змінить, а черга пристрою не має стояти.
- Старіше за `ttl_s` від спрацювання — «не доставлено: застаріло»: новина про перетин уже не новина (і push-сервіс
  з тим самим TTL її б однаково відкинув).
- Пристрою нема (вимкнули на ньому сповіщення) або підписник не web — «не доставлено».
Пристрої — паралельно (не більше `PUSH_CONCURRENCY`), записи одного пристрою — по черзі.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Callable, Dict, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

from core.alerts.types import AlertState
from runtime.alerts.devices import WEB_PREFIX, Device, DeviceRegistry
from runtime.alerts.engine import AlertEngine
from runtime.alerts.messages import push_payload
from runtime.alerts.store import OutboxEntry
from runtime.alerts.webpush import PushOutcome, WebPushSender

_log = logging.getLogger(__name__)

DEGRADED_PUSH = "push_unreachable"
PUSH_CONCURRENCY = 8  # пристроїв одночасно: зависле push-сервісу не тримає решту, а сокетів — обмежено

_Key = Tuple[str, int]


@dataclasses.dataclass(frozen=True)
class _Backoff:
    next_try_ms: int
    delay_s: float
    reason: str


class PushDelivery:
    def __init__(
        self,
        engine: AlertEngine,
        registry: DeviceRegistry,
        sender: WebPushSender,
        *,
        digits: Mapping[str, int],
        tz: ZoneInfo,
        site_url: str,
        clock_ms: Callable[[], int],
        ttl_s: int,
        retry_first_s: float,
        retry_max_s: float,
    ) -> None:
        self._engine, self._registry, self._sender, self._clock_ms = engine, registry, sender, clock_ms
        self._format = dict(digits=digits, tz=tz, site_url=site_url)
        self._ttl_ms, self._retry_first_s, self._retry_max_s = ttl_s * 1000, retry_first_s, retry_max_s
        self._backoff: Dict[str, _Backoff] = {}  # device_id → пауза після RETRY

    async def deliver_once(self) -> int:
        """Один прохід черги; повертає кількість доставлених."""
        now = self._clock_ms()
        queues: Dict[str, List[Tuple[_Key, OutboxEntry]]] = {}
        for key, entry in sorted(self._engine.store.outbox.items(), key=lambda item: item[1].enqueued_at_ms):
            if entry.failed is not None:
                continue
            if now - entry.alert.fired.at_ms > self._ttl_ms:  # type: ignore[union-attr]  # у черзі — лише спрацьовані
                self._engine.fail_delivery(key, "застаріло — не доставлено вчасно")
                continue
            queues.setdefault(entry.alert.subscriber_id, []).append((key, entry))
        gate = asyncio.Semaphore(PUSH_CONCURRENCY)

        async def one(subscriber_id: str, items: List[Tuple[_Key, OutboxEntry]]) -> int:
            async with gate:
                return await self._deliver_device(subscriber_id, items, now)

        delivered = sum(await asyncio.gather(*(one(s, items) for s, items in queues.items())))
        self._backoff = {d: b for d, b in self._backoff.items() if d in self._registry.devices}  # вимкнені — геть
        paused = [b for b in self._backoff.values() if b.next_try_ms > now]
        self._engine.set_degraded(DEGRADED_PUSH, paused[-1].reason if paused else None)
        return delivered

    async def _deliver_device(self, subscriber_id: str, items: List[Tuple[_Key, OutboxEntry]], now: int) -> int:
        device = self._device_of(subscriber_id)
        if device is None or device.push is None:
            for key, _ in items:
                self._engine.fail_delivery(key, "пристрій вимкнено — нікуди доставити")
            return 0
        backoff = self._backoff.get(device.device_id)
        if device.push_gone or (backoff is not None and backoff.next_try_ms > now):
            return 0  # чекає нової підписки чи кінця паузи
        delivered = 0
        for key, entry in items:
            result = await self._sender.send(device.push, push_payload(entry.alert, **self._format))
            if result.outcome is PushOutcome.DELIVERED:
                self._backoff.pop(device.device_id, None)
                self._engine.ack_delivered(key)
                delivered += 1
                continue
            if result.outcome is PushOutcome.REJECTED:
                _log.error(
                    "ALERTS_PUSH_REJECTED device=%s status=%s detail=%s", device.device_id, result.status, result.detail
                )
                self._engine.fail_delivery(key, f"push-сервіс відмовив ({result.status}): {result.detail}"[:300])
                continue
            self._engine.note_attempt(key)
            if result.outcome is PushOutcome.GONE:
                self._registry.mark_push_gone(device.device_id)
                break
            previous = self._backoff.get(device.device_id)
            delay = result.retry_after_s or (
                self._retry_first_s if previous is None else min(previous.delay_s * 2, self._retry_max_s)
            )
            reason = f"{result.status or 'мережа'}: {result.detail}"[:200]
            self._backoff[device.device_id] = _Backoff(now + int(delay * 1000), delay, reason)
            break
        return delivered

    def _device_of(self, subscriber_id: str) -> Optional[Device]:
        if not subscriber_id.startswith(WEB_PREFIX):
            return None
        return self._registry.devices.get(subscriber_id[len(WEB_PREFIX) :])

    async def run(self, *, poll_s: float) -> None:
        while True:
            await self.deliver_once()
            await asyncio.sleep(poll_s)


def forget_device(engine: AlertEngine, registry: DeviceRegistry, device: Device) -> int:
    """«Вимкнути на цьому пристрої» і прибирання неактивних: сповіщення пристрою, сам пристрій (і його підписка) —
    геть. Вже спрацьоване в черзі доставка позначить «не доставлено» (пристрою нема). → скільки сповіщень прибрано."""
    mine = [a.alert_id for a in engine.store.alerts.values() if a.subscriber_id == device.subscriber_id]
    for alert_id in mine:
        engine.remove_alert(alert_id)
    registry.revoke(device.device_id, device.subscriber_id)
    return len(mine)


def prune_idle_devices(engine: AlertEngine, registry: DeviceRegistry, *, now_ms: int, idle_ms: int) -> int:
    """Web-пристрій без запитів `idle_ms` і без взведених сповіщень — забути (сторінки, що його показувала, давно
    нема; сайт відкритий, тож реєстр не має рости вічно). Заодно last_seen — на диск. → скільки прибрано."""
    busy = {a.subscriber_id for a in engine.store.alerts.values() if a.state is AlertState.ARMED}
    idle = registry.idle_web_devices(now_ms=now_ms, idle_ms=idle_ms, busy=busy)
    for device in idle:
        dropped = forget_device(engine, registry, device)
        _log.info("ALERTS_DEVICE_PRUNED device=%s alerts=%d label=%s", device.device_id, dropped, device.label)
    registry.commit_if_dirty()
    return len(idle)


async def run_device_pruning(
    engine: AlertEngine, registry: DeviceRegistry, *, idle_ms: int, every_s: float, clock_ms: Callable[[], int]
) -> None:
    while True:
        prune_idle_devices(engine, registry, now_ms=clock_ms(), idle_ms=idle_ms)
        await asyncio.sleep(every_s)
