"""CSRF protection + replay guard (ADR-0052 S8, threat T4 + T7).

Double-submit cookie pattern: the token value lives both in a cookie (set by
the server at login) and in a custom request header ``X-CSRF-Token``. The
request is accepted only if both are present and equal. ``SameSite=Strict``
on the cookie prevents cross-site injection; the header check catches any
cross-origin attempt that bypasses SameSite (e.g. stolen cookies replayed
from a compromised extension with matching origin).

Additionally guards against replay: when the caller provides a ``ts_ms``
timestamp (from a signed payload), we reject anything older than
``ts_cutoff_s`` (default 300 s / 5 min) — matches the nonce-window in
``audit.py``.

Fail-closed: when enabled, any missing piece denies. This is stricter than
auth/rate_limit which fail-open — CSRF is a Zone-crossing check where a
permissive default is unacceptable (I7: degraded-but-loud means we DENY and
SHOUT, never DENY silently; the denial is loud via the reason code).
"""
from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping

CsrfReason = Literal[
    "ok",
    "ok_disabled",
    "deny_missing_cookie",
    "deny_missing_header",
    "deny_token_mismatch",
    "deny_bad_origin",
    "deny_ts_expired",
    "deny_ts_future",
]


@dataclass(frozen=True)
class CsrfConfig:
    enabled: bool = False
    cookie_name: str = "csrf_token"
    header_name: str = "X-CSRF-Token"
    allowed_origins: frozenset[str] = field(default_factory=frozenset)
    ts_cutoff_s: int = 300
    require_origin: bool = True

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> "CsrfConfig":
        origins_raw: Iterable[Any] = m.get("allowed_origins", []) or []
        return cls(
            enabled=bool(m.get("enabled", False)),
            cookie_name=str(m.get("cookie_name", "csrf_token")),
            header_name=str(m.get("header_name", "X-CSRF-Token")),
            allowed_origins=frozenset(str(o) for o in origins_raw),
            ts_cutoff_s=int(m.get("ts_cutoff_s", 300)),
            require_origin=bool(m.get("require_origin", True)),
        )


def generate_token(n_bytes: int = 32) -> str:
    """Cryptographically-strong random hex token for the CSRF cookie."""
    return secrets.token_hex(n_bytes)


def check_csrf(
    cookie_val: str,
    header_val: str,
    origin: str,
    ts_ms: int | None,
    cfg: CsrfConfig,
    now_ms: int | None = None,
) -> tuple[bool, CsrfReason]:
    """Validate a state-changing request. Returns ``(allowed, reason)``.

    ``ts_ms`` is optional — when the request body carries a signed timestamp
    we reject outside of ``±ts_cutoff_s``. Small clock skew window on the
    future side (allow up to +cutoff) covers monotonic-clock drift.
    """
    if not cfg.enabled:
        return (True, "ok_disabled")
    if cfg.require_origin and cfg.allowed_origins:
        if origin not in cfg.allowed_origins:
            return (False, "deny_bad_origin")
    if not cookie_val:
        return (False, "deny_missing_cookie")
    if not header_val:
        return (False, "deny_missing_header")
    if not hmac.compare_digest(cookie_val, header_val):
        return (False, "deny_token_mismatch")
    if ts_ms is not None:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        age = now - ts_ms
        cutoff_ms = cfg.ts_cutoff_s * 1000
        if age > cutoff_ms:
            return (False, "deny_ts_expired")
        if age < -cutoff_ms:
            return (False, "deny_ts_future")
    return (True, "ok")
