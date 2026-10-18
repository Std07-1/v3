"""ADR-0114 Rev 2 S10b: доставка черги пушем (runtime/alerts/push_delivery.py) і «не доставлено» в черзі (store).

Push-сервіс — фейковий відправник із тим самим інтерфейсом, що WebPushSender (справжній HTTPS перевірено в
tests/test_alerts_webpush.py); пристрої, двигун і сховище — справжні, на диску tmp_path.
Run: python -m pytest tests/test_alerts_push_delivery.py -q
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import os
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

import pytest

from core.alerts.crossing import on_tick
from core.alerts.types import AlertSide, PriceAlert
from runtime.alerts.devices import DEVICES_FILE, DeviceRegistry, WebLimits
from runtime.alerts.engine import AlertEngine
from runtime.alerts.messages import push_payload
from runtime.alerts.push_delivery import DEGRADED_PUSH, PushDelivery
from runtime.alerts.store import STATE_FILE, AlertStore
from runtime.alerts.webpush import PushOutcome, PushResult, PushSubscription

T0 = 1_792_086_371_000
TZ = ZoneInfo("Europe/Prague")
DIGITS = {"XAU/USD": 2}
SITE = "https://aione-smc.com"
TTL_S = 21600

OK = PushResult(PushOutcome.DELIVERED, 201)


def _push(name: str) -> PushSubscription:
    point = base64.urlsafe_b64encode(b"\x04" + os.urandom(64)).rstrip(b"=").decode()
    auth = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
    return PushSubscription(f"https://fcm.googleapis.com/fcm/send/{name}", point, auth)


class FakeSender:
    """Відповіді — за endpoint; без сценарію — DELIVERED."""

    def __init__(self) -> None:
        self.plan: Dict[str, List[PushResult]] = {}
        self.sent: List[Tuple[str, dict]] = []

    async def send(self, sub: PushSubscription, payload) -> PushResult:
        self.sent.append((sub.endpoint.rsplit("/", 1)[1], dict(payload)))
        queue = self.plan.get(sub.endpoint.rsplit("/", 1)[1])
        return queue.pop(0) if queue else OK


class World:
    def __init__(self, tmp_path) -> None:
        counter = itertools.count(1)
        self.registry = DeviceRegistry(
            tmp_path / DEVICES_FILE,
            confirm_attempts=3,
            max_pending=4,
            new_secret=lambda n: f"s{next(counter)}x{n}",
            web_limits=WebLimits(100, 100),
        )
        self.now = [T0]
        self.engine = AlertEngine(AlertStore(tmp_path / STATE_FILE), gap_after_ms=900_000, clock_ms=lambda: self.now[0])
        self.events: List[Tuple[str, str]] = []
        self.engine.listeners.append(lambda name, alert: self.events.append((name, alert.alert_id)))
        self.sender = FakeSender()
        self.cookies: Dict[str, str] = {}
        self.devices = {}
        for name in ("A", "B"):
            device, raw = self.registry.subscribe_web(None, _push(name), name, ip=f"1.1.1.{len(self.devices)}", now_ms=T0)
            self.devices[name], self.cookies[name] = device, raw
        self.delivery = PushDelivery(
            self.engine,
            self.registry,
            self.sender,  # type: ignore[arg-type]
            digits=DIGITS,
            tz=TZ,
            site_url=SITE,
            clock_ms=lambda: self.now[0],
            ttl_s=TTL_S,
            retry_first_s=2.0,
            retry_max_s=8.0,
        )

    def fire(self, alert_id: str, device: str, *, price: float = 4380.25, at_ms: int = T0) -> PriceAlert:
        alert = PriceAlert(
            alert_id=alert_id,
            drawing_id=f"d-{alert_id}",
            subscriber_id=self.devices[device].subscriber_id,
            symbol="XAU/USD",
            level=4380.0,
            tf_label="M15",
            active_from_ms=at_ms,
            created_at_ms=at_ms,
            side=AlertSide.BELOW,
        )
        fired = on_tick(alert, price, at_ms, prev_tick_ts_ms=None, gap_after_ms=900_000)
        self.engine.put_alert(fired)
        self.engine.store.enqueue(fired, now_ms=at_ms + len(self.engine.store.outbox))
        return fired


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def _key(alert: PriceAlert) -> Tuple[str, int]:
    return alert.alert_id, alert.version


@pytest.mark.asyncio
async def test_delivered_push_acks_entry_with_payload_for_os_notification(world):
    fired = world.fire("a1", "A")
    assert await world.delivery.deliver_once() == 1
    (device, payload), = world.sender.sent
    assert device == "A" and payload == push_payload(fired, digits=DIGITS, tz=TZ, site_url=SITE)
    assert payload["title"] == "XAU/USD 4380.00 — перетнуто знизу вгору" and payload["tag"] == "a1"
    assert payload["url"] == "https://aione-smc.com/?symbol=XAU/USD&tf=M15"
    assert world.engine.store.outbox == {} and ("delivered", "a1") in world.events
    assert AlertStore.load(world.engine.store.path, now_ms=T0).outbox == {}


@pytest.mark.asyncio
async def test_retry_pauses_only_that_device_keeps_its_order_and_doubles(world):
    a1, a2 = world.fire("a1", "A"), world.fire("a2", "A", price=4381.0)
    world.fire("b1", "B")
    world.sender.plan["A"] = [PushResult(PushOutcome.RETRY, 503, detail="down"), PushResult(PushOutcome.RETRY, 503)]
    assert await world.delivery.deliver_once() == 1  # B пішов, A — на паузі, a2 не обігнав a1
    assert [d for d, _ in world.sender.sent] == ["A", "B"]
    assert set(world.engine.store.outbox) == {_key(a1), _key(a2)} and DEGRADED_PUSH in world.engine.degraded
    assert world.engine.store.outbox[_key(a1)].attempts == 1
    world.now[0] = T0 + 1_999
    assert await world.delivery.deliver_once() == 0 and len(world.sender.sent) == 2  # пауза 2 с ще йде
    world.now[0] = T0 + 2_000
    assert await world.delivery.deliver_once() == 0  # друга невдача — пауза 4 с
    world.now[0] = T0 + 2_000 + 3_999
    assert await world.delivery.deliver_once() == 0 and len(world.sender.sent) == 3
    world.now[0] = T0 + 6_000
    assert await world.delivery.deliver_once() == 2
    assert [p["tag"] for d, p in world.sender.sent if d == "A"][-2:] == ["a1", "a2"]
    assert DEGRADED_PUSH not in world.engine.degraded and world.engine.store.outbox == {}


@pytest.mark.asyncio
async def test_success_resets_backoff_so_next_failure_starts_from_first_pause(world):
    world.fire("a1", "A")
    world.sender.plan["A"] = [PushResult(PushOutcome.RETRY, 503), PushResult(PushOutcome.RETRY, 503)]
    await world.delivery.deliver_once()  # пауза 2 с
    world.now[0] = T0 + 2_000
    await world.delivery.deliver_once()  # друга — 4 с
    world.now[0] = T0 + 6_000
    assert await world.delivery.deliver_once() == 1  # доставлено — пауза скинута
    world.fire("a2", "A", at_ms=T0 + 6_000)
    world.sender.plan["A"] = [PushResult(PushOutcome.RETRY, 503)]
    await world.delivery.deliver_once()
    world.now[0] = T0 + 8_000  # нова невдача починає з 2 с, а не 8
    assert await world.delivery.deliver_once() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("limit, peak", [(1, 1), (8, 2)])
async def test_concurrency_is_bounded_by_push_concurrency(world, monkeypatch, limit, peak):
    import runtime.alerts.push_delivery as push_delivery

    monkeypatch.setattr(push_delivery, "PUSH_CONCURRENCY", limit)
    world.fire("a1", "A")
    world.fire("b1", "B")
    running, seen = [0], []

    async def send(sub, payload):
        running[0] += 1
        seen.append(running[0])
        await asyncio.sleep(0.02)
        running[0] -= 1
        return OK

    world.sender.send = send  # type: ignore[method-assign]
    assert await world.delivery.deliver_once() == 2 and max(seen) == peak


@pytest.mark.asyncio
async def test_retry_after_from_service_is_honored(world):
    world.fire("a1", "A")
    world.sender.plan["A"] = [PushResult(PushOutcome.RETRY, 429, retry_after_s=30.0)]
    await world.delivery.deliver_once()
    world.now[0] = T0 + 29_999
    assert await world.delivery.deliver_once() == 0 and len(world.sender.sent) == 1
    world.now[0] = T0 + 30_000
    assert await world.delivery.deliver_once() == 1


@pytest.mark.asyncio
async def test_gone_marks_device_and_keeps_entry_pending_until_resubscribe(world):
    a1 = world.fire("a1", "A")
    world.sender.plan["A"] = [PushResult(PushOutcome.GONE, 410)]
    assert await world.delivery.deliver_once() == 0
    assert world.registry.devices[world.devices["A"].device_id].push_gone is True
    assert world.engine.store.outbox[_key(a1)].failed is None  # «чекає», не «не доставлено»
    world.now[0] = T0 + 60_000
    assert await world.delivery.deliver_once() == 0 and len(world.sender.sent) == 1  # без нової підписки — нікуди
    world.registry.subscribe_web(world.cookies["A"], _push("A"), "A", ip="1.1.1.0", now_ms=world.now[0])
    assert await world.delivery.deliver_once() == 1 and world.engine.store.outbox == {}


@pytest.mark.asyncio
async def test_rejected_entry_becomes_undelivered_and_the_queue_moves_on(world):
    a1, a2 = world.fire("a1", "A"), world.fire("a2", "A", price=4381.0)
    world.sender.plan["A"] = [PushResult(PushOutcome.REJECTED, 400, detail="bad header")]
    assert await world.delivery.deliver_once() == 1  # a1 відмовлено, a2 пішов того ж проходу
    entry = world.engine.store.outbox[_key(a1)]
    assert "400" in entry.failed and "bad header" in entry.failed and _key(a2) not in world.engine.store.outbox
    assert ("undelivered", "a1") in world.events
    assert await world.delivery.deliver_once() == 0 and len(world.sender.sent) == 2  # «не доставлено» не повторюється
    assert AlertStore.load(world.engine.store.path, now_ms=T0).outbox[_key(a1)].failed == entry.failed


@pytest.mark.asyncio
async def test_stale_entry_is_undelivered_without_sending(world):
    old = world.fire("a1", "A", at_ms=T0 - TTL_S * 1000 - 1)
    assert await world.delivery.deliver_once() == 0 and world.sender.sent == []
    assert "застаріло" in world.engine.store.outbox[_key(old)].failed


@pytest.mark.asyncio
async def test_entry_of_switched_off_device_is_undelivered(world):
    a1 = world.fire("a1", "A")
    device = world.devices["A"]
    world.registry.revoke(device.device_id, device.subscriber_id)
    assert await world.delivery.deliver_once() == 0 and world.sender.sent == []
    assert "вимкнено" in world.engine.store.outbox[_key(a1)].failed


@pytest.mark.asyncio
async def test_devices_are_served_concurrently(world):
    """A чекає, доки піде B: послідовний прохід застряг би на A (тайм-аут), паралельний — ні."""
    world.fire("a1", "A")
    world.fire("b1", "B")
    b_sent = asyncio.Event()

    async def send(sub, payload):
        if sub.endpoint.endswith("/A"):
            await asyncio.wait_for(b_sent.wait(), 2.0)
        else:
            b_sent.set()
        return OK

    world.sender.send = send  # type: ignore[method-assign]
    assert await asyncio.wait_for(world.delivery.deliver_once(), 3.0) == 2


# ── «не доставлено» у сховищі ─────────────────────────────────────────────


def test_failed_mark_survives_reload_and_is_dropped_when_line_removed_or_rearmed(world):
    a1 = world.fire("a1", "A")
    world.fire("a2", "A")
    world.engine.fail_delivery(_key(a1), "відмова")
    world.engine.fail_delivery(_key(a1), "вдруге")  # уже позначене — без змін
    assert world.engine.store.outbox[_key(a1)].failed == "відмова" and ("undelivered", "a1") in world.events
    world.engine.remove_alert("a2")  # «чекає» лишається навіть без лінії
    assert set(world.engine.store.outbox) == {_key(a1), ("a2", 1)}
    rearmed = world.engine.store.alerts["a1"]
    import dataclasses

    world.engine.put_alert(dataclasses.replace(rearmed, version=2, state=rearmed.state))
    assert _key(a1) not in world.engine.store.outbox  # нова версія — старе «не доставлено» прибрано


def test_prune_forgets_idle_devices_without_armed_alerts_only(world):
    from runtime.alerts.push_delivery import prune_idle_devices

    day = 24 * 3_600_000
    world.fire("a1", "A")  # у A лише спрацьоване — після 90 днів тиші пристрій забувається
    armed = PriceAlert(
        alert_id="b1",
        drawing_id="d-b1",
        subscriber_id=world.devices["B"].subscriber_id,
        symbol="XAU/USD",
        level=4500.0,
        tf_label="M15",
        active_from_ms=T0,
        created_at_ms=T0,
        side=AlertSide.BELOW,
    )
    world.engine.put_alert(armed)  # у B — взведене: чекає перетину, хоч би скільки тиші
    now = T0 + 90 * day
    assert prune_idle_devices(world.engine, world.registry, now_ms=now, idle_ms=90 * day) == 1
    assert world.devices["A"].device_id not in world.registry.devices and "a1" not in world.engine.store.alerts
    assert world.devices["B"].device_id in world.registry.devices and "b1" in world.engine.store.alerts
    assert prune_idle_devices(world.engine, world.registry, now_ms=now - 1, idle_ms=90 * day) == 0


@pytest.mark.asyncio
async def test_forgotten_device_leaves_no_orphan_in_queue(world):
    """«Вимкнути на цьому пристрої» зі спрацюванням у черзі: доставка позначає «не доставлено» і одразу прибирає —
    лінії вже нема, показувати нікому."""
    from runtime.alerts.push_delivery import forget_device

    world.fire("a1", "A")
    assert forget_device(world.engine, world.registry, world.devices["A"]) == 1
    assert await world.delivery.deliver_once() == 0 and world.sender.sent == []
    assert world.engine.store.outbox == {} and ("undelivered", "a1") in world.events


def test_removing_line_drops_its_undelivered_mark(world):
    a1 = world.fire("a1", "A")
    world.engine.fail_delivery(_key(a1), "відмова")
    world.engine.remove_alert("a1")
    assert world.engine.store.outbox == {}
    assert AlertStore.load(world.engine.store.path, now_ms=T0).outbox == {}
