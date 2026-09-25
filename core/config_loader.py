"""Єдиний SSOT-завантажувач конфігурації (Правило №4).

Ціль: один модуль для визначення шляху до config.json,
завантаження JSON-конфігу і роботи з ENV-ключами.
Усі модулі імпортують звідси замість локальних копій.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

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

    Raises:
        ValueError: CONFIG_LEGACY_ANCHOR_KEY — у config легасі-ключ якоря в секундах (ADR-0095 §3.4).
    """
    target = path or pick_config_path()
    with open(target, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    assert_no_legacy_anchor_keys(cfg, target)
    return cfg


# ADR-0095 §3.4 (S5c): якір H4/D1 задає правило `htf_anchor`, а не секунди. Config із будь-яким із цих ключів
# застарілий: завантажувач відмовляє (CONFIG_LEGACY_ANCHOR_KEY), а не ігнорує ключ мовчки. Це єдине місце цих
# рядків у core/ runtime/ tools/ app/ — тест-сторож tests/test_legacy_anchor_keys_gate.py дозволяє їх лише тут.
LEGACY_ANCHOR_KEYS = (
    "day_anchor_offset_s",
    "day_anchor_offset_s_alt",
    "day_anchor_offset_s_alt2",
    "day_anchor_offset_s_d1",
    "day_anchor_offset_s_d1_alt",
    "binance.day_anchor_offset_s",
    "binance.d1_anchor_offset_s",
)
# Імена, що не повертаються в код: видалене API статичного якоря (ADR-0095 R6), змінна оточення архівних
# HTF-інструментів і валідатори «членства в alt» замість рівності — корінь дефекту (§3.3: писар, провайдер FXCM,
# health). Бакет H4/D1 — лише htf_bucket_start_ms / htf_anchor_offset_s за правилом символу.
RETIRED_ANCHOR_NAMES = (
    "resolve_anchor_offset_ms",
    "resolve_cascade_anchor_s",
    "FXCM_DAY_ANCHOR_OFFSET_S",
    "select_anchor_offset_for_open_ms",
    "_h4_anchor_offsets",
    "_d1_anchor_offsets",
    "anchor_offset_for_tf",
    "_anchor_offset_alts_for_tf",
    "_legal_anchors_ms",
)
_ABSENT = object()


def find_legacy_anchor_keys(cfg: Any) -> List[str]:
    """Шляхи з LEGACY_ANCHOR_KEYS, присутні в config (навіть зі значенням null), у порядку константи."""
    found = []
    for key_path in LEGACY_ANCHOR_KEYS:
        node = cfg
        for part in key_path.split("."):
            node = node.get(part, _ABSENT) if isinstance(node, dict) else _ABSENT
        if node is not _ABSENT:
            found.append(key_path)
    return found


def assert_no_legacy_anchor_keys(cfg: Any, source: str) -> None:
    """Застарілий config — ValueError CONFIG_LEGACY_ANCHOR_KEY з іменами ключів, а не тихий якір (ADR-0095 §3.4)."""
    found = find_legacy_anchor_keys(cfg)
    if found:
        raise ValueError(
            "CONFIG_LEGACY_ANCHOR_KEY key=%s source=%s — якір H4/D1 задає правило htf_anchor, ключі в секундах "
            "прибрано (ADR-0095 §3.4)" % (",".join(found), source)
        )


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


# ── Політика D1 (ADR-0103 §3.1) ──────────────────────────────────────────────────────────────────────────────────────
D1_POLICY_KEY = "d1_policy"
D1_SOURCE_NATIVE = "broker_native"  # D1 устояних діб = нативний D1 брокера (= TV FX: D1)
D1_SOURCE_DERIVED = "derived_m1"  # D1 = агрегат M1 (ADR-0098 §3.7; відкат ADR-0103 §7)


@dataclass(frozen=True)
class D1Policy:
    """Хто власник ключа D1: нативний D1 брокера для діб, старших за лаг ревізії, або агрегат M1."""

    source: str
    native_settle_lag_h: int

    @property
    def native(self) -> bool:
        return self.source == D1_SOURCE_NATIVE


def d1_policy(cfg: Dict[str, Any]) -> D1Policy:
    """`config.json → d1_policy` з валідацією; секції немає — стара політика (агрегат M1), а не мовчазний натив.

    Невалідне значення — ValueError CONFIG_D1_POLICY_INVALID: два записувачі D1 (S7 і d1_native_settle) мусять
    читати одне рішення, тож «якось прочитане» поле гірше за відмову.
    """
    raw = cfg.get(D1_POLICY_KEY)
    if raw is None:
        return D1Policy(D1_SOURCE_DERIVED, 6)
    if not isinstance(raw, dict):
        raise ValueError("CONFIG_D1_POLICY_INVALID %s=%r — очікується об'єкт (ADR-0103)" % (D1_POLICY_KEY, raw))
    source = raw.get("source")
    lag = raw.get("native_settle_lag_h", 6)
    if source not in (D1_SOURCE_NATIVE, D1_SOURCE_DERIVED):
        raise ValueError("CONFIG_D1_POLICY_INVALID source=%r — %s | %s (ADR-0103)" % (source, D1_SOURCE_NATIVE, D1_SOURCE_DERIVED))
    if isinstance(lag, bool) or not isinstance(lag, int) or lag < 0:
        raise ValueError("CONFIG_D1_POLICY_INVALID native_settle_lag_h=%r — ціле число годин ≥ 0 (ADR-0103)" % (lag,))
    return D1Policy(source, lag)


# ── Щоденний settle M1 + нативний D1 у денну перерву (ADR-0103 §3.2, S3) ─────────────────────────────────────────────
M1_SETTLE_KEY = "m1_settle"
# поле → найменше допустиме значення (ціле); 0 там, де «вимкнено» має сенс
_M1_SETTLE_INT_MIN = {"lookback_h": 1, "fetch_call_timeout_s": 1, "fetch_attempts": 1, "backups_keep": 1,
                      "deadline_guard_min": 0, "observe_s": 0}


@dataclass(frozen=True)
class M1SettlePolicy:
    """Нічний прогін settle: вимикач, лаг ревізій брокера по символу (з групи календаря) і межі прогону."""

    schedule_enabled: bool
    lag_h_by_symbol: Dict[str, int]
    lookback_h: int
    fetch_call_timeout_s: int
    fetch_attempts: int
    backups_keep: int
    deadline_guard_min: int
    observe_s: int
    work_dir: str


def m1_settle_policy(cfg: Dict[str, Any]) -> M1SettlePolicy:
    """`config.json → m1_settle` з валідацією; лаг — для кожного символу `cfg.symbols` через його групу календаря.

    Секції немає, поле невалідне або група символу без лагу — ValueError CONFIG_M1_SETTLE_INVALID: settle переписує
    SSOT значеннями брокера, тож хвилини, які брокер ще ревізує, не можна брати за «якимось» лагом за замовчуванням.
    """
    raw = cfg.get(M1_SETTLE_KEY)
    if not isinstance(raw, dict):
        raise ValueError("CONFIG_M1_SETTLE_INVALID %s=%r — очікується об'єкт (ADR-0103 §3.2)" % (M1_SETTLE_KEY, raw))
    enabled = raw.get("schedule_enabled")
    if not isinstance(enabled, bool):
        raise ValueError("CONFIG_M1_SETTLE_INVALID schedule_enabled=%r — true | false" % (enabled,))
    ints: Dict[str, int] = {}
    for field_name, minimum in _M1_SETTLE_INT_MIN.items():
        value = raw.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError("CONFIG_M1_SETTLE_INVALID %s=%r — ціле ≥ %d" % (field_name, value, minimum))
        ints[field_name] = value
    work_dir = raw.get("work_dir")
    if not isinstance(work_dir, str) or not work_dir.strip():
        raise ValueError("CONFIG_M1_SETTLE_INVALID work_dir=%r — каталог прогонів (архів, звіти, стан)" % (work_dir,))
    lag_by_group = raw.get("revision_lag_h_by_group") or {}
    symbol_groups = dict(cfg.get("market_calendar_symbol_groups") or {})
    lag_by_symbol: Dict[str, int] = {}
    for symbol in cfg.get("symbols") or ():
        lag = lag_by_group.get(symbol_groups.get(symbol))
        if isinstance(lag, bool) or not isinstance(lag, int) or lag < 0:
            raise ValueError(
                "CONFIG_M1_SETTLE_INVALID symbol=%s group=%s revision_lag_h=%r — лаг ревізій групи не виміряно"
                % (symbol, symbol_groups.get(symbol), lag)
            )
        lag_by_symbol[symbol] = lag
    return M1SettlePolicy(schedule_enabled=enabled, lag_h_by_symbol=lag_by_symbol, work_dir=work_dir, **ints)
