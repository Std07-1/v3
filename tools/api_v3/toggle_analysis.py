"""ADR-0059 §3.4 — CLI to toggle the analysis API kill switch (slice 059.4).

Usage:
    python -m tools.api_v3.toggle_analysis --status
    python -m tools.api_v3.toggle_analysis --off [--ttl 3600]
    python -m tools.api_v3.toggle_analysis --on

Behavior:
    --status  : Print current state (on/off, TTL if any) and exit 0.
    --off     : SET the runtime kill flag in Redis. Optional --ttl seconds
                makes the kill auto-expire; omit for sticky (no TTL).
                Subsequent /api/v3/bars/* and /api/v3/smc/* requests
                receive 503 `analysis_disabled_runtime`.
    --on      : DEL the runtime kill flag. Analysis serves normally
                provided the config layer (`api_v3.analysis_enabled`) is
                also true.

Notes:
    * This CLI affects only the runtime layer (Layer 2). The config layer
      (Layer 1, `api_v3.analysis_enabled`) requires editing config.json
      and restarting `smc:smc-ws`.
    * Redis key SSOT lives in `runtime/api_v3/kill_switch.kill_flag_redis_key`
      to keep this CLI and the middleware in lockstep.
    * Fail-open semantics on the server side: if Redis is unreachable when
      a request arrives, analysis is served (F-S1-002). This means the
      kill switch is best-effort during a Redis outage.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Optional

from runtime.api_v3.kill_switch import kill_flag_redis_key
from tools.api_v3._common import get_redis

KILL_VALUE = "1"  # presence is what matters; any non-null value works


def _print_status(*, present: bool, ttl_s: Optional[int], key: str) -> None:
    if not present:
        print(f"[on] analysis serving normally (key absent)  key={key}")
        return
    if ttl_s is None or ttl_s < 0:
        # -1 = sticky (no expire), -2 = key vanished mid-call (race)
        ttl_label = "no expiry (sticky)" if ttl_s == -1 else "key disappeared"
        print(f"[off] analysis BLOCKED — {ttl_label}  key={key}")
    else:
        print(f"[off] analysis BLOCKED — auto-expires in {ttl_s}s  key={key}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Toggle the public analysis API kill switch (ADR-0059)"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--off",
        action="store_true",
        help="Engage the kill switch — analysis returns 503",
    )
    group.add_argument(
        "--on",
        action="store_true",
        help="Disengage the kill switch — analysis serves normally",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Print current state without modifying anything",
    )
    parser.add_argument(
        "--ttl",
        type=int,
        default=None,
        help="(--off only) seconds before the kill auto-expires; omit for sticky",
    )
    args = parser.parse_args(argv)

    if args.ttl is not None and not args.off:
        print("ERROR: --ttl is only valid with --off", file=sys.stderr)
        return 2
    if args.ttl is not None and args.ttl < 1:
        print("ERROR: --ttl must be ≥ 1 second", file=sys.stderr)
        return 2

    client, namespace = get_redis()
    key = kill_flag_redis_key(namespace)

    try:
        if args.status:
            exists_raw: Any = client.exists(key)
            present = bool(exists_raw)
            if present:
                ttl_raw: Any = client.ttl(key)
                ttl_s: Optional[int] = int(ttl_raw)
            else:
                ttl_s = None
            _print_status(present=present, ttl_s=ttl_s, key=key)
            return 0

        if args.off:
            if args.ttl:
                client.setex(key, args.ttl, KILL_VALUE)
                print(
                    f"OK: kill switch ENGAGED (auto-expires in {args.ttl}s)  key={key}"
                )
            else:
                client.set(key, KILL_VALUE)
                print(f"OK: kill switch ENGAGED (sticky, no TTL)  key={key}")
            return 0

        if args.on:
            del_raw: Any = client.delete(key)
            removed = int(del_raw)
            if removed:
                print(f"OK: kill switch RELEASED  key={key}")
            else:
                print(f"OK: kill switch already off (key absent)  key={key}")
            return 0
    except Exception as exc:  # pragma: no cover — surfaced loud
        print(f"ERROR: redis operation failed: {exc}", file=sys.stderr)
        return 1

    return 0  # unreachable (mutually exclusive group)


if __name__ == "__main__":
    sys.exit(main())
