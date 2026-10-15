"""Бот сигналів (ADR-0114 §4.4–4.5): доставка черги в Telegram і команди власника.

- **Доставка** — «щонайменше раз»: запис знімається з черги лише після `ok` від Telegram. Порядок — за часом
  постановки; невдача — пауза з подвоєнням (`retry_first_s` … `retry_max_s`, 429 — `retry_after` від Telegram), решта
  черги чекає (порядок зберігається), прапорець `telegram_unreachable`.
- **Команди** — довге опитування `getUpdates`: `/start <код>` прив'язує пристрій, `/alerts`, `/devices`,
  `/revoke <id>`. Поки сповіщення лише для власника (рішення 2а) — будь-який інший чат отримує «лише для власника».
  Відповіді — «не більше разу»: offset рухається до відповіді, тож збій відповіді не повторює команду.

Токен бота живе лише в URL запитів і ніколи не потрапляє в текст помилок і логів.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

import aiohttp

from runtime.alerts.devices import DeviceRegistry, chat_of_subscriber, subscriber_for_chat
from runtime.alerts.engine import AlertEngine
from runtime.alerts.messages import (
    BAD_CODE_TEXT,
    HELP_TEXT,
    OWNER_ONLY_TEXT,
    alerts_list,
    devices_list,
    fire_message,
)

_log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
SEND_TIMEOUT_S = 15.0  # sendMessage зазвичай < 1 с; довше — вважаємо недоступним і повторимо з паузою
DEGRADED_TELEGRAM = "telegram_unreachable"
DEGRADED_COMMANDS = "telegram_commands_unavailable"


class TelegramError(Exception):
    def __init__(self, description: str, *, retry_after_s: Optional[float] = None) -> None:
        super().__init__(description)
        self.retry_after_s = retry_after_s


class TelegramClient:
    def __init__(self, session: aiohttp.ClientSession, token: str, *, api_base: str = API_BASE) -> None:
        if not token:
            raise ValueError("token бота порожній")
        self._session = session
        self._token = token
        self._url = f"{api_base.rstrip('/')}/bot{token}/"

    async def send_message(self, chat_id: int, text: str) -> None:
        payload = {"chat_id": chat_id, "text": text, "link_preview_options": {"is_disabled": True}}
        await self._call("sendMessage", payload, timeout_s=SEND_TIMEOUT_S)

    async def get_updates(self, offset: Optional[int], timeout_s: int) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"timeout": timeout_s, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = await self._call("getUpdates", payload, timeout_s=timeout_s + SEND_TIMEOUT_S)
        if not isinstance(result, list):
            raise TelegramError(f"getUpdates: result не список ({type(result).__name__})")
        return result

    async def _call(self, method: str, payload: Dict[str, Any], *, timeout_s: float) -> Any:
        try:
            timeout = aiohttp.ClientTimeout(total=timeout_s)
            async with self._session.post(self._url + method, json=payload, timeout=timeout) as resp:
                status = resp.status
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
            raise TelegramError(f"{method}: {type(exc).__name__}: {self._mask(str(exc))}") from None
        if not isinstance(body, dict) or body.get("ok") is not True:
            details = body if isinstance(body, dict) else {}
            retry_after = (details.get("parameters") or {}).get("retry_after")
            description = self._mask(str(details.get("description", body)))[:300]
            raise TelegramError(f"{method}: HTTP {status} {description}", retry_after_s=retry_after)
        return body.get("result")

    def _mask(self, text: str) -> str:
        return text.replace(self._token, "<token>")


@dataclasses.dataclass(frozen=True)
class _Backoff:
    next_try_ms: int
    delay_s: float


class Delivery:
    def __init__(
        self,
        engine: AlertEngine,
        client: TelegramClient,
        *,
        digits: Mapping[str, int],
        tz: ZoneInfo,
        site_url: str,
        clock_ms: Callable[[], int],
        retry_first_s: float,
        retry_max_s: float,
    ) -> None:
        self._engine, self._client, self._clock_ms = engine, client, clock_ms
        self._format = dict(digits=digits, tz=tz, site_url=site_url)
        self._retry_first_s, self._retry_max_s = retry_first_s, retry_max_s
        self._backoff: Dict[Tuple[str, int], _Backoff] = {}

    async def deliver_once(self) -> int:
        """Один прохід черги; повертає кількість доставлених."""
        delivered = 0
        now = self._clock_ms()
        entries = sorted(self._engine.store.outbox.items(), key=lambda item: item[1].enqueued_at_ms)
        for key, entry in entries:
            backoff = self._backoff.get(key)
            if backoff is not None and backoff.next_try_ms > now:
                break  # старший запис ще чекає паузи — молодші не обганяють
            chat_id = chat_of_subscriber(entry.alert.subscriber_id)
            if chat_id is None:  # модель це забороняє (Device.subscriber_id) — сюди не мали дійти
                _log.error("ALERTS_UNDELIVERABLE id=%s subscriber=%r", entry.alert.alert_id, entry.alert.subscriber_id)
                self._engine.ack_delivered(key)
                continue
            try:
                await self._client.send_message(chat_id, fire_message(entry.alert, **self._format))
            except TelegramError as exc:
                self._engine.note_attempt(key)
                delay = exc.retry_after_s or (
                    self._retry_first_s if backoff is None else min(backoff.delay_s * 2, self._retry_max_s)
                )
                self._backoff[key] = _Backoff(now + int(delay * 1000), delay)
                self._engine.set_degraded(DEGRADED_TELEGRAM, str(exc))
                break
            self._backoff.pop(key, None)
            self._engine.ack_delivered(key)
            self._engine.set_degraded(DEGRADED_TELEGRAM, None)
            delivered += 1
        return delivered

    async def run(self, *, poll_s: float) -> None:
        while True:
            await self.deliver_once()
            await asyncio.sleep(poll_s)


class CommandHandler:
    def __init__(
        self,
        registry: DeviceRegistry,
        engine: AlertEngine,
        *,
        owner_chat_id: int,
        digits: Mapping[str, int],
        tz: ZoneInfo,
        clock_ms: Callable[[], int],
    ) -> None:
        self._registry, self._engine, self._owner_chat_id = registry, engine, owner_chat_id
        self._digits, self._tz, self._clock_ms = digits, tz, clock_ms

    def handle(self, chat_id: int, text: str) -> str:
        if chat_id != self._owner_chat_id:
            return OWNER_ONLY_TEXT
        command, _, arg = text.strip().partition(" ")
        command, arg = command.split("@", 1)[0].lower(), arg.strip()
        subscriber = subscriber_for_chat(chat_id)
        if command == "/start" and arg:
            device = self._registry.bind(arg, subscriber, now_ms=self._clock_ms())
            return BAD_CODE_TEXT if device is None else f"Пристрій під'єднано: {device.label} ({device.device_id})."
        if command == "/alerts":
            mine = [a for a in self._engine.store.alerts.values() if a.subscriber_id == subscriber]
            return alerts_list(mine, digits=self._digits, tz=self._tz)
        if command == "/devices":
            return devices_list(self._registry.devices_of(subscriber), tz=self._tz)
        if command == "/revoke" and arg:
            return "Пристрій відключено." if self._registry.revoke(arg, subscriber) else "Такого пристрою нема."
        return HELP_TEXT


async def run_commands(
    client: TelegramClient,
    handler: CommandHandler,
    engine: AlertEngine,
    *,
    poll_timeout_s: int,
    error_pause_s: float,
) -> None:
    offset: Optional[int] = None
    while True:
        try:
            updates = await client.get_updates(offset, poll_timeout_s)
        except TelegramError as exc:
            engine.set_degraded(DEGRADED_COMMANDS, str(exc))
            await asyncio.sleep(error_pause_s)
            continue
        engine.set_degraded(DEGRADED_COMMANDS, None)
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = max(offset or 0, update_id + 1)
            message = update.get("message") or {}
            chat_id, text = (message.get("chat") or {}).get("id"), message.get("text")
            if not isinstance(chat_id, int) or not isinstance(text, str):
                continue  # не текстове повідомлення (стікер, фото) — нема на що відповідати
            try:
                await client.send_message(chat_id, handler.handle(chat_id, text))
            except TelegramError as exc:
                _log.warning("ALERTS_COMMAND_REPLY_FAILED chat=%s err=%s", chat_id, exc)
