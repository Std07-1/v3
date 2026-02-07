from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple
from urllib import error, request, parse

try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore


def _http_get_bytes(url: str, timeout_s: float = 3.0) -> Tuple[int, bytes, Optional[str]]:
    req = request.Request(url, headers={"Cache-Control": "no-store"})
    try:
        with request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
            return int(resp.status), raw, None
    except error.URLError as exc:
        return 0, b"", f"url_error:{type(exc).__name__}"
    except Exception as exc:
        return 0, b"", f"http_error:{type(exc).__name__}"


def _load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def run_gate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    base_url = str(inputs.get("base_url", "http://127.0.0.1:8089"))
    symbol = str(inputs.get("symbol", "XAU/USD"))
    tf_s = int(inputs.get("tf_s", 300))
    config_path = str(inputs.get("config_path", "config.json"))
    max_status_bytes = int(inputs.get("max_status_bytes", 16384))
    max_updates_bytes = int(inputs.get("max_updates_bytes", 256000))
    max_bars_bytes = int(inputs.get("max_bars_bytes", 1000000))
    timeout_s = float(inputs.get("timeout_s", 3.0))

    violations = []
    metrics: Dict[str, Any] = {}

    bars_url = base_url.rstrip("/") + "/api/bars?" + parse.urlencode(
        {"symbol": symbol, "tf_s": str(tf_s), "prefer_redis": "1", "limit": "20000"}
    )
    bars_err = None
    code, raw, err = _http_get_bytes(bars_url, timeout_s=timeout_s)
    if err:
        bars_err = err
        violations.append(err)
    if code != 200:
        violations.append("bars_http_status")
    bars_bytes = len(raw)
    metrics["bars_bytes"] = bars_bytes
    if bars_bytes > max_bars_bytes:
        violations.append("bars_bytes_over")

    updates_url = base_url.rstrip("/") + "/api/updates?" + parse.urlencode(
        {"symbol": symbol, "tf_s": str(tf_s), "since_seq": "0"}
    )
    updates_err = None
    code, raw, err = _http_get_bytes(updates_url, timeout_s=timeout_s)
    if err:
        updates_err = err
        violations.append(err)
    if code != 200:
        violations.append("updates_http_status")
    updates_bytes = len(raw)
    metrics["updates_bytes"] = updates_bytes
    if updates_bytes > max_updates_bytes:
        violations.append("updates_bytes_over")

    if bars_err and updates_err:
        if bars_err.startswith(("url_error:", "http_error:")) and updates_err.startswith(
            ("url_error:", "http_error:")
        ):
            return {
                "ok": False,
                "details": bars_err,
                "metrics": metrics,
            }

    cfg = _load_config(config_path)
    redis_cfg = cfg.get("redis")
    if isinstance(redis_cfg, dict) and bool(redis_cfg.get("enabled", False)):
        if redis_lib is None:
            violations.append("redis_package_missing")
        else:
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
            status_key = f"{ns}:status:snapshot"
            try:
                status_raw = client.get(status_key)
            except Exception:
                status_raw = None
                violations.append("status_read_failed")
            if status_raw:
                status_bytes = len(status_raw.encode("utf-8"))
                metrics["status_bytes"] = status_bytes
                if status_bytes > max_status_bytes:
                    violations.append("status_bytes_over")
            else:
                violations.append("status_missing")
    else:
        violations.append("redis_disabled")

    details = f"violations={len(violations)} bars_bytes={metrics.get('bars_bytes')} updates_bytes={metrics.get('updates_bytes')} status_bytes={metrics.get('status_bytes')}"
    return {
        "ok": len(violations) == 0,
        "details": details,
        "metrics": metrics,
    }
