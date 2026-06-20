"""Оркестрація перекладу: валідація -> кеш -> рушій -> кеш.

Тримає один інваріант: будь-який переклад проходить через цей сервіс,
рушій виконується в threadpool (CPU-bound), результат кешується.
"""

from __future__ import annotations

import anyio

from . import languages
from .cache import CacheBackend, make_key
from .detect import detect
from .engines import TranslationEngine


class TranslationError(ValueError):
    """Доменна помилка перекладу (непідтримувана мова, завеликий текст)."""


class TranslationResult:
    __slots__ = ("text", "source", "target", "engine", "cached")

    def __init__(
        self, text: str, source: str, target: str, engine: str, cached: bool
    ) -> None:
        self.text = text
        self.source = source
        self.target = target
        self.engine = engine
        self.cached = cached


class TranslationService:
    def __init__(
        self,
        engine: TranslationEngine,
        cache: CacheBackend,
        namespace: str,
        max_chars: int,
    ) -> None:
        self._engine = engine
        self._cache = cache
        self._namespace = namespace
        self._max_chars = max_chars

    async def translate(
        self, text: str, source: str, target: str
    ) -> TranslationResult:
        text = text.strip()
        if not text:
            raise TranslationError("Порожній текст")
        if len(text) > self._max_chars:
            raise TranslationError(
                f"Текст задовгий: {len(text)} > {self._max_chars} символів"
            )

        # Визначення джерела (auto -> евристика за алфавітом).
        if source == languages.AUTO:
            source = detect(text)
        if not languages.is_supported(source):
            raise TranslationError(f"Непідтримуване джерело: {source!r}")
        if not languages.is_supported(target):
            raise TranslationError(f"Непідтримувана ціль: {target!r}")

        if source == target:
            return TranslationResult(text, source, target, self._engine.name, False)

        key = make_key(self._namespace, self._engine.name, source, target, text)
        cached = await self._cache.get(key)
        if cached is not None:
            return TranslationResult(cached, source, target, self._engine.name, True)

        # CPU-bound — у воркер-потік, щоб не блокувати event loop.
        translated = await anyio.to_thread.run_sync(
            self._engine.translate, text, source, target
        )
        await self._cache.set(key, translated)
        return TranslationResult(
            translated, source, target, self._engine.name, False
        )
