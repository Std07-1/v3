#!/usr/bin/env python3
"""fxcm_raw_compare — порівняння FXCM raw history з API /api/bars.

MODE=DISCOVERY  
Ціль: закрити UNKNOWN "FXCM raw history" та вирішити GO/NO-GO для HTF rebuild.

Дані:
  A) API: GET /api/bars?symbol=...&tf_s=...&limit=...
  B) FXCM raw: FxcmHistoryProvider.fetch_last_n_tf() (те саме що fetch_tf_backfill)

Порівняння: match по open_time_ms (і close_time_ms), далі OHLCV.

Вивід:
  reports/mpv_proof/fxcm_raw_compare.json
  reports/mpv_proof/fxcm_raw_compare.md

Exit gate:
  mismatch_count == 0 => NO-GO rebuild (дані збігаються, rebuild не потрібен)
  mismatch_count  > 0 => GO PATCH rebuild (є розбіжності, потрібен rebuild)

Запуск:
  python -m tools.audit.fxcm_raw_compare --symbol XAU/USD --symbol NAS100 --tfs 14400,86400 --limit-h4 200 --limit-d1 100
  python tools/audit/fxcm_raw_compare.py --symbol XAU/USD --tfs 14400 --limit-h4 200

Потребує: FXCM credentials у .env (FXCM_USERNAME, FXCM_PASSWORD).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ─── Шляхи ────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from env_profile import load_env_secrets
from core.config_loader import pick_config_path, load_system_config, env_str
from core.model.bars import CandleBar

# Імпорт FxcmHistoryProvider — lazy, щоб не падати якщо немає ForexConnect
_provider_cls = None  # type: Any


def _get_provider_cls() -> Any:
    global _provider_cls
    if _provider_cls is None:
        from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider
        _provider_cls = FxcmHistoryProvider
    return _provider_cls


# ─── Конфігурація ─────────────────────────────────────────────────────
_DEFAULT_API_BASE = "http://127.0.0.1:8089"
_REPORT_DIR = _REPO_ROOT / "reports" / "mpv_proof"
_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")

# Допустиме відхилення для float-порівняння (FXCM vs API можуть мати різну точність)
_FLOAT_ATOL = 1e-6  # абсолютна похибка
_FLOAT_RTOL = 1e-8  # відносна похибка


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


# ─── API fetch ────────────────────────────────────────────────────────
def _fetch_api_bars(
    api_base: str,
    symbol: str,
    tf_s: int,
    limit: int,
) -> List[Dict[str, Any]]:
    """Забирає бари з /api/bars (HTTP GET)."""
    sym_enc = symbol.replace("/", "%2F")
    url = f"{api_base}/api/bars?symbol={sym_enc}&tf_s={tf_s}&limit={limit}"
    logging.info("API fetch: %s", url)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.URLError as exc:
        logging.error("API fetch FAILED: %s — %s", url, exc)
        return []
    except Exception as exc:
        logging.error("API fetch ERROR: %s — %s", url, exc)
        return []

    if not data.get("ok"):
        logging.warning("API відповів ok=false для %s tf_s=%d: warnings=%s",
                        symbol, tf_s, data.get("warnings"))
        return []

    bars = data.get("bars", [])
    if not isinstance(bars, list):
        return []
    logging.info("API: отримано %d барів для %s tf_s=%d", len(bars), symbol, tf_s)
    return bars


# ─── FXCM raw fetch ──────────────────────────────────────────────────
def _fetch_fxcm_bars(
    provider: Any,
    symbol: str,
    tf_s: int,
    limit: int,
) -> List[Dict[str, Any]]:
    """Забирає бари напряму з FXCM через FxcmHistoryProvider."""
    logging.info("FXCM fetch: symbol=%s tf_s=%d n=%d", symbol, tf_s, limit)
    try:
        if tf_s == 60:
            raw_bars: List[CandleBar] = provider.fetch_last_n_m1(symbol, n=limit)
        else:
            raw_bars = provider.fetch_last_n_tf(symbol, tf_s=tf_s, n=limit)
    except Exception as exc:
        logging.error("FXCM fetch FAILED: symbol=%s tf_s=%d — %s", symbol, tf_s, exc)
        return []

    if not raw_bars:
        logging.warning("FXCM: 0 барів для %s tf_s=%d", symbol, tf_s)
        return []

    result: List[Dict[str, Any]] = []
    for b in raw_bars:
        result.append({
            "open_time_ms": b.open_time_ms,
            "close_time_ms": b.close_time_ms,
            "open": b.o,
            "high": b.h,
            "low": b.low,
            "close": b.c,
            "volume": b.v,
            "src": b.src,
            "complete": b.complete,
        })
    logging.info("FXCM: отримано %d барів для %s tf_s=%d", len(result), symbol, tf_s)
    return result


# ─── Порівняння ───────────────────────────────────────────────────────
def _floats_match(a: float, b: float) -> bool:
    """Порівняння float з допуском."""
    if a == b:
        return True
    diff = abs(a - b)
    if diff <= _FLOAT_ATOL:
        return True
    denom = max(abs(a), abs(b), 1e-15)
    return diff / denom <= _FLOAT_RTOL


def _compare_bars(
    api_bars: List[Dict[str, Any]],
    fxcm_bars: List[Dict[str, Any]],
    symbol: str,
    tf_s: int,
) -> Dict[str, Any]:
    """Порівнює два списки барів по open_time_ms."""
    # Індексуємо по open_time_ms
    api_by_open: Dict[int, Dict[str, Any]] = {}
    for b in api_bars:
        ot = b.get("open_time_ms")
        if isinstance(ot, int):
            api_by_open[ot] = b

    fxcm_by_open: Dict[int, Dict[str, Any]] = {}
    for b in fxcm_bars:
        ot = b.get("open_time_ms")
        if isinstance(ot, int):
            fxcm_by_open[ot] = b

    api_only = sorted(set(api_by_open.keys()) - set(fxcm_by_open.keys()))
    fxcm_only = sorted(set(fxcm_by_open.keys()) - set(api_by_open.keys()))
    common = sorted(set(api_by_open.keys()) & set(fxcm_by_open.keys()))

    mismatches: List[Dict[str, Any]] = []

    for ot in common:
        ab = api_by_open[ot]
        fb = fxcm_by_open[ot]

        diffs: Dict[str, Any] = {}

        # close_time_ms
        api_ct = ab.get("close_time_ms")
        fxcm_ct = fb.get("close_time_ms")
        if api_ct is not None and fxcm_ct is not None and api_ct != fxcm_ct:
            diffs["close_time_ms"] = {"api": api_ct, "fxcm": fxcm_ct}

        # OHLCV
        for field in _OHLCV_FIELDS:
            av = ab.get(field)
            fv = fb.get(field)
            if av is None or fv is None:
                if av != fv:
                    diffs[field] = {"api": av, "fxcm": fv}
                continue
            try:
                if not _floats_match(float(av), float(fv)):
                    diffs[field] = {"api": float(av), "fxcm": float(fv), "delta": round(float(av) - float(fv), 8)}
            except (TypeError, ValueError):
                diffs[field] = {"api": av, "fxcm": fv, "error": "non_numeric"}

        if diffs:
            mismatches.append({
                "open_time_ms": ot,
                "open_utc": dt.datetime.utcfromtimestamp(ot / 1000).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "diffs": diffs,
            })

    result = {
        "symbol": symbol,
        "tf_s": tf_s,
        "api_count": len(api_bars),
        "fxcm_count": len(fxcm_bars),
        "common_count": len(common),
        "api_only_count": len(api_only),
        "fxcm_only_count": len(fxcm_only),
        "mismatch_count": len(mismatches),
        "mismatches_sample": mismatches[:10],
        "api_only_sample": [
            {"open_time_ms": ot, "utc": dt.datetime.utcfromtimestamp(ot / 1000).strftime("%Y-%m-%dT%H:%M:%SZ")}
            for ot in api_only[:5]
        ],
        "fxcm_only_sample": [
            {"open_time_ms": ot, "utc": dt.datetime.utcfromtimestamp(ot / 1000).strftime("%Y-%m-%dT%H:%M:%SZ")}
            for ot in fxcm_only[:5]
        ],
    }

    return result


# ─── Звіти ────────────────────────────────────────────────────────────
def _write_json(report: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("JSON звіт: %s", path)


def _write_md(report: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = report.get("generated_utc", "?")
    comparisons = report.get("comparisons", [])
    total_mismatch = report.get("total_mismatch_count", 0)
    verdict = report.get("verdict", "UNKNOWN")

    lines = [
        "# FXCM Raw Compare Report",
        f"Згенеровано: {ts}",
        "",
        f"**Verdict: {verdict}**",
        "",
        f"Загальна кількість OHLCV mismatch: **{total_mismatch}**",
        "",
    ]

    for comp in comparisons:
        sym = comp.get("symbol", "?")
        tf = comp.get("tf_s", "?")
        mm = comp.get("mismatch_count", 0)
        api_n = comp.get("api_count", 0)
        fxcm_n = comp.get("fxcm_count", 0)
        common = comp.get("common_count", 0)
        api_only = comp.get("api_only_count", 0)
        fxcm_only = comp.get("fxcm_only_count", 0)

        icon = "✅" if mm == 0 else "❌"
        lines.append(f"## {icon} {sym} TF={tf}s")
        lines.append("")
        lines.append(f"| Метрика | Значення |")
        lines.append(f"|---------|----------|")
        lines.append(f"| API барів | {api_n} |")
        lines.append(f"| FXCM барів | {fxcm_n} |")
        lines.append(f"| Спільних (matched) | {common} |")
        lines.append(f"| Тільки в API | {api_only} |")
        lines.append(f"| Тільки в FXCM | {fxcm_only} |")
        lines.append(f"| **OHLCV mismatch** | **{mm}** |")
        lines.append("")

        if mm > 0:
            sample = comp.get("mismatches_sample", [])[:5]
            lines.append("### Приклади mismatch (перші 5)")
            lines.append("")
            for i, m in enumerate(sample, 1):
                lines.append(f"**{i}. open_time_ms={m['open_time_ms']}** ({m.get('open_utc', '?')})")
                for field, vals in m.get("diffs", {}).items():
                    lines.append(f"  - `{field}`: API={vals.get('api')} FXCM={vals.get('fxcm')}"
                                 + (f" Δ={vals.get('delta')}" if vals.get("delta") is not None else ""))
                lines.append("")

        if api_only > 0:
            lines.append(f"### Тільки в API (перші 5): {comp.get('api_only_sample', [])}")
            lines.append("")
        if fxcm_only > 0:
            lines.append(f"### Тільки в FXCM (перші 5): {comp.get('fxcm_only_sample', [])}")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Exit Gate (strict)")
    lines.append("")
    gate_pass_val = report.get("gate_pass", False)
    gate_failures_val = report.get("gate_failures", [])
    lines.append("Умови PASS (для кожної пари symbol+tf):")
    lines.append("- `mismatch_count == 0`")
    lines.append("- `api_only_count == 0`")
    lines.append("- `fxcm_only_count == 0`")
    lines.append("- `common_count == expected_limit`")
    lines.append("")
    if gate_pass_val:
        lines.append("**PASS**: всі умови виконані.")
    else:
        lines.append("**FAIL**: %d порушень:" % len(gate_failures_val))
        for gf in gate_failures_val:
            lines.append("- %s — %s: actual=%s expected=%s" % (
                gf.get("pair"), gf.get("check"), gf.get("actual"), gf.get("expected"),
            ))
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    logging.info("MD звіт: %s", path)


# ─── Provider factory ────────────────────────────────────────────────
def _create_provider(cfg: Dict[str, Any]) -> Any:
    """Створює FxcmHistoryProvider з конфігу + ENV секретів."""
    ProviderCls = _get_provider_cls()

    user_id = env_str("FXCM_USERNAME") or str(cfg.get("user_id") or "").strip()
    password = env_str("FXCM_PASSWORD") or str(cfg.get("password") or "").strip()
    url = env_str("FXCM_HOST_URL") or str(cfg.get("url", "http://www.fxcorporate.com/Hosts.jsp"))
    connection = env_str("FXCM_CONNECTION") or str(cfg.get("connection", "Demo"))

    if not user_id or not password:
        logging.error("Відсутні FXCM credentials (FXCM_USERNAME/FXCM_PASSWORD у .env)")
        raise RuntimeError("FXCM credentials missing")

    return ProviderCls(
        user_id=user_id,
        password=password,
        url=url,
        connection=connection,
        day_anchor_offset_s=int(cfg.get("day_anchor_offset_s", 0)),
        day_anchor_offset_s_d1=_int_or_none(cfg.get("day_anchor_offset_s_d1")),
        day_anchor_offset_s_d1_alt=_int_or_none(cfg.get("day_anchor_offset_s_d1_alt")),
        day_anchor_offset_s_alt=_int_or_none(cfg.get("day_anchor_offset_s_alt")),
        day_anchor_offset_s_alt2=_int_or_none(cfg.get("day_anchor_offset_s_alt2")),
    )


def _int_or_none(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


# ─── Main ─────────────────────────────────────────────────────────────
def main() -> int:
    _setup_logging()

    ap = argparse.ArgumentParser(
        description="Порівняння FXCM raw history з API /api/bars (DISCOVERY).",
    )
    ap.add_argument(
        "--symbol", action="append", default=[],
        help="Символ для порівняння (можна кілька: --symbol XAU/USD --symbol NAS100)",
    )
    ap.add_argument(
        "--tfs", default="14400,86400",
        help="Список TF через кому (default: 14400,86400)",
    )
    ap.add_argument("--limit-h4", type=int, default=200, help="Ліміт барів для H4 (default: 200)")
    ap.add_argument("--limit-d1", type=int, default=100, help="Ліміт барів для D1 (default: 100)")
    ap.add_argument("--limit", type=int, default=None, help="Ліміт для всіх TF (override)")
    ap.add_argument(
        "--api-base", default=_DEFAULT_API_BASE,
        help="Base URL API сервера (default: http://127.0.0.1:8089)",
    )
    args = ap.parse_args()

    # Символи
    symbols = args.symbol if args.symbol else ["XAU/USD", "NAS100"]
    tfs = [int(x.strip()) for x in args.tfs.split(",") if x.strip()]
    if not tfs:
        logging.error("Порожній список TF")
        return 2

    # Ліміти per-TF
    def _limit_for_tf(tf_s: int) -> int:
        if args.limit is not None:
            return args.limit
        if tf_s == 86400:
            return args.limit_d1
        return args.limit_h4

    # Конфіг + credentials
    load_env_secrets()
    cfg = load_system_config(pick_config_path())

    # Перевірка доступності API
    try:
        with urllib.request.urlopen(f"{args.api_base}/api/status", timeout=5) as r:
            status = json.load(r)
        if not status.get("ok"):
            logging.warning("API /api/status відповів ok=false")
    except Exception as exc:
        logging.error("API недоступний (%s): %s", args.api_base, exc)
        return 2

    # Створюємо FXCM provider
    try:
        provider = _create_provider(cfg)
    except RuntimeError:
        return 2

    comparisons: List[Dict[str, Any]] = []
    total_mismatch = 0

    try:
        with provider:
            for symbol in symbols:
                for tf_s in tfs:
                    limit = _limit_for_tf(tf_s)
                    logging.info("=== Порівняння: %s TF=%ds limit=%d ===", symbol, tf_s, limit)

                    # A) API bars
                    api_bars = _fetch_api_bars(args.api_base, symbol, tf_s, limit)

                    # B) FXCM raw bars
                    fxcm_bars = _fetch_fxcm_bars(provider, symbol, tf_s, limit)

                    if not api_bars and not fxcm_bars:
                        logging.warning("Обидва джерела порожні для %s TF=%d", symbol, tf_s)
                        comparisons.append({
                            "symbol": symbol,
                            "tf_s": tf_s,
                            "api_count": 0,
                            "fxcm_count": 0,
                            "common_count": 0,
                            "api_only_count": 0,
                            "fxcm_only_count": 0,
                            "mismatch_count": 0,
                            "mismatches_sample": [],
                            "note": "both_empty",
                        })
                        continue

                    comp = _compare_bars(api_bars, fxcm_bars, symbol, tf_s)
                    comparisons.append(comp)
                    total_mismatch += comp["mismatch_count"]

                    logging.info(
                        "Результат %s TF=%d: common=%d mismatch=%d api_only=%d fxcm_only=%d",
                        symbol, tf_s,
                        comp["common_count"], comp["mismatch_count"],
                        comp["api_only_count"], comp["fxcm_only_count"],
                    )
    except Exception as exc:
        logging.error("FXCM provider error: %s", exc)
        return 1

    # Verdict — strict gate: PASS лише якщо для КОЖНОГО (symbol, tf)
    # mismatch==0 AND api_only==0 AND fxcm_only==0 AND common==limit
    gate_failures: List[Dict[str, Any]] = []
    for comp in comparisons:
        sym_tf = "%s:tf_%d" % (comp.get("symbol", "?"), comp.get("tf_s", 0))
        tf_s_c = comp.get("tf_s", 0)
        expected_limit = _limit_for_tf(tf_s_c)
        checks = [
            ("mismatch_count", comp.get("mismatch_count", 0), 0),
            ("api_only_count", comp.get("api_only_count", 0), 0),
            ("fxcm_only_count", comp.get("fxcm_only_count", 0), 0),
            ("common_count", comp.get("common_count", 0), expected_limit),
        ]
        for check_name, actual, expected in checks:
            if actual != expected:
                gate_failures.append({
                    "pair": sym_tf,
                    "check": check_name,
                    "actual": actual,
                    "expected": expected,
                })

    gate_pass = len(gate_failures) == 0
    if gate_pass:
        verdict = "PASS (всі 4 умови виконані для кожної пари)"
    else:
        verdict = "FAIL (%d порушень gate)" % len(gate_failures)

    ts_utc = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = {
        "generated_utc": ts_utc,
        "symbols": symbols,
        "tfs": tfs,
        "total_mismatch_count": total_mismatch,
        "gate_pass": gate_pass,
        "gate_failures": gate_failures,
        "verdict": verdict,
        "comparisons": comparisons,
    }

    # Записуємо звіти
    json_path = _REPORT_DIR / "fxcm_raw_compare.json"
    md_path = _REPORT_DIR / "fxcm_raw_compare.md"
    _write_json(report, json_path)
    _write_md(report, md_path)

    # Summary
    logging.info("=" * 60)
    logging.info("VERDICT: %s", verdict)
    logging.info("Gate pass: %s", gate_pass)
    logging.info("Mismatch total: %d", total_mismatch)
    if gate_failures:
        for gf in gate_failures:
            logging.info(
                "  FAIL: %s — %s: actual=%s expected=%s",
                gf["pair"], gf["check"], gf["actual"], gf["expected"],
            )
    logging.info("Звіт: %s", md_path)
    logging.info("=" * 60)

    return 0 if gate_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
