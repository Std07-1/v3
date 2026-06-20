"""Контракт рушія перекладу — вузька талія між сервісом і реалізацією.

Усі рушії приймають канонічні ISO-коди (uk/en/cs/hu/ru) і самі маплять їх
у власний формат. Це дозволяє міняти рушій одним env без правок сервісу.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

# Розбивка на речення для порядкового перекладу: моделі типу NLLB дають
# помітно кращий результат на реченні, ніж на цілому абзаці.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。…])\s+|\n+")


class EngineError(RuntimeError):
    """Помилка рушія (відсутня модель, непідтримувана пара тощо)."""


class TranslationEngine(ABC):
    """Базовий клас рушія. Реалізації перевизначають translate_sentence."""

    name: str = "base"

    def warmup(self) -> None:
        """Підвантажити модель у пам'ять (необов'язково). За замовч. — no-op."""

    def supports(self, source: str, target: str) -> bool:  # noqa: ARG002
        return True

    @abstractmethod
    def translate_sentence(self, text: str, source: str, target: str) -> str:
        """Переклад одного речення. Реалізується конкретним рушієм."""

    def translate(self, text: str, source: str, target: str) -> str:
        """Переклад довільного тексту: порядково по реченнях, зі збереженням
        структури абзаців."""
        if source == target:
            return text

        out_parts: list[str] = []
        for paragraph in text.split("\n"):
            if not paragraph.strip():
                out_parts.append(paragraph)
                continue
            sentences = [s for s in _SENTENCE_SPLIT.split(paragraph) if s.strip()]
            translated = [
                self.translate_sentence(s.strip(), source, target) for s in sentences
            ]
            out_parts.append(" ".join(translated))
        return "\n".join(out_parts)
