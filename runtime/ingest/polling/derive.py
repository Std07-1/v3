from __future__ import annotations

from typing import Dict, List, Optional

from core.model.bars import CandleBar, assert_invariants
from runtime.ingest.polling.time_buckets import floor_bucket_start_ms


class M5Buffer:
    """Буфер закритих 5m барів у памʼяті для побудови derived TF."""

    def __init__(self, max_keep: int = 2000) -> None:
        self._max_keep = max_keep
        self._by_open_ms: Dict[int, CandleBar] = {}
        self._sorted_keys: List[int] = []

    def upsert(self, bar: CandleBar) -> None:
        if bar.tf_s != 300:
            raise ValueError("M5Buffer приймає тільки tf_s=300")
        k = bar.open_time_ms
        if k in self._by_open_ms:
            self._by_open_ms[k] = bar
            return
        self._by_open_ms[k] = bar
        self._sorted_keys.append(k)
        self._sorted_keys.sort()
        self._gc()

    def _gc(self) -> None:
        if len(self._sorted_keys) <= self._max_keep:
            return
        drop = len(self._sorted_keys) - self._max_keep
        to_drop = self._sorted_keys[:drop]
        self._sorted_keys = self._sorted_keys[drop:]
        for k in to_drop:
            self._by_open_ms.pop(k, None)

    def has_range_complete(self, start_ms: int, end_ms: int) -> bool:
        step = 300_000
        for t in range(start_ms, end_ms, step):
            if t not in self._by_open_ms:
                return False
        return True

    def range_bars(self, start_ms: int, end_ms: int) -> List[CandleBar]:
        step = 300_000
        out: List[CandleBar] = []
        for t in range(start_ms, end_ms, step):
            b = self._by_open_ms.get(t)
            if b is None:
                return []
            out.append(b)
        return out

    def missing_count(self, start_ms: int, end_ms: int) -> int:
        step = 300_000
        missing = 0
        for t in range(start_ms, end_ms, step):
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


def derive_from_m5_for_anchor(
    symbol: str,
    tf_s: int,
    m5: M5Buffer,
    anchor_open_ms: int,
    anchor_offset_s: int = 0,
) -> Optional[CandleBar]:
    tf_ms = tf_s * 1000

    b0 = floor_bucket_start_ms(anchor_open_ms, tf_s, anchor_offset_s=anchor_offset_s)
    b1 = b0 + tf_ms

    if anchor_open_ms != (b1 - 300_000):
        return None

    if not m5.has_range_complete(b0, b1):
        return None

    bars = m5.range_bars(b0, b1)
    if not bars:
        return None

    # Фільтруємо calendar-pause flat бари — derived будуємо лише з торгових М5
    trading_bars = [x for x in bars if not x.extensions.get("calendar_pause_flat")]
    if not trading_bars:
        return None  # усі М5 — calendar pause, derived не будуємо

    o = trading_bars[0].o
    c = trading_bars[-1].c
    h = max(x.h for x in trading_bars)
    low = min(x.low for x in trading_bars)
    v = sum(x.v for x in trading_bars)

    extensions = {}  # type: dict[str, Any]
    if len(trading_bars) < len(bars):
        pause_count = len(bars) - len(trading_bars)
        extensions["partial_calendar_pause"] = True
        extensions["calendar_pause_m5_count"] = pause_count

    out = CandleBar(
        symbol=symbol,
        tf_s=tf_s,
        open_time_ms=b0,
        close_time_ms=b1,
        o=o,
        h=h,
        low=low,
        c=c,
        v=v,
        complete=True,
        src="derived",
        extensions=extensions,
    )
    assert_invariants(out, anchor_offset_s=anchor_offset_s)
    return out


