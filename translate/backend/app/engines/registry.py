"""Фабрика рушіїв за іменем з налаштувань. Єдине місце вибору рушія."""

from __future__ import annotations

from ..config import Settings
from .argos import ArgosEngine
from .base import EngineError, TranslationEngine
from .mock import MockEngine
from .nllb_ct2 import NllbCt2Engine


def build_engine(settings: Settings) -> TranslationEngine:
    name = settings.engine.lower()
    if name == "mock":
        return MockEngine()
    if name == "argos":
        return ArgosEngine()
    if name == "nllb":
        return NllbCt2Engine(
            model_dir=settings.nllb_model_dir,
            tokenizer_id=settings.nllb_tokenizer,
            compute_type=settings.nllb_compute_type,
            device=settings.nllb_device,
            inter_threads=settings.nllb_inter_threads,
            intra_threads=settings.nllb_intra_threads,
        )
    raise EngineError(
        f"Невідомий рушій {settings.engine!r}. Доступні: mock | nllb | argos"
    )
