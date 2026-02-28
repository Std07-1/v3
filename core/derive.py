"""Каскадна деривація OHLCV — pure logic (без I/O).

Єдине місце визначення:
- DERIVE_CHAIN — декларативний ланцюг TF-деривації
- GenericBuffer — параметричний буфер барів будь-якого TF
- aggregate_bars() — агрегація N барів нижчого TF → 1 бар вищого TF
- derive_bar() — спроба побудувати derived бар для конкретного bucket

Dependency Rule: core/ не імпортує runtime/ui/tools.
Використовує тільки core.model.bars та core.buckets.
"""
from __future__ import annotations

import bisect
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.model.bars import CandleBar, assert_invariants
from core.buckets import bucket_start_ms as _bucket_start_ms


# ---------------------------------------------------------------------------
# DERIVE_CHAIN — SSOT декларативних правил каскадної деривації
# ---------------------------------------------------------------------------
# Формат: source_tf_s → [(target_tf_s, bars_needed), ...]
#   bars_needed = скільки source-барів потрібно для 1 target-бару
#
# Математична еквівалентність:
#   M30 = agg(2×M15) ≡ agg(6×M5) ≡ agg(30×M1)
#   Каскад дає ідентичний OHLCV, але з чистішою архітектурою.

DERIVE_CHAIN: Dict[int, List[Tuple[int, int]]] = {
    60:   [(180, 3),      # M3  = 3 × M1
           (300, 5),      # M5  = 5 × M1
           (86400, 1440)], # D1  = 1440 × M1 (ADR-0023: live derive from M1)
    300:  [(900, 3)],     # M15 = 3 × M5
    900:  [(1800, 2)],    # M30 = 2 × M15
    1800: [(3600, 2)],    # H1  = 2 × M30
    3600: [(14400, 4)],   # H4  = 4 × H1  (calendar-aware, TV anchor)
}

# Повний порядок виконання cascade (від найнижчого до найвищого)
# D1 перед M15 (900) бо source=M1 (60), не залежить від M5+
DERIVE_ORDER: List[int] = [180, 300, 86400, 900, 1800, 3600, 14400]

# Зворотній маппінг: target_tf_s → (source_tf_s, bars_needed)
DERIVE_SOURCE: Dict[int, Tuple[int, int]] = {}
for _src_tf, _targets in DERIVE_CHAIN.items():
    for _tgt_tf, _n in _targets:
        DERIVE_SOURCE[_tgt_tf] = (_src_tf, _n)

# ADR-0005: максимум пропущених mid-session source-слотів на 1 derived bucket.
# Rollback: встановити 0 → повна regression до попередньої поведінки.
# Per-TF override: D1 дозволяє більше gap-ів через 1440 source-слотів.
MAX_MID_SESSION_GAPS: int = 3
MAX_MID_SESSION_GAPS_BY_TF: Dict[int, int] = {
    86400: 15,  # D1: 1440 M1 slots, breaks/gaps можуть бути ширші
}


# ---------------------------------------------------------------------------
# resolve_cascade_anchor_s — SSOT для anchor routing по TF (ADR-0023)
# ---------------------------------------------------------------------------
def resolve_cascade_anchor_s(
    target_tf_s: int,
    h4_anchor_offset_s: int = 0,
    d1_anchor_offset_s: int = 0,
) -> int:
    """Єдина точка визначення anchor offset для каскадної деривації.

    Guardrail: будь-який новий HTF (Weekly тощо) — додати сюди.
    Без цієї функції anchor routing дублюється у 5+ місцях.

    Args:
        target_tf_s: цільовий TF.
        h4_anchor_offset_s: anchor для H4 (82800 = 23:00 UTC).
        d1_anchor_offset_s: anchor для D1 (79200 = 22:00 UTC, ADR-0023).

    Returns:
        anchor offset в секундах.
    """
    if target_tf_s == 86400:
        return d1_anchor_offset_s
    elif target_tf_s >= 14400:
        return h4_anchor_offset_s
    return 0


# ---------------------------------------------------------------------------
# GenericBuffer — параметричний буфер барів для будь-якого TF
# ---------------------------------------------------------------------------
class GenericBuffer:
    """In-memory буфер закритих барів одного TF.

    Параметризований tf_s — замінює M1Buffer та M5Buffer.
    Pure logic: без I/O, без Redis, без диску.

    Інваріанти:
    - Приймає тільки бари з відповідним tf_s.
    - Sorted keys для швидкого range_bars().
    - GC: зберігає не більше max_keep барів (FIFO).
    """

    __slots__ = ("_tf_s", "_tf_ms", "_max_keep", "_by_open_ms", "_sorted_keys")

    def __init__(self, tf_s: int, max_keep: int = 2000) -> None:
        if tf_s <= 0:
            raise ValueError(f"tf_s має бути > 0, отримано: {tf_s}")
        self._tf_s = tf_s
        self._tf_ms = tf_s * 1000
        self._max_keep = max(1, max_keep)
        self._by_open_ms: Dict[int, CandleBar] = {}
        self._sorted_keys: List[int] = []

    @property
    def tf_s(self) -> int:
        return self._tf_s

    @property
    def tf_ms(self) -> int:
        return self._tf_ms

    def __len__(self) -> int:
        return len(self._sorted_keys)

    def __contains__(self, open_ms: int) -> bool:
        return open_ms in self._by_open_ms

    # -- write --

    def upsert(self, bar: CandleBar) -> None:
        """Додає або оновлює бар у буфері."""
        if bar.tf_s != self._tf_s:
            raise ValueError(
                f"GenericBuffer(tf_s={self._tf_s}) отримав бар з tf_s={bar.tf_s}"
            )
        k = bar.open_time_ms
        if k in self._by_open_ms:
            self._by_open_ms[k] = bar
            return
        self._by_open_ms[k] = bar
        # Вставка з підтримкою сортованості.
        # Оптимістичний шлях: новий бар — найновіший (append)
        if not self._sorted_keys or k > self._sorted_keys[-1]:
            self._sorted_keys.append(k)
        else:
            bisect.insort(self._sorted_keys, k)
        self._gc()

    def upsert_many(self, bars: List[CandleBar]) -> int:
        """Batch upsert. Повертає кількість доданих/оновлених."""
        count = 0
        for bar in bars:
            self.upsert(bar)
            count += 1
        return count

    def _gc(self) -> None:
        if len(self._sorted_keys) <= self._max_keep:
            return
        drop = len(self._sorted_keys) - self._max_keep
        to_drop = self._sorted_keys[:drop]
        self._sorted_keys = self._sorted_keys[drop:]
        for k in to_drop:
            self._by_open_ms.pop(k, None)

    # -- read --

    def get(self, open_ms: int) -> Optional[CandleBar]:
        """Повертає бар за open_ms або None."""
        return self._by_open_ms.get(open_ms)

    def has_range(
        self,
        start_ms: int,
        end_ms: int,
        is_trading_fn: Optional[Callable[[int], bool]] = None,
    ) -> bool:
        """Чи всі trading-слоти від start_ms до end_ms (end-excl) є в буфері."""
        step = self._tf_ms
        for t in range(start_ms, end_ms, step):
            if is_trading_fn is not None and not is_trading_fn(t):
                continue
            if t not in self._by_open_ms:
                return False
        return True

    def range_bars(
        self,
        start_ms: int,
        end_ms: int,
        is_trading_fn: Optional[Callable[[int], bool]] = None,
    ) -> List[CandleBar]:
        """Повертає бари [start_ms, end_ms) з фільтрацією calendar pause.

        Повертає порожній список якщо хоча б один trading-слот відсутній.
        """
        step = self._tf_ms
        out: List[CandleBar] = []
        for t in range(start_ms, end_ms, step):
            if is_trading_fn is not None and not is_trading_fn(t):
                continue
            b = self._by_open_ms.get(t)
            if b is None:
                return []
            out.append(b)
        return out

    def missing_count(
        self,
        start_ms: int,
        end_ms: int,
        is_trading_fn: Optional[Callable[[int], bool]] = None,
    ) -> int:
        """Кількість відсутніх trading-слотів у діапазоні."""
        step = self._tf_ms
        missing = 0
        for t in range(start_ms, end_ms, step):
            if is_trading_fn is not None and not is_trading_fn(t):
                continue
            if t not in self._by_open_ms:
                missing += 1
        return missing

    def earliest_open_ms(self) -> Optional[int]:
        if not self._sorted_keys:
            return None
        return self._sorted_keys[0]

    def latest_open_ms(self) -> Optional[int]:
        if not self._sorted_keys:
            return None
        return self._sorted_keys[-1]

    def all_bars_sorted(self) -> List[CandleBar]:
        """Всі бари в хронологічному порядку."""
        return [self._by_open_ms[k] for k in self._sorted_keys]

    def clear(self) -> None:
        """Повне очищення буфера."""
        self._by_open_ms.clear()
        self._sorted_keys.clear()


# ---------------------------------------------------------------------------
# aggregate_bars — чиста агрегація N барів → 1 бар вищого TF
# ---------------------------------------------------------------------------
def aggregate_bars(
    bars: List[CandleBar],
    *,
    symbol: str,
    target_tf_s: int,
    bucket_open_ms: int,
    anchor_offset_s: int = 0,
    filter_calendar_pause: bool = True,
) -> Optional[CandleBar]:
    """Агрегує N барів нижчого TF → 1 бар вищого TF.

    Pure function: без I/O, без side effects.

    Args:
        bars: бари нижчого TF (sorted by open_time_ms asc).
        symbol: символ.
        target_tf_s: цільовий TF у секундах.
        bucket_open_ms: open_time_ms цільового бару.
        anchor_offset_s: anchor offset для HTF (H4/D1).
        filter_calendar_pause: чи фільтрувати calendar_pause_flat бари.

    Returns:
        CandleBar з complete=True, src="derived", або None якщо немає даних.
    """
    if not bars:
        return None

    # Фільтрація calendar-pause flat барів (якщо увімкнено)
    if filter_calendar_pause:
        trading = [b for b in bars if not b.extensions.get("calendar_pause_flat")]
    else:
        trading = list(bars)

    if not trading:
        return None

    target_tf_ms = target_tf_s * 1000
    close_ms = bucket_open_ms + target_tf_ms

    extensions: Dict[str, Any] = {}
    if len(trading) < len(bars):
        extensions["partial"] = True
        extensions["partial_calendar_pause"] = True
        extensions["calendar_pause_count"] = len(bars) - len(trading)
        extensions["source_count"] = len(trading)
        extensions["expected_count"] = len(bars)
        extensions["partial_reasons"] = ["calendar_pause"]

    out = CandleBar(
        symbol=symbol,
        tf_s=target_tf_s,
        open_time_ms=bucket_open_ms,
        close_time_ms=close_ms,
        o=trading[0].o,
        h=max(b.h for b in trading),
        low=min(b.low for b in trading),
        c=trading[-1].c,
        v=sum(b.v for b in trading),
        complete=True,
        src="derived",
        extensions=extensions,
    )
    assert_invariants(out, anchor_offset_s=anchor_offset_s)
    return out


# ---------------------------------------------------------------------------
# _collect_boundary_tolerant — збір барів з толерантністю до session boundary
# ---------------------------------------------------------------------------
def _collect_boundary_tolerant(
    source_buffer: "GenericBuffer",
    start_ms: int,
    end_ms: int,
    is_trading_fn: Callable[[int], bool],
    max_mid_session_gaps: int = 0,
) -> Optional[Tuple[List[CandleBar], int]]:
    """Збір source-барів з толерантністю до boundary та mid-session gaps.

    Boundary tolerance (Entry 075):
    - session open: is_trading_fn(t)=True, is_trading_fn(t - step)=False
    - session close: is_trading_fn(t)=True, is_trading_fn(t + step)=False

    Mid-session tolerance (ADR-0005):
    - Gap у середині сесії дозволений якщо mid_session_skips ≤ max_mid_session_gaps.
    - Для неліквідних інструментів (NGAS, HKG33): broker не надає M1 для хвилин без угод.
    - Перевищення бюджету → None.

    Returns:
        Tuple (bars, mid_session_skips) або None.
    """
    step = source_buffer.tf_ms
    bars: List[CandleBar] = []
    boundary_skips = 0
    mid_session_skips = 0

    for t in range(start_ms, end_ms, step):
        # Не торговий — пропускаємо (calendar pause / break)
        if not is_trading_fn(t):
            continue

        bar = source_buffer.get(t)
        if bar is not None:
            bars.append(bar)
            continue

        # Бар відсутній. Перевіряємо чи це session boundary gap.
        is_open_boundary = not is_trading_fn(t - step)   # перша хвилина сесії
        is_close_boundary = not is_trading_fn(t + step)  # остання хвилина сесії

        if is_open_boundary or is_close_boundary:
            boundary_skips += 1
            continue

        # Mid-session gap (ADR-0005): дозволяємо в межах бюджету
        mid_session_skips += 1
        if mid_session_skips > max_mid_session_gaps:
            return None

    if not bars:
        return None

    return (bars, mid_session_skips)


# ---------------------------------------------------------------------------
# derive_bar — спроба побудувати 1 derived бар для конкретного bucket
# ---------------------------------------------------------------------------
def derive_bar(
    *,
    symbol: str,
    target_tf_s: int,
    source_buffer: "GenericBuffer",
    bucket_open_ms: int,
    anchor_offset_s: int = 0,
    d1_anchor_offset_s: int = 0,
    is_trading_fn: Optional[Callable[[int], bool]] = None,
    filter_calendar_pause: bool = True,
) -> Optional[CandleBar]:
    """Будує derived бар для target_tf_s з source_buffer.

    Перевіряє:
    1. Що target_tf_s є в DERIVE_SOURCE (має відоме джерело).
    2. Що source_buffer.tf_s == очікуваний source TF.
    3. Що всі trading-слоти в діапазоні [bucket_open_ms, bucket_close_ms) є.
       Fallback: boundary-tolerant збір (пропуск барів на межі сесії).
    4. Aggregate → CandleBar.

    Boundary tolerance (degraded-but-loud, §9):
    Якщо strict has_range() не пройшов, але is_trading_fn задано — спробувати
    зібрати бари з пропуском session open/close boundary gaps. Результат
    отримує extension boundary_partial=True + source_count/expected_count.

    Args:
        symbol: символ.
        target_tf_s: цільовий TF.
        source_buffer: буфер з барами джерельного TF.
        bucket_open_ms: open_time_ms цільового bucket.
        anchor_offset_s: для HTF (H4: TV anchor 82800).
        d1_anchor_offset_s: D1 anchor (79200, ADR-0023).
        is_trading_fn: calendar filter (is_trading_minute).
        filter_calendar_pause: чи ігнорувати calendar_pause_flat.

    Returns:
        CandleBar або None.
    """
    source_info = DERIVE_SOURCE.get(target_tf_s)
    if source_info is None:
        return None

    expected_source_tf_s, _ = source_info
    if source_buffer.tf_s != expected_source_tf_s:
        return None

    # Централізований anchor routing (ADR-0023)
    effective_anchor = resolve_cascade_anchor_s(target_tf_s, anchor_offset_s, d1_anchor_offset_s)

    target_tf_ms = target_tf_s * 1000
    bucket_close_ms = bucket_open_ms + target_tf_ms

    # Strict: всі trading-слоти присутні
    if source_buffer.has_range(
        bucket_open_ms, bucket_close_ms, is_trading_fn=is_trading_fn
    ):
        bars = source_buffer.range_bars(
            bucket_open_ms, bucket_close_ms, is_trading_fn=is_trading_fn
        )
        if not bars:
            return None
        return aggregate_bars(
            bars,
            symbol=symbol,
            target_tf_s=target_tf_s,
            bucket_open_ms=bucket_open_ms,
            anchor_offset_s=effective_anchor,
            filter_calendar_pause=filter_calendar_pause,
        )

    # Fallback: boundary-tolerant збір (тільки якщо є calendar)
    if is_trading_fn is None:
        return None

    # Per-TF max_mid_session_gaps (ADR-0023: D1 дозволяє більше gaps)
    tf_max_gaps = MAX_MID_SESSION_GAPS_BY_TF.get(target_tf_s, MAX_MID_SESSION_GAPS)

    result_tol = _collect_boundary_tolerant(
        source_buffer, bucket_open_ms, bucket_close_ms, is_trading_fn,
        max_mid_session_gaps=tf_max_gaps,
    )
    if not result_tol:
        return None
    bars, mid_gaps = result_tol

    # Рахуємо expected trading slots для degraded metadata
    step = source_buffer.tf_ms
    expected_trading = sum(
        1 for t in range(bucket_open_ms, bucket_close_ms, step)
        if is_trading_fn(t)
    )

    result = aggregate_bars(
        bars,
        symbol=symbol,
        target_tf_s=target_tf_s,
        bucket_open_ms=bucket_open_ms,
        anchor_offset_s=effective_anchor,
        filter_calendar_pause=filter_calendar_pause,
    )
    if result is None:
        return None

    # Degraded-but-loud: позначаємо як boundary partial (§9)
    result.extensions["partial"] = True
    result.extensions["boundary_partial"] = True
    result.extensions["source_count"] = int(result.extensions.get("source_count", len(bars)))
    result.extensions["expected_count"] = expected_trading
    reasons = result.extensions.get("partial_reasons")
    if not isinstance(reasons, list):
        reasons = []
    if "boundary_gap" not in reasons:
        reasons.append("boundary_gap")
    result.extensions["partial_reasons"] = reasons
    if mid_gaps > 0:
        result.extensions["mid_session_gaps"] = mid_gaps
    return result


# ---------------------------------------------------------------------------
# _has_any_trading_in_range — чи є хоча б одна торгова хвилина в діапазоні
# ---------------------------------------------------------------------------
def _has_any_trading_in_range(
    start_ms: int,
    end_ms: int,
    is_trading_fn: Callable[[int], bool],
) -> bool:
    """Чи є хоча б одна торгова хвилина в [start_ms, end_ms).

    Перевіряє кожну хвилину в діапазоні. Для M5-слоту це 5 хвилин,
    для H1-слоту — 60 хвилин. Прийнятна вартість для trigger-контексту.
    """
    _MINUTE_MS = 60_000
    for t in range(start_ms, end_ms, _MINUTE_MS):
        if is_trading_fn(t):
            return True
    return False


# ---------------------------------------------------------------------------
# try_derive_on_commit — визначає які derived бари можна побудувати після
#                        commit нового source-бару
# ---------------------------------------------------------------------------
def derive_triggers(
    source_bar: CandleBar,
    anchor_offset_s: int = 0,
    is_trading_fn: Optional[Callable[[int], bool]] = None,
    d1_anchor_offset_s: int = 0,
) -> List[Tuple[int, int]]:
    """Визначає які target bucket'и слід перевірити після commit source_bar.

    Повертає список (target_tf_s, bucket_open_ms) для кожного
    target TF, де source_bar — потенційно останній бар у bucket.

    Логіка: для кожного target = N × source, target bucket завершується
    коли source_bar.open_time_ms == bucket_end_ms - source_tf_ms
    (тобто source_bar — останній слот у target bucket).

    Calendar-aware (is_trading_fn):
    Якщо останній номінальний source-слот потрапляє на non-trading час
    (daily break), шукаємо останній слот з торговою активністю.
    Це фіксить H4 19:00 для груп cfd_us_22_23 / fx_24x5_utc_winter,
    де partial break перекриває останній H1/M30/M15/M5 у bucket.

    Args:
        anchor_offset_s: H4 anchor (82800 = 23:00 UTC).
        d1_anchor_offset_s: D1 anchor (79200 = 22:00 UTC, ADR-0023).
    """
    source_tf_s = source_bar.tf_s
    targets = DERIVE_CHAIN.get(source_tf_s, [])
    if not targets:
        return []

    result: List[Tuple[int, int]] = []
    source_tf_ms = source_tf_s * 1000

    for target_tf_s, _ in targets:
        target_tf_ms = target_tf_s * 1000
        anchor = resolve_cascade_anchor_s(target_tf_s, anchor_offset_s, d1_anchor_offset_s)
        anchor_offset_ms = anchor * 1000

        bucket_open = _bucket_start_ms(
            source_bar.open_time_ms, target_tf_ms, anchor_offset_ms
        )
        bucket_end = bucket_open + target_tf_ms

        # Номінальний останній source-слот
        expected_last_source = bucket_end - source_tf_ms

        # Calendar-aware: якщо останній слот non-trading, крокуємо назад
        if is_trading_fn is not None:
            candidate = expected_last_source
            while candidate >= bucket_open:
                if _has_any_trading_in_range(
                    candidate, candidate + source_tf_ms, is_trading_fn
                ):
                    break
                candidate -= source_tf_ms
            else:
                # Жодного торгового слоту в bucket — пропускаємо
                continue
            expected_last_source = candidate

        if source_bar.open_time_ms == expected_last_source:
            result.append((target_tf_s, bucket_open))

    return result
