from __future__ import annotations

import json
import time
from typing import Any, Dict

try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore


def _load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


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
    redis_cfg = cfg.get("redis")
    if not isinstance(redis_cfg, dict) or not bool(redis_cfg.get("enabled", False)):
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

    host = str(redis_cfg.get("host", "127.0.0.1"))
    port = int(redis_cfg.get("port", 6379))
    db = int(redis_cfg.get("db", 0))
    ns = str(redis_cfg.get("ns", "v3"))

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
    snap_key = f"{ns}:ohlcv:snap:{symbol}:{tf_s}"
    status_key = f"{ns}:status:snapshot"

    metrics: Dict[str, Any] = {"now_ms": now_ms}

    if require_snap:
        snap_raw = client.get(snap_key)
        if not snap_raw:
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
