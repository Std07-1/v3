"""Облік відкинутих M1 одного символу в живому полері (ADR-0099 §3.4).

Чи відкидати бар, вирішує `runtime/ingest/m1_session_filter`. Цей модуль відповідає за те, як відкинуте видно:
кожна відкинута хвилина рахується й логується один раз, хоч би скільки разів брокер віддав її повторно
(`poll_once` і `live_recover` в одному циклі, `tail_catchup`). Watermark рухає лише коміт, а відкинуте не
комітиться, тож без обліку лічильник і WARN завищувались утричі (13 унікальних → 39).

Модуль чистий, без I/O і годинника; лог пише полер.
"""
from __future__ import annotations

import collections

# Скільки відкинутих хвилин пам'ятає полер одного символу. Найдовша пауза — вихідні cfd_us (Пт 20:45 → Нд 22:00,
# 2955 хв), тож 4096 вміщують кожну хвилину одних вихідних: повторний fetch тих самих вихідних не рахується двічі.
DROPPED_LEDGER_CAPACITY = 4096


class DroppedM1Ledger:
    """Пам'ять відкинутих хвилин одного символу."""

    def __init__(self, capacity: int = DROPPED_LEDGER_CAPACITY) -> None:
        self._capacity = capacity
        self._dropped_opens: "collections.OrderedDict[int, None]" = collections.OrderedDict()

    def first_drop(self, open_ms: int) -> bool:
        """True — хвилину відкинуто вперше: рахувати й логувати. False — повторний fetch уже врахованої хвилини."""
        if open_ms in self._dropped_opens:
            return False
        self._dropped_opens[open_ms] = None
        if len(self._dropped_opens) > self._capacity:
            self._dropped_opens.popitem(last=False)
        return True
