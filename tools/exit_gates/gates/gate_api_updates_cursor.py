from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple
from urllib import error, parse, request


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


def run_gate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    base_url = str(inputs.get("base_url", "http://127.0.0.1:8089"))
    symbol = inputs.get("symbol")
    tf_s = inputs.get("tf_s")
    since_seq = inputs.get("since_seq")
    timeout_s = float(inputs.get("timeout_s", 3.0))

    qs = {}
    if symbol:
        qs["symbol"] = str(symbol)
    if tf_s is not None:
        qs["tf_s"] = str(int(tf_s))
    if since_seq is not None:
        qs["since_seq"] = str(int(since_seq))

    url = base_url.rstrip("/") + "/api/updates?" + parse.urlencode(qs)

    code, raw, data, err = _http_get_json(url, timeout_s=timeout_s)
    if err:
        return {"ok": False, "details": err, "metrics": {"status": code}}
    if code != 200 or not isinstance(data, dict):
        return {"ok": False, "details": "http_or_json_invalid", "metrics": {"status": code}}

    events = data.get("events")
    cursor_seq = _parse_int(data.get("cursor_seq"))
    if not isinstance(events, list) or cursor_seq is None:
        return {
            "ok": False,
            "details": "events_or_cursor_missing",
            "metrics": {"events": 0},
        }

    max_seq: Optional[int] = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        seq = _parse_int(ev.get("seq"))
        if seq is None:
            continue
        max_seq = seq if max_seq is None else max(max_seq, seq)

    expected = max_seq
    if expected is None:
        if since_seq is None:
            expected = 0
        else:
            expected = int(since_seq)

    ok = cursor_seq == expected
    details = "ok" if ok else f"cursor_seq_mismatch:cursor={cursor_seq},expected={expected}"
    return {
        "ok": ok,
        "details": details,
        "metrics": {
            "events": len(events),
            "cursor_seq": cursor_seq,
            "expected": expected,
        },
    }
