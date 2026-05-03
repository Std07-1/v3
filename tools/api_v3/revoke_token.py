"""Revoke an API token (instant via Redis DEL).

Usage:
    python -m tools.api_v3.revoke_token --token tk_abc...
    python -m tools.api_v3.revoke_token --consumer old_news_bot   # revokes ALL tokens for consumer

Returns 0 on success (1+ tokens revoked), 1 on no-match, 2 on bad args.
"""

from __future__ import annotations

import argparse
import sys

from runtime.api_v3.token_store import is_well_formed, token_redis_key
from tools.api_v3._common import get_redis, parse_redis_json
from tools.api_v3.list_tokens import iter_token_keys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Revoke API token(s) (ADR-0058)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--token", help="Specific token to revoke")
    group.add_argument("--consumer", help="Revoke ALL tokens for this consumer")
    args = parser.parse_args(argv)

    client, namespace = get_redis()

    if args.token:
        if not is_well_formed(args.token):
            print(f"ERROR: malformed token shape", file=sys.stderr)
            return 2
        key = token_redis_key(namespace, args.token)
        deleted = client.delete(key)
        if deleted:
            print(f"OK revoked token={args.token[:11]}...")
            return 0
        print(f"NOT_FOUND token={args.token[:11]}...", file=sys.stderr)
        return 1

    # --consumer path: SCAN + match consumer field, DEL each
    revoked = []
    for key in iter_token_keys(client, namespace):
        payload = parse_redis_json(client.get(key))
        if payload is None:
            continue
        if payload.get("consumer") == args.consumer:
            if client.delete(key):
                revoked.append(key.rsplit(":", 1)[-1][:11])

    if not revoked:
        print(f"NOT_FOUND consumer={args.consumer!r}", file=sys.stderr)
        return 1

    print(f"OK revoked count={len(revoked)} prefixes={revoked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
