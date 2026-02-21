"""Actions — control plane для aione-top v0.4.

Kill processes, clear cache.
Не імпортує runtime/core/ui — тільки psutil/redis.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

import psutil
import redis


def kill_by_pid(pid: int) -> Tuple[bool, str]:
    """Вбити процес за PID. Повертає (ok, message)."""
    if pid == os.getpid():
        return False, "Cannot kill self"
    try:
        p = psutil.Process(pid)
        p.terminate()
        try:
            p.wait(timeout=5)
        except psutil.TimeoutExpired:
            p.kill()
        return True, f"Killed PID {pid}"
    except psutil.NoSuchProcess:
        return False, f"PID {pid} not found"
    except psutil.AccessDenied:
        return False, f"Access denied: PID {pid}"
    except Exception as e:
        return False, f"Error: {str(e)[:40]}"


def kill_duplicates(procs: List[Dict[str, Any]]) -> str:
    """Вбити всі is_duplicate=True."""
    dups = [p for p in procs if p.get("is_duplicate")]
    if not dups:
        return "No duplicates"
    ok = sum(1 for p in dups if kill_by_pid(p["pid"])[0])
    return f"Killed {ok}/{len(dups)} duplicates"


def kill_all_v3(procs: List[Dict[str, Any]]) -> str:
    """Вбити ВСІ v3 процеси (крім aione_top)."""
    targets = [p for p in procs if p.get("role") != "aione_top"]
    if not targets:
        return "No v3 processes"
    ok = sum(1 for p in targets if kill_by_pid(p["pid"])[0])
    return f"Killed {ok}/{len(targets)} v3 processes"


def clear_redis_ns(cfg: Dict[str, Any]) -> str:
    """Очистити v3 namespace ключі в Redis."""
    rc = cfg.get("redis", {})
    ns = rc.get("namespace", "v3_local")
    try:
        r = redis.Redis(
            host=rc.get("host", "127.0.0.1"),
            port=rc.get("port", 6379),
            db=rc.get("db", 1),
            decode_responses=True, socket_timeout=2,
        )
        keys = list(r.scan_iter(f"{ns}:*", count=1000))
        if not keys:
            return "Redis: 0 keys"
        return f"Redis: deleted {r.delete(*keys)} keys"
    except Exception as e:
        return f"Redis error: {str(e)[:40]}"


def clear_app_cache() -> str:
    """Скинути TTL-кеш колекторів aione-top."""
    from aione_top.collectors import _cache
    _cache._store.clear()
    return "App cache cleared"
