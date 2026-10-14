"""Шлях ціни від народження зони — стан сценарію як чиста функція барів (ADR-0113 Ф2a, §3.8 варіант А).

Зона народжується зламом, що її створив (`SmcZone.origin_event_ms`, Ф0 K6, — open бару зламу на TF зони). Від цього
бару і далі по барах TF зони видно все, що наративу треба знати без пам'яті між викликами:

- ATR TF зони на барі зламу — масштаб стопу й відстаней, однаковий для всього життя зони і для будь-якого TF глядача
  (ATR глядача з кожним баром інший — стоп і закриття «пливли» б);
- рух від зони до входу — його екстремум (long — найвищий high, short — найнижчий low, бар зламу включно): ліквідність,
  яку рух уже пройшов, знята і ціллю бути не може;
- вхід — перший бар ПІСЛЯ бару зламу, тінь якого торкнулась краю входу (long — верху зони, short — низу); бар зламу
  входом не є — це той самий імпульс, що народив зону;
- після входу — сприятливий екстремум (ціль) і несприятливий (стоп). Порядку всередині бару входу не видно, тож його
  сприятливий екстремум зараховано руху до входу, а несприятливий — шляху після входу (стоп важить більше).

Однаково для WS-кадру, дельти, API, журналу й прогону по історії — нічого не пам'ятає, від рестарту не залежить.
S0: pure, без I/O.
"""
from __future__ import annotations

import dataclasses
from operator import attrgetter
from typing import Optional, Sequence

from core.model.bars import CandleBar
from core.smc.swings import compute_atr
from core.smc.types import SmcZone

LONG = "long"
SHORT = "short"
BIRTH_ATR_PERIOD = 14  # як ATR рушія (engine.get_measured_atr)


def zone_direction(zone: SmcZone) -> str:
    """Напрям угоди від зони: ведмежа (`*_bear`) — short, решта — long (правило наративу ADR-0033)."""
    return SHORT if "bear" in zone.kind else LONG


@dataclasses.dataclass(frozen=True)
class ZonePath:
    zone_id: str
    direction: str
    entry_edge: float  # long — верх зони, short — низ
    birth_atr: float  # ATR(14) TF зони на барі зламу
    pre_entry_extreme: float  # long — max high від бару зламу до входу (бар входу включно), short — min low
    entered_ms: Optional[int] = None  # open бару входу; None — ціна ще не поверталась до зони
    favorable_after_entry: Optional[float] = None  # long — max high ПІСЛЯ бару входу, short — min low; None — барів ще нема
    adverse_since_entry: Optional[float] = None  # long — min low з бару входу включно, short — max high

    @property
    def entered(self) -> bool:
        return self.entered_ms is not None


def zone_path(zone: SmcZone, bars: Sequence[CandleBar]) -> Optional[ZonePath]:
    """Шлях від бару зламу зони по барах її TF (за зростанням open_time_ms).

    None — у зони немає якоря народження (FVG, старий OB) або бару зламу немає серед барів (зона старша за вікно барів
    рушія, діра в даних): шлях з середини руху дав би хибний вхід і екстремум, тож стан не відомий і наратив
    поводиться як без шляху.
    """
    if zone.origin_event_ms is None:
        return None
    first = next((i for i, bar in enumerate(bars) if bar.open_time_ms >= zone.origin_event_ms), None)
    if first is None or bars[first].open_time_ms != zone.origin_event_ms:
        return None
    long = zone_direction(zone) == LONG
    entry_edge = zone.high if long else zone.low
    favorable = attrgetter("h") if long else attrgetter("low")
    adverse = attrgetter("low") if long else attrgetter("h")
    better = max if long else min
    worse = min if long else max
    extreme = favorable(bars[first])
    entered_ms: Optional[int] = None
    after: Optional[float] = None
    against: Optional[float] = None
    for bar in bars[first + 1:]:
        if entered_ms is not None:
            after = favorable(bar) if after is None else better(after, favorable(bar))
            against = worse(against, adverse(bar))
            continue
        extreme = better(extreme, favorable(bar))
        if (bar.low <= entry_edge) if long else (bar.h >= entry_edge):
            entered_ms, against = bar.open_time_ms, adverse(bar)
    birth_atr = compute_atr(list(bars[max(0, first + 1 - BIRTH_ATR_PERIOD):first + 1]), BIRTH_ATR_PERIOD)
    return ZonePath(zone.id, zone_direction(zone), entry_edge, birth_atr, extreme, entered_ms, after, against)
