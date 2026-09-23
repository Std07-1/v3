"""tools/symbol_health_check.py — здоров'я символу по всіх TF (ADR-0054 Фаза 1).

Тонка I/O-оболонка над pure-вимірами ``core/health``: читає SSOT-JSONL з диску,
будує календар символу з config і віддає JSON-звіт із вердиктом на кожен (symbol, TF).

Навіщо: 06.09 засів NAS100 виглядав цілим (M1 і H4 доходили до вересня), а D1 тихо
обірвався на два місяці раніше. Око цього не бачить — цей інструмент бачить.

Приклади:

    python -m tools.symbol_health_check --symbol NAS100
    python -m tools.symbol_health_check --all --days 30 --json out.json
    python -m tools.symbol_health_check --symbol XAU/USD --gate   # rc=1 якщо не GREEN
    python -m tools.symbol_health_check --all --days 2 --compare base.json --gate-symbols "XAU/USD,NAS100"

Коди виходу: 0 — ок; 1 — регресія або не-GREEN під --gate (за рунбуком активації — ВІДКАТ);
2 — не вказано символів; 3 — baseline знято іншою версією виміру (`measure_version`): вердикти
не порівнювались, відкочувати нічого, перезніміть baseline.

Версія виміру 2 (ADR-0094 P4): батьки й діти згортаються так, як їх показують читачі, і кожен
derived-бар звіряється з агрегацією M1 у своєму бакеті (`root`). Каскад сусідніх рівнів цього не
бачив: 14.09.2026 він показував 137 розбіжностей на XAU/XAG, а проти M1 — 659.

Версія виміру 3 (ADR-0095 S5a): правило якоря H4/D1 — на символ з `htf_anchor_rule_resolver`, сітка одна,
сезонна. Бар H4/D1 не на ній — `off_season_grid` (RED) з очікуваним відкриттям у звіті; легасі-якорів
config інструмент не читає. Символ із невиміряною групою календаря — RED `htf_anchor_rule_missing`,
а не тихий якір.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import logging
import os
import sys
from typing import Any, Callable, Dict, List, Optional

from core.config_loader import htf_anchor_rule_resolver, load_system_config, resolve_config_path
from core.derive import DERIVE_SOURCE
from core.health import (
    HEALTH_MEASURE_VERSION,
    check_anchor_on_session_edge,
    compare_reports,
    grade_symbol_tf,
    measure_age,
    measure_cascade,
    measure_depth,
    measure_geometry,
    measure_holes,
    measure_root_consistency,
)
from core.model.bars import CandleBar
from core.session_anchor import season_label
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


def check_symbol(
    cfg: Dict[str, Any],
    symbol: str,
    *,
    data_root: str,
    now_ms: int,
    window_days: int,
    anchor_rule_for_symbol: Callable[[str], str],
) -> Dict[str, Any]:
    """Порахувати всі виміри для одного символу по кожному TF з allowlist.

    ``anchor_rule_for_symbol`` — резолвер правила якоря H4/D1 (``htf_anchor_rule_resolver``, ADR-0095),
    збудований раз на прогін.
    """
    calendars, rejected = resolve_symbol_calendars(cfg, [symbol], where="symbol_health_check")
    if rejected:
        return {"symbol": symbol, "grade": "RED", "reasons": ["calendar_group_missing"], "tfs": {}}
    try:
        rule = anchor_rule_for_symbol(symbol)
    except ValueError as exc:
        # Група календаря без виміряної сітки H4/D1 (ADR-0095 §8.4): міряти нема чим — не тихий якір.
        _log.error("HEALTH_HTF_ANCHOR_RULE_MISSING symbol=%s err=%s", symbol, exc)
        return {"symbol": symbol, "grade": "RED", "reasons": ["htf_anchor_rule_missing"], "tfs": {}}
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

        age = measure_age(opens, now_ms=now_ms, tf_s=tf_s, rule=rule, is_trading_fn=is_trading)
        holes = measure_holes(
            opens, start_ms=window_start, end_ms=now_ms, tf_s=tf_s, rule=rule, is_trading_fn=is_trading,
        )
        geometry = measure_geometry(bars, tf_s=tf_s, rule=rule)
        depth = measure_depth(opens, required_bars=REQUIRED_BARS_BY_TF.get(tf_s, 0))

        cascade = None
        source_tf = DERIVE_SOURCE.get(tf_s, (None, None))[0]
        if source_tf and bars and bars_by_tf.get(source_tf):
            cascade = measure_cascade(
                bars, bars_by_tf[source_tf],
                target_tf_s=tf_s, source_tf_s=source_tf, rule=rule,
                declares_partial_fn=_declares_partial,
            )

        # Корінь ланцюга: кожен derived-бар проти M1 у своєму бакеті (ADR-0094 P4). Каскад вище
        # бачить лише сусідній рівень і не помітить, якщо застарів цілий ланцюжок разом.
        root = None
        if tf_s != 60 and bars and bars_by_tf.get(60):
            root = measure_root_consistency(
                bars, bars_by_tf[60], tf_s=tf_s, rule=rule, declares_partial_fn=_declares_partial,
            )

        grade = grade_symbol_tf(age=age, holes=holes, geometry=geometry, cascade=cascade, root=root, depth=depth)
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
                "off_season_grid": geometry.off_season_grid,
                "off_season_grid_samples": [
                    {"open": _iso(open_ms), "expected_open": _iso(expected_ms), "season": season_label(open_ms, rule),
                     "open_ms": open_ms, "expected_open_ms": expected_ms}
                    for open_ms, expected_ms in geometry.off_season_grid_samples
                ],
            },
            "cascade": (
                None if cascade is None
                else {"checked": cascade.checked, "mismatched": cascade.mismatched,
                      "declared_partial": cascade.declared_partial,
                      "skipped_incomplete": cascade.skipped_incomplete}
            ),
            "root": (
                None if root is None
                else {"checked": root.checked, "mismatched": root.mismatched,
                      "declared_partial": root.declared_partial, "uncovered": root.uncovered}
            ),
        }

    d1_bars = bars_by_tf.get(86400)
    anchor_ok = check_anchor_on_session_edge(
        max(b.open_time_ms for b in d1_bars), tf_ms=86_400_000, is_trading_fn=is_trading,
    ) if d1_bars else None
    return {
        "symbol": symbol, "grade": worst, "htf_anchor_rule": rule,
        "d1_anchor_on_session_edge": anchor_ok, "tfs": tfs,
    }


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
    anchor_rule_for_symbol = htf_anchor_rule_resolver(cfg)
    report = {
        "generated_at": _iso(now_ms),
        "measure_version": HEALTH_MEASURE_VERSION,
        "window_days": args.days,
        "symbols": {
            sym: check_symbol(
                cfg, sym, data_root=data_root, now_ms=now_ms, window_days=args.days,
                anchor_rule_for_symbol=anchor_rule_for_symbol,
            )
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
            root = d.get("root")
            root_txt = (
                "-" if root is None
                else f"{root['checked']}/{root['mismatched']}(+{root['declared_partial']}p,{root['uncovered']} без M1)"
            )
            print(
                f"  [{flag}] tf_{tf:<6} bars={d['bars']:<7} {d['first']} .. {d['last']}"
                f"  age={d['age_buckets']} holes={d['holes']['missing']}/{d['holes']['expected']}"
                f" cascade(chk/мовчазних+позначених)={casc_txt} root={root_txt}"
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
        if not cmp_res.verdicts_comparable:
            print("  !!! BASELINE ЗНЯТО ІНШОЮ ВЕРСІЄЮ ВИМІРУ (v%d, зараз v%d): вердикти не порівнювались, "
                  "лише числові виміри. Це НЕ сигнал до відкату — перезніміть baseline поточним інструментом."
                  % cmp_res.measure_versions)
        if cmp_res.grid_skipped_measures:
            print("  H4/D1 через межу v3 (інша сітка) НЕ порівнювались: %s; решта чисел H4/D1 (бари, дублікати, "
                  "close, OHLC) — порівнювались." % ", ".join(cmp_res.grid_skipped_measures))
        print("  перевірено символів: %s" % (", ".join(cmp_res.compared_symbols) or "-"))
        if cmp_res.new_symbols:
            print("  нових (не в baseline, не перевіряються): %s" % ", ".join(cmp_res.new_symbols))
        for reg in cmp_res.missing_symbols:
            print("  ЗНИК символ: %s" % reg)
        for reg in cmp_res.improvements:
            print("  краще: %s" % reg.describe())
        for reg in cmp_res.regressions:
            print("  РЕГРЕСІЯ: %s" % reg.describe())
        if not cmp_res.ok:
            print("  => РЕГРЕСІЙ: %d" % (len(cmp_res.regressions) + len(cmp_res.missing_symbols)))
            rc = 1
        elif not cmp_res.verdicts_comparable:
            # Окремий код: rc=1 за рунбуком означає відкат, а тут відкочувати нічого — треба новий baseline.
            print("  => числових регресій немає, але baseline непорівнюваний (rc=3)")
            rc = 3
        else:
            print("  => регресій немає")

    if args.gate and any(r["grade"] != "GREEN" for r in report["symbols"].values()):
        rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
