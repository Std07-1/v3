"""Рушій NLLB-200 distilled-600M через CTranslate2 (int8).

Основний прод-рушій: найкращий баланс швидкість/якість для UA<->CS/HU.
Потребує заздалегідь сконвертованої моделі (див. scripts/convert_nllb.sh)
та залежностей requirements-nllb.txt. Імпорти важких пакетів — ліниві,
щоб mock/argos працювали без них.
"""

from __future__ import annotations

import os
from typing import Any

from .. import languages
from .base import EngineError, TranslationEngine


class NllbCt2Engine(TranslationEngine):
    name = "nllb"

    def __init__(
        self,
        model_dir: str,
        tokenizer_id: str,
        compute_type: str = "int8",
        device: str = "cpu",
        inter_threads: int = 1,
        intra_threads: int = 0,
    ) -> None:
        self._model_dir = model_dir
        self._tokenizer_id = tokenizer_id
        self._compute_type = compute_type
        self._device = device
        self._inter_threads = inter_threads
        self._intra_threads = intra_threads
        self._translator: Any | None = None
        self._tokenizer: Any | None = None

    def warmup(self) -> None:
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        if self._translator is not None:
            return
        if not os.path.isdir(self._model_dir):
            raise EngineError(
                f"Модель NLLB не знайдена: {self._model_dir!r}. "
                "Сконвертуй її через scripts/convert_nllb.sh."
            )
        try:
            import ctranslate2  # type: ignore
            import transformers  # type: ignore
        except ImportError as exc:  # гучно: бракує важких залежностей
            raise EngineError(
                "Не встановлено ctranslate2/transformers. "
                "pip install -r requirements-nllb.txt"
            ) from exc

        self._translator = ctranslate2.Translator(
            self._model_dir,
            device=self._device,
            compute_type=self._compute_type,
            inter_threads=self._inter_threads,
            intra_threads=self._intra_threads,
        )
        self._tokenizer = transformers.AutoTokenizer.from_pretrained(
            self._tokenizer_id
        )

    def translate_sentence(self, text: str, source: str, target: str) -> str:
        self._ensure_loaded()
        assert self._translator is not None and self._tokenizer is not None

        src_flores = languages.flores(source)
        tgt_flores = languages.flores(target)

        self._tokenizer.src_lang = src_flores
        tokens = self._tokenizer.convert_ids_to_tokens(
            self._tokenizer.encode(text)
        )
        results = self._translator.translate_batch(
            [tokens],
            target_prefix=[[tgt_flores]],
            beam_size=2,
        )
        # Перший токен у гіпотезі — це тег цільової мови, його відкидаємо.
        hypothesis = results[0].hypotheses[0][1:]
        target_ids = self._tokenizer.convert_tokens_to_ids(hypothesis)
        return self._tokenizer.decode(target_ids, skip_special_tokens=True)
