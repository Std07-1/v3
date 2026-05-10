"""Аудит: класифікація gap-ів M5 (expected_closed vs unexpected_open_gap).

Використання:
    python -m tools.diag.classify_m5_gaps --symbol GER30 --days 7
    python -m tools.diag.classify_m5_gaps --all --days 14

Для кожного символу сканує M5-файли за останні N днів, знаходить
missing M5 слоти і класифікує їх через MarketCalendar:
  - expected_closed  — ринок був закритий (break/weekend)
  - unexpected_gap   — ринок мав бути відкритий, але бару немає (дефект)
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

TF_M5_S = 300
TF_M5_MS = 300_000


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
    """Побудувати календар для символу з конфігу."""
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


def _parse_outage_ts(s):
    # type: (str) -> int
    """Парсинг ISO UTC → epoch ms."""
    s = s.replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except Exception:
        d = dt.datetime.strptime(s.split("+")[0], "%Y-%m-%dT%H:%M:%S")
        d = d.replace(tzinfo=dt.timezone.utc)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


def _load_known_outages(cfg, symbol):
    # type: (dict, str) -> List[Tuple[int, int]]
    """Повернути список (from_ms, to_ms) для відомих брокерських outage."""
    raw = cfg.get("known_broker_outages", [])
    intervals = []  # type: List[Tuple[int, int]]
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("symbol", "")) != symbol:
            continue
        try:
            from_ms = _parse_outage_ts(str(entry["from_utc"]))
            to_ms = _parse_outage_ts(str(entry["to_utc"]))
            intervals.append((from_ms, to_ms))
        except Exception:
            continue
    return intervals


def _is_in_known_outage(open_ms, outage_intervals):
    # type: (int, List[Tuple[int, int]]) -> bool
    for from_ms, to_ms in outage_intervals:
        if from_ms <= open_ms < to_ms:
            return True
    return False


def _symbol_dir_name(symbol):
    # type: (str) -> str
    return symbol.replace("/", "_")


def _load_m5_opens(data_root, symbol, start_ms, end_ms):
    # type: (str, str, int, int) -> set
    """Зібрати set(open_time_ms) з part-*.jsonl для M5."""
    opens = set()  # type: set
    sym_dir = os.path.join(data_root, _symbol_dir_name(symbol), "tf_%d" % TF_M5_S)
    if not os.path.isdir(sym_dir):
        return opens

    # Визначити потрібні day ключі
    day_start = dt.datetime.utcfromtimestamp(start_ms / 1000)
    day_end = dt.datetime.utcfromtimestamp(end_ms / 1000)
    d = day_start.date()
    end_d = day_end.date()
    while d <= end_d:
        day_key = d.strftime("%Y%m%d")
        path = os.path.join(sym_dir, "part-%s.jsonl" % day_key)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            ot = obj.get("open_time_ms")
                            if isinstance(ot, int) and start_ms <= ot <= end_ms:
                                opens.add(ot)
                        except Exception:
                            continue
            except Exception:
                pass
        d += dt.timedelta(days=1)
    return opens


def classify_gaps(
    data_root,   # type: str
    symbol,      # type: str
    calendar,    # type: MarketCalendar
    days,        # type: int
    known_outages=None,  # type: Optional[List[Tuple[int, int]]]
):
    # type: (...) -> Dict[str, Any]
    """Класифікувати missing M5 слоти за останні N днів."""
    if known_outages is None:
        known_outages = []
    now_ms = int(dt.datetime.utcnow().timestamp() * 1000)  # type: ignore[attr-defined]
    start_ms = now_ms - days * 24 * 3600 * 1000
    # Вирівняти на початок M5 бакета
    start_ms = (start_ms // TF_M5_MS) * TF_M5_MS
    end_ms = (now_ms // TF_M5_MS) * TF_M5_MS

    opens = _load_m5_opens(data_root, symbol, start_ms, end_ms)

    expected_closed = 0
    unexpected_gap = 0
    known_outage_count = 0
    total_slots = 0
    present = 0
    unexpected_intervals = []  # type: List[Tuple[int, int]]

    ot = start_ms
    while ot <= end_ms:
        total_slots += 1
        if ot in opens:
            present += 1
        else:
            if not calendar.is_trading_minute(ot):
                expected_closed += 1
            elif _is_in_known_outage(ot, known_outages):
                known_outage_count += 1
            else:
                unexpected_gap += 1
                # Групувати суміжні unexpected у інтервали
                if unexpected_intervals and unexpected_intervals[-1][1] == ot - TF_M5_MS:
                    unexpected_intervals[-1] = (unexpected_intervals[-1][0], ot)
                else:
                    unexpected_intervals.append((ot, ot))
        ot += TF_M5_MS

    effective_expected = total_slots - expected_closed - known_outage_count
    return {
        "symbol": symbol,
        "days": days,
        "total_slots": total_slots,
        "present": present,
        "expected_closed": expected_closed,
        "known_outage": known_outage_count,
        "unexpected_gap": unexpected_gap,
        "coverage_pct": round(100.0 * present / max(1, effective_expected), 2),
        "unexpected_intervals_count": len(unexpected_intervals),
        "unexpected_intervals_sample": [
            {
                "from": dt.datetime.utcfromtimestamp(iv[0] / 1000).strftime("%Y-%m-%dT%H:%MZ"),
                "to": dt.datetime.utcfromtimestamp(iv[1] / 1000).strftime("%Y-%m-%dT%H:%MZ"),
                "bars": (iv[1] - iv[0]) // TF_M5_MS + 1,
            }
            for iv in unexpected_intervals[:20]
        ],
    }


def main():
    # type: () -> int
    ap = argparse.ArgumentParser(
        description="Класифікація M5 gap-ів: expected_closed vs unexpected_gap.",
    )
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--symbol", default=None, help="Один символ для аналізу")
    ap.add_argument("--all", action="store_true", help="Усі symbols[] з конфігу")
    ap.add_argument("--days", type=int, default=7, help="Кількість днів назад (default=7)")
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
        result = classify_gaps(data_root, sym, cal, args.days, known_outages=outages)
        all_results.append(result)

        status = "OK" if result["unexpected_gap"] == 0 else "GAPS"
        if result["unexpected_gap"] > 0:
            has_unexpected = True
        known_str = ""
        if result.get("known_outage", 0) > 0:
            known_str = " known_outage=%d" % result["known_outage"]
        logging.info(
            "%s: %s | present=%d expected_closed=%d unexpected=%d%s coverage=%.1f%%",
            sym, status, result["present"], result["expected_closed"],
            result["unexpected_gap"], known_str, result["coverage_pct"],
        )
        if result["unexpected_intervals_sample"]:
            for iv in result["unexpected_intervals_sample"][:5]:
                logging.info(
                    "  gap: %s → %s (%d bars)",
                    iv["from"], iv["to"], iv["bars"],
                )

    # Підсумок
    total_unexpected = sum(r["unexpected_gap"] for r in all_results)
    total_expected = sum(r["expected_closed"] for r in all_results)
    total_known = sum(r.get("known_outage", 0) for r in all_results)
    logging.info(
        "=== ПІДСУМОК: %d символів, expected_closed=%d, known_outage=%d, unexpected_gap=%d ===",
        len(all_results), total_expected, total_known, total_unexpected,
    )

    return 1 if has_unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
