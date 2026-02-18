#!/usr/bin/env python3
"""
tv_tooltip_compare — порівняння TV tooltip OHLC з /api/bars по open_time_ms.

Приклад:
  python -m tools.audit.tv_tooltip_compare \
    --symbol XAU/USD --tf-s 14400 --align tv \
    --open-time-ms 1770966000000 \
    --tv-open 5038.52 --tv-high 5046.11 --tv-low 5015.08 --tv-close 5038.52
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
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


def _open_utc(open_ms: int | None) -> str | None:
    if open_ms is None:
        return None
    return dt.datetime.utcfromtimestamp(int(open_ms) / 1000).strftime("%Y-%m-%d %H:%M UTC")


def _float_eq(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def _write_report(payload: Dict[str, Any]) -> tuple[str, str]:
    os.makedirs("reports/mpv_proof", exist_ok=True)
    ts = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    md_path = os.path.join("reports", "mpv_proof", "tv_tooltip_compare.md")
    json_path = os.path.join("reports", "mpv_proof", "tv_tooltip_compare.json")

    lines = [
        "# TV Tooltip Compare Report",
        "",
        "| Поле | Значення |",
        "|---|---|",
        f"| Дата | {ts} |",
        f"| Символ | {payload['symbol']} |",
        f"| TF | {payload['tf_s']}s |",
        f"| Align | {payload['align']} |",
        f"| Open time ms | {payload['open_time_ms']} |",
        f"| Tol | {payload.get('tol')} |",
        f"| Verdict | **{payload['verdict']}** |",
        f"| Verdict reason | {payload.get('verdict_reason')} |",
        f"| Searched limit | {payload.get('searched_limit')} |",
        "",
        "## Деталі",
        "",
    ]

    if payload.get("verdict_reason") == "bar_not_found":
        lines.extend([
            "Bar не знайдено у вікні.",
            f"first_open_ms={payload.get('window_first_open_ms')} ({payload.get('window_first_open_utc')})",
            f"last_open_ms={payload.get('window_last_open_ms')} ({payload.get('window_last_open_utc')})",
            "",
        ])
        if payload.get("window_first_open_utc_sample") or payload.get("window_last_open_utc_sample"):
            lines.append("Window proof (UTC samples):")
            for item in payload.get("window_first_open_utc_sample", []):
                lines.append(f"  - first: {item}")
            for item in payload.get("window_last_open_utc_sample", []):
                lines.append(f"  - last: {item}")
            lines.append("")
    else:
        lines.extend([
            f"TV:  O={payload['tv']['o']} H={payload['tv']['h']} L={payload['tv']['l']} C={payload['tv']['c']}",
            f"API: O={payload['api']['o']} H={payload['api']['h']} L={payload['api']['l']} C={payload['api']['c']}",
            f"Δ:   O={payload['delta']['o']} H={payload['delta']['h']} L={payload['delta']['l']} C={payload['delta']['c']}",
            "",
        ])
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return md_path, json_path


def compare_tooltip_payload(
    payload: Dict[str, Any],
    *,
    symbol: str,
    tf_s: int,
    align: str,
    open_time_ms: int,
    tv_open: float,
    tv_high: float,
    tv_low: float,
    tv_close: float,
    tol: float,
    searched_limit: int,
    dump_window_on_miss: bool,
) -> Dict[str, Any]:
    bars = payload.get("bars", [])
    bar = _find_bar(bars, open_time_ms)
    if bar is None:
        first_open_ms = None
        last_open_ms = None
        first_open_utc = None
        last_open_utc = None
        if bars:
            first_open_ms = bars[0].get("open_time_ms")
            last_open_ms = bars[-1].get("open_time_ms")
            first_open_utc = _open_utc(first_open_ms)
            last_open_utc = _open_utc(last_open_ms)

        first_sample: list[str] = []
        last_sample: list[str] = []
        if dump_window_on_miss and bars:
            for b in bars[:5]:
                first_sample.append(_open_utc(b.get("open_time_ms")) or "?")
            for b in bars[-5:]:
                last_sample.append(_open_utc(b.get("open_time_ms")) or "?")

        return {
            "symbol": symbol,
            "tf_s": int(tf_s),
            "align": align,
            "open_time_ms": int(open_time_ms),
            "tol": float(tol),
            "searched_limit": int(searched_limit),
            "verdict": "FAIL",
            "verdict_reason": "bar_not_found",
            "bar_found": False,
            "window_first_open_ms": first_open_ms,
            "window_last_open_ms": last_open_ms,
            "window_first_open_utc": first_open_utc,
            "window_last_open_utc": last_open_utc,
            "window_first_open_utc_sample": first_sample,
            "window_last_open_utc_sample": last_sample,
            "tv": {"o": tv_open, "h": tv_high, "l": tv_low, "c": tv_close},
            "api": {"o": None, "h": None, "l": None, "c": None},
            "delta": {"o": None, "h": None, "l": None, "c": None},
        }

    api_o = float(bar.get("open", bar.get("o", 0.0)))
    api_h = float(bar.get("high", bar.get("h", 0.0)))
    api_l = float(bar.get("low", bar.get("l", 0.0)))
    api_c = float(bar.get("close", bar.get("c", 0.0)))

    ok = (
        _float_eq(tv_open, api_o, tol)
        and _float_eq(tv_high, api_h, tol)
        and _float_eq(tv_low, api_l, tol)
        and _float_eq(tv_close, api_c, tol)
    )
    verdict_reason = "match_within_tol" if ok else "delta_exceeds_tol"

    return {
        "symbol": symbol,
        "tf_s": int(tf_s),
        "align": align,
        "open_time_ms": int(open_time_ms),
        "tol": float(tol),
        "searched_limit": int(searched_limit),
        "verdict": "PASS" if ok else "FAIL",
        "verdict_reason": verdict_reason,
        "bar_found": True,
        "tv": {"o": tv_open, "h": tv_high, "l": tv_low, "c": tv_close},
        "api": {"o": api_o, "h": api_h, "l": api_l, "c": api_c},
        "delta": {
            "o": round(tv_open - api_o, 6),
            "h": round(tv_high - api_h, 6),
            "l": round(tv_low - api_l, 6),
            "c": round(tv_close - api_c, 6),
        },
    }


def compare_tooltip(
    *,
    api_base_url: str,
    symbol: str,
    tf_s: int,
    align: str,
    open_time_ms: int,
    tv_open: float,
    tv_high: float,
    tv_low: float,
    tv_close: float,
    api_limit: int,
    tol: float,
    dump_window_on_miss: bool,
) -> Dict[str, Any]:
    payload = _fetch_bars(api_base_url, symbol, tf_s, api_limit, align)
    return compare_tooltip_payload(
        payload,
        symbol=symbol,
        tf_s=tf_s,
        align=align,
        open_time_ms=open_time_ms,
        tv_open=tv_open,
        tv_high=tv_high,
        tv_low=tv_low,
        tv_close=tv_close,
        tol=tol,
        searched_limit=api_limit,
        dump_window_on_miss=dump_window_on_miss,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare TV tooltip OHLC vs /api/bars")
    parser.add_argument(
        "--api-base-url",
        dest="api_base_url",
        default="http://127.0.0.1:8089",
        help="API Base URL",
    )
    parser.add_argument(
        "--base-url",
        dest="api_base_url",
        default=None,
        help="Alias for --api-base-url",
    )
    parser.add_argument("--symbol", required=True, help="Symbol")
    parser.add_argument("--tf-s", type=int, required=True, help="TF in seconds")
    parser.add_argument("--align", default="tv", choices=["tv", "fxcm"], help="align mode")
    parser.add_argument("--api-limit", dest="api_limit", type=int, default=600, help="bars limit")
    parser.add_argument("--limit", dest="api_limit", type=int, default=None, help="Alias for --api-limit")
    parser.add_argument("--open-time-ms", type=int, required=True, help="open_time_ms from TV tooltip")
    parser.add_argument("--tv-open", type=float, required=True)
    parser.add_argument("--tv-high", type=float, required=True)
    parser.add_argument("--tv-low", type=float, required=True)
    parser.add_argument("--tv-close", type=float, required=True)
    parser.add_argument("--float-tol", type=float, default=0.02, help="float tolerance")
    parser.add_argument(
        "--dump-window-on-miss",
        dest="dump_window_on_miss",
        action="store_true",
        help="write first/last window samples when bar not found",
    )
    parser.add_argument(
        "--no-dump-window-on-miss",
        dest="dump_window_on_miss",
        action="store_false",
        help="disable window proof samples",
    )
    parser.set_defaults(dump_window_on_miss=True)
    args = parser.parse_args()

    api_base_url = args.api_base_url or "http://127.0.0.1:8089"
    api_limit = int(args.api_limit) if args.api_limit is not None else 600

    result = compare_tooltip(
        api_base_url=api_base_url,
        symbol=args.symbol,
        tf_s=int(args.tf_s),
        align=args.align,
        open_time_ms=int(args.open_time_ms),
        tv_open=float(args.tv_open),
        tv_high=float(args.tv_high),
        tv_low=float(args.tv_low),
        tv_close=float(args.tv_close),
        api_limit=api_limit,
        tol=float(args.float_tol),
        dump_window_on_miss=bool(args.dump_window_on_miss),
    )

    md_path, json_path = _write_report(result)
    print("Report: {} + {}".format(md_path, json_path))
    print("VERDICT: {}".format(result["verdict"]))
    return 0 if result.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
