"""Завантаження профілю середовища (local/prod) для репозиторію FXCM Connector.

Ціль:
- мати один перемикач профілю через `.env` dispatcher;
- локальний запуск не має торкатись прод Redis-каналів, якщо задано
  `FXCM_CHANNEL_PREFIX=fxcm_local` (або інший префікс);
- не зберігати секрети у dispatcher-файлі (він має містити лише прості ключі).

Очікуваний формат:
- `.env` містить тільки `AI_ONE_ENV_FILE=.env.local` або `.env.prod`.
- самі профілі (`.env.local` / `.env.prod`) містять `FXCM_*` змінні.

Примітка:
Цей модуль НЕ друкує значення змінних (щоб не витікали секрети), лише
керує порядком завантаження файлів.
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
    """Результат завантаження env-профілю (без значень)."""

    dispatcher_path: Optional[Path]
    profile_path: Optional[Path]
    dispatcher_loaded: bool
    profile_loaded: bool


def load_env_profile(
    *,
    override: bool = False,
    dispatcher: str | Path = ".env",
) -> EnvLoadReport:
    """Завантажує `.env` dispatcher і профіль із `AI_ONE_ENV_FILE`.

    Порядок:
    1) Якщо python-dotenv недоступний — нічого не робимо.
    2) Якщо існує dispatcher-файл — вантажимо його.
    3) Якщо після цього задано `AI_ONE_ENV_FILE` і файл існує — вантажимо профіль.

    Args:
        override: Якщо True — значення з файлів перекривають поточне ENV.
        dispatcher: Шлях до dispatcher `.env`.

    Returns:
        EnvLoadReport: Шляхи і прапорці завантаження.
    """

    if load_dotenv is None:
        return EnvLoadReport(
            dispatcher_path=None,
            profile_path=None,
            dispatcher_loaded=False,
            profile_loaded=False,
        )

    dispatcher_path = Path(dispatcher).resolve()
    dispatcher_loaded = False
    if dispatcher_path.exists():
        dispatcher_loaded = bool(load_dotenv(dotenv_path=str(dispatcher_path), override=override))

    env_file_raw = (os.environ.get("AI_ONE_ENV_FILE") or "").strip()
    profile_path: Optional[Path] = None
    profile_loaded = False
    if env_file_raw:
        candidate = Path(env_file_raw)
        if not candidate.is_absolute():
            candidate = (dispatcher_path.parent / candidate).resolve()
        profile_path = candidate
        if candidate.exists():
            profile_loaded = bool(load_dotenv(dotenv_path=str(candidate), override=override))

    # Fallback: якщо dispatcher не існує і AI_ONE_ENV_FILE не заданий —
    # залишаємо стару поведінку "autodetect .env".
    if not dispatcher_path.exists() and not env_file_raw:
        profile_loaded = bool(load_dotenv(override=override))

    return EnvLoadReport(
        dispatcher_path=dispatcher_path if dispatcher_path.exists() else None,
        profile_path=profile_path if (profile_path and profile_path.exists()) else profile_path,
        dispatcher_loaded=dispatcher_loaded,
        profile_loaded=profile_loaded,
    )
