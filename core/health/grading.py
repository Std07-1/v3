"""core/health/grading.py — вимір → вердикт (ADR-0054 §3.2 Grading).

RED    — дефект даних, який ламає SMC-аналіз: зсунута сітка, побитий OHLC,
         дублікати, розбіжність каскаду, відставання понад допуск.
YELLOW — те, що не бреше, але й не готове: молода історія, дірки в межах допуску.
GREEN  — можна рахувати SMC.

Пороги — аргументи, а не константи: молодий символ і зрілий мають різні очікування.
"""
from __future__ import annotations

import dataclasses
from typing import List, Optional

from core.health.measures import AgeResult, CascadeResult, DepthResult, GeometryResult, HolesResult

RED = "RED"
YELLOW = "YELLOW"
GREEN = "GREEN"


@dataclasses.dataclass(frozen=True)
class Grade:
    """Вердикт по одному (symbol, tf) з причинами."""

    grade: str
    reasons: List[str]

    @property
    def ok(self) -> bool:
        return self.grade == GREEN


def grade_symbol_tf(
    *,
    age: Optional[AgeResult] = None,
    holes: Optional[HolesResult] = None,
    geometry: Optional[GeometryResult] = None,
    cascade: Optional[CascadeResult] = None,
    depth: Optional[DepthResult] = None,
    max_age_buckets: int = 1,
    max_missing_ratio: float = 0.0,
) -> Grade:
    """Звести виміри в один вердикт. Порожній ряд = RED (нема чого аналізувати)."""
    red: List[str] = []
    yellow: List[str] = []

    if geometry is not None:
        if geometry.total == 0:
            red.append("no_bars")
        for field in ("dup_conflicting", "align_bad", "close_bad", "ohlc_bad"):
            value = getattr(geometry, field)
            if value:
                red.append(f"{field}={value}")
        if geometry.exact_dup and not geometry.dup_conflicting:
            # Повторний запис ІДЕНТИЧНОГО бару — легальний наслідок append-only SSOT
            # (rebuild/backfill дописує те саме). Читач злипає їх, графік не страждає.
            yellow.append(f"exact_dup={geometry.exact_dup}")
        if geometry.unsorted:
            # Порядок рядків у part-файлі — не дефект даних: читачі сортують, а
            # backfill законно дописує старіші бари після новіших. Значення саме
            # як сигнал безладу у файлі, не як стоп для аналізу.
            yellow.append(f"unsorted={geometry.unsorted}")

    if cascade is not None and cascade.mismatched:
        red.append(f"cascade_mismatch={cascade.mismatched}")

    if age is not None and age.age_buckets is not None and age.age_buckets > max_age_buckets:
        red.append(f"age_buckets={age.age_buckets}")

    if holes is not None and holes.missing:
        ratio = holes.missing / holes.expected if holes.expected else 1.0
        if ratio > max_missing_ratio:
            red.append(f"holes={holes.missing}/{holes.expected}")
        else:
            yellow.append(f"holes={holes.missing}")

    if depth is not None and not depth.enough:
        yellow.append(f"depth={depth.bars}<{depth.required}")

    if red:
        return Grade(grade=RED, reasons=red + yellow)
    if yellow:
        return Grade(grade=YELLOW, reasons=yellow)
    return Grade(grade=GREEN, reasons=[])
