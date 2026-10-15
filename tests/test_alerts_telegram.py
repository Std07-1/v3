"""ADR-0114 S4: пристрої, тексти і бот сигналів (runtime/alerts/{devices,messages,telegram}.py).

Bot API — справжній HTTP до локального фейкового сервера (aiohttp TestServer), не мок методу.
Run: python -m pytest tests/test_alerts_telegram.py -q
"""

from __future__ import annotations

import asyncio
import itertools
import json
from zoneinfo import ZoneInfo

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from core.alerts.crossing import on_m1_bar, on_tick
from core.alerts.types import AlertSide, PriceAlert
from runtime.alerts.devices import (
    DEVICES_FILE,
    ConfirmOutcome,
    ConfirmResult,
    DeviceRegistry,
    chat_of_subscriber,
    subscriber_for_chat,
)
from runtime.alerts.engine import AlertEngine
from runtime.alerts.messages import (
    BAD_CODE_TEXT,
    HELP_TEXT,
    MAX_TEXT,
    OWNER_ONLY_TEXT,
    alerts_list,
    chart_link,
    devices_list,
    fire_message,
    require_digits,
)
from runtime.alerts.store import STATE_FILE, AlertStore
from runtime.alerts.telegram import (
    DEGRADED_COMMANDS,
    DEGRADED_TELEGRAM,
    CommandHandler,
    Delivery,
    TelegramClient,
    TelegramError,
    run_commands,
)

T0 = 1_792_086_371_000  # 15.10.2026 19:46:11 за Прагою (CEST)
OWNER = 1001
PAIR_TTL = 600_000
TOKEN = "123456:SECRET-token"
TZ = ZoneInfo("Europe/Prague")
DIGITS = {"XAU/USD": 2, "XAG/USD": 3}
SITE = "https://aione-smc.com"


def _secrets():
    counter = itertools.count(1)
    return lambda n: f"s{next(counter)}x{n}"


def _alert(alert_id="a1", symbol="XAU/USD", level=4380.0, subscriber=None) -> PriceAlert:
    return PriceAlert(
        alert_id=alert_id,
        drawing_id=f"d-{alert_id}",
        subscriber_id=subscriber or subscriber_for_chat(OWNER),
        symbol=symbol,
        level=level,
        tf_label="M15",
        active_from_ms=T0,
        created_at_ms=T0,
        side=AlertSide.BELOW,
    )


def _fired(alert_id="a1", price=4380.25, **kw) -> PriceAlert:
    return on_tick(_alert(alert_id, **kw), price, T0, prev_tick_ts_ms=None, gap_after_ms=900_000)


# ── пристрої ─────────────────────────────────────────────────────────────


@pytest.fixture
def registry(tmp_path):
    return _registry(tmp_path / DEVICES_FILE)


def _registry(path, *, secrets=None, codes=None, max_pending=4) -> DeviceRegistry:
    codes = codes or (f"{n:06d}" for n in itertools.count(111111, 111111))
    return DeviceRegistry(
        path,
        confirm_attempts=3,
        max_pending=max_pending,
        new_secret=secrets or _secrets(),
        new_confirm_code=lambda: next(codes),
    )


def _pair(registry, *, label="ПК", chat=OWNER, now_ms=T0):
    """Повний «код назад»: сторінка → бот → сторінка. → (пристрій, сирий токен)."""
    link, raw = registry.start_pairing(label, now_ms=now_ms, ttl_ms=PAIR_TTL)
    digits = registry.issue_confirm_code(link, subscriber_for_chat(chat), now_ms=now_ms, ttl_ms=PAIR_TTL)
    return registry.confirm(raw, digits, now_ms=now_ms).device, raw


def test_link_alone_does_not_bind_only_bot_digits_entered_in_same_browser_do(registry):
    link, raw = registry.start_pairing("Android · Chrome", now_ms=T0, ttl_ms=PAIR_TTL)
    assert registry.confirm(raw, "111111", now_ms=T0).outcome is ConfirmOutcome.AWAITING_BOT  # бот ще не видав цифр
    digits = registry.issue_confirm_code(link, subscriber_for_chat(OWNER), now_ms=T0, ttl_ms=PAIR_TTL)
    assert registry.devices == {} and registry.authenticate(raw) is None  # посилання саме нічого не прив'язало
    _, other_browser = registry.start_pairing("чужий", now_ms=T0, ttl_ms=PAIR_TTL)
    assert registry.confirm(other_browser, digits, now_ms=T0).outcome is ConfirmOutcome.AWAITING_BOT
    assert registry.confirm(raw, "000000", now_ms=T0) == ConfirmResult(ConfirmOutcome.WRONG_CODE, attempts_left=2)
    result = registry.confirm(raw, digits, now_ms=T0 + 1_000)
    assert result.outcome is ConfirmOutcome.BOUND and registry.authenticate(raw) == result.device
    assert result.device.subscriber_id == subscriber_for_chat(OWNER)
    assert registry.authenticate(raw + "x") is None and registry.authenticate(link) is None
    assert registry.confirm(raw, digits, now_ms=T0).outcome is ConfirmOutcome.NO_PAIRING  # одноразово
    assert raw not in registry.path.read_text(encoding="utf-8")  # на диску лише sha256
    assert DeviceRegistry.load(registry.path, confirm_attempts=3, max_pending=4).devices == registry.devices


def test_wrong_or_malformed_digits_spend_attempts_and_lock(registry):
    link, raw = registry.start_pairing("ПК", now_ms=T0, ttl_ms=PAIR_TTL)
    digits = registry.issue_confirm_code(link, subscriber_for_chat(OWNER), now_ms=T0, ttl_ms=PAIR_TTL)
    assert registry.confirm(raw, "١١١١١١", now_ms=T0).attempts_left == 2  # не-ASCII цифри — не виняток, а спроба
    assert registry.confirm(raw, "12345", now_ms=T0).attempts_left == 1
    assert registry.confirm(raw, "abcdef", now_ms=T0).outcome is ConfirmOutcome.LOCKED
    assert registry.confirm(raw, digits, now_ms=T0).outcome is ConfirmOutcome.NO_PAIRING
    assert registry.issue_confirm_code(link, subscriber_for_chat(OWNER), now_ms=T0, ttl_ms=PAIR_TTL) is None


def test_reopened_link_gives_new_digits_and_full_attempts(registry):
    link, raw = registry.start_pairing("ПК", now_ms=T0, ttl_ms=PAIR_TTL)
    old = registry.issue_confirm_code(link, subscriber_for_chat(OWNER), now_ms=T0, ttl_ms=PAIR_TTL)
    registry.confirm(raw, "000000", now_ms=T0)
    new = registry.issue_confirm_code(link, subscriber_for_chat(OWNER), now_ms=T0 + 1_000, ttl_ms=PAIR_TTL)
    assert new != old
    assert registry.confirm(raw, old, now_ms=T0).attempts_left == 2  # старі цифри недійсні, запас спроб повний
    assert registry.confirm(raw, new, now_ms=T0).outcome is ConfirmOutcome.BOUND


def test_digits_live_full_ttl_from_issue_as_the_bot_promises(registry):
    link, raw = registry.start_pairing("ПК", now_ms=T0, ttl_ms=PAIR_TTL)
    late = T0 + PAIR_TTL - 1_000  # посилання відкрили наприкінці вікна
    digits = registry.issue_confirm_code(link, subscriber_for_chat(OWNER), now_ms=late, ttl_ms=PAIR_TTL)
    assert registry.confirm(raw, digits, now_ms=late + PAIR_TTL - 1).outcome is ConfirmOutcome.BOUND


def test_expired_unknown_or_evicted_pairing_does_not_issue_or_bind(tmp_path):
    registry = _registry(tmp_path / DEVICES_FILE, max_pending=2)
    link, raw = registry.start_pairing("ПК", now_ms=T0, ttl_ms=PAIR_TTL)
    assert registry.issue_confirm_code(link, subscriber_for_chat(OWNER), now_ms=T0 + PAIR_TTL, ttl_ms=PAIR_TTL) is None
    assert registry.issue_confirm_code("nope", subscriber_for_chat(OWNER), now_ms=T0, ttl_ms=PAIR_TTL) is None
    assert registry.confirm(raw, "111111", now_ms=T0 + PAIR_TTL).outcome is ConfirmOutcome.NO_PAIRING
    first, _ = registry.start_pairing("1", now_ms=T0, ttl_ms=PAIR_TTL)
    registry.start_pairing("2", now_ms=T0 + 1, ttl_ms=PAIR_TTL)
    registry.start_pairing("3", now_ms=T0 + 2, ttl_ms=PAIR_TTL)  # понад max_pending — найстаріша відкинута
    assert registry.issue_confirm_code(first, subscriber_for_chat(OWNER), now_ms=T0 + 3, ttl_ms=PAIR_TTL) is None
    assert registry.devices == {}


def test_revoke_only_own_device(registry):
    device, raw = _pair(registry)
    assert registry.revoke(device.device_id, subscriber_for_chat(999)) is False
    assert registry.revoke(device.device_id, subscriber_for_chat(OWNER)) is True
    assert registry.authenticate(raw) is None
    assert DeviceRegistry.load(registry.path, confirm_attempts=3, max_pending=4).devices == {}


def test_short_device_id_collision_does_not_overwrite(tmp_path):
    ids = iter(["c1", "t1", "dup", "c2", "t2", "dup", "fresh"])
    registry = _registry(tmp_path / DEVICES_FILE, secrets=lambda n: next(ids))
    first, _ = _pair(registry, label="A")
    second, _ = _pair(registry, label="B")
    assert (first.device_id, second.device_id) == ("dup", "fresh") and len(registry.devices) == 2


def test_devices_file_with_unknown_field_refuses_to_load(tmp_path):
    path = tmp_path / DEVICES_FILE
    device = {"device_id": "d", "token_sha256": "h", "subscriber_id": "tg:1", "label": "ПК", "created_ms": T0}
    path.write_text(json.dumps({"schema": 1, "devices": [{**device, "admin": True}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="рівно"):
        DeviceRegistry.load(path, confirm_attempts=3, max_pending=4)


@pytest.mark.parametrize(
    "subscriber, chat", [("tg:1001", 1001), ("tg:-100123", -100123), ("tg:", None), ("xtg:1", None), ("tg:1a", None)]
)
def test_chat_of_subscriber(subscriber, chat):
    assert chat_of_subscriber(subscriber) == chat


# ── тексти ───────────────────────────────────────────────────────────────


def test_fire_message_tick_uses_symbol_digits_local_time_and_chart_link():
    text = fire_message(_fired(), digits=DIGITS, tz=TZ, site_url=SITE + "/")
    assert text.splitlines() == [
        "🔔 XAU/USD 4380.00 — перетнуто знизу вгору",
        "Ціна 4380.25 · 15.10 19:46:11 CEST",
        "https://aione-smc.com/?symbol=XAU/USD&tf=M15",
    ]


def test_fire_message_m1_and_gap_explain_how_crossing_was_seen():
    m1 = on_m1_bar(_alert(symbol="XAG/USD", level=50.0), open_ms=T0 + 60_000, low=49.9, high=50.1234)
    assert "Свічка M1 15.10 19:47 CEST дійшла до 50.123" in fire_message(m1, digits=DIGITS, tz=TZ, site_url=SITE)
    gap = on_tick(_alert(), 4390.0, T0 + 3_600_000, prev_tick_ts_ms=T0, gap_after_ms=900_000)
    assert "після паузи ринку" in fire_message(gap, digits=DIGITS, tz=TZ, site_url=SITE)


def test_lists_and_digits_guard():
    text = alerts_list([_alert("a2", level=4400.0), _fired()], digits=DIGITS, tz=TZ)
    assert text.splitlines() == [
        "Сповіщення (2):",
        "🔕 XAU/USD 4380.00 — спрацювало 15.10 19:46 знизу вгору",
        "🔔 XAU/USD 4400.00 — чекає з 15.10 19:46",
    ]
    assert "Сповіщень нема" in alerts_list([], digits=DIGITS, tz=TZ)
    assert "Під'єднаних пристроїв нема" in devices_list([], tz=TZ)
    with pytest.raises(ValueError, match="NAS100"):
        require_digits(["XAU/USD", "NAS100"], DIGITS)
    assert chart_link(SITE, "USD/JPY", "H1") == "https://aione-smc.com/?symbol=USD/JPY&tf=H1"
    many = [_alert(f"a{i}", level=4000.0 + i) for i in range(300)]
    assert len(alerts_list(many, digits=DIGITS, tz=TZ)) == MAX_TEXT


# ── TelegramClient через справжній HTTP ──────────────────────────────────


class FakeBotApi:
    def __init__(self) -> None:
        self.sent: list = []
        self.fail_with: dict | None = None

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post(f"/bot{TOKEN}/sendMessage", self.send_message)
        app.router.add_post(f"/bot{TOKEN}/getUpdates", self.get_updates)
        return app

    async def send_message(self, request: web.Request) -> web.Response:
        if self.fail_with is not None:
            return web.json_response(self.fail_with, status=self.fail_with.get("error_code", 500))
        self.sent.append(await request.json())
        return web.json_response({"ok": True, "result": {"message_id": len(self.sent)}})

    async def get_updates(self, request: web.Request) -> web.Response:
        body = await request.json()
        return web.json_response({"ok": True, "result": [{"update_id": 7, "echo_offset": body.get("offset")}]})


@pytest.mark.asyncio
async def test_client_round_trip_errors_carry_retry_after_and_never_the_token():
    import aiohttp

    api = FakeBotApi()
    async with TestServer(api.app()) as server, aiohttp.ClientSession() as session:
        client = TelegramClient(session, TOKEN, api_base=str(server.make_url("")))
        await client.send_message(OWNER, "привіт")
        assert api.sent[0]["chat_id"] == OWNER and api.sent[0]["link_preview_options"] == {"is_disabled": True}
        assert (await client.get_updates(8, 0))[0]["echo_offset"] == 8
        api.fail_with = {
            "ok": False,
            "error_code": 429,
            "description": "Too Many Requests",
            "parameters": {"retry_after": 3},
        }
        with pytest.raises(TelegramError) as caught:
            await client.send_message(OWNER, "x")
        assert caught.value.retry_after_s == 3 and "HTTP 429" in str(caught.value)
        api.fail_with = {"ok": False, "error_code": 502, "description": f"proxy: bad upstream /bot{TOKEN}/sendMessage"}
        with pytest.raises(TelegramError) as caught:
            await client.send_message(OWNER, "x")
        assert TOKEN not in str(caught.value) and "/bot<token>/sendMessage" in str(caught.value)
        port = server.port
    async with aiohttp.ClientSession() as session:  # сервер зупинено — зв'язку нема
        dead = TelegramClient(session, TOKEN, api_base=f"http://127.0.0.1:{port}")
        with pytest.raises(TelegramError) as caught:
            await dead.send_message(OWNER, "x")
        assert TOKEN not in str(caught.value) and "SECRET" not in str(caught.value)


# ── доставка ─────────────────────────────────────────────────────────────


class FakeClient:
    def __init__(self) -> None:
        self.sent: list = []
        self.errors: list = []

    async def send_message(self, chat_id, text):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append((chat_id, text))


@pytest.fixture
def engine(tmp_path):
    store = AlertStore(tmp_path / STATE_FILE)
    return AlertEngine(store, gap_after_ms=900_000, clock_ms=lambda: T0)


def _delivery(engine, client, clock):
    return Delivery(
        engine, client, digits=DIGITS, tz=TZ, site_url=SITE, clock_ms=clock, retry_first_s=2.0, retry_max_s=8.0
    )


@pytest.mark.asyncio
async def test_delivery_acks_only_after_send_and_keeps_order_with_backoff(engine):
    first, second = _fired("a1"), _fired("a2", price=4381.0)
    engine.store.enqueue(first, now_ms=T0)
    engine.store.enqueue(second, now_ms=T0 + 1)
    client, now = FakeClient(), [T0]
    delivery = _delivery(engine, client, lambda: now[0])
    client.errors = [TelegramError("down"), TelegramError("down")]

    assert await delivery.deliver_once() == 0  # перший не пішов — другий не обганяє
    assert DEGRADED_TELEGRAM in engine.degraded and len(engine.store.outbox) == 2
    assert [e.attempts for e in engine.store.outbox.values()] == [1, 0]
    now[0] = T0 + 1_999
    assert await delivery.deliver_once() == 0 and len(client.errors) == 1  # пауза 2 с ще не минула
    now[0] = T0 + 2_000
    assert await delivery.deliver_once() == 0  # друга невдача — пауза подвоюється до 4 с
    now[0] = T0 + 2_000 + 3_999
    assert await delivery.deliver_once() == 0 and client.sent == []
    now[0] = T0 + 6_000
    assert await delivery.deliver_once() == 2
    assert [text.splitlines()[1] for _, text in client.sent] == [
        "Ціна 4380.25 · 15.10 19:46:11 CEST",
        "Ціна 4381.00 · 15.10 19:46:11 CEST",
    ]
    assert DEGRADED_TELEGRAM not in engine.degraded and engine.store.outbox == {}
    assert engine.store.path.exists() and AlertStore.load(engine.store.path, now_ms=T0).outbox == {}
    assert await delivery.deliver_once() == 0 and len(client.sent) == 2  # доставлене не повторюється


@pytest.mark.asyncio
async def test_delivery_honors_retry_after_and_caps_backoff(engine):
    engine.store.enqueue(_fired(), now_ms=T0)
    client, now = FakeClient(), [T0]
    delivery = _delivery(engine, client, lambda: now[0])
    client.errors = [TelegramError("429", retry_after_s=30)]
    await delivery.deliver_once()
    now[0] = T0 + 29_999
    assert await delivery.deliver_once() == 0
    client.errors = [TelegramError("x")] * 3
    for step in range(3):  # 30 с → далі min(подвоєння, 8 с)
        now[0] += 30_000
        await delivery.deliver_once()
    assert delivery._backoff[("a1", 1)].delay_s == 8.0


# ── команди ──────────────────────────────────────────────────────────────


def _handler(registry, engine):
    return CommandHandler(
        registry, engine, owner_chat_id=OWNER, digits=DIGITS, tz=TZ, clock_ms=lambda: T0, pair_ttl_ms=PAIR_TTL
    )


def test_commands_owner_only_start_alerts_devices_revoke(registry, engine):
    handler = _handler(registry, engine)
    link, raw = registry.start_pairing("Android · Chrome", now_ms=T0, ttl_ms=PAIR_TTL)
    assert handler.handle(999, f"/start {link}") == OWNER_ONLY_TEXT
    assert registry.confirm(raw, "111111", now_ms=T0).outcome is ConfirmOutcome.AWAITING_BOT  # чужий чат цифр не дав
    reply = handler.handle(OWNER, f"/start@v3_signals_bot {link}")
    assert reply.splitlines()[0] == "Код для сайту: 111111" and "Діє 10 хв" in reply
    assert registry.authenticate(raw) is None  # саме посилання не прив'язує
    device = registry.confirm(raw, "111111", now_ms=T0).device
    assert registry.authenticate(raw) == device
    assert handler.handle(OWNER, f"/start {link}") == BAD_CODE_TEXT  # прив'язку завершено — посилання мертве

    engine.store.put(_alert("mine"))
    engine.store.put(_alert("theirs", subscriber=subscriber_for_chat(999)))
    assert handler.handle(OWNER, "/alerts").splitlines()[0] == "Сповіщення (1):"
    assert device.device_id in handler.handle(OWNER, "/devices")
    assert handler.handle(OWNER, "/revoke nope") == "Такого пристрою нема."
    assert handler.handle(OWNER, f"/revoke {device.device_id}") == "Пристрій відключено."
    assert handler.handle(OWNER, "/start") == HELP_TEXT and handler.handle(OWNER, "що це?") == HELP_TEXT


class FakeUpdatesClient(FakeClient):
    def __init__(self, batches) -> None:
        super().__init__()
        self.batches, self.offsets = batches, []

    async def get_updates(self, offset, timeout_s):
        self.offsets.append(offset)
        if not self.batches:
            await asyncio.Event().wait()
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch


@pytest.mark.asyncio
async def test_command_loop_moves_offset_survives_reply_failure_and_flags_outage(registry, engine):
    def update(update_id, chat, text):
        return {"update_id": update_id, "message": {"chat": {"id": chat}, "text": text}}

    client = FakeUpdatesClient(
        [
            [update(10, OWNER, "/alerts"), {"update_id": 11, "message": {"chat": {"id": OWNER}, "sticker": {}}}],
            TelegramError("getUpdates: HTTP 502"),
            [update(12, 999, "/alerts")],
        ]
    )
    client.errors = [TelegramError("reply failed")]
    task = asyncio.create_task(
        run_commands(client, _handler(registry, engine), engine, poll_timeout_s=0, error_pause_s=0)
    )
    flags = []
    for _ in range(200):
        flags.append(DEGRADED_COMMANDS in engine.degraded)
        if len(client.offsets) >= 4:
            break
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.offsets == [None, 12, 12, 13]
    assert client.sent == [(999, OWNER_ONLY_TEXT)]  # відповідь власнику впала й не повторювалась
    assert True in flags and DEGRADED_COMMANDS not in engine.degraded
