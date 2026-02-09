from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore


DEFAULT_SOURCE_ALLOWLIST = {"history", "derived", "history_agg", ""}


def _symbol_key(symbol: str) -> str:
    return str(symbol).strip().replace("/", "_")


def _load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _key_parts(key: str) -> List[str]:
    return key.split(":")


def _parse_symbol_tf_from_key(key: str, ns: str, kind: str) -> Optional[Tuple[str, int]]:
    parts = _key_parts(key)
    if len(parts) < 5:
        return None
    if parts[0] != ns or parts[1] != "ohlcv" or parts[2] != kind:
        return None
    tf_s = _parse_int(parts[-1])
    if tf_s is None:
        return None
    symbol = ":".join(parts[3:-1])
    if not symbol:
        return None
    return symbol, tf_s


def _scan_keys(client: Any, pattern: str) -> Iterable[str]:
    try:
        for key in client.scan_iter(match=pattern):
            if isinstance(key, bytes):
                yield key.decode("utf-8", errors="ignore")
            else:
                yield str(key)
    except Exception:
        return []


def _read_json(client: Any, key: str) -> Tuple[Optional[dict], Optional[int], Optional[str]]:
    try:
        raw = client.get(key)
    except Exception as exc:
        return None, None, f"redis_get_failed:{type(exc).__name__}"
    if raw is None:
        return None, None, "redis_miss"
    try:
        payload = json.loads(raw)
    except Exception:
        return None, None, "redis_json_invalid"
    ttl_left: Optional[int] = None
    try:
        ttl = client.ttl(key)
        if isinstance(ttl, int) and ttl >= 0:
            ttl_left = ttl
    except Exception:
        ttl_left = None
    return payload, ttl_left, None


def _violations_summary(violations: List[str], keys_checked: int, ttl_min: Optional[int]) -> str:
    ttl_txt = str(ttl_min) if ttl_min is not None else "n/a"
    if not violations:
        return f"keys_checked={keys_checked} ttl_min={ttl_txt} violations=0"
    short = ",".join(violations[:6])
    more = "" if len(violations) <= 6 else f"+{len(violations) - 6}"
    return (
        f"keys_checked={keys_checked} ttl_min={ttl_txt} "
        f"violations={len(violations)} list={short}{more}"
    )


def run_gate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    config_path = str(inputs.get("config_path", "config.json"))
    symbol_in = inputs.get("symbol")
    tf_s_in = _parse_int(inputs.get("tf_s"))
    ttl_min_s = _parse_int(inputs.get("ttl_min_s")) or 1
    allow_empty_keys = bool(inputs.get("allow_empty_keys", False))
    allow_empty_tail = bool(inputs.get("allow_empty_tail", False))
    max_status_bytes = _parse_int(inputs.get("max_status_bytes")) or 16384
    source_allowlist = set(inputs.get("source_allowlist", [])) or DEFAULT_SOURCE_ALLOWLIST

    cfg = _load_config(config_path)
    redis_cfg = cfg.get("redis")
    if not isinstance(redis_cfg, dict) or not bool(redis_cfg.get("enabled", False)):
        return {"ok": False, "details": "redis.disabled", "metrics": {}}

    if redis_lib is None:
        return {"ok": False, "details": "redis.package_missing", "metrics": {}}

    host = str(redis_cfg.get("host", "127.0.0.1"))
    port = int(redis_cfg.get("port", 6379))
    db = int(redis_cfg.get("db", 0))
    ns = str(redis_cfg.get("ns", "v3"))

    client = redis_lib.Redis(
        host=host,
        port=port,
        db=db,
        decode_responses=True,
        socket_connect_timeout=0.3,
        socket_timeout=0.3,
    )

    try:
        if client.ping() is not True:
            return {"ok": False, "details": "redis.ping_failed", "metrics": {}}
    except Exception as exc:
        return {"ok": False, "details": f"redis.ping_error:{type(exc).__name__}", "metrics": {}}

    violations: List[str] = []
    keys_checked = 0
    ttl_min_seen: Optional[int] = None

    snap_pattern = f"{ns}:ohlcv:snap:*"
    snap_keys = list(_scan_keys(client, snap_pattern))
    if not snap_keys:
        if allow_empty_keys:
            return {
                "ok": True,
                "details": "skip:no_keys",
                "metrics": {"keys_checked": 0, "ttl_min": None},
            }
        return {"ok": False, "details": "no_snap_keys", "metrics": {"keys_checked": 0}}

    target_symbol = _symbol_key(symbol_in) if symbol_in else None
    target_tf_s = tf_s_in if tf_s_in is not None else None

    if target_symbol is None or target_tf_s is None:
        parsed = None
        for key in snap_keys:
            parsed = _parse_symbol_tf_from_key(key, ns, "snap")
            if parsed:
                target_symbol, target_tf_s = parsed
                break
        if parsed is None:
            return {"ok": False, "details": "snap_key_parse_failed", "metrics": {"keys_checked": 0}}

    assert target_symbol is not None
    assert target_tf_s is not None

    snap_key = f"{ns}:ohlcv:snap:{target_symbol}:{target_tf_s}"
    tail_key = f"{ns}:ohlcv:tail:{target_symbol}:{target_tf_s}"
    status_key = f"{ns}:status:snapshot"

    snap_payload, snap_ttl, snap_err = _read_json(client, snap_key)
    keys_checked += 1
    if snap_err is not None or snap_payload is None:
        violations.append(f"snap:{snap_err or 'empty'}")
    else:
        bar = snap_payload.get("bar") if isinstance(snap_payload, dict) else None
        if not isinstance(bar, dict):
            violations.append("snap:bar_missing")
        else:
            open_ms = _parse_int(bar.get("open_ms"))
            close_ms = _parse_int(bar.get("close_ms"))
            if open_ms is None or close_ms is None:
                violations.append("snap:time_not_int")
            else:
                # Політика: Redis snapshots для UI мають end-incl close_ms (open + tf*1000 - 1).
                expect_close = open_ms + target_tf_s * 1000 - 1
                if close_ms != expect_close:
                    violations.append("snap:close_time_invalid")
        complete = snap_payload.get("complete") if isinstance(snap_payload, dict) else None
        if not isinstance(complete, bool):
            violations.append("snap:complete_not_bool")
        if complete is True:
            event_ts_ms = _parse_int(snap_payload.get("event_ts_ms"))
            if event_ts_ms is None or bar is None:
                violations.append("snap:event_ts_missing")
            else:
                close_ms = _parse_int(bar.get("close_ms"))
                if close_ms is None or event_ts_ms != close_ms:
                    violations.append("snap:event_ts_invalid")
        source = snap_payload.get("source") if isinstance(snap_payload, dict) else None
        if not isinstance(source, str) or source not in source_allowlist:
            violations.append("snap:source_not_allowed")
        if _parse_int(snap_payload.get("payload_ts_ms")) is None:
            violations.append("snap:payload_ts_invalid")
        if _parse_int(snap_payload.get("seq")) is None:
            violations.append("snap:seq_invalid")

    if snap_ttl is None or snap_ttl <= 0 or snap_ttl < ttl_min_s:
        violations.append("snap:ttl_invalid")
    if snap_ttl is not None:
        ttl_min_seen = snap_ttl if ttl_min_seen is None else min(ttl_min_seen, snap_ttl)

    tail_payload, tail_ttl, tail_err = _read_json(client, tail_key)
    keys_checked += 1
    if tail_err is not None or tail_payload is None:
        violations.append(f"tail:{tail_err or 'empty'}")
    else:
        bars = tail_payload.get("bars") if isinstance(tail_payload, dict) else None
        if not isinstance(bars, list):
            violations.append("tail:bars_missing")
        elif not bars and not allow_empty_tail:
            violations.append("tail:bars_empty")
        else:
            for item in bars:
                if not isinstance(item, dict):
                    violations.append("tail:bar_invalid")
                    continue
                open_ms = _parse_int(item.get("open_ms"))
                close_ms = _parse_int(item.get("close_ms"))
                if open_ms is None or close_ms is None:
                    violations.append("tail:time_not_int")
                    continue
                # Політика: Redis snapshots для UI мають end-incl close_ms (open + tf*1000 - 1).
                expect_close = open_ms + target_tf_s * 1000 - 1
                if close_ms != expect_close:
                    violations.append("tail:close_time_invalid")
        last_seq = _parse_int(tail_payload.get("last_seq")) if isinstance(tail_payload, dict) else None
        if last_seq is None:
            violations.append("tail:last_seq_invalid")
        else:
            max_seq = None
            if isinstance(bars, list):
                for item in bars:
                    if isinstance(item, dict) and "seq" in item:
                        seq_val = _parse_int(item.get("seq"))
                        if seq_val is not None:
                            max_seq = seq_val if max_seq is None else max(max_seq, seq_val)
            if max_seq is not None and last_seq < max_seq:
                violations.append("tail:last_seq_lt_bars_seq")
        source = tail_payload.get("source") if isinstance(tail_payload, dict) else None
        if not isinstance(source, str) or source not in source_allowlist:
            violations.append("tail:source_not_allowed")
        if _parse_int(tail_payload.get("payload_ts_ms")) is None:
            violations.append("tail:payload_ts_invalid")

    if tail_ttl is None or tail_ttl <= 0 or tail_ttl < ttl_min_s:
        violations.append("tail:ttl_invalid")
    if tail_ttl is not None:
        ttl_min_seen = tail_ttl if ttl_min_seen is None else min(ttl_min_seen, tail_ttl)

    status_payload, status_ttl, status_err = _read_json(client, status_key)
    keys_checked += 1
    if status_err is not None or status_payload is None:
        violations.append(f"status:{status_err or 'empty'}")
    else:
        raw = json.dumps(status_payload, ensure_ascii=False)
        if len(raw.encode("utf-8")) > max_status_bytes:
            violations.append("status:bytes_over_limit")
        if not isinstance(status_payload.get("boot_id"), str):
            violations.append("status:boot_id_invalid")
        if _parse_int(status_payload.get("now_ms")) is None:
            violations.append("status:now_ms_invalid")
        redis_obj = status_payload.get("redis")
        if not isinstance(redis_obj, dict) or not isinstance(redis_obj.get("ok"), bool):
            violations.append("status:redis_ok_invalid")
        bars_obj = status_payload.get("bars")
        if not isinstance(bars_obj, dict):
            violations.append("status:bars_missing")
        else:
            last_final = bars_obj.get("last_final_close_ms")
            if last_final is not None and _parse_int(last_final) is None:
                violations.append("status:last_final_invalid")

    details = _violations_summary(violations, keys_checked, ttl_min_seen)
    return {
        "ok": len(violations) == 0,
        "details": details,
        "metrics": {
            "keys_checked": keys_checked,
            "ttl_min": ttl_min_seen,
            "violations": len(violations),
        },
    }
