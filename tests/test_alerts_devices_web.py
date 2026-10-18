"""ADR-0114 Rev 2 S10a: пристрій-браузер у реєстрі (runtime/alerts/devices.py) — підписка, ліміти, push_gone,
last_seen, прибирання неактивних, читання схеми 1.

Run: python -m pytest tests/test_alerts_devices_web.py -q
"""

from __future__ import annotations

import base64
import itertools
import json
import os

import pytest

from runtime.alerts.devices import (
    DEVICES_FILE,
    DEVICES_SCHEMA,
    TOUCH_RESOLUTION_MS,
    Device,
    DeviceLimitError,
    DeviceRegistry,
    WebLimits,
    subscriber_for_chat,
    subscriber_for_web,
)
from runtime.alerts.webpush import PushSubscription

T0 = 1_792_086_371_000
HOUR = 3_600_000
DAY = 24 * HOUR


def _push(n: int = 1) -> PushSubscription:
    point = base64.urlsafe_b64encode(b"\x04" + os.urandom(64)).rstrip(b"=").decode()
    auth = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
    return PushSubscription(f"https://fcm.googleapis.com/fcm/send/dev{n}", point, auth)


def _registry(path, *, max_devices=100, per_ip=10, web=True) -> DeviceRegistry:
    counter = itertools.count(1)
    return DeviceRegistry(
        path,
        confirm_attempts=3,
        max_pending=4,
        new_secret=lambda n: f"s{next(counter)}x{n}",
        web_limits=WebLimits(max_devices, per_ip) if web else None,
    )


@pytest.fixture
def registry(tmp_path):
    return _registry(tmp_path / DEVICES_FILE)


def _reload(registry: DeviceRegistry) -> DeviceRegistry:
    return DeviceRegistry.load(registry.path, confirm_attempts=3, max_pending=4)


# ── підписка ─────────────────────────────────────────────────────────────


def test_subscribe_creates_web_device_with_cookie_token_persisted_as_hash(registry):
    push = _push()
    device, raw = registry.subscribe_web(None, push, "Android · Chrome", ip="1.1.1.1", now_ms=T0)
    assert raw is not None and device.subscriber_id == subscriber_for_web(device.device_id)
    assert (device.push, device.push_gone, device.created_ms, device.last_seen_ms) == (push, False, T0, T0)
    assert registry.authenticate(raw) == device
    on_disk = registry.path.read_text(encoding="utf-8")
    assert raw not in on_disk and json.loads(on_disk)["schema"] == DEVICES_SCHEMA
    assert _reload(registry).devices == registry.devices


def test_resubscribe_with_own_cookie_updates_same_device_and_clears_gone(registry):
    device, raw = registry.subscribe_web(None, _push(1), "Android · Chrome", ip="1.1.1.1", now_ms=T0)
    registry.mark_push_gone(device.device_id)
    new_push = _push(2)
    again, token = registry.subscribe_web(raw, new_push, "Android · Chrome 2", ip="1.1.1.1", now_ms=T0 + HOUR)
    assert token is None and again.device_id == device.device_id and len(registry.devices) == 1
    assert (again.push, again.push_gone, again.label, again.last_seen_ms) == (new_push, False, "Android · Chrome 2", T0 + HOUR)
    assert registry.authenticate(raw) == again and _reload(registry).devices == registry.devices


def test_unknown_or_telegram_cookie_gets_a_new_web_device(registry):
    first, _ = registry.subscribe_web("cookie-from-nowhere", _push(1), "ПК", ip="1.1.1.1", now_ms=T0)
    tg = Device("tg1", "h" * 64, subscriber_for_chat(42), "ПК", T0, T0)
    registry._put(tg)
    second, raw = registry.subscribe_web(None, _push(2), "ПК", ip="1.1.1.1", now_ms=T0)
    assert raw is not None and first.device_id != second.device_id and len(registry.devices) == 3


# ── ліміти ───────────────────────────────────────────────────────────────


def test_new_devices_per_ip_are_limited_per_hour_but_resubscribe_is_not(tmp_path):
    registry = _registry(tmp_path / DEVICES_FILE, per_ip=2)
    _, raw = registry.subscribe_web(None, _push(1), "a", ip="9.9.9.9", now_ms=T0)
    registry.subscribe_web(None, _push(2), "b", ip="9.9.9.9", now_ms=T0 + 1)
    with pytest.raises(DeviceLimitError) as err:
        registry.subscribe_web(None, _push(3), "c", ip="9.9.9.9", now_ms=T0 + 2)
    assert err.value.code == "devices_per_ip" and len(registry.devices) == 2
    registry.subscribe_web(raw, _push(4), "a", ip="9.9.9.9", now_ms=T0 + 3)  # своя сторінка — не новий пристрій
    registry.subscribe_web(None, _push(5), "d", ip="8.8.8.8", now_ms=T0 + 4)  # інша IP
    registry.subscribe_web(None, _push(6), "e", ip="9.9.9.9", now_ms=T0 + HOUR + 1)  # година минула
    assert len(registry.devices) == 4


def test_max_devices_refuses_new_ones_and_keeps_existing(tmp_path):
    registry = _registry(tmp_path / DEVICES_FILE, max_devices=2)
    registry.subscribe_web(None, _push(1), "a", ip="1.1.1.1", now_ms=T0)
    registry.subscribe_web(None, _push(2), "b", ip="2.2.2.2", now_ms=T0)
    before = dict(registry.devices)
    with pytest.raises(DeviceLimitError) as err:
        registry.subscribe_web(None, _push(3), "c", ip="3.3.3.3", now_ms=T0)
    assert err.value.code == "devices_full" and registry.devices == before


def test_web_subscription_disabled_without_limits(tmp_path):
    registry = _registry(tmp_path / DEVICES_FILE, web=False)
    with pytest.raises(DeviceLimitError) as err:
        registry.subscribe_web(None, _push(), "a", ip="1.1.1.1", now_ms=T0)
    assert err.value.code == "web_disabled" and registry.devices == {}


# ── push_gone, last_seen, прибирання ──────────────────────────────────────


def test_mark_push_gone_once_persisted_and_only_for_web(registry):
    device, _ = registry.subscribe_web(None, _push(), "a", ip="1.1.1.1", now_ms=T0)
    assert registry.mark_push_gone(device.device_id) is True
    assert registry.mark_push_gone(device.device_id) is False
    assert registry.mark_push_gone("nope") is False
    assert _reload(registry).devices[device.device_id].push_gone is True
    tg = Device("tg1", "h" * 64, subscriber_for_chat(42), "ПК", T0, T0)
    registry._put(tg)
    assert registry.mark_push_gone("tg1") is False


def test_touch_updates_last_seen_hourly_in_memory_until_commit_if_dirty(registry):
    device, _ = registry.subscribe_web(None, _push(), "a", ip="1.1.1.1", now_ms=T0)
    on_disk = registry.path.read_bytes()
    assert registry.touch(device, now_ms=T0 + TOUCH_RESOLUTION_MS - 1) is device
    registry.commit_if_dirty()
    assert registry.path.read_bytes() == on_disk  # нічого не змінилось — запису нема
    touched = registry.touch(device, now_ms=T0 + TOUCH_RESOLUTION_MS)
    assert touched.last_seen_ms == T0 + TOUCH_RESOLUTION_MS and registry.devices[device.device_id] == touched
    assert registry.path.read_bytes() == on_disk  # лише в пам'яті
    registry.commit_if_dirty()
    assert _reload(registry).devices[device.device_id].last_seen_ms == T0 + TOUCH_RESOLUTION_MS


def test_idle_web_devices_skip_busy_recent_and_telegram(registry):
    old, _ = registry.subscribe_web(None, _push(1), "old", ip="1.1.1.1", now_ms=T0)
    busy, _ = registry.subscribe_web(None, _push(2), "busy", ip="1.1.1.2", now_ms=T0)
    recent, _ = registry.subscribe_web(None, _push(3), "recent", ip="1.1.1.3", now_ms=T0 + 80 * DAY)
    registry._put(Device("tg1", "h" * 64, subscriber_for_chat(42), "ПК", T0, T0))
    idle = registry.idle_web_devices(now_ms=T0 + 90 * DAY, idle_ms=90 * DAY, busy={busy.subscriber_id})
    assert [d.device_id for d in idle] == [old.device_id]
    assert recent.device_id not in [d.device_id for d in idle]


def test_token_index_follows_device_replacement(registry):
    """Інваріант індексу: заміна запису з іншим токеном — старий токен більше не входить (майбутня ротація cookie)."""
    import dataclasses

    from runtime.alerts.devices import token_digest

    device, raw = registry.subscribe_web(None, _push(), "a", ip="1.1.1.1", now_ms=T0)
    registry._put(dataclasses.replace(device, token_sha256=token_digest("new-cookie")))
    assert registry.authenticate(raw) is None
    assert registry.authenticate("new-cookie").device_id == device.device_id


def test_revoked_web_device_no_longer_authenticates(registry):
    device, raw = registry.subscribe_web(None, _push(), "a", ip="1.1.1.1", now_ms=T0)
    assert registry.revoke(device.device_id, device.subscriber_id) is True
    assert registry.authenticate(raw) is None and _reload(registry).devices == {}


# ── схеми файлу ───────────────────────────────────────────────────────────


def test_schema1_telegram_file_loads_and_is_rewritten_as_schema2(tmp_path):
    path = tmp_path / DEVICES_FILE
    legacy = {"device_id": "d1", "token_sha256": "h" * 64, "subscriber_id": "tg:42", "label": "ПК", "created_ms": T0}
    path.write_text(json.dumps({"schema": 1, "devices": [legacy]}), encoding="utf-8")
    registry = DeviceRegistry.load(path, confirm_attempts=3, max_pending=4)
    device = registry.devices["d1"]
    assert (device.last_seen_ms, device.push, device.push_gone) == (T0, None, False)
    registry.commit()
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["schema"] == 2 and doc["devices"][0]["last_seen_ms"] == T0


@pytest.mark.parametrize("broken", ["schema3", "bad_push", "web_without_push", "web_wrong_id", "tg_with_push"])
def test_broken_devices_file_refuses_to_load(tmp_path, broken):
    path = tmp_path / DEVICES_FILE
    push = {"endpoint": "https://fcm.googleapis.com/x", "p256dh": "p", "auth": "a"}
    row = {
        "device_id": "w1",
        "token_sha256": "h" * 64,
        "subscriber_id": "web:w1",
        "label": "a",
        "created_ms": T0,
        "last_seen_ms": T0,
        "push": push,
        "push_gone": False,
    }
    schema = 2
    if broken == "schema3":
        schema = 3
    elif broken == "bad_push":
        row["push"] = {**push, "extra": 1}
    elif broken == "web_without_push":
        row["push"] = None
    elif broken == "web_wrong_id":
        row["subscriber_id"] = "web:someone-else"
    else:
        row["subscriber_id"] = "tg:42"
    path.write_text(json.dumps({"schema": schema, "devices": [row]}), encoding="utf-8")
    with pytest.raises(ValueError):
        DeviceRegistry.load(path, confirm_attempts=3, max_pending=4)
