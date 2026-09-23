"""Деривація на сезонній сітці (ADR-0095 S2b): рівність сітці, вікно до наступного бакета, обрубки DST-діб."""
from __future__ import annotations

import datetime as dt

import pytest

from core.derive import GenericBuffer, derive_bar, derive_triggers
from core.model.bars import CandleBar
from core.session_anchor import D1_S, H4_S, RULE_NY_CLOSE_US_DST, OffSeasonGridError
from runtime.ingest.market_calendar import MarketCalendar

UTC = dt.timezone.utc
FXCM = RULE_NY_CLOSE_US_DST
H1_MS = 3_600_000


def _ms(y, mo, d, h=0, mi=0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp() * 1000)


def _calendar() -> MarketCalendar:
    """cfd_us_22_23 з config.json (літні години): вихідні Пт 20:45 → Нд 22:00, перерва 21:00–22:00."""
    return MarketCalendar(enabled=True, weekend_close_dow=4, weekend_close_hm="20:45", weekend_open_dow=6,
                          weekend_open_hm="22:00", daily_break_start_hm="21:00", daily_break_end_hm="22:00",
                          daily_break_enabled=True)


def _bar(tf_s: int, open_ms: int, price: float) -> CandleBar:
    return CandleBar(symbol="XAU/USD", tf_s=tf_s, open_time_ms=open_ms, close_time_ms=open_ms + tf_s * 1000,
                     o=price, h=price + 1.0, low=price - 1.0, c=price + 0.5, v=10.0, complete=True, src="derived")


def _h1_buffer(first_ms: int, hours: int, start_price: float = 100.0) -> GenericBuffer:
    buf = GenericBuffer(tf_s=3600)
    for k in range(hours):
        buf.upsert(_bar(3600, first_ms + k * H1_MS, start_price + k))
    return buf


def test_h4_summer_bucket_2100_is_built_from_h1_22_23_00():
    """Літо: H4 вт 22.09 21:00 = H1 22:00, 23:00, 00:00 (21:00 — перерва), open = H1 22:00, close = H1 00:00."""
    buf = _h1_buffer(_ms(2026, 9, 22, 22), 3)
    bar = derive_bar(symbol="XAU/USD", target_tf_s=H4_S, source_buffer=buf, bucket_open_ms=_ms(2026, 9, 22, 21),
                     is_trading_fn=_calendar().is_trading_minute, anchor_rule=FXCM)
    assert bar is not None and bar.open_time_ms == _ms(2026, 9, 22, 21)
    assert (bar.o, bar.c) == (100.0, 102.5)
    assert bar.close_time_ms == bar.open_time_ms + H4_S * 1000


def test_h4_on_winter_grid_in_summer_is_refused_loudly():
    buf = _h1_buffer(_ms(2026, 9, 22, 22), 4)
    with pytest.raises(OffSeasonGridError):
        derive_bar(symbol="XAU/USD", target_tf_s=H4_S, source_buffer=buf, bucket_open_ms=_ms(2026, 9, 22, 22),
                   is_trading_fn=_calendar().is_trading_minute, anchor_rule=FXCM)


def test_fall_stub_sun_2100_does_not_absorb_next_day():
    """01.11.2026: H4 нд 21:00 — обрубок на 1 год. H1 22:00..01:00 належать новій добі (відкриття 22:00), а не йому."""
    buf = _h1_buffer(_ms(2026, 11, 1, 22), 4)
    stub = derive_bar(symbol="XAU/USD", target_tf_s=H4_S, source_buffer=buf, bucket_open_ms=_ms(2026, 11, 1, 21),
                      is_trading_fn=_calendar().is_trading_minute, anchor_rule=FXCM)
    assert stub is None
    first = derive_bar(symbol="XAU/USD", target_tf_s=H4_S, source_buffer=buf, bucket_open_ms=_ms(2026, 11, 1, 22),
                       is_trading_fn=_calendar().is_trading_minute, anchor_rule=FXCM)
    assert first is not None and first.o == 100.0 and first.c == 103.5


def test_triggers_close_summer_h4_on_last_trading_h1():
    """H1 00:00 — останній торговий слот H4 21:00 (у сітці 21/01/05/…): тригер саме на цей бакет."""
    triggers = derive_triggers(_bar(3600, _ms(2026, 9, 23, 0), 100.0), is_trading_fn=_calendar().is_trading_minute,
                               anchor_rule=FXCM)
    assert (H4_S, _ms(2026, 9, 22, 21)) in triggers


def test_triggers_close_d1_on_last_trading_minute_both_seasons():
    cal = _calendar().is_trading_minute
    summer = derive_triggers(_bar(60, _ms(2026, 9, 22, 20, 59), 100.0), is_trading_fn=cal, anchor_rule=FXCM)
    assert (D1_S, _ms(2026, 9, 21, 21)) in summer
    winter_friday = derive_triggers(_bar(60, _ms(2026, 3, 6, 20, 44), 100.0), is_trading_fn=cal, anchor_rule=FXCM)
    assert (D1_S, _ms(2026, 3, 5, 22)) in winter_friday


def test_rule_and_legacy_anchor_together_are_refused():
    buf = _h1_buffer(_ms(2026, 9, 22, 22), 3)
    with pytest.raises(ValueError, match="anchor_rule_with_legacy_anchor_offset"):
        derive_bar(symbol="XAU/USD", target_tf_s=H4_S, source_buffer=buf, bucket_open_ms=_ms(2026, 9, 22, 21),
                   anchor_offset_s=79200, anchor_rule=FXCM)
    with pytest.raises(ValueError, match="anchor_rule_with_legacy_anchor_offset"):
        derive_triggers(_bar(3600, _ms(2026, 9, 23, 0), 100.0), anchor_offset_s=79200, anchor_rule=FXCM)
