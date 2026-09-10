"""core/health — pure-виміри здоров'я символу (ADR-0054 Фаза 1).

Нуль I/O і нуль імпортів з ``runtime``: календар приходить у виміри як
``is_trading_fn``. I/O-оболонка — ``tools/symbol_health_check.py``.
"""
from core.health.compare import CompareResult, Regression, compare_reports
from core.health.grading import Grade, grade_symbol_tf
from core.health.measures import (
    AgeResult,
    CascadeResult,
    DepthResult,
    GeometryResult,
    HolesResult,
    bucket_has_trading_minute,
    check_anchor_on_session_edge,
    expected_bucket_opens,
    measure_age,
    measure_cascade,
    measure_depth,
    measure_geometry,
    measure_holes,
    normalize_open_to_grid,
)

__all__ = [
    "CompareResult",
    "Regression",
    "compare_reports",
    "Grade",
    "grade_symbol_tf",
    "AgeResult",
    "CascadeResult",
    "DepthResult",
    "GeometryResult",
    "HolesResult",
    "bucket_has_trading_minute",
    "check_anchor_on_session_edge",
    "expected_bucket_opens",
    "measure_age",
    "measure_cascade",
    "measure_depth",
    "measure_geometry",
    "measure_holes",
    "normalize_open_to_grid",
]
