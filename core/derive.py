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
           (300, 5)],     # M5  = 5 × M1
    300:  [(900, 3)],     # M15 = 3 × M5
    900:  [(1800, 2)],    # M30 = 2 × M15
    1800: [(3600, 2)],    # H1  = 2 × M30
    3600: [(14400, 4)],   # H4  = 4 × H1  (calendar-aware, TV anchor)
}

# Повний порядок виконання cascade (від найнижчого до найвищого)
DERIVE_ORDER: List[int] = [180, 300, 900, 1800, 3600, 14400]

# Зворотній маппінг: target_tf_s → (source_tf_s, bars_needed)
DERIVE_SOURCE: Dict[int, Tuple[int, int]] = {}
for _src_tf, _targets in DERIVE_CHAIN.items():
    for _tgt_tf, _n in _targets:
        DERIVE_SOURCE[_tgt_tf] = (_src_tf, _n)


def derive_chain_for(source_tf_s: int) -> List[Tuple[int, int]]:
    """Які target TF можна побудувати з source_tf_s."""
    return list(DERIVE_CHAIN.get(source_tf_s, []))


def full_cascade_from(source_tf_s: int) -> List[int]:
    """Повний cascade TF, які (транзитивно) залежать від source_tf_s.

    Наприклад, full_cascade_from(60) → [180, 300, 900, 1800, 3600, 14400].
    """
    result: List[int] = []
    queue = [t for t, _ in DERIVE_CHAIN.get(source_tf_s, [])]
    seen = set(queue)
    while queue:
        tf = queue.pop(0)
        result.append(tf)
        for child_tf, _ in DERIVE_CHAIN.get(tf, []):
            if child_tf not in seen:
                seen.add(child_tf)
                queue.append(child_tf)
    return sorted(result)


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
        # Вставка з підтримкою сортованості (bisect-like)
        # Оптимістичний шлях: новий бар — найновіший (append)
        if not self._sorted_keys or k > self._sorted_keys[-1]:
            self._sorted_keys.append(k)
        else:
            self._sorted_keys.append(k)
            self._sorted_keys.sort()
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
        extensions["partial_calendar_pause"] = True
        extensions["calendar_pause_count"] = len(bars) - len(trading)

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
# derive_bar — спроба побудувати 1 derived бар для конкретного bucket
# ---------------------------------------------------------------------------
def derive_bar(
    *,
    symbol: str,
    target_tf_s: int,
    source_buffer: "GenericBuffer",
    bucket_open_ms: int,
    anchor_offset_s: int = 0,
    is_trading_fn: Optional[Callable[[int], bool]] = None,
    filter_calendar_pause: bool = True,
) -> Optional[CandleBar]:
    """Будує derived бар для target_tf_s з source_buffer.

    Перевіряє:
    1. Що target_tf_s є в DERIVE_SOURCE (має відоме джерело).
    2. Що source_buffer.tf_s == очікуваний source TF.
    3. Що всі trading-слоти в діапазоні [bucket_open_ms, bucket_close_ms) є.
    4. Aggregate → CandleBar.

    Args:
        symbol: символ.
        target_tf_s: цільовий TF.
        source_buffer: буфер з барами джерельного TF.
        bucket_open_ms: open_time_ms цільового bucket.
        anchor_offset_s: для HTF (H4: TV anchor).
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

    target_tf_ms = target_tf_s * 1000
    bucket_close_ms = bucket_open_ms + target_tf_ms

    # Перевіряємо повноту source-барів для цього bucket
    if not source_buffer.has_range(
        bucket_open_ms, bucket_close_ms, is_trading_fn=is_trading_fn
    ):
        return None

    # Збираємо бари
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
        anchor_offset_s=anchor_offset_s,
        filter_calendar_pause=filter_calendar_pause,
    )


# ---------------------------------------------------------------------------
# try_derive_on_commit — визначає які derived бари можна побудувати після
#                        commit нового source-бару
# ---------------------------------------------------------------------------
def derive_triggers(source_bar: CandleBar, anchor_offset_s: int = 0) -> List[Tuple[int, int]]:
    """Визначає які target bucket'и слід перевірити після commit source_bar.

    Повертає список (target_tf_s, bucket_open_ms) для кожного
    target TF, де source_bar — потенційно останній бар у bucket.

    Логіка: для кожного target = N × source, target bucket завершується
    коли source_bar.open_time_ms == bucket_end_ms - source_tf_ms
    (тобто source_bar — останній слот у target bucket).
    """
    source_tf_s = source_bar.tf_s
    targets = DERIVE_CHAIN.get(source_tf_s, [])
    if not targets:
        return []

    result: List[Tuple[int, int]] = []
    source_tf_ms = source_tf_s * 1000

    for target_tf_s, _ in targets:
        target_tf_ms = target_tf_s * 1000
        anchor_offset_ms = anchor_offset_s * 1000 if target_tf_s >= 14400 else 0

        bucket_open = _bucket_start_ms(
            source_bar.open_time_ms, target_tf_ms, anchor_offset_ms
        )
        bucket_end = bucket_open + target_tf_ms

        # Перевіряємо чи source_bar — останній слот у target bucket
        expected_last_source = bucket_end - source_tf_ms
        if source_bar.open_time_ms == expected_last_source:
            result.append((target_tf_s, bucket_open))

    return result
