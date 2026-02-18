#!/usr/bin/env python3
"""
three_steps_proof — 3-кроковий доказовий звіт для H4/TV.

Кроки:
  1) API lookup з limit=api_limit (found/NOT_FOUND + proof window)
  2) tv_tooltip_compare (tol=0.02, PASS/FAIL)
  3) last_bucket_probe (reason + bar dump)

Виходи:
  reports/mpv_proof/three_steps_proof.md
  reports/mpv_proof/three_steps_proof.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from typing import Any, Dict, List

from tools.audit import tv_tooltip_compare as ttc


def _open_utc(open_ms: int | None) -> str | None:
    if open_ms is None:
        return None
    return dt.datetime.utcfromtimestamp(int(open_ms) / 1000).strftime("%Y-%m-%d %H:%M UTC")


def _window_proof(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    first_open_ms = None
    last_open_ms = None
    if bars:
        first_open_ms = bars[0].get("open_time_ms")
        last_open_ms = bars[-1].get("open_time_ms")

    first_sample: list[str] = []
    last_sample: list[str] = []
    for b in bars[:5]:
        first_sample.append(_open_utc(b.get("open_time_ms")) or "?")
    for b in bars[-5:]:
        last_sample.append(_open_utc(b.get("open_time_ms")) or "?")

    return {
        "window_first_open_ms": first_open_ms,
        "window_last_open_ms": last_open_ms,
        "window_first_open_utc": _open_utc(first_open_ms),
        "window_last_open_utc": _open_utc(last_open_ms),
        "window_first_open_utc_sample": first_sample,
        "window_last_open_utc_sample": last_sample,
    }


def _step1_lookup(
    *,
    api_base_url: str,
    symbol: str,
    tf_s: int,
    align: str,
    open_time_ms: int,
    api_limit: int,
) -> Dict[str, Any]:
    payload = ttc._fetch_bars(api_base_url, symbol, tf_s, api_limit, align)
    bars = payload.get("bars", [])
    bar = ttc._find_bar(bars, open_time_ms)
    result: Dict[str, Any] = {
        "step": "STEP1",
        "verdict": "FOUND" if bar is not None else "NOT_FOUND",
        "symbol": symbol,
        "tf_s": int(tf_s),
        "align": align,
        "open_time_ms": int(open_time_ms),
        "api_limit": int(api_limit),
    }

    if bar is not None:
        result.update({
            "bar": {
                "open": bar.get("open", bar.get("o")),
                "high": bar.get("high", bar.get("h")),
                "low": bar.get("low", bar.get("l")),
                "close": bar.get("close", bar.get("c")),
                "volume": bar.get("volume", bar.get("v")),
                "open_time_ms": bar.get("open_time_ms"),
                "close_time_ms": bar.get("close_time_ms"),
                "open_utc": _open_utc(bar.get("open_time_ms")),
                "close_utc": _open_utc(bar.get("close_time_ms")),
                "complete": bar.get("complete"),
                "src": bar.get("src"),
            }
        })
    else:
        result.update(_window_proof(bars))

    return {"payload": payload, "result": result}


def _step2_tooltip_compare(
    *,
    payload: Dict[str, Any],
    symbol: str,
    tf_s: int,
    align: str,
    open_time_ms: int,
    api_limit: int,
    tv_open: float,
    tv_high: float,
    tv_low: float,
    tv_close: float,
    tol: float,
) -> Dict[str, Any]:
    return ttc.compare_tooltip_payload(
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
        dump_window_on_miss=True,
    )


def _step3_last_bucket_probe(
    *,
    api_base_url: str,
    symbol: str,
    tf_s: int,
    align: str,
    open_time_ms: int,
    api_limit: int,
) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        "-m",
        "tools.audit.last_bucket_probe",
        "--base-url",
        api_base_url,
        "--symbol",
        symbol,
        "--tf-s",
        str(tf_s),
        "--align",
        align,
        "--open-time-ms",
        str(open_time_ms),
        "--limit",
        str(api_limit),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "step": "STEP3",
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def _write_report(report: Dict[str, Any]) -> tuple[str, str]:
    os.makedirs("reports/mpv_proof", exist_ok=True)
    ts = report.get("ts_utc")
    md_path = os.path.join("reports", "mpv_proof", "three_steps_proof.md")
    json_path = os.path.join("reports", "mpv_proof", "three_steps_proof.json")

    step1 = report.get("step1", {})
    step2 = report.get("step2", {})
    step3 = report.get("step3", {})

    lines = [
        "# Three Steps Proof Report",
        "",
        f"Дата: {ts}",
        "",
        "## STEP1 — API lookup",
        "",
        f"VERDICT: **{step1.get('verdict')}**",
        f"symbol={step1.get('symbol')} tf_s={step1.get('tf_s')} align={step1.get('align')} open_time_ms={step1.get('open_time_ms')}",
        f"api_limit={step1.get('api_limit')}",
        "",
    ]

    if step1.get("verdict") == "FOUND":
        bar = step1.get("bar", {})
        lines.extend([
            "bar:",
            f"  open_utc={bar.get('open_utc')} close_utc={bar.get('close_utc')}",
            f"  O={bar.get('open')} H={bar.get('high')} L={bar.get('low')} C={bar.get('close')} V={bar.get('volume')}",
            f"  complete={bar.get('complete')} src={bar.get('src')}",
            "",
        ])
    else:
        lines.extend([
            "window proof:",
            f"  first_open_ms={step1.get('window_first_open_ms')} ({step1.get('window_first_open_utc')})",
            f"  last_open_ms={step1.get('window_last_open_ms')} ({step1.get('window_last_open_utc')})",
            "  samples:",
        ])
        for item in step1.get("window_first_open_utc_sample", []) or []:
            lines.append(f"    - first: {item}")
        for item in step1.get("window_last_open_utc_sample", []) or []:
            lines.append(f"    - last: {item}")
        lines.append("")

    lines.extend([
        "## STEP2 — TV tooltip compare",
        "",
        f"VERDICT: **{step2.get('verdict')}**",
        f"reason={step2.get('verdict_reason')} tol={step2.get('tol')}",
        "",
        f"TV:  O={step2.get('tv', {}).get('o')} H={step2.get('tv', {}).get('h')} L={step2.get('tv', {}).get('l')} C={step2.get('tv', {}).get('c')}",
        f"API: O={step2.get('api', {}).get('o')} H={step2.get('api', {}).get('h')} L={step2.get('api', {}).get('l')} C={step2.get('api', {}).get('c')}",
        f"Δ:   O={step2.get('delta', {}).get('o')} H={step2.get('delta', {}).get('h')} L={step2.get('delta', {}).get('l')} C={step2.get('delta', {}).get('c')}",
        "",
        "## STEP3 — last_bucket_probe",
        "",
        f"returncode={step3.get('returncode')}",
        "stdout:",
        step3.get("stdout", "(empty)"),
        "",
    ])
    if step3.get("stderr"):
        lines.extend(["stderr:", step3.get("stderr"), ""])

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    return md_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Three steps proof runner")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8089")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--tf-s", type=int, required=True)
    parser.add_argument("--align", default="tv", choices=["tv", "fxcm"])
    parser.add_argument("--open-time-ms", type=int, required=True)
    parser.add_argument("--api-limit", type=int, default=600)
    parser.add_argument("--tv-open", type=float, required=True)
    parser.add_argument("--tv-high", type=float, required=True)
    parser.add_argument("--tv-low", type=float, required=True)
    parser.add_argument("--tv-close", type=float, required=True)
    parser.add_argument("--float-tol", type=float, default=0.02)
    args = parser.parse_args()

    step1 = _step1_lookup(
        api_base_url=args.api_base_url,
        symbol=args.symbol,
        tf_s=args.tf_s,
        align=args.align,
        open_time_ms=args.open_time_ms,
        api_limit=args.api_limit,
    )

    step2 = _step2_tooltip_compare(
        payload=step1["payload"],
        symbol=args.symbol,
        tf_s=args.tf_s,
        align=args.align,
        open_time_ms=args.open_time_ms,
        api_limit=args.api_limit,
        tv_open=args.tv_open,
        tv_high=args.tv_high,
        tv_low=args.tv_low,
        tv_close=args.tv_close,
        tol=args.float_tol,
    )

    step3 = _step3_last_bucket_probe(
        api_base_url=args.api_base_url,
        symbol=args.symbol,
        tf_s=args.tf_s,
        align=args.align,
        open_time_ms=args.open_time_ms,
        api_limit=args.api_limit,
    )

    report = {
        "ts_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inputs": {
            "symbol": args.symbol,
            "tf_s": int(args.tf_s),
            "align": args.align,
            "open_time_ms": int(args.open_time_ms),
            "api_limit": int(args.api_limit),
            "api_base_url": args.api_base_url,
            "tv": {
                "open": float(args.tv_open),
                "high": float(args.tv_high),
                "low": float(args.tv_low),
                "close": float(args.tv_close),
            },
            "tol": float(args.float_tol),
        },
        "step1": step1["result"],
        "step2": step2,
        "step3": step3,
    }

    md_path, json_path = _write_report(report)
    print("Report: {} + {}".format(md_path, json_path))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
