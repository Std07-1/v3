"""Спільні фікстури тестів. Рушій=mock, кеш=memory — без мережі й моделей."""

from __future__ import annotations

import os

import pytest

# Гарантуємо детермінований рушій ще до імпорту застосунку.
os.environ.setdefault("TRANSLATE_ENGINE", "mock")
os.environ.setdefault("TRANSLATE_REDIS_URL", "")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    # Контекст-менеджер запускає lifespan (ініціалізація сервісу/кешу).
    with TestClient(create_app()) as c:
        yield c
