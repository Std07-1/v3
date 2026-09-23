"""Суцільний ланцюг свічок M1 (ADR-0101 §3.1) — одне визначення розриву для записувачів і health.

Інваріант SSOT: для сусідніх видимих барів a < b ``o(b) == c(a)`` у межах представлення float. Бар із маркером
``calendar_pause_flat`` display ховає (``runtime/ws/candle_map.py``): він не сусід у ланцюзі, хоча ключ його зайнятий.

Правило послідовності, що тримає інваріант, живе в ``runtime.ingest.m1_session_filter``, вимір у SSOT — у
``core.health.measure_chain_breaks``. Предикат і маркер тут, в одному місці, щоб записувач і вимір не розійшлись
допуском (D15.2), а ``core`` не імпортував ``runtime`` (I0).

Модуль чистий і сумісний з Python 3.7: його імпортує ``m1_session_filter``, яким користується і ``.venv37``.
"""
from __future__ import annotations

# Допуск — представлення float, не крок ціни (як рейка ADR-0100 open_chain_breaks)
CHAIN_REL_TOL = 1e-9

# Маркер інжесту, за яким display ховає бар (артефакт паузи/вихідних від брокера)
MARKER_CALENDAR_PAUSE_FLAT = "calendar_pause_flat"


def open_breaks_chain(prev_close: float, bar_open: float) -> bool:
    """Розрив ланцюга: open бару ≠ close попереднього в межах представлення float."""
    return abs(bar_open - prev_close) > CHAIN_REL_TOL * max(1.0, abs(prev_close))
