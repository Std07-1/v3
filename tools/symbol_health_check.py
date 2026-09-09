"""tools/symbol_health_check.py — здоров'я символу по всіх TF (ADR-0054 Фаза 1).

Тонка I/O-оболонка над pure-вимірами ``core/health``: читає SSOT-JSONL з диску,
будує календар символу з config і віддає JSON-звіт із вердиктом на кожен (symbol, TF).

Навіщо: 06.09 засів NAS100 виглядав цілим (M1 і H4 доходили до вересня), а D1 тихо
обірвався на два місяці раніше. Око цього не бачить — цей інструмент бачить.

Приклади:

    python -m tools.symbol_health_check --symbol NAS100
    python -m tools.symbol_health_check --all --days 30 --json out.json
    python -m tools.symbol_health_check --symbol XAU/USD --gate   # rc=1 якщо не GREEN
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

from core.buckets import resolve_anchor_offset_ms, tf_to_ms
from core.config_loader import load_system_config, resolve_config_path
from core.derive import DERIVE_SOURCE, resolve_cascade_anchor_s
from core.health import (
    check_anchor_on_session_edge,
    compare_reports,
    grade_symbol_tf,
    measure_age,
    measure_cascade,
    measure_depth,
    measure_geometry,
    measure_holes,
)
from core.model.bars import CandleBar
from runtime.ingest.tick_common import resolve_symbol_calendars

_log = logging.getLogger("symbol_health")

# Скільки барів потрібно, щоб SMC мав що аналізувати на цьому TF (ADR-0054 §3.2 dim 6).
REQUIRED_BARS_BY_TF = {60: 1440, 180: 480, 300: 288, 900: 96, 1800: 48, 3600: 120, 14400: 120, 86400: 20}
DEFAULT_WINDOW_DAYS = 7


def _read_bars(data_root: str, symbol: str, tf_s: int) -> List[CandleBar]:
    """Прочитати всі бари символу/TF з SSOT-JSONL (порядок як на диску)."""
    sym_dir = symbol.replace("/", "_")
    pattern = os.path.join(data_root, sym_dir, f"tf_{tf_s}", "part-*.jsonl")
    bars: List[CandleBar] = []
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        _log.warning("HEALTH_BAD_JSON path=%s", path)
                        continue
                    bars.append(
                        CandleBar(
                            symbol=symbol,
                            tf_s=tf_s,
                            open_time_ms=int(d["open_time_ms"]),
                            close_time_ms=int(d.get("close_time_ms", int(d["open_time_ms"]) + tf_s * 1000)),
                            o=float(d["o"]),
                            h=float(d["h"]),
                            # Пастка P1: на диску ключ "l", у dataclass поле .low
                            low=float(d.get("low", d.get("l", 0.0))),
                            c=float(d["c"]),
                            v=float(d.get("v", 0.0)),
                            complete=bool(d.get("complete", True)),
                            src=str(d.get("src", "history")),
                            extensions=dict(d.get("extensions") or {}),
                        )
                    )
        except OSError as exc:
            _log.warning("HEALTH_READ_FAIL path=%s err=%s", path, exc)
    return bars


def _declares_partial(bar: CandleBar) -> bool:
    """Чи бар САМ повідомив, що зібраний не з повного набору (ADR-0013b маркери).

    ADR-0015 Option C: `complete=true` означає «бакет минув», а не «N з N», і такі бари
    легальні. Health-check має ловити МОВЧАЗНІ розбіжності, а не задокументовані:
    інакше він тоне у 37 тисячах чесно позначених барів і перестає бути сигналом.
    """
    ext = bar.extensions or {}
    return bool(
        ext.get("partial")
        or ext.get("boundary_partial")
        or ext.get("partial_calendar_pause")
        or ext.get("partial_reasons")
    )


def _legal_anchors_ms(cfg: Dict[str, Any], tf_s: int, primary_ms: int) -> List[int]:
    """Легальні якорі для TF: основний + DST-альтернативи з config.

    HTF-якір рухається з переходом на зимовий/літній час (D1 21:00/22:00, H4 22:00/23:00).
    Бар на alt-якорі — не дефект, тому вимір має знати весь дозволений набір
    (SSOT цих значень — ті самі ключі, що читає `select_anchor_offset_for_open_ms`).
    """
    keys = (
        ("day_anchor_offset_s_d1", "day_anchor_offset_s_d1_alt")
        if tf_s == 86400
        else ("day_anchor_offset_s", "day_anchor_offset_s_alt", "day_anchor_offset_s_alt2")
    )
    out = [primary_ms]
    for key in keys:
        raw = cfg.get(key)
        if raw is None:
            continue
        value = int(raw) * 1000 % (tf_s * 1000)
        if value not in out:
            out.append(value)
    return out


def check_symbol(
    cfg: Dict[str, Any],
    symbol: str,
    *,
    data_root: str,
    now_ms: int,
    window_days: int,
) -> Dict[str, Any]:
    """Порахувати всі виміри для одного символу по кожному TF з allowlist."""
    calendars, rejected = resolve_symbol_calendars(cfg, [symbol], where="symbol_health_check")
    if rejected:
        return {"symbol": symbol, "grade": "RED", "reasons": ["calendar_group_missing"], "tfs": {}}
    calendar = calendars[symbol]
    is_trading = calendar.is_trading_minute

    tf_list = sorted(int(t) for t in cfg.get("tf_allowlist_s", []))
    bars_by_tf = {tf: _read_bars(data_root, symbol, tf) for tf in tf_list}
    window_start = now_ms - window_days * 86_400_000

    tfs: Dict[str, Any] = {}
    worst = "GREEN"
    for tf_s in tf_list:
        bars = bars_by_tf.get(tf_s, [])
        opens = [b.open_time_ms for b in bars]
        tf_ms = tf_to_ms(tf_s)
        anchor_ms = resolve_cascade_anchor_s(
            tf_s,
            h4_anchor_offset_s=int(cfg.get("day_anchor_offset_s", 0)),
            d1_anchor_offset_s=int(cfg.get("day_anchor_offset_s_d1", 0)),
        ) * 1000 or resolve_anchor_offset_ms(tf_s, cfg)
        anchors_ms = _legal_anchors_ms(cfg, tf_s, anchor_ms)

        age = measure_age(opens, now_ms=now_ms, tf_ms=tf_ms, anchor_offset_ms=anchor_ms, is_trading_fn=is_trading)
        holes = measure_holes(
            opens, start_ms=window_start, end_ms=now_ms, tf_ms=tf_ms,
            anchor_offset_ms=anchor_ms, is_trading_fn=is_trading,
        )
        geometry = measure_geometry(bars, tf_ms=tf_ms, anchor_offsets_ms=anchors_ms)
        depth = measure_depth(opens, required_bars=REQUIRED_BARS_BY_TF.get(tf_s, 0))

        cascade = None
        source_tf = DERIVE_SOURCE.get(tf_s, (None, None))[0]
        if source_tf and bars and bars_by_tf.get(source_tf):
            cascade = measure_cascade(
                bars, bars_by_tf[source_tf],
                target_tf_ms=tf_ms, source_tf_ms=tf_to_ms(source_tf), anchor_offsets_ms=anchors_ms,
                declares_partial_fn=_declares_partial,
            )

        grade = grade_symbol_tf(age=age, holes=holes, geometry=geometry, cascade=cascade, depth=depth)
        if grade.grade == "RED" or (grade.grade == "YELLOW" and worst == "GREEN"):
            worst = grade.grade
        tfs[str(tf_s)] = {
            "grade": grade.grade,
            "reasons": grade.reasons,
            "bars": geometry.total,
            "first": _iso(depth.first_open_ms),
            "last": _iso(depth.last_open_ms),
            "span_days": depth.span_days,
            "age_buckets": age.age_buckets,
            "holes": {"missing": holes.missing, "expected": holes.expected},
            "geometry": {
                "exact_dup": geometry.exact_dup, "dup_conflicting": geometry.dup_conflicting,
                "unsorted": geometry.unsorted,
                "align_bad": geometry.align_bad, "close_bad": geometry.close_bad,
                "ohlc_bad": geometry.ohlc_bad,
            },
            "cascade": (
                None if cascade is None
                else {"checked": cascade.checked, "mismatched": cascade.mismatched,
                      "declared_partial": cascade.declared_partial,
                      "skipped_incomplete": cascade.skipped_incomplete}
            ),
        }

    anchor_ok = check_anchor_on_session_edge(
        _last_anchor_open(bars_by_tf.get(86400, []), int(cfg.get("day_anchor_offset_s_d1", 0))),
        tf_ms=86_400_000,
        is_trading_fn=is_trading,
    ) if bars_by_tf.get(86400) else None
    return {"symbol": symbol, "grade": worst, "d1_anchor_on_session_edge": anchor_ok, "tfs": tfs}


def _last_anchor_open(bars: Sequence[CandleBar], d1_anchor_offset_s: int) -> int:
    return max(b.open_time_ms for b in bars) if bars else 0


def _iso(ms: Optional[int]) -> Optional[str]:
    if not ms:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Здоров'я символу по всіх TF (ADR-0054 Фаза 1)")
    parser.add_argument("--symbol", action="append", default=None, help="символ (можна кілька разів)")
    parser.add_argument("--all", action="store_true", help="усі symbols[] з config")
    parser.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS, help="вікно для holes/age")
    parser.add_argument("--json", type=str, default=None, help="записати звіт у файл")
    parser.add_argument("--gate", action="store_true", help="rc=1 якщо є не-GREEN символ")
    parser.add_argument(
        "--compare",
        type=str,
        default=None,
        help="baseline-JSON: порівняти з ним; rc=1 при будь-якому погіршенні (ADR-0054 §3.4)",
    )
    parser.add_argument(
        "--gate-symbols",
        type=str,
        default=None,
        help="через кому: перевіряти регресію лише на цих символах (напр. вже активні)",
    )
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    cfg = load_system_config(resolve_config_path(args.config))
    data_root = str(cfg.get("data_root", "./data_v3"))
    symbols = list(cfg.get("symbols", [])) if args.all or not args.symbol else list(args.symbol)
    if not symbols:
        _log.error("HEALTH_NO_SYMBOLS: вкажіть --symbol або --all")
        return 2

    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    report = {
        "generated_at": _iso(now_ms),
        "window_days": args.days,
        "symbols": {
            sym: check_symbol(cfg, sym, data_root=data_root, now_ms=now_ms, window_days=args.days)
            for sym in symbols
        },
    }

    for sym, res in report["symbols"].items():
        print(f"\n=== {sym}: {res['grade']}  (D1 якір на межі сесії: {res.get('d1_anchor_on_session_edge')})")
        for tf, d in res["tfs"].items():
            flag = {"GREEN": "  ok", "YELLOW": "WARN", "RED": " RED"}[d["grade"]]
            casc = d["cascade"]
            casc_txt = (
                "-" if casc is None
                else f"{casc['checked']}/{casc['mismatched']}(+{casc['declared_partial']}p)"
            )
            print(
                f"  [{flag}] tf_{tf:<6} bars={d['bars']:<7} {d['first']} .. {d['last']}"
                f"  age={d['age_buckets']} holes={d['holes']['missing']}/{d['holes']['expected']}"
                f" cascade(chk/мовчазних+позначених)={casc_txt}"
                + (f"  {','.join(d['reasons'])}" if d["reasons"] else "")
            )

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"\nJSON: {args.json}")

    rc = 0

    if args.compare:
        with open(args.compare, "r", encoding="utf-8") as fh:
            baseline = json.load(fh)
        only = [s.strip() for s in args.gate_symbols.split(",")] if args.gate_symbols else None
        cmp_res = compare_reports(baseline, report, only_symbols=only)
        print("")
        print("=== ПОРІВНЯННЯ з %s ===" % args.compare)
        print("  перевірено символів: %s" % (", ".join(cmp_res.compared_symbols) or "-"))
        if cmp_res.new_symbols:
            print("  нових (не в baseline, не перевіряються): %s" % ", ".join(cmp_res.new_symbols))
        for reg in cmp_res.missing_symbols:
            print("  ЗНИК символ: %s" % reg)
        for reg in cmp_res.improvements:
            print("  краще: %s" % reg.describe())
        for reg in cmp_res.regressions:
            print("  РЕГРЕСІЯ: %s" % reg.describe())
        if cmp_res.ok:
            print("  => регресій немає")
        else:
            print("  => РЕГРЕСІЙ: %d" % (len(cmp_res.regressions) + len(cmp_res.missing_symbols)))
            rc = 1

    if args.gate and any(r["grade"] != "GREEN" for r in report["symbols"].values()):
        rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
