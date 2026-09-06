"""runtime.api.auth — check_bearer / hmac_verify: будь-який вхід клієнта → рішення, ніколи не виняток."""
from __future__ import annotations

import pytest

from runtime.api.auth import AuthConfig, check_bearer, constant_time_equal, hmac_sign, hmac_verify

CFG = AuthConfig(enabled=True, token="tk_secret_ascii")


def test_correct_bearer_is_accepted():
    assert check_bearer("Bearer tk_secret_ascii", "", CFG) == (True, "ok_bearer")


def test_wrong_ascii_bearer_is_denied():
    assert check_bearer("Bearer nope", "", CFG) == (False, "deny_bad_token")


@pytest.mark.parametrize("presented", ["…", "tk_secret_asciï", "токен", "\u2026" * 8])
def test_non_ascii_bearer_is_denied_not_500(presented):
    """Раніше hmac.compare_digest(str, str) кидав TypeError → aiohttp 500 зі стектрейсом."""
    assert check_bearer(f"Bearer {presented}", "", CFG) == (False, "deny_bad_token")


def test_non_ascii_query_token_is_denied_not_500():
    assert check_bearer("", "…", CFG) == (False, "deny_bad_token")


def test_non_ascii_configured_token_still_matches_itself():
    cfg = AuthConfig(enabled=True, token="секрет…")
    assert check_bearer("Bearer секрет…", "", cfg) == (True, "ok_bearer")
    assert check_bearer("Bearer секрет", "", cfg) == (False, "deny_bad_token")


def test_hmac_verify_with_non_ascii_signature_is_false_not_exception():
    payload, secret = b"{}", "s3"
    assert hmac_verify(payload, hmac_sign(payload, secret), secret) is True
    assert hmac_verify(payload, "…", secret) is False


def test_constant_time_equal_is_plain_bool_for_any_unicode():
    assert constant_time_equal("a…", "a…") is True
    assert constant_time_equal("a…", "a") is False


def test_csrf_non_ascii_header_is_mismatch_not_exception():
    from runtime.api.csrf import CsrfConfig, check_csrf

    cfg = CsrfConfig(enabled=True, require_origin=False)
    assert check_csrf("cookie-token", "…", "", None, cfg) == (False, "deny_token_mismatch")
    assert check_csrf("tok…", "tok…", "", None, cfg)[0] is True
