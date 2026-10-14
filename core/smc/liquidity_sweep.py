"""Знята чи не знята ліквідність — коли ціна вперше дійшла до рівня після того, як він утворився (ADR-0113 Ф2a; §3.3
«детектор знята / не знята»).

Рівень чи свінг — ліквідність, поки ціна не торгувала за ним ПІСЛЯ кінця його періоду (open + TF). Екстремум руху від
народження зони цього не розрізняв: рівень, що утворився вже на відкаті й ціною не брався, вважався знятим (стара зона
XAG H1 25.05: ціль «хай імпульсу 89.243» замість незнятого «хай попер. H4 78.676»).

Відповідь — момент першого дотику, а не «так/ні»: «знято до входу» і «знято зараз» — різні питання (після входу дотик
цілі — це ціль узята, а не причина її відкинути). Ряд барів рушія → розріджені таблиці max high / min low, перший
дотик — бінарний пошук: O(n log n) побудова, O(log² n) запит. Кілька рядів — дрібний для свіжого, старші — для давнього
(береться найдрібніший, що покриває момент). S0: pure, без I/O.
"""
from __future__ import annotations

import bisect
from typing import List, Optional, Sequence, Tuple

from core.model.bars import CandleBar

FirstReached = Tuple[Optional[int], Optional[int]]  # (open бару першого high ≥ ціни, open бару першого low ≤ ціни)


class TouchIndex:
    """Перший дотик ціни в одному ряді барів (за зростанням open_time_ms) від моменту."""

    def __init__(self, bars: Sequence[CandleBar]) -> None:
        self._opens = [bar.open_time_ms for bar in bars]
        self._max = [[bar.h for bar in bars]]
        self._min = [[bar.low for bar in bars]]
        span = 1
        while span * 2 <= len(bars):
            prev_max, prev_min = self._max[-1], self._min[-1]
            self._max.append([max(prev_max[i], prev_max[i + span]) for i in range(len(bars) - span * 2 + 1)])
            self._min.append([min(prev_min[i], prev_min[i + span]) for i in range(len(bars) - span * 2 + 1)])
            span *= 2

    def covers(self, moment_ms: int) -> bool:
        """Ряд почався не пізніше моменту — дотик після нього не міг випасти за початок вікна."""
        return bool(self._opens) and self._opens[0] <= moment_ms

    def _range(self, table: List[List[float]], lo: int, hi: int, pick) -> float:
        level = (hi - lo + 1).bit_length() - 1
        return pick(table[level][lo], table[level][hi - (1 << level) + 1])

    def _first(self, moment_ms: int, reached) -> Optional[int]:
        start = bisect.bisect_left(self._opens, moment_ms)
        last = len(self._opens) - 1
        if start > last or not reached(start, last):
            return None
        lo, hi = start, last
        while lo < hi:
            mid = (lo + hi) // 2
            if reached(start, mid):
                hi = mid
            else:
                lo = mid + 1
        return self._opens[lo]

    def first_reached(self, moment_ms: int, price: float) -> FirstReached:
        above = self._first(moment_ms, lambda lo, hi: self._range(self._max, lo, hi, max) >= price)
        below = self._first(moment_ms, lambda lo, hi: self._range(self._min, lo, hi, min) <= price)
        return above, below


def first_reached(indexes: Sequence[TouchIndex], moment_ms: int, price: float) -> FirstReached:
    """Перший дотик від моменту в найдрібнішому ряді, що його покриває (indexes — від дрібного до старшого).

    Момент старший за всі вікна — (None, None): дотику не видно, рівень вважається незнятим (так само, як без детектора).
    """
    for index in indexes:
        if index.covers(moment_ms):
            return index.first_reached(moment_ms, price)
    return None, None
