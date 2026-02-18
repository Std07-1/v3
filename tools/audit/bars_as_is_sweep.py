"""tools/audit/bars_as_is_sweep.py

Read-only прогін /api/bars по всіх symbols × tfs → JSON + MD репорт.
НЕ пише в SSOT/Redis, тільки читає через API.

CLI:
  python -m tools.audit.bars_as_is_sweep \\
      --symbols-from-config \\
      --tfs 60,180,300,900,1800,3600,14400,86400 \\
      --limit 600

  python -m tools.audit.bars_as_is_sweep \\
      --symbols XAU/USD,SPX500 --tfs 60,14400 --limit 300

Для tf=14400 додатково знімає align=fxcm та align=tv (окремі секції).

Output: reports/mpv_proof/bars_as_is_sweep.json + .md
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("bars_as_is_sweep")


# ─── Fetch helper ────────────────────────────────────────────


def _fetch_bars(
    api_base: str,
    symbol: str,
    tf_s: int,
    limit: int,
    align: Optional[str] = None,
) -> Dict[str, Any]:
    """HTTP GET /api/bars → parsed JSON."""
    params = "symbol=%s&tf_s=%d&limit=%d" % (
        urllib.request.quote(symbol, safe=""),
        tf_s,
        limit,
    )
    if align:
        params += "&align=%s" % align
    url = "%s/api/bars?%s" % (api_base, params)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
        return data
    except Exception as exc:
        return {"ok": False, "error": str(exc), "bars": []}


# ─── Analysis per (symbol, tf, align) ────────────────────────


def _analyze(
    bars: List[Dict[str, Any]],
    symbol: str,
    tf_s: int,
    now_ms: int,
    align: Optional[str],
    meta: Optional[Dict[str, Any]],
    warnings: Optional[List[str]],
) -> Dict[str, Any]:
    """Аналіз одного набору барів."""
    result: Dict[str, Any] = {
        "symbol": symbol,
        "tf_s": tf_s,
        "align": align or "fxcm",
        "count": len(bars),
    }

    if not bars:
        result["status"] = "empty"
        return result

    # first/last
    opens = [b.get("open_time_ms", 0) for b in bars]
    first_ms = opens[0] if opens else 0
    last_ms = opens[-1] if opens else 0
    result["first_open_ms"] = first_ms
    result["last_open_ms"] = last_ms
    result["first_open_utc"] = dt.datetime.utcfromtimestamp(
        first_ms / 1000
    ).strftime("%Y-%m-%dT%H:%M:%SZ") if first_ms else None
    result["last_open_utc"] = dt.datetime.utcfromtimestamp(
        last_ms / 1000
    ).strftime("%Y-%m-%dT%H:%M:%SZ") if last_ms else None

    # monotonic
    is_mono = True
    for i in range(1, len(opens)):
        if opens[i] <= opens[i - 1]:
            is_mono = False
            break
    result["monotonic"] = is_mono

    # dup
    dup_count = len(opens) - len(set(opens))
    result["dup_open_ms_count"] = dup_count

    # future close
    future_count = 0
    for b in bars:
        close_ms = b.get("close_time_ms", 0)
        if close_ms > now_ms:
            future_count += 1
    result["future_close_count"] = future_count

    # flat bars (high == low)
    flat_count = 0
    for b in bars:
        h = b.get("high", b.get("h"))
        lo = b.get("low")
        if h is not None and lo is not None and h == lo:
            flat_count += 1
    result["flat_bars_count"] = flat_count

    # source + complete ratio
    sources: Dict[str, int] = {}
    complete_true = 0
    complete_false = 0
    for b in bars:
        src = b.get("src", "?")
        sources[src] = sources.get(src, 0) + 1
        if b.get("complete"):
            complete_true += 1
        else:
            complete_false += 1
    result["sources"] = sources
    result["complete_true"] = complete_true
    result["complete_false"] = complete_false

    # age
    last_close_ms = bars[-1].get("close_time_ms", 0) if bars else 0
    if last_close_ms > 0:
        age_s = int((now_ms - last_close_ms) / 1000)
        result["age_s"] = age_s
        result["last_close_utc"] = dt.datetime.utcfromtimestamp(
            last_close_ms / 1000
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        result["age_s"] = None

    # meta
    if meta:
        result["meta_source"] = meta.get("source")
        ext = meta.get("extensions", {})
        if ext:
            result["meta_extensions"] = ext
    if warnings:
        result["warnings"] = list(warnings)

    result["status"] = "ok"
    return result


# ─── Write reports ────────────────────────────────────────────


def _write_json(data: Any, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _write_md(report: Dict[str, Any], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lines: List[str] = [
        "# bars_as_is_sweep — %s" % report.get("ts_utc", "?"),
        "",
        "Symbols: %d, TFs: %s, Limit: %s"
        % (
            len(report.get("symbols", [])),
            report.get("tfs", []),
            report.get("limit", "?"),
        ),
        "",
        "## Результати",
        "",
        "| Symbol | TF | Align | Count | Mono | Dups | Flat | Future | Age(s) | Source | Status |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for e in report.get("entries", []):
        lines.append(
            "| %s | %s | %s | %d | %s | %d | %d | %d | %s | %s | %s |"
            % (
                e.get("symbol", ""),
                e.get("tf_s", ""),
                e.get("align", "fxcm"),
                e.get("count", 0),
                "YES" if e.get("monotonic") else "NO",
                e.get("dup_open_ms_count", 0),
                e.get("flat_bars_count", 0),
                e.get("future_close_count", 0),
                e.get("age_s", "—"),
                ",".join(e.get("sources", {}).keys()),
                e.get("status", "?"),
            )
        )
    lines.append("")

    # Totals
    entries = report.get("entries", [])
    total = len(entries)
    ok_count = sum(1 for e in entries if e.get("status") == "ok")
    empty_count = sum(1 for e in entries if e.get("status") == "empty")
    mono_fail = sum(1 for e in entries if not e.get("monotonic", True))
    dup_total = sum(e.get("dup_open_ms_count", 0) for e in entries)
    flat_total = sum(e.get("flat_bars_count", 0) for e in entries)

    lines.append("## Підсумок")
    lines.append("")
    lines.append("- Total entries: %d" % total)
    lines.append("- OK: %d" % ok_count)
    lines.append("- Empty: %d" % empty_count)
    lines.append("- Monotonic violations: %d" % mono_fail)
    lines.append("- Duplicate open_ms total: %d" % dup_total)
    lines.append("- Flat bars total: %d" % flat_total)
    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ─── Main ─────────────────────────────────────────────────────


def run_sweep(
    symbols: List[str],
    tfs: List[int],
    limit: int,
    api_base: str,
) -> Dict[str, Any]:
    """Прогін sweep. Повертає повний report dict."""
    now_ms = int(time.time() * 1000)
    report: Dict[str, Any] = {
        "tool": "bars_as_is_sweep",
        "ts_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbols": list(symbols),
        "tfs": list(tfs),
        "limit": limit,
        "api_base": api_base,
        "entries": [],
    }

    for symbol in symbols:
        for tf_s in tfs:
            aligns = ["fxcm"]
            # H4: також align=tv для порівняння
            if tf_s == 14400:
                aligns.append("tv")

            for align_val in aligns:
                align_param = None if align_val == "fxcm" else align_val
                LOG.info("── %s tf=%d align=%s ──", symbol, tf_s, align_val)
                data = _fetch_bars(api_base, symbol, tf_s, limit, align=align_param)
                bars = data.get("bars", data.get("candles", []))
                meta = data.get("meta")
                warnings = data.get("warnings")
                entry = _analyze(
                    bars, symbol, tf_s, now_ms,
                    align=align_val, meta=meta, warnings=warnings,
                )
                if not data.get("ok", True):
                    entry["status"] = "api_error"
                    entry["api_error"] = data.get("error", "unknown")
                report["entries"].append(entry)

    return report


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    ap = argparse.ArgumentParser(
        description="bars_as_is_sweep: read-only audit /api/bars по всіх symbols × tfs.",
    )
    ap.add_argument("--symbols", default=None, help="Символи через кому")
    ap.add_argument(
        "--symbols-from-config",
        action="store_true",
        help="Символи з config.json",
    )
    ap.add_argument(
        "--tfs",
        default="60,180,300,900,1800,3600,14400,86400",
        help="TF через кому",
    )
    ap.add_argument("--limit", type=int, default=600, help="Кількість барів")
    ap.add_argument(
        "--api-base-url",
        default="http://127.0.0.1:8089",
        help="API base URL",
    )
    ap.add_argument(
        "--out",
        default="reports/mpv_proof/bars_as_is_sweep.json",
        help="Шлях JSON-звіту",
    )
    args = ap.parse_args()

    # Символи
    if args.symbols_from_config:
        from core.config_loader import pick_config_path, load_system_config

        config_path = pick_config_path()
        cfg = load_system_config(config_path)
        sym_list = [str(s) for s in cfg.get("symbols", []) if str(s).strip()]
    elif args.symbols:
        sym_list = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        LOG.error("Вкажіть --symbols або --symbols-from-config")
        return 2

    if not sym_list:
        LOG.error("Порожній список символів")
        return 2

    tfs = [int(t.strip()) for t in args.tfs.split(",") if t.strip()]
    if not tfs:
        LOG.error("Порожній список TF")
        return 2

    LOG.info("=== bars_as_is_sweep === symbols=%d tfs=%s limit=%d", len(sym_list), tfs, args.limit)

    report = run_sweep(sym_list, tfs, args.limit, args.api_base_url)

    # Write
    json_path = args.out
    md_path = json_path.replace(".json", ".md")
    _write_json(report, json_path)
    _write_md(report, md_path)

    entries = report.get("entries", [])
    ok_count = sum(1 for e in entries if e.get("status") == "ok")
    empty_count = sum(1 for e in entries if e.get("status") == "empty")

    LOG.info(
        "=== ПІДСУМОК: entries=%d ok=%d empty=%d ===",
        len(entries),
        ok_count,
        empty_count,
    )
    LOG.info("Report: %s + %s", json_path, md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
