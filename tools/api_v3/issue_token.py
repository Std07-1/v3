"""Issue a new public API token and store it in Redis with TTL.

Usage:
    python -m tools.api_v3.issue_token --consumer old_news_bot --scope read --ttl-days 90

Token format (F-S1-006): "tk_" + secrets.token_bytes(32).hex() (64 hex chars).
Redis SETEX with ttl_days*86400 seconds.

The full token is printed to stdout — capture and deliver to consumer
out-of-band (Telegram, encrypted message). Token is NEVER re-displayable
from Redis later (only first 8 chars in `list_tokens`).
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import datetime, timedelta, timezone

from runtime.api_v3.token_store import TOKEN_PREFIX, VALID_SCOPES, token_redis_key
from tools.api_v3._common import get_redis


def generate_token() -> str:
    """F-S1-006: cryptographically secure 256-bit random token."""
    return TOKEN_PREFIX + secrets.token_bytes(32).hex()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Issue a new API token (ADR-0058)")
    parser.add_argument(
        "--consumer", required=True, help="Consumer identifier (free-form)"
    )
    parser.add_argument(
        "--scope",
        default="read",
        choices=sorted(VALID_SCOPES),
        help="Token scope (only 'read' implemented in 058.1)",
    )
    parser.add_argument(
        "--ttl-days",
        type=int,
        default=90,
        help="Token validity in days (default 90; max 365)",
    )
    args = parser.parse_args(argv)

    if not 1 <= args.ttl_days <= 365:
        print("ERROR: --ttl-days must be 1..365", file=sys.stderr)
        return 2

    client, namespace = get_redis()
    token = generate_token()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=args.ttl_days)
    payload = {
        "consumer": args.consumer,
        "scope": args.scope,
        "created": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "expires": expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    ttl_s = args.ttl_days * 86400
    key = token_redis_key(namespace, token)
    client.setex(key, ttl_s, json.dumps(payload))

    # Confirmation to stderr; token to stdout (so caller can pipe-capture).
    print(
        f"OK consumer={args.consumer!r} scope={args.scope!r} ttl_days={args.ttl_days} "
        f"expires={payload['expires']}",
        file=sys.stderr,
    )
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
