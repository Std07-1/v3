from __future__ import annotations

from aiohttp.test_utils import TestClient, TestServer
import pytest_asyncio


@pytest_asyncio.fixture
async def aiohttp_client():
    clients: list[TestClient] = []

    async def factory(app):
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        clients.append(client)
        return client

    try:
        yield factory
    finally:
        for client in reversed(clients):
            await client.close()
