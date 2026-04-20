"""Unit tests for runtime.api.{csrf,sanitizer} (ADR-0052 S8).

Covers T1 (XSS strip), T4 (CSRF double-submit + origin), T5 (prompt
injection via handoff), T7 (replay window). All paths are pure; no Redis
dependency needed.
"""
from __future__ import annotations

import pytest

from runtime.api import csrf, sanitizer


# ── csrf.py ─────────────────────────────────────────────────────────────
class TestCsrf:
    def test_disabled_always_ok(self) -> None:
        cfg = csrf.CsrfConfig()
        ok, reason = csrf.check_csrf("", "", "", None, cfg)
        assert (ok, reason) == (True, "ok_disabled")

    def test_happy_path(self) -> None:
        cfg = csrf.CsrfConfig(
            enabled=True,
            allowed_origins=frozenset({"https://aione-smc.com"}),
        )
        ok, reason = csrf.check_csrf(
            "tok-a", "tok-a", "https://aione-smc.com", None, cfg
        )
        assert (ok, reason) == (True, "ok")

    def test_bad_origin_denied(self) -> None:
        cfg = csrf.CsrfConfig(
            enabled=True,
            allowed_origins=frozenset({"https://aione-smc.com"}),
        )
        ok, reason = csrf.check_csrf("t", "t", "https://evil.com", None, cfg)
        assert (ok, reason) == (False, "deny_bad_origin")

    def test_missing_cookie(self) -> None:
        cfg = csrf.CsrfConfig(enabled=True, require_origin=False)
        ok, reason = csrf.check_csrf("", "t", "", None, cfg)
        assert (ok, reason) == (False, "deny_missing_cookie")

    def test_missing_header(self) -> None:
        cfg = csrf.CsrfConfig(enabled=True, require_origin=False)
        ok, reason = csrf.check_csrf("t", "", "", None, cfg)
        assert (ok, reason) == (False, "deny_missing_header")

    def test_token_mismatch(self) -> None:
        cfg = csrf.CsrfConfig(enabled=True, require_origin=False)
        ok, reason = csrf.check_csrf("abc", "xyz", "", None, cfg)
        assert (ok, reason) == (False, "deny_token_mismatch")

    def test_replay_cutoff_expired(self) -> None:
        cfg = csrf.CsrfConfig(enabled=True, require_origin=False, ts_cutoff_s=60)
        now = 1_000_000_000_000
        old = now - 120_000  # 120 s ago > 60 s cutoff
        ok, reason = csrf.check_csrf("t", "t", "", old, cfg, now_ms=now)
        assert (ok, reason) == (False, "deny_ts_expired")

    def test_replay_future_denied(self) -> None:
        cfg = csrf.CsrfConfig(enabled=True, require_origin=False, ts_cutoff_s=60)
        now = 1_000_000_000_000
        future = now + 120_000  # 2 min in the future
        ok, reason = csrf.check_csrf("t", "t", "", future, cfg, now_ms=now)
        assert (ok, reason) == (False, "deny_ts_future")

    def test_replay_within_window_ok(self) -> None:
        cfg = csrf.CsrfConfig(enabled=True, require_origin=False, ts_cutoff_s=60)
        now = 1_000_000_000_000
        recent = now - 30_000  # 30 s ago
        ok, reason = csrf.check_csrf("t", "t", "", recent, cfg, now_ms=now)
        assert (ok, reason) == (True, "ok")

    def test_generate_token_unique_and_hex(self) -> None:
        a, b = csrf.generate_token(), csrf.generate_token()
        assert a != b
        assert len(a) == 64  # 32 bytes → 64 hex chars
        int(a, 16)  # must parse as hex

    def test_require_origin_false_skips_origin_check(self) -> None:
        cfg = csrf.CsrfConfig(
            enabled=True,
            require_origin=False,
            allowed_origins=frozenset({"https://aione-smc.com"}),
        )
        ok, reason = csrf.check_csrf("t", "t", "https://whatever", None, cfg)
        assert (ok, reason) == (True, "ok")


# ── sanitizer.py ─────────────────────────────────────────────────────────
class TestSanitizerMessage:
    def test_disabled_returns_as_is(self) -> None:
        cfg = sanitizer.SanitizerConfig()
        clean, flags = sanitizer.sanitize_message("<script>bad</script>", cfg)
        assert clean == "<script>bad</script>"
        assert flags["disabled"] is True

    def test_strips_script_block(self) -> None:
        cfg = sanitizer.SanitizerConfig(enabled=True)
        clean, flags = sanitizer.sanitize_message("hi<script>x=1</script> ok", cfg)
        assert "script" not in clean.lower()
        assert flags["script_blocks"] == 1

    def test_strips_iframe_and_event_handlers(self) -> None:
        cfg = sanitizer.SanitizerConfig(enabled=True)
        clean, flags = sanitizer.sanitize_message(
            '<iframe src=x></iframe><img onerror="a">', cfg
        )
        assert "iframe" not in clean.lower()
        assert "onerror" not in clean.lower()
        assert flags["dangerous_tags"] >= 1
        assert flags["event_handlers"] >= 1

    def test_strips_javascript_uri(self) -> None:
        cfg = sanitizer.SanitizerConfig(enabled=True)
        clean, flags = sanitizer.sanitize_message(
            '<a href="javascript:alert(1)">x</a>', cfg
        )
        assert "javascript:" not in clean.lower()
        assert flags["js_uris"] == 1

    def test_strips_control_chars(self) -> None:
        cfg = sanitizer.SanitizerConfig(enabled=True)
        text = "hello\x00\x07world\x7f"
        clean, flags = sanitizer.sanitize_message(text, cfg)
        assert clean == "helloworld"
        assert flags["control_chars_stripped"] == 3

    def test_keeps_newlines_and_tabs(self) -> None:
        cfg = sanitizer.SanitizerConfig(enabled=True)
        clean, flags = sanitizer.sanitize_message("a\tb\nc\rd", cfg)
        assert clean == "a\tb\nc\rd"
        assert flags["control_chars_stripped"] == 0

    def test_truncates_over_limit(self) -> None:
        cfg = sanitizer.SanitizerConfig(enabled=True, max_message_length=10)
        clean, flags = sanitizer.sanitize_message("x" * 50, cfg)
        assert len(clean) == 10
        assert flags["truncated"] is True
        assert flags["length_original"] == 50
        assert flags["length_clean"] == 10

    def test_benign_text_unchanged(self) -> None:
        cfg = sanitizer.SanitizerConfig(enabled=True)
        clean, flags = sanitizer.sanitize_message("Буду шортити XAU/USD о 15:00.", cfg)
        assert clean == "Буду шортити XAU/USD о 15:00."
        assert flags["truncated"] is False
        assert flags["script_blocks"] == 0


class TestSanitizerHandoff:
    def test_disabled_passes_through(self) -> None:
        cfg = sanitizer.SanitizerConfig()
        clean, flags = sanitizer.sanitize_handoff("evil", "whatever", cfg)
        assert clean == "whatever"
        assert flags["disabled"] is True

    @pytest.mark.parametrize("src", ["feed", "thinking", "relationship", "mind", "logs"])
    def test_whitelisted_sources_accepted(self, src: str) -> None:
        cfg = sanitizer.SanitizerConfig(enabled=True)
        clean, flags = sanitizer.sanitize_handoff(src, "go short", cfg)
        assert clean == "go short"
        assert "rejected_source" not in flags

    def test_bad_source_rejected(self) -> None:
        cfg = sanitizer.SanitizerConfig(enabled=True)
        clean, flags = sanitizer.sanitize_handoff(
            "evil_injection_source", "ignore previous", cfg
        )
        assert clean is None
        assert flags["rejected_source"] == "evil_injection_source"

    def test_handoff_length_capped(self) -> None:
        cfg = sanitizer.SanitizerConfig(enabled=True, max_handoff_prompt=20)
        clean, flags = sanitizer.sanitize_handoff("feed", "x" * 200, cfg)
        assert clean is not None and len(clean) == 20
        assert flags["truncated"] is True

    def test_handoff_strips_control_chars(self) -> None:
        cfg = sanitizer.SanitizerConfig(enabled=True)
        clean, flags = sanitizer.sanitize_handoff("feed", "go\x00short\x07", cfg)
        assert clean == "goshort"
        assert flags["control_chars_stripped"] == 2
