"""Потоки подій для відкритих сторінок (ADR-0114 §4.4): SSE `fired` / `delivered` по підписнику + heartbeat.

Heartbeat — коментар `: ping` кожні `heartbeat_s` (Cloudflare рве тихе з'єднання через 100 с). Черга потоку обмежена:
повільний клієнт не роздуває пам'ять — потік закривається, сторінка перепідключається і перечитує список.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Set, Tuple

from aiohttp import web

_log = logging.getLogger(__name__)

QUEUE_MAX = 100

_Event = Optional[Tuple[str, Dict[str, Any]]]  # None — сигнал «закрити потік»


class EventStreams:
    def __init__(self, *, heartbeat_s: float) -> None:
        self._heartbeat_s = heartbeat_s
        self._queues: Dict[str, Set["asyncio.Queue[_Event]"]] = {}

    def publish(self, subscriber_id: str, name: str, payload: Dict[str, Any]) -> None:
        for queue in list(self._queues.get(subscriber_id, ())):
            try:
                queue.put_nowait((name, payload))
            except asyncio.QueueFull:
                _log.warning("ALERTS_SSE_OVERFLOW subscriber=%s — потік закрито", subscriber_id)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)

    def open_count(self, subscriber_id: str) -> int:
        return len(self._queues.get(subscriber_id, ()))

    async def serve(self, request: web.Request, subscriber_id: str) -> web.StreamResponse:
        queue: "asyncio.Queue[_Event]" = asyncio.Queue(maxsize=QUEUE_MAX)
        queues = self._queues.setdefault(subscriber_id, set())
        queues.add(queue)
        response = web.StreamResponse(
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )
        try:
            await response.prepare(request)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=self._heartbeat_s)
                except asyncio.TimeoutError:
                    await response.write(b": ping\n\n")
                    continue
                if event is None:
                    break
                name, payload = event
                await response.write(f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
        except ConnectionResetError:
            _log.debug("ALERTS_SSE_CLOSED subscriber=%s", subscriber_id)
        finally:
            queues.discard(queue)
        return response
