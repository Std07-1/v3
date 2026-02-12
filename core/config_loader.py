"""Єдиний SSOT-завантажувач конфігурації (Правило №4).

Ціль: один модуль для визначення шляху до config.json,
завантаження JSON-конфігу і роботи з ENV-ключами.
Усі модулі імпортують звідси замість локальних копій.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

# Корінь репозиторію — батько core/
_REPO_ROOT = Path(__file__).resolve().parents[1]


def resolve_config_path(raw_path: str | None = None) -> str:
    """Resolves config file path відносно кореня репозиторію.

    Args:
        raw_path: Шлях (абсолютний або відносний). Якщо None — ``config.json``.

    Returns:
        Абсолютний шлях до config-файлу.
    """
    raw_value = (raw_path or "").strip()
    if not raw_value:
        return str((_REPO_ROOT / "config.json").resolve())
    p = Path(raw_value)
    if p.is_absolute():
        return str(p.resolve())
    return str((_REPO_ROOT / raw_value).resolve())


def pick_config_path() -> str:
    """Визначає шлях до config.json (ENV ``AI_ONE_CONFIG_PATH`` або дефолт).

    Returns:
        Абсолютний шлях до config-файлу.
    """
    env_path = (os.environ.get("AI_ONE_CONFIG_PATH") or "").strip()
    if env_path:
        return resolve_config_path(env_path)
    return resolve_config_path("config.json")


def load_system_config(path: str | None = None) -> Dict[str, Any]:
    """Завантажує JSON-конфіг і повертає його як dict.

    Args:
        path: Шлях до файлу. Якщо None — ``pick_config_path()``.

    Returns:
        Вміст config як dict.
    """
    target = path or pick_config_path()
    with open(target, "r", encoding="utf-8") as f:
        return json.load(f)


def env_str(key: str) -> Optional[str]:
    """Зчитує ENV-змінну, очищає пробіли, повертає None якщо порожньо."""
    value = os.environ.get(key)
    if value is None:
        return None
    value = str(value).strip()
    return value or None
