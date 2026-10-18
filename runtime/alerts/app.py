"""HTTP процесу `smc-alerts` (ADR-0114 §4.5–4.6): `/api/alerts/*` за nginx сайту.

    GET    /api/alerts/health            без cookie: увімкнено, канал (push / telegram), прапорці деградацій
    GET    /api/alerts/push/key          [пуш] публічний ключ VAPID для pushManager.subscribe
    POST   /api/alerts/push/subscribe    [пуш] підписка браузера → пристрій web:<id> і cookie; своя сторінка — оновлення
    DELETE /api/alerts/device            «вимкнути на цьому пристрої»: його сповіщення, пристрій, cookie — геть
    POST   /api/alerts/pair              [Telegram] почати прив'язку: посилання t.me + cookie (поки нічого не відкриває)
    POST   /api/alerts/pair/confirm      [Telegram] 6 цифр з бота → прив'язка САМЕ цього браузера («код назад»)
    GET    /api/alerts                   мої сповіщення зі станом доставки
    PUT    /api/alerts/{drawing_id}      створити / пересунути / знову взвести сповіщення лінії
    DELETE /api/alerts/{drawing_id}      лінію видалено
    GET    /api/alerts/events            SSE: fired / delivered моїх сповіщень + heartbeat

Канал — рівно один (`alerts.telegram_enabled`): маршрути іншого каналу відповідають 404 `<канал>_disabled`.
Захист: cookie `aione_alerts` (HttpOnly, Secure, SameSite=Strict, Path=/api/alerts); зміни стану — лише із заголовком
`X-Alerts: 1` і Origin сайту (CSRF); ліміт запитів на IP за хвилину; тіло ≤ 4 КБ. Сервер — джерело стану (X28):
сторінка показує те, що повернуто, і сама перетинів не рахує.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import uuid
from typing import Any, Awaitable, Callable, Dict, FrozenSet, Optional, Tuple

from aiohttp import web

from core.alerts.codec import alert_to_dict
from core.alerts.crossing import arm, side_of
from core.alerts.types import AlertState, PriceAlert, validate_price
from runtime.alerts.devices import ConfirmOutcome, Device, DeviceLimitError, DeviceRegistry
from runtime.alerts.engine import AlertEngine
from runtime.alerts.feeds import M1ReadError
from runtime.alerts.push_delivery import forget_device
from runtime.alerts.sse import EventStreams
from runtime.alerts.telegram import TelegramError
from runtime.alerts.webpush import PushSubscription
from runtime.visitors.journal import classify_device

_log = logging.getLogger(__name__)

COOKIE_NAME = "aione_alerts"
COOKIE_PATH = "/api/alerts"
COOKIE_MAX_AGE_S = 400 * 24 * 3600  # Chrome обрізає довший строк cookie до 400 діб
CSRF_HEADER = "X-Alerts"
MAX_BODY_BYTES = 4096
_DRAWING_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TF_LABEL_RE = re.compile(r"^[A-Z0-9]{1,4}$")
_CONFIRM_STATUS = {
    ConfirmOutcome.BOUND: 200,
    ConfirmOutcome.WRONG_CODE: 400,
    ConfirmOutcome.AWAITING_BOT: 409,
    ConfirmOutcome.NO_PAIRING: 404,
    ConfirmOutcome.LOCKED: 410,
}


@dataclasses.dataclass(frozen=True)
class ApiSettings:
    symbols: FrozenSet[str]
    site_origin: str  # «https://aione-smc.com» — єдиний дозволений Origin змін стану
    pair_ttl_ms: int
    max_active_per_subscriber: int
    requests_per_min: int
    sse_heartbeat_s: float
    channel: str = "telegram"  # "push" (Rev 2) або "telegram" (Rev 1): сервіс задає з alerts.telegram_enabled
    push_host_suffixes: Tuple[str, ...] = ()
    push_public_key: Optional[str] = None


def build_app(
    engine: AlertEngine,
    registry: DeviceRegistry,
    settings: ApiSettings,
    *,
    clock_ms: Callable[[], int],
    bot_username: Callable[[], Awaitable[str]],
    reference_price: Callable[[str], Awaitable[Optional[float]]],
    new_alert_id: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> web.Application:
    api = _AlertsApi(engine, registry, settings, clock_ms, bot_username, reference_price, new_alert_id)
    app = web.Application(client_max_size=MAX_BODY_BYTES, middlewares=[api.rate_limit])
    app.router.add_get("/api/alerts/health", api.health)
    app.router.add_get("/api/alerts/push/key", api.push_key)
    app.router.add_post("/api/alerts/push/subscribe", api.push_subscribe)
    app.router.add_delete("/api/alerts/device", api.delete_device)  # до {drawing_id}: інакше «device» стало б id лінії
    app.router.add_post("/api/alerts/pair", api.pair)
    app.router.add_post("/api/alerts/pair/confirm", api.pair_confirm)
    app.router.add_get("/api/alerts/events", api.events)
    app.router.add_get("/api/alerts", api.list_alerts)
    app.router.add_put("/api/alerts/{drawing_id}", api.put_alert)
    app.router.add_delete("/api/alerts/{drawing_id}", api.delete_alert)
    return app


_HTTP_ERRORS: Dict[int, type] = {
    400: web.HTTPBadRequest,
    401: web.HTTPUnauthorized,
    403: web.HTTPForbidden,
    404: web.HTTPNotFound,
    409: web.HTTPConflict,
    410: web.HTTPGone,
    429: web.HTTPTooManyRequests,
    503: web.HTTPServiceUnavailable,
}


def _fail(status: int, code: str, **extra: Any) -> web.HTTPException:
    """JSON-відмова з кодом причини — сторінка показує причину, а не «щось не так»."""
    body = json.dumps({"error": code, **extra}, ensure_ascii=False)
    return _HTTP_ERRORS[status](text=body, content_type="application/json")


class _AlertsApi:
    def __init__(
        self,
        engine: AlertEngine,
        registry: DeviceRegistry,
        settings: ApiSettings,
        clock_ms: Callable[[], int],
        bot_username: Callable[[], Awaitable[str]],
        reference_price: Callable[[str], Awaitable[Optional[float]]],
        new_alert_id: Callable[[], str],
    ) -> None:
        self._engine, self._registry, self._settings, self._clock_ms = engine, registry, settings, clock_ms
        self._bot_username, self._reference_price, self._new_alert_id = bot_username, reference_price, new_alert_id
        self._hits: Dict[str, Tuple[int, int]] = {}  # IP → (хвилина, запитів)
        self._streams = EventStreams(heartbeat_s=settings.sse_heartbeat_s)
        engine.listeners.append(self._on_engine_event)

    # ── захист ───────────────────────────────────────────────────────────

    @web.middleware
    async def rate_limit(self, request: web.Request, handler: Callable) -> web.StreamResponse:
        if request.path == "/api/alerts/events":  # довгий потік — не запити
            return await handler(request)
        ip = self._client_ip(request)
        minute = self._clock_ms() // 60_000
        window, count = self._hits.get(ip, (minute, 0))
        if window != minute:
            window, count = minute, 0
        if count >= self._settings.requests_per_min:
            raise _fail(429, "rate_limited")
        if len(self._hits) > 10_000:  # стара хвилина нікому не потрібна — пам'ять не росте від потоку IP
            self._hits = {k: v for k, v in self._hits.items() if v[0] == minute}
        self._hits[ip] = (window, count + 1)
        return await handler(request)

    def _require_csrf(self, request: web.Request) -> None:
        if request.headers.get(CSRF_HEADER) != "1":
            raise _fail(403, "csrf_header")
        origin = request.headers.get("Origin")
        if origin is not None and origin != self._settings.site_origin:
            raise _fail(403, "origin")

    def _device(self, request: web.Request) -> Device:
        token = request.cookies.get(COOKIE_NAME)
        device = self._registry.authenticate(token) if token else None
        if device is None:
            raise _fail(401, "not_paired")
        return self._registry.touch(device, now_ms=self._clock_ms())

    def _require_channel(self, channel: str) -> None:
        if self._settings.channel != channel:
            raise _fail(404, f"{channel}_disabled")

    @staticmethod
    def _client_ip(request: web.Request) -> str:
        return request.headers.get("X-Real-IP") or request.remote or "?"

    @staticmethod
    def _label(request: web.Request) -> str:
        device_class = classify_device(request.headers.get("User-Agent", ""))
        return f"{device_class['os']} · {device_class['browser']}"

    @staticmethod
    def _set_device_cookie(response: web.Response, raw_token: str) -> None:
        response.set_cookie(
            COOKIE_NAME,
            raw_token,
            path=COOKIE_PATH,
            max_age=COOKIE_MAX_AGE_S,
            httponly=True,
            secure=True,
            samesite="Strict",
        )

    async def _json_object(self, request: web.Request) -> Dict[str, Any]:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            raise _fail(400, "bad_json") from None
        if not isinstance(body, dict):
            raise _fail(400, "bad_json")
        return body

    # ── стан і прив'язка ─────────────────────────────────────────────────

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"enabled": True, "channel": self._settings.channel, "degraded": sorted(self._engine.degraded)}
        )

    async def push_key(self, request: web.Request) -> web.Response:
        self._require_channel("push")
        return web.json_response({"public_key": self._settings.push_public_key})

    async def push_subscribe(self, request: web.Request) -> web.Response:
        """Підписка з `PushSubscription.toJSON()` браузера. Своя сторінка (cookie web-пристрою) — оновлення підписки;
        інакше — новий пристрій і cookie. Ліміти нових пристроїв: з IP — 429, усього — 503."""
        self._require_channel("push")
        self._require_csrf(request)
        try:
            push = PushSubscription.parse(await self._json_object(request), self._settings.push_host_suffixes)
        except ValueError as exc:
            _log.info("ALERTS_PUSH_BAD_SUBSCRIPTION reason=%s", exc)
            raise _fail(400, "bad_subscription") from None
        try:
            device, raw_token = self._registry.subscribe_web(
                request.cookies.get(COOKIE_NAME),
                push,
                self._label(request),
                ip=self._client_ip(request),
                now_ms=self._clock_ms(),
            )
        except DeviceLimitError as exc:
            raise _fail(429 if exc.code == "devices_per_ip" else 503, exc.code) from None
        response = web.json_response({"device": {"device_id": device.device_id, "label": device.label}})
        if raw_token is not None:
            self._set_device_cookie(response, raw_token)
        return response

    async def delete_device(self, request: web.Request) -> web.Response:
        self._require_csrf(request)
        device = self._device(request)
        removed = forget_device(self._engine, self._registry, device)
        response = web.json_response({"removed_alerts": removed})
        response.del_cookie(COOKIE_NAME, path=COOKIE_PATH, secure=True, httponly=True, samesite="Strict")
        return response

    async def pair(self, request: web.Request) -> web.Response:
        self._require_channel("telegram")
        self._require_csrf(request)
        token = request.cookies.get(COOKIE_NAME)
        if token and self._registry.authenticate(token) is not None:
            raise _fail(409, "already_paired")  # нова прив'язка затерла б cookie робочого пристрою
        try:
            username = await self._bot_username()
        except TelegramError as exc:
            _log.warning("ALERTS_PAIR_BOT_UNAVAILABLE err=%s", exc)
            raise _fail(503, "bot_unavailable") from None
        label = self._label(request)
        link_code, raw_token = self._registry.start_pairing(
            label, now_ms=self._clock_ms(), ttl_ms=self._settings.pair_ttl_ms
        )
        response = web.json_response(
            {
                "link": f"https://t.me/{username}?start={link_code}",
                "label": label,
                "expires_in_s": self._settings.pair_ttl_ms // 1000,
            }
        )
        self._set_device_cookie(response, raw_token)
        return response

    async def pair_confirm(self, request: web.Request) -> web.Response:
        self._require_channel("telegram")
        self._require_csrf(request)
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            raise _fail(401, "not_pairing")
        code = (await self._json_object(request)).get("code")
        if not isinstance(code, str):
            raise _fail(400, "bad_code")
        result = self._registry.confirm(token, re.sub(r"[\s-]", "", code), now_ms=self._clock_ms())
        payload: Dict[str, Any] = {"status": result.outcome.value}
        if result.device is not None:
            payload["device"] = {"device_id": result.device.device_id, "label": result.device.label}
        if result.outcome is ConfirmOutcome.WRONG_CODE:
            payload["attempts_left"] = result.attempts_left
        return web.json_response(payload, status=_CONFIRM_STATUS[result.outcome])

    # ── сповіщення ───────────────────────────────────────────────────────

    async def list_alerts(self, request: web.Request) -> web.Response:
        device = self._device(request)
        mine = [a for a in self._engine.store.alerts.values() if a.subscriber_id == device.subscriber_id]
        mine.sort(key=lambda a: a.created_at_ms)
        # push_gone — підписка цього браузера зникла (404/410): сторінка просить увімкнути сповіщення знову
        about = {"device_id": device.device_id, "label": device.label, "push_gone": device.push_gone}
        return web.json_response(
            {"alerts": [self._wire(a) for a in mine], "device": about, "degraded": sorted(self._engine.degraded)}
        )

    async def put_alert(self, request: web.Request) -> web.Response:
        """Створити, пересунути (новий рівень — нове взведення) чи знову взвести (`rearm`). Той самий рівень без
        `rearm` — без змін: сторінка шле PUT після кожного збереження фігур, і спрацьоване не має «оживати» саме."""
        self._require_csrf(request)
        device = self._device(request)
        drawing_id = self._drawing_id(request)
        symbol, level, tf_label, anchor_ms, rearm = self._parse_put(await self._json_object(request))
        now = self._clock_ms()
        existing = self._find(device.subscriber_id, drawing_id)
        if existing is not None and existing.symbol != symbol:
            raise _fail(409, "symbol_mismatch")
        if existing is not None and not self._needs_arming(existing, level, anchor_ms, rearm, now):
            return self._alert_response(existing)
        adds_armed = existing is None or existing.state is AlertState.FIRED
        if adds_armed and self._armed_count(device) >= self._settings.max_active_per_subscriber:
            raise _fail(409, "limit", max_active=self._settings.max_active_per_subscriber)
        price = await self._price(symbol)
        active_from = max(now, anchor_ms or 0)  # перетин у минулому не рахується; промінь з майбутнього — з якоря
        if existing is not None:
            alert = dataclasses.replace(
                arm(existing, level=level, reference_price=price, active_from_ms=active_from), tf_label=tf_label
            )
        else:
            alert = PriceAlert(
                alert_id=self._new_alert_id(),
                drawing_id=drawing_id,
                subscriber_id=device.subscriber_id,
                symbol=symbol,
                level=level,
                tf_label=tf_label,
                active_from_ms=active_from,
                created_at_ms=now,
                side=side_of(level, price),
            )
        self._engine.put_alert(alert)
        return self._alert_response(alert)

    async def delete_alert(self, request: web.Request) -> web.Response:
        self._require_csrf(request)
        device = self._device(request)
        existing = self._find(device.subscriber_id, self._drawing_id(request))
        removed = existing is not None and self._engine.remove_alert(existing.alert_id)
        return web.json_response({"removed": removed, "degraded": sorted(self._engine.degraded)})

    # ── SSE ──────────────────────────────────────────────────────────────

    async def events(self, request: web.Request) -> web.StreamResponse:
        return await self._streams.serve(request, self._device(request).subscriber_id)

    def _on_engine_event(self, name: str, alert: PriceAlert) -> None:
        self._streams.publish(alert.subscriber_id, name, self._wire(alert))

    # ── допоміжне ────────────────────────────────────────────────────────

    def _parse_put(self, body: Dict[str, Any]) -> Tuple[str, float, str, Optional[int], bool]:
        symbol, tf_label = body.get("symbol"), body.get("tf_label")
        anchor_ms, rearm = body.get("active_from_ms"), body.get("rearm", False)
        if not isinstance(symbol, str) or symbol not in self._settings.symbols:
            raise _fail(400, "symbol")
        try:
            level = validate_price("level", body.get("level"))
        except ValueError:
            raise _fail(400, "level") from None
        if not isinstance(tf_label, str) or not _TF_LABEL_RE.match(tf_label):
            raise _fail(400, "tf_label")
        if anchor_ms is not None and (isinstance(anchor_ms, bool) or not isinstance(anchor_ms, int) or anchor_ms < 0):
            raise _fail(400, "active_from_ms")
        if not isinstance(rearm, bool):
            raise _fail(400, "rearm")
        return symbol, level, tf_label, anchor_ms, rearm

    @staticmethod
    def _needs_arming(existing: PriceAlert, level: float, anchor_ms: Optional[int], rearm: bool, now: int) -> bool:
        anchor_moved = anchor_ms is not None and anchor_ms > now and anchor_ms != existing.active_from_ms
        return rearm or level != existing.level or anchor_moved

    async def _price(self, symbol: str) -> float:
        try:
            price = await self._reference_price(symbol)
        except M1ReadError as exc:
            _log.warning("ALERTS_REFERENCE_PRICE_FAILED sym=%s err=%s", symbol, exc)
            raise _fail(503, "price_unavailable") from None
        if price is None:
            raise _fail(409, "no_price")  # нема з чим порівняти бік — краще відмова, ніж тихе хибне взведення
        return price

    def _drawing_id(self, request: web.Request) -> str:
        drawing_id = request.match_info["drawing_id"]
        if not _DRAWING_ID_RE.match(drawing_id):
            raise _fail(400, "drawing_id")
        return drawing_id

    def _find(self, subscriber_id: str, drawing_id: str) -> Optional[PriceAlert]:
        alerts = self._engine.store.alerts.values()
        return next((a for a in alerts if a.subscriber_id == subscriber_id and a.drawing_id == drawing_id), None)

    def _armed_count(self, device: Device) -> int:
        alerts = self._engine.store.alerts.values()
        return sum(a.subscriber_id == device.subscriber_id and a.state is AlertState.ARMED for a in alerts)

    def _wire(self, alert: PriceAlert) -> Dict[str, Any]:
        wire = alert_to_dict(alert)
        del wire["subscriber_id"]
        if alert.fired is not None:
            entry = self._engine.store.outbox.get((alert.alert_id, alert.version))
            wire["delivery"] = "delivered" if entry is None else ("failed" if entry.failed else "pending")
        else:
            wire["delivery"] = None
        return wire

    def _alert_response(self, alert: PriceAlert) -> web.Response:
        return web.json_response({"alert": self._wire(alert), "degraded": sorted(self._engine.degraded)})
