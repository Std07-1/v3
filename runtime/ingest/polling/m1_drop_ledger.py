"""Облік відкинутих M1 одного символу в живому полері (ADR-0099 §3.3–3.4).

Чи відкидати бар, вирішує `runtime/ingest/m1_session_filter`. Цей модуль відповідає за те, як відкинуте видно:
- кожна відкинута хвилина рахується й логується один раз, хоч би скільки разів брокер віддав її повторно
  (`poll_once` і `live_recover` в одному циклі, `tail_catchup`). Watermark рухає лише коміт, а відкинуте не
  комітиться, тож без обліку лічильник і WARN завищувались утричі (13 унікальних → 39);
- шум глибоко в паузі, схожий на торгівлю (великий обсяг або густота за вікно часу барів), дає тривогу хибного
  календаря. Вікно рахується за часом барів, а не годинника: після вихідних полер за один цикл доганяє всю суботу.

Модуль чистий, без I/O і годинника; лог пише полер.
"""
from __future__ import annotations

import bisect
import collections
import dataclasses
from typing import List, Optional

from runtime.ingest.m1_session_filter import PausePolicy

_M1_MS = 60_000

# Скільки відкинутих хвилин пам'ятає полер одного символу. Найдовша пауза — вихідні cfd_us (Пт 20:45 → Нд 22:00,
# 2955 хв), тож 4096 вміщують кожну хвилину одних вихідних: повторний fetch тих самих вихідних не рахується двічі.
DROPPED_LEDGER_CAPACITY = 4096

ALARM_REASON_VOLUME = "volume"
ALARM_REASON_DENSITY = "density"


@dataclasses.dataclass(frozen=True)
class NoiseAlarm:
    """Тривога: шум глибоко в паузі схожий на торгівлю — ймовірно, календар символу хибний."""

    reason: str
    noise_in_window: int
    suppressed_since_last: int


class DroppedM1Ledger:
    """Пам'ять відкинутих хвилин одного символу і тривога хибного календаря."""

    def __init__(self, pause_policy: PausePolicy, capacity: int = DROPPED_LEDGER_CAPACITY) -> None:
        self._policy = pause_policy
        self._capacity = capacity
        self._dropped_opens: "collections.OrderedDict[int, None]" = collections.OrderedDict()
        self._noise_opens: List[int] = []  # відсортовані open_ms шуму — для густоти у вікні часу барів
        self._last_alarm_open_ms: Optional[int] = None
        self._suppressed_alarms = 0

    def first_drop(self, open_ms: int) -> bool:
        """True — хвилину відкинуто вперше: рахувати й логувати. False — повторний fetch уже врахованої хвилини."""
        if open_ms in self._dropped_opens:
            return False
        self._dropped_opens[open_ms] = None
        if len(self._dropped_opens) > self._capacity:
            self._dropped_opens.popitem(last=False)
        return True

    def observe_noise(self, open_ms: int, volume: float) -> Optional[NoiseAlarm]:
        """Враховує нову хвилину шуму глибоко в паузі. Повертає тривогу, якщо шум схожий на торгівлю.

        Тривога — не частіше одного разу на вікно часу барів (X10): при хибному календарі справжні хвилини йдуть
        щохвилини, і ERROR на кожну заглушив би лог. Придушені тривоги рахуються й віддаються в наступній.
        """
        bisect.insort(self._noise_opens, open_ms)
        if len(self._noise_opens) > self._capacity:
            del self._noise_opens[0]
        window_ms = self._policy.alarm_window_min * _M1_MS
        noise_in_window = (bisect.bisect_right(self._noise_opens, open_ms)
                           - bisect.bisect_right(self._noise_opens, open_ms - window_ms))
        reason = self._alarm_reason(volume, noise_in_window)
        if reason is None:
            return None
        if self._last_alarm_open_ms is not None and abs(open_ms - self._last_alarm_open_ms) < window_ms:
            self._suppressed_alarms += 1
            return None
        alarm = NoiseAlarm(reason=reason, noise_in_window=noise_in_window,
                           suppressed_since_last=self._suppressed_alarms)
        self._last_alarm_open_ms = open_ms
        self._suppressed_alarms = 0
        return alarm

    def _alarm_reason(self, volume: float, noise_in_window: int) -> Optional[str]:
        if self._policy.is_trading_like_volume(volume):
            return ALARM_REASON_VOLUME
        if noise_in_window > self._policy.alarm_max_dropped:
            return ALARM_REASON_DENSITY
        return None
