"""Шлях ціни від народження зони — стан сценарію як чиста функція барів (ADR-0113 Ф2a, §3.8 варіант А).

Зона народжується зламом, що її створив (`SmcZone.origin_event_ms`, Ф0 K6, — open бару зламу на TF зони). Від цього
бару і далі по барах TF зони видно все, що наративу треба знати без пам'яті між викликами:

- рух від зони до входу — його екстремум (long — найвищий high, short — найнижчий low, бар зламу включно): ліквідність,
  яку рух уже пройшов, знята і ціллю бути не може;
- вхід — перший бар ПІСЛЯ бару зламу, тінь якого торкнулась краю входу (long — верху зони, short — низу); бар зламу
  входом не є — це той самий імпульс, що народив зону;
- після входу — найвищий high і найнижчий low (бар входу включно): ціль чи стоп уже взяті.

Однаково для WS-кадру, дельти, API, журналу й прогону по історії — нічого не пам'ятає, від рестарту не залежить.
S0: pure, без I/O.
"""
from __future__ import annotations

import dataclasses
from typing import Optional, Sequence

from core.model.bars import CandleBar
from core.smc.types import SmcZone

LONG = "long"
SHORT = "short"


def zone_direction(zone: SmcZone) -> str:
    """Напрям угоди від зони: ведмежа (`*_bear`) — short, решта — long (правило наративу ADR-0033)."""
    return SHORT if "bear" in zone.kind else LONG


@dataclasses.dataclass(frozen=True)
class ZonePath:
    zone_id: str
    direction: str
    entry_edge: float  # long — верх зони, short — низ
    pre_entry_extreme: float  # long — max high від бару зламу до входу (без бару входу), short — min low
    entered_ms: Optional[int] = None  # open бару входу; None — ціна ще не поверталась до зони
    post_entry_high: Optional[float] = None  # з бару входу включно
    post_entry_low: Optional[float] = None

    @property
    def entered(self) -> bool:
        return self.entered_ms is not None


def zone_path(zone: SmcZone, bars: Sequence[CandleBar]) -> Optional[ZonePath]:
    """Шлях від бару зламу зони по барах її TF (за зростанням open_time_ms).

    None — у зони немає якоря народження (FVG, старий OB) або на барах немає бару зламу: стан не відомий, наратив
    поводиться як без шляху.
    """
    if zone.origin_event_ms is None:
        return None
    direction = zone_direction(zone)
    long = direction == LONG
    entry_edge = zone.high if long else zone.low
    break_end_ms = zone.origin_event_ms + zone.tf_s * 1000
    extreme: Optional[float] = None
    entered_ms: Optional[int] = None
    post_high: Optional[float] = None
    post_low: Optional[float] = None
    for bar in bars:
        if bar.open_time_ms < zone.origin_event_ms:
            continue
        if entered_ms is not None:
            post_high, post_low = max(post_high, bar.h), min(post_low, bar.low)
            continue
        touches_entry = bar.low <= entry_edge if long else bar.h >= entry_edge
        if bar.open_time_ms >= break_end_ms and touches_entry:
            entered_ms, post_high, post_low = bar.open_time_ms, bar.h, bar.low
            continue
        bar_extreme = bar.h if long else bar.low
        extreme = bar_extreme if extreme is None else (max(extreme, bar_extreme) if long else min(extreme, bar_extreme))
    if extreme is None:
        return None  # бару зламу на барах немає — шлях не відомий
    return ZonePath(zone.id, direction, entry_edge, extreme, entered_ms, post_high, post_low)
