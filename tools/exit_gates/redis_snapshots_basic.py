from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional, Tuple

try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore

from runtime.store.redis_keys import symbol_key
from runtime.store.redis_spec import resolve_redis_spec


def _load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_json(client: Any, key: str) -> Tuple[Optional[dict], Optional[str]]:
    try:
        raw = client.get(key)
    except Exception as exc:
        return None, f"redis_get_failed:{type(exc).__name__}"
    if raw is None:
        return None, "redis_miss"
    try:
        payload = json.loads(raw)
    except Exception:
        return None, "redis_json_invalid"
    return payload, None


def run_gate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    config_path = str(inputs.get("config_path", "config.json"))
    symbol = str(inputs.get("symbol", ""))
    tf_s = int(inputs.get("tf_s", 0))
    require_snap = bool(inputs.get("require_snap", True))
    require_status = bool(inputs.get("require_status", True))
    ttl_min_s = int(inputs.get("ttl_min_s", 1))
    connect_timeout_ms = int(inputs.get("connect_timeout_ms", 200))
    socket_timeout_ms = int(inputs.get("socket_timeout_ms", 200))

    cfg = _load_config(config_path)
    spec = resolve_redis_spec(cfg, role="exit_gate", log=False)
    if spec is None:
        return {
            "ok": False,
            "details": "redis.enabled=false або відсутній redis блок",
            "metrics": {},
        }

    if redis_lib is None:
        return {
            "ok": False,
            "details": "python-пакет redis не встановлено",
            "metrics": {},
        }

    host = spec.host
    port = spec.port
    db = spec.db
    ns = spec.namespace

    client = redis_lib.Redis(
        host=host,
        port=port,
        db=db,
        decode_responses=True,
        socket_connect_timeout=max(0.05, connect_timeout_ms / 1000.0),
        socket_timeout=max(0.05, socket_timeout_ms / 1000.0),
    )

    try:
        if client.ping() is not True:
            return {"ok": False, "details": "redis.ping failed", "metrics": {}}
    except Exception as exc:
        return {"ok": False, "details": f"redis.ping error: {exc}", "metrics": {}}

    now_ms = int(time.time() * 1000)
    prime_key = f"{ns}:prime:ready"
    prime_payload, prime_err = _read_json(client, prime_key)
    prime_ready = False
    if prime_err is None and isinstance(prime_payload, dict):
        ready_flag = prime_payload.get("ready")
        prime_ready = bool(ready_flag) if isinstance(ready_flag, bool) else True

    metrics: Dict[str, Any] = {"now_ms": now_ms, "prime_ready": prime_ready}
    if not prime_ready:
        return {
            "ok": True,
            "details": "skip:prime_not_ready",
            "metrics": metrics,
        }

    snap_key = f"{ns}:ohlcv:snap:{symbol_key(symbol)}:{tf_s}"
    status_key = f"{ns}:status:snapshot"

    if require_snap:
        snap_raw = client.get(snap_key)
        if not snap_raw:
            try:
                pattern = f"{ns}:ohlcv:snap:*:{tf_s}"
                sample = list(client.scan_iter(match=pattern, count=20))[:5]
                metrics["snap_keys_sample"] = [
                    k.decode("utf-8", errors="ignore") if isinstance(k, bytes) else str(k)
                    for k in sample
                ]
            except Exception:
                pass
            return {
                "ok": False,
                "details": f"snap key missing: {snap_key}",
                "metrics": metrics,
            }
        ttl = int(client.ttl(snap_key))
        metrics["snap_ttl_s"] = ttl
        if ttl < ttl_min_s:
            return {
                "ok": False,
                "details": f"snap ttl too low: {ttl}",
                "metrics": metrics,
            }

    if require_status:
        status_raw = client.get(status_key)
        if not status_raw:
            return {
                "ok": False,
                "details": f"status key missing: {status_key}",
                "metrics": metrics,
            }
        metrics["status_bytes"] = len(status_raw)

    return {"ok": True, "details": "ok", "metrics": metrics}
