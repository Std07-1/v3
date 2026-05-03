"""ADR-0058 slice 058.1 — token_store + auth_validator FastAPI sidecar."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from runtime.api_v3 import auth_validator
from runtime.api_v3.token_store import (
    TOKEN_FULL_LEN,
    TOKEN_HEX_LEN,
    TOKEN_PREFIX,
    TokenRecord,
    TokenStore,
    is_well_formed,
    token_redis_key,
)

VALID_TOKEN = TOKEN_PREFIX + ("a" * TOKEN_HEX_LEN)


# ─────────────────────────── token_store: shape check ────────────────────────


class TestIsWellFormed:
    def test_valid_token_passes(self) -> None:
        assert is_well_formed(VALID_TOKEN)

    def test_none_fails(self) -> None:
        assert not is_well_formed(None)

    def test_empty_string_fails(self) -> None:
        assert not is_well_formed("")

    def test_missing_prefix_fails(self) -> None:
        assert not is_well_formed("a" * TOKEN_FULL_LEN)

    def test_too_short_fails(self) -> None:
        assert not is_well_formed("tk_abc")

    def test_too_long_fails(self) -> None:
        assert not is_well_formed(VALID_TOKEN + "extra")

    def test_non_hex_tail_fails(self) -> None:
        assert not is_well_formed(TOKEN_PREFIX + ("z" * TOKEN_HEX_LEN))


def test_token_redis_key_shape() -> None:
    assert token_redis_key("v3_local", VALID_TOKEN) == f"v3_local:tokens:{VALID_TOKEN}"


# ─────────────────────────── token_store: lookup ─────────────────────────────


def _make_store(redis_get_value: object) -> tuple[TokenStore, MagicMock]:
    redis_mock = MagicMock()
    redis_mock.get.return_value = redis_get_value
    return TokenStore(redis_mock, "test_ns"), redis_mock


class TestTokenStoreLookup:
    def test_valid_record_returned(self) -> None:
        store, redis_mock = _make_store(
            json.dumps(
                {
                    "consumer": "old_news_bot",
                    "scope": "read",
                    "created": "2026-05-03T10:00:00Z",
                    "expires": "2026-08-01T10:00:00Z",
                }
            )
        )
        record = store.lookup(VALID_TOKEN)
        assert record == TokenRecord(
            consumer="old_news_bot",
            scope="read",
            created="2026-05-03T10:00:00Z",
            expires="2026-08-01T10:00:00Z",
        )
        redis_mock.get.assert_called_once_with(f"test_ns:tokens:{VALID_TOKEN}")

    def test_missing_in_redis_returns_none(self) -> None:
        store, _ = _make_store(None)
        assert store.lookup(VALID_TOKEN) is None

    def test_malformed_token_skips_redis(self) -> None:
        store, redis_mock = _make_store(None)
        assert store.lookup("garbage") is None
        redis_mock.get.assert_not_called()

    def test_unknown_scope_returns_none(self) -> None:
        # F-S1-007: future scope reserved → fail-closed.
        store, _ = _make_store(
            json.dumps(
                {
                    "consumer": "future_bot",
                    "scope": "read:XAU/USD",
                }
            )
        )
        assert store.lookup(VALID_TOKEN) is None

    def test_missing_consumer_returns_none(self) -> None:
        store, _ = _make_store(json.dumps({"scope": "read"}))
        assert store.lookup(VALID_TOKEN) is None

    def test_consumer_must_be_string(self) -> None:
        store, _ = _make_store(json.dumps({"scope": "read", "consumer": 123}))
        assert store.lookup(VALID_TOKEN) is None

    def test_malformed_json_returns_none(self) -> None:
        store, _ = _make_store("not-json{")
        assert store.lookup(VALID_TOKEN) is None

    def test_redis_error_propagates(self) -> None:
        # I5 — caller (auth endpoint) must fail-closed; do not swallow here.
        redis_mock = MagicMock()
        redis_mock.get.side_effect = RedisError("connection refused")
        store = TokenStore(redis_mock, "test_ns")
        with pytest.raises(RedisError):
            store.lookup(VALID_TOKEN)


# ─────────────────────────── auth_validator: HTTP ────────────────────────────


@pytest.fixture
def reset_singletons(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with fresh module-level singletons."""
    monkeypatch.setattr(auth_validator, "_redis_client", None)
    monkeypatch.setattr(auth_validator, "_token_store", None)


def _inject_store(monkeypatch: pytest.MonkeyPatch, store: object) -> None:
    monkeypatch.setattr(auth_validator, "_get_store", lambda: store)


class TestHealth:
    def test_health_no_auth_required(self, reset_singletons: None) -> None:
        client = TestClient(auth_validator.app)
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "api_v3_auth"


class TestAuthEndpoint:
    def test_missing_header_returns_401(self, reset_singletons: None) -> None:
        client = TestClient(auth_validator.app)
        response = client.get("/_auth")
        assert response.status_code == 401
        assert response.json()["detail"] == "missing_api_key"

    def test_valid_token_returns_200_with_headers(
        self, reset_singletons: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = MagicMock()
        store.lookup.return_value = TokenRecord(
            consumer="old_news_bot",
            scope="read",
            created="2026-05-03T10:00:00Z",
            expires="2026-08-01T10:00:00Z",
        )
        _inject_store(monkeypatch, store)
        client = TestClient(auth_validator.app)
        response = client.get("/_auth", headers={"X-API-Key": VALID_TOKEN})
        assert response.status_code == 200
        assert response.headers["X-Consumer"] == "old_news_bot"
        assert response.headers["X-Scope"] == "read"
        body = response.json()
        assert body["consumer"] == "old_news_bot"
        assert body["scope"] == "read"

    def test_invalid_token_returns_401(
        self, reset_singletons: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = MagicMock()
        store.lookup.return_value = None
        _inject_store(monkeypatch, store)
        client = TestClient(auth_validator.app)
        response = client.get("/_auth", headers={"X-API-Key": VALID_TOKEN})
        assert response.status_code == 401
        assert response.json()["detail"] == "invalid_token"

    def test_redis_down_returns_503_fail_closed(
        self, reset_singletons: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # I5: degraded-but-loud — Redis outage MUST NOT silently allow.
        store = MagicMock()
        store.lookup.side_effect = RedisError("connection refused")
        _inject_store(monkeypatch, store)
        client = TestClient(auth_validator.app)
        response = client.get("/_auth", headers={"X-API-Key": VALID_TOKEN})
        assert response.status_code == 503
        assert response.json()["detail"] == "auth_backend_unavailable"

    def test_docs_disabled(self, reset_singletons: None) -> None:
        # Server-internal sidecar: no schema advertisement to the world.
        client = TestClient(auth_validator.app)
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404
