"""tools/repair/settle_chain.py — суцільний ланцюг settle M1 (ADR-0101 §3.1–3.2, ADR-0103 S2) над ціллю вікна.

Ті самі функції, що в живого полера й пакетних записувачів (D15.2): класифікатор ADR-0099, `fold_edge_stale`,
`chain_open_to_prev_close`, `hole_possible_between` із запізненням відкриття сесії групи.
  • вкладення: хвилина архіву, яку класифікатор називає застарілим краєм (перша хвилина паузи), вкладається в
    попередню хвилину сесії, як у TV; наш рядок цієї хвилини паузи вилучається (FOLD_STALE_ROW_REMOVED);
  • ланцюг: open := close попереднього видимого бару — від останнього видимого бару до вікна через ціль до першого
    видимого після вікна (CHAIN_SUCCESSOR). Лише між барами без можливої діри між ними: торгова хвилина без бару після
    вставки архіву — геп самого брокера або неповний архів; розрив там гучно CHAIN_GAP_SKIPPED, не вигадана свічка.
    Прихований бар брокера (тік вихідних) ланцюг не рве: TV H1 XAU нд 08.03.2026 22:00 o = close п'ятниці.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from core.model.bars import CandleBar
from core.model.candle_chain import hole_possible_between, open_breaks_chain
from runtime.ingest.m1_session_filter import (
    MARKER_LATE_TICKS_FOLDED,
    MARKER_OPEN_CHAINED,
    VERDICT_PAUSE_EDGE_STALE_DROPPED,
    chain_open_to_prev_close,
    fold_edge_stale,
)
from tools.repair.partfile_io import PartFile
from tools.repair.settle_gate import IsTrading, utc_label
from tools.repair.settle_rules import (
    M1_MS, Classify, KeyTarget, Occ, SettleTrace, occ_conflict, row_of, to_bar, vals_of,
)

_CHAINED_SAMPLES = 12  # приклади ланцюга в лозі; великі стрибки open (> 1.0) — завжди


@dataclass(frozen=True)
class SuccessorFix:
    """Правка першого видимого бару після вікна: його close-попередника змінила ціль вікна."""

    key: int
    occ: Occ
    vals: Tuple[float, float, float]  # o, h, low
    raw_o: float


def fold_edges(target: Dict[int, KeyTarget], archive: Dict[int, List[float]], ours: Dict[int, Occ], delete_rows: Occ,
               *, symbol: str, is_trading: IsTrading, classify: Classify, trace: SettleTrace) -> None:
    """Застарілий край архіву (вердикт edge_stale) — у попередню хвилину сесії; змінює `target` і `delete_rows`."""
    for key in sorted(archive):
        if is_trading(key) or not is_trading(key - M1_MS):
            continue
        _bar, verdict = classify(key, archive[key])
        if verdict != VERDICT_PAUSE_EDGE_STALE_DROPPED:
            continue
        prev = target.get(key - M1_MS)
        if prev is None or not prev.visible or prev.conflict:
            trace.note("FOLD_NO_PREV_MINUTE", [utc_label(key), archive[key]])
            continue
        if not prev.from_archive and MARKER_LATE_TICKS_FOLDED in prev.ext:
            trace.note("FOLD_ALREADY_IN_OURS")  # наш рядок уже з вкладеними тіками — вдруге не додаємо
            continue
        stale = to_bar(symbol, key, archive[key])
        folded = fold_edge_stale(to_bar(symbol, key - M1_MS, prev.vals), stale)
        trace.note("FOLDED", [utc_label(key - M1_MS), prev.vals[3], folded.c, stale.v])
        prev.vals = [folded.o, folded.h, folded.low, folded.c, folded.v]
        prev.markers[MARKER_LATE_TICKS_FOLDED] = stale.v
        if key in target:
            delete_rows.extend(ours.get(key, []))
            del target[key]
            trace.note("FOLD_STALE_ROW_REMOVED", utc_label(key))


def chain_step(prev_bar: Optional[CandleBar], bar: CandleBar, *, is_trading: IsTrading, grace_min: int,
               trace: SettleTrace) -> CandleBar:
    """Один крок ланцюга між сусідніми видимими барами; через можливу діру — без правки (гучно, якщо розрив)."""
    if prev_bar is None:
        return bar
    if hole_possible_between(prev_bar.open_time_ms, bar.open_time_ms, is_trading_fn=is_trading,
                             session_open_grace_min=grace_min):
        if open_breaks_chain(prev_bar.c, bar.o):
            trace.note("CHAIN_GAP_SKIPPED", [utc_label(prev_bar.open_time_ms), prev_bar.c, utc_label(bar.open_time_ms),
                                             bar.o])
        return bar
    fixed = chain_open_to_prev_close(prev_bar, bar)
    if fixed is not bar:
        sample = len(trace.log["CHAINED"]) < _CHAINED_SAMPLES or abs(fixed.o - bar.o) > 1.0
        trace.note("CHAINED", [utc_label(prev_bar.open_time_ms), prev_bar.c, utc_label(bar.open_time_ms), bar.o]
                   if sample else None)
    return fixed


def chain_keys(target: Dict[int, KeyTarget], files: Dict[str, PartFile], ctx_before: Optional[Tuple[int, Occ]],
               successor: Optional[Tuple[int, Occ]], *, symbol: str, is_trading: IsTrading, grace_min: int,
               trace: SettleTrace) -> Optional[SuccessorFix]:
    """Ланцюг через ціль вікна; змінює `target` (маркер open_chained_from = сирий open) і повертає правку наступника."""
    prev_bar = None
    if ctx_before is not None and not occ_conflict(files, ctx_before[1]):
        prev_bar = to_bar(symbol, ctx_before[0], vals_of(row_of(files, ctx_before[1])))
    for key in sorted(k for k, t in target.items() if t.visible):
        entry = target[key]
        if entry.conflict:
            trace.note("CHAIN_DUP_CONFLICT")
            prev_bar = None  # ланцюг через неоднозначний бар не тягнемо
            continue
        bar = to_bar(symbol, key, entry.vals)
        fixed = chain_step(prev_bar, bar, is_trading=is_trading, grace_min=grace_min, trace=trace)
        if fixed is not bar:
            entry.markers[MARKER_OPEN_CHAINED] = bar.o
            entry.vals[0:3] = [fixed.o, fixed.h, fixed.low]
        prev_bar = fixed
    if successor is None or prev_bar is None or occ_conflict(files, successor[1]):
        return None
    succ_key, succ_occ = successor
    succ_bar = to_bar(symbol, succ_key, vals_of(row_of(files, succ_occ)))
    fixed = chain_step(prev_bar, succ_bar, is_trading=is_trading, grace_min=grace_min, trace=trace)
    if fixed is succ_bar:
        return None
    trace.note("CHAIN_SUCCESSOR", [utc_label(prev_bar.open_time_ms), prev_bar.c, utc_label(succ_key), succ_bar.o])
    return SuccessorFix(key=succ_key, occ=succ_occ, vals=(fixed.o, fixed.h, fixed.low), raw_o=succ_bar.o)
