"""Секція `config.json:alerts` (ADR-0114 §4.7) — суворо: усі ключі обов'язкові, невідомий ключ — помилка старту.

У коді нема значень за замовчуванням: config.json — SSOT, а одруківка в назві ключа не має тихо вмикати дефолт.
Секрети — не в config, а в `env_file`: пуш (Rev 2) — `ALERTS_VAPID_PRIVATE_KEY`; Telegram (`telegram_enabled`) —
`ALERTS_TELEGRAM_TOKEN`, `ALERTS_OWNER_CHAT_ID`.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Mapping, Tuple


@dataclasses.dataclass(frozen=True)
class AlertsConfig:
    enabled: bool
    port: int
    state_dir: str
    env_file: str
    site_url: str
    display_tz: str
    telegram_api: str
    telegram_enabled: bool  # false — пуш у браузер (Rev 2), true — бот Telegram (Rev 1); канал рівно один
    vapid_subject: str  # «sub» підпису VAPID: https://сайт або mailto:
    push_ttl_s: int
    push_timeout_s: float
    push_host_suffixes: Tuple[str, ...]  # куди серверу дозволено слати пуш (SSRF, webpush.validate_endpoint)
    device_idle_days: int
    device_prune_s: float
    max_active_per_subscriber: int
    max_devices: int
    devices_per_ip_per_hour: int
    pair_code_ttl_s: int
    pair_confirm_attempts: int
    pair_max_pending: int
    requests_per_min: int
    sse_heartbeat_s: float
    gap_after_s: int
    tick_reconnect_s: float
    m1_poll_s: float
    m1_final_lag_s: int
    m1_chunk_bars: int
    delivery_poll_s: float
    retry_first_s: float
    retry_max_s: float
    telegram_poll_timeout_s: int
    telegram_error_pause_s: float

    @classmethod
    def from_cfg(cls, cfg: Mapping[str, Any]) -> "AlertsConfig":
        section = cfg.get("alerts")
        if not isinstance(section, Mapping):
            raise ValueError("config.json: нема секції alerts (ADR-0114 §4.7)")
        raw = {k: v for k, v in section.items() if not k.startswith("_")}  # _comment — як в інших секціях
        fields = {f.name: f.type for f in dataclasses.fields(cls)}
        missing, extra = sorted(fields.keys() - raw.keys()), sorted(raw.keys() - fields.keys())
        if missing or extra:
            raise ValueError(f"config.json:alerts — бракує {missing}, невідомі {extra}")
        for name, annotation in fields.items():
            _check_type(name, raw[name], annotation)
        config = cls(**{**raw, "push_host_suffixes": tuple(raw["push_host_suffixes"])})
        if not config.vapid_subject.startswith(("https://", "mailto:")):
            raise ValueError(f"alerts.vapid_subject: очікується https://… або mailto:…, отримано {config.vapid_subject!r}")
        if not config.site_url.startswith("https://") or config.site_url.endswith("/"):
            raise ValueError(f"alerts.site_url: очікується https://… без «/» в кінці, отримано {config.site_url!r}")
        return config


_KINDS = {"bool": (bool,), "int": (int,), "float": (int, float), "str": (str,)}
_HOST_SUFFIX_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")


def _check_type(name: str, value: Any, annotation: str) -> None:
    if annotation == "Tuple[str, ...]":  # список суфіксів хостів: непорожній, без схеми й шляху, малими літерами
        if not isinstance(value, list) or not value or not all(isinstance(v, str) and _HOST_SUFFIX_RE.match(v) for v in value):
            raise ValueError(f"alerts.{name}: очікується непорожній список хостів (a.b.c), отримано {value!r}")
        return
    kinds = _KINDS[annotation]
    if (isinstance(value, bool) and annotation != "bool") or not isinstance(value, kinds):
        raise ValueError(f"alerts.{name}: очікується {annotation}, отримано {value!r}")
    if annotation in ("int", "float") and value <= 0:
        raise ValueError(f"alerts.{name}: очікується > 0, отримано {value!r}")
    if annotation == "str" and not value:
        raise ValueError(f"alerts.{name}: порожній рядок")
