"""Шар кешу перекладів. Абстракція з трьома бекендами:

- RedisCache  — прод, спільний між процесами, з TTL.
- MemoryCache — dev/тести, у пам'яті процесу.
- NullCache   — кеш вимкнено.

Принцип degraded-but-loud: якщо Redis недоступний — гучний лог і
прозоре падіння на MemoryCache, але ніколи не тихе ковтання помилки.
"""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("translate.cache")


def make_key(namespace: str, engine: str, source: str, target: str, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{namespace}:{engine}:{source}:{target}:{digest}"


class CacheBackend(ABC):
    @abstractmethod
    async def get(self, key: str) -> str | None: ...

    @abstractmethod
    async def set(self, key: str, value: str) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @property
    @abstractmethod
    def label(self) -> str: ...


class NullCache(CacheBackend):
    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str) -> None:
        return None

    async def close(self) -> None:
        return None

    @property
    def label(self) -> str:
        return "disabled"


class MemoryCache(CacheBackend):
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str) -> None:
        self._store[key] = value

    async def close(self) -> None:
        self._store.clear()

    @property
    def label(self) -> str:
        return "memory"


class RedisCache(CacheBackend):
    def __init__(self, client: Any, ttl_seconds: int) -> None:
        self._client = client
        self._ttl = ttl_seconds

    async def get(self, key: str) -> str | None:
        try:
            return await self._client.get(key)
        except Exception as exc:  # gучно, але не валимо запит
            logger.warning("Redis GET збій, працюю без кешу: %s", exc)
            return None

    async def set(self, key: str, value: str) -> None:
        try:
            await self._client.set(key, value, ex=self._ttl)
        except Exception as exc:
            logger.warning("Redis SET збій, пропускаю запис у кеш: %s", exc)

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001 — закриття не критичне
            pass

    @property
    def label(self) -> str:
        return "redis"


async def build_cache(redis_url: str, ttl_seconds: int) -> CacheBackend:
    """Створює бекенд кешу. За недоступного Redis — гучний fallback у memory."""
    if not redis_url:
        logger.info("REDIS_URL не задано — використовую in-memory кеш")
        return MemoryCache()
    try:
        import redis.asyncio as aioredis  # type: ignore

        client = aioredis.from_url(redis_url, decode_responses=True)
        await client.ping()
        logger.info("Підключено Redis-кеш: %s", redis_url)
        return RedisCache(client, ttl_seconds)
    except Exception as exc:  # degraded-but-loud
        logger.warning(
            "Redis недоступний (%s) — падаю на in-memory кеш. Причина: %s",
            redis_url,
            exc,
        )
        return MemoryCache()
