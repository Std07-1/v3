"""Геп M1 добирається від watermark повністю, а не останніми n до cutoff (ADR-0002 §P0.2, рев'ю dfca437 23.09.2026).

Модель брокера як у проді: FXCM get_history віддає n останніх ІСНУЮЧИХ барів з open ≤ date_to, включно з тим, що
відкрився о date_to (формується); sidecar ріже запит до MAX_BARS_PER_FETCH. Хвилин без тіків (тонкі, пауза) у
відповіді немає; бар може з'явитись в історії пізніше за своє закриття.
"""
from __future__ import annotations

import logging

import pytest

from core.model.bars import CandleBar
from runtime.ingest.broker import MAX_BARS_PER_FETCH
from runtime.ingest.m1_session_filter import PausePolicy
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.polling import m1_poller as poller_mod
from runtime.ingest.polling.m1_poller import M1SymbolPoller

M1_MS = 60_000
WED_1200 = 1_788_955_200_000  # 2026-09-09 12:00 UTC (середа)
WED_2059 = WED_1200 + (8 * 60 + 59) * M1_MS
WED_2200 = WED_2059 + 61 * M1_MS
FRI_2044 = WED_1200 + 2 * 86_400_000 + (8 * 60 + 44) * M1_MS  # 2026-09-11 20:44 — остання хвилина тижня
SUN_2200 = FRI_2044 + (2 * 24 * 60 + 76) * M1_MS  # 2026-09-13 22:00 — відкриття
US_CFD_POLICY = PausePolicy(noise_margin_min=60, edge_stale_max_volume=8)  # як config для cfd_us_22_23


def _calendar() -> MarketCalendar:
    """Група cfd_us_22_23 з config.json: вихідні Пт 20:45 → Нд 22:00, денна перерва 21:00–22:00."""
    return MarketCalendar(enabled=True, weekend_close_dow=4, weekend_close_hm="20:45", weekend_open_dow=6,
                          weekend_open_hm="22:00", daily_break_start_hm="21:00", daily_break_end_hm="22:00",
                          daily_break_enabled=True)


def _bar(open_ms: int, v: float = 50.0, o: float = 100.0, c: float = 100.5) -> CandleBar:
    return CandleBar(symbol="SYM", tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + M1_MS, o=o,
                     h=max(o, c) + 0.5, low=min(o, c) - 0.5, c=c, v=v, complete=True, src="history")


class _Broker:
    """FXCM через sidecar: n останніх видимих барів з open ≤ date_to (разом із формуючим), n ≤ стелі."""

    def __init__(self, now_ms: int, missing=(), extra=(), release=None, fail_calls=(), horizon_ms=None):
        self._calendar = _calendar()
        self._horizon_ms = horizon_ms  # старших барів у брокера немає
        self.deep_down = False  # сторінки гортання (date_to у минулому) повертають порожньо
        self.now_ms = now_ms
        self._missing = set(missing)
        self._extra = {b.open_time_ms: b for b in extra}
        self._release = dict(release or {})  # open_ms → момент, з якого бар видно в історії
        self._fail_calls = set(fail_calls)  # номери викликів (з 1), що падають
        self.dead = False
        self.requests = []

    def _visible(self, t: int) -> bool:
        return self.now_ms >= self._release.get(t, t)

    def fetch_last_n_m1(self, symbol, n, date_to_utc=None):
        self.requests.append(n)
        if len(self.requests) in self._fail_calls:
            raise RuntimeError("BROKER_PROXY_TIMEOUT")
        if self.dead:
            return []
        date_to_ms = int(date_to_utc.timestamp() * 1000)
        if self.deep_down and date_to_ms <= (self.now_ms // M1_MS) * M1_MS - M1_MS:
            return []
        top = min(date_to_ms, (self.now_ms // M1_MS) * M1_MS)
        floor_ms = self._horizon_ms if self._horizon_ms is not None else top - 8 * 86_400_000
        out, t = [], top
        while len(out) < min(n, MAX_BARS_PER_FETCH) and t >= floor_ms:
            if self._visible(t):
                if t in self._extra:
                    out.append(self._extra[t])
                elif self._calendar.is_trading_minute(t) and t not in self._missing:
                    out.append(_bar(t))
            t -= M1_MS
        return out


class _Uds:
    def __init__(self):
        self.committed = []
        self.gap_states = []

    def commit_final_bar(self, bar):
        self.committed.append(bar)
        return type("Result", (), {"ok": True})()

    def set_gap_state(self, **kwargs):
        self.gap_states.append(kwargs)


def _poller(monkeypatch, broker: _Broker, watermark_ms: int, **kwargs):
    uds = _Uds()
    kwargs.setdefault("pause_policy", US_CFD_POLICY)
    poller = M1SymbolPoller(symbol="SYM", provider=broker, uds=uds, calendar=_calendar(), m3_derive=False,
                            live_recover_cooldown_s=0, **kwargs)
    poller._watermark_ms = watermark_ms  # noqa: SLF001
    monkeypatch.setattr(poller_mod, "_utc_now_ms", lambda: broker.now_ms)
    monkeypatch.setattr(poller_mod, "_PAGE_RETRY_PAUSE_S", 0, raising=False)
    return poller, uds


def _run_cycles(poller, broker: _Broker, first_minute_ms: int, cycles: int, uds: _Uds):
    """Основний цикл: poll_once щохвилини о T+8 с; повертає найбільший приріст комітів за один цикл."""
    max_step = 0
    for k in range(cycles):
        broker.now_ms = _at(first_minute_ms + k * M1_MS)
        before = len(uds.committed)
        poller.poll_once()
        max_step = max(max_step, len(uds.committed) - before)
    return max_step


def _written(uds: _Uds):
    return [b.open_time_ms for b in uds.committed]


def _minutes(first_ms: int, last_ms: int):
    return list(range(first_ms, last_ms + M1_MS, M1_MS))


def _at(minute_ms: int) -> int:
    """Момент опитування полера: T+8 с після закриття хвилини."""
    return minute_ms + M1_MS + 8_000


def test_tail_catchup_after_5h_outage_writes_every_minute_despite_broker_cap(monkeypatch):
    """300 пропущених хвилин > стелі 200: раніше писалось 199 найновіших, 101 найстаріша губилась назавжди."""
    broker = _Broker(now_ms=_at(WED_1200 + 300 * M1_MS))
    poller, uds = _poller(monkeypatch, broker, WED_1200)

    result = poller.tail_catchup()
    # Бутстрап пише найстаріші 120 (не блокує старт), решту — основний цикл від watermark
    assert result["tail_catchup_written"] == 120
    assert uds.gap_states[-1]["policy"] == "m1_tail_catchup_backlog"
    _run_cycles(poller, broker, WED_1200 + 300 * M1_MS, 3, uds)

    assert _written(uds) == _minutes(WED_1200 + M1_MS, WED_1200 + 302 * M1_MS)
    assert max(broker.requests) <= MAX_BARS_PER_FETCH


def test_live_outage_5h_is_filled_oldest_first_without_loss(monkeypatch):
    """Брокер ожив після 5 год: цикли дописують геп за зростанням (≤ 2×120 за цикл), нічого не перестрибуючи."""
    broker = _Broker(now_ms=_at(WED_1200 + 300 * M1_MS))
    poller, uds = _poller(monkeypatch, broker, WED_1200)

    max_step = _run_cycles(poller, broker, WED_1200 + 300 * M1_MS, 3, uds)

    assert _written(uds) == _minutes(WED_1200 + M1_MS, WED_1200 + 302 * M1_MS)
    assert max_step <= 240
    assert not poller.stats["recover_active"]


def test_history_lag_after_reopen_loses_no_minute(monkeypatch):
    """Історія FXCM лагує після відкриття: 22:00..22:03 з'являються разом о 22:04:10 — жодна хвилина не губиться."""
    lag = {t: WED_2200 + 4 * M1_MS + 10_000 for t in _minutes(WED_2200, WED_2200 + 3 * M1_MS)}
    broker = _Broker(now_ms=_at(WED_2200), release=lag)
    poller, uds = _poller(monkeypatch, broker, WED_2059)

    for minute in range(5):
        broker.now_ms = _at(WED_2200 + minute * M1_MS)
        poller.poll_once()

    assert _written(uds) == _minutes(WED_2200, WED_2200 + 4 * M1_MS)


def test_weekend_gap_shows_pause_bars_to_the_classifier(monkeypatch):
    """Вихідні: суботній шум і п'ятничний бар після закриття доходять до правила ADR-0099, як і раніше."""
    fri_after_close = _bar(FRI_2044 + 6 * M1_MS, v=30.0, o=100.0, c=101.0)  # Пт 20:50, неплаский — anomaly
    sat_noise = _bar(FRI_2044 + (13 * 60 + 16) * M1_MS, v=2.0, o=100.0, c=100.0)  # Сб 10:00, шум
    broker = _Broker(now_ms=_at(SUN_2200), extra=[fri_after_close, sat_noise])
    poller, uds = _poller(monkeypatch, broker, FRI_2044)

    poller.poll_once()

    assert _written(uds) == [fri_after_close.open_time_ms, SUN_2200]
    assert uds.committed[0].extensions == {"calendar_pause_nonflat_anomaly": True}
    assert poller.stats["pause_noise_dropped"] == 1


def test_daily_break_stale_edge_bar_reaches_the_classifier(monkeypatch):
    stale = _bar(WED_2059 + M1_MS, v=4.0, o=100.5, c=100.6)  # 21:00 — перша хвилина перерви, кілька тіків
    broker = _Broker(now_ms=_at(WED_2200), extra=[stale])
    poller, uds = _poller(monkeypatch, broker, WED_2059)

    poller.poll_once()

    assert _written(uds) == [WED_2200]
    assert poller.stats["pause_edge_stale_dropped"] == 1


def test_thin_minutes_without_bars_do_not_start_recover(monkeypatch, caplog):
    """Брокер не має 5 тонких хвилин поспіль: добір дійшов до watermark — це не збій, recover не вмикається."""
    thin = _minutes(WED_1200 + M1_MS, WED_1200 + 5 * M1_MS)
    broker = _Broker(now_ms=_at(WED_1200 + 5 * M1_MS), missing=thin)
    poller, uds = _poller(monkeypatch, broker, WED_1200)
    caplog.set_level(logging.WARNING)

    poller.poll_once()
    broker.now_ms = _at(WED_1200 + 6 * M1_MS)
    poller.poll_once()

    assert _written(uds) == [WED_1200 + 6 * M1_MS]
    assert "M1_LIVE_RECOVER_START" not in caplog.text


def test_dead_broker_writes_nothing_and_keeps_watermark(monkeypatch):
    broker = _Broker(now_ms=_at(WED_1200 + 300 * M1_MS))
    broker.dead = True
    poller, uds = _poller(monkeypatch, broker, WED_1200)

    result = poller.tail_catchup()

    assert _written(uds) == [] and "tail_catchup_error" in result
    assert poller.stats["watermark_ms"] == WED_1200


def test_deep_pages_down_write_nothing_then_later_cycles_fill_all(monkeypatch):
    """Глибокі сторінки порожні (sidecar reconnect): частковий добір не пишеться, watermark не стрибає; потім — усе."""
    broker = _Broker(now_ms=_at(WED_1200 + 300 * M1_MS))
    broker.deep_down = True
    poller, uds = _poller(monkeypatch, broker, WED_1200, live_recover_max_consecutive_empty=99)

    _run_cycles(poller, broker, WED_1200 + 300 * M1_MS, 3, uds)
    assert _written(uds) == []
    assert poller.stats["watermark_ms"] == WED_1200

    broker.deep_down = False
    _run_cycles(poller, broker, WED_1200 + 303 * M1_MS, 3, uds)
    assert _written(uds) == _minutes(WED_1200 + M1_MS, WED_1200 + 305 * M1_MS)


def test_single_failed_page_is_retried_not_dropped(monkeypatch):
    """Разовий таймаут сторінки повторюється тим самим викликом — геп не відкладається на наступний цикл."""
    broker = _Broker(now_ms=_at(WED_1200 + 150 * M1_MS), fail_calls={2})
    poller, uds = _poller(monkeypatch, broker, WED_1200)

    poller.poll_once()

    assert _written(uds)[:120] == _minutes(WED_1200 + M1_MS, WED_1200 + 120 * M1_MS)


def test_recover_budget_is_not_eaten_by_discarded_attempts(monkeypatch, caplog):
    """Відкинуті спроби (порожні глибокі сторінки) не з'їдають бюджет: геп 1500 < 5000 добирається без дірки."""
    start = WED_1200 - (24 * 60 - 60) * M1_MS  # вт 13:00 → геп через перерву ≈ 1380 барів
    broker = _Broker(now_ms=_at(WED_1200))
    broker.deep_down = True
    poller, uds = _poller(monkeypatch, broker, start, live_recover_max_consecutive_empty=99)
    caplog.set_level(logging.WARNING)

    _run_cycles(poller, broker, WED_1200, 4, uds)
    broker.deep_down = False
    _run_cycles(poller, broker, WED_1200 + 4 * M1_MS, 16, uds)

    assert "M1_GAP_BEYOND_BUDGET" not in caplog.text
    written = _written(uds)
    assert written == sorted(written) and len(written) == len(set(written))
    assert written[0] == start + M1_MS and written[-1] == WED_1200 + 19 * M1_MS


def test_history_horizon_stops_paging_loudly(monkeypatch, caplog):
    """Watermark старший за горизонт історії брокера: гортання зупиняється одразу, а не крутиться до бюджету."""
    horizon = WED_1200 + 100 * M1_MS
    broker = _Broker(now_ms=_at(WED_1200 + 400 * M1_MS), horizon_ms=horizon)
    poller, uds = _poller(monkeypatch, broker, WED_1200)
    caplog.set_level(logging.WARNING)

    poller.tail_catchup()

    assert "M1_GAP_HISTORY_HORIZON" in caplog.text
    assert len(broker.requests) <= 4
    assert _written(uds)[0] == horizon


def test_gap_beyond_budget_is_loud_and_kept_in_gap_state(monkeypatch, caplog):
    """Геп більший за бюджет добору: найстаріша частина — дірка, але гучно (WARN + gap_state), а не тихо."""
    broker = _Broker(now_ms=_at(WED_1200 + 500 * M1_MS))  # 12:01..20:20 — без перерви
    poller, uds = _poller(monkeypatch, broker, WED_1200, tail_catchup_max_bars=300)
    caplog.set_level(logging.WARNING)

    poller.tail_catchup()

    assert "M1_GAP_BEYOND_BUDGET" in caplog.text
    state = uds.gap_states[-1]
    assert state["policy"] == "m1_gap_beyond_budget" and state["gap_from_ms"] == WED_1200 + M1_MS
    first_written = _written(uds)[0]
    assert state["gap_to_ms"] == first_written - M1_MS
    assert _written(uds) == _minutes(first_written, first_written + 119 * M1_MS)


@pytest.mark.parametrize("gap_minutes", [1, 3, 150, 199, 200, 201, 450])
def test_poll_requests_never_exceed_broker_cap(monkeypatch, gap_minutes):
    broker = _Broker(now_ms=_at(WED_1200 + gap_minutes * M1_MS))
    poller, uds = _poller(monkeypatch, broker, WED_1200)

    _run_cycles(poller, broker, WED_1200 + gap_minutes * M1_MS, 4, uds)

    assert max(broker.requests) <= MAX_BARS_PER_FETCH
    assert _written(uds) == _minutes(WED_1200 + M1_MS, WED_1200 + (gap_minutes + 3) * M1_MS)
