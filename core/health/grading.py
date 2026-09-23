"""core/health/grading.py — вимір → вердикт (ADR-0054 §3.2 Grading).

RED    — дефект даних, який ламає SMC-аналіз: зсунута сітка (зокрема H4/D1 поза сезонною
         сіткою ADR-0095), побитий OHLC,
         дублікати, розбіжність каскаду або кореня M1, відставання понад допуск.
YELLOW — те, що не бреше, але й не готове: молода історія, дірки в межах допуску, розриви ланцюга M1 без діри
         (ADR-0101: графік рваний, а OHLC кожного бару узгоджений — SMC рахується, зсув на центи).
GREEN  — можна рахувати SMC.

Пороги — аргументи, а не константи: молодий символ і зрілий мають різні очікування.
"""
from __future__ import annotations

import dataclasses
from typing import List, Optional

from core.health.measures import (
    AgeResult,
    CascadeResult,
    ChainResult,
    DepthResult,
    GeometryResult,
    HolesResult,
    RootResult,
)

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
    root: Optional[RootResult] = None,
    depth: Optional[DepthResult] = None,
    chain: Optional[ChainResult] = None,
    max_age_buckets: int = 1,
    max_missing_ratio: float = 0.0,
) -> Grade:
    """Звести виміри в один вердикт. Порожній ряд = RED (нема чого аналізувати)."""
    red: List[str] = []
    yellow: List[str] = []

    if geometry is not None:
        if geometry.total == 0:
            red.append("no_bars")
        for field in ("dup_conflicting", "align_bad", "off_season_grid", "close_bad", "ohlc_bad"):
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
    if root is not None and root.mismatched:
        # Бар мовчки не дорівнює агрегації M1, з якої мав бути зібраний (ADR-0002): графік
        # показує свічку з даних, яких у SSOT уже немає. Каскад це пропускає, якщо застарів
        # цілий ланцюжок рівнів разом (ADR-0094 P4).
        red.append(f"root_mismatch={root.mismatched}")

    if age is not None and age.age_buckets is not None and age.age_buckets > max_age_buckets:
        red.append(f"age_buckets={age.age_buckets}")
    if age is not None and age.last_open_ms is not None and age.age_buckets is None:
        # I5 degraded-but-loud: «не змогли порахувати» — це не «свіжо». Мовчазний None
        # тут і ховав D1-вимір, поки той був сліпим (ADR-0054 §3.8 п.1): ряд є, бари є,
        # а відставання невідоме — такий рядок звіту не має виглядати чистим.
        yellow.append("age_unknown")

    if holes is not None and holes.missing:
        ratio = holes.missing / holes.expected if holes.expected else 1.0
        if ratio > max_missing_ratio:
            red.append(f"holes={holes.missing}/{holes.expected}")
        else:
            yellow.append(f"holes={holes.missing}")

    if depth is not None and not depth.enough:
        yellow.append(f"depth={depth.bars}<{depth.required}")

    if chain is not None and chain.inner:
        # Порушення інваріанту SSOT ADR-0101 §3.1, але не брехня даних: кожен бар сам по собі цілий, не залежить від
        # порядку у файлі й не розходиться з агрегацією — рветься лише вигляд графіка (центи ревізії брокера).
        # Прибирає settle у вікні закритого ринку (C5), тож це «не готове», а не стоп аналізу. Розрив на межі діри
        # (`at_gap`) не оцінюється: ланцюг через нашу діру не тягнуть, геп брокера вимір від неї не відрізнить, а саму
        # діру оцінює `holes`. Відкриття сесії, яке брокер пропускає (метали — 22:01), — `inner`, не `at_gap`.
        yellow.append(f"chain_breaks_inner={chain.inner}")

    if red:
        return Grade(grade=RED, reasons=red + yellow)
    if yellow:
        return Grade(grade=YELLOW, reasons=yellow)
    return Grade(grade=GREEN, reasons=[])
