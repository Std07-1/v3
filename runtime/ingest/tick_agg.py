"""Preview-агрегація тиков у бари (preview-plane).

Підтримує auto-promote: при rollover бакету повертає
завершений (promoted) бар попередньої хвилини одразу,
не чекаючи M1 poller (+8 с).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

from core.buckets import bucket_start_ms, tf_to_ms
from core.model.bars import CandleBar


@dataclass
class _BucketState:
    open_ms: int
    close_ms: int
    open_tick_ts_ms: int
    last_tick_ts_ms: int
    o: float
    h: float
    low: float
    c: float
    v: float
    ticks_n: int


# Результат update: (promoted_bar | None, preview_bar | None)
UpdateResult = Tuple[Optional[CandleBar], Optional[CandleBar]]


class TickAggregator:
    """Агрегує тики у preview-бари з open=перший тик бакету.

    auto_promote=True: при rollover (перший тик нового бакету)
    повертає завершений бар попереднього бакету як complete=True,
    src="tick_promoted". UI бачить "миттєвий final" до приходу
    справжнього History final.
    """

    def __init__(
        self,
        tf_allowlist: Iterable[int] = (60, 180),
        *,
        source: str = "preview_tick",
        anchor_offset_ms: int = 0,
        auto_promote: bool = False,
    ) -> None:
        self._tf_allowlist = set(int(v) for v in tf_allowlist)
        self._source = str(source)
        self._anchor_offset_ms = int(anchor_offset_ms)
        self._auto_promote = bool(auto_promote)
        self._state: Dict[Tuple[str, int], _BucketState] = {}
        self._stats = {
            "ticks_total": 0,
            "ticks_rejected_tf": 0,
            "ticks_dropped_late_bucket": 0,
            "ticks_dropped_before_open": 0,
            "ticks_dropped_out_of_order": 0,
            "promoted_total": 0,
        }

    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def update(
        self,
        symbol: str,
        tf_s: int,
        tick_ts_ms: int,
        price: float,
    ) -> UpdateResult:
        """Повертає (promoted_bar, preview_bar).

        promoted_bar: якщо auto_promote і відбувся rollover —
            завершений бар попереднього бакету (complete=True).
        preview_bar: поточний бакет (complete=False).
        Кожен може бути None.
        """
        self._stats["ticks_total"] += 1
        if tf_s not in self._tf_allowlist:
            self._stats["ticks_rejected_tf"] += 1
            return (None, None)

        tf_ms = tf_to_ms(int(tf_s))
        open_ms = bucket_start_ms(int(tick_ts_ms), tf_ms, self._anchor_offset_ms)
        close_ms = int(open_ms + tf_ms)
        key = (str(symbol), int(tf_s))
        state = self._state.get(key)

        if state is not None and open_ms < state.open_ms:
            self._stats["ticks_dropped_late_bucket"] += 1
            return (None, None)
        if state is None or state.open_ms != open_ms:
            # Rollover: новий бакет
            promoted: Optional[CandleBar] = None
            if state is not None and self._auto_promote:
                promoted = self._to_promoted(symbol, tf_s, state)
                self._stats["promoted_total"] += 1
            state = _BucketState(
                open_ms=open_ms,
                close_ms=close_ms,
                open_tick_ts_ms=int(tick_ts_ms),
                last_tick_ts_ms=int(tick_ts_ms),
                o=float(price),
                h=float(price),
                low=float(price),
                c=float(price),
                v=0.0,
                ticks_n=1,
            )
            self._state[key] = state
            return (promoted, self._to_bar(symbol, tf_s, state))

        if tick_ts_ms < state.open_tick_ts_ms:
            self._stats["ticks_dropped_before_open"] += 1
            return (None, None)
        if tick_ts_ms < state.last_tick_ts_ms:
            self._stats["ticks_dropped_out_of_order"] += 1
            return (None, None)

        state.last_tick_ts_ms = int(tick_ts_ms)
        state.c = float(price)
        state.ticks_n += 1
        if price > state.h:
            state.h = float(price)
        if price < state.low:
            state.low = float(price)
        return (None, self._to_bar(symbol, tf_s, state))

    def _to_bar(self, symbol: str, tf_s: int, state: _BucketState) -> CandleBar:
        extensions: Dict[str, Any] = {"ticks_n": state.ticks_n}
        return CandleBar(
            symbol=str(symbol),
            tf_s=int(tf_s),
            open_time_ms=int(state.open_ms),
            close_time_ms=int(state.close_ms),
            o=float(state.o),
            h=float(state.h),
            low=float(state.low),
            c=float(state.c),
            v=0.0,
            complete=False,
            src=self._source,
            extensions=extensions,
        )

    def _to_promoted(self, symbol: str, tf_s: int, state: _BucketState) -> CandleBar:
        """Promoted бар: complete=True, src=tick_promoted."""
        extensions: Dict[str, Any] = {
            "ticks_n": state.ticks_n,
            "promoted": True,
        }
        return CandleBar(
            symbol=str(symbol),
            tf_s=int(tf_s),
            open_time_ms=int(state.open_ms),
            close_time_ms=int(state.close_ms),
            o=float(state.o),
            h=float(state.h),
            low=float(state.low),
            c=float(state.c),
            v=0.0,
            complete=True,
            src="tick_promoted",
            extensions=extensions,
        )