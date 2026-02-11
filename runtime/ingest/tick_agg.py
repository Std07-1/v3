"""Preview-агрегація тиков у бари (preview-plane)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

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


class TickAggregator:
    """Агрегує тики у preview-бари з open=перший тик бакету."""

    def __init__(
        self,
        tf_allowlist: Iterable[int] = (60, 180),
        *,
        source: str = "preview_tick",
        anchor_offset_ms: int = 0,
    ) -> None:
        self._tf_allowlist = set(int(v) for v in tf_allowlist)
        self._source = str(source)
        self._anchor_offset_ms = int(anchor_offset_ms)
        self._state: Dict[Tuple[str, int], _BucketState] = {}
        self._stats = {
            "ticks_total": 0,
            "ticks_rejected_tf": 0,
            "ticks_dropped_late_bucket": 0,
            "ticks_dropped_before_open": 0,
            "ticks_dropped_out_of_order": 0,
        }

    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def update(
        self,
        symbol: str,
        tf_s: int,
        tick_ts_ms: int,
        price: float,
    ) -> Optional[CandleBar]:
        self._stats["ticks_total"] += 1
        if tf_s not in self._tf_allowlist:
            self._stats["ticks_rejected_tf"] += 1
            return None

        tf_ms = tf_to_ms(int(tf_s))
        open_ms = bucket_start_ms(int(tick_ts_ms), tf_ms, self._anchor_offset_ms)
        close_ms = int(open_ms + tf_ms)
        key = (str(symbol), int(tf_s))
        state = self._state.get(key)

        if state is not None and open_ms < state.open_ms:
            self._stats["ticks_dropped_late_bucket"] += 1
            return None
        if state is None or state.open_ms != open_ms:
            state = _BucketState(
                open_ms=open_ms,
                close_ms=close_ms,
                open_tick_ts_ms=int(tick_ts_ms),
                last_tick_ts_ms=int(tick_ts_ms),
                o=float(price),
                h=float(price),
                low=float(price),
                c=float(price),
                v=1.0,
            )
            self._state[key] = state
            return self._to_bar(symbol, tf_s, state)

        if tick_ts_ms < state.open_tick_ts_ms:
            self._stats["ticks_dropped_before_open"] += 1
            return None
        if tick_ts_ms < state.last_tick_ts_ms:
            self._stats["ticks_dropped_out_of_order"] += 1
            return None

        state.last_tick_ts_ms = int(tick_ts_ms)
        state.c = float(price)
        if price > state.h:
            state.h = float(price)
        if price < state.low:
            state.low = float(price)
        state.v += 1.0
        return self._to_bar(symbol, tf_s, state)

    def _to_bar(self, symbol: str, tf_s: int, state: _BucketState) -> CandleBar:
        return CandleBar(
            symbol=str(symbol),
            tf_s=int(tf_s),
            open_time_ms=int(state.open_ms),
            close_time_ms=int(state.close_ms),
            o=float(state.o),
            h=float(state.h),
            low=float(state.low),
            c=float(state.c),
            v=float(state.v),
            complete=False,
            src=self._source,
        )