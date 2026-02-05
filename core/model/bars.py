from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any, Dict, Tuple


@dataclasses.dataclass(frozen=True)
class CandleBar:
    """Канонічний бар (один формат для SSOT і derived).

    - Час строго UTC.
    - open_time_ms = початок bucket.
    - close_time_ms = open_time_ms + tf_s*1000.
    - complete=true лише для закритих барів (у версії B — всі збережені 1m/derived є complete).
    """

    symbol: str
    tf_s: int
    open_time_ms: int
    close_time_ms: int
    o: float
    h: float
    low: float
    c: float
    v: float
    complete: bool
    src: str  # "history" | "derived"

    def key(self) -> Tuple[str, int, int]:
        return (self.symbol, self.tf_s, self.open_time_ms)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "tf_s": self.tf_s,
            "open_time_ms": self.open_time_ms,
            "close_time_ms": self.close_time_ms,
            "o": self.o,
            "h": self.h,
            "low": self.low,
            "c": self.c,
            "v": self.v,
            "complete": self.complete,
            "src": self.src,
        }


def assert_invariants(b: CandleBar, anchor_offset_s: int = 0) -> None:
    tf_ms = b.tf_s * 1000
    if (b.open_time_ms - anchor_offset_s * 1000) % tf_ms != 0:
        raise ValueError(
            f"bar_bucket_misaligned tf_s={b.tf_s} open_time_ms={b.open_time_ms}"
        )
    if b.close_time_ms != b.open_time_ms + tf_ms:
        raise ValueError(
            f"bar_close_time_invalid tf_s={b.tf_s} open_time_ms={b.open_time_ms}"
        )
    if b.tf_s == 60 and b.src == "derived":
        raise ValueError("derived_1m_forbidden")


def ms_to_utc_dt(ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000.0, tz=dt.timezone.utc)


def utc_dt_to_ms(d: dt.datetime) -> int:
    if d.tzinfo is None:
        raise ValueError("Очікується datetime з tz=UTC.")
    return int(d.timestamp() * 1000)
