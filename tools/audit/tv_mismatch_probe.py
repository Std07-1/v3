#!/usr/bin/env python3
"""
tv_mismatch_probe — детерміновна діагностика розбіжностей UI vs TradingView.

Читає /api/bars (API нашого сервера), друкує останні N барів у таблиці
(open_utc, O, H, L, C, bullish), показує meta.extensions.instrument.
Опційно порівнює з FXCM raw (FxcmHistoryProvider).

Дозволяє довести/спростувати:
- чи API бачить правильний символ/інструмент
- чи OHLC адекватні для цього інструменту
- чи є розбіжності між API і FXCM raw

Використання:
  python -m tools.audit.tv_mismatch_probe --symbol XAU/USD --tf 14400 --limit 20
  python -m tools.audit.tv_mismatch_probe --symbol XAU/USD --tf 14400 --limit 20 --compare-fxcm

Scope: tools/audit (read-only діагностика, не змінює дані).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import urllib.request
from typing import Any, Dict, List, Optional


def _fetch_api_bars(
    api_base: str,
    symbol: str,
    tf_s: int,
    limit: int,
) -> Dict[str, Any]:
    """Витягує бари через /api/bars."""
    url = "{}/api/bars?symbol={}&tf_s={}&limit={}".format(
        api_base.rstrip("/"),
        urllib.request.quote(symbol, safe=""),
        tf_s,
        limit,
    )
    logging.info("API fetch: %s", url)
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.load(resp)


def _bars_table(bars: List[Dict[str, Any]], tail_n: int = 10) -> str:
    """Форматує останні tail_n барів у readable таблицю."""
    if not bars:
        return "  (пусто)"
    show = bars[-tail_n:]
    lines = []
    header = "{:<22s} {:>10s} {:>10s} {:>10s} {:>10s} {:>7s}".format(
        "open_utc", "Open", "High", "Low", "Close", "bull?",
    )
    lines.append(header)
    lines.append("-" * len(header))
    for b in show:
        open_ms = b.get("open_time_ms")
        if open_ms is None and isinstance(b.get("time"), (int, float)):
            open_ms = int(b["time"]) * 1000
        if open_ms is not None:
            utc_str = dt.datetime.utcfromtimestamp(int(open_ms) / 1000).strftime("%Y-%m-%d %H:%M UTC")
        else:
            utc_str = "???"
        o = b.get("open", b.get("o"))
        h = b.get("high", b.get("h"))
        lo = b.get("low")
        c = b.get("close", b.get("c"))
        if o is not None and c is not None:
            bull = "YES" if float(c) >= float(o) else "no"
        else:
            bull = "?"
        lines.append("{:<22s} {:>10.4f} {:>10.4f} {:>10.4f} {:>10.4f} {:>7s}".format(
            utc_str,
            float(o) if o is not None else 0,
            float(h) if h is not None else 0,
            float(lo) if lo is not None else 0,
            float(c) if c is not None else 0,
            bull,
        ))
    return "\n".join(lines)


def _price_sanity_check(symbol: str, bars: List[Dict[str, Any]]) -> List[str]:
    """Перевірка адекватності ціни для символу (евристика)."""
    issues = []
    if not bars:
        issues.append("NO_BARS")
        return issues
    # Визначаємо очікуваний діапазон ціни по символу
    price_ranges = {
        "XAU/USD": (500, 10000),     # Золото: $500-$10000
        "XAG/USD": (5, 200),         # Срібло: $5-$200
        "NAS100": (1000, 30000),     # Nasdaq: 1000-30000
        "SPX500": (1000, 10000),     # S&P: 1000-10000
        "US30": (10000, 60000),      # Dow: 10000-60000
        "GER30": (5000, 25000),      # DAX: 5000-25000
        "NGAS": (0.5, 20),           # Природний газ: $0.5-$20
    }
    expected = price_ranges.get(symbol)
    if expected is None:
        return issues  # Ніякої перевірки для невідомих символів
    lo, hi = expected
    last = bars[-1]
    c = last.get("close", last.get("c"))
    if c is not None:
        c = float(c)
        if c < lo or c > hi:
            issues.append(
                "PRICE_OUT_OF_RANGE: {} close={:.4f} expected=[{},{}]".format(symbol, c, lo, hi)
            )
    return issues


def _compare_with_fxcm(
    symbol: str,
    tf_s: int,
    api_bars: List[Dict[str, Any]],
    limit: int,
) -> Optional[str]:
    """Порівнює API bars з FXCM raw (якщо доступний)."""
    try:
        from env_profile import load_env_secrets
        load_env_secrets()
        import os
        from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider
    except Exception as e:
        return "FXCM provider недоступний: {}".format(e)

    user_id = os.environ.get("FXCM_USER")
    password = os.environ.get("FXCM_PASS")
    url = os.environ.get("FXCM_URL", "https://www.fxcorporate.com/Hosts.jsp")
    connection = os.environ.get("FXCM_CONNECTION", "Demo")

    if not user_id or not password:
        return "FXCM credentials не задано (FXCM_USER/FXCM_PASS)"

    lines = []
    try:
        provider = FxcmHistoryProvider(
            user_id=user_id,
            password=password,
            url=url,
            connection=connection,
            day_anchor_offset_s=int(os.environ.get("FXCM_DAY_ANCHOR_OFFSET_S", "0")),
        )
        with provider:
            fxcm_bars = provider.fetch_last_n_tf(symbol, tf_s, limit)
        lines.append("\n=== FXCM raw ({} барів) ===".format(len(fxcm_bars)))
        if fxcm_bars:
            fxcm_dicts = []
            for fb in fxcm_bars:
                fxcm_dicts.append({
                    "open_time_ms": fb.open_time_ms,
                    "open": fb.o,
                    "high": fb.h,
                    "low": fb.low,
                    "close": fb.c,
                })
            lines.append(_bars_table(fxcm_dicts, tail_n=10))

            # Порівняння: знайти спільні бари по open_time_ms
            api_by_open = {b.get("open_time_ms"): b for b in api_bars if b.get("open_time_ms")}
            mismatch = 0
            for fb in fxcm_bars[-10:]:
                ab = api_by_open.get(fb.open_time_ms)
                if ab is None:
                    continue
                diffs = []
                for field, fv, av in [
                    ("open", fb.o, ab.get("open", ab.get("o"))),
                    ("high", fb.h, ab.get("high", ab.get("h"))),
                    ("low", fb.low, ab.get("low")),
                    ("close", fb.c, ab.get("close", ab.get("c"))),
                ]:
                    if fv is not None and av is not None and abs(float(fv) - float(av)) > 0.001:
                        diffs.append("{}:fxcm={:.4f},api={:.4f}".format(field, float(fv), float(av)))
                if diffs:
                    mismatch += 1
                    lines.append("  MISMATCH open_ms={}: {}".format(fb.open_time_ms, ", ".join(diffs)))
            if mismatch == 0:
                lines.append("  ✅ API==FXCM (останні 10 спільних барів)")
            else:
                lines.append("  ❌ {} mismatch з 10".format(mismatch))
        else:
            lines.append("  (FXCM повернув 0 барів)")
    except Exception as e:
        lines.append("FXCM ERROR: {}".format(e))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="TV Mismatch Probe: діагностика розбіжностей API vs TradingView",
    )
    parser.add_argument("--symbol", required=True, help="Символ (напр. XAU/USD)")
    parser.add_argument("--tf", type=int, required=True, help="TF у секундах (напр. 14400)")
    parser.add_argument("--limit", type=int, default=20, help="Кількість барів")
    parser.add_argument("--api-base", default="http://127.0.0.1:8089", help="API base URL")
    parser.add_argument("--compare-fxcm", action="store_true", help="Порівняти з FXCM raw")
    parser.add_argument("--tail", type=int, default=10, help="Показати останні N барів")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    print("=" * 60)
    print("TV Mismatch Probe: {} TF={}s limit={}".format(args.symbol, args.tf, args.limit))
    print("=" * 60)

    # 1) Fetch API bars
    try:
        data = _fetch_api_bars(args.api_base, args.symbol, args.tf, args.limit)
    except Exception as e:
        print("ERROR: не вдалося прочитати API: {}".format(e))
        return 1

    bars = data.get("bars", [])
    meta = data.get("meta", {})
    warnings = data.get("warnings", [])

    print("\n--- meta.extensions.instrument ---")
    ext = meta.get("extensions", {}) if isinstance(meta, dict) else {}
    instrument = ext.get("instrument", {})
    if instrument:
        for k, v in instrument.items():
            print("  {}: {}".format(k, v))
    else:
        print("  (відсутній — server.py Slice 1 не активовано?)")

    sample_bar = ext.get("sample_last_bar")
    if sample_bar:
        print("\n--- meta.extensions.sample_last_bar ---")
        for k, v in sample_bar.items():
            print("  {}: {}".format(k, v))

    if warnings:
        print("\n--- warnings ---")
        for w in warnings:
            print("  ⚠️  {}".format(w))

    # 2) Таблиця барів
    print("\n--- API bars (останні {}) ---".format(args.tail))
    print(_bars_table(bars, tail_n=args.tail))

    # 3) Price sanity check
    price_issues = _price_sanity_check(args.symbol, bars)
    if price_issues:
        print("\n❌ PRICE SANITY FAIL:")
        for issue in price_issues:
            print("  {}".format(issue))
    else:
        print("\n✅ Price sanity OK для {}".format(args.symbol))

    # 4) FXCM compare (optional)
    if args.compare_fxcm:
        result = _compare_with_fxcm(args.symbol, args.tf, bars, args.limit)
        if result:
            print(result)

    # 5) Verdict
    print("\n" + "=" * 60)
    has_instrument = bool(instrument)
    has_bars = len(bars) > 0
    price_ok = len(price_issues) == 0
    if has_instrument and has_bars and price_ok:
        print("PROBE OK: instrument present, bars OK, price sanity OK")
        print("Якщо UI свічки відрізняються від TradingView — проблема в UI mapping або TF/timezone.")
        return 0
    else:
        issues_summary = []
        if not has_instrument:
            issues_summary.append("instrument_missing")
        if not has_bars:
            issues_summary.append("no_bars")
        if not price_ok:
            issues_summary.append("price_sanity_fail")
        print("PROBE ISSUES: {}".format(", ".join(issues_summary)))
        return 1


if __name__ == "__main__":
    sys.exit(main())
