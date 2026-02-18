"""tools/audit/htf_tail_watch.py

Доказовий watch-інструмент: збирає факти «які (symbol, tf) stale» і генерує звіт.

Для кожного symbol×tf:
  GET /api/bars?symbol=...&tf_s=...&align=fxcm&limit=<limit>
  → зняти last_open_ms / last_close_ms / event_ts / src / complete
  → age_s = now_ms − last_close_ms
  → stale = (age_s > tf_s × 2)

Output: reports/mpv_proof/htf_tail_watch.json + .md

CLI:
  python -m tools.audit.htf_tail_watch \\
      --api-base-url http://127.0.0.1:8089 \\
      --symbols-from-config \\
      --tfs 14400,86400 \\
      --limit 5
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
import urllib.request
from typing import Any, Dict, List, Optional


# ═══════ API helper ═══════


def _api_fetch_bars(
    base_url: str, symbol: str, tf_s: int, limit: int,
) -> Dict[str, Any]:
    """GET /api/bars → parsed JSON (або error dict)."""
    url = "%s/api/bars?symbol=%s&tf_s=%d&align=fxcm&limit=%d" % (
        base_url.rstrip("/"),
        urllib.request.quote(symbol, safe=""),
        tf_s,
        limit,
    )
    try:
        resp_raw = urllib.request.urlopen(url, timeout=15).read()
        return json.loads(resp_raw)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "bars": []}


# ═══════ Tail probe ═══════


def probe_tail(
    base_url: str,
    symbol: str,
    tf_s: int,
    limit: int,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Повертає dict-факт про хвіст (symbol, tf_s).

    Поля: symbol, tf_s, limit, last_open_ms, last_close_ms, event_ts,
          src, complete, age_s, stale, bar_count, error.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    entry: Dict[str, Any] = {
        "symbol": symbol,
        "tf_s": tf_s,
        "limit": limit,
        "now_ms": now_ms,
        "now_utc": dt.datetime.utcfromtimestamp(now_ms / 1000).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }

    data = _api_fetch_bars(base_url, symbol, tf_s, limit)
    bars = data.get("bars") or []
    entry["bar_count"] = len(bars)

    if data.get("error"):
        entry["error"] = data["error"]
        entry["stale"] = True
        return entry

    if not bars:
        entry["error"] = "no_bars_returned"
        entry["stale"] = True
        return entry

    last = bars[-1]
    last_open_ms = last.get("open_time_ms")
    if last_open_ms is None and isinstance(last.get("time"), int):
        last_open_ms = int(last["time"]) * 1000
    last_close_ms = last.get("close_time_ms")
    if last_close_ms is None and isinstance(last_open_ms, int):
        last_close_ms = int(last_open_ms) + tf_s * 1000

    entry["last_open_ms"] = last_open_ms
    entry["last_close_ms"] = last_close_ms
    entry["last_open_utc"] = (
        dt.datetime.utcfromtimestamp(last_open_ms / 1000).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        if isinstance(last_open_ms, int)
        else None
    )
    entry["last_close_utc"] = (
        dt.datetime.utcfromtimestamp(last_close_ms / 1000).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        if isinstance(last_close_ms, int)
        else None
    )
    entry["event_ts"] = last.get("event_ts")
    entry["src"] = last.get("src", "")
    entry["complete"] = last.get("complete")

    if isinstance(last_close_ms, int):
        entry["age_s"] = int((now_ms - last_close_ms) / 1000)
        entry["stale"] = entry["age_s"] > (tf_s * 2)
    else:
        entry["age_s"] = None
        entry["stale"] = True

    return entry


# ═══════ Report writers ═══════


def _write_json_report(results: List[Dict[str, Any]], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    report = {
        "tool": "htf_tail_watch",
        "ts_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entries": results,
        "summary": {
            "total": len(results),
            "stale": sum(1 for e in results if e.get("stale")),
            "fresh": sum(1 for e in results if not e.get("stale")),
            "errors": sum(1 for e in results if e.get("error")),
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)


def _write_md_report(results: List[Dict[str, Any]], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lines = [
        "# HTF Tail Watch Report",
        "",
        "Дата: %s" % dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "",
        "| Symbol | TF | last_open_utc | last_close_utc | age_s | stale | source | complete | error |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for e in results:
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %s | %s | %s |"
            % (
                e.get("symbol", ""),
                e.get("tf_s", ""),
                e.get("last_open_utc", "—"),
                e.get("last_close_utc", "—"),
                e.get("age_s", "—"),
                "**YES**" if e.get("stale") else "no",
                e.get("src", ""),
                e.get("complete", ""),
                e.get("error", ""),
            )
        )
    stale_count = sum(1 for e in results if e.get("stale"))
    lines.append("")
    lines.append("**Stale: %d / %d**" % (stale_count, len(results)))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ═══════ CLI ═══════


def main() -> int:
    parser = argparse.ArgumentParser(
        description="HTF Tail Watch — доказовий звіт staleness H4/D1."
    )
    parser.add_argument(
        "--api-base-url", default="http://127.0.0.1:8089",
    )
    parser.add_argument(
        "--symbols", help="Список символів через кому (XAU/USD,NAS100,...)",
    )
    parser.add_argument(
        "--symbols-from-config",
        action="store_true",
        help="Взяти символи з config.json",
    )
    parser.add_argument(
        "--tfs",
        default="14400,86400",
        help="TF через кому (default: 14400,86400)",
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument(
        "--out-dir",
        default="reports/mpv_proof",
        help="Каталог для звітів (default: reports/mpv_proof)",
    )
    args = parser.parse_args()

    # Symbols
    sym_list: List[str] = []
    if args.symbols_from_config:
        from core.config_loader import load_system_config

        cfg = load_system_config()
        sym_list = list(cfg.get("symbols") or [])
    elif args.symbols:
        sym_list = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not sym_list:
        print("ERROR: порожній список символів")
        return 2

    tfs = [int(t.strip()) for t in args.tfs.split(",") if t.strip()]
    if not tfs:
        print("ERROR: порожній список TF")
        return 2

    now_ms = int(time.time() * 1000)
    results: List[Dict[str, Any]] = []

    for symbol in sym_list:
        for tf_s in tfs:
            entry = probe_tail(args.api_base_url, symbol, tf_s, args.limit, now_ms)
            results.append(entry)
            stale_mark = " [STALE]" if entry.get("stale") else ""
            print(
                "%s tf=%d age=%ss%s"
                % (symbol, tf_s, entry.get("age_s", "?"), stale_mark)
            )

    json_path = os.path.join(args.out_dir, "htf_tail_watch.json")
    md_path = os.path.join(args.out_dir, "htf_tail_watch.md")
    _write_json_report(results, json_path)
    _write_md_report(results, md_path)
    print("Report: %s + %s" % (json_path, md_path))

    stale_count = sum(1 for e in results if e.get("stale"))
    if stale_count:
        print("WARN: %d / %d stale entries" % (stale_count, len(results)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
