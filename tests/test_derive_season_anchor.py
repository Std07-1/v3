"""Деривація на сезонній сітці (ADR-0095 S2b): рівність сітці, вікно до наступного бакета, обрубки DST-діб."""
from __future__ import annotations

import datetime as dt

import pytest

from core.derive import GenericBuffer, aggregate_bars, derive_bar, derive_triggers
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


def test_h4_summer_bucket_2200_is_built_from_h1_22_to_01():
    """Літо: H4 вт 22.09 22:00 (відкриття сесії 18:00 NY, як TV FX:) = H1 22:00, 23:00, 00:00, 01:00; open = H1 22:00,
    close = H1 01:00."""
    buf = _h1_buffer(_ms(2026, 9, 22, 22), 4)
    bar = derive_bar(symbol="XAU/USD", target_tf_s=H4_S, source_buffer=buf, bucket_open_ms=_ms(2026, 9, 22, 22),
                     is_trading_fn=_calendar().is_trading_minute, anchor_rule=FXCM)
    assert bar is not None and bar.open_time_ms == _ms(2026, 9, 22, 22)
    assert (bar.o, bar.c) == (100.0, 103.5)
    assert bar.close_time_ms == bar.open_time_ms + H4_S * 1000


def test_h4_on_winter_grid_in_summer_is_refused_loudly():
    buf = _h1_buffer(_ms(2026, 9, 22, 22), 4)
    with pytest.raises(OffSeasonGridError):
        derive_bar(symbol="XAU/USD", target_tf_s=H4_S, source_buffer=buf, bucket_open_ms=_ms(2026, 9, 22, 23),
                   is_trading_fn=_calendar().is_trading_minute, anchor_rule=FXCM)


def test_fall_stub_sun_2200_does_not_absorb_next_day():
    """01.11.2026: H4 нд 22:00 — обрубок сесійної доби на 1 год. H1 23:00..02:00 належать новій сесії (відкриття
    23:00, 18:00 EST), а не йому."""
    buf = _h1_buffer(_ms(2026, 11, 1, 23), 4)
    stub = derive_bar(symbol="XAU/USD", target_tf_s=H4_S, source_buffer=buf, bucket_open_ms=_ms(2026, 11, 1, 22),
                      is_trading_fn=_calendar().is_trading_minute, anchor_rule=FXCM)
    assert stub is None
    first = derive_bar(symbol="XAU/USD", target_tf_s=H4_S, source_buffer=buf, bucket_open_ms=_ms(2026, 11, 1, 23),
                       is_trading_fn=_calendar().is_trading_minute, anchor_rule=FXCM)
    assert first is not None and first.o == 100.0 and first.c == 103.5


def test_triggers_close_summer_h4_on_last_trading_h1():
    """H1 01:00 — останній торговий слот H4 22:00 (у сітці 22/02/06/…): тригер саме на цей бакет."""
    triggers = derive_triggers(_bar(3600, _ms(2026, 9, 23, 1), 100.0), is_trading_fn=_calendar().is_trading_minute,
                               anchor_rule=FXCM)
    assert (H4_S, _ms(2026, 9, 22, 22)) in triggers


def test_triggers_close_d1_on_last_trading_minute_both_seasons():
    cal = _calendar().is_trading_minute
    summer = derive_triggers(_bar(60, _ms(2026, 9, 22, 20, 59), 100.0), is_trading_fn=cal, anchor_rule=FXCM)
    assert (D1_S, _ms(2026, 9, 21, 21)) in summer
    winter_friday = derive_triggers(_bar(60, _ms(2026, 3, 6, 20, 44), 100.0), is_trading_fn=cal, anchor_rule=FXCM)
    assert (D1_S, _ms(2026, 3, 5, 22)) in winter_friday


def test_derive_bar_and_aggregate_have_no_legacy_anchor_kwargs():
    """З S5b легасі-секунд у derive_bar / aggregate_bars немає: TypeError, а не друга сітка поруч із правилом."""
    buf = _h1_buffer(_ms(2026, 9, 22, 22), 3)
    with pytest.raises(TypeError):
        derive_bar(symbol="XAU/USD", target_tf_s=H4_S, source_buffer=buf, bucket_open_ms=_ms(2026, 9, 22, 21),
                   anchor_offset_s=79200, anchor_rule=FXCM)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        derive_bar(symbol="XAU/USD", target_tf_s=D1_S, source_buffer=GenericBuffer(tf_s=60),
                   bucket_open_ms=_ms(2026, 9, 21, 21), d1_anchor_offset_s=75600)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        aggregate_bars([_bar(3600, _ms(2026, 9, 22, 22), 100.0)], symbol="XAU/USD", target_tf_s=H4_S,
                       bucket_open_ms=_ms(2026, 9, 22, 21), anchor_offset_s=75600)  # type: ignore[call-arg]


def test_htf_without_rule_is_refused_loudly_not_anchor_zero():
    """H4 00:00 UTC на сітці якоря 0: раніше без правила проходив мовчки — H4 XAU на сітці Binance."""
    h1 = _bar(3600, _ms(2026, 9, 23, 0), 100.0)
    with pytest.raises(ValueError, match="anchor_rule_missing"):
        aggregate_bars([h1], symbol="XAU/USD", target_tf_s=H4_S, bucket_open_ms=_ms(2026, 9, 23, 0))
    with pytest.raises(ValueError, match="anchor_rule_missing"):
        derive_bar(symbol="XAU/USD", target_tf_s=H4_S, source_buffer=_h1_buffer(_ms(2026, 9, 23, 0), 4),
                   bucket_open_ms=_ms(2026, 9, 23, 0))
    m3 = aggregate_bars([_bar(60, _ms(2026, 9, 23, 0), 100.0)], symbol="XAU/USD", target_tf_s=180,
                        bucket_open_ms=_ms(2026, 9, 23, 0))
    assert m3 is not None and m3.open_time_ms == _ms(2026, 9, 23, 0)  # M1..H1 правила не потребують


def test_derive_triggers_requires_rule_and_has_no_legacy_anchor():
    """З S4a тригери мають лише правило: без нього чи з легасі-секундами — TypeError, а не тихий якір 0."""
    h1 = _bar(3600, _ms(2026, 9, 23, 0), 100.0)
    with pytest.raises(TypeError):
        derive_triggers(h1)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        derive_triggers(h1, anchor_offset_s=79200, anchor_rule=FXCM)  # type: ignore[call-arg]
