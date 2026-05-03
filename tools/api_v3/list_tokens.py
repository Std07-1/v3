"""List all active API tokens (read-only audit).

Usage:
    python -m tools.api_v3.list_tokens [--json]

Default output: human-readable table.
With --json: JSONL (one record per line) for piping to jq/grep.

Token full value is NEVER displayed — only the 8-char prefix.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Iterator

from tools.api_v3._common import get_redis, parse_redis_json


def iter_token_keys(client, namespace: str, batch: int = 100) -> Iterator[str]:
    """SCAN over {namespace}:tokens:* (avoids KEYS blocking on large DBs)."""
    pattern = f"{namespace}:tokens:*"
    cursor = 0
    while True:
        cursor, keys = client.scan(cursor=cursor, match=pattern, count=batch)
        for key in keys:
            yield key
        if cursor == 0:
            break


def parse_record(client, key: str) -> dict | None:
    """Read + parse one token record. Includes TTL and prefix."""
    raw = client.get(key)
    ttl_s = client.ttl(key)
    payload = parse_redis_json(raw)
    if raw is not None and payload is None:
        return {"key_prefix": key.rsplit(":", 1)[-1][:11], "error": "malformed_json", "ttl_s": ttl_s}
    if payload is None:
        return None
    token = key.rsplit(":", 1)[-1]
    return {
        "key_prefix": token[:11],  # "tk_" + 8 hex chars
        "consumer": payload.get("consumer"),
        "scope": payload.get("scope"),
        "created": payload.get("created"),
        "expires": payload.get("expires"),
        "ttl_s": int(ttl_s) if isinstance(ttl_s, (int, float)) else -1,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List API tokens (ADR-0058)")
    parser.add_argument("--json", action="store_true", help="JSONL output instead of table")
    args = parser.parse_args(argv)

    client, namespace = get_redis()
    records = []
    for key in iter_token_keys(client, namespace):
        record = parse_record(client, key)
        if record is not None:
            records.append(record)

    if args.json:
        for record in records:
            print(json.dumps(record, ensure_ascii=False))
        return 0

    if not records:
        print(f"(no tokens in {namespace}:tokens:*)")
        return 0

    print(f"{'PREFIX':<13} {'CONSUMER':<24} {'SCOPE':<8} {'TTL_DAYS':>8}  EXPIRES")
    print("-" * 80)
    for record in sorted(records, key=lambda r: r.get("consumer") or ""):
        ttl_s = record.get("ttl_s", -1)
        ttl_days = "expired" if ttl_s < 0 else f"{ttl_s // 86400}"
        print(
            f"{record.get('key_prefix','?'):<13} "
            f"{(record.get('consumer') or '?'):<24} "
            f"{(record.get('scope') or '?'):<8} "
            f"{ttl_days:>8}  "
            f"{record.get('expires','?')}"
        )
    print(f"\ntotal={len(records)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
