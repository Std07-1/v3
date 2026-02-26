from __future__ import annotations

import tempfile
import pytest

from core.derive import GenericBuffer, derive_bar
from core.model.bars import CandleBar

def _make_bar(symbol: str, tf_s: int, open_ms: int, *, price: float, calendar_pause_flat: bool = False) -> CandleBar:
    return CandleBar(
        symbol=symbol,
        tf_s=tf_s,
        open_time_ms=open_ms,
        close_time_ms=open_ms + (tf_s * 1000),
        o=price,
        h=price + 0.2,
        low=price - 0.2,
        c=price + 0.1,
        v=10.0,
        complete=True,
        src="history",
        extensions={"calendar_pause_flat": True} if calendar_pause_flat else {},
    )

import itertools

SYMBOLS = ["EUR/USD", "BTC/USD", "NGAS", "HKG33"]
TARGET_TFS = [
    (180, 60, 3),      # M3  = 3x M1
    (300, 60, 5),      # M5  = 5x M1
    (900, 300, 3),     # M15 = 3x M5
    (1800, 900, 2),    # M30 = 2x M15
    (3600, 1800, 2),   # H1  = 2x M30
    (14400, 3600, 4),  # H4  = 4x H1
]

@pytest.mark.parametrize("symbol, tf_tuple", list(itertools.product(SYMBOLS, TARGET_TFS)))
def test_all_symbols_all_tfs_partial_calendar_pause(symbol, tf_tuple):
    target_tf_s, source_tf_s, bars_needed = tf_tuple
    
    bucket_open_ms = 1_728_000_000_000 # Divisible by 14400000 (H4)
    source_tf_ms = source_tf_s * 1000
    
    buf = GenericBuffer(tf_s=source_tf_s, max_keep=100)
    
    # We will simulate exactly `bars_needed` source bars in this bucket.
    # The last one will be marked calendar_pause_flat
    
    bars = []
    for i in range(bars_needed):
        open_ms = bucket_open_ms + (i * source_tf_ms)
        is_pause = (i == bars_needed - 1) # last bar is pause
        bars.append(_make_bar(symbol, source_tf_s, open_ms, price=10.0 + i, calendar_pause_flat=is_pause))
        
    buf.upsert_many(bars)
    
    # Derive!
    out = derive_bar(
        symbol=symbol,
        target_tf_s=target_tf_s,
        source_buffer=buf,
        bucket_open_ms=bucket_open_ms,
        is_trading_fn=lambda _t: True, # All minutes are nominal trading minutes
        filter_calendar_pause=True,
    )
    
    assert out is not None
    assert out.symbol == symbol
    assert out.tf_s == target_tf_s
    assert out.complete is True
    assert out.extensions.get("partial") is True
    assert out.extensions.get("partial_calendar_pause") is True
    assert out.extensions.get("source_count") == bars_needed - 1
    assert out.extensions.get("expected_count") == bars_needed
    assert "calendar_pause" in out.extensions.get("partial_reasons", [])

