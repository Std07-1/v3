#!/usr/bin/env python3
"""
last_bucket_probe — перевірка наявності конкретного derived H4 бару.

Приклад:
  python -m tools.audit.last_bucket_probe --symbol XAU/USD --tf-s 14400 --align tv --open-time-ms 1771009200000
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from typing import Any, Dict, List


def _fetch_bars(base_url: str, symbol: str, tf_s: int, limit: int, align: str) -> Dict[str, Any]:
    url = "{}/api/bars?symbol={}&tf_s={}&limit={}&align={}".format(
        base_url.rstrip("/"),
        urllib.request.quote(symbol, safe=""),
        tf_s,
        limit,
        urllib.request.quote(align, safe=""),
    )
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.load(resp)


def _find_bar(bars: List[Dict[str, Any]], open_ms: int) -> Dict[str, Any] | None:
    for b in bars:
        if b.get("open_time_ms") == open_ms:
            return b
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe derived H4 bucket by open_time_ms")
    parser.add_argument("--base-url", default="http://127.0.0.1:8089")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--tf-s", type=int, required=True)
    parser.add_argument("--align", default="tv", choices=["tv", "fxcm"])
    parser.add_argument("--open-time-ms", type=int, required=True)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    payload = _fetch_bars(args.base_url, args.symbol, args.tf_s, args.limit, args.align)
    bars = payload.get("bars", [])
    warnings = payload.get("warnings", [])
    meta = payload.get("meta", {})
    ext = meta.get("extensions") if isinstance(meta, dict) else {}

    bar = _find_bar(bars, args.open_time_ms)
    if bar is not None:
        reason = "found_full"
        if isinstance(bar, dict) and bar.get("complete") is False:
            reason = "emitted_partial_calendar_break"
            if isinstance(ext, dict) and ext.get("partial_reason") == "calendar_break_no_m5":
                reason = "emitted_partial_calendar_break_no_m5"
        print("FOUND complete=%s reason=%s" % (bar.get("complete"), reason))
        print("bar=", json.dumps(bar, ensure_ascii=False))
        return 0

    # Not found — reason from warnings/extensions
    if isinstance(warnings, list) and "derived_incomplete_bucket" in warnings:
        print("NOT_FOUND reason=dropped_incomplete")
        return 1
    if isinstance(warnings, list) and "derived_insufficient_h1" in warnings:
        print("NOT_FOUND reason=derived_insufficient_h1")
        return 1
    print("NOT_FOUND reason=unknown")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
