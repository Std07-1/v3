"""tools/repair/settle_plan.py — план settle M1 символу (ADR-0098 §3.3, ADR-0101, ADR-0103 S2): рядки вікна → дії на диску.

Порядок: рядки part-файлів вікна з тижнем контексту → правила ключів (`settle_rules`) → вкладення застарілого краю і
ланцюг (`settle_chain`) → дії: заміна рядків, чия ціль відрізняється (значення або extensions), вставка нових ключів,
вилучення. Порівняння з диском — по цілі, тож повторний прогін по записаному = 0 дій (VERIFY_REPLAN).
"""

from __future__ import annotations

import collections
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.model.bars import CandleBar
from tools.repair.partfile_io import PartFile, day_of_ms, list_part_days, load_part, part_path, row_bytes
from tools.repair.settle_chain import SuccessorFix, chain_keys, fold_edges
from tools.repair.settle_gate import IsTrading, utc_label
from tools.repair.settle_rules import (
    M1_MS, M1_S, Classify, KeyTarget, Occ, SettleTrace, decide_keys, is_visible, row_of, vals_of,
)

DAY_MS = 86_400_000
CONTEXT_MS = 7 * DAY_MS  # сусіди вікна для ланцюга: тиждень покриває вихідні
# Маркери ремонтів, які значення архіву скасовують (ADR-0096/0098), і маркери ADR-0101, що перераховуються з архіву
STRIPPED_MARKERS = ("session_open_rebuilt", "open_before", "high_before", "low_before", "open_provisional",
                    "open_unsettled", "close_suspect", "open_chained_from", "late_ticks_folded")
REPLACE_EXAMPLES = 8


@dataclass
class SymbolPlan:
    """Дії settle символу над part-файлами M1."""

    files: Dict[str, PartFile]
    replace: List[Tuple[str, int, int, Dict[str, Any]]] = field(default_factory=list)  # (шлях, рядок, ключ, об'єкт)
    insert: Dict[str, List[Tuple[int, bytes]]] = field(default_factory=lambda: collections.defaultdict(list))
    delete: Occ = field(default_factory=list)
    newline_fix: List[str] = field(default_factory=list)  # файли вікна без \n у кінці
    trace: SettleTrace = field(default_factory=SettleTrace)

    @property
    def actions(self) -> int:
        return len(self.replace) + sum(len(rows) for rows in self.insert.values()) + len(self.delete)

    def touched(self) -> List[str]:
        return sorted({p for p, _i, _k, _o in self.replace} | set(self.insert) | {p for p, _i in self.delete}
                      | set(self.newline_fix))

    def changed_keys(self) -> List[int]:
        """Ключі M1, які змінює план: вхід перебудови похідних (S7 --changed-m1)."""
        return sorted({k for _p, _i, k, _o in self.replace} | {k for rows in self.insert.values() for k, _b in rows}
                      | {self.files[p].lines[i].own_key for p, i in self.delete})


def canonical_row(obj: Dict[str, Any]) -> bytes:
    """Серіалізація рядка як у писаря SSOT (`ssot_jsonl.serialize_bar`), зі збереженням ключів наявного рядка."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def collect_rows(data_root: str, sym_dir: str, lo_ms: int, hi_ms: int):
    """(файли, наші рядки вікна за ключем, файли вікна без \\n, останній видимий бар до вікна, перший після)."""
    files: Dict[str, PartFile] = {}
    ours: Dict[int, Occ] = {}
    newline_fix: List[str] = []
    before: Dict[int, Occ] = {}
    after: Dict[int, Occ] = {}
    win_lo, win_hi = day_of_ms(lo_ms), day_of_ms(hi_ms - 1)
    ctx_lo, ctx_hi = day_of_ms(lo_ms - CONTEXT_MS), day_of_ms(hi_ms - 1 + CONTEXT_MS)
    for day in list_part_days(data_root, sym_dir, M1_S):
        if not ctx_lo <= day <= ctx_hi:
            continue
        path = part_path(data_root, sym_dir, M1_S, day)
        part = files[path] = load_part(path, sym_dir)
        if win_lo <= day <= win_hi and part.lines and part.lines[-1].eol == b"":
            newline_fix.append(path)
        for index, line in enumerate(part.lines):
            key = line.own_key
            if key is None:
                continue
            if lo_ms <= key < hi_ms:
                ours.setdefault(key, []).append((path, index))
            elif is_visible(line.obj.get("extensions")):
                (before if key < lo_ms else after).setdefault(key, []).append((path, index))
    ctx_before = (max(before), before[max(before)]) if before else None
    successor = (min(after), after[min(after)]) if after else None
    return files, ours, newline_fix, ctx_before, successor


def plan_symbol(data_root: str, sym_dir: str, symbol: str, archive: Dict[int, List[float]], lo_ms: int, hi_ms: int, *,
                is_trading: IsTrading, classify: Classify, provenance: Optional[str], grace_min: int,
                drop_by_classifier: bool = False, drop_flat: bool = False, flat_max: int = 0,
                unarchived_from_ms: Optional[int] = None) -> SymbolPlan:
    """План символу: ціль вікна → вкладення → ланцюг → дії на диску."""
    files, ours, newline_fix, ctx_before, successor = collect_rows(data_root, sym_dir, lo_ms, hi_ms)
    plan = SymbolPlan(files=files, newline_fix=newline_fix)
    target, plan.delete = decide_keys(files, ours, archive, is_trading=is_trading, classify=classify, trace=plan.trace,
                                      drop_by_classifier=drop_by_classifier, drop_flat=drop_flat, flat_max=flat_max,
                                      unarchived_from_ms=unarchived_from_ms)
    fold_edges(target, archive, ours, plan.delete, symbol=symbol, is_trading=is_trading, classify=classify,
               trace=plan.trace)
    successor_fix = chain_keys(target, files, ctx_before, successor, symbol=symbol, is_trading=is_trading,
                               grace_min=grace_min, trace=plan.trace)
    _emit_actions(plan, target, successor_fix, ours, data_root, sym_dir, symbol, provenance, is_trading)
    stats = plan.trace.stats
    stats["keys_ours_in_window"], stats["keys_archive_in_window"] = len(ours), len(archive)
    stats["files_without_trailing_newline"] = len(newline_fix)
    return plan


def _emit_actions(plan: SymbolPlan, target: Dict[int, KeyTarget], successor_fix: Optional[SuccessorFix],
                  ours: Dict[int, Occ], data_root: str, sym_dir: str, symbol: str, provenance: Optional[str],
                  is_trading: IsTrading) -> None:
    trace = plan.trace
    for key in sorted(target):
        entry = target[key]
        if key not in ours:
            ext = dict(entry.insert_ext or {})
            if provenance is not None:
                ext["settled"] = provenance
            ext.update(entry.markers)
            bar = CandleBar(symbol=symbol, tf_s=M1_S, open_time_ms=key, close_time_ms=key + M1_MS, o=entry.vals[0],
                            h=entry.vals[1], low=entry.vals[2], c=entry.vals[3], v=entry.vals[4], complete=True,
                            src="history", extensions=ext)
            plan.insert[part_path(data_root, sym_dir, M1_S, day_of_ms(key))].append((key, row_bytes(bar)))
            trace.note("INSERT", [utc_label(key), entry.verdict, entry.vals])
            continue
        if entry.conflict:
            continue
        changed = False
        for path, index in ours[key]:
            obj = plan.files[path].lines[index].obj
            new_obj = dict(obj)
            new_obj["o"], new_obj["h"], new_obj["low"], new_obj["c"], new_obj["v"] = entry.vals
            _set_extensions(new_obj, _target_extensions(obj, entry, provenance))
            if new_obj != obj and not _only_settled_differs(obj, new_obj, provenance):
                plan.replace.append((path, index, key, new_obj))
                changed = True
        if not entry.from_archive:
            trace.stats["CHAIN_ONLY_OURS"] += int(changed)
        elif not changed:
            trace.note("SAME")
        else:
            trace.note("REPLACE")
            if not is_trading(key):
                trace.note("REPLACE_PAUSE", utc_label(key))
            if len(trace.log["REPLACE_EXAMPLES"]) < REPLACE_EXAMPLES:
                trace.log["REPLACE_EXAMPLES"].append({"t": utc_label(key), "ours": vals_of(row_of(plan.files, ours[key])),
                                                      "new": list(entry.vals)})
    if successor_fix is not None:
        for path, index in successor_fix.occ:
            obj = plan.files[path].lines[index].obj
            new_obj = dict(obj)
            new_obj["o"], new_obj["h"], new_obj["low"] = successor_fix.vals
            ext = dict(obj.get("extensions") or {})
            ext.setdefault("open_chained_from", successor_fix.raw_o)
            _set_extensions(new_obj, ext)
            plan.replace.append((path, index, successor_fix.key, new_obj))


def _target_extensions(obj: Dict[str, Any], entry: KeyTarget, provenance: Optional[str]) -> Dict[str, Any]:
    old = obj.get("extensions") if isinstance(obj.get("extensions"), dict) else {}
    if not entry.from_archive:
        ext = dict(old)
        for marker, value in entry.markers.items():
            ext.setdefault(marker, value)  # сирий open брокера — перший, якщо рядок уже правили
        return ext
    ext = {k: v for k, v in old.items() if k not in STRIPPED_MARKERS}
    if provenance is not None:
        ext["settled"] = provenance  # на місці старої мітки, як у settle_prev/3 — байти рядка без зайвих перестановок
    else:
        ext.pop("settled", None)
    ext.update(entry.markers)
    return ext


def _set_extensions(obj: Dict[str, Any], ext: Dict[str, Any]) -> None:
    """extensions — як у `CandleBar.to_dict`: порожній dict не пишеться."""
    if ext:
        obj["extensions"] = ext
    else:
        obj.pop("extensions", None)


def _only_settled_differs(obj: Dict[str, Any], new_obj: Dict[str, Any], provenance: Optional[str]) -> bool:
    """Рядок уже має значення цілі й ті самі маркери, бракує лише мітки settled — не переписуємо (SAME)."""
    if provenance is None:
        return False
    strip = lambda o: {k: v for k, v in (o.get("extensions") or {}).items() if k != "settled"}  # noqa: E731
    return vals_of(obj) == vals_of(new_obj) and strip(obj) == strip(new_obj)
