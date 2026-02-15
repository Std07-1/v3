from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, request, parse


def _http_get_json(url: str, timeout_s: float = 3.0) -> Tuple[int, bytes, Optional[dict], Optional[str]]:
    req = request.Request(url, headers={"Cache-Control": "no-store"})
    try:
        with request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
            code = int(resp.status)
    except error.URLError as exc:
        return 0, b"", None, f"url_error:{type(exc).__name__}"
    except Exception as exc:
        return 0, b"", None, f"http_error:{type(exc).__name__}"
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        data = None
    return code, raw, data, None


def _parse_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _validate_lwc_bar(bar: dict, violations: List[str]) -> None:
    for key in ("time", "open", "high", "low", "close", "volume"):
        if key not in bar:
            violations.append("bar:missing_lwc")
            break
    for key in ("open_time_ms", "close_time_ms", "tf_s", "src", "complete"):
        if key not in bar:
            violations.append("bar:missing_meta")
            break

    open_ms = _parse_int(bar.get("open_time_ms"))
    close_ms = _parse_int(bar.get("close_time_ms"))
    tf_s = _parse_int(bar.get("tf_s"))
    if open_ms is None or close_ms is None or tf_s is None:
        violations.append("bar:time_not_int")
        return
    expect_close = open_ms + tf_s * 1000
    if close_ms != expect_close:
        violations.append("bar:close_time_invalid")
    if not isinstance(bar.get("complete"), bool):
        violations.append("bar:complete_not_bool")
    if bar.get("complete") is True:
        event_ts = _parse_int(bar.get("event_ts"))
        if event_ts is None or event_ts != close_ms:
            violations.append("bar:event_ts_invalid")
    if not isinstance(bar.get("complete"), bool):
        violations.append("bar:complete_not_bool")
    if bar.get("complete") is True:
        event_ts = _parse_int(bar.get("event_ts"))
        if event_ts is None or event_ts != close_ms:
            violations.append("bar:event_ts_invalid")


def run_gate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    base_url = str(inputs.get("base_url", "http://127.0.0.1:8089"))
    symbol = str(inputs.get("symbol", "XAU/USD"))
    tf_s = int(inputs.get("tf_s", 300))
    prefer_redis = bool(inputs.get("prefer_redis", True))
    limit = int(inputs.get("limit", 20000))
    timeout_s = float(inputs.get("timeout_s", 3.0))

    qs = {
        "symbol": symbol,
        "tf_s": str(tf_s),
        "limit": str(limit),
        "prefer_redis": "1" if prefer_redis else "0",
    }
    url = base_url.rstrip("/") + "/api/bars?" + parse.urlencode(qs)

    violations: List[str] = []
    code, raw, data, err = _http_get_json(url, timeout_s=timeout_s)
    if err:
        return {"ok": False, "details": err, "metrics": {"status": code}}
    if code != 200 or not isinstance(data, dict):
        return {"ok": False, "details": "http_or_json_invalid", "metrics": {"status": code}}

    bars = data.get("bars")
    meta = data.get("meta")
    if not isinstance(bars, list):
        violations.append("bars_missing")
    if not isinstance(meta, dict):
        violations.append("meta_missing")

    if isinstance(meta, dict):
        redis_hit = meta.get("redis_hit")
        source = meta.get("source")
        if redis_hit is True:
            if source not in ("redis_tail", "redis_snap", "redis"):
                violations.append("meta:source_not_redis")
            if _parse_int(meta.get("redis_ttl_s_left")) is None:
                violations.append("meta:ttl_missing")
            if _parse_int(meta.get("redis_payload_ts_ms")) is None:
                violations.append("meta:payload_ts_missing")
            if _parse_int(meta.get("redis_seq")) is None:
                violations.append("meta:seq_missing")
        elif redis_hit is False:
            if source != "disk":
                violations.append("meta:source_not_disk")
            if not isinstance(meta.get("redis_error_code"), str):
                violations.append("meta:error_code_missing")
        else:
            violations.append("meta:redis_hit_invalid")

    if isinstance(bars, list):
        for bar in bars:
            if not isinstance(bar, dict):
                violations.append("bar:not_object")
                continue
            _validate_lwc_bar(bar, violations)

    details = f"violations={len(violations)} bytes={len(raw)}"
    return {
        "ok": len(violations) == 0,
        "details": details,
        "metrics": {"violations": len(violations), "bytes": len(raw)},
    }
