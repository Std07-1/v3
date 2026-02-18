#!/usr/bin/env python3
"""
tv_csv_compare — детерміноване порівняння TradingView CSV ↔ /api/bars.

Парсить "Export chart data" CSV з TradingView, порівнює з нашим API
бар-за-баром (align по open_time_ms), генерує PASS/FAIL verdict.

Використання:
  python -m tools.audit.tv_csv_compare \\
      --tv-csv exports/xau_h4.csv \\
      --symbol XAU/USD --tf-s 14400 --limit 200

  python -m tools.audit.tv_csv_compare \\
      --tv-csv exports/nas100_d1.csv \\
      --symbol NAS100 --tf-s 86400 --limit 100 --time-field close

Scope: tools/audit (read-only діагностика, не змінює дані).
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import logging
import os
import sys
import urllib.request
from datetime import timezone
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

# ───────── CONSTANTS ─────────

_FLOAT_TOL = 1e-2  # толеранс для порівняння OHLC (ціна)
_REPORT_DIR = os.path.join("reports", "mpv_proof")

# ───────── CSV PARSING ─────────

# TradingView CSV зазвичай має заголовки:
#   time,open,high,low,close,Volume   (або "Volume MA")
# Час може бути у форматах:
#   2026-02-12T10:00:00Z   (UTC ISO)
#   2026-02-12 10:00       (no TZ)
#   2026-02-12             (daily)

_TV_DATE_FMTS = [
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
]


def parse_tv_datetime(raw: str) -> Optional[_dt.datetime]:
    """Парсить дату/час з TradingView CSV (декілька форматів)."""
    raw = raw.strip().strip('"')
    for fmt in _TV_DATE_FMTS:
        try:
            return _dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _canonical_header(h: str) -> str:
    """Зводить заголовок CSV до канонічного ключа."""
    h = h.strip().strip('"').strip("\ufeff").lower()
    aliases = {
        "time": "time",
        "date": "time",
        "date/time": "time",
        "datetime": "time",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
        "vol": "volume",
        "volume ma": "volume",
    }
    return aliases.get(h, h)


def parse_tv_csv(path: str) -> List[Dict[str, Any]]:
    """
    Парсить TradingView CSV → список dict з полями:
      time_str, time_dt, open, high, low, close, volume
    """
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        raw_headers = next(reader, None)
        if raw_headers is None:
            return rows
        headers = [_canonical_header(h) for h in raw_headers]

        # Визначаємо індекси
        idx = {}
        for i, h in enumerate(headers):
            if h in ("time", "open", "high", "low", "close", "volume"):
                idx[h] = i

        required = {"time", "open", "high", "low", "close"}
        missing = required - set(idx.keys())
        if missing:
            logging.error("TV CSV: відсутні обов'язкові колонки: %s (знайдені: %s)", missing, headers)
            return rows

        for line_no, row in enumerate(reader, start=2):
            if len(row) <= max(idx.values()):
                continue
            time_str = row[idx["time"]].strip().strip('"')
            time_dt = parse_tv_datetime(time_str)
            if time_dt is None:
                logging.warning("TV CSV рядок %d: не вдалося розпарсити дату '%s'", line_no, time_str)
                continue
            try:
                o = float(row[idx["open"]])
                h = float(row[idx["high"]])
                lo = float(row[idx["low"]])
                c = float(row[idx["close"]])
                v = float(row[idx.get("volume", idx["close"])]) if "volume" in idx else 0.0
            except (ValueError, IndexError):
                logging.warning("TV CSV рядок %d: помилка парсингу OHLCV", line_no)
                continue
            rows.append({
                "time_str": time_str,
                "time_dt": time_dt,
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": v,
            })
    return rows


# ───────── TIME NORMALISATION ─────────


def _to_epoch_seconds(dt_naive: _dt.datetime, tz_mode: str) -> int:
    """Конвертує naive datetime з CSV у epoch seconds через режим tz."""
    if tz_mode == "utc":
        aware = dt_naive.replace(tzinfo=timezone.utc)
    else:
        # tv: трактуємо час CSV як локальний час машини
        local_tz = _dt.datetime.now().astimezone().tzinfo
        aware = dt_naive.replace(tzinfo=local_tz)
    return int(aware.timestamp())


def tv_bar_to_key_ms(
    bar: Dict[str, Any],
    *,
    tz_mode: str,
    time_field: str,
) -> int:
    """
    Обчислює ключ для align:
      time_field=open  -> key_ms = open_time_ms
      time_field=close -> key_ms = close_time_ms

    tz_mode:
      utc: CSV вже в UTC
      tv:  CSV як є (часто локальний час)
    """
    epoch_s = _to_epoch_seconds(bar["time_dt"], tz_mode)
    return epoch_s * 1000


def api_bar_to_key_ms(api_bar: Dict[str, Any], *, tf_s: int, time_field: str) -> Optional[int]:
    open_ms = api_bar.get("open_time_ms")
    if open_ms is None and isinstance(api_bar.get("time"), (int, float)):
        open_ms = int(api_bar["time"]) * 1000
    if open_ms is None:
        return None
    if time_field == "open":
        return int(open_ms)

    close_ms = api_bar.get("close_time_ms")
    if close_ms is None:
        try:
            close_ms = int(open_ms) + int(tf_s) * 1000
        except Exception:
            close_ms = None
    return int(close_ms) if close_ms is not None else None


def infer_time_field(
    *,
    tv_bars: List[Dict[str, Any]],
    api_bars: List[Dict[str, Any]],
    tf_s: int,
    tz_mode: str,
) -> str:
    """Heuristic: вибирає open/close, що краще співпадає по ключах з API."""
    api_open_keys = set()
    api_close_keys = set()
    for b in api_bars:
        k_open = api_bar_to_key_ms(b, tf_s=tf_s, time_field="open")
        k_close = api_bar_to_key_ms(b, tf_s=tf_s, time_field="close")
        if k_open is not None:
            api_open_keys.add(k_open)
        if k_close is not None:
            api_close_keys.add(k_close)

    # Беремо невеликий хвіст для оцінки (останнє більш релевантне)
    sample = tv_bars[-min(50, len(tv_bars)) :]
    tv_keys = [tv_bar_to_key_ms(b, tz_mode=tz_mode, time_field="open") for b in sample]

    open_hits = sum(1 for k in tv_keys if k in api_open_keys)
    close_hits = sum(1 for k in tv_keys if k in api_close_keys)
    logging.info("infer time_field: open_hits=%s close_hits=%s", open_hits, close_hits)
    if close_hits > open_hits:
        return "close"
    return "open"


# ───────── API FETCH ─────────


def fetch_api_bars(
    base_url: str,
    symbol: str,
    tf_s: int,
    limit: int,
) -> Dict[str, Any]:
    """Витягує /api/bars з нашого сервера."""
    url = "{}/api/bars?symbol={}&tf_s={}&limit={}".format(
        base_url.rstrip("/"),
        urllib.request.quote(symbol, safe=""),
        tf_s,
        limit,
    )
    logging.info("API fetch: %s", url)
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.load(resp)


# ───────── ALIGNMENT + COMPARE ─────────


def _float_eq(a: float, b: float, tol: float = _FLOAT_TOL) -> bool:
    return abs(a - b) <= tol


def align_and_compare(
    tv_bars: List[Dict[str, Any]],
    api_bars: List[Dict[str, Any]],
    tf_s: int,
    tz_mode: str,
    time_field: str,
    expected_limit: int,
    float_tol: float = _FLOAT_TOL,
) -> Dict[str, Any]:
    """
    Align TV та API бари по open_time_ms, порівнює OHLC.

    Повертає:
      common_count, tv_only_count, api_only_count, mismatch_count,
      first_mismatch (dict або None), verdict (PASS/FAIL), details.
    """
    # Будуємо dict {key_ms -> bar} для обох джерел
    tv_map: Dict[int, Dict[str, Any]] = {}
    for b in tv_bars:
        k = tv_bar_to_key_ms(b, tz_mode=tz_mode, time_field=time_field)
        tv_map[k] = b

    api_map: Dict[int, Dict[str, Any]] = {}
    for b in api_bars:
        k = api_bar_to_key_ms(b, tf_s=tf_s, time_field=time_field)
        if k is not None:
            api_map[int(k)] = b

    all_keys = sorted(set(tv_map.keys()) | set(api_map.keys()))

    common_count = 0
    tv_only_count = 0
    api_only_count = 0
    mismatch_count = 0
    first_mismatch: Optional[Dict[str, Any]] = None
    mismatches: List[Dict[str, Any]] = []

    for key_ms in all_keys:
        tv_b = tv_map.get(key_ms)
        api_b = api_map.get(key_ms)
        if tv_b is not None and api_b is None:
            tv_only_count += 1
            continue
        if tv_b is None and api_b is not None:
            api_only_count += 1
            continue
        # Обидва присутні
        common_count += 1
        # Порівнюємо OHLC
        api_o = float(api_b.get("open", api_b.get("o", 0)))
        api_h = float(api_b.get("high", api_b.get("h", 0)))
        api_l = float(api_b.get("low", api_b.get("l", 0)))
        api_c = float(api_b.get("close", api_b.get("c", 0)))

        fields_ok = (
            _float_eq(tv_b["open"], api_o, float_tol)
            and _float_eq(tv_b["high"], api_h, float_tol)
            and _float_eq(tv_b["low"], api_l, float_tol)
            and _float_eq(tv_b["close"], api_c, float_tol)
        )
        if not fields_ok:
            mismatch_count += 1
            mm = {
                "key_time_ms": key_ms,
                "key_utc": _dt.datetime.utcfromtimestamp(key_ms / 1000).strftime("%Y-%m-%d %H:%M UTC"),
                "tv": {"o": tv_b["open"], "h": tv_b["high"], "l": tv_b["low"], "c": tv_b["close"]},
                "api": {"o": api_o, "h": api_h, "l": api_l, "c": api_c},
                "delta": {
                    "o": round(tv_b["open"] - api_o, 6),
                    "h": round(tv_b["high"] - api_h, 6),
                    "l": round(tv_b["low"] - api_l, 6),
                    "c": round(tv_b["close"] - api_c, 6),
                },
            }
            mismatches.append(mm)
            if first_mismatch is None:
                first_mismatch = mm

    # Verdict
    verdict = (
        int(expected_limit) > 0
        and mismatch_count == 0
        and tv_only_count == 0
        and api_only_count == 0
        and common_count == int(expected_limit)
    )

    return {
        "common_count": common_count,
        "tv_only_count": tv_only_count,
        "api_only_count": api_only_count,
        "mismatch_count": mismatch_count,
        "total_tv": len(tv_bars),
        "total_api": len(api_bars),
        "expected_limit": int(expected_limit),
        "tz_mode": tz_mode,
        "time_field": time_field,
        "verdict": "PASS" if verdict else "FAIL",
        "first_mismatch": first_mismatch,
        "mismatches_sample": mismatches[:5],
    }


# ───────── REPORT GENERATION ─────────


def write_report(
    result: Dict[str, Any],
    symbol: str,
    tf_s: int,
    tv_csv_path: str,
) -> Tuple[str, str]:
    """Генерує MD + JSON звіт у reports/mpv_proof/."""
    os.makedirs(_REPORT_DIR, exist_ok=True)

    md_path = os.path.join(_REPORT_DIR, "tv_csv_compare.md")
    json_path = os.path.join(_REPORT_DIR, "tv_csv_compare.json")

    ts_str = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        "# TV CSV Compare Report",
        "",
        "| Поле | Значення |",
        "|---|---|",
        "| Дата | {} |".format(ts_str),
        "| Символ | {} |".format(symbol),
        "| TF | {}s |".format(tf_s),
        "| TV CSV | {} |".format(tv_csv_path),
        "| TZ mode | {} |".format(result.get("tz_mode")),
        "| Time field | {} |".format(result.get("time_field")),
        "| Expected limit | {} |".format(result.get("expected_limit")),
        "| **VERDICT** | **{}** |".format(result["verdict"]),
        "",
        "## Статистика",
        "",
        "| Метрика | Значення |",
        "|---|---|",
        "| Спільних барів | {} |".format(result["common_count"]),
        "| TV-only | {} |".format(result["tv_only_count"]),
        "| API-only | {} |".format(result["api_only_count"]),
        "| OHLC mismatch | {} |".format(result["mismatch_count"]),
        "| Всього TV | {} |".format(result["total_tv"]),
        "| Всього API | {} |".format(result["total_api"]),
        "",
    ]

    if result["first_mismatch"]:
        fm = result["first_mismatch"]
        lines.extend([
            "## Перший mismatch",
            "",
            "- key_time: {} ({})".format(fm["key_time_ms"], fm["key_utc"]),
            "- TV:  O={o} H={h} L={l} C={c}".format(**fm["tv"]),
            "- API: O={o} H={h} L={l} C={c}".format(**fm["api"]),
            "- Δ:   O={o} H={h} L={l} C={c}".format(**fm["delta"]),
            "",
        ])

    if result["verdict"] == "FAIL":
        lines.extend([
            "## Можливі причини FAIL",
            "",
            "- **Bid vs Mid**: FXCM повертає Bid-ціни, TradingView — Mid. Різниця = половина спреду.",
            "- **H4 anchor offset**: Shift 02:00/06:00/... vs 00:00/04:00/...",
            "- **Time field**: CSV може містити close-time замість open-time.",
            "- **Timezone**: CSV не в UTC.",
            "",
        ])

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    json_payload = {
        "ts": ts_str,
        "symbol": symbol,
        "tf_s": tf_s,
        "tv_csv": tv_csv_path,
        "result": result,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_payload, f, indent=2, ensure_ascii=False)

    return md_path, json_path


# ───────── CLI ─────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Порівняння TradingView CSV ↔ /api/bars",
    )
    parser.add_argument("--tv-csv", required=True, help="Шлях до CSV з TradingView")
    parser.add_argument("--base-url", default="http://127.0.0.1:8089", help="Базовий URL API")
    parser.add_argument("--symbol", required=True, help="Символ (наприклад XAU/USD)")
    parser.add_argument("--tf-s", type=int, required=True, help="Таймфрейм у секундах")
    parser.add_argument("--limit", type=int, default=200, help="Скільки барів запитувати")
    parser.add_argument("--tz", choices=["tv", "utc"], default="tv",
                        help="tv: як є в CSV (часто локальний); utc: CSV вже в UTC")
    parser.add_argument("--time-field", choices=["auto", "open", "close"], default="auto",
                        help="Що в CSV: open-time чи close-time (auto робить heuristic)")
    parser.add_argument("--float-tol", type=float, default=_FLOAT_TOL,
                        help="Толеранс порівняння цін (default: %(default)s)")
    args = parser.parse_args()

    print("=" * 60)
    print("TV CSV Compare: {} TF={}s".format(args.symbol, args.tf_s))
    print("=" * 60)

    # 1) Парсити CSV
    if not os.path.isfile(args.tv_csv):
        print("ПОМИЛКА: файл не знайдено: {}".format(args.tv_csv))
        return 1
    tv_bars = parse_tv_csv(args.tv_csv)
    print("\nTV CSV розпарсено: {} барів з {}".format(len(tv_bars), args.tv_csv))

    if tv_bars:
        print("\n--- Перші 3 бари (debug) ---")
        for b in tv_bars[:3]:
            print("  {} | O={:.4f} H={:.4f} L={:.4f} C={:.4f}".format(
                b["time_str"], b["open"], b["high"], b["low"], b["close"],
            ))

    if not tv_bars:
        print("ПОМИЛКА: CSV порожній або не вдалося розпарсити")
        return 1

    # 2) Fetch API bars
    try:
        api_resp = fetch_api_bars(args.base_url, args.symbol, args.tf_s, args.limit)
    except Exception as e:
        print("ПОМИЛКА API fetch: {}".format(e))
        return 1

    api_bars = api_resp.get("bars", [])
    print("API bars отримано: {}".format(len(api_bars)))

    if not api_bars:
        print("ПОМИЛКА: API повернув 0 барів")
        return 1

    # 3) time_field auto-infer (якщо потрібно)
    time_field = args.time_field
    if time_field == "auto":
        time_field = infer_time_field(
            tv_bars=tv_bars,
            api_bars=api_bars,
            tf_s=args.tf_s,
            tz_mode=args.tz,
        )
        print("Auto time_field => {}".format(time_field))

    # 4) Align + Compare
    result = align_and_compare(
        tv_bars,
        api_bars,
        args.tf_s,
        tz_mode=args.tz,
        time_field=time_field,
        expected_limit=args.limit,
        float_tol=args.float_tol,
    )

    print("\n--- Результат ---")
    print("  Спільних:      {}".format(result["common_count"]))
    print("  TV-only:       {}".format(result["tv_only_count"]))
    print("  API-only:      {}".format(result["api_only_count"]))
    print("  OHLC mismatch: {}".format(result["mismatch_count"]))

    if result["first_mismatch"]:
        fm = result["first_mismatch"]
        print("\n--- Перший mismatch ---")
        print("  key_time: {} ({})".format(fm["key_time_ms"], fm["key_utc"]))
        print("  TV:  O={o} H={h} L={l} C={c}".format(**fm["tv"]))
        print("  API: O={o} H={h} L={l} C={c}".format(**fm["api"]))
        print("  Δ:   O={o} H={h} L={l} C={c}".format(**fm["delta"]))

    # 4) Report
    md_path, json_path = write_report(result, args.symbol, args.tf_s, args.tv_csv)
    print("\nЗвіт: {} + {}".format(md_path, json_path))

    print("\n" + "=" * 60)
    verdict = result["verdict"]
    if verdict == "PASS":
        print("VERDICT: PASS — TradingView CSV ↔ API повне співпадіння.")
    else:
        reasons = []
        if result["mismatch_count"] > 0:
            reasons.append("OHLC_mismatch={}".format(result["mismatch_count"]))
        if result["tv_only_count"] > 0:
            reasons.append("tv_only={}".format(result["tv_only_count"]))
        if result["api_only_count"] > 0:
            reasons.append("api_only={}".format(result["api_only_count"]))
        if result["common_count"] != int(result.get("expected_limit", 0)):
            reasons.append("common={}/{}".format(result["common_count"], result.get("expected_limit")))
        print("VERDICT: FAIL — {}".format(", ".join(reasons)))
        print("Можливі причини: bid vs mid, H4 anchor offset, time field, timezone.")
    print("=" * 60)

    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
