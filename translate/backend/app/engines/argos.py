"""Рушій Argos Translate — легкий локальний fallback.

Простий у встановленні, без GPU. Якість для UA<->CS/HU посередня, тож
це запасний варіант, а не основний. Канонічні ISO-коди збігаються з
кодами Argos (uk/en/cs/hu/ru), тож мапінг тривіальний.
"""

from __future__ import annotations

from typing import Any

from .base import EngineError, TranslationEngine


class ArgosEngine(TranslationEngine):
    name = "argos"

    def __init__(self) -> None:
        self._translate_fn: Any | None = None

    def warmup(self) -> None:
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        if self._translate_fn is not None:
            return
        try:
            import argostranslate.translate  # type: ignore
        except ImportError as exc:
            raise EngineError(
                "Не встановлено argostranslate. "
                "pip install -r requirements-argos.txt"
            ) from exc
        self._translate_fn = argostranslate.translate.translate

    def translate_sentence(self, text: str, source: str, target: str) -> str:
        self._ensure_loaded()
        assert self._translate_fn is not None
        return self._translate_fn(text, source, target)
