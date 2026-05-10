"""Аудит: класифікація gap-ів H1 (derived з M5).

Використання:
    python -m tools.diag.classify_h1_gaps --all --days 18

Для кожного символу сканує H1-файли, знаходить missing H1 слоти
і класифікує їх:
  - expected_closed         — вся година в break/weekend (0 M5)
  - partial_built           — partial бар побудований (break перетинає годину, бар є)
  - partial_no_bar          — partial можливий, але бар ще не побудований
  - known_outage            — хоч один M5 у відомому broker outage
  - unexpected_gap          — вся година торгова, бар має бути, але немає
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys

from core.config_loader import load_system_config
from runtime.ingest.market_calendar import MarketCalendar

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

TF_H1_S = 3600
TF_H1_MS = 3600_000
TF_M5_MS = 300_000
M5_PER_H1 = 12


def _calendar_from_group(group_cfg):
    # type: (dict) -> Optional[MarketCalendar]
    try:
        daily_breaks_raw = group_cfg.get("market_daily_breaks", [])
        daily_breaks = tuple(
            (str(pair[0]), str(pair[1]))
            for pair in daily_breaks_raw
            if isinstance(pair, (list, tuple)) and len(pair) >= 2
        )
        return MarketCalendar(
            enabled=True,
            weekend_close_dow=int(group_cfg["market_weekend_close_dow"]),
            weekend_close_hm=str(group_cfg["market_weekend_close_hm"]),
            weekend_open_dow=int(group_cfg["market_weekend_open_dow"]),
            weekend_open_hm=str(group_cfg["market_weekend_open_hm"]),
            daily_break_start_hm=str(group_cfg["market_daily_break_start_hm"]),
            daily_break_end_hm=str(group_cfg["market_daily_break_end_hm"]),
            daily_break_enabled=True,
            daily_breaks=daily_breaks,
        )
    except Exception:
        return None


def _build_calendar(cfg, symbol):
    # type: (dict, str) -> Optional[MarketCalendar]
    groups = cfg.get("market_calendar_by_group", {})
    sym_groups = cfg.get("market_calendar_symbol_groups", {})
    group_name = sym_groups.get(symbol)
    if not group_name:
        return None
    group_cfg = groups.get(group_name)
    if not isinstance(group_cfg, dict):
        return None
    return _calendar_from_group(group_cfg)


def _symbols_from_config(cfg):
    # type: (dict) -> List[str]
    raw = cfg.get("symbols", [])
    if isinstance(raw, list) and raw:
        return [str(s) for s in raw if str(s).strip()]
    sym = cfg.get("symbol", "")
    return [str(sym)] if sym else []


def _load_known_outages(cfg, symbol):
    # type: (dict, str) -> List[Tuple[int, int]]
    raw = cfg.get("known_broker_outages", [])
    intervals = []  # type: List[Tuple[int, int]]
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("symbol", "")) != symbol:
            continue
        try:
            f_str = str(entry["from_utc"]).replace("Z", "+00:00")
            t_str = str(entry["to_utc"]).replace("Z", "+00:00")
            f = dt.datetime.fromisoformat(f_str)
            t = dt.datetime.fromisoformat(t_str)
            intervals.append((int(f.timestamp() * 1000), int(t.timestamp() * 1000)))
        except Exception:
            continue
    return intervals


def _load_h1_opens(data_root, symbol, start_ms, end_ms):
    # type: (str, str, int, int) -> set
    opens = set()  # type: set
    sym_dir = os.path.join(data_root, symbol.replace("/", "_"), "tf_%d" % TF_H1_S)
    if not os.path.isdir(sym_dir):
        return opens
    for fn in os.listdir(sym_dir):
        if not (fn.startswith("part-") and fn.endswith(".jsonl")):
            continue
        try:
            with open(os.path.join(sym_dir, fn), "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ot = json.loads(line).get("open_time_ms")
                        if isinstance(ot, int) and start_ms <= ot <= end_ms:
                            opens.add(ot)
                    except Exception:
                        continue
        except Exception:
            pass
    return opens


def _classify_hour(ot_ms, cal, outages):
    # type: (int, MarketCalendar, List[Tuple[int, int]]) -> str
    """Класифікувати H1 слот у якому відсутній бар.

    Повертає категорію missing:
      - expected_closed: 0 торгових M5 (вся година в break/weekend)
      - partial_expected: є хоча б 1 торговий M5, а всі missing — calendar/outage
      - known_outage: хоч один M5 у broker outage
      - expected_present: повна торгова година — бар мав існувати
    """
    has_break = False
    has_outage = False
    trading_count = 0

    for i in range(M5_PER_H1):
        m5_ms = ot_ms + i * TF_M5_MS
        is_trading = cal.is_trading_minute(m5_ms)
        in_outage = any(f <= m5_ms < t for f, t in outages)
        if is_trading and not in_outage:
            trading_count += 1
        if not is_trading:
            has_break = True
        if in_outage:
            has_outage = True

    if trading_count == 0:
        return "known_outage" if has_outage else "expected_closed"

    if has_break or has_outage:
        return "known_outage" if has_outage else "partial_expected"

    return "expected_present"


def classify_h1_gaps(data_root, symbol, calendar, days, known_outages=None):
    # type: (str, str, MarketCalendar, int, Optional[List[Tuple[int, int]]]) -> Dict[str, Any]
    if known_outages is None:
        known_outages = []
    now_ms = int(dt.datetime.utcnow().timestamp() * 1000)
    start_ms = now_ms - days * 24 * 3600 * 1000
    start_ms = (start_ms // TF_H1_MS) * TF_H1_MS
    end_ms = (now_ms // TF_H1_MS) * TF_H1_MS

    opens = _load_h1_opens(data_root, symbol, start_ms, end_ms)

    expected_closed = 0
    known_outage = 0
    partial_built = 0
    partial_no_bar = 0
    unexpected_gap = 0
    present = 0
    total_slots = 0

    ot = start_ms
    while ot <= end_ms:
        total_slots += 1
        cls = _classify_hour(ot, calendar, known_outages)
        if ot in opens:
            if cls == "partial_expected":
                partial_built += 1
            else:
                present += 1
        elif cls == "expected_closed":
            expected_closed += 1
        elif cls == "known_outage":
            known_outage += 1
        elif cls == "partial_expected":
            partial_no_bar += 1
        else:  # expected_present
            unexpected_gap += 1
        ot += TF_H1_MS

    effective = total_slots - expected_closed - known_outage
    return {
        "symbol": symbol,
        "days": days,
        "total_slots": total_slots,
        "present": present,
        "expected_closed": expected_closed,
        "known_outage": known_outage,
        "partial_built": partial_built,
        "partial_no_bar": partial_no_bar,
        "unexpected_gap": unexpected_gap,
        "coverage_pct": round(100.0 * (present + partial_built) / max(1, effective), 2),
    }


def main():
    # type: () -> int
    ap = argparse.ArgumentParser(
        description="Класифікація H1 gap-ів (derived з M5).",
    )
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--days", type=int, default=18)
    args = ap.parse_args()

    cfg = load_system_config(args.config)
    data_root = str(cfg.get("data_root", "./data_v3"))

    if getattr(args, "all", False):
        sym_list = _symbols_from_config(cfg)
    elif args.symbol:
        sym_list = [args.symbol]
    else:
        sym_list = _symbols_from_config(cfg)

    if not sym_list:
        logging.error("Порожній список символів")
        return 2

    all_results = []
    has_unexpected = False

    for sym in sym_list:
        cal = _build_calendar(cfg, sym)
        if cal is None:
            logging.warning("%s: календар не знайдено, пропуск", sym)
            continue
        outages = _load_known_outages(cfg, sym)
        result = classify_h1_gaps(data_root, sym, cal, args.days, known_outages=outages)
        all_results.append(result)

        status = "OK" if result["unexpected_gap"] == 0 else "GAPS"
        if result["unexpected_gap"] > 0:
            has_unexpected = True
        extras = ""
        if result["known_outage"]:
            extras += " known_outage=%d" % result["known_outage"]
        if result["partial_built"]:
            extras += " partial_built=%d" % result["partial_built"]
        if result["partial_no_bar"]:
            extras += " partial_no_bar=%d" % result["partial_no_bar"]
        logging.info(
            "%s: %s | present=%d expected_closed=%d unexpected=%d%s coverage=%.1f%%",
            sym, status, result["present"], result["expected_closed"],
            result["unexpected_gap"], extras, result["coverage_pct"],
        )

    total_unexpected = sum(r["unexpected_gap"] for r in all_results)
    total_known = sum(r["known_outage"] for r in all_results)
    total_partial_built = sum(r["partial_built"] for r in all_results)
    total_partial_no = sum(r["partial_no_bar"] for r in all_results)
    logging.info(
        "=== ПІДСУМОК: %d символів, known_outage=%d, partial_built=%d, partial_no_bar=%d, unexpected_gap=%d ===",
        len(all_results), total_known, total_partial_built, total_partial_no, total_unexpected,
    )

    return 1 if has_unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
