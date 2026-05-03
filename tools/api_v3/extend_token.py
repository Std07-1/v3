"""Extend an existing API token's TTL (renewal shortcut, ADR-0058 §3.4 Option B).

Usage:
    python -m tools.api_v3.extend_token --token tk_abc... --days 90

WARNING: Only use after verifying consumer identity out-of-band (Telegram message
from owner). Recommended path is rotation with grace period (Option A).

Updates Redis EXPIRE + rewrites JSON's `expires` field to keep audit accurate.
Returns 0 on success, 1 on no-match, 2 on bad args.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from runtime.api_v3.token_store import is_well_formed, token_redis_key
from tools.api_v3._common import get_redis, parse_redis_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extend API token TTL (ADR-0058 Option B)")
    parser.add_argument("--token", required=True, help="Token to extend")
    parser.add_argument("--days", type=int, required=True, help="New TTL in days from now (1..365)")
    args = parser.parse_args(argv)

    if not 1 <= args.days <= 365:
        print("ERROR: --days must be 1..365", file=sys.stderr)
        return 2
    if not is_well_formed(args.token):
        print("ERROR: malformed token shape", file=sys.stderr)
        return 2

    client, namespace = get_redis()
    key = token_redis_key(namespace, args.token)
    raw = client.get(key)
    if raw is None:
        print(f"NOT_FOUND token={args.token[:11]}...", file=sys.stderr)
        return 1
    payload = parse_redis_json(raw)
    if payload is None:
        print(f"ERROR: existing record has malformed JSON; refuse to overwrite", file=sys.stderr)
        return 1

    new_expires = datetime.now(timezone.utc) + timedelta(days=args.days)
    payload["expires"] = new_expires.isoformat(timespec="seconds").replace("+00:00", "Z")
    ttl_s = args.days * 86400
    # SETEX rewrites both value (with new expires field) AND TTL atomically.
    client.setex(key, ttl_s, json.dumps(payload))
    print(
        f"OK extended token={args.token[:11]}... consumer={payload.get('consumer')!r} "
        f"new_expires={payload['expires']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
