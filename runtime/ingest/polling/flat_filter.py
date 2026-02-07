from __future__ import annotations

from core.model.bars import CandleBar


def is_flat_bar(bar: CandleBar, max_volume: int) -> bool:
    return bar.o == bar.h == bar.low == bar.c and bar.v <= max_volume
