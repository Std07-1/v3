from __future__ import annotations

from typing import Any

from runtime.ingest.market_calendar import MarketCalendar


def expected_last_closed_m5_open_ms(now_ms: int) -> int:
    tf_ms = 300_000
    return (now_ms // tf_ms) * tf_ms - tf_ms


def last_trading_minute_open_ms(
    provider: Any,
    symbol: str,
    calendar: MarketCalendar,
    now_ms: int,
) -> int:
    cur = (now_ms // 60_000) * 60_000 - 60_000
    for _ in range(7 * 24 * 60):
        if provider.is_market_open(symbol, cur, calendar):
            return cur
        cur -= 60_000
    return (now_ms // 60_000) * 60_000 - 60_000
