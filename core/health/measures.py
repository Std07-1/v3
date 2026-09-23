"""core/health/measures.py — pure-виміри здоров'я символу (ADR-0054 Фаза 1).

Кожна функція: дані на вході → факт на виході. Нуль I/O, нуль імпортів з ``runtime``
(календар приходить як ``is_trading_fn``), тому виміри тестуються на синтетичних барах
і однаково працюють для диску, Redis чи майбутнього джерела.

Що вимірюємо і навіщо (ADR-0054 §3.2):

- ``bucket_age`` — чи не відстає останній бар від очікуваного закритого бакета.
- ``holes`` — яких торгових бакетів немає взагалі.
- ``geometry`` — дублікати, порядок, сітка (``align_bad`` для M1..H1, ``off_season_grid`` для H4/D1), close_ms.
- ``cascade`` — чи derived-бар справді дорівнює агрегації свого source.
- ``root`` — чи derived-бар дорівнює агрегації M1 у своєму бакеті (корінь ланцюга, ADR-0002).
- ``history_depth`` — чи вистачає глибини для SMC (lookback вищих TF).

Сітка бакетів одна — сезонна (ADR-0095): виміри приймають ``tf_s`` і правило якоря символу, а кінець бакета
беруть з ``htf_next_bucket_start_ms``, а не ``open + tf``. Тому H4-обрубок доби переходу DST (1 або 3 год) —
окремий бакет, доба D1 має 23/24/25 год, а бар H4/D1 на «іншому» якорі — дефект, не DST-альтернатива.

Клас дефекту, заради якого це існує: 06.09 засів NAS100 виглядав цілим (M1 і H4
доходили до вересня), а D1 тихо обірвався на два місяці раніше — око цього не бачить,
вимір бачить.
"""
from __future__ import annotations

import bisect
import dataclasses
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from core.model.bar_choice import choose_better_bar
from core.model.bars import CandleBar
from core.session_anchor import (
    H4_S,
    OffSeasonGridError,
    assert_on_season_grid,
    htf_bucket_start_ms,
    htf_next_bucket_start_ms,
)

IsTradingFn = Callable[[int], bool]

# Крок календаря: `is_trading_fn` визначена на хвилинах, тому всі проби — хвилинні.
MINUTE_MS = 60_000

# Скільки ХВИЛИН назад шукаємо останню торгову: 2 тижні (довгі вихідні + свята).
# Саме хвилин, а не бакетів, тому горизонт однаковий для M1 і для D1.
MAX_BACKWARD_MINUTE_PROBES = 20_160


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

    Сітку міряє одне з двох полів, ніколи обидва: ``align_bad`` — M1..H1, відкриття не кратне TF;
    ``off_season_grid`` — H4/D1, відкриття не дорівнює сезонному бакету символу (ADR-0095 §3.3). Семпли
    ``off_season_grid_samples`` — пари ``(open_ms, expected_open_ms)``: куди бар мав стати.
    """

    total: int
    exact_dup: int
    dup_conflicting: int
    unsorted: int
    align_bad: int
    off_season_grid: int
    close_bad: int
    ohlc_bad: int
    align_bad_samples: Tuple[int, ...]
    off_season_grid_samples: Tuple[Tuple[int, int], ...]


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
class RootResult:
    """Чи derived-бари дорівнюють агрегації M1 у своєму бакеті — корені ланцюга деривації.

    Навіщо окремо від ``cascade``. Каскад порівнює лише сусідні рівні і пропускає бакети з
    неповним набором дітей: M30, зібраний колись із застарілого M15, узгоджений з тим M15 і
    для каскаду чистий, хоча з M1 розходиться. На живих даних 14.09.2026 каскад бачив 137
    розбіжностей на XAU/XAG, а проти M1 їх 659 — плюс 6 на SPX500, який каскад вважав GREEN.

    ``uncovered`` — бари, у бакеті яких M1 немає зовсім (історія, старша за M1-покриття,
    наприклад брокерський імпорт): перевірити їх нема чим, це не дефект і не «ок».
    """

    checked: int
    mismatched: int
    declared_partial: int
    uncovered: int
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


def bucket_has_trading_minute(
    bucket_open_ms: int,
    bucket_close_ms: int,
    is_trading_fn: IsTradingFn,
) -> bool:
    """Чи є в бакеті хоч одна торгова хвилина — тобто чи має writer дати для нього бар.

    Це предикат САМОГО writer'а: ``derive_bar`` збирає source-бари з торгових слотів
    бакета і віддає ``None`` лише коли не набралось жодного
    (``core/derive.py`` → ``_collect_boundary_tolerant``: ``if not bars: return None``).

    Дешевий сурогат «торгується ПЕРША хвилина бакета» тут не працює і саме він зробив
    D1-вимір сліпим (ADR-0054 §3.8 п.1): D1 у нас якориться на ЗАКРИТТІ дня
    (21:00 UTC влітку = перша хвилина денної перерви), тому для 13 з 15 символів
    ``config.json`` очікуваних D1-бакетів виходило РІВНО НУЛЬ — ні дірок, ні
    відставання не міг показати жоден звіт.
    """
    for minute_ms in range(bucket_open_ms, bucket_close_ms, MINUTE_MS):
        if is_trading_fn(minute_ms):
            return True
    return False


def expected_bucket_opens(
    start_ms: int,
    end_ms: int,
    *,
    tf_s: int,
    rule: str,
    is_trading_fn: IsTradingFn,
) -> List[int]:
    """Торгові бакети сезонної сітки, що ПОВНІСТЮ лежать у ``[start_ms, end_ms)``.

    Дві умови, і обидві — про writer'а, а не про календар сам по собі:

    - бакет містить торгову хвилину (``bucket_has_trading_minute``) у своєму вікні до наступного бакета;
    - бакет уже закрився до ``end_ms``, тобто наступний бакет почався не пізніше ``end_ms``. Незакритий
      бакет — не «дірка», бар для нього ще пишеться.
    """
    if end_ms <= start_ms:
        return []
    return [
        bucket_open
        for bucket_open, bucket_end in _closed_grid_buckets(start_ms, end_ms, tf_s, rule)
        if bucket_has_trading_minute(bucket_open, bucket_end, is_trading_fn)
    ]


def measure_age(
    opens: Sequence[int],
    *,
    now_ms: int,
    tf_s: int,
    rule: str,
    is_trading_fn: IsTradingFn,
) -> AgeResult:
    """Скільки бакетів сезонної сітки минуло після останнього наявного бара до останнього закритого."""
    if not opens:
        return AgeResult(last_open_ms=None, expected_last_open_ms=None, age_buckets=None)
    last_open = max(opens)
    current_open = htf_bucket_start_ms(now_ms, tf_s, rule)
    # Останній ЗАКРИТИЙ бакет, за який writer мав дати бар (поточний ще формується) —
    # це бакет, що містить останню торгову ХВИЛИНУ перед поточним бакетом.
    # Шукаємо саму хвилину, а не питаємо «чи торгується відкриття бакета»: у D1
    # відкриття припадає на денну перерву, тож старий предикат не знаходив жодного
    # бакета, доходив до ``last_open`` і тихо повертав age=0 навіть для ряду,
    # обірваного два місяці тому (ADR-0054 §3.8 п.1).
    expected = None
    probe_minute = current_open - MINUTE_MS
    for _ in range(MAX_BACKWARD_MINUTE_PROBES):
        if is_trading_fn(probe_minute):
            expected = htf_bucket_start_ms(probe_minute, tf_s, rule)
            break
        probe_minute -= MINUTE_MS
    if expected is None:
        return AgeResult(last_open_ms=last_open, expected_last_open_ms=None, age_buckets=None)
    return AgeResult(
        last_open_ms=last_open,
        expected_last_open_ms=expected,
        age_buckets=_grid_steps_between(last_open, expected, tf_s, rule),
    )


def measure_holes(
    opens: Iterable[int],
    *,
    start_ms: int,
    end_ms: int,
    tf_s: int,
    rule: str,
    is_trading_fn: IsTradingFn,
    max_samples: int = 5,
) -> HolesResult:
    """Скільки торгових бакетів сезонної сітки у вікні не мають бара."""
    present = {o for o in opens if start_ms <= o < end_ms}
    expected = expected_bucket_opens(start_ms, end_ms, tf_s=tf_s, rule=rule, is_trading_fn=is_trading_fn)
    missing = [b for b in expected if b not in present]
    return HolesResult(
        expected=len(expected),
        present=len(expected) - len(missing),
        missing=len(missing),
        missing_samples=tuple(missing[:max_samples]),
    )


def _closed_grid_buckets(start_ms: int, end_ms: int, tf_s: int, rule: str) -> Iterator[Tuple[int, int]]:
    """Бакети сітки ``(open, next_open)`` з ``start_ms <= open`` і ``next_open <= end_ms``, по зростанню."""
    bucket_open = htf_bucket_start_ms(start_ms, tf_s, rule)
    if bucket_open < start_ms:
        bucket_open = htf_next_bucket_start_ms(bucket_open, tf_s, rule)
    while True:
        bucket_end = htf_next_bucket_start_ms(bucket_open, tf_s, rule)
        if bucket_end > end_ms:
            return
        yield bucket_open, bucket_end
        bucket_open = bucket_end


def _grid_steps_between(from_ms: int, to_open_ms: int, tf_s: int, rule: str) -> int:
    """Кроків сітки від бакета, що містить ``from_ms``, до бакета ``to_open_ms``; 0, якщо він не далі.

    H4/D1 рахуємо ітератором, а не ``(to - from) // tf_ms``: осінній H4-обрубок (1 год) арифметика
    недорахувала б, а доби на 23/25 год зсунули б ділення. M1..H1 мають рівний крок — там ділення точне
    і не ганяє цикл по місяцях хвилин обірваного ряду.
    """
    bucket_open = htf_bucket_start_ms(from_ms, tf_s, rule)
    if tf_s < H4_S:
        return max(0, (to_open_ms - bucket_open) // (tf_s * 1000))
    steps = 0
    while bucket_open < to_open_ms:
        bucket_open = htf_next_bucket_start_ms(bucket_open, tf_s, rule)
        steps += 1
    return steps


def measure_geometry(
    bars: Sequence[CandleBar],
    *,
    tf_s: int,
    rule: str,
    max_samples: int = 5,
) -> GeometryResult:
    """Дублікати, порядок, сітка, close_ms і співвідношення OHLC.

    Сітку перевіряє та сама ``assert_on_season_grid``, що й писар SSOT (ADR-0095 R3): M1..H1 — кратність TF
    (``align_bad``), H4/D1 — рівність сезонному бакету правила (``off_season_grid``). Набору «дозволених»
    якорів більше немає: H4 22:00 влітку — дефект з очікуваним 21:00, а не DST-альтернатива.

    ``close_time_ms`` перевіряємо за end-exclusive конвенцією диску/SSOT (I2):
    ``close = open + tf``. Redis-конвенція (``-1``) — інша межа, не тут.
    """
    tf_ms = tf_s * 1000
    opens = [b.open_time_ms for b in bars]
    exact_dup = len(opens) - len(set(opens))
    by_open: Dict[int, set] = {}
    for bar in bars:
        by_open.setdefault(bar.open_time_ms, set()).add((bar.o, bar.h, bar.low, bar.c))
    dup_conflicting = sum(1 for values in by_open.values() if len(values) > 1)
    unsorted = sum(1 for a, b in zip(opens, opens[1:]) if b < a)
    off_grid = _off_grid_opens(opens, tf_s, rule)
    off_season = off_grid if tf_s >= H4_S else []
    align_bad_list = [] if tf_s >= H4_S else [open_ms for open_ms, _expected in off_grid]
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
        off_season_grid=len(off_season),
        close_bad=close_bad,
        ohlc_bad=ohlc_bad,
        align_bad_samples=tuple(align_bad_list[:max_samples]),
        off_season_grid_samples=tuple(off_season[:max_samples]),
    )


def _off_grid_opens(opens: Iterable[int], tf_s: int, rule: str) -> List[Tuple[int, int]]:
    """``(open_ms, expected_open_ms)`` кожного відкриття поза сіткою — через єдину перевірку писаря."""
    off_grid: List[Tuple[int, int]] = []
    for open_ms in opens:
        try:
            assert_on_season_grid(open_ms, tf_s, rule)
        except OffSeasonGridError as exc:
            off_grid.append((open_ms, exc.expected_open_ms))
    return off_grid


def _choice_view(bar: CandleBar) -> Dict[str, Any]:
    """Поля, на які дивиться єдиний вибирач `core.model.bar_choice`."""
    return {"complete": bar.complete, "src": bar.src, "extensions": bar.extensions}


def ssot_winners(bars: Sequence[CandleBar]) -> List[CandleBar]:
    """Згорнути дублікати open_time_ms так, як їх згортають читачі (ADR-0094), за зростанням ключа.

    Вимір мусить бачити рівно те, що бачить графік. До цього health перевіряв КОЖЕН запис
    дубліката (переможений запис батька давав хибну «розбіжність каскаду» — 18 таких на
    XAU/XAG), а дітей згортав позиційно — третім вибирачем у репо. `bars` — у порядку диска:
    нічия вибирача дістається пізнішому запису.
    """
    chosen: Dict[int, Tuple[CandleBar, Dict[str, Any]]] = {}
    for bar in bars:
        view = _choice_view(bar)
        previous = chosen.get(bar.open_time_ms)
        if previous is None or choose_better_bar(previous[1], view) is view:
            chosen[bar.open_time_ms] = (bar, view)
    return [chosen[key][0] for key in sorted(chosen)]


def _matches_aggregate(bar: CandleBar, children: Sequence[CandleBar], price_epsilon: float) -> bool:
    return (
        abs(children[0].o - bar.o) <= price_epsilon
        and abs(children[-1].c - bar.c) <= price_epsilon
        and abs(max(c.h for c in children) - bar.h) <= price_epsilon
        and abs(min(c.low for c in children) - bar.low) <= price_epsilon
    )


def measure_cascade(
    derived_bars: Sequence[CandleBar],
    source_bars: Sequence[CandleBar],
    *,
    target_tf_s: int,
    source_tf_s: int,
    rule: str,
    declares_partial_fn: Optional[Callable[[CandleBar], bool]] = None,
    price_epsilon: float = 1e-9,
    max_samples: int = 5,
) -> CascadeResult:
    """Чи кожен derived-бар дорівнює агрегації своїх source-барів.

    Кожен source-бар належить рівно одному бакету сезонної сітки (ADR-0095), а повний набір дітей
    рахується з вікна бакета до наступного: H4-обрубок осінньої доби DST (1 год) повний з одним H1.
    Бакет із неповним набором source-барів пропускається (``skipped_incomplete``):
    це нормально на межах сесії, і саме тому неповнота не рахується розбіжністю. Бар поза сіткою дітей
    у своєму бакеті не має і теж іде сюди — дефектом його рахує ``geometry.off_season_grid``.
    """
    by_bucket: Dict[int, List[CandleBar]] = {}
    for bar in source_bars:
        bucket = htf_bucket_start_ms(bar.open_time_ms, target_tf_s, rule)
        by_bucket.setdefault(bucket, []).append(bar)
    source_tf_ms = source_tf_s * 1000

    checked = 0
    skipped = 0
    declared = 0
    mismatched: List[int] = []
    # І батьків, і дітей — так, як їх показують читачі (ADR-0094), а не кожен запис на диску.
    for bar in ssot_winners(derived_bars):
        children = ssot_winners(by_bucket.get(bar.open_time_ms, []))
        bucket_end = htf_next_bucket_start_ms(bar.open_time_ms, target_tf_s, rule)
        expected_children = max(1, (bucket_end - bar.open_time_ms) // source_tf_ms)
        if len(children) < expected_children:
            skipped += 1
            continue
        checked += 1
        if not _matches_aggregate(bar, children, price_epsilon):
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


def measure_root_consistency(
    derived_bars: Sequence[CandleBar],
    m1_bars: Sequence[CandleBar],
    *,
    tf_s: int,
    rule: str,
    declares_partial_fn: Optional[Callable[[CandleBar], bool]] = None,
    price_epsilon: float = 1e-9,
    max_samples: int = 5,
) -> RootResult:
    """Кожен derived-бар (як його бачать читачі) проти агрегації M1 у ``[open, наступний бакет сітки)``.

    Кінець вікна — ``htf_next_bucket_start_ms`` (ADR-0095), а не ``open + tf``: H4-обрубок доби переходу
    DST закінчується на відкритті нового торгового дня і не тягне в себе хвилини наступного бакета.
    Бакет не мусить мати ПОВНИЙ набір хвилин: derived-бар будується з тих хвилин, що є, тож
    на незмінному M1 агрегація збігається і з частковим набором. Розбіжність означає, що бар
    зібрано з інших даних, ніж зараз лежать у M1 (M1 перезалили, бар не перебудували).
    """
    minutes = ssot_winners(m1_bars)
    keys = [bar.open_time_ms for bar in minutes]
    checked = declared = uncovered = 0
    mismatched: List[int] = []
    for bar in ssot_winners(derived_bars):
        lo = bisect.bisect_left(keys, bar.open_time_ms)
        hi = bisect.bisect_left(keys, htf_next_bucket_start_ms(bar.open_time_ms, tf_s, rule))
        if lo == hi:
            uncovered += 1
            continue
        checked += 1
        if _matches_aggregate(bar, minutes[lo:hi], price_epsilon):
            continue
        if declares_partial_fn is not None and declares_partial_fn(bar):
            declared += 1
        else:
            mismatched.append(bar.open_time_ms)
    return RootResult(
        checked=checked,
        mismatched=len(mismatched),
        declared_partial=declared,
        uncovered=uncovered,
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

    Межа — це точка, де торговість **змінюється**, і обидві її сторони легальні. D1 і H4 якоряться
    на відкритті торгового дня ADR-0095 — 17:00 America/New_York: 21:00 UTC влітку, 22:00 UTC взимку.
    Для ``cfd_us`` влітку це перша хвилина денної перерви (торговість вимикається), а взимку на
    статичному літньому календарі (до ADR-0095 S6b) — перша хвилина після перерви (вмикається).
    Вимога «якір торгується» відкидала б літній якір як дефект, тому дивимось саме на зміну стану.
    Зсунутий якір ріже добу навпіл — саме так колись «поїхали» D1-свічки. Рівність сезонній сітці
    міряє ``geometry.off_season_grid``; тут — лише що сітка узгоджена з календарем символу.
    """
    if tf_ms <= 0:
        return False
    return is_trading_fn(anchor_open_ms) != is_trading_fn(anchor_open_ms - 60_000)
