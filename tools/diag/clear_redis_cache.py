"""Утиліта очищення Redis-кешу для v3 (preview/updates/all).

За замовчуванням працює у режимі dry-run і не видаляє ключі.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config_loader import load_system_config, pick_config_path
from runtime.store.redis_spec import resolve_redis_spec

try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore


def _iter_keys(client, patterns: Iterable[str]):
    for pattern in patterns:
        for key in client.scan_iter(match=pattern, count=1000):
            yield key


def _delete_keys(client, keys: List[str]) -> int:
    if not keys:
        return 0
    deleted = 0
    chunk = 500
    for i in range(0, len(keys), chunk):
        part = keys[i : i + chunk]
        try:
            deleted += int(client.unlink(*part))
        except Exception:
            deleted += int(client.delete(*part))
    return deleted


def main() -> int:
    ap = argparse.ArgumentParser(description="Очистка Redis-кешу v3 за namespace")
    ap.add_argument("--config", default=None, help="Шлях до config.json (default: auto)")
    ap.add_argument(
        "--scope",
        choices=["preview", "updates", "all"],
        default="preview",
        help="Що чистити: preview | updates | all",
    )
    ap.add_argument(
        "--flushdb",
        action="store_true",
        help="Виконати FLUSHDB для поточного db (небезпечна повна очистка DB)",
    )
    ap.add_argument("--yes", action="store_true", help="Підтвердити реальне видалення")
    args = ap.parse_args()

    if redis_lib is None:
        print("redis бібліотека недоступна")
        return 2

    config_path = args.config if args.config else pick_config_path()
    cfg = load_system_config(config_path)
    spec = resolve_redis_spec(cfg, role="tools_cache_clear", log=False)
    if spec is None:
        print("Redis вимкнено у config.json")
        return 2

    client = redis_lib.Redis(
        host=spec.host,
        port=spec.port,
        db=spec.db,
        decode_responses=True,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )

    ns = spec.namespace
    if args.scope == "preview":
        patterns = [
            "{}:preview:*".format(ns),
        ]
    elif args.scope == "updates":
        patterns = [
            "{}:updates:*".format(ns),
            "{}:preview:updates:*".format(ns),
        ]
    else:
        patterns = ["{}:*".format(ns)]

    keys = sorted(set(_iter_keys(client, patterns)))
    print(
        "Redis target host={} port={} db={} ns={} scope={} keys={}".format(
            spec.host,
            spec.port,
            spec.db,
            spec.namespace,
            args.scope,
            len(keys),
        )
    )

    if args.flushdb:
        if not args.yes:
            print("DRY-RUN: для FLUSHDB додайте --yes")
            return 0
        client.flushdb()
        print("FLUSHDB done for db={} (namespace filter bypassed)".format(spec.db))
        return 0

    if not args.yes:
        preview = keys[:20]
        if preview:
            print("DRY-RUN sample keys:")
            for key in preview:
                print("  " + str(key))
        print("DRY-RUN done. Додайте --yes для видалення.")
        return 0

    deleted = _delete_keys(client, keys)
    print("Deleted keys: {}".format(deleted))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
