"""Unit tests for runtime.api.{auth,rate_limit,audit} (ADR-0052 S7).

Feature-flag OFF verification + happy-path + degraded-but-loud (I7) paths.
No Redis dependency — a tiny in-memory fake satisfies the Protocol.
"""
from __future__ import annotations

from typing import Any

import pytest

from runtime.api import audit, auth, rate_limit


# ── Fakes ──────────────────────────────────────────────────────────────────
class FakeRedis:
    """Minimal stand-in for the subset of redis.Redis used by api/*."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.expires: dict[str, int] = {}
        self.stream: list[tuple[str, dict[str, str]]] = []
        self.xadd_calls: int = 0

    def incr(self, name: str) -> int:
        self.counters[name] = self.counters.get(name, 0) + 1
        return self.counters[name]

    def expire(self, name: str, time: int) -> bool:
        self.expires[name] = time
        return True

    def xadd(self, name: str, fields: dict[str, Any], **_: Any) -> str:
        self.xadd_calls += 1
        self.stream.append((name, {str(k): str(v) for k, v in fields.items()}))
        return "0-1"


class BoomRedis(FakeRedis):
    def incr(self, name: str) -> int:  # pragma: no cover - raised below
        raise RuntimeError("redis down")

    def xadd(self, name: str, fields: dict[str, Any], **_: Any) -> str:
        raise RuntimeError("stream down")


# ── auth.py ────────────────────────────────────────────────────────────────
class TestAuth:
    def test_disabled_by_default_denies(self) -> None:
        cfg = auth.AuthConfig()
        ok, reason = auth.check_bearer("Bearer x", "", cfg)
        assert (ok, reason) == (False, "deny_disabled")

    def test_bearer_header_accepts(self) -> None:
        cfg = auth.AuthConfig(enabled=True, token="s3cret")
        ok, reason = auth.check_bearer("Bearer s3cret", "", cfg)
        assert (ok, reason) == (True, "ok_bearer")

    def test_query_token_accepts(self) -> None:
        cfg = auth.AuthConfig(enabled=True, token="s3cret")
        ok, reason = auth.check_bearer("", "s3cret", cfg)
        assert (ok, reason) == (True, "ok_query_token")

    def test_wrong_token_denied(self) -> None:
        cfg = auth.AuthConfig(enabled=True, token="s3cret")
        ok, reason = auth.check_bearer("Bearer wrong", "", cfg)
        assert (ok, reason) == (False, "deny_bad_token")

    def test_missing_token_denied(self) -> None:
        cfg = auth.AuthConfig(enabled=True, token="s3cret")
        ok, reason = auth.check_bearer("", "", cfg)
        assert (ok, reason) == (False, "deny_missing")

    def test_no_token_configured_without_dev_mode(self) -> None:
        cfg = auth.AuthConfig(enabled=True, token="")
        ok, reason = auth.check_bearer("Bearer anything", "", cfg)
        assert (ok, reason) == (False, "deny_no_token_configured")

    def test_no_token_dev_mode_opens(self) -> None:
        cfg = auth.AuthConfig(enabled=True, token="", allow_no_token_dev_mode=True)
        ok, reason = auth.check_bearer("", "", cfg)
        assert (ok, reason) == (True, "ok_no_token_dev")

    def test_hmac_sign_and_verify_roundtrip(self) -> None:
        sig = auth.hmac_sign(b"hello", "topsecret")
        assert sig and auth.hmac_verify(b"hello", sig, "topsecret")
        assert not auth.hmac_verify(b"tampered", sig, "topsecret")

    def test_hmac_empty_secret_is_signing_disabled(self) -> None:
        assert auth.hmac_sign(b"x", "") == ""
        assert auth.hmac_verify(b"x", "anything", "") is False


# ── rate_limit.py ──────────────────────────────────────────────────────────
class TestRateLimit:
    def test_disabled_always_allows(self) -> None:
        cfg = rate_limit.RateLimitConfig()
        allowed, retry = rate_limit.check_and_consume(FakeRedis(), "u1", cfg)
        assert allowed and retry == -1

    def test_consumes_up_to_limit(self) -> None:
        cfg = rate_limit.RateLimitConfig(enabled=True, requests_per_minute=3)
        r = FakeRedis()
        results = [
            rate_limit.check_and_consume(r, "u1", cfg, now_s=0.0) for _ in range(3)
        ]
        assert all(ok for ok, _ in results)

    def test_blocks_over_limit_with_retry_after(self) -> None:
        cfg = rate_limit.RateLimitConfig(enabled=True, requests_per_minute=2)
        r = FakeRedis()
        rate_limit.check_and_consume(r, "u1", cfg, now_s=10.0)
        rate_limit.check_and_consume(r, "u1", cfg, now_s=10.0)
        allowed, retry = rate_limit.check_and_consume(r, "u1", cfg, now_s=10.0)
        assert not allowed and retry == 50  # window 0; next at 60s; now=10 → 50s

    def test_new_window_resets(self) -> None:
        cfg = rate_limit.RateLimitConfig(enabled=True, requests_per_minute=1)
        r = FakeRedis()
        rate_limit.check_and_consume(r, "u1", cfg, now_s=10.0)
        allowed, _ = rate_limit.check_and_consume(r, "u1", cfg, now_s=75.0)
        assert allowed

    def test_fail_open_on_redis_none(self) -> None:
        cfg = rate_limit.RateLimitConfig(enabled=True)
        allowed, retry = rate_limit.check_and_consume(None, "u1", cfg)
        assert allowed and retry == -1  # degraded signal

    def test_fail_open_on_redis_error(self) -> None:
        cfg = rate_limit.RateLimitConfig(enabled=True)
        allowed, retry = rate_limit.check_and_consume(BoomRedis(), "u1", cfg)
        assert allowed and retry == -1

    def test_sets_expire_on_first_hit(self) -> None:
        cfg = rate_limit.RateLimitConfig(enabled=True, window_seconds=30)
        r = FakeRedis()
        rate_limit.check_and_consume(r, "u1", cfg, now_s=0.0)
        assert 30 in r.expires.values()


# ── audit.py ───────────────────────────────────────────────────────────────
class TestAudit:
    def test_disabled_is_noop(self) -> None:
        r = FakeRedis()
        cfg = audit.AuditConfig()
        assert audit.log_event(r, "auth_deny", {"ip": "1.2.3.4"}, cfg) is False
        assert r.xadd_calls == 0

    def test_writes_event_fields(self) -> None:
        r = FakeRedis()
        cfg = audit.AuditConfig(enabled=True)
        ok = audit.log_event(r, "auth_deny", {"ip": "1.2.3.4", "reason": "bad"}, cfg)
        assert ok and r.xadd_calls == 1
        _, fields = r.stream[0]
        assert fields["type"] == "auth_deny"
        assert fields["ip"] == "1.2.3.4"
        assert fields["reason"] == "bad"
        assert fields["nonce"] and fields["ts_ms"]

    def test_fail_open_on_redis_error(self) -> None:
        cfg = audit.AuditConfig(enabled=True)
        assert audit.log_event(BoomRedis(), "auth_deny", {}, cfg) is False

    def test_coerces_non_string_values(self) -> None:
        r = FakeRedis()
        cfg = audit.AuditConfig(enabled=True)
        audit.log_event(r, "rate_limit_hit", {"count": 11, "none_field": None}, cfg)
        _, fields = r.stream[0]
        assert fields["count"] == "11"
        assert fields["none_field"] == ""

    @pytest.mark.parametrize("event", ["auth_deny", "rate_limit_hit", "csrf_fail"])
    def test_arbitrary_event_type(self, event: str) -> None:
        r = FakeRedis()
        cfg = audit.AuditConfig(enabled=True)
        audit.log_event(r, event, {"x": 1}, cfg)
        assert r.stream[0][1]["type"] == event
