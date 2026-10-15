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

Рушій тримає лише закриті бари, тож бар TF зони, що ще формується, складається із закритих барів дрібнішого TF
(`forming_spans`), а момент першого дотику всередині бакета входу — з найдрібнішого ряду, що покриває бакет цілком
(`first_touch_end_ms`): інакше вхід у зону H1/H4/D1 видно лише після закриття її бару, а підтвердження на молодшому TF —
лише після кінця бару входу (рецензія Ф2a, E-03).

Однаково для WS-кадру, дельти, API, журналу й прогону по історії — нічого не пам'ятає, від рестарту не залежить.
S0: pure, без I/O.
"""
from __future__ import annotations

import dataclasses
import itertools
from operator import attrgetter
from typing import Callable, Iterable, List, Optional, Sequence

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
    # Кінець найдрібнішого бару, де видно перший дотик (`first_touch_end_ms`); підтвердження — лише злам не раніше.
    # None — дрібніших барів на весь бакет входу немає: до кінця бару входу на TF зони (рецензія Ф2a, D-11)
    touch_end_ms: Optional[int] = None

    @property
    def entered(self) -> bool:
        return self.entered_ms is not None


@dataclasses.dataclass(frozen=True)
class FormingSpan:
    """Бакет TF зони, що ще формується, — high/low його закритих барів дрібнішого TF.

    `open_time_ms` — відкриття бакета на сітці символу, тож вхід у ньому має той самий `entered_ms`, що й після
    закриття бару TF зони, а high/low закритого бару лише ширші: шлях від цього тільки продовжується.
    """

    open_time_ms: int
    h: float
    low: float


def forming_spans(
    last_closed_open_ms: int, finer_bars: Sequence[CandleBar], bucket_start: Callable[[int], int]
) -> List[FormingSpan]:
    """Закриті бари дрібнішого TF після останнього закритого бару TF зони, згруповані в бакети TF зони.

    `bucket_start(ts_ms)` — відкриття бакета TF зони, що містить момент (сітка символу, `core.session_anchor`). Бари
    бакета останнього закритого бару вже в ньому. Бакетів може бути кілька, якщо рушій ще не отримав закритий бар TF
    зони, а дрібніший уже прийшов.
    """
    tail = []
    start: Optional[int] = None
    for bar in reversed(finer_bars):  # за зростанням open_time_ms — з кінця до бакета закритого бару
        if start is None or bar.open_time_ms < start:  # бакети суцільні: новий початок — лише коли вийшли з поточного
            start = bucket_start(bar.open_time_ms)
        if start <= last_closed_open_ms:
            break
        tail.append((start, bar))
    spans: List[FormingSpan] = []
    for start, bar in reversed(tail):
        if spans and spans[-1].open_time_ms == start:
            spans[-1] = FormingSpan(start, max(spans[-1].h, bar.h), min(spans[-1].low, bar.low))
        else:
            spans.append(FormingSpan(start, bar.h, bar.low))
    return spans


def first_touch_end_ms(
    path: ZonePath, finer_series: Iterable[Sequence[CandleBar]], entry_bucket_end_ms: int
) -> Optional[int]:
    """Кінець першого бару дрібнішого TF у бакеті входу [`entered_ms`, `entry_bucket_end_ms`), тінь якого торкнулась
    краю входу.

    `finer_series` — ряди від найдрібнішого (лінивий ітератор — ряд береться, лише коли потрібен); вирішує перший, що
    покриває бакет від відкриття (інакше перший дотик міг бути в непокритій частині). None — входу немає, жоден ряд
    бакет не покриває або дотику в ньому не видно (high/low бару TF зони ширші за дрібні бари). Коли найдрібніший ряд
    перестає покривати бакет (вхід старший за його вікно), вирішує грубший — момент може зсунутись пізніше в межах бару
    нового ряду.
    """
    if path.entered_ms is None:
        return None
    long = path.direction == LONG
    covering = next((s for s in finer_series if s and s[0].open_time_ms <= path.entered_ms), None)
    if covering is None:
        return None
    for bar in covering:
        if bar.open_time_ms < path.entered_ms:
            continue
        if bar.open_time_ms >= entry_bucket_end_ms:
            return None  # бакет входу скінчився без дотику на дрібному ряді
        if (bar.low <= path.entry_edge) if long else (bar.h >= path.entry_edge):
            return bar.open_time_ms + bar.tf_s * 1000
    return None


def zone_path(
    zone: SmcZone, bars: Sequence[CandleBar], forming: Sequence[FormingSpan] = ()
) -> Optional[ZonePath]:
    """Шлях від бару зламу зони по барах її TF (за зростанням open_time_ms) і бакетах, що формуються (`forming_spans`).

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
    for bar in itertools.chain(bars[first + 1:], forming):
        if entered_ms is not None:
            after = favorable(bar) if after is None else better(after, favorable(bar))
            against = worse(against, adverse(bar))
            continue
        extreme = better(extreme, favorable(bar))
        if (bar.low <= entry_edge) if long else (bar.h >= entry_edge):
            entered_ms, against = bar.open_time_ms, adverse(bar)
    birth_atr = compute_atr(list(bars[max(0, first + 1 - BIRTH_ATR_PERIOD):first + 1]), BIRTH_ATR_PERIOD)
    return ZonePath(zone.id, zone_direction(zone), entry_edge, birth_atr, extreme, entered_ms, after, against)
