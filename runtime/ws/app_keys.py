"""SSOT-визначення AppKey для aiohttp.web.Application у v3 платформі.

Чому окремий модуль:
    Коли `runtime.ws.ws_server` запускається через `python -m runtime.ws.ws_server`,
    Python завантажує файл як модуль `__main__` і створює один набір AppKey-інстансів.
    Якщо інший модуль (наприклад, `runtime.api_v3.endpoints`) робить
    `from runtime.ws.ws_server import APP_FULL_CONFIG`, Python імпортує файл вдруге
    як `runtime.ws.ws_server` — і отримує **інші** AppKey-інстанси.

    `aiohttp.web.AppKey` хешується по identity (а не по name), тому
    `app[APP_FULL_CONFIG_main]` НЕ доступний як `app.get(APP_FULL_CONFIG_pkg)`.
    Результат: handler бачить порожній dict / None навіть коли init фактично виконався.

    Цей файл ніколи не запускається як `__main__`, тому імпортується завжди один раз
    і всі AppKey-інстанси — singletons на весь процес.

Type-параметри AppKey свідомо `Any` для типів з runtime-залежностями (Redis/UDS/SMC),
щоб уникнути circular imports. Точне типування лишається в ws_server.py через Protocol.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from aiohttp import web

# ── Core lifecycle ───────────────────────────────────────────────────────
APP_HEARTBEAT_S = web.AppKey("heartbeat_s", int)
APP_DELTA_POLL_S = web.AppKey("delta_poll_s", float)
APP_WS_SESSIONS = web.AppKey("ws_sessions", dict)
APP_CONFIG_PATH = web.AppKey("config_path", str)
APP_BOOT_ID = web.AppKey("boot_id", str)

# ── Config / SSOT (read by /api/v3 validators) ────────────────────────────
APP_FULL_CONFIG = web.AppKey("full_config", dict)
APP_SYMBOLS_SET = web.AppKey("symbols_set", set)
APP_TF_ALLOWLIST = web.AppKey("tf_allowlist", set)
APP_PREVIEW_TF_SET = web.AppKey("preview_tf_set", set)
APP_D1_TICK_RELAY_TFS = web.AppKey("d1_tick_relay_tfs", set)

# ── Redis (tick stream) ───────────────────────────────────────────────────
APP_TICK_REDIS_CLIENT = web.AppKey("tick_redis_client", object)
APP_TICK_REDIS_NS = web.AppKey("tick_redis_ns", str)

# ── UDS / SMC engine handles ──────────────────────────────────────────────
APP_UDS_EXECUTOR = web.AppKey("uds_executor", ThreadPoolExecutor)
APP_UDS = web.AppKey("uds", object)
APP_SMC_RUNNER = web.AppKey("smc_runner", object)

# ── HTTP plumbing ─────────────────────────────────────────────────────────
APP_CORS_ORIGINS = web.AppKey("cors_origins", set)

# ── Background tasks ──────────────────────────────────────────────────────
APP_GLOBAL_DELTA_TASK = web.AppKey("global_delta_task", asyncio.Task)
APP_BG_SMC_TASK = web.AppKey("bg_smc_task", asyncio.Task)

# ── ADR-0049: Wake Engine ─────────────────────────────────────────────────
APP_WAKE_ENGINE = web.AppKey("wake_engine", object)
