"""Єдиний SSOT-завантажувач конфігурації (Правило №4).

Ціль: один модуль для визначення шляху до config.json,
завантаження JSON-конфігу і роботи з ENV-ключами.
Усі модулі імпортують звідси замість локальних копій.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from core.session_anchor import HTF_ANCHOR_RULES

# Корінь репозиторію — батько core/
_REPO_ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger("config_loader")


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
DEFAULT_PREVIEW_TF_ALLOWLIST: set[int] = {60, 180, 300, 900, 1800, 3600, 14400}
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
                logger.debug("CONFIG_TF_ALLOWLIST_ITEM_INVALID value=%r", item)
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
                logger.debug("CONFIG_DERIVED_TF_ITEM_INVALID value=%r", item)
                continue
            if tf_s > 0:
                out.append(tf_s)

    broker_base = cfg.get("broker_base_tfs_s")
    if isinstance(broker_base, list):
        for item in broker_base:
            try:
                tf_s = int(item)
            except Exception:
                logger.debug("CONFIG_BROKER_BASE_TF_ITEM_INVALID value=%r", item)
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
                logger.debug("CONFIG_PREVIEW_ALLOWLIST_ITEM_INVALID value=%r", item)
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
                logger.debug("CONFIG_PREVIEW_TICK_TF_ITEM_INVALID value=%r", item)
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
                logger.debug("CONFIG_MIN_COLDLOAD_ITEM_INVALID key=%r value=%r", k, v)
                continue
            if tf_s > 0 and min_n > 0:
                out[tf_s] = min_n
    if out:
        return out
    return {}


def htf_anchor_rule_resolver(cfg: Dict[str, Any]) -> Callable[[str], str]:
    """Символ → правило якоря H4/D1 (ADR-0095 §3.4) через групу календаря `market_calendar_symbol_groups`.

    Секція `htf_anchor.rule_by_calendar_group` валідується один раз тут. Група без правила — сітку брокера не
    виміряно (HKG33, FX): символ з неї отримує ValueError, а не тихий default (рішення власника 23.09.2026).
    """
    section = cfg.get("htf_anchor")
    by_group = section.get("rule_by_calendar_group") if isinstance(section, dict) else None
    if not isinstance(by_group, dict) or not by_group:
        raise ValueError("CONFIG_HTF_ANCHOR_MISSING: config.htf_anchor.rule_by_calendar_group обов'язковий (ADR-0095)")
    for group, rule in by_group.items():
        if rule not in HTF_ANCHOR_RULES:
            raise ValueError(
                "CONFIG_HTF_ANCHOR_RULE_UNKNOWN group=%s rule=%r allowed=%s" % (group, rule, sorted(HTF_ANCHOR_RULES))
            )
    rules = dict(by_group)
    symbol_groups = dict(cfg.get("market_calendar_symbol_groups") or {})

    def rule_for_symbol(symbol: str) -> str:
        group = symbol_groups.get(symbol)
        if group is None:
            raise ValueError("HTF_ANCHOR_SYMBOL_WITHOUT_GROUP symbol=%s (market_calendar_symbol_groups)" % symbol)
        rule = rules.get(group)
        if rule is None:
            raise ValueError(
                "HTF_ANCHOR_GROUP_UNMEASURED symbol=%s group=%s — сітку H4/D1 брокера не виміряно (ADR-0095 §8.4)"
                % (symbol, group)
            )
        return rule

    return rule_for_symbol


# Ключ групи `market_calendar_by_group`: на скільки хвилин брокер відкриває сесію пізніше календаря (ADR-0101 §3.5)
SESSION_OPEN_GRACE_KEY = "session_open_grace_min"


def session_open_grace_resolver(cfg: Dict[str, Any]) -> Callable[[str], int]:
    """Символ → запізнення першого бару сесії в брокера, хвилин (`session_open_grace_min` групи календаря).

    Група без ключа — брокер відкриває сесію на хвилині календаря (0). Значення валідується один раз тут: ціле ≥ 0,
    інакше ValueError, а не тихий 0. Символ без групи — ValueError, як у `htf_anchor_rule_resolver`.
    """
    grace_by_group: Dict[str, int] = {}
    for group, group_cfg in (cfg.get("market_calendar_by_group") or {}).items():
        if not isinstance(group_cfg, dict):
            continue  # група без розкладу: її символ отримає ValueError нижче (і CALENDAR_GROUP_MISSING у календаря)
        grace = group_cfg.get(SESSION_OPEN_GRACE_KEY, 0)
        if isinstance(grace, bool) or not isinstance(grace, int) or grace < 0:
            raise ValueError(
                "CONFIG_SESSION_OPEN_GRACE_INVALID group=%s %s=%r — ціле число хвилин ≥ 0 (ADR-0101 §3.5)"
                % (group, SESSION_OPEN_GRACE_KEY, grace)
            )
        grace_by_group[group] = grace
    symbol_groups = dict(cfg.get("market_calendar_symbol_groups") or {})

    def grace_for_symbol(symbol: str) -> int:
        group = symbol_groups.get(symbol)
        if group not in grace_by_group:
            raise ValueError(
                "SESSION_OPEN_GRACE_SYMBOL_WITHOUT_GROUP symbol=%s group=%s (market_calendar_symbol_groups)"
                % (symbol, group)
            )
        return grace_by_group[group]

    return grace_for_symbol
