"""runtime/agent_bridge/config.py — SSOT-резолвер секції `config.json:agent_bridge` (ADR-0090 S4).

Одна секція для всього, що існує заради зовнішнього AI-клієнта. Top-level ключів
`wake_engine` / `agent_console` / `public_snapshot` у config.json більше немає —
це стереже exit-gate `platform_config_no_agent_keys` (список LEGACY_TOP_LEVEL_KEYS
нижче = єдине джерело для гейта, D15.2).

Вмикання на хості: config.json = git-singleton (overlay заборонено,
`gate_config_singleton`), тому репо-дефолт `enabled=false` перекривається лише
env `AI_ONE_AGENT_BRIDGE_ENABLED` у supervisor-програмі того хоста. Невалідне
значення env = ValueError на старті (degraded-but-loud, I5), не тихий False.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

AGENT_BRIDGE_SECTION = "agent_bridge"
AGENT_BRIDGE_ENABLED_ENV = "AI_ONE_AGENT_BRIDGE_ENABLED"
LEGACY_TOP_LEVEL_KEYS = ("wake_engine", "agent_console", "public_snapshot")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8010
DEFAULT_SMC_SNAPSHOT_URL = "http://127.0.0.1:8000/api/internal/smc_snapshot"
DEFAULT_AUTH_TOKEN_ENV = "ARCHI_AUTH_TOKEN"
DEFAULT_THINKING_MAX_ITEMS = 100
DEFAULT_FEED_MAX_ITEMS = 200

_ENV_TRUE = frozenset({"1", "true", "yes", "on"})
_ENV_FALSE = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class ConsoleConfig:
    """`agent_bridge.console` — маршрути `/api/archi/*`, `/api/agent/*` (ADR-025 у trader-v3)."""

    enabled: bool = False
    auth_token_env: str = DEFAULT_AUTH_TOKEN_ENV
    allow_no_token_dev_mode: bool = False
    thinking_max_items: int = DEFAULT_THINKING_MAX_ITEMS
    feed_max_items: int = DEFAULT_FEED_MAX_ITEMS

    def resolve_token(self, environ: Optional[Mapping[str, str]] = None) -> str:
        """Bearer-токен береться з env за ім'ям `auth_token_env`; у config.json секретів немає."""
        source = os.environ if environ is None else environ
        return str(source.get(self.auth_token_env, "") or "")


@dataclass(frozen=True)
class AgentBridgeConfig:
    """Розв'язана секція `agent_bridge`. `enabled` — головний перемикач: без нього
    `wake_engine_enabled` і `console_enabled` завжди False."""

    enabled: bool = False
    enabled_source: str = "config"  # "config" | "env"
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    data_dir: str = ""
    smc_snapshot_url: str = DEFAULT_SMC_SNAPSHOT_URL
    wake_engine: Dict[str, Any] = field(default_factory=dict)
    console: ConsoleConfig = field(default_factory=ConsoleConfig)
    public_snapshot: Dict[str, Any] = field(default_factory=dict)

    @property
    def wake_engine_enabled(self) -> bool:
        return self.enabled and bool(self.wake_engine.get("enabled", False))

    @property
    def console_enabled(self) -> bool:
        return self.enabled and self.console.enabled


def resolve_agent_bridge_config(
    full_cfg: Mapping[str, Any],
    environ: Optional[Mapping[str, str]] = None,
) -> AgentBridgeConfig:
    """Секція відсутня → усе вимкнено (ws_server стартує без `agent_bridge`).

    Raises:
        ValueError: env `AI_ONE_AGENT_BRIDGE_ENABLED` має значення поза
            {1,0,true,false,yes,no,on,off} — помилка конфігурації хоста.
    """
    section = full_cfg.get(AGENT_BRIDGE_SECTION) or {}
    if not isinstance(section, Mapping):
        raise ValueError(f"config.json:{AGENT_BRIDGE_SECTION} must be an object, got {type(section).__name__}")
    enabled, enabled_source = _resolve_enabled(bool(section.get("enabled", False)), environ)
    console_raw = section.get("console") or {}
    console = ConsoleConfig(
        enabled=bool(console_raw.get("enabled", False)),
        auth_token_env=str(console_raw.get("auth_token_env", DEFAULT_AUTH_TOKEN_ENV) or DEFAULT_AUTH_TOKEN_ENV),
        allow_no_token_dev_mode=bool(console_raw.get("allow_no_token_dev_mode", False)),
        thinking_max_items=int(console_raw.get("thinking_max_items", DEFAULT_THINKING_MAX_ITEMS)),
        feed_max_items=int(console_raw.get("feed_max_items", DEFAULT_FEED_MAX_ITEMS)),
    )
    return AgentBridgeConfig(
        enabled=enabled,
        enabled_source=enabled_source,
        host=str(section.get("host", DEFAULT_HOST) or DEFAULT_HOST),
        port=int(section.get("port", DEFAULT_PORT)),
        data_dir=str(section.get("data_dir", "") or ""),
        smc_snapshot_url=str(section.get("smc_snapshot_url", DEFAULT_SMC_SNAPSHOT_URL) or DEFAULT_SMC_SNAPSHOT_URL),
        wake_engine=dict(section.get("wake_engine") or {}),
        console=console,
        public_snapshot=dict(section.get("public_snapshot") or {}),
    )


def _resolve_enabled(config_value: bool, environ: Optional[Mapping[str, str]]) -> tuple[bool, str]:
    source = os.environ if environ is None else environ
    raw = str(source.get(AGENT_BRIDGE_ENABLED_ENV, "") or "").strip().lower()
    if not raw:
        return config_value, "config"
    if raw in _ENV_TRUE:
        return True, "env"
    if raw in _ENV_FALSE:
        return False, "env"
    raise ValueError(
        f"{AGENT_BRIDGE_ENABLED_ENV}={raw!r} is not a boolean "
        f"(expected one of {sorted(_ENV_TRUE | _ENV_FALSE)})"
    )
