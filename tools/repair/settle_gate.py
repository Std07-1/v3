"""tools/repair/settle_gate.py — гейт архіву брокера перед settle M1 (ADR-0098 §3.8, ADR-0103 S2): чисті функції.

Settle переписує SSOT значеннями архіву, тож неповний архів тихо зіпсував би історію (ADR-0096 «тиха компенсація»).
Гейт відмовляє ДО будь-якого запису, якщо на символ:
  • чанк забору з помилкою містить торгову хвилину календаря (`errors_trading`);
  • покриття UTC-доби вікна гірше за базу: з `ours_keys` (перший забір усієї історії) — торгових барів архіву менше,
    ніж видимих торгових барів нашого SSOT, мінус `ours_tolerance`; з `baseline_keys` (попередній успішний забір) —
    барів менше, ніж тоді; без бази — got/calendar < `min_coverage`, а порожня торгова доба — відмова;
  • хвилини закриття тижня (торгова, після якої ≥ 24 год без торгів) немає в архіві. Якщо її немає й у нас — це раннє
    закриття свята (`week_close_holiday`), не відмова.
Календар — предикат хвилини `is_trading(ms)` сезонного календаря символу (ADR-0095 S6a), той самий, що в записувачів.
"""

from __future__ import annotations

import collections
import datetime as dt
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

M1_MS = 60_000
WEEK_CLOSE_GAP_MS = 24 * 3600 * 1000  # «закриття/відкриття тижня» — межа ≥ 24 год без торгових хвилин

IsTrading = Callable[[int], bool]


def utc_label(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%a %Y-%m-%d %H:%M")


def utc_day(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def has_trading(is_trading: IsTrading, from_ms: int, to_ms: int) -> bool:
    """У [from_ms, to_ms) є торгова хвилина календаря."""
    return any(is_trading(t) for t in range(from_ms - from_ms % M1_MS, to_ms, M1_MS))


def is_week_close_minute(is_trading: IsTrading, minute_ms: int) -> bool:
    """Торгова хвилина, після якої ≥ 24 год без торгів (Пт 20:44 для cfd_us_22_23)."""
    after = minute_ms + M1_MS
    return bool(is_trading(minute_ms)) and not has_trading(is_trading, after, after + WEEK_CLOSE_GAP_MS)


def last_week_open_ms(is_trading: IsTrading, at_ms: int) -> int:
    """Останнє відкриття тижня (торгова хвилина після ≥ 24 год без торгів) не пізніше `at_ms`: тиждень, якого архів
    ще не засвідчив (брокер ревізує свіжі бари), вилучення наших рядків не зачіпають."""
    t = at_ms - at_ms % M1_MS
    while not (is_trading(t) and not has_trading(is_trading, t - WEEK_CLOSE_GAP_MS, t)):
        t -= M1_MS
    return t


def coverage_by_day(keys: Iterable[int], is_trading: IsTrading, lo_ms: int, hi_ms: int) -> Dict[str, Dict[str, int]]:
    """UTC-доба вікна → {calendar: торгових хвилин календаря, got: ключів у вікні}."""
    out: Dict[str, Dict[str, int]] = {}
    for t in range(lo_ms - lo_ms % M1_MS, hi_ms, M1_MS):
        out.setdefault(utc_day(t), {"calendar": 0, "got": 0})["calendar"] += int(bool(is_trading(t)))
    for k in keys:
        if lo_ms <= k < hi_ms:
            out[utc_day(k)]["got"] += 1
    return out


def trading_by_day(keys: Iterable[int], is_trading: IsTrading) -> collections.Counter:
    return collections.Counter(utc_day(k) for k in keys if is_trading(k))


def archive_gate(
    chunks: Optional[List[Dict[str, Any]]],
    archive_keys: Set[int],
    is_trading: IsTrading,
    lo_ms: int,
    hi_ms: int,
    *,
    min_coverage: float,
    baseline_keys: Optional[Set[int]] = None,
    ours_keys: Optional[Set[int]] = None,
    ours_tolerance: int = 0,
    parse_iso: Callable[[str], int],
) -> Tuple[List[str], Dict[str, Any]]:
    """(проблеми, таблиця) гейта для символу; порожній список проблем — архів придатний для settle вікна.

    `chunks` — `meta.symbols[<SYM_DIR>].chunks` забору (None — meta без чанків, відмова); `parse_iso` — ISO UTC → мс.
    """
    problems: List[str] = []
    if chunks is None:
        problems.append("ARCHIVE_META_NO_CHUNKS")
    errors_trading = 0
    for rec in chunks or ():
        if "error" in rec and has_trading(is_trading, parse_iso(rec["start"]), parse_iso(rec["end"])):
            errors_trading += 1
            problems.append("ARCHIVE_CHUNK_ERROR_TRADING %s %s..%s %s"
                            % (rec.get("label"), rec["start"][:16], rec["end"][:16], rec["error"]))
    days = _coverage_table(archive_keys, is_trading, lo_ms, hi_ms, min_coverage, baseline_keys, ours_keys,
                           ours_tolerance)
    problems.extend("%s day=%s" % (row["verdict"], row["day"]) for row in days if row["verdict"] != "ok")
    holiday_closes: List[str] = []
    for t in range(lo_ms - lo_ms % M1_MS, hi_ms, M1_MS):
        if t in archive_keys or not is_week_close_minute(is_trading, t):
            continue
        if ours_keys is not None and t not in ours_keys:
            holiday_closes.append(utc_label(t))  # немає ні в брокера, ні в нас — раннє закриття свята
        else:
            problems.append("WEEK_CLOSE_MINUTE_MISSING %s" % utc_label(t))
    return problems, {"errors_trading": errors_trading, "days": days, "week_close_holiday": holiday_closes}


def _coverage_table(archive_keys, is_trading, lo_ms, hi_ms, min_coverage, baseline_keys, ours_keys, ours_tolerance):
    coverage = coverage_by_day(archive_keys, is_trading, lo_ms, hi_ms)
    base_cov = coverage_by_day(baseline_keys, is_trading, lo_ms, hi_ms) if baseline_keys is not None else {}
    arc_trading = trading_by_day(archive_keys, is_trading) if ours_keys is not None else {}
    ours_trading = trading_by_day(ours_keys, is_trading) if ours_keys is not None else None
    table = []
    for day, cov in sorted(coverage.items()):
        if not cov["calendar"]:
            continue
        base_got = (base_cov.get(day) or {}).get("got") or None
        ratio = cov["got"] / cov["calendar"]
        row: Dict[str, Any] = {"day": day, "calendar": cov["calendar"], "got": cov["got"], "ratio": round(ratio, 4),
                               "baseline_got": base_got}
        verdict = "ok"
        if ours_trading is not None:
            got_t, ours_t = arc_trading.get(day, 0), ours_trading.get(day, 0)
            row.update(archive_trading=got_t, ours_trading=ours_t)
            if got_t < ours_t - ours_tolerance:
                verdict = "COVERAGE_BELOW_OURS archive_trading=%d ours_trading=%d tol=%d" % (got_t, ours_t, ours_tolerance)
        elif not cov["got"]:
            verdict = "EMPTY_TRADING_DAY"
        elif base_got is not None and cov["got"] < base_got:
            verdict = "COVERAGE_BELOW_BASELINE got=%d baseline=%d" % (cov["got"], base_got)
        elif base_got is None and ratio < min_coverage:
            verdict = "COVERAGE_BELOW_MIN ratio=%.4f min=%s" % (ratio, min_coverage)
        row["verdict"] = verdict
        table.append(row)
    return table
