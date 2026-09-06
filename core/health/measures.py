"""core/health/measures.py — pure-виміри здоров'я символу (ADR-0054 Фаза 1).

Кожна функція: дані на вході → факт на виході. Нуль I/O, нуль імпортів з ``runtime``
(календар приходить як ``is_trading_fn``), тому виміри тестуються на синтетичних барах
і однаково працюють для диску, Redis чи майбутнього джерела.

Що вимірюємо і навіщо (ADR-0054 §3.2):

- ``bucket_age`` — чи не відстає останній бар від очікуваного закритого бакета.
- ``holes`` — яких торгових бакетів немає взагалі.
- ``geometry`` — дублікати, порядок, вирівнювання по сітці, узгодженість close_ms.
- ``cascade`` — чи derived-бар справді дорівнює агрегації свого source.
- ``history_depth`` — чи вистачає глибини для SMC (lookback вищих TF).

Клас дефекту, заради якого це існує: 06.09 засів NAS100 виглядав цілим (M1 і H4
доходили до вересня), а D1 тихо обірвався на два місяці раніше — око цього не бачить,
вимір бачить.
"""
from __future__ import annotations

import dataclasses
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from core.buckets import bucket_start_ms
from core.model.bars import CandleBar

IsTradingFn = Callable[[int], bool]

# Скільки бакетів назад шукаємо останній торговий: 2 тижні M1 (довгі вихідні + свята).
MAX_BACKWARD_PROBES = 20_160


@dataclasses.dataclass(frozen=True)
class AgeResult:
    """Відставання останнього бара від очікуваного закритого бакета."""

    last_open_ms: Optional[int]
    expected_last_open_ms: Optional[int]
    age_buckets: Optional[int]


@dataclasses.dataclass(frozen=True)
class HolesResult:
    """Відсутні торгові бакети у вікні."""

    expected: int
    present: int
    missing: int
    missing_samples: Tuple[int, ...]


@dataclasses.dataclass(frozen=True)
class GeometryResult:
    """Структурні дефекти ряду барів.

    ``exact_dup`` — скільки зайвих записів на бакет. SSOT append-only, тому повторний
    запис ІДЕНТИЧНОГО бару (rebuild/backfill) легальний і нешкідливий: читач злипає їх
    і бачить те саме. Небезпечний лише ``dup_conflicting`` — коли на один бакет лежать
    РІЗНІ значення, бо тоді результат вирішує порядок у файлі.
    """

    total: int
    exact_dup: int
    dup_conflicting: int
    unsorted: int
    align_bad: int
    close_bad: int
    ohlc_bad: int
    align_bad_samples: Tuple[int, ...]


@dataclasses.dataclass(frozen=True)
class CascadeResult:
    """Чи derived-бари відтворюються з source-барів.

    ``declared_partial`` — розбіжності, про які сам бар чесно попередив у ``extensions``
    (ADR-0013b маркери; ADR-0015 Option C: ``complete=true`` означає «бакет минув», а не
    «зібрано N з N»). Це не дефект даних, а задокументований компроміс.
    ``mismatched`` лишає тільки МОВЧАЗНІ розбіжності — бар відрізняється від агрегації
    і нічого про це не каже. Саме їх і має ловити health-check.
    """

    checked: int
    mismatched: int
    declared_partial: int
    skipped_incomplete: int
    mismatch_samples: Tuple[int, ...]


@dataclasses.dataclass(frozen=True)
class DepthResult:
    """Глибина історії відносно потреб SMC."""

    bars: int
    span_days: float
    first_open_ms: Optional[int]
    last_open_ms: Optional[int]
    enough: bool
    required: int


def expected_bucket_opens(
    start_ms: int,
    end_ms: int,
    tf_ms: int,
    anchor_offset_ms: int,
    is_trading_fn: IsTradingFn,
) -> List[int]:
    """Торгові відкриття бакетів у ``[start_ms, end_ms)`` за календарем.

    Бакет очікується, якщо торгується його **перша хвилина**: саме так живий writer
    вирішує, чи взагалі буде бар.Напіввідкритий інтервал — як у ``market_calendar``.
    """
    if tf_ms <= 0 or end_ms <= start_ms:
        return []
    first = bucket_start_ms(start_ms, tf_ms, anchor_offset_ms)
    if first < start_ms:
        first += tf_ms
    return [b for b in range(first, end_ms, tf_ms) if is_trading_fn(b)]


def normalize_open_to_grid(
    open_ms: int,
    *,
    tf_ms: int,
    anchor_offsets_ms: Sequence[int],
) -> Optional[int]:
    """Звести відкриття бара до основної сітки, якщо воно легальне.

    HTF-якорі рухаються з DST (D1 21:00 влітку / 22:00 взимку — `day_anchor_offset_s_d1`
    та `_d1_alt`), тому «не на сітці за primary» ще не означає дефект. Бар легальний,
    якщо вирівняний за будь-яким дозволеним якорем; повертаємо його відкриття у primary-
    сітці, щоб решта вимірів порівнювала яблука з яблуками. ``None`` = справді зсунутий.
    """
    if tf_ms <= 0 or not anchor_offsets_ms:
        return None
    primary = anchor_offsets_ms[0]
    for offset in anchor_offsets_ms:
        if bucket_start_ms(open_ms, tf_ms, offset) == open_ms:
            return open_ms + (primary - offset) % tf_ms if offset != primary else open_ms
    return None


def measure_age(
    opens: Sequence[int],
    *,
    now_ms: int,
    tf_ms: int,
    anchor_offset_ms: int,
    is_trading_fn: IsTradingFn,
) -> AgeResult:
    """Скільки закритих бакетів минуло після останнього наявного бара."""
    if not opens:
        return AgeResult(last_open_ms=None, expected_last_open_ms=None, age_buckets=None)
    last_open = max(opens)
    current_open = bucket_start_ms(now_ms, tf_ms, anchor_offset_ms)
    expected = None
    probe = current_open - tf_ms
    # Останній ЗАКРИТИЙ торговий бакет: поточний ще формується. Ліміт має покривати
    # вихідні: у неділю останній торговий M1-бакет лежить ~2600 бакетів позаду, тож
    # маленьке вікно давало age=None (не «свіжо», а «не змогли порахувати»).
    for _ in range(MAX_BACKWARD_PROBES):
        if probe <= last_open:
            expected = last_open
            break
        if is_trading_fn(probe):
            expected = probe
            break
        probe -= tf_ms
    if expected is None:
        return AgeResult(last_open_ms=last_open, expected_last_open_ms=None, age_buckets=None)
    return AgeResult(
        last_open_ms=last_open,
        expected_last_open_ms=expected,
        age_buckets=max(0, (expected - last_open) // tf_ms),
    )


def measure_holes(
    opens: Iterable[int],
    *,
    start_ms: int,
    end_ms: int,
    tf_ms: int,
    anchor_offset_ms: int,
    is_trading_fn: IsTradingFn,
    max_samples: int = 5,
) -> HolesResult:
    """Скільки торгових бакетів у вікні не мають бара."""
    present = {o for o in opens if start_ms <= o < end_ms}
    expected = expected_bucket_opens(start_ms, end_ms, tf_ms, anchor_offset_ms, is_trading_fn)
    missing = [b for b in expected if b not in present]
    return HolesResult(
        expected=len(expected),
        present=len(expected) - len(missing),
        missing=len(missing),
        missing_samples=tuple(missing[:max_samples]),
    )


def measure_geometry(
    bars: Sequence[CandleBar],
    *,
    tf_ms: int,
    anchor_offsets_ms: Sequence[int],
    max_samples: int = 5,
) -> GeometryResult:
    """Дублікати, порядок, сітка, close_ms і співвідношення OHLC.

    ``close_time_ms`` перевіряємо за end-exclusive конвенцією диску/SSOT (I2):
    ``close = open + tf_ms``. Redis-конвенція (``-1``) — інша межа, не тут.
    """
    opens = [b.open_time_ms for b in bars]
    exact_dup = len(opens) - len(set(opens))
    by_open: Dict[int, set] = {}
    for bar in bars:
        by_open.setdefault(bar.open_time_ms, set()).add((bar.o, bar.h, bar.low, bar.c))
    dup_conflicting = sum(1 for values in by_open.values() if len(values) > 1)
    unsorted = sum(1 for a, b in zip(opens, opens[1:]) if b < a)
    align_bad_list = [
        o
        for o in opens
        if normalize_open_to_grid(o, tf_ms=tf_ms, anchor_offsets_ms=anchor_offsets_ms) is None
    ]
    close_bad = sum(1 for b in bars if b.close_time_ms != b.open_time_ms + tf_ms)
    ohlc_bad = sum(
        1
        for b in bars
        if not (b.low <= b.o <= b.h and b.low <= b.c <= b.h and b.low <= b.h)
    )
    return GeometryResult(
        total=len(bars),
        exact_dup=exact_dup,
        dup_conflicting=dup_conflicting,
        unsorted=unsorted,
        align_bad=len(align_bad_list),
        close_bad=close_bad,
        ohlc_bad=ohlc_bad,
        align_bad_samples=tuple(align_bad_list[:max_samples]),
    )


def measure_cascade(
    derived_bars: Sequence[CandleBar],
    source_bars: Sequence[CandleBar],
    *,
    target_tf_ms: int,
    source_tf_ms: int,
    anchor_offsets_ms: Sequence[int],
    declares_partial_fn: Optional[Callable[[CandleBar], bool]] = None,
    price_epsilon: float = 1e-9,
    max_samples: int = 5,
) -> CascadeResult:
    """Чи кожен derived-бар дорівнює агрегації своїх source-барів.

    Бакет із неповним набором source-барів пропускається (``skipped_incomplete``):
    це нормально на межах сесії, і саме тому неповнота не рахується розбіжністю.
    """
    by_bucket: Dict[int, List[CandleBar]] = {}
    for bar in source_bars:
        for offset in anchor_offsets_ms:
            bucket = bucket_start_ms(bar.open_time_ms, target_tf_ms, offset)
            by_bucket.setdefault(bucket, []).append(bar)
    expected_children = max(1, target_tf_ms // source_tf_ms)

    checked = 0
    skipped = 0
    declared = 0
    mismatched: List[int] = []
    for bar in derived_bars:
        children = sorted(
            {c.open_time_ms: c for c in by_bucket.get(bar.open_time_ms, [])}.values(),
            key=lambda b: b.open_time_ms,
        )
        if len(children) < expected_children:
            skipped += 1
            continue
        checked += 1
        if (
            abs(children[0].o - bar.o) > price_epsilon
            or abs(children[-1].c - bar.c) > price_epsilon
            or abs(max(c.h for c in children) - bar.h) > price_epsilon
            or abs(min(c.low for c in children) - bar.low) > price_epsilon
        ):
            if declares_partial_fn is not None and declares_partial_fn(bar):
                declared += 1
            else:
                mismatched.append(bar.open_time_ms)
    return CascadeResult(
        checked=checked,
        mismatched=len(mismatched),
        declared_partial=declared,
        skipped_incomplete=skipped,
        mismatch_samples=tuple(mismatched[:max_samples]),
    )


def measure_depth(opens: Sequence[int], *, required_bars: int) -> DepthResult:
    """Скільки історії є і чи вистачає її для SMC-аналізу на цьому TF."""
    if not opens:
        return DepthResult(
            bars=0, span_days=0.0, first_open_ms=None, last_open_ms=None,
            enough=required_bars <= 0, required=required_bars,
        )
    first, last = min(opens), max(opens)
    return DepthResult(
        bars=len(opens),
        span_days=round((last - first) / 86_400_000, 2),
        first_open_ms=first,
        last_open_ms=last,
        enough=len(opens) >= required_bars,
        required=required_bars,
    )


def check_anchor_on_session_edge(
    anchor_open_ms: int,
    *,
    tf_ms: int,
    is_trading_fn: IsTradingFn,
) -> bool:
    """Чи якір HTF стоїть на межі сесії, а не всередині торгового дня.

    Межа — це точка, де торговість **змінюється**, і обидві її сторони легальні:
    H4 у нас якориться на відкритті (22:00, перша торгова хвилина після перерви),
    а D1 — на закритті (21:00, перша хвилина перерви). Вимога «якір торгується»
    відкидала б робочий D1-якір як дефект, тому дивимось саме на зміну стану.
    Зсунутий якір ріже добу навпіл — саме так колись «поїхали» D1-свічки.
    """
    if tf_ms <= 0:
        return False
    return is_trading_fn(anchor_open_ms) != is_trading_fn(anchor_open_ms - 60_000)
