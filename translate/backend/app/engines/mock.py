"""Детермінований рушій-заглушка. Без моделей, без мережі.

Призначення: тести, CI, smoke-перевірка повного конвеєра (API -> кеш ->
рушій) до встановлення важких залежностей. Не для продакшену.
"""

from __future__ import annotations

from .base import TranslationEngine


class MockEngine(TranslationEngine):
    name = "mock"

    def translate_sentence(self, text: str, source: str, target: str) -> str:
        # Детермінований маркер — достатньо, щоб перевірити маршрутизацію/кеш.
        return f"[{source}->{target}] {text}"
