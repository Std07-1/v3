"""Налаштування сервісу. Єдине джерело істини для конфігурації (через env)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфігурація backend. Усе перевизначається через env / .env."""

    model_config = SettingsConfigDict(
        env_prefix="TRANSLATE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Рушій перекладу ---
    # mock  — детермінований, без моделей (тести / CI / smoke).
    # nllb  — NLLB-200 distilled-600M через CTranslate2 (int8). Основний для прод.
    # argos — Argos Translate, легкий fallback.
    engine: str = Field(default="mock")

    # Шлях до сконвертованої CTranslate2-моделі (для engine=nllb).
    nllb_model_dir: str = Field(default="models/nllb-200-distilled-600M-int8")
    # HF-репозиторій токенайзера (для engine=nllb).
    nllb_tokenizer: str = Field(default="facebook/nllb-200-distilled-600M")
    # int8 | int8_float16 | float16 | float32 — компроміс швидкість/якість.
    nllb_compute_type: str = Field(default="int8")
    nllb_device: str = Field(default="cpu")
    # Кількість CPU-потоків на один переклад (0 = автовибір CTranslate2).
    nllb_inter_threads: int = Field(default=1)
    nllb_intra_threads: int = Field(default=0)

    # --- Кеш ---
    # Якщо порожньо — кеш у пам'яті процесу (in-memory). Інакше Redis URL.
    redis_url: str = Field(default="")
    cache_ttl_seconds: int = Field(default=60 * 60 * 24 * 30)  # 30 днів
    cache_namespace: str = Field(default="tr_v1")

    # --- Обмеження запиту ---
    max_chars: int = Field(default=5000)

    # --- CORS (для dev, коли фронт окремо) ---
    cors_origins: str = Field(default="*")

    @property
    def cors_origin_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if not raw or raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
