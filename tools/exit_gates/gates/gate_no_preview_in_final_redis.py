from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore

from runtime.store.redis_spec import resolve_redis_spec


def _load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def run_gate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    config_path = str(inputs.get("config_path", "config.json"))
    symbol = inputs.get("symbol")
    tf_s = _parse_int(inputs.get("tf_s"))
    max_events = _parse_int(inputs.get("max_events")) or 200

    if not symbol or tf_s is None:
        return {"ok": False, "details": "symbol_or_tf_missing", "metrics": {}}

    cfg = _load_config(config_path)
    spec = resolve_redis_spec(cfg, role="updates_bus", log=False)
    if spec is None:
        return {"ok": False, "details": "redis.disabled", "metrics": {}}

    if redis_lib is None:
        return {"ok": False, "details": "redis.package_missing", "metrics": {}}

    client = redis_lib.Redis(
        host=spec.host,
        port=spec.port,
        db=spec.db,
        decode_responses=True,
        socket_connect_timeout=0.3,
        socket_timeout=0.3,
    )

    try:
        if client.ping() is not True:
            return {"ok": False, "details": "redis.ping_failed", "metrics": {}}
    except Exception as exc:
        return {"ok": False, "details": f"redis.ping_error:{type(exc).__name__}", "metrics": {}}

    ns = spec.namespace
    list_key = f"{ns}:updates:list:{symbol}:{tf_s}"

    try:
        raw_list = client.lrange(list_key, -max(1, int(max_events)), -1)
    except Exception as exc:
        return {"ok": False, "details": f"redis.lrange_failed:{type(exc).__name__}", "metrics": {}}

    violations: List[str] = []
    scanned = 0
    for raw in raw_list or []:
        scanned += 1
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            ev = json.loads(raw)
        except Exception:
            continue
        complete = ev.get("complete")
        source = ev.get("source")
        if complete is False:
            violations.append("complete=false")
            break
        if isinstance(source, str) and source.startswith("preview"):
            violations.append(f"source={source}")
            break

    if not raw_list:
        return {
            "ok": True,
            "details": "no_events",
            "metrics": {"events_scanned": 0, "violations": 0},
        }

    ok = not violations
    details = "ok" if ok else "found=" + ";".join(violations[:5])
    return {
        "ok": ok,
        "details": details,
        "metrics": {"events_scanned": scanned, "violations": len(violations)},
    }


def main() -> int:
    result = run_gate({})
    if not result.get("ok"):
        details = str(result.get("details", ""))
        if details.startswith("found="):
            details = details[len("found=") :]
        print("EXIT_GATE_FAIL: preview не має потрапляти у final updates")
        for item in details.split(";"):
            if item:
                print(" - " + item)
        return 2
    print("EXIT_GATE_OK: preview не знайдено у final updates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
