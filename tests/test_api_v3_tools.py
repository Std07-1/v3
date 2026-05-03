"""ADR-0058 slice 058.4 — token tooling tests.

Uses MagicMock-based Redis stub (consistent with tests/test_api_v3_auth.py).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from runtime.api_v3.token_store import TOKEN_PREFIX, token_redis_key
from tools.api_v3 import extend_token, issue_token, list_tokens, revoke_token


@pytest.fixture
def redis_mock() -> Iterator[MagicMock]:
    """A clean MagicMock Redis client + namespace patched into every module
    that imported `get_redis` at import time (CLI scripts use
    `from tools.api_v3._common import get_redis`, so patching the bound name
    in each module is required)."""
    client = MagicMock()
    client.scan.return_value = (0, [])
    fake = (client, "test_ns")
    with (
        patch.object(issue_token, "get_redis", return_value=fake),
        patch.object(list_tokens, "get_redis", return_value=fake),
        patch.object(revoke_token, "get_redis", return_value=fake),
        patch.object(extend_token, "get_redis", return_value=fake),
    ):
        yield client


# ---------------------------------------------------------------------------
# issue_token


class TestIssueToken:
    def test_generate_token_format(self) -> None:
        token = issue_token.generate_token()
        assert token.startswith(TOKEN_PREFIX)
        assert len(token) == 67  # tk_ + 64 hex
        # All hex
        int(token[3:], 16)

    def test_two_calls_unique(self) -> None:
        # Cryptographic randomness — collisions astronomically unlikely
        assert issue_token.generate_token() != issue_token.generate_token()

    def test_main_writes_setex(self, redis_mock: MagicMock, capsys) -> None:
        rc = issue_token.main(["--consumer", "old_news_bot", "--ttl-days", "30"])
        assert rc == 0
        redis_mock.setex.assert_called_once()
        key, ttl_s, value = redis_mock.setex.call_args.args
        assert key.startswith("test_ns:tokens:tk_")
        assert ttl_s == 30 * 86400
        payload = json.loads(value)
        assert payload["consumer"] == "old_news_bot"
        assert payload["scope"] == "read"
        assert payload["expires"].endswith("Z")
        # token printed to stdout
        out = capsys.readouterr().out.strip()
        assert out.startswith("tk_")

    def test_ttl_out_of_range(self, redis_mock: MagicMock) -> None:
        assert issue_token.main(["--consumer", "x", "--ttl-days", "0"]) == 2
        assert issue_token.main(["--consumer", "x", "--ttl-days", "366"]) == 2
        redis_mock.setex.assert_not_called()


# ---------------------------------------------------------------------------
# list_tokens


class TestListTokens:
    def test_empty(self, redis_mock: MagicMock, capsys) -> None:
        rc = list_tokens.main([])
        assert rc == 0
        assert "no tokens" in capsys.readouterr().out

    def test_table_output(self, redis_mock: MagicMock, capsys) -> None:
        token_a = TOKEN_PREFIX + "a" * 64
        token_b = TOKEN_PREFIX + "b" * 64
        redis_mock.scan.return_value = (
            0,
            [token_redis_key("test_ns", token_a), token_redis_key("test_ns", token_b)],
        )
        redis_mock.get.side_effect = [
            json.dumps(
                {"consumer": "alice", "scope": "read", "expires": "2026-08-01Z"}
            ),
            json.dumps({"consumer": "bob", "scope": "read", "expires": "2026-09-01Z"}),
        ]
        redis_mock.ttl.side_effect = [86400 * 30, 86400 * 60]
        rc = list_tokens.main([])
        assert rc == 0
        out = capsys.readouterr().out
        assert "alice" in out and "bob" in out
        assert "tk_aaaaaaaa" in out
        assert "total=2" in out

    def test_json_output(self, redis_mock: MagicMock, capsys) -> None:
        token = TOKEN_PREFIX + "c" * 64
        redis_mock.scan.return_value = (0, [token_redis_key("test_ns", token)])
        redis_mock.get.return_value = json.dumps({"consumer": "carol", "scope": "read"})
        redis_mock.ttl.return_value = 86400 * 7
        rc = list_tokens.main(["--json"])
        assert rc == 0
        line = capsys.readouterr().out.strip()
        record = json.loads(line)
        assert record["consumer"] == "carol"
        assert record["ttl_s"] == 86400 * 7

    def test_malformed_json_surfaced(self, redis_mock: MagicMock, capsys) -> None:
        token = TOKEN_PREFIX + "d" * 64
        redis_mock.scan.return_value = (0, [token_redis_key("test_ns", token)])
        redis_mock.get.return_value = "{not-json"
        redis_mock.ttl.return_value = 100
        rc = list_tokens.main(["--json"])
        assert rc == 0
        record = json.loads(capsys.readouterr().out.strip())
        assert record["error"] == "malformed_json"


# ---------------------------------------------------------------------------
# revoke_token


class TestRevokeToken:
    def test_revoke_by_token_ok(self, redis_mock: MagicMock) -> None:
        token = TOKEN_PREFIX + "e" * 64
        redis_mock.delete.return_value = 1
        rc = revoke_token.main(["--token", token])
        assert rc == 0
        redis_mock.delete.assert_called_once_with(token_redis_key("test_ns", token))

    def test_revoke_by_token_not_found(self, redis_mock: MagicMock) -> None:
        token = TOKEN_PREFIX + "f" * 64
        redis_mock.delete.return_value = 0
        assert revoke_token.main(["--token", token]) == 1

    def test_revoke_malformed_token(self, redis_mock: MagicMock) -> None:
        assert revoke_token.main(["--token", "not_a_token"]) == 2
        redis_mock.delete.assert_not_called()

    def test_revoke_by_consumer(self, redis_mock: MagicMock) -> None:
        token_a = TOKEN_PREFIX + "1" * 64
        token_b = TOKEN_PREFIX + "2" * 64
        token_c = TOKEN_PREFIX + "3" * 64
        redis_mock.scan.return_value = (
            0,
            [
                token_redis_key("test_ns", token_a),
                token_redis_key("test_ns", token_b),
                token_redis_key("test_ns", token_c),
            ],
        )
        redis_mock.get.side_effect = [
            json.dumps({"consumer": "target_bot", "scope": "read"}),
            json.dumps({"consumer": "other_bot", "scope": "read"}),
            json.dumps({"consumer": "target_bot", "scope": "read"}),
        ]
        redis_mock.delete.return_value = 1
        rc = revoke_token.main(["--consumer", "target_bot"])
        assert rc == 0
        assert redis_mock.delete.call_count == 2

    def test_revoke_consumer_not_found(self, redis_mock: MagicMock) -> None:
        redis_mock.scan.return_value = (0, [])
        assert revoke_token.main(["--consumer", "ghost"]) == 1


# ---------------------------------------------------------------------------
# extend_token


class TestExtendToken:
    def test_extend_ok(self, redis_mock: MagicMock, capsys) -> None:
        token = TOKEN_PREFIX + "9" * 64
        redis_mock.get.return_value = json.dumps(
            {"consumer": "old_news_bot", "scope": "read", "expires": "old"}
        )
        rc = extend_token.main(["--token", token, "--days", "30"])
        assert rc == 0
        redis_mock.setex.assert_called_once()
        key, ttl_s, value = redis_mock.setex.call_args.args
        assert key == token_redis_key("test_ns", token)
        assert ttl_s == 30 * 86400
        payload = json.loads(value)
        # expires field rewritten to new ISO timestamp ending Z
        assert payload["expires"] != "old"
        assert payload["expires"].endswith("Z")
        assert payload["consumer"] == "old_news_bot"

    def test_extend_not_found(self, redis_mock: MagicMock) -> None:
        token = TOKEN_PREFIX + "8" * 64
        redis_mock.get.return_value = None
        assert extend_token.main(["--token", token, "--days", "30"]) == 1

    def test_extend_malformed_existing(self, redis_mock: MagicMock) -> None:
        token = TOKEN_PREFIX + "7" * 64
        redis_mock.get.return_value = "{not-json"
        assert extend_token.main(["--token", token, "--days", "30"]) == 1
        redis_mock.setex.assert_not_called()

    def test_extend_bad_token_shape(self, redis_mock: MagicMock) -> None:
        assert extend_token.main(["--token", "not_a_token", "--days", "30"]) == 2

    def test_extend_bad_days(self, redis_mock: MagicMock) -> None:
        token = TOKEN_PREFIX + "0" * 64
        assert extend_token.main(["--token", token, "--days", "0"]) == 2
        assert extend_token.main(["--token", token, "--days", "400"]) == 2
