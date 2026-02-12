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


# ── SSOT-допустимі TF (Правило №7) ────────────────────────────────

DEFAULT_TF_ALLOWLIST: set[int] = {300, 900, 1800, 3600, 14400, 86400}
DEFAULT_PREVIEW_TF_ALLOWLIST: set[int] = {60, 180}
MAX_EVENTS_PER_RESPONSE: int = 500


def tf_allowlist_from_cfg(cfg: Dict[str, Any]) -> set[int]:
    """Повертає набір дозволених TF (у секундах) з конфігу.

    Пріоритет: tf_allowlist_s → (derived_tfs_s + broker_base_tfs_s) → DEFAULT.
    Гарантує наявність M5=300 у derived/broker fallback.
    """
    raw = cfg.get("tf_allowlist_s")
    out: list[int] = []
    if isinstance(raw, list):
        for item in raw:
            try:
                tf_s = int(item)
            except Exception:
                continue
            if tf_s > 0:
                out.append(tf_s)
    if out:
        return set(out)

    derived = cfg.get("derived_tfs_s")
    if isinstance(derived, list):
        for item in derived:
            try:
                tf_s = int(item)
            except Exception:
                continue
            if tf_s > 0:
                out.append(tf_s)

    broker_base = cfg.get("broker_base_tfs_s")
    if isinstance(broker_base, list):
        for item in broker_base:
            try:
                tf_s = int(item)
            except Exception:
                continue
            if tf_s > 0:
                out.append(tf_s)

    if 300 not in out:
        out.append(300)

    if out:
        return set(out)

    return set(DEFAULT_TF_ALLOWLIST)


def preview_tf_allowlist_from_cfg(cfg: Dict[str, Any]) -> tuple[set[int], str]:
    """Повертає набір дозволених preview TF (у секундах) і мітку джерела.

    Пріоритет: tf_preview_allowlist_s → preview_tick_tfs_s → DEFAULT.
    Returns:
        (set_of_tf_s, source_label) де source = 'config' | 'default'.
    """
    raw = cfg.get("tf_preview_allowlist_s")
    out: list[int] = []
    if isinstance(raw, list):
        for item in raw:
            try:
                tf_s = int(item)
            except Exception:
                continue
            if tf_s > 0:
                out.append(tf_s)
    if out:
        return set(out), "config"

    raw = cfg.get("preview_tick_tfs_s")
    out = []
    if isinstance(raw, list):
        for item in raw:
            try:
                tf_s = int(item)
            except Exception:
                continue
            if tf_s > 0:
                out.append(tf_s)
    if out:
        return set(out), "config"

    return set(DEFAULT_PREVIEW_TF_ALLOWLIST), "default"


def min_coldload_bars_from_cfg(cfg: Dict[str, Any]) -> dict[int, int]:
    """Повертає мінімальну кількість барів для coldload за TF.

    Читає cfg["min_coldload_bars_by_tf_s"] → {tf_s: min_n}.
    Порожній dict якщо не задано.
    """
    raw = cfg.get("min_coldload_bars_by_tf_s")
    out: dict[int, int] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                tf_s = int(k)
                min_n = int(v)
            except Exception:
                continue
            if tf_s > 0 and min_n > 0:
                out[tf_s] = min_n
    if out:
        return out
    return {}
