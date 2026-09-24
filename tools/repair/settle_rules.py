"""tools/repair/settle_rules.py — правила ключів settle M1 (ADR-0098 §3.3, ADR-0103 S2): ціль вікна до вкладення і ланцюга.

  є в архіві | є в нас | хвилина     | ціль
  так        | так     | будь-яка    | значення архіву (усі наші рядки ключа однаково); хвилина паузи — лічиться окремо
  так        | ні      | торгова     | вставка, якщо класифікатор ADR-0099 її пише (вердикт None — INSERT_DROPPED_BY_CLASSIFIER)
  так        | ні      | пауза       | не вставляти (SETTLE_BROKER_PAUSE_SKIPPED)
  ні         | так     | будь-яка    | лишити гучно (SETTLE_KEY_ONLY_OURS / SETTLE_PAUSE_ONLY_OURS)
Опції власника (за замовчуванням вимкнені): ключ лише в нас, який класифікатор сьогодні не записав би
(`drop_by_classifier`) або плаский з v ≤ flat_max (`drop_flat`), вилучається — крім хвилини закриття тижня і ключів
тижня, якого архів ще не засвідчив (`unarchived_from_ms`): SETTLE_DROP_PROTECTED.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
from typing import Any, Callable, DefaultDict, Dict, List, Optional, Tuple

from core.model.bars import CandleBar
from tools.repair.partfile_io import PartFile
from tools.repair.settle_gate import IsTrading, is_week_close_minute, utc_label

M1_S = 60
M1_MS = 60_000

Occ = List[Tuple[str, int]]  # рядки ключа: (шлях part-файла, індекс рядка)
Classify = Callable[[int, List[float]], Tuple[Optional[CandleBar], str]]  # (ключ, [o,h,low,c,v]) → (бар|None, вердикт)


@dataclass
class SettleTrace:
    """Лічильники й приклади плану settle — для звіту і гучних логів (I5)."""

    stats: collections.Counter = field(default_factory=collections.Counter)
    log: DefaultDict[str, list] = field(default_factory=lambda: collections.defaultdict(list))

    def note(self, tag: str, entry: Any = None) -> None:
        self.stats[tag] += 1
        if entry is not None:
            self.log[tag].append(entry)


@dataclass
class KeyTarget:
    """Ціль ключа вікна: значення [o, h, low, c, v] і звідки вони."""

    vals: List[float]
    from_archive: bool
    visible: bool
    markers: Dict[str, Any] = field(default_factory=dict)  # маркери ADR-0101, перераховані з архівних значень
    insert_ext: Optional[Dict[str, Any]] = None  # новий ключ: extensions від класифікатора
    verdict: Optional[str] = None
    conflict: bool = False  # лише наш ключ, кілька рядків з різними значеннями — ланцюг через нього не тягнеться
    ext: Dict[str, Any] = field(default_factory=dict)  # лише наш ключ: extensions нашого рядка


def is_visible(ext: Any) -> bool:
    """Бар, який показує display: без calendar_pause_flat (ADR-0101 §3.1 — ланцюг по видимих барах)."""
    return not (isinstance(ext, dict) and ext.get("calendar_pause_flat"))


def vals_of(obj: Dict[str, Any]) -> List[float]:
    return [obj["o"], obj["h"], obj["low"], obj["c"], obj["v"]]


def row_of(files: Dict[str, PartFile], occ: Occ) -> Dict[str, Any]:
    """Останній рядок ключа — той, що переміг би в читача (last-wins у межах файла)."""
    path, index = occ[-1]
    return files[path].lines[index].obj


def occ_conflict(files: Dict[str, PartFile], occ: Occ) -> bool:
    return len({tuple(vals_of(files[path].lines[index].obj)) for path, index in occ}) > 1


def to_bar(symbol: str, open_ms: int, vals: List[float]) -> CandleBar:
    return CandleBar(symbol=symbol, tf_s=M1_S, open_time_ms=open_ms, close_time_ms=open_ms + M1_MS, o=vals[0],
                     h=vals[1], low=vals[2], c=vals[3], v=vals[4], complete=True, src="history", extensions={})


def decide_keys(
    files: Dict[str, PartFile],
    ours: Dict[int, Occ],
    archive: Dict[int, List[float]],
    *,
    is_trading: IsTrading,
    classify: Classify,
    trace: SettleTrace,
    drop_by_classifier: bool = False,
    drop_flat: bool = False,
    flat_max: int = 0,
    unarchived_from_ms: Optional[int] = None,
) -> Tuple[Dict[int, KeyTarget], Occ]:
    """(ціль вікна за ключем, наші рядки на вилучення) за таблицею модуля."""
    target: Dict[int, KeyTarget] = {}
    delete_rows: Occ = []
    for key in sorted(set(ours) | set(archive)):
        if key in archive and key in ours:
            if len(ours[key]) > 1:
                trace.note("DUP_ROWS_KEY", utc_label(key))
            visible = is_visible(row_of(files, ours[key]).get("extensions"))
            target[key] = KeyTarget(vals=list(archive[key]), from_archive=True, visible=visible)
        elif key in archive:
            if not is_trading(key):
                trace.note("SETTLE_BROKER_PAUSE_SKIPPED", utc_label(key))
                continue
            bar, verdict = classify(key, archive[key])
            if bar is None:
                trace.note("INSERT_DROPPED_BY_CLASSIFIER", [utc_label(key), verdict, archive[key]])
                continue
            target[key] = KeyTarget(vals=list(archive[key]), from_archive=True, visible=True,
                                    insert_ext=dict(bar.extensions or {}), verdict=verdict)
        else:
            obj = row_of(files, ours[key])
            entry = [utc_label(key), vals_of(obj), obj.get("extensions")]
            dropped = _only_ours_drop_reason(obj, key, classify, drop_by_classifier, drop_flat, flat_max)
            if dropped is not None:
                protected = _drop_protection(key, is_trading, unarchived_from_ms)
                if protected is not None:
                    trace.note("SETTLE_DROP_PROTECTED", entry + [dropped, protected])
                else:
                    delete_rows.extend(ours[key])
                    trace.note("SETTLE_ONLY_OURS_DROPPED", entry + [dropped])
                    continue
            trace.note("SETTLE_KEY_ONLY_OURS" if is_trading(key) else "SETTLE_PAUSE_ONLY_OURS", entry)
            target[key] = KeyTarget(vals=vals_of(obj), from_archive=False, visible=is_visible(obj.get("extensions")),
                                    conflict=occ_conflict(files, ours[key]), ext=obj.get("extensions") or {})
    return target, delete_rows


def _only_ours_drop_reason(obj, key, classify, drop_by_classifier, drop_flat, flat_max) -> Optional[str]:
    if drop_by_classifier:
        bar, verdict = classify(key, vals_of(obj))
        if bar is None:
            return "classifier:%s" % verdict
    if drop_flat and obj["o"] == obj["h"] == obj["low"] == obj["c"] and obj["v"] <= flat_max:
        return "flat_v<=flat_max"
    return None


def _drop_protection(key: int, is_trading: IsTrading, unarchived_from_ms: Optional[int]) -> Optional[str]:
    if is_week_close_minute(is_trading, key):
        return "week_close_minute"
    if unarchived_from_ms is not None and key >= unarchived_from_ms:
        return "unarchived_week_from_%s" % utc_label(unarchived_from_ms)
    return None
