"""Процес `smc-alerts` (ADR-0114 §4.3): `python -m runtime.alerts [--config config.json]`.

Збирає стан (`alerts.json`, `devices.json`), двигун, джерела цін (PubSub тіків, фінальні M1 з UDS), доставку і
HTTP на 127.0.0.1:`alerts.port` (nginx `/api/alerts/` сайту). Канал — рівно один: `alerts.telegram_enabled=false` —
пуш у браузер (Rev 2: ключ VAPID, доставка з чергою на пристрій, прибирання неактивних), `true` — бот Telegram (Rev 1:
доставка і команди). `alerts.enabled=false` — живий процес
лише з health `{"enabled": false}`, як smc-agent-bridge (ADR-0090): supervisor бачить RUNNING, а не BACKOFF.
Неготовність на старті (нема секретів, теки стану, каналу тіків, знаків символу) — вихід з причиною, а не
напівживий процес, що мовчки не сповіщає.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

import aiohttp
import redis.asyncio as aredis
from aiohttp import web
from redis.exceptions import RedisError

from core.config_loader import env_str, load_system_config, resolve_config_path
from env_profile import load_env_secrets
from runtime.alerts.app import ApiSettings, build_app
from runtime.alerts.config import AlertsConfig
from runtime.alerts.devices import DEVICES_FILE, DeviceRegistry, WebLimits
from runtime.alerts.engine import AlertEngine
from runtime.alerts.feeds import (
    parse_tick,
    run_m1_poller,
    run_tick_feed,
    uds_final_m1_reader,
    uds_last_close_reader,
)
from runtime.alerts.messages import require_digits
from runtime.alerts.push_delivery import PushDelivery, run_device_pruning
from runtime.alerts.store import STATE_FILE, AlertStore
from runtime.alerts.telegram import CommandHandler, Delivery, TelegramClient, run_commands
from runtime.alerts.webpush import WebPushSender, application_server_key, load_vapid
from runtime.ingest.tick_common import pick_tick_channel, symbols_from_cfg
from runtime.store.redis_keys import symbol_key
from runtime.store.redis_spec import resolve_redis_spec
from runtime.store.uds import build_uds_from_config

_log = logging.getLogger(__name__)

DEGRADED_STATE_QUARANTINED = "state_quarantined"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="smc-alerts — лінія-сповіщення (ADR-0114)")
    parser.add_argument("--config", type=str, default="config.json")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config_path = resolve_config_path(args.config)
    cfg = load_system_config(config_path)
    settings = AlertsConfig.from_cfg(cfg)
    app = build_service(cfg, config_path, settings) if settings.enabled else _disabled_app()
    _log.info("ALERTS_STARTING enabled=%s port=%s", settings.enabled, settings.port)
    web.run_app(app, host="127.0.0.1", port=settings.port, access_log=None, print=None, reuse_address=True)
    return 0


def build_service(cfg: Mapping[str, Any], config_path: str, settings: AlertsConfig) -> web.Application:
    symbols = symbols_from_cfg(dict(cfg))
    require_digits(symbols, (cfg.get("price_display") or {}).get("digits_by_symbol") or {})
    digits = cfg["price_display"]["digits_by_symbol"]
    channel = pick_tick_channel(dict(cfg))
    redis_spec = resolve_redis_spec(dict(cfg), role="alerts", log=True)
    state_dir = Path(settings.state_dir)
    if channel is None or redis_spec is None:
        raise SystemExit("ALERTS_NO_TICKS: нема channels.price_tick або redis у config.json")
    if not state_dir.is_dir():
        raise SystemExit(f"ALERTS_STATE_DIR_MISSING: {state_dir} (створює ops: install -d -o smc -g smc -m 750)")
    push = not settings.telegram_enabled
    if push:
        vapid_key, public_key = _read_vapid_key(settings.env_file)
    else:
        token, owner_chat_id = _read_telegram_secrets(settings.env_file)

    store = AlertStore.load(state_dir / STATE_FILE, now_ms=_now_ms())
    registry = DeviceRegistry.load(
        state_dir / DEVICES_FILE,
        confirm_attempts=settings.pair_confirm_attempts,
        max_pending=settings.pair_max_pending,
        web_limits=WebLimits(settings.max_devices, settings.devices_per_ip_per_hour) if push else None,
    )
    engine = AlertEngine(store, gap_after_ms=settings.gap_after_s * 1000, clock_ms=_now_ms)
    for problem in store.problems:
        engine.set_degraded(DEGRADED_STATE_QUARANTINED, problem)
    uds = build_uds_from_config(
        config_path, str(cfg.get("data_root", "./data_v3")), f"alerts-{os.getpid()}", role="reader"
    )
    read_final_m1, read_last_close = uds_final_m1_reader(uds), uds_last_close_reader(uds)
    redis_client = aredis.Redis(
        host=redis_spec.host, port=redis_spec.port, db=redis_spec.db, **redis_spec.auth_kwargs()
    )
    runtime: Dict[str, Any] = {}

    async def bot_username() -> str:
        if "username" not in runtime:
            runtime["username"] = await runtime["telegram"].get_username()
        return runtime["username"]

    async def reference_price(symbol: str) -> Optional[float]:
        """Ціна для боку при взведенні: тік цього процесу → `tick:last` у Redis → close останньої фінальної M1."""
        price = engine.last_price(symbol)
        if price is not None:
            return price
        try:
            raw = await redis_client.get(f"{redis_spec.namespace}:tick:last:{symbol_key(symbol)}")
            tick = parse_tick(raw, frozenset({symbol})) if raw is not None else None
        except (RedisError, OSError, ValueError) as exc:
            _log.warning("ALERTS_TICK_LAST_UNAVAILABLE sym=%s err=%s — беремо close M1", symbol, exc)
            tick = None
        if tick is not None:
            return tick[1]
        return await asyncio.get_running_loop().run_in_executor(None, read_last_close, symbol, _now_ms())

    async def open_pubsub() -> Any:
        pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(channel)
        return pubsub

    api_settings = ApiSettings(
        symbols=frozenset(symbols),
        site_origin=settings.site_url,
        pair_ttl_ms=settings.pair_code_ttl_s * 1000,
        max_active_per_subscriber=settings.max_active_per_subscriber,
        requests_per_min=settings.requests_per_min,
        sse_heartbeat_s=settings.sse_heartbeat_s,
        channel="push" if push else "telegram",
        push_host_suffixes=settings.push_host_suffixes,
        push_public_key=public_key if push else None,
    )
    app = build_app(
        engine, registry, api_settings, clock_ms=_now_ms, bot_username=bot_username, reference_price=reference_price
    )

    def push_tasks(session: aiohttp.ClientSession, tz: ZoneInfo) -> List[Any]:
        sender = WebPushSender(
            session,
            vapid_key,
            settings.vapid_subject,
            ttl_s=settings.push_ttl_s,
            timeout_s=settings.push_timeout_s,
            host_suffixes=settings.push_host_suffixes,
        )
        delivery = PushDelivery(
            engine,
            registry,
            sender,
            digits=digits,
            tz=tz,
            site_url=settings.site_url,
            clock_ms=_now_ms,
            ttl_s=settings.push_ttl_s,
            retry_first_s=settings.retry_first_s,
            retry_max_s=settings.retry_max_s,
        )
        idle_ms = settings.device_idle_days * 86_400_000
        return [
            delivery.run(poll_s=settings.delivery_poll_s),
            run_device_pruning(engine, registry, idle_ms=idle_ms, every_s=settings.device_prune_s, clock_ms=_now_ms),
        ]

    def telegram_tasks(session: aiohttp.ClientSession, tz: ZoneInfo) -> List[Any]:
        telegram = TelegramClient(session, token, api_base=settings.telegram_api)
        runtime["telegram"] = telegram
        delivery = Delivery(
            engine,
            telegram,
            digits=digits,
            tz=tz,
            site_url=settings.site_url,
            clock_ms=_now_ms,
            retry_first_s=settings.retry_first_s,
            retry_max_s=settings.retry_max_s,
        )
        handler = CommandHandler(
            registry,
            engine,
            owner_chat_id=owner_chat_id,
            digits=digits,
            tz=tz,
            clock_ms=_now_ms,
            pair_ttl_ms=settings.pair_code_ttl_s * 1000,
        )
        return [
            delivery.run(poll_s=settings.delivery_poll_s),
            run_commands(
                telegram,
                handler,
                engine,
                poll_timeout_s=settings.telegram_poll_timeout_s,
                error_pause_s=settings.telegram_error_pause_s,
            ),
        ]

    async def start_background(_app: web.Application) -> None:
        tz = ZoneInfo(settings.display_tz)
        session = aiohttp.ClientSession()
        runtime["session"] = session
        channel_tasks = push_tasks(session, tz) if push else telegram_tasks(session, tz)
        runtime["tasks"] = [
            asyncio.create_task(
                run_tick_feed(open_pubsub, engine, frozenset(symbols), reconnect_s=settings.tick_reconnect_s)
            ),
            asyncio.create_task(
                run_m1_poller(
                    read_final_m1,
                    engine,
                    symbols,
                    poll_s=settings.m1_poll_s,
                    final_lag_ms=settings.m1_final_lag_s * 1000,
                    chunk_bars=settings.m1_chunk_bars,
                    clock_ms=_now_ms,
                )
            ),
            *(asyncio.create_task(coro) for coro in channel_tasks),
        ]
        for task in runtime["tasks"]:
            task.add_done_callback(_loud_task_end)
        _log.info(
            "ALERTS_READY channel=%s symbols=%d alerts=%d devices=%d",
            "push" if push else "telegram",
            len(symbols),
            len(store.alerts),
            len(registry.devices),
        )

    async def stop_background(_app: web.Application) -> None:
        tasks = runtime.get("tasks", [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if "session" in runtime:
            await runtime["session"].close()
        await redis_client.aclose()

    app.on_startup.append(start_background)
    app.on_cleanup.append(stop_background)
    return app


def _loud_task_end(task: "asyncio.Task[Any]") -> None:
    """Фонові цикли нескінченні: завершення без cancel — це збій (I5), а не тиха смерть сповіщень."""
    if task.cancelled():
        return
    _log.critical("ALERTS_BACKGROUND_TASK_DIED task=%s err=%r", task.get_coro(), task.exception())


def _load_env(env_file: str) -> None:
    report = load_env_secrets(env_path=env_file)
    if not report.loaded:
        _log.warning("ALERTS_ENV_FILE_NOT_LOADED path=%s — секрети лише з оточення процесу", env_file)


def _read_vapid_key(env_file: str) -> Tuple[str, str]:
    """Пуш (Rev 2): приватний ключ VAPID і публічний для сторінки. Нема чи кривий — процес не стартує."""
    _load_env(env_file)
    key = env_str("ALERTS_VAPID_PRIVATE_KEY")
    if not key:
        raise SystemExit(
            "ALERTS_SECRETS_MISSING: потрібен ALERTS_VAPID_PRIVATE_KEY — sudo .venv/bin/python -m runtime.alerts.vapid_keys"
        )
    try:
        return key, application_server_key(load_vapid(key))
    except ValueError as exc:
        raise SystemExit(f"ALERTS_SECRETS_INVALID: ALERTS_VAPID_PRIVATE_KEY — {exc}") from None


def _read_telegram_secrets(env_file: str) -> Tuple[str, int]:
    """Telegram (Rev 1, `telegram_enabled=true`): токен бота і числовий чат власника."""
    _load_env(env_file)
    token, owner = env_str("ALERTS_TELEGRAM_TOKEN"), env_str("ALERTS_OWNER_CHAT_ID")
    if not token or not owner or not owner.lstrip("-").isdigit():
        raise SystemExit("ALERTS_SECRETS_MISSING: потрібні ALERTS_TELEGRAM_TOKEN і числовий ALERTS_OWNER_CHAT_ID")
    return token, int(owner)


def _disabled_app() -> web.Application:
    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"enabled": False})

    app = web.Application()
    app.router.add_get("/api/alerts/health", health)
    return app


def _now_ms() -> int:
    return int(time.time() * 1000)
