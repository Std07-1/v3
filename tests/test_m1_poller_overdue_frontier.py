"""Межа даних символу для overdue DeriveEngine (рев'ю 23.09): хвилини раніше за неї закомічені або підтверджено
відсутні; хвилина, якої добір не знайшов, стає «відсутньою» лише через grace; пауза календаря проходиться одразу."""
from __future__ import annotations

from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.polling.m1_poller import M1SymbolPoller

M1_MS = 60_000
TUE_2059 = 1_790_110_740_000  # 2026-09-22 20:59 UTC — остання хвилина сесії XAU перед перервою
FRI_2044 = 1_789_764_240_000  # 2026-09-18 20:44 UTC — остання хвилина тижня


def _calendar() -> MarketCalendar:
    return MarketCalendar(enabled=True, weekend_close_dow=4, weekend_close_hm="20:45", weekend_open_dow=6,
                          weekend_open_hm="22:00", daily_break_start_hm="21:00", daily_break_end_hm="22:00",
                          daily_break_enabled=True)


def _poller(watermark_ms, scanned_ms, grace_s=180) -> M1SymbolPoller:
    poller = M1SymbolPoller(symbol="XAU/USD", provider=object(), uds=object(), calendar=_calendar(), m3_derive=False,
                            overdue_grace_s=grace_s)
    poller._watermark_ms = watermark_ms  # noqa: SLF001
    poller._scanned_through_ms = scanned_ms  # noqa: SLF001
    return poller


def test_minute_not_found_yet_is_not_closed_before_grace():
    """20:59 ще не опублікована о 21:00:08: добір дійшов до 20:59, але межа — 20:59, бакет 20:55–21:00 чекає."""
    poller = _poller(watermark_ms=TUE_2059 - M1_MS, scanned_ms=TUE_2059)
    assert poller.overdue_frontier_ms(TUE_2059 + M1_MS + 8_000) == TUE_2059


def test_after_grace_the_missing_minute_counts_as_absent_and_the_break_is_crossed():
    poller = _poller(watermark_ms=TUE_2059 - M1_MS, scanned_ms=TUE_2059)
    now = TUE_2059 + 4 * M1_MS + 8_000  # 21:03:08
    assert poller.overdue_frontier_ms(now) == now  # 21:00 → перерва → межа дорівнює годиннику


def test_committed_last_minute_closes_the_bucket_at_once():
    poller = _poller(watermark_ms=TUE_2059, scanned_ms=TUE_2059)
    now = TUE_2059 + M1_MS + 8_000
    assert poller.overdue_frontier_ms(now) == now  # 21:00 уже пауза — перехід одразу


def test_weekend_frontier_follows_the_clock_through_the_closed_market():
    poller = _poller(watermark_ms=FRI_2044, scanned_ms=FRI_2044)
    saturday = FRI_2044 + 20 * 3_600_000
    assert poller.overdue_frontier_ms(saturday) == saturday


def test_broker_down_mid_session_holds_the_frontier_at_the_watermark():
    """Простій посеред сесії: добір не дійшов далі watermark — межа стоїть, бакети після неї не закриваються."""
    t1200 = TUE_2059 - (8 * 60 + 59) * M1_MS
    poller = _poller(watermark_ms=t1200, scanned_ms=t1200)
    assert poller.overdue_frontier_ms(t1200 + 90 * M1_MS) == t1200 + M1_MS
