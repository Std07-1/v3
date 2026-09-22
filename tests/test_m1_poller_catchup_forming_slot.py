"""Tail catchup / live recover не гублять найстаршу хвилину гепа (інцидент 22.09.2026 17:15).

Брокер на «останні n до date_to» віддає і свічку, що відкрилась о date_to (формується). Без запасу в один слот
найстарша потрібна хвилина випадає з відповіді, а після оновлення watermark її вже ніхто не добирає.
"""
from __future__ import annotations

from core.model.bars import CandleBar
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.polling import m1_poller as poller_mod
from runtime.ingest.polling.m1_poller import M1SymbolPoller

M1_MS = 60_000
WEDNESDAY_1700 = 1_788_973_200_000  # 2026-09-09 17:00 UTC — торгова хвилина (cfd_us_22_23)


def _calendar() -> MarketCalendar:
    return MarketCalendar(enabled=True, weekend_close_dow=4, weekend_close_hm="20:45", weekend_open_dow=6,
                          weekend_open_hm="22:00", daily_break_start_hm="21:00", daily_break_end_hm="22:00",
                          daily_break_enabled=True)


class _BrokerLikeProvider:
    """Як FXCM get_history(date_to, n): n свічок з відкриттям ≤ date_to включно з тією, що відкрилась о date_to."""

    def __init__(self):
        self.requests = []

    def fetch_last_n_m1(self, symbol, n, date_to_utc=None):
        self.requests.append((n, date_to_utc))
        date_to_ms = int(date_to_utc.timestamp() * 1000)
        opens = [date_to_ms - k * M1_MS for k in range(n)]
        return [CandleBar(symbol=symbol, tf_s=60, open_time_ms=t, close_time_ms=t + M1_MS, o=1.0, h=2.0, low=0.5,
                          c=1.5, v=10.0, complete=True, src="history") for t in opens]


class _RecordingUds:
    def __init__(self):
        self.committed = []
        self.gap_states = []

    def commit_final_bar(self, bar):
        self.committed.append(bar)
        return type("Result", (), {"ok": True})()

    def set_gap_state(self, **kwargs):
        self.gap_states.append(kwargs)


def _poller_with_gap(monkeypatch, gap_minutes: int):
    """Watermark = 17:00, зараз 17:00 + gap + 1 хв (+20 с): бракує рівно gap_minutes хвилин 17:01…"""
    uds = _RecordingUds()
    provider = _BrokerLikeProvider()
    poller = M1SymbolPoller(symbol="SYM", provider=provider, uds=uds, calendar=_calendar(), m3_derive=False)
    poller._watermark_ms = WEDNESDAY_1700  # noqa: SLF001
    now_ms = WEDNESDAY_1700 + (gap_minutes + 1) * M1_MS + 20_000
    monkeypatch.setattr(poller_mod, "_utc_now_ms", lambda: now_ms)
    return poller, provider, uds


def test_tail_catchup_writes_every_missing_minute_including_the_oldest(monkeypatch):
    poller, provider, uds = _poller_with_gap(monkeypatch, gap_minutes=13)

    result = poller.tail_catchup()

    written = sorted(b.open_time_ms for b in uds.committed)
    assert written == [WEDNESDAY_1700 + k * M1_MS for k in range(1, 14)], "усі 13 хвилин, і 17:01 теж"
    assert result["tail_catchup_written"] == 13
    assert provider.requests[0][0] == 13 + poller_mod._FORMING_SLOT  # noqa: SLF001


def test_live_recover_batch_keeps_the_oldest_minute_of_the_gap(monkeypatch):
    poller, provider, uds = _poller_with_gap(monkeypatch, gap_minutes=5)  # 5 > threshold 3 → вхід у recover

    poller._live_recover_check()  # noqa: SLF001

    written = sorted(b.open_time_ms for b in uds.committed)
    assert written == [WEDNESDAY_1700 + k * M1_MS for k in range(1, 6)]
    assert provider.requests[0][0] == 5 + poller_mod._FORMING_SLOT  # noqa: SLF001
