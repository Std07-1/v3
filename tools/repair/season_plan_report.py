"""tools/repair/season_plan_report.py — звіт dry-run плану S7 (ADR-0095 S7.1, формат MIGRATION §5).

Таблиця за символом і TF (набір, рядки до/після, незмінені, замінені, додані, поза сіткою, бакети без бару без
торгових хвилин і без джерела, дублікати, partial, формуючий хвіст, файли), зрізи сезонної сітки H4, D1 re-key з
рівністю OHLCV, MANUAL_REVIEW, діри й поза областю, межі джерела і підсумок. Числа — з того самого плану, що йде в
JSON (`SeasonPlan.to_json`).
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from core.session_anchor import D1_S, H4_S, htf_anchor_offset_s
from tools.rebuild_from_m1 import _tf_label
from tools.repair.season_plan import (
    ALL_TIME, ROW_ADDED, ROW_DUPLICATE, ROW_OFF_GRID, ROW_OLD, ROW_REPLACED, ROW_UNCHANGED, SeasonPlan, SymbolPlan,
)

_TABLE_HEADER = ("SYM", "TF", "REBUILD", "OLD", "NEW", "SAME", "REPL", "ADD", "OFFGRID", "DROP_NT", "DROP_NS", "DUP",
                 "PARTIAL", "TAIL", "FILES")
_SAMPLES = 8  # скільки прикладів (діб, бакетів) у рядку звіту


def format_report(plan: SeasonPlan) -> str:
    """Текст звіту dry-run (ASCII: друк у консоль Windows не падає на кодуванні)."""
    lines = ["S7_PLAN scopes=%s git_rev=%s data_root=%s window=%s changed_m1=%s calendar=calendar_for_symbol" % (
        ",".join(plan.scopes), plan.git_rev, plan.data_root, _window_text(plan.window), "yes" if plan.changed_m1 else "no")]
    lines.append(_row(_TABLE_HEADER))
    for symbol_plan in plan.symbols:
        lines.extend(_row(cells) for cells in _tf_rows(symbol_plan))
    for symbol_plan in plan.symbols:
        lines.extend(_symbol_notes(symbol_plan))
    files = plan.files
    lines.append("TOTAL files=%d created=%d emptied=%d eol_added=%d rows: remove %d add %d" % (
        len(files), sum(1 for f in files if not f.src_exists), sum(1 for f in files if f.src_size and not f.new_bytes),
        sum(1 for f in files if f.eol_added), sum(len(f.removed_keys) for f in files), sum(len(f.added_keys) for f in files)))
    return "\n".join(lines)


def _tf_rows(symbol_plan: SymbolPlan) -> List[Tuple[Any, ...]]:
    ctx = symbol_plan.context
    files_by_tf = Counter(file_plan.tf_s for file_plan in symbol_plan.files)
    out = []
    for tf_s in sorted(set(symbol_plan.rebuild) | set(symbol_plan.rows)):
        built = [bar for bar in symbol_plan.planned.get(tf_s, {}).values() if bar is not None]
        rows = symbol_plan.rows.get(tf_s, Counter())
        no_trading, no_source = symbol_plan.dropped(tf_s)
        out.append((ctx.sym_dir, _tf_label(tf_s), len(symbol_plan.rebuild.get(tf_s, ())), rows[ROW_OLD], len(built),
                    rows[ROW_UNCHANGED], rows[ROW_REPLACED], rows[ROW_ADDED], rows[ROW_OFF_GRID], len(no_trading),
                    len(no_source), rows[ROW_DUPLICATE], sum(1 for bar in built if bar.extensions.get("partial")),
                    symbol_plan.tail_kept.get(tf_s, 0), files_by_tf[tf_s]))
    return out


def _symbol_notes(symbol_plan: SymbolPlan) -> List[str]:
    ctx = symbol_plan.context
    notes = []
    h4_keys = sorted(k for k, bar in symbol_plan.planned.get(H4_S, {}).items() if bar is not None)
    if h4_keys:
        slices = _runs([htf_anchor_offset_s(H4_S, k, ctx.rule) for k in h4_keys])
        notes.append("SLICES %s H4 %d[%s] anchors=%s" % (ctx.sym_dir, len(slices), "/".join(str(n) for _a, n in slices),
                                                       "/".join(str(a) for a, _n in slices)))
    rekey = symbol_plan.rekey_results()
    if rekey:
        thin = [_day(r["new_open_ms"]) for r in rekey if r["thin_session"]]
        notes.append("D1_REKEY %s %d->%d ohlcv_equal=%d/%d thin_session=%d(%s)" % (
            ctx.sym_dir, len(rekey), sum(1 for r in rekey if symbol_plan.planned.get(D1_S, {}).get(r["new_open_ms"])),
            sum(1 for r in rekey if r["ohlcv_equal"]), len(rekey), len(thin), ",".join(thin[:_SAMPLES])))
    if symbol_plan.manual_review:
        notes.append("MANUAL_REVIEW %s D1 off-grid outside M1 era=%d first=%s" % (
            ctx.sym_dir, len(symbol_plan.manual_review), _utc(symbol_plan.manual_review[0].open_time_ms)))
    if symbol_plan.holes or symbol_plan.holes_out_of_scope:
        notes.append("HOLES %s %s | OUT_OF_SCOPE %s" % (
            ctx.sym_dir, _per_tf(symbol_plan.holes), _per_tf(symbol_plan.holes_out_of_scope, with_days=True)))
    for tf_s in sorted(symbol_plan.planned):
        no_source = symbol_plan.dropped(tf_s)[1]
        if no_source:
            notes.append("DROP_NO_SOURCE %s %s=%d first=%s" % (ctx.sym_dir, _tf_label(tf_s), len(no_source),
                                                               ",".join(_utc(b) for b in no_source[:_SAMPLES])))
    notes.append("SOURCE %s rule=%s season_rule=%s m1=%s..%s source_end=%s rejected_rows=%d" % (
        ctx.sym_dir, ctx.rule, ctx.calendar.season_rule, _utc(ctx.m1_head_ms), _utc(ctx.m1_tail_ms),
        _utc(ctx.source_end_ms), symbol_plan.rejected_rows))
    return notes


def _runs(values: Sequence[int]) -> List[Tuple[int, int]]:
    """[(значення, довжина)] суцільних серій однакових значень."""
    runs: List[Tuple[int, int]] = []
    for value in values:
        if runs and runs[-1][0] == value:
            runs[-1] = (value, runs[-1][1] + 1)
        else:
            runs.append((value, 1))
    return runs


def _per_tf(buckets: Mapping[int, Any], with_days: bool = False) -> str:
    parts = []
    for tf_s in sorted(buckets):
        keys = sorted(buckets[tf_s])
        text = "%s=%d" % (_tf_label(tf_s), len(keys))
        if with_days and keys:
            text += "(%s)" % ",".join(sorted({_day(k) for k in keys})[:_SAMPLES])
        parts.append(text)
    return " ".join(parts) or "-"


def _row(cells: Sequence[Any]) -> str:
    return "%-8s %-4s" % tuple(cells[:2]) + "".join(" %7s" % cell for cell in cells[2:])


def _utc(ms: Optional[int]) -> str:
    return "-" if ms is None else dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")


def _day(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def _window_text(window: Tuple[int, int]) -> str:
    """Вікно плану: межа, що збігається з межею ALL_TIME, — відкрита (`*`)."""
    lo, hi = ("*" if bound == edge else _utc(bound) for bound, edge in zip(window, ALL_TIME))
    return "all" if window == ALL_TIME else "%s..%s" % (lo, hi)
