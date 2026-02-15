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


def _event_key(ev: dict) -> Optional[Tuple[str, int, int]]:
    key = ev.get("key") if isinstance(ev, dict) else None
    if not isinstance(key, dict):
        return None
    symbol = key.get("symbol")
    tf_s = _parse_int(key.get("tf_s"))
    open_ms = _parse_int(key.get("open_ms"))
    if not isinstance(symbol, str) or tf_s is None or open_ms is None:
        return None
    return symbol, tf_s, open_ms


def run_gate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    base_url = str(inputs.get("base_url", "http://127.0.0.1:8089"))
    symbol = inputs.get("symbol")
    tf_s = inputs.get("tf_s")
    since_seq = int(inputs.get("since_seq", 0))
    max_events_expected = int(inputs.get("max_events_expected", 500))
    timeout_s = float(inputs.get("timeout_s", 3.0))

    qs = {"since_seq": str(since_seq)}
    if symbol:
        qs["symbol"] = str(symbol)
    if tf_s is not None:
        qs["tf_s"] = str(tf_s)
    url = base_url.rstrip("/") + "/api/updates?" + parse.urlencode(qs)

    violations: List[str] = []
    code, raw, data, err = _http_get_json(url, timeout_s=timeout_s)
    if err:
        return {"ok": False, "details": err, "metrics": {"status": code}}
    if code != 200 or not isinstance(data, dict):
        return {"ok": False, "details": "http_or_json_invalid", "metrics": {"status": code}}

    events = data.get("events")
    cursor_seq = _parse_int(data.get("cursor_seq"))
    disk_last = data.get("disk_last_open_ms")
    api_seen = _parse_int(data.get("api_seen_ts_ms"))
    ssot_write = data.get("ssot_write_ts_ms")

    if not isinstance(events, list):
        violations.append("events_missing")
    if cursor_seq is None:
        violations.append("cursor_seq_missing")
    if disk_last is not None and _parse_int(disk_last) is None:
        violations.append("disk_last_invalid")
    if api_seen is None:
        violations.append("api_seen_missing")
    if ssot_write is not None and _parse_int(ssot_write) is None:
        violations.append("ssot_write_invalid")

    if isinstance(events, list) and len(events) > max_events_expected:
        violations.append("events_over_max")

    max_seq = None
    final_sources: Dict[Tuple[str, int, int], str] = {}

    if isinstance(events, list):
        for ev in events:
            if not isinstance(ev, dict):
                violations.append("event_not_object")
                continue
            key = _event_key(ev)
            if key is None:
                violations.append("event_key_invalid")
            seq = _parse_int(ev.get("seq"))
            if seq is None:
                violations.append("event_seq_invalid")
            else:
                max_seq = seq if max_seq is None else max(max_seq, seq)
            bar = ev.get("bar") if isinstance(ev.get("bar"), dict) else None
            complete = ev.get("complete") if ev.get("complete") is not None else None
            if not isinstance(complete, bool):
                violations.append("event_complete_invalid")
            if bar is not None:
                open_ms = _parse_int(bar.get("open_time_ms"))
                close_ms = _parse_int(bar.get("close_time_ms"))
                tf_s_bar = _parse_int(bar.get("tf_s"))
                if open_ms is None or close_ms is None or tf_s_bar is None:
                    violations.append("event_bar_time_invalid")
                else:
                    expect_close = open_ms + tf_s_bar * 1000
                    if close_ms != expect_close:
                        violations.append("event_bar_close_invalid")
            event_ts = ev.get("event_ts")
            if complete is True:
                if bar is None:
                    violations.append("event_bar_missing")
                else:
                    close_ms = _parse_int(bar.get("close_time_ms"))
                    event_ts_i = _parse_int(event_ts)
                    if event_ts_i is None or close_ms is None or event_ts_i != close_ms:
                        violations.append("event_ts_invalid")
            if complete is False:
                if event_ts not in (None,):
                    violations.append("event_ts_should_be_null")
            source = ev.get("source")
            if complete is True and key is not None and isinstance(source, str):
                if source == "tick_promoted":
                    continue
                prev = final_sources.get(key)
                if prev is None:
                    final_sources[key] = source
                elif prev != source:
                    violations.append("nomix_final_source")

    if cursor_seq is not None and max_seq is not None and cursor_seq < max_seq:
        violations.append("cursor_seq_lt_max")

    details = f"violations={len(violations)} events={(len(events) if isinstance(events, list) else 0)}"
    return {
        "ok": len(violations) == 0,
        "details": details,
        "metrics": {"violations": len(violations), "events": len(events) if isinstance(events, list) else 0},
    }
