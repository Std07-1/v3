"""Bearer-token auth + HMAC response signing (ADR-0052 S7).

Pure module — no Redis, no global state. Callers pass an :class:`AuthConfig`
(usually built from ``config.json`` ``security.auth`` section) and the request
object. Returns a boolean decision plus a structured *reason code* so the
caller can audit the outcome (I7 degraded-but-loud — never silent deny).
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Literal, Mapping

AuthReason = Literal[
    "ok_bearer",
    "ok_query_token",
    "ok_no_token_dev",
    "deny_disabled",
    "deny_no_token_configured",
    "deny_bad_token",
    "deny_missing",
]


@dataclass(frozen=True)
class AuthConfig:
    """Auth feature config. Defaults are safe (disabled)."""

    enabled: bool = False
    token: str = ""
    allow_no_token_dev_mode: bool = False
    hmac_secret: str = ""

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> "AuthConfig":
        return cls(
            enabled=bool(m.get("enabled", False)),
            token=str(m.get("token", "") or ""),
            allow_no_token_dev_mode=bool(m.get("allow_no_token_dev_mode", False)),
            hmac_secret=str(m.get("hmac_secret", "") or ""),
        )


def check_bearer(
    auth_header: str,
    query_token: str,
    cfg: AuthConfig,
) -> tuple[bool, AuthReason]:
    """Validate a request's Bearer token against ``cfg``.

    Accepts either ``Authorization: Bearer <tok>`` header or ``?token=<tok>``
    query (legacy browser direct access). Returns ``(allowed, reason)`` — the
    reason code lets audit.py log *why* without duplicating the policy.
    """
    if not cfg.enabled:
        return (False, "deny_disabled")
    if not cfg.token:
        if cfg.allow_no_token_dev_mode:
            return (True, "ok_no_token_dev")
        return (False, "deny_no_token_configured")
    if auth_header.startswith("Bearer "):
        presented = auth_header[7:].strip()
        if hmac.compare_digest(presented, cfg.token):
            return (True, "ok_bearer")
        return (False, "deny_bad_token")
    if query_token:
        if hmac.compare_digest(query_token, cfg.token):
            return (True, "ok_query_token")
        return (False, "deny_bad_token")
    return (False, "deny_missing")


def hmac_sign(payload: bytes, secret: str) -> str:
    """Return HMAC-SHA256 hex digest of ``payload`` under ``secret``.

    Empty secret → empty string (signing disabled). Callers must treat empty
    signature as "no signature present".
    """
    if not secret:
        return ""
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def hmac_verify(payload: bytes, signature: str, secret: str) -> bool:
    """Constant-time HMAC-SHA256 verification. Empty secret rejects always."""
    if not secret or not signature:
        return False
    expected = hmac_sign(payload, secret)
    return hmac.compare_digest(expected, signature)
