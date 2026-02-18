#!/usr/bin/env python3
"""
anchor_compare_api — порівняння H4 anchor remainder для align=fxcm vs align=tv.

Запускає /api/bars для обох align і друкує:
- перші N open_utc
- гістограму remainder_ms (open_time_ms % tf_ms)

Приклад:
  python -m tools.audit.anchor_compare_api --symbol XAU/USD --tf-s 14400 --limit 20
"""

from __future__ import annotations

import argparse
import datetime as dt
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


def _open_utc(open_ms: int) -> str:
    return dt.datetime.utcfromtimestamp(int(open_ms) / 1000).strftime("%Y-%m-%d %H:%M UTC")


def _remainder_hist(bars: List[Dict[str, Any]], tf_ms: int) -> Dict[int, int]:
    hist: Dict[int, int] = {}
    for b in bars:
        open_ms = b.get("open_time_ms")
        if not isinstance(open_ms, int):
            continue
        rem = int(open_ms % tf_ms)
        hist[rem] = hist.get(rem, 0) + 1
    return hist


def _print_report(align: str, bars: List[Dict[str, Any]], tf_ms: int, n: int) -> None:
    print("\n== align={} ==".format(align))
    if not bars:
        print("  (немає барів)")
        return
    print("  Перші {} open_utc:".format(min(n, len(bars))))
    for b in bars[:n]:
        open_ms = b.get("open_time_ms")
        if not isinstance(open_ms, int):
            continue
        print("   - {}".format(_open_utc(open_ms)))
    hist = _remainder_hist(bars, tf_ms)
    print("  Remainder histogram (ms):")
    for rem in sorted(hist.keys()):
        print("   {} => {}".format(rem, hist[rem]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare H4 anchor remainder for align=fxcm vs align=tv")
    parser.add_argument("--base-url", default="http://127.0.0.1:8089", help="Base URL of API")
    parser.add_argument("--symbol", required=True, help="Symbol (e.g. XAU/USD)")
    parser.add_argument("--tf-s", type=int, default=14400, help="TF in seconds (default 14400)")
    parser.add_argument("--limit", type=int, default=20, help="Number of bars to fetch")
    args = parser.parse_args()

    tf_ms = int(args.tf_s) * 1000

    for align in ("fxcm", "tv"):
        payload = _fetch_bars(args.base_url, args.symbol, args.tf_s, args.limit, align)
        bars = payload.get("bars", [])
        _print_report(align, bars, tf_ms, n=10)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
