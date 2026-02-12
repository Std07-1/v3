from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional


_LOG_ONCE_KEYS = set()  # type: set


@dataclass(frozen=True)
class RedisSpec:
    host: str
    port: int
    db: int
    namespace: str
    source: str
    cfg_host: str
    cfg_port: int
    cfg_db: int
    cfg_namespace: str
    mismatch: bool
    mismatch_fields: list[str]


def _env_str(key: str) -> Optional[str]:
    value = os.environ.get(key)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _env_int(key: str) -> Optional[int]:
    raw = _env_str(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def resolve_redis_spec(
    cfg: dict[str, Any],
    *,
    role: str,
    log: bool = True,
) -> Optional[RedisSpec]:
    raw = cfg.get("redis")
    if not isinstance(raw, dict):
        return None
    if not bool(raw.get("enabled", False)):
        return None
    if "ns" in raw:
        raise RuntimeError("redis_ns_key_forbidden")
    raw_namespace = raw.get("namespace")
    if raw_namespace is None or str(raw_namespace).strip() == "":
        raise RuntimeError("redis_namespace_missing")

    cfg_host = str(raw.get("host", "127.0.0.1"))
    cfg_port = int(raw.get("port", 6379))
    cfg_db = int(raw.get("db", 0))
    cfg_namespace = str(raw_namespace)

    host = cfg_host
    port = cfg_port
    db = cfg_db
    namespace = cfg_namespace

    env_host = _env_str("FXCM_REDIS_HOST")
    env_port = _env_int("FXCM_REDIS_PORT")
    env_db = _env_int("FXCM_REDIS_DB")
    env_namespace = _env_str("FXCM_REDIS_NS")
    allow_env_override = bool(raw.get("allow_env_override", False))

    mismatch_fields: list[str] = []
    source = "config"
    if not allow_env_override:
        if env_host is not None:
            mismatch_fields.append("host")
        if env_port is not None:
            mismatch_fields.append("port")
        if env_db is not None:
            mismatch_fields.append("db")
        if env_namespace is not None:
            mismatch_fields.append("namespace")
    else:
        if env_host is not None:
            host = env_host
            source = "env_override"
            if env_host != cfg_host:
                mismatch_fields.append("host")
        if env_port is not None:
            port = env_port
            source = "env_override"
            if env_port != cfg_port:
                mismatch_fields.append("port")
        if env_db is not None:
            db = env_db
            source = "env_override"
            if env_db != cfg_db:
                mismatch_fields.append("db")
        if env_namespace is not None:
            namespace = env_namespace
            source = "env_override"
            if env_namespace != cfg_namespace:
                mismatch_fields.append("namespace")

    mismatch = bool(mismatch_fields)
    if log:
        key = "cfg={}:{}:{}:{}|eff={}:{}:{}:{}|src={}|mismatch={}|fields={}|allow_env_override={}".format(
            cfg_host,
            cfg_port,
            cfg_db,
            cfg_namespace,
            host,
            port,
            db,
            namespace,
            source,
            int(mismatch),
            ",".join(mismatch_fields) if mismatch_fields else "",
            int(bool(allow_env_override)),
        )
        if key not in _LOG_ONCE_KEYS:
            _LOG_ONCE_KEYS.add(key)
            if mismatch:
                logging.warning(
                    "UDS_REDIS_SPEC_MISMATCH fields=%s src=%s",
                    ",".join(mismatch_fields),
                    source,
                )
            if not allow_env_override and mismatch_fields:
                logging.warning(
                    "UDS_REDIS_ENV_OVERRIDE_IGNORED fields=%s",
                    ",".join(mismatch_fields),
                )
    return RedisSpec(
        host=host,
        port=port,
        db=db,
        namespace=namespace,
        source=source,
        cfg_host=cfg_host,
        cfg_port=cfg_port,
        cfg_db=cfg_db,
        cfg_namespace=cfg_namespace,
        mismatch=mismatch,
        mismatch_fields=mismatch_fields,
    )
