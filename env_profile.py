"""Завантаження секретів із `.env` для репозиторію FXCM Connector.

Ціль:
- один файл `.env` із секретами (FXCM_USERNAME, FXCM_PASSWORD, канали тощо);
- пряме завантаження без посередників;
- цей модуль НЕ друкує значення змінних (щоб не витікали секрети).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]


@dataclass(frozen=True)
class EnvLoadReport:
    """Результат завантаження .env (без значень)."""

    path: Optional[Path]
    loaded: bool
    keys_count: int


def load_env_secrets(
    *,
    override: bool = False,
    env_path: str | Path = ".env",
) -> EnvLoadReport:
    """Завантажує секрети з одного `.env` файлу.

    Args:
        override: Якщо True — значення з файлу перекривають поточне ENV.
        env_path: Шлях до `.env` файлу.

    Returns:
        EnvLoadReport: Шлях, чи завантажено, кількість ключів.
    """

    if load_dotenv is None:
        return EnvLoadReport(path=None, loaded=False, keys_count=0)

    resolved = Path(env_path).resolve()
    if not resolved.exists():
        return EnvLoadReport(path=None, loaded=False, keys_count=0)

    # Підрахуємо кількість ключів у файлі (до завантаження)
    keys_count = 0
    try:
        for line in resolved.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                keys_count += 1
    except Exception:
        pass

    loaded = bool(load_dotenv(dotenv_path=str(resolved), override=override))

    return EnvLoadReport(path=resolved, loaded=loaded, keys_count=keys_count)
