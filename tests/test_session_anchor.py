"""Сезонний якір торгового дня (ADR-0095 §3.1, §3.7): правило, свідок tz-бази, конвенція FXCM, вкладеність H4 у D1."""
from __future__ import annotations

import datetime as dt
import random
from zoneinfo import ZoneInfo

import pytest

from core.session_anchor import (
    D1_S,
    H4_S,
    RULE_NY_CLOSE_US_DST,
    RULE_UTC_MIDNIGHT,
    htf_anchor_offset_s,
    htf_bucket_start_ms,
    trading_day_open_ms,
)

UTC = dt.timezone.utc
NY = ZoneInfo("America/New_York")
FXCM = RULE_NY_CLOSE_US_DST
H4_MS = H4_S * 1000
D1_MS = D1_S * 1000


def _ms(y, mo, d, h=0, mi=0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp() * 1000)


def test_session_anchor_matches_zoneinfo_1987_2040_equal():
    """Свідок: для кожної дати 1987–2040 відкриття = 17:00 America/New_York у UTC — з історією законів DST США
    (1987–2006 квітень…жовтень, з 2007 березень…листопад). Зміниться закон/tzdata — червоне."""
    day = dt.date(1987, 1, 1)
    while day <= dt.date(2040, 12, 31):
        ny_close = dt.datetime(day.year, day.month, day.day, 17, 0, tzinfo=NY).astimezone(UTC)
        expected_ms = int(ny_close.timestamp() * 1000)
        assert trading_day_open_ms(expected_ms, FXCM) == expected_ms, day
        assert trading_day_open_ms(expected_ms - 1, FXCM) < expected_ms, day
        day += dt.timedelta(days=1)


@pytest.mark.parametrize("open_utc", [
    (1995, 3, 30, 22), (1995, 10, 30, 22), (2006, 3, 28, 22), (2006, 10, 30, 22), (2006, 4, 3, 21),
    (2007, 3, 12, 21), (2007, 11, 1, 21),
])
def test_session_anchor_pre2007_follows_the_law_of_its_year_like_fxcm_native_d1(open_utc):
    """Нативний D1 FXCM (= TV), забраний 23.09.2026 з 1990: 30.03.1995 і 28.03.2006 — 22:00 UTC (старе правило:
    літо з першої неділі квітня), 03.04.2006 — 21:00, з 2007 — нове правило. XAU і NAS100 однаково."""
    open_ms = _ms(*open_utc)
    assert trading_day_open_ms(open_ms, FXCM) == open_ms
    assert htf_bucket_start_ms(open_ms + 5 * 3_600_000, D1_S, FXCM) == open_ms


def test_h4_bucket_nested_in_session_day_all_seasons_true():
    """H4 — від відкриття сесії (18:00 NY = відкриття торгового дня + 1 год, як TV FX:): бакет у межах сесійного дня,
    кратний 4 год від його відкриття; D1 — від 17:00 NY, тож відкриття сесії = D1 + 1 год у кожному сезоні."""
    rng = random.Random(95)
    lo, hi = _ms(2024, 1, 1), _ms(2028, 1, 1)
    for _ in range(20_000):
        ts = rng.randrange(lo, hi)
        session_open = trading_day_open_ms(ts - 3_600_000, FXCM) + 3_600_000
        session_next = trading_day_open_ms(session_open - 3_600_000 + 26 * 3_600_000, FXCM) + 3_600_000
        h4_open = htf_bucket_start_ms(ts, H4_S, FXCM)
        assert session_open <= h4_open <= ts < session_next
        assert (h4_open - session_open) % H4_MS == 0
        assert min(h4_open + H4_MS, session_next) > ts
        assert htf_anchor_offset_s(H4_S, ts, FXCM) == (htf_anchor_offset_s(D1_S, ts - 3_600_000, FXCM) + 3600) % 86_400


def test_dst_weekend_2026_03_08_grid_switch_matches_tv():
    """Пт 06.03 19:00 UTC — останній H4 зимової сітки (18:00 EST + 4k); Нд 08.03 21:00 UTC — відкриття першої літньої
    доби (23 год), перший літній H4 — 22:00 (18:00 EDT); D1 лишається 21:00."""
    fri_last_h4 = _ms(2026, 3, 6, 19)
    assert htf_bucket_start_ms(fri_last_h4 + 30 * 60_000, H4_S, FXCM) == fri_last_h4
    sun_open = _ms(2026, 3, 8, 21)
    assert trading_day_open_ms(sun_open, FXCM) == sun_open
    assert trading_day_open_ms(sun_open - 1, FXCM) == _ms(2026, 3, 7, 22)  # субота-доба: 23 год
    assert htf_bucket_start_ms(sun_open + 60_000, D1_S, FXCM) == sun_open
    assert htf_bucket_start_ms(_ms(2026, 3, 8, 22, 1), H4_S, FXCM) == _ms(2026, 3, 8, 22)
    assert htf_anchor_offset_s(H4_S, _ms(2026, 3, 8, 22, 1), FXCM) == 22 * 3600


def test_dst_weekend_2026_11_01_winter_grid_starts_at_23():
    """01.11.2026 (перша неділя листопада) — перша зимова доба відкривається о 22:00 UTC (D1), сесія — о 23:00 (H4);
    субота-доба має 25 год."""
    sun_open = _ms(2026, 11, 1, 22)
    assert trading_day_open_ms(sun_open, FXCM) == sun_open
    assert trading_day_open_ms(sun_open - 1, FXCM) == _ms(2026, 10, 31, 21)
    assert htf_bucket_start_ms(_ms(2026, 11, 1, 23, 1), H4_S, FXCM) == _ms(2026, 11, 1, 23)
    assert htf_bucket_start_ms(_ms(2026, 11, 2, 3), H4_S, FXCM) == _ms(2026, 11, 2, 3)


def test_h4_summer_2026_grid_is_tv_fx_grid():
    """Літо 2026: H4 на 22/02/06/10/14/18 UTC — сітка TV FX: (від відкриття сесії 18:00 NY), звірено 24.09 на
    FX:XAUUSD/XAGUSD/NAS100/SPX500/US30/EUSTX50; сітку 21/01/05/… (17:00 NY) мають TV OANDA і нативний H4 FXCM."""
    starts = {htf_bucket_start_ms(_ms(2026, 9, 22, h, 30), H4_S, FXCM) % D1_MS // 3_600_000 for h in range(24)}
    assert starts == {22, 2, 6, 10, 14, 18}
    d1_starts = {htf_bucket_start_ms(_ms(2026, 9, 22, h, 30), D1_S, FXCM) % D1_MS // 3_600_000 for h in range(24)}
    assert d1_starts == {21}


def test_below_h4_buckets_are_epoch_aligned_and_anchor_zero():
    ts = _ms(2026, 9, 22, 17, 44) + 12_345
    assert htf_bucket_start_ms(ts, 900, FXCM) == _ms(2026, 9, 22, 17, 30)
    assert htf_bucket_start_ms(ts, 3600, FXCM) == _ms(2026, 9, 22, 17)
    assert htf_anchor_offset_s(3600, ts, FXCM) == 0


def test_utc_midnight_rule_is_binance_grid():
    ts = _ms(2026, 9, 22, 17, 44)
    assert trading_day_open_ms(ts, RULE_UTC_MIDNIGHT) == _ms(2026, 9, 22)
    assert htf_bucket_start_ms(ts, H4_S, RULE_UTC_MIDNIGHT) == _ms(2026, 9, 22, 16)


@pytest.mark.parametrize("call", [
    lambda: trading_day_open_ms(0, "legacy_79200"),
    lambda: htf_bucket_start_ms(0, 43_200, FXCM),
    lambda: htf_anchor_offset_s(43_200, 0, FXCM),
])
def test_unknown_rule_or_tf_raises_loudly(call):
    with pytest.raises(ValueError):
        call()
