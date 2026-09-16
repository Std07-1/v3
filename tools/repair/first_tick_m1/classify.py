"""Чиста класифікація ключа M1: чи можна замінити o/h/low рядка-переможця значеннями FIRST_TICK (ADR-0096 §3.3 B).

Заміна дозволена лише коли доведено, що рядок staging — той самий бар: close збігся в межах eps, новий діапазон
лише звужує старий і лише там, де PREVIOUS_CLOSE його розтягнув (стара межа — або та сама, або дорівнює старому
open), фінальний бар геометрично цілий, а брокер віддав справжній перший тік. «Запечений» FIRST_TICK (§1.4) — o == close попередньої у 100% хвилин, частина open поза
[low, high]: такі рядки і весь їхній ланцюжок o == prev_c лишаються «не доведено» (SKIP_BAKED) і перезабираються
пізніше. Бар, що після заміни стане O=H=L=C з обсягом ≤ порогу candle_map, не переписується (SKIP_WOULD_HIDE):
кеш-бар Redis не несе extensions, тож на cold-load свічку сховало б навіть із маркером trading_flat. Перша умова,
що спрацювала, визначає категорію.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

from core.model.bar_choice import is_complete, is_final_source
from core.model.bars import CandleBar, normalize_ohlc
from runtime.ingest.broker.fxcm.provider import extract_ohlc
from runtime.ingest.polling.m1_poller import _is_flat
from runtime.ws.candle_map import is_display_flat_ohlcv
from tools.repair.first_tick_m1.common import (
    CLOSE_EPS_MAX, MINUTE_MS, SUSPECT_EQ_PREV_SHARE, SUSPECT_MIN_ROWS, TF_S, day_key, day_of_ms, sha256_bytes,
)
from tools.repair.first_tick_m1.ssot_part import Winner, detect_line_style

CATEGORIES = ("REPLACE", "SAME", "SKIP_BAKED", "SKIP_CLOSE_MISMATCH", "SKIP_RANGE_EXPANDS",
              "SKIP_RANGE_CHANGED_BEYOND_STRETCH", "SKIP_V_DIFFERS", "SKIP_FLAT_NON_TRADING", "SKIP_WOULD_HIDE",
              "SKIP_WINNER_INELIGIBLE", "MISSING_IN_STAGING", "EXTRA_IN_STAGING")
OHLCV = ("o", "h", "low", "c", "v")


class PlanInvariantBroken(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class ClassifyContext:
    calendar: Any
    close_eps: float
    baked_keys: FrozenSet[int]
    suspect_days: FrozenSet[str]

    def __post_init__(self) -> None:
        if not 0 < self.close_eps <= CLOSE_EPS_MAX:
            raise ValueError("FT_CLOSE_EPS_OUT_OF_RANGE close_eps=%r max=%r" % (self.close_eps, CLOSE_EPS_MAX))


@dataclasses.dataclass(frozen=True)
class BakedScan:
    baked_keys: FrozenSet[int]
    runs: Tuple[Dict[str, int], ...]
    edge_runs: Tuple[Dict[str, int], ...]
    eq_prev_share: Dict[str, float]
    suspect_days: FrozenSet[str]


def baked_scan(days: Sequence[Tuple[str, Optional[Sequence[Dict[str, Any]]], bool]], close_eps: float) -> BakedScan:
    """Ланцюжок рядків staging за днями `(day, rows|None, has_part)` у хронологічному порядку.

    Доба з part-файлом без staging розриває ланцюжок (її рядків не знаємо); доба без обох — прозора (субота).
    eq_prev = попередній рядок ланцюжка існує і |o − prev_c| ≤ eps. Run — максимальна послідовність eq_prev
    разом з рядком-якорем перед нею (його close і є їхній open); «запечений», якщо хоч один рядок run-а має open
    поза [low, high]. Якір входить, бо «запечений» рядок на межі контексту (Нд 23:59 без п'ятниці) сам eq_prev
    не має, а ранкові хвилини понеділка тягнуть саме його close.
    """
    chain: List[Tuple[Dict[str, Any], bool, str]] = []
    segment_starts, previous = {0}, None
    for key, rows, has_part in days:
        if rows is None:
            if has_part and previous is not None:
                previous = None
                segment_starts.add(len(chain))
            continue
        for row in rows:
            eq_prev = previous is not None and abs(extract_ohlc(row)[0] - extract_ohlc(previous)[3]) <= close_eps
            chain.append((row, eq_prev, key))
            previous = row
    baked_keys, runs, edge_runs = set(), [], []
    index = 0
    while index < len(chain):
        if not chain[index][1]:
            index += 1
            continue
        end = index
        while end + 1 < len(chain) and chain[end + 1][1]:
            end += 1
        anchor = index - 1  # eq_prev гарантує попередника в тому самому сегменті
        members = [chain[i][0] for i in range(anchor, end + 1)]
        outside = sum(1 for row in members if row["raw_open_not_tick"])
        if outside:
            run = {"first_open_ms": members[0]["open_time_ms"], "last_open_ms": members[-1]["open_time_ms"],
                   "rows": len(members), "open_outside_range_rows": outside}
            runs.append(run)
            baked_keys.update(row["open_time_ms"] for row in members)
            if anchor in segment_starts or end + 1 == len(chain) or end + 1 in segment_starts:
                edge_runs.append(run)
        index = end + 1
    share = _eq_prev_share(chain, segment_starts)
    suspect = frozenset(day for day, (eq, total) in share.items()
                        if total >= SUSPECT_MIN_ROWS and eq / total >= SUSPECT_EQ_PREV_SHARE)
    return BakedScan(frozenset(baked_keys), tuple(runs), tuple(edge_runs),
                     {day: round(eq / total, 6) for day, (eq, total) in share.items()}, suspect)


def classify_key(winner: Winner, staged: Optional[Dict[str, Any]], ctx: ClassifyContext) -> Dict[str, Any]:
    bar, key = winner.bar, winner.open_ms
    base = {"k": key, "line": winner.line_index, "line_sha256": sha256_bytes(winner.line.encode("utf-8")),
            "members": winner.members}
    evidence = dict(base)
    if all(_is_number(bar.get(field)) for field in OHLCV):
        evidence["old"] = {field: bar[field] for field in OHLCV}
    if staged is not None:
        evidence["staged"] = staged_values(staged)
    reason = ineligible_reason(winner)
    if reason:
        return dict(evidence, cat="SKIP_WINNER_INELIGIBLE", reason=reason)
    if staged is None:
        return dict(base, cat="MISSING_IN_STAGING")
    if staged["raw_open_not_tick"] or key in ctx.baked_keys:
        return dict(evidence, cat="SKIP_BAKED",
                    reason="open_outside_range" if staged["raw_open_not_tick"] else "baked_run")
    o_s, h_s, l_s, c_s = extract_ohlc(staged)
    old_c = float(bar["c"])
    if abs(c_s - old_c) > ctx.close_eps:
        return dict(evidence, cat="SKIP_CLOSE_MISMATCH", reason="close_delta", delta=c_s - old_c)
    new_o, new_h, new_low, _close = normalize_ohlc(o_s, h_s, l_s, c_s)
    if new_h > float(bar["h"]) or new_low < float(bar["low"]):
        return dict(evidence, cat="SKIP_RANGE_EXPANDS", reason="range_expands",
                    normalized={"o": new_o, "h": new_h, "low": new_low})
    if not (new_low <= min(new_o, old_c) and max(new_o, old_c) <= new_h):
        return dict(evidence, cat="SKIP_CLOSE_MISMATCH", reason="close_outside_new_range")
    beyond = range_change_beyond_stretch(bar, new_h, new_low, ctx.close_eps)
    if beyond:
        return dict(evidence, cat="SKIP_RANGE_CHANGED_BEYOND_STRETCH", reason=beyond,
                    normalized={"o": new_o, "h": new_h, "low": new_low})
    v_differs = float(bar["v"]) != float(staged["Volume"])
    if (new_o, new_h, new_low) == (float(bar["o"]), float(bar["h"]), float(bar["low"])):
        same = dict(base, cat="SAME", v_differs=v_differs)
        return dict(same, suspect=True) if day_key(day_of_ms(key)) in ctx.suspect_days else same
    if v_differs:
        # Інший tick volume у тій самій хвилині = інший набір тіків, тобто ІНША витяжка брокера. Ремонт value-only
        # не заміняє v, тож заміна o/h/low дала б бар, якого не було ні в одній версії даних. Той самий висновок
        # інструмент уже робить механічною категорією SKIP_RANGE_CHANGED_BEYOND_STRETCH — тут він мусить бути теж.
        return dict(evidence, cat="SKIP_V_DIFFERS", reason="volume_from_other_extraction",
                    normalized={"o": new_o, "h": new_h, "low": new_low})
    after = CandleBar(bar["symbol"], TF_S, key, key + MINUTE_MS, new_o, new_h, new_low, old_c, float(bar["v"]), True,
                      bar["src"], {})
    flat, trading = _is_flat(after), ctx.calendar.is_trading_minute(key)
    if flat and not trading:
        return dict(evidence, cat="SKIP_FLAT_NON_TRADING", reason="flat_in_calendar_pause")
    extensions = bar.get("extensions") or {}
    if extensions.get("trading_flat") and not flat:
        raise PlanInvariantBroken("PLAN_INVARIANT_BROKEN k=%d: trading_flat бар став не пласким" % key)
    if is_display_flat_ohlcv(new_o, new_h, new_low, old_c, float(bar["v"])):
        # Redis cold-load віддає кеш-бар без extensions: candle_map сховав би O=H=L=C з малим обсягом навіть із
        # trading_flat на диску. До окремого патча межі Redis (ADR-0096 §3.3 B) такий ключ лишається PREV.
        return dict(evidence, cat="SKIP_WOULD_HIDE", reason="display_flat_without_extensions",
                    normalized={"o": new_o, "h": new_h, "low": new_low})
    if day_key(day_of_ms(key)) in ctx.suspect_days:
        # Доба з ≥90% o == prev_c — «запечена» за часткою: заміна тут не доведена, перезабір пізніше.
        return dict(evidence, cat="SKIP_BAKED", reason="suspect_day")
    return dict(evidence, cat="REPLACE", new={"o": new_o, "h": new_h, "low": new_low},
                trading_flat_add=bool(flat and trading and not extensions.get("trading_flat")), v_differs=v_differs)


def range_change_beyond_stretch(bar: Dict[str, Any], new_h: float, new_low: float, eps: float) -> Optional[str]:
    """Чому зміна меж — не зняття розтягування PREVIOUS_CLOSE (None — лише зняття).

    PREVIOUS_CLOSE брав open = close попередньої і розтягував до нього лише ту межу, за яку той вийшов (§1.3).
    Отже кожна стара межа — або справжня (== нова в межах eps), або сам старий open. Звужена межа, що не
    дорівнювала старому open, означає інші тіки в барі — інша версія даних, а не ремонт open.
    """
    old_o, old_h, old_low = float(bar["o"]), float(bar["h"]), float(bar["low"])
    if abs(new_h - old_h) > eps and abs(old_h - old_o) > eps:
        return "high_changed_not_stretched"
    if abs(new_low - old_low) > eps and abs(old_low - old_o) > eps:
        return "low_changed_not_stretched"
    return None


def extra_entry(staged: Dict[str, Any]) -> Dict[str, Any]:
    """Рядок staging без ключа в part-файлі — лише звіт; нових ключів ремонт не додає."""
    return {"cat": "EXTRA_IN_STAGING", "k": staged["open_time_ms"], "staged": staged_values(staged)}


def ineligible_reason(winner: Winner) -> Optional[str]:
    """Переможець, якого ремонт не має права чіпати: не в PRIME-погляді, нечислові OHLCV або невідомий стиль рядка."""
    bar = winner.bar
    if not is_complete(bar):
        return "not_complete"
    if not is_final_source(bar):
        return "not_final_source"
    if not all(_is_number(bar.get(field)) for field in OHLCV):
        return "ohlcv_invalid"
    if "extensions" in bar and not isinstance(bar["extensions"], dict):
        return "extensions_not_object"
    if detect_line_style(winner.line) is None:
        return "line_style_unknown"
    return None


def staged_values(staged: Dict[str, Any]) -> Dict[str, Any]:
    o, h, low, c = extract_ohlc(staged)
    return {"o": o, "h": h, "low": low, "c": c, "v": staged["Volume"], "raw_open_not_tick": staged["raw_open_not_tick"]}


def _eq_prev_share(chain: Sequence[Tuple[Dict[str, Any], bool, str]], segment_starts: Any) -> Dict[str, Tuple[int, int]]:
    counts: Dict[str, List[int]] = {}
    for index, (_row, eq_prev, day) in enumerate(chain):
        if index in segment_starts:
            continue  # без попередника частка не визначена
        bucket = counts.setdefault(day, [0, 0])
        bucket[0] += int(eq_prev)
        bucket[1] += 1
    return {day: (eq, total) for day, (eq, total) in counts.items()}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
